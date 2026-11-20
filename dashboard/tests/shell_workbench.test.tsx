// WO-10.10: the shell workbench in the Build view (D367-D370), against a stubbed core: the form's
// job bodies, the version list (what was asked, by whom, blocks and cost, the current one), make
// current, compare (material and block differences side by side), Design and Revise as jobs with
// a stage and a Cancel, and the 3D viewer's layer slider and ghosts.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ShellDesign, ShellScene } from "../src/api/types";
import { stageText } from "../src/state/jobs";
import { ShellWorkbench, byText, costText, ghostsOf, jobBody } from "../src/views/ShellWorkbench";
import { boxEdges, DEFAULT_VIEW } from "../src/views/guideMesh";

const V1 = "sh-aaaaaaaaaaaa";
const V2 = "sh-bbbbbbbbbbbb";
const DS = "ds-111111111111";

function version(n: number, build: string, over: Partial<ShellDesign["versions"][0]> = {}) {
  return {
    n,
    build,
    parent: n === 1 ? null : V1,
    asked: n === 1 ? "a foundry shell" : "make the roof steeper",
    by: "procedural" as const,
    at: 1_794_000_000 + n,
    cost_usd: 0,
    status: "accepted" as const,
    blocks: n === 1 ? 714 : 750,
    theme: "th-foundry@v1",
    headroom: 4,
    max_blocks: 20000,
    ...over,
  };
}

let design: ShellDesign;

const scene = (build: string): ShellScene => ({
  build,
  size: [4, 3, 4],
  palette: [
    { block: "gregtech:gt.blockconcretes@3", name: "Concrete", role: "wall", count: 3, top: null, side: null },
    { block: "minecraft:stone_slab@0", name: "Slab", role: "roof", count: 2, top: "#888888", side: null },
  ],
  cells: [
    [0, 0, 0, 0, -1],
    [1, 0, 0, 0, -1],
    [0, 1, 0, 0, -1],
    [0, 2, 0, 1, -1],
    [1, 2, 0, 1, -1],
  ],
  layers: [
    { y: 0, blocks: 2, materials: { "gregtech:gt.blockconcretes@3": 2 } },
    { y: 1, blocks: 1, materials: { "gregtech:gt.blockconcretes@3": 1 } },
    { y: 2, blocks: 2, materials: { "minecraft:stone_slab@0": 2 } },
  ],
  ghosts: { machines: [{ id: "m1", name: "Furnace", ref: "mte:9001", lo: [1, 0, 1], hi: [2, 1, 2] }], keepouts: [{ id: "k1", lo: [1, 0, 1], hi: [2, 1, 2] }] },
  caveats: [],
});

const shellCard = (build: string) => ({
  type: "shell",
  title: `Shell ${build}`,
  ref: "plan:pl-steel",
  build,
  layout: "lo-x",
  status: "accepted",
  source: "procedural",
  theme: "th-foundry@v1",
  size: { x: 4, y: 3, z: 4 },
  blocks: 5,
  max_blocks: 20000,
  palette: [],
  faces: [],
  paste: ["Lab only: stand in e1's lo cell"],
  notes: [],
});

type Route = (body: unknown, url: string) => { status: number; body: unknown };
let routes: Record<string, Route>;
let calls: { method: string; url: string; body: unknown }[];
let jobState: Record<string, unknown>;

beforeEach(() => {
  calls = [];
  design = {
    id: DS,
    plans: ["pl-steel"],
    created: 1_794_000_001,
    current: V2,
    versions: [version(1, V1), version(2, V2, { by: "architect", cost_usd: null })],
    history: [],
    written: true,
  };
  jobState = {};
  routes = {
    "GET /api/shells/designs": () => ({
      status: 200,
      body: {
        designs: [
          { id: DS, plans: ["pl-steel"], current: design.current, current_n: 2, versions: design.versions.length, asked: "a foundry shell", theme: "th-foundry@v1", blocks: 750, touched: 1 },
        ],
        current: DS,
      },
    }),
    [`GET /api/shells/designs/${DS}`]: () => ({ status: 200, body: { design } }),
    [`POST /api/shells/designs/${DS}/current`]: (b) => {
      design = { ...design, current: (b as { build: string }).build };
      return { status: 200, body: { design } };
    },
    "GET /api/shells/themes": () => ({
      status: 200,
      body: { themes: [{ id: "th-foundry", name: "Foundry", v: 1, summary: "" }], line_types: ["general", "smelting"], limits: { max_blocks: [20000, 1, 200000], headroom: [4, 1, 16] }, problems: [] },
    }),
    "GET /api/plans": () => ({ status: 200, body: { plans: [{ id: "pl-steel", name: "Steel", status: "proposed", kind: "production" }], problems: [] } }),
    [`GET /api/shells/${V1}/view`]: () => ({ status: 200, body: scene(V1) }),
    [`GET /api/shells/${V2}/view`]: () => ({ status: 200, body: scene(V2) }),
    [`GET /api/shells/${V1}`]: () => ({ status: 200, body: { card: shellCard(V1) } }),
    [`GET /api/shells/${V2}`]: () => ({ status: 200, body: { card: shellCard(V2) } }),
    "GET /api/shells/compare": () => ({
      status: 200,
      body: {
        a: { build: V1, status: "accepted", source: "procedural", theme: null, size: [4, 3, 4], blocks: 714, max_blocks: 20000, headroom: 4, types: 2 },
        b: { build: V2, status: "accepted", source: "architect", theme: null, size: [4, 3, 4], blocks: 750, max_blocks: 20000, headroom: 8, types: 2 },
        blocks_delta: 36,
        materials: [{ block: "minecraft:stone_slab@0", name: "Slab", role: "roof", a: 68, b: 104, delta: 36 }],
        ops: ["roof (roof): style flat -> gable"],
      },
    }),
    "POST /api/shells/jobs": () => {
      jobState = { id: "job-0123456789ab", kind: "shell", title: "Shell: revise", status: "running", stage: "gate", k: null, n: null, sub: null, detail: "gate", started: 1, finished: null, elapsed_s: 0, error: null, cancel_asked: false };
      return { status: 200, body: { job: jobState } };
    },
    "GET /api/design-jobs/job-0123456789ab": () => ({ status: 200, body: { job: jobState } }),
    "POST /api/design-jobs/job-0123456789ab/cancel": () => {
      jobState = { ...jobState, cancel_asked: true };
      return { status: 200, body: { job: jobState } };
    },
  };
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    const path = url.split("?")[0];
    const answer = routes[`${method} ${path}`] ?? (() => ({ status: 404, body: { error: `no route ${method} ${path}` } }));
    const got = answer(body, url);
    return new Response(JSON.stringify(got.body), { status: got.status });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const form = { words: "", theme: "", plans: ["pl-steel"], lineType: "general", maxBlocks: "", headroom: "" };

describe("the workbench's job bodies", () => {
  it("designs with the plan, theme, words and the sizes the gate supports", () => {
    expect(jobBody("design", { ...form, words: "tall windows", theme: "th-foundry", maxBlocks: "9000", headroom: "6" }, null)).toEqual({
      design: { plans: ["pl-steel"], theme: "th-foundry", line_type: "general", prompt: "tall windows", max_blocks: 9000, headroom: 6 },
    });
    expect(jobBody("design", { ...form, plans: [] }, null)).toMatch(/plan/);
    expect(jobBody("design", { ...form, headroom: "2.5" }, null)).toMatch(/whole number/);
  });
  it("revises the current design, and needs something to change", () => {
    expect(jobBody("revise", { ...form, words: " no lights " }, DS)).toEqual({ revise: { design: DS, words: "no lights" } });
    expect(jobBody("revise", form, DS)).toMatch(/Say what to change/);
    expect(jobBody("revise", { ...form, words: "x" }, null)).toMatch(/Design a shell first/);
  });
  it("says cost and author plainly, and a shell job's stages in words", () => {
    expect(costText({ cost_usd: 0, by: "procedural" })).toBe("free");
    expect(costText({ cost_usd: null, by: "architect" })).toBe("AI (see Costs)");
    expect(costText({ cost_usd: 0.4, by: "architect" })).toBe("$0.40");
    expect(byText({ by: "architect" })).toBe("the architect");
    expect(stageText({ kind: "shell", stage: "gate", k: null, n: null, sub: null })).toBe("Checking it (the gate)");
  });
  it("draws ghosts as box edges", () => {
    const g = ghostsOf(scene(V1));
    expect(g.map((x) => x.kind)).toEqual(["keepout", "machine"]);
    expect(g[1].label).toBe("Furnace (mte:9001)");
    expect(boxEdges([4, 3, 4], g[0], DEFAULT_VIEW).match(/M/g)).toHaveLength(12);
  });
});

describe("ShellWorkbench", () => {
  it("lists the versions with what was asked, by whom, blocks and cost, and draws the current one", async () => {
    render(<ShellWorkbench />);
    const list = await screen.findByTestId("shell-version-list");
    await waitFor(() => expect(list.querySelectorAll("tbody tr")).toHaveLength(2));
    const rows = list.querySelectorAll("tbody tr");
    expect(rows[0].getAttribute("data-version")).toBe("2"); // the newest first
    expect(within(rows[0] as HTMLElement).getByTestId("shell-current-mark")).toBeTruthy();
    expect(screen.getByTestId("shell-cost-2").textContent).toBe("AI (see Costs)");
    expect(screen.getByTestId("shell-cost-1").textContent).toBe("free");
    expect(screen.getByTestId("shell-blocks-1").textContent).toBe("714");
    expect(rows[0].textContent).toContain("make the roof steeper");
    const view = await screen.findByTestId("shell3d-view");
    expect(view.getAttribute("data-build")).toBe(V2);
    expect(screen.getByTestId("shell3d-scene").getAttribute("data-ghosts")).toBe("2");
  });

  it("the layer slider shows that layer's materials; ghosts can be hidden", async () => {
    render(<ShellWorkbench />);
    const slider = await screen.findByTestId("shell3d-layer");
    const mats = () => Array.from(screen.getByTestId("shell3d-layer-materials").querySelectorAll("tr")).map((r) => r.getAttribute("data-block"));
    expect(mats()).toEqual(["minecraft:stone_slab@0"]); // the top layer first
    const facesTop = Number(screen.getByTestId("shell3d-scene").getAttribute("data-faces"));
    fireEvent.change(slider, { target: { value: "0" } });
    expect(mats()).toEqual(["gregtech:gt.blockconcretes@3"]);
    expect(Number(screen.getByTestId("shell3d-scene").getAttribute("data-faces"))).toBeLessThan(facesTop);
    fireEvent.click(screen.getByTestId("shell3d-ghosts"));
    expect(screen.getByTestId("shell3d-scene").getAttribute("data-ghosts")).toBe("0");
    const yaw = screen.getByTestId("shell3d-scene").getAttribute("data-yaw");
    fireEvent.click(screen.getByTestId("shell3d-turn-right"));
    expect(screen.getByTestId("shell3d-scene").getAttribute("data-yaw")).not.toBe(yaw);
    fireEvent.click(screen.getByTestId("shell3d-zoom-in"));
    expect(screen.getByTestId("shell3d-scene").getAttribute("data-zoom")).toBe("1.25");
  });

  it("makes an older version current (undo) and compares two side by side", async () => {
    render(<ShellWorkbench />);
    fireEvent.click(await screen.findByTestId("shell-make-current-1"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/current"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/current"))?.body).toEqual({ build: V1 });
    await waitFor(() => expect(screen.getByTestId("shell3d-view").getAttribute("data-build")).toBe(V1));
    fireEvent.click(screen.getByTestId("shell-compare-1"));
    fireEvent.click(screen.getByTestId("shell-compare-2"));
    const cmp = await screen.findByTestId("shell-compare");
    await within(cmp).findByTestId("shell-compare-materials");
    expect(within(cmp).getByTestId("shell-compare-blocks").getAttribute("data-delta")).toBe("36");
    const row = within(cmp).getByTestId("shell-compare-materials").querySelector('tr[data-block="minecraft:stone_slab@0"]');
    expect(row?.getAttribute("data-delta")).toBe("36");
    expect(await within(cmp).findByTestId("shell3d-a-view")).toBeTruthy();
    expect(await within(cmp).findByTestId("shell3d-b-view")).toBeTruthy();
  });

  it("revises as a job with its stage and a Cancel, then shows what changed", async () => {
    render(<ShellWorkbench />);
    await screen.findByTestId("shell-version-list");
    fireEvent.change(screen.getByTestId("shell-words"), { target: { value: "no lights" } });
    await waitFor(() => expect((screen.getByTestId("shell-revise") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("shell-revise"));
    const stage = await screen.findByTestId("shell-job-stage");
    expect(stage.textContent).toContain("Checking it (the gate)");
    expect(calls.find((c) => c.url === "/api/shells/jobs")?.body).toEqual({ revise: { design: DS, words: "no lights" } });
    fireEvent.click(screen.getByTestId("shell-cancel"));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/cancel"))).toBe(true));
    await act(async () => {
      jobState = { ...jobState, status: "done", result: { design: DS, build: V2, changes: ["lights: none"] } };
      await new Promise((r) => setTimeout(r, 1100));
    });
    await waitFor(() => expect(screen.getByTestId("shell-notice").textContent).toContain("lights: none"));
  });

  it("says a refused revision wrote nothing", async () => {
    render(<ShellWorkbench />);
    await screen.findByTestId("shell-version-list");
    routes["POST /api/shells/jobs"] = () => {
      jobState = { id: "job-0123456789ab", kind: "shell", title: "x", status: "done", stage: "gate", k: null, n: null, sub: null, detail: null, started: 1, finished: 2, elapsed_s: 0, error: null, cancel_asked: false, result: { refused: ["GOS's reviser can't do 'gothic'"] } };
      return { status: 200, body: { job: jobState } };
    };
    fireEvent.change(screen.getByTestId("shell-words"), { target: { value: "gothic" } });
    fireEvent.click(screen.getByTestId("shell-revise"));
    await waitFor(() => expect(screen.getByTestId("shell-notice").textContent).toMatch(/Refused, nothing written.*gothic/));
  });

  it("asks the architect through the router, and shows the empty state", async () => {
    routes["POST /api/ask"] = () => ({ status: 200, body: { text: "On it.", lane: "deep", intent: "shell_revise" } });
    render(<ShellWorkbench />);
    await screen.findByTestId("shell-version-list");
    fireEvent.change(screen.getByTestId("shell-words"), { target: { value: "make the roof steeper" } });
    fireEvent.click(screen.getByTestId("shell-ask"));
    await waitFor(() => expect(screen.getByTestId("shell-notice").textContent).toContain("The architect revises the current shell"));
    expect(calls.find((c) => c.url === "/api/ask")?.body).toEqual({ text: "make the roof steeper" });
    cleanup();
    routes["GET /api/shells/designs"] = () => ({ status: 200, body: { designs: [], current: null } });
    render(<ShellWorkbench />);
    expect(await screen.findByTestId("shells-empty")).toBeTruthy();
    expect((screen.getByTestId("shell-revise") as HTMLButtonElement).disabled).toBe(true);
  });
});
