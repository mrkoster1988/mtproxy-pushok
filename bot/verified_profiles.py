"""Запрос своего контакта и поздравления. Номер/дата не попадают в логи."""
from datetime import date
from urllib.error import HTTPError, URLError

from .advertiser_notices import deliver_advertiser_notices


def birthday_from_chat(chat):
    value = chat.get('birthdate') if isinstance(chat, dict) else None
    if not isinstance(value, dict):
        return None
    day, month, year = value.get('day'), value.get('month'), value.get('year')
    if type(day) is not int or type(month) is not int or (year is not None and type(year) is not int):
        return None
    try:
        birthday = date(year if year is not None else 2000, month, day)
        if year and (birthday > date.today() or date.today().year-year > 120 or year < 1900):
            return None
    except (ValueError, TypeError):
        return None
    return dict(day=day, month=month, year=year)


def verification_response(update, api, telegram):
    message = update.get('message') or {}
    text = message.get('text', '').strip()
    command = text.split(maxsplit=1)[0].split('@')[0].lower() if text else ''
    requested = command == '/verify' or (command == '/start' and text.split(maxsplit=1)[1:] == ['verify'])
    contact = message.get('contact')
    if not requested and not contact:
        return None
    sender, chat = message.get('from') or {}, message.get('chat') or {}
    uid = sender.get('id')
    if chat.get('type') != 'private' or chat.get('id') != uid or type(uid) is not int or sender.get('is_bot'):
        return None
    response = {'chat_id': uid, 'reply_markup': {'remove_keyboard': True}}
    try:
        if requested:
            intent = api('verification/request', {'telegram_user_id': uid, 'birthday_opt_in': True})
            birthday_consent = ('Доступную дату рождения сохраним для ежегодного поздравления и бонуса по условиям puShok; '
                'поздравления можно отключить в профиле. ') if isinstance(intent, dict) and intent.get('birthday_opt_in') else ''
            response['text'] = ('Подтвердите свой номер в puShok. Нажмите кнопку ниже и разрешите Telegram передать контакт. '
                'Номер сохранится в профиле и будет виден вам и администратору. '
                + birthday_consent + 'Синяя галочка означает подтверждённый номер в puShok.')
            response['reply_markup'] = {'keyboard': [[{'text': 'Подтвердить мой номер', 'request_contact': True}]],
                                      'resize_keyboard': True, 'one_time_keyboard': True}
            return response
        if (contact.get('user_id') != uid or any(key in message for key in
                ('forward_origin', 'forward_from', 'forward_from_chat', 'forward_date', 'via_bot'))):
            response['text'] = 'Нужен ваш собственный контакт. Откройте /verify и нажмите «Подтвердить мой номер».'
            return response
        birthday = None
        try:
            birthday = birthday_from_chat(telegram('getChat', {'chat_id': uid}))
        except (HTTPError, URLError, OSError, RuntimeError, ValueError):
            pass  # Недоступная дата рождения не отменяет подтверждение номера.
        result = api('verification/contact', {
            'telegram_user_id': uid, 'chat_id': chat['id'], 'contact_user_id': contact['user_id'],
            'private_chat': True, 'forwarded': False, 'phone_number': contact.get('phone_number', ''),
            'update_id': update['update_id'], 'message_date': message.get('date', 0), 'birthday': birthday,
        })
        response['text'] = ('Номер подтверждён в puShok. Синяя галочка появится в профиле. '
            'Дату рождения и поздравления можно проверить в разделе «Профиль».') if result.get('verified') else 'Подтверждение не завершено. Откройте /verify ещё раз.'
        return response
    except HTTPError as exc:
        if exc.code not in (400, 404, 409, 422):
            raise
        response['text'] = ('Сначала войдите в кабинет puShok, затем откройте /verify и отправьте свой номер кнопкой Telegram. '
                            'Запрос действует 10 минут.')
        return response


def render_birthday(notice):
    name = ''.join(c for c in str(notice.get('first_name') or '') if c.isprintable()).strip()[:80]
    text = (name + ', с днём рождения!' if name else 'С днём рождения!')
    text += '\n\nПусть этот год принесёт радость, хорошие новости и удачные начинания. Ваш Пушок.'
    amount = notice.get('bonus_amount', 0)
    if type(amount) is int and amount > 0:
        text += f'\n\nПодарок уже на бонусном балансе: {amount / 100:,.2f} ₽.'.replace(',', ' ').replace('.00 ₽', ' ₽')
    return text


def deliver_birthdays(api, sender, miniapp_url=None):
    def mapped(route, payload):
        return api(route.replace('advertisers/outbox/', 'birthday/outbox/'), payload)
    def keyboard(_):
        if miniapp_url:
            return {'inline_keyboard': [[{'text': 'Мой профиль', 'web_app': {'url': miniapp_url.split('#')[0]+'#/profile'}}]]}
        return {'inline_keyboard': [[{'text': 'Мой кабинет', 'callback_data': 'start'}]]}
    deliver_advertiser_notices(mapped, sender, renderer=render_birthday, keyboard_builder=keyboard)
