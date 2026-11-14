// The advisor's next steps on Now (WO-6.5): the advisor_next table card drawn as steps in GOS's
// order (never re-sorted), each a link to its plan, quest or view with its citations; a notice
// push asks the advisor again; the empty states.
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TableCard } from "../src/cards/types.gen";
import { applyMessage, initialState, store } from "../src/state/store";
import { NextSteps, advisorSteps } from "../src/views/Now";

const Q = `q:${"A".repeat(20)}Cw`;

function card(rows: string[][], notes: string[] = []): TableCard {
  return {
    type: "table",
    tool: "advisor_next",
    error: null,
    title: "advisor_next",
    head: [
      { key: "tier", value: "stone (quests)" },
      { key: "spoil", value: "0" },
    ],
    columns: ["rank", "kind", "step", "why", "refs", "link"],
    rows,
    span: null,
    total: rows.length,
    notes,
  };
}

const ROWS = [
  ["1", "fix", "Power deficit: your running lines use more than they make", "running lines use 512 EU/t, sources give 0 EU/t, net -512 EU/t (EU ledger: plan:pl-a)", "plan:pl-a", "#/power"],
  ["2", "build", "You can build Macerators from stock", "every build part is in stock (plan:pl-m); an accepted line", "plan:pl-m", "#/designer/pl-m"],
  ["3", "goal", "Quest: Bricks (Steam)", "goal: Electric Blast Furnace (5 steps); unlocks 1", Q, "#/quests"],
];

let calls: string[];
let queue: unknown[];

beforeEach(() => {
  calls = [];
  queue = [];
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    const next = queue.shift() ?? { card: card([]) };
    return new Response(JSON.stringify(next), { status: 200 });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  store.set({ notices: 0 });
});

describe("advisorSteps", () => {
  it("keeps GOS's order and splits reasons and citations", () => {
    const steps = advisorSteps(card([ROWS[2], ROWS[0]]));
    expect(steps.map((s) => s.rank)).toEqual(["3", "1"]); // never re-sorted (D266)
    expect(steps[1].why).toEqual(["running lines use 512 EU/t, sources give 0 EU/t, net -512 EU/t (EU ledger: plan:pl-a)"]);
    expect(steps[0].refs).toEqual([Q]);
    expect(steps[0].why).toEqual(["goal: Electric Blast Furnace (5 steps)", "unlocks 1"]);
  });

  it("refuses a link that isn't the dashboard's and a card of another shape", () => {
    expect(advisorSteps(card([["1", "quest", "x", "", "", "https://example.com"]]))[0].link).toBe("#/now");
    expect(advisorSteps({ ...card([]), columns: ["a"] })).toEqual([]);
  });
});

describe("NextSteps", () => {
  it("draws the three steps as links with their citations", async () => {
    queue.push({ card: card(ROWS) });
    render(<NextSteps />);
    const list = await screen.findByTestId("advisor-steps");
    const links = within(list).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["#/power", "#/designer/pl-m", "#/quests"]);
    expect(within(list).getByText("plan:pl-a")).toBeTruthy();
    expect(within(list).getByText(Q)).toBeTruthy();
    expect(calls[0]).toContain("/api/tools/advisor_next");
  });

  it("shows the advisor's own note when it has nothing to suggest", async () => {
    queue.push({ card: card([], ["nothing to suggest yet: load the quest book"]) });
    render(<NextSteps />);
    expect((await screen.findByTestId("steps-empty")).textContent).toContain("nothing to suggest yet");
  });

  it("asks the advisor again when a notice arrives", async () => {
    queue.push({ card: card(ROWS) }, { card: card([ROWS[1]]) });
    render(<NextSteps />);
    await screen.findByTestId("advisor-step-3");
    act(() => store.set(applyMessage(store.get(), { type: "notice", data: { kind: "plan_buildable" } })));
    await vi.waitFor(() => expect(screen.queryByTestId("advisor-step-3")).toBeNull());
    expect(calls.filter((u) => u.includes("advisor_next"))).toHaveLength(2);
  });

  it.each(["fired", "cleared"])("asks the advisor again when a stall alert is %s", async (event) => {
    // The alert engine owns the stall (D281) and advisor_next ranks its rows (review M1 for stalls).
    queue.push({ card: card(ROWS) }, { card: card([ROWS[1]]) });
    render(<NextSteps />);
    await screen.findByTestId("advisor-step-3");
    const message = { type: "alert", data: { event, binding: "lab", alert: { rule: "machine_stalled" } } };
    act(() => store.set(applyMessage(store.get(), message)));
    await vi.waitFor(() => expect(screen.queryByTestId("advisor-step-3")).toBeNull());
    expect(calls.filter((u) => u.includes("advisor_next"))).toHaveLength(2);
  });
});

describe("the notice push", () => {
  it("bumps the notices count and nothing else", () => {
    expect(applyMessage(initialState, { type: "notice", data: {} })).toEqual({ notices: 1 });
  });
  it("bumps it too when an announced problem clears", () => {
    expect(applyMessage(initialState, { type: "notice.cleared", data: {} })).toEqual({ notices: 1 });
  });
  it("bumps it on a fired or cleared alert, not on a stale or fresh one", () => {
    expect(applyMessage(initialState, { type: "alert", data: { event: "fired" } })).toEqual({ notices: 1 });
    expect(applyMessage(initialState, { type: "alert", data: { event: "cleared" } })).toEqual({ notices: 1 });
    expect(applyMessage(initialState, { type: "alert", data: { event: "stale" } })).toEqual({});
    expect(applyMessage(initialState, { type: "alert", data: null })).toEqual({});
  });
});
