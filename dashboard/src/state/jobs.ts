// Design jobs (WO-3.9, D353, D354): how a job's stage and time read, and which job a view
// follows. Pure, so it is tested without a DOM; the hook and the panel are in
// components/JobProgress.tsx.
import type { CoreState, DesignJob } from "../api/types";

const WORDS: Record<string, string> = {
  waiting: "Waiting for the other design to finish",
  graph: "Building the recipe graph",
  solve: "Solving route",
  metrics: "Measuring route",
  ranking: "Ranking (code's order, rank.py)",
  saving: "Saving the plans",
  candidates: "Finding generators and fuels",
  chains: "Fuel chain",
  lines: "Sizing the lines",
};

/** A job's stage in words: "Solving route 2 of 3", "Measuring route 1 of 3 · effort 12 of 40". */
export function stageText(job: Pick<DesignJob, "stage" | "k" | "n" | "sub">): string {
  const base = WORDS[job.stage] ?? job.stage;
  const count = job.k && job.n ? ` ${job.k} of ${job.n}` : "";
  const sub = job.sub ? ` · effort ${job.sub[0]} of ${job.sub[1]}` : "";
  return `${base}${count}${sub}`;
}

/** Elapsed time as m:ss (h:mm:ss past an hour): the core's `elapsed_s` as received at
 * `receivedMs`, plus the time since on this device. Never the core's start time against this
 * device's clock, so a phone whose clock is skewed still shows the right time (review LOW-3). */
export function elapsed(elapsedS: number, receivedMs: number, nowMs: number): string {
  const s = Math.max(0, Math.floor(elapsedS + (nowMs - receivedMs) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export const ENDED = ["done", "failed", "cancelled", "interrupted"];

export function ended(job: Pick<DesignJob, "status">): boolean {
  return ENDED.includes(job.status);
}

/** The running job of a kind the core's state lists (the right rail's), for a view opened
 * while it runs: the view follows it instead of starting another. */
export function runningOf(core: CoreState | null, kind: "designer" | "power"): string | null {
  const j = (core?.jobs ?? []).find((x) => x.kind === kind && x.status === "running");
  return j ? j.id : null;
}

/** What an ended job tells the view when it has no result to show. */
export function endedText(job: Pick<DesignJob, "status" | "error">): string {
  if (job.status === "cancelled") return "Cancelled: nothing was saved.";
  if (job.status === "interrupted") return job.error ?? "The core stopped while the design ran: start it again.";
  return job.error ?? "The design failed.";
}

/** The newer of two views of one job: a push and a poll can cross. */
export function newer(a: DesignJob | null, b: DesignJob | undefined): DesignJob | null {
  if (!b || (a && a.id !== b.id)) return a;
  if (!a) return b;
  if (ended(a) && !ended(b)) return a;
  if (ended(b) && !ended(a)) return b;
  return b.elapsed_s >= a.elapsed_s ? b : a;
}
