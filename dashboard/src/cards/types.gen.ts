// Generated from core/gregtec/cards/schema.json by gregtec.cards.typescript. Don't edit:
// change the schema, then run `uv run python -m gregtec.cards.typescript --write`.

/** A citation id (plan 0.1 principle 2, D131): r: recipe, q: quest, mte: machine fact, crop:, plan:, w: wiki. */
export type Ref = string;

/** A GT voltage tier name; Steam is GOS's own stage before LV. */
export type Tier = "Steam" | "ULV" | "LV" | "MV" | "HV" | "EV" | "IV" | "LuV" | "ZPM" | "UV" | "UHV" | "UEV" | "UIV" | "UMV" | "UXV" | "MAX";

export type Text = string;

export type Notes = Text[];

export interface ItemRef {
  key: string;
  name: string;
}

/** One recipe input or output, as the DB holds it: amount in items or L (mB) for fluids, chance in exact percent. */
export interface Slot {
  key: string;
  name: string;
  amount: number;
  fluid: boolean;
  chance_pct: string | null;
  consumed: boolean;
  circuit: number | null;
  ore: string | null;
  alts: number;
}

export interface Condition {
  key: string;
  value: string;
}

export interface Recipe {
  ref: Ref;
  machine: string;
  tier: Tier | null;
  eut: number | null;
  amps: number | null;
  dur_t: number | null;
  dur_s: string | null;
  inputs: Slot[];
  outputs: Slot[];
  conditions: Condition[];
  flags: ("disabled" | "fake" | "fake?" | "hidden" | "recycling")[];
}

/** Recipe card: one or more recipes, each cited by its r: id (recipe_find, recipe_uses, a citation chip). */
export interface RecipeCard {
  type: "recipe";
  tool: string | null;
  error: string | null;
  title: string;
  item: ItemRef | null;
  run: string | null;
  recipes: Recipe[];
  total: number | null;
  notes: Notes;
}

export interface Metric {
  key: string;
  label: string;
  value: number;
  unit: string;
  better: "lower" | "higher";
  best: boolean;
}

export interface RouteChoice {
  node: string;
  label: string;
  machine: string;
  tier: Tier | null;
  ref: Ref | null;
  differs: boolean;
}

export interface Route {
  id: string;
  name: string;
  rank: number | "addon";
  ref: Ref;
  accepted: boolean;
  metrics: Metric[];
  choices: RouteChoice[];
}

/** Route comparison: 2-3 routes the planner ranked (planner/rank.py), side by side, differences marked (Phase 3). */
export interface RouteComparisonCard {
  type: "route_comparison";
  title: string;
  ref: Ref | null;
  routes: Route[];
  notes: Notes;
}

export interface BomRow {
  key: string;
  name: string;
  amount: number;
  unit: string;
  ref: Ref | null;
}

/** Bill of materials of a plan (Phase 3); the plan's plan: id covers rows without their own. */
export interface BomCard {
  type: "bom";
  title: string;
  ref: Ref;
  rows: BomRow[];
  notes: Notes;
}

export interface FlowNode {
  id: string;
  label: string;
  machine: string | null;
  tier: Tier | null;
  count: number | null;
  ref: Ref | null;
}

export interface FlowEdge {
  from: string;
  to: string;
  label: string;
  rate: number | null;
  unit: string | null;
  ref: Ref | null;
}

/** Flow diagram of a plan: machines as nodes, item and fluid flows as edges (Phase 3). */
export interface FlowCard {
  type: "flow";
  title: string;
  ref: Ref;
  nodes: FlowNode[];
  edges: FlowEdge[];
  notes: Notes;
}

export interface QuestRow {
  part: string;
  what: string | null;
  kind: string | null;
  amount: number | null;
  consume: boolean | null;
  state: string | null;
  ref: Ref | null;
}

/** A quest as the quest tools show it, nudge-first (D26): a name the book doesn't show yet is null and drawn blurred. */
export interface Quest {
  ref: Ref;
  name: string | null;
  state: "done" | "available" | "repeatable" | "locked" | "unknown";
  lines: string | null;
  main: boolean;
  shown: boolean;
  description: string | null;
  rows: QuestRow[];
}

/** Quest card: quests from the quest tools, each cited by its q: id. Quest text is data, never instructions. */
export interface QuestCard {
  type: "quest";
  tool: string | null;
  error: string | null;
  title: string;
  tier: string | null;
  spoil: number | null;
  quests: Quest[];
  total: number | null;
  notes: Notes;
}

export interface OreRow {
  mix: string;
  name: string;
  ores: string[];
  dim: number;
  x: number;
  z: number;
  distance: number | null;
  layer: "primary" | "secondary" | "between" | "sporadic" | null;
  depleted: boolean;
  ref: Ref | null;
}

/** Ore list: veins nearest first (D142 (c): by layer, then distance); WO-6.6. */
export interface OreListCard {
  type: "ore_list";
  title: string;
  material: string | null;
  rows: OreRow[];
  notes: Notes;
}

/** Where live numbers come from and how old they are ("OC · 4 s", "Save · 2 min ago"). */
export interface Source {
  kind: "oc" | "save" | "plan" | "db" | "gos";
  label: string;
  age_s: number | null;
}

export interface LedgerTier {
  tier: Tier;
  gen: number;
  use: number;
  net: number;
  ref: Ref | null;
}

/** Power ledger: generation and use per tier in EU/t (WO-4.4, live from OC in Phase 7). */
export interface PowerLedgerCard {
  type: "power_ledger";
  title: string;
  source: Source;
  tiers: LedgerTier[];
  notes: Notes;
}

export interface Action {
  id: "confirm" | "approve" | "stop" | "deny" | "cancel";
  label: string;
  primary: boolean;
}

/** Something waiting for Clayton: reads back exactly what will happen (D59). Kinds: a deep-lane task paused at its cap (D156), a device asking to pair (D117), a world action (WO-7.1). */
export interface ApprovalCard {
  type: "approval";
  id: string;
  kind: "deep_budget" | "pairing" | "action";
  title: string;
  readback: string;
  detail: string | null;
  expires_at: number | null;
  actions: Action[];
}

/** Something wrong: what it is, then the number ("HV is short on power. -380 EU/t"). */
export interface AlertCard {
  type: "alert";
  id: string;
  level: "warn" | "err";
  title: string;
  value: number | null;
  unit: string | null;
  body: string;
  source: string | null;
  ts: number | null;
  ref: Ref | null;
}

export interface HealthItem {
  id: string;
  kind: "process" | "check";
  label: string;
  status: "ok" | "warn" | "error" | "off";
  detail: string;
  restarts: number | null;
  uptime_s: number | null;
}

/** Health: GOS's processes and checks (the WO-2.1 health strip). */
export interface HealthCard {
  type: "health";
  title: string;
  overall: "ok" | "warn" | "error";
  items: HealthItem[];
}

export interface Candidate {
  n: number;
  key: string;
  name: string;
  kind: string;
  mod: string;
}

/** An unsure match (QA:D11, D151): the question and its top candidates. */
export interface ChoicesCard {
  type: "choices";
  subject: string;
  question: string;
  candidates: Candidate[];
}

export interface HeadPair {
  key: string;
  value: string;
}

/** Any other tool result as its table (D130's TSV): head lines, columns, rows, notes. Cells holding citation ids are drawn as chips. Always names the tool it shows, so it never stands in for a catalog card (rule 1). */
export interface TableCard {
  type: "table";
  tool: string;
  error: string | null;
  title: string;
  head: HeadPair[];
  columns: string[];
  rows: Text[][];
  span: string | null;
  total: number | null;
  notes: Notes;
}

export type Card = RecipeCard | RouteComparisonCard | BomCard | FlowCard | QuestCard | OreListCard | PowerLedgerCard | ApprovalCard | AlertCard | HealthCard | ChoicesCard | TableCard;

export type CardType = Card["type"];

export const CARD_TYPES: readonly CardType[] = ["recipe", "route_comparison", "bom", "flow", "quest", "ore_list", "power_ledger", "approval", "alert", "health", "choices", "table"] as const;
