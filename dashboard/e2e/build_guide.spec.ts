// WO-10.1 end to end: the Build view frames a guide export page the core serves from
// GOS_HOME/data/guide-site (e2e/serve_core.py writes the hand-made stand-in,
// tests/fixtures/guide_site/standin.py; no GuideNH file). Inside the frame the stand-in viewer
// takes GuideNH's viewer's path (module script, .scene.gz fetched and gunzipped in the page,
// <base>, canvas, layer control, hover), and the frame's CSP refuses its own fetch of /api.
// Known gap (D304, fixed by WO-10.8's separate origin): the frame is same-origin with the
// dashboard, so window.parent.fetch runs under the dashboard's CSP and does reach /api with the
// paired cookie. The test pins that down so the gap can't be forgotten or silently "fixed".
// Needs Clayton's board approval (no board, D92).
import { expect, test } from "@playwright/test";
import { clipped, pair } from "./harness";

test("the Build view frames the guide export; its CSP limits loads, not the parent", async ({ browser }) => {
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
    // The known gap: through the parent window the frame's scripts reach /api (D304, WO-10.8).
    const guideFrame = page.frames().find((f) => new URL(f.url()).pathname.startsWith("/guide/"));
    if (!guideFrame) throw new Error("no guide frame");
    const viaParent = await guideFrame.evaluate(async () => {
      const r = await window.parent.fetch("/api/state", { credentials: "same-origin" });
      return r.status;
    });
    expect(viaParent).toBe(200);

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
