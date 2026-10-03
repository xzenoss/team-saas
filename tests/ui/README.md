# UI quality regression checks

Run `npm ci`, `npx playwright install chromium`, then `npm test` in this directory. Perkora additionally requires building `apps/admin-portal` first. APIs use synthetic fixtures; tests never authenticate with or send traffic to real providers. Checks cover selected WCAG rules, 320px reflow and project-specific recovery/keyboard behavior. These checks are not a conformance certificate or backend security audit. Results are written to `results/report.json`. `UI_CHROMIUM_EXECUTABLE` can select an existing local Chromium executable.

The suite also doubles the computed text sizes in selected rendered screens, tests a 320 CSS-pixel viewport, applies user text-spacing overrides, and checks for clipped button labels or unscrollable navigation. A narrow viewport models the reflow width; it is not a real browser 400% zoom test. Modal Tab/Shift+Tab/Escape and skip-link focus are checked. Arabic/English order and customer fixtures test native semantics and bidi isolation, not a complete localization or screen-reader audit.

## Browser matrix

CI runs the same suite independently on Chromium, Firefox and WebKit (`fail-fast: false`). Each engine preserves its own report artifact and the JSON includes the engine/version. Locally install with `npx playwright install --with-deps chromium firefox webkit`; run `UI_BROWSER=firefox npm test` or `UI_BROWSER=webkit npm test`. Without UI_BROWSER, Chromium remains the default. UI_CHROMIUM_EXECUTABLE applies only to Chromium. WebKit on Linux is engine coverage, not a claim of testing Safari on Apple devices.
