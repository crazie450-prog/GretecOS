// WO-7.4 end to end: the Base view's live half against the real core. An OC computer's token is
// issued in the harness's DB (as `gos token new oc-lab` does), the contract's golden telemetry
// (tests/fixtures/oc/telemetry-display.json: an EBF that has a job and isn't running) is posted
// to POST /oc/telemetry as the agent does, and the view shows the stall first, the power, the
// machines and AE2 with their tel: citations; the next message (the EBF running) reaches the view
// within 10 s, and the reply to each message carries the in-game display. Every theme reads at
// WCAG contrast at 2560x1440 and 390x844, and nothing is cut off on the phone. Screenshots go to
// test-results/. Runs after the other harness-core specs that read the shell's state chips (the
// OC chip goes "disconnected" 30 s after the last message here).
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { type Browser, type Page, expect, test } from "@playwright/test";
import { HOME, PORT, clipped, pair, themeMisses } from "./harness";

const REPO = fileURLToPath(new URL("../..", import.meta.url));
const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };
const TELEMETRY = JSON.parse(readFileSync(join(REPO, "tests", "fixtures", "oc", "telemetry-display.json"), "utf-8")) as {
  seq: number;
  machines: { name: string; addr: string; active: boolean }[];
  display: { rev: number };
};
const EBF = TELEMETRY.machines.find((m) => m.name === "multimachine.blastfurnace")?.addr ?? "";

let page: Page;
let token = "";

function issueToken(): string {
  const code = [
    "import sys",
    "from pathlib import Path",
    "from gregtec.api.security import Auth",
    "from gregtec.store import DB_NAME, Store",
    "store = Store(Path(sys.argv[1]) / 'data' / DB_NAME)",
    "print(Auth(store, 'e2e-admin-' + 'x' * 32).issue_client_token('oc-lab'))",
    "store.close()",
  ].join("\n");
  const run = spawnSync("uv", ["run", "--quiet", "python", "-c", code, HOME], { cwd: REPO, encoding: "utf-8" });
  if (run.status !== 0) throw new Error(`token not issued: ${run.stderr}`);
  return run.stdout.trim();
}

async function send(seq: number, ebfActive: boolean): Promise<{ ok: boolean; display?: { lines?: { t: string }[] } }> {
  const doc = structuredClone(TELEMETRY);
  doc.seq = seq;
  for (const m of doc.machines) if (m.addr === EBF) m.active = ebfActive;
  const r = await fetch(`http://127.0.0.1:${PORT}/oc/telemetry`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, Host: `127.0.0.1:${PORT}` },
    body: JSON.stringify(doc),
  });
  expect(r.status).toBe(200);
  const text = await r.text();
  expect(text.length).toBeLessThanOrEqual(2048);
  return JSON.parse(text) as { ok: boolean; display?: { lines?: { t: string }[] } };
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  token = issueToken();
  page = await browser.newPage({ viewport: MONITOR_2 });
  await pair(page, "Monitor 2 (OC displays)");
});

test.afterAll(async () => {
  await page.close();
});

async function openBase(): Promise<void> {
  await page.evaluate(() => {
    window.location.hash = "/base";
  });
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "base");
  await expect(page.getByTestId("base-live")).toBeVisible();
}

test("a stalled machine is first, with power, machines and AE2, each cited", async () => {
  const reply = await send(1, false);
  expect(reply.ok).toBe(true);
  // The in-game board's lines ride in the same reply (both transports share Bridge.reply).
  expect((reply.display?.lines ?? []).map((l) => l.t).join("\n")).toContain("stalled");
  await page.getByTestId("nav-base").click();
  await openBase();
  const lab = page.getByTestId("live-lab");
  await expect(lab).toBeVisible();
  await expect(lab.getByTestId("live-problems").locator("li").first()).toHaveAttribute("data-kind", "stalled");
  await expect(lab.getByTestId("live-power")).toContainText("12.3%");
  await expect(lab.locator(`[data-machine-live="lab/${EBF}"]`)).toHaveAttribute("data-status", "stalled");
  await expect(lab.locator(`[data-cite="tel:lab/${EBF}"]`).first()).toBeVisible();
  await expect(lab.getByTestId("live-ae2")).toContainText("Crafting CPUs");
  await page.screenshot({ path: shot("base-live-stalled") });
});

test("the next message reaches the view within 10 s", async () => {
  await send(2, true);
  const started = Date.now();
  await expect(page.getByTestId("live-lab").getByTestId("live-no-problems")).toBeVisible({ timeout: 10_000 });
  expect(Date.now() - started).toBeLessThanOrEqual(10_000);
  await expect(page.locator(`[data-machine-live="lab/${EBF}"]`)).toHaveAttribute("data-status", "running");
  await page.screenshot({ path: shot("base-live-running") });
});

test("every theme reads at WCAG contrast, and nothing is cut off on the phone", async () => {
  await send(3, false);
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await openBase();
    await expect(page.getByTestId("live-problems")).toBeVisible({ timeout: 10_000 });
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/base-live/${slug}`] = found;
  }
  expect(misses).toEqual({});
  expect(await clipped(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  await page.screenshot({ path: shot("phone-base-live"), fullPage: true });
  await page.setViewportSize(MONITOR_2);
});
