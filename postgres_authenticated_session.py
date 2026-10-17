"""Trusted credential-to-session binding, isolated from the legacy SQLite HTTP app.

This is an internal staging contract, not a public login endpoint.  The
authenticated principal comes ONLY from a verified password + PostgreSQL
users lookup; a caller must never supply a user_id for issuance.
"""
import hashlib
import hmac
import re
import secrets

from postgres_rate_limiter import RateStoreUnavailable, postgres_rate_limited
from postgres_session_store import SessionStoreUnavailable, _ALLOWED_WORKSPACE

_ITERATIONS = 260000  # Matches current SQLite Gather PBKDF2-SHA256 hashes.
_DUMMY_SALT = bytes.fromhex('00' * 16)
_DUMMY_DIGEST = hashlib.pbkdf2_hmac('sha256', b'absent-user-sentinel', _DUMMY_SALT, _ITERATIONS)
_HEX_SALT = re.compile(r'^[0-9a-f]{32}$')
_HEX_DIGEST = re.compile(r'^[0-9a-f]{64}$')
_LOGIN_WINDOW = 300
_LOGIN_LIMIT = 12
_SESSION_SECONDS = 7 * 86400


class LoginThrottled(Exception):
    """The shared throttle rejected the attempt; HTTP should return 429."""


def _matches_current_hash(password, stored):
    """Verify exact legacy salted PBKDF2 format, using a dummy on missing users."""
    salt, expected = _DUMMY_SALT, _DUMMY_DIGEST
    valid_hash = False
    if isinstance(stored, str):
        parts = stored.split(':')
        if (len(parts) == 2 and _HEX_SALT.fullmatch(parts[0])
                and _HEX_DIGEST.fullmatch(parts[1])):
            salt = bytes.fromhex(parts[0])
            expected = bytes.fromhex(parts[1])
            valid_hash = True
    supplied = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, _ITERATIONS)
    return bool(valid_hash and hmac.compare_digest(supplied, expected))


def authenticate_and_issue(*, identity_dsn, throttle_dsn, throttle_secret,
                           trusted_client_key, email, password, workspace_id):
    """Authenticate and atomically create a tenant-scoped session.

    Return (cookie_value, csrf_token) or None (invalid credentials, missing or
    revoked membership).  A trusted HTTP ingress must supply the network key
    from an authenticated peer/proxy, NEVER untrusted X-Forwarded-For.
    """
    if (not isinstance(email, str) or not isinstance(password, str)
            or not isinstance(workspace_id, str)
            or not isinstance(trusted_client_key, str)
            or not (1 <= len(trusted_client_key) <= 160)
            or not (1 <= len(email) <= 254)
            or not (1 <= len(password) <= 128)
            or not _ALLOWED_WORKSPACE.fullmatch(workspace_id)):
        return None
    if not identity_dsn:
        raise SessionStoreUnavailable('Identity storage is not configured.')
    email = email.strip().lower()
    if len(email) > 254 or not email or '@' not in email:
        return None

    try:
        denied = postgres_rate_limited(
            'auth:' + trusted_client_key,
            dsn=throttle_dsn,
            secret=throttle_secret,
            window=_LOGIN_WINDOW,
            limit=_LOGIN_LIMIT
        )
    except RateStoreUnavailable as exc:
        raise SessionStoreUnavailable('Shared sign-in protection unavailable.') from exc
    if denied:
        raise LoginThrottled()

    try:
        import psycopg
    except ImportError as exc:
        raise SessionStoreUnavailable('Identity database driver unavailable.') from exc

    try:
        with psycopg.connect(identity_dsn, connect_timeout=5) as db:
            with db.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5000ms'")
                # This role has read-only access to id/email/password, not
                # name or non-auth workspace/business data.
                cursor.execute(
                    'SELECT id,password FROM users WHERE email=%s', (email,)
                )
                found = cursor.fetchone()
                if not _matches_current_hash(password, found[1] if found else None):
                    return None
                user_id = found[0]
                # The workspace is a selector only, never a proof of entitlement.
                cursor.execute(
                    "SELECT set_config('app.workspace_id', %s, true)", (workspace_id,)
                )
                cursor.execute(
                    "SELECT 1 FROM members WHERE workspace_id=%s AND user_id=%s "
                    "AND revoked_at IS NULL",
                    (workspace_id, user_id)
                )
                if cursor.fetchone() is None:
                    return None
                cookie = workspace_id + '.' + secrets.token_hex(32)
                csrf = secrets.token_urlsafe(24)
                cursor.execute(
                    "INSERT INTO sessions(token,user_id,workspace_id,csrf,expires) "
                    "VALUES (%s,%s,%s,%s,EXTRACT(EPOCH FROM clock_timestamp())+%s)",
                    (hashlib.sha256(cookie.encode('ascii')).hexdigest(),
                     user_id, workspace_id, csrf, _SESSION_SECONDS)
                )
                return cookie, csrf
    except psycopg.Error as exc:
        raise SessionStoreUnavailable('Identity session issuance unavailable.') from exc
