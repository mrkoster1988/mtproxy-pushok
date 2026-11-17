"""Изоляция интересов, общий ключ и реальная маршрутизация HAProxy на loopback."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import test_postgres as fixture
import test_wallet as wallet
from app import proxy_gateway as api, proxy_link, main

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import proxy_gateway as host
import proxy_provisioner as provisioner


class GatewayTests(unittest.TestCase):
    def test_invalid_config_and_failed_reload_preserve_previous_config(self):
        settings=dict(port=10443,interests_enabled=False,topics=[])
        nodes=[dict(id=str(uuid4()),address='127.0.0.1',port=8443,topic=None)]
        old='global\n  maxconn 1024\n# previous working configuration\n'
        with tempfile.TemporaryDirectory() as tmp, patch.object(host,'DIRECTORY',Path(tmp)),patch.object(host,'CONFIG',Path(tmp)/'haproxy.cfg'),\
             patch.object(host,'active',return_value=True),patch.object(provisioner,'free_port',return_value=True),\
             patch.object(shutil,'which',return_value=None),patch.object(subprocess,'run') as rollback:
            host.CONFIG.write_text(old)
            with patch.object(provisioner,'command',side_effect=ValueError('invalid_config')),self.assertRaises(ValueError):
                host.apply(settings,nodes)
            self.assertEqual(host.CONFIG.read_text(),old);rollback.assert_not_called()
            def command(*args):
                if args[:2]==('systemctl','reload'):raise RuntimeError('reload_failed')
                return ''
            with patch.object(provisioner,'command',side_effect=command),self.assertRaises(RuntimeError):
                host.apply(settings,nodes)
            self.assertEqual(host.CONFIG.read_text(),old)
            self.assertEqual(rollback.call_args.args[0],['systemctl','reload',host.SERVICE])

    def test_disabled_gateway_stops_only_gateway_and_removes_link(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(host,'CONTROL',Path(tmp)),\
             patch.object(host,'load_proxy',return_value=('8.8.8.8',8443,'a'*32)),\
             patch.object(provisioner,'primary_host',return_value='8.8.8.8'),\
             patch.object(host,'exchange',return_value=dict(enabled=False,revision='x',port=10443,interests_enabled=False)),\
             patch.object(provisioner,'command') as command:
            (Path(tmp)/'proxy-gateway.json').write_text('{}')
            status={};host.tick(status)
            command.assert_called_once_with('systemctl','stop',host.SERVICE)
            self.assertFalse((Path(tmp)/'proxy-gateway.json').exists())
            self.assertEqual(status['code'],'disabled')

    def test_live_shared_link_priority_topic_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(proxy_link,'LINK_FILE',Path(tmp)/'proxy-link.json'):
            direct=proxy_link.connection_url('proxy.example.org',8443,'a'*32)
            gateway=direct.replace('8443','10443')
            proxy_link.LINK_FILE.write_text(json.dumps({'url':direct}))
            path=Path(tmp)/'proxy-gateway.json'
            data=dict(url=gateway,expires_at=time.time()+120,topic_ports={'technology':10444})
            path.write_text(json.dumps(data))
            self.assertEqual(proxy_link.current_proxy_url(),gateway)
            self.assertIn('port=10444',proxy_link.current_gateway_url('technology'))
            self.assertIsNone(proxy_link.current_gateway_url('other'))
            for expiry in (True,time.time()-1,time.time()+3600):
                path.write_text(json.dumps(dict(data,expires_at=expiry)))
                self.assertEqual(proxy_link.current_proxy_url(),direct)
            path.unlink();path.symlink_to(proxy_link.LINK_FILE)
            self.assertIsNone(proxy_link.current_gateway_url())

    def test_config_injection_recursion_and_stable_ports(self):
        settings=dict(port=10443,interests_enabled=True,topics=list(api.TOPICS))
        node=dict(id=str(uuid4()),address='127.0.0.1',port=8443,topic='technology')
        text=host.configuration(settings,[node])
        self.assertIn('bind 0.0.0.0:10444',text)
        self.assertEqual(len(host.ports(settings)),26)
        self.assertNotIn('log ',text)
        for bad in (dict(node,address='127.0.0.1\nlisten bad'),dict(node,port=10443),dict(node,id='bad')):
            with self.assertRaises(ValueError):host.configuration(settings,[bad])
        with self.assertRaises(ValueError):host.ports(dict(settings,port=19998))

    def test_running_process_must_accept_common_secret(self):
        raw=b'/opt/MTProxy/objs/bin/mtproto-proxy\x00-H\x008443\x00-S\x00'+b'a'*32+b'\x00-S\x00'+b'b'*32+b'\x00'
        with patch.object(provisioner,'command',return_value='42'),patch.object(Path,'read_bytes',return_value=raw):
            self.assertTrue(host.process_ready('primary',8443,'b'*32))
            self.assertFalse(host.process_ready('primary',8443,'c'*32))
            self.assertFalse(host.process_ready('primary',9443,'a'*32))

    def test_extra_instance_keeps_private_key_and_accepts_primary(self):
        node=str(uuid4());cfg=dict(node_id=node,host='8.8.8.8',port=9443,stats_port=20000,secret='a'*32)
        env='MT_PROXY_HOST=8.8.8.8\nMT_PROXY_PORT=9443\nMT_PROXY_SECRET='+cfg['secret']+'\n'
        with patch.object(provisioner,'read_instance',return_value=cfg),patch.object(provisioner,'private_read',return_value=env),\
             patch.object(provisioner,'command',return_value='[{"prefsrc":"10.0.0.2"}]'),\
             patch('check_telegram_promo.load_proxy',return_value=('8.8.8.8',8443,'b'*32)),patch.object(os,'execv') as execute:
            provisioner.run_instance(node)
        args=execute.call_args.args[1]
        self.assertEqual([args[i+1] for i,a in enumerate(args[:-1]) if a=='-S'],['a'*32,'b'*32])

    @unittest.skipUnless(shutil.which('haproxy') or os.environ.get('PUSHOK_TEST_HAPROXY'),'Нужен HAProxy для loopback smoke')
    def test_real_tcp_topics_fallback_and_config_validation(self):
        class Echo(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(1)
                try:
                    data=self.request.recv(1024)
                    if data:self.request.sendall(self.server.label+data)
                except OSError:pass
        servers=[];process=None
        with tempfile.TemporaryDirectory() as tmp:
            try:
                for label in (b'A:',b'B:'):
                    server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo);server.daemon_threads=True;server.label=label
                    threading.Thread(target=server.serve_forever,daemon=True).start();servers.append(server)
                # Выбираем свободную пару портов: один обычный, один тематический.
                for _ in range(50):
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    if port<65000 and host.provisioner.free_port(port+1):break
                cfg=dict(port=port,interests_enabled=True,topics=['technology'])
                nodes=[dict(id=str(uuid4()),address='127.0.0.1',port=s.server_address[1],topic='technology' if i==0 else 'travel') for i,s in enumerate(servers)]
                path=Path(tmp)/'haproxy.cfg';path.write_text(host.configuration(cfg,nodes))
                binary=os.environ.get('PUSHOK_TEST_HAPROXY') or shutil.which('haproxy')
                subprocess.run([binary,'-c','-f',str(path)],capture_output=True,check=True,timeout=10)
                process=subprocess.Popen([binary,'-W','-db','-f',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                def request(port):
                    with socket.create_connection(('127.0.0.1',port),timeout=1) as s:
                        s.sendall(b'test');return s.recv(64)
                for _ in range(40):
                    try:
                        if request(port+1)==b'A:test':break
                    except OSError:pass
                    time.sleep(.1)
                self.assertEqual(request(port+1),b'A:test')
                self.assertIn(request(port),(b'A:test',b'B:test'))
                # Реальный мягкий reload master-worker, как в systemd-службе.
                nodes[0]['topic']='travel';nodes[1]['topic']='technology'
                path.write_text(host.configuration(cfg,nodes));process.send_signal(signal.SIGUSR2)
                for _ in range(40):
                    try:
                        if request(port+1)==b'B:test':break
                    except OSError:pass
                    time.sleep(.1)
                self.assertEqual(request(port+1),b'B:test')
                servers[1].shutdown();servers[1].server_close()
                for _ in range(100):
                    try:
                        if request(port+1)==b'A:test':break
                    except OSError:pass
                    time.sleep(.1)
                self.assertEqual(request(port+1),b'A:test')
            finally:
                if process:process.terminate();process.wait(timeout=5)
                for server in servers:server.shutdown();server.server_close()


@unittest.skipUnless(fixture._approved_test_postgres(),'Нужен отдельный тестовый PostgreSQL')
class GatewayIntegration(unittest.TestCase):
    setUpClass=classmethod(fixture.PostgresIntegration.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PostgresIntegration.tearDownClass.__func__)
    request=fixture.PostgresIntegration.request
    setUp=wallet.WalletIntegration.setUp
    _management_fixture=fixture.PostgresIntegration._management_fixture
    write_promotion_report=fixture.PostgresIntegration.write_promotion_report

    def test_topic_requires_fresh_applied_current_plan(self):
        from app.promotion_runtime import handle
        f=self._management_fixture(ready=True)
        main.app.dependency_overrides[main.get_current_user]=lambda:dict(id=f['admin'],role='admin')
        self.request('PUT','/admin/promotion-bindings',json=dict(node_id=str(f['node']),channel_id=str(f['channel']),ad_tag=uuid4().hex,telegram_binding_confirmed=True))
        hostname='management-'+f['node'].hex+'.example.org'
        request=dict(host=hostname,port=8443)
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            conn.execute("UPDATE campaigns SET topic='technology',proxy_node_id=%s,placement_status='scheduled',scheduled_start_at=now()-interval '1 minute',scheduled_end_at=now()+interval '1 hour' WHERE id=%s",(f['node'],f['campaign']))
            plan=handle(conn,request)
            self.assertTrue(handle(conn,dict(request,action='ack',revision=plan['revision']))['accepted'])
        self.write_promotion_report(plan)
        def topic():
            with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
                return api.snapshot(conn,dict(hosts=[hostname]))['nodes'][0]['topic']
        self.assertEqual(topic(),'technology')
        self.write_promotion_report(plan,code='tag_not_verified')
        self.assertIsNone(topic())
        self.write_promotion_report(plan)
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE channel_promotion_bindings SET ad_tag=%s WHERE proxy_node_id=%s",(uuid4().hex,f['node']))
        self.assertIsNone(topic())

    def owner_actor(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('UPDATE users SET is_owner=false WHERE is_owner')
            conn.execute('UPDATE users SET is_owner=true WHERE id=%s',(self.admin,))
            conn.execute('UPDATE proxy_gateway_settings SET enabled=false,interests_enabled=false,port=10443')
        self.actor=dict(id=self.admin,role='admin',is_owner=True)

    def test_interests_optional_private_and_deletable(self):
        self.assertIsNone(self.request('GET','/me/proxy-interest')['topic'])
        self.request('PUT','/me/proxy-interest',json={'topic':'technology'})
        self.actor=dict(id=self.other,role='advertiser')
        self.assertIsNone(self.request('GET','/me/proxy-interest')['topic'])
        self.request('PUT','/me/proxy-interest',422,json={'topic':'adult'})
        self.request('PUT','/me/proxy-interest',422,json={'topic':'travel','user_id':str(self.owner)})
        self.actor=dict(id=self.owner,role='advertiser')
        self.assertEqual(self.request('GET','/me/proxy-interest')['topic'],'technology')
        self.assertIsNone(self.request('PUT','/me/proxy-interest',json={'topic':None})['topic'])
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM user_proxy_interests WHERE user_id=%s',(self.owner,)).fetchone()[0],0)

    def test_owner_settings_revision_ports_and_snapshot(self):
        self.request('GET','/admin/proxy-gateway',403)
        self.owner_actor()
        settings=self.request('GET','/admin/proxy-gateway')
        data=dict(enabled=True,port=10443,interests_enabled=True,revision=settings['revision'])
        saved=self.request('PUT','/admin/proxy-gateway',json=data)
        self.request('PUT','/admin/proxy-gateway',409,json=data)
        data['revision']=saved['revision']
        self.request('PUT','/admin/proxy-gateway',409,json=dict(data,port=19998))
        self.request('PUT','/admin/proxy-gateway',409,json=dict(data,port=11443))
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            result=api.snapshot(conn,dict(hosts=['8.8.8.8']))
        self.assertTrue(result['enabled']);self.assertNotIn('user_id',str(result))
        with psycopg.connect(self.dsn) as conn:conn.execute('UPDATE users SET is_owner=false WHERE id=%s',(self.admin,))
        with psycopg.connect(self.dsn,row_factory=dict_row) as conn:
            self.assertFalse(api.snapshot(conn,dict(hosts=['8.8.8.8']))['enabled'])

    def test_connection_uses_only_own_topic_and_falls_back(self):
        self.request('PUT','/me/proxy-interest',json={'topic':'technology'})
        with patch.object(proxy_link,'current_gateway_url',return_value=None) as gateway,patch.object(proxy_link,'current_proxy_url',return_value='fallback'):
            data=self.request('GET','/me/proxy-connection')
            self.assertFalse(data['personalized']);self.assertEqual(data['url'],'fallback');gateway.assert_called_with('technology')
