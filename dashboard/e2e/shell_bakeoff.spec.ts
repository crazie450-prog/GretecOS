// WO-10.11 end to end: the shell bake-off panel in the Build view against a real core
// (serve_core.py --shells: WO-10.7's made-up data and two finished bake-off runs with the fake
// models). At 2560x1440 and 390x844: a run is shown blind (the entries are A and B, drawn side
// by side in the workbench's own 3D viewer with the layer slider and a materials table, and no
// model's name is on the page or in what the core sends), the version after the follow-up, a
// pick per case, and the report naming the models only after every case is rated; ratings are
// closed after that. Every theme reads at WCAG contrast, nothing is cut off, and the phone has no
// sideways scroll. Each size rates its own run. Nothing here writes a world or spends anything.
// Needs Clayton's board approval (D92): no board yet.
import { type Browser, type Page, expect, test } from "@playwright/test";
import { clipped, themeMisses } from "./harness";
import { SHELLS_BASE, shellsAdmin, shellsCode } from "./shells_core";

const SIZES = { monitor: { width: 2560, height: 1440 }, phone: { width: 390, height: 844 } };
const NAMES = ["fake-plain", "fake-ornate", "fake", "free"]; // what must stay hidden while blind

type Runs = { runs: { id: string; rated: number; rateable: number; revealed: boolean }[] };
type Blind = {
  run: { revealed: boolean; cases: { id: string; pick: string | null; entries: { letter: string }[] }[] };
  report: unknown;
};

test.describe.configure({ mode: "serial" });

for (const [size, viewport] of Object.entries(SIZES) as [keyof typeof SIZES, { width: number; height: number }][]) {
  test.describe(`the shell bake-off at ${size}`, () => {
    let page: Page;
    let run: string;

    test.beforeAll(async ({ browser }: { browser: Browser }) => {
      const listed = await shellsAdmin<Runs>("/api/shells/bakeoff");
      expect(listed.runs).toHaveLength(2);
      run = listed.runs[size === "monitor" ? 0 : 1].id; // each size rates its own run
      page = await browser.newPage({ baseURL: SHELLS_BASE, viewport });
      await page.goto("/");
      await page.getByTestId("pair-name").fill(`Shell bake-off (${size})`);
      await page.getByTestId("pair-start").click();
      await page.getByTestId("pair-code").fill(await shellsCode());
      await page.getByTestId("pair-complete").click();
      await page.getByTestId("shell").waitFor();
      await page.evaluate(() => {
        window.location.hash = "/build";
      });
      await page.getByTestId("bakeoff").waitFor();
      await page.getByTestId("bakeoff-run-pick").selectOption(run);
      await expect(page.getByTestId("bakeoff-status")).toHaveAttribute("data-run", run);
    });

    test.afterAll(async () => {
      await page.close();
    });

    test("two answers to one brief, side by side and blind", async () => {
      const panel = page.getByTestId("bakeoff");
      await expect(panel.getByTestId("bakeoff-blind")).toContainText("blind");
      await expect(panel.getByTestId("bakeoff-brief")).toContainText("foundry");
      await expect(panel.getByTestId("bakeoff-followup")).toContainText("make it taller");
      const a = panel.getByTestId("bakeoff3d-A-scene");
      const b = panel.getByTestId("bakeoff3d-B-scene");
      await expect(a).toBeVisible();
      await expect(b).toBeVisible();
      expect(Number(await a.getAttribute("data-faces"))).toBeGreaterThan(10);
      expect(Number(await b.getAttribute("data-faces"))).toBeGreaterThan(10);
      expect(Number(await a.getAttribute("data-ghosts"))).toBeGreaterThan(0); // the machines inside, as in the workbench
      // the two models did not draw the same shell
      const builds = [
        await panel.getByTestId("bakeoff3d-A-view").getAttribute("data-build"),
        await panel.getByTestId("bakeoff3d-B-view").getAttribute("data-build"),
      ];
      expect(builds[0]).toMatch(/^sh-[0-9a-f]{12}$/);
      expect(builds[0]).not.toBe(builds[1]);
      // the workbench's own viewer: the layer slider shows fewer faces at the lowest layer
      const top = Number(await a.getAttribute("data-faces"));
      await panel.getByTestId("bakeoff3d-A-layer").fill("0");
      await expect.poll(async () => Number(await a.getAttribute("data-faces"))).toBeLessThan(top);
      const materials = panel.getByTestId("bakeoff-materials");
      await expect(materials).toBeVisible();
      expect(await materials.locator("tbody tr").count()).toBeGreaterThan(0);
      await expect(materials.locator("tbody tr").first()).toHaveAttribute("data-block", /^[a-z0-9_]+:[^@\s]+@\d+$/);
      await expect(panel.getByTestId("bakeoff-blocks")).toContainText("blocks against");
      // blind: no model's name on the page, nor in what the core sends
      const text = (await panel.innerText()).toLowerCase();
      for (const name of NAMES) expect(text).not.toContain(name);
      const sent = JSON.stringify(await shellsAdmin<Blind>(`/api/shells/bakeoff/${run}`)) + JSON.stringify(await shellsAdmin<Runs>("/api/shells/bakeoff"));
      for (const name of NAMES) expect(sent).not.toContain(name);
      // and his own designs are untouched: the bake-off's shells are not among them
      const mine = JSON.stringify(await shellsAdmin<unknown>("/api/shells")) + JSON.stringify(await shellsAdmin<unknown>("/api/shells/designs"));
      for (const build of builds) expect(mine).not.toContain(build as string);
      await page.screenshot({ path: test.info().outputPath(`bakeoff-blind-${size}.png`), fullPage: true });
    });

    test("the version after the follow-up", async () => {
      const panel = page.getByTestId("bakeoff");
      const before = await panel.getByTestId("bakeoff3d-A-view").getAttribute("data-build");
      await panel.getByTestId("bakeoff-which-revise").click();
      await expect(panel.getByTestId("bakeoff3d-A-view")).not.toHaveAttribute("data-build", before ?? "");
      await expect(panel.getByTestId("bakeoff3d-B-scene")).toBeVisible();
      await expect(panel.getByTestId("bakeoff-materials")).toBeVisible();
      await panel.getByTestId("bakeoff-which-design").click();
      await expect(panel.getByTestId("bakeoff3d-A-view")).toHaveAttribute("data-build", before ?? "");
    });

    test("rate each case, then the report names the models and the ratings close", async () => {
      const panel = page.getByTestId("bakeoff");
      const reveal = panel.getByTestId("bakeoff-reveal");
      await expect(reveal).toBeDisabled();
      await expect(panel.getByTestId("bakeoff-left")).toContainText("2 cases left");
      await panel.getByTestId("bakeoff-pick-B").click();
      await expect(panel.getByTestId("bakeoff-pick-text")).toContainText("B is better");
      await expect(reveal).toBeDisabled();
      const cases = (await shellsAdmin<Blind>(`/api/shells/bakeoff/${run}`)).run.cases;
      expect(cases[0].pick).toBe("B");
      await panel.getByTestId(`bakeoff-case-${cases[1].id}`).click();
      await expect(panel.getByTestId("bakeoff-case")).toHaveAttribute("data-case", cases[1].id);
      await expect(panel.getByTestId("bakeoff3d-A-scene")).toBeVisible();
      await panel.getByTestId("bakeoff-pick-tie").click();
      await expect(panel.getByTestId("bakeoff-pick-text")).toContainText("a tie");
      await expect(panel.getByTestId("bakeoff-left")).toContainText("every case is rated");
      // rated, not yet named: the page and the core still say nothing
      expect((await panel.innerText()).toLowerCase()).not.toContain("fake-plain");
      const still = await shellsAdmin<Blind>(`/api/shells/bakeoff/${run}`);
      expect(still.report).toBeNull();
      expect(JSON.stringify(still)).not.toContain("fake-plain");
      await page.screenshot({ path: test.info().outputPath(`bakeoff-rated-${size}.png`), fullPage: true });
      await reveal.click();
      const report = panel.getByTestId("bakeoff-report");
      await expect(report).toBeVisible();
      await expect(report).toContainText("fake-plain");
      await expect(report).toContainText("fake-ornate");
      await expect(report).toContainText("free (fake)");
      await expect(panel.getByTestId("bakeoff-blind")).toContainText("named");
      await expect(panel.getByTestId("bakeoff-pick-tie")).toBeDisabled();
      const after = await shellsAdmin<Blind>(`/api/shells/bakeoff/${run}`);
      expect(after.run.revealed).toBe(true);
      expect(after.run.cases.map((c) => c.pick)).toEqual(["B", "tie"]);
      await report.scrollIntoViewIfNeeded();
      await page.screenshot({ path: test.info().outputPath(`bakeoff-report-${size}.png`), fullPage: true });
    });

    test("every theme reads, nothing is cut off, no sideways scroll", async () => {
      const misses = await themeMisses(page);
      expect(misses).toEqual({});
      expect(await clipped(page)).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    });
  });
}
