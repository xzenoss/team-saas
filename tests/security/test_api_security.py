"""Exercise the real HTTP handler against a disposable SQLite database."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server


class QuietHandler(server.Handler):
    def log_message(self, *args):
        pass


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(server, 'DB_PATH', Path(self.tmp.name) / 'test.db')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        # Secure cookies are returned even though loopback test transport is HTTP.
        self.env_patch = patch.dict(os.environ, {'TEAMSAAS_SECURE_COOKIE': '1'})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        with server._rate_lock:
            server._rate_hits.clear()
        server.initialize()
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.a = self.register('Alpha')
        self.b = self.register('Beta')
        for client in (self.a, self.b):
            status, state, _ = self.request('/api/projects/create', client, {
                'name': client['label'] + ' private project', 'description': '', 'color': 'violet'})
            self.assertEqual(status, 200)
            client['project'] = state['projects'][0]['id']
            status, state, _ = self.request('/api/tasks/create', client, self.task(client))
            self.assertEqual(status, 200)
            client['task'] = state['tasks'][0]['id']

    def close_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, path, client=None, data=None, headers=None, raw=None):
        conn = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        request_headers = {'Content-Type': 'application/json'}
        if client:
            request_headers.update({'Cookie': client['cookie'], 'X-CSRF-Token': client.get('csrf', '')})
        request_headers.update(headers or {})
        payload = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        try:
            conn.request('POST' if payload is not None else 'GET', path, payload, request_headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            conn.close()

    def register(self, label):
        status, _, headers = self.request('/api/register', data={
            'name': label, 'email': label.lower() + '@example.test',
            'password': 'Synthetic-password-123!', 'workspace': label})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie']
        for flag in ('HttpOnly', 'SameSite=Lax', 'Secure', 'Path=/'):
            self.assertIn(flag, cookie)
        client = {'cookie': cookie.split(';')[0], 'label': label}
        status, state, _ = self.request('/api/state', client)
        self.assertEqual(status, 200)
        client.update(csrf=state['csrf'], user=state['user']['id'], workspace=state['workspace']['id'])
        return client

    def task(self, client, **overrides):
        return dict({'title': client['label'] + ' private task', 'description': '',
                     'project_id': client['project'], 'status': 'todo', 'priority': 'high',
                     'assignee_id': client['user'], 'due': ''}, **overrides)

    def state(self, client):
        status, state, _ = self.request('/api/state', client)
        self.assertEqual(status, 200)
        return state

    def denied_unchanged(self, path, data, expected, client=None, headers=None):
        before = [self.state(c) for c in (self.a, self.b)]
        status, result, _ = self.request(path, client or self.a, data, headers)
        self.assertEqual(status, expected)
        self.assertEqual(set(result), {'error'})
        self.assertEqual([self.state(c) for c in (self.a, self.b)], before)

    def test_state_isolates_all_collections(self):
        for client, other in ((self.a, self.b), (self.b, self.a)):
            state = self.state(client)
            self.assertEqual(state['workspace']['id'], client['workspace'])
            self.assertEqual({p['id'] for p in state['projects']}, {client['project']})
            self.assertEqual({t['id'] for t in state['tasks']}, {client['task']})
            self.assertEqual({m['id'] for m in state['members']}, {client['user']})
            self.assertEqual({a['actor'] for a in state['activity']}, {client['label']})
            self.assertNotIn(other['project'], json.dumps(state))
            self.assertNotIn(other['task'], json.dumps(state))

    def test_foreign_task_update_and_delete_are_denied(self):
        self.denied_unchanged('/api/tasks/update', self.task(self.a, id=self.b['task'], title='Intrusion'), 404)
        self.denied_unchanged('/api/tasks/delete', {'id': self.b['task']}, 404)
        # Positive control: own task updates succeed, and Beta is unchanged.
        before_b = self.state(self.b)
        status, state, _ = self.request('/api/tasks/update', self.a, self.task(self.a, id=self.a['task'], title='Allowed'))
        self.assertEqual(status, 200)
        self.assertEqual(state['tasks'][0]['title'], 'Allowed')
        self.assertEqual(self.state(self.b), before_b)

    def test_foreign_project_and_assignee_are_denied(self):
        for endpoint in ('/api/tasks/create', '/api/tasks/update'):
            data = self.task(self.a, id=self.a['task'], project_id=self.b['project'])
            self.denied_unchanged(endpoint, data, 404)
            data = self.task(self.a, id=self.a['task'], assignee_id=self.b['user'])
            self.denied_unchanged(endpoint, data, 400)

    def test_csrf_and_origin_rejection_preserve_data(self):
        for headers in ({'X-CSRF-Token': ''}, {'X-CSRF-Token': self.b['csrf']},
                        {'Origin': 'https://attacker.example.test'}):
            self.denied_unchanged('/api/settings', {'name': 'Intrusion'}, 403, headers=headers)
        status, state, _ = self.request('/api/settings', self.a, {'name': 'Allowed Alpha'},
                                       {'Origin': 'http://127.0.0.1:' + str(self.httpd.server_port)})
        self.assertEqual(status, 200)
        self.assertEqual(state['workspace']['name'], 'Allowed Alpha')
        self.assertEqual(self.state(self.b)['workspace']['name'], 'Beta')

    def test_anonymous_and_forged_sessions_are_denied(self):
        for client in (None, {'cookie': 'gather_session=forged', 'csrf': 'forged'}):
            self.assertEqual(self.request('/api/state', client)[0], 401)
            self.assertEqual(self.request('/api/settings', client, {'name': 'Intrusion'})[0], 401)

    def test_logout_revokes_session(self):
        self.assertEqual(self.request('/api/logout', self.a, {})[0], 200)
        self.assertEqual(self.request('/api/state', self.a)[0], 401)
        self.assertEqual(self.request('/api/settings', self.a, {'name': 'Intrusion'})[0], 401)
        self.state(self.b)

    def test_expiry_and_membership_revocation(self):
        with server.connect() as db:
            db.execute('UPDATE sessions SET expires=? WHERE user_id=?', (time.time() - 1, self.a['user']))
        self.assertEqual(self.request('/api/state', self.a)[0], 401)
        self.assertEqual(self.request('/api/settings', self.a, {'name': 'Intrusion'})[0], 401)
        with server.connect() as db:
            db.execute('DELETE FROM members WHERE workspace_id=? AND user_id=?', (self.b['workspace'], self.b['user']))
        self.assertEqual(self.request('/api/state', self.b)[0], 401)
        self.assertEqual(self.request('/api/settings', self.b, {'name': 'Intrusion'})[0], 401)

    def test_member_permissions_and_invite_replay(self):
        member = self.register('Member')
        status, invite, _ = self.request('/api/invites/create', self.a, {})
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/join', member, {'token': invite['token']})[0], 200)
        self.assertEqual(self.state(member)['workspace']['id'], self.a['workspace'])
        self.assertEqual(self.state(member)['user']['role'], 'member')
        self.denied_unchanged('/api/settings', {'name': 'Intrusion'}, 403, client=member)
        self.denied_unchanged('/api/invites/create', {}, 403, client=member)
        self.denied_unchanged('/api/join', {'token': invite['token']}, 404, client=self.b)
        self.denied_unchanged('/api/join', {'token': 'invented-token'}, 404, client=self.b)

    def test_invalid_and_oversized_json(self):
        before = [self.state(c) for c in (self.a, self.b)]
        for raw, expected in ((b'{', 400), (b'[]', 400), (b'x' * (server.MAX_REQUEST_BYTES + 1), 413)):
            self.assertEqual(self.request('/api/settings', self.a, raw=raw)[0], expected)
        self.assertEqual([self.state(c) for c in (self.a, self.b)], before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
