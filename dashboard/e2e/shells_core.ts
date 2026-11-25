// The shell workbench's core (WO-10.10): serve_core.py --shells, WO-10.7's test home (the
// made-up block catalog, the structure fixture, the steel line and its power plan, no shell yet),
// on its own port and dashboard name.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const SHELLS_PORT = Number(process.env.GOS_E2E_SHELLS_PORT ?? 8805);
export const SHELLS_HOST = "gos-e2e-shells.localhost";
export const SHELLS_BASE = `http://${SHELLS_HOST}:${SHELLS_PORT}`;
export const SHELLS_ROOT = process.env.GOS_E2E_SHELLS_HOME ?? join(tmpdir(), `gos-e2e-shells-${SHELLS_PORT}`);
export const SHELLS_HOME = join(SHELLS_ROOT, "home"); // GOS_HOME: test_shell.make_home's

/** A call to the shells core as Clayton's `gos` commands make it (its admin token). */
export async function shellsAdmin<T>(path: string): Promise<T> {
  const token = (JSON.parse(readFileSync(join(SHELLS_HOME, "run", "core.json"), "utf-8")) as { token: string }).token;
  const response = await fetch(`http://127.0.0.1:${SHELLS_PORT}${path}`, {
    headers: { Authorization: `Bearer ${token}`, Host: `127.0.0.1:${SHELLS_PORT}` },
  });
  return (await response.json()) as T;
}

/** The newest pending pairing code of the shells core. */
export async function shellsCode(): Promise<string> {
  for (let i = 0; i < 20; i += 1) {
    const pending = await shellsAdmin<{ code: string }[]>("/api/pair/pending");
    if (pending.length) return pending[pending.length - 1].code;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("no pending pairing request");
}

/** Seconds each job stage waits (serve_core.py's design_delay), so a test can cancel one. */
export function shellsDelay(seconds: string): void {
  mkdirSync(join(SHELLS_ROOT, "e2e"), { recursive: true });
  writeFileSync(join(SHELLS_ROOT, "e2e", "design_delay"), seconds, "utf-8");
}

/** The live shell designer's label the shells core reads per request (serve_core.py's
 * shell_designer, as `[shell] designer` names it, D423): a fake's, a label with no model, or "" for none. */
export function shellsDesigner(label: string): void {
  mkdirSync(join(SHELLS_ROOT, "e2e"), { recursive: true });
  writeFileSync(join(SHELLS_ROOT, "e2e", "shell_designer"), label, "utf-8");
}
