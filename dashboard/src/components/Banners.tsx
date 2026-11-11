// The banners above every view (WO-2.8, plan 7.16, D48), in glance and workbench and on the
// phone: the credit alert (a "needs you" panel with D48's buttons: Open billing, No-AI mode or
// Resume AI, Stop) and "AI offline" (what still works and what needs the AI). Designed to the
// system without a board: needs Clayton's board approval (D92, D158).
import { useState } from "react";
import type { CreditChoice } from "../api/types";
import { ACTION_LABEL, type AiBanner, type Banner, type CreditBanner, NEEDS_AI } from "../state/banners";
import { GLYPH } from "../state/states";
import { Button } from "./core";

function CreditView(props: { b: CreditBanner; decide: (c: CreditChoice) => void; busy: boolean }) {
  const { b } = props;
  const [askStop, setAskStop] = useState(false);
  return (
    <section className="gos-approval banner" data-testid="banner-credits" data-level={b.level} data-paused={b.paused}>
      <div className="gos-rail-head">
        <span className="gos-label approval-head">
          <span className="needs-glyph" aria-hidden="true">
            {GLYPH.warn}
          </span>{" "}
          Waiting for you · API credits
        </span>
      </div>
      <h3 className="gos-title approval-title">{b.title}</h3>
      <p className="gos-sub">{b.body}</p>
      {askStop ? (
        <div className="actions">
          <span className="gos-sub">Stop GOS? It stays stopped until you start it again (gos serve).</span>
          <Button variant="confirm" disabled={props.busy} onClick={() => props.decide("stop")} testId="credit-stop-confirm">
            Stop GOS
          </Button>
          <Button onClick={() => setAskStop(false)} testId="credit-stop-cancel">
            Cancel
          </Button>
        </div>
      ) : (
        <div className="actions">
          {b.actions.map((a) => (
            <Button
              key={a}
              variant={a === "billing" ? "primary" : "plain"}
              disabled={props.busy}
              onClick={() => {
                if (a === "stop") setAskStop(true);
                else {
                  if (a === "billing") window.open(b.billingUrl, "_blank", "noopener,noreferrer");
                  props.decide(a);
                }
              }}
              testId={`credit-${a}`}
            >
              {ACTION_LABEL[a]}
            </Button>
          ))}
        </div>
      )}
    </section>
  );
}

function AiView(props: { b: AiBanner; turnOn: () => void; busy: boolean }) {
  const { b } = props;
  return (
    <section className="gos-panel banner banner-ai" data-testid="banner-ai" role="status">
      <span className="gos-label banner-head">
        <span className="banner-glyph" aria-hidden="true">
          {GLYPH.warn}
        </span>{" "}
        {b.title}
      </span>
      <p className="gos-sub">{b.body}</p>
      <p className="gos-sub">Still works: {b.works}.</p>
      <p className="gos-sub">{NEEDS_AI}</p>
      {b.canTurnOn && (
        <div className="actions">
          <Button variant="primary" disabled={props.busy} onClick={props.turnOn} testId="ai-turn-on">
            Turn the AI on
          </Button>
        </div>
      )}
    </section>
  );
}

export function Banners(props: {
  banners: Banner[];
  decide: (c: CreditChoice) => Promise<void>;
  turnOn: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  if (!props.banners.length) return null;
  const run = (fn: () => Promise<void>): void => {
    setBusy(true);
    fn().finally(() => setBusy(false));
  };
  return (
    <div className="banners" data-testid="banners">
      {props.banners.map((b) =>
        b.kind === "credits" ? (
          <CreditView key="credits" b={b} busy={busy} decide={(c) => run(() => props.decide(c))} />
        ) : (
          <AiView key="ai" b={b} busy={busy} turnOn={() => run(props.turnOn)} />
        ),
      )}
    </div>
  );
}
