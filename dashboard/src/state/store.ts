// The dashboard's state: what the core pushed or answered, kept in one small external store
// (React reads it with useSyncExternalStore). Pure reducers live beside it (mode.ts, now.ts,
// states.ts) so the logic is tested without a DOM.
import { useSyncExternalStore } from "react";
import type { Answer, CoreState, DeepTask, Health, Session, VoiceState } from "../api/types";
import { addTasks } from "./deep";

export type Phase = "loading" | "unpaired" | "ready";
export type Socket = "connecting" | "open" | "closed";

export interface AppState {
  phase: Phase;
  reachable: boolean; // false while the core doesn't answer (core offline, D76)
  socket: Socket;
  session: Session | null;
  core: CoreState | null;
  health: Health | null;
  answers: Answer[];
  deep: DeepTask[]; // deep-lane tasks (WO-2.4), for Chat
  voice: { state: VoiceState; trigger: string | null; ts: number } | null;
  nextCard: number; // bumped by the "next card" hotkey (WO-5.1's ui push)
  error: string | null;
}

export const ANSWERS_KEEP = 50;

export const initialState: AppState = {
  phase: "loading",
  reachable: true,
  socket: "closed",
  session: null,
  core: null,
  health: null,
  answers: [],
  deep: [],
  voice: null,
  nextCard: 0,
  error: null,
};

export interface Store {
  get: () => AppState;
  set: (patch: Partial<AppState> | ((s: AppState) => Partial<AppState>)) => void;
  subscribe: (fn: () => void) => () => void;
}

export function createStore(start: AppState = initialState): Store {
  let state = start;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set(patch) {
      const next = typeof patch === "function" ? patch(state) : patch;
      state = { ...state, ...next };
      for (const fn of listeners) fn();
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

export const store = createStore();

export function useApp<T>(select: (s: AppState) => T, from: Store = store): T {
  return useSyncExternalStore(from.subscribe, () => select(from.get()));
}

/** Add an answer (from /api/answers or an ask.reply push) once, keeping the newest 50. */
export function addAnswers(list: Answer[], more: Answer[]): Answer[] {
  const byId = new Map(list.map((a) => [a.id, a]));
  for (const a of more) byId.set(a.id, a);
  return [...byId.values()].sort((a, b) => a.ts - b.ts).slice(-ANSWERS_KEEP);
}

/** What one WebSocket message from the core changes (unknown messages change nothing). */
export function applyMessage(s: AppState, message: unknown): Partial<AppState> {
  if (typeof message !== "object" || message === null) return {};
  const m = message as { type?: unknown; data?: unknown; action?: unknown };
  switch (m.type) {
    case "health":
      return { health: m.data as Health };
    case "state":
      return { core: m.data as CoreState };
    case "ask.reply":
      return { answers: addAnswers(s.answers, [m.data as Answer]) };
    case "deep.task":
      return { deep: addTasks(s.deep, [m.data as DeepTask]) };
    case "voice.state":
      return { voice: m.data as AppState["voice"] };
    case "ui":
      return m.action === "next_card" ? { nextCard: s.nextCard + 1 } : {};
    default:
      return {};
  }
}
