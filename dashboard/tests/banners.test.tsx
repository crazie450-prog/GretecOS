// The banners (WO-2.8, plan 7.16, D48): the credit alert at 80% and 100% with D48's buttons, and
// "AI offline" with what still works and what needs the AI.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AiState, CoreState, CreditState } from "../src/api/types";
import { Banners } from "../src/components/Banners";
import { NEEDS_AI, bannersFor } from "../src/state/banners";

afterEach(cleanup);

function core(ai: Partial<AiState> = {}, credits: Partial<CreditState> | null = null): CoreState {
  return {
    ts: 0,
    game: { running: ["play"], focused: true, paused: null, export: null, error: null },
    data: { stale: false, line: null },
    ai: { online: true, detail: "online", reason: null, reasons: [], since: null, works: "instant commands, Search", ...ai },
    credits:
      credits === null
        ? null
        : {
            level: null,
            threshold: null,
            pending: false,
            pct: 0,
            spent_usd: 0,
            budget_usd: 100,
            cycle_start: "2026-11-01T00:00:00+00:00",
            cycle_end: "2026-12-01T00:00:00+00:00",
            answer: null,
            paused: false,
            actions: [],
            billing_url: "https://console.anthropic.com/settings/billing",
            ...credits,
          },
    oc: { status: "not_set_up", detail: "" },
    tier: null,
    alerts: [],
    approvals: [],
    jobs: [],
    asking: [],
    health: null,
  };
}

const at80: Partial<CreditState> = {
  level: "warn",
  threshold: 80,
  pending: true,
  pct: 80.5,
  spent_usd: 80.5,
  actions: ["billing", "no_ai", "stop"],
};
const at100: Partial<CreditState> = {
  level: "err",
  threshold: 100,
  pending: true,
  paused: true,
  pct: 101,
  spent_usd: 101,
  actions: ["billing", "resume", "stop"],
};

describe("which banners show", () => {
  it("none while all is well, or with no state yet", () => {
    expect(bannersFor(null)).toEqual([]);
    expect(bannersFor(core({}, {}))).toEqual([]);
  });
  it("AI offline, with what works and whether his switch turns it back on", () => {
    const [b] = bannersFor(core({ online: false, reason: "no_key", detail: "no Anthropic key is stored" }));
    expect(b).toMatchObject({ kind: "ai", title: "AI offline", body: "no Anthropic key is stored", canTurnOn: false });
    const [mine] = bannersFor(core({ online: false, reason: "choice", detail: "you turned the AI off" }));
    expect(mine).toMatchObject({ kind: "ai", canTurnOn: true });
  });
  it("80%: the credit alert asks, the AI stays on", () => {
    const b = bannersFor(core({}, at80));
    expect(b).toHaveLength(1);
    expect(b[0]).toMatchObject({ kind: "credits", level: "warn", title: "API credits at 80%", paused: false });
  });
  it("100%: one banner says the AI is paused (no separate AI offline)", () => {
    const b = bannersFor(core({ online: false, reason: "credits", detail: "paused for credits" }, at100));
    expect(b.map((x) => x.kind)).toEqual(["credits"]);
    expect(b[0]).toMatchObject({ level: "err", title: "API credits at 101%: AI paused", paused: true });
  });
  it("an answered 80% alert goes away; a pause stays until resumed", () => {
    expect(bannersFor(core({}, { ...at80, pending: false, answer: "billing" }))).toEqual([]);
    const paused = bannersFor(core({ online: false, reason: "credits" }, { ...at100, pending: false, answer: "billing" }));
    expect(paused.map((x) => x.kind)).toEqual(["credits"]);
  });
  it("credits paused and the API down: both", () => {
    const b = bannersFor(core({ online: false, reason: "no_key", detail: "no key" }, at100));
    expect(b.map((x) => x.kind)).toEqual(["credits", "ai"]);
  });
});

describe("the banners' buttons", () => {
  it("the 80% alert offers Open billing, No-AI mode and Stop; billing opens the Console", () => {
    const decide = vi.fn(() => Promise.resolve());
    const opened = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<Banners banners={bannersFor(core({}, at80))} decide={decide} turnOn={() => Promise.resolve()} />);
    expect(screen.getByTestId("credit-billing").textContent).toBe("Open billing");
    expect(screen.getByTestId("credit-no_ai").textContent).toBe("No-AI mode");
    fireEvent.click(screen.getByTestId("credit-billing"));
    expect(opened).toHaveBeenCalledWith("https://console.anthropic.com/settings/billing", "_blank", "noopener,noreferrer");
    expect(decide).toHaveBeenCalledWith("billing");
    opened.mockRestore();
  });
  it("Stop asks once more before it stops GOS", () => {
    const decide = vi.fn(() => Promise.resolve());
    render(<Banners banners={bannersFor(core({}, at100))} decide={decide} turnOn={() => Promise.resolve()} />);
    expect(screen.getByTestId("credit-resume").textContent).toBe("Resume AI");
    fireEvent.click(screen.getByTestId("credit-stop"));
    expect(decide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("credit-stop-cancel"));
    fireEvent.click(screen.getByTestId("credit-stop"));
    fireEvent.click(screen.getByTestId("credit-stop-confirm"));
    expect(decide).toHaveBeenCalledWith("stop");
  });
  it("AI offline says what still works and what needs the AI", () => {
    const turnOn = vi.fn(() => Promise.resolve());
    render(
      <Banners banners={bannersFor(core({ online: false, reason: "choice", detail: "you turned the AI off" }))} decide={() => Promise.resolve()} turnOn={turnOn} />,
    );
    const banner = screen.getByTestId("banner-ai");
    expect(banner.textContent).toContain("Still works: instant commands, Search.");
    expect(banner.textContent).toContain(NEEDS_AI);
    fireEvent.click(screen.getByTestId("ai-turn-on"));
    expect(turnOn).toHaveBeenCalled();
  });
});
