"""Одно меню на чат. Платежи, заявки и пользовательские сообщения не удаляются."""
import json
from urllib.error import HTTPError

TRANSIENT = frozenset(('start', 'tariffs', 'help', 'channels'))


class Navigation:
    def __init__(self, call):
        self.call = call
        self.messages = {}

    def deliver(self, token, update, payload):
        cb = update.get('callback_query') or {}
        msg = update.get('message') or {}
        command = cb.get('data') or str(msg.get('text', '')).split(' ', 1)[0].split('@')[0].lstrip('/')
        chat = payload.get('chat_id')
        if command not in TRANSIENT:
            return self.call(token, 'sendMessage', payload)
        old = self.messages.get(chat)
        # Только ID меню, которое мы сами создали. Кнопки меню встречаются и у счетов.
        source_id = (cb.get('message') or {}).get('message_id')
        if old and (source_id is None or source_id == old):
            try:
                result = self.call(token, 'editMessageText', dict(payload, message_id=old))
                self.messages[chat] = old
                return result
            except HTTPError as exc:
                if exc.code != 400:
                    raise
                try:
                    description = json.loads(exc.read(8192)).get('description', '').lower()
                except (ValueError, OSError):
                    raise exc
                if 'message is not modified' in description:
                    self.messages[chat] = old
                    return None
                if not any(s in description for s in ('message to edit not found', "message can't be edited")):
                    raise exc
        result = self.call(token, 'sendMessage', payload)
        self.messages[chat] = result['message_id']
        if len(self.messages) > 10000:
            self.messages.pop(next(iter(self.messages)))
        if old:
            try:
                self.call(token, 'deleteMessage', {'chat_id': chat, 'message_id': old})
            except (OSError, RuntimeError, ValueError):
                pass  # Новый экран уже показан; ошибка уборки не повторяет отправку.
        return result
