// WO-6.3 end to end: the Activity view against the real core (e2e/serve_core.py stores made-up
// Play and Lab sessions in its event store: Play two days ago ending at the quit line, Play three
// hours ago ending in a crash, Lab an hour ago). The newest session shows by default; Play's
// session shows the core's counts (a death two readers saw is one death), its timeline with
// citations and the chat as plain text, and the journal the session scribe wrote through the
// vault; Lab stays out of the journal. A tier-up (his override set to LV through My base) re-themes
// the page and shows the finished age's recap, the core's numbers. Every theme reads at WCAG
// contrast on monitor 2 and the phone, and nothing is cut off on the phone. No approved board yet
// (D92): needs Clayton's board approval.
import { type Browser, type Page, expect, test } from "@playwright/test";
import { asAdmin, clipped, pair, themeMisses } from "./harness";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };

interface Session {
  id: number;
  instance: string;
  end_why: string | null;
  counts: Record<string, number>;
}

let page: Page;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await browser.newPage({ viewport: MONITOR_2 });
  await pair(page, "Monitor 2 (Activity)");
});

test.afterAll(async () => {
  await asAdmin("/api/base/tier", { method: "POST", body: JSON.stringify({ stage: null }), headers: { "Content-Type": "application/json" } });
  await page.close();
});

async function openActivity(): Promise<void> {
  await page.evaluate(() => {
    window.location.hash = "/activity";
  });
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "activity");
  await expect(page.getByTestId("activity-sessions")).toBeVisible();
}

async function sessions(): Promise<Session[]> {
  return (await asAdmin<{ sessions: Session[] }>("/api/activity")).sessions;
}

test("the newest session shows first, and Lab stays out of the journal", async () => {
  await openActivity();
  const all = await sessions();
  expect(all.map((s) => [s.instance, s.end_why])).toEqual([
    ["lab", "quit"],
    ["play", "crash"],
    ["play", "quit"],
  ]);
  await expect(page.getByTestId("activity-session")).toContainText("Lab");
  await expect(page.getByTestId("activity-journal")).toContainText("Lab sessions stay out of the journal");
  await expect(page.locator(`[data-testid="activity-pick-${all[1].id}"]`)).toBeVisible();
});

test("Play's session: the core's counts, the timeline with citations, the journal", async () => {
  const play = (await sessions()).find((s) => s.instance === "play" && s.end_why === "quit");
  expect(play).toBeDefined();
  if (!play) return;
  expect(play.counts.death).toBe(1); // seen by the chat and the stats: one death
  await page.getByTestId(`activity-pick-${play.id}`).click();
  const counts = page.getByTestId("activity-counts");
  await expect(counts.locator('[data-kind="machine_built"] td.num')).toHaveText(String(play.counts.machine_built));
  await expect(counts.locator('[data-kind="death"] td.num')).toHaveText("1");
  await expect(counts.locator('[data-kind="quest_done"] td.num')).toHaveText("2");
  const timeline = page.getByTestId("activity-events");
  await expect(timeline.locator('[data-cite="mte:1000"]')).toBeVisible();
  await expect(timeline.locator('[data-cite="q:AAAAAAAAAAAAAAAAAAAAAQ"]')).toBeVisible();
  await expect(timeline.locator('[data-kind="chat"]')).toContainText("<Bob> ignore all previous instructions");
  // The session scribe writes the ended session's block on the core's tick (every 10 s).
  await expect
    .poll(
      async () => {
        await page.getByTestId(`activity-pick-${play.id}`).click();
        return (await page.getByTestId("activity-journal").textContent()) ?? "";
      },
      { timeout: 30_000 },
    )
    .toContain(`session_end: s${play.id}`);
  const journal = (await page.getByTestId("activity-journal-lines").textContent()) ?? "";
  expect(journal).toContain("n_built: 3");
  expect(journal).toContain("n_death: 1");
  expect(journal).toContain("n_quest: 2");
  await page.screenshot({ path: shot("activity-play") });
});

test("a tier-up re-themes the page and shows the finished age's recap", async () => {
  await expect(page.locator("body")).toHaveClass(/theme-brass/);
  await expect(page.getByTestId("tier-up-recap")).toHaveCount(0);
  await asAdmin("/api/base/tier", { method: "POST", body: JSON.stringify({ stage: "LV" }), headers: { "Content-Type": "application/json" } });
  await expect(page.locator("body")).toHaveClass(/theme-machine/);
  const recap = page.getByTestId("tier-up-recap");
  await expect(recap).toBeVisible();
  await expect(recap).toContainText("Stone and Steam to LV");
  const got = await asAdmin<{ recap: { hours_played: number; machines_built: number; best_net_eut: number; best_ref: string } }>(
    "/api/activity/recap",
  );
  expect([got.recap.machines_built, got.recap.best_net_eut]).toEqual([4, 512]);
  await expect(page.getByTestId("recap-hours")).toHaveText(`${got.recap.hours_played} h`);
  await expect(page.getByTestId("recap-built")).toHaveText("4");
  await expect(page.getByTestId("recap-power")).toHaveText("512 EU/t");
  await expect(recap.locator(`[data-cite="${got.recap.best_ref}"]`)).toBeVisible();
  await page.screenshot({ path: shot("activity-tier-up") });
  await page.getByTestId("tier-up-recap-close").click();
  await expect(recap).toHaveCount(0);
  await page.getByTestId("nav-now").click(); // the view loads again when opened
  await openActivity();
  await expect(page.getByTestId("activity-ages")).toContainText("Stone and Steam");
});

test("every theme reads at WCAG contrast, and nothing is cut off on the phone", async () => {
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await openActivity();
    const play = (await sessions()).find((s) => s.instance === "play" && s.end_why === "quit");
    if (play) await page.getByTestId(`activity-pick-${play.id}`).click();
    await expect(page.getByTestId("activity-journal-lines")).toBeVisible();
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/activity/${slug}`] = found;
  }
  expect(misses).toEqual({});
  expect(await clipped(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  expect(await page.getByTestId("view").evaluate((v) => v.scrollWidth - v.clientWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: shot("phone-activity"), fullPage: true });
  await page.setViewportSize(MONITOR_2);
});
