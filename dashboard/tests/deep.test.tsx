// Deep-lane tasks in Chat (WO-2.4's `deep.task` pushes): kept once each, matched to the question
// that handed them off, and shown from queued to their result, never as checked numbers.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Answer, DeepTask } from "../src/api/types";
import { MATCH_SLACK_S, TASKS_KEEP, addTasks, deepLook, matchTasks } from "../src/state/deep";
import { GLYPH } from "../src/state/states";
import { applyMessage, initialState } from "../src/state/store";
import { Chat } from "../src/views/Chat";

afterEach(cleanup);

function task(id: string, created: number, patch: Partial<DeepTask> = {}): DeepTask {
  return {
    id,
    status: "queued",
    request: "plan a steel line",
    source: "router",
    created,
    started: null,
    ended: null,
    cap_usd: 2,
    spent_usd: 0,
    approvals: 0,
    text: null,
    error: null,
    progress: [],
    ...patch,
  };
}

function answer(id: string, askedTs: number, ts: number, handoff: Answer["handoff"]): Answer {
  return {
    id,
    by: "Monitor 2",
    request: "plan a steel line",
    asked_ts: askedTs,
    ts,
    text: "On it: the deep lane is working on that.",
    lane: "deep",
    intent: "design",
    asked: false,
    card: null,
    target: null,
    refs: [],
    checked: true,
    ttft_ms: null,
    total_ms: 5,
    calls: 0,
    error: null,
    handoff,
  };
}

describe("deep-lane tasks", () => {
  it("are kept once each, the newest version winning, at most 50", () => {
    const first = addTasks([], [task("h-1", 10), task("h-2", 20)]);
    const updated = addTasks(first, [task("h-1", 10, { status: "done", text: "Route 1 wins." })]);
    expect(updated.map((t) => t.id)).toEqual(["h-1", "h-2"]);
    expect(updated[0].status).toBe("done");
    const many = addTasks([], Array.from({ length: 60 }, (_, i) => task(`h-${i}`, i)));
    expect(many).toHaveLength(TASKS_KEEP);
    expect(many[0].id).toBe("h-10");
  });

  it("come from the core's deep.task push", () => {
    const s = { ...initialState, deep: [task("h-1", 10)] };
    const next = applyMessage(s, { type: "deep.task", data: task("h-1", 10, { status: "running" }) });
    expect(next.deep?.map((t) => t.status)).toEqual(["running"]);
  });

  it("are matched by the router's task id, or by time for the fast lane's hand-off", () => {
    const byRouter = answer("a1", 100, 101, { id: "h-r", source: "router", status: "queued" });
    const byFast = answer("a2", 200, 203, { source: "fast_lane" });
    const plain = { ...answer("a3", 300, 301, null), lane: "fast" as const };
    const tasks = [
      task("h-r", 100),
      task("h-f", 202, { source: "fast_lane" }),
      task("h-other", 500, { source: "router" }), // another process's
    ];
    const { byAnswer, loose } = matchTasks([byRouter, byFast, plain], tasks);
    expect(byAnswer.get("a1")?.id).toBe("h-r");
    expect(byAnswer.get("a2")?.id).toBe("h-f");
    expect(byAnswer.has("a3")).toBe(false);
    expect(loose.map((t) => t.id)).toEqual(["h-other"]);
  });

  it("aren't matched by time outside the ask", () => {
    const byFast = answer("a2", 200, 203, { source: "fast_lane" });
    const late = task("h-late", 203 + MATCH_SLACK_S + 1, { source: "fast_lane" });
    expect(matchTasks([byFast], [late]).byAnswer.size).toBe(0);
  });

  it("read as a glyph and a word in every state", () => {
    for (const status of ["queued", "running", "needs_approval", "done", "failed", "stopped", "offline", "later"]) {
      const look = deepLook(status);
      expect(look.glyph.length).toBeGreaterThan(0);
      expect(look.label).toMatch(/^Deep lane · /);
    }
    expect(deepLook("done").glyph).toBe(GLYPH.done);
    expect(deepLook("failed").status).toBe("err");
  });
});

describe("Chat with the deep lane", () => {
  const ask = async (): Promise<void> => undefined;

  it("shows a task's result under the question that started it, as unchecked numbers", () => {
    const a = answer("a1", 100, 101, { id: "h-r", source: "router", status: "queued" });
    const done = task("h-r", 100, { status: "done", text: "Route 1: 4 EBFs at r:gt.recipe.blastfurnace/abc." });
    render(<Chat answers={[a]} deep={[done]} core={null} onAsk={ask} openCard={() => undefined} />);
    const exchange = document.querySelector('[data-answer="a1"]');
    const result = exchange?.querySelector('[data-deep="h-r"]');
    expect(result?.getAttribute("data-status")).toBe("done");
    expect(result?.textContent).toContain("Route 1: 4 EBFs");
    expect(result?.textContent).toContain("numbers not checked");
    expect(screen.getByText("Deep lane · done")).toBeTruthy();
  });

  it("shows a running task's latest step, and a task no answer started on its own", () => {
    const running = task("h-1", 100, { status: "running", progress: ["reading recipes", "ranking routes"] });
    render(<Chat answers={[]} deep={[running]} core={null} onAsk={ask} openCard={() => undefined} />);
    const own = document.querySelector('[data-task="h-1"]');
    expect(own?.textContent).toContain("plan a steel line");
    expect(own?.textContent).toContain("ranking routes");
    expect(own?.textContent).not.toContain("reading recipes");
  });

  it("shows a failed task's error and no result", () => {
    const failed = task("h-1", 100, { status: "failed", error: "the init check failed" });
    render(<Chat answers={[]} deep={[failed]} core={null} onAsk={ask} openCard={() => undefined} />);
    expect(screen.getByText("the init check failed")).toBeTruthy();
    expect(screen.queryByText("numbers not checked")).toBeNull();
  });
});
