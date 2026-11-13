// The Power view (WO-4.6): the ledger per tier (a tier opens its draws), its warnings and the
// storage checks; the four modes; the options in the core's order with the weights printed, net =
// gross − chain, the effort card, the validator's verdict, the ledger preview, and accepting after
// a confirm. Every name and number here is made up.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PowerLedgerSection, PowerOption, PowerView } from "../src/api/types";
import type { PowerLedgerCard, TableCard } from "../src/cards/types.gen";
import { MODES, Power, atTier, parseWindow, powerFromHash } from "../src/views/Power";

const WEIGHTS = "mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1";

function ledgerCard(tiers: { tier: "LV" | "MV"; gen: number; use: number }[]): PowerLedgerCard {
  return {
    type: "power_ledger",
    title: "EU ledger",
    source: { kind: "plan", label: "Accepted plans", age_s: null },
    tiers: tiers.map((t) => ({ ...t, net: t.gen - t.use, ref: null })),
    notes: [],
  };
}

function section(withLines = true): PowerLedgerSection {
  const card = ledgerCard(withLines ? [{ tier: "LV", gen: 0, use: 40 }] : []);
  return {
    card,
    running: ledgerCard([]),
    warnings: withLines
      ? [{ kind: "deficit", tier: null, detail: "lines use 40 EU/t on average, sources give 0 EU/t: short 40 EU/t", plans: ["plan:pl-aaa"] }]
      : [],
    running_warnings: [],
    lines: withLines ? 1 : 0,
    entries: withLines
      ? [{ plan: "pl-aaa", kind: "production", name: "Test widgets", status: "accepted", ref: "plan:pl-aaa", draws: [{ tier: "LV", eut_avg: 40, eut_peak: 64, amps: 2 }], source: null }]
      : [],
    window_s: 0,
    distribution: { transformers: [], buffers: [], notes: ["give a peak window (seconds) to size buffers and an LSC"] },
  };
}

const effortCard: TableCard = {
  type: "table",
  tool: "effort_estimate",
  error: null,
  title: "Effort",
  head: [{ key: "plan", value: "plan:pw-one" }],
  columns: ["part", "map", "machine", "recipe", "crafts", "machines", "hours"],
  rows: [["chain", "gt.recipe.mixer", "mte:7", "r:gt.recipe.mixer/0123456789ab", "2", "1", "30 s"]],
  span: null,
  total: null,
  notes: [],
};

function option(id: string, position: number, net: number, extra: Partial<PowerOption> = {}): PowerOption {
  return {
    id,
    name: `Test generator · ${net} EU/t net · MV · option ${position}`,
    status: "proposed",
    position,
    of: 2,
    mode: "best",
    score: 0.5 - position / 10,
    eut_per_effort_h: null,
    terms: {},
    values: {},
    rank_notes: [],
    differs: position === 1 ? [] : ["fuel Test Acid"],
    weights: WEIGHTS,
    net_eut: net,
    gross_eut: net + 4,
    chain_eut: 4,
    units: 1,
    generator: { label: `Test Combustion Generator ${position}`, kind: "single_block", tier: "MV", eut: net + 4, cites: ["mte:110"] },
    fuel: { id: "fluid:testgas", name: "Test Gas", rate_s: 0.5, ref: "r:gt.recipe.dieselgeneratorfuel/0123456789ab" },
    distribution: { tier: "MV", voltage: 128, amps: 1 },
    upkeep_h: 0,
    machines: 3,
    effort: { eff_h: 1.5, build_h: 1, unlock_h: 0.5, uncounted: 2, card: effortCard },
    closed: { ok: position === 1, imports: position === 1 ? [] : [{ id: "fluid:testacid", name: "Test Acid", rate_s: 0.25 }], surplus: [], problems: [] },
    bootstrap: null,
    warnings: [],
    nodes: [{ n: "n1", kind: "recipe", name: "Test Gas", recipe: "r:gt.recipe.mixer/0123456789ab", machine: "Mixer", mte: "mte:7", tier: "LV", count: 1 }],
    cables: [{ ref: "mte:1300", name: "1x Copper Cable", voltage: 128, amps: 1, loss: 2 }],
    ledger: {
      counted: false,
      card: ledgerCard([{ tier: "LV", gen: 0, use: 40 }, { tier: "MV", gen: net, use: 0 }]),
      warnings: [],
      cleared: [{ kind: "deficit", tier: null, detail: "", plans: [] }],
      added: [],
      transformers: [],
    },
    ...extra,
  };
}

function view(over: Partial<PowerView> = {}): PowerView {
  return {
    id: "pw-one",
    ids: ["pw-one", "pw-two"],
    mode: "best",
    mode_label: "Best at tier",
    mode_how: "the weighted score",
    tier_cap: "MV",
    weights: WEIGHTS,
    w: { net_eut: 1, effort: 1, upkeep: 1, complexity: 1, scarcity: 1 },
    notes: ["2 of 4 generator x fuel candidates pruned"],
    rejected: [{ candidate: "gen:x", why: "no route" }],
    // Deliberately the core's order, not sorted by net EU/t: the view must keep it.
    options: [option("pw-one", 1, 28), option("pw-two", 2, 60)],
    ledger: section(),
    ...over,
  };
}

let urls: string[];
let methods: string[];
let bodies: unknown[];
let queue: { status: number; body: unknown }[];

beforeEach(() => {
  urls = [];
  methods = [];
  bodies = [];
  queue = [];
  window.location.hash = "#/power";
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
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
  it("reads the plan from the address", () => {
    expect(powerFromHash("#/power/pw-abc123")).toBe("pw-abc123");
    expect(powerFromHash("#/power")).toBeNull();
    expect(powerFromHash("#/designer/pl-abc")).toBeNull();
  });

  it("reads a peak window in seconds, minutes or hours", () => {
    expect(parseWindow("600")).toBe(600);
    expect(parseWindow("10 min")).toBe(600);
    expect(parseWindow("2h")).toBe(7200);
    expect(parseWindow("0")).toBeNull();
    expect(parseWindow("-5")).toBeNull();
    expect(parseWindow("200h")).toBeNull(); // over a week
    expect(parseWindow("soon")).toBeNull();
  });

  it("lists the plans at a tier from the ledger's own entries", () => {
    expect(atTier(section().entries, "LV")).toEqual([{ ref: "plan:pl-aaa", name: "Test widgets", what: "uses", eut: 40 }]);
    expect(atTier(section().entries, "MV")).toEqual([]);
  });

  it("has the plan's four modes", () => {
    expect(MODES.map((m) => m.id)).toEqual(["best", "max_output", "output_per_effort", "self_sustaining"]);
  });
});

describe("Power view", () => {
  it("shows the ledger first: a tier opens its draws, the warnings name their plans", async () => {
    queue.push({ status: 200, body: { ids: [], ledger: section() } });
    render(<Power />);
    expect(await screen.findByTestId("power-tiers")).toBeTruthy();
    expect(urls[0]).toBe("/api/power?window=0");
    const warnings = screen.getByTestId("power-warnings");
    expect(warnings.textContent).toContain("Deficit");
    expect(within(warnings).getByText("plan:pl-aaa")).toBeTruthy();
    fireEvent.click(screen.getByTestId("power-tier-LV"));
    const draws = screen.getByTestId("power-draws-LV");
    expect(draws.textContent).toContain("Test widgets");
    expect(draws.textContent).toContain("uses 40 EU/t");
    expect(screen.getByTestId("power-empty")).toBeTruthy();
    expect(screen.getByTestId("power-distribution").textContent).toContain("give a peak window");
  });

  it("says so when the ledger is empty", async () => {
    queue.push({ status: 200, body: { ids: [], ledger: section(false) } });
    render(<Power />);
    expect(await screen.findByTestId("power-ledger-empty")).toBeTruthy();
  });

  it("asks the core for buffers for a peak window", async () => {
    queue.push({ status: 200, body: { ids: [], ledger: section() } });
    render(<Power />);
    await screen.findByTestId("power-tiers");
    const withBuffers = section();
    withBuffers.window_s = 600;
    withBuffers.distribution.buffers = [
      {
        tier: "LV", peak_eut: 64, gen_eut: 0, window_s: 600, deficit_eut: 64, energy_eu: 768000,
        battery_buffer: { kind: "battery_buffer", count: 2, unit: "Test Buffer with 4 x Test Battery", unit_ref: "mte:161", unit_eu: 400000, capacity_eu: 800000, detail: "", refs: [], eut_max: 256 },
        lsc: null, recommend: "battery_buffer", reason: "fits in 2 of at most 4 buffers", notes: [],
      },
    ]; // prettier-ignore
    queue.push({ status: 200, body: { ids: [], ledger: withBuffers } });
    fireEvent.change(screen.getByTestId("power-window"), { target: { value: "10 min" } });
    fireEvent.click(screen.getByTestId("power-window-go"));
    const buffers = await screen.findByTestId("power-buffers");
    expect(urls[1]).toBe("/api/power?window=600");
    expect(buffers.textContent).toContain("2 x Test Buffer with 4 x Test Battery");
    expect(within(buffers).getByText("mte:161")).toBeTruthy();
    fireEvent.change(screen.getByTestId("power-window"), { target: { value: "later" } });
    expect((screen.getByTestId("power-window-go") as HTMLButtonElement).disabled).toBe(true);
  });

  it("designs: the mode and tier go to the core, the options come in its order with the weights", async () => {
    queue.push({ status: 200, body: { ids: [], ledger: section() } });
    render(<Power />);
    await screen.findByTestId("power-tiers");
    fireEvent.click(screen.getByTestId("power-mode-max_output"));
    expect(screen.getByTestId("power-mode-max_output").getAttribute("aria-checked")).toBe("true");
    fireEvent.change(screen.getByTestId("power-scale"), { target: { value: "eut_cap" } });
    fireEvent.change(screen.getByTestId("power-tier"), { target: { value: "MV" } });
    expect((screen.getByTestId("power-go") as HTMLButtonElement).disabled).toBe(true); // no bound yet
    fireEvent.change(screen.getByTestId("power-bound"), { target: { value: "500" } });
    queue.push({ status: 200, body: { ids: ["pw-one", "pw-two"], view: view({ mode: "max_output", mode_label: "Max output" }) } });
    fireEvent.click(screen.getByTestId("power-go"));
    await screen.findByTestId("option-2");
    expect(methods[1]).toBe("POST");
    expect(urls[1]).toBe("/api/power/design");
    expect(bodies[1]).toEqual({ mode: "max_output", tier: "MV", scale: "eut_cap", bound: 500, options: 3 });
    const cards = document.querySelectorAll(".power-option");
    expect([...cards].map((c) => c.getAttribute("data-option"))).toEqual(["pw-one", "pw-two"]); // not re-sorted
    expect(screen.getByTestId("power-weights").textContent).toBe(WEIGHTS);
    expect(window.location.hash).toBe("#/power/pw-one");
  });

  it("draws each option with its numbers and their chips", async () => {
    window.location.hash = "#/power/pw-one";
    queue.push({ status: 200, body: view() });
    render(<Power />);
    const one = await screen.findByTestId("option-1");
    expect(urls[0]).toBe("/api/power/pw-one?window=0");
    expect(within(one).getByText("Option 1 of 2")).toBeTruthy();
    expect(screen.getByTestId("net-pw-one").textContent).toContain("+28 EU/t net");
    expect(screen.getByTestId("net-pw-one").textContent).toContain("= 32 out − 4 its own chain uses");
    for (const cite of ["plan:pw-one", "mte:110", "r:gt.recipe.dieselgeneratorfuel/0123456789ab", "mte:1300"]) {
      expect(one.querySelector(`[data-cite="${cite}"]`)).toBeTruthy();
    }
    expect(screen.getByTestId("closed-pw-one").textContent).toContain("Closed");
    expect(screen.getByTestId("closed-pw-two").textContent).toContain("Test Acid 0.25/s");
    expect(screen.getByTestId("preview-pw-one").textContent).toContain("clears Deficit");
    const effort = screen.getByTestId("effort-pw-one");
    expect(effort.textContent).toContain("1.5 h = build 1 + unlock 0.5 · 2 items not counted");
    expect(effort.querySelector('[data-cite="r:gt.recipe.mixer/0123456789ab"]')).toBeTruthy();
    fireEvent.click(screen.getByTestId("nodes-toggle-pw-one"));
    expect(screen.getByTestId("nodes-pw-one").textContent).toContain("Mixer");
    expect(screen.getByTestId("power-notes").textContent).toContain("gen:x: no route");
    // How an option differs from option 1 is printed; option 1 has no such line.
    expect(screen.getByTestId("differs-pw-two").textContent).toBe("Differs from option 1: fuel Test Acid");
    expect(screen.queryByTestId("differs-pw-one")).toBeNull();
  });

  it("accepts only after a confirm, then shows the core's answer", async () => {
    window.location.hash = "#/power/pw-one";
    queue.push({ status: 200, body: view() });
    render(<Power />);
    await screen.findByTestId("option-1");
    fireEvent.click(screen.getByTestId("accept-pw-two"));
    expect(screen.getByTestId("accept-confirm-pw-two").textContent).toContain("adds its 60 EU/t to the EU ledger");
    expect(urls).toHaveLength(1); // nothing sent yet
    const after = view({ options: [option("pw-one", 1, 28), option("pw-two", 2, 60, { status: "accepted" })] });
    queue.push({ status: 200, body: { id: "pw-two", status: "accepted", problems: [], view: after } });
    fireEvent.click(screen.getByTestId("accept-yes-pw-two"));
    await screen.findByText("accepted");
    expect(urls[1]).toBe("/api/power/pw-two/accept");
    expect(methods[1]).toBe("POST");
    expect(screen.queryByTestId("accept-pw-two")).toBeNull();
  });

  it("shows the core's refusal", async () => {
    queue.push({ status: 200, body: { ids: [], ledger: section() } });
    render(<Power />);
    await screen.findByTestId("power-tiers");
    queue.push({ status: 400, body: { error: "no power line at LV for mode self_sustaining" } });
    fireEvent.click(screen.getByTestId("power-mode-self_sustaining"));
    fireEvent.click(screen.getByTestId("power-go"));
    expect((await screen.findByTestId("power-notice")).textContent).toContain("no power line at LV");
  });
});
