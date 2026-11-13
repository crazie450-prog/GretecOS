// The finished age's recap (D162, D265; WO-6.3): hours played, machines built and best net power,
// every number the core computed (core/gregtec/events/ages.py), shown as written. On the page when
// a tier-up re-themes it (App), and in the Activity view's "Finished ages". Designed to the system
// without a board: needs Clayton's board approval (D92, D158 (5)).
import type { AgeRecap } from "../api/types";
import { Button, CitationChip } from "../components/core";
import { plain } from "../state/format";
import { GLYPH } from "../state/states";

const TIER_AGES = ["LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV"];

/** An age's name (D162): Stone and Steam is age 1, LV 2 ... UV 9, UHV and up 10. */
export function ageName(age: number): string {
  if (age <= 1) return "Stone and Steam";
  if (age >= 10) return "UHV and up";
  return TIER_AGES[age - 2];
}

export function day(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function RecapFigures({ recap }: { recap: AgeRecap }) {
  return (
    <dl className="recap-figures">
      <div>
        <dt className="gos-label">Hours played</dt>
        <dd className="num" data-testid="recap-hours">
          {recap.hours_played} h
        </dd>
        <dd className="gos-sub">
          {recap.sessions} {recap.sessions === 1 ? "session" : "sessions"}
        </dd>
      </div>
      <div>
        <dt className="gos-label">Machines built</dt>
        <dd className="num" data-testid="recap-built">
          {recap.machines_built}
        </dd>
      </div>
      <div>
        <dt className="gos-label">Best net power</dt>
        {recap.best_net_eut === null ? (
          <dd className="gos-sub" data-testid="recap-power">
            {GLYPH.todo} no readings (OC reports power from its station)
          </dd>
        ) : (
          <>
            <dd className="num" data-testid="recap-power">
              {plain(recap.best_net_eut)} EU/t
            </dd>
            {recap.best_ref && (
              <dd>
                <CitationChip cite={recap.best_ref} />
              </dd>
            )}
          </>
        )}
      </div>
    </dl>
  );
}

/** The tier-up moment's panel: the age just finished, until he dismisses it. */
export function TierUpRecap(props: { recap: AgeRecap; reached: number; onClose: () => void }) {
  const { recap } = props;
  return (
    <section className="gos-panel recap-banner" role="status" data-testid="tier-up-recap" data-age={recap.age}>
      <div className="panel-head">
        <span className="gos-label">
          {GLYPH.done} Age finished · {ageName(recap.age)} to {ageName(props.reached)}
        </span>
        <Button onClick={props.onClose} testId="tier-up-recap-close">
          Dismiss
        </Button>
      </div>
      <h2 className="gos-title">The {ageName(recap.age)} age is done</h2>
      <p className="gos-sub">
        {day(recap.start_ts)} to {day(recap.end_ts)}, as GOS saw it. Also in today's journal.
      </p>
      <RecapFigures recap={recap} />
    </section>
  );
}
