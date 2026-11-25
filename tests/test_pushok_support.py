import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_postgres as fixture
from app import pushok_support as support


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен тестовый PostgreSQL')
class SupportChat(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)

    def test_own_chat_escalation_and_delivery(self):
        actor = {'role': 'advertiser', 'telegram_user_id': 8452001}
        uid = actor['telegram_user_id']
        app = FastAPI()
        app.include_router(support.router_for(lambda: self.dsn, lambda: actor))
        client = TestClient(app)
        base = '/api/v1/support/cases'
        text = {'text': 'Предлагаю недельный отчёт', 'category': 'idea', 'request_id': str(uuid4())}
        new = client.post(base, json=text)
        self.assertEqual(new.status_code, 200, new.text)
        cid = new.json()['id']
        self.assertEqual(client.post(base, json=text).json()['id'], cid)
        case = f'{base}/{cid}'
        self.assertEqual(client.get(case).json()['state'], 'waiting_staff')
        self.assertEqual(len(client.get(case).json()['messages']), 2)
        self.assertEqual(client.get(case).headers['cache-control'], 'no-store')
        actor['telegram_user_id'] += 1
        self.assertEqual(client.get(case).status_code, 404)
        self.assertEqual(client.get(base).json()['items'], [])
        self.assertEqual(client.get(base+'?team=true').status_code, 403)
        self.assertEqual(client.post(base, json=dict(text, request_id=str(uuid4()), case_id=cid)).status_code, 404)
        response = {'text':'Учли предложение.', 'request_id':str(uuid4()), 'close':True}
        self.assertEqual(client.post(case+'/staff', json=response).status_code, 403)
        with support.connection(self.dsn) as db, patch.object(support, 'bot_call') as send:
            msg = {'message_id':15,'chat':{'id':uid,'type':'group'},'from':{'id':uid},'text':'/start'}
            support.handle_update(db,self.dsn,'fixture',{'message':msg})
            send.assert_not_called()
            msg['chat']['type']='private'
            support.handle_update(db,self.dsn,'fixture',{'message':msg})
            send.assert_called_once()
            self.assertEqual(send.call_args.args[2]['parse_mode'], 'HTML')
            self.assertEqual(support.format_body('**Тариф** {x} <a> __руб.__'), '<b>Тариф</b> {x} &lt;a&gt; <i>руб.</i>')
            msg.update(message_id=16,text='/idea Быстрый доступ')
            support.handle_update(db,self.dsn,'fixture',{'message':msg})
            support.handle_update(db,self.dsn,'fixture',{'message':msg})
            saved=db.execute('SELECT current_case FROM support_bot_contacts WHERE telegram_id=%s',(uid,)).fetchone()['current_case']
            self.assertEqual(len(db.execute('SELECT id FROM support_chat_messages WHERE conversation_id=%s',(saved,)).fetchall()),2)
        actor['role'] = 'operator'
        self.assertEqual(client.get(case).status_code, 200)
        for _ in range(2):
            self.assertEqual(client.post(case+'/staff',json=response).status_code, 200)
        actor.update(role='advertiser',telegram_user_id=uid)
        result=client.get(case).json()
        self.assertEqual((result['state'],result['messages'][-1]['sender']), ('closed','staff'))
        with support.connection(self.dsn) as db:
            self.assertEqual(len(db.execute('SELECT message_id FROM support_bot_outbox WHERE telegram_id=%s',(uid,)).fetchall()),1)
