// The persona dropdown in Settings (WO-5.6, plan 7.11): lists the packs, marks the active one, and
// switches through POST /api/persona (the call the Discord /persona command will make too).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import type { PersonaState } from "../src/api/types";
import { PersonaSettings } from "../src/views/Settings";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const STATE: PersonaState = {
  active: "gt-veteran",
  random_per_session: false,
  packs: [
    { id: "coop-buddy", name: "Co-op Buddy", voice: "kokoro:am_puck", verbosity: "chatty", source: "shipped" },
    { id: "gt-veteran", name: "GT Veteran", voice: "kokoro:am_fenrir", verbosity: "short", source: "shipped" },
    { id: "dungeon", name: "Dungeon Announcer", voice: "", verbosity: "short", source: "vault" },
  ],
  problems: [],
};

describe("PersonaSettings", () => {
  it("lists every pack with the active one selected", () => {
    render(<PersonaSettings persona={STATE} trusted />);
    const select = screen.getByTestId("persona-select") as HTMLSelectElement;
    expect(select.value).toBe("gt-veteran");
    expect([...select.options].map((o) => o.textContent)).toEqual(["Co-op Buddy", "GT Veteran", "Dungeon Announcer (yours)"]);
  });

  it("switches through the persona API", async () => {
    const call = vi.spyOn(api, "persona").mockResolvedValue({ ...STATE, active: "coop-buddy", persona: STATE.packs[0] });
    render(<PersonaSettings persona={STATE} trusted />);
    fireEvent.change(screen.getByTestId("persona-select"), { target: { value: "coop-buddy" } });
    await waitFor(() => expect(call).toHaveBeenCalledWith("coop-buddy"));
  });

  it("shows the API's refusal and pack problems", async () => {
    vi.spyOn(api, "persona").mockRejectedValue(new Error("No persona called 'x'."));
    render(<PersonaSettings persona={{ ...STATE, problems: ["pe-bad.md: no name: line"] }} trusted />);
    expect(screen.getByTestId("persona-problems").textContent).toContain("pe-bad.md");
    fireEvent.change(screen.getByTestId("persona-select"), { target: { value: "dungeon" } });
    await waitFor(() => expect(screen.getByText(/No persona called/)).toBeTruthy());
  });

  it("can't switch from an unpaired client", () => {
    render(<PersonaSettings persona={STATE} trusted={false} />);
    expect((screen.getByTestId("persona-select") as HTMLSelectElement).disabled).toBe(true);
  });
});
