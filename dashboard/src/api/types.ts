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
  PowerLedgerCard,
  RouteComparisonCard,
  TableCard,
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
  // A spoiler command's grant (WO-6.4, D268 R3): the level and the quests it is for, only.
  spoil?: { level: 1 | 2; quests: string[] } | null;
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

// GET /api/guide (WO-10.1, D304): Clayton's GuideNH export in GOS_HOME/data/guide-site.
export interface GuideSite {
  present: boolean;
  pages: { path: string; title: string; scenes: number }[];
  files: number;
  bytes: number;
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
  lines: { key: string; rate_s: number; plans: string[] }[];
  has_db: boolean;
}

// The live base (WO-7.4, GET /api/base/live): OC telemetry as the core reads it. Every row
// carries its tel: citation and its age; stale rows are old readings, never current ones.
export type LiveStatus = "running" | "stopped" | "stalled" | "paused" | "idle" | "unknown" | "stale";
export type LiveLevel = "bad" | "warn" | "info";

export interface LivePower {
  id: string;
  cite: string;
  name: string;
  meta: string | null;
  mte_id: number | null;
  kind: "battery_buffer" | "storage" | string;
  age_s: number;
  stale: boolean;
  eu_in: number | null;
  eu_out: number | null;
  eu?: number | null;
  eu_max?: number | null;
  eu_str?: string | null;
  eu_max_str?: string | null;
  pct: number | null;
}

export interface LiveMachine {
  id: string;
  cite: string;
  name: string;
  meta: string | null;
  mte_id: number | null;
  xyz: number[] | null;
  status: LiveStatus;
  progress: number | null;
  progress_max: number | null;
  problems: number | null;
  problems_age_s: number | null;
  age_s: number;
  stale: boolean;
}

export interface LiveProblem {
  level: LiveLevel;
  kind: string;
  cite: string;
  text: string;
  age_s?: number;
}

export interface LiveCraft {
  id: number;
  name: string;
  amount: number;
  state: string;
  err: string | null;
  cite: string;
  stale: boolean;
}

/** A telemetry alert (WO-7.5, gregtec.alerts.engine.Alert.as_dict). */
export interface LiveAlert {
  key: string;
  rule: "power_low" | "maintenance" | "machine_stalled" | "crafting_done";
  binding: string;
  subject: string;
  level: "err" | "warn" | "info";
  title: string;
  body: string;
  say: string;
  cite: string;
  fired_at: number;
  value: number | null;
  unit: string | null;
  stale: "paused" | "silent" | null;
}

export interface LiveBinding {
  binding: string;
  status: "connected" | "stale" | "not_set_up";
  age_s: number | null;
  agents: { address: string; cite: string; transport: "http" | "mailbox"; age_s: number; stale: boolean; tick_ms: number | null }[];
  power: LivePower[];
  net_eu_t: number | null;
  machines: LiveMachine[];
  problems: LiveProblem[];
  /** WO-7.5: the alerts firing here (crafting done included) and why they are held, if so. */
  alerts: LiveAlert[];
  held: "paused" | "silent" | null;
  ae2: {
    cpus: number;
    busy: number;
    crafts: LiveCraft[];
    items: { key: string; name: string; size: number | null; craftable: boolean | null; cite: string }[];
  } | null;
}

export interface LiveBase {
  ts: number;
  stale_s: number;
  bindings: LiveBinding[];
}

export interface ActionRow {
  id: number;
  kind: string;
  state: string;
  target: string;
  readback: string;
  created: number;
  done_ts: number | null;
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
  open: string[] | null; // the quests `spoil` is for (a grant from chat); null: all of them
  default: 0 | 1 | 2;
  goals: string[];
  frontier: unknown; // a quest card, checked against the catalog before it is drawn
  beyond: unknown;
  links: Record<string, QuestLink[]>;
  lines: QuestLine[];
}

// The Ores view (WO-6.6, GET /api/ores): where he was at the last save, the veins' world, and
// with a material the catalog's ore list card, ranked by the core (D142 (c)); never re-ranked.
export interface OrePosition {
  x: number;
  y: number;
  z: number;
  dim: number;
  dim_name: string | null;
  world: string;
  age_s: number;
}

export interface OresView {
  source: { world: string | null; instance: "lab" | "play" | null; loaded_utc: string | null; veins: number };
  position: OrePosition | null;
  position_note: string | null;
  dims: { dim: number; name: string | null; veins: number }[];
  materials: string[];
  material: string | null;
  dim: number | null;
  card: unknown; // an ore list card, checked against the catalog before it is drawn
  can_mark: boolean;
  mark_note: string | null;
}

export interface WaypointProposal {
  text: string; // GOS's read-back: "Add waypoint ... Say confirm."
  action: { id: number; state: string };
}

// --- The Crops view (WO-6.7, plan 7.14, 7.17, D275) ---------------------------------------------
// Every number is the crop planner's (core/gregtec/crops/planner.py), with its citations.

export interface CropNum {
  value: number;
  unit: string;
  text: string;
  refs: string[];
}

export interface CropLine {
  text: string;
  nums: CropNum[];
  refs: string[]; // every citation of the line: its own and its numbers'
}

export interface CropRequirement extends CropLine {
  what: string; // soil, sub_soil, light, nutrients, catalyst, machine, analyzed, other
  status: "met" | "needed" | "check";
}

export interface CropCell {
  role: "parent" | "double" | "empty";
  crop: string | null;
  name: string | null;
  under: string | null; // the block to put under the soil, by name
}

export interface CropStep {
  n: number;
  kind: "crop_manager" | "obtain" | "sticks" | "pool" | "breeder";
  child: string;
  child_name: string;
  ref: string;
  parents: { crop: string; ref: string }[];
  title: string;
  layout: CropCell[][];
  requirements: CropRequirement[];
  numbers: CropLine[];
  warnings: CropLine[];
  keep: CropLine[];
  refs: string[];
  done: boolean;
}

export interface CropGuide {
  target: string;
  target_name: string;
  ref: string;
  tier: number;
  tier_name: string;
  run: string;
  steps: CropStep[];
  next: number | null;
  total: CropLine | null;
  stats: { target: number[]; variations: number[]; lines: CropLine[]; warnings: CropLine[] } | null;
  notes: CropLine[];
}

export interface CropRef {
  crop: string;
  ref: string;
  name: string;
}

export interface CropsView {
  run: string;
  tier: number;
  tier_name: string;
  have: (CropRef & { stats: number[] })[];
  target: (CropRef & { aim: number[] }) | null;
  crops: (CropRef & { tier: number })[];
  picks: CropRef[];
  picks_note: string;
  // the times' best case: water and fertilizer topped up (by a Crop Manager he has, else by hand)
  setup: { water: number; sky: boolean; liked_biome_tags: number; crop_manager: boolean };
  guide: CropGuide | null;
  error: string | null;
}

// --- The breeding station (WO-7.6, plan 7.17 item 4, D282-D284) --------------------------------
// The plan is gregtec.crops.station's; the status is each OC station's last report (game data).

export interface CropStationStatus {
  binding: string;
  address: string;
  ref: string; // tel:<binding>/<address>
  age_s: number;
  stale: boolean;
  state: string;
  runs: number;
  ok: number;
  fail: number;
  stock: Record<string, number>;
  plan?: string;
  target?: string;
  count?: number;
  steps?: number;
  step?: number;
  doing?: string;
  note?: string;
  copiers?: (string | false)[];
  cat_left?: number[]; // catalyst items left in each Seed Generator's inputs
}

export interface CropStationPlan {
  id: string;
  target: string;
  target_name: string;
  count: number;
  tier: number;
  steps: { n: number; child: string; child_name: string; chance_pct: number; runs: number; copies: number }[];
  lines: string[];
  refs: string[];
}

export interface CropStationView {
  status: CropStationStatus[];
  plan: CropStationPlan | null;
  error: string | null;
}

export interface CropStationProposal {
  ok: boolean;
  say: string; // GOS's read-back: "Start the breeding station: ... Say confirm."
  action: { id: number; state: string };
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

// --- The Activity view (WO-6.3, GET /api/activity, plan 7.5, 7.14) ------------------------------
// Sessions, one session's events and counts (computed by the core, D142 (f)), its journal lines
// and the finished ages' recaps (D162, D265). Event text is the game's: data, shown as plain text.

export interface ActivityEvent {
  id: number;
  ts: number; // epoch seconds
  kind: string;
  session: number | null;
  instance: "play" | "lab" | null;
  source: string | null;
  ref: string | null; // q:<id>, mte:<id>
  where: { dim: number | null; x: number; y: number | null; z: number | null } | null;
  data: Record<string, unknown>;
}

export interface ActivitySession {
  id: number;
  instance: "play" | "lab" | null;
  world: string | null;
  start_ts: number;
  end_ts: number | null;
  open: boolean;
  start_why: string | null; // login, resume
  end_why: string | null; // quit, crash, idle, relogin
  last_ts: number | null;
  played_s: number;
  journal: string | null;
  counts: Record<string, number>;
}

export interface AgeRecap {
  age: number;
  stage: string;
  start_ts: number;
  end_ts: number;
  hours_played: number;
  sessions: number;
  machines_built: number;
  best_net_eut: number | null; // null: no OC power readings in the age
  best_ref: string | null; // db:tel_sample/<src>@<ts>
}

export interface ActivityView {
  ts: number;
  sessions: ActivitySession[];
  session: ActivitySession | null;
  events: ActivityEvent[]; // newest first
  outside_sessions: number;
  journal: { paths: string[]; lines: string[]; written: string | null } | null;
  recaps: AgeRecap[]; // newest first
  problems: string[];
}

export interface RecapReply {
  recap: AgeRecap | null;
  age: { age: number; stage: string; start_ts: number } | null;
}

// --- The Power view (WO-4.6, plan 7.2, 7.14 "Power") -------------------------------------------
// Every number is the power designer's (core/gregtec/planner/powerview.py), the EU ledger's or
// the distribution checks'; the options come in rank order (planner/rank.py), never re-sorted.

export type PowerMode = "best" | "max_output" | "output_per_effort" | "self_sustaining";

export interface LedgerWarning {
  kind: string; // deficit, tier_mismatch, amp_overload
  tier: string | null;
  detail: string;
  plans: string[]; // plan:<id>
}

export interface LedgerEntry {
  plan: string;
  kind: string; // production or power
  name: string;
  status: string;
  ref: string; // plan:<id>
  draws: { tier: string; eut_avg: number; eut_peak: number; amps: number }[];
  source: { tier: string; eut: number; gross_eut: number; amps: number; label: string } | null;
}

export interface BufferOption {
  kind: string; // battery_buffer or lsc
  count: number;
  unit: string;
  unit_ref: string;
  unit_eu: number;
  capacity_eu: number;
  detail: string;
  refs: string[];
  eut_max: number | null;
}

export interface BufferSizing {
  tier: string;
  peak_eut: number;
  gen_eut: number;
  window_s: number;
  deficit_eut: number;
  energy_eu: number;
  battery_buffer: BufferOption | null;
  lsc: BufferOption | null;
  recommend: string; // battery_buffer, lsc or none
  reason: string;
  notes: string[];
}

export interface TransformerStep {
  high: string;
  low: string;
  mode: string; // down or up
  unit: { name: string; ref: string; amps_out: number };
  count: number;
  amps_in: number;
  amps_out: number;
}

export interface TransformerPlan {
  source: string;
  line: string;
  amps: number;
  source_amps: number;
  steps: TransformerStep[];
  notes: string[];
}

export interface PowerDistribution {
  transformers: TransformerPlan[];
  buffers: BufferSizing[];
  notes: string[];
}

export interface PowerLedgerSection {
  card: PowerLedgerCard;
  running: PowerLedgerCard;
  warnings: LedgerWarning[];
  running_warnings: LedgerWarning[];
  lines: number;
  entries: LedgerEntry[];
  window_s: number;
  distribution: PowerDistribution;
}

export interface PowerNode {
  n: string;
  kind: string; // recipe, generator, farm, member
  name: string;
  recipe: string | null;
  machine: string | null;
  mte: string | null;
  tier: string | null;
  count: number | null;
}

export interface PowerKit {
  seeds_saplings: { id: string; qty: number }[];
  kept_inputs: { id: string; qty: number }[];
  tools: { tool: string; qty: number }[];
  parts: { part: string; qty: number }[];
  starter_fuel: { id: string; qty: number }[];
}

export interface PowerOption {
  id: string;
  name: string;
  status: PlanStatus;
  position: number;
  of: number;
  mode: PowerMode;
  score: number;
  eut_per_effort_h: number | null;
  terms: Record<string, number>;
  values: Record<string, number>;
  rank_notes: string[];
  /** How this option differs from option 1 (empty for option 1). */
  differs: string[];
  weights: string;
  net_eut: number;
  gross_eut: number;
  chain_eut: number;
  units: number | null;
  generator: { label: string; kind: string; tier: string; eut: number; cites: string[] };
  fuel: { id: string; name: string; rate_s: number; ref: string | null };
  distribution: { tier: string | null; voltage: number | null; amps: number | null };
  upkeep_h: number | null;
  machines: number | null;
  effort: { eff_h: number; build_h: number; unlock_h: number; uncounted: number; card: TableCard } | null;
  closed: {
    ok: boolean;
    imports: { id: string; name: string; rate_s: number }[];
    surplus: { id: string; name: string; rate_s: number }[];
    problems: string[];
  };
  bootstrap: PowerKit | null;
  warnings: string[];
  nodes: PowerNode[];
  cables: { ref: string; name: string; voltage: number; amps: number; loss: number }[];
  ledger: {
    counted: boolean;
    card: PowerLedgerCard;
    warnings: LedgerWarning[];
    cleared: LedgerWarning[];
    added: LedgerWarning[];
    transformers: TransformerPlan[];
  };
}

export interface PowerView {
  id: string;
  ids: string[];
  mode: PowerMode;
  mode_label: string;
  mode_how: string;
  tier_cap: string | null;
  weights: string;
  w: Record<string, number>;
  notes: string[];
  rejected: { candidate: string; why: string }[];
  options: PowerOption[];
  ledger: PowerLedgerSection;
}

export interface PowerSpec {
  mode: PowerMode;
  tier?: string;
  scale?: "unit" | "generators" | "eut_cap" | "raw_budget";
  bound?: number;
  options?: number;
}

export interface PowerLatest {
  ids: string[];
  ledger: PowerLedgerSection | null;
}
