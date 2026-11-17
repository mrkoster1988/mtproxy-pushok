"""Ограниченный диалог владельца с @MTProxyBot; никаких выдуманных callback_data."""
import asyncio
import re
import time
from urllib.parse import parse_qs, urlparse


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


def card_tag(message, hosts, port):
    if not endpoints(text(message) + '\n' + '\n'.join(urls(message))) & {(h.lower(), port) for h in hosts}:
        raise BotProtocolError('proxy_identity_not_verified')
    # Не путать тег с расположенным рядом 32-символьным secret.
    tags = set(re.findall(r'(?i)(?:proxy[ _-]*)?(?:tag|тег)\s*[:=]\s*[`\s]*([a-f0-9]{32})(?![a-f0-9])', text(message)))
    tags = {t.lower() for t in tags}
    if len(tags) != 1 or '0' * 32 in tags:
        raise BotProtocolError('tag_not_verified')
    return tags.pop()


def promoted_channel(message):
    # Проверяем именно поле продвижения, а не случайное упоминание канала.
    match = re.search(r'(?im)^\s*(?:promotion|promoted channel|sponsor(?: channel)?|канал(?: спонсора)?|продвижение)\s*:\s*(.+)$', text(message))
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


def buttons(message):
    for row in getattr(message, 'buttons', None) or []:
        for button in row:
            # Не нажимаем URL, login, запрос телефона, платежи или другие типы.
            if type(button.button).__name__ in ('KeyboardButtonCallback', 'KeyboardButton'):
                yield button


def snapshot(message):
    return (text(message), tuple((b.text, getattr(b, 'data', None)) for b in buttons(message)))


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

    async def select(self, hosts, port):
        message = await self.send('/myproxies')
        matches, pages = [], set()
        wanted = {(h.lower(), port) for h in hosts}
        for _ in range(10):
            signature = snapshot(message)
            if signature in pages:
                raise BotProtocolError('proxy_list_loop')
            pages.add(signature)
            for button in buttons(message):
                if endpoints(button.text) & wanted:
                    matches.append((message, button))
            next_buttons = [b for b in buttons(message) if b.text.strip().lower() in ('next', 'next ›', 'next »', '›', '»', 'далее', 'вперёд')]
            if not next_buttons:
                break
            if len(next_buttons) != 1:
                raise BotProtocolError('proxy_list_ambiguous')
            message = await self.click(message, next_buttons[0])
        else:
            raise BotProtocolError('proxy_list_too_long')
        if len(matches) != 1:
            raise BotProtocolError('proxy_not_found' if not matches else 'proxy_ambiguous')
        return await self.click(*matches[0])

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
                           ('set promotion', 'изменить продвижение', 'задать продвижение')]
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
                'telegram_channel_id': channel.id, 'bot_message_id': card.id}

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
