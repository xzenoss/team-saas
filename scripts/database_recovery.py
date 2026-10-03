"""Consistent Gather snapshots; restores only to a new, inactive database path."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import time

TABLES = {'users', 'workspaces', 'members', 'sessions', 'projects', 'tasks', 'invites', 'activity'}


def validate(db):
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('Database integrity check failed')
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not TABLES.issubset(tables):
        raise ValueError('Not a Gather database: required tables are missing')
    # All columns used by the current application must be available.
    required = {'users': {'id', 'name', 'email', 'password'},
                'sessions': {'token', 'user_id', 'workspace_id', 'csrf', 'expires'},
                'invites': {'token', 'workspace_id', 'expires', 'created_by'},
                'members': {'workspace_id', 'user_id', 'role'},
                'projects': {'id', 'workspace_id', 'name', 'description', 'color', 'created'},
                'tasks': {'id', 'workspace_id', 'project_id', 'title', 'description', 'status',
                          'priority', 'assignee_id', 'due', 'created'},
                'activity': {'id', 'workspace_id', 'actor', 'message', 'created'},
                'workspaces': {'id', 'name'}}
    for table, columns in required.items():
        if not columns.issubset({row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}):
            raise ValueError('Not a compatible Gather database')
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('Database foreign key check failed')


def snapshot(source, destination, *, restore=False, timeout=30):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Destination already exists; choose a new inactive path')
    if not source.is_file():
        raise FileNotFoundError('Source database does not exist')
    if not destination.parent.is_dir():
        raise FileNotFoundError('Destination directory does not exist')
    fd, temp_name = tempfile.mkstemp(prefix='.gather-recovery-', suffix='.db', dir=destination.parent)
    os.close(fd)  # mkstemp creates a private 0600 file.
    temporary = Path(temp_name)
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('Snapshot deadline exceeded; no destination published')

    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                # SQLite backup includes committed WAL pages and produces a consistent snapshot.
                src.backup(dst, pages=128, progress=progress, sleep=0.05)
                dst.execute('PRAGMA journal_mode=DELETE')
                validate(dst)
                if restore:
                    with dst:
                        dst.execute('DELETE FROM sessions')
                        dst.execute('DELETE FROM invites')
                    validate(dst)
        with temporary.open('rb') as data:
            os.fsync(data.fileno())
        # Atomic, no-clobber publication. A racing existing file or symlink is never overwritten.
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(temporary) + suffix).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore'])
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        result = snapshot(args.source, args.destination, restore=args.operation == 'restore')
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')
    print('Verified database written to ' + str(result))


if __name__ == '__main__':
    main()
