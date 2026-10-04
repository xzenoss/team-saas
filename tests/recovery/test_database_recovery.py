"""Disposable SQLite recovery drill, including the real restored HTTP handler."""
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server
from scripts.database_recovery import snapshot, TABLES


class QuietHandler(server.Handler):
    def log_message(self, *args):
        pass


class DatabaseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'live.db'
        self.backup = self.root / 'snapshot.db'
        self.restored = self.root / 'restored.db'
        self.db_patch = patch.object(server, 'DB_PATH', self.source)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        with server._rate_lock:
            server._rate_hits.clear()
        server.initialize()
        self.db = server.connect()
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA wal_autocheckpoint=0')
        for label in ('a', 'b'):
            self.db.execute('INSERT INTO users VALUES(?,?,?,?)',
                            (label, label, label + '@example.test', server.hash_password('Synthetic-password-123!')))
            self.db.execute('INSERT INTO workspaces VALUES(?,?)', (label, 'Workspace ' + label))
            self.db.execute('INSERT INTO members VALUES(?,?,?)', (label, label, 'owner'))
            self.db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?)', (label, label, 'Private ' + label, '', 'violet', 1))
            self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (label, label, label, 'Task ' + label, '', 'todo', 'high', label, '', 1))
            self.db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (server.digest('cookie-' + label), label, label, 'csrf', 9999999999))
            self.db.execute('INSERT INTO invites VALUES(?,?,?,?)', (server.digest('invite-' + label), label, 9999999999, label))
            self.db.execute('INSERT INTO activity(workspace_id,actor,message,created) VALUES(?,?,?,?)', (label, label, 'Created', 1))
        self.db.commit()

    def rows(self, path, tables=TABLES):
        with sqlite3.connect(path) as db:
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1,2').fetchall() for table in tables}

    def test_backup_includes_committed_wal_rows_and_is_private(self):
        self.assertGreater(Path(str(self.source) + '-wal').stat().st_size, 0)
        before = self.rows(self.source)
        self.db.execute("UPDATE tasks SET title='Uncommitted change' WHERE id='a'")
        snapshot(self.source, self.backup)
        self.assertEqual(before, self.rows(self.backup))
        self.assertEqual(before, self.rows(self.source))
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)
        self.assertFalse(Path(str(self.backup) + '-wal').exists())
        self.db.rollback()

    def test_restored_service_requires_login_and_preserves_tenant_isolation(self):
        snapshot(self.source, self.backup)
        before = self.rows(self.backup)
        # Later source changes cannot retroactively change the saved snapshot.
        self.db.execute("UPDATE tasks SET title='Later change' WHERE id='a'")
        self.db.commit()
        snapshot(self.backup, self.restored, restore=True)
        actual = self.rows(self.restored)
        for table in TABLES - {'sessions', 'invites'}:
            self.assertEqual(before[table], actual[table])
        self.assertEqual(actual['sessions'], [])
        self.assertEqual(actual['invites'], [])
        self.assertEqual(before, self.rows(self.backup))
        self.assertEqual(self.restored.stat().st_mode & 0o777, 0o600)
        with patch.object(server, 'DB_PATH', self.restored):
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            def request(path, cookie='', data=None):
                conn = http.client.HTTPConnection(*httpd.server_address, timeout=5)
                try:
                    conn.request('POST' if data else 'GET', path, json.dumps(data) if data else None,
                                 {'Content-Type': 'application/json', 'Cookie': cookie})
                    response = conn.getresponse()
                    return response.status, json.loads(response.read()), dict(response.getheaders())
                finally:
                    conn.close()
            try:
                self.assertEqual(request('/api/state', 'gather_session=cookie-a')[0], 401)
                for label in ('a', 'b'):
                    status, _, headers = request('/api/login', data={'email': label + '@example.test', 'password': 'Synthetic-password-123!'})
                    self.assertEqual(status, 200)
                    status, state, _ = request('/api/state', headers['Set-Cookie'].split(';')[0])
                    self.assertEqual(status, 200)
                    self.assertEqual(state['workspace']['id'], label)
                    self.assertEqual([task['title'] for task in state['tasks']], ['Task ' + label])
                    self.assertEqual([project['id'] for project in state['projects']], [label])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

    def test_existing_destinations_are_never_overwritten(self):
        self.backup.write_bytes(b'Keep this file')
        for restore in (False, True):
            with self.assertRaises(FileExistsError):
                snapshot(self.source, self.backup, restore=restore)
            self.assertEqual(self.backup.read_bytes(), b'Keep this file')

    def test_broken_symlink_destination_is_rejected(self):
        missing = self.root / 'missing-target.db'
        self.backup.symlink_to(missing)
        with self.assertRaises(FileExistsError):
            snapshot(self.source, self.backup)
        self.assertFalse(missing.exists())

    def test_corrupt_or_wrong_schema_sources_publish_nothing(self):
        bad = self.root / 'bad.db'
        bad.write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError):
            snapshot(bad, self.backup, restore=True)
        bad.unlink()
        with sqlite3.connect(bad) as db:
            db.execute('CREATE TABLE unrelated(id)')
        with self.assertRaises(ValueError):
            snapshot(bad, self.backup, restore=True)
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.root.glob('.gather-recovery-*')), [])

    def test_foreign_key_damage_is_rejected(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE tasks SET project_id='missing' WHERE id='a'")
        with self.assertRaises(ValueError):
            snapshot(self.source, self.backup)
        self.assertFalse(self.backup.exists())

    def test_missing_source_is_not_created(self):
        missing = self.root / 'absent.db'
        with self.assertRaises(FileNotFoundError):
            snapshot(missing, self.backup)
        self.assertFalse(missing.exists())
        self.assertFalse(self.backup.exists())

    def assert_semantic_damage_rejected(self, statement, message):
        self.db.execute(statement)
        self.db.commit()
        before = self.rows(self.source)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchall()[0][0], 'ok')
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        for restore in (False, True):
            with self.subTest(restore=restore):
                target = self.restored if restore else self.backup
                with self.assertRaisesRegex(ValueError, message):
                    snapshot(self.source, target, restore=restore)
                self.assertFalse(target.exists())
                self.assertEqual(list(self.root.glob('.gather-recovery-*')), [])
                self.assertEqual(before, self.rows(self.source))

    def test_cross_workspace_project_is_rejected(self):
        self.assert_semantic_damage_rejected(
            "UPDATE tasks SET project_id='b' WHERE id='a'", 'project is outside')

    def test_foreign_workspace_assignee_is_rejected(self):
        self.assert_semantic_damage_rejected(
            "UPDATE tasks SET assignee_id='b' WHERE id='a'", 'assignee is not a member')

    def test_removed_member_assignee_is_rejected(self):
        self.assert_semantic_damage_rejected(
            "DELETE FROM members WHERE workspace_id='a' AND user_id='a'", 'assignee is not a member')

    def test_unknown_member_role_is_rejected(self):
        self.assert_semantic_damage_rejected(
            "UPDATE members SET role='administrator' WHERE workspace_id='a'", 'Unsupported workspace member role')

    def test_unassigned_and_shared_member_tasks_are_allowed(self):
        self.db.execute("UPDATE tasks SET assignee_id=NULL WHERE id='a'")
        self.db.execute("INSERT INTO members VALUES('b','a','member')")
        self.db.execute("UPDATE tasks SET assignee_id='a' WHERE id='b'")
        self.db.commit()
        before = self.rows(self.source)
        snapshot(self.source, self.backup)
        self.assertEqual(before, self.rows(self.backup))
        snapshot(self.backup, self.restored, restore=True)
        for table in TABLES - {'sessions', 'invites'}:
            self.assertEqual(before[table], self.rows(self.restored)[table])
        self.assertEqual(self.rows(self.restored, {'sessions', 'invites'}), {'sessions': [], 'invites': []})

    def test_failed_backup_deadline_leaves_no_destination(self):
        with self.assertRaises(TimeoutError):
            snapshot(self.source, self.backup, timeout=-1)
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.root.glob('.gather-recovery-*')), [])

    def test_cli_round_trip_and_failure_exit_code(self):
        script = str(Path(__file__).resolve().parents[2] / 'scripts/database_recovery.py')
        backup = self.root / 'snapshot #1.db'
        for operation, source, target in (('backup', self.source, backup), ('restore', backup, self.restored)):
            result = subprocess.run([sys.executable, script, operation, str(source), str(target)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows(self.source, {'tasks', 'members'}), self.rows(self.restored, {'tasks', 'members'}))
        self.assertEqual(self.rows(self.restored, {'sessions', 'invites'}), {'sessions': [], 'invites': []})
        before = self.restored.read_bytes()
        result = subprocess.run([sys.executable, script, 'restore', str(backup), str(self.restored)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self.restored.read_bytes())

    def test_destination_created_during_backup_is_preserved(self):
        link = os.link
        def publish(source, target):
            Path(target).write_bytes(b'Concurrent owner file')
            return link(source, target)
        with patch('scripts.database_recovery.os.link', side_effect=publish):
            with self.assertRaises(FileExistsError):
                snapshot(self.source, self.backup)
        self.assertEqual(self.backup.read_bytes(), b'Concurrent owner file')
        self.assertEqual(list(self.root.glob('.gather-recovery-*')), [])


if __name__ == '__main__':
    unittest.main()
