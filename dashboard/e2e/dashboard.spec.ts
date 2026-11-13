// WO-2.6 acceptance, end to end against a real core (fake fast lane): usable on monitor 2 at
// 2560x1440, switching theme moves nothing and every theme reads at WCAG contrast, glance mode
// comes with the game's focus and is clickable (D49), the keyboard drives the shell, and the
// phone layout fits 390 px. Screenshots go to test-results/ (never into git).
import { type Browser, type Page, expect, test } from "@playwright/test";
import { THEMES, asAdmin, clipped, contrastMisses, control, fingerprintState, holdInstanceLock, layout, pair, pendingCode, spend, themeMisses } from "./harness";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };

let page: Page;
const refused: string[] = []; // what the page's Content-Security-Policy blocked (last test)

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  control("running", "[]");
  control("foreground", "");
  control("key", "stored");
  fingerprintState(false);
  page = await browser.newPage({ viewport: { width: 2560, height: 1440 } });
  page.on("console", (m) => {
    if (/Content Security Policy/i.test(m.text())) refused.push(m.text());
  });
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

// The chat log is the core's, shared by every spec (crops.spec asks "next crop step" before this
// file runs), so these tests count from what is there and open their own answer's card.
test("Chat keeps every answer as a card, and a citation opens its recipe card", async () => {
  await page.keyboard.press("2");
  const log = page.getByTestId("chat-log");
  const before = await log.locator("[data-answer]").count();
  await page.getByTestId("chat-input").fill("what uses steel");
  await page.getByTestId("chat-send").click();
  await expect(log.locator("[data-answer]")).toHaveCount(before + 1);
  const mine = log.locator("[data-answer]").last();
  await expect(mine.locator('[data-card="recipe"]')).toBeVisible();
  await mine.getByTestId("open-card").click();
  const modal = page.getByTestId("card-modal");
  await expect(modal.locator('[data-card="recipe"]')).toBeVisible();
  await page.keyboard.press("Escape");
  await log.locator('button[data-cite^="r:gt.recipe.blastfurnace/"]').first().click();
  await expect(modal.locator(".card-title")).toContainText("Blast Furnace");
  await page.screenshot({ path: shot("recipe-card") });
  await page.getByTestId("modal-close").click();
});

test("an unsure match asks, and a candidate is picked by clicking it", async () => {
  const before = await page.getByTestId("chat-log").locator("[data-answer]").count();
  await page.getByTestId("chat-input").fill("recipe for dust");
  await page.getByTestId("chat-send").click();
  const choices = page.getByTestId("chat-log").locator('[data-card="choices"]').last();
  await expect(choices).toBeVisible();
  await choices.getByRole("button").first().click();
  await expect(page.getByTestId("chat-log").locator("[data-answer]")).toHaveCount(before + 2);
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
  // The palette's "Re-run doctor" runs it too, from any view, and Settings shows the run.
  await page.keyboard.press("1");
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("re-run doctor");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "settings");
  await expect(page.locator(".doctor-output")).toContainText("doctor exited", { timeout: 30_000 });
  await expect(page.getByTestId("devices")).toContainText("Monitor 2");
  await expect(page.getByTestId("devices")).toContainText("this device");
  await page.screenshot({ path: shot("settings") });
});

test("the persona switches from Settings' dropdown and by the spoken command (WO-5.6)", async () => {
  await page.keyboard.press("9");
  const select = page.getByTestId("persona-select");
  await expect(select).toHaveValue("gt-veteran"); // D60's default
  await expect(select.locator("option")).toHaveCount(3);
  await select.selectOption("ship-ai");
  await expect(select).toHaveValue("ship-ai");
  // "persona coop buddy" as a transcript would arrive: the router's instant command (no model
  // call); the core pushes the new state and the dropdown follows.
  await page.getByTestId("ask-input").fill("persona coop buddy");
  await page.getByTestId("ask-send").click();
  await expect(select).toHaveValue("coop-buddy");
  await page.getByTestId("persona-settings").screenshot({ path: shot("settings-persona") });
  await select.selectOption("gt-veteran"); // as it was for the tests after this one
  await expect(select).toHaveValue("gt-veteran");
});

test("Costs shows the cycle against the included credits", async () => {
  await page.keyboard.press("8");
  await expect(page.getByTestId("spent")).toHaveText("$0.00");
  await expect(page.getByTestId("credits")).toContainText("of $100.00 included");
  await expect(page.getByTestId("by-lane")).toBeVisible();
});

test("every view reads at WCAG contrast in every theme, on monitor 2 and on the phone", async () => {
  // What the tests above left: answers on Now and in Chat, an item in Search (its recipe card,
  // chips and all), doctor's output and the health card in Settings, the cycle in Costs.
  const views: [string, string][] = [
    ["now", "now-answer"],
    ["chat", "chat-log"],
    ["search", "search-detail"],
    ["costs", "by-lane"],
    ["settings", "verify"],
    ["designer", "view-designer"], // WO-3.7: its empty state or the latest design (e2e/designer.spec.ts)
  ];
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    for (const [view, ready] of views) {
      if (size === PHONE && view === "designer") continue; // not one of the phone's tabs
      await page.evaluate((v) => {
        window.location.hash = `/${v}`;
      }, view);
      await expect(page.getByTestId("view")).toHaveAttribute("data-view", view);
      await expect(page.getByTestId(ready)).toBeVisible();
      if (view === "search") {
        const detail = page.getByTestId("search-detail");
        await detail.getByRole("tab", { name: "Recipes" }).click();
        await expect(detail.locator('[data-card="recipe"] li.recipe').first()).toBeVisible();
      }
      if (view === "settings") await expect(page.locator('[data-card="health"]')).toBeVisible();
      for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/${view}/${slug}`] = found;
    }
  }
  // A card opened full size, over the page.
  await page.setViewportSize(MONITOR_2);
  await page.evaluate(() => {
    window.location.hash = "/chat";
  });
  const recipeAnswers = page.getByTestId("chat-log").locator("[data-answer]");
  await recipeAnswers.filter({ has: page.locator('[data-card="recipe"]') }).first().getByTestId("open-card").click();
  await expect(page.getByTestId("card-modal").locator('[data-card="recipe"]')).toBeVisible();
  for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`2560/card-modal/${slug}`] = found;
  await page.getByTestId("modal-close").click();
  expect(misses).toEqual({});
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
  const now = await themeMisses(page); // the alerts and state chips in every theme
  await page.keyboard.press("2");
  await expect(page.locator('button[data-cite^="r:"]').first()).toContainText("data stale");
  const chat = await themeMisses(page); // the "data stale" badges in every theme
  expect({ now, chat }).toEqual({ now: {}, chat: {} });
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
  expect(await themeMisses(page)).toEqual({});
  await page.screenshot({ path: shot("glance") });
  // Clickable (D49): a click opens its view, and the pointer coming in means workbench.
  await page.getByTestId("glance-answer").getByRole("button", { name: /Open/ }).click();
  await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "workbench");
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "chat");
  control("foreground", "chrome.exe");
  control("running", "[]");
});

test("a held glance always has a way back: Esc, a glance block, or the header's button", async () => {
  const hold = async (): Promise<void> => {
    await page.keyboard.press("9");
    await page.getByTestId("mode-glance").click();
    await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "glance");
  };
  // Held, glance stays while the pointer is in the window and the game isn't running.
  await hold();
  await page.mouse.move(1200, 700);
  await page.mouse.move(1210, 710);
  await page.waitForTimeout(500); // past the 300 ms the pointer needs to count as in
  await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "glance");
  expect(await themeMisses(page)).toEqual({}); // the header's way back reads in every theme
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "workbench");
  expect(await page.evaluate(() => window.localStorage.getItem("gos.mode"))).toBe("auto");
  // A click on a glance block opens its view in the workbench.
  await hold();
  await page.getByTestId("glance-alerts").getByRole("button", { name: /Open/ }).click();
  await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "workbench");
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "now");
  // The hold outlives a reload (it is kept per device); the header's button ends it.
  await hold();
  await page.reload();
  await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "glance");
  await page.getByTestId("leave-glance").click();
  await expect(page.getByTestId("shell")).toHaveAttribute("data-mode", "workbench");
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "settings");
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
  expect(await page.getByTestId("nav").locator("button").count()).toBe(6);
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
  for (const tab of ["chat", "search", "base", "costs", "settings"]) {
    await page.getByTestId(`nav-${tab}`).click();
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", tab);
    expect(await page.evaluate(() => document.documentElement.scrollWidth), tab).toBeLessThanOrEqual(390);
    // The view itself never scrolls sideways either: a wide table scrolls in its own wrap.
    expect(await page.getByTestId("view").evaluate((v) => v.scrollWidth - v.clientWidth), tab).toBeLessThanOrEqual(0);
    expect(await clipped(page), tab).toEqual([]);
    if (tab === "chat") {
      // "what uses steel" (above) cites a long NEI ref: each chip wraps inside its card.
      const overflow = await page.evaluate(() =>
        [...document.querySelectorAll("button.gos-chip.cite")].map((chip) => {
          const card = chip.closest(".card");
          return card ? chip.getBoundingClientRect().right - card.getBoundingClientRect().right : 0;
        }),
      );
      expect(overflow.length).toBeGreaterThan(0);
      expect(overflow.filter((d) => d > 0.5)).toEqual([]);
    }
  }
  await page.getByTestId("nav-now").click();
  await page.screenshot({ path: shot("phone-now"), fullPage: true });
  expect(await contrastMisses(page)).toEqual([]);
  await page.setViewportSize({ width: 2560, height: 1440 });
});

// --- WO-2.8: no-AI mode (plan 7.16) and the credits flow (D48) ---------------------------------

async function askInChat(text: string): Promise<string> {
  const log = page.getByTestId("chat-log");
  const before = await log.locator("[data-answer]").count();
  await page.getByTestId("chat-input").fill(text);
  await page.getByTestId("chat-send").click();
  await expect(log.locator("[data-answer]")).toHaveCount(before + 1);
  return (await log.locator("[data-answer]").last().locator(".answer-text").textContent()) ?? "";
}

test("with the key revoked: instant commands, Search and recipe cards work, and AI offline shows", async () => {
  await page.keyboard.press("1");
  control("key", "missing");
  const banner = page.getByTestId("banner-ai");
  await expect(banner).toBeVisible({ timeout: 15_000 });
  await expect(banner).toContainText("no Anthropic key is stored");
  await expect(banner).toContainText("Still works: instant commands, Search, recipe cards");
  await expect(banner).toContainText("Needs the AI: design, advice");
  await expect(page.getByTestId("status-chips").locator('[data-state="ai_offline"]')).toBeVisible();
  const now = await themeMisses(page); // the banner reads in every theme
  await page.screenshot({ path: shot("no-ai-key-revoked") });
  await page.keyboard.press("2");
  expect(await askInChat("recipe for steel")).toMatch(/^Steel Ingot: /); // an instant command
  const log = page.getByTestId("chat-log");
  await expect(log.locator("[data-answer]").last().locator('[data-card="recipe"]')).toBeVisible();
  const fast = await askInChat("how do I make steel"); // a fast-lane question: its card, no model
  expect(fast).toMatch(/^Steel Ingot: .*\(AI is offline; I can do lookups\.\)$/);
  await expect(log.locator("[data-answer]").last().locator('[data-card="recipe"]')).toBeVisible();
  expect(await askInChat("what's my tier")).toMatch(/^Your tier: /);
  expect(await askInChat("show my plans")).toBe("No plans are saved yet.");
  expect(await askInChat("design a steel line for me")).toContain("needs the AI");
  await page.keyboard.press("3");
  await page.getByTestId("search-input").fill("steel");
  await page.getByTestId("search-go").click();
  await page.locator('[data-result="gregtech:gt.metaitem.01@11305"]').click();
  await expect(page.getByTestId("search-detail").locator('[data-card="recipe"] li.recipe')).toHaveCount(5);
  expect({ now }).toEqual({ now: {} });
  control("key", "stored"); // recovery: the key works again
  await expect(banner).toHaveCount(0, { timeout: 15_000 });
  await page.keyboard.press("2");
  expect(await askInChat("how do I make steel")).not.toContain("AI is offline");
});

test("simulated 80% and 100% credit use raise the banner and switch to no-AI mode", async () => {
  await page.keyboard.press("1");
  await spend(81);
  const banner = page.getByTestId("banner-credits");
  await expect(banner).toBeVisible({ timeout: 15_000 });
  await expect(banner).toHaveAttribute("data-level", "warn");
  await expect(banner).toContainText("API credits at 81%");
  await expect(banner.getByTestId("credit-billing")).toBeVisible();
  await expect(banner.getByTestId("credit-no_ai")).toBeVisible();
  await expect(banner.getByTestId("credit-stop")).toBeVisible();
  await expect(page.getByTestId("banner-ai")).toHaveCount(0); // 80% only asks
  const at80 = await themeMisses(page);
  await page.screenshot({ path: shot("credits-80") });
  await spend(20);
  await expect(banner).toHaveAttribute("data-paused", "true", { timeout: 15_000 });
  await expect(banner).toHaveAttribute("data-level", "err");
  await expect(banner).toContainText("API credits at 101%: AI paused");
  await expect(page.getByTestId("status-chips").locator('[data-state="ai_offline"]')).toBeVisible();
  const at100 = await themeMisses(page);
  await page.screenshot({ path: shot("credits-100") });
  await page.keyboard.press("2");
  expect(await askInChat("how do I make steel")).toContain("AI is offline"); // the switch
  expect(await askInChat("recipe for steel")).toMatch(/^Steel Ingot: /);
  // Open billing opens the Console and keeps the pause; Resume AI ends it for this cycle.
  await page.evaluate(() => {
    (window as unknown as { opened: unknown[] }).opened = [];
    window.open = (...args: unknown[]) => {
      (window as unknown as { opened: unknown[] }).opened.push(args[0]);
      return null;
    };
  });
  await banner.getByTestId("credit-billing").click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { opened: unknown[] }).opened)).toEqual([
    "https://console.anthropic.com/settings/billing",
  ]);
  await expect(banner).toHaveAttribute("data-paused", "true");
  await banner.getByTestId("credit-resume").click();
  await expect(banner).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByTestId("status-chips").locator('[data-state="ai_offline"]')).toHaveCount(0);
  expect(await askInChat("how do I make steel")).not.toContain("AI is offline");
  const state = await asAdmin<{ credits: { answer: string; paused: boolean } }>("/api/state");
  expect(state.credits).toMatchObject({ answer: "resume", paused: false });
  expect({ at80, at100 }).toEqual({ at80: {}, at100: {} });
});

test("nothing the page drew broke its Content-Security-Policy (no inline style or script)", () => {
  expect(refused).toEqual([]);
});
