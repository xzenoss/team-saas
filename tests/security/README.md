# Gather API security regression coverage

Run from the repository root:
`python -m unittest discover -s tests/security -p 'test_*.py' -v`

The nine unittest methods send HTTP requests to the real Handler with a fresh temporary SQLite database per test. The server binds only to loopback on an ephemeral port. Synthetic accounts use example.test; no external provider, webhook or production database is accessed. No additional packages are needed.

Coverage:
- Workspace state isolates projects, tasks, members and activity in both directions.
- Foreign task updates/deletions and foreign project/assignee references are denied.
- Rejected mutations preserve both workspaces' state; positive controls prove allowed changes succeed.
- Missing/wrong CSRF and a foreign Origin are rejected.
- Anonymous/forged, expired, logged-out and membership-revoked sessions are rejected.
- Member settings/invitation creation are denied; valid invitation joining succeeds, then replay and invented tokens are denied.
- Malformed/non-object/oversized JSON are rejected without changing state.
- Registration responses carry HttpOnly, SameSite=Lax, Secure and Path cookie attributes. Manual cookie replay over test HTTP does not verify browser HTTPS enforcement.

Local validation on 2026-10-03: nine tests passed. Temporary mutation checks removed the task-update workspace predicate, CSRF validation and owner settings check individually; the corresponding tests failed by assertion in all three cases. These mutated servers were discarded.

CI adds this suite to the existing Test workflow. UI quality remains a separate three-engine workflow. This is scoped regression evidence, not complete security verification, an ISO certificate or a production readiness decision. Not covered: simultaneous invite replay, all role combinations, n8n authorization/provider behavior, rate-limit exhaustion, infrastructure/TLS, backups/restores and all applicable ASVS controls.
