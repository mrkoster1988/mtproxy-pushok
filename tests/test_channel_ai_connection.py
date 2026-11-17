"""Подключение ИИ: секреты, права, проверка провайдера и конкурентное сохранение."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from app import channel_ai_connection as connection, channel_topic_suggestions as suggest, control


class AIConnectionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        env=patch.dict(os.environ, {'PUSHOK_CONTROL_DIR':tmp.name,'CHANNEL_TOPIC_AI_ENABLED':'0',
                                   'CHANNEL_TOPIC_AI_API_KEY':'','CHANNEL_TOPIC_AI_MODEL':'gpt-4.1-mini'})
        env.start();self.addCleanup(env.stop)
        self.role='admin'
        def admin():
            if self.role!='admin':raise HTTPException(403,'admin_required')
            return {'id':'00000000-0000-0000-0000-000000000001'}
        app=FastAPI();app.include_router(connection.router_for(lambda:'dsn',admin));self.client=TestClient(app)
        self.db=MagicMock();self.db.__enter__.return_value=self.db
        db=patch('psycopg.connect',return_value=self.db);db.start();self.addCleanup(db.stop)
        reply=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'topic':'education','confident':True})}}]}).encode()
        transport=patch.object(suggest,'read_url',return_value=reply);self.transport=transport.start();self.addCleanup(transport.stop)
        connection._checks.clear();suggest._ai_calls.clear()
        self.path='/api/v1/admin/channel-ai'
        self.key='sk-test-private-key-123456789'
        self.body=dict(enabled=True,model='gpt-4.1-mini',key=self.key,revision='environment')

    def save(self,body=None):
        response=self.client.put(self.path,json=self.body if body is None else body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertNotIn(self.key,response.text)
        return response.json()

    def test_save_checks_key_and_model_and_persists_private_file(self):
        result=self.save()
        self.assertTrue(result['enabled']);self.assertTrue(result['key_saved']);self.assertTrue(result['checked_at'])
        self.assertNotIn(self.key,self.client.get(self.path).text)
        self.assertEqual(suggest.ai_config(),(self.key,'gpt-4.1-mini'))
        self.assertEqual((control.root()/connection.FILE).stat().st_mode&0o777,0o600)
        request=self.transport.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.openai.com/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'),'Bearer '+self.key)
        self.assertEqual(json.loads(request.data)['model'],'gpt-4.1-mini')
        self.assertNotIn(self.key,str(self.db.execute.call_args))

    def test_test_connection_does_not_save_or_enable(self):
        response=self.client.post(self.path+'/check',json=self.body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['verified'])
        self.assertNotIn(self.key,response.text)
        self.assertIsNone(control.read(connection.FILE));self.assertIsNone(suggest.ai_config())
        self.db.execute.assert_not_called()

    def test_successful_key_and_model_rotation_replaces_active_configuration(self):
        saved=self.save()
        rotated=self.save({**self.body,'key':'sk-replacement-private-key','model':'fixture-compatible-model','revision':saved['revision']})
        self.assertNotEqual(rotated['revision'],saved['revision'])
        self.assertEqual(suggest.ai_config(),('sk-replacement-private-key','fixture-compatible-model'))
        self.assertNotIn('sk-replacement-private-key',self.client.get(self.path).text)
        self.assertNotIn('sk-replacement-private-key',str(self.db.execute.call_args))

    def test_only_admin_can_read_check_and_save(self):
        for role in ('advertiser','operator'):
            self.role=role
            for method,url,body in (('GET',self.path,None),('POST',self.path+'/check',self.body),('PUT',self.path,self.body)):
                response=self.client.request(method,url,json=body)
                self.assertEqual(response.status_code,403);self.assertNotIn(self.key,response.text)
        self.transport.assert_not_called();self.assertIsNone(control.read(connection.FILE))

    def test_enable_requires_key_but_off_configuration_does_not(self):
        body={**self.body,'key':''}
        self.assertEqual(self.client.put(self.path,json=body).status_code,422)
        self.save({**body,'enabled':False});self.transport.assert_not_called()

    def test_blank_key_preserves_and_off_does_not_require_provider(self):
        saved=self.save();self.transport.reset_mock()
        off=self.save({**self.body,'enabled':False,'key':'','revision':saved['revision']})
        self.transport.assert_not_called();self.assertTrue(off['key_saved']);self.assertIsNone(suggest.ai_config())
        self.assertEqual(connection.current()['key'],self.key)

    def test_delete_overrides_environment_without_reactivating_key(self):
        with patch.dict(os.environ,{'CHANNEL_TOPIC_AI_ENABLED':'1','CHANNEL_TOPIC_AI_API_KEY':self.key}):
            self.assertIsNotNone(suggest.ai_config())
            result=self.save({'enabled':False,'model':'gpt-4.1-mini','revision':'environment','clear_key':True})
            self.assertFalse(result['key_saved']);self.assertIsNone(suggest.ai_config())
        self.transport.assert_not_called()

    def test_deleted_key_cannot_be_combined_with_enable_or_replacement(self):
        for body in ({**self.body,'clear_key':True},{**self.body,'enabled':False,'clear_key':True}):
            self.assertEqual(self.client.put(self.path,json=body).status_code,422)
        self.transport.assert_not_called()

    def test_provider_errors_never_leak_payload_or_replace_saved_key(self):
        saved=self.save();previous=control.read(connection.FILE)
        for code,expected in ((401,'key_rejected'),(403,'access_denied'),(404,'model_unavailable'),(400,'model_unsupported'),(429,'provider_limit')):
            connection._checks.clear()
            self.transport.side_effect=HTTPError('https://api.openai.com',code,self.key,{},io.BytesIO(self.key.encode()))
            response=self.client.put(self.path,json={**self.body,'key':'replacement-secret','revision':saved['revision']})
            self.assertEqual(response.status_code,502);self.assertEqual(response.json()['detail'],'channel_ai_'+expected)
            self.assertNotIn(self.key,response.text);self.assertNotIn('replacement-secret',response.text)
            self.assertEqual(control.read(connection.FILE),previous)

    def test_timeout_and_invalid_response_fail_without_saving(self):
        for failure in (TimeoutError(self.key),URLError(self.key),ValueError(self.key),KeyError(self.key)):
            self.transport.side_effect=failure
            response=self.client.put(self.path,json=self.body)
            self.assertEqual(response.status_code,502);self.assertNotIn(self.key,response.text)
        self.assertIsNone(control.read(connection.FILE))

    def test_validation_errors_redact_entire_input(self):
        for change in (dict(key={'secret':self.key}),dict(model=self.key+'\n'),dict(enabled=self.key),dict(extra=self.key)):
            response=self.client.put(self.path,json=self.body|change)
            self.assertEqual(response.status_code,422);self.assertNotIn(self.key,response.text)
            self.assertEqual(response.headers['cache-control'],'no-store')
        malformed=self.client.put(self.path,content='{"key":"'+self.key,headers={'Content-Type':'application/json'})
        self.assertEqual(malformed.status_code,422);self.assertNotIn(self.key,malformed.text)
        self.transport.assert_not_called()

    def test_revision_prevents_overwriting_newer_configuration(self):
        saved=self.save();previous=control.read(connection.FILE);self.transport.reset_mock()
        response=self.client.put(self.path,json=self.body)
        self.assertEqual(response.status_code,409);self.transport.assert_not_called()
        self.assertEqual(control.read(connection.FILE),previous)
        def concurrent_probe(cfg):control.write(connection.FILE,{**previous,'revision':'another-admin','enabled':False})
        with patch.object(connection,'probe',side_effect=concurrent_probe):
            response=self.client.put(self.path,json={**self.body,'revision':saved['revision']})
        self.assertEqual(response.status_code,409);self.assertFalse(connection.current()['enabled'])

    def test_probe_is_throttled_and_returns_no_confident_topic_as_failure(self):
        with patch.object(suggest,'ai_topic',return_value=None):
            response=self.client.post(self.path+'/check',json=self.body)
            self.assertEqual(response.status_code,502)
        connection._checks.extend([connection.time.monotonic()]*6)
        self.assertEqual(self.client.post(self.path+'/check',json=self.body).status_code,429)

    def test_corrupt_saved_config_fails_closed_and_can_be_replaced(self):
        (control.root()/connection.FILE).write_text('broken')
        with patch.dict(os.environ,{'CHANNEL_TOPIC_AI_ENABLED':'1','CHANNEL_TOPIC_AI_API_KEY':self.key}):
            self.assertIsNone(suggest.ai_config())
            self.assertEqual(self.client.get(self.path).json()['source'],'invalid')
            self.save({**self.body,'revision':'invalid'})

    def test_configuration_is_in_private_backup_allowlist(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import cabinet_backup
        self.assertEqual(cabinet_backup.CONFIG['.control/'+connection.FILE],connection.FILE)
        self.assertIn(connection.FILE,cabinet_backup.ALLOWED)

    def test_capability_permissions_are_persisted_preserved_and_can_be_revoked(self):
        saved=self.save({**self.body,'capabilities':['launch_diagnostics','campaign_review']})
        self.assertIsNone(connection.active())
        self.assertEqual(connection.active('campaign_review'),(self.key,'gpt-4.1-mini'))
        self.assertEqual(saved['capabilities'],['campaign_review','launch_diagnostics'])
        saved=self.save({**self.body,'revision':saved['revision'],'key':''})
        self.assertEqual(saved['capabilities'],['campaign_review','launch_diagnostics'])
        saved=self.save({**self.body,'revision':saved['revision'],'capabilities':[]})
        self.assertIsNone(connection.active('campaign_review'))
        self.assertEqual(saved['capabilities'],[])
        self.assertEqual(self.client.put(self.path,json={**self.body,'revision':saved['revision'],'capabilities':['change_balance']}).status_code,422)

    def test_invalid_stored_permissions_fail_closed(self):
        saved=self.save()
        control.write(connection.FILE,{**connection.current(),'capabilities':['change_balance']})
        self.assertIsNone(connection.active('campaign_review'))
        self.assertEqual(connection.current()['source'],'invalid')
