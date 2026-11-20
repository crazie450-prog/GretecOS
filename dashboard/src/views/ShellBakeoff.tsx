// The shell bake-off panel (WO-10.11, D374-D377): several models' answers to the same brief, side
// by side and blind. A run is started only by `gos shell bakeoff run` (spending is his, at the
// CLI); this panel shows a run case by case with the workbench's own 3D viewer and materials
// table, lettered A, B, C, and takes his pick per case (a letter, a tie, or none is good). The
// models' names, costs and times are not sent to the dashboard until every case is rated and he
// asks for the report, which then shows as a table card. Nothing here writes a world, and a
// bake-off shell is never one of his own designs.
// Built to the design system from the workbench's parts, no board yet: needs Clayton's board
// approval (D92, D158 (5)).
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import type { BakeoffCase, BakeoffEntry, BakeoffSummary, BakeoffView, BakeoffWhich, ShellCompare } from "../api/types";
import { Card } from "../cards/Card";
import type { Card as CardData } from "../cards/types.gen";
import { Button, Panel } from "../components/core";
import { GLYPH } from "../state/states";
import { ShellScene3D } from "./ShellWorkbench";

const message = (e: unknown): string => (e instanceof ApiError ? e.message : String(e));

export const TIE = "tie";
export const BAD = "bad";

/** A pick in words: a letter, a tie, or none is good. */
export function pickText(pick: string | null): string {
  if (pick === null) return "not rated";
  if (pick === TIE) return "a tie";
  if (pick === BAD) return "none is good";
  return `${pick} is better`;
}

/** The letters of a case that have something to look at for this step. */
export function shownLetters(c: BakeoffCase, which: BakeoffWhich): string[] {
  return c.entries.filter((e) => e[which] !== null).map((e) => e.letter);
}

/** The cases still to rate: the ones with a design from at least one model and no pick. */
export function leftToRate(run: BakeoffView): string[] {
  return run.cases.filter((c) => c.pick === null && c.entries.some((e) => e.design !== null)).map((c) => c.id);
}

function entryText(e: BakeoffEntry, which: BakeoffWhich): string {
  const b = e[which];
  if (b) return `${b.blocks} blocks, ${b.types} kinds${b.status === "repaired" ? ", a few blocks removed by the gate" : ""}`;
  if (which === "revise" && e.design) return "no version after the follow-up (the gate refused it, or nothing changed)";
  return "no design (the gate refused every answer, or the model gave none)";
}

function Materials(props: { run: string; caseId: string; a: string; b: string; which: BakeoffWhich }) {
  const [got, setGot] = useState<ShellCompare | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setGot(null);
    setError(null);
    api.bakeoffCompare(props.run, props.caseId, props.a, props.b, props.which).then(
      (c) => live && setGot(c),
      (e: unknown) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [props.run, props.caseId, props.a, props.b, props.which]);
  if (error) return <p className="empty">{GLYPH.warn} {error}</p>;
  if (!got) return null;
  return (
    <>
      <p className="gos-sub" data-testid="bakeoff-blocks" data-delta={got.blocks_delta}>
        {props.a}: {got.a.blocks} blocks against {props.b}: {got.b.blocks} ({got.blocks_delta >= 0 ? "+" : ""}
        {got.blocks_delta})
      </p>
      <table className="bom" data-testid="bakeoff-materials">
        <thead>
          <tr>
            <th>Block</th>
            <th className="gos-src">registry@meta</th>
            <th className="num">{props.a}</th>
            <th className="num">{props.b}</th>
            <th className="num">change</th>
          </tr>
        </thead>
        <tbody>
          {got.materials.map((m) => (
            <tr key={m.block} data-block={m.block} data-delta={m.delta}>
              <td>{m.name ?? "—"}</td>
              <td className="gos-src">{m.block}</td>
              <td className="num">{m.a}</td>
              <td className="num">{m.b}</td>
              <td className="num">{m.delta > 0 ? `+${m.delta}` : m.delta}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function ShellBakeoff() {
  const [runs, setRuns] = useState<BakeoffSummary[] | null>(null);
  const [id, setId] = useState<string | null>(null);
  const [run, setRun] = useState<BakeoffView | null>(null);
  const [report, setReport] = useState<CardData | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [which, setWhich] = useState<BakeoffWhich>("design");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api.bakeoffRuns().then(
      (r) => {
        if (!live) return;
        setRuns(r.runs);
        setId((prev) => prev ?? r.runs[0]?.id ?? null);
      },
      () => live && setRuns([]),
    );
    return () => {
      live = false;
    };
  }, []);

  const took = useCallback((r: { run: BakeoffView; report?: CardData | null }) => {
    setRun(r.run);
    setReport(r.report ?? null);
    setCaseId((prev) => (prev && r.run.cases.some((c) => c.id === prev) ? prev : (r.run.cases[0]?.id ?? null)));
  }, []);

  useEffect(() => {
    if (!id) return;
    let live = true;
    setRun(null);
    setNotice(null);
    api.bakeoffRun(id).then(
      (r) => live && took(r),
      (e: unknown) => live && setNotice(message(e)),
    );
    return () => {
      live = false;
    };
  }, [id, took]);

  const current = useMemo(() => run?.cases.find((c) => c.id === caseId) ?? null, [run, caseId]);
  const left = run ? leftToRate(run) : [];
  if (!runs || !runs.length || !id) return null; // nothing to show until a run exists

  const rate = (pick: string): void => {
    if (!run || !current) return;
    setBusy(true);
    api.bakeoffRate(run.id, current.id, pick).then(
      (r) => {
        setBusy(false);
        took({ run: r.run, report });
        setNotice(null);
      },
      (e: unknown) => {
        setBusy(false);
        setNotice(message(e));
      },
    );
  };

  const reveal = (): void => {
    if (!run) return;
    setBusy(true);
    api.bakeoffReport(run.id).then(
      (r) => {
        setBusy(false);
        took(r);
      },
      (e: unknown) => {
        setBusy(false);
        setNotice(message(e));
      },
    );
  };

  const shown = current ? shownLetters(current, which) : [];
  return (
    <Panel
      label="Shell bake-off"
      testId="bakeoff"
      className="shell-bakeoff"
      aside={
        <span className="gos-src" data-testid="bakeoff-blind">
          {run?.revealed ? "the models are named below" : "blind: the models are A, B, C until you have rated"}
        </span>
      }
    >
      {runs.length > 1 && (
        <select className="ask-input base-select" aria-label="Bake-off run" value={id} onChange={(e) => setId(e.target.value)} data-testid="bakeoff-run-pick">
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              {r.id} · {r.cases} cases · rated {r.rated} of {r.rateable}
            </option>
          ))}
        </select>
      )}
      {notice && (
        <p className="gos-sub" role="status" data-testid="bakeoff-notice">
          {GLYPH.warn} {notice}
        </p>
      )}
      {run && (
        <>
          <p className="gos-sub" data-testid="bakeoff-status" data-run={run.id}>
            {run.id} · {run.cases.length} cases, {run.models} models · {run.status}
            {run.why ? ` (${run.why})` : ""} · rated {run.rated} of {run.rateable}
          </p>
          <div className="shell-form-row" role="group" aria-label="Case">
            {run.cases.map((c) => (
              <button
                key={c.id}
                type="button"
                className="gos-chip"
                aria-pressed={c.id === caseId}
                onClick={() => setCaseId(c.id)}
                data-testid={`bakeoff-case-${c.id}`}
                data-pick={c.pick ?? ""}
              >
                {c.pick ? GLYPH.ok : GLYPH.todo} {c.id}
              </button>
            ))}
          </div>
        </>
      )}
      {run && current && (
        <div data-testid="bakeoff-case" data-case={current.id}>
          <p data-testid="bakeoff-brief">
            <span className="gos-label">The brief</span> {current.brief || "(no words: the theme alone)"}{" "}
            <span className="gos-src">
              · {current.theme} · the {current.line} line · at most {current.max_blocks} blocks
            </span>
          </p>
          <p data-testid="bakeoff-followup">
            <span className="gos-label">Then</span> {current.followup}
          </p>
          <div className="shell-form-row" role="group" aria-label="Which version">
            <button type="button" className="gos-chip" aria-pressed={which === "design"} onClick={() => setWhich("design")} data-testid="bakeoff-which-design">
              The first design
            </button>
            <button type="button" className="gos-chip" aria-pressed={which === "revise"} onClick={() => setWhich("revise")} data-testid="bakeoff-which-revise">
              After the follow-up
            </button>
          </div>
          <div className="shell-compare-grid bakeoff-grid">
            {current.entries.map((e) => (
              <div key={e.letter} data-testid={`bakeoff-entry-${e.letter}`} data-letter={e.letter}>
                <span className="gos-label">{e.letter}</span>
                {e[which] ? (
                  <ShellScene3D
                    build={e[which]!.build}
                    prefix={`bakeoff3d-${e.letter}`}
                    compact
                    source={{
                      key: `${run.id}|${current.id}|${e.letter}|${which}`,
                      load: () => api.bakeoffScene(run.id, current.id, e.letter, which),
                    }}
                  />
                ) : null}
                <p className="gos-sub" data-testid={`bakeoff-entry-text-${e.letter}`}>
                  {e[which] ? GLYPH.ok : GLYPH.warn} {entryText(e, which)}
                </p>
              </div>
            ))}
          </div>
          {shown.length >= 2 && <Materials run={run.id} caseId={current.id} a={shown[0]} b={shown[1]} which={which} />}
          <div className="shell-form-row" role="group" aria-label="Your pick" data-testid="bakeoff-rate" data-pick={current.pick ?? ""}>
            {current.entries
              .filter((e) => e.design !== null)
              .map((e) => (
                <Button
                  key={e.letter}
                  testId={`bakeoff-pick-${e.letter}`}
                  variant={current.pick === e.letter ? "primary" : "plain"}
                  disabled={busy || run.revealed}
                  onClick={() => rate(e.letter)}
                >
                  {e.letter} is better
                </Button>
              ))}
            <Button testId="bakeoff-pick-tie" variant={current.pick === TIE ? "primary" : "plain"} disabled={busy || run.revealed} onClick={() => rate(TIE)}>
              A tie
            </Button>
            <Button testId="bakeoff-pick-bad" variant={current.pick === BAD ? "primary" : "plain"} disabled={busy || run.revealed} onClick={() => rate(BAD)}>
              None is good
            </Button>
            <span className="gos-src" data-testid="bakeoff-pick-text">
              Your pick: {pickText(current.pick)}
            </span>
          </div>
        </div>
      )}
      {run && !run.revealed && (
        <div className="shell-form-row">
          <Button
            testId="bakeoff-reveal"
            variant="primary"
            disabled={busy || left.length > 0 || run.status === "running"}
            onClick={reveal}
            title="Writes the report and names the models; ratings are closed after that"
          >
            Name the models and show the report
          </Button>
          <span className="gos-src" data-testid="bakeoff-left">
            {left.length ? `${left.length} case${left.length === 1 ? "" : "s"} left to rate first` : "every case is rated"}
          </span>
        </div>
      )}
      {run?.revealed && report && (
        <div data-testid="bakeoff-report">
          <Card card={report} />
        </div>
      )}
    </Panel>
  );
}
