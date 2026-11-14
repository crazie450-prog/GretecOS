// WO-10.1 end to end: the Build view frames a guide export page the core serves from
// GOS_HOME/data/guide-site (e2e/serve_core.py writes the hand-made stand-in,
// tests/fixtures/guide_site/standin.py; no GuideNH file). Inside the frame the stand-in viewer
// takes GuideNH's viewer's path (module script, .scene.gz fetched and gunzipped in the page,
// <base>, canvas, layer control, hover), and the frame's CSP refuses its fetch of /api.
// Needs Clayton's board approval (no board, D92).
import { expect, test } from "@playwright/test";
import { clipped, pair } from "./harness";

test("the Build view frames the guide export, and the frame can't reach /api", async ({ browser }) => {
  const page = await browser.newPage({ viewport: { width: 2560, height: 1440 } });
  try {
    await pair(page, "Monitor 2 (Build)");
    await page.evaluate(() => {
      window.location.hash = "/build";
    });
    await expect(page.getByTestId("view")).toHaveAttribute("data-view", "build");
    await expect(page.getByTestId("guide")).toContainText("1 page");
    const frame = page.frameLocator('[data-testid="guide-frame"]');
    const body = frame.locator("body");
    await expect(body).toHaveAttribute("data-ready", "1");
    await expect(body).toHaveAttribute("data-blocks", "26");
    await expect(body).toHaveAttribute("data-api-blocked", "1");

    const canvas = frame.locator("canvas.game-scene");
    await expect(canvas).toBeVisible();
    const box = await canvas.boundingBox();
    if (!box) throw new Error("no canvas box");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 10);
    await expect(frame.getByTestId("hover")).toHaveText(/^standin:[a-z]+@\d+ at \d, \d, \d$/);

    await frame.locator("[data-testid=layer]").fill("0");
    await expect(body).toHaveAttribute("data-layer", "0");
    expect(await clipped(page)).toEqual([]);
    await page.screenshot({ path: test.info().outputPath("build-guide.png") });
  } finally {
    await page.close();
  }
});
