"""Собственный Contact, приватность и однократное ежегодное начисление в реальной БД."""
import os
import io
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import Mock, patch
from uuid import uuid4
from urllib.error import HTTPError
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
from app import verified_profiles as verified
from bot.verified_profiles import verification_response, birthday_from_chat, render_birthday

main = fixture.main


class BotVerification(unittest.TestCase):
    def contact(self, **changes):
        message = {'from': {'id': 123}, 'chat': {'id': 123, 'type': 'private'}, 'date': 100,
                   'contact': {'phone_number': '+79991234567', 'user_id': 123}}
        message.update(changes)
        return {'update_id': 42, 'message': message}

    def test_reject_forwarded_other_missing_and_group_contact(self):
        for changes in ({'forward_origin': {'type': 'user'}}, {'via_bot': {'id': 7}},
                        {'contact': {'phone_number': '+79991234567', 'user_id': 124}},
                        {'contact': {'phone_number': '+79991234567'}},
                        {'chat': {'id': -10, 'type': 'group'}}):
            api, telegram = Mock(), Mock()
            verification_response(self.contact(**changes), api, telegram)
            api.assert_not_called(); telegram.assert_not_called()

    def test_native_contact_proof_and_optional_telegram_birthday(self):
        api = Mock(return_value={'verified': True})
        telegram = Mock(return_value={'birthdate': {'day': 29, 'month': 2}})
        reply = verification_response(self.contact(), api, telegram)
        self.assertIn('Номер подтверждён', reply['text'])
        self.assertEqual(api.call_args.args[0], 'verification/contact')
        self.assertEqual(api.call_args.args[1]['birthday'], {'day': 29, 'month': 2, 'year': None})
        self.assertTrue(reply['reply_markup']['remove_keyboard'])
        telegram.side_effect = OSError('private')
        verification_response(self.contact(), api, telegram)
        self.assertIsNone(api.call_args.args[1]['birthday'])

    def test_bot_prompt_consent_and_birthdate_validation(self):
        api = Mock()
        reply = verification_response(self.contact(text='/start verify', contact=None), api, Mock())
        self.assertTrue(reply['reply_markup']['keyboard'][0][0]['request_contact'])
        self.assertIn('администратору', reply['text'])
        api.assert_called_once_with('verification/request', {'telegram_user_id': 123, 'birthday_opt_in': True})
        for value in ({'day': 31, 'month': 2}, {'day': True, 'month': 1}, {'day': 1, 'month': 1, 'year': 0}, {'day': 1, 'month': 1, 'year': 2999}):
            self.assertIsNone(birthday_from_chat({'birthdate': value}))
        self.assertNotIn('Подарок уже', render_birthday({'first_name': 'Маша', 'bonus_amount': 0}))
        self.assertIn('100 ₽', render_birthday({'first_name': 'Маша', 'bonus_amount': 10000}))


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен отдельный тестовый PostgreSQL')
class VerificationIntegration(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request = fixture.PostgresIntegration.request

    def setUp(self):
        self.uid, self.admin, self.other = uuid4(), uuid4(), uuid4()
        self.tg = int(self.uid.hex[:12], 16)
        self.update = int(self.uid.hex[12:24], 16)
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE verified_profiles SET birthday_opt_in=false')
            conn.execute('UPDATE birthday_settings SET congratulations_enabled=true,bonus_amount=0')
            conn.execute('UPDATE wallet_settings SET enabled=true')
            for uid, role in ((self.uid, 'advertiser'), (self.admin, 'admin'), (self.other, 'operator')):
                conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,%s,%s,'Тест')",
                             (uid, self.tg if uid == self.uid else int(uid.hex[:12], 16), role))
        self.actor = {'id': self.uid, 'telegram_user_id': self.tg, 'role': 'advertiser'}
        main.app.dependency_overrides[main.get_current_user] = lambda: self.actor
        self.addCleanup(main.app.dependency_overrides.clear)
        env = patch.dict(os.environ, {'BOT_INTERNAL_SECRET': 'f'*64, 'PUSHOK_TELEGRAM_TEST_ENVIRONMENT': '0'})
        env.start(); self.addCleanup(env.stop)
        self.headers = {'X-Pushok-Bot-Secret': 'f'*64, 'X-Pushok-Telegram-Environment': 'production'}

    def verify(self, birthday=None):
        self.request('POST', '/me/verification/request', json={'birthday_opt_in': True})
        return self.request('POST', '/internal/bot/verification/contact', headers=self.headers,
                            json=self.payload(birthday=birthday))

    def payload(self, **changes):
        data = dict(telegram_user_id=self.tg, contact_user_id=self.tg, chat_id=self.tg, private_chat=True,
                    forwarded=False, phone_number='+7 (999) 123-45-67', update_id=self.update,
                    message_date=int(time.time()), birthday=None)
        data.update(changes)
        return data

    def db(self, sql, args=()):
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            cursor = conn.execute(sql, args)
            return cursor.fetchall() if cursor.description else None

    def test_polling_bot_real_transport_verifies_contact_and_delivers_annual_gift(self):
        """Реальные Request/заголовки и API; тестовые Telegram updates и часы outbox."""
        from bot import main as bot
        birthday = verified.today()
        self.db('UPDATE birthday_settings SET bonus_amount=12500')
        messages, routes = [], []
        poll_clock = Mock(wraps=time)
        poll_clock.monotonic.return_value = 0.0
        poll_clock.sleep.side_effect = AssertionError('The polling fixture must not enter a transport retry')
        verify_update = {'update_id': self.update, 'message': {'from': {'id': self.tg},
                         'chat': {'id': self.tg, 'type': 'private'}, 'text': '/verify'}}

        def bridge(request, **kwargs):
            path = urlsplit(request.full_url).path
            self.assertTrue(path.startswith('/api/v1/internal/bot/'))
            routes.append(path)
            response = self.client.request(request.get_method(), path,
                content=request.data, headers=dict(request.header_items()))
            if not response.is_success:
                raise HTTPError(request.full_url, response.status_code, 'test response', {}, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        def telegram(token, method, payload, **kwargs):
            if method == 'getMe':
                return {'username': 'mtproxy_pushok_bot'}
            if method == 'getWebhookInfo':
                return {}
            if method == 'getUpdates':
                if payload['offset'] is None:
                    return [verify_update]
                if payload['offset'] == self.update+1:
                    # The user can send a new contact only after receiving the prompt.
                    # Prebuilding it before /verify crosses a second boundary races
                    # the server's legitimate message_date >= requested_at check.
                    self.assertTrue(any(button.get('request_contact') is True
                                        for message in messages if message.get('chat_id') == self.tg
                                        for row in message.get('reply_markup', {}).get('keyboard', [])
                                        for button in row))
                    intent = self.db('''SELECT floor(extract(epoch FROM requested_at))::bigint AS message_after
                        FROM phone_verification_requests WHERE user_id=%s AND telegram_environment='production' ''', (self.uid,))
                    self.assertEqual(len(intent), 1)
                    contact_date = int(time.time())
                    self.assertGreaterEqual(contact_date, intent[0]['message_after'])
                    # Advance only the bot's outbox scheduler, without real sleeps
                    # or weakening the API's wall-clock contact freshness guard.
                    poll_clock.monotonic.return_value = 30.0
                    return [{'update_id': self.update+1, 'message': {'from': {'id': self.tg},
                        'chat': {'id': self.tg, 'type': 'private'}, 'date': contact_date,
                        'contact': {'user_id': self.tg, 'phone_number': '+79991234567'}}}]
                self.assertEqual(payload['offset'], self.update+2)
                raise KeyboardInterrupt  # Закончить после полного прохода polling/outbox.
            if method == 'getChat':
                self.assertEqual(kwargs['timeout'], 3)
                return {'birthdate': {'day': birthday.day, 'month': birthday.month}}
            if method == 'sendMessage':
                messages.append(payload)
                return {'message_id': len(messages)}
            self.fail('Unexpected Telegram method: '+method)

        self.db('INSERT INTO support_bot_contacts(telegram_id,bot_id) VALUES(%s,123)',(self.tg,))
        def support_send(key,method,payload):
            self.assertEqual(key,'123:fixture')
            return telegram(key,method,payload)
        with patch('app.service_messages.support.credential',return_value={'token':'123:fixture','username':'server_pushok_bot'}), \
             patch('app.service_messages.verified_identity',return_value=123), \
             patch('app.service_messages.support.bot_call',side_effect=support_send), \
             patch.dict(os.environ, {'DATABASE_URL':self.dsn,'TELEGRAM_BOT_TOKEN': 'local-fixture',
                                    'TELEGRAM_BOT_USERNAME': 'mtproxy_pushok_bot'}), \
             patch.object(bot, 'urlopen', side_effect=bridge), \
             patch.object(bot, 'raw_telegram_call', side_effect=telegram), \
             patch.object(bot, 'telegram_call', side_effect=telegram), \
             patch.object(bot, 'time', poll_clock), \
             patch.object(bot, 'Navigation'), patch.object(bot, '_navigation', None), \
             patch.object(bot, 'deliver_operator_outbox'), patch.object(bot, 'deliver_advertiser_notices'), \
             patch.object(bot, 'deliver_lifecycle_notices'):
            with self.assertRaises(KeyboardInterrupt):
                bot.run()
        self.assertTrue(self.request('GET', '/me/verification')['phone_verified'])
        self.assertEqual(self.db('SELECT bonus_amount FROM birthday_rewards WHERE user_id=%s', (self.uid,))[0]['bonus_amount'], 12500)
        self.assertEqual(len([m for m in messages if 'с днём рождения!' in m['text']]), 1)
        for route in ('verification/request', 'verification/contact', 'birthday/outbox/claim', 'birthday/outbox/finish'):
            self.assertIn('/api/v1/internal/bot/'+route, routes)
        self.assertIsNotNone(self.db('''SELECT delivered_at FROM birthday_notice_outbox o
            JOIN birthday_rewards r ON r.id=o.reward_id WHERE r.user_id=%s''', (self.uid,))[0]['delivered_at'])

    def test_contact_requires_intent_secret_environment_and_matching_owner(self):
        path = '/internal/bot/verification/contact'
        self.request('POST', path, 401, json=self.payload())
        self.request('POST', path, 403, json=self.payload(), headers={**self.headers, 'X-Pushok-Telegram-Environment': 'test'})
        self.request('POST', path, 409, json=self.payload(), headers=self.headers)
        self.request('POST', '/me/verification/request', json={'birthday_opt_in': True})
        for changes in ({'contact_user_id': self.tg+1}, {'chat_id': self.tg+1}, {'private_chat': False},
                        {'forwarded': True}, {'phone_number': '555 abc 12345'}):
            self.request('POST', path, 400, json=self.payload(**changes), headers=self.headers)
        intent = self.db('''SELECT floor(extract(epoch FROM requested_at))::bigint AS message_after
            FROM phone_verification_requests WHERE user_id=%s AND telegram_environment='production' ''', (self.uid,))[0]
        self.request('POST', path, 409, json=self.payload(message_date=intent['message_after']-1), headers=self.headers)
        self.request('POST', path, 409, json=self.payload(message_date=int(time.time())+60), headers=self.headers)
        self.assertFalse(self.request('GET', '/me/verification')['phone_verified'])
        self.verify(birthday={'day': 29, 'month': 2})
        self.request('POST', path, json=self.payload(), headers=self.headers)  # повтор доставки Telegram
        data = self.request('GET', '/me/verification')
        self.assertEqual(data['phone_number'], '+79991234567')
        self.assertTrue(data['phone_verified'])
        self.assertEqual(data['birthday_source'], 'telegram')
        self.assertIsNone(data['birthday']['year'])
        self.assertEqual(len(self.db('SELECT * FROM verified_profiles WHERE user_id=%s', (self.uid,))), 1)
        self.assertEqual(self.db('SELECT * FROM phone_verification_requests WHERE user_id=%s', (self.uid,)), [])

    def test_expired_request_and_no_frontend_phone_write(self):
        self.request('POST', '/me/verification/request', 422, json={'phone_number': '+79991234567'})
        self.request('POST', '/me/verification/request', json={'birthday_opt_in': False})
        self.db("UPDATE phone_verification_requests SET expires_at=now()-interval '1 second' WHERE user_id=%s", (self.uid,))
        self.request('POST', '/internal/bot/verification/contact', 409, headers=self.headers, json=self.payload())
        self.request('PUT', '/me/birthday', 422, json={'birthday': None, 'birthday_opt_in': False, 'phone_verified': True})

    def test_new_contact_after_telegram_update_sequence_reset(self):
        self.verify()
        self.request('POST', '/me/verification/request', json={'birthday_opt_in': False})
        self.request('POST', '/internal/bot/verification/contact', headers=self.headers,
                     json=self.payload(update_id=self.update-1, phone_number='+79991234567'))
        self.assertEqual(self.request('GET', '/me/verification')['phone_number'], '+79991234567')

    def test_bot_fallback_preserves_opt_out_and_test_phone_is_separate(self):
        self.request('POST', '/me/verification/request', json={'birthday_opt_in': False})
        intent = self.request('POST', '/internal/bot/verification/request', headers=self.headers,
                              json={'telegram_user_id': self.tg, 'birthday_opt_in': True})
        self.assertFalse(intent['birthday_opt_in'])
        self.request('POST', '/internal/bot/verification/contact', headers=self.headers,
                     json=self.payload(birthday={'day': 1, 'month': 1}))
        self.assertIsNone(self.request('GET', '/me/verification')['birthday'])
        with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT': '1'}):
            self.assertFalse(self.request('GET', '/me/verification')['phone_verified'])
            self.assertFalse(self.request('GET', f'/users/{self.uid}/profile')['user']['phone_verified'])
            self.request('PUT', '/me/birthday', json={'birthday': {'day': 2, 'month': 2}, 'birthday_opt_in': True})
            self.assertFalse(self.request('GET', '/me/verification')['phone_verified'])
        self.assertTrue(self.request('GET', '/me/verification')['phone_verified'])
        self.assertIsNone(self.request('GET', '/me/verification')['birthday'])

    def test_failed_credit_rolls_back_year_and_blocked_user_gets_nothing(self):
        self.verify(birthday={'day': 4, 'month': 4})
        self.db('UPDATE birthday_settings SET bonus_amount=10000')
        with self.assertRaises(RuntimeError), patch.object(verified, 'post', side_effect=RuntimeError('offline')):
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                verified.generate_birthdays(conn, date(2027, 4, 4))
        self.assertEqual(self.db('SELECT * FROM birthday_rewards WHERE user_id=%s', (self.uid,)), [])
        self.db('UPDATE users SET blocked_at=now() WHERE id=%s', (self.uid,))
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            verified.generate_birthdays(conn, date(2027, 4, 4))
        self.assertEqual(self.db('SELECT * FROM birthday_rewards WHERE user_id=%s', (self.uid,)), [])

    def test_private_details_admin_only_and_public_badge(self):
        self.verify(birthday={'day': 15, 'month': 5, 'year': 1990})
        path = f'/users/{self.uid}/profile'
        own = self.request('GET', path)
        self.assertTrue(own['user']['phone_verified'])
        self.assertEqual(own['verification']['phone_number'], '+79991234567')
        self.actor = {'id': self.other, 'role': 'operator'}
        operator = self.request('GET', path)
        self.assertTrue(operator['user']['phone_verified'])
        self.assertNotIn('verification', operator)
        directory = self.request('GET', '/admin/users?query='+str(self.tg))
        self.assertTrue(directory['items'][0]['phone_verified'])
        self.assertNotIn('79991234567', str(directory))
        self.request('GET', '/admin/birthday-settings', 403)
        self.actor = {'id': self.admin, 'role': 'admin'}
        self.assertEqual(self.request('GET', path)['verification']['birthday']['year'], 1990)
        self.request('PUT', '/admin/birthday-settings', json={'congratulations_enabled': True, 'bonus_amount': 10000})

    def test_manual_date_validation_removal_and_opt_out(self):
        for birthday in ({'day': 31, 'month': 2}, {'day': 29, 'month': 2, 'year': 2025},
                         {'day': 1, 'month': 1, 'year': 2199}, {'day': True, 'month': 1}):
            self.request('PUT', '/me/birthday', 422, json={'birthday': birthday, 'birthday_opt_in': True})
        result = self.request('PUT', '/me/birthday', json={'birthday': {'day': 2, 'month': 3}, 'birthday_opt_in': True})
        self.assertFalse(result['phone_verified'])
        self.verify(birthday={'day': 5, 'month': 6})
        data = self.request('GET', '/me/verification')
        self.assertEqual(data['birthday']['day'], 2)  # Не затирать исправленную пользователем дату.
        self.assertEqual(data['birthday_source'], 'manual')
        self.request('PUT', '/me/birthday',409,json={'birthday': None, 'birthday_opt_in': False})
        removed = self.request('PUT', '/me/birthday', json={'birthday':data['birthday'], 'birthday_opt_in': False})
        self.assertTrue(removed['phone_verified'])
        self.assertEqual(removed['birthday'],data['birthday'])
        self.assertFalse(removed['birthday_opt_in'])

    def test_profile_import_uses_own_private_chat_and_optional_year(self):
        self.verify()
        reply = {'ok': True, 'result': {'id': self.tg, 'type': 'private',
                 'birthdate': {'day': 29, 'month': 2}}}
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(reply).encode())
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fixture-token'}), \
             patch.object(verified, 'build_opener', return_value=opener):
            result = self.request('POST', '/me/birthday/sync')
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data), {'chat_id': self.tg})
        self.assertTrue(request.full_url.endswith('/botfixture-token/getChat'))
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 3)
        self.assertEqual(result['birthday'], {'day': 29, 'month': 2, 'year': None})
        self.assertEqual(result['birthday_source'], 'telegram')
        self.assertEqual(result['birthday_sync_state'], 'imported')
        self.assertTrue(result['phone_verified'])

    def test_missing_birthday_and_failed_lookup_are_distinct_and_retryable(self):
        self.verify()
        with patch.object(verified, 'telegram_birthday', return_value=None) as lookup:
            result = self.request('POST', '/me/birthday/sync')
            self.assertEqual(result['birthday_sync_state'], 'unavailable')
            self.assertIsNone(result['birthday'])
            self.request('POST', '/me/birthday/sync')
            lookup.assert_called_once_with(self.tg)  # Сервер ограничивает повторы.
        self.db("UPDATE verified_profiles SET birthday_sync_checked_at=now()-interval '2 minutes' WHERE user_id=%s", (self.uid,))
        with patch.object(verified, 'telegram_birthday', side_effect=TimeoutError('fixture')):
            result = self.request('POST', '/me/birthday/sync')
            self.assertEqual(result['birthday_sync_state'], 'failed')
            self.assertTrue(result['phone_verified'])
        self.db("UPDATE verified_profiles SET birthday_sync_checked_at=now()-interval '2 minutes' WHERE user_id=%s", (self.uid,))
        with patch.object(verified, 'telegram_birthday', return_value=verified.Birthday(day=3, month=4)):
            self.assertEqual(self.request('POST', '/me/birthday/sync')['birthday']['day'], 3)

    def test_import_does_not_read_unverified_opted_out_other_or_test_profile(self):
        with patch.object(verified, 'telegram_birthday') as lookup:
            self.request('POST', '/me/birthday/sync')
            self.verify()
            self.actor = {'id': self.other, 'telegram_user_id': self.tg+1, 'role': 'operator'}
            self.request('POST', '/me/birthday/sync')
            self.actor = {'id': self.uid, 'telegram_user_id': self.tg, 'role': 'advertiser'}
            with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT': '1'}):
                self.request('POST', '/me/birthday/sync')
            self.db('UPDATE verified_profiles SET birthday_opt_in=false WHERE user_id=%s', (self.uid,))
            self.request('POST', '/me/birthday/sync')
            lookup.assert_not_called()

    def test_late_import_cannot_overwrite_manual_edit_or_restore_deleted_birthday(self):
        self.verify()
        def while_fetching(uid):
            self.assertEqual(self.request('POST', '/me/birthday/sync')['birthday_sync_state'], 'pending')
            self.request('PUT', '/me/birthday', json={'birthday': {'day': 10, 'month': 5}, 'birthday_opt_in': True})
            return verified.Birthday(day=3, month=4)
        with patch.object(verified, 'telegram_birthday', side_effect=while_fetching) as lookup:
            result = self.request('POST', '/me/birthday/sync')
            self.assertEqual(result['birthday'], {'day': 10, 'month': 5, 'year': None})
            self.assertEqual(result['birthday_source'], 'manual')
            lookup.assert_called_once_with(self.tg)
        self.actor['is_owner']=True  # Только владелец вправе исправить сохранённое.
        self.request('PUT', '/me/birthday', json={'birthday': None, 'birthday_opt_in': True})
        with patch.object(verified, 'telegram_birthday') as lookup:
            result = self.request('POST', '/me/birthday/sync')
            self.assertIsNone(result['birthday'])
            self.assertEqual(result['birthday_sync_state'], 'manual')
            lookup.assert_not_called()
        self.update += 1
        self.verify(birthday={'day': 3, 'month': 4})
        self.assertIsNone(self.request('GET', '/me/verification')['birthday'])

    def test_wrong_chat_or_invalid_date_does_not_import(self):
        self.verify()
        for chat in ({'id': self.tg+1, 'type': 'private', 'birthdate': {'day': 1, 'month': 1}},
                     {'id': self.tg, 'type': 'group', 'birthdate': {'day': 1, 'month': 1}},
                     {'id': self.tg, 'type': 'private', 'birthdate': {'day': 31, 'month': 2}}):
            self.db('UPDATE verified_profiles SET birthday_sync_checked_at=NULL WHERE user_id=%s', (self.uid,))
            opener = Mock()
            opener.open.return_value = io.BytesIO(json.dumps({'ok': True, 'result': chat}).encode())
            with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fixture-token'}), \
                 patch.object(verified, 'build_opener', return_value=opener):
                result = self.request('POST', '/me/birthday/sync')
            self.assertEqual(result['birthday_sync_state'], 'failed')
            self.assertIsNone(result['birthday'])

    def test_annual_bonus_atomic_concurrency_date_changes_and_next_year(self):
        self.verify(birthday={'day': 29, 'month': 2})
        self.db('UPDATE birthday_settings SET bonus_amount=10000')
        def run(current):
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                verified.generate_birthdays(conn, current)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, [date(2027, 2, 28)]*4))
        entries = self.db("SELECT delta FROM wallet_entries WHERE user_id=%s AND operation_key LIKE 'birthday:%%'", (self.uid,))
        self.assertEqual(entries, [{'delta': 10000}])
        self.assertEqual(self.db("SELECT balance FROM wallet_accounts WHERE user_id=%s AND currency='BONUS'", (self.uid,))[0]['balance'], 10000)
        self.actor['is_owner']=True
        self.request('PUT', '/me/birthday', json={'birthday': {'day': 3, 'month': 3}, 'birthday_opt_in': True})
        run(date(2027, 3, 3))
        self.assertEqual(len(self.db('SELECT * FROM birthday_rewards WHERE user_id=%s', (self.uid,))), 1)
        run(date(2028, 3, 3))
        self.assertEqual(self.db("SELECT balance FROM wallet_accounts WHERE user_id=%s AND currency='BONUS'", (self.uid,))[0]['balance'], 20000)

    def test_disabled_wallet_unverified_opted_out_and_leap_year(self):
        self.request('PUT', '/me/birthday', json={'birthday': {'day': 29, 'month': 2}, 'birthday_opt_in': True})
        self.db('UPDATE birthday_settings SET bonus_amount=10000')
        def run(current):
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                verified.generate_birthdays(conn, current)
        run(date(2028, 2, 29))
        self.assertEqual(self.db('SELECT * FROM birthday_rewards WHERE user_id=%s', (self.uid,)), [])
        self.verify()
        run(date(2028, 2, 28))
        self.assertEqual(self.db('SELECT * FROM birthday_rewards WHERE user_id=%s', (self.uid,)), [])
        self.db('UPDATE verified_profiles SET birthday_opt_in=false WHERE user_id=%s', (self.uid,))
        run(date(2028, 2, 29))
        self.assertEqual(self.db('SELECT * FROM birthday_rewards WHERE user_id=%s', (self.uid,)), [])
        self.db('UPDATE verified_profiles SET birthday_opt_in=true WHERE user_id=%s', (self.uid,))
        self.db('UPDATE wallet_settings SET enabled=false')
        run(date(2028, 2, 29))
        self.assertEqual(self.db('SELECT bonus_amount,entry_id FROM birthday_rewards WHERE user_id=%s', (self.uid,)), [{'bonus_amount': 0, 'entry_id': None}])

    def test_outbox_retry_ack_and_default_no_money(self):
        current = verified.today()
        self.verify(birthday={'day': current.day, 'month': current.month})
        path = '/internal/bot/birthday/outbox/'
        claim = self.request('POST', path+'claim', headers=self.headers, json={})
        mine = next(x for x in claim if x['telegram_user_id'] == self.tg)
        self.assertEqual(mine['bonus_amount'], 0)
        with patch.dict(os.environ, {'PUSHOK_TELEGRAM_TEST_ENVIRONMENT': '1'}):
            with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
                self.assertEqual(verified.claim_birthdays(conn), [])
        self.assertFalse(any(x['telegram_user_id'] == self.tg for x in self.request('POST', path+'claim', headers=self.headers, json={})))
        wrong = {'notice_id': mine['notice_id'], 'claim_token': str(uuid4()), 'permanent_failure': False}
        self.assertFalse(self.request('POST', path+'finish', headers=self.headers, json=wrong)['acknowledged'])
        self.db("UPDATE birthday_notice_outbox SET lease_until=now()-interval '1 second',next_attempt_at=now()-interval '1 second' WHERE id=%s", (mine['notice_id'],))
        retry = next(x for x in self.request('POST', path+'claim', headers=self.headers, json={}) if x['telegram_user_id'] == self.tg)
        self.assertNotEqual(retry['claim_token'], mine['claim_token'])
        self.assertTrue(self.request('POST', path+'finish', headers=self.headers, json={**wrong, 'claim_token': retry['claim_token']})['acknowledged'])
        self.assertFalse(any(x['telegram_user_id'] == self.tg for x in self.request('POST', path+'claim', headers=self.headers, json={})))
        self.assertEqual(self.db('SELECT * FROM wallet_entries WHERE user_id=%s', (self.uid,)), [])
