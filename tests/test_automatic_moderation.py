"""Отказ ИИ: основание, транзакция, адресат и фактический текст уведомления."""
import unittest
import json
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import psycopg

import test_community_profiles as community_fixture
import test_postgres as fixture
from app import automatic_moderation as ai
from bot.advertiser_notices import deliver_advertiser_notices

TITLE = 'Канал с эротическими материалами для взрослых'
REASON = 'Канал продвигает сексуальный контент, который не рекомендуется для размещения.'
DESCRIPTION = 'Канал публикует анонсы эротических материалов для взрослой аудитории.'


def rejection(**changes):
    return ai.Decision(**({'decision': 'reject', 'reason': REASON, 'description': DESCRIPTION,
        'category': 'adult', 'evidence': [{'source': 'title', 'quote': TITLE}]} | changes))


class ModerationDecisionTests(unittest.TestCase):
    def test_rejection_requires_grounded_evidence_description_and_category(self):
        channel = {'title': TITLE, 'description': '', 'posts': []}
        self.assertEqual(ai.validate_answer(rejection(), channel).decision, 'reject')
        for changes in ({'evidence': []}, {'description': '  '}, {'reason': '\n'}, {'category': 'none'},
                        {'evidence': [{'source': 'title', 'quote': 'Несуществующий текст'}]},
                        {'evidence': [{'source': 'post_1', 'quote': TITLE}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ai.validate_answer(rejection(**changes), channel)

    def test_marker_alone_is_not_a_local_rejection_rule(self):
        channel = {'title': 'Медицинские новости 18+', 'posts': ['Профилактика и здоровье.']}
        answer = ai.Decision(decision='review', reason='Для оценки медицинских материалов нужен сотрудник.')
        self.assertEqual(ai.validate_answer(answer, channel).decision, 'review')

    def test_generated_notice_fits_outbox_and_preserves_exact_signature(self):
        text = ai.rejection_comment('п' * 500, 'д' * 500)
        self.assertLess(len(text), 1100)
        self.assertTrue(text.endswith('С уважением, Агент ИИ'))

    def test_structured_ai_transport_accepts_reject_but_refusal_is_not_a_decision(self):
        channel = {'title': TITLE, 'description': '', 'posts': []}
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': rejection().model_dump_json()}}]}
        with patch.object(ai.topics, 'reserve_ai_call'), patch.object(ai.topics, 'read_url', return_value=json.dumps(response).encode()) as read:
            answer = ai.validate_answer(ai.classify(channel, ('offline-key', 'model')), channel)
            self.assertEqual(answer.decision, 'reject')
            payload = json.loads(read.call_args.args[0].data)
            self.assertEqual(json.loads(payload['messages'][1]['content']), channel)
            self.assertFalse(payload['store'])
            self.assertNotIn('tools', payload)
            response['choices'][0]['message']['refusal'] = 'refused'
            read.return_value = json.dumps(response).encode()
            with self.assertRaises(ValueError):ai.classify(channel, ('offline-key', 'model'))


@unittest.skipUnless(fixture._approved_test_postgres(), 'Нужен тестовый PostgreSQL')
class AutomaticRejectionIntegration(unittest.TestCase):
    setUpClass = classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request = fixture.PostgresIntegration.request
    db = community_fixture.CommunityIntegration.db
    act = community_fixture.CommunityIntegration.act
    verify = community_fixture.CommunityIntegration.verify
    campaign = community_fixture.CommunityIntegration.campaign
    setUp = community_fixture.CommunityIntegration.setUp

    def pending(self):
        self.verify(self.member)
        cid = self.request('POST', '/campaigns', 201, json=self.campaign())['id']
        self.db("UPDATE campaigns SET moderation_status='pending',topic='travel' WHERE id=%s", (cid,))
        return cid

    @contextmanager
    def ai_result(self, answer=None, *, fully_textual=False, posts=None, title=TITLE):
        with ExitStack() as stack:
            stack.enter_context(patch.object(ai.settings, 'active', return_value=('offline-key', 'model')))
            stack.enter_context(patch.object(ai.settings, 'current', return_value={'revision': 'rejection-tests'}))
            stack.enter_context(patch.object(ai.public_channels, 'lookup', return_value={
                'title': title, 'description': '', 'username': 'test_channel', 'telegram_channel_id': -int(self.member.hex[:12],16)}))
            stack.enter_context(patch.object(ai.topics, 'public_sample', return_value={
                'posts': posts or [], 'fully_textual': fully_textual}))
            classify = stack.enter_context(patch.object(ai, 'classify', return_value=answer or rejection()))
            yield classify

    def reconsider(self, decision):
        cid = self.pending()
        with self.ai_result():
            self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 1)
        path = '/operator/campaigns/'+cid+'/moderation'
        body = dict(decision=decision, comment='Ручная проверка канала', reconsider_ai=True)
        self.request('POST',path,403,json=body)
        self.act(self.admin)
        card = next(row for row in self.request('GET','/operator/campaigns') if row['id']==cid)
        self.assertTrue(card['ai_rejected'])
        self.assertEqual(card['ai_description'],DESCRIPTION)
        self.assertTrue(card['ai_checked_at'])
        if decision=='approved':
            self.request('POST',path,409,json=body)
            blocks=self.request('GET','/operator/channel-blocks')['items']
            entry=next(b for b in blocks if b['telegram_channel_id']==str(-int(self.member.hex[:12],16)))
            self.request('DELETE','/operator/channel-blocks/'+entry['id'])
        result=self.request('POST',path,json=body)
        self.assertEqual(result['moderation_status'],decision)
        self.request('POST',path,409,json=body)
        notices=self.db('SELECT decision,ai_moderation_event_id FROM bot_advertiser_notice_outbox WHERE campaign_id=%s AND abandoned_at IS NULL',(cid,))
        self.assertEqual(len(notices),1)
        self.assertEqual(notices[0]['decision'],decision)
        self.assertIsNone(notices[0]['ai_moderation_event_id'])

    def test_operator_approves_rejected_agent_application(self):
        self.reconsider('approved')

    def test_operator_maintains_rejection_with_own_reason(self):
        self.reconsider('rejected')

    def test_rejection_notifies_owner_and_counts_once_with_real_outbox(self):
        cid = self.pending()
        self.act(self.admin)
        original_payment = self.db('SELECT payment_status FROM campaigns WHERE id=%s', (cid,))[0]['payment_status']
        before = self.request('GET', '/users/ai-agent/profile')['counts']['rejected']
        with self.ai_result():
            self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 1)
            self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 0)
            self.assertTrue(self.request('GET', '/users/ai-agent/profile')['automatic_rejection_enabled'])
        campaign = self.db('SELECT * FROM campaigns WHERE id=%s', (cid,))[0]
        self.assertEqual(campaign['moderation_status'], 'rejected')
        self.assertEqual(campaign['payment_status'], original_payment)
        self.assertEqual(campaign['placement_status'], 'not_scheduled')
        self.assertIn(REASON, campaign['moderator_comment'])
        self.assertIn(DESCRIPTION, campaign['moderator_comment'])
        self.assertTrue(campaign['moderator_comment'].endswith(ai.SIGNATURE))
        notices = self.db('SELECT * FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,))
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['recipient_user_id'], self.member)
        events = self.db('SELECT * FROM ai_moderation_events WHERE campaign_id=%s', (cid,))
        self.assertEqual(len(events), 1)
        self.assertEqual(notices[0]['ai_moderation_event_id'], events[0]['id'])
        self.assertEqual(events[0]['category'], 'adult')
        self.assertEqual(events[0]['evidence'][0]['quote'], TITLE)
        profile = self.request('GET', '/users/ai-agent/profile')
        self.assertEqual(profile['counts']['rejected'], before + 1)
        self.assertTrue(any(t['topic']=='travel' and t['rejected']>=1 for t in profile['topics']))
        check = self.request('GET', '/community/moderation/' + cid)['check']
        self.assertTrue(check['rejected_by_agent'])
        self.assertFalse(check['approved_by_agent'])
        self.assertEqual(check['description'], DESCRIPTION)
        self.assertTrue(any(e['event']=='campaign_rejected_by_ai'
                            for e in self.request('GET', '/campaigns/' + cid + '/report')['timeline']))
        # Изолируем транспорт от уведомлений других сценариев этой временной БД.
        self.db('UPDATE bot_advertiser_notice_outbox SET delivered_at=now() WHERE id<>%s AND abandoned_at IS NULL', (notices[0]['id'],))
        sent = []
        def api(route, data):
            return self.request('POST', '/internal/bot/' + route, headers={'X-Pushok-Bot-Secret': 'f'*64}, json=data)
        with patch.dict('os.environ', {'DATABASE_URL': self.dsn, 'BOT_INTERNAL_SECRET': 'f'*64}):
            deliver_advertiser_notices(api, sent.append)
            deliver_advertiser_notices(api, sent.append)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['chat_id'], int(self.member.hex[:12], 16))
        self.assertIn(REASON, sent[0]['text'])
        self.assertIn(DESCRIPTION, sent[0]['text'])
        self.assertTrue(sent[0]['text'].endswith(ai.SIGNATURE))
        self.assertNotIn('parse_mode', sent[0])
        self.assertIsNotNone(self.db('SELECT delivered_at FROM bot_advertiser_notice_outbox WHERE id=%s', (notices[0]['id'],))[0]['delivered_at'])
        self.db("INSERT INTO audit_events(actor_user_id,campaign_id,event_type) VALUES(%s,%s,'campaign_moderated')", (self.admin,cid))
        self.assertFalse(self.request('GET', '/community/moderation/' + cid)['check']['rejected_by_agent'])

    def test_media_or_short_captions_alone_do_not_escalate(self):
        for prediction, expected in [('approve','approved'), ('request_context','changes_requested')]:
            cid = self.pending()
            with self.ai_result(ai.Decision(decision=prediction,reason='Оценены доступные тексты.'),
                                title='Фотографии нашего города', posts=['Открытие выставки']):
                self.assertEqual(ai.service_once(self.dsn, limit=1)[expected], 1)
            self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s',(cid,))[0]['moderation_status'],expected)

    def test_block_applies_to_channel_across_advertisers_and_operator_can_lift_it(self):
        cid = self.pending()
        identity = -int(self.member.hex[:12],16)
        with self.ai_result():
            self.assertEqual(ai.service_once(self.dsn,limit=1)['rejected'],1)
        self.assertIsNone(self.db('SELECT blocked_at FROM users WHERE id=%s',(self.member,))[0]['blocked_at'])
        self.request('GET','/campaigns')
        self.request('GET','/operator/channel-blocks',403)
        self.act(self.friend)
        self.verify(self.friend)
        from uuid import uuid4
        alias = 'channel_' + uuid4().hex[:8]
        published = {'telegram_url':'https://t.me/'+alias, 'telegram_channel_id':identity, 'title':alias, 'username':alias}
        order = dict(channel={'title':alias,'telegram_url':published['telegram_url']},
                     tariff_id=self.db('SELECT tariff_id FROM campaigns WHERE id=%s',(cid,))[0]['tariff_id'],
                     expected_price_kopecks=30000,expected_duration_seconds=86400,request_id=str(uuid4()))
        order['tariff_id']=str(order['tariff_id'])
        with patch.object(ai.public_channels,'lookup',return_value=published):
            self.request('POST','/orders',409,json=order)
            # Переход старого имени к новому каналу не переносит блокировку по имени.
            other=dict(published,telegram_channel_id=identity-1)
            original='https://t.me/'+self.db('SELECT telegram_url FROM sponsor_channels WHERE id=(SELECT channel_id FROM campaigns WHERE id=%s)',(cid,))[0]['telegram_url'].rsplit('/',1)[-1]
            with patch.object(ai.public_channels,'lookup',return_value=dict(other,telegram_url=original)):
                self.request('POST','/orders',201,json={**order,'request_id':str(uuid4()),'channel':{'title':alias,'telegram_url':original}})
            self.act(self.admin)
            entry=next(b for b in self.request('GET','/operator/channel-blocks')['items'] if b['telegram_channel_id']==str(identity))
            path='/operator/channel-blocks/'+entry['id']
            self.act(self.friend)
            self.request('DELETE',path,403)
            self.db("UPDATE users SET role='operator' WHERE id=%s",(self.admin,))
            self.act(self.admin)
            self.actor['role']='operator'
            self.request('DELETE',path)
            self.request('DELETE',path)
            self.act(self.friend)
            self.request('POST','/orders',201,json=order)
        self.assertEqual(self.db('SELECT lifted_by FROM channel_moderation_blocks WHERE id=%s',(entry['id'],))[0]['lifted_by'],self.admin)
        self.db("UPDATE campaigns SET archived_at=now() WHERE advertiser_user_id=%s",(self.friend,))

    def test_requested_categories_enter_channel_list_only_with_public_evidence(self):
        for category in ('armed_conflict','homicide','drugs','terrorism','scam'):
            cid = self.pending()
            identity = -int(cid.replace('-','')[:12],16)
            title='Описание тестового канала ' + category
            prediction=rejection(category=category, evidence=[{'source':'title','quote':title}])
            with self.ai_result(prediction,title=title):
                ai.public_channels.lookup.return_value['telegram_channel_id']=identity
                self.assertEqual(ai.service_once(self.dsn,limit=1)['rejected'],1)
            rows=self.db('SELECT id FROM channel_moderation_blocks WHERE telegram_channel_id=%s AND lifted_at IS NULL',(identity,))
            self.assertEqual(bool(rows),category!='scam')
        self.assertIsNone(self.db('SELECT blocked_at FROM users WHERE id=%s',(self.member,))[0]['blocked_at'])

    def test_pending_card_shows_only_completed_check_for_current_submission(self):
        cid = self.pending()
        self.addCleanup(self.db, 'UPDATE campaigns SET archived_at=now() WHERE id=%s', (cid,))
        def card(route):
            return next(item for item in self.request('GET', route) if item['id'] == cid)
        def check_absent(item):
            for key in ('moderation_check', 'moderation_reason', 'moderation_attempted_at'):
                self.assertIsNone(item[key])
        self.db("""INSERT INTO ai_moderation_checks(campaign_id,snapshot,revision)
                   SELECT id,updated_at,'test' FROM campaigns WHERE id=%s""", (cid,))
        check_absent(card('/campaigns'))
        self.act(self.admin)
        self.assertIsNone(self.request('GET', '/community/moderation/' + cid)['check'])
        for decision in ('retry', 'review'):
            reason = 'Не удалось загрузить публикации.' if decision == 'retry' else 'В публикациях есть медиа.'
            self.db('UPDATE ai_moderation_checks SET decision=%s,reason=%s WHERE campaign_id=%s', (decision, reason, cid))
            self.act(self.member)
            own = card('/campaigns')
            self.assertEqual((own['moderation_check'], own['moderation_reason']), (decision, reason))
            self.assertTrue(own['moderation_attempted_at'])
            self.request('GET', '/community/moderation/' + cid, 403)
            self.act(self.friend)
            self.assertFalse(any(item['id'] == cid for item in self.request('GET', '/campaigns')))
            self.act(self.admin)
            operator = card('/operator/campaigns?moderation_status=pending')
            self.assertEqual((operator['moderation_reason'], operator['moderation_attempted_at']),
                             (reason, own['moderation_attempted_at']))
            self.assertEqual(self.request('GET', '/community/moderation/' + cid)['check']['reason'], reason)
        self.db('UPDATE campaigns SET updated_at=clock_timestamp() WHERE id=%s', (cid,))
        self.assertIsNone(self.request('GET', '/community/moderation/' + cid)['check'])
        self.act(self.member)
        check_absent(card('/campaigns'))

    def test_invalid_evidence_retries_without_rejection_or_notice(self):
        cid = self.pending()
        with self.ai_result(rejection(evidence=[{'source': 'title', 'quote': 'Вымышленное нарушение'}])):
            self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 0)
        self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s', (cid,))[0]['moderation_status'], 'pending')
        self.assertEqual(self.db('SELECT decision FROM ai_moderation_checks WHERE campaign_id=%s', (cid,))[0]['decision'], 'retry')
        self.assertEqual(self.db('SELECT id FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,)), [])

    def test_declared_adult_trial_in_manual_queue_rejected_once_without_external_calls(self):
        from uuid import uuid4
        from app import auto_schedule
        self.verify(self.member)
        order = self.campaign()
        self.db('UPDATE tariffs SET price_kopecks=0 WHERE id=%s', (order['tariff_id'],))
        order.update(request_id=str(uuid4()), expected_price_kopecks=0,
                     expected_duration_seconds=86400, topic='adult')
        cid = self.request('POST', '/orders', 201, json=order)['id']
        other = self.pending()
        previous_policy = 'rejection-tests:' + ai.POLICY_REVISION
        self.db("""INSERT INTO ai_moderation_checks(campaign_id,snapshot,revision,decision)
                   SELECT id,updated_at,%s,'review' FROM campaigns WHERE id IN (%s,%s)""",
                (previous_policy,cid,other))
        with self.ai_result() as classify:
            self.assertEqual(ai.service_once(self.dsn)['rejected'], 1)
            self.assertEqual(ai.service_once(self.dsn)['rejected'], 0)
            ai.public_channels.lookup.assert_not_called()
            ai.topics.public_sample.assert_not_called()
            classify.assert_not_called()
        auto_schedule.service_once(self.dsn)
        campaign = self.db('SELECT moderation_status,payment_status,placement_status FROM campaigns WHERE id=%s', (cid,))[0]
        self.assertEqual(campaign, dict(moderation_status='rejected', payment_status='not_required',
                                        placement_status='not_scheduled'))
        event, = self.db('SELECT id,topic,category,evidence FROM ai_moderation_events WHERE campaign_id=%s',(cid,))
        self.assertEqual((event['topic'], event['category']), ('adult','adult'))
        self.assertEqual(event['evidence'], [{'source':'campaign_topic','quote':'18+ · Для взрослых'}])
        notice, = self.db('SELECT * FROM bot_advertiser_notice_outbox WHERE campaign_id=%s',(cid,))
        self.assertEqual(notice['decision'], 'rejected')
        self.assertEqual(notice['ai_moderation_event_id'], event['id'])
        self.assertEqual(notice['recipient_user_id'], self.member)
        self.assertIn('В заявке указана категория', notice['comment_snapshot'])
        self.assertTrue(notice['comment_snapshot'].endswith(ai.SIGNATURE))
        self.assertFalse(self.db('SELECT id FROM ai_moderation_events WHERE campaign_id=%s',(other,)))

    def test_archived_pending_campaign_cannot_receive_a_late_web_decision(self):
        cid = self.pending()
        self.request('DELETE', '/campaigns/' + cid)
        self.act(self.admin)
        self.request('POST', f'/operator/campaigns/{cid}/moderation', 409, json={'decision':'approved'})
        self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s', (cid,))[0]['moderation_status'], 'pending')

    def test_temporary_post_failure_retries_but_allows_grounded_metadata_rejection(self):
        unavailable = {'posts':[], 'fully_textual':False, 'temporary_failure':True}
        cid = self.pending()
        with self.ai_result(ai.Decision(decision='review', reason='Нужны публикации.')), patch.object(
                ai.topics, 'public_sample', return_value=unavailable):
            ai.service_once(self.dsn, limit=1)
            self.assertEqual(self.db('SELECT decision FROM ai_moderation_checks WHERE campaign_id=%s', (cid,))[0]['decision'], 'retry')
            ai.service_once(self.dsn, limit=1)
            self.assertEqual(len(self.db('SELECT id FROM ai_moderation_events WHERE campaign_id=%s', (cid,))), 1)
        self.db("UPDATE ai_moderation_checks SET attempted_at=now()-interval '6 minutes',retry_at=now()-interval '1 second' WHERE campaign_id=%s", (cid,))
        with self.ai_result(), patch.object(ai.topics, 'public_sample', return_value=unavailable):
            self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 1)
        self.assertEqual(self.db('SELECT decision FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,)), [{'decision':'rejected'}])

    def test_uncertainty_and_media_do_not_become_rejection(self):
        for answer in (ai.Decision(decision='review', reason='Требуется контекст.'),
                       ai.Decision(decision='approve', reason='Нарушений не видно.')):
            cid = self.pending()
            with self.ai_result(answer, title='Новости медицины 18+'):
                self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 0)
            self.assertEqual(self.db('SELECT decision FROM ai_moderation_checks WHERE campaign_id=%s', (cid,))[0]['decision'], 'review')
            self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s', (cid,))[0]['moderation_status'], 'pending')
            self.assertEqual(self.db('SELECT id FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,)), [])

    def test_confirmed_adult_promotion_rejects_without_operator_for_another_topic(self):
        for initial in ('approve', 'review'):
            with self.subTest(initial=initial):
                cid = self.pending()
                self.db("UPDATE campaigns SET topic='travel' WHERE id=%s", (cid,))
                with self.ai_result() as classify:
                    classify.side_effect = [ai.Decision(decision=initial, reason='Общий разбор.'), rejection()]
                    self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 1)
                    self.assertEqual(classify.call_count, 2)
                    self.assertEqual(classify.call_args.kwargs, {'check_adult':True})
                    ai.service_once(self.dsn, limit=1)
                    self.assertEqual(classify.call_count, 2)
                events = self.db('SELECT decision,category,evidence FROM ai_moderation_events WHERE campaign_id=%s', (cid,))
                self.assertEqual([(e['decision'],e['category']) for e in events], [('rejected','adult')])
                self.assertEqual(events[0]['evidence'][0]['quote'], TITLE)
                notice = self.db('SELECT decision,comment_snapshot FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,))
                self.assertEqual([n['decision'] for n in notice], ['rejected'])
                self.assertTrue(notice[0]['comment_snapshot'].endswith(ai.SIGNATURE))
                self.assertEqual(self.db('SELECT moderation_status,placement_status FROM campaigns WHERE id=%s',(cid,))[0],
                                 {'moderation_status':'rejected','placement_status':'not_scheduled'})

    def test_ungrounded_focused_reply_cannot_reject_or_approve(self):
        cid = self.pending()
        with self.ai_result() as classify:
            classify.side_effect = [ai.Decision(decision='approve', reason='Новости.'),
                                    rejection(evidence=[{'source':'title','quote':'Такого текста нет в канале'}])]
            self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 0)
        self.assertEqual(self.db('SELECT decision FROM ai_moderation_checks WHERE campaign_id=%s',(cid,)), [{'decision':'retry'}])
        self.assertEqual(self.db('SELECT decision FROM bot_advertiser_notice_outbox WHERE campaign_id=%s',(cid,)), [])

    def test_stale_rejection_does_not_override_manual_approval(self):
        cid = self.pending()
        def manual(*args):
            self.db("UPDATE campaigns SET moderation_status='approved',updated_at=clock_timestamp(),moderator_comment='Решение сотрудника' WHERE id=%s", (cid,))
            return rejection()
        with self.ai_result() as classify:
            classify.side_effect = manual
            self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 0)
        self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s', (cid,))[0]['moderation_status'], 'approved')
        self.assertEqual(self.db('SELECT id FROM ai_moderation_events WHERE campaign_id=%s', (cid,)), [])
        notices = self.db('SELECT decision,ai_moderation_event_id FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,))
        self.assertEqual(notices, [{'decision': 'approved', 'ai_moderation_event_id': None}])

    def test_disabled_or_changed_configuration_does_not_reject(self):
        cid = self.pending()
        with patch.object(ai.settings, 'active', return_value=None), patch.object(ai, 'classify') as classify:
            self.assertEqual(ai.service_once(self.dsn), {'status': 'disabled'})
            classify.assert_not_called()
        with self.ai_result(), patch.object(ai.settings, 'current', side_effect=[{'revision': 'rejection-tests'}, {'revision': 'new'}]):
            self.assertEqual(ai.service_once(self.dsn, limit=1)['rejected'], 0)
        self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s', (cid,))[0]['moderation_status'], 'pending')
        self.assertEqual(self.db('SELECT id FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,)), [])

    def test_delivery_failure_keeps_notice_for_retry(self):
        cid = self.pending()
        with self.ai_result():ai.service_once(self.dsn, limit=1)
        self.db('UPDATE bot_advertiser_notice_outbox SET delivered_at=now() WHERE campaign_id<>%s AND abandoned_at IS NULL', (cid,))
        calls = []
        def api(route, data):
            calls.append(route)
            return self.request('POST', '/internal/bot/' + route, headers={'X-Pushok-Bot-Secret': 'f'*64}, json=data)
        def failed(_):raise OSError('offline')
        with patch.dict('os.environ', {'DATABASE_URL': self.dsn, 'BOT_INTERNAL_SECRET': 'f'*64}):
            deliver_advertiser_notices(api, failed)
        self.assertEqual(calls, ['advertisers/outbox/claim'])
        row = self.db('SELECT * FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,))[0]
        self.assertIsNone(row['delivered_at'])
        self.assertIsNone(row['abandoned_at'])
        self.assertIsNotNone(row['ai_moderation_event_id'])

    def test_outbox_failure_rolls_back_decision_and_event(self):
        cid = self.pending()
        self.db('ALTER TABLE bot_advertiser_notice_outbox ADD CONSTRAINT test_no_ai_event CHECK(ai_moderation_event_id IS NULL) NOT VALID')
        try:
            with self.ai_result(), self.assertRaises(psycopg.errors.CheckViolation):
                ai.service_once(self.dsn, limit=1)
        finally:self.db('ALTER TABLE bot_advertiser_notice_outbox DROP CONSTRAINT test_no_ai_event')
        self.assertEqual(self.db('SELECT moderation_status FROM campaigns WHERE id=%s', (cid,))[0]['moderation_status'], 'pending')
        self.assertEqual(self.db('SELECT id FROM ai_moderation_events WHERE campaign_id=%s', (cid,)), [])
        self.assertEqual(self.db('SELECT id FROM bot_advertiser_notice_outbox WHERE campaign_id=%s', (cid,)), [])

    def test_migration_can_be_applied_again_without_losing_decisions(self):
        cid = self.pending()
        with self.ai_result():ai.service_once(self.dsn, limit=1)
        migration = (Path(__file__).resolve().parents[1] / 'database/064_ai_moderation_rejection.sql').read_text()
        self.db(migration)
        self.db(migration)
        event = self.db('SELECT description,evidence FROM ai_moderation_events WHERE campaign_id=%s', (cid,))[0]
        self.assertEqual(event['description'], DESCRIPTION)
        self.assertEqual(event['evidence'][0]['quote'], TITLE)
