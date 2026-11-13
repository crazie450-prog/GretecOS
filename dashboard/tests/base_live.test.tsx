// The Base view's live half (WO-7.4): power, machines, problems (stalled first) and AE2 from
// GET /api/base/live with their tel: citations, WO-7.5's alerts (held while paused), the stale state when OC went quiet, pending
// actions, and a refresh every 5 s so a change shows within 10 s.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveBase, LiveBinding } from "../src/api/types";
import { BaseLivePanel, LIVE_POLL_MS, ageText, euText } from "../src/views/BaseLive";

const EBF = "lab/2e000000-0000-4000-8000-000000000008";
const LSC = "lab/4c000000-0000-4000-8000-00000000000a";

function binding(over: Partial<LiveBinding> = {}): LiveBinding {
  return {
    binding: "lab",
    status: "connected",
    age_s: 2,
    agents: [{ address: "c0c0c0c0-0000-4000-8000-000000000001", cite: "tel:lab/c0c0c0c0", transport: "http", age_s: 2, stale: false, tick_ms: 12.3 }],
    power: [
      {
        id: LSC,
        cite: `tel:${LSC}`,
        name: "Lapotronic Supercapacitor",
        meta: "multimachine.supercapacitor",
        mte_id: 13106,
        kind: "storage",
        age_s: 2,
        stale: false,
        eu_in: 2048,
        eu_out: 512,
        eu_str: "123456789012345678901234",
        eu_max_str: "999999999999999999999999",
        pct: 12.3,
      },
    ],
    net_eu_t: 1536,
    machines: [
      {
        id: EBF,
        cite: `tel:${EBF}`,
        name: "Electric Blast Furnace",
        meta: "multimachine.blastfurnace",
        mte_id: 1000,
        xyz: [120, 64, -35],
        status: "stalled",
        progress: 120,
        progress_max: 400,
        problems: 0,
        problems_age_s: 30,
        age_s: 2,
        stale: false,
      },
    ],
    problems: [{ level: "warn", kind: "stalled", cite: `tel:${EBF}`, text: "Electric Blast Furnace: stalled (has a job, not running)" }],
    ae2: {
      cpus: 2,
      busy: 1,
      crafts: [{ id: 7, name: "Steel Ingot", amount: 64, state: "check_ae2", err: "may still run", cite: "tel:lab/c0", stale: false }],
      items: [{ key: "gregtech:gt.metaitem.01@11305", name: "Steel Ingot", size: 640, craftable: true, cite: "tel:lab/5e" }],
    },
    alerts: [],
    held: null,
    ...over,
  };
}

let live: LiveBase;
let actions: unknown;
let gets: number;

beforeEach(() => {
  gets = 0;
  live = { ts: 0, stale_s: 30, bindings: [binding()] };
  actions = { pending: null, recent: [] };
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.includes("/api/base/live")) {
      gets += 1;
      return new Response(JSON.stringify(live), { status: 200 });
    }
    return new Response(JSON.stringify(actions), { status: 200 });
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("numbers", () => {
  it("are exact below a million and short above, never guessed", () => {
    expect(euText(1536)).toBe("1,536");
    expect(euText("999999")).toBe("999,999");
    expect(euText("123456789012345678901234")).toBe("1.23e23");
    expect(euText(null)).toBe("–");
    expect(euText("12x")).toBe("–");
    expect(ageText(4)).toBe("4 s ago");
    expect(ageText(125)).toBe("2 min ago");
  });
});

describe("the live base", () => {
  it("shows problems stalled first, power, machines and AE2, each cited", async () => {
    render(<BaseLivePanel />);
    const problems = await screen.findByTestId("live-problems");
    expect(problems.querySelector("li")?.getAttribute("data-kind")).toBe("stalled");
    expect(problems.textContent).toContain("Electric Blast Furnace: stalled");
    expect(screen.getByTestId("live-power").textContent).toContain("12.3%");
    expect(screen.getByTestId("live-power").textContent).toContain("1.23e23");
    expect(screen.getByTestId("live-net").textContent).toContain("+1,536");
    const row = document.querySelector(`[data-machine-live="${EBF}"]`);
    expect(row?.getAttribute("data-status")).toBe("stalled");
    expect(row?.textContent).toContain("6 / 20 s");
    expect(document.querySelector(`[data-cite="tel:${EBF}"]`)).not.toBeNull();
    expect(screen.getByTestId("live-ae2").textContent).toContain("check AE2");
    expect(screen.getByTestId("live-lab").textContent).toContain("M2 HTTP");
  });

  it("shows a machine GT stopped as a problem, and one GOS paused as paused", async () => {
    const stopped = { level: "warn" as const, kind: "stopped", cite: `tel:${EBF}`, text: "Electric Blast Furnace: stopped (work disabled: power loss, maintenance or by hand)" };
    live = { ts: 0, stale_s: 30, bindings: [binding({ problems: [stopped], machines: [{ ...binding().machines[0], status: "stopped" }] })] };
    render(<BaseLivePanel />);
    const problems = await screen.findByTestId("live-problems");
    expect(problems.querySelector("li")?.getAttribute("data-kind")).toBe("stopped");
    const row = document.querySelector(`[data-machine-live="${EBF}"]`);
    expect(row?.getAttribute("data-status")).toBe("stopped");
    expect(row?.textContent).toContain("stopped");
  });

  it("says when OC went quiet and keeps the last readings marked stale", async () => {
    live = { ts: 0, stale_s: 30, bindings: [binding({ status: "stale", age_s: 95 })] };
    render(<BaseLivePanel />);
    expect((await screen.findByTestId("live-stale")).textContent).toContain("No OC report for 95 s");
    expect(screen.getByTestId("live-lab").getAttribute("data-status")).toBe("stale");
  });

  it("before any OC computer reported, says how to set one up", async () => {
    live = { ts: 0, stale_s: 30, bindings: [] };
    render(<BaseLivePanel />);
    expect((await screen.findByTestId("live-not-set-up")).textContent).toContain("gos-agent");
  });

  it("shows the action waiting for confirm", async () => {
    actions = {
      pending: { id: 3, kind: "craft", state: "proposed", target: "lab", readback: "Craft 64 Steel Ingot in Lab", created: 0, done_ts: null },
      recent: [{ id: 2, kind: "machine_pause", state: "done", target: "lab", readback: "Pause machine 2e000000 in Lab", created: 0, done_ts: 1 }],
    };
    render(<BaseLivePanel />);
    expect((await screen.findByTestId("live-pending")).textContent).toContain("Craft 64 Steel Ingot");
    expect(screen.getByTestId("live-actions").textContent).toContain("Pause machine");
  });

  it("refreshes every 5 s, so a change shows within 10 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<BaseLivePanel />);
    await screen.findByTestId("live-problems");
    live = { ts: 5, stale_s: 30, bindings: [binding({ problems: [], machines: [{ ...binding().machines[0], status: "running" }] })] };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_POLL_MS);
    });
    expect(await screen.findByTestId("live-no-problems")).toBeTruthy();
    expect(document.querySelector(`[data-machine-live="${EBF}"]`)?.getAttribute("data-status")).toBe("running");
    expect(gets).toBeGreaterThanOrEqual(2);
    expect(LIVE_POLL_MS).toBeLessThanOrEqual(5000);
  });

  it("shows the alerts, crafting done included, and says when they are held (WO-7.5)", async () => {
    const lowPower = {
      key: `power_low:${LSC}`,
      rule: "power_low" as const,
      binding: "lab",
      subject: LSC,
      level: "err" as const,
      title: "Power low: Lapotronic Supercapacitor",
      body: "Lab: 12.3 % stored",
      say: "Lab: power low.",
      cite: `tel:${LSC}`,
      fired_at: 100,
      value: 12.3,
      unit: "%",
      stale: null,
    };
    const done = {
      ...lowPower,
      key: "crafting_done:lab/7",
      rule: "crafting_done" as const,
      subject: "lab/7",
      level: "info" as const,
      title: "Crafting done: 64 Steel Ingot",
      cite: "tel:lab/c0",
      value: 64,
      unit: null,
    };
    live = { ts: 0, stale_s: 30, bindings: [binding({ alerts: [lowPower, done] })] };
    render(<BaseLivePanel />);
    const alerts = await screen.findByTestId("live-alerts");
    const rows = alerts.querySelectorAll("li");
    expect([...rows].map((r) => r.getAttribute("data-alert"))).toEqual(["power_low", "crafting_done"]);
    expect(alerts.textContent).toContain("Power low: Lapotronic Supercapacitor");
    expect(alerts.textContent).toContain("Crafting done: 64 Steel Ingot");
    expect(alerts.querySelector(`[data-cite="tel:${LSC}"]`)).not.toBeNull();
    expect(screen.queryByTestId("live-alerts-held")).toBeNull();
    cleanup();
    live = { ts: 0, stale_s: 30, bindings: [binding({ held: "paused", alerts: [{ ...lowPower, stale: "paused" }] })] };
    render(<BaseLivePanel />);
    expect((await screen.findByTestId("live-alerts-held")).textContent).toContain("the game is paused");
    expect(screen.getByTestId("live-alerts").querySelector("li")?.getAttribute("data-stale")).toBe("paused");
  });

  it("with no alerts, says so", async () => {
    render(<BaseLivePanel />);
    expect((await screen.findByTestId("live-no-alerts")).textContent).toContain("No alerts");
  });
});
