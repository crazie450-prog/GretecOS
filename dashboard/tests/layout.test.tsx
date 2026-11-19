// The layout card (WO-10.5, D313): the floor plan drawn from data only, every box at its block
// position, the front marked, an unreached machine in the error colour, and each machine listed
// with its mte: chip and tier (rule 2).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import valid from "../../tests/fixtures/cards/valid.json";
import { Card } from "../src/cards/Card";
import { CardContext } from "../src/cards/context";
import type { LayoutCard } from "../src/cards/types.gen";

afterEach(cleanup);

const LAYOUT = valid.layout as LayoutCard;

function draw(card: LayoutCard) {
  return render(
    <CardContext.Provider value={{ stale: false }}>
      <Card card={card} />
    </CardContext.Provider>,
  );
}

describe("the layout card", () => {
  it("draws every box at its block position, scaled to the bounding volume", () => {
    const { container } = draw(LAYOUT);
    const svg = screen.getByTestId("layout-plan");
    expect(svg.getAttribute("viewBox")).toBe("-0.5 -0.5 15 14");
    for (const r of LAYOUT.rects) {
      const rect = container.querySelector(`[data-rect="${r.id}"] rect`);
      expect(rect, r.id).not.toBeNull();
      expect(rect?.getAttribute("x")).toBe(String(r.x));
      expect(rect?.getAttribute("y")).toBe(String(r.z));
      expect(rect?.getAttribute("width")).toBe(String(r.w));
      expect(rect?.getAttribute("height")).toBe(String(r.l));
      expect(rect?.closest("g")?.getAttribute("class")).toContain(`layout-${r.kind}`);
    }
    // the machines' front marks sit on the side they face
    const ebf = container.querySelector('[data-rect="m1"] polygon');
    expect(ebf?.getAttribute("points")).toMatch(/ 5\.5,4$/); // its south edge, z 1 + 3
    expect(container.querySelectorAll(".layout-front")).toHaveLength(2);
    // keep-outs are drawn under machines, the entrance on top
    const order = [...container.querySelectorAll("[data-rect]")].map((g) => g.getAttribute("data-rect"));
    expect(order.indexOf("k1")).toBeLessThan(order.indexOf("m1"));
    expect(order.at(-1)).toBe("e1");
  });

  it("lists the machines with their chips and marks one not reached", () => {
    const bad = {
      ...LAYOUT,
      rects: LAYOUT.rects.map((r) => (r.id === "m2" ? { ...r, ok: false } : r)),
    };
    const { container } = draw(bad);
    expect(container.querySelector('[data-rect="m2"]')?.getAttribute("class")).toContain("bad");
    expect(screen.getByText("mte:1000")).toBeTruthy();
    expect(screen.getByText("mte:401")).toBeTruthy();
    expect(screen.getByText(/1 not reached/)).toBeTruthy();
    expect(screen.getByText("plan:pl-0007")).toBeTruthy();
    expect(screen.getByText(/14 × 13 blocks · 5 high/)).toBeTruthy();
  });

  it("groups identical machines into one row", () => {
    const twin = { ...LAYOUT.rects[5], id: "m3", x: 8 };
    const { container } = draw({ ...LAYOUT, rects: [...LAYOUT.rects, twin] });
    expect(container.querySelectorAll(".layout-machines tr")).toHaveLength(2);
    expect(screen.getByText(/2× Electric Blast Furnace/)).toBeTruthy();
  });
});
