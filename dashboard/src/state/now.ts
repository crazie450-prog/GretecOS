// What Now shows first (D55): alerts when something is wrong, the last answer right after a
// question, otherwise the next steps; power net and running jobs follow.
import type { AlertCard } from "../cards/types.gen";
import type { Answer } from "../api/types";

export type Section = "approvals" | "alerts" | "answer" | "steps" | "power" | "jobs";
export type Focus = "alerts" | "answer" | "steps";

/** An answer this recent is still "right after a question". */
export const ANSWER_FRESH_S = 300;

export interface NowInputs {
  alerts: AlertCard[];
  lastAnswer: Answer | null;
  asking: boolean; // a question is being worked on
  nowS: number;
  phone: boolean; // no right rail: approvals and jobs come inline
  approvals: number;
}

export interface NowRank {
  focus: Focus;
  order: Section[];
}

export function rankNow(i: NowInputs): NowRank {
  const errors = i.alerts.some((a) => a.level === "err");
  const fresh = i.asking || (i.lastAnswer !== null && i.nowS - i.lastAnswer.ts < ANSWER_FRESH_S);
  let rank: NowRank;
  if (errors) {
    rank = { focus: "alerts", order: ["alerts", ...(fresh ? (["answer"] as const) : []), "steps", "power", "jobs"] };
  } else if (fresh) {
    rank = { focus: "answer", order: ["answer", ...(i.alerts.length ? (["alerts"] as const) : []), "steps", "power", "jobs"] };
  } else if (i.alerts.length) {
    rank = { focus: "alerts", order: ["alerts", "steps", "power", "jobs"] };
  } else {
    rank = { focus: "steps", order: ["steps", "power", "jobs", ...(i.lastAnswer ? (["answer"] as const) : [])] };
  }
  if (i.phone && i.approvals > 0) rank = { ...rank, order: ["approvals", ...rank.order] };
  return rank;
}
