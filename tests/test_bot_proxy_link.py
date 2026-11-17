"""Проверяем путь от приватного env хоста до ссылки в сообщении Telegram."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from bot import main, proxy_link
from bot.navigation import Navigation
from publish_proxy_link import publish_proxy_link


class ProxyLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.control').mkdir()
        self.env = self.root / 'proxy.env'
        self.file = self.root / '.control/proxy-link.json'
        self.env.write_text('MT_PROXY_HOST=proxy.example.org\nMT_PROXY_PORT=8443\n'
                            'MT_PROXY_SECRET=' + 'a' * 32 + '\nMT_PROXY_TAG=private-sponsor-tag\n')
        self.env.chmod(0o600)
        p = patch.object(proxy_link, 'LINK_FILE', self.file)
        p.start(); self.addCleanup(p.stop)
        p = patch('publish_proxy_link.os.fchown')
        p.start(); self.addCleanup(p.stop)

    def test_config_to_welcome_and_single_advertise_button_without_admin_data(self):
        self.assertTrue(publish_proxy_link(self.root, self.env))
        url = proxy_link.current_proxy_url()
        self.assertIn('server=proxy.example.org&port=8443&secret=dd' + 'a' * 32, url)
        self.assertEqual(set(json.loads(self.file.read_text())), {'url'})
        self.assertNotIn('private-sponsor-tag', self.file.read_text())
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        update = {'message': {'text': '/start', 'from': {'id': 77}, 'chat': {'id': 77, 'type': 'private'}}}
        payload, _ = main.response_for(update)
        self.assertIn(url, payload['text'])
        self.assertNotIn('разрабатывается', payload['text'])
        rows = payload['reply_markup']['inline_keyboard']
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0]['style'], 'success')
        self.assertEqual(rows[0][0]['callback_data'], 'advertise')
        calls = []
        nav = Navigation(lambda token, method, body: calls.append((method, body)) or {'message_id': len(calls)})
        nav.deliver('fixture', update, payload)
        welcome = next(body for method, body in calls if method == 'sendMessage')
        self.assertIn(url, welcome['text'])

    def test_rotation_is_read_without_restart_and_dd_not_doubled(self):
        publish_proxy_link(self.root, self.env)
        before = proxy_link.current_proxy_url()
        self.env.write_text(self.env.read_text().replace('a' * 32, 'dd' + 'b' * 32).replace('8443', '9443'))
        self.assertTrue(publish_proxy_link(self.root, self.env))
        after = proxy_link.current_proxy_url()
        self.assertNotEqual(before, after)
        self.assertTrue(after.endswith('secret=dd' + 'b' * 32))
        self.assertIn('port=9443', after)

    def test_removed_or_invalid_config_removes_stale_link(self):
        for value in (None, 'MT_PROXY_SECRET=bad\n'):
            self.file.write_text('{"url":"stale"}')
            if value is None:
                self.env.unlink()
            else:
                self.env.write_text(value); self.env.chmod(0o600)
            self.assertFalse(publish_proxy_link(self.root, self.env))
            self.assertFalse(self.file.exists())
            self.assertIsNone(proxy_link.current_proxy_url())
            self.assertIn('Пушок', main.message_for('start'))

    def test_malformed_urls_and_files_never_create_connect_button(self):
        good = proxy_link.connection_url('proxy.example.org', 8443, 'a' * 32)
        for url in (good.replace('t.me/', 'evil.example/'), good + '&server=other.example',
                    good.replace('8443', '0'), good.replace('a' * 32, 'bad'),
                    good.replace('proxy.example.org', '127.0.0.1'), good + '#fragment'):
            self.file.write_text(json.dumps({'url': url}))
            self.assertIsNone(proxy_link.current_proxy_url())
        self.file.write_text('x' * 2049)
        self.assertIsNone(proxy_link.current_proxy_url())
        self.file.unlink(); self.file.symlink_to(self.env)
        self.assertIsNone(proxy_link.current_proxy_url())

    def test_private_env_symlink_is_not_read(self):
        linked = self.root / 'linked.env'
        linked.symlink_to(self.env)
        self.assertFalse(publish_proxy_link(self.root, linked))
        self.assertFalse(self.file.exists())

    def test_extra_links_rotate_only_while_agent_confirms_readiness(self):
        from uuid import uuid4
        import time
        node=str(uuid4())
        publish_proxy_link(self.root,self.env)
        publish_proxy_link(self.root,self.env,instance=node)
        extra=self.root/'.control'/('proxy-link-'+node+'.json')
        data=json.loads(extra.read_text());self.assertGreater(data['expires_at'],time.time())
        data['url']=data['url'].replace('8443','9443');extra.write_text(json.dumps(data))
        with patch('secrets.choice',side_effect=lambda choices:choices[-1]):
            self.assertIn('9443',proxy_link.current_proxy_url())
            data['expires_at']=time.time()-1;extra.write_text(json.dumps(data))
            self.assertIn('8443',proxy_link.current_proxy_url())
            data['expires_at']=time.time()+100000;extra.write_text(json.dumps(data))
            self.assertIn('8443',proxy_link.current_proxy_url())
