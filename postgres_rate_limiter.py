"""Shared PostgreSQL login throttle, opt-in for pre-production validation.

This module owns only authentication attempt admission. It does not change
Gather's SQLite application database, sessions or authorization model.
"""
import hashlib
import hmac


class RateStoreUnavailable(RuntimeError):
    """Do not admit auth attempts when the configured shared store is broken."""


def postgres_rate_limited(key, *, dsn, secret, window, limit):
    """Atomically count and admit an attempt across independent app processes.

    All callers must use the same PostgreSQL database and secret. An advisory
    transaction lock serializes the read/insert for the derived key.
    """
    if not isinstance(secret, str) or len(secret) < 32 or not dsn:
        raise RateStoreUnavailable('Shared authentication protection is not configured.')
    if not isinstance(key, str) or not isinstance(limit, int) or limit < 1 or window <= 0:
        raise ValueError('Invalid authentication throttle configuration.')

    try:
        import psycopg
    except ImportError as exc:
        raise RateStoreUnavailable('PostgreSQL authentication driver is unavailable.') from exc

    key_hash = hmac.new(secret.encode('utf-8'), key.encode('utf-8'), hashlib.sha256).hexdigest()
    lock_key = int.from_bytes(bytes.fromhex(key_hash[:16]), 'big', signed=True)

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5000ms'")
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key,))
                cursor.execute('SELECT EXTRACT(EPOCH FROM clock_timestamp())')
                now = float(cursor.fetchone()[0])
                cutoff = now - window
                # Keep cleanup key-local; production retention must prune
                # long-idle keys using a separately scheduled bounded job.
                cursor.execute(
                    'DELETE FROM auth_rate_hits WHERE key_hash=%s AND attempted_at <= %s',
                    (key_hash, cutoff)
                )
                cursor.execute(
                    'SELECT COUNT(*) FROM auth_rate_hits WHERE key_hash=%s AND attempted_at > %s',
                    (key_hash, cutoff)
                )
                if cursor.fetchone()[0] >= limit:
                    return True
                cursor.execute(
                    'INSERT INTO auth_rate_hits(key_hash, attempted_at) VALUES (%s, %s)',
                    (key_hash, now)
                )
                return False
    except psycopg.Error as exc:
        raise RateStoreUnavailable('Shared authentication protection is temporarily unavailable.') from exc
