// Deep-lane tasks in the conversation (WO-2.4's `deep.task` pushes, D154): kept by id, the newest
// 50, and matched to the answer that handed them off, so Chat shows a task's progress and result
// under the question that started it. A task's text gets no number check (the fast lane's check,
// D152, isn't run on it), so it is always shown as "numbers not checked".
import type { Answer, DeepStatus, DeepTask } from "../api/types";
import { GLYPH } from "./states";

export const TASKS_KEEP = 50;
/** Seconds of slack around an ask when a task is matched by its time (a fast-lane hand-off). */
export const MATCH_SLACK_S = 2;

/** Add tasks (from /api/deep/tasks or a push) once each, the newest version winning. */
export function addTasks(list: DeepTask[], more: DeepTask[]): DeepTask[] {
  const byId = new Map(list.map((t) => [t.id, t]));
  for (const t of more) byId.set(t.id, t);
  return [...byId.values()].sort((a, b) => a.created - b.created).slice(-TASKS_KEEP);
}

export interface Matched {
  byAnswer: Map<string, DeepTask>; // answer id -> the task it handed off
  loose: DeepTask[]; // tasks no answer here handed off (another process's, say)
}

/** Each answer's task: the router's hand-off names it; the fast lane's `handoff` tool only says
 * one happened, so its task is the one made while that question was being answered. */
export function matchTasks(answers: Answer[], tasks: DeepTask[]): Matched {
  const byAnswer = new Map<string, DeepTask>();
  const claimed = new Set<string>();
  const byId = new Map(tasks.map((t) => [t.id, t]));
  for (const a of answers) {
    const id = a.handoff?.id;
    const task = id ? byId.get(id) : undefined;
    if (task && !claimed.has(task.id)) {
      byAnswer.set(a.id, task);
      claimed.add(task.id);
    }
  }
  for (const a of answers) {
    if (!a.handoff || a.handoff.id || byAnswer.has(a.id)) continue;
    const task = tasks.find(
      (t) => !claimed.has(t.id) && t.created >= a.asked_ts - MATCH_SLACK_S && t.created <= a.ts + MATCH_SLACK_S,
    );
    if (task) {
      byAnswer.set(a.id, task);
      claimed.add(task.id);
    }
  }
  return { byAnswer, loose: tasks.filter((t) => !claimed.has(t.id)) };
}

export interface DeepLook {
  glyph: string;
  status: "ok" | "warn" | "err" | "off" | "info";
  label: string;
}

const LOOKS: Record<DeepStatus, DeepLook> = {
  queued: { glyph: GLYPH.todo, status: "info", label: "Deep lane · queued" },
  running: { glyph: GLYPH.progress, status: "info", label: "Deep lane · working" },
  needs_approval: { glyph: GLYPH.paused, status: "warn", label: "Deep lane · paused at its cap" },
  done: { glyph: GLYPH.done, status: "ok", label: "Deep lane · done" },
  failed: { glyph: GLYPH.failed, status: "err", label: "Deep lane · failed" },
  stopped: { glyph: GLYPH.paused, status: "off", label: "Deep lane · stopped" },
  offline: { glyph: GLYPH.waiting, status: "warn", label: "Deep lane · offline" },
};

/** How a task's state reads: a glyph and a word, never colour alone. */
export function deepLook(status: string): DeepLook {
  return LOOKS[status as DeepStatus] ?? { glyph: GLYPH.waiting, status: "info", label: `Deep lane · ${status}` };
}
