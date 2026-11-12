// The core's API shapes the dashboard reads (gregtec.api.app, gregtec.api.dashboard). Cards are
// the catalog's own types (../cards/types.gen.ts, generated from the shared schema).
import type {
  AlertCard,
  ApprovalCard,
  BomCard,
  Card,
  ChoicesCard,
  FlowCard,
  HealthCard,
  RouteComparisonCard,
} from "../cards/types.gen";

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

/** No-AI mode (WO-2.8, plan 7.16): why the AI is off, if it is. A core without the mode sends
 * only `online` and `detail`. */
export interface AiState {
  online: boolean;
  detail: string;
  reason?: "choice" | "credits" | "no_key" | "auth" | "credits_api" | "network" | "outage" | null;
  reasons?: string[];
  since?: number | null;
  works?: string;
}

export type CreditChoice = "billing" | "no_ai" | "resume" | "stop";

/** The D48 credit flow's banner state (WO-2.8): the threshold crossed, whether it waits for an
 * answer, the pause, and the buttons. `level` null: nothing crossed this cycle. */
export interface CreditState {
  level: "warn" | "err" | null;
  threshold: number | null;
  pending: boolean;
  pct: number;
  spent_usd: number;
  budget_usd: number;
  cycle_start: string;
  cycle_end: string;
  answer: CreditChoice | null;
  paused: boolean;
  actions: CreditChoice[];
  billing_url: string;
}

/** One persona pack (WO-5.6): tone only; its voice entries per engine (D207). */
export interface PersonaInfo {
  id: string;
  name: string;
  voice: string;
  verbosity: string;
  source: "shipped" | "vault";
}

/** The packs, the active one and any pack problems (`GET /api/persona`, the state's `persona`). */
export interface PersonaState {
  active: string | null;
  random_per_session: boolean;
  packs: PersonaInfo[];
  problems: string[];
}

export interface CoreState {
  ts: number;
  game: GameState;
  data: DataState;
  ai: AiState;
  credits?: CreditState | null;
  oc: { status: "not_set_up" | "connected" | "disconnected"; detail: string };
  tier: TierState | null;
  alerts: AlertCard[];
  approvals: ApprovalCard[];
  jobs: Job[];
  asking: Asking[];
  health: HealthCard | null;
  persona?: PersonaState | null;
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

// My base (WO-3.8, /api/base): the tier with the quests' stage beside any override (D64), the
// machines he has by GT mte id and the stock he entered by item or fluid key (D63, D210).
export interface BaseTier {
  stage: string;
  tier: number;
  how: "override" | "quests";
  detected: string | null;
  detected_tier: number | null;
  quest: string | null; // q:<id>, the key quest that shows the detected stage
  quest_name: string | null;
}

export type BaseSource = "manual" | "event" | "census";

export interface BaseMachine {
  mte_id: number;
  ref: string; // mte:<id>
  name: string | null; // null: not in the current DB
  tier: number | null;
  multiblock: boolean | null;
  count: number;
  source: BaseSource; // whose count is used (D211)
  sources: Partial<Record<BaseSource, number>>;
  upd: number;
}

export interface BaseStock {
  key: string;
  name: string | null; // null: not in the current DB
  kind: "item" | "fluid";
  qty: number;
  unit: "items" | "L";
  upd: number;
}

export interface MyBase {
  tier: BaseTier;
  stages: { stage: string; tier: number }[];
  machines: BaseMachine[];
  stock: BaseStock[];
  lines: { key: string; rate_s: number }[];
  has_db: boolean;
}

export interface BaseCandidate {
  kind: "item" | "fluid" | "machine";
  key: string;
  name: string;
  mod: string | null;
  mte_id?: number;
}

export interface BaseFind {
  q: string;
  kind: "item" | "machine";
  ambiguous: boolean;
  candidates: BaseCandidate[];
}

// The setup wizard (WO-2.9, gregtec.api.setup_routes). Steps come from the core's registry, so a
// later phase's step needs no dashboard code: each field says how it is drawn.
export type SetupKind = "path" | "text" | "choice" | "device" | "key" | "phrase" | "secret" | "checks";
export type CheckStatus = "ok" | "warn" | "error";

export interface SetupField {
  id: string;
  label: string;
  kind: SetupKind;
  help: string;
  required: boolean;
  placeholder: string;
  options: { value: string; label: string }[];
  suggestions: string[];
  value?: string; // value fields: gos.toml's value, else the default
  key?: string | null; // value fields: "[table] key"
  devices?: "inputs" | "outputs" | null; // device fields
  secret?: string; // secret fields: its keyring name (the route's {name})
  stored?: boolean; // secret fields: whether one is stored (never the value)
  test?: boolean; // secret fields: a test call exists
}

export interface SetupStep {
  id: string;
  title: string;
  intro: string;
  optional: boolean;
  fields: SetupField[];
}

export interface SetupState {
  done: boolean;
  completed_utc: string | null;
  config_exists: boolean; // false: a first run, which opens on the wizard (D167)
  config_path: string;
  config_error: string | null;
  steps: SetupStep[];
}

export interface SetupCheck {
  id: string;
  label: string;
  status: CheckStatus;
  detail: string;
}

export interface SetupDevice {
  name: string;
  default: boolean;
}

export interface SetupDevices {
  provider: string;
  inputs: SetupDevice[];
  outputs: SetupDevice[];
  error: string | null;
}

// The Quests view (WO-6.4, GET /api/quests): two catalog quest cards, the frontier's item links,
// the quest lines' progress. Past the frontier only what the spoiler level shows is sent at all.
export interface QuestLink {
  part: "task" | "reward";
  key: string;
  name: string;
  amount: number | null;
  consume: boolean | null;
}

export interface QuestLine {
  name: string;
  done: number;
  available: number;
  locked: number;
  total: number;
  main_left: number;
  frontier: string[];
  note: string;
}

export interface QuestsView {
  player: string;
  tier: string;
  spoil: 0 | 1 | 2;
  default: 0 | 1 | 2;
  goals: string[];
  frontier: unknown; // a quest card, checked against the catalog before it is drawn
  beyond: unknown;
  links: Record<string, QuestLink[]>;
  lines: QuestLine[];
}

// --- The Designer (WO-3.7, plan 7.1, D250) ------------------------------------------------------
// Every number is the planner's (core/gregtec/planner/designer.py); the routes come in rank order.

export interface PlanPin {
  node: string; // the item or fluid key
  recipe: string; // r:<map>/<sig12>
}

export interface DesignerNode {
  n: string; // the plan's node id (the flow card's box id)
  item: string | null; // the item or fluid key it makes
  name: string;
  recipe: string;
  map: string | null;
  machine: string | null;
  mte: string | null;
  tier: string | null;
  count: number | null;
  pinned: boolean;
  primary: boolean;
}

export type PlanStatus = "proposed" | "accepted" | "building" | "running" | "retired";

export interface DesignerRoute {
  id: string;
  name: string;
  status: PlanStatus;
  column: "base" | "addon";
  position: number;
  of: number;
  score: number;
  target: { id: string; name: string; rate_s: number };
  tier_cap: string;
  pins: PlanPin[];
  nodes: DesignerNode[];
  warnings: string[];
  /** the comparison metrics, by key (machines, eut_avg, ..., score) */
  metrics: Record<string, number>;
  /** the choices another route of the design makes differently: "name (r:...)" */
  differs: string[];
  flow: FlowCard;
  bom: BomCard;
}

export interface DesignerView {
  id: string;
  weights: string;
  routes: DesignerRoute[];
  comparison: RouteComparisonCard;
}

export interface Designed {
  ids: string[];
  problems: string[];
  view: DesignerView;
}

/** POST /api/designer/design: the new design, or the one question when the target is unclear. */
export type DesignAnswer = Designed | { clarify: ChoicesCard };

export interface DesignSpec {
  target: string;
  rate?: string;
  tier?: string;
  routes?: number;
  addons?: boolean;
}

export interface Alternative {
  recipe: string;
  status: string; // chosen, alternative, pruned; with "show all" also unchecked, rejected
  reason: string;
  map: string | null;
  tier: string | null;
  eut: number | null;
  duration_t: number | null;
  used: boolean;
}

export interface Alternatives {
  plan: string;
  node: string;
  name: string;
  pinned: string | null;
  all: boolean;
  alternatives: Alternative[];
}

export interface Accepted {
  id: string;
  status: PlanStatus;
  line: string | null;
  note: string | null;
  ledger: { recorded: boolean; note: string };
  problems: string[];
  view: DesignerView;
}

export interface SavedPlanRow {
  id: string;
  name: string;
  status: string;
  kind: string | null;
}
