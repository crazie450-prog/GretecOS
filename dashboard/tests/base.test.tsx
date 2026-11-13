// The Base view's My base panels (WO-3.8): the designed empty states (D63: nothing stocked by
// default), the tier with the quests' stage beside the override (D64), edits that answer with
// the whole of My base, and a refusal shown with the core's reason.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MyBase } from "../src/api/types";
import { Base, parseAmount } from "../src/views/Base";

const STAGES = ["stone", "steam", "LV", "MV", "HV"].map((stage, i) => ({ stage, tier: Math.max(0, i - 1) }));

function base(over: Partial<MyBase> = {}): MyBase {
  return {
    tier: { stage: "stone", tier: 0, how: "quests", detected: "stone", detected_tier: 0, quest: null, quest_name: null },
    stages: STAGES,
    machines: [],
    stock: [],
    lines: [],
    has_db: true,
    ...over,
  };
}

type Call = { method: string; url: string; body: unknown };
let calls: Call[];
let answers: { status: number; body: unknown }[];

beforeEach(() => {
  calls = [];
  answers = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    calls.push({ method: init.method ?? "GET", url, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const next = answers.shift() ?? { status: 500, body: { error: "no answer queued" } };
    return new Response(JSON.stringify(next.body), { status: next.status });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function shown(first: MyBase): Promise<void> {
  answers.push({ status: 200, body: first });
  render(<Base />);
  await screen.findByTestId("base-tier");
}

describe("amounts", () => {
  it("are whole numbers for machines and positive numbers for stock", () => {
    expect(parseAmount("12", true)).toBe(12);
    expect(parseAmount(" 1,000 ", true)).toBe(1000);
    expect(parseAmount("1.5", true)).toBeNull();
    expect(parseAmount("1.5", false)).toBe(1.5);
    expect(parseAmount("-1", false)).toBeNull();
    expect(parseAmount("1e3", false)).toBeNull();
    expect(parseAmount("", false)).toBeNull();
  });
});

describe("the Base view", () => {
  it("shows nothing owned and nothing stocked by default", async () => {
    await shown(base());
    expect(screen.getByTestId("machines-empty").textContent).toContain("No machines yet");
    expect(screen.getByTestId("stock-empty").textContent).toContain("Nothing stocked");
    expect(screen.getByTestId("base-tier-how").textContent).toContain("From quest progress");
    expect(screen.getByTestId("base-stalled").textContent).toContain("WO-7.4");
    expect(screen.queryByTestId("base-tier-clear")).toBeNull();
    expect(calls).toEqual([{ method: "GET", url: "/api/base", body: undefined }]);
  });

  it("shows the override beside the quests' stage, and clears it", async () => {
    await shown(
      base({ tier: { stage: "HV", tier: 3, how: "override", detected: "LV", detected_tier: 1, quest: "q:abc", quest_name: "Tier one" } }),
    );
    expect(screen.getByTestId("base-tier-how").textContent).toContain("Your override");
    const detected = screen.getByTestId("base-tier-detected");
    expect(detected.textContent).toContain("LV");
    expect(detected.querySelector('[data-cite="q:abc"]')).not.toBeNull();
    answers.push({ status: 200, body: base() });
    await act(async () => {
      fireEvent.click(screen.getByTestId("base-tier-clear"));
    });
    expect(calls[1]).toEqual({ method: "POST", url: "/api/base/tier", body: { stage: null } });
    expect(screen.getByTestId("base-tier-how").textContent).toContain("From quest progress");
  });

  it("steps a machine's count and shows where each count came from", async () => {
    const ebf = {
      mte_id: 1000,
      ref: "mte:1000",
      name: "Electric Blast Furnace",
      tier: null,
      multiblock: true,
      count: 2,
      source: "manual" as const,
      sources: { manual: 2, census: 3 },
      upd: 0,
    };
    await shown(base({ machines: [ebf] }));
    const row = document.querySelector('[data-machine="1000"]') as HTMLElement;
    expect(row.textContent).toContain("ticked");
    expect(row.textContent).toContain("census 3");
    expect(row.querySelector('[data-cite="mte:1000"]')).not.toBeNull();
    answers.push({ status: 200, body: base({ machines: [{ ...ebf, count: 3, sources: { manual: 3, census: 3 } }] }) });
    await act(async () => {
      fireEvent.click(screen.getByTestId("machine-more-1000"));
    });
    expect(calls[1]).toEqual({ method: "POST", url: "/api/base/machines", body: { mte_id: 1000, count: 3 } });
    expect((screen.getByTestId("machine-count-1000") as HTMLInputElement).value).toBe("3");
  });

  it("shows a refused edit with the core's reason and keeps what was there", async () => {
    const steel = { key: "gregtech:gt.metaitem.01@11305", name: "Steel Ingot", kind: "item" as const, qty: 64, unit: "items" as const, upd: 0 };
    await shown(base({ stock: [steel] }));
    answers.push({ status: 503, body: { error: "the DB can't be written now (OperationalError)" } });
    await act(async () => {
      fireEvent.click(screen.getByTestId(`stock-remove-${steel.key}`));
    });
    expect(screen.getByTestId("base-notice").textContent).toContain("can't be written now");
    expect(document.querySelector(`[data-stock="${steel.key}"]`)).not.toBeNull();
  });

  it("lists running lines with their rate and the plans that cite it (WO-4.4)", async () => {
    await shown(base());
    expect(screen.getByTestId("base-lines").textContent).toContain("No running lines yet");
    cleanup();
    await shown(base({ lines: [{ key: "gregtech:gt.metaitem.01@11305", rate_s: 0.333333333, plans: ["plan:pl-aaaaaaaa", "plan:pl-bbbbbbbb"] }] }));
    const [row] = screen.getAllByTestId("base-line");
    expect(row.textContent).toContain("gregtech:gt.metaitem.01@11305");
    expect(row.textContent).toContain("0.333333333/s");
    expect([...row.querySelectorAll("[data-cite]")].map((c) => c.getAttribute("data-cite"))).toEqual([
      "plan:pl-aaaaaaaa",
      "plan:pl-bbbbbbbb",
    ]);
  });

  it("asks for a DB build before anything can be added", async () => {
    await shown(base({ has_db: false }));
    expect(screen.getByTestId("base-no-db").textContent).toContain("gos db build");
    expect((screen.getByTestId("machine-find-input") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByTestId("stock-find-input") as HTMLInputElement).disabled).toBe(true);
  });
});
