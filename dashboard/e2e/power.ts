// The Power view's core (WO-4.6): serve_core.py --power, WO-4.2's made-up power export (a test
// gas burnt in a test combustion generator), on its own port and dashboard name.
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const POWER_PORT = Number(process.env.GOS_E2E_POWER_PORT ?? 8799);
export const POWER_HOST = "gos-e2e-power.localhost";
export const POWER_BASE = `http://${POWER_HOST}:${POWER_PORT}`;
export const POWER_HOME = process.env.GOS_E2E_POWER_HOME ?? join(tmpdir(), `gos-e2e-power-${POWER_PORT}`);

/** A call to the power core as Clayton's `gos` commands make it (its admin token). */
export async function powerAdmin<T>(path: string): Promise<T> {
  const token = (JSON.parse(readFileSync(join(POWER_HOME, "run", "core.json"), "utf-8")) as { token: string }).token;
  const response = await fetch(`http://127.0.0.1:${POWER_PORT}${path}`, {
    headers: { Authorization: `Bearer ${token}`, Host: `127.0.0.1:${POWER_PORT}` },
  });
  return (await response.json()) as T;
}

/** The newest pending pairing code of the power core. */
export async function powerCode(): Promise<string> {
  for (let i = 0; i < 20; i += 1) {
    const pending = await powerAdmin<{ code: string }[]>("/api/pair/pending");
    if (pending.length) return pending[pending.length - 1].code;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("no pending pairing request");
}
