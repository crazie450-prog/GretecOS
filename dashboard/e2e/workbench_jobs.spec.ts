// WO-3.9 end to end: designs run as jobs against the real cores (serve_core.py's made-up exports).
// A design shows its stage within a second, with the time it has run and a Cancel, in the view
// and (monitor 2) in the right rail's jobs; Cancel stops it within a few seconds and nothing is
// saved; the next design runs to its result. At 2560x1440 and 390x844, for the Designer and the
// Power view. The made-up exports design in well under a second, so each stage waits
// (`design_delay`, a harness control) long enough to watch it. Needs Clayton's board approval
// (D92): the job panel is built to the design system's busy state.
// Accept and rename go ahead while a reviewed design runs (D411 (6)): `review_delay` holds its
// review outside the planner lock, as a model call does (D409). During a design without a review
// they wait for the planner lock until it has saved, and the route says so (D417 (5)):
// `design_delay` holds that design's stages inside the lock.
// Named to run after power.spec.ts (files run in name order): its power designs leave a latest
// design on the power core, whose empty ledger power.spec.ts checks first.
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { type Browser, type Page, expect, test } from "@playwright/test";
import type { DesignerView } from "../src/api/types";
import { HOME, asAdmin, clipped, control, pair, themeMisses } from "./harness";
import { POWER_BASE, POWER_HOME, powerAdmin, powerCode } from "./power";

const shot = (name: string) => test.info().outputPath(`${name}.png`);
const SIZES = { monitor: { width: 2560, height: 1440 }, phone: { width: 390, height: 844 } };

type Jobs = { running: { id: string }[]; latest: Record<string, { id: string; status: string }> };

test.describe.configure({ mode: "serial" });

function powerDelay(seconds: string): void {
  mkdirSync(join(POWER_HOME, "e2e"), { recursive: true });
  writeFileSync(join(POWER_HOME, "e2e", "design_delay"), seconds, "utf-8");
}

test.afterAll(() => {
  rmSync(join(HOME, "e2e", "design_delay"), { force: true });
  rmSync(join(HOME, "e2e", "review_delay"), { force: true });
  rmSync(join(POWER_HOME, "e2e", "design_delay"), { force: true });
});

for (const [size, viewport] of Object.entries(SIZES)) {
  test.describe(`the Designer at ${size}`, () => {
    let page: Page;

    test.beforeAll(async ({ browser }: { browser: Browser }) => {
      page = await browser.newPage({ viewport });
      await pair(page, `Design jobs (${size})`);
    });

    test.afterAll(async () => {
      control("design_delay", "0");
      control("review_delay", "0");
      await page.close();
    });

    test("a design shows its stage at once, its time and a Cancel that saves nothing", async () => {
      control("design_delay", "1.5");
      await page.goto("/#/designer");
      await expect(page.getByTestId("view-designer")).toBeVisible();
      const before = (await asAdmin<{ plans: unknown[] }>("/api/plans")).plans.length;
      await page.getByTestId("designer-target").fill("steel ingot");
      await page.getByTestId("designer-tier").selectOption("HV");
      const t0 = Date.now();
      await page.getByTestId("designer-go").click();
      const stage = page.getByTestId("designer-job-stage");
      await expect(stage).toBeVisible({ timeout: 1000 }); // within a second
      expect(Date.now() - t0).toBeLessThan(1000);
      await expect(stage).toContainText(/Building the recipe graph|Solving route|Waiting/);
      await expect(page.getByTestId("designer-job-elapsed")).toHaveText(/^\d+:\d\d$/);
      await expect(page.getByTestId("designer-go")).toBeDisabled();
      const jobs = await asAdmin<Jobs>("/api/design-jobs");
      expect(jobs.running).toHaveLength(1);
      const id = jobs.running[0].id;
      if (size === "monitor") {
        // The right rail lists the running job with its time and a Cancel.
        await expect(page.locator(`[data-job="${id}"]`).first()).toBeVisible();
        await expect(page.getByTestId(`job-cancel-${id}`)).toBeVisible();
      } else {
        expect(await clipped(page)).toEqual([]);
      }
      await page.screenshot({ path: shot(`designer-job-${size}`), fullPage: true });
      const asked = Date.now();
      await page.getByTestId("designer-cancel").click();
      await expect(page.getByTestId("designer-cancel")).toContainText("Cancelling");
      await expect(page.getByTestId("designer-notice")).toContainText("Cancelled: nothing was saved.", { timeout: 5000 });
      expect(Date.now() - asked).toBeLessThan(5000); // within a few seconds
      await expect(page.getByTestId("designer-busy")).toHaveCount(0);
      const after = await asAdmin<Jobs>("/api/design-jobs");
      expect(after.running).toEqual([]);
      expect(after.latest.designer).toMatchObject({ id, status: "cancelled" });
      expect((await asAdmin<{ plans: unknown[] }>("/api/plans")).plans.length).toBe(before);
    });

    test("the next design runs through its stages to the routes", async () => {
      control("design_delay", "0.3");
      await page.getByTestId("designer-notice").getByRole("button", { name: "Dismiss" }).click();
      await page.getByTestId("designer-go").click();
      await expect(page.getByTestId("designer-job-stage")).toBeVisible({ timeout: 1000 });
      await expect(page.getByTestId("route-1-base")).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("designer-busy")).toHaveCount(0);
      expect(page.url()).toMatch(/#\/designer\/pl-[a-z0-9]+$/);
      const latest = (await asAdmin<Jobs>("/api/design-jobs")).latest.designer;
      expect(latest.status).toBe("done");
      if (size === "phone") expect(await clipped(page)).toEqual([]);
      await page.screenshot({ path: shot(`designer-job-done-${size}`), fullPage: true });
    });

    test("a reload while a design runs follows the job again, to its routes", async () => {
      control("design_delay", "1.0");
      await page.goto("/#/designer");
      await page.getByTestId("designer-target").fill("steel ingot");
      await page.getByTestId("designer-tier").selectOption("HV");
      await page.getByTestId("designer-go").click();
      await expect(page.getByTestId("designer-job-stage")).toBeVisible({ timeout: 1000 });
      await page.reload(); // the core's state arrives only after the view mounted
      await expect(page.getByTestId("view-designer")).toBeVisible();
      expect((await asAdmin<Jobs>("/api/design-jobs")).running).toHaveLength(1);
      await expect(page.getByTestId("designer-job-stage")).toBeVisible({ timeout: 3000 });
      await expect(page.getByTestId("designer-cancel")).toBeVisible();
      await expect(page.getByTestId("designer-go")).toBeDisabled();
      await expect(page.getByTestId("designer-busy")).toHaveCount(0, { timeout: 30_000 });
      expect((await asAdmin<Jobs>("/api/design-jobs")).latest.designer.status).toBe("done");
      await expect(page.getByTestId("route-1-base")).toBeVisible();
      expect(page.url()).toMatch(/#\/designer\/pl-[a-z0-9]+$/); // the result handed to the view
    });

    test("accept and rename go ahead while a reviewed design runs, and stay when it ends (D411 (6))", async () => {
      control("design_delay", "0");
      control("review_delay", "8");
      await page.goto("/#/designer");
      await expect(page.getByTestId("route-1-base")).toBeVisible({ timeout: 10_000 });
      // The open design is steel at HV; the job designs it again (the review toggle doesn't
      // change plan ids), so it saves these same plans once its review ends.
      const pid = (await page.getByTestId("route-1-base").getAttribute("data-route")) ?? "";
      const take = ((await page.locator('[data-testid^="accept-pl-"]').first().getAttribute("data-testid")) ?? "").slice("accept-".length);
      expect(pid).toMatch(/^pl-/);
      expect(take).toMatch(/^pl-/); // a route still proposed (the monitor run accepted one)
      await page.getByTestId("designer-target").fill("steel ingot");
      await page.getByTestId("designer-tier").selectOption("HV");
      await page.getByTestId("designer-review").check();
      await page.getByTestId("designer-go").click();
      const stage = page.getByTestId("designer-job-stage");
      await expect(stage).toContainText("review", { timeout: 10_000 });
      // Design, pin and re-solve wait for the job (one per kind, D353); accept and rename don't.
      await expect(page.getByTestId("designer-go")).toBeDisabled();
      await expect(page.getByTestId("designer-resolve")).toBeDisabled();
      const name = `Steel during a review (${size})`;
      await page.getByTestId(`rename-${pid}`).click();
      await page.getByTestId(`rename-input-${pid}`).fill(name);
      await page.getByTestId(`rename-save-${pid}`).click();
      const title = page.locator(`.designer-route[data-route="${pid}"] .gos-title`);
      await expect(title).toHaveText(name);
      await page.getByTestId(`accept-${take}`).click();
      await page.getByTestId(`accept-yes-${take}`).click();
      const column = page.locator(`.designer-route[data-route="${take}"]`);
      await expect(column).toContainText("accepted");
      // Both answered while the job still ran.
      await expect(stage).toContainText("review");
      expect((await asAdmin<Jobs>("/api/design-jobs")).running).toHaveLength(1);
      if (size === "phone") expect(await clipped(page)).toEqual([]);
      await page.screenshot({ path: shot(`designer-accept-during-job-${size}`), fullPage: true });
      // The job ends on the same plans: the view keeps the new name and the accepted route.
      await expect(page.getByTestId("designer-busy")).toHaveCount(0, { timeout: 30_000 });
      expect((await asAdmin<Jobs>("/api/design-jobs")).latest.designer.status).toBe("done");
      expect(page.url()).toMatch(new RegExp(`#/designer/${pid}$`));
      await expect(title).toHaveText(name);
      await expect(column).toContainText("accepted");
      await expect(page.getByTestId(`accept-${take}`)).toHaveCount(0);
      await expect(page.getByTestId("designer-resolve")).toBeEnabled();
      const core = await asAdmin<DesignerView>(`/api/plans/${pid}/designer`);
      expect(core.routes.find((r) => r.id === pid)?.name).toBe(name);
      expect(core.routes.find((r) => r.id === take)?.status).toBe("accepted");
    });

    test("a rename during a design without a review says it will apply when the design finishes (D417 (5))", async () => {
      control("review_delay", "0");
      control("design_delay", "3"); // each stage waits inside the planner lock
      await page.goto("/#/designer");
      await expect(page.getByTestId("route-1-base")).toBeVisible({ timeout: 10_000 });
      const pid = (await page.getByTestId("route-1-base").getAttribute("data-route")) ?? "";
      expect(pid).toMatch(/^pl-/);
      await page.getByTestId("designer-target").fill("steel ingot");
      await page.getByTestId("designer-tier").selectOption("HV");
      await page.getByTestId("designer-review").uncheck(); // the last test ticked it
      await page.getByTestId("designer-go").click();
      const stage = page.getByTestId("designer-job-stage");
      await expect(stage).toBeVisible({ timeout: 1000 });
      const name = `Steel after the design (${size})`;
      await page.getByTestId(`rename-${pid}`).click();
      await page.getByTestId(`rename-input-${pid}`).fill(name);
      await page.getByTestId(`rename-save-${pid}`).click();
      // The rename waits for the job's lock: its route says so, beside the job's own panel.
      const note = page.getByTestId(`pending-${pid}`);
      await expect(note).toHaveText(/Renaming\. Will apply when the design finishes\./);
      await expect(page.locator(`.designer-route[data-route="${pid}"]`).getByTestId(`pending-${pid}`)).toBeVisible();
      await expect(stage).toBeVisible();
      expect((await asAdmin<Jobs>("/api/design-jobs")).running).toHaveLength(1);
      await note.scrollIntoViewIfNeeded();
      expect(await themeMisses(page)).toEqual({});
      if (size === "phone") {
        expect((await clipped(page)).filter((c) => !c.startsWith("react-flow"))).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
      }
      await page.screenshot({ path: shot(`designer-rename-waits-${size}`), fullPage: true });
      // The design saves, the rename applies after it, and the note goes.
      await expect(page.getByTestId("designer-busy")).toHaveCount(0, { timeout: 60_000 });
      await expect(note).toHaveCount(0);
      expect((await asAdmin<Jobs>("/api/design-jobs")).latest.designer.status).toBe("done");
      const title = page.locator(`.designer-route[data-route="${pid}"] .gos-title`);
      await expect(title).toHaveText(name);
      const core = await asAdmin<DesignerView>(`/api/plans/${pid}/designer`);
      expect(core.routes.find((r) => r.id === pid)?.name).toBe(name);
    });
  });
}

test.describe("the Power view's design job", () => {
  let page: Page;

  test.beforeAll(async ({ browser }: { browser: Browser }) => {
    page = await browser.newPage({ baseURL: POWER_BASE, viewport: SIZES.monitor });
    await page.goto("/");
    await page.getByTestId("pair-name").fill("Monitor 2 (Power jobs)");
    await page.getByTestId("pair-start").click();
    await page.getByTestId("pair-code").fill(await powerCode());
    await page.getByTestId("pair-complete").click();
    await page.getByTestId("shell").waitFor();
  });

  test.afterAll(async () => {
    powerDelay("0");
    await page.close();
  });

  for (const [size, viewport] of Object.entries(SIZES)) {
    test(`at ${size}: stage, Cancel, then a design to its options`, async () => {
      await page.setViewportSize(viewport);
      powerDelay("1.5");
      await page.goto(`${POWER_BASE}/#/power`);
      await expect(page.getByTestId("view-power")).toBeVisible();
      await page.getByTestId("power-mode-best").click();
      await page.getByTestId("power-tier").selectOption("MV"); // the fixture's generator is MV
      await page.getByTestId("power-go").click();
      const stage = page.getByTestId("power-job-stage");
      await expect(stage).toBeVisible({ timeout: 1000 });
      await expect(stage).toContainText(/Finding generators|Fuel chain|Waiting/);
      await expect(page.getByTestId("power-job-elapsed")).toHaveText(/^\d+:\d\d$/);
      if (size === "phone") expect(await clipped(page)).toEqual([]);
      await page.screenshot({ path: shot(`power-job-${size}`), fullPage: true });
      await page.getByTestId("power-cancel").click();
      await expect(page.getByTestId("power-notice")).toContainText("Cancelled: nothing was saved.", { timeout: 5000 });
      const jobs = await powerAdmin<Jobs>("/api/design-jobs");
      expect(jobs.latest.power.status).toBe("cancelled");
      powerDelay("0.2");
      await page.getByTestId("power-go").click();
      await expect(page.getByTestId("power-job-stage")).toBeVisible({ timeout: 1000 });
      await expect(page.getByTestId("option-1")).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("power-busy")).toHaveCount(0);
      expect((await powerAdmin<Jobs>("/api/design-jobs")).latest.power.status).toBe("done");
    });
  }

  test("a reload while a power design runs follows the job again, to its options", async () => {
    await page.setViewportSize(SIZES.monitor);
    powerDelay("1.0");
    await page.goto(`${POWER_BASE}/#/power`);
    await page.getByTestId("power-mode-best").click();
    await page.getByTestId("power-tier").selectOption("MV");
    await page.getByTestId("power-go").click();
    await expect(page.getByTestId("power-job-stage")).toBeVisible({ timeout: 1000 });
    await page.reload(); // the core's state arrives only after the view mounted
    await expect(page.getByTestId("view-power")).toBeVisible();
    expect((await powerAdmin<Jobs>("/api/design-jobs")).running).toHaveLength(1);
    await expect(page.getByTestId("power-job-stage")).toBeVisible({ timeout: 3000 });
    await expect(page.getByTestId("power-cancel")).toBeVisible();
    await expect(page.getByTestId("power-go")).toBeDisabled();
    // the job ends in the reloaded view and its result shows (an earlier design's options may
    // already be on the page, so wait for the end first)
    await expect(page.getByTestId("power-busy")).toHaveCount(0, { timeout: 30_000 });
    expect((await powerAdmin<Jobs>("/api/design-jobs")).latest.power.status).toBe("done");
    await expect(page.getByTestId("option-1")).toBeVisible();
  });
});
