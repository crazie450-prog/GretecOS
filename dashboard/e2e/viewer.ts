// The shell viewer's e2e helpers (WO-10.19, D433): a paired page on the Build view whose shells
// all show a made-up scene, and WebGL2 refused for the SVG fallback.
import type { Browser, Page } from "@playwright/test";
import type { ShellScene } from "../src/api/types";
import { SHELLS_BASE, shellsCode } from "./shells_core";

/** A paired page on the Build view with every shell's 3D view answered by ``hall``; ``before``
 * runs on the page before it first loads (init scripts). */
export async function buildView(browser: Browser, hall: ShellScene, before?: (page: Page) => Promise<unknown>): Promise<Page> {
  const page = await browser.newPage({ baseURL: SHELLS_BASE, viewport: { width: 2560, height: 1440 } });
  await before?.(page);
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
  return page;
}

/** WebGL2 refused from the next load on: the SVG fallback. */
export async function refuseWebGL(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const get = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
      if (kind === "webgl2") return null;
      return (get as (...a: unknown[]) => unknown).call(this, kind, ...rest);
    } as typeof get;
  });
}

