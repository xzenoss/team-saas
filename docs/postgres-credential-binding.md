# Credential-bound PostgreSQL sessions — pre-production identity contract

**Status: experimental internal library, not an HTTP endpoint or a production identity provider.** Stacked above Gather PR #13, #12, #11 and #5. This slice prevents the immediate *caller-supplied user_id* design error that would occur if the untrusted session issuance function in PR #13 were exposed directly to clients.

## Authentication and admission flow

1. An authenticated network ingress passes a trusted client rate-limit key (it must not take `X-Forwarded-For` from arbitrary untrusted clients).
2. A shared PostgreSQL limiter using a **separate DB role** performs atomic attempt admission and fails closed when unavailable.
3. The identity component queries a user by normalized email through a constrained PostgreSQL role, verifies the existing salted PBKDF2-SHA256 hash using a dummy hash for missing users, and **derives user ID only from the database row**. Caller JSON cannot supply an actor ID.
4. The selected workspace is only a hint; transaction-scoped tenant RLS plus current, non-revoked membership must succeed.
5. In the same identity database transaction, the implementation persists only a SHA-256 digest of a fresh, 256-bit random opaque session cookie. A fresh CSRF token is generated separately.
6. Later reads and revocation use the previously tested session-store component and recheck current membership, token digest, and expiration using the PostgreSQL server clock.

The implementation intentionally returns the same `None` for missing users, bad passwords and cross-workspace access, avoiding an identity-enumeration response difference. Credentials are not included in log messages. PostgreSQL faults and unconfigured rate-limit secrets return backend-unavailable, not a permissive fallback.

## Least privilege and residual risks

The disposable CI-only identity role reads `users.id`, `users.email`, `users.password`, and specific membership columns, and can insert session rows. It cannot read other user fields, see project data, change user records, delete sessions or change memberships. **It still has read access to password hashes**, which is a sensitive exposure if the identity-service SQL execution context is compromised. Before production, use a hardened separated authentication service or IdP/OIDC, secrets managed outside the application, reviewed password/hash migration and independent security assessment.

Password hashes use Gather's **legacy PBKDF2-SHA256 (260,000 rounds) compatibility format**; this is not a modern IdP with MFA, password reset, lockout recovery, breached-password screening, SCIM, enterprise SSO, step-up authentication, or consent workflows.

The current JWT/OIDC-based external principal binding for organizations does **not** exist. Workspace membership revocation is rechecked on every token resolution, but password changes do not automatically revoke existing sessions. Password-rotation session invalidation and user-suspension policies are additional P0 work.

The selected workspace may be user supplied, but does not confer membership. `app.workspace_id` is not itself an authorization credential and must only be set inside the authenticated transaction. Do not expose the identity database role as a general SQL interface. The shared limiter uses a signed HMAC key derived from the trusted ingress peer identity, and its finite-window/retention properties remain subject to security review.

## Disposable integration tests

CI uses PostgreSQL 16 plus distinct throttle, credential-read/issue, and session-resolution roles. Tests cover:
- Valid password -> database-derived user ID -> tenant-scoped digest-only cookie;
- Missing user, wrong password, malformed credential hash;
- Cross-tenant email/workspace combinations;
- Membership revocation before and after login;
- Session resolution across independent processes;
- Shared attempt cap and fail-closed backend errors;
- Restricted credential role grants.

## P0 incomplete

The **original HTTP server still uses SQLite**. This new function is not connected to its login, role-mapping or CSRF lifecycle. No user data migration, multi-instance application serving, production secrets/TLS, RPO/RTO or regulatory assessment was performed. Do not deploy publicly or treat CI success as production approval. Review the account provisioning, IdP decision, data migration and proxy trust model first.
