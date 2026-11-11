// Search (WO-2.6): items and quests by name, then an item's recipes, uses and quests, straight
// from SQLite through the lookup tools. Works without the AI (plan 7.16).
import { type FormEvent, useEffect, useState } from "react";
import { Card } from "../cards/Card";
import { itemEntries, questEntries } from "../components/palette";
import { Button } from "../components/core";
import { GLYPH } from "../state/states";
import { useTool } from "./useTool";

export type SearchTarget =
  | { kind: "item"; key: string; name: string; tool: "recipe_find" | "recipe_uses" | "quest_for_item" }
  | { kind: "quest"; ref: string; spoil: number };

const TABS = [
  { tool: "recipe_find", label: "Recipes" },
  { tool: "recipe_uses", label: "Uses" },
  { tool: "quest_for_item", label: "Quests" },
] as const;

export function Search(props: { target: SearchTarget | null; setTarget: (t: SearchTarget | null) => void }) {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const items = useTool(query ? "item_resolve" : null, { text: query });
  const quests = useTool(query ? "quest_find" : null, { text: query });
  const t = props.target;
  const detail = useTool(
    t === null ? null : t.kind === "item" ? t.tool : "quest_get",
    t === null ? {} : t.kind === "item" ? { item: t.key } : { id: t.ref.replace(/^q:/, ""), spoil: t.spoil },
  );
  useEffect(() => {
    if (t?.kind === "item") setText((v) => v || t.name);
  }, [t]);

  const submit = (e: FormEvent): void => {
    e.preventDefault();
    setQuery(text.trim());
    props.setTarget(null);
  };
  const itemList = itemEntries(items.card).filter((e) => e.group === "Items");
  const questList = questEntries(quests.card);

  return (
    <div className="view-search" data-testid="view-search">
      <div className="view-head">
        <h1 className="page-title">Search</h1>
        <p className="lede">Items, recipes and quests from your own export. Works with the AI offline.</p>
      </div>
      <form className="search-form" onSubmit={submit}>
        <input
          className="ask-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="steel, EBF, oxygen, a quest name"
          aria-label="Search for"
          data-testid="search-input"
        />
        <Button type="submit" variant="primary" disabled={!text.trim()} testId="search-go">
          Search
        </Button>
      </form>
      <div className="search-grid">
        <section className="gos-panel results" data-testid="search-results">
          <span className="gos-label">Items</span>
          {!query && <p className="empty">{GLYPH.todo} Type a name and press Search.</p>}
          {query && items.loading && <p className="empty">{GLYPH.progress} Looking up</p>}
          {query && !items.loading && itemList.length === 0 && <p className="empty">{GLYPH.todo} No item matches.</p>}
          <ul className="result-list">
            {itemList.map((e) =>
              e.action.kind === "item" ? (
                <li key={e.id}>
                  <button
                    type="button"
                    className={`result${t?.kind === "item" && t.key === e.action.key ? " on" : ""}`}
                    onClick={() => e.action.kind === "item" && props.setTarget({ ...e.action, kind: "item", tool: "recipe_find" })}
                    data-result={e.action.key}
                  >
                    <span>{e.label}</span>
                    <span className="gos-src">{e.hint}</span>
                  </button>
                </li>
              ) : null,
            )}
          </ul>
          <span className="gos-label">Quests</span>
          {query && !quests.loading && questList.length === 0 && <p className="empty">{GLYPH.todo} No quest matches.</p>}
          <ul className="result-list">
            {questList.map((e) =>
              e.action.kind === "quest" ? (
                <li key={e.id}>
                  <button
                    type="button"
                    className="result"
                    onClick={() => e.action.kind === "quest" && props.setTarget({ kind: "quest", ref: e.action.ref, spoil: 0 })}
                  >
                    <span>{e.label}</span>
                    <span className="gos-src">{e.hint}</span>
                  </button>
                </li>
              ) : null,
            )}
          </ul>
        </section>
        <section className="gos-panel detail" data-testid="search-detail">
          {t === null && <p className="empty">{GLYPH.todo} Pick an item or a quest to see its card.</p>}
          {t?.kind === "item" && (
            <div className="tabs" role="tablist">
              {TABS.map((tab) => (
                <button
                  key={tab.tool}
                  type="button"
                  role="tab"
                  aria-selected={t.tool === tab.tool}
                  className={`gos-btn${t.tool === tab.tool ? " primary" : ""}`}
                  onClick={() => props.setTarget({ ...t, tool: tab.tool })}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}
          {t !== null && detail.loading && <p className="empty">{GLYPH.progress} Loading</p>}
          {t !== null && detail.error && <p className="empty">{GLYPH.warn} {detail.error}</p>}
          {t !== null && detail.card && <Card card={detail.card} />}
        </section>
      </div>
    </div>
  );
}
