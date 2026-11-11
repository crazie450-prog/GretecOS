// What the e2e tests share: where the harness core runs, its admin token (for pairing codes,
// as `gos pair` reads them), the desktop controls it reads, and the in-page checks.
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";

const REPO = fileURLToPath(new URL("../..", import.meta.url));

export const PORT = Number(process.env.GOS_E2E_PORT ?? 8795);
export const HOST = "gos-e2e.localhost";
export const BASE = `http://${HOST}:${PORT}`;
export const HOME = process.env.GOS_E2E_HOME ?? join(tmpdir(), `gos-e2e-home-${PORT}`);

function adminToken(): string {
  return (JSON.parse(readFileSync(join(HOME, "run", "core.json"), "utf-8")) as { token: string }).token;
}

/** A call to the core as Clayton's `gos` commands make it (the admin token, a loopback Host). */
export async function asAdmin<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`http://127.0.0.1:${PORT}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${adminToken()}`, Host: `127.0.0.1:${PORT}`, ...(init.headers ?? {}) },
  });
  return (await response.json()) as T;
}

/** The newest pending pairing code, as monitor 2 or `gos pair` shows it. */
export async function pendingCode(): Promise<string> {
  for (let i = 0; i < 20; i += 1) {
    const pending = await asAdmin<{ code: string }[]>("/api/pair/pending");
    if (pending.length) return pending[pending.length - 1].code;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("no pending pairing request");
}

/** Write one of the harness's desktop controls (foreground, running, key). */
export function control(name: "foreground" | "running" | "key", value: string): void {
  mkdirSync(join(HOME, "e2e"), { recursive: true });
  writeFileSync(join(HOME, "e2e", name), value, "utf-8");
}

/** Write the fingerprint state the core reads for the stale flag (WO-1.4's state.json). */
export function fingerprintState(stale: boolean): void {
  const dir = join(HOME, "data", "fingerprint");
  mkdirSync(dir, { recursive: true });
  const state = {
    format: "gos-fingerprint/1",
    stale,
    play: { fingerprint: "f".repeat(64), checked_utc: "2026-11-07T10:00:00Z", by: "pre-launch" },
    exports: [{ run_id: "20261106T120000Z", fingerprint: "e".repeat(64), recorded_utc: "2026-11-06T12:00:00Z" }],
  };
  writeFileSync(join(dir, "state.json"), JSON.stringify(state), "utf-8");
}

/** Hold the instance lock as `gos export run` does (WO-1.4, D121): a Python process takes it in
 * the harness's GOS_HOME and keeps it until the returned function closes its stdin. */
export async function holdInstanceLock(reason: string): Promise<() => Promise<void>> {
  const code = [
    "import sys",
    "from pathlib import Path",
    "from gregtec import locks",
    "lock = locks.InstanceLock(Path(sys.argv[1])).acquire('export', sys.argv[2])",
    "print('held', flush=True)",
    "sys.stdin.read()",
    "lock.release()",
  ].join("\n");
  const child = spawn("uv", ["run", "--quiet", "python", "-c", code, HOME, reason], {
    cwd: REPO,
    stdio: ["pipe", "pipe", "inherit"],
  });
  const exited = new Promise<void>((resolve) => child.on("exit", () => resolve()));
  await new Promise<void>((resolve, reject) => {
    child.stdout?.on("data", (d: Buffer) => {
      if (d.toString().includes("held")) resolve();
    });
    child.on("exit", (status) => reject(new Error(`the lock holder exited (${status}) before taking the lock`)));
  });
  return async () => {
    child.stdin?.end();
    await exited;
  };
}

/** Simulated credit use (WO-2.8): one `llm_usage` row of `usd`, written the way the lanes' meter
 * writes it, into the harness core's DB; the core's watcher reads the cycle every second. */
export async function spend(usd: number): Promise<void> {
  const code = [
    "import sys",
    "from pathlib import Path",
    "from gregtec.llm.prices import Usage",
    "from gregtec.llm.usage import UsageMeter",
    "from gregtec.store import Store",
    "store = Store(Path(sys.argv[1]) / 'data' / 'gos.sqlite')",
    "UsageMeter(store, 100.0, (80, 100)).record('fast', 'claude-haiku-5-5', Usage(), usd=float(sys.argv[2]), task='e2e')",
    "store.close()",
  ].join("\n");
  const child = spawn("uv", ["run", "--quiet", "python", "-c", code, HOME, String(usd)], {
    cwd: REPO,
    stdio: ["ignore", "inherit", "inherit"],
  });
  await new Promise<void>((resolve, reject) =>
    child.on("exit", (status) => (status === 0 ? resolve() : reject(new Error(`spend exited ${status}`)))),
  );
}

export async function pair(page: Page, name = "Monitor 2"): Promise<void> {
  await page.goto("/");
  await page.getByTestId("pair-name").fill(name);
  await page.getByTestId("pair-start").click();
  await page.getByTestId("pair-code").fill(await pendingCode());
  await page.getByTestId("pair-complete").click();
  await page.getByTestId("shell").waitFor();
}

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The boxes of the shell's regions and the visible view's panels, by test id. */
export async function layout(page: Page): Promise<Record<string, Box>> {
  return page.evaluate(() => {
    const out: Record<string, Box> = {};
    const ids = ["header", "nav", "view", "rail", "voice-strip", "slot-a", "slot-b", "slot-c", "nav-now", "nav-settings", "core-art"];
    for (const id of ids) {
      const el = document.querySelector(`[data-testid="${id}"]`);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      out[id] = { x: r.x, y: r.y, width: r.width, height: r.height };
    }
    return out;
  });
}

/** Elements in the view that cut their content off vertically (overflow hidden, or a scroll box
 * inside the view). On the phone every panel is as tall as what it holds and only the view
 * itself scrolls, so this must come back empty there. */
export async function clipped(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const view = document.querySelector('[data-testid="view"]');
    if (!view) return ["no view"];
    const out: string[] = [];
    for (const el of view.querySelectorAll("*")) {
      const style = getComputedStyle(el);
      if (style.display === "none" || style.overflowY === "visible") continue;
      if (el.scrollHeight - el.clientHeight > 1) {
        const name = typeof el.className === "string" && el.className ? el.className : el.tagName;
        out.push(`${name}: ${el.scrollHeight} px of content in ${el.clientHeight} px`);
      }
    }
    return out;
  });
}

export interface ContrastMiss {
  text: string;
  ratio: number;
  need: number;
  color: string;
  background: string;
}

export const THEMES = ["brass", "machine", "alu", "hud", "circuit", "tung", "holo", "fusion", "naq", "deep", "calm", "manual"];

/** contrastMisses of what is on screen now, in each of the 12 themes (the theme class swapped on
 * <body> as the app does, then put back), keyed by theme; empty when every theme reads. */
export async function themeMisses(page: Page): Promise<Record<string, ContrastMiss[]>> {
  const before = await page.evaluate(() => document.body.className);
  const out: Record<string, ContrastMiss[]> = {};
  try {
    for (const slug of THEMES) {
      await page.evaluate((s) => {
        document.body.className = `theme-${s}`;
      }, slug);
      await page.evaluate(() => document.fonts.ready.then(() => undefined));
      const found = await contrastMisses(page);
      if (found.length) out[slug] = found;
    }
  } finally {
    await page.evaluate((c) => {
      document.body.className = c;
    }, before);
  }
  return out;
}

/** Every visible text on the page against the colour under it (WCAG: 4.5:1, 3:1 when large).
 * Exempt, as the design system says: disabled controls and decoration (text-3), and blurred
 * spoilers. Background images are ignored; translucent backgrounds are composited. */
export async function contrastMisses(page: Page): Promise<ContrastMiss[]> {
  return page.evaluate(() => {
    type C = [number, number, number, number];
    // Computed colours come as rgb()/rgba() or, from color-mix(), color(srgb r g b / a).
    const parse = (v: string): C => {
      const rgb = /^rgba?\(([^)]+)\)$/.exec(v.trim());
      if (rgb) {
        const p = rgb[1].split(/[\s,/]+/).filter(Boolean).map(Number);
        return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
      }
      const srgb = /^color\(srgb ([\d.e-]+) ([\d.e-]+) ([\d.e-]+)(?: \/ ([\d.e-]+))?\)$/.exec(v.trim());
      if (srgb) return [Number(srgb[1]) * 255, Number(srgb[2]) * 255, Number(srgb[3]) * 255, srgb[4] ? Number(srgb[4]) : 1];
      return [0, 0, 0, 0];
    };
    const over = (f: C, b: C): C => [f[0] * f[3] + b[0] * (1 - f[3]), f[1] * f[3] + b[1] * (1 - f[3]), f[2] * f[3] + b[2] * (1 - f[3]), 1];
    const ch = (c: number) => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    const lum = (c: C) => 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2]);
    const ratio = (a: C, b: C) => {
      const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
      return (hi + 0.05) / (lo + 0.05);
    };
    const exempt = (el: Element): boolean =>
      !!el.closest("[aria-hidden='true'], .spoiler, .spoiler-text, [disabled], .later, svg");
    const misses: ContrastMiss[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const seen = new Set<Element>();
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const el = node.parentElement;
      if (!el || seen.has(el) || !node.textContent?.trim() || exempt(el)) continue;
      seen.add(el);
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      if (style.visibility !== "visible" || rect.width === 0 || rect.height === 0 || Number(style.opacity) === 0) continue;
      // The colours under the text, top first, down to the first opaque one.
      const layers: C[] = [];
      for (let up: Element | null = el; up; up = up.parentElement) {
        const bg = parse(getComputedStyle(up).backgroundColor);
        if (bg[3] > 0) layers.push(bg);
        if (bg[3] >= 1) break;
      }
      let ground: C = parse(getComputedStyle(document.body).backgroundColor);
      if (ground[3] < 1) ground = over(ground, [255, 255, 255, 1]);
      for (const layer of layers.reverse()) ground = over(layer, ground);
      const fg = over(parse(style.color), ground);
      const size = parseFloat(style.fontSize);
      const bold = Number(style.fontWeight) >= 700;
      const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
      const r = ratio(fg, ground);
      if (r < need) {
        misses.push({ text: node.textContent.trim().slice(0, 60), ratio: Math.round(r * 100) / 100, need, color: style.color, background: `rgb(${ground.slice(0, 3).map(Math.round).join(",")})` });
      }
    }
    return misses;
  });
}
