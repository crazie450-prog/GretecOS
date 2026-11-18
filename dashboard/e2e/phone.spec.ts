// WO-9.3 end to end: the phone dashboard over the tailnet, at 390x844. e2e/fake_serve.py stands in
// for Tailscale Serve (TLS on the tailnet name, every byte piped to the core with the browser's
// Host kept), and the harness core allows that name ([core] allowed_hosts, --tailnet), as on the
// PC. A Discord plan link opened before pairing lands on the plan once the phone has paired; every
// view past the five tabs opens through More; a Lab action (a JourneyMap waypoint in the fake Lab
// instance) proposed on the phone is confirmed there, as "phone"; status changes reach the phone
// live over its WebSocket; and a request from outside the tailnet (another name, a Funnel request)
// is refused. The More tab has no approved board (D92): needs Clayton's board approval.
import { type ChildProcess, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { type Browser, type BrowserContext, type Page, chromium, expect, test } from "@playwright/test";
import { PORT, SERVE_PORT, TAILNET, asAdmin, clipped, contrastMisses, control, pendingCode } from "./harness";

const REPO = fileURLToPath(new URL("../..", import.meta.url));
const PHONE = { width: 390, height: 844 };
const ORIGIN = `https://${TAILNET}:${SERVE_PORT}`;
const OTHER = "other-pc.tail-e2e.ts.net"; // another node of the tailnet, not configured
const MORE = ["designer", "power", "quests", "crops", "costs", "activity", "ores", "build"];
const shot = (name: string) => test.info().outputPath(`${name}.png`);

let serve: ChildProcess;
let browser: Browser;
let context: BrowserContext;
let page: Page;
let plan = "";

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  serve = spawn(
    "uv",
    ["run", "--quiet", "python", "dashboard/e2e/fake_serve.py", "--name", TAILNET, "--listen", String(SERVE_PORT), "--to", String(PORT)],
    { cwd: REPO, stdio: ["ignore", "pipe", "inherit"] },
  );
  await new Promise<void>((resolve, reject) => {
    serve.stdout?.on("data", (d: Buffer) => {
      if (d.toString().includes("listening")) resolve();
    });
    serve.on("exit", (status) => reject(new Error(`fake_serve.py exited (${status})`)));
  });
  // The tailnet name resolves to this machine, as MagicDNS resolves it on the phone.
  // Never through a proxy (Chromium on Linux reads the *_proxy variables): the phone talks to the
  // PC over the tailnet only.
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/proxy/i.test(k)));
  browser = await chromium.launch({ args: [`--host-resolver-rules=MAP ${TAILNET} 127.0.0.1,MAP ${OTHER} 127.0.0.1`, "--no-proxy-server"], env });
  context = await browser.newContext({ viewport: PHONE, ignoreHTTPSErrors: true, hasTouch: true });
  page = await context.newPage();
  // A saved plan for the Discord link: the Designer spec's, or one designed here.
  const plans = await asAdmin<{ plans: { id: string; kind: string }[] }>("/api/plans");
  plan = plans.plans.find((p) => p.id.startsWith("pl-"))?.id ?? "";
  if (!plan) {
    const made = await asAdmin<{ ids: string[] }>("/api/designer/design", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: "steel ingot", tier: "HV" }),
    });
    plan = made.ids[0];
  }
  expect(plan).toMatch(/^pl-/);
});

test.afterAll(async () => {
  control("running", "[]");
  await browser?.close();
  serve?.kill();
});

async function phones(): Promise<number> {
  const devices = await asAdmin<{ name: string; revoked: boolean }[]>("/api/devices");
  return devices.filter((d) => d.name === "Phone" && !d.revoked).length;
}

test("a Discord plan link opens the plan once the phone has paired over the tailnet", async () => {
  // The link the bot sends: https://<pc>.<tailnet>.ts.net/#/designer/<id> (WO-9.1).
  await page.goto(`${ORIGIN}/#/designer/${plan}`);
  await expect(page.getByTestId("pairing")).toBeVisible();
  const before = await phones();
  await page.getByTestId("pair-name").fill("Phone");
  await page.getByTestId("pair-start").click();
  await page.getByTestId("pair-code").fill(await pendingCode());
  await page.getByTestId("pair-complete").click();
  await expect(page.getByTestId("shell")).toBeVisible();
  // The cookie is Secure, HttpOnly and host-only to the tailnet name.
  const cookie = (await context.cookies(ORIGIN)).find((c) => c.name === "gos_device");
  expect(cookie).toMatchObject({ domain: TAILNET, secure: true, httpOnly: true, sameSite: "Strict" });
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "designer");
  expect(page.url()).toContain(`#/designer/${plan}`);
  await expect(page.locator(`.designer-route[data-route="${plan}"]`)).toBeVisible();
  await expect(page.getByTestId("nav-more")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("shell")).toHaveAttribute("data-socket", "open");
  expect(await phones()).toBe(before + 1); // one device row for this phone
  await page.screenshot({ path: shot("phone-plan-link"), fullPage: true });
  // Paired once: a reload (or the next link) goes straight in.
  await page.reload();
  await expect(page.getByTestId("shell")).toBeVisible();
  await expect(page.getByTestId("pairing")).toHaveCount(0);
});

test("five tabs and More; every other view opens through More", async () => {
  const nav = page.getByTestId("nav");
  expect(await nav.locator("button").evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")))).toEqual([
    "nav-now",
    "nav-chat",
    "nav-search",
    "nav-settings",
    "nav-base",
    "nav-more",
  ]);
  await page.getByTestId("nav-more").click();
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "more");
  expect(await page.getByTestId("more-list").locator("button").count()).toBe(MORE.length);
  expect(await clipped(page)).toEqual([]);
  expect(await contrastMisses(page)).toEqual([]);
  await page.screenshot({ path: shot("phone-more"), fullPage: true });
  for (const view of MORE) {
    await page.getByTestId("nav-more").click();
    await page.getByTestId(`more-${view}`).click();
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", view);
    await expect(page.getByTestId("nav-more")).toHaveAttribute("aria-current", "page");
    expect(await page.evaluate(() => document.documentElement.scrollWidth), view).toBeLessThanOrEqual(PHONE.width);
    expect(await contrastMisses(page), view).toEqual([]);
  }
});

test("a Lab action proposed on the phone is confirmed on the phone", async () => {
  await page.getByTestId("nav-more").click();
  await page.getByTestId("more-ores").click();
  await page.getByTestId("ores-input").fill("Ferrite");
  await page.getByTestId("ores-go").click();
  await expect(page.getByTestId("ores-list").locator("[data-cite]")).toHaveCount(4);
  await page.getByTestId("ores-mark-2").click();
  await expect(page.getByTestId("ores-said")).toContainText("in Lab. Say confirm.");
  // No rail on the phone: the approval is first on Now (D159).
  await page.getByTestId("nav-now").click();
  const card = page.getByTestId("view-now").locator('[data-kind="action"]');
  await expect(card).toBeVisible();
  await page.screenshot({ path: shot("phone-confirm"), fullPage: true });
  await card.getByTestId("action-confirm").click();
  await expect(card).toHaveCount(0);
  await expect
    .poll(async () => (await asAdmin<{ recent: { kind: string; state: string; confirmed_via: string }[] }>("/api/actions")).recent[0])
    .toMatchObject({ kind: "waypoint", state: "done", confirmed_via: "phone" });
});

test("status updates live on the phone", async () => {
  await page.getByTestId("nav-now").click();
  const chip = page.getByTestId("status-chips").locator('[data-state="game_not_running"]');
  await expect(chip).toBeVisible();
  control("running", '["lab"]');
  await expect(chip).toHaveCount(0, { timeout: 15_000 });
  control("running", "[]");
  await expect(chip).toBeVisible({ timeout: 15_000 });
});

test("outside the tailnet the dashboard is refused", async () => {
  // Another node's name in front of the same port: the core refuses its Host.
  const other = await context.newPage();
  const r = await other.goto(`https://${OTHER}:${SERVE_PORT}/`);
  expect(r?.status()).toBe(403);
  expect(await other.locator("body").innerText()).toContain("host not allowed");
  await other.close();
  // A request Tailscale marks as from Funnel (the public internet), with the phone's own cookie.
  const funnel = await page.evaluate(async () => {
    const res = await fetch("/api/state", { headers: { "Tailscale-Funnel-Request": "?1" } });
    return { status: res.status, body: (await res.json()) as unknown };
  });
  expect(funnel).toEqual({ status: 403, body: { error: "funnel request" } });
  expect(await page.evaluate(async () => (await fetch("/api/state")).status)).toBe(200);
});
