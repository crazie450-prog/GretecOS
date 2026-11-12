// WO-3.7 end to end: the Designer against the real core (e2e/serve_core.py's made-up export, where
// steel ingot has three routes at HV). A typed request designs and saves every route, shown side by
// side in the core's rank order with the differences marked and each route's flow laid out by
// elkjs; a node pinned in the Designer survives a reload and a re-solve (the §8 acceptance); a
// route is accepted after a confirm and its line note lands in the vault; every theme reads at
// WCAG contrast on monitor 2 and the phone, and nothing is cut off on the phone.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { type Browser, type Page, expect, test } from "@playwright/test";
import type { DesignerView } from "../src/api/types";
import { HOME, asAdmin, clipped, pair, themeMisses } from "./harness";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };
const STEEL = "gregtech:gt.metaitem.01@11305";
const DUST = "gregtech:gt.metaitem.01@2305";

let page: Page;
let pinnedPlan = "";
let pinnedRecipe = "";

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await browser.newPage({ viewport: MONITOR_2 });
  await pair(page, "Monitor 2 (Designer)");
});

test.afterAll(async () => {
  await page.close();
});

function planInHash(): string {
  const m = /#\/designer\/(pl-[a-z0-9]+)$/.exec(page.url());
  if (!m) throw new Error(`no plan in ${page.url()}`);
  return m[1];
}

async function routeIds(): Promise<string[]> {
  return page.locator(".designer-route[data-route]").evaluateAll((els) => els.map((e) => e.getAttribute("data-route") ?? ""));
}

test("a typed request designs every route, in the core's rank order, differences marked", async () => {
  await page.keyboard.press("4");
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "designer");
  await expect(page.getByTestId("view-designer")).toBeVisible();
  await page.getByTestId("designer-target").fill("steel ingot");
  await page.getByTestId("designer-tier").selectOption("HV");
  await page.getByTestId("designer-go").click();
  await expect(page.getByTestId("route-3-base")).toBeVisible({ timeout: 30_000 });
  const pid = planInHash();
  const ids = await routeIds();
  expect(ids).toHaveLength(3);
  expect(ids[0]).toBe(pid);
  // The order on screen is the core's (rank.py's), never re-sorted.
  const core = await asAdmin<DesignerView>(`/api/plans/${pid}/designer`);
  expect(ids).toEqual(core.routes.map((r) => r.id));
  expect(core.routes.map((r) => r.position)).toEqual([1, 2, 3]);
  const compare = page.getByTestId("designer-compare");
  await expect(compare.locator("li.differs").first()).toBeVisible();
  await expect(compare.locator("dd.best").first()).toBeVisible();
  // Each route's flow diagram, laid out by elkjs (the card's one layout, D159).
  await expect(page.locator(".designer-route .flow-canvas")).toHaveCount(3);
  await expect(page.locator(".designer-route .react-flow__node").first()).toBeVisible();
  await page.screenshot({ path: shot("designer-routes") });
});

test("a pin made in the Designer survives a reload and a re-solve", async () => {
  const second = page.locator(".designer-route").nth(1);
  await second.locator(`.designer-node[data-node="${DUST}"] button.result`).click();
  const panel = page.getByTestId("designer-alternatives");
  await expect(panel.locator("tr[data-recipe]").first()).toBeVisible();
  const free = panel.locator("tr[data-recipe]:not(.used)").first();
  pinnedRecipe = (await free.getAttribute("data-recipe")) ?? "";
  expect(pinnedRecipe).toMatch(/^r:/);
  await free.getByRole("button", { name: "Pin" }).click();
  pinnedPlan = "";
  await expect(page.locator(`[data-testid^="pins-"]`).first()).toBeVisible({ timeout: 30_000 });
  pinnedPlan = planInHash();
  const pins = page.getByTestId(`pins-${pinnedPlan}`);
  await expect(pins).toContainText("pinned Steel Dust");
  await expect(pins.locator(`[data-cite="${pinnedRecipe}"]`)).toBeVisible();
  await expect(panel.locator(`tr[data-recipe="${pinnedRecipe}"]`)).toHaveClass(/used/);
  await page.screenshot({ path: shot("designer-pinned") });

  // Reload: the address holds the design, the saved plan holds the pin.
  await page.reload();
  await page.getByTestId("shell").waitFor();
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "designer");
  await expect(page.getByTestId(`pins-${pinnedPlan}`).locator(`[data-cite="${pinnedRecipe}"]`)).toBeVisible();
  const reloaded = await asAdmin<DesignerView>(`/api/plans/${pinnedPlan}/designer`);
  expect(reloaded.routes[0].pins).toContainEqual({ node: DUST, recipe: pinnedRecipe });

  // Re-solve: the same request, the same routes, the pin held.
  await page.getByTestId("designer-resolve").click();
  await expect(page.getByTestId("designer-busy")).toHaveCount(0, { timeout: 30_000 });
  expect(planInHash()).toBe(pinnedPlan);
  await expect(page.getByTestId(`pins-${pinnedPlan}`).locator(`[data-cite="${pinnedRecipe}"]`)).toBeVisible();
  const again = await asAdmin<DesignerView>(`/api/plans/${pinnedPlan}/designer`);
  expect(again.routes[0].pins).toContainEqual({ node: DUST, recipe: pinnedRecipe });
  // Opening the Designer again from the nav opens the latest design: the pinned one.
  await page.keyboard.press("1");
  await page.keyboard.press("4");
  await expect(page.getByTestId(`pins-${pinnedPlan}`)).toBeVisible();
});

test("accepting asks first, then writes the line note in the vault", async () => {
  const pid = pinnedPlan;
  await page.getByTestId(`accept-${pid}`).click();
  await expect(page.getByTestId(`accept-confirm-${pid}`)).toContainText("EU ledger");
  await page.screenshot({ path: shot("designer-accept-confirm") });
  await page.getByTestId(`accept-yes-${pid}`).click();
  const column = page.locator(`.designer-route[data-route="${pid}"]`);
  await expect(column).toContainText("accepted");
  await expect(page.getByTestId(`accept-${pid}`)).toHaveCount(0);
  const line = join(HOME, "vault", "base", "lines", `ln-${pid.slice(3)}.md`);
  expect(existsSync(line)).toBe(true);
  const text = readFileSync(line, "utf-8");
  expect(text).toContain("st: accepted");
  expect(text).toContain(`plan: plan:${pid}`);
  expect(text).toContain(`tgt: ${STEEL}`);
  expect(readFileSync(join(HOME, "vault", "plans", `${pid}.md`), "utf-8")).toContain("st: accepted");
});

test("a route gets a human name", async () => {
  const pid = pinnedPlan;
  await page.getByTestId(`rename-${pid}`).click();
  await page.getByTestId(`rename-input-${pid}`).fill("Steel for the EBF wall");
  await page.getByTestId(`rename-save-${pid}`).click();
  await expect(page.locator(`.designer-route[data-route="${pid}"] .gos-title`)).toHaveText("Steel for the EBF wall");
  await expect(page.getByTestId("designer-saved")).toContainText("Steel for the EBF wall");
});

test("every theme reads at WCAG contrast, and nothing is cut off on the phone", async () => {
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await page.evaluate((id) => {
      window.location.hash = `/designer/${id}`;
    }, pinnedPlan);
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", "designer");
    await expect(page.locator(".designer-route .react-flow__node").first()).toBeVisible();
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/designer/${slug}`] = found;
  }
  expect(misses).toEqual({});
  const cut = (await clipped(page)).filter((c) => !c.startsWith("react-flow"));
  expect(cut).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  await page.screenshot({ path: shot("phone-designer"), fullPage: true });
  await page.setViewportSize(MONITOR_2);
  await page.screenshot({ path: shot("designer-final") });
});
