// The left nav (plan 7.14): every view, its number key, and the work order that builds the ones
// that aren't here yet. Keys stay with their view as later views arrive (D159).
export type ViewId =
  | "now"
  | "chat"
  | "search"
  | "designer"
  | "power"
  | "quests"
  | "crops"
  | "costs"
  | "settings"
  | "base"
  | "activity"
  | "ores"
  | "build";

export interface View {
  id: ViewId;
  label: string;
  key: string | null; // the number key, workbench only
  later: string | null; // the work order that builds it; null: built
  board: boolean; // has an approved board (D92); false: needs Clayton's board approval
}

export const VIEWS: readonly View[] = [
  { id: "now", label: "Now", key: "1", later: null, board: true },
  { id: "chat", label: "Chat", key: "2", later: null, board: false },
  { id: "search", label: "Search", key: "3", later: null, board: false },
  { id: "designer", label: "Designer", key: "4", later: null, board: true }, // built to the system (D158 (5))
  { id: "power", label: "Power", key: "5", later: "WO-4.6", board: true },
  { id: "quests", label: "Quests", key: "6", later: null, board: true }, // WO-6.4: built without the handoff (D158)
  { id: "crops", label: "Crops", key: "7", later: null, board: true }, // WO-6.7: built without the handoff (D158)
  { id: "costs", label: "Costs", key: "8", later: null, board: false },
  { id: "settings", label: "Settings", key: "9", later: null, board: true },
  { id: "base", label: "Base", key: null, later: null, board: false }, // My base (WO-3.8); stalled machines WO-7.4
  { id: "activity", label: "Activity", key: null, later: "WO-6.3", board: false },
  { id: "ores", label: "Ores", key: null, later: null, board: false }, // WO-6.6: needs Clayton's board approval
  { id: "build", label: "Build", key: null, later: "Phase 10", board: false },
];

export const PHONE_TABS: readonly ViewId[] = ["now", "chat", "search", "base", "costs", "settings"];

export function viewOf(id: string): View {
  return VIEWS.find((v) => v.id === id) ?? VIEWS[0];
}

export function viewForKey(key: string): View | null {
  return VIEWS.find((v) => v.key === key) ?? null;
}

/** The view a location hash names (#/chat), else Now. */
export function viewFromHash(hash: string): ViewId {
  const id = hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return VIEWS.some((v) => v.id === id) ? (id as ViewId) : "now";
}
