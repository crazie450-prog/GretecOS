// Now's and Glance's "Net power · per tier" block (QA 5-9): the EU ledger the core sends (GET
// /api/power), drawn as the Power view draws it; the empty text only when there's nothing to show,
// and never naming WOs that have landed. Every name and number here is made up.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PowerLatest, PowerLedgerSection } from "../src/api/types";
import { NetPower } from "../src/views/Now";

function section(tiers: { tier: "LV" | "HV"; gen: number; use: number }[]): PowerLedgerSection {
  const card = {
    type: "power_ledger" as const,
    title: "EU ledger",
    source: { kind: "plan" as const, label: "Accepted plans", age_s: null },
    tiers: tiers.map((t) => ({ ...t, net: t.gen - t.use, ref: "plan:pl-aaa" })),
    notes: [],
  };
  return {
    card,
    running: { ...card, tiers: [] },
    warnings: [],
    running_warnings: [],
    lines: tiers.length,
    entries: [],
    window_s: 0,
    distribution: { transformers: [], buffers: [], notes: [] },
  };
}

let answer: PowerLatest;
let urls: string[];

beforeEach(() => {
  urls = [];
  vi.stubGlobal("fetch", async (url: string) => {
    urls.push(url);
    return new Response(JSON.stringify(answer), { status: 200 });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("NetPower", () => {
  it("draws the ledger's tiers when there are accepted lines", async () => {
    answer = { ids: [], ledger: section([{ tier: "HV", gen: 0, use: 512 }]) };
    render(<NetPower />);
    expect(await screen.findByTestId("power-tier-HV")).toBeTruthy();
    expect(screen.queryByTestId("now-power-empty")).toBeNull();
    expect(urls[0]).toContain("/api/power");
  });

  it("says there is no power data only when there is none, naming no work order", async () => {
    for (const ledger of [null, section([])]) {
      answer = { ids: [], ledger };
      render(<NetPower />);
      const empty = await screen.findByTestId("now-power-empty");
      expect(empty.textContent).not.toMatch(/WO-\d/);
      cleanup();
    }
  });
});
