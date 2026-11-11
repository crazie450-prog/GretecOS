// The setup wizard (WO-2.9): its pure logic, and the view drawn from the core's step descriptions
// (any step the registry lists, so a later phase's step needs no dashboard code). A stored secret
// is never kept in the page.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../src/api/client";
import type { SetupDevices, SetupField, SetupState, SetupStep } from "../src/api/types";
import { canFinish, deviceChoices, startValues, stepMark, wizardGate, wizardInHash } from "../src/state/setup";
import { Setup } from "../src/views/Setup";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function field(over: Partial<SetupField>): SetupField {
  return { id: "x", label: "X", kind: "text", help: "", required: false, placeholder: "", options: [], suggestions: [], ...over };
}

const paths: SetupStep = {
  id: "paths",
  title: "Paths",
  intro: "Where things are.",
  optional: false,
  fields: [
    field({ id: "prism_instances", label: "Prism instances folder", kind: "path", required: true, value: "C:/Games/PrismLauncher/instances", key: "[paths] prism_instances" }),
    field({ id: "play", label: "Play instance", required: true, value: "GTNH-Play" }),
  ],
};
const anthropic: SetupStep = {
  id: "anthropic",
  title: "Anthropic API key",
  intro: "The key.",
  optional: false,
  fields: [field({ id: "key", label: "API key", kind: "secret", secret: "anthropic", stored: false, test: true })],
};
const later: SetupStep = {
  id: "later",
  title: "A later phase",
  intro: "Added by a later WO's module.",
  optional: true,
  fields: [field({ id: "mode", label: "Mode", kind: "choice", value: "a", options: [{ value: "a", label: "A" }, { value: "b", label: "B" }] })],
};
const checklist: SetupStep = {
  id: "checklist",
  title: "Checklist",
  intro: "gos doctor's checks.",
  optional: false,
  fields: [field({ id: "doctor", label: "gos doctor", kind: "checks" })],
};

function state(over: Partial<SetupState> = {}): SetupState {
  return { done: false, completed_utc: null, config_exists: false, config_path: "C:/Users/c/GregtecOS/config/gos.toml", config_error: null, steps: [paths, anthropic, later, checklist], ...over };
}

describe("setup logic", () => {
  it("starts from gos.toml's values and leaves secrets out", () => {
    expect(startValues(paths)).toEqual({ prism_instances: "C:/Games/PrismLauncher/instances", play: "GTNH-Play" });
    expect(startValues(anthropic)).toEqual({});
  });

  it("marks steps", () => {
    const saved = new Set(["paths"]);
    expect(stepMark(paths, 0, 1, saved, false)).toBe("done");
    expect(stepMark(anthropic, 1, 1, saved, false)).toBe("current");
    expect(stepMark(later, 2, 1, saved, false)).toBe("todo");
    expect(stepMark(later, 2, 1, new Set(), true)).toBe("done"); // setup done before
    const stored = { ...anthropic, fields: [{ ...anthropic.fields[0], stored: true }] };
    expect(stepMark(stored, 1, 0, new Set(), false)).toBe("done");
    expect(stepMark(anthropic, 1, 0, new Set(), true)).toBe("todo"); // a missing secret stays to do
  });

  it("offers Windows' default, each device, and a saved value that isn't listed", () => {
    const listing: SetupDevices = {
      provider: "fake",
      inputs: [],
      outputs: [
        { name: "Headphones (Arctis Nova Elite Game)", default: true },
        { name: "Headphones (Arctis Nova Elite Chat)", default: false },
      ],
      error: null,
    };
    const out = field({ id: "output_endpoint", kind: "device", devices: "outputs" });
    const choices = deviceChoices(out, listing, "Chat");
    expect(choices[0]).toEqual({ value: "", label: "Windows' default (Headphones (Arctis Nova Elite Game))" });
    expect(choices.map((c) => c.value)).toEqual(["", "Headphones (Arctis Nova Elite Game)", "Headphones (Arctis Nova Elite Chat)", "Chat"]);
    expect(choices[3].label).toBe('"Chat" · Headphones (Arctis Nova Elite Chat)');
    expect(deviceChoices(out, listing, "USB").at(-1)?.label).toBe('"USB" · not connected now');
    expect(deviceChoices(out, null, "")).toEqual([{ value: "", label: "Windows' default" }]);
    // A field that must name a device (the spoken answers' output) has no default to fall back to.
    const named = field({ id: "output_endpoint", kind: "device", devices: "outputs", required: true });
    expect(deviceChoices(named, listing, "Chat").map((c) => c.value)).toEqual(["Headphones (Arctis Nova Elite Game)", "Headphones (Arctis Nova Elite Chat)", "Chat"]);
  });

  it("opens on the wizard only on a first run; an unfinished setup gets a banner", () => {
    expect(wizardGate(null)).toBe("none");
    expect(wizardGate({ done: false, config_exists: false })).toBe("wizard");
    expect(wizardGate({ done: false, config_exists: true })).toBe("banner");
    expect(wizardGate({ done: true, config_exists: true })).toBe("none");
  });

  it("finishes only with a checklist and no error row", () => {
    expect(canFinish(null)).toBe(false);
    expect(canFinish([])).toBe(false);
    expect(canFinish([{ id: "a", label: "A", status: "warn", detail: "" }])).toBe(true);
    expect(canFinish([{ id: "a", label: "A", status: "error", detail: "" }])).toBe(false);
  });

  it("opens at #/setup", () => {
    expect(wizardInHash("#/setup")).toBe(true);
    expect(wizardInHash("#setup")).toBe(true);
    expect(wizardInHash("#/settings")).toBe(false);
    expect(wizardInHash("#/setupx")).toBe(false);
  });
});

describe("the wizard view", () => {
  it("draws every step the core lists, a later phase's included", async () => {
    vi.spyOn(api, "setup").mockResolvedValue(state());
    render(<Setup onClose={() => undefined} onDone={() => undefined} />);
    await screen.findByTestId("setup-step-paths");
    for (const id of ["paths", "anthropic", "later", "checklist"]) expect(screen.getByTestId(`setup-nav-${id}`)).toBeTruthy();
    expect((screen.getByTestId("setup-field-play") as HTMLInputElement).value).toBe("GTNH-Play");
    fireEvent.click(screen.getByTestId("setup-nav-later"));
    const select = (await screen.findByTestId("setup-field-mode")) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["a", "b"]);
    expect(screen.queryByTestId("setup-close")).toBeNull(); // first run: no way around it
  });

  it("saves a step, shows refusals by field and moves on", async () => {
    vi.spyOn(api, "setup").mockResolvedValue(state());
    const save = vi
      .spyOn(api, "setupSave")
      .mockRejectedValueOnce(new ApiError(400, "not saved", { error: "not saved", problems: { play: "needed" }, checks: [] }))
      .mockResolvedValueOnce({ saved: ["[paths] play"], checks: [] });
    render(<Setup onClose={() => undefined} onDone={() => undefined} />);
    await screen.findByTestId("setup-step-paths");
    fireEvent.change(screen.getByTestId("setup-field-play"), { target: { value: "" } });
    await act(async () => fireEvent.click(screen.getByTestId("setup-save")));
    expect((await screen.findByTestId("setup-problem-play")).textContent).toContain("needed");
    fireEvent.change(screen.getByTestId("setup-field-play"), { target: { value: "My Play" } });
    await act(async () => fireEvent.click(screen.getByTestId("setup-save")));
    expect(save).toHaveBeenLastCalledWith("paths", { prism_instances: "C:/Games/PrismLauncher/instances", play: "My Play" });
    await screen.findByTestId("setup-step-anthropic");
  });

  it("stores a secret without keeping it, then tests it", async () => {
    const stored = state({ steps: [paths, { ...anthropic, fields: [{ ...anthropic.fields[0], stored: true }] }, checklist] });
    vi.spyOn(api, "setup").mockResolvedValueOnce(state()).mockResolvedValue(stored);
    const put = vi.spyOn(api, "setupSecret").mockResolvedValue({ name: "anthropic", stored: true, replaced: false });
    vi.spyOn(api, "setupSecretTest").mockResolvedValue({ name: "anthropic", ok: true, detail: "the API answered" });
    render(<Setup onClose={() => undefined} onDone={() => undefined} />);
    fireEvent.click(await screen.findByTestId("setup-nav-anthropic"));
    const input = screen.getByTestId("setup-secret-key") as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.change(input, { target: { value: "sk-ant-api03-secret" } });
    await act(async () => fireEvent.click(screen.getByTestId("setup-store-key")));
    expect(put).toHaveBeenCalledWith("anthropic", "sk-ant-api03-secret");
    expect(input.value).toBe("");
    await waitFor(() => expect(screen.getByTestId("setup-stored-key").dataset.stored).toBe("yes"));
    expect(document.body.innerHTML).not.toContain("sk-ant-api03-secret");
    await act(async () => fireEvent.click(screen.getByTestId("setup-test-key")));
    expect(screen.getByTestId("setup-note-key").textContent).toContain("the API answered");
  });

  it("finishes once the checklist has no error, and re-runs it", async () => {
    vi.spyOn(api, "setup").mockResolvedValue(state({ steps: [checklist] }));
    const rows = vi
      .spyOn(api, "setupChecklist")
      .mockResolvedValueOnce({ checks: [{ id: "instance.play", label: "Play instance", status: "error", detail: "no Prism instance" }] })
      .mockResolvedValueOnce({ checks: [{ id: "instance.play", label: "Play instance", status: "ok", detail: "GTNH 2.9.0" }] });
    vi.spyOn(api, "setupFinish").mockResolvedValue({ done: true, completed_utc: "2026-11-11T10:00:00+00:00", checks: [] });
    const onDone = vi.fn();
    render(<Setup onClose={() => undefined} onDone={onDone} />);
    await waitFor(() => expect(screen.getByTestId("setup-checks").textContent).toContain("no Prism instance"));
    expect((screen.getByTestId("setup-finish") as HTMLButtonElement).disabled).toBe(true);
    await act(async () => fireEvent.click(screen.getByTestId("setup-rerun")));
    expect(rows).toHaveBeenCalledTimes(2);
    expect((screen.getByTestId("setup-finish") as HTMLButtonElement).disabled).toBe(false);
    await act(async () => fireEvent.click(screen.getByTestId("setup-finish")));
    expect(onDone).toHaveBeenCalled();
  });

  it("can be closed once setup is done", async () => {
    vi.spyOn(api, "setup").mockResolvedValue(state({ done: true, completed_utc: "2026-11-11T10:00:00+00:00" }));
    const onClose = vi.fn();
    render(<Setup onClose={onClose} onDone={() => undefined} />);
    fireEvent.click(await screen.findByTestId("setup-close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("can be left for later when gos.toml exists but setup isn't done", async () => {
    vi.spyOn(api, "setup").mockResolvedValue(state({ config_exists: true }));
    const onClose = vi.fn();
    render(<Setup onClose={onClose} onDone={() => undefined} />);
    const later = await screen.findByTestId("setup-close");
    expect(later.textContent).toBe("Later");
    fireEvent.click(later);
    expect(onClose).toHaveBeenCalled();
  });
});
