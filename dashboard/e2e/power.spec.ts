// WO-4.6 end to end: the Power view against a real core on WO-4.2's made-up power export
// (serve_core.py --power: a test gas burnt in a test combustion generator, closed on water). The
// ledger shows first; each of the four modes designs 2-3 options, drawn side by side in the core's
// rank order with the weights printed and every number cited; a self-sustaining option is proven
// closed with its kit; accepting asks first, then the option's net EU/t is a source in the ledger
// (a tier opens its draws) and its line note lands in the vault; every theme reads at WCAG
// contrast on monitor 2 and the phone, and nothing is cut off on the phone.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { type Browser, type Page, expect, test } from "@playwright/test";
import type { PowerView } from "../src/api/types";
import { clipped, themeMisses } from "./harness";
import { POWER_BASE, POWER_HOME, powerAdmin, powerCode } from "./power";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };
const BALANCED = "weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1";

let page: Page;
let accepted = "";

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await browser.newPage({ baseURL: POWER_BASE, viewport: MONITOR_2 });
  await page.goto("/");
  await page.getByTestId("pair-name").fill("Monitor 2 (Power)");
  await page.getByTestId("pair-start").click();
  await page.getByTestId("pair-code").fill(await powerCode());
  await page.getByTestId("pair-complete").click();
  await page.getByTestId("shell").waitFor();
});

test.afterAll(async () => {
  await page.close();
});

function planInHash(): string {
  const m = /#\/power\/(pw-[a-z0-9]+)$/.exec(page.url());
  if (!m) throw new Error(`no power plan in ${page.url()}`);
  return m[1];
}

async function optionIds(): Promise<string[]> {
  return page.locator(".power-option[data-option]").evaluateAll((els) => els.map((e) => e.getAttribute("data-option") ?? ""));
}

test("key 5 opens Power: the ledger first, empty until a plan is accepted", async () => {
  await page.keyboard.press("5");
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "power");
  await expect(page.getByTestId("view-power")).toBeVisible();
  await expect(page.getByTestId("power-ledger-empty")).toBeVisible();
  await expect(page.getByTestId("power-empty")).toBeVisible();
  await expect(page.getByTestId("power-distribution")).toContainText("no storage data in the current export run");
});

for (const mode of ["best", "max_output", "output_per_effort", "self_sustaining"] as const) {
  test(`mode ${mode}: 2-3 options in the core's rank order, the weights printed`, async () => {
    await page.getByTestId(`power-mode-${mode}`).click();
    await page.getByTestId("power-tier").selectOption("MV");
    await page.getByTestId("power-go").click();
    await expect(page.getByTestId("power-busy")).toHaveCount(0, { timeout: 60_000 });
    await expect(page.getByTestId("option-2")).toBeVisible();
    const pid = planInHash();
    const ids = await optionIds();
    expect(ids.length).toBeGreaterThanOrEqual(2);
    expect(ids.length).toBeLessThanOrEqual(3);
    expect(ids[0]).toBe(pid);
    // The order on screen is the core's (rank.py's), never re-sorted.
    const core = await powerAdmin<PowerView>(`/api/power/${pid}`);
    expect(ids).toEqual(core.options.map((o) => o.id));
    expect(core.options.map((o) => o.position)).toEqual(ids.map((_, i) => i + 1));
    expect(core.mode).toBe(mode);
    await expect(page.getByTestId("power-weights")).toContainText(`mode ${mode}`);
    await expect(page.getByTestId("power-weights")).toContainText(BALANCED);
    // Every option: net = gross - its chain, cited by its plan, generator and fuel.
    for (const o of core.options) {
      const card = page.locator(`.power-option[data-option="${o.id}"]`);
      await expect(card.getByTestId(`net-${o.id}`)).toContainText("EU/t net");
      await expect(card.locator(`[data-cite="plan:${o.id}"]`).first()).toBeVisible();
      for (const c of o.generator.cites) await expect(card.locator(`[data-cite="${c}"]`).first()).toBeVisible();
      if (o.fuel.ref) await expect(card.locator(`[data-cite="${o.fuel.ref}"]`).first()).toBeVisible();
      if (mode === "self_sustaining") {
        expect(o.closed.ok).toBe(true);
        await expect(card.getByTestId(`closed-${o.id}`)).toContainText("Closed");
        await expect(card.getByTestId(`kit-${o.id}`)).toBeVisible();
      }
      if (o.effort) await expect(card.getByTestId(`effort-${o.id}`)).toBeVisible();
    }
    await page.screenshot({ path: shot(`power-${mode}`) });
  });
}

test("the effort breakdown opens as the catalog's table card, its ids as chips", async () => {
  const pid = planInHash();
  const effort = page.getByTestId(`effort-${pid}`);
  await effort.locator("summary").click();
  await expect(effort.locator('[data-card="table"]')).toBeVisible();
  await expect(effort.locator('[data-cite^="plan:"]').first()).toBeVisible();
});

test("accepting asks first, then the line is a source in the ledger and its note is written", async () => {
  const pid = planInHash();
  accepted = pid;
  await page.getByTestId(`accept-${pid}`).click();
  await expect(page.getByTestId(`accept-confirm-${pid}`)).toContainText("EU ledger as a source");
  await page.screenshot({ path: shot("power-accept-confirm") });
  await page.getByTestId(`accept-yes-${pid}`).click();
  const card = page.locator(`.power-option[data-option="${pid}"]`);
  await expect(card).toContainText("accepted");
  await expect(page.getByTestId(`accept-${pid}`)).toHaveCount(0);
  const core = await powerAdmin<PowerView>(`/api/power/${pid}`);
  const tier = core.options[0].distribution.tier ?? "";
  await expect(page.getByTestId(`power-tier-${tier}`)).toBeVisible();
  await page.getByTestId(`power-tier-${tier}`).click();
  await expect(page.getByTestId(`power-draws-${tier}`)).toContainText("gives");
  await expect(page.getByTestId(`power-draws-${tier}`).locator(`[data-cite="plan:${pid}"]`)).toBeVisible();
  const line = join(POWER_HOME, "vault", "base", "lines", `ln-${pid}.md`);
  expect(existsSync(line)).toBe(true);
  expect(readFileSync(line, "utf-8")).toContain("st: accepted");
  // Reload: the address holds the design.
  await page.reload();
  await page.getByTestId("shell").waitFor();
  await expect(page.locator(`.power-option[data-option="${pid}"]`)).toContainText("accepted");
  await page.screenshot({ path: shot("power-accepted") });
});

test("every theme reads at WCAG contrast, and nothing is cut off on the phone", async () => {
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await page.evaluate((id) => {
      window.location.hash = `/power/${id}`;
    }, accepted);
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", "power");
    await expect(page.locator(".power-option").first()).toBeVisible();
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/power/${slug}`] = found;
  }
  expect(misses).toEqual({});
  expect(await clipped(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  await page.screenshot({ path: shot("phone-power"), fullPage: true });
  await page.setViewportSize(MONITOR_2);
  await page.screenshot({ path: shot("power-final") });
});
