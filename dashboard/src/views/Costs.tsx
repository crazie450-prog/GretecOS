// Costs (plan 7.14): am I within credits? Month to date against the included credits, by lane
// and model, from the llm_usage meter (D118). Needs Clayton's board approval (no board, D92/D158).
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Usage, UsageRow } from "../api/types";
import { Panel, StatusGlyph } from "../components/core";
import { pct, tokens, usd } from "../state/format";
import { GLYPH } from "../state/states";

const REFRESH_MS = 30000;

/** The credit level of a cycle: ok, warn past the first alert, err past the last (D48). */
export function creditLevel(u: Pick<Usage, "crossed_pct" | "alerts_pct">): "ok" | "warn" | "err" {
  if (!u.crossed_pct.length) return "ok";
  return u.crossed_pct.includes(Math.max(...u.alerts_pct)) ? "err" : "warn";
}

function Rows({ rows, by }: { rows: UsageRow[]; by: "lane" | "model" }) {
  if (!rows.length) return <p className="empty">{GLYPH.todo} No calls this cycle.</p>;
  return (
    <table className="usage">
      <thead>
        <tr>
          <th>{by}</th>
          <th className="num">calls</th>
          <th className="num">cost</th>
          <th className="num">in</th>
          <th className="num">out</th>
          <th className="num">cache read</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r[by]} data-row={r[by]}>
            <td>{r[by]}</td>
            <td className="num">{r.calls}</td>
            <td className="num">{usd(r.usd)}</td>
            <td className="num">{tokens(r.in_tok)}</td>
            <td className="num">{tokens(r.out_tok)}</td>
            <td className="num">{tokens(r.cache_read)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Costs() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const load = (): void => {
      api.usage().then(
        (u) => {
          if (!live) return;
          setUsage(u);
          setError(null);
        },
        (e: unknown) => {
          if (live) setError(e instanceof ApiError ? e.message : String(e));
        },
      );
    };
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  const level = usage ? creditLevel(usage) : "ok";
  const fill = usage ? Math.min(100, usage.pct) : 0;
  return (
    <div className="view-costs" data-testid="view-costs">
      <div className="view-head">
        <h1 className="page-title">Costs</h1>
        <p className="lede">Month to date against the included API credits, by lane.</p>
      </div>
      {error && <p className="empty">{GLYPH.warn} {error}</p>}
      {!usage && !error && <p className="empty">{GLYPH.progress} Loading the meter</p>}
      {usage && (
        <div className="costs-grid">
          <Panel label="This cycle" testId="credits">
            <div className={`gos-big${level === "err" ? " neg" : ""}`} data-testid="spent">
              {usd(usage.spent_usd)}
            </div>
            <p className="gos-sub">
              of {usd(usage.budget_usd)} included · {pct(usage.pct)} ·{" "}
              {level === "ok" ? (
                <StatusGlyph status="ok" label="within credits" />
              ) : (
                <StatusGlyph status={level} label={`past ${Math.max(...usage.crossed_pct)}%`} />
              )}
            </p>
            <div className="gos-track credit-track" role="img" aria-label={`${pct(usage.pct)} of the included credits`}>
              <span className={`fill ${level}`} style={{ width: `${fill}%` }} />
              {usage.alerts_pct.map((a) => (
                <span key={a} className="mark" style={{ left: `${Math.min(100, a)}%` }} title={`alert at ${a}%`} />
              ))}
            </div>
            <p className="gos-src">
              {new Date(usage.cycle_start).toLocaleDateString()} to {new Date(usage.cycle_end).toLocaleDateString()} ·{" "}
              {usage.calls} calls · alerts at {usage.alerts_pct.join("% and ")}%
            </p>
          </Panel>
          <Panel label="By lane" testId="by-lane">
            <Rows rows={usage.by_lane} by="lane" />
          </Panel>
          <Panel label="By model" testId="by-model">
            <Rows rows={usage.by_model} by="model" />
          </Panel>
        </div>
      )}
    </div>
  );
}
