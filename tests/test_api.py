import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import server


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.original_db = server.DB_PATH
        server.DB_PATH = Path(cls.temp.name) / 'test.db'
        server.initialize()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        server.DB_PATH = cls.original_db
        cls.temp.cleanup()

    def request(self, path, data=None, cookie=None, csrf=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        if csrf:
            headers['X-CSRF-Token'] = csrf
        conn.request('GET' if data is None else 'POST', path, body=None if data is None else json.dumps(data), headers=headers)
        response = conn.getresponse()
        result = (response.status, json.loads(response.read()), response.getheader('Set-Cookie'))
        conn.close()
        return result

    def register(self):
        status, _, cookie = self.request('/api/register', {'email':server.uid()+'@example.test', 'password':'synthetic-password-123', 'name':'Test', 'workspace':'Synthetic'})
        self.assertEqual(status, 200)
        cookie = cookie.split(';')[0]
        status, state, _ = self.request('/api/state', cookie=cookie)
        self.assertEqual(status, 200)
        return cookie, state

    def test_health_endpoint(self):
        self.assertEqual(self.request('/healthz')[:2], (200, {'status':'ok'}))

    def test_state_requires_authentication(self):
        self.assertEqual(self.request('/api/state')[0], 401)

    def test_csrf_required_for_mutation(self):
        cookie, _ = self.register()
        self.assertEqual(self.request('/api/projects/create', {'name':'Blocked', 'color':'blue'}, cookie)[0], 403)

    def test_project_and_task_tenant_isolation(self):
        a, sa = self.register()
        b, sb = self.register()
        status, state, _ = self.request('/api/projects/create', {'name':'Private A', 'color':'blue'}, a, sa['csrf'])
        self.assertEqual(status, 200)
        pid = state['projects'][0]['id']
        data = {'project_id':pid, 'title':'Private task', 'status':'todo', 'priority':'low'}
        self.assertEqual(self.request('/api/tasks/create', data, b, sb['csrf'])[0], 404)
        status, state, _ = self.request('/api/tasks/create', data, a, sa['csrf'])
        self.assertEqual(status, 200)
        tid = state['tasks'][0]['id']
        self.assertEqual(self.request('/api/tasks/delete', {'id':tid}, b, sb['csrf'])[0], 404)
        self.assertEqual(self.request('/api/state', cookie=b)[1]['projects'], [])
        self.assertEqual(len(self.request('/api/state', cookie=a)[1]['tasks']), 1)

    def test_database_rejects_cross_workspace_task_relations(self):
        workspace_a, workspace_b = server.uid(), server.uid()
        user_a, user_b = server.uid(), server.uid()
        project_a = server.uid()
        with server.connect() as db:
            db.execute('INSERT INTO workspaces VALUES(?,?)', (workspace_a, 'A'))
            db.execute('INSERT INTO workspaces VALUES(?,?)', (workspace_b, 'B'))
            db.execute('INSERT INTO users VALUES(?,?,?,?)', (user_a, 'A', user_a+'@example.test', server.hash_password('synthetic-password-123')))
            db.execute('INSERT INTO users VALUES(?,?,?,?)', (user_b, 'B', user_b+'@example.test', server.hash_password('synthetic-password-123')))
            db.execute('INSERT INTO members VALUES(?,?,?)', (workspace_a, user_a, 'owner'))
            db.execute('INSERT INTO members VALUES(?,?,?)', (workspace_b, user_b, 'owner'))
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?)', (project_a, workspace_a, 'A project', '', 'blue', 1.0))

        with server.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)', (server.uid(), workspace_b, project_a, 'Cross project', '', 'todo', 'low', None, '', 1.0))

        with server.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)', (server.uid(), workspace_a, project_a, 'Cross assignee', '', 'todo', 'low', user_b, '', 1.0))

    def test_logout_revokes_session(self):
        cookie, state = self.register()
        self.assertEqual(self.request('/api/logout', {}, cookie, state['csrf'])[0], 200)
        self.assertEqual(self.request('/api/state', cookie=cookie)[0], 401)

    def test_non_object_payload_rejected(self):
        self.assertEqual(self.request('/api/register', [1, 2])[0], 400)


if __name__ == '__main__':
    unittest.main()
