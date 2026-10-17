# UAT lab

Start the isolated UAT environment from this directory:

```powershell
docker compose -f docker-compose.uat.yml up -d --build
```

Open:

- Gather UAT: http://localhost:8001
- n8n UAT: http://localhost:3984

The existing development n8n instance on port 3983 is left untouched. UAT data persists in Docker volumes named `team_saas_uat_data` and `team_saas_uat_n8n`.

Create a separate n8n API key in the UAT n8n instance before connecting the application. Do not reuse production or development keys.

Stop the lab with:

```powershell
docker compose -f docker-compose.uat.yml down
```



## Security review updates — 16 October 2026

- App (8001) and n8n (3984) now bind **127.0.0.1** only. This is a local-development posture, not public UAT hosting.
- Demo account creation is **disabled by default** (`TEAMSAAS_ENABLE_DEMO=0`). To temporarily enable local demo, set it to `1`, restart only the local app, then disable it again.
- n8n is **opt-in** via `docker compose --profile automation -f docker-compose.uat.yml up -d --build`. The existing container image is a nightly candidate, **not validated for production**, and must be reviewed/pinned to an approved release before any internet exposure.
- Automation webhook requires workspace-owner role, HTTPS by default, and rejects cross-host HTTP redirect chains. Local HTTP is available only with explicit UAT exception to trusted hostnames.
- For deployment beyond the local machine, enforce TLS, Secure cookies, external authentication/MFA, a trusted reverse proxy, hardened deployment image, per-workspace quotas, tested backups, and security assessments. Never change the localhost binding to 0.0.0.0 without network security review.


## Health and correlation boundaries

- `GET /livez` proves only that the HTTP process can answer; it does not touch the database.
- `GET /readyz` checks database availability and is the container readiness target.
- `GET /healthz` remains a compatibility alias for readiness.
- Every JSON/static response carries `X-Request-ID`. A valid incoming bounded request ID is preserved; invalid/missing values are replaced.
- Server access/error events are emitted as bounded JSON records keyed by request ID. They must not contain credentials or request bodies.
