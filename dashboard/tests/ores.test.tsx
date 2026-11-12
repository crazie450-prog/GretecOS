// The Ores view (WO-6.6): the core's ore list as the catalog card in its order, the map around
// him, a waypoint only proposed by its vein: id (the read-back shows; nothing else is sent), the
// state when the desk can't mark, and the empty state before an ore load. Every name and number
// here is made up.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OresView } from "../src/api/types";
import type { OreRow } from "../src/cards/types.gen";
import { Ores, mapPoints, oreRows } from "../src/views/Ores";

function row(ref: string, name: string, x: number, z: number, distance: number | null, layer: OreRow["layer"], depleted = false): OreRow {
  return { mix: `ore.mix.${name.toLowerCase()}`, name, ores: [name, "Other"], dim: 0, x, z, distance, layer, depleted, ref };
}

const ROWS = [
  row("vein:0/10/-5", "Ferrite", 168, -72, 114, "primary"),
  row("vein:0/-2/4", "Ferrite", -24, 72, 134, "primary"),
  row("vein:0/4/1", "Copperish", 72, 24, 28, "sporadic"),
  row("vein:0/1/1", "Ferrite", 24, 24, 76, "primary", true),
];

function view(material: string | null, canMark = true): OresView {
  return {
    source: { world: "lab-ores", instance: "lab", loaded_utc: "2026-11-12T00:00:00Z", veins: 8 },
    position: { x: 100, y: 64, z: 20, dim: 0, dim_name: "Overworld", world: "lab-ores", age_s: 12 },
    position_note: null,
    dims: [
      { dim: -1, name: "Nether", veins: 2 },
      { dim: 0, name: "Overworld", veins: 5 },
    ],
    materials: ["Copperish", "Ferrite"],
    material,
    dim: 0,
    card: material ? { type: "ore_list", title: `Nearest ${material} veins`, material, rows: ROWS, notes: ["ranked by code"] } : null,
    can_mark: canMark,
    mark_note: canMark ? null : "the veins are from the world 'x', which is neither [paths] lab_world nor [paths] world",
  };
}

let urls: string[];
let bodies: unknown[];
let queue: { status: number; body: unknown }[];

beforeEach(() => {
  urls = [];
  bodies = [];
  queue = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    urls.push(url);
    bodies.push(init?.body ? JSON.parse(String(init.body)) : null);
    const next = queue.shift() ?? { status: 500, body: { error: "no answer queued" } };
    return new Response(JSON.stringify(next.body), { status: next.status });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("helpers", () => {
  it("read an ore list's rows and place them around him, north up", () => {
    expect(oreRows(null)).toEqual([]);
    expect(oreRows({ type: "quest" })).toEqual([]);
    const points = mapPoints(ROWS, 0, { x: 100, z: 20 });
    expect(points.map((p) => p.n)).toEqual([1, 2, 3, 4]); // the core's order, numbered
    // The farthest offset (124 blocks west) sits at the edge (90 of 100).
    expect(points[1]).toMatchObject({ x: -90, y: 37.7, ref: "vein:0/-2/4" });
    expect(points[0].y).toBeLessThan(0); // -z is north, drawn up
    expect(points[3].depleted).toBe(true);
    expect(mapPoints(ROWS, -1, null)).toEqual([]); // none in that dimension
    expect(mapPoints(ROWS, 0, null)[0]).toMatchObject({ x: 0, y: 0 }); // no position: the first vein
    // No dimension chosen: only the first row's dimension, never two dimensions in one plot.
    const mixed = [ROWS[0], { ...ROWS[1], dim: -1 }, ...ROWS.slice(2)];
    const plotted = mapPoints(mixed, null, null);
    expect(plotted.map((p) => p.n)).not.toContain(2);
    expect(plotted.length).toBe(mixed.filter((r) => r.dim === mixed[0].dim).length);
  });
});

describe("the Ores view", () => {
  it("lists a material's veins in the core's order and draws them", async () => {
    queue.push({ status: 200, body: view(null) }, { status: 200, body: view("Ferrite") });
    render(<Ores />);
    const input = await screen.findByTestId("ores-input");
    expect(screen.getByTestId("ores-position").textContent).toContain("x 100, y 64, z 20");
    expect(screen.getByTestId("ores-source").textContent).toContain("Veins from lab-ores (Lab): 8");
    fireEvent.change(input, { target: { value: " Ferrite " } });
    fireEvent.click(screen.getByTestId("ores-go"));
    const list = await screen.findByTestId("ores-list");
    expect(urls).toEqual(["/api/ores", "/api/ores?material=Ferrite"]);
    const cites = [...list.querySelectorAll("[data-cite]")].map((e) => e.getAttribute("data-cite"));
    expect(cites).toEqual(ROWS.map((r) => r.ref));
    expect(within(list).getByText("114 blocks")).toBeTruthy();
    expect(screen.getByTestId("ores-map").querySelectorAll(".ore-map-vein")).toHaveLength(4);
    expect(screen.getByTestId("ores-map-you")).toBeTruthy();
  });

  it("proposes a waypoint by its vein id only, and shows GOS's read-back", async () => {
    queue.push({ status: 200, body: view(null) }, { status: 200, body: view("Ferrite") });
    queue.push({ status: 200, body: { text: 'Add waypoint "Ferrite vein" at 168, 25, -72 in dimension 0 in Lab. Say confirm.', action: { id: 7, state: "proposed" } } });
    render(<Ores />);
    fireEvent.change(await screen.findByTestId("ores-input"), { target: { value: "Ferrite" } });
    fireEvent.click(screen.getByTestId("ores-go"));
    fireEvent.click(await screen.findByTestId("ores-mark-1"));
    const said = await screen.findByTestId("ores-said");
    expect(said.textContent).toContain("Say confirm.");
    expect(urls[2]).toBe("/api/ores/waypoint");
    expect(bodies[2]).toEqual({ vein: "vein:0/10/-5" }); // never coordinates
  });

  it("says why it can't mark, and says so before an ore load", async () => {
    queue.push({ status: 200, body: view(null, false) }, { status: 200, body: view("Ferrite", false) });
    render(<Ores />);
    fireEvent.change(await screen.findByTestId("ores-input"), { target: { value: "Ferrite" } });
    fireEvent.click(screen.getByTestId("ores-go"));
    expect((await screen.findByTestId("ores-no-mark")).textContent).toContain("neither");
    expect(screen.queryByTestId("ores-mark-1")).toBeNull();
    cleanup();
    queue.push({ status: 404, body: { error: "no ore data yet: run gos ores load" } });
    render(<Ores />);
    expect((await screen.findByTestId("ores-empty")).textContent).toContain("gos ores load");
  });

  it("asks for another dimension's veins", async () => {
    queue.push({ status: 200, body: view(null) }, { status: 200, body: view("Ferrite") }, { status: 200, body: view("Ferrite") });
    render(<Ores />);
    fireEvent.change(await screen.findByTestId("ores-input"), { target: { value: "Ferrite" } });
    fireEvent.click(screen.getByTestId("ores-go"));
    await screen.findByTestId("ores-map");
    fireEvent.change(screen.getByTestId("ores-dim"), { target: { value: "-1" } });
    await vi.waitFor(() => expect(urls[2]).toBe("/api/ores?material=Ferrite&dim=-1"));
  });
});
