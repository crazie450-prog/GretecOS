// WO-10.7's e2e checks, run inside build_guide.spec.ts on its already paired page (the core allows
// 10 pairing requests per 10 minutes, and the suite uses them up): the Build view lists the shell
// build e2e/serve_core.py made with the real designer (procedural, th-foundry, around WO-10.5's
// made-up steel line). Since WO-10.10 the Build view shows it in the shell workbench: a build from
// before the workbench is a one-version design, drawn in the build guide's 3D viewer from its own
// files (this core has no block catalog, so the colours are stand-ins), with the layer slider,
// per-layer materials, the whole manifest and the Lab-only paste steps. Every theme reads at
// WCAG contrast on monitor 2 and the phone, and nothing is cut off. The workbench's own flows run
// on their own core (shell_workbench.spec.ts). Needs Clayton's board approval (no board, D92).
import { expect, type Page, type TestInfo } from "@playwright/test";
import { asAdmin, clipped, themeMisses } from "./harness";

const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };

export async function checkShells(page: Page, info: TestInfo): Promise<void> {
  const listed = await asAdmin<{ shells: { id: string; theme: string | null; source: string }[] }>("/api/shells");
  expect(listed.shells).toHaveLength(1);
  expect(listed.shells[0]).toMatchObject({ theme: "th-foundry@v1", source: "procedural" });
  const designs = await asAdmin<{ designs: { current: string; versions: number }[] }>("/api/shells/designs");
  expect(designs.designs).toEqual([expect.objectContaining({ current: listed.shells[0].id, versions: 1 })]);
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await page.evaluate(() => {
      window.location.hash = "/build";
    });
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", "build");
    const bench = page.getByTestId("shell-workbench");
    await expect(bench.getByTestId("shell-version-list").locator("tbody tr")).toHaveCount(1);
    const scene = bench.getByTestId("shell3d-scene");
    await expect(scene).toBeVisible();
    expect(Number(await scene.getAttribute("data-faces"))).toBeGreaterThan(10);
    await expect(bench.getByTestId("shell-status")).toContainText("accepted as designed");
    const manifest = bench.getByTestId("shell3d-manifest");
    if (!(await manifest.evaluate((d) => (d as HTMLDetailsElement).open))) await manifest.locator("summary").click();
    await expect(manifest).toContainText("gregtech:gt.blockconcretes@3");
    await bench.getByTestId("shell3d-layer").fill("0");
    await expect(bench.getByTestId("shell3d-layer-materials")).toContainText("Layer 1: materials");
    const paste = bench.getByTestId("shell-paste");
    if (!(await paste.evaluate((d) => (d as HTMLDetailsElement).open))) await paste.locator("summary").click();
    await expect(paste).toContainText("Lab only");
    const yaw = await scene.getAttribute("data-yaw");
    await bench.getByTestId("shell3d-turn-left").click();
    await expect(scene).not.toHaveAttribute("data-yaw", yaw ?? "");
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/shells/${slug}`] = found;
    expect(await clipped(page)).toEqual([]);
    await page.screenshot({ path: info.outputPath(`shells-${size.width}.png`), fullPage: true });
  }
  expect(misses).toEqual({});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  await page.setViewportSize(MONITOR_2);
}
