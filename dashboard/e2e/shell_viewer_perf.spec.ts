// WO-10.19 (D433) end to end: the shell viewer's speed on a big build. Against the shells core
// (serve_core.py --shells), the Build view's 3D viewer is handed a made-up ~21,000-block hall
// (e2e/hall.ts, by routing /api/shells/*/view) and turned by a mouse drag while the page records
// its frame times; then the same with WebGL2 refused (the SVG fallback), on the same build and the
// same drag. The numbers are attached to the test as evidence and logged. The check is loose
// (WebGL's mean frame and main-thread time are below the SVG's) so a software GPU on CI doesn't
// flake it. Also
// checks the WebGL viewer's hover names the block under the pointer, and that the drag turned it.
// Needs Clayton's board approval (D92): no board yet.
import { type Page, expect, test } from "@playwright/test";
import { hallScene } from "./hall";
import { SHELLS_BASE, shellsCode } from "./shells_core";

type Frames = {
  frames: number; // animation frames seen during the drag
  p50: number; // frame interval percentiles (ms)
  p95: number;
  max: number;
  mean: number;
  turns: number; // how many times the drawing turned (data-yaw changed)
  dragMs: number; // the drag's wall time
  busyMs: number; // main-thread long tasks (> 50 ms) during the drag, summed
};

/** Drag across the scene in 60 steps while the page records its frame intervals (an rAF loop),
 * every turn of the drawing (data-yaw) and the main thread's long tasks. */
async function dragFrames(page: Page): Promise<Frames> {
  const scene = page.getByTestId("shell3d-scene");
  await scene.scrollIntoViewIfNeeded();
  const box = await scene.boundingBox();
  if (!box) throw new Error("no scene box");
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.evaluate(() => {
    type Rec = { frames: number[]; on: boolean; turns: number; busy: number; t0: number; stop: () => void };
    const el = document.querySelector('[data-testid="shell3d-scene"]');
    const rec: Rec = { frames: [], on: true, turns: 0, busy: 0, t0: performance.now(), stop: () => undefined };
    (window as unknown as { __rec: Rec }).__rec = rec;
    let last = performance.now();
    const tick = (t: number) => {
      rec.frames.push(t - last);
      last = t;
      if (rec.on) requestAnimationFrame(tick);
    };
    requestAnimationFrame((t) => {
      last = t;
      requestAnimationFrame(tick);
    });
    const mo = new MutationObserver(() => {
      rec.turns += 1;
    });
    if (el) mo.observe(el, { attributes: true, attributeFilter: ["data-yaw"] });
    const po = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) rec.busy += e.duration;
    });
    po.observe({ type: "longtask" });
    rec.stop = () => {
      mo.disconnect();
      po.disconnect();
    };
  });
  await page.mouse.move(cx - 150, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 150, cy + 40, { steps: 60 });
  await page.mouse.up();
  const got = await page.evaluate(
    () =>
      new Promise<{ ms: number[]; turns: number; busy: number; dragMs: number }>((done) =>
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            const rec = (window as unknown as { __rec: { frames: number[]; on: boolean; turns: number; busy: number; t0: number; stop: () => void } }).__rec;
            rec.on = false;
            rec.stop();
            done({ ms: rec.frames, turns: rec.turns, busy: rec.busy, dragMs: performance.now() - rec.t0 });
          }),
        ),
      ),
  );
  const ms = got.ms;
  const sorted = [...ms].sort((a, b) => a - b);
  const q = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  const round = (n: number) => Math.round(n * 10) / 10;
  return {
    frames: ms.length,
    p50: round(q(0.5)),
    p95: round(q(0.95)),
    max: round(sorted[sorted.length - 1]),
    mean: round(ms.reduce((a, b) => a + b, 0) / ms.length),
    turns: got.turns,
    dragMs: Math.round(got.dragMs),
    busyMs: Math.round(got.busy),
  };
}

test("a ~21,000-block shell turns faster in WebGL than in the SVG fallback", async ({ browser }) => {
  test.setTimeout(180_000);
  const hall = hallScene();
  const page = await browser.newPage({ baseURL: SHELLS_BASE, viewport: { width: 2560, height: 1440 } });
  await page.route("**/api/shells/*/view", (route) => route.fulfill({ json: hall }));
  await page.goto("/");
  await page.getByTestId("pair-name").fill("Shell viewer speed");
  await page.getByTestId("pair-start").click();
  await page.getByTestId("pair-code").fill(await shellsCode());
  await page.getByTestId("pair-complete").click();
  await page.getByTestId("shell").waitFor();
  await page.evaluate(() => {
    window.location.hash = "/build";
  });
  const scene = page.getByTestId("shell3d-scene");

  // WebGL2 (SwiftShader in headless Chromium)
  await expect(scene).toHaveAttribute("data-renderer", "webgl", { timeout: 30_000 });
  expect(Number(await scene.getAttribute("data-faces"))).toBeGreaterThan(10_000);
  expect(Number(await scene.getAttribute("data-ghosts"))).toBe(2);
  await scene.scrollIntoViewIfNeeded();
  await scene.screenshot({ path: test.info().outputPath("viewer-webgl-first.png") });
  const box = await scene.boundingBox();
  if (!box) throw new Error("no scene box");
  await page.mouse.move(box.x + box.width / 2 - 20, box.y + box.height / 2);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page.getByTestId("shell3d-hover")).toHaveText(/^.+ · [a-z0-9_]+:[^@\s]+@\d+ · \w+ · layer \d+$/);
  const yaw = await scene.getAttribute("data-yaw");
  const webgl = await dragFrames(page);
  await expect(scene).not.toHaveAttribute("data-yaw", yaw ?? "");
  await scene.screenshot({ path: test.info().outputPath("viewer-webgl.png") });
  // the layer slider cuts the hall open: fewer faces, the cut layer's tops shown
  const whole = Number(await scene.getAttribute("data-faces"));
  await page.getByTestId("shell3d-layer").fill("8");
  await expect.poll(async () => Number(await scene.getAttribute("data-faces"))).toBeLessThan(whole);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page.getByTestId("shell3d-hover")).toContainText("layer");
  await scene.screenshot({ path: test.info().outputPath("viewer-webgl-layer9.png") });
  await page.getByTestId("shell3d-layer").fill("23");

  // the SVG fallback: WebGL2 refused, same build, same drag
  await page.addInitScript(() => {
    const get = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
      if (kind === "webgl2") return null;
      return (get as (...a: unknown[]) => unknown).call(this, kind, ...rest);
    } as typeof get;
  });
  await page.reload();
  await page.getByTestId("shell").waitFor();
  await expect(scene.locator("polygon").first()).toBeAttached({ timeout: 30_000 });
  expect(await scene.getAttribute("data-renderer")).toBeNull();
  expect(await scene.locator("polygon").count()).toBeGreaterThan(10_000);
  const svg = await dragFrames(page);
  await scene.screenshot({ path: test.info().outputPath("viewer-svg.png") });

  const evidence = { blocks: hall.cells.length, size: hall.size, drag: "60 mouse steps, 300 px", webgl, svg };
  console.log(`WO-10.19 frame times (ms): ${JSON.stringify(evidence)}`);
  await test.info().attach("frame-times", { body: JSON.stringify(evidence, null, 2), contentType: "application/json" });
  expect(webgl.mean).toBeLessThan(svg.mean);
  expect(webgl.busyMs).toBeLessThan(svg.busyMs);
  await page.close();
});
