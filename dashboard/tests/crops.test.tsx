// The Crops view (WO-6.7): the core's guide in its order, the chosen step's layout grid, its
// requirements and numbers with citation chips, following a crop, his crops (edits sent as he
// typed them, answered with the view) and the empty and error states. Every name and number here
// is made up.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CropLine, CropStep, CropsView } from "../src/api/types";
import { Crops, parseStats, shownStep } from "../src/views/Crops";

const line = (text: string, refs: string[]): CropLine => ({ text, refs, nums: [] });

function step(n: number, kind: CropStep["kind"], child: string, done = false): CropStep {
  const sticks = kind === "sticks" || kind === "pool";
  const cell = (role: "parent" | "double" | "empty", name: string | null = null, under: string | null = null) => ({
    role,
    crop: name ? `cropsnh:${name.toLowerCase()}` : null,
    name,
    under,
  });
  return {
    n,
    kind,
    child: `cropsnh:${child.toLowerCase()}`,
    child_name: child,
    ref: `crop:cropsnh:${child.toLowerCase()}`,
    parents: [],
    title: `${kind} ${child}`,
    layout: sticks
      ? [
          [cell("empty"), cell("parent", "Carrot"), cell("empty")],
          [cell("empty"), cell("double", child, "Stone"), cell("empty")],
          [cell("empty"), cell("parent", "Reed"), cell("empty")],
        ]
      : [],
    requirements: [
      { what: "sub_soil", status: "needed", ...line("Child: Stone directly under the soil block", [`crop:cropsnh:${child.toLowerCase()}`]) },
      { what: "nutrients", status: "met", ...line("Child: needs 2 points; the setup gives 27 points", ["crop:cropsnh:x"]) },
    ],
    numbers: [line("Chance per growth tick (every 256 ticks): 16.7%", ["crop:cropsnh:a", "crop:cropsnh:b"])],
    warnings: [line("Weeds: 1% of growth ticks", ["crop:cropsnh:a"])],
    keep: [line("Check each child with the Plant Lens", ["crop:cropsnh:a"])],
    refs: [],
    done,
  };
}

function view(withGuide = true, following = false): CropsView {
  return {
    run: "20261111T000000Z",
    tier: 1,
    tier_name: "LV",
    have: [{ crop: "cropsnh:reed", ref: "crop:cropsnh:reed", name: "Reed", stats: [10, 4, 31] }],
    target: following ? { crop: "cropsnh:lily", ref: "crop:cropsnh:lily", name: "Lily", aim: [31, 31, 31] } : null,
    crops: [
      { crop: "cropsnh:lily", ref: "crop:cropsnh:lily", name: "Lily", tier: 1 },
      { crop: "cropsnh:reed", ref: "crop:cropsnh:reed", name: "Reed", tier: 2 },
    ],
    picks: [{ crop: "cropsnh:lily", ref: "crop:cropsnh:lily", name: "Lily" }],
    picks_note: "the PM's default picks, pending Clayton's",
    setup: { water: 100, sky: true, liked_biome_tags: 0, crop_manager: false },
    guide: withGuide
      ? {
          target: "cropsnh:lily",
          target_name: "Lily",
          ref: "crop:cropsnh:lily",
          tier: 1,
          tier_name: "LV",
          run: "20261111T000000Z",
          steps: [step(1, "crop_manager", "Lily", true), step(2, "obtain", "Carrot"), step(3, "sticks", "Lily")],
          next: 2,
          total: line("Expected time for every step: 20 min", ["crop:cropsnh:lily"]),
          stats: { target: [31, 31, 31], variations: [0, 1], lines: [line("Growth 1 to 31: about 33 children", ["crop:cropsnh:lily"])], warnings: [] },
          notes: [],
        }
      : null,
    error: null,
  };
}

let urls: string[];
let methods: string[];
let bodies: unknown[];
let queue: { status: number; body: unknown }[];
// The breeding station panel (WO-7.6) polls its own route; answered apart from the view's queue.
let station: { status: number; body: unknown };
let stationCalls: { url: string; method: string; body: unknown }[];

beforeEach(() => {
  urls = [];
  methods = [];
  bodies = [];
  queue = [];
  station = { status: 200, body: { status: [], plan: null, error: null } };
  stationCalls = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.startsWith("/api/crops/station")) {
      stationCalls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
      return new Response(JSON.stringify(station.body), { status: station.status });
    }
    urls.push(url);
    methods.push(init?.method ?? "GET");
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
  it("shows the picked step, else the next, else the last", () => {
    const steps = [step(1, "obtain", "A", true), step(2, "sticks", "B")];
    expect(shownStep(steps, 2, null)?.n).toBe(2);
    expect(shownStep(steps, 2, 1)?.n).toBe(1);
    expect(shownStep(steps, null, null)?.n).toBe(2);
    expect(shownStep([], null, null)).toBeNull();
  });

  it("reads three stats from 1 to 31", () => {
    expect(parseStats("10 4 31")).toEqual([10, 4, 31]);
    expect(parseStats("10/4/31")).toEqual([10, 4, 31]);
    expect(parseStats("0 1 1")).toBeNull();
    expect(parseStats("1 1")).toBeNull();
    expect(parseStats("1 1 32")).toBeNull();
    expect(parseStats("a b c")).toBeNull();
  });
});

describe("Crops view", () => {
  it("draws the path in the core's order and the next step's layout with cited lines", async () => {
    queue.push({ status: 200, body: view() });
    render(<Crops />);
    const steps = await screen.findByTestId("crops-steps");
    const items = within(steps).getAllByRole("button");
    expect(items.map((b) => b.textContent)).toEqual([
      "✓ done1. crop_manager Lily",
      "◐ next2. obtain Carrot",
      "○ to do3. sticks Lily",
    ]);
    expect(screen.getByTestId("crops-step").getAttribute("data-step")).toBe("2");
    fireEvent.click(screen.getByTestId("crops-step-3"));
    const grid = screen.getByTestId("crops-layout");
    expect(grid.querySelectorAll(".crop-cell.parent")).toHaveLength(2);
    expect(grid.querySelector(".crop-cell.double")?.textContent).toContain("under it: Stone");
    const reqs = screen.getByTestId("crops-reqs");
    expect(within(reqs).getAllByText("needed")).toHaveLength(1);
    const numbers = screen.getByTestId("crops-numbers");
    expect(numbers.textContent).toContain("16.7%");
    expect(numbers.querySelectorAll("[data-cite]")).toHaveLength(2);
    expect(screen.getByTestId("crops-stats").textContent).toContain("about 33 children");
    expect(screen.getByTestId("crops-picks").textContent).toContain("pending Clayton");
    // the times are the best case, and the panel says so (review M1)
    const setup = screen.getByTestId("crops-setup-text").textContent ?? "";
    expect(setup).toContain("best case for water and fertilizer");
    expect(setup).toContain("by hand, though a stick uses some of each every growth tick");
    expect(urls).toEqual(["/api/crops"]);
  });

  it("follows the crop shown, and looks another up by name", async () => {
    queue.push({ status: 200, body: view() });
    render(<Crops />);
    queue.push({ status: 200, body: view(true, true) });
    fireEvent.click(await screen.findByTestId("crops-follow"));
    expect(await screen.findByText(/Following Lily/)).toBeTruthy();
    expect(bodies[1]).toEqual({ crop: "cropsnh:lily" });
    expect(screen.queryByTestId("crops-follow")).toBeNull();
    queue.push({ status: 200, body: view() });
    fireEvent.change(screen.getByTestId("crops-input"), { target: { value: "reed" } });
    fireEvent.click(screen.getByTestId("crops-go"));
    await screen.findByTestId("crops-follow");
    expect(urls[2]).toBe("/api/crops?crop=cropsnh%3Areed");
  });

  it("adds a crop he has as typed and refuses bad stats without sending", async () => {
    queue.push({ status: 200, body: view() });
    render(<Crops />);
    await screen.findByTestId("crops-have");
    fireEvent.change(screen.getByTestId("crops-have-crop"), { target: { value: "Lily" } });
    fireEvent.change(screen.getByTestId("crops-have-stats"), { target: { value: "1 2" } });
    fireEvent.click(screen.getByTestId("crops-have-add"));
    expect((await screen.findByTestId("crops-note")).textContent).toContain("three stats");
    expect(urls).toHaveLength(1);
    queue.push({ status: 200, body: view() });
    fireEvent.change(screen.getByTestId("crops-have-stats"), { target: { value: "3 2 1" } });
    fireEvent.click(screen.getByTestId("crops-have-add"));
    await vi.waitFor(() => expect(urls).toHaveLength(2));
    expect(methods[1]).toBe("POST");
    expect(bodies[1]).toEqual({ crop: "cropsnh:lily", stats: [3, 2, 1] });
    queue.push({ status: 200, body: view() });
    fireEvent.click(screen.getByText("Remove"));
    await vi.waitFor(() => expect(urls).toHaveLength(3));
    expect(methods[2]).toBe("DELETE");
    expect(urls[2]).toBe("/api/crops/have?crop=cropsnh%3Areed");
  });

  it("says why there is no guide", async () => {
    queue.push({ status: 200, body: { ...view(false), error: "no way to Lonely (crop:cropsnh:lonely) from your crops" } });
    render(<Crops />);
    expect((await screen.findByTestId("crops-error")).textContent).toContain("no way to Lonely");
  });

  it("shows the core's error before a DB exists", async () => {
    queue.push({ status: 404, body: { error: "no crop data yet: run gos db build" } });
    render(<Crops />);
    expect((await screen.findByTestId("crops-empty")).textContent).toContain("gos db build");
  });
});

describe("Breeding station panel", () => {
  it("shows each station's last report, marks a stale one, and only proposes", async () => {
    station = {
      status: 200,
      body: {
        status: [
          {
            binding: "lab",
            address: "1a000000-0000-4000-8000-000000000001",
            ref: "tel:lab/1a000000-0000-4000-8000-000000000001",
            age_s: 3,
            stale: false,
            state: "running",
            runs: 5,
            ok: 2,
            fail: 3,
            stock: { "Lily Seeds": 1 },
            target: "Lily Seeds",
            count: 4,
            steps: 3,
            step: 2,
            note: "step 2: bred Reed Seeds (run 5)",
          },
          {
            binding: "lab",
            address: "1a000000-0000-4000-8000-000000000002",
            ref: "tel:lab/1a000000-0000-4000-8000-000000000002",
            age_s: 95,
            stale: true,
            state: "running",
            runs: 0,
            ok: 0,
            fail: 0,
            stock: {},
          },
        ],
        plan: {
          id: "0a1b2c3d",
          target: "cropsnh:lily",
          target_name: "Lily",
          count: 4,
          tier: 2,
          steps: [{ n: 1, child: "cropsnh:lily", child_name: "Lily", chance_pct: 50, runs: 4, copies: 5 }],
          lines: ["Step 1: Carrot + Reed -> Lily: 50% a run [r:x, mte:28026]"],
          refs: ["r:x"],
        },
        error: null,
      },
    };
    queue.push({ status: 200, body: view() });
    render(<Crops />);
    const rows = await screen.findAllByTestId("crops-station-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("step 2 of 3");
    expect(rows[0].textContent).toContain("Lily Seeds: 1 of 4");
    expect(rows[0].querySelectorAll("[data-cite]")).toHaveLength(1);
    expect(rows[1].textContent).toContain("stale, 95 s old");
    expect(screen.getByTestId("crops-station-plan").textContent).toContain("50% a run");
    expect(stationCalls[0].url).toBe("/api/crops/station?crop=cropsnh%3Alily");
    station = {
      status: 200,
      body: { ok: true, say: "Start the breeding station: breed Lily Seeds in 1 Crop Breeder step and stock 4 in Lab. Say confirm.", action: { id: 7, state: "proposed" } },
    };
    fireEvent.click(screen.getByTestId("crops-station-start"));
    expect((await screen.findByTestId("crops-station-said")).textContent).toContain("Say confirm.");
    const post = stationCalls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ action: "start", crop: "cropsnh:lily" });
  });

  it("says when no station has reported, and a stop proposes too", async () => {
    queue.push({ status: 200, body: view(false) });
    render(<Crops />);
    expect((await screen.findByTestId("crops-station-none")).textContent).toContain("No OC computer");
    expect((screen.getByTestId("crops-station-start") as HTMLButtonElement).disabled).toBe(true);
    station = { status: 503, body: { error: "the action desk isn't running in this core" } };
    fireEvent.click(screen.getByTestId("crops-station-stop"));
    expect((await screen.findByTestId("crops-station-said")).textContent).toContain("action desk");
    expect(stationCalls.find((c) => c.method === "POST")?.body).toEqual({ action: "stop" });
  });
});
