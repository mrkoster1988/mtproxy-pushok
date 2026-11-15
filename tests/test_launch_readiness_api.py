"""Приёмка по источникам, отрицательные результаты, давность и доступ."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
os.environ.setdefault('SESSION_SIGNING_KEY', 'a' * 64)
from app import main
from app.launch_readiness import build_launch_readiness, MANUAL_CHECKS

ADMIN = UUID('00000000-0000-4000-8000-000000000951')
NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
SHA = 'a' * 40


def campaign(**changes):
    result = dict(campaign_id=str(uuid4()), node_name='Test node', observation_id=5, result='visible',
        method='separate_unsubscribed_client', observed_at=NOW-timedelta(hours=2),
        scheduled_start_at=NOW-timedelta(hours=3), scheduled_end_at=NOW-timedelta(hours=1),
        activated_at=NOW-timedelta(hours=2)+timedelta(seconds=1), completed_at=NOW-timedelta(minutes=30),
        placement_status='completed', tag_removed_confirmed=True, verification_method='separate_unsubscribed_client')
    return {**result, **changes}


def reports():
    return [dict(id=str(uuid4()),created_at=NOW,details_json=dict(check_id=key,result='passed',steps_confirmed=True,
        performed_on_vps=True,checked_at=NOW.isoformat(),version='1.04',commit=SHA,note='Actual external acceptance test result')) for key in MANUAL_CHECKS]


def build(c=None, checks=(), **kw):
    return build_launch_readiness({'nodes_total':1, 'nodes_active':1}, c, checks, version='1.04',commit=SHA,
        ci=dict(verified=True,commit=SHA,checked_at=NOW.isoformat()),now=NOW,**kw)


class ReadinessEvidenceTests(unittest.TestCase):
    def test_counts_and_tag_do_not_prove_live_tests(self):
        report=build_launch_readiness(dict(nodes_total=3,nodes_active=3,placements_completed=9,manual_observations=10,ad_tag='PRIVATE'))
        self.assertFalse(report['ready_for_public_launch']);self.assertFalse(report['real_sponsor_verified'])
        self.assertNotIn('PRIVATE',str(report));self.assertEqual(len(report['pending_paid']),7)
        self.assertEqual(report['scope'],'recorded_acceptance_not_live_monitoring')

    def test_full_cycle_and_reports_enable_separate_goals_without_side_effects(self):
        free=build(campaign(),reports()[:1]);paid=build(campaign(),reports())
        self.assertTrue(free['ready_for_free_pilot']);self.assertFalse(free['ready_for_public_launch'])
        self.assertEqual(set(free['pending_paid']),{'stars_payment','sbp_payment'})
        self.assertTrue(paid['ready_for_public_launch']);self.assertEqual(paid['pending_paid'],[])
        self.assertFalse(paid['payments_automatically_verified']);self.assertFalse(paid['telegram_api_verified'])
        self.assertFalse(paid['changes_applied'])

    def test_completion_requires_matching_ordered_positive_evidence(self):
        for changes in ({'result':'not_visible'},{'activated_at':None},{'completed_at':None},
                        {'activated_at':NOW-timedelta(days=1)},{'completed_at':NOW+timedelta(minutes=1)},
                        {'placement_status':'active'},{'tag_removed_confirmed':'true'},
                        {'verification_method':'other'},
                        {'observed_at':NOW+timedelta(days=1)},{'scheduled_end_at':NOW-timedelta(days=1)}):
            with self.subTest(changes=changes):self.assertIn('campaign_cycle',build(campaign(**changes),reports())['pending_free'])

    def test_negative_promotion_does_not_claim_broken_connection(self):
        data=build(campaign(result='not_visible'),reports());items={x['id']:x for x in data['checks']}
        self.assertEqual(items['real_sponsor']['status'],'failed');self.assertEqual(items['real_mtproxy']['status'],'not_verified')
        self.assertFalse(data['ready_for_free_pilot'])

    def test_failed_stale_future_different_release_reports_never_pass(self):
        for change in ({'result':'failed'},{'steps_confirmed':False},{'performed_on_vps':False},
                       {'version':None},
                       {'checked_at':(NOW+timedelta(seconds=1)).isoformat()},{'checked_at':'invalid'}):
            rows=reports();rows[0]['details_json'].update(change)
            with self.subTest(change=change):self.assertIn('backup_restore',build(campaign(),rows)['pending_free'])
        rows=reports();failed={**rows[0],'details_json':{**rows[0]['details_json'],'result':'failed'}}
        self.assertIn('backup_restore',build(campaign(),[failed,*rows])['pending_free'])

    def test_acceptance_history_survives_patch_updates(self):
        rows=reports();rows[0]['details_json'].update(version='1.02',commit='b'*40,checked_at=(NOW-timedelta(days=20)).isoformat())
        result=build(campaign(),rows)
        self.assertTrue(result['ready_for_public_launch'])
        item=next(x for x in result['checks'] if x['id']=='backup_restore')
        self.assertEqual(item['version'],'1.02');self.assertEqual(item['status'],'reported')

    def test_new_campaign_does_not_erase_completed_cycle(self):
        old=campaign()
        active=campaign(placement_status='active',completed_at=None)
        result=build(active,reports(),cycle_campaign=old)
        self.assertTrue(result['ready_for_public_launch'])
        cycle=next(x for x in result['checks'] if x['id']=='campaign_cycle')
        self.assertEqual(cycle['campaign_id'],old['campaign_id'])
        self.assertNotEqual(cycle['campaign_id'],active['campaign_id'])
        self.assertIn('campaign_cycle',build(active,reports(),cycle_campaign={})['pending_free'])

    def test_ci_must_match_installed_commit(self):
        for ci in (None,dict(verified=True,commit='b'*40),dict(verified=False,commit=SHA)):
            result=build_launch_readiness({},campaign(),reports(),version='1.04',commit=SHA,ci=ci,now=NOW)
            self.assertFalse(result['ci_verified']);self.assertFalse(result['ready_for_public_launch'])


class LaunchReadinessApiTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides.clear();self.client=TestClient(main.app);self.path='/api/v1/admin/launch-readiness'
    def tearDown(self):
        main.app.dependency_overrides.clear();self.client.close()
    def payload(self, **changes):
        return dict(request_id=str(uuid4()),check_id='backup_restore',result='passed',
            checked_at=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(),
            note='Restored separate database and files; records and login checked.',
            steps_confirmed=True,performed_on_vps=True,**changes)
    def admin(self):main.app.dependency_overrides[main.require_admin]=lambda:dict(id=ADMIN,role='admin')

    def test_unauthenticated_and_nonadmin_before_db(self):
        for role in (None,'operator','advertiser'):
            if role:main.app.dependency_overrides[main.get_current_user]=lambda:dict(id=ADMIN,role=role)
            with patch('app.main.psycopg.connect') as db:
                for method,path,body in [('GET',self.path,None),('POST',self.path+'/checks',self.payload())]:
                    r=self.client.request(method,path,json=body)
                    self.assertEqual(r.status_code,403 if role else 401,r.text)
                db.assert_not_called()

    def test_get_uses_read_only_snapshot_and_never_exports_secrets(self):
        self.admin();conn=MagicMock();db=conn.__enter__.return_value
        db.execute.return_value.fetchone.side_effect=[{'nodes_active':1},None,None]
        db.execute.return_value.fetchall.return_value=[]
        with patch('app.main.psycopg.connect',return_value=conn),patch('app.main.control.read',return_value={}),patch('app.release_ci.lookup',return_value=None):
            r=self.client.get(self.path)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['statistics']['nodes_active'],1)
        self.assertEqual(r.headers['cache-control'],'private, no-store')
        queries=[call.args[0].strip() for call in db.execute.call_args_list]
        self.assertIn('READ ONLY',queries[0]);self.assertEqual(len(queries),5)
        for query in queries[1:]:
            self.assertTrue(query.startswith('SELECT'));self.assertNotIn('ad_tag',query)
            self.assertNotIn('SELECT *',query);self.assertNotIn('INSERT',query);self.assertNotIn('DELETE',query)

    def test_invalid_reports_rejected_before_db(self):
        self.admin()
        variants=[{'check_id':'real_sponsor'},{'check_id':'ci_acceptance'},{'result':'passed','steps_confirmed':False},
            {'performed_on_vps':False},{'note':'   too short   '},{'checked_at':'2026-01-01T00:00:00'},
            {'checked_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()},
            {'checked_at':(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()},
            {'version':'spoofed'}, {'request_id':'bad'}]
        for change in variants:
            with self.subTest(change=change),patch('app.main.psycopg.connect') as db:
                r=self.client.post(self.path+'/checks',json={**self.payload(),**change});self.assertEqual(r.status_code,422,r.text);db.assert_not_called()

    def test_report_version_is_server_assigned_and_retry_is_idempotent(self):
        self.admin();body=self.payload();conn=MagicMock();db=conn.__enter__.return_value
        record=dict(id=UUID(body['request_id']),created_at=datetime.now(timezone.utc))
        db.execute.return_value.fetchone.return_value=record
        with patch('app.main.psycopg.connect',return_value=conn),patch('app.main.control.read',return_value=dict(state='succeeded',commit=SHA)):
            r=self.client.post(self.path+'/checks',json=body)
            self.assertEqual(r.status_code,201,r.text);self.assertEqual(r.json()['version'],main.app.version)
            import json
            saved=json.loads(db.execute.call_args.args[1][2]);self.assertEqual(saved['commit'],SHA);self.assertEqual(saved['version'],main.app.version)
            db.execute.return_value.fetchone.side_effect=[None,{**record,'actor_user_id':ADMIN,'event_type':'launch_check_recorded','details_json':saved}]
            self.assertEqual(self.client.post(self.path+'/checks',json=body).status_code,200)
            db.execute.return_value.fetchone.side_effect=[None,{**record,'actor_user_id':ADMIN,'event_type':'launch_check_recorded','details_json':saved}]
            self.assertEqual(self.client.post(self.path+'/checks',json={**body,'note':'Changed report with same request key should be rejected'}).status_code,409)

    def test_database_failure_503(self):
        self.admin()
        with patch('app.main.psycopg.connect',side_effect=main.psycopg.OperationalError('offline')):
            self.assertEqual(self.client.get(self.path).status_code,503)
            self.assertEqual(self.client.post(self.path+'/checks',json=self.payload()).status_code,503)

    def test_ui_no_fixed_failure_or_markup_injection(self):
        js=(ROOT/'api/web/app.js').read_text();html=(ROOT/'api/web/index.html').read_text()
        self.assertNotIn('Готовность к публичному запуску: НЕ ПОДТВЕРЖДЕНА',js)
        self.assertIn('data.ready_for_free_pilot',js);self.assertIn('data.ready_for_public_launch',js)
        self.assertIn('Экспериментальное ядро — отдельные испытания',html)
        self.assertIn('launch-check-form',html);self.assertNotIn('innerHTML',js)


if __name__=='__main__':unittest.main()
