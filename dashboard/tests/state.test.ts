// The shell's state logic: glance or workbench (D49), Now's ranking (D55), the designed states
// (plan 7.14 rule 4), the frame cap (rule 6) and what pushes from the core change.
import { describe, expect, it } from "vitest";
import type { Answer, CoreState } from "../src/api/types";
import type { AlertCard } from "../src/cards/types.gen";
import { helloBuild, staleBuild } from "../src/api/connection";
import { clock, countdown, pct, plain, secondsLeft, signed, usd } from "../src/state/format";
import { GLANCE_FPS, LEAVE_HOLD_MS, type ModeInputs, frameDue, modeFor } from "../src/state/mode";
import { ANSWER_FRESH_S, rankNow } from "../src/state/now";
import { isStale, stateChips } from "../src/state/states";
import { ANSWERS_KEEP, addAnswers, applyMessage, createStore, initialState } from "../src/state/store";

const playing: ModeInputs = {
  phone: false,
  pin: "auto",
  pointerInside: false,
  msSinceLeave: Infinity,
  windowFocused: false,
  gameRunning: ["play"],
  gameFocused: true,
  gamePaused: false,
};

describe("glance or workbench (D49)", () => {
  it("is glance while GTNH has focus", () => {
    expect(modeFor(playing)).toBe("glance");
    expect(modeFor({ ...playing, gameFocused: null })).toBe("glance"); // unknown focus, window not focused
  });
  it("switches to workbench as soon as the mouse comes in", () => {
    expect(modeFor({ ...playing, pointerInside: true })).toBe("workbench");
  });
  it("holds workbench briefly after the mouse leaves, then glances again", () => {
    expect(modeFor({ ...playing, msSinceLeave: LEAVE_HOLD_MS - 1 })).toBe("workbench");
    expect(modeFor({ ...playing, msSinceLeave: LEAVE_HOLD_MS + 1 })).toBe("glance");
  });
  it("is workbench when the game is closed or paused", () => {
    expect(modeFor({ ...playing, gameRunning: [] })).toBe("workbench");
    expect(modeFor({ ...playing, gamePaused: true })).toBe("workbench");
    expect(modeFor({ ...playing, gameRunning: null })).toBe("glance"); // can't tell: the focus decides
  });
  it("is workbench when the dashboard window itself has focus", () => {
    expect(modeFor({ ...playing, windowFocused: true, gameFocused: false })).toBe("workbench");
    expect(modeFor({ ...playing, windowFocused: true, gameFocused: null })).toBe("workbench");
    expect(modeFor({ ...playing, windowFocused: true, gameFocused: true })).toBe("glance");
  });
  it("has no glance mode on the phone, and a pin wins", () => {
    expect(modeFor({ ...playing, phone: true })).toBe("workbench");
    expect(modeFor({ ...playing, pin: "workbench" })).toBe("workbench");
    expect(modeFor({ ...playing, gameRunning: [], pin: "glance" })).toBe("glance");
  });
});

describe("the frame cap", () => {
  it("draws at most 30 frames a second", () => {
    expect(GLANCE_FPS).toBe(30);
    let last = -Infinity;
    let drawn = 0;
    for (let t = 0; t < 1000; t += 1000 / 120) {
      if (frameDue(last, t, GLANCE_FPS)) {
        drawn += 1;
        last = t;
      }
    }
    expect(drawn).toBeGreaterThanOrEqual(29);
    expect(drawn).toBeLessThanOrEqual(31);
  });
});

function alert(level: "warn" | "err", id = level): AlertCard {
  return { type: "alert", id, level, title: id, value: null, unit: null, body: "", source: null, ts: null, ref: null };
}

function answer(ts: number): Answer {
  return {
    id: `a${ts}`, by: "admin", request: "q", asked_ts: ts, ts, text: "t", lane: "fast", intent: "recipe",
    asked: false, card: null, target: null, refs: [], checked: true, ttft_ms: 1, total_ms: 2, calls: 1, error: null,
  };
}

describe("Now ranks by context (D55)", () => {
  const base = { alerts: [], lastAnswer: null, asking: false, nowS: 10_000, phone: false, approvals: 0 };
  it("shows next steps when nothing is wrong", () => {
    expect(rankNow(base)).toEqual({ focus: "steps", order: ["steps", "power", "jobs"] });
  });
  it("puts alerts first when something is wrong", () => {
    const r = rankNow({ ...base, alerts: [alert("err")], lastAnswer: answer(9_990) });
    expect(r.focus).toBe("alerts");
    expect(r.order.slice(0, 2)).toEqual(["alerts", "answer"]);
  });
  it("puts the last answer first right after a question", () => {
    expect(rankNow({ ...base, lastAnswer: answer(9_990) }).focus).toBe("answer");
    expect(rankNow({ ...base, asking: true }).order[0]).toBe("answer");
    const r = rankNow({ ...base, lastAnswer: answer(9_990), alerts: [alert("warn")] });
    expect(r.order.slice(0, 2)).toEqual(["answer", "alerts"]);
  });
  it("lets an old answer fall behind the steps", () => {
    const r = rankNow({ ...base, lastAnswer: answer(10_000 - ANSWER_FRESH_S - 1) });
    expect(r).toEqual({ focus: "steps", order: ["steps", "power", "jobs", "answer"] });
  });
  it("shows warnings first when there is no fresh answer", () => {
    expect(rankNow({ ...base, alerts: [alert("warn")] }).order[0]).toBe("alerts");
  });
  it("brings approvals onto the phone's Now (it has no rail)", () => {
    expect(rankNow({ ...base, phone: true, approvals: 1 }).order[0]).toBe("approvals");
    expect(rankNow({ ...base, phone: false, approvals: 1 }).order).not.toContain("approvals");
  });
});

function core(patch: Partial<CoreState> = {}): CoreState {
  return {
    ts: 0,
    game: { running: ["play"], focused: true, paused: null, export: null, error: null },
    data: { stale: false, line: null },
    ai: { online: true, detail: "stored" },
    oc: { status: "not_set_up", detail: "" },
    tier: null,
    alerts: [],
    approvals: [],
    jobs: [],
    asking: [],
    health: null,
    ...patch,
  };
}

describe("every designed state has a chip with a glyph and a word", () => {
  const ids = (c: Parameters<typeof stateChips>[0]) => stateChips(c).map((s) => s.id);
  it("loading, then nothing when all is well", () => {
    expect(ids({ phase: "loading", reachable: true, core: null })).toEqual(["loading"]);
    expect(ids({ phase: "ready", reachable: true, core: core() })).toEqual([]);
  });
  it("core offline", () => {
    expect(ids({ phase: "ready", reachable: false, core: core() })).toEqual(["core_offline"]);
  });
  it("AI offline, stale data, game not running, paused, OC disconnected, export running", () => {
    const all = core({
      ai: { online: false, detail: "not stored" },
      data: { stale: true, line: "Play changed" },
      oc: { status: "disconnected", detail: "no telemetry for 30 s" },
      game: { running: [], focused: null, paused: null, export: { holder: "export", since: "now", reason: "exporting" }, error: null },
    });
    expect(ids({ phase: "ready", reachable: true, core: all })).toEqual(["export_running", "stale", "ai_offline", "oc_disconnected"]);
    const idle = core({ game: { running: [], focused: null, paused: null, export: null, error: null } });
    expect(ids({ phase: "ready", reachable: true, core: idle })).toEqual(["game_not_running"]);
    const paused = core({ game: { running: ["play"], focused: false, paused: true, export: null, error: null } });
    expect(ids({ phase: "ready", reachable: true, core: paused })).toEqual(["paused"]);
  });
  it("never uses colour alone", () => {
    const all = stateChips({ phase: "ready", reachable: false, core: core({ ai: { online: false, detail: "x" }, data: { stale: true } }) });
    for (const chip of all) {
      expect(chip.glyph).toMatch(/^[▲●✓◐○◼✕◌]$/);
      expect(chip.label.length).toBeGreaterThan(2);
    }
  });
  it("knows when game numbers are stale", () => {
    expect(isStale(core({ data: { stale: true } }))).toBe(true);
    expect(isStale(core())).toBe(false);
    expect(isStale(null)).toBe(false);
  });
});

describe("pushes from the core", () => {
  it("apply health, state, answers, voice and the next-card hotkey", () => {
    const s = initialState;
    expect(applyMessage(s, { type: "health", data: { ts: 1, overall: "ok", items: [] } }).health?.overall).toBe("ok");
    expect(applyMessage(s, { type: "state", data: core() }).core?.ai.online).toBe(true);
    expect(applyMessage(s, { type: "ask.reply", data: answer(5) }).answers).toHaveLength(1);
    expect(applyMessage(s, { type: "voice.state", data: { state: "listening", trigger: "wake", ts: 1 } }).voice?.state).toBe("listening");
    expect(applyMessage(s, { type: "ui", action: "next_card" }).nextCard).toBe(1);
    expect(applyMessage(s, { type: "ui", action: "rm" })).toEqual({});
    expect(applyMessage(s, { type: "pong" })).toEqual({});
    expect(applyMessage(s, "nonsense")).toEqual({});
  });
  it("keep each answer once, oldest first, at most 50", () => {
    const many = Array.from({ length: 60 }, (_, i) => answer(i));
    const kept = addAnswers([], many);
    expect(kept).toHaveLength(ANSWERS_KEEP);
    expect(kept[0].ts).toBe(10);
    expect(addAnswers(kept, [answer(59)])).toHaveLength(ANSWERS_KEEP);
  });
  it("a page reloads when the core serves another build than the one it loaded (D159)", () => {
    expect(helloBuild({ type: "hello", role: "device", name: "Monitor 2", build: "ab12" })).toBe("ab12");
    expect(helloBuild({ type: "hello", build: null })).toBeNull(); // the dashboard isn't built
    expect(helloBuild({ type: "state", build: "ab12" })).toBeNull();
    expect(helloBuild("nonsense")).toBeNull();
    expect(staleBuild(null, "ab12")).toBe(false); // the first hello: what this page loaded
    expect(staleBuild("ab12", "ab12")).toBe(false); // a reconnect to the same build
    expect(staleBuild("ab12", "cd34")).toBe(true); // a deploy: reload
    expect(staleBuild("ab12", null)).toBe(false);
  });
  it("notify subscribers on every change", () => {
    const store = createStore();
    let seen = 0;
    const off = store.subscribe(() => (seen += 1));
    store.set({ phase: "ready" });
    store.set((s) => ({ nextCard: s.nextCard + 1 }));
    off();
    store.set({ phase: "unpaired" });
    expect(seen).toBe(2);
    expect(store.get().nextCard).toBe(1);
  });
});

describe("formats", () => {
  it("sign numbers with the true minus", () => {
    expect(signed(-380)).toBe("−380");
    expect(signed(210)).toBe("+210");
    expect(signed(0)).toBe("0");
    expect(plain(-5)).toBe("−5");
    expect(usd(82.5)).toBe("$82.50");
    expect(pct(82.5)).toBe("82.5%");
    expect(countdown(4)).toBe("4 s");
    expect(countdown(1799)).toBe("29:59"); // a deep-lane task paused at its cap (30 min)
    expect(countdown(-3)).toBe("0 s");
    expect(clock(new Date(2026, 10, 7, 9, 4, 5))).toEqual({ hm: "09:04", s: "05" });
    expect(secondsLeft(100, 40_500)).toBe(60);
    expect(secondsLeft(100, 200_000)).toBe(0);
  });
});
