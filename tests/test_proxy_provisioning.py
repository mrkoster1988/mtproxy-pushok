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
            conn.execute("UPDATE proxy_provisioning_settings SET enabled=true,max_instances=2,wait_minutes=120,first_port=19443,revision=gen_random_uuid(),updated_by=%s,evaluated_at=NULL,decision='waiting'",(self.admin,))
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
