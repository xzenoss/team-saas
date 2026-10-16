import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
from http.server import ThreadingHTTPServer


class GatherIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_db = server.DB_PATH
        cls.old_demo = os.environ.get('TEAMSAAS_ENABLE_DEMO')
        os.environ.pop('TEAMSAAS_ENABLE_DEMO', None)
        server.DB_PATH = Path(cls.tmp.name) / 'gather-test.db'
        server.initialize()
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        server.DB_PATH = cls.old_db
        if cls.old_demo is None:
            os.environ.pop('TEAMSAAS_ENABLE_DEMO', None)
        else:
            os.environ['TEAMSAAS_ENABLE_DEMO'] = cls.old_demo
        cls.tmp.cleanup()

    def request(self, path, payload=None, cookie=None, csrf=None, origin=None):
        headers = {}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if cookie:
            headers['Cookie'] = cookie
        if csrf:
            headers['X-CSRF-Token'] = csrf
        if origin:
            headers['Origin'] = origin
        raw = json.dumps(payload).encode() if payload is not None else None
        req = Request('http://127.0.0.1:%s%s' % (self.port, path),
                      data=raw, method='POST' if payload is not None else 'GET',
                      headers=headers)
        try:
            with urlopen(req, timeout=4) as response:
                return response.status, json.loads(response.read()), response.headers.get('Set-Cookie')
        except HTTPError as exc:
            return exc.code, json.loads(exc.read()), exc.headers.get('Set-Cookie')

    def register(self, email, workspace):
        status, _, cookie = self.request('/api/register', {
            'name':'Test Account', 'email':email, 'password':'long-test-password-2026',
            'workspace':workspace
        })
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)
        status, state, _ = self.request('/api/state', cookie=cookie.split(';')[0])
        self.assertEqual(status, 200)
        return cookie.split(';')[0], state

    def test_security_and_cross_tenant(self):
        code, _, _ = self.request('/api/demo', {})
        self.assertEqual(code, 403)

        a, sa = self.register('a@example.test', 'Tenant A')
        b, sb = self.register('b@example.test', 'Tenant B')

        status, _, _ = self.request('/api/projects/create', {'name':'Secret','description':'A','color':'orange'}, cookie=a)
        self.assertEqual(status, 403)  # without CSRF
        status, sa, _ = self.request('/api/projects/create',
                                    {'name':'Secret','description':'A','color':'orange'},
                                    cookie=a, csrf=sa['csrf'])
        self.assertEqual(status, 200)
        private_id = sa['projects'][0]['id']
        self.assertEqual(sb['projects'], [])

        forged = {'title':'Stolen','description':'x','project_id':private_id,
                  'status':'todo','priority':'high','assignee_id':'','due':''}
        status, _, _ = self.request('/api/tasks/create', forged, cookie=b, csrf=sb['csrf'])
        self.assertEqual(status, 404)
        status, state_b, _ = self.request('/api/state',cookie=b)
        self.assertEqual(status,200)
        self.assertEqual(state_b['tasks'], [])
        self.assertEqual(state_b['projects'], [])

        status, _, _ = self.request('/api/projects/create',
                                    {'name':'Bad Origin','description':'','color':'orange'},
                                    cookie=a, csrf=sa['csrf'],origin='https://evil.example.test')
        self.assertEqual(status,403)

        with sqlite3.connect(server.DB_PATH) as db:
            db.execute("UPDATE members SET role='member' WHERE workspace_id=? AND user_id=?",
                       (sb['workspace']['id'],sb['user']['id']))
        status, _, _ = self.request('/api/n8n/webhook',{'private':'payload'},
                                    cookie=b,csrf=sb['csrf'])
        self.assertEqual(status,403)


if __name__ == '__main__':
    unittest.main()
