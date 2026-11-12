// The card catalog (plan 7.14 rule 1, D160): the same schema and fixtures as the Python side,
// every card drawn from data only, a citation chip by every number, "data stale" when stale.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import invalid from "../../tests/fixtures/cards/invalid.json";
import valid from "../../tests/fixtures/cards/valid.json";
import { Card } from "../src/cards/Card";
import { CardContext, type CardActions } from "../src/cards/context";
import { FlowDiagram, elkLayout, nodeSub } from "../src/cards/FlowDiagram";
import { CARD_TYPES } from "../src/cards/types.gen";
import { schemaCardTypes, validate } from "../src/cards/validate";

afterEach(cleanup);

// jsdom has no ResizeObserver; React Flow (the flow card, WO-3.6) measures with one.
class NoResize {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver ??= NoResize as unknown as typeof ResizeObserver;

const VALID = Object.fromEntries(Object.entries(valid).filter(([k]) => !k.startsWith("_"))) as Record<string, Record<string, unknown>>;
const CITE = /^(r|q|mte|crop|plan|w):\S+$/;

function draw(card: unknown, actions: Partial<CardActions> = {}) {
  return render(
    <CardContext.Provider value={{ stale: false, ...actions }}>
      <Card card={card} />
    </CardContext.Provider>,
  );
}

describe("the shared schema", () => {
  it("lists the generated card types in catalog order", () => {
    expect(schemaCardTypes()).toEqual([...CARD_TYPES]);
    expect(Object.keys(VALID).sort()).toEqual([...CARD_TYPES].sort());
  });

  it.each(Object.keys(VALID))("accepts the %s fixture", (name) => {
    expect(validate(VALID[name])).toEqual([]);
  });

  it.each(invalid.map((c) => [c.why, c] as const))("refuses %s with the Python side's reason", (_why, c) => {
    const errors = validate(c.card);
    expect(errors.length).toBeGreaterThan(0);
    expect(errors.some((e) => e.includes(c.error))).toBe(true);
  });
});

describe("drawing cards", () => {
  it.each(Object.keys(VALID))("draws the %s card", (name) => {
    const { container } = draw(VALID[name]);
    expect(container.querySelector(`[data-card="${name}"], [data-approval], [data-alert]`)).not.toBeNull();
    expect(container.querySelector('[data-card="refused"]')).toBeNull();
  });

  it("refuses a card outside the catalog instead of drawing it", () => {
    const { container } = draw({ type: "html", html: "<img src=x onerror=alert(1)>" });
    expect(screen.getByText(/Card not shown/)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows text from a card as text, never markup", () => {
    const card = { ...VALID.alert, body: "<b>bold</b><script>x()</script>" };
    const { container } = draw(card);
    expect(container.querySelector("b, script")).toBeNull();
    expect(screen.getByText("<b>bold</b><script>x()</script>")).toBeTruthy();
  });

  it("puts a citation chip by every recipe's numbers", () => {
    const { container } = draw(VALID.recipe);
    const rows = container.querySelectorAll("li.recipe");
    expect(rows).toHaveLength(1);
    for (const row of rows) {
      const chip = row.querySelector("[data-cite]");
      expect(chip?.getAttribute("data-cite")).toBe(row.getAttribute("data-ref"));
      expect(within(row as HTMLElement).getByText("120")).toBeTruthy();
    }
    expect(screen.getByText(/11\.11% chance/)).toBeTruthy();
    expect(screen.getByText(/1000 L Oxygen/)).toBeTruthy();
  });

  it("cites every route, every ledger tier and the bill of materials", () => {
    for (const [name, minChips] of [["route_comparison", 3], ["power_ledger", 1], ["bom", 2], ["flow", 2]] as const) {
      const { container, unmount } = draw(VALID[name]);
      const chips = [...container.querySelectorAll("[data-cite]")].map((c) => c.getAttribute("data-cite") ?? "");
      expect(chips.length, name).toBeGreaterThanOrEqual(minChips);
      expect(chips.every((c) => CITE.test(c)), name).toBe(true);
      unmount();
    }
  });

  it("cites a flow's rate beside it when the flow has its own source", () => {
    const cited = { from: "n1", to: "n2", label: "Ethylene", rate: 1000, unit: "L/s", ref: "r:gt.recipe.chemicalreactor/0123456789ab" };
    const { container } = draw({ ...VALID.flow, edges: [cited] });
    const edge = container.querySelector('[data-edge="n1-n2"]');
    expect(edge?.textContent).toContain("Ethylene 1000 L/s");
    expect(edge?.querySelector("[data-cite]")?.getAttribute("data-cite")).toBe(cited.ref);
  });

  it("replaces game-data chips with a data stale badge, and still opens them", () => {
    const openCite = vi.fn();
    const { container } = draw(VALID.recipe, { stale: true, openCite });
    const chip = container.querySelector('[data-cite^="r:"]') as HTMLButtonElement;
    expect(chip.textContent).toContain("data stale");
    expect(chip.title).toContain("r:gt.recipe.blastfurnace/9c40869458cb");
    fireEvent.click(chip);
    expect(openCite).toHaveBeenCalledWith("r:gt.recipe.blastfurnace/9c40869458cb");
  });

  it("keeps quest citations when data is stale (quests aren't recipe data)", () => {
    const { container } = draw(VALID.quest, { stale: true });
    expect(container.querySelector('[data-cite^="q:"]')?.textContent).toMatch(/^q:/);
  });

  it("blurs a quest past the frontier until it is tapped (D26)", () => {
    const openQuest = vi.fn();
    draw(VALID.quest, { openQuest });
    const spoiler = screen.getByTestId("spoiler");
    expect(spoiler.className).toContain("spoiler");
    expect(screen.queryByText(/Any Steel/)).toBeNull();
    fireEvent.click(spoiler);
    expect(openQuest).toHaveBeenCalledWith("q:AAAAAAAAAAAAAAAAAAAABg", 0);
  });

  it("a name tap on a non-granted quest asks for that quest's own level, never the card's (B1)", () => {
    // "spoil stainless dreams" granted q4 at 2; q14 is locked, named by the book, at the global 0.
    const quest = (n: string, name: string, spoil: number) => ({
      ref: `q:${"A".repeat(20)}${n}`, name, state: "locked", lines: null, main: false, shown: true, description: null, rows: [], spoil,
    });
    const card = { ...VALID.quest, title: "Beyond the frontier", spoil: 2, quests: [quest("04", "Stainless Dreams", 2), quest("14", "Blast Off", 0)], total: 2 };
    const openQuest = vi.fn();
    const { container } = draw(card, { openQuest });
    fireEvent.click(screen.getByText("Blast Off"));
    expect(openQuest).toHaveBeenLastCalledWith(`q:${"A".repeat(20)}14`, 0);
    fireEvent.click(screen.getByText("Stainless Dreams"));
    expect(openQuest).toHaveBeenLastCalledWith(`q:${"A".repeat(20)}04`, 2);
    // the step-up button is per quest: q14 asks for its tasks (1), the granted q4 has none
    const q14 = container.querySelector(`[data-quest="q:${"A".repeat(20)}14"]`) as HTMLElement;
    fireEvent.click(within(q14).getByText("What does it ask for?"));
    expect(openQuest).toHaveBeenLastCalledWith(`q:${"A".repeat(20)}14`, 1);
    const q4 = container.querySelector(`[data-quest="q:${"A".repeat(20)}04"]`) as HTMLElement;
    expect(within(q4).queryByText(/Spoil it|What does it ask for/)).toBeNull();
  });

  it("runs an approval's actions and picks a candidate", () => {
    const act = vi.fn();
    draw(VALID.approval, { act });
    fireEvent.click(screen.getByTestId("action-approve"));
    expect(act).toHaveBeenCalledWith(expect.objectContaining({ id: "deep-0123456789ab" }), "approve");
    cleanup();
    const pick = vi.fn();
    draw(VALID.choices, { pick });
    fireEvent.click(screen.getByText("Steel Dust"));
    expect(pick).toHaveBeenCalledWith(2);
  });

  it("shows a table card's error instead of a table", () => {
    const card = { ...VALID.table, error: "the DB has no export run yet", columns: [], rows: [], head: [] };
    draw(card);
    expect(screen.getByText(/Couldn't look that up/)).toBeTruthy();
    expect(screen.getByText("the DB has no export run yet")).toBeTruthy();
  });

  it("draws citation ids in a table's cells as chips", () => {
    const { container } = draw(VALID.table);
    expect([...container.querySelectorAll("[data-cite]")].map((c) => c.getAttribute("data-cite"))).toEqual(["mte:1000", "mte:1000"]);
  });
});

describe("the flow diagram (React Flow + elkjs, WO-3.6)", () => {
  const card = VALID.flow as unknown as import("../src/cards/types.gen").FlowCard;

  it("draws every machine with its count, machine and tier, once elkjs has laid it out", async () => {
    const { container } = draw(card);
    expect(container.querySelector(".flow-wait")?.textContent).toContain("Laying out the diagram");
    expect(container.querySelector(".flow-node")).toBeNull(); // one layout, never two (D159)
    await waitFor(() => expect(container.querySelector(".flow-node")).not.toBeNull(), { timeout: 10000 });
    const nodes = [...container.querySelectorAll(".flow-node")];
    expect(nodes.length).toBe(card.nodes.length);
    for (const n of card.nodes) {
      const box = nodes.find((x) => x.querySelector(".flow-node-label")?.textContent === n.label);
      expect(box, n.id).toBeTruthy();
      expect(box?.querySelector(".flow-node-sub")?.textContent).toBe(nodeSub(n));
      if (n.tier) expect(box?.className).toContain(`tier-${n.tier.toLowerCase()}`);
    }
  });

  it("says the card isn't shown when the layout fails, and draws no other layout", async () => {
    const { container } = render(<FlowDiagram card={card} layout={async () => null} />);
    await waitFor(() => expect(container.querySelector(".flow-failed")).not.toBeNull());
    expect(container.querySelector(".flow-failed")?.textContent).toContain("Card not shown: layout failed");
    expect(container.querySelector(".flow-node")).toBeNull();
  });

  it("lays the card out left to right with elkjs", async () => {
    const boxes = await elkLayout(card);
    expect(boxes).not.toBeNull();
    const at = new Map((boxes ?? []).map((b) => [b.id, b]));
    expect(at.size).toBe(card.nodes.length);
    for (const e of card.edges) {
      if (e.from === e.to || !at.has(e.from) || !at.has(e.to)) continue;
      expect(at.get(e.to)!.x, `${e.from}->${e.to}`).toBeGreaterThan(at.get(e.from)!.x);
    }
  });
});
