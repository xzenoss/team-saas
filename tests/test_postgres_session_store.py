"""PostgreSQL 16 integration tests for isolated session lifecycle contract.

Test database is disposable and pre-seeded by postgres_schema_contract.sql.
This subsystem is not yet wired to the HTTP application.
"""
import hashlib
import os
import secrets
import subprocess
import sys
import unittest

from postgres_session_store import (
    SessionStoreUnavailable, issue_session, resolve_session, revoke_session
)


@unittest.skipUnless(
    os.environ.get('TEAMSAAS_SESSION_TEST_DSN')
    and os.environ.get('TEAMSAAS_SESSION_ADMIN_DSN'),
    'Disposable PostgreSQL session CI required'
)
class PostgresSessionStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg = psycopg
        cls.dsn = os.environ['TEAMSAAS_SESSION_TEST_DSN']
        cls.admin_dsn = os.environ['TEAMSAAS_SESSION_ADMIN_DSN']

    def setUp(self):
        # All tests are run on a disposable PostgreSQL CI database.
        with self.pg.connect(self.admin_dsn) as db:
            db.execute("UPDATE members SET revoked_at=NULL WHERE workspace_id='w-a'")
            db.execute("UPDATE members SET revoked_at=NULL WHERE workspace_id='w-b'")

    def tearDown(self):
        with self.pg.connect(self.admin_dsn) as db:
            db.execute('DELETE FROM sessions')
            db.execute("UPDATE members SET revoked_at=NULL WHERE workspace_id='w-a'")
            db.execute("UPDATE members SET revoked_at=NULL WHERE workspace_id='w-b'")

    def test_issuance_hashes_cookie_and_stores_current_tenant_member(self):
        issued = issue_session(dsn=self.dsn, user_id='u-a', workspace_id='w-a')
        self.assertIsNotNone(issued)
        cookie, csrf = issued
        self.assertTrue(cookie.startswith('w-a.'))
        self.assertGreaterEqual(len(csrf), 24)
        result = resolve_session(dsn=self.dsn, token=cookie)
        self.assertEqual(result['user_id'], 'u-a')
        self.assertEqual(result['workspace_id'], 'w-a')
        self.assertEqual(result['role'], 'owner')
        self.assertEqual(result['csrf'], csrf)
        digest = hashlib.sha256(cookie.encode()).hexdigest()
        with self.pg.connect(self.admin_dsn) as db:
            record = db.execute("SELECT token FROM sessions WHERE workspace_id='w-a'").fetchone()
        self.assertEqual(record, (digest,))
        self.assertNotEqual(record[0], cookie)

    def test_resolve_is_shared_across_independent_process(self):
        cookie, _ = issue_session(dsn=self.dsn, user_id='u-a', workspace_id='w-a')
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, '-c',
             "import sys; from postgres_session_store import resolve_session; "
             "print(resolve_session(dsn=sys.argv[1],token=sys.argv[2])['workspace_id'])",
             self.dsn, cookie],
            env=env, capture_output=True, text=True, timeout=25
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'w-a')

    def test_foreign_workspace_claim_or_secret_cannot_reuse_session(self):
        cookie, _ = issue_session(dsn=self.dsn, user_id='u-a', workspace_id='w-a')
        tampered = 'w-b.' + cookie.split('.', 1)[1]
        self.assertIsNone(resolve_session(dsn=self.dsn, token=tampered))
        self.assertIsNone(resolve_session(dsn=self.dsn, token='w-a.' + secrets.token_hex(32)))
        self.assertIsNone(issue_session(dsn=self.dsn, user_id='u-b', workspace_id='w-a'))
        self.assertIsNone(resolve_session(dsn=self.dsn, token='w-a.' + 'nothex'))
        self.assertIsNone(resolve_session(dsn=self.dsn, token='w-a.' + '0'*64 + '.extra'))

    def test_revocation_is_immediate_across_connections(self):
        cookie, _ = issue_session(dsn=self.dsn, user_id='u-a', workspace_id='w-a')
        self.assertTrue(revoke_session(dsn=self.dsn, token=cookie))
        self.assertFalse(revoke_session(dsn=self.dsn, token=cookie))
        self.assertIsNone(resolve_session(dsn=self.dsn, token=cookie))

    def test_membership_revocation_blocks_existing_and_new_sessions(self):
        cookie, _ = issue_session(dsn=self.dsn, user_id='u-a', workspace_id='w-a')
        with self.pg.connect(self.admin_dsn) as db:
            db.execute(
                "UPDATE members SET revoked_at=EXTRACT(EPOCH FROM clock_timestamp()) "
                "WHERE workspace_id='w-a' AND user_id='u-a'"
            )
        self.assertIsNone(resolve_session(dsn=self.dsn, token=cookie))
        self.assertIsNone(issue_session(dsn=self.dsn, user_id='u-a', workspace_id='w-a'))

    def test_expired_cookie_denied_with_database_time(self):
        cookie, _ = issue_session(dsn=self.dsn, user_id='u-a', workspace_id='w-a')
        digest = hashlib.sha256(cookie.encode()).hexdigest()
        with self.pg.connect(self.admin_dsn) as db:
            db.execute(
                "UPDATE sessions SET expires=EXTRACT(EPOCH FROM clock_timestamp()) - 1 "
                "WHERE token=%s", (digest,)
            )
        self.assertIsNone(resolve_session(dsn=self.dsn, token=cookie))

    def test_restricted_role_and_rls_are_default_deny(self):
        with self.pg.connect(self.dsn) as db:
            n = db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
            self.assertEqual(n, 0)
            access = db.execute(
                "SELECT has_table_privilege(current_user,'users','SELECT'), "
                "has_table_privilege(current_user,'members','UPDATE'), "
                "has_table_privilege(current_user,'sessions','UPDATE')"
            ).fetchone()
            self.assertEqual(access, (False, False, False))

    def test_invalid_store_configuration_fails_closed(self):
        with self.assertRaises(SessionStoreUnavailable):
            issue_session(dsn='', user_id='u-a', workspace_id='w-a')
        with self.assertRaises(SessionStoreUnavailable):
            resolve_session(
                dsn='postgresql://no-user:bad@127.0.0.1:1/missing',
                token='w-a.' + secrets.token_hex(32)
            )
        self.assertIsNone(resolve_session(dsn=self.dsn, token='malformed'))


if __name__ == '__main__':
    unittest.main()
