// The Designer view (WO-3.7): it shows the routes in the order the core ranked them (never its
// own), marks differences, opens the design in the address (so a reload shows it again), swaps or
// pins a node through the core and shows the re-solved design, asks before accepting, and asks
// which item when the target is unclear.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DesignerRoute, DesignerView } from "../src/api/types";
import { RouteComparisonView } from "../src/cards/renderers";
import { Designer, planFromHash, routeLabel } from "../src/views/Designer";

const STEEL = "gregtech:gt.metaitem.01@11305";
const DUST = "gregtech:gt.metaitem.01@2305";

function route(id: string, position: number, over: Partial<DesignerRoute> = {}): DesignerRoute {
  return {
    id,
    name: `Steel Ingot line · 1/s · HV${position > 1 ? ` · route ${position}` : ""}`,
    status: "proposed",
    column: "base",
    position,
    of: 2,
    score: position === 1 ? 0.76 : 0.5,
    target: { id: STEEL, name: "Steel Ingot", rate_s: 1 },
    tier_cap: "HV",
    pins: [],
    nodes: [
      { n: "n1", item: STEEL, name: "Steel Ingot", recipe: "r:gt.recipe.blastfurnace/9c40869458cb", map: "Blast Furnace",
        machine: "Electric Blast Furnace", mte: "mte:1000", tier: "HV", count: 13, pinned: false, primary: true },
      { n: "n2", item: DUST, name: "Steel Dust", recipe: "r:gt.recipe.macerator/c04c5d068f50", map: "Macerator",
        machine: "Basic Macerator", mte: "mte:30", tier: "LV", count: 5, pinned: false, primary: true },
    ],
    warnings: [],
    metrics: { machines: 18, score: position === 1 ? 0.76 : 0.5 },
    differs: [],
    flow: { type: "flow", title: "Steel", ref: `plan:${id}`, nodes: [], edges: [], notes: [] },
    bom: { type: "bom", title: "Steel · BOM", ref: `plan:${id}`, rows: [], notes: [] },
    ...over,
  };
}

function view(id: string, routes: DesignerRoute[]): DesignerView {
  return {
    id,
    weights: "ranked by weights (balanced): machines 1",
    routes,
    comparison: {
      type: "route_comparison",
      title: "Steel Ingot line · 1/s · HV",
      ref: `plan:${routes[0].id}`,
      routes: routes.map((r) => ({
        id: r.id,
        name: r.name,
        rank: r.position,
        ref: `plan:${r.id}`,
        accepted: r.status !== "proposed",
        metrics: [{ key: "score", label: "score", value: r.score, unit: "", better: "higher", best: r.position === 1 }],
        choices: [{ node: STEEL, label: "Steel Ingot", machine: "Electric Blast Furnace", tier: "HV", ref: r.nodes[0].recipe, differs: true }],
      })),
      notes: ["ranked by code (planner/rank.py)"],
    },
  };
}

// Out of rank order on purpose in the ids: the view must keep the order the core gave.
const FIRST = view("pl-bbbbbbbbbb", [route("pl-bbbbbbbbbb", 1), route("pl-aaaaaaaaaa", 2)]);

type Call = { method: string; url: string; body: unknown };
let calls: Call[];
let routes: Record<string, () => { status: number; body: unknown }>;

beforeEach(() => {
  calls = [];
  window.location.hash = "";
  routes = {
    "GET /api/plans": () => ({ status: 200, body: { plans: [], problems: [] } }),
    "GET /api/designer": () => ({ status: 200, body: { ids: [] } }),
  };
  // React Flow measures its canvas; jsdom has no ResizeObserver.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    },
  );
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    calls.push({ method, url, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const path = url.split("?")[0];
    const answer = routes[`${method} ${path}`] ?? (() => ({ status: 404, body: { error: `no route ${method} ${path}` } }));
    const { status, body } = answer();
    return new Response(JSON.stringify(body), { status });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("helpers", () => {
  it("reads the plan from the address", () => {
    expect(planFromHash("#/designer/pl-0123456789")).toBe("pl-0123456789");
    expect(planFromHash("#/designer")).toBeNull();
    expect(planFromHash("#/designer/../x")).toBeNull();
    expect(planFromHash("#/now")).toBeNull();
  });

  it("labels a route by the core's rank", () => {
    expect(routeLabel({ column: "base", position: 2, of: 3 })).toBe("Route 2 of 3");
    expect(routeLabel({ column: "addon", position: 1, of: 1 })).toBe("Addon column");
  });

  it("heads the comparison's addon route as the addon column, never as a rank", () => {
    const card = view("pl-bbbbbbbbbb", [route("pl-bbbbbbbbbb", 1), route("pl-cccccccccc", 1, { column: "addon" })]).comparison;
    card.routes[1].rank = "addon";
    render(<RouteComparisonView card={card} />);
    const heads = [...document.querySelectorAll(".route-head .gos-label")].map((e) => e.textContent);
    expect(heads).toEqual(["Route 1", "Addon column"]);
  });
});

describe("the Designer view", () => {
  it("says so when there is no design yet", async () => {
    render(<Designer />);
    expect((await screen.findByTestId("designer-empty")).textContent).toContain("No design open");
  });

  it("opens the latest design in rank order, and puts it in the address", async () => {
    routes["GET /api/designer"] = () => ({ status: 200, body: { ids: [FIRST.id] } });
    routes[`GET /api/plans/${FIRST.id}/designer`] = () => ({ status: 200, body: FIRST });
    render(<Designer />);
    await screen.findByTestId("route-1-base");
    const columns = [...document.querySelectorAll(".designer-route[data-route]")].map((e) => e.getAttribute("data-route"));
    expect(columns).toEqual(["pl-bbbbbbbbbb", "pl-aaaaaaaaaa"]);
    expect(screen.getByTestId("route-1-base").textContent).toContain("Route 1 of 2");
    expect(screen.getByTestId("designer-compare").querySelector("li.differs")?.textContent).toContain("differs");
    expect(window.location.hash).toBe(`#/designer/${FIRST.id}`);
    expect(screen.getByTestId("designer-weights").textContent).toContain("balanced");
  });

  it("swaps a node: its alternatives, then a pin that the core solves again", async () => {
    window.location.hash = `/designer/${FIRST.id}`;
    routes[`GET /api/plans/${FIRST.id}/designer`] = () => ({ status: 200, body: FIRST });
    const other = "r:gt.recipe.macerator/74d679116534";
    routes[`GET /api/plans/${FIRST.id}/alternatives`] = () => ({
      status: 200,
      body: {
        plan: FIRST.id, node: DUST, name: "Steel Dust", pinned: null, all: false,
        alternatives: [
          { recipe: "r:gt.recipe.macerator/c04c5d068f50", status: "chosen", reason: "", map: "Macerator", tier: "LV", eut: 2, duration_t: 98, used: true },
          { recipe: other, status: "alternative", reason: "", map: "Macerator", tier: "LV", eut: 2, duration_t: 100, used: false },
        ],
      },
    });
    const pinnedRoute = route("pl-cccccccccc", 1, {
      pins: [{ node: DUST, recipe: other }],
      nodes: route("x", 1).nodes.map((n) => (n.item === DUST ? { ...n, recipe: other, pinned: true } : n)),
    });
    const pinned = view("pl-cccccccccc", [pinnedRoute]);
    routes[`POST /api/plans/${FIRST.id}/pin`] = () => ({ status: 200, body: { ids: [pinned.id], problems: [], view: pinned } });
    routes[`GET /api/plans/${pinned.id}/alternatives`] = routes[`GET /api/plans/${FIRST.id}/alternatives`];
    render(<Designer />);
    fireEvent.click(await screen.findByTestId(`swap-${FIRST.id}-n2`));
    const panel = await screen.findByTestId("designer-alternatives");
    const pin = await within(panel).findByTestId(`pin-${other}`);
    expect(within(panel).queryByTestId("pin-r:gt.recipe.macerator/c04c5d068f50")).not.toBeNull();
    await act(async () => {
      fireEvent.click(pin);
    });
    const posted = calls.find((c) => c.method === "POST");
    expect(posted).toEqual({ method: "POST", url: `/api/plans/${FIRST.id}/pin`, body: { node: DUST, recipe: other } });
    const pins = await screen.findByTestId(`pins-${pinned.id}`);
    expect(pins.textContent).toContain("pinned Steel Dust");
    expect(pins.querySelector(`[data-cite="${other}"]`)).not.toBeNull();
    expect(window.location.hash).toBe(`#/designer/${pinned.id}`);
  });

  it("asks before accepting, then shows the route accepted", async () => {
    window.location.hash = `/designer/${FIRST.id}`;
    routes[`GET /api/plans/${FIRST.id}/designer`] = () => ({ status: 200, body: FIRST });
    const done = view(FIRST.id, [route(FIRST.id, 1, { status: "accepted" }), route("pl-aaaaaaaaaa", 2)]);
    routes[`POST /api/plans/${FIRST.id}/accept`] = () => ({
      status: 200,
      body: { id: FIRST.id, status: "accepted", line: "base/lines/ln-bbbbbbbbbb.md", note: null, ledger: { recorded: false, note: "" }, problems: [], view: done },
    });
    render(<Designer />);
    fireEvent.click(await screen.findByTestId(`accept-${FIRST.id}`));
    expect(calls.some((c) => c.method === "POST")).toBe(false); // nothing until he confirms
    expect(screen.getByTestId(`accept-confirm-${FIRST.id}`).textContent).toContain("EU ledger");
    await act(async () => {
      fireEvent.click(screen.getByTestId(`accept-yes-${FIRST.id}`));
    });
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual([`/api/plans/${FIRST.id}/accept`]);
    await screen.findByTestId("route-1-base");
    expect(screen.getByTestId("route-1-base").textContent).toContain("accepted");
    expect(screen.queryByTestId(`accept-${FIRST.id}`)).toBeNull();
  });

  it("asks which item when the target is unclear, then designs with its key", async () => {
    routes["POST /api/designer/design"] = () => ({
      status: 200,
      body: {
        clarify: {
          type: "choices", subject: "ingot", question: "Which ingot did you mean?",
          candidates: [{ n: 1, key: STEEL, name: "Steel Ingot", kind: "item", mod: "gregtech" }],
        },
      },
    });
    render(<Designer />);
    fireEvent.change(await screen.findByTestId("designer-target"), { target: { value: "ingot" } });
    fireEvent.change(screen.getByTestId("designer-tier"), { target: { value: "HV" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("designer-go"));
    });
    expect((await screen.findByTestId("designer-clarify")).textContent).toContain("Which ingot");
    routes["POST /api/designer/design"] = () => ({ status: 200, body: { ids: [FIRST.id], problems: [], view: FIRST } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("clarify-1"));
    });
    const designs = calls.filter((c) => c.url === "/api/designer/design").map((c) => c.body);
    expect(designs).toEqual([
      { target: "ingot", rate: "1/s", tier: "HV", routes: 3, addons: false },
      { target: STEEL, rate: "1/s", tier: "HV", routes: 3, addons: false },
    ]);
    await screen.findByTestId("route-1-base");
  });

  it("shows the core's refusal", async () => {
    routes["POST /api/designer/design"] = () => ({ status: 400, body: { error: "no route: no recipe makes the target" } });
    render(<Designer />);
    fireEvent.change(await screen.findByTestId("designer-target"), { target: { value: "unobtainium" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId("designer-go"));
    });
    expect((await screen.findByTestId("designer-notice")).textContent).toContain("no recipe makes the target");
  });
});
