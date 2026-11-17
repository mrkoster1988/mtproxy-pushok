"""Автосоздание: реальные транзакции БД, изолированный host и синтетический Telegram."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
import test_wallet as wallet_fixture
from app import proxy_provisioning as provision, channel_ai_connection as ai, control, main
from app import channel_topic_suggestions as transport

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import proxy_provisioner as host


class AIProvisioningTests(unittest.TestCase):
    def test_only_bounded_enum_and_no_commands_or_private_data(self):
        transport._ai_calls.clear()
        def response(value,**extra):
            return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)},**extra}]}).encode()
        with patch.object(transport,'read_url',return_value=response({'action':'create_proxy'})) as remote:
            self.assertEqual(provision.advise({'delayed_campaigns':2},('secret','model')),'create_proxy')
        body=json.loads(remote.call_args.args[0].data)
        self.assertNotIn('tools',body);self.assertFalse(body['store']);self.assertNotIn('secret',str(body))
        for result in (response({'action':'run_shell'}),response({'action':'create_proxy','command':'rm -rf /'}),
                       response({'action':'wait'},finish_reason='length'),response([]),b'invalid'):
            with patch.object(transport,'read_url',return_value=result),self.assertRaises((ValueError,KeyError)):
                provision.advise({},('key','model'))


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class ProvisioningIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request
    campaign=wallet_fixture.WalletIntegration.campaign

    def setUp(self):
        wallet_fixture.WalletIntegration.setUp(self)
        self.actor={'id':self.admin,'role':'admin','is_owner':True}
        self.node=uuid4();self.host='8.8.8.8'
        self.target=self.campaign();self.blocker=self.campaign()
        with psycopg.connect(self.dsn) as conn:
            old_nodes=[row[0] for row in conn.execute('DELETE FROM proxy_provisioning_jobs RETURNING proxy_node_id').fetchall()]
            conn.execute('DELETE FROM proxy_nodes WHERE id=ANY(%s)',(old_nodes,))
            conn.execute('UPDATE users SET is_owner=false WHERE is_owner')
            conn.execute('UPDATE users SET is_owner=true WHERE id=%s',(self.admin,))
            conn.execute('UPDATE campaigns SET auto_schedule_paused=true')
            conn.execute("UPDATE proxy_nodes SET status='paused'")
            # Старый экземпляр того же IP остаётся как paused запись.
            port=conn.execute('SELECT COALESCE(max(port),8000)+1 FROM proxy_nodes WHERE host=%s',(self.host,)).fetchone()[0]
            conn.execute("INSERT INTO proxy_nodes(id,name,host,port,status,target_engine) VALUES(%s,'Primary',%s,%s,'active','official_mtproxy')",(self.node,self.host,port))
            conn.execute('UPDATE sponsor_channels SET telegram_channel_id=-100123456789 WHERE id=%s',(self.channel,))
            conn.execute("UPDATE campaigns SET payment_status='verified',auto_schedule_paused=false WHERE id=ANY(%s)",([self.target,self.blocker],))
            conn.execute("UPDATE campaigns SET placement_status='active',proxy_node_id=%s,scheduled_start_at=now()-interval '1 minute',scheduled_end_at=now()+interval '5 hours' WHERE id=%s",(self.node,self.blocker))
            conn.execute("UPDATE proxy_provisioning_settings SET enabled=true,reserve_enabled=false,max_instances=2,wait_minutes=120,first_port=19443,revision=gen_random_uuid(),updated_by=%s,evaluated_at=NULL,decision='waiting'",(self.admin,))
            conn.execute('UPDATE proxy_gateway_settings SET enabled=false')
        control.write(ai.FILE,dict(enabled=True,key='test-private',model='model',revision=str(uuid4()),capabilities=['proxy_provisioning']))
        self.addCleanup(lambda:(control.root()/ai.FILE).unlink(missing_ok=True))
        patcher=patch.object(provision,'advise',return_value='create_proxy');self.advice=patcher.start();self.addCleanup(patcher.stop)

    def call(self,**values):
        request=dict(action='claim',host=self.host,resources_ok=True,free_ports=[19443,19444])|values
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            return provision.handle(conn,request)

    def execute(self,sql,*params):
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            return conn.execute(sql,params).fetchall() if sql.lstrip().upper().startswith('SELECT') else conn.execute(sql,params)

    def test_prepare_paused_then_activate_only_after_verified_registration(self):
        job=self.call();self.assertEqual(job['state'],'provision')
        node=self.execute('SELECT * FROM proxy_nodes WHERE id=%s',job['node_id'])[0]
        self.assertEqual(node['status'],'paused');self.assertEqual(node['port'],19443)
        self.assertFalse(self.call(action='complete',job_id=job['job_id'],ready=False,tag='a'*32)['accepted'])
        self.assertTrue(self.call(action='complete',job_id=job['job_id'],ready=True,tag='a'*32)['accepted'])
        self.assertEqual(self.execute('SELECT status FROM proxy_nodes WHERE id=%s',job['node_id'])[0]['status'],'active')
        self.assertEqual(self.call()['reason'],'daily_limit')
        self.assertEqual(self.advice.call_count,1)
        public=self.request('GET','/admin/proxy-provisioning')
        self.assertEqual(public['jobs'][0]['state'],'active');self.assertNotIn('test-private',str(public));self.assertNotIn('a'*32,str(public))

    def test_no_new_proxy_when_existing_capacity_is_free(self):
        self.execute("UPDATE campaigns SET placement_status='completed' WHERE id=%s",self.blocker)
        self.assertEqual(self.call()['reason'],'no_demand');self.advice.assert_not_called()
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],0)

    def enable_reserve(self):
        saved=self.request('GET','/admin/proxy-provisioning')
        body={key:saved[key] for key in ('enabled','max_instances','wait_minutes','first_port','revision')}
        current=self.request('PUT','/admin/proxy-provisioning',json=body|{'reserve_enabled':True})
        self.assertTrue(current['reserve_enabled'])
        self.execute('UPDATE campaigns SET auto_schedule_paused=true')

    def test_reserve_without_campaigns_requires_verified_registration_and_is_created_once(self):
        self.enable_reserve()
        primary=self.execute('SELECT * FROM proxy_nodes WHERE id=%s',self.node)[0]
        tag=uuid4().hex
        job=self.call(); self.assertEqual(job['state'],'provision')
        self.assertEqual(self.execute('SELECT status FROM proxy_nodes WHERE id=%s',job['node_id'])[0]['status'],'paused')
        self.assertEqual(self.call()['reason'],'retry_later')
        self.assertFalse(self.call(action='complete',job_id=job['job_id'],ready=False,tag=tag)['accepted'])
        self.assertTrue(self.call(action='complete',job_id=job['job_id'],ready=True,tag=tag)['accepted'])
        self.assertTrue(self.call(action='complete',job_id=job['job_id'],ready=True,tag=tag)['accepted'])
        self.assertEqual(self.call()['reason'],'daily_limit')
        self.execute("UPDATE proxy_provisioning_jobs SET created_at=now()-interval '2 days'")
        self.assertEqual(self.call()['reason'],'no_demand')
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],1)
        self.assertEqual(self.request('GET','/admin/proxy-provisioning')['jobs'][0]['reason'],'reserve')
        self.assertEqual(self.execute("SELECT count(*) AS n FROM audit_events WHERE event_type='proxy_created_for_reserve' AND details_json->>'node_id'=%s",job['node_id'])[0]['n'],1)
        self.assertEqual(self.execute('SELECT * FROM proxy_nodes WHERE id=%s',self.node)[0],primary)
        self.advice.assert_not_called()

    def test_reserve_claim_keeps_global_lock_and_does_not_duplicate(self):
        self.enable_reserve()
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            first=provision.handle(conn,dict(action='claim',host=self.host,resources_ok=True,free_ports=[19443,19444]))
            self.assertEqual(first['state'],'provision')
            self.assertEqual(self.call(),{'state':'wait'})
        self.assertEqual(self.call()['reason'],'retry_later')
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],1)
        self.advice.assert_not_called()

    def test_reserve_preserves_permission_resource_and_retry_guards(self):
        self.enable_reserve()
        self.assertEqual(self.call(resources_ok=False)['reason'],'resources_low')
        self.execute('UPDATE proxy_provisioning_settings SET enabled=false')
        self.assertEqual(self.call()['state'],'disabled')
        self.execute('UPDATE proxy_provisioning_settings SET enabled=true')
        with patch.object(ai,'active',return_value=None):
            self.assertEqual(self.call()['state'],'disabled')
        self.execute('UPDATE users SET blocked_at=now() WHERE id=%s',self.admin)
        self.assertEqual(self.call()['state'],'disabled')
        self.assertEqual(self.call(action='inspect')['policy'],'owner_unavailable')
        self.execute('UPDATE users SET blocked_at=NULL WHERE id=%s',self.admin)
        first=self.call()
        for attempt in range(1,6):
            job=first if attempt==1 else self.call()
            self.assertEqual(job['node_id'],first['node_id'])
            self.call(action='failed',job_id=job['job_id'],code='telegram_unavailable')
            self.execute("UPDATE proxy_provisioning_jobs SET retry_at=now()-interval '1 second'")
        self.assertEqual(self.call()['reason'],'limit_reached')
        row=self.execute('SELECT * FROM proxy_provisioning_jobs')[0]
        self.assertEqual((row['reason'],row['attempts'],row['state']),('reserve',5,'failed'))
        self.advice.assert_not_called()

    def test_reserve_does_not_replace_existing_demand_instance(self):
        job=self.call(); self.assertEqual(job['state'],'provision')
        self.assertTrue(self.call(action='complete',job_id=job['job_id'],ready=True,tag='d'*32)['accepted'])
        self.advice.reset_mock(); self.enable_reserve()
        self.execute("UPDATE proxy_provisioning_jobs SET created_at=now()-interval '2 days'")
        self.assertEqual(self.call()['reason'],'no_demand')
        self.execute('UPDATE proxy_provisioning_settings SET max_instances=1')
        self.assertEqual(self.call()['reason'],'limit_reached')
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],1)
        self.advice.assert_not_called()

    def test_reserve_skips_gateway_ports_before_gateway_starts(self):
        self.enable_reserve()
        self.execute('UPDATE proxy_gateway_settings SET enabled=true,interests_enabled=false,port=19443')
        self.assertIn(19443,self.call(action='inspect')['reserved_ports'])
        self.assertEqual(self.call()['port'],19444)
        self.advice.assert_not_called()

    def test_reserve_cannot_use_topic_or_domain_alias_ports(self):
        self.enable_reserve()
        self.execute('UPDATE proxy_gateway_settings SET enabled=true,interests_enabled=true,port=19442')
        self.assertEqual(self.call()['reason'],'port_busy')
        self.execute('UPDATE proxy_gateway_settings SET enabled=false')
        self.execute("UPDATE proxy_nodes SET host='proxy.example.org',port=19443 WHERE id=%s",self.node)
        self.assertIn(19443,self.call(action='inspect',primary_host='proxy.example.org')['reserved_ports'])
        self.assertEqual(self.call(primary_host='proxy.example.org')['port'],19444)
        self.advice.assert_not_called()

    def test_queue_explains_delayed_free_empty_and_missing_nodes_without_mutations(self):
        before=self.execute('SELECT * FROM proxy_provisioning_settings')[0]
        report=self.call(action='inspect')['queue']
        self.assertEqual((report['reason'],report['campaign_count'],report['delayed_count']),('delayed',1,1))
        self.assertGreater(report['max_delay_seconds'],17000)
        self.execute("UPDATE campaigns SET placement_status='completed' WHERE id=%s",self.blocker)
        report=self.call(action='inspect')['queue']
        self.assertEqual((report['reason'],report['delayed_count'],report['max_delay_seconds']),('below_threshold',0,0))
        self.execute('UPDATE campaigns SET auto_schedule_paused=true WHERE id=%s',self.target)
        self.assertEqual(self.call(action='inspect')['queue']['reason'],'no_campaigns')
        self.execute("UPDATE proxy_nodes SET status='paused' WHERE id=%s",self.node)
        self.assertEqual(self.call(action='inspect')['queue']['reason'],'no_nodes')
        self.assertEqual(self.execute('SELECT * FROM proxy_provisioning_settings')[0],before)
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],0)
        self.advice.assert_not_called()

    def test_queue_respects_domain_alias_and_free_second_node(self):
        self.execute("UPDATE proxy_nodes SET host='proxy.example.org' WHERE id=%s",self.node)
        report=self.call(action='inspect',primary_host='proxy.example.org')['queue']
        self.assertEqual((report['node_count'],report['delayed_count']),(1,1))
        self.execute("INSERT INTO proxy_nodes(id,name,host,port,status,target_engine) VALUES(%s,'Free',%s,19445,'active','official_mtproxy')",uuid4(),self.host)
        report=self.call(action='inspect',primary_host='proxy.example.org')['queue']
        self.assertEqual((report['node_count'],report['delayed_count'],report['max_delay_seconds']),(2,0,0))

    def test_distant_future_does_not_hide_current_demand_and_scan_limit_is_explicit(self):
        ids=[self.campaign() for _ in range(101)]
        self.execute("UPDATE campaigns SET payment_status='verified',auto_schedule_paused=false,requested_start_at=now()+interval '2 days',created_at=now()-interval '1 day' WHERE id=ANY(%s)",ids)
        report=self.call(action='inspect')['queue']
        self.assertEqual(report['campaign_count'],1); self.assertFalse(report['limited'])
        self.assertEqual(report['delayed_count'],1)
        self.execute('UPDATE campaigns SET requested_start_at=NULL WHERE id=ANY(%s)',ids)
        report=self.call(action='inspect')['queue']
        self.assertEqual(report['campaign_count'],100); self.assertTrue(report['limited'])
        self.assertEqual(report['delayed_count'],100)

    def test_queue_threshold_uses_requested_start_and_ignores_own_reserved_period(self):
        self.execute("UPDATE campaigns SET requested_start_at=now()+interval '4 hours',placement_status='scheduled',auto_schedule_managed=true,proxy_node_id=%s,scheduled_start_at=now()+interval '6 hours',scheduled_end_at=now()+interval '30 hours' WHERE id=%s",self.node,self.target)
        report=self.call(action='inspect')['queue']
        self.assertEqual(report['reason'],'below_threshold')
        self.assertTrue(3500 < report['max_delay_seconds'] <= 3600)

    def test_ai_wait_is_preserved_in_public_report(self):
        from datetime import datetime,timezone
        self.advice.return_value='wait'
        self.assertEqual(self.call()['reason'],'wait')
        revision=str(self.execute('SELECT revision FROM proxy_provisioning_settings')[0]['revision'])
        self.addCleanup(lambda:(control.root()/'proxy-provisioner.json').unlink(missing_ok=True))
        control.write('proxy-provisioner.json',dict(code='wait',checked_at=datetime.now(timezone.utc).isoformat(),policy_revision=revision))
        saved=self.request('GET','/admin/proxy-provisioning')
        self.assertEqual(saved['agent']['code'],'wait'); self.assertTrue(saved['agent']['current_policy'])

    def test_demand_withdrawn_during_ai_has_a_real_wait_reason(self):
        def withdraw(*_):
            self.execute("UPDATE campaigns SET placement_status='completed' WHERE id=%s",self.blocker)
            return 'create_proxy'
        self.advice.side_effect=withdraw
        self.assertEqual(self.call()['reason'],'no_demand')
        self.assertEqual(self.execute('SELECT decision FROM proxy_provisioning_settings')[0]['decision'],'no_demand')
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],0)

    def test_full_queue_to_host_registration_binding_and_full_period(self):
        from app import auto_schedule, telegram_binding
        self.addCleanup(lambda:self.execute("UPDATE campaigns SET placement_status='not_scheduled',proxy_node_id=NULL,scheduled_start_at=NULL,scheduled_end_at=NULL WHERE id=%s",self.target))
        auto_schedule.service_once(self.dsn)
        previous=self.execute('SELECT * FROM campaigns WHERE id=%s',self.target)[0]
        self.assertEqual(previous['proxy_node_id'],self.node)
        job=self.call(); self.assertEqual(job['state'],'provision')
        def cleanup_bindings():
            with psycopg.connect(self.dsn) as conn:
                for table in ('telegram_binding_events','telegram_binding_jobs','channel_promotion_bindings'):
                    conn.execute('DELETE FROM '+table+' WHERE proxy_node_id=%s',(job['node_id'],))
        self.addCleanup(cleanup_bindings)
        cfg=dict(node_id=job['node_id'],host=self.host,port=job['port'],secret='b'*32)
        def exchange(request):
            with psycopg.connect(self.dsn,row_factory=dict_row) as conn:return provision.handle(conn,request)
        with tempfile.TemporaryDirectory() as temporary:
            receipt=Path(temporary)/'receipt.json'
            with patch.object(host,'create_instance',return_value=cfg), patch.object(host,'install_instance'), \
                 patch.object(host,'ready',return_value=True), patch.object(host,'command'), \
                 patch.object(host,'stop_instance') as stop, patch.object(host,'exchange',side_effect=exchange), \
                 patch.object(host,'save_receipt',side_effect=lambda *_:receipt.write_text('ready')), \
                 patch.object(host,'receipt_path',return_value=receipt), \
                 patch('automatic_binding.helper',return_value=dict(ok=True,tag='b'*32)) as telegram:
                host.provision(job)
                telegram.assert_called_once(); stop.assert_not_called(); self.assertFalse(receipt.exists())
        self.assertEqual(self.execute('SELECT status FROM proxy_nodes WHERE id=%s',job['node_id'])[0]['status'],'active')
        def bind(action,**extra):
            with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
                return telegram_binding.handle(conn,dict(action=action,host=self.host,port=job['port'])|extra)
        binding=bind('owner_claim'); self.assertEqual(binding['state'],'job')
        proof=dict(job_id=binding['job_id'],tag='b'*32,telegram_channel_id=123456789,owner_telegram_id=456,
                   bot_message_id=789,channel_username=binding['channel_url'].rstrip('/').rsplit('/',1)[-1].lower())
        self.assertTrue(bind('owner_authorize',**proof)['accepted'])
        self.assertTrue(bind('owner_verified',**proof)['accepted'])
        self.assertFalse(bind('owner_verified',**proof)['accepted'])  # Пауза Telegram обязательна.
        self.execute("UPDATE telegram_binding_jobs SET retry_at=now()-interval '1 second' WHERE id=%s",binding['job_id'])
        self.assertTrue(bind('owner_verified',**proof)['accepted'])
        auto_schedule.service_once(self.dsn)
        current=self.execute('SELECT * FROM campaigns WHERE id=%s',self.target)[0]
        self.assertEqual(str(current['proxy_node_id']),job['node_id'])
        self.assertLess(current['scheduled_start_at'],previous['scheduled_start_at'])
        self.assertEqual((current['scheduled_end_at']-current['scheduled_start_at']).total_seconds(),current['duration_seconds_snapshot'])
        self.assertEqual(self.execute('SELECT placement_status FROM campaigns WHERE id=%s',self.blocker)[0]['placement_status'],'active')
        self.assertEqual(self.call()['reason'],'daily_limit')

    def test_readiness_uses_read_only_transaction_and_does_not_evaluate_or_claim(self):
        before=self.execute('SELECT * FROM proxy_provisioning_settings')[0]
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            result=provision.handle(conn,dict(action='inspect',host=self.host))
            self.assertEqual(conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'],'on')
        self.assertEqual(result['policy'],'ready'); self.assertEqual(result['capacity'],'ready')
        self.assertEqual(self.execute('SELECT * FROM proxy_provisioning_settings')[0],before)
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],0)
        self.assertNotIn('test-private',json.dumps(result)); self.advice.assert_not_called()

    def test_readiness_explains_disabled_ai_pending_and_failed_preparation(self):
        job=self.call()
        before=self.execute('SELECT * FROM proxy_provisioning_jobs')[0]
        self.assertEqual(self.call(action='inspect')['capacity'],'preparation_pending')
        self.assertEqual(self.execute('SELECT * FROM proxy_provisioning_jobs')[0],before)
        self.execute("UPDATE proxy_provisioning_jobs SET state='failed' WHERE id=%s",job['job_id'])
        self.assertEqual(self.call(action='inspect')['capacity'],'preparation_failed')
        with patch.object(ai,'active',return_value=None):
            self.assertEqual(self.call(action='inspect')['policy'],'ai_unavailable')
        self.execute('UPDATE proxy_provisioning_settings SET enabled=false')
        self.assertEqual(self.call(action='inspect')['policy'],'disabled')

    def test_reports_from_previous_policy_remain_outdated_even_when_written_after_save(self):
        from datetime import datetime,timezone
        for name in ('proxy-provisioner.json','proxy-readiness.json'):
            self.addCleanup(lambda name=name:(control.root()/name).unlink(missing_ok=True))
        saved=self.request('GET','/admin/proxy-provisioning')
        body={key:saved[key] for key in ('enabled','max_instances','wait_minutes','first_port','revision')}
        disabled=self.request('PUT','/admin/proxy-provisioning',json=body|{'enabled':False})
        cfg=self.call(action='settings')
        self.assertFalse(cfg['enabled']); self.assertEqual(cfg['policy_revision'],disabled['revision'])
        enabled=self.request('PUT','/admin/proxy-provisioning',json=body|{'revision':disabled['revision']})
        agent=dict(code='disabled',checked_at=datetime.now(timezone.utc).isoformat(),policy_revision=disabled['revision'])
        readiness=dict(schema=1,checked_at=agent['checked_at'],policy_revision=disabled['revision'],
                       checks={key:'ready' for key in provision.READINESS})
        readiness['checks']['policy']='disabled'
        control.write('proxy-provisioner.json',agent); control.write('proxy-readiness.json',readiness)
        current=self.request('GET','/admin/proxy-provisioning')
        self.assertTrue(current['enabled'])
        for report in ('agent','readiness'):
            self.assertTrue(current[report]['recent']); self.assertFalse(current[report]['current_policy'])
        agent.update(code='no_demand',policy_revision=enabled['revision'])
        readiness.update(policy_revision=enabled['revision']); readiness['checks']['policy']='ready'
        control.write('proxy-provisioner.json',agent); control.write('proxy-readiness.json',readiness)
        current=self.request('GET','/admin/proxy-provisioning')
        self.assertTrue(current['agent']['current_policy']); self.assertTrue(current['readiness']['current_policy'])
        self.assertEqual(self.call(action='inspect')['policy_revision'],enabled['revision'])
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],0)
        self.advice.assert_not_called()

    def test_unversioned_report_cannot_confirm_new_settings(self):
        from datetime import datetime,timezone
        self.addCleanup(lambda:(control.root()/'proxy-provisioner.json').unlink(missing_ok=True))
        control.write('proxy-provisioner.json',dict(code='ready',checked_at=datetime.now(timezone.utc).isoformat()))
        report=self.request('GET','/admin/proxy-provisioning')['agent']
        self.assertTrue(report['recent']); self.assertFalse(report['current_policy'])

    def test_unpaid_paused_rejected_unknown_or_distant_channel_do_not_create(self):
        for change in ("payment_status='awaiting_payment'","moderation_status='rejected'","auto_schedule_paused=true", "requested_start_at=now()+interval '2 days'"):
            self.execute('UPDATE campaigns SET '+change+' WHERE id=%s',self.target)
            self.execute('UPDATE proxy_provisioning_settings SET evaluated_at=NULL')
            self.assertEqual(self.call()['reason'],'no_demand',change)
            self.execute("UPDATE campaigns SET payment_status='verified',moderation_status='approved',auto_schedule_paused=false,requested_start_at=NULL WHERE id=%s",self.target)
        self.execute('UPDATE sponsor_channels SET telegram_channel_id=NULL WHERE id=%s',self.channel)
        self.execute('UPDATE proxy_provisioning_settings SET evaluated_at=NULL')
        self.assertEqual(self.call()['reason'],'no_demand');self.advice.assert_not_called()

    def test_retries_preserve_instance_and_stop_after_five_failures(self):
        first=self.call()
        for attempt in range(1,6):
            job=first if attempt==1 else self.call()
            self.assertEqual(job['node_id'],first['node_id'])
            self.call(action='failed',job_id=job['job_id'],code='telegram_unavailable')
            self.execute("UPDATE proxy_provisioning_jobs SET retry_at=now()-interval '1 second'")
        self.assertEqual(self.call()['reason'],'limit_reached')
        row=self.execute('SELECT * FROM proxy_provisioning_jobs')[0]
        self.assertEqual(row['attempts'],5);self.assertEqual(row['state'],'failed')
        self.assertEqual(self.advice.call_count,1)

    def test_policy_change_during_preparation_requires_explicit_resume(self):
        job=self.call()
        saved=self.request('GET','/admin/proxy-provisioning')
        body={k:saved[k] for k in ('enabled','max_instances','wait_minutes','first_port','revision')}
        self.request('PUT','/admin/proxy-provisioning',json=body)
        self.assertFalse(self.call(action='authorize',job_id=job['job_id'])['accepted'])
        self.assertFalse(self.call(action='complete',job_id=job['job_id'],ready=True,tag='c'*32)['accepted'])
        self.request('POST','/admin/proxy-provisioning/'+job['job_id']+'/retry')
        self.assertEqual(self.call()['node_id'],job['node_id'])

    def test_disable_capability_resource_shortage_and_owner_revocation_stop_actions(self):
        self.assertEqual(self.call(resources_ok=False)['reason'],'resources_low')
        self.execute('UPDATE users SET is_owner=false WHERE id=%s',self.admin)
        self.assertEqual(self.call()['state'],'disabled')
        self.execute('UPDATE users SET is_owner=true WHERE id=%s',self.admin)
        cfg=ai.current();cfg['capabilities']=[];control.write(ai.FILE,cfg)
        self.assertEqual(self.call()['state'],'disabled');self.advice.assert_not_called()

    def test_ai_cannot_override_withdrawn_demand_or_changed_policy(self):
        def withdraw(*_):
            self.execute("UPDATE campaigns SET payment_status='refunded' WHERE id=%s",self.target)
            return 'create_proxy'
        self.advice.side_effect=withdraw
        self.assertNotEqual(self.call()['state'],'provision')
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],0)

    def test_ai_outage_and_wait_are_rate_limited_without_new_nodes(self):
        self.advice.side_effect=OSError('private API error')
        self.assertEqual(self.call()['reason'],'ai_unavailable')
        self.assertEqual(self.call()['reason'],'evaluation_cooldown')
        self.assertEqual(self.advice.call_count,1)
        self.execute('UPDATE proxy_provisioning_settings SET evaluated_at=NULL')
        self.advice.side_effect=None;self.advice.return_value='wait'
        self.assertEqual(self.call()['reason'],'wait')
        self.assertEqual(self.execute('SELECT count(*) AS n FROM proxy_provisioning_jobs')[0]['n'],0)

    def test_existing_domain_primary_and_second_free_node(self):
        self.execute("UPDATE proxy_nodes SET host='proxy.example.org' WHERE id=%s",self.node)
        job=self.call(primary_host='proxy.example.org')
        self.assertEqual(job['state'],'provision')

    def test_owner_only_settings_and_revision_validation(self):
        saved=self.request('GET','/admin/proxy-provisioning')
        body={k:saved[k] for k in ('enabled','max_instances','wait_minutes','first_port','revision')}
        for role in ('advertiser','operator','admin'):
            self.actor={'id':self.other,'role':role,'is_owner':False}
            self.request('GET','/admin/proxy-provisioning',403)
            self.request('PUT','/admin/proxy-provisioning',403,json=body)
        self.actor={'id':self.admin,'role':'admin','is_owner':True}
        self.request('PUT','/admin/proxy-provisioning',422,json=body|{'max_instances':1000})
        self.request('PUT','/admin/proxy-provisioning',409,json=body|{'revision':str(uuid4())})

    def test_manual_retry_cannot_shorten_telegram_pause_even_after_settings_change(self):
        job=self.call()
        self.call(action='failed',job_id=job['job_id'],code='telegram_flood_wait',retry_seconds=900000)
        before=self.execute('SELECT * FROM proxy_provisioning_jobs')[0]
        self.assertGreater((before['retry_at']-before['updated_at']).total_seconds(),899990)
        self.execute('UPDATE proxy_provisioning_settings SET revision=gen_random_uuid()')
        self.request('POST','/admin/proxy-provisioning/'+job['job_id']+'/retry',409)
        after=self.execute('SELECT * FROM proxy_provisioning_jobs')[0]
        self.assertEqual(before,after)
        self.execute("UPDATE proxy_provisioning_jobs SET retry_at=now()-interval '1 second'")
        self.request('POST','/admin/proxy-provisioning/'+job['job_id']+'/retry')
        self.assertEqual(self.call()['node_id'],job['node_id'])

    def test_complete_is_idempotent_and_recovery_works_with_automation_disabled(self):
        job=self.call()
        complete=dict(action='complete',job_id=job['job_id'],tag='f'*32,ready=True)
        self.assertTrue(self.call(**complete)['accepted'])
        self.execute('UPDATE proxy_provisioning_settings SET enabled=false')
        self.assertTrue(self.call(**complete)['accepted'])
        self.assertFalse(self.call(**(complete|{'tag':'e'*32}))['accepted'])
        self.assertEqual(self.call(action='recover',node_id=job['node_id'],port=job['port'])['state'],'active')
        self.assertFalse(self.call(action='interrupted',job_id=job['job_id'])['accepted'])
        self.assertEqual(self.execute("SELECT count(*) AS n FROM audit_events WHERE event_type='proxy_created_by_ai' AND details_json->>'node_id'=%s",job['node_id'])[0]['n'],1)

    def test_pending_recovery_checks_endpoint_policy_and_terminal_attempt(self):
        job=self.call()
        recover=dict(action='recover',node_id=job['node_id'],port=job['port'])
        self.assertEqual(self.call(**recover)['state'],'pending')
        self.assertEqual(self.call(**(recover|{'port':1}))['state'],'unknown')
        self.assertEqual(self.call(**(recover|{'host':'1.1.1.1'}))['state'],'unknown')
        self.execute('UPDATE proxy_provisioning_jobs SET attempts=5')
        self.assertTrue(self.call(action='interrupted',job_id=job['job_id'])['accepted'])
        row=self.execute('SELECT * FROM proxy_provisioning_jobs')[0]
        self.assertEqual((row['state'],row['error_code']),('failed','preparation_interrupted'))
        self.assertEqual(self.call(**recover)['state'],'blocked')

    def test_disabled_pending_job_can_be_cleaned_but_never_activated(self):
        job=self.call()
        self.execute('UPDATE proxy_provisioning_settings SET enabled=false')
        self.assertEqual(self.call(action='recover',node_id=job['node_id'],port=job['port'])['state'],'blocked')
        self.assertFalse(self.call(action='complete',job_id=job['job_id'],tag='a'*32,ready=True)['accepted'])
        self.assertTrue(self.call(action='interrupted',job_id=job['job_id'])['accepted'])
        row=self.execute('SELECT * FROM proxy_provisioning_jobs')[0]
        self.assertEqual((row['state'],row['error_code']),('failed','policy_changed'))

    def test_kill_before_manifest_on_last_attempt_does_not_leave_pending_forever(self):
        self.call()
        self.execute("UPDATE proxy_provisioning_jobs SET attempts=5,retry_at=now()-interval '1 second'")
        self.assertEqual(self.call()['reason'],'preparation_interrupted')
        self.assertEqual(self.execute('SELECT state FROM proxy_provisioning_jobs')[0]['state'],'failed')

    def test_manually_activated_node_is_not_eligible_for_interrupted_cleanup(self):
        job=self.call()
        self.execute("UPDATE proxy_nodes SET status='active' WHERE id=%s",job['node_id'])
        self.assertEqual(self.call(action='recover',node_id=job['node_id'],port=job['port'])['state'],'unknown')
        self.assertFalse(self.call(action='interrupted',job_id=job['job_id'])['accepted'])
        self.addCleanup(lambda:self.execute('UPDATE campaigns SET proxy_node_id=%s WHERE id=%s',self.node,self.blocker))
        self.execute('UPDATE campaigns SET proxy_node_id=%s WHERE id=%s',job['node_id'],self.blocker)
        self.execute("UPDATE proxy_nodes SET status='paused' WHERE id=%s",job['node_id'])
        self.assertEqual(self.call(action='recover',node_id=job['node_id'],port=job['port'])['state'],'unknown')

    def test_duplicate_tag_is_explicit_failure_for_recovery(self):
        job=self.call()
        self.execute("INSERT INTO proxy_sponsor_registrations(proxy_node_id,engine,ad_tag,registered_by_user_id) VALUES(%s,'official_mtproxy',%s,%s)",self.node,'e'*32,self.admin)
        self.assertEqual(self.call(action='complete',job_id=job['job_id'],tag='e'*32,ready=True),{'accepted':False,'reason':'registration_conflict'})
        self.assertEqual(self.execute('SELECT state FROM proxy_provisioning_jobs')[0]['state'],'pending')


class ProvisionerHostTests(unittest.TestCase):
    def setUp(self):
        self.job=dict(node_id=str(uuid4()),job_id=str(uuid4()),host='8.8.8.8',port=9443)
        self.cfg=self.job|{'secret':'a'*32,'stats_port':20000}

    def test_untrusted_identifier_never_reaches_systemd(self):
        for value in ('../../primary','x;reboot',str(uuid4())+'\n',''):
            with self.assertRaises(ValueError):host.unit(value)

    def test_real_private_manifest_reuses_secret_and_never_exceeds_three_instances(self):
        with tempfile.TemporaryDirectory() as temp:
            instances=Path(temp)/'instances'
            original_stat=Path.stat
            def root_stat(path,*args,**kwargs):
                info=list(original_stat(path,*args,**kwargs));info[4]=0
                return os.stat_result(info)
            def private(path):return path.read_text()
            with patch.object(host,'INSTANCES',instances),patch.object(host,'primary_host',return_value='8.8.8.8'),\
                 patch.object(host,'resource_ready',return_value=True),patch.object(host,'free_port',return_value=True),\
                 patch.object(host,'private_read',side_effect=private),patch.object(Path,'stat',root_stat):
                first=host.create_instance(self.job)
                self.assertEqual(first,host.create_instance(self.job))
                self.assertEqual((instances/self.job['node_id']/'instance.json').stat().st_mode&0o777,0o600)
                self.assertNotIn(first['secret'],host.unit(first['node_id']))
                for port in (9444,9445):host.create_instance(self.job|{'node_id':str(uuid4()),'port':port})
                with self.assertRaisesRegex(RuntimeError,'resources_low'):
                    host.create_instance(self.job|{'node_id':str(uuid4()),'port':9446})
                with self.assertRaises(ValueError):host.create_instance(self.job|{'port':9555})

    def test_flood_wait_is_never_shortened(self):
        self.assertEqual(host.ProvisionError('telegram_flood_wait',3600).retry_seconds,3600)
        self.assertEqual(host.ProvisionError('telegram_flood_wait',-1).retry_seconds,900)
        self.assertEqual(host.ProvisionError('telegram_flood_wait',900000).retry_seconds,900000)

    def test_stopped_additional_proxy_is_not_published_even_when_schedule_is_idle(self):
        import promotion_agent as agent
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'.control').mkdir()
            link=root/'.control'/('proxy-link-'+self.job['node_id']+'.json');link.write_text('{}')
            with patch.object(agent,'ROOT',root),patch.object(agent,'INSTANCE',self.job['node_id']),\
                 patch.object(agent,'main',side_effect=lambda status:status.update(code='no_current_campaign')),\
                 patch.object(agent,'publish_status'),patch.object(agent,'preserve_failure_notice'),patch.object(agent,'flush_failure_notice'),\
                 patch.object(agent,'publish_proxy_link') as publish,patch.object(host,'ready',return_value=False),\
                 patch.object(host,'read_instance',return_value=self.cfg):
                agent.run_cycle()
            publish.assert_not_called();self.assertFalse(link.exists())

    def test_registration_failure_stops_only_new_instance(self):
        with patch.object(host,'create_instance',return_value=self.cfg),patch.object(host,'install_instance'),\
             patch.object(host,'ready',return_value=True),patch.object(host,'exchange',return_value={'accepted':True}),\
             patch.object(host,'stop_instance') as stop,patch('automatic_binding.helper',return_value={'ok':False,'code':'registration_uncertain'}):
            with self.assertRaisesRegex(RuntimeError,'registration_uncertain'):host.provision(self.job)
        stop.assert_called_once_with(self.job['node_id'])

    def test_revocation_before_install_does_not_contact_telegram(self):
        with patch.object(host,'create_instance',return_value=self.cfg),patch.object(host,'install_instance') as install,\
             patch.object(host,'exchange',return_value={'accepted':False}),patch.object(host,'stop_instance'),\
             patch('automatic_binding.helper') as telegram:
            with self.assertRaisesRegex(RuntimeError,'policy_changed'):host.provision(self.job)
        install.assert_not_called();telegram.assert_not_called()

    def test_lost_final_ack_does_not_stop_possibly_activated_proxy(self):
        def exchange(request):
            if request['action']=='complete':raise OSError('lost reply')
            return {'accepted':True}
        with patch.object(host,'create_instance',return_value=self.cfg),patch.object(host,'install_instance'),\
             patch.object(host,'ready',return_value=True),patch.object(host,'exchange',side_effect=exchange),\
             patch.object(host,'command'),patch.object(host,'save_receipt') as receipt,patch.object(host,'stop_instance') as stop,\
             patch('automatic_binding.helper',return_value={'ok':True,'tag':'a'*32}):
            with self.assertRaises(OSError):host.provision(self.job)
        stop.assert_not_called()
        receipt.assert_called_once_with(self.job,'a'*32)

    def test_service_is_bounded_and_never_installs_model_commands(self):
        with patch.object(host,'BINARY',Mock(is_file=lambda:True)),patch.object(host,'ROOT',Path('/opt/mtproxy-pushok')),\
             patch.object(host,'write_unit') as writer,patch.object(host,'command') as command,patch.object(host.shutil,'which',return_value=None):
            host.install_instance(self.cfg)
        value=writer.call_args_list[0].args[1]
        self.assertIn('MemoryMax=384M',value);self.assertIn('CPUQuota=50%',value)
        self.assertNotIn(self.cfg['secret'],value);self.assertNotIn('bash -c',value)
        self.assertEqual(command.call_args.args,('systemctl','enable','--now',host.unit(self.job['node_id'])))
