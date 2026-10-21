# Console shell browser checks

From the repository root:

```sh
npm --prefix dashboard ci
(cd dashboard && npx --no-install playwright install chromium)
make dashboard-e2e
```

The target typechecks the suite, builds Vite with the existing captured-data MSW preview API, and starts an isolated preview server on 127.0.0.1:5198. It does not use an existing server or require the Go stack. This tests the built console shell, not authentication or backend integration.

Each internal sidebar destination, the Project page and a captured call detail open directly under the default organisation/project in light and dark themes at desktop and 390 px phone widths. The desktop overview check also rejects sidebar links missing from the test matrix. External documentation is outside this suite.

Every embeddable widget is also opened at each of its sizes, in its own frame size, and scanned the same way (embeds.spec.ts).

Each run verifies the deep URL, page content, applied theme, console errors and uncaught exceptions, then scans the whole rendered page with axe. Serious and critical violations fail the test. No rule, selector or console error is excluded. Reports include lower-severity findings for review, but axe is not a substitute for keyboard or screen-reader testing.

Keyboard checks also exercise Calls, Issues and call-detail tabs in both directions and verify that the selected tab names and controls its visible panel. Finite entry animations finish before axe scans; continuous visual effects are not disabled.

Reports, full axe JSON, screenshots and traces are generated under dashboard/node_modules/.cache/playwright/ (already ignored). Open the report with:

```sh
(cd dashboard && npx --no-install playwright show-report node_modules/.cache/playwright/report)
```

The Dashboard workflow runs the same target and uploads failure artifacts for seven days. Production accessibility fixes must keep this gate green; do not suppress findings in the test harness. Scalar fixes are applied on installation and documented in [the patch notes](../patches/README.md).
