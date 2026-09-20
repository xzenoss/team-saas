# Product security baseline

This is the minimum baseline before a customer pilot.

## Accounts

- Passwords use a slow password hash and never appear in logs.
- Sessions are short-lived, revocable, hashed at rest, and protected with secure cookies in production.
- MFA is required for owners and administrators.
- Login, invitation, password-reset, and API endpoints have rate limits.
- Every administrative action is logged.

## Data

- Encrypt traffic with HTTPS.
- Encrypt backups and secrets.
- Never commit API keys, tokens, database files, or customer exports.
- Separate tenant data in every query and test cross-tenant access.
- Define retention and deletion jobs.
- Test restore from backup.

## Application

- Use a production WSGI/ASGI server instead of the built-in development server.
- Add dependency pinning, automated tests, CI, code scanning, and dependency scanning.
- Validate all uploaded content and external URLs.
- Use least-privilege service accounts.
- Review all AI prompts and external webhook actions for data leakage.

## Operations

- Create staging and production environments.
- Keep production credentials out of developer machines.
- Monitor errors, latency, queue depth, failed logins, abuse reports, and unusual reward activity.
- Maintain a contact path for security reports.
- Practice incident response before launch.
