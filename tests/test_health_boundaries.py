import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

import server
from http.server import ThreadingHTTPServer


class GatherHealthBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_db = server.DB_PATH
        server.DB_PATH = Path(cls.tmp.name) / 'gather-health-test.db'
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
        cls.tmp.cleanup()

    def test_liveness_readiness_and_correlation(self):
        for path in ('/livez', '/readyz', '/healthz'):
            request = Request(
                'http://127.0.0.1:%s%s' % (self.port, path),
                headers={'X-Request-ID': 'gather-test-123'}
            )
            with urlopen(request, timeout=4) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get('X-Request-ID'), 'gather-test-123')
                self.assertEqual(json.loads(response.read()), {'status': 'ok'})

    def test_invalid_request_id_is_replaced(self):
        request = Request(
            'http://127.0.0.1:%s/livez' % self.port,
            headers={'X-Request-ID': 'bad request id with spaces'}
        )
        with urlopen(request, timeout=4) as response:
            generated = response.headers.get('X-Request-ID')
            self.assertIsNotNone(generated)
            self.assertNotEqual(generated, 'bad request id with spaces')
            self.assertEqual(len(generated), 32)
            int(generated, 16)


if __name__ == '__main__':
    unittest.main()
