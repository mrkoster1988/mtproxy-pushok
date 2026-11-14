import json,sys,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api'))
from app import release_ci
class ReleaseCITests(unittest.TestCase):
    def setUp(self): release_ci._cache.clear()
    def test_exact_commit_and_latest_run(self):
        sha='a'*40
        runs=[dict(head_sha=sha,name='API and PostgreSQL checks',run_number=1,status='completed',conclusion='success'),dict(head_sha=sha,name='API and PostgreSQL checks',run_number=2,status='in_progress',conclusion=None),dict(head_sha='b'*40,name='API and PostgreSQL checks',run_number=3,status='completed',conclusion='success')]
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'workflow_runs':runs}).encode()
        with patch.object(release_ci.urllib.request,'urlopen',return_value=response) as http:
            self.assertFalse(release_ci.lookup(sha)['verified'])
            release_ci.lookup(sha);self.assertEqual(http.call_count,1)
    def test_invalid_or_unavailable_is_not_verified(self):
        with patch.object(release_ci.urllib.request,'urlopen',side_effect=OSError) as http:
            self.assertIsNone(release_ci.lookup('../main'));http.assert_not_called()
            self.assertIsNone(release_ci.lookup('a'*40))
