# Gather PostgreSQL Session Store — staging contract

This is a PostgreSQL-backed **session lifecycle component and database integration suite**, NOT a cutover of the Gather HTTP server. Current `server.py` still uses SQLite for users, memberships, sessions, projects and tasks. Real production authentication and migration are not complete.

## Design

Migration `002_session_lifecycle.sql` adds a member `revoked_at` field, session expiration index and a SHA-256 digest shape constraint. The `postgres_session_store.py` implementation issues fresh 256-bit random session secrets and a separate CSRF secret. Only SHA-256 digests of complete session cookie values are written to PostgreSQL; plaintext cookie values are returned to the trusted issuer but never persisted.

A cookie has the format `<workspace-id>.<random-secret>`. The workspace component is **untrusted routing information**, not authorization. All resolution and revocation checks require a matching digest of the entire cookie under a transaction-local workspace RLS scope. Resolution also verifies PostgreSQL server-side expiration and *current unrevoked* membership. A member marked revoked immediately loses access to all sessions on the next lookup. A valid browser session can be revoked by deleting its digest under the matching workspace context.

## Security rules

- `issue_session` is an **internal trusted-only API**. Never accept `user_id` or `workspace_id` from browser JSON, a stale JWT or any unverified caller. A production IdP principal binding, membership authorization and tenant bootstrap are required before this can be exposed through HTTP.
- The CI session role can read membership and only select, insert and delete its own RLS-scoped sessions. It cannot read global `users`, update memberships, or access projects.
- The database RLS policy uses a custom `app.workspace_id` setting; this setting is not itself authenticated. Possession of the random cookie is indispensable to validating the claimed workspace.
- The caller must fail closed if the PostgreSQL session DB is unavailable. The module raises `SessionStoreUnavailable` for backend faults.
- A transaction-scoped identity must be reconstructed for every DB transaction; never persist a tenant GUC across pooled sessions.
- Since `sessions` references membership, member deletion must first revoke/delete dependent sessions in a reviewed IAM transaction; `revoked_at` is supported as a deny-first step.

## Disposable CI evidence

`.github/workflows/postgres-contract.yml` adds PostgreSQL 16 session migration, a restricted test role and integration tests covering:
- Randomized issue/resolve and digest-only persistence.
- Valid session resolution across separate Python processes.
- Rejecting modified workspace hints, wrong token secrets and foreign membership.
- Explicit revocation, expired tokens and revoked membership.
- A runtime role that cannot read global users or change membership.
- Fail-closed behavior for unavailable storage.

## Still blocking issue #6

- The application is **not wired to this module**; the current SQLite session APIs and authentication are unchanged.
- SQL data migration, principal identity mapping, read consistency, invitation lifecycle, atomic membership changes, production credential rotation, connection pooling, TLS verification, backup/restore and multiple live HTTP instances remain to implement.
- There is no session refresh/rotation, global sign-out, scheduled expired-session cleanup, audit of privileged revocations or token-family incident revocation yet.
- This branch is stacked on PR #12 and PR #11. Do not merge or deploy to production without review, integration, and the larger production readiness gate.
