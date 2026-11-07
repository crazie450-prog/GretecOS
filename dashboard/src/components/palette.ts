// The Ctrl+K command palette's entries (plan 7.14): views, settings, and items, recipes and
// quests found through the core's lookup tools (WO-2.2); plans arrive with the Designer.
import type { Card } from "../cards/types.gen";
import { VIEWS, type ViewId } from "../views/nav";

export type Group = "Views" | "Settings" | "Items" | "Recipes" | "Quests" | "Plans";

export type Action =
  | { kind: "view"; view: ViewId }
  | { kind: "setting"; id: "theme" | "doctor" | "devices" | "motion" }
  | { kind: "item"; key: string; name: string; tool: "recipe_find" | "recipe_uses" }
  | { kind: "quest"; ref: string }
  | { kind: "none" };

export interface Entry {
  id: string;
  group: Group;
  label: string;
  hint: string;
  action: Action;
}

export const GROUPS: readonly Group[] = ["Views", "Items", "Recipes", "Quests", "Plans", "Settings"];

const SETTINGS: Entry[] = [
  { id: "s-theme", group: "Settings", label: "Theme and comfort modes", hint: "Settings", action: { kind: "setting", id: "theme" } },
  { id: "s-motion", group: "Settings", label: "Pause motion", hint: "toggle", action: { kind: "setting", id: "motion" } },
  { id: "s-doctor", group: "Settings", label: "Re-run doctor", hint: "Settings + Health", action: { kind: "setting", id: "doctor" } },
  { id: "s-devices", group: "Settings", label: "Paired devices", hint: "Settings", action: { kind: "setting", id: "devices" } },
];

function matches(label: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || q.split(/\s+/).every((w) => label.toLowerCase().includes(w));
}

/** The entries that don't need the core: views and settings, filtered by the query. */
export function staticEntries(query: string): Entry[] {
  const views: Entry[] = VIEWS.map((v) => ({
    id: `v-${v.id}`,
    group: "Views",
    label: v.label,
    hint: v.later ? `arrives with ${v.later}` : v.key ? `key ${v.key}` : "",
    action: { kind: "view", view: v.id },
  }));
  return [...views, ...SETTINGS].filter((e) => matches(e.label, query));
}

/** Items from an item_resolve card, and the recipe lookups of the best one. */
export function itemEntries(card: Card | null): Entry[] {
  if (!card || card.type !== "table" || card.error) return [];
  const col = (name: string) => card.columns.indexOf(name);
  const [k, n, kind] = [col("key"), col("name"), col("kind")];
  if (k < 0 || n < 0) return [];
  const items = card.rows.slice(0, 6).map((r) => ({ key: r[k], name: r[n] || r[k], kind: kind >= 0 ? r[kind] : "" }));
  const out: Entry[] = items.map((it) => ({
    id: `i-${it.key}`,
    group: "Items",
    label: it.name,
    hint: it.key,
    action: { kind: "item", key: it.key, name: it.name, tool: "recipe_find" },
  }));
  const best = items[0];
  if (best) {
    out.push(
      { id: `r-find-${best.key}`, group: "Recipes", label: `Recipes for ${best.name}`, hint: "recipe_find", action: { kind: "item", key: best.key, name: best.name, tool: "recipe_find" } },
      { id: `r-uses-${best.key}`, group: "Recipes", label: `What uses ${best.name}`, hint: "recipe_uses", action: { kind: "item", key: best.key, name: best.name, tool: "recipe_uses" } },
    );
  }
  return out;
}

/** Quests from a quest_find card; one the book doesn't show yet stays unnamed (D26). */
export function questEntries(card: Card | null): Entry[] {
  if (!card || card.type !== "quest") return [];
  return card.quests.slice(0, 6).map((q) => ({
    id: `q-${q.ref}`,
    group: "Quests",
    label: q.name ?? "A quest past your frontier",
    hint: `${q.state} · ${q.ref}`,
    action: { kind: "quest", ref: q.ref },
  }));
}

export const PLANS_LATER: Entry = {
  id: "p-later",
  group: "Plans",
  label: "Plans arrive with the Designer (WO-3.7)",
  hint: "",
  action: { kind: "none" },
};

/** All entries in group order. */
export function arrange(entries: Entry[]): Entry[] {
  return GROUPS.flatMap((g) => entries.filter((e) => e.group === g));
}

/** Arrow-key movement through the runnable entries. */
export function move(index: number, delta: number, count: number): number {
  if (count === 0) return 0;
  return (index + delta + count) % count;
}
