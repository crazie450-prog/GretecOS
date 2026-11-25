// WO-10.19 (D433) review fixes, end to end in Chromium's WebGL2 (SwiftShader): the faces turned
// away from the viewer are culled without changing the picture (the same pixels as with culling
// off, from all eight turns), each viewer sends its buffers to the GPU once, and a context that is
// lost and not restored falls back to the SVG drawing (one that is restored keeps WebGL).
import { type Page, expect, test } from "@playwright/test";
import { hallScene } from "./hall";
import { buildView } from "./viewer";

/** The main viewer's picture from each of the eight turns of the turn-right button. */
async function turns(page: Page): Promise<Buffer[]> {
  const scene = page.getByTestId("shell3d-scene");
  await expect(scene).toHaveAttribute("data-renderer", "webgl", { timeout: 30_000 });
  await scene.scrollIntoViewIfNeeded();
  const shots: Buffer[] = [];
  for (let i = 0; i < 8; i++) {
    const yaw = await scene.getAttribute("data-yaw");
    if (i) {
      await page.getByTestId("shell3d-turn-right").click();
      await expect(scene).not.toHaveAttribute("data-yaw", yaw ?? "");
    }
    await page.mouse.move(0, 0);
    shots.push(await scene.screenshot());
  }
  return shots;
}

/** How many pixels differ between two PNG screenshots (decoded in the page). */
async function differing(page: Page, a: Buffer, b: Buffer): Promise<number> {
  return page.evaluate(
    async ([x, y]) => {
      const pixels = async (src: string) => {
        const img = new Image();
        img.src = `data:image/png;base64,${src}`;
        await img.decode();
        const c = document.createElement("canvas");
        c.width = img.width;
        c.height = img.height;
        const ctx = c.getContext("2d") as CanvasRenderingContext2D;
        ctx.drawImage(img, 0, 0);
        return ctx.getImageData(0, 0, c.width, c.height).data;
      };
      const [p, q] = [await pixels(x), await pixels(y)];
      if (p.length !== q.length) return Number.POSITIVE_INFINITY;
      let n = 0;
      for (let i = 0; i < p.length; i += 4) if (p[i] !== q[i] || p[i + 1] !== q[i + 1] || p[i + 2] !== q[i + 2]) n += 1;
      return n;
    },
    [a.toString("base64"), b.toString("base64")],
  );
}

test("culling the faces turned away leaves the picture the same from every turn", async ({ browser }) => {
  test.setTimeout(120_000);
  const hall = hallScene();
  const culled = await buildView(browser, hall);
  const on = await turns(culled);
  await culled.close();
  const whole = await buildView(browser, hall, (page) =>
    page.addInitScript(() => {
      const enable = WebGL2RenderingContext.prototype.enable;
      WebGL2RenderingContext.prototype.enable = function (this: WebGL2RenderingContext, cap: number) {
        if (cap !== this.CULL_FACE) enable.call(this, cap);
      };
    }),
  );
  const off = await turns(whole);
  await whole.close();
  // the same pixels, but for a stray one where two faces meet on screen (measured: 1 of 1.4 million
  // at two of the eight turns, a back face filling a rasterisation crack when it isn't culled)
  const check = await browser.newPage();
  for (let i = 0; i < 8; i++) expect(await differing(check, on[i], off[i]), `turn ${i}`).toBeLessThan(20);
  await check.close();
});

test("each WebGL viewer uploads its buffers once", async ({ browser }) => {
  const page = await buildView(browser, hallScene(), (p) =>
    p.addInitScript(() => {
      const w = window as unknown as { __uploads: number };
      w.__uploads = 0;
      const bufferData = WebGL2RenderingContext.prototype.bufferData;
      WebGL2RenderingContext.prototype.bufferData = function (this: WebGL2RenderingContext, ...a: unknown[]) {
        w.__uploads += 1;
        return (bufferData as (...b: unknown[]) => void).apply(this, a);
      } as typeof bufferData;
    }),
  );
  await expect(page.getByTestId("shell3d-scene")).toHaveAttribute("data-renderer", "webgl", { timeout: 30_000 });
  await page.waitForTimeout(1500);
  const viewers = await page.locator('[data-renderer="webgl"]').count();
  expect(viewers).toBeGreaterThan(0);
  // a vertex and an index buffer per viewer
  expect(await page.evaluate(() => (window as unknown as { __uploads: number }).__uploads)).toBe(viewers * 2);
  await page.close();
});

test("a lost context that is restored keeps WebGL; one that never comes back falls back to SVG", async ({ browser }) => {
  const page = await buildView(browser, hallScene());
  const scene = page.getByTestId("shell3d-scene");
  await expect(scene).toHaveAttribute("data-renderer", "webgl", { timeout: 30_000 });
  const lose = () =>
    page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="shell3d-scene"] canvas') as HTMLCanvasElement;
      const ext = (canvas.getContext("webgl2") as WebGL2RenderingContext).getExtension("WEBGL_lose_context");
      (window as unknown as { __ext: unknown }).__ext = ext;
      ext?.loseContext();
    });
  await lose();
  await page.waitForTimeout(500);
  await page.evaluate(() => (window as unknown as { __ext: { restoreContext: () => void } }).__ext.restoreContext());
  await page.waitForTimeout(3500);
  await expect(scene).toHaveAttribute("data-renderer", "webgl");
  await lose();
  await expect(scene).not.toHaveAttribute("data-renderer", "webgl", { timeout: 10_000 });
  await expect(scene.locator("polygon").first()).toBeAttached({ timeout: 30_000 });
  await page.close();
});
