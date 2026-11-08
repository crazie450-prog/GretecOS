// End-to-end tests (WO-2.6): the built dashboard (pnpm build first) against a real core with a
// fake fast lane (e2e/serve_core.py), in Chromium at 2560x1440 (monitor 2, D50) and 390x844 (the
// phone, D53). Screenshots go to test-results/, which git ignores.
import { defineConfig } from "@playwright/test";
import { BASE, HOME, PORT } from "./e2e/harness";

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
  webServer: {
    command: `uv run python dashboard/e2e/serve_core.py --home "${HOME}" --port ${PORT}`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/health`,
    timeout: 120_000,
    reuseExistingServer: false,
    stdout: "pipe",
  },
});
