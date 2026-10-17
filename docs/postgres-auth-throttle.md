# Gather: PostgreSQL-backed authentication throttle (prototype)

This slice integrates a real shared PostgreSQL backend for **authentication attempt admission only**. It builds on PR #11's isolated PostgreSQL schema; it does **not** replace the application's SQLite persistence, session storage, or workspace authorization. Production remains blocked under issues #6 and #3.

## Opt-in configuration

Install `requirements-postgres.txt` in the **test/staging** runtime, and provision an independently authenticated database role with only SELECT, INSERT and DELETE on `auth_rate_hits` plus sequence usage. The disposable CI-only role and password in `tests/postgres_auth_throttle_role.sql` must never be used outside CI.

Set these environment variables securely (through managed secrets, not committed files):
- `TEAMSAAS_AUTH_RATE_BACKEND=postgres`
- `TEAMSAAS_AUTH_RATE_DSN`: verified-TLS PostgreSQL DSN for the least-privilege throttle role
- `TEAMSAAS_AUTH_RATE_SECRET`: at least 32 characters of shared high-entropy secret, identical across replicas

Every app instance must reach the same PostgreSQL database with the same secret. A missing secret, missing library, unknown backend or database outage **fails closed** for authentication (HTTP 503); it never silently downgrades to a process-local counter. `TEAMSAAS_ENVIRONMENT=production` prevents the process-memory throttle, but **does not** make the current app production ready.

Without opt-in, process-local memory remains for the existing single-instance UAT. Another independent PR (#9) offers a SQLite-backed local-UAT throttle; the two changes must be integrated/rebased explicitly before either branch is merged.

## Transaction and privacy boundaries

The limiter HMACs each key with the shared secret, then derives a signed 64-bit PostgreSQL advisory lock key from the digest. Under a transaction lock, it uses PostgreSQL server time to prune expired rows for the same key, count active attempts, and insert the new admitted attempt. The lock prevents concurrent replicas using the same database from exceeding the configured admission ceiling.

The rows are pseudonymous, **not anonymized**. IP addresses and their derived hashes can be personal data; protect the database, limit retention, and verify the user's privacy requirements. Retention cleanup for keys that receive no future attempts **does not exist yet** and must be scheduled safely before production. Digest/key rotation will change throttling identity and needs a controlled rollout.

## Evidence and missing gates

The PR workflow provisions disposable PostgreSQL 16, creates a least-privilege runtime role and tests:
- 24 concurrent calls through separate connections.
- Shared counters across distinct Python processes.
- Expiration based on the PostgreSQL database clock.
- HMAC-stored keys rather than clear IP keys.
- Fail-closed behavior for bad configuration and unreachable database.
- Restricted runtime role cannot write workspace projects or read sessions.

Still outstanding:
- PostgreSQL application data adapter and migration, **shared sessions**, production IAM/tenant source-of-truth.
- Proxy/IP trust model and fair treatment of shared NAT users.
- Connection pooling, bounded retention cleanup, timeouts, DB failover and performance load tests.
- Approved dependency lock/SBOM, TLS cert verification, secret storage/rotation, operational alerts and runbooks.
- Real two-application-instance HTTP security test and tested backup/restore.

Do not deploy this PR as a complete production architecture, do not automatically merge it, and do not enable public access.
