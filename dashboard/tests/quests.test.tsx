// The Quests view (WO-6.4): the ranked frontier as a catalog card with its reasons, items linked
// to Search, the quests past the frontier blurred until tapped (D26), "Spoil it" asking the core
// for the full level, a spoken "spoil it" doing the same, and the empty state before a quest load.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Answer, QuestsView } from "../src/api/types";
import { Quests, spokeSpoil, stageOf } from "../src/views/Quests";

const Q = (n: number) => `q:${"A".repeat(20)}${String(n).padStart(2, "0")}`;

function row(part: string, what: string | null, ref: string | null = null) {
  return { part, what, kind: null, amount: null, consume: null, state: null, ref };
}

function card(title: string, spoil: number, quests: unknown[]) {
  return { type: "quest", tool: null, error: null, title, tier: "stone (GT voltage tier 0, quests)", spoil, quests, total: quests.length, notes: [] };
}

function view(spoil: 0 | 1 | 2 = 0): QuestsView {
  const hidden = spoil >= 1;
  return {
    player: "u",
    tier: "stone (GT voltage tier 0, quests)",
    spoil,
    open: null,
    default: 0,
    goals: ["Electric Blast Furnace"],
    frontier: card("Frontier", 2, [
      { ref: Q(11), name: "Bricks", state: "available", lines: "Steam", main: false, shown: true, description: null, rows: [row("why", "goal: Electric Blast Furnace (5 steps)")], spoil: null },
    ]),
    beyond: card("Beyond the frontier", spoil, [
      {
        ref: Q(14),
        name: hidden ? "Pressure Cooker" : null,
        state: "locked",
        lines: "Steam",
        main: false,
        shown: false,
        description: spoil === 2 ? "Pressure makes diamonds, they say." : null,
        rows: [row("after", "Bricks", Q(11))],
        spoil,
      },
    ]),
    links: { [Q(11)]: [{ part: "task", key: "gregtech:gt.blockmachines@140", name: "Bricked Blast Furnace", amount: 1, consume: false }] },
    lines: [{ name: "Steam", done: 1, available: 3, locked: 2, total: 6, main_left: 0, frontier: [Q(11)], note: "quests/qs-steam.md" }],
  };
}

let urls: string[];
let queue: { status: number; body: unknown }[];

beforeEach(() => {
  urls = [];
  queue = [];
  vi.stubGlobal("fetch", async (url: string) => {
    urls.push(url);
    const next = queue.shift() ?? { status: 500, body: { error: "no answer queued" } };
    return new Response(JSON.stringify(next.body), { status: next.status });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("helpers", () => {
  it("read the stage and a spoken spoil", () => {
    expect(stageOf("LV (GT voltage tier 1, override)")).toBe("LV");
    const a = { intent: "spoil", asked_ts: 10, spoil: { level: 2, quests: [Q(14)] } } as Answer;
    expect(spokeSpoil([a], 5)).toEqual({ level: 2, quests: [Q(14)] });
    expect(spokeSpoil([a], 20)).toBeNull();
    // the intent alone grants nothing: only the core's grant does (D268 R3)
    expect(spokeSpoil([{ ...a, spoil: null }], 5)).toBeNull();
    expect(spokeSpoil([{ ...a, spoil: { level: 2, quests: [] } }], 5)).toBeNull();
    expect(spokeSpoil([], 5)).toBeNull();
  });
});

describe("the Quests view", () => {
  it("shows the ranked frontier, its items and the blurred quests past it", async () => {
    queue.push({ status: 200, body: view(0) });
    const openItem = vi.fn();
    render(<Quests answers={[]} openItem={openItem} />);
    const front = await screen.findByTestId("quests-frontier");
    expect(urls).toEqual(["/api/quests"]);
    expect(within(front).getByText("Bricks")).toBeTruthy();
    expect(screen.getByText("goal: Electric Blast Furnace (5 steps)")).toBeTruthy();
    fireEvent.click(screen.getByText(/needs Bricked Blast Furnace/));
    expect(openItem).toHaveBeenCalledWith("gregtech:gt.blockmachines@140", "Bricked Blast Furnace");
    const zone = screen.getByTestId("spoiler-zone");
    expect(zone.className).not.toContain("revealed");
    expect(screen.queryByText("Pressure Cooker")).toBeNull(); // not sent at all at level 0
    fireEvent.click(zone); // the first tap only lifts the blur
    expect(zone.className).toContain("revealed");
    expect(screen.getByTestId("quests-level").textContent).toContain("nudge");
  });

  it("asks the core for the full level on Spoil it, and goes back", async () => {
    queue.push({ status: 200, body: view(0) }, { status: 200, body: view(2) }, { status: 200, body: view(0) });
    render(<Quests answers={[]} openItem={() => undefined} />);
    fireEvent.click(await screen.findByTestId("quests-spoil"));
    await screen.findByText("Pressure Cooker");
    expect(urls).toEqual(["/api/quests", "/api/quests?spoil=2"]);
    expect(screen.getByText("Pressure makes diamonds, they say.")).toBeTruthy();
    expect(screen.getByTestId("spoiler-zone").className).toContain("revealed");
    fireEvent.click(screen.getByTestId("quests-nudge"));
    await act(async () => undefined);
    expect(urls[2]).toBe("/api/quests");
  });

  it("opens only the quests a spoken spoil it granted, at its level", async () => {
    queue.push({ status: 200, body: view(0) }, { status: 200, body: { ...view(2), open: [Q(14)] } });
    const { rerender } = render(<Quests answers={[]} openItem={() => undefined} />);
    await screen.findByTestId("quests-frontier");
    const said = { intent: "spoil", asked_ts: Date.now() / 1000 + 1, spoil: { level: 2, quests: [Q(14)] } } as Answer;
    rerender(<Quests answers={[said]} openItem={() => undefined} />);
    await screen.findByText("Pressure Cooker");
    expect(urls).toEqual(["/api/quests", `/api/quests?spoil=2&open=${encodeURIComponent(Q(14))}`]);
    expect(screen.getByTestId("quests-spoil")).toBeTruthy(); // the rest can still be asked for
  });

  it("keeps a partial answer partial (B6)", async () => {
    queue.push({ status: 200, body: view(0) }, { status: 200, body: { ...view(1), open: [Q(14)] } });
    const { rerender } = render(<Quests answers={[]} openItem={() => undefined} />);
    await screen.findByTestId("quests-frontier");
    const partial = {
      intent: "spoil",
      text: "What it asks for: Pressure Cooker",
      card: { type: "quest", spoil: 1 },
      asked_ts: Date.now() / 1000 + 1,
      spoil: { level: 1, quests: [Q(14)] },
    } as unknown as Answer;
    rerender(<Quests answers={[partial]} openItem={() => undefined} />);
    await screen.findByText("Pressure Cooker");
    expect(urls).not.toContain("/api/quests?spoil=2");
    expect(urls[1]).toBe(`/api/quests?spoil=1&open=${encodeURIComponent(Q(14))}`);
    expect(screen.queryByText("Pressure makes diamonds, they say.")).toBeNull();
  });

  it("opens nothing for a spoil intent without a grant", async () => {
    queue.push({ status: 200, body: view(0) });
    const { rerender } = render(<Quests answers={[]} openItem={() => undefined} />);
    await screen.findByTestId("quests-frontier");
    const asked = { intent: "spoil", text: "Which quest?", asked_ts: Date.now() / 1000 + 1, spoil: null } as unknown as Answer;
    rerender(<Quests answers={[asked]} openItem={() => undefined} />);
    await new Promise((r) => setTimeout(r, 30));
    expect(urls).toEqual(["/api/quests"]);
  });

  it("says when no quests are loaded", async () => {
    queue.push({ status: 404, body: { error: "no quests loaded yet: run `gos quests load` first" } });
    render(<Quests answers={[]} openItem={() => undefined} />);
    const empty = await screen.findByTestId("quests-empty");
    expect(empty.textContent).toContain("gos quests load");
  });
});
