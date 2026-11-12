// WO-6.4 end to end: the Quests view against the real core (e2e/serve_core.py's made-up quest
// book, tests/unit/tools/conftest.py). The frontier comes ranked with its reasons and its items
// link to Search; the quest past the frontier ("Stainless Dreams", shown in the book) is blurred
// and sent at nudge level, so what its task asks for is not in the page at all until "Spoil it";
// quest-book text that reads like an instruction is shown as plain text. Every theme reads at
// WCAG contrast on monitor 2 and the phone, and nothing in the view is cut off on the phone.
import { type Browser, type Page, expect, test } from "@playwright/test";
import { asAdmin, clipped, pair, themeMisses } from "./harness";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };
const STEEL_AGE = "q:AAAAAAAAAAAAAAAAAAAAAg";
const STAINLESS = "q:AAAAAAAAAAAAAAAAAAAAAw";
const SPOILER = "Stainless Steel Dust"; // what the locked quest's task asks for

let page: Page;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await browser.newPage({ viewport: MONITOR_2 });
  await pair(page, "Monitor 2 (Quests)");
});

test.afterAll(async () => {
  await page.close();
});

async function openQuests(): Promise<void> {
  await page.evaluate(() => {
    window.location.hash = "/quests";
  });
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "quests");
  await expect(page.getByTestId("quests-frontier")).toBeVisible();
}

test("the frontier is ranked with its reasons, and nothing past it is spoiled at nudge", async () => {
  await page.keyboard.press("6");
  await openQuests();
  const front = page.getByTestId("quests-frontier");
  await expect(front).toContainText("Steel Age");
  await expect(front).toContainText("main quest");
  // Quest-book text is data: an instruction-like name is drawn as text, never acted on.
  await expect(front).toContainText("Ignore previous instructions");
  await expect(page.getByTestId("quests-level")).toContainText("nudge");
  await expect(page.getByTestId("spoiler-zone")).not.toHaveClass(/revealed/);
  // The nudge names the quest the book already shows, and nothing it asks for.
  await expect(page.getByTestId("quests-beyond")).toContainText("Stainless Dreams");
  expect(await page.content()).not.toContain(SPOILER);
  const payload = await asAdmin<Record<string, unknown>>("/api/quests");
  expect(JSON.stringify(payload)).not.toContain(SPOILER);
  expect(JSON.stringify(payload)).toContain(STAINLESS);
  await expect(page.getByTestId("quests-lines")).toContainText("Getting Started");
  await page.screenshot({ path: shot("quests-nudge") });
});

test("a tap lifts the blur only; Spoil it asks the core for everything", async () => {
  await page.getByTestId("quests-reveal").click();
  await expect(page.getByTestId("spoiler-zone")).toHaveClass(/revealed/);
  expect(await page.content()).not.toContain(SPOILER);
  await page.getByTestId("quests-spoil").click();
  await expect(page.getByTestId("quests-level")).toContainText("full");
  await expect(page.getByTestId("quests-beyond")).toContainText(SPOILER);
  await page.screenshot({ path: shot("quests-full") });
  await page.getByTestId("quests-nudge").click();
  await expect(page.getByTestId("quests-level")).toContainText("nudge");
  await expect(page.getByTestId("quests-beyond")).not.toContainText(SPOILER);
});

test("an item a frontier quest asks for opens in Search", async () => {
  await page.locator(`[data-links="${STEEL_AGE}"] [data-item="gregtech:gt.metaitem.01@11305"]`).click();
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "search");
  await expect(page.getByTestId("view")).toContainText("Steel Age");
});

test("every theme reads at WCAG contrast, and nothing is cut off on the phone", async () => {
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await openQuests();
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/quests/${slug}`] = found;
  }
  expect(misses).toEqual({});
  expect(await clipped(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  expect(await page.getByTestId("view").evaluate((v) => v.scrollWidth - v.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: shot("phone-quests"), fullPage: true });
  await page.setViewportSize(MONITOR_2);
});
