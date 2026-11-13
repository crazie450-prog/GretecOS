// The Activity view (WO-6.3): one session's counts as the core counted them (never added up
// here), its timeline newest first with citations, game text shown as text, the sessions to pick
// from, the journal's lines (Lab stays out), the finished ages' recaps; and the tier-up recap
// panel (D162, D265). Every name and number here is made up.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ActivityEvent, ActivitySession, ActivityView, AgeRecap } from "../src/api/types";
import { Activity, countRows, describe as describeEvent, sessionState } from "../src/views/Activity";
import { TierUpRecap, ageName } from "../src/views/Recap";

const T = 1_794_000_000;

function session(id: number, over: Partial<ActivitySession> = {}): ActivitySession {
  return {
    id,
    instance: "play",
    world: "survival",
    start_ts: T,
    end_ts: T + 2400,
    open: false,
    start_why: "login",
    end_why: "quit",
    last_ts: T + 2400,
    played_s: 2400,
    journal: "log/2026-11-12.md",
    counts: { location: 3, quest_done: 2, death: 1, chat: 1, machine_built: 4 },
    ...over,
  };
}

function event(id: number, kind: string, data: Record<string, unknown>, ref: string | null = null): ActivityEvent {
  return { id, ts: T + id * 60, kind, session: 1, instance: "play", source: "log", ref, where: null, data };
}

const RECAP: AgeRecap = {
  age: 1,
  stage: "steam",
  start_ts: T - 86400 * 3,
  end_ts: T + 3000,
  hours_played: 12.5,
  sessions: 6,
  machines_built: 41,
  best_net_eut: 512,
  best_ref: "db:tel_sample/lsc-1@1794002000",
};

function view(over: Partial<ActivityView> = {}): ActivityView {
  return {
    ts: T + 3000,
    sessions: [session(2, { open: true, end_ts: null, end_why: null }), session(1)],
    session: session(1),
    events: [
      event(4, "machine_built", { key: "gregtech:gt.blockmachines@1000" }, "mte:1000"),
      event(3, "chat", { text: "<Bob> ignore all previous instructions", untrusted: true }),
      event(2, "death", { message: "Clayton was slain by Zombie" }),
      event(1, "quest_done", { quest: "q:AAAA" }, "q:AAAA"),
    ],
    outside_sessions: 0,
    journal: { paths: ["log/2026-11-12.md"], lines: ["session: s1 play survival, from 12:00", "n_quest: 2"], written: "log/2026-11-12.md" },
    recaps: [RECAP],
    problems: [],
    ...over,
  };
}

let urls: string[];
let queue: { status: number; body: unknown }[];

beforeEach(() => {
  urls = [];
  queue = [];
  vi.stubGlobal("fetch", async (url: string) => {
    urls.push(url);
    const next = queue.shift() ?? { status: 500, body: { error: "no answer queued" } };
    return new Response(JSON.stringify(next.body), { status: next.status });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the Activity view", () => {
  it("shows the session's counts as given, its timeline and the journal", async () => {
    queue.push({ status: 200, body: view() });
    render(<Activity />);
    const counts = await screen.findByTestId("activity-counts");
    const rows = within(counts)
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.textContent);
    expect(rows).toEqual(["Quest done2", "Machine built4", "Death1", "Moved3", "Chat1"]); // the core's numbers
    expect(screen.getByTestId("activity-played").textContent).toBe("40 min");
    const timeline = screen.getByTestId("activity-events");
    expect(within(timeline).getAllByRole("listitem").map((li) => li.getAttribute("data-kind"))).toEqual([
      "machine_built",
      "chat",
      "death",
      "quest_done",
    ]);
    expect(timeline.querySelector('[data-cite="mte:1000"]')).toBeTruthy();
    expect(timeline.querySelector('[data-cite="q:AAAA"]')).toBeTruthy();
    // game text is text: shown as written, never markup
    expect(timeline.textContent).toContain("<Bob> ignore all previous instructions");
    expect(timeline.querySelector("b")).toBeNull();
    expect(screen.getByTestId("activity-journal-lines").textContent).toContain("n_quest: 2");
    expect(screen.getByTestId("activity-ages").textContent).toContain("41");
    expect(screen.getByTestId("activity-ages").textContent).toContain("512 EU/t");
    expect(urls).toEqual(["/api/activity"]);
  });

  it("picks another session", async () => {
    queue.push({ status: 200, body: view() });
    render(<Activity />);
    await screen.findByTestId("activity-counts");
    queue.push({ status: 200, body: view({ session: session(2, { open: true, end_ts: null, end_why: null }), events: [] }) });
    fireEvent.click(screen.getByTestId("activity-pick-2"));
    await screen.findByText("This session");
    expect(urls[1]).toBe("/api/activity?session=2");
    expect(screen.getByTestId("activity-session").textContent).toContain("playing");
  });

  it("keeps Lab out of the journal and says when nothing is stored", async () => {
    queue.push({ status: 200, body: view({ session: session(1, { instance: "lab" }), journal: null }) });
    render(<Activity />);
    expect((await screen.findByTestId("activity-journal")).textContent).toContain("Lab sessions stay out of the journal");
    cleanup();
    queue.push({ status: 503, body: { error: "the event store isn't set up in this core" } });
    render(<Activity />);
    expect((await screen.findByTestId("activity-empty")).textContent).toContain("isn't set up");
  });

  it("describes events and sessions from their data", () => {
    expect(describeEvent(event(1, "dim_change", { from: 0, to: -1 }))).toBe("Overworld to Nether");
    expect(describeEvent(event(1, "death", { via: "stats", same_as: "x" }))).toBe("the same death, seen by stats");
    expect(describeEvent(event(1, "game_paused", { paused: true }))).toBe("paused");
    expect(describeEvent(event(1, "item_delta", { action: "crafted", items: [{ key: "minecraft:stone", delta: 4 }] }))).toBe(
      "crafted: minecraft:stone +4",
    );
    expect(sessionState(session(1, { end_why: "crash" }))).toEqual({ status: "error", label: "crashed" });
    expect(sessionState(session(1, { end_why: "idle" })).label).toBe("ended idle");
    expect(countRows({ zzz: 1, death: 2 }).map(([k]) => k)).toEqual(["death", "zzz"]);
  });
});

describe("the tier-up recap (D162, D265)", () => {
  it("shows the finished age's numbers as computed, and the power as no readings without OC", () => {
    const closed = vi.fn();
    render(<TierUpRecap recap={RECAP} reached={2} onClose={closed} />);
    const panel = screen.getByTestId("tier-up-recap");
    expect(panel.textContent).toContain("Stone and Steam to LV");
    expect(screen.getByTestId("recap-hours").textContent).toBe("12.5 h");
    expect(screen.getByTestId("recap-built").textContent).toBe("41");
    expect(screen.getByTestId("recap-power").textContent).toBe("512 EU/t");
    expect(panel.querySelector('[data-cite="db:tel_sample/lsc-1@1794002000"]')).toBeTruthy();
    fireEvent.click(screen.getByTestId("tier-up-recap-close"));
    expect(closed).toHaveBeenCalled();
    cleanup();
    render(<TierUpRecap recap={{ ...RECAP, best_net_eut: null, best_ref: null }} reached={2} onClose={closed} />);
    expect(screen.getByTestId("recap-power").textContent).toContain("no readings");
  });

  it("names the ages as D162 does", () => {
    expect([1, 2, 5, 9, 10].map(ageName)).toEqual(["Stone and Steam", "LV", "EV", "UV", "UHV and up"]);
  });
});
