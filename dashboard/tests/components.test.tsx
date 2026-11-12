// The core components and shell pieces: status is a glyph and a word, approvals read back and
// count down, the palette's entries and keys, the nav's number keys, the voice strip's states.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoreState } from "../src/api/types";
import type { ApprovalCard, Card } from "../src/cards/types.gen";
import { ApprovalView, CitationChip, NavItem, StatusGlyph, TierChip, stageTier } from "../src/components/core";
import { Header } from "../src/components/Header";
import { PLANS_LATER, arrange, itemEntries, move, questEntries, staticEntries } from "../src/components/palette";
import { Rail } from "../src/components/Rail";
import { voiceLook } from "../src/components/VoiceStrip";
import { stateChips } from "../src/state/states";
import { creditLevel } from "../src/views/Costs";
import { PHONE_TABS, VIEWS, viewForKey, viewFromHash } from "../src/views/nav";

afterEach(cleanup);

const approval: ApprovalCard = {
  type: "approval",
  id: "pair-abc",
  kind: "pairing",
  title: "A device wants to pair",
  readback: '"Pair Pixel 9" with code 1234 5678',
  detail: null,
  expires_at: Math.floor(Date.now() / 1000) + 3,
  actions: [{ id: "deny", label: "Deny", primary: false }],
};

describe("status", () => {
  it("is a glyph and a word", () => {
    render(<StatusGlyph status="err" label="Core offline" />);
    // The word is its own span (the glyph is hidden from screen readers): check the whole status.
    const el = screen.getByText("Core offline").closest(".gos-status");
    expect(el?.textContent).toContain("\u25B2");
    expect(el?.getAttribute("data-status")).toBe("err");
    expect(el?.className).toContain("err");
  });
  it("tier chips always carry the tier's name", () => {
    render(<TierChip tier="LV" />);
    expect(screen.getByText("LV").className).toContain("tier-lv");
    expect(stageTier("stone")).toBe("Steam");
    expect(stageTier("MV")).toBe("MV");
  });
  it("a citation chip shows its id unless data is stale", () => {
    const { rerender } = render(<CitationChip cite="r:m/0123456789ab" />);
    expect(screen.getByText("r:m/0123456789ab")).toBeTruthy();
    rerender(<CitationChip cite="r:m/0123456789ab" stale />);
    expect(screen.getByText(/data stale/)).toBeTruthy();
  });
});

describe("approvals", () => {
  it("read back what will happen, count down, and run their actions", () => {
    vi.useFakeTimers();
    const onAction = vi.fn();
    render(<ApprovalView card={approval} onAction={onAction} />);
    expect(screen.getByText(/Waiting for you/)).toBeTruthy();
    expect(screen.getByText(/"Pair Pixel 9" with code 1234 5678/)).toBeTruthy();
    expect(Number(screen.getByTestId("countdown").textContent?.split(" ")[0])).toBeGreaterThanOrEqual(2);
    fireEvent.click(screen.getByTestId("action-deny"));
    expect(onAction).toHaveBeenCalledWith(approval, "deny");
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByTestId("countdown").textContent).toBe("0 s");
    expect((screen.getByTestId("action-deny") as HTMLButtonElement).disabled).toBe(true);
    vi.useRealTimers();
  });
  it("end a world action's read-back with 'Or just say confirm' (D59)", () => {
    render(<ApprovalView card={{ ...approval, kind: "action", expires_at: null }} onAction={() => undefined} />);
    expect(screen.getByText(/Or just say confirm\./)).toBeTruthy();
  });
});

const core: CoreState = {
  ts: 0,
  game: { running: [], focused: null, paused: null, export: null, error: null },
  data: { stale: true, line: "Play changed" },
  ai: { online: false, detail: "not stored" },
  oc: { status: "not_set_up", detail: "" },
  tier: { stage: "LV", tier: 1, how: "quests", detected: "LV" },
  alerts: [],
  approvals: [approval],
  jobs: [{ id: "t1", kind: "deep", title: "plan a steel line", status: "running", detail: "solving", started: 1 }],
  asking: [{ id: "q1", text: "recipe for steel", ts: 1 }],
  health: null,
};

describe("the shell", () => {
  it("header shows the state chips, the tier and the clock", () => {
    render(<Header chips={stateChips({ phase: "ready", reachable: true, core })} core={core} glance={false} held={false} onLeave={() => undefined} onSearch={() => undefined} />);
    const chips = screen.getByTestId("status-chips");
    expect(chips.textContent).toContain("Game not running");
    expect(chips.textContent).toContain("Data stale");
    expect(chips.textContent).toContain("AI offline");
    expect(screen.getByText("LV").className).toContain("tier-lv");
    expect(screen.getByLabelText("time").textContent).toMatch(/^\d\d:\d\d\d\d$/);
  });
  it("glance drops the search box", () => {
    render(<Header chips={[]} core={null} glance held={false} onLeave={() => undefined} onSearch={() => undefined} />);
    expect(screen.queryByTestId("open-palette")).toBeNull();
    expect(screen.queryByTestId("leave-glance")).toBeNull();
  });
  it("a held glance shows the way back to automatic (D161)", () => {
    const leave = vi.fn();
    render(<Header chips={[]} core={null} glance held onLeave={leave} onSearch={() => undefined} />);
    fireEvent.click(screen.getByTestId("leave-glance"));
    expect(leave).toHaveBeenCalledOnce();
  });
  it("the rail shows what the agent does, what waits and the jobs", () => {
    const act = vi.fn();
    render(<Rail core={core} act={act} />);
    expect(screen.getByText(/"recipe for steel"/)).toBeTruthy();
    expect(screen.getByText("Waiting for you · 1")).toBeTruthy();
    expect(screen.getAllByText("plan a steel line").length).toBeGreaterThan(0);
  });
  it("the voice strip follows the voice client, else the questions in flight", () => {
    expect(voiceLook(null, false)).toBe("standby");
    expect(voiceLook(null, true)).toBe("thinking");
    expect(voiceLook("listening", true)).toBe("listening");
    expect(voiceLook("speaking", false)).toBe("speaking");
    expect(voiceLook("off", false)).toBe("off");
    expect(voiceLook("idle", false)).toBe("standby");
  });
});

describe("the nav", () => {
  it("gives the built views and the boarded ones number keys 1 to 9", () => {
    expect(VIEWS.filter((v) => v.key).map((v) => `${v.key}:${v.id}`)).toEqual([
      "1:now", "2:chat", "3:search", "4:designer", "5:power", "6:quests", "7:crops", "8:costs", "9:settings",
    ]);
    expect(viewForKey("8")?.id).toBe("costs");
    expect(viewForKey("0")).toBeNull();
    expect(VIEWS.filter((v) => !v.later).map((v) => v.id)).toEqual(["now", "chat", "search", "designer", "quests", "costs", "settings", "base", "ores"]);
    expect(PHONE_TABS).toEqual(["now", "chat", "search", "base", "costs", "settings"]);
  });
  it("marks the views with no approved board (D92)", () => {
    expect(VIEWS.filter((v) => !v.later && !v.board).map((v) => v.id)).toEqual(["chat", "search", "costs", "base", "ores"]);
  });
  it("reads the view from the hash", () => {
    expect(viewFromHash("#/chat")).toBe("chat");
    expect(viewFromHash("#/settings?x=1")).toBe("settings");
    expect(viewFromHash("#/nope")).toBe("now");
    expect(viewFromHash("")).toBe("now");
  });
  it("items show their key and state", () => {
    const click = vi.fn();
    render(<NavItem label="Now" keyHint="1" active onClick={click} testId="nav-now" />);
    const item = screen.getByTestId("nav-now");
    expect(item.getAttribute("aria-current")).toBe("page");
    expect(item.textContent).toContain("1");
    fireEvent.click(item);
    expect(click).toHaveBeenCalled();
  });
});

describe("the palette", () => {
  it("filters views and settings", () => {
    expect(staticEntries("cos").map((e) => e.label)).toEqual(["Costs"]);
    expect(staticEntries("doctor").map((e) => e.id)).toEqual(["s-doctor"]);
    expect(staticEntries("").length).toBe(VIEWS.length + 4);
  });
  it("turns item_resolve and quest_find cards into entries", () => {
    const items: Card = {
      type: "table", tool: "item_resolve", error: null, title: "steel", head: [],
      columns: ["n", "kind", "key", "name", "mod", "score", "via"],
      rows: [["1", "item", "gregtech:gt.metaitem.01@11305", "Steel Ingot", "gregtech", "0.75", ""]],
      span: "1-1", total: null, notes: [],
    };
    const found = itemEntries(items);
    expect(found.map((e) => e.group)).toEqual(["Items", "Recipes", "Recipes"]);
    expect(found[1].label).toBe("Recipes for Steel Ingot");
    expect(found[2].action).toEqual({ kind: "item", key: "gregtech:gt.metaitem.01@11305", name: "Steel Ingot", tool: "recipe_uses" });
    const quests: Card = {
      type: "quest", tool: "quest_find", error: null, title: "steel", tier: null, spoil: null, total: 1, notes: [],
      quests: [{ ref: "q:AAAAAAAAAAAAAAAAAAAABg", name: null, state: "locked", lines: null, main: false, shown: false, description: null, rows: [] }],
    };
    expect(questEntries(quests)[0].label).toBe("A quest past your frontier"); // nudge-first (D26)
    expect(itemEntries({ ...items, error: "no DB" } as Card)).toEqual([]);
  });
  it("orders groups and wraps the arrow keys", () => {
    const order = arrange([PLANS_LATER, ...staticEntries("now")]).map((e) => e.group);
    expect(order).toEqual(["Views", "Plans"]);
    expect(move(0, -1, 3)).toBe(2);
    expect(move(2, 1, 3)).toBe(0);
    expect(move(0, 1, 0)).toBe(0);
  });
});

describe("costs", () => {
  it("are ok below the first alert, warn past it, err past the last (D48)", () => {
    expect(creditLevel({ alerts_pct: [80, 100], crossed_pct: [] })).toBe("ok");
    expect(creditLevel({ alerts_pct: [80, 100], crossed_pct: [80] })).toBe("warn");
    expect(creditLevel({ alerts_pct: [80, 100], crossed_pct: [80, 100] })).toBe("err");
  });
});
