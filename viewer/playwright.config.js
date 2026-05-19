// Playwright config for the Phase 5 snapshot suite.
//
// Runs the Vite dev server on demand and points Chromium at it.
// Why dev server, not a static preview of ``dist/``: re-running
// ``npm run build`` before every change loop is several seconds;
// the dev server hot-reloads in under 200 ms.
//
// CI install footprint: Playwright's Chromium download (~150 MB)
// plus the npm packages. The viewer.yml workflow installs only
// Chromium (``npx playwright install chromium``) to keep the
// runner cache size sane.

import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  // Snapshot tests can race on slow CI; one retry buffers viz.js
  // WASM init jitter without masking real regressions.
  retries: process.env.CI ? 1 : 0,
  fullyParallel: true,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    // Pin viewport so visual snapshots aren't tied to a default
    // that varies across Playwright minor versions.
    viewport: { width: 1280, height: 800 },
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Visual screenshots are flaky across host fonts / GPU paths.
  // We still take them (handy for human review on PR) but the
  // assertion-level checks below are about node/edge counts +
  // selection + overlay-toggle behavior, which are stable.
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.05,
    },
  },
})
