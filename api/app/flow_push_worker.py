"""Deliver a generic account change signal to active, opted-in Flow sessions."""
import logging
import os
import re
import time

import google.auth
from google.auth.transport.requests import AuthorizedSession
import psycopg
from psycopg.rows import dict_row


def deliver(conn, transport, url):
    row = conn.execute("""SELECT e.id,e.session_id,e.attempts,d.registration_token,s.user_id
        FROM flow_push_events e JOIN flow_push_devices d USING(session_id)
        JOIN flow_sessions s ON s.id=e.session_id JOIN users u ON u.id=s.user_id
        WHERE e.next_attempt<=now() AND s.expires_at>now() AND s.revoked_at IS NULL
            AND u.blocked_at IS NULL ORDER BY e.next_attempt,e.id
        LIMIT 1 FOR UPDATE OF e SKIP LOCKED""").fetchone()
    if not row:
        return False
    message = {'token': row['registration_token'], 'data': {
        'event': 'account_changed', 'account': str(row['user_id']), 'event_id': str(row['id'])},
        'android': {'priority': 'normal', 'ttl': '300s', 'collapse_key': 'flow-account'}}
    try:
        reply = transport.post(url, json={'message': message}, timeout=8)
        errors = reply.json().get('error', {}).get('details', [])
        invalid = any(isinstance(e, dict) and e.get('errorCode') == 'UNREGISTERED' for e in errors)
        delivered = reply.ok
    except Exception:
        invalid = delivered = False
    if invalid:
        conn.execute('DELETE FROM flow_push_devices WHERE session_id=%s AND registration_token=%s',
                     (row['session_id'], row['registration_token']))
    if delivered or invalid or row['attempts'] >= 8:
        conn.execute('DELETE FROM flow_push_events WHERE id=%s', (row['id'],))
    else:
        conn.execute("""UPDATE flow_push_events SET attempts=attempts+1,
            next_attempt=now()+make_interval(secs=>%s) WHERE id=%s""",
                     (min(3600, 30 * 2 ** row['attempts']), row['id']))
    return True


def main():
    project = os.environ['FLOW_FCM_PROJECT_ID']
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,29}', project):
        raise SystemExit('Invalid FCM project identifier')
    credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/firebase.messaging'])
    with AuthorizedSession(credentials) as transport:
        while True:
            try:
                with psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=5, row_factory=dict_row) as conn:
                    active = deliver(conn, transport, f'https://fcm.googleapis.com/v1/projects/{project}/messages:send')
            except psycopg.Error:
                logging.warning('Flow push queue unavailable')
                active = False
            if not active:
                time.sleep(10)


if __name__ == '__main__':
    main()
