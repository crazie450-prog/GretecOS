// WO-10.17 end to end: plan-free buildings (D424) and the live shell designer (D423) in the shell
// workbench, against the shells core (serve_core.py --shells: WO-10.7's made-up data) with the
// designer's label set per request through its e2e control file (a fake's, fake-plain: no key,
// no cost; a label with no model: not ready; none: today's buttons). Plan-free and a floor area:
// the core's plot note, Design new, a design listed "plan-free · ≥N floor" whose viewer says it
// has no machines. Design with the architect: a job drawn by fake-plain, its version "AI
// (fake-plain)"; Ask the architect revises it as a job. Not ready: both AI buttons off with the
// why. Every theme reads at WCAG contrast, nothing is cut off, and the phone has no sideways
// scroll. Nothing here spends anything or writes a world. Needs Clayton's board approval (D92).
import { type Browser, type Page, expect, test } from "@playwright/test";
import { clipped, themeMisses } from "./harness";
import { SHELLS_BASE, shellsAdmin, shellsCode, shellsDelay, shellsDesigner } from "./shells_core";

const MONITOR = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };
const AREA = 400;

type Designs = { designs: { id: string; versions: number; current: string; area: number | null }[]; current: string | null };
type Plot = { area: number; factor: number; size: [number, number, number] };

test.describe.configure({ mode: "serial" });

let page: Page;

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  shellsDelay("0");
  shellsDesigner(""); // none set: today's workbench
  page = await browser.newPage({ baseURL: SHELLS_BASE, viewport: MONITOR });
  await page.goto("/");
  await page.getByTestId("pair-name").fill("Shell designer");
  await page.getByTestId("pair-start").click();
  await page.getByTestId("pair-code").fill(await shellsCode());
  await page.getByTestId("pair-complete").click();
  await page.getByTestId("shell").waitFor();
  await page.evaluate(() => {
    window.location.hash = "/build";
  });
});

test.afterAll(async () => {
  shellsDesigner("");
  await page.close();
});

/** The workbench again, so it reads the designer's status afresh. */
async function reload(): Promise<void> {
  await page.reload();
  await page.getByTestId("shell").waitFor();
  await expect(page.getByTestId("shell-workbench")).toBeVisible();
}

test("with no designer set the workbench keeps today's buttons", async () => {
  const bench = page.getByTestId("shell-workbench");
  await expect(bench).toBeVisible();
  await expect(bench.getByTestId("shell-ask")).toBeVisible();
  await expect(bench.getByTestId("shell-ai-design")).toHaveCount(0);
  await expect(bench.getByTestId("shell-designer")).toHaveCount(0);
  expect(await shellsAdmin<{ configured: boolean }>("/api/shells/designer")).toEqual({ configured: false });
});

test("a plan-free building from its floor area: the core's plot, no plans, no machines", async () => {
  test.setTimeout(120_000);
  const bench = page.getByTestId("shell-workbench");
  await bench.getByTestId("shell-mode-free").click();
  await expect(bench.getByTestId("shell-mode-free")).toHaveAttribute("aria-pressed", "true");
  await expect(bench.getByTestId("shell-plan")).toHaveCount(0);
  await bench.getByTestId("shell-area").fill(String(AREA));
  const plot = await shellsAdmin<Plot>(`/api/shells/plot?area=${AREA}`);
  await expect(bench.getByTestId("shell-plot")).toHaveText(`plot about ${plot.size[0]} × ${plot.size[2]} at factor 2.0`);
  await bench.getByTestId("shell-theme").selectOption("th-marble-works");
  await bench.getByTestId("shell-design").click();
  await expect(bench.getByTestId("shell-notice")).toContainText("Designed", { timeout: 30_000 });
  const listed = await shellsAdmin<Designs>("/api/shells/designs");
  expect(listed.designs[0]).toMatchObject({ id: listed.current, area: AREA, versions: 1 });
  const pick = bench.getByTestId("shell-design-pick");
  await expect(pick.locator("option:checked")).toContainText(`plan-free · ≥${AREA} floor`);
  await expect(bench.getByTestId("shell-versions")).toContainText(`plan-free · ≥${AREA} floor`);
  await expect(bench.getByTestId("shell3d-plan-free")).toContainText("plan-free: no machines");
  await expect(bench.getByTestId("shell3d-ghosts")).toHaveCount(0);
  const scene = bench.getByTestId("shell3d-scene");
  expect(Number(await scene.getAttribute("data-faces"))).toBeGreaterThan(10);
  expect(Number(await scene.getAttribute("data-ghosts"))).toBe(0);
  await bench.getByTestId("shell-form").screenshot({ path: test.info().outputPath("designer-plan-free-form.png") });
  await bench.getByTestId("shell-viewer").screenshot({ path: test.info().outputPath("designer-plan-free-viewer.png") });
});

test("Design with the architect: the designer draws it as a job and its version names it", async () => {
  test.setTimeout(120_000);
  shellsDesigner("fake-plain");
  await reload();
  const bench = page.getByTestId("shell-workbench");
  await expect(bench.getByTestId("shell-designer")).toContainText("Designer: fake-plain · up to $4.00 a request");
  await expect(bench.getByTestId("shell-designer")).toHaveAttribute("data-ready", "true");
  await bench.getByTestId("shell-mode-plans").click();
  await bench.getByTestId("shell-plan").selectOption("pl-steel");
  await bench.getByTestId("shell-theme").selectOption("th-foundry");
  await bench.getByTestId("shell-words").fill("a heavy foundry");
  const before = (await shellsAdmin<Designs>("/api/shells/designs")).designs.length;
  await bench.getByTestId("shell-ai-design").click();
  await expect(bench.getByTestId("shell-notice")).toContainText("Drawn by fake-plain", { timeout: 60_000 });
  await expect(bench.getByTestId("shell-notice")).toContainText("Designed");
  expect((await shellsAdmin<Designs>("/api/shells/designs")).designs).toHaveLength(before + 1);
  const first = bench.getByTestId("shell-version-list").locator("tbody tr").first();
  await expect(first).toContainText("AI (fake-plain)");
  await expect(first).toContainText("a heavy foundry");
  await bench.getByTestId("shell-form").screenshot({ path: test.info().outputPath("designer-ready-form.png") });
  await bench.getByTestId("shell-versions").screenshot({ path: test.info().outputPath("designer-versions.png") });
});

test("Ask the architect: the designer revises the design picked here as a job", async () => {
  test.setTimeout(120_000);
  const bench = page.getByTestId("shell-workbench");
  const ds = (await shellsAdmin<Designs>("/api/shells/designs")).current as string;
  const rows = bench.getByTestId("shell-version-list").locator("tbody tr");
  const before = await rows.count();
  await bench.getByTestId("shell-theme").selectOption(""); // the designer revises from words alone
  await bench.getByTestId("shell-words").fill("make it taller");
  await bench.getByTestId("shell-ask").click();
  await expect(bench.getByTestId("shell-notice")).toContainText("Drawn by fake-plain", { timeout: 60_000 });
  await expect.poll(() => rows.count()).toBe(before + 1);
  await expect(rows.first()).toContainText("AI (fake-plain)");
  await expect(rows.first()).toContainText("make it taller");
  const got = await shellsAdmin<{ design: { id: string; versions: unknown[] } }>(`/api/shells/designs/${ds}`);
  expect(got.design.versions).toHaveLength(before + 1);
});

test("a designer that isn't ready: both AI buttons are off and the workbench says why", async () => {
  shellsDesigner("nope");
  await reload();
  const bench = page.getByTestId("shell-workbench");
  const line = bench.getByTestId("shell-designer");
  await expect(line).toContainText("Designer: nope · not ready: no [[bakeoff.models]] entry is labelled nope");
  await expect(line).toHaveAttribute("data-ready", "false");
  await expect(bench.getByTestId("shell-ai-design")).toBeDisabled();
  await expect(bench.getByTestId("shell-ask")).toBeDisabled();
  await expect(bench.getByTestId("shell-design")).toBeEnabled(); // GOS's own generator needs no AI
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR, PHONE]) {
    await page.setViewportSize(size);
    await bench.getByTestId("shell-mode-free").click();
    await bench.locator('[data-testid="shell-advanced"] summary').click();
    await expect(bench.getByTestId("shell-site-factor")).toBeVisible();
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/${slug}`] = found;
    expect(await clipped(page)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(size.width);
    await bench.getByTestId("shell-form").screenshot({ path: test.info().outputPath(`designer-not-ready-${size.width}.png`) });
    await bench.locator('[data-testid="shell-advanced"] summary').click();
    await bench.getByTestId("shell-mode-plans").click();
  }
  expect(misses).toEqual({});
  await page.setViewportSize(MONITOR);
});
