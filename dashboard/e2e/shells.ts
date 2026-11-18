// WO-10.7's e2e checks, run inside build_guide.spec.ts on its already paired page (the core allows
// 10 pairing requests per 10 minutes, and the suite uses them up): the Build view lists the shell
// build e2e/serve_core.py made with the real designer (procedural, th-foundry, around WO-10.5's
// made-up steel line) and draws its card from the core: the isometric preview, the palette
// manifest, the Lab-only paste steps; the view turns. Every theme reads at WCAG contrast on
// monitor 2 and the phone, and nothing is cut off. Needs Clayton's board approval (no board, D92).
import { expect, type Page, type TestInfo } from "@playwright/test";
import { asAdmin, clipped, themeMisses } from "./harness";

const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };

export async function checkShells(page: Page, info: TestInfo): Promise<void> {
  const listed = await asAdmin<{ shells: { id: string; theme: string | null; source: string }[] }>("/api/shells");
  expect(listed.shells).toHaveLength(1);
  expect(listed.shells[0]).toMatchObject({ theme: "th-foundry@v1", source: "procedural" });
  const misses: Record<string, unknown> = {};
  for (const size of [MONITOR_2, PHONE]) {
    await page.setViewportSize(size);
    await page.evaluate(() => {
      window.location.hash = "/build";
    });
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", "build");
    const shells = page.getByTestId("shells");
    await expect(shells).toContainText("1 saved");
    const preview = shells.getByTestId("shell-preview");
    await expect(preview).toBeVisible();
    expect(Number(await preview.getAttribute("data-faces"))).toBeGreaterThan(10);
    await expect(shells.getByTestId("shell-status")).toContainText("accepted as designed");
    await expect(shells.getByTestId("shell-palette")).toContainText("gregtech:gt.blockconcretes@3");
    const paste = shells.getByTestId("shell-paste");
    if (!(await paste.evaluate((d) => (d as HTMLDetailsElement).open))) await paste.locator("summary").click();
    await expect(paste).toContainText("Lab only");
    const before = await preview.getAttribute("aria-label");
    await shells.getByTestId("shell-turn").click();
    await expect(preview).not.toHaveAttribute("aria-label", before ?? "");
    for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/shells/${slug}`] = found;
    expect(await clipped(page)).toEqual([]);
    await page.screenshot({ path: info.outputPath(`shells-${size.width}.png`), fullPage: true });
  }
  expect(misses).toEqual({});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  await page.setViewportSize(MONITOR_2);
}
