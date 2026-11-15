import base64
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
os.environ.setdefault('SESSION_SIGNING_KEY', 'a' * 64)
from app import releases, main
from fastapi.testclient import TestClient


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        releases._cache.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        releases._cache.clear()

    def replies(self, version='1.01'):
        return [{'object': {'type': 'commit', 'sha': 'a' * 40}},
                {'encoding': 'base64', 'content': base64.b64encode(
                    ('Версия проекта: v' + version + '.\n').encode()).decode()}]

    def test_numeric_versions_legacy_major_and_minor(self):
        with patch.object(releases, '_json', side_effect=self.replies('1.10')) as http:
            for local, state in [('0.066', 'available'), ('1.01', 'available'),
                                 ('1.09', 'available'), ('1.10', 'current'), ('2.00', 'ahead')]:
                self.assertEqual(releases.check(local)['state'], state)
            self.assertEqual(http.call_count, 2)
            self.assertEqual(http.call_args.args[0], '/contents/README.md?ref=' + 'a' * 40)
        with self.assertRaises(ValueError):
            releases.version_key('1.01<script>')

    def test_failure_is_not_current_and_retains_last_success_as_stale(self):
        with patch.object(releases, '_json', side_effect=self.replies()):
            first = releases.check('1.00')
        releases._cache['time'] -= 61
        with patch.object(releases, '_json', side_effect=OSError) as http:
            report = releases.check('1.00')
            self.assertEqual(report['state'], 'unavailable')
            self.assertEqual(report['checked_at'], first['checked_at'])
            self.assertEqual(report['available_version'], '1.01')
            releases.check('1.00')
            self.assertEqual(http.call_count, 1)

    def test_invalid_or_missing_metadata_does_not_offer_installation(self):
        for replies in [[{}], [{'object': {'type': 'tree', 'sha': 'a'*40}}],
                        [self.replies()[0], {'encoding': 'base64', 'content': '%%%'}]]:
            releases._cache.clear()
            with patch.object(releases, '_json', side_effect=replies):
                self.assertEqual(releases.check('1.01')['state'], 'unavailable')

    def test_bounded_http_no_credentials(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({}).encode()
        with patch.object(releases.urllib.request, 'urlopen', return_value=response) as http:
            self.assertEqual(releases._json('/git/ref/heads/release/stable'), {})
            request = http.call_args.args[0]
            self.assertFalse(request.has_header('Authorization'))
            self.assertEqual(http.call_args.kwargs['timeout'], 3)
            response.__enter__.return_value.read.assert_called_once_with(65537)
            response.__enter__.return_value.read.return_value = b'x' * 65537
            with self.assertRaises(ValueError):
                releases._json('/git/ref/heads/release/stable')

    def test_admin_only_and_running_version_is_authoritative(self):
        client = TestClient(main.app)
        with patch.object(releases, '_json', side_effect=self.replies('2.00')) as http:
            self.assertEqual(client.get('/api/v1/admin/control/release').status_code, 401)
            main.app.dependency_overrides[main.get_current_user] = lambda: dict(id='u', role='advertiser')
            self.assertEqual(client.get('/api/v1/admin/control/release').status_code, 403)
            main.app.dependency_overrides[main.get_current_user] = lambda: dict(id='u', role='operator')
            self.assertEqual(client.get('/api/v1/admin/control/release').status_code, 403)
            http.assert_not_called()
            main.app.dependency_overrides[main.get_current_user] = lambda: dict(id='u', role='admin')
            response = client.get('/api/v1/admin/control/release')
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(response.json()['installed_version'], main.app.version)
            self.assertEqual(response.json()['state'], 'available')
