// The designed states (plan 7.14 rule 4) as status chips for the header and banners: loading,
// core offline, AI offline, stale data, game not running, paused, OC disconnected, export running.
// Each has a glyph and a word, never colour alone (design system: iconography).
import type { CoreState } from "../api/types";

export type StateId =
  | "loading"
  | "core_offline"
  | "ai_offline"
  | "stale"
  | "game_not_running"
  | "paused"
  | "oc_disconnected"
  | "export_running";

export type Tone = "ok" | "warn" | "err" | "info";

export interface StateChip {
  id: StateId;
  glyph: string;
  tone: Tone;
  label: string;
  detail: string;
}

export const GLYPH = {
  warn: "▲", // ▲ warning or error
  ok: "●", // ● OK, live, listening
  done: "✓", // ✓
  progress: "◐", // ◐ in progress
  todo: "○", // ○
  paused: "◼", // ◼ paused or stopping
  failed: "✕", // ✕ failed or missing
  waiting: "◌", // ◌ waiting for something outside GOS
} as const;

export interface StateInputs {
  phase: "loading" | "unpaired" | "ready";
  reachable: boolean;
  core: CoreState | null;
}

export function stateChips(i: StateInputs): StateChip[] {
  const out: StateChip[] = [];
  if (!i.reachable) {
    out.push({ id: "core_offline", glyph: GLYPH.warn, tone: "err", label: "Core offline", detail: "Reconnecting to the core" });
  } else if (i.core === null) {
    out.push({ id: "loading", glyph: GLYPH.progress, tone: "info", label: "Loading", detail: "Waiting for the core's first update" });
  }
  const c = i.core;
  if (c === null) return out;
  if (c.game.export !== null) {
    out.push({ id: "export_running", glyph: GLYPH.progress, tone: "info", label: "Export running", detail: c.game.export.reason });
  }
  if (c.game.paused === true) {
    out.push({ id: "paused", glyph: GLYPH.paused, tone: "warn", label: "Game paused", detail: "Esc menu open: GTNH stops while paused" });
  } else if (c.game.running !== null && c.game.running.length === 0 && c.game.export === null) {
    out.push({ id: "game_not_running", glyph: GLYPH.waiting, tone: "info", label: "Game not running", detail: "Start Play to see live data" });
  }
  if (c.data.stale === true) {
    out.push({ id: "stale", glyph: GLYPH.warn, tone: "warn", label: "Data stale", detail: c.data.line ?? "Recipe data doesn't match Play" });
  }
  if (!c.ai.online) {
    out.push({ id: "ai_offline", glyph: GLYPH.warn, tone: "warn", label: "AI offline", detail: `Lookups only: ${c.ai.detail}` });
  }
  if (c.oc.status === "disconnected") {
    out.push({ id: "oc_disconnected", glyph: GLYPH.failed, tone: "err", label: "OC disconnected", detail: c.oc.detail });
  }
  return out;
}

/** Whether game numbers on screen may be out of date (the stale flag, plan 7.14 rule 2). */
export function isStale(core: CoreState | null): boolean {
  return core?.data.stale === true;
}
