// Base (plan 7.14: "What's broken? What do I have?"). WO-3.8 builds My base: the tier the quests
// show beside his override (D64), the machines he has (manual ticks now; placement events and the
// census once WO-6.2 lands) and the stock he entered (none by default, D63). What it holds is the
// designer's boundary (plan 7.1 Request). The live half above it (WO-7.4, BaseLive.tsx): power,
// machines, problems (stalled first), AE2 crafting and pending actions from OpenComputers.
// No approved board (D92): designed to the system, needs Clayton's board approval (D158).
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { BaseCandidate, BaseMachine, BaseSource, BaseStock, MyBase } from "../api/types";
import { useCardActions } from "../cards/context";
import { Button, CitationChip, Panel, StatusGlyph, TierChip, stageTier } from "../components/core";
import { plain } from "../state/format";
import { GLYPH } from "../state/states";
import { BaseLivePanel } from "./BaseLive";

const SOURCE_WORD: Record<BaseSource, string> = {
  manual: "ticked",
  census: "census",
  event: "placed",
};

type Run = (edit: () => Promise<MyBase>) => Promise<boolean>;

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/** Whole numbers 0 and up for counts, any number above 0 for stock; null when it isn't one. */
export function parseAmount(text: string, whole: boolean): number | null {
  const t = text.trim().replace(/[,_\s]/g, "");
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const n = Number(t);
  if (!Number.isFinite(n) || (whole && !Number.isInteger(n))) return null;
  return n;
}

function Amount(props: {
  value: number;
  whole: boolean;
  label: string;
  disabled: boolean;
  onSet: (n: number) => void;
  testId: string;
}) {
  const [text, setText] = useState(String(props.value));
  useEffect(() => setText(String(props.value)), [props.value]);
  const commit = (): void => {
    const n = parseAmount(text, props.whole);
    if (n === null || (!props.whole && n === 0)) {
      setText(String(props.value));
      return;
    }
    if (n !== props.value) props.onSet(n);
  };
  return (
    <input
      className="ask-input num base-amount"
      inputMode={props.whole ? "numeric" : "decimal"}
      value={text}
      disabled={props.disabled}
      aria-label={props.label}
      data-testid={props.testId}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setText(String(props.value));
      }}
    />
  );
}

/** Find an item, fluid or machine by name, alias or key (the lookup tools' resolution). */
function Finder(props: {
  kind: "item" | "machine";
  disabled: boolean;
  placeholder: string;
  onPick: (c: BaseCandidate) => void;
  testId: string;
}) {
  const [text, setText] = useState("");
  const [found, setFound] = useState<BaseCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = (e: FormEvent): void => {
    e.preventDefault();
    const q = text.trim();
    if (!q) return;
    setBusy(true);
    api.baseFind(q, props.kind).then(
      (r) => {
        setFound(r.candidates);
        setError(null);
        setBusy(false);
      },
      (err: unknown) => {
        setFound(null);
        setError(message(err));
        setBusy(false);
      },
    );
  };
  return (
    <div className="base-finder" data-testid={props.testId}>
      <form className="search-form" onSubmit={submit}>
        <input
          className="ask-input"
          value={text}
          disabled={props.disabled}
          onChange={(e) => setText(e.target.value)}
          placeholder={props.placeholder}
          aria-label={props.placeholder}
          data-testid={`${props.testId}-input`}
        />
        <Button type="submit" disabled={props.disabled || busy || !text.trim()} testId={`${props.testId}-go`}>
          Find
        </Button>
      </form>
      {error && <p className="empty">{GLYPH.warn} {error}</p>}
      {found !== null && found.length === 0 && <p className="empty">{GLYPH.todo} Nothing matches.</p>}
      {found !== null && found.length > 0 && (
        <ul className="result-list">
          {found.map((c) => (
            <li key={c.key}>
              <button
                type="button"
                className="result"
                disabled={props.disabled}
                data-candidate={c.key}
                onClick={() => {
                  props.onPick(c);
                  setFound(null);
                  setText("");
                }}
              >
                <span>{c.name}</span>
                <span className="gos-src">{c.key}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TierPanel(props: { base: MyBase; run: Run; busy: boolean }) {
  const { tier, stages } = props.base;
  const [pick, setPick] = useState(tier.stage);
  useEffect(() => setPick(tier.stage), [tier.stage]);
  const { openQuest } = useCardActions();
  const override = tier.how === "override";
  return (
    <Panel label="Tier" testId="base-tier">
      <div className="base-tier-now">
        <TierChip tier={stageTier(tier.stage)} />
        <span className="gos-sub" data-testid="base-tier-how">
          {override ? "Your override" : "From quest progress"} · GT voltage tier {tier.tier}
        </span>
      </div>
      <p className="gos-sub" data-testid="base-tier-detected">
        {tier.detected === null ? (
          <>{GLYPH.waiting} Quests show nothing yet: load your quest progress.</>
        ) : (
          <>
            Quests show <TierChip tier={stageTier(tier.detected)} />{" "}
            {tier.quest && (
              <CitationChip cite={tier.quest} onOpen={openQuest ? (ref) => openQuest(ref, 0) : undefined} />
            )}
            {tier.quest_name && <span className="gos-src"> {tier.quest_name}</span>}
          </>
        )}
      </p>
      <form
        className="search-form"
        onSubmit={(e) => {
          e.preventDefault();
          void props.run(() => api.baseTier(pick));
        }}
      >
        <select
          className="ask-input base-select"
          value={pick}
          disabled={props.busy}
          onChange={(e) => setPick(e.target.value)}
          aria-label="Override the tier with"
          data-testid="base-tier-pick"
        >
          {stages.map((s) => (
            <option key={s.stage} value={s.stage}>
              {s.stage === "stone" || s.stage === "steam" ? `${s.stage} (Steam)` : s.stage}
            </option>
          ))}
        </select>
        <Button type="submit" variant="primary" disabled={props.busy || (override && pick === tier.stage)} testId="base-tier-set">
          Override
        </Button>
        {override && (
          <Button disabled={props.busy} onClick={() => void props.run(() => api.baseTier(null))} testId="base-tier-clear">
            Back to quests
          </Button>
        )}
      </form>
    </Panel>
  );
}

function MachineRow(props: { m: BaseMachine; run: Run; busy: boolean }) {
  const { m } = props;
  const others = (Object.keys(m.sources) as BaseSource[]).filter((s) => s !== m.source);
  return (
    <tr data-machine={m.mte_id}>
      <td>
        <span className="base-name">{m.name ?? `${GLYPH.failed} not in the current DB`}</span>{" "}
        <CitationChip cite={m.ref} />
      </td>
      <td className="num">
        <div className="base-stepper">
          <Button
            disabled={props.busy || m.count === 0}
            title="One fewer"
            onClick={() => void props.run(() => api.baseMachine(m.mte_id, m.count - 1))}
            testId={`machine-less-${m.mte_id}`}
          >
            −
          </Button>
          <Amount
            value={m.count}
            whole
            label={`How many ${m.name ?? m.ref}`}
            disabled={props.busy}
            onSet={(n) => void props.run(() => api.baseMachine(m.mte_id, n))}
            testId={`machine-count-${m.mte_id}`}
          />
          <Button
            disabled={props.busy}
            title="One more"
            onClick={() => void props.run(() => api.baseMachine(m.mte_id, m.count + 1))}
            testId={`machine-more-${m.mte_id}`}
          >
            +
          </Button>
        </div>
      </td>
      <td>
        {m.count === 0 ? <StatusGlyph status="info" glyph={GLYPH.todo} label="none" /> : SOURCE_WORD[m.source]}
        {others.length > 0 && (
          <span className="gos-src"> ({others.map((s) => `${SOURCE_WORD[s]} ${m.sources[s]}`).join(", ")})</span>
        )}
      </td>
      <td>
        {m.sources.manual !== undefined && (
          <Button disabled={props.busy} onClick={() => void props.run(() => api.baseMachineClear(m.mte_id))} testId={`machine-remove-${m.mte_id}`}>
            Remove
          </Button>
        )}
      </td>
    </tr>
  );
}

function MachinesPanel(props: { base: MyBase; run: Run; busy: boolean }) {
  const { machines } = props.base;
  const owned = machines.filter((m) => m.count > 0).length;
  const add = (c: BaseCandidate): void => {
    if (c.mte_id === undefined) return;
    const have = machines.find((m) => m.mte_id === c.mte_id);
    void props.run(() => api.baseMachine(c.mte_id as number, Math.max(1, have?.count ?? 0)));
  };
  return (
    <Panel label="Machines" testId="base-machines" aside={<span className="gos-src">{owned} {owned === 1 ? "type" : "types"} owned</span>}>
      <Finder
        kind="machine"
        disabled={props.busy || !props.base.has_db}
        placeholder="Add a machine: EBF, macerator"
        onPick={add}
        testId="machine-find"
      />
      {machines.length === 0 ? (
        <p className="empty" data-testid="machines-empty">
          {GLYPH.todo} No machines yet. The designer counts every machine as one to build until you tick it here.
        </p>
      ) : (
        <div className="table-wrap">
          <table className="base-table">
            <thead>
              <tr>
                <th>machine</th>
                <th className="num">count</th>
                <th>from</th>
                <th aria-label="remove">
                </th>
              </tr>
            </thead>
            <tbody>
              {machines.map((m) => (
                <MachineRow key={m.mte_id} m={m} run={props.run} busy={props.busy} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function StockRow(props: { s: BaseStock; run: Run; busy: boolean }) {
  const { s } = props;
  return (
    <tr data-stock={s.key}>
      <td>
        <span className="base-name">{s.name ?? `${GLYPH.failed} not in the current DB`}</span>
        <div className="gos-src">{s.key}</div>
      </td>
      <td className="num">
        <Amount
          value={s.qty}
          whole={false}
          label={`How much ${s.name ?? s.key}`}
          disabled={props.busy}
          onSet={(n) => void props.run(() => api.baseStock(s.key, n))}
          testId={`stock-qty-${s.key}`}
        />{" "}
        <span className="unit">{s.unit}</span>
      </td>
      <td>
        <Button disabled={props.busy} onClick={() => void props.run(() => api.baseStockRemove(s.key))} testId={`stock-remove-${s.key}`}>
          Remove
        </Button>
      </td>
    </tr>
  );
}

function StockPanel(props: { base: MyBase; run: Run; busy: boolean }) {
  const { stock } = props.base;
  const [picked, setPicked] = useState<BaseCandidate | null>(null);
  const [qty, setQty] = useState("");
  const amount = parseAmount(qty, false);
  const add = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    if (picked === null || amount === null || amount <= 0) return;
    if (await props.run(() => api.baseStock(picked.key, amount))) {
      setPicked(null);
      setQty("");
    }
  };
  return (
    <Panel label="Stock" testId="base-stock" aside={<span className="gos-src">{stock.length} stocked</span>}>
      <Finder
        kind="item"
        disabled={props.busy || !props.base.has_db}
        placeholder="Add an item or fluid: steel ingot, oxygen"
        onPick={setPicked}
        testId="stock-find"
      />
      {picked && (
        <form className="search-form" onSubmit={(e) => void add(e)} data-testid="stock-add">
          <span className="base-name">{picked.name}</span>
          <input
            className="ask-input num base-amount"
            inputMode="decimal"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            placeholder={picked.kind === "fluid" ? "L" : "items"}
            aria-label={`How much ${picked.name}`}
            data-testid="stock-add-qty"
          />
          <span className="unit">{picked.kind === "fluid" ? "L" : "items"}</span>
          <Button type="submit" variant="primary" disabled={props.busy || amount === null || amount <= 0} testId="stock-add-go">
            Add
          </Button>
          <Button onClick={() => setPicked(null)}>Cancel</Button>
        </form>
      )}
      {stock.length === 0 ? (
        <p className="empty" data-testid="stock-empty">
          {GLYPH.todo} Nothing stocked. The designer makes everything from raw materials until you add what you have.
        </p>
      ) : (
        <div className="table-wrap">
          <table className="base-table">
            <thead>
              <tr>
                <th>item or fluid</th>
                <th className="num">on hand</th>
                <th aria-label="remove">
                </th>
              </tr>
            </thead>
            <tbody>
              {stock.map((s) => (
                <StockRow key={s.key} s={s} run={props.run} busy={props.busy} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function Base() {
  const [base, setBase] = useState<MyBase | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    api.base().then(
      (b) => live && setBase(b),
      (e: unknown) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, []);
  const run: Run = useCallback(async (edit) => {
    setBusy(true);
    try {
      setBase(await edit());
      setNotice(null);
      return true;
    } catch (e) {
      setNotice(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <div className="view-base" data-testid="view-base">
      <div className="view-head">
        <h1 className="page-title">Base</h1>
        <p className="lede">What's broken? What do I have? Your tier, machines and stock are what the designer plans from.</p>
      </div>
      {error && <p className="empty">{GLYPH.warn} {error}</p>}
      {!base && !error && <p className="empty">{GLYPH.progress} Loading My base</p>}
      {base && (
        <>
          {notice && (
            <div className="notice gos-panel" role="alert" data-testid="base-notice">
              <span>
                {GLYPH.warn} {notice}
              </span>
              <Button onClick={() => setNotice(null)}>Dismiss</Button>
            </div>
          )}
          {!base.has_db && (
            <p className="empty" data-testid="base-no-db">
              {GLYPH.warn} No recipe data yet: run <code>gos db build</code> to add machines and stock.
            </p>
          )}
          <BaseLivePanel />
          <div className="base-grid">
            <div className="col">
              <TierPanel base={base} run={run} busy={busy} />
              <Panel label="Running lines" testId="base-lines">
                {base.lines.length === 0 ? (
                  <p className="empty">{GLYPH.todo} No running lines yet. A line's output joins the designer's inputs once you mark its plan running.</p>
                ) : (
                  <ul className="result-list">
                    {base.lines.map((l) => (
                      <li key={l.key} data-testid="base-line">
                        <code>{l.key}</code> <span className="num">{plain(l.rate_s)}/s</span>{" "}
                        {l.plans.map((p) => (
                          <CitationChip key={p} cite={p} />
                        ))}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
            <MachinesPanel base={base} run={run} busy={busy} />
            <StockPanel base={base} run={run} busy={busy} />
          </div>
        </>
      )}
    </div>
  );
}
