// QA 5-9: the flow diagram's machine boxes keep their measured size across re-renders. React Flow
// hides a node (visibility: hidden) until it has been measured; Drawn used to hand it new node
// objects on every render, which drops the measurement, and a burst of re-renders could leave
// every box hidden for good (the designer.spec.ts "hidden" flake).
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import valid from "../../tests/fixtures/cards/valid.json";
import { FlowDiagram } from "../src/cards/FlowDiagram";
import type { FlowCard } from "../src/cards/types.gen";

const card = (valid as Record<string, unknown>).flow as FlowCard;
const boxes = card.nodes.map((n, i) => ({ id: n.id, x: i * 240, y: 0 }));
const layout = async () => boxes;

// jsdom lays nothing out: every element measures 200 x 60, and a ResizeObserver reports each
// element it is asked to watch, once, as the browser does on observe().
class MeasuringObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element): void {
    setTimeout(() => this.callback([{ target } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver), 0);
  }
  unobserve(): void {}
  disconnect(): void {}
}

// React Flow reads the viewport's zoom from the transform; jsdom has no DOMMatrixReadOnly.
class Matrix {
  m22 = 1;
}

const saved: Record<string, PropertyDescriptor | undefined> = {};
beforeAll(() => {
  for (const [name, value] of [["offsetWidth", 200], ["offsetHeight", 60]] as const) {
    saved[name] = Object.getOwnPropertyDescriptor(HTMLElement.prototype, name);
    Object.defineProperty(HTMLElement.prototype, name, { configurable: true, get: () => value });
  }
  globalThis.ResizeObserver = MeasuringObserver as unknown as typeof ResizeObserver;
  (window as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly ??= Matrix;
  // an edge label measures its text box
  (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox ??= () => ({ x: 0, y: 0, width: 40, height: 12 }) as DOMRect;
});
afterAll(() => {
  for (const [name, d] of Object.entries(saved)) if (d) Object.defineProperty(HTMLElement.prototype, name, d);
});
afterEach(cleanup);

let bump: () => void = () => undefined;
function Parent() {
  const [n, setN] = useState(0);
  bump = () => setN((x) => x + 1);
  return (
    <div data-renders={n}>
      <FlowDiagram card={card} layout={layout} />
    </div>
  );
}

function hidden(container: HTMLElement): number {
  return [...container.querySelectorAll<HTMLElement>(".react-flow__node")].filter((el) => el.style.visibility === "hidden").length;
}

describe("the flow diagram's boxes", () => {
  it("stay measured and visible through a burst of parent re-renders", async () => {
    const { container } = render(<Parent />);
    await waitFor(() => expect(container.querySelectorAll(".react-flow__node").length).toBe(card.nodes.length));
    await waitFor(() => expect(hidden(container)).toBe(0));
    for (let i = 0; i < 20; i++) {
      act(() => bump());
      expect(hidden(container)).toBe(0); // never a frame without them
      await act(async () => new Promise((r) => setTimeout(r, i % 3)));
    }
    await act(async () => new Promise((r) => setTimeout(r, 50)));
    expect(hidden(container)).toBe(0);
  });
});
