// Ctrl+K (workbench mode): one box over views, settings, items, recipes and quests. Needs
// Clayton's board approval (no board yet, D92/D158).
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { Card } from "../cards/types.gen";
import { type Action, type Entry, PLANS_LATER, arrange, itemEntries, move, questEntries, staticEntries } from "./palette";

const DEBOUNCE_MS = 200;

export function CommandPalette(props: { onRun: (action: Action) => void; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Card | null>(null);
  const [quests, setQuests] = useState<Card | null>(null);
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setItems(null);
      setQuests(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      api.tool("item_resolve", { text: q }).then((r) => live && setItems(r.card), () => live && setItems(null));
      api.tool("quest_find", { text: q }).then((r) => live && setQuests(r.card), () => live && setQuests(null));
    }, DEBOUNCE_MS);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [query]);

  const entries = useMemo(() => {
    const found = [...staticEntries(query), ...itemEntries(items), ...questEntries(quests)];
    if (query.trim().length >= 2 || /plan/i.test(query)) found.push(PLANS_LATER);
    return arrange(found);
  }, [query, items, quests]);
  const runnable = entries.filter((e) => e.action.kind !== "none");
  const selected = runnable[Math.min(index, runnable.length - 1)] as Entry | undefined;

  const run = (e: Entry | undefined): void => {
    if (!e || e.action.kind === "none") return;
    props.onRun(e.action);
    props.onClose();
  };

  return (
    <div className="palette-backdrop" onMouseDown={props.onClose} data-testid="palette">
      <div className="palette gos-panel" role="dialog" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}>
        <input
          ref={input}
          className="palette-input"
          value={query}
          placeholder="Items, recipes, quests, plans, settings"
          aria-label="Search"
          onChange={(e) => {
            setQuery(e.target.value);
            setIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => move(Math.min(i, runnable.length - 1), e.key === "ArrowDown" ? 1 : -1, runnable.length));
            } else if (e.key === "Enter") {
              e.preventDefault();
              run(selected);
            } else if (e.key === "Escape") {
              e.preventDefault();
              props.onClose();
            }
          }}
          data-testid="palette-input"
        />
        <ul className="palette-list" role="listbox">
          {entries.map((e, i) => {
            const head = i === 0 || entries[i - 1].group !== e.group;
            return (
              <li key={e.id} role="presentation">
                {head && <div className="gos-label palette-group">{e.group}</div>}
                <button
                  type="button"
                  role="option"
                  aria-selected={e === selected}
                  className={`palette-entry${e === selected ? " on" : ""}${e.action.kind === "none" ? " later" : ""}`}
                  disabled={e.action.kind === "none"}
                  onClick={() => run(e)}
                  data-entry={e.id}
                >
                  <span>{e.label}</span>
                  {e.hint && <span className="gos-src">{e.hint}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
