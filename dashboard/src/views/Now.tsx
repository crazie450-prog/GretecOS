// Now, the home view (plan 7.14, D55): ranked by context. Alerts first when something is wrong,
// the last answer right after a question, otherwise the next steps; then power and jobs. Data
// that doesn't exist yet gets its designed empty state.
import type { ReactNode } from "react";
import type { Answer, CoreState } from "../api/types";
import { Card } from "../cards/Card";
import type { ApprovalCard, TableCard } from "../cards/types.gen";
import { AlertView, CitationChip, Panel, StatusGlyph } from "../components/core";
import { Approvals, Jobs } from "../components/Rail";
import { type Section, rankNow } from "../state/now";
import { GLYPH } from "../state/states";
import { useApp } from "../state/store";
import { useTool } from "./useTool";

export function AnswerBlock({ answer, asking, big }: { answer: Answer | null; asking: string | null; big?: boolean }) {
  if (asking) {
    return (
      <div className="answer thinking" data-testid="now-asking">
        <StatusGlyph status="info" glyph={GLYPH.progress} label="Thinking about" />
        <p className="quote">"{asking}"</p>
      </div>
    );
  }
  if (!answer) return <p className="empty">{GLYPH.todo} No question yet. Ask by voice, or type in the strip below.</p>;
  return (
    <div className="answer" data-testid="now-answer">
      <p className="gos-src">"{answer.request}"</p>
      <p className={big ? "answer-text big" : "answer-text"}>{answer.text}</p>
      <div className="answer-meta">
        <span className="gos-chip">{answer.lane}</span>
        {answer.checked ? (
          <StatusGlyph status="ok" glyph={GLYPH.done} label="numbers checked" />
        ) : (
          <StatusGlyph status="warn" label="numbers not checked" />
        )}
      </div>
      {answer.card && <Card card={answer.card} compact={!big} />}
    </div>
  );
}

const CITE = /^(r|q|mte|crop|plan|w|vein|pos|tel):\S+$/; // tel: an OC reading (WO-7.1)

export interface AdvisorStep {
  rank: string;
  kind: string;
  title: string;
  why: string[];
  refs: string[];
  link: string;
}

/** The advisor's steps from its table card (advisor_next, WO-6.5), in GOS's order: never sorted
 * here (the ranking is code's, D266). */
export function advisorSteps(card: TableCard): AdvisorStep[] {
  const col = (name: string) => card.columns.indexOf(name);
  const idx = ["rank", "kind", "step", "why", "refs", "link"].map(col);
  if (idx.some((i) => i < 0)) return [];
  const [rank, kind, step, why, refs, link] = idx;
  const split = (text: string, sep: string) =>
    text
      .split(sep)
      .map((t) => t.trim())
      .filter(Boolean);
  return card.rows.map((r) => ({
    rank: r[rank] ?? "",
    kind: r[kind] ?? "",
    title: r[step] ?? "",
    why: split(r[why] ?? "", "; "),
    refs: split(r[refs] ?? "", ", "),
    link: (r[link] ?? "").startsWith("#/") ? (r[link] ?? "") : "#/now",
  }));
}

export function NextSteps({ compact }: { compact?: boolean }) {
  const notices = useApp((s) => s.notices); // a notice asks the advisor again
  const advice = useTool("advisor_next", { n: 3 }, notices);
  if (advice.loading && !advice.card) return <p className="empty">{GLYPH.progress} Working out your next steps</p>;
  const card = advice.card;
  if (!card || card.type !== "table" || card.error) {
    return (
      <p className="empty">
        {GLYPH.todo} No advice yet. Load Play's quest book (<code>gos quests load</code>), add goals in Settings or accept a
        plan in the Designer.
      </p>
    );
  }
  const steps = advisorSteps(card);
  if (steps.length === 0) {
    return (
      <p className="empty" data-testid="steps-empty">
        {GLYPH.todo} {card.notes[card.notes.length - 1] ?? "Nothing to suggest yet."}
      </p>
    );
  }
  return (
    <ol className="advisor-steps" data-testid="advisor-steps">
      {steps.map((s) => (
        <li key={s.rank} className="advisor-step" data-kind={s.kind}>
          <div className="advisor-step-head">
            <span className="gos-label">{s.rank}</span>
            <a className="gos-title advisor-step-title" href={s.link} data-testid={`advisor-step-${s.rank}`}>
              {s.title}
            </a>
          </div>
          {!compact && s.why.length > 0 && (
            <ul className="gos-sub advisor-why">
              {s.why.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {s.refs.length > 0 && (
            <div className="answer-meta">
              {s.refs
                .filter((r) => CITE.test(r))
                .map((r) => (
                  <CitationChip key={r} cite={r} />
                ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

export function PowerEmpty() {
  return (
    <p className="empty" data-testid="power-empty">
      {GLYPH.waiting} No power data yet. The EU ledger arrives with WO-4.4; live OC readings with WO-7.1.
    </p>
  );
}

const TITLES: Record<Section, string> = {
  approvals: "Waiting for you",
  alerts: "Alerts",
  answer: "Last answer",
  steps: "Next steps",
  power: "Net power · per tier",
  jobs: "Running jobs",
};

export function Now(props: {
  core: CoreState | null;
  answers: Answer[];
  phone: boolean;
  nowS: number;
  act: (card: ApprovalCard, action: string) => void;
}) {
  const core = props.core;
  const last = props.answers.length ? props.answers[props.answers.length - 1] : null;
  const asking = core?.asking.length ? core.asking[core.asking.length - 1].text : null;
  const rank = rankNow({
    alerts: core?.alerts ?? [],
    lastAnswer: last,
    asking: asking !== null,
    nowS: props.nowS,
    phone: props.phone,
    approvals: core?.approvals.length ?? 0,
  });
  const lede =
    rank.focus === "alerts" && core?.alerts.length
      ? `Alerts first: ${core.alerts[0].title}.`
      : rank.focus === "answer"
        ? "Right after your question: the answer first."
        : "Nothing is wrong. Here is what you can do next.";

  const body = (s: Section, first: boolean): ReactNode => {
    switch (s) {
      case "approvals":
        return <Approvals core={core} act={props.act} />;
      case "alerts":
        return core?.alerts.length ? (
          <div className="alerts">
            {core.alerts.map((a) => (
              <AlertView key={a.id} card={a} big={first} />
            ))}
          </div>
        ) : (
          <p className="empty">{GLYPH.ok} All clear</p>
        );
      case "answer":
        return <AnswerBlock answer={last} asking={asking} big={first} />;
      case "steps":
        return <NextSteps compact={!first} />;
      case "power":
        return <PowerEmpty />;
      case "jobs":
        return <Jobs core={core} />;
    }
  };

  const [a, b, ...rest] = rank.order;
  return (
    <div className="view-now" data-testid="view-now" data-focus={rank.focus}>
      <div className="view-head">
        <h1 className="page-title">Now</h1>
        <p className="lede">{lede}</p>
      </div>
      <div className="now-grid">
        <Panel label={TITLES[a]} className="slot slot-a" testId="slot-a">
          <div className="slot-body">{body(a, true)}</div>
        </Panel>
        <Panel label={TITLES[b]} className="slot slot-b" testId="slot-b">
          <div className="slot-body">{body(b, false)}</div>
        </Panel>
        <Panel className="slot slot-c" testId="slot-c">
          <div className="slot-body">
            {rest.map((s) => (
              <div key={s} className="stacked" data-section={s}>
                <span className="gos-label">{TITLES[s]}</span>
                {body(s, false)}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
