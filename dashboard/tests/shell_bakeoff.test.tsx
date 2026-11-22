// WO-10.11: the shell bake-off panel in the Build view (D374-D377), against a stubbed core: a run
// shown blind (letters, the workbench's own 3D viewer and materials table), the first design
// and the version after the follow-up, his pick per case, and the report (the models named) only
// after every case is rated. No model name, cost or time is on the page before that.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BakeoffView, ShellScene } from "../src/api/types";
import { BAD, REFRESH_MS, ShellBakeoff, TIE, leftToRate, pickText, shownLetters } from "../src/views/ShellBakeoff";

const RUN = "bo-20261120-210000-ab12";
const build = (n: string) => ({ build: `sh-${n.repeat(12)}`, blocks: 700 + n.charCodeAt(0), types: 5, status: "accepted" as const });

function view(): BakeoffView {
  return {
    id: RUN,
    created: 1_794_000_000,
    status: "done",
    why: "",
    models: 2,
    rateable: 2,
    rated: 0,
    revealed: false,
    cases: [
      {
        id: "foundry-small",
        line: "small",
        theme: "th-foundry",
        max_blocks: 20000,
        brief: "a heavy foundry around the line",
        followup: "make the roof steeper",
        pick: null,
        entries: [
          { letter: "A", asked: true, design: build("a"), revise: build("c") },
          { letter: "B", asked: true, design: build("b"), revise: null },
        ],
      },
      {
        id: "marble-small",
        line: "small",
        theme: "th-marble-works",
        max_blocks: 20000,
        brief: "",
        followup: "no lights",
        pick: null,
        entries: [
          { letter: "A", asked: true, design: null, revise: null },
          { letter: "B", asked: true, design: build("d"), revise: build("e") },
        ],
      },
    ],
  };
}

const scene = (b: string): ShellScene => ({
  build: b,
  size: [4, 3, 4],
  palette: [{ block: "gregtech:gt.blockconcretes@3", name: "Concrete", role: "wall", count: 3, top: null, side: null }],
  cells: [
    [0, 0, 0, 0, -1],
    [1, 0, 0, 0, -1],
    [0, 1, 0, 0, -1],
  ],
  layers: [
    { y: 0, blocks: 2, materials: { "gregtech:gt.blockconcretes@3": 2 } },
    { y: 1, blocks: 1, materials: { "gregtech:gt.blockconcretes@3": 1 } },
  ],
  ghosts: { machines: [], keepouts: [] },
  caveats: [],
});

const REPORT = {
  type: "table",
  tool: "shell_bakeoff",
  error: null,
  title: `Shell bake-off ${RUN}: the models, named`,
  head: [{ key: "run", value: RUN }],
  columns: ["model", "provider", "id", "picked"],
  rows: [
    ["astra", "openai", "gpt-6-astra", "1"],
    ["opus", "anthropic", "claude-opus-5-5", "0"],
  ],
  span: null,
  total: 2,
  notes: ["With a handful of cases this is a first look, not proof."],
};

let run: BakeoffView;
let runs: { id: string }[];
let report: unknown;
let calls: { method: string; url: string; body: unknown }[];

beforeEach(() => {
  run = view();
  runs = [{ id: RUN }];
  report = null;
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    const [path, query = ""] = url.split("?");
    const q = new URLSearchParams(query);
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status });
    if (method === "GET" && path === "/api/shells/bakeoff")
      return json({ runs: runs.map((r) => ({ ...run, ...r, cases: run.cases.length })) });
    if (method === "GET" && path === `/api/shells/bakeoff/${RUN}`) return json({ run, report });
    if (method === "GET" && path === `/api/shells/bakeoff/${RUN}/view`) {
      const c = run.cases.find((x) => x.id === q.get("case"));
      const e = c?.entries.find((x) => x.letter === q.get("letter"));
      const b = e?.[q.get("which") as "design" | "revise"];
      return b ? json(scene(b.build)) : json({ error: "no shell build" }, 404);
    }
    if (method === "GET" && path === `/api/shells/bakeoff/${RUN}/compare`)
      return json({
        a: { build: "x", status: "accepted", source: "architect", theme: null, size: [4, 3, 4], blocks: 797, max_blocks: 20000, headroom: 4, types: 5 },
        b: { build: "y", status: "accepted", source: "architect", theme: null, size: [4, 3, 4], blocks: 798, max_blocks: 20000, headroom: 4, types: 5 },
        blocks_delta: 1,
        materials: [{ block: "minecraft:glass@0", name: "Glass", role: "window", a: 10, b: 11, delta: 1 }],
        ops: [],
      });
    if (method === "POST" && path === `/api/shells/bakeoff/${RUN}/rate`) {
      const { case: id, pick } = body as { case: string; pick: string };
      if (run.revealed) return json({ error: "ratings are closed" }, 409);
      run = { ...run, cases: run.cases.map((c) => (c.id === id ? { ...c, pick } : c)) };
      run.rated = run.cases.filter((c) => c.pick).length;
      return json({ run });
    }
    if (method === "POST" && path === `/api/shells/bakeoff/${RUN}/report`) {
      if (leftToRate(run).length) return json({ error: "1 case(s) are not rated yet" }, 400);
      run = { ...run, revealed: true };
      report = REPORT;
      return json({ run, report });
    }
    return json({ error: `no route ${method} ${path}` }, 404);
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** The panel's next look at the core (it looks again every REFRESH_MS). */
const nextLook = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(REFRESH_MS);
  });

describe("the bake-off's words", () => {
  it("says a pick plainly", () => {
    expect(pickText(null)).toBe("not rated");
    expect(pickText("B")).toBe("B is better");
    expect(pickText(TIE)).toBe("a tie");
    expect(pickText(BAD)).toBe("none is good");
  });
  it("knows which letters have something to show, and which cases are left", () => {
    const v = view();
    expect(shownLetters(v.cases[0], "design")).toEqual(["A", "B"]);
    expect(shownLetters(v.cases[0], "revise")).toEqual(["A"]);
    expect(shownLetters(v.cases[1], "design")).toEqual(["B"]);
    expect(leftToRate(v)).toEqual(["foundry-small", "marble-small"]);
    v.cases[0].pick = "A";
    expect(leftToRate(v)).toEqual(["marble-small"]);
    v.cases[1].entries[1].design = null; // nothing to look at: nothing to rate
    expect(leftToRate(v)).toEqual([]);
  });
});

describe("ShellBakeoff", () => {
  it("shows nothing until a run exists", async () => {
    runs = [];
    const { container } = render(<ShellBakeoff />);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/shells/bakeoff")).toBe(true));
    expect(container.textContent).toBe("");
    expect(screen.queryByTestId("bakeoff")).toBeNull();
  });

  it("shows a case blind: letters, the two shells side by side, their materials, no names", async () => {
    render(<ShellBakeoff />);
    const panel = await screen.findByTestId("bakeoff");
    await screen.findByTestId("bakeoff-case");
    expect(screen.getByTestId("bakeoff-blind").textContent).toContain("blind");
    expect(screen.getByTestId("bakeoff-brief").textContent).toContain("a heavy foundry around the line");
    expect(screen.getByTestId("bakeoff-followup").textContent).toContain("make the roof steeper");
    const a = await screen.findByTestId("bakeoff3d-A-view");
    const b = await screen.findByTestId("bakeoff3d-B-view");
    expect(a.getAttribute("data-build")).toBe(build("a").build);
    expect(b.getAttribute("data-build")).toBe(build("b").build);
    expect(screen.getByTestId("bakeoff3d-A-layer")).toBeTruthy(); // the workbench's own viewer: the layer slider
    const mats = await screen.findByTestId("bakeoff-materials");
    expect(within(mats).getByText("minecraft:glass@0")).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/compare?case=foundry-small&a=A&b=B&which=design"))).toBe(true);
    for (const word of ["astra", "opus", "gpt-", "claude", "openai", "anthropic", "$", "tokens", "seconds"])
      expect(panel.textContent).not.toContain(word);
    expect(panel.querySelector("[data-testid='bakeoff-report']")).toBeNull();
  });

  it("shows the version after the follow-up, and says when an entry has none", async () => {
    render(<ShellBakeoff />);
    await screen.findByTestId("bakeoff3d-B-view");
    fireEvent.click(screen.getByTestId("bakeoff-which-revise"));
    await waitFor(() => expect(screen.getByTestId("bakeoff3d-A-view").getAttribute("data-build")).toBe(build("c").build));
    expect(screen.queryByTestId("bakeoff3d-B-view")).toBeNull();
    expect(screen.getByTestId("bakeoff-entry-text-B").textContent).toContain("no version after the follow-up");
    expect(screen.queryByTestId("bakeoff-materials")).toBeNull(); // one shell: nothing to compare
    expect(calls.some((c) => c.url.includes("letter=A&which=revise"))).toBe(true);
    fireEvent.click(screen.getByTestId("bakeoff-case-marble-small"));
    fireEvent.click(screen.getByTestId("bakeoff-which-design"));
    await screen.findByTestId("bakeoff3d-B-view");
    expect(screen.getByTestId("bakeoff-entry-text-A").textContent).toContain("no design");
    expect(screen.getByTestId("bakeoff-brief").textContent).toContain("the theme alone");
    expect(screen.queryByTestId("bakeoff-pick-A")).toBeNull(); // an entry with no design can't be picked
    expect(screen.getByTestId("bakeoff-pick-B")).toBeTruthy();
  });

  it("takes a pick per case and names the models only after every case is rated", async () => {
    render(<ShellBakeoff />);
    await screen.findByTestId("bakeoff-case");
    const reveal = screen.getByTestId("bakeoff-reveal") as HTMLButtonElement;
    expect(reveal.disabled).toBe(true);
    expect(screen.getByTestId("bakeoff-left").textContent).toContain("2 cases left");
    // the tie button can't be read as entry A's ("A tie" beside "A is better")
    expect(screen.getByTestId("bakeoff-pick-tie").textContent).toBe("Tie");
    expect(screen.getByTestId("bakeoff-pick-A").textContent).toBe("A is better");
    fireEvent.click(screen.getByTestId("bakeoff-pick-A"));
    await waitFor(() => expect(screen.getByTestId("bakeoff-pick-text").textContent).toContain("A is better"));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ case: "foundry-small", pick: "A" });
    expect(screen.getByTestId("bakeoff-case-foundry-small").getAttribute("data-pick")).toBe("A");
    expect(reveal.disabled).toBe(true);
    fireEvent.click(screen.getByTestId("bakeoff-case-marble-small"));
    fireEvent.click(await screen.findByTestId("bakeoff-pick-bad"));
    await waitFor(() => expect(screen.getByTestId("bakeoff-left").textContent).toContain("every case is rated"));
    expect(screen.getByTestId("bakeoff-status").textContent).toContain("rated 2 of 2");
    expect(screen.getByTestId("bakeoff").textContent).not.toContain("gpt-6-astra");
    fireEvent.click(screen.getByTestId("bakeoff-reveal"));
    const shown = await screen.findByTestId("bakeoff-report");
    expect(shown.textContent).toContain("gpt-6-astra");
    expect(shown.textContent).toContain("claude-opus-5-5");
    expect(screen.getByTestId("bakeoff-blind").textContent).toContain("named");
    expect(screen.queryByTestId("bakeoff-reveal")).toBeNull();
    expect((screen.getByTestId("bakeoff-pick-B") as HTMLButtonElement).disabled).toBe(true); // ratings are closed
  });

  // The runs are made, finished and deleted at the CLI while the Build view stays open
  // (the runbook's steps 9, 10 and 20): the panel has to follow without a reload.
  it("shows a run made while the view is open, without a reload", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    runs = [];
    render(<ShellBakeoff />);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/shells/bakeoff")).toBe(true));
    expect(screen.queryByTestId("bakeoff")).toBeNull();
    runs = [{ id: RUN }]; // `gos shell bakeoff run` in PowerShell
    await nextLook();
    expect(await screen.findByTestId("bakeoff")).toBeTruthy();
    expect((await screen.findByTestId("bakeoff-status")).getAttribute("data-run")).toBe(RUN);
    expect(await screen.findByTestId("bakeoff3d-A-view")).toBeTruthy();
    expect(REFRESH_MS).toBeLessThanOrEqual(5000);
  });

  it("follows a run that is still going until it has ended", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const done = view();
    run = { ...done, status: "running", rateable: 1, cases: [done.cases[0], { ...done.cases[1], entries: done.cases[1].entries.map((e) => ({ ...e, asked: false, design: null, revise: null })) }] };
    render(<ShellBakeoff />);
    expect((await screen.findByTestId("bakeoff-status")).textContent).toContain("running");
    fireEvent.click(screen.getByTestId("bakeoff-case-marble-small"));
    // an answer still to come is not called a refusal
    expect(screen.getByTestId("bakeoff-entry-text-B").textContent).toContain("not asked yet");
    expect(screen.getByTestId("bakeoff-entry-text-B").textContent).not.toContain("refused");
    expect((screen.getByTestId("bakeoff-reveal") as HTMLButtonElement).disabled).toBe(true);
    run = done; // the run ended at the CLI
    await nextLook();
    await waitFor(() => expect(screen.getByTestId("bakeoff-status").textContent).toContain("done"));
    expect(screen.getByTestId("bakeoff-status").textContent).not.toContain("running");
    expect(screen.getByTestId("bakeoff-case").getAttribute("data-case")).toBe("marble-small"); // he stays where he was
    expect(await screen.findByTestId("bakeoff3d-B-view")).toBeTruthy();
    expect(screen.getByTestId("bakeoff-entry-text-A").textContent).toContain("no design (the gate refused"); // asked, none came
  });

  // He rates in the panel or at the command line, and may write the report there
  // (`gos shell bakeoff rate`, `report`): the panel has to show it without a reload, or the
  // button stays disabled and a pick gets "ratings are closed".
  it("picks up a pick and a report made at the command line, then stops asking", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ShellBakeoff />);
    await screen.findByTestId("bakeoff-case");
    expect(screen.getByTestId("bakeoff-status").textContent).toContain("done");
    expect((screen.getByTestId("bakeoff-reveal") as HTMLButtonElement).disabled).toBe(true);
    const looks = () => calls.filter((c) => c.url === `/api/shells/bakeoff/${RUN}`).length;
    // `gos shell bakeoff rate` in PowerShell: both cases
    run = { ...run, rated: 2, cases: run.cases.map((c, n) => ({ ...c, pick: n === 0 ? "A" : BAD })) };
    await nextLook();
    await waitFor(() => expect(screen.getByTestId("bakeoff-left").textContent).toContain("every case is rated"));
    expect(screen.getByTestId("bakeoff-case-foundry-small").getAttribute("data-pick")).toBe("A");
    expect(screen.getByTestId("bakeoff-pick-text").textContent).toContain("A is better");
    expect((screen.getByTestId("bakeoff-reveal") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByTestId("bakeoff-case").getAttribute("data-case")).toBe("foundry-small"); // he stays where he was
    // `gos shell bakeoff report`: the models are named there, so here too
    run = { ...run, revealed: true };
    report = REPORT;
    await nextLook();
    expect((await screen.findByTestId("bakeoff-report")).textContent).toContain("gpt-6-astra");
    expect(screen.queryByTestId("bakeoff-reveal")).toBeNull();
    expect((screen.getByTestId("bakeoff-pick-B") as HTMLButtonElement).disabled).toBe(true); // ratings are closed, and it shows
    const asked = looks();
    await nextLook();
    await nextLook();
    expect(looks()).toBe(asked); // a reported run changes no more: it is not asked for again
  });

  it("does not let a look that started before his pick undo it on the page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ShellBakeoff />);
    await screen.findByTestId("bakeoff-case");
    const real = globalThis.fetch;
    let release: (() => void) | null = null;
    const stale = JSON.stringify({ run, report: null }); // the run as it was before the pick
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      if ((init.method ?? "GET") === "GET" && url === `/api/shells/bakeoff/${RUN}` && release === null) {
        await new Promise<void>((done) => (release = done));
        return new Response(stale, { status: 200 });
      }
      return real(url, init);
    });
    await nextLook(); // the look goes out, and is slow
    await waitFor(() => expect(release).not.toBeNull());
    fireEvent.click(screen.getByTestId("bakeoff-pick-A"));
    await waitFor(() => expect(screen.getByTestId("bakeoff-pick-text").textContent).toContain("A is better"));
    await act(async () => release!()); // the slow look comes back with the run as it was
    expect(screen.getByTestId("bakeoff-pick-text").textContent).toContain("A is better");
    vi.stubGlobal("fetch", real);
  });

  it("lists a second run when it is made, and lets go of a deleted one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ShellBakeoff />);
    await screen.findByTestId("bakeoff-case");
    expect(screen.queryByTestId("bakeoff-run-pick")).toBeNull(); // one run: nothing to choose
    const second = "bo-20261121-193000-cd34";
    runs = [{ id: second }, { id: RUN }];
    await nextLook();
    const pick = (await screen.findByTestId("bakeoff-run-pick")) as HTMLSelectElement;
    expect([...pick.options].map((o) => o.value)).toEqual([second, RUN]);
    expect(pick.value).toBe(RUN); // the run he is looking at stays
    expect(screen.getByTestId("bakeoff-status").getAttribute("data-run")).toBe(RUN);
    runs = []; // `gos shell bakeoff delete`
    await nextLook();
    await waitFor(() => expect(screen.queryByTestId("bakeoff")).toBeNull());
  });

  it("keeps what it shows when one look at the core fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ShellBakeoff />);
    await screen.findByTestId("bakeoff-case");
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: "the core is restarting" }), { status: 503 }));
    await nextLook();
    expect(screen.getByTestId("bakeoff-case")).toBeTruthy();
    vi.stubGlobal("fetch", real);
  });

  it("says what the core refuses", async () => {
    render(<ShellBakeoff />);
    await screen.findByTestId("bakeoff-case");
    run = { ...run, revealed: true };
    fireEvent.click(screen.getByTestId("bakeoff-pick-tie"));
    expect((await screen.findByTestId("bakeoff-notice")).textContent).toContain("ratings are closed");
  });
});
