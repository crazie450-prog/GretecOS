// Power (plan 7.14: "Am I in deficit, at which tier?"; plan 7.2, D13, D67, WO-4.6). The EU ledger
// first: generation against use per tier (click a tier to see each plan's draw there), its
// warnings, and the storage and distribution checks (transformer chains for a tier with no source
// at it; buffers and an LSC once he gives a peak window, D241). Then the power designer: the four
// objective modes, 2-3 option cards side by side in the core's rank order (the view never sorts
// them) with the weights printed, each with net = gross − its own chain's use, its effort breakdown
// (WO-4.3's table card), the validator's verdict and bootstrap kit, the cables that carry its
// output and the ledger as it would be with it accepted; accepting is his, after a confirm. Every
// number is the core's, with its citation chip. The design shown is in the address
// (#/power/<plan id>). Power has an approved board (D92) whose canvas handoff isn't in the repo:
// built to the design system (D158 (5)), to be reconciled with the board.
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  LedgerEntry,
  LedgerWarning,
  PowerLedgerSection,
  PowerMode,
  PowerOption,
  PowerSpec,
  PowerView,
  TransformerPlan,
} from "../api/types";
import { Card } from "../cards/Card";
import { useCardActions } from "../cards/context";
import type { PowerLedgerCard } from "../cards/types.gen";
import { Button, CitationChip, Panel, PowerRow, StatusGlyph, TierChip } from "../components/core";
import { plain, signed } from "../state/format";
import { GLYPH } from "../state/states";

const TIERS = ["", "ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV", "UHV"];

export const MODES: { id: PowerMode; label: string; how: string }[] = [
  { id: "best", label: "Best at tier", how: "the weighted score: net EU/t up; effort, upkeep, complexity, scarcity down" },
  { id: "max_output", label: "Max output", how: "the most net EU/t at the tier cap, within a scale bound" },
  { id: "output_per_effort", label: "Output per effort", how: "net EU/t per effort hour" },
  { id: "self_sustaining", label: "Self-sustaining", how: "nothing from outside the free set, proven; a bootstrap kit" },
];

const SCALES: { id: NonNullable<PowerSpec["scale"]>; label: string; unit: string }[] = [
  { id: "unit", label: "One build unit", unit: "" },
  { id: "generators", label: "Generators", unit: "count" },
  { id: "eut_cap", label: "Net EU/t cap", unit: "EU/t" },
  { id: "raw_budget", label: "Input budget", unit: "per hour" },
];

const WARNING_LABEL: Record<string, string> = {
  deficit: "Deficit",
  tier_mismatch: "Tier mismatch",
  amp_overload: "Amp overload",
};

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

/** The power plan the address names (#/power/pw-...), or null. */
export function powerFromHash(hash: string): string | null {
  const m = /^#\/?power\/(pw-[a-z0-9._-]{1,61})$/.exec(hash);
  return m ? m[1] : null;
}

function showPlan(id: string): void {
  const want = `#/power/${id}`;
  if (window.location.hash !== want) window.location.hash = want.slice(1);
}

/** A peak window in seconds from his text ("600", "10 min", "2h"), or null. */
export function parseWindow(text: string): number | null {
  const m = /^\s*([0-9]+(?:\.[0-9]+)?)\s*(s|sec|min|m|h)?\s*$/i.exec(text);
  if (!m) return null;
  const n = Number(m[1]);
  const unit = (m[2] ?? "s").toLowerCase();
  const s = unit === "h" ? n * 3600 : unit === "min" || unit === "m" ? n * 60 : n;
  return s > 0 && s <= 7 * 24 * 3600 ? s : null;
}

/** The plans that draw or give power at one tier, as the ledger records them. */
export function atTier(entries: LedgerEntry[], tier: string): { ref: string; name: string; what: string; eut: number }[] {
  const out: { ref: string; name: string; what: string; eut: number }[] = [];
  for (const e of entries) {
    if (e.source && e.source.tier === tier) out.push({ ref: e.ref, name: e.name, what: "gives", eut: e.source.eut });
    for (const d of e.draws) if (d.tier === tier) out.push({ ref: e.ref, name: e.name, what: "uses", eut: d.eut_avg });
  }
  return out;
}

function Cite({ cite }: { cite: string | null | undefined }) {
  const { openCite, stale } = useCardActions();
  if (!cite) return null;
  const game = cite.startsWith("r:") || cite.startsWith("mte:");
  return <CitationChip cite={cite} stale={stale && game} onOpen={cite.startsWith("r:") ? openCite : undefined} />;
}

function Warnings({ warnings, testId }: { warnings: LedgerWarning[]; testId: string }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="power-warnings" data-testid={testId}>
      {warnings.map((w) => (
        <li key={`${w.kind}-${w.tier ?? ""}`} data-warning={w.kind}>
          <StatusGlyph status="warn" label={`${WARNING_LABEL[w.kind] ?? w.kind}${w.tier ? ` · ${w.tier}` : ""}`} />
          <span className="gos-sub">{w.detail}</span>
          {w.plans.slice(0, 4).map((p) => (
            <Cite key={p} cite={p} />
          ))}
        </li>
      ))}
    </ul>
  );
}

function LedgerTiers(props: { card: PowerLedgerCard; entries: LedgerEntry[] }) {
  const { stale } = useCardActions();
  const [open, setOpen] = useState<string | null>(null);
  const tiers = props.card.tiers;
  const scale = Math.max(1, ...tiers.map((t) => Math.abs(t.net)));
  if (tiers.length === 0) {
    return (
      <p className="empty" data-testid="power-ledger-empty">
        {GLYPH.todo} No accepted plans yet. Accept a power line below, or a production line in the Designer.
      </p>
    );
  }
  return (
    <ul className="power-tiers" data-testid="power-tiers">
      {tiers.map((t) => {
        const here = open === t.tier ? atTier(props.entries, t.tier) : [];
        return (
          <li key={t.tier} data-tier={t.tier}>
            <button
              type="button"
              className="power-tier"
              aria-expanded={open === t.tier}
              onClick={() => setOpen(open === t.tier ? null : t.tier)}
              data-testid={`power-tier-${t.tier}`}
            >
              <PowerRow tier={t.tier} net={t.net} scale={scale} cite={t.ref} stale={stale} />
              <span className="gos-src">
                gen {signed(t.gen)} · use {plain(t.use)} EU/t
              </span>
            </button>
            {open === t.tier && (
              <ul className="power-draws" data-testid={`power-draws-${t.tier}`}>
                {here.map((d, i) => (
                  <li key={`${d.ref}-${d.what}-${i}`}>
                    <span>{d.name}</span>
                    <span className="gos-src num">
                      {d.what} {plain(d.eut)} EU/t
                    </span>
                    <Cite cite={d.ref} />
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function Transformers({ plans, testId }: { plans: TransformerPlan[]; testId: string }) {
  if (plans.length === 0) return null;
  return (
    <ul className="power-chain" data-testid={testId}>
      {plans.map((t) => (
        <li key={`${t.source}-${t.line}`}>
          <span className="gos-label">
            <TierChip tier={t.source} /> → <TierChip tier={t.line} />
          </span>
          <span className="gos-src">
            {plain(t.amps)} A at {t.line} · {plain(t.source_amps)} A at {t.source}
          </span>
          {t.steps.map((s) => (
            <span key={`${s.high}-${s.low}`} className="gos-src">
              {plain(s.count)} x {s.unit.name} ({s.mode}) <Cite cite={s.unit.ref} />
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

function LedgerPanel(props: {
  ledger: PowerLedgerSection | null;
  error: string | null;
  windowS: number;
  setWindow: (s: number) => void;
}) {
  const [scope, setScope] = useState<"all" | "running">("all");
  const [text, setText] = useState(props.windowS ? String(props.windowS) : "");
  const led = props.ledger;
  const card = led ? (scope === "all" ? led.card : led.running) : null;
  const warnings = led ? (scope === "all" ? led.warnings : led.running_warnings) : [];
  const dist = led?.distribution;
  const submit = (e: FormEvent): void => {
    e.preventDefault();
    const s = text.trim() ? parseWindow(text) : 0;
    if (s !== null) props.setWindow(s);
  };
  const bad = text.trim() !== "" && parseWindow(text) === null;
  return (
    <div className="power-top">
      <Panel
        label="EU ledger · per tier"
        testId="power-ledger"
        aside={
          <span className="power-scope" role="group" aria-label="Scope">
            <Button variant={scope === "all" ? "primary" : "plain"} onClick={() => setScope("all")} testId="power-scope-all">
              Accepted
            </Button>
            <Button
              variant={scope === "running" ? "primary" : "plain"}
              onClick={() => setScope("running")}
              testId="power-scope-running"
            >
              Running
            </Button>
          </span>
        }
      >
        {props.error && <p className="empty">{GLYPH.warn} {props.error}</p>}
        {!led && !props.error && <p className="empty">{GLYPH.progress} Loading the ledger</p>}
        {card && (
          <>
            <p className="gos-src">
              {card.source.label}
              {card.source.age_s !== null ? ` · ${card.source.age_s} s` : ""} · {led?.lines ?? 0} plans
            </p>
            <LedgerTiers card={card} entries={led?.entries ?? []} />
            {warnings.length === 0 && card.tiers.length > 0 && (
              <p className="gos-sub" data-testid="power-no-warnings">
                <StatusGlyph status="ok" label="No warnings" /> generation covers use at every tier in this scope
              </p>
            )}
            <Warnings warnings={warnings} testId="power-warnings" />
          </>
        )}
      </Panel>
      <Panel label="Storage and distribution" testId="power-distribution">
        <form className="power-window" onSubmit={submit}>
          <label className="field small">
            <span className="gos-label">Peak window</span>
            <input
              className="ask-input num"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="600 s, 10 min"
              aria-label="Peak window: how long the peak lasts"
              aria-invalid={bad}
              data-testid="power-window"
            />
          </label>
          <Button type="submit" disabled={bad} testId="power-window-go">
            Size buffers
          </Button>
        </form>
        {bad && <p className="gos-sub">{GLYPH.warn} A window is seconds, minutes or hours, up to a week.</p>}
        {dist && (
          <>
            {dist.buffers.length > 0 && (
              <ul className="power-buffers" data-testid="power-buffers">
                {dist.buffers.map((b) => {
                  const pick = b.recommend === "lsc" ? b.lsc : b.battery_buffer;
                  return (
                    <li key={b.tier}>
                      <span className="gos-label">
                        <TierChip tier={b.tier} /> short {plain(b.deficit_eut)} EU/t for {plain(b.window_s)} s ·{" "}
                        {plain(b.energy_eu)} EU
                      </span>
                      {pick ? (
                        <span className="gos-src">
                          {plain(pick.count)} x {pick.unit} <Cite cite={pick.unit_ref} /> · {b.reason}
                        </span>
                      ) : (
                        <span className="gos-src">{b.reason}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            <Transformers plans={dist.transformers} testId="power-transformers" />
            {dist.notes.map((n) => (
              <p key={n} className="gos-sub">
                {GLYPH.waiting} {n}
              </p>
            ))}
          </>
        )}
      </Panel>
    </div>
  );
}

function DesignForm(props: { busy: boolean; mode: PowerMode; setMode: (m: PowerMode) => void; onDesign: (s: PowerSpec) => void }) {
  const [tier, setTier] = useState("");
  const [scale, setScale] = useState<NonNullable<PowerSpec["scale"]>>("unit");
  const [bound, setBound] = useState("");
  const [options, setOptions] = useState(3);
  const needsBound = props.mode === "max_output" && scale !== "unit";
  const boundN = Number(bound);
  const boundOk = !needsBound || (bound.trim() !== "" && Number.isFinite(boundN) && boundN > 0);
  const submit = (e: FormEvent): void => {
    e.preventDefault();
    if (!boundOk) return;
    props.onDesign({
      mode: props.mode,
      ...(tier ? { tier } : {}),
      ...(props.mode === "max_output" ? { scale } : {}),
      ...(needsBound ? { bound: boundN } : {}),
      options,
    });
  };
  return (
    <Panel label="Design a power line" testId="power-form">
      <div className="power-modes" role="radiogroup" aria-label="Objective mode">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={props.mode === m.id}
            className={`power-mode${props.mode === m.id ? " on" : ""}`}
            onClick={() => props.setMode(m.id)}
            data-testid={`power-mode-${m.id}`}
          >
            <span className="gos-label">{m.label}</span>
            <span className="gos-src">{m.how}</span>
          </button>
        ))}
      </div>
      <form className="designer-form" onSubmit={submit}>
        <label className="field small">
          <span className="gos-label">Tier cap</span>
          <select className="ask-input base-select" value={tier} onChange={(e) => setTier(e.target.value)} aria-label="Tier cap" data-testid="power-tier">
            {TIERS.map((t) => (
              <option key={t} value={t}>
                {t || "My base"}
              </option>
            ))}
          </select>
        </label>
        {props.mode === "max_output" && (
          <>
            <label className="field small">
              <span className="gos-label">Scale bound</span>
              <select
                className="ask-input base-select"
                value={scale}
                onChange={(e) => setScale(e.target.value as NonNullable<PowerSpec["scale"]>)}
                aria-label="Scale bound"
                data-testid="power-scale"
              >
                {SCALES.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            {needsBound && (
              <label className="field small">
                <span className="gos-label">{SCALES.find((s) => s.id === scale)?.unit}</span>
                <input
                  className="ask-input num"
                  value={bound}
                  onChange={(e) => setBound(e.target.value)}
                  aria-label="Bound"
                  aria-invalid={!boundOk}
                  data-testid="power-bound"
                />
              </label>
            )}
          </>
        )}
        <label className="field small">
          <span className="gos-label">Options</span>
          <select
            className="ask-input base-select"
            value={options}
            onChange={(e) => setOptions(Number(e.target.value))}
            aria-label="How many options"
            data-testid="power-options"
          >
            {[3, 2, 1].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <Button type="submit" variant="primary" disabled={props.busy || !boundOk} testId="power-go">
          Design
        </Button>
      </form>
    </Panel>
  );
}

function Num(props: { label: string; value: number | null | undefined; unit?: string; testId?: string }) {
  return (
    <div className="power-num" data-testid={props.testId}>
      <dt className="gos-label">{props.label}</dt>
      <dd className="num">
        {props.value === null || props.value === undefined ? "–" : plain(props.value)}
        {props.unit && props.value !== null && props.value !== undefined ? ` ${props.unit}` : ""}
      </dd>
    </div>
  );
}

function OptionCard(props: { o: PowerOption; busy: boolean; onAccept: () => void }) {
  const o = props.o;
  const [confirming, setConfirming] = useState(false);
  const [nodes, setNodes] = useState(false);
  const accepted = o.status !== "proposed";
  const kit = o.bootstrap;
  return (
    <section className={`gos-panel power-option${accepted ? " accepted" : ""}`} data-option={o.id} data-testid={`option-${o.position}`}>
      <div className="panel-head">
        <span className="gos-label">
          Option {o.position} of {o.of}
        </span>
        {accepted ? (
          <StatusGlyph status="ok" glyph={GLYPH.done} label={o.status} />
        ) : (
          <span className="gos-src">score {plain(o.score)}</span>
        )}
      </div>
      <h2 className="gos-title">{o.generator.label}</h2>
      <div className="designer-cites">
        <Cite cite={`plan:${o.id}`} />
        <TierChip tier={o.generator.tier} />
        {o.generator.cites.map((c) => (
          <Cite key={c} cite={c} />
        ))}
      </div>
      <div className="power-net" data-testid={`net-${o.id}`}>
        <span className={`gos-big${o.net_eut < 0 ? " neg" : ""}`}>
          {signed(o.net_eut)}
          <span className="unit"> EU/t net</span>
        </span>
        <span className="gos-src">
          = {plain(o.gross_eut)} out − {plain(o.chain_eut)} its own chain uses
        </span>
      </div>
      <p className="gos-sub">
        Burns {o.fuel.name} at {plain(o.fuel.rate_s)}/s <Cite cite={o.fuel.ref} />
      </p>
      {o.differs.length > 0 && (
        <p className="gos-src" data-testid={`differs-${o.id}`}>
          Differs from option 1: {o.differs.join("; ")}
        </p>
      )}
      <dl className="power-nums">
        <Num label="Out at" value={o.distribution.amps} unit={`A ${o.distribution.tier ?? ""}`} />
        <Num label="Per effort h" value={o.eut_per_effort_h} unit="EU/t" />
        <Num label="Effort" value={o.effort?.eff_h} unit="h" />
        <Num label="Upkeep" value={o.upkeep_h} unit="/h" />
        <Num label="Machines" value={o.machines} />
        {o.units !== null && o.units !== 1 && <Num label="Units" value={o.units} />}
      </dl>
      <p className="gos-sub" data-testid={`closed-${o.id}`}>
        {o.closed.ok ? (
          <StatusGlyph status="ok" label="Closed" />
        ) : (
          <StatusGlyph status="info" glyph={GLYPH.waiting} label="Imports" />
        )}{" "}
        {o.closed.ok
          ? "the validator found no import outside the free set"
          : o.closed.imports.map((i) => `${i.name} ${plain(i.rate_s)}/s`).join(", ") || o.closed.problems.join("; ")}
      </p>
      {kit && (
        <div className="power-kit" data-testid={`kit-${o.id}`}>
          <span className="gos-label">Bootstrap kit</span>
          <ul>
            {[
              ...kit.seeds_saplings.map((x) => `${x.id} x${plain(x.qty)}`),
              ...kit.kept_inputs.map((x) => `${x.id} x${plain(x.qty)}`),
              ...kit.tools.map((x) => `${x.tool} x${plain(x.qty)}`),
              ...kit.parts.map((x) => `${x.part} x${plain(x.qty)}`),
              ...kit.starter_fuel.map((x) => `${x.id} x${plain(x.qty)} (starter fuel)`),
            ].map((t) => (
              <li key={t} className="gos-src">
                {t}
              </li>
            ))}
          </ul>
        </div>
      )}
      {[...o.rank_notes, ...o.warnings].slice(0, 4).map((w) => (
        <p key={w} className="gos-sub">
          {GLYPH.warn} {w}
        </p>
      ))}
      <div className="power-preview" data-testid={`preview-${o.id}`}>
        <span className="gos-label">{o.ledger.counted ? "In the ledger" : "The ledger with this line"}</span>
        {o.ledger.cleared.map((w) => (
          <p key={`c-${w.kind}-${w.tier ?? ""}`} className="gos-sub" data-cleared={w.kind}>
            <StatusGlyph status="ok" glyph={GLYPH.done} label={`clears ${WARNING_LABEL[w.kind] ?? w.kind}${w.tier ? ` · ${w.tier}` : ""}`} />
          </p>
        ))}
        <Warnings warnings={o.ledger.added} testId={`added-${o.id}`} />
        {o.ledger.cleared.length === 0 && o.ledger.added.length === 0 && (
          <p className="gos-src">no change to the ledger's warnings</p>
        )}
        <Transformers plans={o.ledger.transformers} testId={`transformers-${o.id}`} />
      </div>
      {o.cables.length > 0 && (
        <div className="power-cables" data-testid={`cables-${o.id}`}>
          <span className="gos-label">Cables for {plain(o.distribution.amps ?? 0)} A at {o.distribution.tier}</span>
          <ul>
            {o.cables.map((c) => (
              <li key={c.ref} className="gos-src">
                {c.name} · {plain(c.amps)} A · loss {plain(c.loss)} <Cite cite={c.ref} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {o.effort && (
        <details className="power-effort" data-testid={`effort-${o.id}`}>
          <summary className="gos-label">
            Effort breakdown · {plain(o.effort.eff_h)} h = build {plain(o.effort.build_h)} + unlock {plain(o.effort.unlock_h)}
            {o.effort.uncounted ? ` · ${o.effort.uncounted} items not counted` : ""}
          </summary>
          <Card card={o.effort.card} compact />
        </details>
      )}
      <Button onClick={() => setNodes(!nodes)} testId={`nodes-toggle-${o.id}`}>
        {nodes ? "Hide" : "Show"} its {o.nodes.length} nodes
      </Button>
      {nodes && (
        <ul className="designer-nodes" data-testid={`nodes-${o.id}`}>
          {o.nodes.map((n) => (
            <li key={n.n} className="designer-node">
              <span className="result">
                <span>{n.name}</span>
                <span className="gos-src">
                  {n.count !== null ? `${plain(n.count)}x ` : ""}
                  {n.machine ?? n.kind}
                </span>
              </span>
              <span className="designer-node-meta">
                {n.tier && <TierChip tier={n.tier} />}
                <Cite cite={n.recipe} />
                <Cite cite={n.mte} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {!accepted &&
        (confirming ? (
          <div className="designer-accept needs" data-testid={`accept-confirm-${o.id}`}>
            <p className="gos-sub">
              Accept "{o.name}"? GOS writes its line note in the vault and adds its {plain(o.net_eut)} EU/t to the EU ledger as a source.
            </p>
            <Button
              variant="confirm"
              disabled={props.busy}
              onClick={() => {
                setConfirming(false);
                props.onAccept();
              }}
              testId={`accept-yes-${o.id}`}
            >
              Confirm
            </Button>
            <Button onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        ) : (
          <Button variant="primary" disabled={props.busy} onClick={() => setConfirming(true)} testId={`accept-${o.id}`}>
            Accept this line
          </Button>
        ))}
    </section>
  );
}

export function Power() {
  const [view, setView] = useState<PowerView | null>(null);
  const [ledger, setLedger] = useState<PowerLedgerSection | null>(null);
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [mode, setMode] = useState<PowerMode>("best");
  const [windowS, setWindowS] = useState(0);
  const shown = useRef<string | null>(null);

  const show = useCallback((v: PowerView) => {
    shown.current = v.id;
    setView(v);
    setLedger(v.ledger);
    setMode(v.mode);
    setError(null);
    showPlan(v.id);
  }, []);

  const load = useCallback(
    (id: string, w: number) => {
      shown.current = id;
      api.powerView(id, w).then(show, (e: unknown) => {
        shown.current = null;
        setError(message(e));
      });
    },
    [show],
  );

  useEffect(() => {
    const fromHash = powerFromHash(window.location.hash);
    if (fromHash) load(fromHash, 0);
    else
      api.powerLatest(0).then(
        (r) => {
          setLedger(r.ledger);
          if (r.ledger === null) setLedgerError("No recipe DB yet: run gos db build.");
          if (r.ids.length) load(r.ids[0], 0);
        },
        (e: unknown) => setLedgerError(message(e)),
      );
    const on = (): void => {
      const id = powerFromHash(window.location.hash);
      if (id && id !== shown.current) load(id, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [load]);

  const changeWindow = (s: number): void => {
    setWindowS(s);
    if (view) load(view.id, s);
    else
      api.powerLatest(s).then(
        (r) => setLedger(r.ledger),
        (e: unknown) => setLedgerError(message(e)),
      );
  };

  const design = (spec: PowerSpec): void => {
    setBusy("Sizing generators and fuel chains");
    setNotice(null);
    api.powerDesign(spec).then(
      (out) => {
        setBusy(null);
        show(out.view);
      },
      (e: unknown) => {
        setBusy(null);
        setNotice(message(e));
      },
    );
  };

  const accept = (id: string): void => {
    setBusy("Accepting");
    api.powerAccept(id).then(
      (out) => {
        setBusy(null);
        show(out.view);
        setNotice(out.problems.length ? out.problems.join("; ") : null);
      },
      (e: unknown) => {
        setBusy(null);
        setNotice(message(e));
      },
    );
  };

  const options = view?.options ?? [];
  return (
    <div className="view-power" data-testid="view-power">
      <div className="view-head">
        <h1 className="page-title">Power</h1>
        <p className="lede">Am I in deficit, at which tier? Then 2-3 ranked lines for any of four goals; ranked by code.</p>
      </div>
      <LedgerPanel ledger={ledger} error={ledgerError} windowS={windowS} setWindow={changeWindow} />
      <DesignForm busy={busy !== null} mode={mode} setMode={setMode} onDesign={design} />
      {busy && (
        <div className="gos-panel" aria-busy="true" data-testid="power-busy">
          <p className="empty">
            {GLYPH.progress} {busy}
          </p>
        </div>
      )}
      {notice && (
        <div className="notice gos-panel" role="alert" data-testid="power-notice">
          <span>
            {GLYPH.warn} {notice}
          </span>
          <Button onClick={() => setNotice(null)}>Dismiss</Button>
        </div>
      )}
      {error && (
        <div className="gos-panel">
          <p className="empty">
            {GLYPH.warn} {error}
          </p>
        </div>
      )}
      {!view && !error && (
        <div className="gos-panel" data-testid="power-empty">
          <p className="empty">
            {GLYPH.todo} No power design open. Pick a goal above, or say "design power at HV, self-sustaining".
          </p>
        </div>
      )}
      {view && (
        <>
          <div className="designer-bar gos-panel" data-testid="power-bar">
            <span className="gos-label">
              {view.mode_label}
              {view.tier_cap ? (
                <>
                  {" "}
                  · cap <TierChip tier={view.tier_cap} />
                </>
              ) : null}
            </span>
            <span className="gos-src">{view.mode_how}</span>
            <span className="gos-src" data-testid="power-weights">
              {view.weights}
            </span>
          </div>
          <div className="designer-routes" style={{ gridTemplateColumns: `repeat(${Math.max(1, options.length)}, minmax(0, 1fr))` }}>
            {options.map((o) => (
              <OptionCard key={o.id} o={o} busy={busy !== null} onAccept={() => accept(o.id)} />
            ))}
          </div>
          {(view.notes.length > 0 || view.rejected.length > 0) && (
            <details className="gos-panel power-notes" data-testid="power-notes">
              <summary className="gos-label">
                Notes · {view.notes.length} · rejected candidates · {view.rejected.length}
              </summary>
              {view.notes.map((n) => (
                <p key={n} className="gos-sub">
                  {GLYPH.waiting} {n}
                </p>
              ))}
              {view.rejected.map((r) => (
                <p key={`${r.candidate}-${r.why}`} className="gos-src">
                  {GLYPH.failed} {r.candidate}: {r.why}
                </p>
              ))}
            </details>
          )}
        </>
      )}
    </div>
  );
}
