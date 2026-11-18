// WO-10.7 end to end: the Build view lists the shell build e2e/serve_core.py made with the real
// designer (procedural, th-foundry, around WO-10.5's made-up steel line) and draws its card from the
// core: the isometric preview, the palette manifest, the Lab-only paste steps; the view turns.
// Every theme reads at WCAG contrast on monitor 2 and the phone, and nothing is cut off.
// Needs Clayton's board approval (no board, D92).
import { expect, test } from "@playwright/test";
import { asAdmin, clipped, pair, themeMisses } from "./harness";

const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };

test("the Build view shows the saved shell with its preview, palette and paste steps", async ({ browser }) => {
  const listed = await asAdmin<{ shells: { id: string; theme: string | null; source: string }[] }>("/api/shells");
  expect(listed.shells).toHaveLength(1);
  expect(listed.shells[0]).toMatchObject({ theme: "th-foundry@v1", source: "procedural" });
  const page = await browser.newPage({ viewport: MONITOR_2 });
  try {
    await pair(page, "Monitor 2 (Shells)");
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
      await shells.getByTestId("shell-paste").locator("summary").click();
      await expect(shells.getByTestId("shell-paste")).toContainText("Lab only");
      const before = await preview.getAttribute("aria-label");
      await shells.getByTestId("shell-turn").click();
      await expect(preview).not.toHaveAttribute("aria-label", before ?? "");
      for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/shells/${slug}`] = found;
      expect(await clipped(page)).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`shells-${size.width}.png`), fullPage: true });
    }
    expect(misses).toEqual({});
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(PHONE.width);
  } finally {
    await page.close();
  }
});
