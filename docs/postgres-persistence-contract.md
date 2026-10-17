# Gather PostgreSQL persistence contract — pre-production foundation

This is a **schema contract and isolated database integration test**, not a switch of the Gather application to PostgreSQL. The current `server.py` still uses `sqlite3` and existing UAT flows remain unchanged.

## Initial tables

`users`, `workspaces`, `members`, `sessions`, `projects`, `tasks`, `invites`, `activity`, and `auth_rate_hits` are represented in `db/postgres/001_foundation.sql`. Composite foreign keys reject tasks referring to a different workspace's project or assignee; sessions must belong to an actual member in the same workspace. A task may remain unassigned.

## Isolation policy and caveats

Tenant tables have PostgreSQL `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`. The policy uses transaction-local `app.workspace_id` and returns no tenant rows without a context. The tests run a non-superuser, non-BYPASSRLS restricted runtime role, not only a database superuser.

**Important limitation:** a custom PostgreSQL GUC such as `app.workspace_id` is not itself authenticated. It must be bound by a trusted application identity layer within the same transaction after authenticating the caller and establishing actual workspace membership. Do not expose raw SQL access to callers or accept workspace selection solely from request JSON. The `users` global identity table is intentionally not granted to the CI runtime role; secure authentication bootstrap, cross-workspace account access and session migration require further design.

The SQL's `users.password` field is kept for schema compatibility, **not** a recommendation to persist raw passwords. Existing application hashes are PBKDF2; identity lifecycle and stronger production password/SSO policies remain separate gates.

## Run the disposable database contract

The pull-request workflow starts a fresh PostgreSQL 16 container and runs:

```sh
psql -v ON_ERROR_STOP=1 -f db/postgres/001_foundation.sql
psql -v ON_ERROR_STOP=1 -f tests/postgres_schema_contract.sql
```

The tests verify valid records, cross-workspace project/assignee/session references being rejected, task status validation, default-deny RLS and visible tenant-specific rows, a rejected cross-tenant insert and a successful same-tenant insert.

## Explicitly not done

- Application PostgreSQL connection/pool abstraction and SQL dialect changes (`INSERT OR IGNORE`, SQLite placeholders, `lastrowid`, etc.).
- Durable migration of SQLite data, identity mapping, session cutover, or online dual writes.
- Full row-access authorization with a trusted IdP, audited IAM provisioning and deprovisioning.
- Redis/shared rate limiter, multi-replica auth/session behavior and PostgreSQL backup/restore.
- Production load, failure recovery, KMS/secrets, TLS, SLO/alerting, or independent security review.

All these remain blocking in issue #6 / parent #3. Do not deploy this schema to live environments; it is deliberately validated on disposable CI data only.
