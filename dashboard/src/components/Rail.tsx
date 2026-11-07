// The right rail, always there in workbench mode (plan 7.14): what the agent is doing, what is
// waiting for Clayton, and the jobs that run.
import type { ApprovalCard } from "../cards/types.gen";
import type { CoreState } from "../api/types";
import { GLYPH } from "../state/states";
import { ApprovalView, StatusGlyph } from "./core";

export function Activity({ core }: { core: CoreState | null }) {
  const asking = core?.asking ?? [];
  const running = (core?.jobs ?? []).filter((j) => j.kind === "deep" && j.status === "running");
  if (!asking.length && !running.length) {
    return <p className="gos-src idle">{GLYPH.ok} Idle · ask by voice or type below</p>;
  }
  return (
    <ul className="activity">
      {asking.map((a) => (
        <li key={a.id}>
          <StatusGlyph status="info" glyph={GLYPH.progress} label="Thinking about" />
          <span className="quote">"{a.text}"</span>
        </li>
      ))}
      {running.map((j) => (
        <li key={j.id}>
          <StatusGlyph status="info" glyph={GLYPH.progress} label="Deep lane" />
          <span className="quote">{j.detail ?? j.title}</span>
        </li>
      ))}
    </ul>
  );
}

export function Jobs({ core }: { core: CoreState | null }) {
  const jobs = core?.jobs ?? [];
  if (!jobs.length) return <p className="gos-src">{GLYPH.todo} No jobs running</p>;
  return (
    <ul className="jobs">
      {jobs.map((j) => (
        <li key={j.id} data-job={j.id}>
          <StatusGlyph
            status={j.status === "needs_approval" ? "warn" : "info"}
            glyph={j.status === "queued" ? GLYPH.todo : j.status === "needs_approval" ? GLYPH.paused : GLYPH.progress}
            label={j.status.replace("_", " ")}
          />
          <span className="job-title">{j.title}</span>
          {j.detail && <span className="gos-src">{j.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

export function Approvals(props: { core: CoreState | null; act: (card: ApprovalCard, action: string) => void }) {
  const approvals = props.core?.approvals ?? [];
  if (!approvals.length) return <p className="gos-src">{GLYPH.done} Nothing is waiting for you</p>;
  return (
    <div className="approvals">
      {approvals.map((a) => (
        <ApprovalView key={a.id} card={a} onAction={props.act} />
      ))}
    </div>
  );
}

export function Rail(props: { core: CoreState | null; act: (card: ApprovalCard, action: string) => void }) {
  const waiting = props.core?.approvals.length ?? 0;
  return (
    <aside className="gos-rail shell-rail" data-testid="rail">
      <div className="rail-section">
        <div className="gos-rail-head">
          <span className="gos-label">Agent</span>
        </div>
        <Activity core={props.core} />
      </div>
      <div className="rail-section" data-testid="rail-approvals">
        <div className="gos-rail-head">
          <span className={`gos-label${waiting ? " gos-needs-h" : ""}`}>Waiting for you{waiting ? ` · ${waiting}` : ""}</span>
        </div>
        <Approvals core={props.core} act={props.act} />
      </div>
      <div className="rail-section">
        <div className="gos-rail-head">
          <span className="gos-label">Jobs</span>
        </div>
        <Jobs core={props.core} />
      </div>
    </aside>
  );
}
