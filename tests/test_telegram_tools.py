import json
import unittest
from unittest.mock import patch
from uuid import uuid4
import zlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_postgres as fixture
from app import editorial, flow_check_report, pushok_support as support, subscription_widgets as widgets


class Projection(unittest.TestCase):
    def test_widget_private_fields_and_size(self):
        data=widgets.snapshot({'is_premium':True,'state':'active','received':0,'sent':2048,'endpoint_ip':'secret'})
        self.assertIn('Premium',data[0])
        self.assertIn('бессрочный',data[1])
        self.assertNotIn('secret',json.dumps(data))
        self.assertLess(len(zlib.compress(widgets.HTML.encode(),9)),4097)

    def test_flow_report_is_only_a_fixed_result(self):
        report=flow_check_report.describe('flowcheck_https_1780000000','en')
        self.assertIn('HTTPS',report)
        self.assertIsNone(flow_check_report.describe('flowcheck_10.0.0.1_1780000000','ru'))
        self.assertEqual(support.lang.guided(report,[]),('probe_failed',True))


@unittest.skipUnless(fixture._approved_test_postgres(),'isolated PostgreSQL')
class TelegramWorkflows(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)

    def test_revision_checked_publication_is_not_repeated(self):
        actor={'id':str(uuid4()),'telegram_user_id':429108,'role':'admin','is_owner':True}
        app=FastAPI()
        app.include_router(editorial.router_for(lambda:self.dsn,lambda:actor))
        client=TestClient(app);url='/api/v1/editorial';did=str(uuid4());path=url+'/'+did
        self.assertEqual(client.post(url,json={'id':did,'product':'flow'}).status_code,200)
        self.assertEqual(set(client.get(path).json()['variants']),{'ru','en','es'})
        change={'revision':1,'text':'**Welcome**','locale':'en','art':'none'}
        self.assertEqual(client.put(path,json=change).json(),{'revision':2})
        self.assertEqual(client.put(path,json=change).status_code,409)
        actor['is_owner']=False
        self.assertEqual(client.get(path).status_code,403)
        actor['is_owner']=True
        def lookup(_,method,payload):
            return {'id':-777,'type':'channel'} if method=='getChat' else {'status':'creator'}
        with patch.object(editorial,'is_test_environment',return_value=False),patch.object(editorial.service_messages,'verify_sender'),patch.object(editorial.service_messages,'telegram_call',side_effect=lookup),patch.object(editorial.requests,'Session') as session,patch.object(editorial,'bot_api_url',return_value='https://local.invalid/'):
            post=session.return_value.__enter__.return_value.post
            post.return_value.json.return_value={'ok':True,'result':{'chat':{'id':-777},'message_id':82}}
            payload={'channel':'@pushok_channel','locale':'en','revision':2}
            self.assertEqual(client.post(path+'/publish',json=payload).json()['message_id'],82)
            self.assertEqual(client.post(path+'/publish',json=payload).status_code,409)
            self.assertEqual(post.call_count,1)
            self.assertEqual(post.call_args.kwargs['data']['text'],'<b>Welcome</b>')

    def test_existing_widget_updates_and_recovers_only_for_its_owner(self):
        from fastapi import HTTPException
        uid=2848211
        with support.connection(self.dsn) as db:
            subject=db.execute('INSERT INTO users(telegram_user_id) VALUES(%s) RETURNING id',(uid,)).fetchone()['id']
        accounts={str(subject):{'tariff_name':'Trial','state':'active','received':200,'sent':50}}
        with patch.object(widgets,'is_test_environment',return_value=False),patch.object(widgets,'observations',return_value={'accounts':accounts}),patch.object(widgets.telegram,'verify_sender'),patch.object(widgets.telegram,'telegram_call') as send:
            send.return_value={'chat':{'id':uid},'message_id':24}
            widgets.refresh(self.dsn,uid)
            self.assertEqual(send.call_args.args[1],'sendWidget')
            self.assertNotIn('public_data',send.call_args.args[2])
            accounts[str(subject)]['received']=400
            send.return_value=True
            widgets.refresh(self.dsn,uid)
            self.assertEqual(send.call_args.args[1],'editMessageWidgetData')
            self.assertEqual(send.call_args.args[2]['message_id'],24)
            send.side_effect=[HTTPException(404,'widget_message_missing'),{'chat':{'id':uid},'message_id':25}]
            widgets.refresh(self.dsn,uid)
            self.assertEqual(send.call_args.args[1],'sendWidget')
            with support.connection(self.dsn) as db:
                row=db.execute('SELECT message_id FROM subscription_widgets WHERE telegram_id=%s',(uid,)).fetchone()
            self.assertEqual(row['message_id'],25)

    def test_thread_bound_to_user_and_bot(self):
        uid=2982772;other=22981921;key='222:fixture'
        with support.connection(self.dsn) as db:
            def handle(mid,text,thread=None):
                message={'message_id':mid,'text':text,'chat':{'id':uid,'type':'private'},'from':{'id':uid}}
                if thread:message['message_thread_id']=thread
                support.handle_update(db,self.dsn,key,{'message':message});db.commit()
            with patch.object(support,'bot_call',side_effect=lambda k,m,p:{'message_thread_id':776} if m=='createForumTopic' else {}):
                handle(1,'/idea Тема А')
                handle(2,'/new')
                handle(3,'Хочу поиск',776)
            row=db.execute('SELECT case_id FROM support_telegram_topics WHERE chat_id=%s',(uid,)).fetchone()
            app=FastAPI();actor={'role':'advertiser','telegram_user_id':uid}
            app.include_router(support.router_for(lambda:self.dsn,lambda:actor))
            with patch.object(support,'credential',return_value={'token':key,'username':'server_pushok_bot'}):
                client=TestClient(app);path='/api/v1/support/cases/'+str(row['case_id'])
                data=client.get(path).json()
                self.assertEqual(data['continue_url'],'https://t.me/server_pushok_bot/776')
                self.assertEqual(len(data['messages']),4)
                actor['telegram_user_id']=other
                self.assertEqual(client.get(path).status_code,404)
