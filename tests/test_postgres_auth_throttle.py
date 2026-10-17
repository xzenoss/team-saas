"""Real PostgreSQL tests; enabled only in the disposable postgres CI job."""
import hashlib
import hmac
import os
import secrets
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import server
from postgres_rate_limiter import postgres_rate_limited


@unittest.skipUnless(
    os.environ.get('TEAMSAAS_AUTH_RATE_TEST_DSN'),
    'Requires isolated PostgreSQL integration test DSN'
)
class PostgresAuthThrottleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['TEAMSAAS_AUTH_RATE_TEST_DSN']
        cls.secret = 'ci-throttle-pepper-not-a-production-secret-2026'
        cls.env = {
            'TEAMSAAS_AUTH_RATE_BACKEND': 'postgres',
            'TEAMSAAS_AUTH_RATE_DSN': cls.dsn,
            'TEAMSAAS_AUTH_RATE_SECRET': cls.secret,
        }

    def key(self):
        return 'auth:ci-test-' + secrets.token_hex(14)

    def test_concurrent_calls_share_atomic_counter(self):
        key = self.key()
        with patch.dict(os.environ, self.env):
            with ThreadPoolExecutor(max_workers=12) as workers:
                results = list(workers.map(server.rate_limited, [key] * 24))
        self.assertEqual(results.count(False), server.RATE_LIMIT)
        self.assertEqual(results.count(True), 24 - server.RATE_LIMIT)

    def test_counter_persists_across_independent_process(self):
        key = self.key()
        with patch.dict(os.environ, self.env):
            for _ in range(server.RATE_LIMIT):
                self.assertFalse(server.rate_limited(key))
            external_env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, '-c',
             'import server, sys; print(server.rate_limited(sys.argv[1]))', key],
            env=external_env, capture_output=True, text=True, timeout=20
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'True')

    def test_window_expires_using_database_clock(self):
        key = self.key()
        args = {'dsn': self.dsn, 'secret': self.secret, 'window': 1, 'limit': 2}
        self.assertFalse(postgres_rate_limited(key, **args))
        self.assertFalse(postgres_rate_limited(key, **args))
        self.assertTrue(postgres_rate_limited(key, **args))
        time.sleep(1.15)
        self.assertFalse(postgres_rate_limited(key, **args))

    def test_keys_are_hmac_not_raw_identifiers(self):
        import psycopg
        key = self.key()
        self.assertFalse(postgres_rate_limited(
            key, dsn=self.dsn, secret=self.secret, window=60, limit=2
        ))
        expected = hmac.new(self.secret.encode(), key.encode(), hashlib.sha256).hexdigest()
        with psycopg.connect(self.dsn) as db:
            row = db.execute(
                'SELECT key_hash FROM auth_rate_hits WHERE key_hash=%s',
                (expected,)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], expected)
        self.assertNotIn(key, row[0])

    def test_store_outage_and_bad_configuration_fail_closed(self):
        with patch.dict(os.environ, self.env | {
            'TEAMSAAS_AUTH_RATE_SECRET': 'short'
        }):
            with self.assertRaises(server.ApiError) as err:
                server.rate_limited(self.key())
            self.assertEqual(err.exception.status, 503)
        with patch.dict(os.environ, self.env | {
            'TEAMSAAS_AUTH_RATE_DSN':
                'postgresql://nonexistent:invalid@127.0.0.1:1/missing'
        }):
            with self.assertRaises(server.ApiError) as err:
                server.rate_limited(self.key())
            self.assertEqual(err.exception.status, 503)
        with patch.dict(os.environ, {
            'TEAMSAAS_ENVIRONMENT': 'production',
            'TEAMSAAS_AUTH_RATE_BACKEND': 'memory'
        }):
            with self.assertRaises(server.ApiError) as err:
                server.rate_limited(self.key())
            self.assertEqual(err.exception.status, 503)

    def test_runtime_identity_cannot_modify_workspace_data(self):
        import psycopg
        with psycopg.connect(self.dsn) as db:
            grants = db.execute(
                "SELECT has_table_privilege(current_user,'projects','INSERT'), "
                "has_table_privilege(current_user,'sessions','SELECT'), "
                "has_table_privilege(current_user,'auth_rate_hits','INSERT')"
            ).fetchone()
        self.assertEqual(grants, (False, False, True))


if __name__ == '__main__':
    unittest.main()
