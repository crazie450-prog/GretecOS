// WO-10.1/WO-10.8: the Build view's frame address, the page it opens, the guide's address, and the
// 3D build guide drawn from a fixture footprint (tests/fixtures/guide/furnace.json: what the core
// sends for WO-1.8's made-up furnace; a pytest keeps it equal to the core's output).
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import furnaceJson from "../../tests/fixtures/guide/furnace.json";
import { guideParams } from "../src/api/client";
import type { BuildGuide } from "../src/api/types";
import { store } from "../src/state/store";
import { Build, firstPage, guideFor, guideFromHash, guideHash, guideSrc } from "../src/views/Build";
import { GuideView, cellLabel } from "../src/views/BuildGuide";
import { DEFAULT_VIEW, type GuideCell, compass, fallbackColour, hatchWords, project, shown, toView, zoomed } from "../src/views/guideMesh";

const furnace = furnaceJson as unknown as BuildGuide;
const CONTROLLER = "gregtech:gt.blockmachines@9001";

describe("guideSrc", () => {
  const base = "http://guide-gos.localhost:8765/guide/1800000000-abcdef/";
  it("keeps the folders and encodes each segment under the token", () => {
    expect(guideSrc(base, "standin/en_us/multiblock.html")).toBe(`${base}standin/en_us/multiblock.html`);
    expect(guideSrc(base, "gt/en_us/a b#?.html")).toBe(`${base}gt/en_us/a%20b%23%3F.html`);
  });
});

describe("firstPage", () => {
  const page = (path: string) => ({ path, title: "", scenes: 1 });
  it("opens GOS's own guide page before the pack's", () => {
    expect(firstPage([page("aaa/en_us/x.html"), page("gos/en_us/index.html")])).toBe("gos/en_us/index.html");
  });
  it("falls back to the first page, or none", () => {
    expect(firstPage([page("aaa/en_us/x.html"), page("gosling/en_us/y.html")])).toBe("aaa/en_us/x.html");
    expect(firstPage([])).toBeNull();
  });
});

describe("the guide's address", () => {
  it("round-trips through the hash and the API's query", () => {
    const q = { controller: CONTROLLER, variant: "t1", facing: "east" as const, tiers: { coil: 3 } };
    const hash = guideHash(q);
    expect(hash).toBe("#/build?guide=gregtech%3Agt.blockmachines%409001&variant=t1&facing=east&tier=coil%3D3");
    expect(guideFromHash(hash)).toEqual(q);
    expect(guideParams(q)).toBe("controller=gregtech%3Agt.blockmachines%409001&variant=t1&facing=east&tier=coil%3D3");
  });
  it("ignores what isn't a guide", () => {
    expect(guideFromHash("#/build")).toBeNull();
    expect(guideFromHash("#/build?x=1")).toBeNull();
    expect(guideFromHash(`#/build?guide=${CONTROLLER}&facing=up&tier=coil%3Dhot`)).toEqual({
      controller: CONTROLLER,
      variant: null,
      facing: null,
      tiers: {},
    });
  });
  it("links an mte id only when it has a footprint", () => {
    const guides = [{ controller: CONTROLLER, name: "Fixture Furnace", mte: 9001, source: "gtnh-extractor", variants: ["t1"] }];
    expect(guideFor(guides, 9001)).toBe(CONTROLLER);
    expect(guideFor(guides, 1000)).toBeNull();
    expect(guideFor(guides, null)).toBeNull();
  });
});

describe("the drawing", () => {
  const size: [number, number, number] = furnace.size;
  const cells = furnace.cells as GuideCell[];

  it("shows the layers up to the slider's", () => {
    expect(shown(cells, 0).every((i) => cells[i][1] === 0)).toBe(true);
    expect(shown(cells, 2)).toHaveLength(cells.length);
  });

  it("draws only faces that look at the viewer and nothing hides", () => {
    const one: GuideCell[] = [[0, 0, 0, 0, -1]];
    const d = project(one, [1, 1, 1], 0, DEFAULT_VIEW);
    expect(d.polys.map((p) => p.shade).sort()).toEqual(["side-a", "side-b", "top"]); // three faces of a cube
    const two: GuideCell[] = [
      [0, 0, 0, 0, -1],
      [0, 1, 0, 0, -1],
    ];
    const stacked = project(two, [1, 2, 1], 1, DEFAULT_VIEW);
    expect(stacked.polys.filter((p) => p.shade === "top")).toHaveLength(1); // the lower top is covered
    // A hatch-only cell is air: it hides nothing.
    const air: GuideCell[] = [
      [0, 0, 0, 0, -1],
      [0, 1, 0, -1, 0],
    ];
    expect(project(air, [1, 2, 1], 1, DEFAULT_VIEW).polys.filter((p) => p.shade === "top")).toHaveLength(2);
  });

  it("sorts far to near and keeps the box steady across layers", () => {
    const d = project(cells, size, 2, DEFAULT_VIEW);
    for (let i = 1; i < d.polys.length; i++) expect(d.polys[i].depth).toBeGreaterThanOrEqual(d.polys[i - 1].depth);
    expect(project(cells, size, 0, DEFAULT_VIEW).box).toEqual(d.box);
  });

  it("turns: from the south (yaw 0, flat) the south face looks at the viewer", () => {
    expect(toView([0, 0, 1], { yaw: 0, pitch: 0 })[2]).toBeCloseTo(1);
    expect(toView([0, 0, -1], { yaw: 180, pitch: 0 })[2]).toBeCloseTo(1);
    expect(toView([0, 1, 0], { yaw: 0, pitch: 90 })[2]).toBeCloseTo(1); // straight down
  });

  it("zooms about the middle", () => {
    expect(zoomed([0, 0, 10, 10], 2)).toEqual([2.5, 2.5, 5, 5]);
    expect(zoomed([0, 0, 10, 10], 100)[2]).toBeCloseTo(10 / 8);
  });

  it("names hatches and colours unknown blocks steadily", () => {
    expect(hatchWords("InputBus")).toBe("Input Bus");
    expect(fallbackColour("a:b@1")).toBe(fallbackColour("a:b@1"));
  });

  it("labels a cell with its name, registry name, place and hatches, never a numeric id", () => {
    const controller = furnace.cells.findIndex((c) => c[0] === 1 && c[1] === 0 && c[2] === 0);
    expect(cellLabel(furnace, controller)).toMatch(/^unnamed block · gregtech:gt\.blockmachines@9001 · layer 1 · 0 east, 0 south/);
    const casing = furnace.cells.findIndex((c) => c[0] === 0 && c[1] === 0 && c[2] === 0);
    expect(cellLabel(furnace, casing)).toBe(
      "Fixture Casing · fixture:casing@0 · layer 1 · -1 east, 0 south of the controller · takes Energy, Input Bus, Maintenance, Output Bus",
    );
    const muffler = furnace.cells.findIndex((c) => c[3] === -1);
    expect(cellLabel(furnace, muffler)).toMatch(/^hatch only .* takes Muffler$/);
  });
});

type Handler = (body: unknown) => { status: number; body: unknown };
let routes: Record<string, Handler>;
let calls: { method: string; url: string; body: unknown }[];

beforeEach(() => {
  calls = [];
  let done: string[] = [];
  routes = {
    "GET /api/build/guide": () => ({ status: 200, body: { ...furnace, done } }),
    "POST /api/build/guide/checklist": (b) => {
      const body = b as { item?: string; done?: boolean; reset?: boolean };
      if (body.reset) done = [];
      else if (body.done) done = [...new Set([...done, body.item as string])].sort();
      else done = done.filter((i) => i !== body.item);
      return { status: 200, body: { done } };
    },
    "GET /api/build/guides": () => ({
      status: 200,
      body: { guides: [{ controller: CONTROLLER, name: "Fixture Furnace", mte: 9001, source: "gtnh-extractor", variants: ["t1"] }] },
    }),
    "GET /api/guide": () => ({ status: 200, body: { present: false, pages: [], files: 0, bytes: 0, frame: null } }),
    "GET /api/shells": () => ({ status: 200, body: { shells: [] } }),
  };
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    const path = url.split("?")[0];
    const answer = routes[`${method} ${path}`] ?? (() => ({ status: 404, body: { error: `no route ${method} ${path}` } }));
    const got = answer(body);
    return new Response(JSON.stringify(got.body), { status: got.status });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("GuideView", () => {
  const q = { controller: CONTROLLER };

  it("draws the footprint, and the layer slider changes the materials shown", async () => {
    render(<GuideView query={q} onQuery={() => undefined} />);
    const scene = await screen.findByTestId("guide-scene");
    expect(calls[0].url).toBe("/api/build/guide?controller=gregtech%3Agt.blockmachines%409001");
    const rows = () =>
      Object.fromEntries(
        Array.from(screen.getByTestId("guide-layer-materials").querySelectorAll("tr")).map((tr) => [
          tr.getAttribute("data-block"),
          Number(tr.getAttribute("data-count")),
        ]),
      );
    for (const layer of furnace.layers) {
      fireEvent.change(screen.getByTestId("guide-layer"), { target: { value: String(layer.y) } });
      expect(rows()).toEqual(layer.materials); // equals the footprint's counts for that layer
      const drawn = new Set(Array.from(scene.querySelectorAll("polygon")).map((p) => furnace.cells[Number(p.getAttribute("data-cell"))][1]));
      expect(Math.max(...drawn)).toBe(layer.y);
    }
    // The BOM is the whole structure's.
    const bom = within(screen.getByTestId("guide-bom"));
    expect(bom.getByText("fixture:casing@0").closest("tr")?.getAttribute("data-count")).toBe("16");
    expect(screen.getByTestId("guide-bom").textContent).toContain("25 blocks");
  });

  it("says a block's name and registry name on hover", async () => {
    render(<GuideView query={q} onQuery={() => undefined} />);
    const scene = await screen.findByTestId("guide-scene");
    fireEvent.change(screen.getByTestId("guide-layer"), { target: { value: "2" } });
    const face = Array.from(scene.querySelectorAll("polygon")).find((p) => furnace.cells[Number(p.getAttribute("data-cell"))][3] === 0);
    if (!face) throw new Error("no casing face");
    fireEvent.pointerMove(face);
    expect(screen.getByTestId("guide-hover").textContent).toMatch(/Fixture Casing · fixture:casing@0 · layer \d/);
    expect(face.getAttribute("class")).toContain("hovered");
    fireEvent.pointerLeave(scene);
    expect(screen.getByTestId("guide-hover").textContent).toContain("Hover or tap a block");
  });

  it("marks hatch slots with the kinds they take", async () => {
    render(<GuideView query={q} onQuery={() => undefined} />);
    const scene = await screen.findByTestId("guide-scene");
    fireEvent.change(screen.getByTestId("guide-layer"), { target: { value: "2" } });
    const marked = scene.querySelectorAll("polygon.hatch-slot");
    expect(marked.length).toBeGreaterThan(0);
    for (const p of marked) expect(furnace.cells[Number(p.getAttribute("data-cell"))][4]).toBeGreaterThanOrEqual(0);
    expect(scene.querySelectorAll("polygon.hatch-only").length).toBeGreaterThan(0); // the muffler's cell
    const slots = screen.getByTestId("guide-slots");
    expect(slots.textContent).toContain("8 cells take Energy, Input Bus, Maintenance, Output Bus");
    expect(slots.textContent).toContain("1 cell takes Muffler");
  });

  it("marks the controller's front and turns the compass with the drawing (review M1)", async () => {
    render(<GuideView query={q} onQuery={() => undefined} />);
    const scene = await screen.findByTestId("guide-scene");
    expect(furnace.front).toEqual([0, 0, -1]); // the fixture's controller faces north
    const [ax, ay, az] = furnace.controller_at;
    const controller = furnace.cells.findIndex((c) => c[0] === ax && c[1] === ay && c[2] === az);
    expect(screen.getByTestId("guide-front-arrow").getAttribute("d")).toMatch(/^M[-\d.,]+ L[-\d.,]+ L[-\d.,]+ Z$/);
    const northY = () => Number(screen.getByTestId("guide-compass").querySelector('[data-letter="N"]')?.getAttribute("y"));
    expect(northY()).toBeLessThan(0); // the first view looks from the south-east: north is away, up the screen
    // From the first view the north face looks away; turned half round it faces the viewer.
    expect(scene.querySelector("polygon.controller-front")).toBeNull();
    for (let i = 0; i < 4; i++) fireEvent.click(screen.getByTestId("guide-turn-right"));
    const front = scene.querySelector("polygon.controller-front");
    expect(Number(front?.getAttribute("data-cell"))).toBe(controller);
    expect(front?.getAttribute("class")).toContain("controller");
    expect(northY()).toBeGreaterThan(0); // now north is towards the viewer, down the screen
    expect(screen.getByTestId("guide-compass").getAttribute("aria-label")).toContain("faces north");
  });

  it("puts the compass's letters where the directions lie on the screen", () => {
    const at = (yaw: number) => Object.fromEntries(compass({ yaw, pitch: 30 }).map((c) => [c.letter, [c.x, c.y]]));
    const flat = at(0); // from the south: east to the right, north up the screen
    expect(flat.E[0]).toBeCloseTo(1);
    expect(flat.N[1]).toBeCloseTo(-1);
    expect(at(90).N[0]).toBeCloseTo(1); // turned a quarter: north is now to the right
  });

  it("turns and zooms", async () => {
    render(<GuideView query={q} onQuery={() => undefined} />);
    const scene = await screen.findByTestId("guide-scene");
    const before = scene.getAttribute("viewBox");
    fireEvent.click(screen.getByTestId("guide-turn-right"));
    expect(scene.getAttribute("data-yaw")).toBe(String(DEFAULT_VIEW.yaw + 45));
    fireEvent.click(screen.getByTestId("guide-zoom-in"));
    expect(scene.getAttribute("data-zoom")).toBe("1.25");
    expect(scene.getAttribute("viewBox")).not.toBe(before);
    fireEvent.click(screen.getByTestId("guide-view-reset"));
    expect(scene.getAttribute("data-yaw")).toBe(String(DEFAULT_VIEW.yaw));
  });

  it("keeps the checklist in the core", async () => {
    const { unmount } = render(<GuideView query={q} onQuery={() => undefined} />);
    await screen.findByTestId("guide-scene");
    const box = () => screen.getByTestId("guide-checklist").querySelector<HTMLInputElement>('[data-item="layer:1"]');
    await act(async () => {
      fireEvent.click(box() as HTMLInputElement);
    });
    expect(calls.at(-1)).toEqual({
      method: "POST",
      url: "/api/build/guide/checklist",
      body: { controller: CONTROLLER, variant: null, facing: null, tiers: [], item: "layer:1", done: true },
    });
    expect(box()?.checked).toBe(true);
    expect(screen.getByTestId("guide-checklist").textContent).toContain("1 of 3");
    unmount();
    // Opened again (a reload, the phone): ticked from the core.
    render(<GuideView query={q} onQuery={() => undefined} />);
    await screen.findByTestId("guide-scene");
    expect(box()?.checked).toBe(true);
    await act(async () => {
      fireEvent.click(screen.getByTestId("guide-check-reset"));
    });
    expect(box()?.checked).toBe(false);
  });

  it("names each tier option by the catalog name the core sends, else the palette's, else the key", async () => {
    const options = async () => {
      const select = await screen.findByTestId("guide-tier-coil");
      return Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    };
    render(<GuideView query={q} onQuery={() => undefined} />);
    // the fixture: coil@0 named (also in the palette), coil@1 and coil@2 not in the catalog
    expect(await options()).toEqual(["1 · Fixture Coil", "2 · fixture:coil@1", "3 · fixture:coil@2"]);
    cleanup();
    const named = structuredClone(furnace);
    named.choices.coil[1].name = "Fixture Coil Mk II"; // a tier the default doesn't build: not in the palette
    named.choices.coil[0].name = null; // an older core without the name: the palette's
    routes["GET /api/build/guide"] = () => ({ status: 200, body: named });
    render(<GuideView query={q} onQuery={() => undefined} />);
    expect(await options()).toEqual(["1 · Fixture Coil", "2 · Fixture Coil Mk II", "3 · fixture:coil@2"]);
  });

  it("asks the core again for another facing or tier", async () => {
    const onQuery = vi.fn();
    render(<GuideView query={q} onQuery={onQuery} />);
    await screen.findByTestId("guide-scene");
    fireEvent.change(screen.getByTestId("guide-facing"), { target: { value: "west" } });
    expect(onQuery).toHaveBeenLastCalledWith({ controller: CONTROLLER, facing: "west" });
    fireEvent.change(screen.getByTestId("guide-tier-coil"), { target: { value: "3" } });
    expect(onQuery).toHaveBeenLastCalledWith({ controller: CONTROLLER, tiers: { coil: 3 } });
  });
});

describe("Build", () => {
  it("opens the guide the address names, and picks one from the list", async () => {
    // An export on disk, asked for on the tailnet name: no frame there.
    routes["GET /api/guide"] = () => ({
      status: 200,
      body: { present: true, pages: [{ path: "gos/en_us/a.html", title: "A", scenes: 1 }], files: 3, bytes: 10, frame: null },
    });
    window.location.hash = `/build?guide=${encodeURIComponent(CONTROLLER)}`;
    render(<Build />);
    expect(await screen.findByTestId("guide-scene")).toBeTruthy();
    expect(await screen.findByTestId("guide-frame-pc-only")).toBeTruthy(); // frame: null
    const choose = await screen.findByTestId("guide-choose");
    expect(within(choose).getByText("Fixture Furnace (mte:9001)")).toBeTruthy();
  });

  it("asks for a new frame token when the socket opens again after a core restart (review L2)", async () => {
    let n = 0;
    routes["GET /api/guide"] = () => {
      n += 1;
      const base = `http://guide-gos.localhost:8765/guide/1800000000-${String(n).padStart(32, "0")}/`;
      return {
        status: 200,
        body: { present: true, pages: [{ path: "gos/a.html", title: "A", scenes: 1 }], files: 1, bytes: 1, frame: { base, expires: 1800000000 } },
      };
    };
    act(() => store.set({ socket: "open" }));
    render(<Build />);
    const frame = await screen.findByTestId("guide-frame");
    expect(frame.getAttribute("src")).toMatch(/0001\/gos\/a\.html$/);
    act(() => store.set({ socket: "closed" }));
    act(() => store.set({ socket: "open" }));
    await waitFor(() => expect(screen.getByTestId("guide-frame").getAttribute("src")).toMatch(/0002\/gos\/a\.html$/));
    expect(calls.filter((c) => c.url === "/api/guide")).toHaveLength(2);
    act(() => store.set({ socket: "closed" }));
  });
});
