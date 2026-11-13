// WO-6.6 end to end: the Ores view against the real core (e2e/serve_core.py loads WO-1.6's made-up
// ore mixes and Lab world, with Clayton at x 100, z 20). Ferrite's veins come in the core's order
// (live first, then the layer, then nearest: the nearest vein ranks third, where Ferrite is only
// sporadic), are drawn on the map, and a waypoint is only proposed from the page: it is written
// into the fake Lab instance's JourneyMap folder after the approval card's confirm, and not
// before. Every theme reads at WCAG contrast on monitor 2 and the phone, and nothing in the view
// is cut off on the phone. No approved board yet (D92): needs Clayton's board approval.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { type Browser, type Page, expect, test } from "@playwright/test";
import { HOME, asAdmin, clipped, pair, themeMisses } from "./harness";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };
// Worked out by hand from the fixture (tests/unit/test_ore_intel.py, EXPECTED).
const ORDER = ["vein:0/10/-5", "vein:0/-2/4", "vein:0/4/1", "vein:0/1/1"];
const WAYPOINTS = join(HOME, "fixtures", "instances", "GTNH-Lab", ".minecraft", "journeymap", "data", "sp", "lab-ores", "waypoints");
const FILE = join(WAYPOINTS, "Ferrite vein_168,25,-72.json");

let page: Page;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await browser.newPage({ viewport: MONITOR_2 });
  await pair(page, "Monitor 2 (Ores)");
});

test.afterAll(async () => {
  await page.close();
});

async function openOres(): Promise<void> {
  await page.evaluate(() => {
    window.location.hash = "/ores";
  });
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "ores");
  await expect(page.getByTestId("ores-find")).toBeVisible();
}

async function findFerrite(): Promise<void> {
  await page.getByTestId("ores-input").fill("Ferrite");
  await page.getByTestId("ores-go").click();
  await expect(page.getByTestId("ores-list").locator("[data-cite]")).toHaveCount(4);
}

test("Ferrite's veins come in the core's order, from where he stands, on the map", async () => {
  await openOres();
  await expect(page.getByTestId("ores-position")).toContainText("x 100, y 64, z 20");
  await expect(page.getByTestId("ores-source")).toContainText("lab-ores (Lab)");
  await findFerrite();
  const cites = await page.getByTestId("ores-list").locator("[data-cite]").evaluateAll((els) => els.map((e) => e.getAttribute("data-cite")));
  expect(cites).toEqual(ORDER);
  await expect(page.getByTestId("ores-list")).toContainText("114 blocks");
  await expect(page.getByTestId("ores-map").locator(".ore-map-vein")).toHaveCount(4);
  await expect(page.getByTestId("ores-map-you")).toBeVisible();
  const payload = await asAdmin<{ card: { rows: { ref: string; distance: number }[] } }>("/api/ores?material=Ferrite");
  expect(payload.card.rows.map((r) => [r.ref, r.distance])).toEqual([
    [ORDER[0], 114],
    [ORDER[1], 134],
    [ORDER[2], 28],
    [ORDER[3], 76],
  ]);
  await page.screenshot({ path: shot("ores-ferrite") });
});

test("a waypoint is proposed, read back, and written only after his confirm", async () => {
  expect(existsSync(FILE)).toBe(false);
  await page.getByTestId("ores-mark-1").click();
  await expect(page.getByTestId("ores-said")).toContainText('Add waypoint "Ferrite vein" at 168, 25, -72 in dimension 0 in Lab. Say confirm.');
  const card = page.getByTestId("rail-approvals").locator('[data-kind="action"]');
  await expect(card).toContainText("Ferrite vein");
  expect(existsSync(FILE)).toBe(false); // nothing is written on a proposal
  await page.screenshot({ path: shot("ores-proposed") });
  await card.getByTestId("action-confirm").click();
  await expect.poll(() => existsSync(FILE)).toBe(true);
  const doc = JSON.parse(readFileSync(FILE, "utf-8")) as Record<string, unknown>;
  expect([doc.x, doc.y, doc.z, doc.dimensions, doc.origin]).toEqual([168, 25, -72, [0], "JourneyMap"]);
  const actions = await asAdmin<{ recent: { kind: string; state: string; confirmed_via: string }[] }>("/api/actions");
  expect(actions.recent[0]).toMatchObject({ kind: "waypoint", state: "done", confirmed_via: "dashboard" });
});

test("every theme reads at WCAG contrast, and nothing is cut off on the phone", async () => {
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await openOres();
    await findFerrite();
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/ores/${slug}`] = found;
  }
  expect(misses).toEqual({});
  expect(await clipped(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  expect(await page.getByTestId("view").evaluate((v) => v.scrollWidth - v.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: shot("phone-ores"), fullPage: true });
  await page.setViewportSize(MONITOR_2);
});
