"""Сертификат с точной суммой; отправку в выбранный чат подтверждает пользователь."""
import io
import json
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from .stars_checkout import credentials, _NoRedirect
from .telegram_environment import bot_api_url

_lock = Lock()
_requests = OrderedDict()


def throttle(uid):
    now = time.monotonic()
    with _lock:
        recent = [t for t in _requests.get(uid, []) if now - t < 60]
        if len(recent) >= 10:
            raise HTTPException(429, 'gift_share_rate_limit')
        _requests[uid] = recent + [now]
        _requests.move_to_end(uid)
        while len(_requests) > 512:
            _requests.popitem(last=False)


def rubles(amount):
    whole, cents = divmod(amount, 100)
    return f'{whole:,}'.replace(',', ' ') + (f',{cents:02d}' if cents else '') + ' ₽'


def image_path(amount):
    return f'/api/v1/certificates/bonus/v1/{amount}.jpg'


@lru_cache(maxsize=64)
def certificate(amount):
    # В изображении нет кода, имени и Telegram ID. Оно не даёт права на активацию.
    if type(amount) is not int or not 1 <= amount <= 1000000:
        raise ValueError('invalid_certificate_amount')
    image = Image.new('RGB', (1200, 720), '#071625')
    draw = ImageDraw.Draw(image)
    for y in range(720):
        draw.line((0, y, 1200, y), fill=(7 + y * 4 // 720, 22 + y * 16 // 720, 37 + y * 15 // 720))
    draw.rounded_rectangle((28, 28, 1172, 692), radius=32, outline='#2b6d75', width=2)
    draw.rounded_rectangle((58, 58, 64, 662), radius=3, fill='#35d4c4')
    def font(size, bold=False):
        return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans' + ('-Bold' if bold else '') + '.ttf', size)
    draw.text((100, 68), 'puShok', font=font(36, True), fill='#35d4c4')
    draw.text((100, 150), 'ПОДАРОЧНЫЙ', font=font(24), fill='#a7c5d4')
    draw.text((96, 187), 'СЕРТИФИКАТ', font=font(46, True), fill='#edf7fc')
    size = 92
    while draw.textlength(rubles(amount), font=font(size, True)) > 690:
        size -= 2
    draw.text((96, 272), rubles(amount), font=font(size, True), fill='#edf7fc')
    draw.text((100, 391), 'бонусами на рекламу в MTProxy', font=font(28), fill='#35d4c4')
    mascot = Image.open(Path(__file__).resolve().parents[1] / 'web' / 'pushok-pencil.png').convert('RGBA')
    mascot.thumbnail((325, 440))
    image.paste(mascot, (830 + (300 - mascot.width) // 2, 112 + (440 - mascot.height) // 2), mascot)
    draw.line((100, 515, 1100, 515), fill='#294758', width=2)
    draw.text((100, 548), 'Активируйте подарок по ссылке под картинкой', font=font(25), fill='#edf7fc')
    draw.text((100, 602), 'Для оплаты рекламы в веб-кабинете.', font=font(21), fill='#a7c5d4')
    draw.text((100, 634), 'Не обменивается на деньги или Telegram Stars.', font=font(21), fill='#a7c5d4')
    output = io.BytesIO()
    image.save(output, 'JPEG', quality=90, optimize=True)
    return output.getvalue()


def share_data(gift):
    now = datetime.now(timezone.utc)
    if gift['deleted_at']:
        raise HTTPException(409, 'wallet_gift_deleted')
    if not gift['active']:
        raise HTTPException(409, 'wallet_gift_disabled')
    if gift['expires_at'] <= now:
        raise HTTPException(409, 'wallet_gift_expired_code')
    if gift['used'] >= gift['max_uses']:
        raise HTTPException(409, 'wallet_gift_limit_reached')
    bot = os.getenv('TELEGRAM_BOT_USERNAME', '')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', bot):
        raise HTTPException(503, 'gift_share_bot_unavailable')
    url = 'https://t.me/' + bot + '?startapp=wallet_' + gift['code']
    expires = gift['expires_at'].astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y, %H:%M')
    text = (f"🎁 Подарочный сертификат puShok на {rubles(gift['amount'])}\n\n"
            'Бонусы на рекламу вашего Telegram-канала в MTProxy.\n'
            'Откройте ссылку — подарок зачислится на бонусный баланс.\n'
            'Используйте его для оплаты рекламы в веб-кабинете.\n\n'
            f'Активировать до {expires} МСК. Один раз на пользователя.\n')
    if gift['recipient_telegram_id']:
        text += 'Именной подарок: доступен только назначенному получателю.\n'
    text += 'Бонусы не обмениваются на деньги или Stars.\n\n' + url
    return dict(text=text, url=url, image_path=image_path(gift['amount']),
                share_url='https://t.me/share/url?' + urlencode({'url': url, 'text': text.rsplit('\n\n', 1)[0]}))


def public_origin():
    # Не доверяем входящему Host/X-Forwarded-Host при передаче URL в Telegram.
    raw = os.getenv('PUSHOK_MINIAPP_URL', '')
    try:
        url = urlsplit(raw)
        if (any(c.isspace() for c in raw) or url.scheme != 'https' or url.username or url.password
                or url.port not in (None, 443) or url.query or url.fragment
                or url.path not in ('/miniapp', '/miniapp/')
                or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', url.hostname or '')):
            raise ValueError()
        return 'https://' + url.hostname
    except ValueError:
        raise HTTPException(503, 'gift_share_image_unavailable') from None


def prepare_message(user_id, gift, data):
    token, _ = credentials()
    photo = public_origin() + data['image_path']
    body = dict(user_id=user_id, allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True,
                result=dict(type='photo', id=str(gift['id']), photo_url=photo, thumbnail_url=photo,
                            photo_width=1200, photo_height=720, title='Подарочный сертификат puShok',
                            caption=data['text'], reply_markup={'inline_keyboard': [[{'text': 'Получить подарок', 'url': data['url']}]]}))
    request = Request(bot_api_url(token, 'savePreparedInlineMessage'), data=json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with build_opener(_NoRedirect).open(request, timeout=8) as response:
            raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError()
        reply = json.loads(raw)
        result = reply.get('result', {})
        if (reply.get('ok') is not True or not isinstance(result.get('id'), str)
                or not 1 <= len(result['id']) <= 512 or type(result.get('expiration_date')) is not int
                or result['expiration_date'] <= time.time()):
            raise ValueError()
        return result['id']
    except (OSError, ValueError, TypeError, AttributeError):
        # Не раскрываем URL с bot token или ответ Telegram в журнале/клиенте.
        raise HTTPException(503, 'gift_share_image_unavailable') from None
