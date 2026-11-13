// WO-6.7 end to end: the Crops view against the real core (e2e/serve_core.py loads the crop guide's
// made-up world, tests/fixtures/crops/cropworld.py). The guide to Iron Fern comes in the core's
// order with the layout grid and cited numbers; following it, adding a crop he has, and "next
// crop step" (answered in code, no model) all go through the core. Every theme reads at WCAG
// contrast on monitor 2 and the phone, and nothing in the view is cut off on the phone. Crops has
// an approved board (D92) but no handoff in the repo: needs Clayton's board check (D158 (5)).
import { type Browser, type Page, expect, test } from "@playwright/test";
import { asAdmin, clipped, pair, themeMisses } from "./harness";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };

interface Payload {
  target: { crop: string } | null;
  have: { crop: string; stats: number[] }[];
  guide: { steps: { n: number; kind: string; child: string; numbers: { refs: string[] }[] }[]; next: number } | null;
}

let page: Page;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await browser.newPage({ viewport: MONITOR_2 });
  await pair(page, "Monitor 2 (Crops)");
});

test.afterAll(async () => {
  await page.close();
});

async function openCrops(): Promise<void> {
  await page.evaluate(() => {
    window.location.hash = "/crops";
  });
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "crops");
  await expect(page.getByTestId("crops-target")).toBeVisible();
}

async function showIronFern(): Promise<void> {
  await page.getByTestId("crops-input").fill("Iron Fern");
  await page.getByTestId("crops-go").click();
  await expect(page.getByTestId("crops-steps").getByRole("button")).toHaveCount(5);
}

test("the guide to Iron Fern comes in the core's order, with its layout and cited numbers", async () => {
  await openCrops();
  await expect(page.getByTestId("crops-following")).toContainText("Following no crop yet");
  await showIronFern();
  const payload = await asAdmin<Payload>("/api/crops?crop=cropsnh%3AironFern");
  const kinds = payload.guide?.steps.map((s) => s.kind);
  expect(kinds).toEqual(["obtain", "obtain", "sticks", "sticks", "breeder"]);
  for (const s of payload.guide?.steps ?? []) for (const n of s.numbers) expect(n.refs.length).toBeGreaterThan(0);
  await page.getByTestId("crops-step-4").click();
  await expect(page.getByTestId("crops-layout").locator(".crop-cell.parent")).toHaveCount(2);
  await expect(page.getByTestId("crops-layout").locator(".crop-cell.double")).toContainText("under it: Stone");
  await expect(page.getByTestId("crops-numbers")).toContainText("Chance per growth tick (every 256 ticks): 16.7%");
  await expect(page.getByTestId("crops-numbers").locator("[data-cite]").first()).toBeVisible();
  await page.getByTestId("crops-step-5").click();
  await expect(page.getByTestId("crops-step")).toContainText("Crop Breeder");
  await expect(page.getByTestId("crops-reqs")).toContainText("Catalyst: 1 x Iron Ore");
  await page.screenshot({ path: shot("crops-iron-fern") });
});

test("following it, adding a crop he has, and next crop step go through the core", async () => {
  await page.getByTestId("crops-follow").click();
  await expect(page.getByTestId("crops-following")).toContainText("Following Iron Fern");
  await page.getByTestId("crops-have-crop").fill("Carrot");
  await page.getByTestId("crops-have-stats").fill("3 2 1");
  await page.getByTestId("crops-have-add").click();
  await expect(page.getByTestId("crops-have")).toContainText("3/2/1");
  const payload = await asAdmin<Payload>("/api/crops");
  expect(payload.target?.crop).toBe("cropsnh:ironFern");
  expect(payload.have).toEqual([{ crop: "cropsnh:carrot", ref: "crop:cropsnh:carrot", name: "Carrot", stats: [3, 2, 1] }]);
  expect(payload.guide?.steps.some((s) => s.child === "cropsnh:carrot")).toBe(false);
  const reply = await asAdmin<{ text: string; lane: string; refs: string[] }>("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: "next crop step" }),
  });
  expect(reply.lane).toBe("instant");
  expect(reply.text).toMatch(/^Step 1 of 4: Plant Seeds to start Wheat\./);
  expect(reply.refs).toEqual(["crop:cropsnh:wheat"]);
});

test("every theme reads at WCAG contrast, and nothing is cut off on the phone", async () => {
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await openCrops();
    await page.getByTestId("crops-step-3").click();
    await expect(page.getByTestId("crops-layout")).toBeVisible();
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/crops/${slug}`] = found;
  }
  expect(misses).toEqual({});
  expect(await clipped(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  expect(await page.getByTestId("view").evaluate((v) => v.scrollWidth - v.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: shot("phone-crops"), fullPage: true });
  await page.setViewportSize(MONITOR_2);
});
