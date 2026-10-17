"""Isolated PostgreSQL session lifecycle for Gather's future data cutover.

This module is NOT wired into server.py. Issuance must only be called after a
trusted authentication and tenant-membership flow (still outstanding).
The embedded workspace ID is an untrusted routing hint; only possession of
the 256-bit secret and database membership checks authorize access.
"""
import hashlib
import re
import secrets

_ALLOWED_WORKSPACE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_ALLOWED_SECRET = re.compile(r'^[a-f0-9]{64}$')
_MAX_AGE_SECONDS = 7 * 24 * 3600


class SessionStoreUnavailable(RuntimeError):
    """The caller must fail closed if durable session storage is not healthy."""


def _driver():
    try:
        import psycopg
    except ImportError as exc:
        raise SessionStoreUnavailable('PostgreSQL session driver unavailable.') from exc
    return psycopg


def _split_token(token):
    if not isinstance(token, str) or len(token) > 129:
        return None
    parts = token.split('.')
    if len(parts) != 2 or not _ALLOWED_WORKSPACE.fullmatch(parts[0]):
        return None
    if not _ALLOWED_SECRET.fullmatch(parts[1]):
        return None
    return parts[0], hashlib.sha256(token.encode('ascii')).hexdigest()


def _set_workspace(cursor, workspace_id):
    # Must remain transaction-local for safe connection pooling later.
    cursor.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))


def issue_session(*, dsn, user_id, workspace_id, ttl_seconds=_MAX_AGE_SECONDS):
    """Trusted internal API: returns (opaque_cookie_value, csrf) or None.

    Never accept user_id from JSON or browser-provided claims. This API is only
    safe when an upstream authenticated identity mapper already established it.
    """
    if (not isinstance(user_id, str) or not user_id or len(user_id) > 128
            or not isinstance(workspace_id, str)
            or not _ALLOWED_WORKSPACE.fullmatch(workspace_id)
            or not isinstance(ttl_seconds, int)
            or ttl_seconds < 1 or ttl_seconds > _MAX_AGE_SECONDS):
        raise ValueError('Invalid trusted session issuance parameters.')
    if not dsn:
        raise SessionStoreUnavailable('PostgreSQL session storage is not configured.')
    psycopg = _driver()
    token = workspace_id + '.' + secrets.token_hex(32)
    digest = hashlib.sha256(token.encode('ascii')).hexdigest()
    csrf = secrets.token_urlsafe(24)
    try:
        with psycopg.connect(dsn, connect_timeout=5) as db:
            with db.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5000ms'")
                _set_workspace(cursor, workspace_id)
                cursor.execute(
                    "SELECT 1 FROM members WHERE workspace_id=%s AND user_id=%s "
                    "AND revoked_at IS NULL",
                    (workspace_id, user_id)
                )
                if cursor.fetchone() is None:
                    return None
                cursor.execute(
                    "INSERT INTO sessions(token,user_id,workspace_id,csrf,expires) "
                    "VALUES (%s,%s,%s,%s,EXTRACT(EPOCH FROM clock_timestamp())+%s)",
                    (digest, user_id, workspace_id, csrf, ttl_seconds)
                )
        return token, csrf
    except psycopg.Error as exc:
        raise SessionStoreUnavailable('PostgreSQL session issuance failed.') from exc


def resolve_session(*, dsn, token):
    """Validate a presented cookie against current membership and DB time.

    Session metadata is returned ONLY when the secret, tenant and member agree.
    """
    parsed = _split_token(token)
    if parsed is None:
        return None
    if not dsn:
        raise SessionStoreUnavailable('PostgreSQL session storage is not configured.')
    psycopg = _driver()
    workspace_id, token_digest = parsed
    try:
        with psycopg.connect(dsn, connect_timeout=5) as db:
            with db.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5000ms'")
                _set_workspace(cursor, workspace_id)
                cursor.execute(
                    "SELECT s.user_id,s.workspace_id,s.csrf,s.expires,m.role "
                    "FROM sessions s JOIN members m ON "
                    "m.workspace_id=s.workspace_id AND m.user_id=s.user_id "
                    "WHERE s.token=%s AND s.workspace_id=%s "
                    "AND s.expires > EXTRACT(EPOCH FROM clock_timestamp()) "
                    "AND m.revoked_at IS NULL",
                    (token_digest, workspace_id)
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return dict(zip(
                    ('user_id', 'workspace_id', 'csrf', 'expires', 'role'), row
                ))
    except psycopg.Error as exc:
        raise SessionStoreUnavailable('PostgreSQL session validation failed.') from exc


def revoke_session(*, dsn, token):
    """Revoke only the session whose full opaque secret was supplied."""
    parsed = _split_token(token)
    if parsed is None:
        return False
    if not dsn:
        raise SessionStoreUnavailable('PostgreSQL session storage is not configured.')
    psycopg = _driver()
    workspace_id, token_digest = parsed
    try:
        with psycopg.connect(dsn, connect_timeout=5) as db:
            with db.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5000ms'")
                _set_workspace(cursor, workspace_id)
                cursor.execute(
                    'DELETE FROM sessions WHERE token=%s AND workspace_id=%s',
                    (token_digest, workspace_id)
                )
                return cursor.rowcount == 1
    except psycopg.Error as exc:
        raise SessionStoreUnavailable('PostgreSQL session revocation failed.') from exc
