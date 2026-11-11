// The first-run core (WO-2.9): serve_core.py --fresh, a core with an empty GOS_HOME (no gos.toml,
// no DB), a fake Prism tree and a keyring in a file, on its own port and dashboard name.
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("../..", import.meta.url));

export const FRESH_PORT = Number(process.env.GOS_E2E_FRESH_PORT ?? 8797);
export const FRESH_HOST = "gos-e2e-fresh.localhost";
export const FRESH_BASE = `http://${FRESH_HOST}:${FRESH_PORT}`;
export const FRESH_ROOT = process.env.GOS_E2E_FRESH_HOME ?? join(tmpdir(), `gos-e2e-fresh-${FRESH_PORT}`);
export const FRESH_GOS_HOME = join(FRESH_ROOT, "GregtecOS");
export const FIXTURES = join(FRESH_ROOT, "fixtures");
export const PRISM = join(FIXTURES, "PrismLauncher");

/** The newest pending pairing code of the fresh core, as `gos pair` reads it (its admin token). */
export async function freshCode(): Promise<string> {
  const token = (JSON.parse(readFileSync(join(FRESH_GOS_HOME, "run", "core.json"), "utf-8")) as { token: string }).token;
  for (let i = 0; i < 20; i += 1) {
    const response = await fetch(`http://127.0.0.1:${FRESH_PORT}/api/pair/pending`, {
      headers: { Authorization: `Bearer ${token}`, Host: `127.0.0.1:${FRESH_PORT}` },
    });
    const pending = (await response.json()) as { code: string }[];
    if (pending.length) return pending[pending.length - 1].code;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("no pending pairing request");
}

/** Run a few lines of Python in the repo's environment and parse what they print as JSON. */
export async function python<T>(code: string, ...args: string[]): Promise<T> {
  const child = spawn("uv", ["run", "--quiet", "python", "-c", code, ...args], { cwd: REPO, stdio: ["ignore", "pipe", "inherit"] });
  let out = "";
  child.stdout?.on("data", (d: Buffer) => {
    out += d.toString();
  });
  await new Promise<void>((resolve, reject) =>
    child.on("exit", (status) => (status === 0 ? resolve() : reject(new Error(`python exited ${status}`)))),
  );
  return JSON.parse(out) as T;
}
