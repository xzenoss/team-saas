-- PostgreSQL-only session lifecycle contract (issue #6).
-- Apply after 001_foundation.sql; disposable CI until reviewed migration/cutover.

BEGIN;
ALTER TABLE members ADD COLUMN revoked_at double precision;
CREATE INDEX idx_gather_sessions_expiry ON sessions(expires);
ALTER TABLE sessions ADD CONSTRAINT session_token_digest_shape
    CHECK (token ~ '^[a-f0-9]{64}$');
ALTER TABLE sessions ADD CONSTRAINT session_expires_positive
    CHECK (expires > 0);
COMMIT;
