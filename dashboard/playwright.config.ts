// End-to-end tests (WO-2.6): the built dashboard (pnpm build first) against a real core with a
// fake fast lane (e2e/serve_core.py), in Chromium at 2560x1440 (monitor 2, D50) and 390x844 (the
// phone, D53). Screenshots go to test-results/, which git ignores. The setup wizard's tests
// (WO-2.9, e2e/setup.spec.ts) run against a second core with an empty GOS_HOME (--fresh); the Power
// view's (--power) and the shell workbench's (--shells, WO-10.10) have their own cores too.
import { defineConfig } from "@playwright/test";
import { FRESH_HOST, FRESH_PORT, FRESH_ROOT } from "./e2e/fresh";
import { BASE, HOME, PORT, SERVE_PORT, TAILNET } from "./e2e/harness";
import { POWER_HOME, POWER_HOST, POWER_PORT } from "./e2e/power";
import { SHELLS_HOST, SHELLS_PORT, SHELLS_ROOT } from "./e2e/shells_core";

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
      command: `uv run python dashboard/e2e/serve_core.py --home "${HOME}" --port ${PORT} --tailnet ${TAILNET}:${SERVE_PORT}`,
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
    {
      // The shell workbench's core (WO-10.10): WO-10.7's test home and one shell.
      command: `uv run python dashboard/e2e/serve_core.py --home "${SHELLS_ROOT}" --port ${SHELLS_PORT} --shells --host-name ${SHELLS_HOST}`,
      cwd: "..",
      url: `http://127.0.0.1:${SHELLS_PORT}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "pipe",
    },
  ],
});
