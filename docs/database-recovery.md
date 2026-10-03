# Gather database backup and restore drill

Run from the repository root on a POSIX filesystem supporting hard links. Use an existing private output directory owned by the operator. These commands are examples for a test environment, not a production cutover.

```sh
python scripts/database_recovery.py backup /test-data/team.db /private-backups/team-2026-10-03.db
python scripts/database_recovery.py restore /private-backups/team-2026-10-03.db /test-data/restored-team.db
```

The online SQLite backup API captures committed WAL changes; copying the main `.db` file alone can miss those changes. The tool opens the source read-only, verifies database integrity, required application tables/columns and foreign keys, then publishes a standalone snapshot only after validation. Files are created with mode 0600. An existing destination, including a symlink or file created during backup, is never overwritten. Failed validation or the 30-second snapshot deadline leaves no new destination. This deadline limits the backup operation, not the subsequent full integrity check.

Restore always targets a new inactive path. It clears sessions and invitations before publication: restoring an old image must not reactivate those saved access credentials. Users must log in again and owners must issue new invitations. Passwords, users, memberships and business data are restored to their snapshot state; later membership removals and password changes must be reconciled by the operator before allowing access. Session removal alone does not reconcile identity-provider or business permissions.

Test before switching:

1. Keep the running service on its current database. Restore into a separate directory with trusted permissions.
2. Start an isolated test service with `TEAMSAAS_DB` set to the restored path, never the production path. Use private network access and no outbound integrations.
3. Verify login, workspace isolation, project/task records and expected snapshot time. Reconcile post-snapshot access changes before any real cutover.
4. Only after an operational review, stop the service and switch its configured database path. Retain the original database and define rollback. This tool performs neither cutover nor deletion of the original.

Regression: `python -m unittest discover -s tests/recovery -p 'test_*.py' -v`. Ten disposable-data tests cover committed WAL data while a writer has an uncommitted transaction, real HTTP login/state after restore, both workspace datasets, session/invite invalidation, immutable snapshots, private permissions, corrupt/wrong-schema/foreign-key damaged inputs, missing sources, deadlines, existing/broken-symlink/racing destinations and CLI exit status.

Limits: snapshots contain personal data and password hashes. Mode 0600 is not encryption. Offsite retention, encryption/key custody, automatic scheduling, signed provenance, RPO/RTO targets, power-loss durability, full permission reconciliation and production recovery exercises remain operational work. Hard-link publication is atomic against overwrite; this tool does not guarantee storage-device or power-failure durability. Only restore operator-trusted backups.

References: [SQLite Online Backup API](https://www.sqlite.org/backup.html), [Python sqlite3 backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).
