"""Private repository credentials: boundaries, Git integration and release checks."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))
sys.path.insert(0, str(ROOT / 'scripts'))
from app import github_access, releases, release_ci
import github_credentials
import repository_access

TOKEN = 'github_pat_' + 'testonly_' * 5


class GitHubAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / '.control'
        self.directory.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {'PUSHOK_CONTROL_DIR': str(self.directory)})
        self.env.start()
        releases._cache.clear()
        release_ci._cache.clear()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
        releases._cache.clear()
        release_ci._cache.clear()

    def token_file(self):
        path = self.directory / github_access.FILE
        path.write_text(json.dumps({'token': TOKEN}))
        path.chmod(0o600)
        return path

    def test_token_file_is_bounded_private_regular_and_not_symlink(self):
        self.assertEqual(github_access.read_token(), '')
        path = self.token_file()
        self.assertEqual(github_access.read_token(), TOKEN)
        path.chmod(0o644)
        self.assertEqual(github_access.read_token(), '')
        path.chmod(0o600)
        other = self.root / 'hardlink'
        os.link(path, other)
        self.assertEqual(github_access.read_token(), '')
        other.unlink()
        path.rename(other)
        path.symlink_to(other)
        self.assertEqual(github_access.read_token(), '')
        path.unlink()
        path.write_text('x' * 4097)
        path.chmod(0o600)
        self.assertEqual(github_access.read_token(), '')
        for value in ('[]', '{}', '{"token":"bad\\nheader"}', '{'):
            path.write_text(value)
            self.assertEqual(github_access.read_token(), '')

    def test_authorization_not_in_url_or_forwarded_on_redirect(self):
        self.token_file()
        request = github_access.request('/git/ref/heads/release/stable')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertNotIn(TOKEN, request.full_url)
        redirected = urllib.request.HTTPRedirectHandler().redirect_request(
            request, None, 302, 'Found', {}, 'https://example.invalid/redirect')
        self.assertFalse(redirected.has_header('Authorization'))
        for invalid in ('//evil.example/', 'https://evil.example', '/x\nHeader: value'):
            with self.assertRaises(ValueError):
                github_access.request(invalid)

    def test_helper_only_serves_exact_repository_get(self):
        self.token_file()
        valid = 'protocol=https\nhost=github.com\npath=mrkoster1988/mtproxy-pushok.git\n\n'
        self.assertIn('password=' + TOKEN, github_credentials.credentials('get', valid, self.directory))
        for raw in (valid.replace('https', 'http'), valid.replace('github.com', 'github.com.evil'),
                    valid.replace('mtproxy-pushok.git', 'other.git'),
                    'protocol=https\nhost=github.com\n', 'x' * 4097):
            self.assertEqual(github_credentials.credentials('get', raw, self.directory), '')
        for op in ('store', 'erase', ''):
            self.assertEqual(github_credentials.credentials(op, valid, self.directory), '')

    @unittest.skipUnless(shutil.which('git'), 'host-only Git integration; runs in the main CI job')
    def test_real_git_helper_with_private_token_and_no_global_credentials(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'scripts').mkdir()
        (self.root / 'api/app').mkdir(parents=True)
        shutil.copy(ROOT / 'scripts/github_credentials.py', self.root / 'scripts')
        shutil.copy(ROOT / 'api/app/github_access.py', self.root / 'api/app')
        with patch.object(repository_access.os, 'chown'), patch.object(repository_access.os, 'fchown'):
            repository_access.save(self.root, TOKEN)
        token_path = self.directory / github_access.FILE
        self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
        config = (self.root / '.git/config').read_text()
        self.assertNotIn(TOKEN, config)
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
        for path, success in [('mrkoster1988/mtproxy-pushok.git', True), ('mrkoster1988/other.git', False)]:
            result = subprocess.run(['git', '-C', str(self.root), 'credential', 'fill'],
                                    input='protocol=https\nhost=github.com\npath=' + path + '\n\n',
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode == 0, success)
            self.assertEqual(TOKEN in result.stdout, success)
            self.assertNotIn(TOKEN, result.stderr)

    def test_release_and_ci_requests_include_saved_token(self):
        self.token_file()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with patch.object(urllib.request, 'urlopen', return_value=response) as http:
            releases._json('/git/ref/heads/release/stable')
            release_ci.lookup('a' * 40)
        self.assertEqual(len(http.call_args_list), 2)
        for call in http.call_args_list:
            self.assertEqual(call.args[0].get_header('Authorization'), 'Bearer ' + TOKEN)
            self.assertNotIn(TOKEN, call.args[0].full_url)

    def test_bad_credentials_do_not_overwrite_existing_secret(self):
        self.token_file()
        with patch.object(repository_access, 'ROOT', self.root), \
                patch.object(repository_access.os, 'geteuid', return_value=0), \
                patch.object(sys, 'argv', ['repository_access.py']), \
                patch.object(repository_access.getpass, 'getpass', return_value='b' * 40), \
                patch.object(repository_access, 'verify', side_effect=OSError), \
                patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(repository_access.main(), 1)
        self.assertEqual(github_access.read_token(), TOKEN)
        self.assertNotIn('b' * 40, error.getvalue())

    def test_verify_checks_both_contents_and_actions(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(urllib.request, 'urlopen', return_value=response) as http:
            repository_access.verify(TOKEN)
        self.assertEqual(len(http.call_args_list), 2)
        self.assertIn('/git/ref/heads/release/stable', http.call_args_list[0].args[0].full_url)
        self.assertIn('/actions/runs', http.call_args_list[1].args[0].full_url)

    def test_no_echo_fallback_when_terminal_is_unavailable(self):
        import warnings
        def insecure_prompt(*args):
            warnings.warn('No secure terminal', repository_access.getpass.GetPassWarning)
            self.fail('must not continue with echoed input')
        with patch.object(repository_access.os, 'geteuid', return_value=0), \
                patch.object(sys, 'argv', ['repository_access.py']), \
                patch.object(repository_access.getpass, 'getpass', side_effect=insecure_prompt), \
                patch.object(repository_access, 'verify') as verify, \
                patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(repository_access.main(), 1)
        verify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
