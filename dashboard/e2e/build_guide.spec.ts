// WO-10.8 end to end, with WO-10.1's frame: the Build view's 3D build guide draws the e2e DB's
// fixture footprint (WO-1.8's made-up furnace standing in for mte 1000, e2e/serve_core.py) from the
// core: hover label (registry name, never a numeric id), layer slider, materials per layer equal to
// the footprint's counts, a checklist that survives a reload, hatch-slot markers, turn and zoom, at
// 2560x1440 and 390x844. Then Clayton's GuideNH export (the hand-made stand-in,
// tests/fixtures/guide_site/standin.py) framed from the guide's own origin (D304, D319): the frame
// loads its module script and gunzips its scene, but can't reach the dashboard (window.parent is
// another origin, and the core refuses /api with the guide's Origin). That was WO-10.1's known gap;
// this test used to pin it open and now pins it shut.
// Needs Clayton's board approval (no board, D92).
import { expect, type Page, test } from "@playwright/test";
import { asAdmin, clipped, pair, themeMisses } from "./harness";
import { checkShells } from "./shells";

const CONTROLLER = "gregtech:gt.blockmachines@1000";
const MONITOR_2 = { width: 2560, height: 1440 };
const PHONE = { width: 390, height: 844 };

interface Guide {
  size: number[];
  palette: { block: string; count: number }[];
  cells: [number, number, number, number, number][];
  slots: { kinds: string[] }[];
  checklist: { id: string }[];
}

/** The footprint's own counts per layer, from its cells (not from the guide's layer summary). */
function countsByLayer(g: Guide): Record<number, Record<string, number>> {
  const out: Record<number, Record<string, number>> = {};
  for (const [, y, , b] of g.cells) {
    out[y] ??= {};
    if (b < 0) continue;
    const key = g.palette[b].block;
    out[y][key] = (out[y][key] ?? 0) + 1;
  }
  return out;
}

async function shownMaterials(page: Page): Promise<Record<string, number>> {
  return page.getByTestId("guide-layer-materials").evaluate((panel) =>
    Object.fromEntries(
      Array.from(panel.querySelectorAll("tr")).map((tr) => [tr.getAttribute("data-block"), Number(tr.getAttribute("data-count"))]),
    ),
  );
}

async function checkGuide(page: Page, g: Guide, size: { width: number; height: number }): Promise<void> {
  await page.setViewportSize(size);
  await page.evaluate((c) => {
    window.location.hash = `/build?guide=${encodeURIComponent(c)}`;
  }, CONTROLLER);
  await expect(page.getByTestId("view")).toHaveAttribute("data-view", "build");
  const scene = page.getByTestId("guide-scene");
  await expect(scene).toBeVisible();
  // Materials per layer equal the footprint's counts; the drawing shows layers up to the slider.
  const want = countsByLayer(g);
  for (let y = 0; y < g.size[1]; y++) {
    await page.getByTestId("guide-layer").fill(String(y));
    await expect(page.getByTestId("guide-layer-materials")).toContainText(`Layer ${y + 1}`);
    expect(await shownMaterials(page)).toEqual(want[y]);
    const drawnTop = await scene.evaluate((svg, cells) => {
      const ys = Array.from(svg.querySelectorAll("polygon")).map((p) => cells[Number(p.getAttribute("data-cell"))][1]);
      return Math.max(...ys);
    }, g.cells);
    expect(drawnTop).toBe(y);
  }
  // Hover (the phone: a tap) says the block's name and registry name.
  const face = scene.locator("polygon:not(.hatch-only)").last();
  if (size.width < 760) await face.click(); // a tap: down and up with no move
  else await face.hover();
  await expect(page.getByTestId("guide-hover")).toHaveText(/^.+ · [^\s@]+:[^\s@]+@\d+ · layer \d+ · /);
  await expect(face).toHaveClass(/hovered/);
  // Hatch-slot markers with the kinds they take.
  expect(await scene.locator("polygon.hatch-slot").count()).toBeGreaterThan(0);
  for (const s of g.slots) await expect(page.getByTestId("guide-slots")).toContainText(s.kinds[0].replace(/([a-z])([A-Z])/g, "$1 $2"));
  // Which way the controller faces (review M1): the arrow out of its front and the compass.
  await expect(page.getByTestId("guide-compass")).toBeVisible();
  await expect(page.getByTestId("guide-front-arrow")).toBeAttached();
  const north = () => page.getByTestId("guide-compass").locator('[data-letter="N"]').getAttribute("x");
  const northBefore = await north();
  // Turn and zoom.
  const yaw = await scene.getAttribute("data-yaw");
  await page.getByTestId("guide-turn-right").click();
  await expect(scene).not.toHaveAttribute("data-yaw", yaw ?? "");
  expect(await north()).not.toBe(northBefore); // the compass turned with it
  await page.getByTestId("guide-zoom-in").click();
  await expect(scene).toHaveAttribute("data-zoom", "1.25");
  if (size.width >= 760) {
    const box = await scene.boundingBox();
    if (!box) throw new Error("no scene box");
    const turned = await scene.getAttribute("data-yaw");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 10, { steps: 6 });
    await page.mouse.up();
    await expect(scene).not.toHaveAttribute("data-yaw", turned ?? "");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, -200);
    await expect(scene).not.toHaveAttribute("data-zoom", "1.25");
  }
  await page.getByTestId("guide-view-reset").click();
  expect(await clipped(page)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(size.width);
}

test("the 3D build guide, and the GuideNH frame on its own origin", async ({ browser }) => {
  test.setTimeout(240_000); // twelve themes at two sizes, then the shells' own checks
  const page = await browser.newPage({ viewport: MONITOR_2 });
  try {
    await pair(page, "Monitor 2 (Build)");
    const g = await asAdmin<Guide>(`/api/build/guide?controller=${encodeURIComponent(CONTROLLER)}`);
    expect(g.cells.length).toBeGreaterThan(0);
    await asAdmin("/api/build/guide/checklist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ controller: CONTROLLER, reset: true }),
    });

    // Opened from the picker, as from a plan's machines or My base (#/build?guide=...).
    await page.evaluate(() => {
      window.location.hash = "/build";
    });
    await page.getByTestId("guide-choose").selectOption(CONTROLLER);
    await expect(page).toHaveURL(/#\/build\?guide=gregtech%3Agt\.blockmachines%401000$/);
    await expect(page.getByTestId("guide-scene")).toBeVisible();

    const misses: Record<string, unknown> = {};
    for (const size of [MONITOR_2, PHONE]) {
      await checkGuide(page, g, size);
      for (const [slug, found] of Object.entries(await themeMisses(page))) misses[`${size.width}/guide/${slug}`] = found;
      await page.screenshot({ path: test.info().outputPath(`build-guide-${size.width}.png`), fullPage: true });
    }
    expect(misses).toEqual({});
    await page.setViewportSize(MONITOR_2);

    // The checklist persists: ticked, then the page reloaded, still ticked (the core keeps it).
    const first = page.getByTestId("guide-checklist").locator('[data-item="layer:0"]');
    await first.click(); // the box follows the core's answer, so not check(): it wants it at once
    await expect(first).toBeChecked();
    await expect(page.getByTestId("guide-checklist")).toContainText(`1 of ${g.checklist.length}`);
    await page.reload();
    await expect(page.getByTestId("guide-checklist").locator('[data-item="layer:0"]')).toBeChecked();
    await page.getByTestId("guide-check-reset").click();
    await expect(page.getByTestId("guide-checklist").locator('[data-item="layer:0"]')).not.toBeChecked();

    // WO-10.1's frame, now on the guide's own origin with a path token.
    await expect(page.getByTestId("guide")).toContainText("1 page");
    const frame = page.frameLocator('[data-testid="guide-frame"]');
    const body = frame.locator("body");
    await expect(body).toHaveAttribute("data-ready", "1");
    await expect(body).toHaveAttribute("data-blocks", "26");
    await expect(body).toHaveAttribute("data-api-blocked", "1");
    const guideFrame = page.frames().find((f) => new URL(f.url()).hostname.startsWith("guide-"));
    if (!guideFrame) throw new Error("no guide frame");
    expect(new URL(guideFrame.url()).pathname).toMatch(/^\/guide\/\d{10}-[0-9a-f]{32}\//);
    // D304's gap, closed: the parent window is another origin, and the core refuses /api from it.
    const reach = await guideFrame.evaluate(async (dashboard) => {
      let parent = "reachable";
      try {
        await window.parent.fetch("/api/state", { credentials: "same-origin" });
      } catch {
        parent = "blocked";
      }
      let api = "reachable";
      try {
        const r = await fetch(`${dashboard}/api/state`, { credentials: "include", mode: "no-cors" });
        api = r.type === "opaque" ? "opaque" : String(r.status);
      } catch {
        api = "blocked";
      }
      return { parent, api };
    }, new URL(page.url()).origin);
    expect(reach.parent).toBe("blocked");
    expect(reach.api).toBe("blocked"); // the frame's CSP (connect-src: /guide/ only)
    // Even past the CSP the core refuses /api with the guide's Origin (tests/unit/test_build_guide.py).
    // The guide host is a sibling of the dashboard's, never a child (review H1, D319): a script in the
    // frame can't plant a gos_device cookie the dashboard would then be sent.
    const dashboardHost = new URL(page.url()).hostname;
    await guideFrame.evaluate((host) => {
      for (const domain of [host, host.split(".").slice(1).join("."), ""]) {
        for (const path of ["/", "/api"]) {
          document.cookie = `gos_device=tossed;${domain ? ` domain=${domain};` : ""} path=${path}`;
        }
      }
    }, dashboardHost);
    const sent = await page.evaluate(async () => {
      const r = await fetch("/api/state", { credentials: "same-origin" });
      return r.status;
    });
    expect(sent).toBe(200);
    const cookies = await page.context().cookies(page.url());
    expect(cookies.filter((c) => c.name === "gos_device")).toHaveLength(1);

    const canvas = frame.locator("canvas.game-scene");
    await expect(canvas).toBeVisible();
    await page.getByTestId("guide-frame").scrollIntoViewIfNeeded(); // the frame sits below the guide now
    const box = await canvas.boundingBox();
    if (!box) throw new Error("no canvas box");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 - 10);
    await expect(frame.getByTestId("hover")).toHaveText(/^standin:[a-z]+@\d+ at \d, \d, \d$/);
    await frame.locator("[data-testid=layer]").fill("0");
    await expect(body).toHaveAttribute("data-layer", "0");
    expect(await clipped(page)).toEqual([]);
    // WO-10.7: the same view's Shells panel, on this page (pairing is rate-limited, e2e/shells.ts)
    await checkShells(page, test.info());
  } finally {
    await page.close();
  }
});
