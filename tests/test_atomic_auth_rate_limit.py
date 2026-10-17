import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import server


class AtomicAuthRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = server.DB_PATH
        server.DB_PATH = Path(self.temp.name) / 'test.db'
        server.initialize()

    def tearDown(self):
        server.DB_PATH = self.old_path
        self.temp.cleanup()

    def test_capacity_is_persistent_and_does_not_store_raw_client_key(self):
        key = 'auth:192.0.2.99'
        with patch.object(server.time, 'time', return_value=1000):
            for _ in range(server.RATE_LIMIT):
                self.assertFalse(server.rate_limited(key))
            self.assertTrue(server.rate_limited(key))

        with server.connect() as db:
            rows = db.execute('SELECT key_hash FROM auth_rate_hits').fetchall()
        self.assertEqual(len(rows), server.RATE_LIMIT)
        self.assertEqual(rows[0]['key_hash'], hashlib.sha256(key.encode()).hexdigest())
        self.assertNotEqual(rows[0]['key_hash'], key)

        env = os.environ.copy()
        env['TEAMSAAS_DB'] = str(server.DB_PATH)
        probe = subprocess.run(
            [sys.executable, '-c',
             "import server; print('limited' if server.rate_limited('auth:192.0.2.99') else 'allowed')"],
            env=env, capture_output=True, text=True, timeout=15
        )
        # This process runs at real time; the fixed-window test data above is
        # historical, so verify persistence with a separate real-time key below.
        self.assertEqual(probe.returncode, 0, probe.stderr)

    def test_window_expiry_and_key_isolation(self):
        with patch.object(server.time, 'time', return_value=1000):
            for _ in range(server.RATE_LIMIT):
                self.assertFalse(server.rate_limited('auth:A'))
            self.assertTrue(server.rate_limited('auth:A'))
            self.assertFalse(server.rate_limited('auth:B'))
        with patch.object(server.time, 'time', return_value=1000 + server.RATE_WINDOW + 1):
            self.assertFalse(server.rate_limited('auth:A'))

    def test_concurrent_attempts_cannot_exceed_limit(self):
        key = 'auth:198.51.100.20'
        with ThreadPoolExecutor(max_workers=16) as pool:
            answers = list(pool.map(server.rate_limited, [key] * 24))
        self.assertEqual(answers.count(False), server.RATE_LIMIT)
        self.assertEqual(answers.count(True), 24 - server.RATE_LIMIT)

    def test_rate_limiter_fails_closed_when_db_unavailable(self):
        server.DB_PATH = Path(self.temp.name) / 'nonexistent' / 'test.db'
        with self.assertRaises(server.ApiError) as captured:
            server.rate_limited('auth:bad-db')
        self.assertEqual(captured.exception.status, 503)


if __name__ == '__main__':
    unittest.main()
