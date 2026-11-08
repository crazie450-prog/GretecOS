// The core's API shapes the dashboard reads (gregtec.api.app, gregtec.api.dashboard). Cards are
// the catalog's own types (../cards/types.gen.ts, generated from the shared schema).
import type { AlertCard, ApprovalCard, Card, HealthCard } from "../cards/types.gen";

export type Role = "admin" | "client" | "device";
export type Status = "ok" | "warn" | "error" | "off";

export interface Session {
  role: Role;
  name: string;
  device_id: string | null;
}

export interface HealthItem {
  id: string;
  kind: "process" | "check";
  label: string;
  status: Status;
  detail: string;
  restarts?: number | null;
  uptime_s?: number | null;
}

export interface Health {
  ts: number;
  overall: "ok" | "warn" | "error";
  items: HealthItem[];
}

export interface GameState {
  running: string[] | null; // roles with a GTNH client ("play", "export", "lab"); null: unknown
  focused: boolean | null; // GTNH's window in front (the core's focus signal, D161)
  paused: boolean | null; // V29: arrives with WO-6.1
  export: { holder: string; since: string; reason: string } | null;
  error: string | null;
}

export interface DataState {
  stale?: boolean | null;
  line?: string | null;
  last_export?: { run_id: string; recorded_utc: string | null } | null;
  checked_utc?: string | null;
  db_run?: { run_key: string; created_at: string } | null;
}

export interface TierState {
  stage: string; // stone, steam, LV, ... (D64)
  tier: number | null;
  how: "override" | "quests";
  detected: string | null;
}

export interface Job {
  id: string;
  kind: "deep" | "export";
  title: string;
  status: string;
  detail: string | null;
  started: number | null;
}

export interface Asking {
  id: string;
  text: string;
  ts: number;
}

export interface CoreState {
  ts: number;
  game: GameState;
  data: DataState;
  ai: { online: boolean; detail: string };
  oc: { status: "not_set_up" | "connected" | "disconnected"; detail: string };
  tier: TierState | null;
  alerts: AlertCard[];
  approvals: ApprovalCard[];
  jobs: Job[];
  asking: Asking[];
  health: HealthCard | null;
}

export interface Reply {
  text: string;
  lane: "instant" | "fast" | "deep" | "no_ai";
  intent: string;
  asked: boolean;
  card: Card | null;
  target: { key: string; name: string; kind: string } | null;
  refs: string[];
  checked: boolean;
  ttft_ms: number | null;
  total_ms: number;
  calls: number;
  error: string | null;
  // Work handed to the deep lane (D152): the router's hand-off names its task; the fast lane's
  // `handoff` tool only says it happened (the task is found by its time, see state/deep.ts).
  handoff?: { id?: string; source: string; status?: string } | null;
}

export type DeepStatus = "queued" | "running" | "needs_approval" | "done" | "failed" | "stopped" | "offline";

/** A deep-lane task as `/api/deep/tasks` and the `deep.task` push show it (D154, D156). Its
 * text is the deep lane's own: no number check runs on it (that is the fast lane's, D152). */
export interface DeepTask {
  id: string;
  status: DeepStatus;
  request: string;
  source: string;
  created: number;
  started: number | null;
  ended: number | null;
  cap_usd: number;
  spent_usd: number;
  approvals: number;
  text: string | null;
  error: string | null;
  progress: string[];
  stopping?: boolean;
}

/** An answer as the core keeps and pushes it (`ask.reply`). */
export interface Answer extends Reply {
  id: string;
  by: string;
  request: string;
  asked_ts: number;
  ts: number;
}

export interface UsageRow {
  lane?: string;
  model?: string;
  calls: number;
  usd: number;
  in_tok: number;
  out_tok: number;
  cache_read: number;
  cache_write: number;
}

export interface Usage {
  cycle_start: string;
  cycle_end: string;
  budget_usd: number;
  spent_usd: number;
  pct: number;
  alerts_pct: number[];
  crossed_pct: number[];
  calls: number;
  by_lane: UsageRow[];
  by_model: UsageRow[];
}

export interface Device {
  id: string;
  name: string;
  paired: number | null;
  last_seen: number | null;
  revoked: boolean;
}

export interface VerifyItem {
  id: string;
  item: string;
  how: string;
  wo: string;
  status: string | null;
}

export interface Verify {
  generated: string | null;
  items: VerifyItem[];
}

export interface PairPending {
  request_id: string;
  name: string;
  code: string;
  expires_in: number;
}

export type VoiceState = "idle" | "listening" | "thinking" | "speaking" | "off";
