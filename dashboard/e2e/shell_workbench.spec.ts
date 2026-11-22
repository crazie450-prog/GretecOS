// WO-10.10 end to end: the shell workbench in the Build view against a real core on WO-10.7's
// made-up data (serve_core.py --shells: the test block catalog, the structure fixture, the steel
// line and its power plan, and one foundry shell from before the workbench). At 2560x1440 and 390x844: Design a new shell (a job),
// the 3D viewer's free turn, zoom, layer slider and per-layer materials with the machines and
// keep-outs as ghosts, Revise it from words (a new version through the same gate), a refused
// revision that writes nothing, Compare two versions side by side, Make current (undo), and a
// Cancel that saves nothing. Every theme reads at WCAG contrast, nothing is cut off, and the
// phone has no sideways scroll. Nothing here writes a world: the .schematic is `gos shell export`.
// Needs Clayton's board approval (D92): no board yet.
import { type Browser, type Page, expect, test } from "@playwright/test";
import { clipped, themeMisses } from "./harness";
import { SHELLS_BASE, shellsAdmin, shellsCode, shellsDelay } from "./shells_core";

const SIZES = { monitor: { width: 2560, height: 1440 }, phone: { width: 390, height: 844 } };
// Each size revises with its own words, so the second run's revision is a new version too.
const WORDS = { monitor: "make the roof steeper", phone: "no lights" };
const CANCELLED = { monitor: "no lights", phone: "fewer pillars" };

type Designs = { designs: { id: string; versions: number; current: string }[]; current: string | null };
type Design = { design: { id: string; current: string; versions: { n: number; build: string }[] } };

test.describe.configure({ mode: "serial" });

test.afterAll(() => shellsDelay("0"));

async function versionRows(page: Page): Promise<number> {
  return page.getByTestId("shell-version-list").locator("tbody tr").count();
}

for (const [size, viewport] of Object.entries(SIZES) as [keyof typeof SIZES, { width: number; height: number }][]) {
  test.describe(`the shell workbench at ${size}`, () => {
    let page: Page;

    test.beforeAll(async ({ browser }: { browser: Browser }) => {
      page = await browser.newPage({ baseURL: SHELLS_BASE, viewport });
      await page.goto("/");
      await page.getByTestId("pair-name").fill(`Shell workbench (${size})`);
      await page.getByTestId("pair-start").click();
      await page.getByTestId("pair-code").fill(await shellsCode());
      await page.getByTestId("pair-complete").click();
      await page.getByTestId("shell").waitFor();
    });

    test.afterAll(async () => {
      await page.close();
    });

    test("design a shell, then turn, zoom and step through its layers with the ghosts", async () => {
      test.setTimeout(120_000);
      shellsDelay("0");
      await page.evaluate(() => {
        window.location.hash = "/build";
      });
      const bench = page.getByTestId("shell-workbench");
      await expect(bench).toBeVisible();
      await bench.getByTestId("shell-plan").selectOption("pl-steel");
      await bench.getByTestId("shell-theme").selectOption("th-marble-works"); // a new design
      await bench.getByTestId("shell-design").click();
      await expect(bench.getByTestId("shell-notice")).toContainText("Designed", { timeout: 30_000 });
      await expect(bench.getByTestId("shell-version-list")).toBeVisible();
      await expect(bench.getByTestId("shell-current-mark")).toHaveCount(1);
      const scene = bench.getByTestId("shell3d-scene");
      await expect(scene).toBeVisible();
      expect(Number(await scene.getAttribute("data-faces"))).toBeGreaterThan(10);
      expect(Number(await scene.getAttribute("data-ghosts"))).toBeGreaterThan(0);
      // the layer slider: the lowest layer shows fewer faces and its own materials
      const top = Number(await scene.getAttribute("data-faces"));
      await bench.getByTestId("shell3d-layer").fill("0");
      await expect.poll(async () => Number(await scene.getAttribute("data-faces"))).toBeLessThan(top);
      const mats = bench.getByTestId("shell3d-layer-materials");
      await expect(mats).toContainText("Layer 1: materials");
      expect(await mats.locator("tr[data-block]").count()).toBeGreaterThan(0);
      await expect(mats.locator("tr[data-block]").first()).toHaveAttribute("data-block", /^[a-z0-9_]+:[^@\s]+@\d+$/);
      const yaw = await scene.getAttribute("data-yaw");
      await bench.getByTestId("shell3d-turn-right").click();
      await expect(scene).not.toHaveAttribute("data-yaw", yaw ?? "");
      await bench.getByTestId("shell3d-zoom-in").click();
      await expect(scene).toHaveAttribute("data-zoom", "1.25");
      await bench.getByTestId("shell3d-ghosts").click();
      await expect(scene).toHaveAttribute("data-ghosts", "0");
      await bench.getByTestId("shell3d-ghosts").click();
      await page.screenshot({ path: test.info().outputPath(`workbench-design-${size}.png`), fullPage: true });
    });

    test("revise it from words, and a refused revision writes nothing", async () => {
      const bench = page.getByTestId("shell-workbench");
      const before = await versionRows(page);
      await bench.getByTestId("shell-words").fill(WORDS[size]);
      await bench.getByTestId("shell-revise").click();
      await expect(bench.getByTestId("shell-notice")).toContainText("New version", { timeout: 30_000 });
      await expect.poll(() => versionRows(page)).toBe(before + 1);
      const first = bench.getByTestId("shell-version-list").locator("tbody tr").first();
      await expect(first.getByTestId("shell-current-mark")).toBeVisible();
      await expect(first).toContainText(WORDS[size]);
      await expect(first).toContainText("free");
      const listed = await shellsAdmin<Designs>("/api/shells/designs");
      const count = listed.designs[0].versions;
      await bench.getByTestId("shell-words").fill("change the roof to gothic");
      await bench.getByTestId("shell-revise").click();
      await expect(bench.getByTestId("shell-notice")).toContainText("Refused, nothing written", { timeout: 30_000 });
      expect((await shellsAdmin<Designs>("/api/shells/designs")).designs[0].versions).toBe(count);
    });

    test("compare two versions side by side, then make the older one current", async () => {
      const bench = page.getByTestId("shell-workbench");
      const rows = bench.getByTestId("shell-version-list").locator("tbody tr");
      const newest = Number(await rows.first().getAttribute("data-version"));
      const older = Number(await rows.nth(1).getAttribute("data-version"));
      await bench.getByTestId(`shell-compare-${older}`).check();
      await bench.getByTestId(`shell-compare-${newest}`).check();
      const cmp = bench.getByTestId("shell-compare");
      await expect(cmp).toBeVisible();
      await expect(cmp.getByTestId("shell3d-a-scene")).toBeVisible();
      await expect(cmp.getByTestId("shell3d-b-scene")).toBeVisible();
      const materials = cmp.getByTestId("shell-compare-materials");
      await expect(materials).toBeVisible();
      expect(await materials.locator("tbody tr").count()).toBeGreaterThan(0);
      await expect(cmp.getByTestId("shell-compare-blocks")).toContainText("blocks against");
      await cmp.scrollIntoViewIfNeeded();
      await page.screenshot({ path: test.info().outputPath(`workbench-compare-${size}.png`), fullPage: true });
      const ds = (await shellsAdmin<Designs>("/api/shells/designs")).current as string;
      await bench.getByTestId(`shell-make-current-${older}`).click();
      const olderRow = bench.locator(`tr[data-version="${older}"]`);
      await expect(olderRow.getByTestId("shell-current-mark")).toBeVisible();
      const got = await shellsAdmin<Design>(`/api/shells/designs/${ds}`);
      expect(got.design.versions.find((v) => v.build === got.design.current)?.n).toBe(older);
      expect(got.design.versions.map((v) => v.n)).toContain(newest); // nothing is deleted
      await expect(bench.getByTestId("shell3d-view")).toHaveAttribute("data-build", got.design.current);
    });

    test("a revision cancelled while it runs saves nothing", async () => {
      const bench = page.getByTestId("shell-workbench");
      const count = (await shellsAdmin<Designs>("/api/shells/designs")).designs[0].versions;
      shellsDelay("1.5");
      await bench.getByTestId("shell-words").fill(CANCELLED[size]);
      await bench.getByTestId("shell-revise").click();
      const stage = bench.getByTestId("shell-job-stage");
      await expect(stage).toBeVisible({ timeout: 5000 });
      await expect(bench.getByTestId("shell-job-elapsed")).toHaveText(/^\d+:\d\d$/);
      await bench.getByTestId("shell-cancel").click();
      await expect(bench.getByTestId("shell-notice")).toContainText("Cancelled: nothing was saved", { timeout: 20_000 });
      shellsDelay("0");
      expect((await shellsAdmin<Designs>("/api/shells/designs")).designs[0].versions).toBe(count);
      const misses = await themeMisses(page);
      expect(misses).toEqual({});
      expect(await clipped(page)).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
      await page.screenshot({ path: test.info().outputPath(`workbench-${size}.png`), fullPage: true });
    });
  });
}
