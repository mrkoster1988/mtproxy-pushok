"""Ограниченный диалог владельца с @MTProxyBot; никаких выдуманных callback_data."""
import asyncio
import hashlib
import re
import time
from urllib.parse import parse_qs, urlparse


CARD_HEADER = re.compile(
    r'^[ \t]*(?:proxy(?: server)?|прокси(?:-сервер)?)(?:\s*:\s*|\s+)'
    r'([a-z0-9][a-z0-9.-]*):(\d{1,5})\s+(?:with (?:the )?tag|с тегом)\s*'
    r'`?([a-f0-9]{32})(?!\w)', re.I | re.M)


class BotProtocolError(Exception):
    pass


def username(value):
    match = re.fullmatch(r'(?:https://(?:t\.me|telegram\.me)/|@)?([A-Za-z][A-Za-z0-9_]{3,31})/?', value.strip(), re.I)
    if not match:
        raise BotProtocolError('public_channel_required')
    return match[1].lower()


async def resolve_channel(client, target):
    """Проверяем тип и все активные имена; ошибки не маскируются под ожидание."""
    try:
        channel = await client.get_entity('@' + target)
    except Exception as exc:
        # Сетевые ошибки и FloodWait сохраняют отдельную обработку worker.
        if isinstance(exc, ValueError) or type(exc).__name__ in ('UsernameInvalidError', 'UsernameNotOccupiedError'):
            raise BotProtocolError('channel_not_found') from None
        if type(exc).__name__ in ('ChannelPrivateError', 'ChannelInvalidError'):
            raise BotProtocolError('channel_access_denied') from None
        raise
    if not getattr(channel, 'broadcast', False):
        raise BotProtocolError('channel_not_broadcast')
    active = {str(getattr(channel, 'username', '') or '').lower()}
    active.update(str(item.username).lower() for item in (getattr(channel, 'usernames', None) or [])
                  if getattr(item, 'active', False))
    if target not in active:
        raise BotProtocolError('channel_username_mismatch')
    return channel


def text(message):
    return getattr(message, 'raw_text', '') or ''


def urls(message):
    # Ссылки могут быть скрыты под текстом названия канала.
    return [e.url for e in (getattr(message, 'entities', None) or []) if hasattr(e, 'url')]


def endpoints(value):
    result = set()
    address = re.search(r'(?im)^\s*(?:address|server|ip|адрес)\s*:\s*([a-zA-Z0-9][a-zA-Z0-9.-]*)\s*$', value)
    port_field = re.search(r'(?im)^\s*(?:port|порт)\s*:\s*(\d{1,5})\s*$', value)
    if address and port_field and 0 < int(port_field[1]) <= 65535:
        result.add((address[1].lower(), int(port_field[1])))
    for host, port in re.findall(r'(?<![\w.-])([a-zA-Z0-9][a-zA-Z0-9.-]*):(\d{1,5})(?!\d)', value):
        if 0 < int(port) <= 65535:
            result.add((host.lower(), int(port)))
    for link in re.findall(r'(?:https?://t\.me/proxy\?[^\s<>]+|tg://proxy\?[^\s<>]+)', value):
        query = parse_qs(urlparse(link).query)
        if len(query.get('server', [])) == len(query.get('port', [])) == 1:
            try:
                result.add((query['server'][0].lower(), int(query['port'][0])))
            except ValueError:
                pass
    return result


def list_proxy(value):
    """В списке бот может показывать адрес и тег без порта: это лишь кандидат."""
    match = re.fullmatch(r'\s*([a-zA-Z0-9][a-zA-Z0-9.-]*)\s+([a-fA-F0-9]{32})\s*', value)
    if match and match[2] != '0' * 32:
        return match[1].lower(), match[2].lower()
    return None


def card_tag(message, hosts, port):
    wanted = {(h.lower(), port) for h in hosts}
    if not endpoints(text(message) + '\n' + '\n'.join(urls(message))) & wanted:
        raise BotProtocolError('proxy_identity_not_verified')
    # Не путать тег с расположенным рядом 32-символьным secret.
    tags = set(re.findall(r'(?im)^[ \t]*(?:proxy[ _-]*)?(?:tag|тег)\s*[:=]\s*`?([a-f0-9]{32})(?!\w)', text(message)))
    # Оригинал: «Proxy host:port with the tag …». Старые карточки и
    # переводы могут содержать «server» и двоеточие после «Proxy».
    # Тег принимаем только из заголовка с проверенным адресом и портом,
    # а не из названия продвигаемого канала или произвольного текста.
    for host, shown_port, tag in CARD_HEADER.findall(text(message)):
        if (host.lower(), int(shown_port)) not in wanted:
            raise BotProtocolError('proxy_identity_not_verified')
        tags.add(tag)
    tags = {t.lower() for t in tags}
    if len(tags) != 1 or '0' * 32 in tags:
        raise BotProtocolError('tag_not_verified')
    return tags.pop()


def promoted_channel(message):
    # Проверяем именно поле продвижения, а не случайное упоминание канала.
    match = re.search(r'(?im)^\s*(?:promotion|promoted channel|sponsor(?: channel)?|канал(?: спонсора)?|продвижение|рекомендованный канал)\s*:\s*(.+)$', text(message))
    if not match:
        return None
    values = re.findall(r'(?:https://t\.me/|@)([a-zA-Z][a-zA-Z0-9_]{3,31})', match[1])
    # Telegram entity offsets измеряются UTF-16, а не индексами Python.
    left = len(text(message)[:match.start(1)].encode('utf-16-le')) // 2
    right = len(text(message)[:match.end(1)].encode('utf-16-le')) // 2
    for entity in getattr(message, 'entities', None) or []:
        if hasattr(entity, 'url') and left <= entity.offset < right:
            try:
                values.append(username(entity.url))
            except BotProtocolError:
                pass
    values = set(v.lower() for v in values)
    return values.pop() if len(values) == 1 else None


def empty_promotion(message):
    return (promoted_channel(message) is None and
            re.search(r'(?im)^[ \t]*(?:promoted channel|рекомендованный канал)[ \t]*:[ \t]*n/a\.?[ \t]*$', text(message)) is not None)


def buttons(message):
    for row in getattr(message, 'buttons', None) or []:
        for button in row:
            # Не нажимаем URL, login, запрос телефона, платежи или другие типы.
            original = button.button
            kind = type(original).__name__
            action = getattr(original, 'type', None)
            # Telethon 1.45: тип действия теперь вложен в KeyboardInlineButton
            # или KeyboardButton. Проверять только внешний класс небезопасно.
            callback = ((kind == 'KeyboardInlineButton' and type(action).__name__ == 'InlineButtonTypeCallback')
                        or (kind == 'KeyboardButtonCallback' and action is None))
            plain = kind == 'KeyboardButton' and (action is None or type(action).__name__ == 'ButtonTypeDefault')
            if plain or (callback and not getattr(action if action is not None else original, 'requires_password', False)):
                yield button


def snapshot(message):
    return (text(message), tuple((b.text, getattr(b, 'data', None)) for b in buttons(message)))


def registration_tag(message):
    value = text(message)
    if not re.search(r'(?i)\bsuccess(?:ful(?:ly)?)?\b|\b(?:is now|has been) registered\b', value):
        return None
    tags = re.findall(r'(?i)\btag\b[^\n:]{0,90}[:=]\s*`?([a-f0-9]{32})\b', value)
    return tags[0].lower() if len(tags) == 1 else None


class MTProxyDialog:
    def __init__(self, client, bot, timeout=15):
        self.client, self.bot, self.timeout = client, bot, timeout

    async def fresh(self, after, edited=None, before=None):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            messages = await self.client.get_messages(self.bot, min_id=after, limit=10)
            incoming = [m for m in messages if not m.out and m.sender_id == self.bot.id]
            if incoming:
                # Несколько новых сообщений: не угадываем состояние диалога.
                if len(incoming) != 1:
                    raise BotProtocolError('ambiguous_bot_response')
                return incoming[0]
            if edited is not None:
                current = await self.client.get_messages(self.bot, ids=edited)
                if (current and not current.out and current.sender_id == self.bot.id
                        and snapshot(current) != before):
                    return current
            await asyncio.sleep(0.7)
        raise BotProtocolError('bot_response_timeout')

    async def send(self, value):
        sent = await self.client.send_message(self.bot, value, link_preview=False, parse_mode=None)
        return await self.fresh(sent.id)

    async def click(self, message, button):
        last = await self.client.get_messages(self.bot, limit=1)
        after = last[0].id if last else message.id
        before = snapshot(message)
        # Кнопка получена от бота; payload не конструируем и не сохраняем.
        await button.click()
        return await self.fresh(after, message.id, before)

    async def select(self, hosts, port, *, allow_missing=False):
        message = await self.send('/myproxies')
        matches, pages = [], set()
        wanted_hosts = {h.lower() for h in hosts}
        wanted = {(h, port) for h in wanted_hosts}
        # /myproxies иногда сразу открывает карточку узла вместо списка.
        direct = CARD_HEADER.match(text(message).lstrip())
        if direct:
            if (direct[1].lower(), int(direct[2])) in wanted:
                card_tag(message, hosts, port)
                return message
            back = [b for b in buttons(message) if b.text.strip().casefold() in
                    ('« back to list', 'back to list', '« к списку', 'к списку')]
            if len(back) != 1:
                raise BotProtocolError('proxy_identity_not_verified')
            message = await self.click(message, back[0])
        for _ in range(10):
            signature = snapshot(message)
            if signature in pages:
                raise BotProtocolError('proxy_list_loop')
            pages.add(signature)
            for button in buttons(message):
                identity = list_proxy(button.text)
                if endpoints(button.text) & wanted:
                    matches.append((message, button, None))
                elif identity and identity[0] in wanted_hosts:
                    matches.append((message, button, identity[1]))
            next_buttons = [b for b in buttons(message) if b.text.strip().lower() in ('next', 'next ›', 'next »', '›', '»', 'далее', 'вперёд')]
            if not next_buttons:
                break
            if len(next_buttons) != 1:
                raise BotProtocolError('proxy_list_ambiguous')
            message = await self.click(message, next_buttons[0])
        else:
            raise BotProtocolError('proxy_list_too_long')
        if not matches:
            raise BotProtocolError('proxy_not_found')
        # Несколько портов одного IP: проверяем карточки, не меняя продвижение.
        if (len(matches) > 8 or (len(matches)>1 and
                (any(tag is None for _,_,tag in matches) or len({tag for _,_,tag in matches}) != len(matches)))):
            raise BotProtocolError('proxy_ambiguous')
        verified = []
        for message, button, listed_tag in matches:
            card = await self.click(message, button)
            shown = endpoints(text(card)+'\n'+'\n'.join(urls(card)))
            if not shown & wanted:
                if (len(matches)>1 or allow_missing) and shown and all(h in wanted_hosts and p != port for h,p in shown):
                    continue
                raise BotProtocolError('proxy_identity_not_verified')
            if listed_tag and card_tag(card,hosts,port) != listed_tag:
                raise BotProtocolError('tag_changed')
            verified.append((message,button,card))
        if len(verified) != 1:
            raise BotProtocolError('proxy_not_found' if not verified else 'proxy_ambiguous')
        message,button,card = verified[0]
        if len(matches)>1:
            previous = card_tag(card,hosts,port)
            card = await self.click(message,button)
            if card_tag(card,hosts,port) != previous:
                raise BotProtocolError('tag_changed')
        return card

    async def registration_receipt(self, host, port, secret, tag):
        # Старый агент сохранял только намерение. Сверяем последовательность
        # исходящих сообщений и положительную квитанцию именно этого бота.
        history = list(reversed(await self.client.get_messages(self.bot, limit=100)))
        for command, address_prompt, address, secret_prompt, supplied, receipt in zip(
                history, history[1:], history[2:], history[3:], history[4:], history[5:]):
            if not all(m.out for m in (command, address, supplied)):
                continue
            if not all(not m.out and m.sender_id == self.bot.id for m in (address_prompt, secret_prompt, receipt)):
                continue
            if (text(command).strip() == '/newproxy' and text(address).strip() == f'{host}:{port}'
                    and text(supplied).strip().lower() == secret and registration_tag(receipt) == tag):
                return True
        return False

    async def register(self, request, state):
        """Никаких повторов /newproxy после неопределённого исхода отправки."""
        import ipaddress
        from uuid import UUID
        host = str(ipaddress.IPv4Address(request['host']))
        port, secret = request['port'], request['secret']
        if not ipaddress.ip_address(host).is_global or type(port) is not int or not 1024<=port<=65535:
            raise BotProtocolError('proxy_identity_not_verified')
        if not isinstance(secret,str) or not re.fullmatch('[a-f0-9]{32}',secret):
            raise BotProtocolError('registration_secret_mismatch')
        marker = 'registration-'+str(UUID(request['node_id']))+'.json'
        previous = state.read_file(marker)
        identity = {'host':host,'port':port}
        if previous and any(previous.get(k)!=v for k,v in identity.items()):
            raise BotProtocolError('proxy_identity_not_verified')
        digest = hashlib.sha256(secret.encode('ascii')).hexdigest()
        receipt_tag = previous.get('tag') if previous and previous.get('secret_sha256') == digest else None
        try:
            card = await self.select([host],port,allow_missing=True)
        except BotProtocolError as exc:
            if str(exc) != 'proxy_not_found':
                raise
            if previous:
                raise BotProtocolError('registration_uncertain') from None
            prompt = await self.send('/newproxy')
            if not re.search(r'(?is)(?:ip|address|адрес).*(?:port|порт)',text(prompt)):
                raise BotProtocolError('registration_prompt_invalid')
            prompt = await self.send(f'{host}:{port}')
            if not re.search(r'(?is)(?:send|enter|specify|пришл|отправ|укаж).*(?:secret|секрет)',text(prompt)):
                raise BotProtocolError('registration_prompt_invalid')
            # Сохраняем намерение ДО финальной отправки. При обрыве — только чтение.
            state.save_file(marker,identity)
            receipt_tag = registration_tag(await self.send(secret))
            if receipt_tag:
                state.save_file(marker,dict(identity,tag=receipt_tag,secret_sha256=digest))
            previous = identity
            card = await self.select([host],port,allow_missing=True)
        secrets = set(re.findall(r'(?i)secret\s*[:=]\s*[`\s]*([a-f0-9]{32})(?![a-f0-9])',text(card)))
        for url in urls(card)+re.findall(r'(?:https?://t\.me/proxy\?[^\s<>]+|tg://proxy\?[^\s<>]+)',text(card)):
            values = parse_qs(urlparse(url).query).get('secret',[])
            secrets.update(v[2:] if v.startswith('dd') and len(v)==34 else v for v in values)
        tag = card_tag(card,[host],port)
        if secrets:
            if {s.lower() for s in secrets} != {secret}:
                raise BotProtocolError('registration_secret_mismatch')
        elif receipt_tag != tag and not (previous and await self.registration_receipt(host,port,secret,tag)):
            raise BotProtocolError('registration_uncertain')
        state.save_file(marker,dict(identity,tag=tag,secret_sha256=digest))
        return {'tag':tag,'bot_message_id':card.id}

    async def prepare(self, request):
        try:
            target = username(request['channel_url'])
        except BotProtocolError:
            raise BotProtocolError('channel_link_invalid') from None
        channel = await resolve_channel(self.client, target)
        if request.get('telegram_channel_id') and request['telegram_channel_id'] != channel.id:
            raise BotProtocolError('target_changed')
        card = await self.select(request['hosts'], request['port'])
        tag = card_tag(card, request['hosts'], request['port'])
        matches = await self.matches_promotion(card, target, channel.id)
        if request['action'] == 'bind':
            if tag != request['expected_tag']:
                raise BotProtocolError('tag_changed')
            if not matches:
                choices = [b for b in buttons(card) if b.text.strip().lower() in
                           ('set promotion', 'edit promotion', 'изменить продвижение', 'задать продвижение')]
                if len(choices) != 1:
                    raise BotProtocolError('set_promotion_button_missing')
                prompt = await self.click(card, choices[0])
                if not re.search(r'(?is)(?:send|enter|укаж|пришл|отправ).*(?:channel|канал)', text(prompt)):
                    raise BotProtocolError('channel_prompt_not_verified')
                await self.send('https://t.me/' + target)
            # Повторное открытие карточки обязательно, даже после "Success".
            card = await self.select(request['hosts'], request['port'])
            if card_tag(card, request['hosts'], request['port']) != tag:
                raise BotProtocolError('binding_not_verified')
            matches = await self.matches_promotion(card, target, channel.id)
            if not matches:
                raise BotProtocolError('binding_not_verified')
        return {'tag': tag, 'channel_username': target if matches else None,
                'telegram_channel_id': channel.id, 'bot_message_id': card.id, 'promotion_empty': empty_promotion(card)}

    async def matches_promotion(self, card, target, channel_id):
        shown = promoted_channel(card)
        if shown == target:
            return True
        if not shown:
            return False
        # Бот может показать основное имя вместо дополнительного. Одного
        # совпадения названий недостаточно: сверяем числовой ID через Telegram.
        try:
            channel = await resolve_channel(self.client, shown)
        except BotProtocolError:
            return False
        return channel.id == channel_id
