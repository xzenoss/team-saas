# UI and service quality baseline — 2026-10-03

Target: WCAG 2.2 AA for scoped complete journeys; OWASP ASVS 5.0.0 scoped verification; ISO 9241-210 human-centred design. Organization-level readiness: ISO/IEC 27001:2022 including published amendment, ISO 9001:2026, ISO 22301:2019. ISO/IEC 42001:2023 applies when an actual AI system is in scope. These are proposed targets, not certification or completed conformance.

This change adds visible focus, 44px design targets, a skip link, reduced-motion and forced-color support, improved mobile navigation, readable labels and palette updates. Existing identity stays except clarification of Xzenoss Connect. Do not rename database schemas, auth clients, API paths or deploy a new domain solely for a brand change.

Acceptance before a conformance claim: all applicable WCAG A/AA criteria, keyboard and screen reader journeys, zoom/reflow, errors linked to fields, dialogs, accessible authentication, focus after navigation, loading/offline/empty/error states. Check all screens and roles, not just login. Preserve server authorization. Every critical action needs appropriate review/error prevention.

Release evidence: tenant cross-access tests, MFA and revocation, input/secret controls, dependency checks, transaction idempotency, restore exercise, source/versioned ASVS mapping, data retention/deletion and provider consent. No UI badge implies these exist.

Every control record needs owner, scope, proposed/implemented/tested/independently-assessed state, evidence URL, test date and exception. Organization certification requires a scoped management system and independent assessment; CSS does not certify a company.

Sources checked 2026-10-03: https://www.w3.org/TR/WCAG22/ ; https://owasp.org/projects/asvs ; https://www.iso.org/standard/27001 ; https://www.iso.org/standard/88464.html ; https://www.iso.org/standard/75106.html ; https://www.iso.org/standard/77520.html ; https://www.iso.org/standard/42001 .

## Persistent regression coverage

`tests/ui` now contains pinned Chromium/axe regression checks with synthetic API responses, run by `.github/workflows/ui-quality.yml` on pull requests and pushes. The suite checks selected WCAG rules and 320px page reflow plus project-specific error/recovery and keyboard behavior. Install and run instructions are in `tests/ui/README.md`. CI preserves its JSON evidence as an artifact. These checks do not certify WCAG conformance, ISO certification, authorization, provider delivery, or production infrastructure readiness.
