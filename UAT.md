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

