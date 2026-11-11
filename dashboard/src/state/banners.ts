// The banners above every view (WO-2.8, plan 7.16, D48): the credit alert while it waits for
// Clayton or holds the AI paused, and "AI offline" for any other reason the AI is off. Built from
// /api/state's `ai` and `credits`; the core decides, this only words it.
import type { CoreState, CreditChoice } from "../api/types";

export interface CreditBanner {
  kind: "credits";
  level: "warn" | "err";
  title: string;
  body: string;
  paused: boolean;
  actions: CreditChoice[];
  billingUrl: string;
}

export interface AiBanner {
  kind: "ai";
  title: string;
  body: string;
  works: string;
  canTurnOn: boolean; // his own switch: the banner turns it back on
}

export type Banner = CreditBanner | AiBanner;

export const NEEDS_AI = "Needs the AI: design, advice, explanations, screen reading and wiki research.";

export const ACTION_LABEL: Record<CreditChoice, string> = {
  billing: "Open billing",
  no_ai: "No-AI mode",
  resume: "Resume AI",
  stop: "Stop",
};

function money(n: number): string {
  return `$${n.toFixed(2)}`;
}

export function bannersFor(core: CoreState | null): Banner[] {
  if (core === null) return [];
  const out: Banner[] = [];
  const c = core.credits;
  if (c && c.threshold !== null && c.level !== null && (c.pending || c.paused)) {
    out.push({
      kind: "credits",
      level: c.level,
      title: c.paused ? `API credits at ${Math.floor(c.pct)}%: AI paused` : `API credits at ${Math.floor(c.pct)}%`,
      body:
        `${money(c.spent_usd)} of the ${money(c.budget_usd)} included this cycle.` +
        (c.paused ? " Lookups keep working; the AI stays off until you resume it or the next cycle starts." : " GOS never buys credits."),
      paused: c.paused,
      actions: c.actions,
      billingUrl: c.billing_url,
    });
  }
  const ai = core.ai;
  const creditsSaidIt = out.length > 0 && ai.reason === "credits";
  if (!ai.online && !creditsSaidIt) {
    out.push({
      kind: "ai",
      title: "AI offline",
      body: ai.detail,
      works: ai.works ?? "instant commands, Search, recipe cards, uses, BOMs and saved plans",
      canTurnOn: ai.reason === "choice",
    });
  }
  return out;
}
