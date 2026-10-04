"""Deterministic concurrent invite claims through the real HTTP handler and SQLite."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_api_security as fixtures
import server


class HookCursor:
    def __init__(self, cursor, hook):
        self.cursor, self.hook = cursor, hook

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None:
            self.hook(row)
        return row


class HookConnection:
    """Preserve real SQL/transactions; delay only after the invite lookup returns."""
    def __init__(self, db, hook):
        self.db, self.hook = db, hook

    def execute(self, sql, *args):
        cursor = self.db.execute(sql, *args)
        if sql.startswith('SELECT * FROM invites WHERE'):
            return HookCursor(cursor, self.hook)
        return cursor

    def __getattr__(self, name):
        return getattr(self.db, name)

    def __enter__(self):
        self.db.__enter__()
        return self

    def __exit__(self, *args):
        return self.db.__exit__(*args)


class InviteConcurrencyTests(unittest.TestCase):
    def setUp(self):
        # Reuse only the existing fixture helpers, not its inherited test methods.
        self.fx = fixtures.ApiSecurityTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.third = self.fx.register('Gamma')
        status, data, _ = self.fx.request('/api/invites/create', self.fx.a, {})
        self.assertEqual(status, 200)
        self.token = data['token']

    def rows(self, tables=('members', 'sessions', 'invites', 'activity')):
        with server.connect() as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1,2')] for table in tables}

    def join(self, client):
        return self.fx.request('/api/join', client, {'token': self.token})

    def race(self, clients):
        barrier = threading.Barrier(2)
        connect = server.connect
        with patch.object(server, 'connect', side_effect=lambda: HookConnection(connect(), lambda row: barrier.wait(timeout=5))):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.join, client) for client in clients]
                return [future.result(timeout=10) for future in futures]

    def test_concurrent_different_users_have_exactly_one_winner(self):
        clients = [self.fx.b, self.third]
        before = self.rows()
        states = [self.fx.state(client) for client in clients]
        results = self.race(clients)
        self.assertEqual(sorted(result[0] for result in results), [200, 404])
        winner = next(index for index, result in enumerate(results) if result[0] == 200)
        loser = 1 - winner
        self.assertEqual(results[winner][1]['workspace']['id'], self.fx.a['workspace'])
        self.assertEqual(self.fx.state(clients[loser]), states[loser])
        after = self.rows()
        self.assertEqual(len(after['members']), len(before['members']) + 1)
        self.assertEqual(len(after['activity']), len(before['activity']) + 1)
        self.assertEqual(after['invites'], [])
        memberships = [row for row in after['members'] if row[0] == self.fx.a['workspace']]
        self.assertEqual({row[1] for row in memberships}, {self.fx.a['user'], clients[winner]['user']})
        for client in clients:
            self.assertEqual(self.join(client)[0], 404)
        self.assertEqual(after, self.rows())

    def test_concurrent_same_user_logs_only_one_join(self):
        before = self.rows()
        results = self.race([self.fx.b, self.fx.b])
        self.assertEqual(sorted(result[0] for result in results), [200, 404])
        after = self.rows()
        self.assertEqual(len(after['members']), len(before['members']) + 1)
        self.assertEqual(len(after['activity']), len(before['activity']) + 1)
        self.assertEqual(after['invites'], [])

    def test_failed_join_rolls_back_claim_membership_session_and_activity(self):
        before = self.rows()
        with patch.object(server.Handler, 'log_activity', side_effect=server.ApiError('Synthetic transaction failure', 503)):
            self.assertEqual(self.join(self.fx.b)[0], 503)
        self.assertEqual(before, self.rows())
        self.assertEqual(self.join(self.fx.b)[0], 200)

    def test_expiry_after_lookup_cannot_grant_membership(self):
        before = self.rows(('members', 'sessions', 'activity'))
        connect = server.connect
        def expire(row):
            with connect() as db:
                db.execute('UPDATE invites SET expires=? WHERE token=?', (time.time() - 1, row['token']))
        with patch.object(server, 'connect', side_effect=lambda: HookConnection(connect(), expire)):
            self.assertEqual(self.join(self.fx.b)[0], 404)
        self.assertEqual(before, self.rows(('members', 'sessions', 'activity')))
        with connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM invites').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
