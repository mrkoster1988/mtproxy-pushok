import unittest
from uuid import uuid4
from urllib.error import URLError,HTTPError
from bot.lifecycle_notices import deliver_lifecycle_notices,render_lifecycle_notice

class LifecycleNoticeTests(unittest.TestCase):
    def item(self,**changes):
        return dict(notice_id=str(uuid4()),claim_token=str(uuid4()),campaign_id=str(uuid4()),telegram_user_id=123,
                    event_type='started',audience='owner',channel_title='Тест канала',**changes)
    def test_delivery_links_exact_campaign_then_ack(self):
        item=self.item();calls=[];sent=[]
        def api(route,payload):
            calls.append((route,payload));return [item] if route.endswith('/claim') else {'acknowledged':True}
        deliver_lifecycle_notices(api,sent.append,'https://example.com/miniapp')
        self.assertEqual(calls[-1][0],'lifecycle/outbox/finish')
        self.assertFalse(calls[-1][1]['permanent_failure'])
        self.assertEqual(sent[0]['reply_markup']['inline_keyboard'][0][0]['web_app']['url'],'https://example.com/miniapp#/campaign/'+item['campaign_id'])
        self.assertNotIn('parse_mode',sent[0])
    def test_failure_has_no_technical_reason_for_owner(self):
        item=self.item();item.update(event_type='failed',reason='apply_failed')
        self.assertNotIn('Причина:',render_lifecycle_notice(item))
        item['audience']='staff';self.assertIn('Причина:',render_lifecycle_notice(item))
        item['campaign_id']=None
        sent=[]
        deliver_lifecycle_notices(lambda route,payload:[item] if route.endswith('/claim') else {'acknowledged':True},sent.append,'https://example.com/miniapp')
        self.assertTrue(sent[0]['reply_markup']['inline_keyboard'][0][0]['web_app']['url'].endswith('#/sponsors'))
    def test_network_failure_keeps_notice_unacknowledged(self):
        item=self.item();calls=[]
        def api(route,payload):calls.append(route);return [item]
        def send(payload):raise URLError('offline')
        deliver_lifecycle_notices(api,send)
        self.assertEqual(calls,['lifecycle/outbox/claim'])
    def test_completed_notice_opens_archived_report_for_owner_and_staff(self):
        for audience in ('owner','staff'):
            item=self.item();item.update(event_type='completed',audience=audience);sent=[]
            deliver_lifecycle_notices(lambda route,payload:[item] if route.endswith('/claim') else {},sent.append,'https://example.com/miniapp')
            self.assertTrue(sent[0]['reply_markup']['inline_keyboard'][0][0]['web_app']['url'].endswith('#/archive/'+item['campaign_id']))
    def test_403_marks_permanent_failure(self):
        item=self.item();calls=[]
        def api(route,payload):calls.append((route,payload));return [item] if route.endswith('/claim') else {'acknowledged':True}
        def send(payload):raise HTTPError('https://example.com',403,'blocked',None,None)
        deliver_lifecycle_notices(api,send)
        self.assertTrue(calls[-1][1]['permanent_failure'])
    def test_invalid_event_is_not_delivered(self):
        item=self.item();item['event_type']='fake';sent=[];calls=[]
        def api(route,payload):calls.append(route);return [item]
        deliver_lifecycle_notices(api,sent.append)
        self.assertFalse(sent);self.assertEqual(calls,['lifecycle/outbox/claim'])
