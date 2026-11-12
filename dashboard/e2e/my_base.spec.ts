// WO-3.8 end to end: My base in the Base view against the real core (e2e/serve_core.py's made-up
// export). Nothing owned or stocked at first (D63); ticking a machine, entering stock and
// overriding the tier go through the core and come back on reload; the header's tier follows
// the override and clears with it (D64); every theme reads at WCAG contrast on monitor 2 and the
// phone, and nothing in the view is cut off on the phone. Screenshots go to test-results/.
import { type Browser, type Page, expect, test } from "@playwright/test";
import type { MyBase } from "../src/api/types";
import { asAdmin, clipped, pair, themeMisses } from "./harness";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };
const EBF = 1000;
const STEEL = "gregtech:gt.metaitem.01@11305";

let page: Page;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await browser.newPage({ viewport: MONITOR_2 });
  await pair(page, "Monitor 2 (My base)");
});

test.afterAll(async () => {
  // Leave the shared harness core as the other specs expect it: no override, nothing listed.
  await asAdmin("/api/base/tier", { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"stage":null}' });
  await asAdmin(`/api/base/machines/${EBF}`, { method: "DELETE" });
  await asAdmin(`/api/base/stock?key=${encodeURIComponent(STEEL)}`, { method: "DELETE" });
  await page.close();
});

async function openBase(): Promise<void> {
  await page.evaluate(() => {
    window.location.hash = "/base";
  });
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "base");
  await expect(page.getByTestId("base-tier")).toBeVisible();
}

test("nothing is owned or stocked until Clayton says so", async () => {
  await page.getByTestId("nav-base").click();
  await openBase();
  await expect(page.getByTestId("machines-empty")).toBeVisible();
  await expect(page.getByTestId("stock-empty")).toBeVisible();
  await expect(page.getByTestId("base-tier-how")).toContainText("From quest progress");
  await page.screenshot({ path: shot("base-empty") });
});

test("a machine is found, ticked and counted", async () => {
  await page.getByTestId("machine-find-input").fill("electric blast furnace");
  await page.getByTestId("machine-find-go").click();
  await page.locator(`[data-candidate="mte:${EBF}"]`).click();
  const row = page.locator(`[data-machine="${EBF}"]`);
  await expect(row).toContainText("Electric Blast Furnace");
  await expect(page.getByTestId(`machine-count-${EBF}`)).toHaveValue("1");
  await page.getByTestId(`machine-more-${EBF}`).click();
  await expect(page.getByTestId(`machine-count-${EBF}`)).toHaveValue("2");
  await page.getByTestId(`machine-count-${EBF}`).fill("4");
  await page.getByTestId(`machine-count-${EBF}`).press("Enter");
  await expect(page.getByTestId(`machine-count-${EBF}`)).toHaveValue("4");
});

test("stock is found and entered by its key", async () => {
  await page.getByTestId("stock-find-input").fill("steel ingot");
  await page.getByTestId("stock-find-go").click();
  await page.locator(`[data-candidate="${STEEL}"]`).click();
  await page.getByTestId("stock-add-qty").fill("64");
  await page.getByTestId("stock-add-go").click();
  await expect(page.locator(`[data-stock="${STEEL}"]`)).toContainText("Steel Ingot");
  await expect(page.getByTestId(`stock-qty-${STEEL}`)).toHaveValue("64");
});

test("a refused edit says why and changes nothing", async () => {
  await page.getByTestId(`stock-qty-${STEEL}`).fill("0");
  await page.getByTestId(`stock-qty-${STEEL}`).press("Enter");
  await expect(page.getByTestId(`stock-qty-${STEEL}`)).toHaveValue("64"); // 0 isn't stock: put back
  const base = await asAdmin<MyBase>("/api/base");
  expect(base.stock.map((s) => [s.key, s.qty])).toEqual([[STEEL, 64]]);
});

test("the tier override sticks, shows in the header and clears", async () => {
  await page.getByTestId("base-tier-pick").selectOption("HV");
  await page.getByTestId("base-tier-set").click();
  await expect(page.getByTestId("base-tier-how")).toContainText("Your override");
  await expect(page.locator(".header-tier")).toContainText("HV");
  await page.reload();
  await page.getByTestId("shell").waitFor();
  await openBase();
  await expect(page.getByTestId("base-tier-how")).toContainText("Your override");
  await expect(page.getByTestId(`machine-count-${EBF}`)).toHaveValue("4");
  await expect(page.getByTestId(`stock-qty-${STEEL}`)).toHaveValue("64");
  // What the designer reads (BaseInputs, through the API).
  const base = await asAdmin<MyBase>("/api/base");
  expect([base.tier.stage, base.tier.tier, base.tier.how]).toEqual(["HV", 3, "override"]);
  expect(base.machines.map((m) => [m.mte_id, m.count, m.source])).toEqual([[EBF, 4, "manual"]]);
  await page.screenshot({ path: shot("base-filled") });
});

test("every theme reads at WCAG contrast, and nothing is cut off on the phone", async () => {
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await openBase();
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/base/${slug}`] = found;
  }
  expect(misses).toEqual({});
  expect(await clipped(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  await page.screenshot({ path: shot("phone-base"), fullPage: true });
  await page.setViewportSize(MONITOR_2);
});

test("back to quests, and rows removed", async () => {
  await openBase();
  await page.getByTestId("base-tier-clear").click();
  await expect(page.getByTestId("base-tier-how")).toContainText("From quest progress");
  await page.getByTestId(`machine-remove-${EBF}`).click();
  await expect(page.getByTestId("machines-empty")).toBeVisible();
  await page.getByTestId(`stock-remove-${STEEL}`).click();
  await expect(page.getByTestId("stock-empty")).toBeVisible();
});
