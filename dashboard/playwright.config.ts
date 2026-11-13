// End-to-end tests (WO-2.6): the built dashboard (pnpm build first) against a real core with a
// fake fast lane (e2e/serve_core.py), in Chromium at 2560x1440 (monitor 2, D50) and 390x844 (the
// phone, D53). Screenshots go to test-results/, which git ignores. The setup wizard's tests
// (WO-2.9, e2e/setup.spec.ts) run against a second core with an empty GOS_HOME (--fresh).
import { defineConfig } from "@playwright/test";
import { FRESH_HOST, FRESH_PORT, FRESH_ROOT } from "./e2e/fresh";
import { BASE, HOME, PORT } from "./e2e/harness";
import { POWER_HOME, POWER_HOST, POWER_PORT } from "./e2e/power";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: BASE,
    viewport: { width: 2560, height: 1440 },
    // Chromium resolves *.localhost to loopback itself; this keeps any proxy out of the way.
    launchOptions: { args: ["--host-resolver-rules=MAP *.localhost 127.0.0.1"] },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `uv run python dashboard/e2e/serve_core.py --home "${HOME}" --port ${PORT}`,
      cwd: "..",
      url: `http://127.0.0.1:${PORT}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
    },
    {
      command: `uv run python dashboard/e2e/serve_core.py --home "${FRESH_ROOT}" --port ${FRESH_PORT} --fresh --host-name ${FRESH_HOST}`,
      cwd: "..",
      url: `http://127.0.0.1:${FRESH_PORT}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
    },
    {
      command: `uv run python dashboard/e2e/serve_core.py --home "${POWER_HOME}" --port ${POWER_PORT} --power --host-name ${POWER_HOST}`,
      cwd: "..",
      url: `http://127.0.0.1:${POWER_PORT}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
    },
  ],
});
