// Glance mode (plan 7.14, D49): what matters, in large type, readable from the game. Every block
// is clickable and opens its view; moving the mouse onto monitor 2 switches to workbench anyway.
import type { Answer, CoreState } from "../api/types";
import type { ApprovalCard } from "../cards/types.gen";
import { AlertView, ApprovalView } from "../components/core";
import { CoreArt, type VoiceLook } from "../components/CoreArt";
import { GLYPH } from "../state/states";
import type { Slug } from "../theme/themes";
import type { ViewId } from "./nav";
import { AnswerBlock, NextSteps, PowerEmpty } from "./Now";

function Block(props: { label: string; onOpen: () => void; children: React.ReactNode; testId: string; className?: string }) {
  return (
    <section className={`gos-panel glance-block ${props.className ?? ""}`} data-testid={props.testId}>
      <button type="button" className="glance-open" onClick={props.onOpen} aria-label={`Open ${props.label}`}>
        <span className="gos-label">{props.label}</span>
        <span className="gos-src" aria-hidden="true">
          open {"›"}
        </span>
      </button>
      {props.children}
    </section>
  );
}

export function Glance(props: {
  core: CoreState | null;
  answers: Answer[];
  slug: Slug;
  look: VoiceLook;
  motion: boolean;
  nextCard: number;
  open: (view: ViewId) => void;
  act: (card: ApprovalCard, action: string) => void;
}) {
  const core = props.core;
  const last = props.answers.length ? props.answers[props.answers.length - 1] : null;
  const asking = core?.asking.length ? core.asking[core.asking.length - 1].text : null;
  const alerts = core?.alerts ?? [];
  // The "next card" hotkey (WO-5.1) steps back through earlier answers.
  const recent = props.answers.slice(-5);
  const shown = recent.length ? recent[(recent.length - 1 - (props.nextCard % recent.length) + recent.length) % recent.length] : last;
  return (
    <div className="view-glance" data-testid="view-glance">
      <Block label={asking ? "Thinking" : "Latest answer"} onOpen={() => props.open("chat")} testId="glance-answer" className="g-answer">
        <AnswerBlock answer={shown} asking={asking} big />
      </Block>
      <div className="g-side">
        {core?.approvals.length ? (
          <div className="glance-approvals" data-testid="glance-approvals">
            {core.approvals.slice(0, 2).map((a) => (
              <ApprovalView key={a.id} card={a} onAction={props.act} />
            ))}
          </div>
        ) : null}
        <Block label="Alerts" onOpen={() => props.open("now")} testId="glance-alerts">
          {alerts.length ? (
            alerts.slice(0, 2).map((a) => <AlertView key={a.id} card={a} />)
          ) : (
            <p className="empty">{GLYPH.ok} All clear</p>
          )}
        </Block>
        <Block label="Net power · per tier" onOpen={() => props.open("power")} testId="glance-power">
          <PowerEmpty />
        </Block>
        <Block label="Goal · next step" onOpen={() => props.open("quests")} testId="glance-goal">
          <NextSteps compact />
        </Block>
      </div>
      <div className="g-core" aria-hidden="true">
        <CoreArt slug={props.slug} state={props.look} size={200} motion={props.motion} />
      </div>
    </div>
  );
}
