// Design jobs in the dashboard (WO-3.9, D353, D354): how a stage and its time read, the pushed
// jobs the store keeps, the right rail's design job with its time and a Cancel for Clayton's
// paired dashboards only, and a view opened while its job runs following that job.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CoreState, DesignJob } from "../src/api/types";
import { Rail } from "../src/components/Rail";
import { elapsed, endedText, newer, runningOf, stageText } from "../src/state/jobs";
import { JOBS_KEEP, addJob, applyMessage, initialState, store } from "../src/state/store";
import { Power } from "../src/views/Power";

function job(over: Partial<DesignJob> = {}): DesignJob {
  return {
    id: "job-0123456789ab",
    kind: "designer",
    title: "Designer: steel ingot · HV",
    status: "running",
    stage: "graph",
    k: null,
    n: null,
    sub: null,
    detail: "graph",
    started: 1_794_000_000,
    finished: null,
    elapsed_s: 0,
    error: null,
    cancel_asked: false,
    ...over,
  };
}

function core(jobs: CoreState["jobs"]): CoreState {
  return {
    ts: 1_794_000_000,
    game: { running: false, focused: false, paused: null, export: null, error: null },
    data: { stale: null, line: null, run: null },
    ai: { online: true, detail: "" },
    credits: null,
    oc: { status: "not_set_up", detail: "" },
    tier: null,
    alerts: [],
    approvals: [],
    asking: [],
    jobs,
    health: null,
    persona: null,
  } as unknown as CoreState;
}

describe("how a job reads", () => {
  it("says the stage in words, with k of n and the effort walk", () => {
    expect(stageText(job())).toBe("Building the recipe graph");
    expect(stageText(job({ stage: "solve", k: 2, n: 3 }))).toBe("Solving route 2 of 3");
    expect(stageText(job({ stage: "metrics", k: 1, n: 3, sub: [12, 40] }))).toBe("Measuring route 1 of 3 · effort 12 of 40");
    expect(stageText(job({ stage: "chains", k: 4, n: 9 }))).toBe("Fuel chain 4 of 9");
    expect(stageText(job({ stage: "waiting" }))).toBe("Waiting for the other design to finish");
    expect(stageText(job({ stage: "something new" }))).toBe("something new");
  });
  it("counts the core's time, plus the time since it was received", () => {
    expect(elapsed(0, 100_000, 100_000)).toBe("0:00");
    expect(elapsed(60, 100_000, 105_400)).toBe("1:05");
    expect(elapsed(3_725, 5, 5)).toBe("1:02:05");
    expect(elapsed(0, 100_000, 99_000)).toBe("0:00"); // never minus
  });
  it("says why an ended job has no result", () => {
    expect(endedText(job({ status: "cancelled" }))).toBe("Cancelled: nothing was saved.");
    expect(endedText(job({ status: "failed", error: "no route" }))).toBe("no route");
    expect(endedText(job({ status: "interrupted", error: null }))).toContain("start it again");
  });
  it("keeps the newer of a push and a poll, never an ended one going back to running", () => {
    const a = job({ elapsed_s: 5, stage: "solve" });
    expect(newer(a, job({ elapsed_s: 6, stage: "metrics" }))?.stage).toBe("metrics");
    expect(newer(a, job({ elapsed_s: 4, stage: "graph" }))?.stage).toBe("solve");
    expect(newer(job({ status: "done" }), job({ elapsed_s: 99 }))?.status).toBe("done");
    expect(newer(a, job({ id: "job-ffffffffffff" }))).toBe(a);
    expect(newer(null, a)).toBe(a);
  });
  it("finds the running job of a kind in the core's state", () => {
    const c = core([
      { id: "job-a", kind: "power", title: "Power", status: "running", detail: "chains", started: 1 },
      { id: "t1", kind: "deep", title: "deep", status: "running", detail: null, started: 1 },
    ]);
    expect(runningOf(c, "power")).toBe("job-a");
    expect(runningOf(c, "designer")).toBeNull();
    expect(runningOf(null, "designer")).toBeNull();
  });
});

describe("the store", () => {
  it("keeps each pushed job by id, the newest 20", () => {
    let s = initialState;
    s = { ...s, ...applyMessage(s, { type: "job", data: job() }) };
    s = { ...s, ...applyMessage(s, { type: "job", data: job({ stage: "solve" }) }) };
    expect(Object.keys(s.jobs)).toEqual(["job-0123456789ab"]);
    expect(s.jobs["job-0123456789ab"].stage).toBe("solve");
    let many = {};
    for (let i = 0; i < JOBS_KEEP + 5; i++) many = addJob(many, job({ id: `job-${String(i).padStart(12, "0")}`, started: i }));
    expect(Object.keys(many)).toHaveLength(JOBS_KEEP);
    expect(Object.keys(many)).not.toContain("job-000000000000");
    expect(addJob(many, null as unknown as DesignJob)).toBe(many);
  });
});

let calls: { method: string; url: string }[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    calls.push({ method: init.method ?? "GET", url });
    if (url.startsWith("/api/design-jobs/")) {
      const running = job({ kind: "power", stage: "chains", k: 2, n: 5, started: Date.now() / 1000 - 3 });
      return new Response(JSON.stringify({ job: url.endsWith("/cancel") ? { ...running, cancel_asked: true } : running }));
    }
    if (url.startsWith("/api/power")) return new Response(JSON.stringify({ ids: [], ledger: null }));
    return new Response(JSON.stringify({ error: "no route" }), { status: 404 });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  store.set({ session: null, core: null, jobs: {} });
});

describe("the right rail", () => {
  const running = core([
    { id: "job-0123456789ab", kind: "designer", title: "Designer: steel ingot · HV", status: "running", detail: "solve 2 of 3", started: 1, elapsed_s: 75 }, // a device clock far off: still 1:15
  ]);
  it("shows a design job's stage, its time and a Cancel for a paired dashboard", async () => {
    store.set({ session: { role: "device", name: "monitor", device_id: "d1" } });
    render(<Rail core={running} act={() => undefined} />);
    expect(screen.getByText("Designer: steel ingot · HV")).toBeTruthy();
    expect(screen.getByText("solve 2 of 3")).toBeTruthy();
    expect(screen.getByTestId("job-elapsed-job-0123456789ab").textContent).toMatch(/^1:1[5-9]$/);
    await act(async () => {
      fireEvent.click(screen.getByTestId("job-cancel-job-0123456789ab"));
    });
    expect(calls).toEqual([{ method: "POST", url: "/api/design-jobs/job-0123456789ab/cancel" }]);
  });
  it("has no Cancel for a client token's view", () => {
    store.set({ session: { role: "client", name: "voice", device_id: null } });
    render(<Rail core={running} act={() => undefined} />);
    expect(screen.queryByTestId("job-cancel-job-0123456789ab")).toBeNull();
  });
});

describe("a view opened while its job runs", () => {
  it("follows the running job instead of starting another", async () => {
    store.set({
      core: core([{ id: "job-0123456789ab", kind: "power", title: "Power", status: "running", detail: "chains", started: 1 }]),
    });
    window.location.hash = "#/power";
    render(<Power />);
    expect((await screen.findByTestId("power-job-stage")).textContent).toContain("Fuel chain 2 of 5");
    expect((screen.getByTestId("power-go") as HTMLButtonElement).disabled).toBe(true);
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });
});
