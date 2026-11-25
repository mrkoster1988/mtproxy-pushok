"""Последовательность выпуска: копия до миграции, отказ без изменений, очередь."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import updater


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '.control').mkdir()
        (self.root / 'database').mkdir()
        for name in ('001_initial.sql', '002_next.sql', '022_free.sql'):
            (self.root / 'database' / name).write_text('SELECT 1;')
        self.commands = []
        self.patches = [patch.object(updater, 'ROOT', self.root),
                        patch.object(updater, 'CONTROL', self.root / '.control'),
                        patch.object(updater.os, 'fchown'), patch.object(sys, 'argv', ['updater.py'])]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, content='{"action":"update"}'):
        (self.root / '.control/request.json').write_text(content)

    def fake_run(self, *args, capture=False):
        self.commands.append(args)
        if args == ('git', 'rev-parse', 'HEAD'):
            return 'old\n'
        if args == ('git', 'rev-parse', 'FETCH_HEAD'):
            return 'new\n'
        return ''

    def test_busy_finalizer_preserves_active_worker_status(self):
        self.request()
        updater.status('running', stage='build')
        with patch.object(sys, 'argv', ['updater.py', '--finalize']), patch.object(updater.fcntl, 'flock', side_effect=BlockingIOError), patch.object(updater, 'run') as run:
            updater.main()
        run.assert_not_called()
        self.assertTrue((self.root / '.control/request.json').exists())
        self.assertEqual(updater.read_control('status.json')['stage'], 'build')

    def test_worker_waits_for_lock_before_consuming_request(self):
        self.request()
        with patch.object(updater.fcntl, 'flock') as flock, patch.object(updater, 'run', side_effect=self.fake_run):
            updater.main()
        self.assertEqual(flock.call_args_list[0].args[1], updater.fcntl.LOCK_EX)
        self.assertEqual(updater.read_control('status.json')['state'], 'succeeded')

    def test_recovery_uses_new_backup_on_installed_root_before_fast_forward(self):
        with patch.object(updater, 'run', side_effect=self.fake_run):
            updater.main(recovery_target='new')
        backup = ('python3', str(Path(updater.__file__).with_name('cabinet_backup.py')),
                  'create', '--allow-downtime', '--root', str(self.root))
        self.assertLess(self.commands.index(backup), self.commands.index(('git', 'merge', '--ff-only', 'new')))

    def test_recovery_marks_abandoned_running_status_failed(self):
        updater.status('running', stage='build')
        with patch.object(updater, 'run') as run:
            updater.main()
        run.assert_not_called()
        self.assertEqual(updater.read_control('status.json')['code'], 'worker_interrupted')

    def test_fixed_release_backup_before_migration_and_ready_before_success(self):
        self.request()
        with patch.object(updater, 'run', side_effect=self.fake_run):
            updater.main()
        commands = self.commands
        self.assertIn(('git', 'fetch', '--no-tags', updater.REMOTE, 'refs/heads/release/stable'), commands)
        backup = commands.index(('python3', 'scripts/cabinet_backup.py', 'create', '--allow-downtime'))
        merge = commands.index(('git', 'merge', '--ff-only', 'new'))
        migrate = next(i for i, c in enumerate(commands) if 'migrate' in c)
        self.assertLess(backup, merge)
        self.assertLess(merge, migrate)
        self.assertEqual(commands[migrate][3:], ('database/002_next.sql', 'database/022_free.sql'))
        self.assertIn(('python3', 'scripts/installer_runtime.py', 'wait-api'), commands)
        self.assertEqual(json.loads((self.root / '.control/status.json').read_text())['state'], 'succeeded')

    def test_public_screen_keeps_only_state_and_stage_during_update(self):
        from update_screen import prepare
        repo = Path(__file__).resolve().parents[1]
        (self.root / 'api').symlink_to(repo / 'api', target_is_directory=True)
        (self.root / 'Caddyfile').write_text('test.example.org {\n reverse_proxy api:8000\n}\n')
        override = self.root / 'compose.override.yaml'
        override.write_text('services:\n  caddy:\n    volumes:\n      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n')
        prepare(self.root)
        installed = (self.root / 'Caddyfile').read_text()
        prepare(self.root)
        self.assertEqual((self.root / 'Caddyfile').read_text(), installed)
        self.assertEqual(override.read_text().count('.maintenance:'), 1)
        public = self.root / '.maintenance'
        updater.status('running', stage='migration', previous_commit='private')
        self.assertTrue((public / 'running').exists())
        self.assertEqual(json.loads((public / 'status.json').read_text()), {'state':'running','stage':'migration'})
        updater.status('failed', stage='migration', code='private')
        self.assertFalse((public / 'running').exists())
        self.assertEqual(json.loads((public / 'status.json').read_text()), {'state':'failed'})

    def test_backup_failure_never_merges_or_migrates(self):
        self.request()
        def run(*args, **kwargs):
            if 'create' in args:
                raise RuntimeError('backup failed')
            return self.fake_run(*args, **kwargs)
        with patch.object(updater, 'run', side_effect=run), self.assertRaises(RuntimeError):
            updater.main()
        self.assertFalse(any('merge' in c or 'migrate' in c for c in self.commands))
        self.assertEqual(json.loads((self.root / '.control/status.json').read_text())['state'], 'failed')
        self.assertEqual(json.loads((self.root / '.control/status.json').read_text())['stage'], 'backup')

    def test_fetch_denied_never_backs_up_merges_or_stops_services(self):
        self.request()
        def run(*args, **kwargs):
            if 'fetch' in args:
                raise RuntimeError('repository access denied')
            return self.fake_run(*args, **kwargs)
        with patch.object(updater, 'run', side_effect=run), self.assertRaises(RuntimeError):
            updater.main()
        self.assertFalse(any('create' in c or 'merge' in c or 'compose' in c for c in self.commands))
        self.assertEqual(updater.read_control('status.json')['stage'], 'preflight')

    def test_automatic_check_is_opt_in_and_uses_existing_update_pipeline(self):
        sha = 'a' * 40
        with patch.object(updater, 'published_release', return_value={'state':'available','commit':sha}) as discover:
            updater.main()
            discover.assert_not_called()
            updater.write_control('auto-update.json', {'enabled': True})
            def run(*args, capture=False):
                value = self.fake_run(*args, capture=capture)
                return sha if args == ('git', 'rev-parse', 'FETCH_HEAD') else value
            with patch.object(updater, 'run', side_effect=run):
                updater.main()
                self.assertEqual(updater.read_control('status.json')['commit'], sha)
                updater.main()
                self.assertEqual(discover.call_count, 1)
                with patch.object(updater.time, 'time', return_value=updater.time.time() + 3601):
                    updater.main()
            self.assertEqual(discover.call_count, 2)
            self.assertEqual(sum('create' in c for c in self.commands), 1)
            self.assertFalse((self.root / '.control/request.json').exists())

    def test_unavailable_or_changed_release_never_starts_installation(self):
        updater.write_control('auto-update.json', {'enabled': True})
        with patch.object(updater, 'published_release', return_value={'state':'unavailable'}):
            with patch.object(updater, 'run') as run:
                updater.main()
                run.assert_not_called()
        self.assertEqual(updater.read_control('auto-check.json')['state'], 'unavailable')
        self.request(json.dumps({'action':'update','actor':'automatic','commit':'a' * 40}))
        with patch.object(updater, 'run', side_effect=self.fake_run):
            updater.main()
        self.assertFalse(any('compose' in c or 'create' in c for c in self.commands))

    def test_git_commands_disable_interactive_password_prompt(self):
        with patch.object(updater.subprocess, 'run') as execute:
            updater.run('git', 'fetch', '--no-tags', updater.REMOTE, 'refs/heads/release/stable')
        self.assertEqual(execute.call_args.kwargs['env']['GIT_TERMINAL_PROMPT'], '0')

    def test_malformed_queue_is_consumed_without_running_commands(self):
        self.request('[]')
        with patch.object(updater, 'run') as run:
            updater.main()
        run.assert_not_called()
        self.assertFalse((self.root / '.control/request.json').exists())
        self.assertEqual(json.loads((self.root / '.control/status.json').read_text())['code'], 'invalid_request')

    def test_interrupted_worker_becomes_retryable(self):
        (self.root / '.control/status.json').write_text('{"state":"running"}')
        with patch.object(sys, 'argv', ['updater.py', '--finalize']), patch.object(updater, 'run') as run:
            updater.main()
        run.assert_not_called()
        self.assertEqual(json.loads((self.root / '.control/status.json').read_text())['state'], 'failed')

    def test_bot_profile_preserves_running_selection(self):
        for running, expected in [('api\ndb\n', False), ('api\ndb\nbot\n', True)]:
            self.request()
            self.commands.clear()
            def run(*args, **kwargs):
                if 'ps' in args:
                    return running
                return self.fake_run(*args, **kwargs)
            with patch.object(updater, 'run', side_effect=run):
                updater.main()
            up = next(c for c in self.commands if 'up' in c)
            self.assertEqual('--profile' in up, expected)

    def test_lock_symlink_cannot_change_another_file(self):
        outside=self.root/'untouched'
        outside.write_text('untouched')
        (self.root/'.control/lock').symlink_to(outside)
        self.request()
        with patch.object(updater,'run') as run, self.assertRaises(OSError):
            updater.main()
        run.assert_not_called()
        self.assertEqual(outside.read_text(),'untouched')
        updater.os.fchown.assert_not_called()

    def test_control_read_does_not_follow_links_or_accept_oversized_json(self):
        secret=self.root/'private.json';secret.write_text('{"private":"secret"}')
        path=self.root/'.control/payments.json';path.symlink_to(secret)
        self.assertEqual(updater.read_control('payments.json'),{})
        path.unlink();path.write_text(json.dumps({'large':'a'*17000}))
        self.assertEqual(updater.read_control('payments.json'),{})

