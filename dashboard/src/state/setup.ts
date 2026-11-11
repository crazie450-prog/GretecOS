// The setup wizard's logic without a DOM (WO-2.9): a step's starting values, where each step
// stands, the device choices, and whether the checklist lets setup finish.
import type { SetupCheck, SetupDevices, SetupField, SetupStep } from "../api/types";

export type StepMark = "done" | "current" | "todo";

/** Value fields: gos.toml's values (or the defaults) by field id. Secrets and checklists have none. */
export function startValues(step: SetupStep): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of step.fields) if (f.value !== undefined) out[f.id] = f.value;
  return out;
}

/** Where a step stands: the one shown is current; one saved now (or before setup was done), or
 * whose secrets are all stored, is done; the rest are to do. */
export function stepMark(step: SetupStep, index: number, current: number, saved: ReadonlySet<string>, done: boolean): StepMark {
  if (index === current) return "current";
  const secrets = step.fields.filter((f) => f.kind === "secret");
  if (secrets.length && secrets.every((f) => f.stored)) return "done";
  if (saved.has(step.id) || (done && !secrets.length)) return "done";
  return "todo";
}

export interface Choice {
  value: string;
  label: string;
}

/** A device field's choices: Windows' default (empty, for a field that may be empty: the
 * microphone), each listed device, and the saved value when it isn't connected now (so opening the
 * step never changes it). */
export function deviceChoices(field: SetupField, listing: SetupDevices | null, value: string): Choice[] {
  const list = field.devices === "outputs" ? (listing?.outputs ?? []) : (listing?.inputs ?? []);
  const fallback = list.find((d) => d.default);
  const out: Choice[] = field.required ? [] : [{ value: "", label: fallback ? `Windows' default (${fallback.name})` : "Windows' default" }];
  for (const d of list) out.push({ value: d.name, label: d.default ? `${d.name} · default` : d.name });
  if (value && !list.some((d) => d.name === value)) {
    // A part of a name is enough (the voice client matches it), e.g. Appendix C's "Chat".
    const hits = list.filter((d) => d.name.toLowerCase().includes(value.toLowerCase()));
    const label = hits.length === 1 ? `"${value}" · ${hits[0].name}` : hits.length ? `"${value}" · matches ${hits.length} devices` : `"${value}" · not connected now`;
    out.push({ value, label });
  }
  return out;
}

/** Whether setup may be finished: no row of the checklist is an error. */
export function canFinish(checks: readonly SetupCheck[] | null): boolean {
  return checks !== null && checks.length > 0 && !checks.some((c) => c.status === "error");
}

export type WizardGate = "wizard" | "banner" | "none";

/** What an unfinished setup shows (D167, the WO-2.9 review's H2): only a first run (no gos.toml)
 * opens on the wizard alone; a gos.toml without [setup] (an install from before the wizard) gets a
 * banner he can dismiss, so the dashboard is never locked. None when the core can't say. */
export function wizardGate(s: { done: boolean; config_exists: boolean } | null): WizardGate {
  if (s === null || s.done) return "none";
  return s.config_exists ? "banner" : "wizard";
}

/** The wizard's location: #/setup opens it at any time (Settings links there). */
export function wizardInHash(hash: string): boolean {
  return /^#\/?setup(\/|$|\?)/.test(hash);
}
