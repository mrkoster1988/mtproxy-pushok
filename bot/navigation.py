"""Приветствие и один текущий экран; удаляем только известные сообщения.

Состояние можно хранить в SQLite на отдельном Docker volume. Сбой уборки
не повторяет отправку уже доставленного экрана. Неизвестная история чата
не перебирается по предполагаемым message_id.
"""
import json
import logging
import os
import sqlite3
from pathlib import Path
from urllib.error import HTTPError

LOG = logging.getLogger('pushok.bot.navigation')


class Navigation:
    def __init__(self, call, path=None):
        self.call = call
        self.states = {}
        self.db = None
        if path:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.db = sqlite3.connect(target, timeout=5)
            os.chmod(target, 0o600)
            self.db.execute('CREATE TABLE IF NOT EXISTS chat_navigation(chat_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            self.db.commit()

    def state(self, chat):
        if self.db:
            row = self.db.execute('SELECT payload FROM chat_navigation WHERE chat_id=?', (chat,)).fetchone()
            if row:
                return json.loads(row[0])
        return self.states.get(chat, {'welcome': None, 'current': None, 'kind': 'text', 'garbage': []}).copy()

    def save(self, chat, state):
        if self.db:
            with self.db:
                self.db.execute('INSERT INTO chat_navigation VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET payload=excluded.payload',
                                (chat, json.dumps(state)))
        else:
            self.states[chat] = state.copy()

    @staticmethod
    def description(exc):
        try:
            return json.loads(exc.read(8192)).get('description', '').lower()
        except (ValueError, OSError, AttributeError):
            return ''

    def discard(self, chat, state, mid):
        if type(mid) is int and mid > 0 and mid not in (state['welcome'], state['current']) and mid not in state['garbage']:
            state['garbage'].append(mid)
            self.save(chat, state)

    def cleanup(self, token, chat):
        state = self.state(chat)
        for mid in state['garbage'][:20]:
            if mid in (state['welcome'], state['current']):
                state['garbage'].remove(mid)
                continue
            try:
                self.call(token, 'deleteMessage', {'chat_id': chat, 'message_id': mid})
            except HTTPError as exc:
                description = self.description(exc)
                if exc.code != 400 or not any(x in description for x in ('message to delete not found', "message can't be deleted", 'message cannot be deleted')):
                    break
                LOG.info('Telegram не разрешил удалить старое сообщение; продолжаем без повторной отправки')
            except (OSError, RuntimeError, ValueError):
                break
            state['garbage'].remove(mid)
        self.save(chat, state)

    def cleanup_pending(self, token):
        if self.db:
            chats = [r[0] for r in self.db.execute("SELECT chat_id FROM chat_navigation WHERE json_extract(payload,'$.garbage[0]') IS NOT NULL LIMIT 20")]
        else:
            chats = [cid for cid, state in self.states.items() if state['garbage']][:20]
        for chat in chats:
            self.cleanup(token, chat)

    def incoming(self, token, update):
        message = update.get('message') or {}
        callback = update.get('callback_query') or {}
        source = callback.get('message') or message
        chat = source.get('chat') or {}
        actor = (callback or message).get('from') or {}
        if chat.get('type') != 'private' or type(actor.get('id')) is not int or actor['id'] != chat.get('id'):
            return
        cid = chat['id']
        state = self.state(cid)
        if message:
            # Вызывается только после успешной обработки/сохранения платежа.
            self.discard(cid, state, message.get('message_id'))
        else:
            bot_id = token.split(':', 1)[0]
            author = source.get('from') or {}
            if bot_id.isdigit() and author.get('id') == int(bot_id) and author.get('is_bot') is True and source.get('text') and not source.get('invoice'):
                self.discard(cid, state, source.get('message_id'))
        self.cleanup(token, cid)

    def welcome(self, token, payload):
        chat = payload['chat_id']
        state = self.state(chat)
        data = {'chat_id': chat, 'text': payload['text']}
        if state['welcome']:
            try:
                self.call(token, 'editMessageText', dict(data, message_id=state['welcome']))
                return
            except HTTPError as exc:
                description = self.description(exc)
                if exc.code == 400 and 'message is not modified' in description:
                    return
                if exc.code != 400 or not any(x in description for x in ('message to edit not found', "message can't be edited")):
                    raise
        old = state['welcome']
        result = self.call(token, 'sendMessage', data)
        state['welcome'] = result['message_id']
        self.save(chat, state)
        self.discard(chat, state, old)
        self.cleanup(token, chat)

    def screen(self, token, payload, *, force_new=False, method='sendMessage', **kwargs):
        chat = payload.get('chat_id')
        if type(chat) is not int or chat <= 0:
            return self.call(token, method, payload, **kwargs)
        state = self.state(chat)
        old = state['current']
        if method == 'sendMessage' and old and old != state['welcome'] and state['kind'] == 'text' and not force_new:
            try:
                result = self.call(token, 'editMessageText', dict(payload, message_id=old), **kwargs)
                self.cleanup(token, chat)
                return result
            except HTTPError as exc:
                description = self.description(exc)
                if exc.code == 400 and 'message is not modified' in description:
                    self.cleanup(token, chat)
                    return {'message_id': old}
                if exc.code != 400 or not any(x in description for x in ('message to edit not found', "message can't be edited", 'message is not a text message')):
                    raise
        result = self.call(token, method, payload, **kwargs)
        state['current'] = result['message_id']
        state['kind'] = 'invoice' if method == 'sendInvoice' else 'text'
        self.save(chat, state)
        self.discard(chat, state, old)
        self.cleanup(token, chat)
        return result

    def managed_call(self, token, method, payload, **kwargs):
        if method in ('sendMessage', 'sendInvoice'):
            return self.screen(token, payload, method=method, **kwargs)
        return self.call(token, method, payload, **kwargs)

    def deliver(self, token, update, payload):
        cb = update.get('callback_query') or {}
        msg = update.get('message') or {}
        command = cb.get('data') or str(msg.get('text', '')).split(' ', 1)[0].split('@')[0].lstrip('/')
        if command == 'start':
            self.welcome(token, payload)
            payload = dict(payload, text='🐱 Главное меню. Выберите действие:')
        result = self.screen(token, payload, force_new=command == 'start')
        self.incoming(token, update)
        return result
