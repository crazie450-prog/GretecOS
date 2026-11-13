// One renderer per catalog card (plan 7.14 rule 1). Every number sits next to the citation of the
// entry it belongs to (rule 2): a recipe's numbers by its r: chip, a route's by its plan: chip.
import { Fragment } from "react";
import { AlertView, ApprovalView, CitationChip, PowerRow, StatusGlyph, TierChip } from "../components/core";
import { plain, signed } from "../state/format";
import { GLYPH } from "../state/states";
import { useCardActions } from "./context";
import { FlowDiagram, edgeLabel } from "./FlowDiagram";
import type {
  BomCard,
  ChoicesCard,
  FlowCard,
  HealthCard,
  OreListCard,
  PowerLedgerCard,
  QuestCard,
  Recipe,
  RecipeCard,
  RouteComparisonCard,
  Slot,
  TableCard,
} from "./types.gen";

const CITE = /^(r|q|mte|crop|plan|w|vein|pos):\S+$/;

function Chip({ cite }: { cite: string | null }) {
  const { stale, openCite } = useCardActions();
  if (!cite) return null;
  const gameData = cite.startsWith("r:") || cite.startsWith("mte:");
  return <CitationChip cite={cite} stale={stale && gameData} onOpen={cite.startsWith("r:") ? openCite : undefined} />;
}

function CardHead(props: { label: string; title: string; cite?: string | null; extra?: string | null }) {
  return (
    <header className="card-head">
      <span className="gos-label">{props.label}</span>
      <h3 className="gos-title card-title">{props.title}</h3>
      {(props.extra || props.cite) && (
        <div className="card-meta">
          {props.extra && <span className="gos-src">{props.extra}</span>}
          {props.cite && <Chip cite={props.cite} />}
        </div>
      )}
    </header>
  );
}

function slotText(s: Slot): string {
  const amount = s.amount === 0 ? "" : `${s.amount}${s.fluid ? " L" : "x"} `;
  const parts = [`${amount}${s.name || s.key}`];
  if (s.circuit !== null) parts.push(`circuit ${s.circuit}`);
  if (!s.consumed) parts.push("not consumed");
  if (s.ore) parts.push(`any ore:${s.ore}`);
  else if (s.alts > 0) parts.push(`or ${s.alts} other${s.alts > 1 ? "s" : ""}`);
  if (s.chance_pct !== null) parts.push(`${s.chance_pct}% chance`);
  return parts.join(" · ");
}

function RecipeRow({ r }: { r: Recipe }) {
  return (
    <li className="recipe" data-ref={r.ref}>
      <div className="recipe-head">
        <span className="recipe-machine">{r.machine}</span>
        {r.tier ? <TierChip tier={r.tier} /> : <span className="gos-chip">no tier</span>}
        {r.eut !== null && (
          <span className="num">
            {plain(r.eut)} <span className="unit">EU/t</span>
          </span>
        )}
        {r.amps !== null && r.amps !== 1 && <span className="num">{r.amps} A</span>}
        {r.dur_t !== null && (
          <span className="num">
            {r.dur_t} <span className="unit">ticks</span>
            {r.dur_s && <span className="gos-src"> ({r.dur_s} s)</span>}
          </span>
        )}
        {r.flags.map((f) => (
          <span key={f} className="gos-chip flag">
            {f}
          </span>
        ))}
        <Chip cite={r.ref} />
      </div>
      <div className="recipe-io">
        <ul className="slots in" aria-label="inputs">
          {r.inputs.map((s, i) => (
            <li key={`${s.key}-${i}`}>{slotText(s)}</li>
          ))}
        </ul>
        <span className="arrow" aria-hidden="true">
          {"→"}
        </span>
        <ul className="slots out" aria-label="outputs">
          {r.outputs.map((s, i) => (
            <li key={`${s.key}-${i}`}>{slotText(s)}</li>
          ))}
        </ul>
      </div>
      {r.conditions.length > 0 && (
        <div className="conditions">
          {r.conditions.map((c) => (
            <span key={c.key} className="gos-chip">
              {c.key}={c.value}
            </span>
          ))}
        </div>
      )}
    </li>
  );
}

export function RecipeCardView({ card, compact }: { card: RecipeCard; compact?: boolean }) {
  const shown = compact ? card.recipes.slice(0, 3) : card.recipes;
  const total = card.total ?? card.recipes.length;
  return (
    <div className="card recipe-card" data-card="recipe">
      <CardHead
        label={card.tool === "recipe_uses" ? "Recipe card · uses" : "Recipe card"}
        title={card.item?.name ?? card.title}
        extra={card.run ? `run ${card.run}` : null}
      />
      {card.recipes.length === 0 ? (
        <p className="empty">{GLYPH.todo} No recipe in the DB.</p>
      ) : (
        <ol className="recipes">
          {shown.map((r) => (
            <RecipeRow key={r.ref} r={r} />
          ))}
        </ol>
      )}
      {total > shown.length && (
        <p className="gos-src more">
          {shown.length} of {total} recipes shown
        </p>
      )}
      <Notes notes={compact ? [] : card.notes} />
    </div>
  );
}

function Notes({ notes }: { notes: string[] }) {
  if (!notes.length) return null;
  return (
    <ul className="notes">
      {notes.map((n) => (
        <li key={n}>{n}</li>
      ))}
    </ul>
  );
}

const QUEST_GLYPH: Record<string, string> = {
  done: GLYPH.done,
  available: GLYPH.todo,
  repeatable: GLYPH.progress,
  locked: GLYPH.paused,
  unknown: GLYPH.waiting,
};

export function QuestCardView({ card, compact }: { card: QuestCard; compact?: boolean }) {
  const { openQuest } = useCardActions();
  const shown = compact ? card.quests.slice(0, 5) : card.quests;
  return (
    <div className="card quest-card" data-card="quest">
      <CardHead label="Quest card" title={card.title} extra={card.tier} />
      {card.quests.length === 0 && <p className="empty">{GLYPH.todo} No quest matches.</p>}
      <ul className="quests">
        {shown.map((q) => (
          <li key={q.ref} className={`quest state-${q.state}`} data-quest={q.ref}>
            <div className="quest-head">
              <span className="gos-status" aria-label={q.state}>
                {QUEST_GLYPH[q.state]} {q.state}
              </span>
              {q.name !== null ? (
                <button type="button" className="quest-name linkish" onClick={() => openQuest?.(q.ref, q.spoil ?? 0)}>
                  {q.name}
                </button>
              ) : (
                <button
                  type="button"
                  className="quest-name spoiler"
                  onClick={() => openQuest?.(q.ref, 0)}
                  title="Past your frontier: tap for a nudge"
                  data-testid="spoiler"
                >
                  Not in your quest book yet · tap for a nudge
                </button>
              )}
              {q.main && <span className="gos-chip">main</span>}
              <Chip cite={q.ref} />
            </div>
            {q.lines && <div className="gos-src">{q.lines}</div>}
            {!compact && q.description && <p className="quest-text">{q.description}</p>}
            {!compact && q.rows.length > 0 && (
              <table className="rows">
                <tbody>
                  {q.rows.map((r, i) => (
                    <tr key={`${r.part}-${i}`}>
                      <td className="gos-src">{r.part}</td>
                      <td>{r.what ?? "—"}</td>
                      <td className="num">{r.amount ?? ""}</td>
                      <td className="gos-src">{r.state ?? ""}</td>
                      <td>
                        <Chip cite={r.ref} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {!compact && q.state === "locked" && q.spoil !== null && q.spoil < 2 && (
              <button type="button" className="gos-btn spoil" onClick={() => openQuest?.(q.ref, (q.spoil ?? 0) + 1)}>
                {q.spoil === 0 ? "What does it ask for?" : "Spoil it"}
              </button>
            )}
          </li>
        ))}
      </ul>
      <Notes notes={compact ? [] : card.notes} />
    </div>
  );
}

export function TableCardView({ card, compact }: { card: TableCard; compact?: boolean }) {
  if (card.error) {
    return (
      <div className="card table-card error" data-card="table">
        <CardHead label={card.tool ?? "Lookup"} title={`${GLYPH.warn} Couldn't look that up`} />
        <p className="gos-sub">{card.error}</p>
      </div>
    );
  }
  const rows = compact ? card.rows.slice(0, 8) : card.rows;
  return (
    <div className="card table-card" data-card="table">
      <CardHead label={card.tool ?? "Table"} title={card.title} extra={card.span ? `rows ${card.span}` : null} />
      {card.head.length > 0 && (
        <dl className="head">
          {card.head.map((h) => (
            <Fragment key={h.key}>
              <dt>{h.key}</dt>
              <dd>{CITE.test(h.value) ? <Chip cite={h.value} /> : h.value}</dd>
            </Fragment>
          ))}
        </dl>
      )}
      {card.columns.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {card.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{CITE.test(cell) ? <Chip cite={cell} /> : cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Notes notes={compact ? [] : card.notes} />
    </div>
  );
}

export function ChoicesCardView({ card }: { card: ChoicesCard }) {
  const { pick } = useCardActions();
  return (
    <div className="card choices-card" data-card="choices">
      <CardHead label="Which one?" title={card.question} />
      <ol className="choices">
        {card.candidates.map((c) => (
          <li key={c.key}>
            <button type="button" className="gos-btn choice" onClick={() => pick?.(c.n)} disabled={!pick}>
              <span className="num">{c.n}</span> {c.name}
            </button>
            <span className="gos-src">{c.key}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function RouteComparisonView({ card }: { card: RouteComparisonCard }) {
  return (
    <div className="card route-card" data-card="route_comparison">
      <CardHead label="Route comparison" title={card.title} cite={card.ref} />
      <div className="routes" style={{ gridTemplateColumns: `repeat(${card.routes.length}, minmax(0, 1fr))` }}>
        {card.routes.map((r) => (
          <section key={r.id} className={`route${r.accepted ? " accepted" : ""}`} data-route={r.id}>
            <div className="route-head">
              <span className="gos-label">{r.rank === "addon" ? "Addon column" : `Route ${r.rank}`}</span>
              <span className="route-name">{r.name}</span>
              {r.accepted && <StatusGlyph status="ok" label="accepted" glyph={GLYPH.done} />}
              <Chip cite={r.ref} />
            </div>
            <dl className="metrics">
              {r.metrics.map((m) => (
                <Fragment key={m.key}>
                  <dt>{m.label}</dt>
                  <dd className={m.best ? "best" : undefined}>
                    {plain(m.value)} <span className="unit">{m.unit}</span>
                    {m.best && <span className="gos-src"> best</span>}
                  </dd>
                </Fragment>
              ))}
            </dl>
            <ul className="route-choices">
              {r.choices.map((c) => (
                <li key={c.node} className={c.differs ? "differs" : undefined}>
                  <span>{c.label}</span> <span className="gos-src">{c.machine}</span> {c.tier && <TierChip tier={c.tier} />}
                  <Chip cite={c.ref} />
                  {c.differs && <span className="gos-src"> · differs</span>}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <Notes notes={card.notes} />
    </div>
  );
}

export function BomView({ card }: { card: BomCard }) {
  return (
    <div className="card bom-card" data-card="bom">
      <CardHead label="Bill of materials" title={card.title} cite={card.ref} />
      <table className="bom">
        <tbody>
          {card.rows.map((r) => (
            <tr key={r.key}>
              <td>{r.name}</td>
              <td className="num">
                {plain(r.amount)} <span className="unit">{r.unit}</span>
              </td>
              <td>
                <Chip cite={r.ref ?? card.ref} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Notes notes={card.notes} />
    </div>
  );
}

export function FlowView({ card }: { card: FlowCard }) {
  return (
    <div className="card flow-card" data-card="flow">
      <CardHead label="Flow diagram" title={card.title} cite={card.ref} />
      <FlowDiagram card={card} />
      {/* The diagram is a canvas: each node and flow that has its own source is listed with its chip
          (plan 7.14 rule 2); the rest come from the plan cited in the head. */}
      <div className="flow-cites">
        {card.nodes
          .filter((n) => n.ref)
          .map((n) => (
            <span key={n.id} className="gos-src">
              {n.label} <Chip cite={n.ref} />
            </span>
          ))}
        {card.edges
          .filter((e) => e.ref)
          .map((e, i) => (
            <span key={`edge-${i}`} className="gos-src" data-edge={`${e.from}-${e.to}`}>
              {edgeLabel(e)} <Chip cite={e.ref} />
            </span>
          ))}
      </div>
      <Notes notes={card.notes} />
    </div>
  );
}

export function OreListView({ card }: { card: OreListCard }) {
  return (
    <div className="card ore-card" data-card="ore_list">
      <CardHead label="Ore list" title={card.title} extra={card.material} />
      <table className="ores">
        <tbody>
          {card.rows.map((r) => (
            <tr key={`${r.dim}:${r.x}:${r.z}`} className={r.depleted ? "depleted" : undefined}>
              <td>{r.name}</td>
              <td className="gos-src">{r.ores.join(", ")}</td>
              <td className="num">
                {r.x}, {r.z} <span className="gos-src">dim {r.dim}</span>
              </td>
              <td className="num">{r.distance !== null ? `${r.distance} blocks` : ""}</td>
              <td className="gos-src">{r.layer ?? ""}</td>
              <td>{r.depleted ? <StatusGlyph status="off" label="depleted" /> : null}</td>
              <td>
                <Chip cite={r.ref} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Notes notes={card.notes} />
    </div>
  );
}

export function PowerLedgerView({ card }: { card: PowerLedgerCard }) {
  const { stale } = useCardActions();
  const scale = Math.max(1, ...card.tiers.map((t) => Math.abs(t.net)));
  const age = card.source.age_s !== null ? ` · ${card.source.age_s} s` : "";
  return (
    <div className="card power-card" data-card="power_ledger">
      <CardHead label="Net power · per tier" title={card.title} extra={`${card.source.label}${age}`} />
      <div className="power-rows">
        {card.tiers.map((t) => (
          <div key={t.tier} className="power-row">
            <PowerRow tier={t.tier} net={t.net} scale={scale} cite={t.ref} stale={stale} />
            <span className="gos-src">
              gen {signed(t.gen)} · use {plain(t.use)} EU/t
            </span>
          </div>
        ))}
      </div>
      <Notes notes={card.notes} />
    </div>
  );
}

export function HealthView({ card }: { card: HealthCard }) {
  return (
    <div className="card health-card" data-card="health">
      <CardHead label="Health" title={card.title} />
      <ul className="health-items">
        {card.items.map((i) => (
          <li key={i.id} data-item={i.id}>
            <StatusGlyph status={i.status === "error" ? "err" : i.status} label={i.label} />
            <span className="gos-src">{i.detail}</span>
            {i.restarts ? <span className="gos-src">{i.restarts} restarts</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

export { AlertView, ApprovalView };
