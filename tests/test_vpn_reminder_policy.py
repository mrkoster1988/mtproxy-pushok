import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import vpn_reminders as notices, vpn_settings as settings


class VpnPolicy(unittest.TestCase):
    def test_reminders_for_registered_without_plan_and_activity_types(self):
        policy=settings.Policy().model_dump()
        now=1800000000
        day=86400
        row=dict(registered=now-19*day,ever_used=False,last_seen=0,sub=None)
        def kinds(): return [(kind,anchor) for kind,anchor,*_ in notices.due(policy,row,now)]
        self.assertEqual(kinds(),[('never','registration')])
        row['sub']=dict(state='active',activity_known=False,subscription_id='s',expires_at=now+50*day)
        self.assertFalse(kinds())
        row.update(ever_used=True,last_seen=now-16*day)
        row['sub']['activity_known']=True
        for d in (7,3,1):
            end=now+d*day;row['sub']['expires_at']=end
            self.assertEqual(kinds(),[('expiry',f's:{end}:{d}'),('idle',str(row['last_seen']))])
        row['sub']=None
        self.assertFalse(kinds())
        text=notices.format_body('👋 **{имя}**, до __{дата}__',dict(имя='<i>&',дата='01.12'))
        self.assertEqual(text,'👋 <b>&lt;i&gt;&amp;</b>, до <i>01.12</i>')

    def test_direct_policy_and_access_paths_require_admin(self):
        user={'id':uuid4(),'role':'moderator'}
        app=FastAPI();app.include_router(settings.router_for(lambda:'unused',lambda:user))
        client=TestClient(app)
        for role in ('advertiser','moderator','operator'):
            user['role']=role
            for path in ('/api/v1/vpn/settings','/api/v1/vpn/settings/access'):
                self.assertEqual(client.get(path).status_code,403)
        with patch.object(settings,'shop',return_value={'enabled':True}):
            user['is_developer']=True
            self.assertEqual(client.get('/api/v1/vpn/settings/access').json(),{'enabled':True})

    def test_days_are_spelled_out_in_new_templates(self):
        for day,word in ((1,'день'),(3,'дня'),(7,'дней'),(11,'дней'),(12,'дней'),(21,'день'),(32,'дня')):
            rendered=notices.format_body(settings.Policy().expiry_text,dict(имя='Тест',срок=notices.days_label(day),дата='01.12.2026'))
            self.assertIn(f'<b>{day} {word}</b>',rendered)
            self.assertIn('01.12.2026',rendered)
