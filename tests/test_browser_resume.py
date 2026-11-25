import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused')
from app import main
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient


class BrowserResumeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url='https://cabinet.example')
        self.path = '/api/v1/auth/browser/resume'
        self.headers = {'X-Pushok-Cabinet': 'resume', 'Sec-Fetch-Site': 'same-origin'}

    def test_cookie_path_and_session_is_not_extended(self):
        res = Response()
        main.remember_browser_session(res, 'fixture', 90)
        header = res.headers['set-cookie'].lower()
        for part in ('httponly', 'secure', 'samesite=strict', 'max-age=90', 'path=/api/v1/auth/browser'):
            self.assertIn(part, header)
        self.assertNotIn('domain=', header)
        self.client.cookies.set(main.BROWSER_SESSION_COOKIE, 'fixture', path=main.BROWSER_SESSION_PATH)
        with patch.object(main, 'get_current_user', return_value={'login_surface':'web'}), \
             patch.object(main, '_decode_session_token', return_value=(None,None,1090)), patch.object(main.time,'time',return_value=1000):
            resp = self.client.post(self.path, headers=self.headers)
            self.assertEqual(resp.json()['expires_in'], 90)
            self.assertEqual(resp.json()['access_token'], 'fixture')
            self.assertIn('no-store', resp.headers['cache-control'])
            self.assertNotIn('set-cookie', resp.headers)

    def test_missing_cross_site_revoked_and_miniapp(self):
        self.assertEqual(self.client.post(self.path, headers=self.headers).status_code, 401)
        self.client.cookies.set(main.BROWSER_SESSION_COOKIE, 'fixture', path=main.BROWSER_SESSION_PATH)
        for headers in ({}, dict(self.headers, **{'Sec-Fetch-Site':'cross-site'})):
            self.assertEqual(self.client.post(self.path, headers=headers).status_code, 403)
        with patch.object(main, 'get_current_user', side_effect=HTTPException(401, 'revoked')):
            self.assertEqual(self.client.post(self.path, headers=self.headers).status_code, 401)
        with patch.object(main, 'get_current_user', return_value={'login_surface':'telegram'}):
            self.assertEqual(self.client.post(self.path, headers=self.headers).status_code, 403)
