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


    def test_welcome_falls_back_and_buttons_route_the_next_message(self):
        uid=8452991
        chat={'type':'private','id':uid}
        key='111:'+'k'*30
        def update(text,mid):
            return {'message':{'chat':chat,'from':{'id':uid},'text':text,'message_id':mid}}
        with support.connection(self.dsn) as db, patch.object(support,'bot_call') as tg:
            tg.side_effect=[support.PhotoRejected(),True]
            support.handle_update(db,self.dsn,key,update('/start',20))
            photo,text=[c.args for c in tg.call_args_list]
            self.assertEqual((photo[1],text[1]),('sendPhoto','sendMessage'))
            self.assertEqual(photo[2]['caption'],text[2]['text'])
            self.assertEqual(text[2]['reply_markup']['inline_keyboard'][1][0]['callback_data'],'support:idea')
            tg.side_effect=None
            callback={'id':'test','data':'support:idea','from':{'id':uid},'message':{'chat':chat,'message_id':21}}
            support.handle_update(db,self.dsn,key,{'callback_query':callback})
            self.assertEqual(tg.call_args_list[-2].args[1],'answerCallbackQuery')
            support.handle_update(db,self.dsn,key,update('Хочу предложить поиск',22))
            self.assertIn('Обращение №',tg.call_args.args[2]['text'])
            case=db.execute('SELECT category,state FROM support_conversations WHERE telegram_id=%s',(uid,)).fetchone()
            self.assertEqual(case,{'category':'idea','state':'waiting_staff'})
            support.handle_update(db,self.dsn,key,update('/human',23))
            support.handle_update(db,self.dsn,key,update('Проверьте подписку',24))
            case=db.execute('SELECT category,state FROM support_conversations WHERE telegram_id=%s ORDER BY id DESC LIMIT 1',(uid,)).fetchone()
            self.assertEqual(case,{'category':'idea','state':'waiting_staff'})
            self.assertEqual(db.execute('SELECT count(*) AS n FROM support_conversations WHERE telegram_id=%s',(uid,)).fetchone()['n'],1)

    def test_shared_language_preferences_and_handoff(self):
        uid=8452998
        actor={'role':'advertiser','telegram_user_id':uid}
        app=FastAPI()
        app.include_router(support.router_for(lambda:self.dsn,lambda:actor))
        client=TestClient(app)
        url='/api/v1/support/language'
        self.assertEqual(client.get(url).json()['preference'],'auto')
        self.assertEqual(client.put(url,json={'preference':'untrusted'}).status_code,422)
        self.assertEqual(client.put(url,json={'preference':'es'}).status_code,200)
        with support.connection(self.dsn) as db, patch.object(support,'bot_call') as send:
            def message(body,mid):
                support.handle_update(db,self.dsn,'123:fixture',{'message':{'message_id':mid,
                    'from':{'id':uid,'language_code':'en-US'},'chat':{'id':uid,'type':'private'},'text':body}})
            message('/start',10)
            self.assertIn('Hola',send.call_args.args[2]['caption'])
            message('Consumo de datos',11)
            self.assertIn('En Flow para Android',send.call_args.args[2]['text'])
            cid=db.execute('SELECT current_case FROM support_bot_contacts WHERE telegram_id=%s',(uid,)).fetchone()['current_case']
            message('/human',12)
            message('/human',12)
            case=db.execute('SELECT state,reply_locale FROM support_conversations WHERE id=%s',(cid,)).fetchone()
            self.assertEqual(case,{'state':'waiting_staff','reply_locale':'es'})
            self.assertEqual(len(client.get(f'/api/v1/support/cases/{cid}').json()['messages']),4)
            message('/language en',13)
        self.assertEqual(client.get(url).json()['preference'],'en')
        actor['telegram_user_id']+=1
        self.assertEqual(client.get(url).json()['preference'],'auto')


class BotSettings(unittest.TestCase):
    def test_guides_and_fallback_in_supported_languages(self):
        for loc,question,phrase in [('en','data usage','Settings'),('es','mis datos: consumo','Ajustes'),('ru','расход трафика','Настройки')]:
            with patch.object(support.ai,'active',return_value=None):
                self.assertIn(phrase,support.answer('question',question,[],False,loc)[0])
                self.assertTrue(support.answer('problem','?',[],False,loc)[1])
        self.assertEqual(support.lang.effective('auto','es-MX'),'es')
        self.assertEqual(support.lang.effective('auto','fr-FR'),'en')
        self.assertNotIn('privatevalue',support.lang.for_model('PrivateKey = privatevalue'))
        self.assertNotIn('secretticket',support.lang.for_model('https://example.invalid/sub/secretticket'))
    def test_only_developers_can_verify_and_save_the_dedicated_bot(self):
        import os
        from tempfile import TemporaryDirectory
        actor = {'role': 'admin'}
        app = FastAPI()
        app.include_router(support.router_for(lambda: 'unused', lambda: actor))
        client = TestClient(app)
        url = '/api/v1/support/bot'
        fake = '123:' + 'fixture' * 6
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'PUSHOK_CONTROL_DIR':directory, 'PUSHOK_SUPPORT_BOT_TOKEN':''}), patch.object(support, 'bot_call') as telegram:
            self.assertEqual(client.get(url).status_code, 403)
            self.assertEqual(client.put(url, json={'token':fake}).status_code, 403)
            telegram.assert_not_called()
            actor['is_developer'] = True
            self.assertFalse(client.get(url).json()['configured'])
            telegram.return_value = {'username':'unrelated_fixture_bot'}
            self.assertEqual(client.put(url, json={'token':fake}).status_code, 409)
            telegram.side_effect = [{'username':support.BOT},{'url':''}]
            saved = client.put(url, json={'token':fake})
            self.assertEqual(saved.json(), {'username':support.BOT, 'configured':True, 'display_name':''})
            self.assertEqual(client.get(url).json(), saved.json())
            self.assertNotIn(fake, saved.text)

    def test_identity_is_verified_links_follow_it_and_profile_name_is_independent(self):
        import os
        from tempfile import TemporaryDirectory
        app = FastAPI()
        actor = {'is_owner':True}
        app.include_router(support.router_for(lambda: 'unused', lambda: actor))
        client = TestClient(app)
        url = '/api/v1/support/bot'
        old = '345:'+ 'a'*30
        new = '678:'+ 'b'*30
        main = '789:'+ 'c'*30
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'PUSHOK_CONTROL_DIR':directory, 'PUSHOK_SUPPORT_BOT_TOKEN':old,'TELEGRAM_BOT_TOKEN':main}), patch.object(support,'bot_call') as telegram:
            self.assertEqual(client.put(url,json={'username':'other_bot','token':main}).status_code,409)
            telegram.assert_not_called()
            telegram.side_effect=[{'username':'other_bot'}, {'url':'https://example.org/webhook'}]
            self.assertEqual(client.put(url,json={'username':'other_bot','token':new}).status_code,409)
            self.assertEqual(client.get('/ai-support',follow_redirects=False).headers['location'],'https://t.me/'+support.BOT)
            telegram.side_effect=[{'username':'other_bot','first_name':'Поддержка'}, {'url':''}]
            received=client.put(url,json={'username':'@Other_Bot','token':new})
            self.assertEqual(received.json()['username'],'other_bot')
            self.assertNotIn(new,received.text)
            self.assertEqual(support.credential()['legacy_id'],345)
            self.assertEqual(client.get('/ai-support',follow_redirects=False).headers['location'],'https://t.me/other_bot')
            telegram.side_effect=[{'username':'other_bot'}, {'url':''}, True]
            name=client.put(url+'/name',json={'username':'other_bot','name':'Пушок на связи'})
            self.assertEqual(name.json()['display_name'],'Пушок на связи')
            self.assertEqual(telegram.call_args.args,(new,'setMyName',{'name':'Пушок на связи'}))
            self.assertEqual(client.put(url,json={'username':'../../bad','token':new}).status_code,422)

    def test_appearance_upload_preview_access_and_conflicts(self):
        import base64, os
        from io import BytesIO
        from tempfile import TemporaryDirectory
        from PIL import Image
        actor = {'role':'operator'}
        app = FastAPI()
        app.include_router(support.router_for(lambda: 'unused', lambda: actor))
        client = TestClient(app);url='/api/v1/support/bot/appearance'
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'PUSHOK_CONTROL_DIR':directory}):
            self.assertEqual(client.get(url).status_code,403)
            actor['is_developer']=True
            initial=client.get(url).json()
            settings={**initial['settings'],'welcome':'**Привет** <script>','image_mode':'custom','buttons':False,'emoji':False}
            body={'settings':settings,'revision':initial['revision']}
            self.assertEqual(client.put(url,json=dict(body,image_base64='invalid')).status_code,422)
            photo=BytesIO()
            Image.new('RGB',(64,64),'teal').save(photo,format='PNG')
            image=base64.b64encode(photo.getvalue()).decode()
            saved=client.put(url,json=dict(body,image_base64=image)).json()
            self.assertEqual(saved['settings'],settings)
            self.assertEqual(client.get(saved['image_path']).headers['content-type'],'image/jpeg')
            self.assertNotIn(image,client.get(url).text)
            self.assertEqual(client.put(url,json=body).status_code,409)
            fields,path=support.design.welcome_payload(support.design.read(),567)
            self.assertEqual(path,saved['image_path'])
            self.assertIn('<b>Привет</b> &lt;script&gt;',fields['text'])
            self.assertIn('/idea',fields['text'])
            self.assertNotIn('🐾',fields['text']);self.assertNotIn('reply_markup',fields)
            actor['is_developer']=False
            self.assertEqual(client.put(url,json=body).status_code,403)

    def test_new_greeting_replaces_old_text_once_and_keeps_settings(self):
        from tempfile import TemporaryDirectory
        import os
        from app import support_appearance as design
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'PUSHOK_CONTROL_DIR':directory}):
            design.control.write(design.FILE, {'settings':{'title':'Прежний текст','welcome':'Старый текст','buttons':False,
                                  'image_mode':'none'},'revision':'old'})
            migrated=design.public(design.read())
            self.assertEqual(migrated['settings']['welcome'],design.Appearance().welcome)
            self.assertFalse(migrated['settings']['buttons'])
            self.assertEqual(migrated['settings']['image_mode'],'none')
            self.assertNotIn('Старый текст',str(design.control.read(design.FILE)))
            changed=dict(migrated['settings'],title='Новое название',tone='calm')
            edit=design.AppearanceEdit(settings=changed,revision=migrated['revision'])
            design.save(edit)
            self.assertEqual(design.public(design.read())['settings']['title'],'Новое название')
            greeting,handoff=support.answer('question','Привет!',[],False)
            self.assertFalse(handoff)
            self.assertIn('Здравствуйте',greeting)
