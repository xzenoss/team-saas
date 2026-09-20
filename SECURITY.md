# Security policy

Do not post API keys, n8n tokens, passwords, or customer data in GitHub issues, pull requests, or chat. Rotate any secret that has been shared.

Before production deployment:

- Set TEAMSAAS_SECURE_COOKIE=1.
- Serve the application behind HTTPS.
- Enable administrator MFA.
- Configure backups and restore testing.
- Put rate limiting at the reverse proxy.
- Keep production credentials outside developer machines.

Report suspected vulnerabilities privately to the repository owner through GitHub. Include the affected component, reproduction steps, impact, and a safe contact method.
