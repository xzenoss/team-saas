# UI quality regression checks

Run `npm ci`, `npx playwright install chromium`, then `npm test` in this directory. Perkora additionally requires building `apps/admin-portal` first. APIs use synthetic fixtures; tests never authenticate with or send traffic to real providers. Checks cover selected WCAG rules, 320px reflow and project-specific recovery/keyboard behavior. These checks are not a conformance certificate or backend security audit. Results are written to `results/report.json`. `UI_CHROMIUM_EXECUTABLE` can select an existing local Chromium executable.
