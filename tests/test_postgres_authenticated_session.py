"""Disposable PostgreSQL integration tests for credential-bound session issuance.

These tests do not enable a new public HTTP login endpoint.
"""
import hashlib
import os
import secrets
import subprocess
import sys
import unittest

import server
from postgres_authenticated_session import (
    LoginThrottled, authenticate_and_issue
)
from postgres_rate_limiter import postgres_rate_limited
from postgres_session_store import SessionStoreUnavailable, resolve_session


@unittest.skipUnless(
    all(os.environ.get(x) for x in (
        'TEAMSAAS_IDENTITY_TEST_DSN', 'TEAMSAAS_AUTH_RATE_TEST_DSN',
        'TEAMSAAS_SESSION_TEST_DSN', 'TEAMSAAS_SESSION_ADMIN_DSN'
    )),
    'Requires disposable PostgreSQL credentials'
)
class CredentialBoundSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg = psycopg
        cls.admin_dsn = os.environ['TEAMSAAS_SESSION_ADMIN_DSN']
        cls.identity_dsn = os.environ['TEAMSAAS_IDENTITY_TEST_DSN']
        cls.session_dsn = os.environ['TEAMSAAS_SESSION_TEST_DSN']
        cls.throttle_dsn = os.environ['TEAMSAAS_AUTH_RATE_TEST_DSN']
        cls.throttle_secret = 'disposable-ci-identity-throttle-secret-2026'
        cls.password = 'test-only-strong-passphrase'
        with cls.pg.connect(cls.admin_dsn) as db:
            cls.original_hash = db.execute(
                "SELECT password FROM users WHERE id='u-a'"
            ).fetchone()[0]
            cls.other_original_hash = db.execute(
                "SELECT password FROM users WHERE id='u-b'"
            ).fetchone()[0]
            db.execute(
                "UPDATE users SET password=%s WHERE id='u-a'",
                (server.hash_password(cls.password),)
            )
            db.execute(
                "UPDATE users SET password=%s WHERE id='u-b'",
                (server.hash_password(cls.password),)
            )

    @classmethod
    def tearDownClass(cls):
        with cls.pg.connect(cls.admin_dsn) as db:
            db.execute('DELETE FROM sessions')
            db.execute(
                "UPDATE users SET password=%s WHERE id='u-a'",
                (cls.original_hash,)
            )
            db.execute(
                "UPDATE users SET password=%s WHERE id='u-b'",
                (cls.other_original_hash,)
            )
            db.execute('UPDATE members SET revoked_at=NULL')

    def setUp(self):
        self.client_key = secrets.token_hex(20)
        with self.pg.connect(self.admin_dsn) as db:
            db.execute('DELETE FROM sessions')
            db.execute('UPDATE members SET revoked_at=NULL')
            db.execute("UPDATE users SET password=%s WHERE id IN ('u-a','u-b')", (server.hash_password(self.password),))

    def login(self, **kwargs):
        options = {
            'identity_dsn': self.identity_dsn,
            'throttle_dsn': self.throttle_dsn,
            'throttle_secret': self.throttle_secret,
            'trusted_client_key': self.client_key,
            'email': 'a@example.test',
            'password': self.password,
            'workspace_id': 'w-a',
        }
        options.update(kwargs)
        return authenticate_and_issue(**options)

    def test_valid_password_binds_session_to_database_identity(self):
        cookie, csrf = self.login()
        self.assertTrue(cookie.startswith('w-a.'))
        self.assertTrue(csrf)
        resolved = resolve_session(dsn=self.session_dsn, token=cookie)
        self.assertEqual(resolved['user_id'], 'u-a')
        self.assertEqual(resolved['workspace_id'], 'w-a')
        self.assertEqual(resolved['csrf'], csrf)
        digest = hashlib.sha256(cookie.encode('ascii')).hexdigest()
        with self.pg.connect(self.admin_dsn) as db:
            value = db.execute(
                'SELECT token FROM sessions WHERE user_id=%s',
                ('u-a',)
            ).fetchone()
        self.assertEqual(value, (digest,))
        self.assertNotEqual(cookie, digest)

    def test_cross_workspace_claim_not_authority(self):
        self.assertIsNone(self.login(workspace_id='w-b'))
        self.assertIsNone(self.login(email='b@example.test', workspace_id='w-a'))
        with self.pg.connect(self.admin_dsn) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)

    def test_missing_user_wrong_password_and_bad_hash_fail_closed(self):
        self.assertIsNone(self.login(email='missing@example.test'))
        self.assertIsNone(self.login(password='incorrect-password'))
        with self.pg.connect(self.admin_dsn) as db:
            db.execute("UPDATE users SET password='malformed' WHERE id='u-a'")
        self.assertIsNone(self.login())
        with self.pg.connect(self.admin_dsn) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)

    def test_revocation_prevents_future_login_and_invalidates_existing_cookie(self):
        cookie, _ = self.login()
        with self.pg.connect(self.admin_dsn) as db:
            db.execute(
                "UPDATE members SET revoked_at=EXTRACT(EPOCH FROM clock_timestamp()) "
                "WHERE workspace_id='w-a' AND user_id='u-a'"
            )
        self.assertIsNone(self.login())
        self.assertIsNone(resolve_session(dsn=self.session_dsn, token=cookie))

    def test_session_resolves_from_independent_worker(self):
        cookie, _ = self.login()
        result = subprocess.run(
            [sys.executable, '-c',
             'from postgres_session_store import resolve_session; import sys; '
             "print(resolve_session(dsn=sys.argv[1],token=sys.argv[2])['user_id'])",
             self.session_dsn, cookie],
            capture_output=True, text=True, timeout=20
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'u-a')

    def test_throttle_and_backend_failure_do_not_bypass_security(self):
        opts = dict(
            dsn=self.throttle_dsn, secret=self.throttle_secret,
            window=300, limit=12
        )
        for i in range(12):
            self.assertFalse(postgres_rate_limited('auth:' + self.client_key, **opts))
        with self.assertRaises(LoginThrottled):
            self.login()
        with self.assertRaises(SessionStoreUnavailable):
            self.login(throttle_secret='short')
        with self.assertRaises(SessionStoreUnavailable):
            self.login(identity_dsn='')
        with self.pg.connect(self.admin_dsn) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)

    def test_identity_role_restricted_to_credentials_and_session_issuance(self):
        with self.pg.connect(self.identity_dsn) as db:
            row = db.execute(
                "SELECT has_column_privilege(current_user,'users','password','SELECT'), "
                "has_column_privilege(current_user,'users','name','SELECT'), "
                "has_table_privilege(current_user,'users','UPDATE'), "
                "has_table_privilege(current_user,'projects','SELECT'), "
                "has_table_privilege(current_user,'sessions','DELETE')"
            ).fetchone()
        self.assertEqual(row, (True, False, False, False, False))


if __name__ == '__main__':
    unittest.main()
