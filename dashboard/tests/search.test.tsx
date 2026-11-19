// Search (WO-2.6, QA 5-9): a lookup the core refused (no recipe DB yet) shows its reason, never
// "No item matches."
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Search } from "../src/views/Search";

const NO_DB = "there is no DB at /home/data/gos.sqlite yet: run `gos db build` (recipes) or `gos quests load` (quests)";

function failed(tool: string) {
  return { card: { type: "table", tool, error: NO_DB, title: tool, head: [], columns: [], rows: [], span: null, total: 0, notes: [] } };
}

beforeEach(() => {
  vi.stubGlobal("fetch", async (url: string) => {
    const tool = url.split("/api/tools/")[1] ?? "x";
    return new Response(JSON.stringify(failed(tool)), { status: 200 });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Search", () => {
  it("shows the tool's error, not an empty result", async () => {
    render(<Search target={null} setTarget={() => undefined} />);
    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "steel" } });
    fireEvent.submit(screen.getByTestId("search-input").closest("form") as HTMLFormElement);
    const error = await screen.findByTestId("search-items-error");
    expect(error.textContent).toContain("gos db build");
    expect(screen.queryByText(/No item matches/)).toBeNull();
    expect(screen.queryByText(/No quest matches/)).toBeNull();
  });
});
