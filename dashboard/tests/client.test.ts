import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "../src/api/client";
import type { ApprovalCard } from "../src/cards/types.gen";

// WO-7.1: an action card's buttons confirm or cancel that action by its id (D59).
function card(kind: ApprovalCard["kind"], id: string): ApprovalCard {
  return {
    type: "approval",
    id,
    kind,
    title: "Confirm a world action",
    readback: '"Craft 64 Stainless Steel Ingot"',
    detail: null,
    expires_at: null,
    actions: [
      { id: "confirm", label: "Confirm", primary: true },
      { id: "cancel", label: "Cancel", primary: false },
    ],
  };
}

describe("act", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("confirms and cancels an action by its id", async () => {
    const fetch = vi.fn(async (_url: string, _init: RequestInit) => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await act(card("action", "action-42"), "confirm");
    await act(card("action", "action-42"), "cancel");
    expect(fetch.mock.calls.map((c) => [c[0], c[1].method])).toEqual([
      ["/api/actions/42/confirm", "POST"],
      ["/api/actions/42/cancel", "POST"],
    ]);
  });

  it("refuses anything else on an action card", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(act(card("action", "action-42"), "approve")).rejects.toThrow("isn't an action");
    await expect(act(card("action", "deep-42"), "confirm")).rejects.toThrow("isn't an action");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows the core's refusal (an expired action)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "That expired: x. Nothing was done; ask again." }), { status: 409 })),
    );
    await expect(act(card("action", "action-7"), "confirm")).rejects.toThrow("That expired");
  });
});
