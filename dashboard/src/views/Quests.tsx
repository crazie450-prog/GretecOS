// Quests (plan 7.4, 7.14: "What can I do next?"; WO-6.4). The frontier, ranked by his goals and
// active lines (the core ranks it, D269), as a catalog quest card; the items each frontier quest
// asks for or gives, linked to Search; the quests the frontier unlocks next, blurred until he taps
// them or says "spoil it" (D26, D72); and each quest line's progress.
// Past the frontier the core sends only what his spoiler level shows: the blur is the look, never
// the guard. Approved board (D92) but built without the canvas handoff (D158 (5)): designed to the
// system, to be reconciled with the board and approved by Clayton.
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Answer, QuestLink, QuestLine, QuestsView } from "../api/types";
import { Card } from "../cards/Card";
import { Button, Panel, TierChip, stageTier } from "../components/core";
import { GLYPH } from "../state/states";

const LEVEL_WORD = ["nudge", "partial", "full"] as const;

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/** The stage at the start of the core's tier line ("stone (GT voltage tier 0, quests)"). */
export function stageOf(tier: string): string {
  return tier.split(" ")[0] ?? tier;
}

/** The grant of the newest answer when it is a spoiler command asked after ``since`` (seconds):
 * its level and the quests it is for, as the core granted them (D268 R3), else null. A partial
 * answer stays partial, and only its quests open. */
export function spokeSpoil(answers: Answer[], since: number): { level: 1 | 2; quests: string[] } | null {
  const last = answers.length ? answers[answers.length - 1] : null;
  const grant = last?.spoil;
  if (!last || last.asked_ts < since || !grant) return null;
  if ((grant.level !== 1 && grant.level !== 2) || grant.quests.length === 0) return null;
  return grant;
}

function ItemLinks(props: { quest: string; name: string; links: QuestLink[]; openItem: (key: string, name: string) => void }) {
  if (props.links.length === 0) return null;
  return (
    <li className="quest-links" data-links={props.quest}>
      <span className="quest-links-name">{props.name}</span>
      <span className="quest-links-items">
        {props.links.map((l, i) => (
          <button
            key={`${l.part}-${l.key}-${i}`}
            type="button"
            className="gos-chip"
            onClick={() => props.openItem(l.key, l.name)}
            title={`${l.part === "task" ? "asks for" : "rewards"} ${l.key}: open in Search`}
            data-item={l.key}
          >
            {l.part === "task" ? "needs" : "gives"} {l.name}
            {l.amount !== null && <span className="num"> ×{l.amount}</span>}
            {l.part === "task" && l.consume === true && <span className="gos-src"> consumed</span>}
          </button>
        ))}
      </span>
    </li>
  );
}

function LineRow({ line }: { line: QuestLine }) {
  const pct = line.total ? Math.round((line.done / line.total) * 100) : 0;
  return (
    <tr data-line={line.name}>
      <td>
        <span className="base-name">{line.name}</span>
        <div className="quest-bar" role="img" aria-label={`${line.done} of ${line.total} done`}>
          <span style={{ width: `${pct}%` }} />
        </div>
      </td>
      <td className="num">
        {line.done}/{line.total}
      </td>
      <td className="num">{line.available}</td>
      <td className="num">{line.locked}</td>
    </tr>
  );
}

export function Quests(props: {
  answers: Answer[];
  openItem: (key: string, name: string) => void;
}) {
  const [data, setData] = useState<QuestsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  // null: his global setting. `open`: the quests a spoiler command in chat granted (null: all).
  const [want, setWant] = useState<{ level: number | null; open: string[] | null }>({ level: null, open: null });
  const [revealed, setRevealed] = useState(false);
  const opened = useRef(Date.now() / 1000);
  const setLevel = (level: number | null) => setWant({ level, open: null });

  useEffect(() => {
    let live = true;
    api.quests(want.level, want.open).then(
      (v) => {
        if (!live) return;
        setData(v);
        setError(null);
        if (v.spoil === 2 || (v.open && v.open.length > 0)) setRevealed(true);
      },
      (e: unknown) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [want]);

  // A spoiler command said or typed while the view is open: only the quests it granted, at the
  // level it granted (D268 R3).
  const grant = spokeSpoil(props.answers, opened.current);
  const grantKey = grant ? `${grant.level}:${grant.quests.join(",")}` : null;
  useEffect(() => {
    if (grant) setWant({ level: grant.level, open: grant.quests });
  }, [grantKey]); // the grant itself is a new object on every render

  const frontier = (data?.frontier ?? null) as { quests?: { ref: string; name: string | null }[] } | null;
  const beyond = (data?.beyond ?? null) as { quests?: unknown[] } | null;
  const nBeyond = beyond?.quests?.length ?? 0;

  return (
    <div className="view-quests" data-testid="view-quests">
      <div className="view-head">
        <h1 className="page-title">Quests</h1>
        <p className="lede">What can I do next? Ranked by your goals and active lines; what lies past your frontier stays a nudge until you ask.</p>
      </div>
      {error && (
        <p className="empty" data-testid="quests-empty">
          {GLYPH.waiting} {error}
        </p>
      )}
      {!data && !error && <p className="empty">{GLYPH.progress} Loading your quest book</p>}
      {data && (
        <div className="quests-grid">
          <div className="col">
            <Panel
              label="Next quests"
              testId="quests-frontier"
              aside={
                <span className="quests-tier">
                  <TierChip tier={stageTier(stageOf(data.tier))} />
                </span>
              }
            >
              <Card card={data.frontier} />
            </Panel>
            <Panel label="Items for these quests" testId="quests-links">
              {Object.keys(data.links).length === 0 ? (
                <p className="empty">{GLYPH.todo} These quests ask for no items.</p>
              ) : (
                <ul className="quest-link-list">
                  {(frontier?.quests ?? []).map((q) => (
                    <ItemLinks key={q.ref} quest={q.ref} name={q.name ?? q.ref} links={data.links[q.ref] ?? []} openItem={props.openItem} />
                  ))}
                </ul>
              )}
            </Panel>
          </div>
          <div className="col">
            <Panel
              label="Beyond the frontier"
              testId="quests-beyond"
              aside={
                <span className="gos-src" data-testid="quests-level">
                  spoilers: {LEVEL_WORD[data.spoil]}
                  {data.spoil !== data.default && ` (yours: ${LEVEL_WORD[data.default]})`}
                </span>
              }
            >
              {nBeyond === 0 ? (
                <p className="empty">{GLYPH.todo} Nothing past the frontier is in sight yet.</p>
              ) : (
                <>
                  <div
                    className={`spoiler-zone${revealed ? " revealed" : ""}`}
                    data-testid="spoiler-zone"
                    aria-hidden={!revealed}
                    onClickCapture={(e) => {
                      if (revealed) return;
                      e.stopPropagation(); // the first tap only lifts the blur
                      e.preventDefault();
                      setRevealed(true);
                    }}
                  >
                    <Card card={data.beyond} />
                  </div>
                  {!revealed && (
                    <Button onClick={() => setRevealed(true)} testId="quests-reveal">
                      Tap to see where the frontier leads ({nBeyond})
                    </Button>
                  )}
                </>
              )}
              <div className="quests-spoil">
                {(data.spoil < 2 || data.open !== null) && (
                  <Button variant="primary" onClick={() => setLevel(2)} testId="quests-spoil">
                    Spoil it
                  </Button>
                )}
                {data.spoil < 1 && (
                  <Button onClick={() => setLevel(1)} testId="quests-partial">
                    What do they ask for?
                  </Button>
                )}
                {(data.spoil !== data.default || data.open !== null) && (
                  <Button
                    onClick={() => {
                      setLevel(null);
                      setRevealed(false);
                    }}
                    testId="quests-nudge"
                  >
                    Back to {LEVEL_WORD[data.default]}
                  </Button>
                )}
              </div>
              <p className="gos-sub">Or say "spoil it". Quest names and text are the quest book's, shown as written.</p>
            </Panel>
            <Panel label="Quest lines" testId="quests-lines">
              <div className="table-wrap">
                <table className="base-table">
                  <thead>
                    <tr>
                      <th>line</th>
                      <th className="num">done</th>
                      <th className="num">open</th>
                      <th className="num">locked</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.lines.map((l) => (
                      <LineRow key={l.name} line={l} />
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <Panel label="Your goals" testId="quests-goals">
              {data.goals.length === 0 ? (
                <p className="empty">{GLYPH.todo} No goals yet. Goals in me/goals.md (goal: …) rank the frontier first.</p>
              ) : (
                <ol className="quest-goals">
                  {data.goals.map((g) => (
                    <li key={g}>{g}</li>
                  ))}
                </ol>
              )}
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
