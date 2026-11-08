// WO-2.6 acceptance, end to end against a real core (fake fast lane): usable on monitor 2 at
// 2560x1440, switching theme moves nothing and every theme reads at WCAG contrast, glance mode
// comes with the game's focus and is clickable (D49), the keyboard drives the shell, and the
// phone layout fits 390 px. Screenshots go to test-results/ (never into git).
import { type Browser, type Page, expect, test } from "@playwright/test";
import { asAdmin, clipped, contrastMisses, control, fingerprintState, holdInstanceLock, layout, pair, pendingCode } from "./harness";

const THEMES = ["brass", "machine", "alu", "hud", "circuit", "tung", "holo", "fusion", "naq", "deep", "calm", "manual"];
const shot = (name: string) => test.info().outputPath(`${name}.png`);

let page: Page;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  control("running", "[]");
  control("foreground", "");
  control("key", "stored");
  fingerprintState(false);
  page = await browser.newPage({ viewport: { width: 2560, height: 1440 } });
  await pair(page);
});

test.afterAll(async () => {
  await page.close();
});

async function mode(): Promise<string | null> {
  return page.getByTestId("shell").getAttribute("data-mode");
}

test("an unpaired browser gets the pairing screen and nothing else", async ({ browser }) => {
  const other = await browser.newPage();
  await other.goto("/");
  await expect(other.getByTestId("pairing")).toBeVisible();
  await expect(other.getByTestId("shell")).toHaveCount(0);
  const state = await other.evaluate(async () => (await fetch("/api/state")).status);
  expect(state).toBe(401);
  await other.close();
});

test("the workbench fills monitor 2 at 2560x1440", async () => {
  await page.keyboard.press("1");
  await expect(page.getByTestId("view-now")).toBeVisible();
  expect(await mode()).toBe("workbench"); // the game isn't running
  const box = await layout(page);
  expect(box.header).toMatchObject({ x: 0, y: 0, width: 2560, height: 104 });
  expect(box.nav).toMatchObject({ x: 0, y: 104, width: 268 });
  expect(box.rail).toMatchObject({ x: 2560 - 470, y: 104, width: 470 });
  expect(box["voice-strip"]).toMatchObject({ x: 0, y: 1440 - 96, width: 2560, height: 96 });
  expect(box.view).toMatchObject({ x: 268, width: 2560 - 268 - 470, height: 1440 - 104 - 96 });
  for (const slot of ["slot-a", "slot-b", "slot-c"]) expect(box[slot].width).toBeGreaterThan(400);
  const overflow = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
  expect(overflow).toEqual([2560, 1440]);
  await page.screenshot({ path: shot("workbench-now") });
});

test("switching themes moves nothing, and every theme reads at WCAG contrast", async () => {
  await page.getByTestId("ask-input").fill("recipe for steel");
  await page.getByTestId("ask-send").click();
  await expect(page.getByTestId("now-answer")).toBeVisible();
  const before = await page.evaluate(() => document.body.className);
  let first: Awaited<ReturnType<typeof layout>> | null = null;
  const misses: Record<string, unknown[]> = {};
  for (const slug of THEMES) {
    await page.evaluate((s) => {
      document.body.className = `theme-${s}`;
    }, slug);
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    const boxes = await layout(page);
    if (first === null) first = boxes;
    else expect(boxes, slug).toEqual(first);
    const found = await contrastMisses(page);
    if (found.length) misses[slug] = found;
    await page.screenshot({ path: shot(`theme-${slug}`) });
  }
  expect(misses).toEqual({});
  await page.evaluate((c) => {
    document.body.className = c;
  }, before);
});

test("no font is loaded from anywhere but the core", async () => {
  const fonts = await page.evaluate(() =>
    performance.getEntriesByType("resource").map((e) => e.name).filter((n) => /\.(woff2?|ttf|otf)(\?|$)/.test(n) || n.includes("fonts.g")),
  );
  expect(fonts.length).toBeGreaterThan(0);
  for (const url of fonts) expect(new URL(url).host).toBe(new URL(page.url()).host);
  const licence = await page.evaluate(async () => (await fetch("/fonts/licenses/README.txt")).text());
  expect(licence).toContain("Open Font License");
});

test("number keys switch views and Ctrl+K opens the palette", async () => {
  for (const [key, view] of [["2", "chat"], ["3", "search"], ["8", "costs"], ["9", "settings"], ["4", "designer"], ["1", "now"]]) {
    await page.keyboard.press(key);
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", view);
  }
  await page.keyboard.press("Control+k");
  const input = page.getByTestId("palette-input");
  await expect(input).toBeFocused();
  await input.fill("cost");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("palette")).toHaveCount(0);
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "costs");
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("steel");
  await expect(page.locator('[data-entry="r-find-gregtech:gt.metaitem.01@11305"]')).toBeVisible();
  await page.screenshot({ path: shot("palette") });
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("palette")).toHaveCount(0);
  // Typing in a box never switches views.
  await page.keyboard.press("2");
  await page.getByTestId("chat-input").fill("123");
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "chat");
  await page.getByTestId("chat-input").fill("");
});

test("Chat keeps every answer as a card, and a citation opens its recipe card", async () => {
  await page.keyboard.press("2");
  await page.getByTestId("chat-input").fill("what uses steel");
  await page.getByTestId("chat-send").click();
  const log = page.getByTestId("chat-log");
  await expect(log.locator("[data-answer]")).toHaveCount(2);
  await expect(log.locator('[data-card="recipe"]').first()).toBeVisible();
  await log.getByTestId("open-card").first().click();
  const modal = page.getByTestId("card-modal");
  await expect(modal.locator('[data-card="recipe"]')).toBeVisible();
  await page.keyboard.press("Escape");
  await log.locator('button[data-cite^="r:gt.recipe.blastfurnace/"]').first().click();
  await expect(modal.locator(".card-title")).toContainText("Blast Furnace");
  await page.screenshot({ path: shot("recipe-card") });
  await page.getByTestId("modal-close").click();
});

test("an unsure match asks, and a candidate is picked by clicking it", async () => {
  await page.getByTestId("chat-input").fill("recipe for dust");
  await page.getByTestId("chat-send").click();
  const choices = page.getByTestId("chat-log").locator('[data-card="choices"]').last();
  await expect(choices).toBeVisible();
  await choices.getByRole("button").first().click();
  await expect(page.getByTestId("chat-log").locator("[data-answer]")).toHaveCount(4);
});

test("Search finds an item and shows its recipes, uses and quests", async () => {
  await page.keyboard.press("3");
  await page.getByTestId("search-input").fill("steel");
  await page.getByTestId("search-go").click();
  await page.locator('[data-result="gregtech:gt.metaitem.01@11305"]').click();
  const detail = page.getByTestId("search-detail");
  await expect(detail.locator('[data-card="recipe"] li.recipe')).toHaveCount(5);
  await detail.getByRole("tab", { name: "Uses" }).click();
  await expect(detail.locator('[data-card="recipe"]')).toContainText("Macerator");
  await detail.getByRole("tab", { name: "Quests" }).click();
  await expect(detail.locator('[data-card="quest"]')).toContainText("Steel Age");
  await expect(detail.getByTestId("spoiler").first()).toBeVisible(); // nudge-first (D26)
});

test("Settings + Health shows the processes, the last export, VERIFY, doctor and devices", async () => {
  await page.keyboard.press("9");
  await expect(page.locator('[data-card="health"]')).toContainText("Core");
  await expect(page.getByTestId("last-export")).toContainText("20261106T120000Z");
  await expect(page.locator('[data-verify="V01"]')).toBeVisible();
  await expect(page.locator('[data-verify="V33"]')).toBeVisible();
  await page.getByTestId("run-doctor").click();
  await expect(page.locator(".doctor-output")).toContainText("doctor exited", { timeout: 30_000 });
  await expect(page.getByTestId("devices")).toContainText("Monitor 2");
  await expect(page.getByTestId("devices")).toContainText("this device");
  await page.screenshot({ path: shot("settings") });
});

test("Costs shows the cycle against the included credits", async () => {
  await page.keyboard.press("8");
  await expect(page.getByTestId("spent")).toHaveText("$0.00");
  await expect(page.getByTestId("credits")).toContainText("of $100.00 included");
  await expect(page.getByTestId("by-lane")).toBeVisible();
});

test("a device asking to pair waits for Clayton on the rail, and Deny drops it", async ({ browser }) => {
  const phone = await browser.newPage();
  await phone.goto("/");
  await phone.getByTestId("pair-name").fill("Phone");
  await phone.getByTestId("pair-start").click();
  const code = await pendingCode();
  const card = page.getByTestId("rail-approvals").locator('[data-kind="pairing"]');
  await expect(card).toContainText(`${code.slice(0, 4)} ${code.slice(4)}`);
  await expect(card.getByTestId("countdown")).toBeVisible();
  await card.getByTestId("action-deny").click();
  await expect(card).toHaveCount(0);
  expect(await asAdmin<unknown[]>("/api/pair/pending")).toEqual([]);
  await phone.close();
});

test("the designed states: stale data and AI offline", async () => {
  await page.keyboard.press("1");
  fingerprintState(true);
  control("key", "missing");
  const chips = page.getByTestId("status-chips");
  await expect(chips.locator('[data-state="stale"]')).toBeVisible({ timeout: 15_000 });
  await expect(chips.locator('[data-state="ai_offline"]')).toBeVisible({ timeout: 15_000 });
  await expect(chips.locator('[data-state="game_not_running"]')).toBeVisible();
  await expect(page.locator('[data-alert="data-stale"]')).toBeVisible();
  await page.keyboard.press("2");
  await expect(page.locator('button[data-cite^="r:"]').first()).toContainText("data stale");
  await page.screenshot({ path: shot("states-stale-ai-offline") });
  fingerprintState(false);
  control("key", "stored");
  await expect(chips.locator('[data-state="stale"]')).toHaveCount(0, { timeout: 15_000 });
});

test("the designed states: an export running is a state and a job on the rail", async () => {
  await page.keyboard.press("1");
  const release = await holdInstanceLock("e2e: export after the mods changed");
  const chips = page.getByTestId("status-chips");
  try {
    await expect(chips.locator('[data-state="export_running"]')).toBeVisible({ timeout: 15_000 });
    await expect(chips.locator('[data-state="game_not_running"]')).toHaveCount(0);
    await expect(page.getByTestId("rail").locator('[data-job="export"]')).toContainText("Export running");
    await page.screenshot({ path: shot("states-export-running") });
  } finally {
    await release();
  }
  await expect(chips.locator('[data-state="export_running"]')).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByTestId("rail").locator('[data-job="export"]')).toHaveCount(0);
});

test("glance comes with the game's focus, is clickable, and the mouse brings the workbench back", async () => {
  await page.keyboard.press("1");
  control("running", '["play"]');
  control("foreground", "javaw.exe");
  // GTNH takes the mouse: the pointer leaves the dashboard window.
  await page.evaluate(() => document.documentElement.dispatchEvent(new MouseEvent("mouseleave", { relatedTarget: null })));
  await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "glance", { timeout: 15_000 });
  await expect(page.getByTestId("view-glance")).toBeVisible();
  await expect(page.getByTestId("nav")).toHaveCount(0);
  await expect(page.getByTestId("rail")).toHaveCount(0);
  const size = await page.locator(".view-glance").evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(size).toBeGreaterThanOrEqual(22); // type-glance-body
  await page.screenshot({ path: shot("glance") });
  // Clickable (D49): a click opens its view, and the pointer coming in means workbench.
  await page.getByTestId("glance-answer").getByRole("button", { name: /Open/ }).click();
  await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "workbench");
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "chat");
  control("foreground", "chrome.exe");
  control("running", "[]");
});

test("the phone layout fits 390x844 with tabs at the bottom", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.keyboard.press("1");
  await page.evaluate(() => {
    window.location.hash = "/now";
  });
  await expect(page.getByTestId("view-now")).toBeVisible();
  expect(await mode()).toBe("workbench");
  await expect(page.getByTestId("rail")).toHaveCount(0);
  const nav = await page.getByTestId("nav").boundingBox();
  expect(nav?.y).toBeGreaterThan(844 - 80);
  expect(await page.getByTestId("nav").locator("button").count()).toBe(5);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  // Nothing in a view is cut off: panels grow with what they hold and the view scrolls.
  await expect(page.getByTestId("now-answer")).toBeVisible();
  expect(await clipped(page), "now").toEqual([]);
  // The state chips keep one line of their own under the wordmark, never cut top or bottom.
  const chips = page.getByTestId("status-chips");
  await expect(chips.locator('[data-state="game_not_running"]')).toBeVisible();
  expect(await chips.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1);
  const header = await page.getByTestId("header").boundingBox();
  const chipBox = await chips.boundingBox();
  expect(chipBox && header && chipBox.y + chipBox.height <= header.y + header.height).toBe(true);
  for (const tab of ["chat", "search", "costs", "settings"]) {
    await page.getByTestId(`nav-${tab}`).click();
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", tab);
    expect(await page.evaluate(() => document.documentElement.scrollWidth), tab).toBeLessThanOrEqual(390);
    expect(await clipped(page), tab).toEqual([]);
  }
  await page.getByTestId("nav-now").click();
  await page.screenshot({ path: shot("phone-now"), fullPage: true });
  expect(await contrastMisses(page)).toEqual([]);
  await page.setViewportSize({ width: 2560, height: 1440 });
});
