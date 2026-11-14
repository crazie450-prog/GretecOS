// The breeding station (WO-7.6, plan 7.17 item 4, D282-D284), a panel of the Crops view: what each
// OC station last reported (stale rows marked, never shown as current) and the plan GOS's code
// builds for the crop shown. Start and Stop only propose: GOS reads the action back and Clayton
// confirms it on the approval card or by voice (D59). Designed to the system; needs Clayton's board
// check (D92, D158 (5)).
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { CropStationView } from "../api/types";
import { Button, CitationChip, Panel, type Status, StatusGlyph } from "../components/core";
import { GLYPH } from "../state/states";

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

const STATE: Record<string, Status> = {
  running: "info",
  stopping: "info",
  done: "ok",
  stopped: "ok",
  idle: "ok",
  interrupted: "warn",
  failed: "warn",
};

export function CropStation({ crop }: { crop: string | null }) {
  const [data, setData] = useState<CropStationView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = () =>
      api.cropStation(crop).then(
        (v) => {
          if (!live) return;
          setData(v);
          setError(null);
        },
        (e: unknown) => live && setError(message(e)),
      );
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [crop]);

  const propose = (action: "start" | "stop") => {
    api.cropStationPropose(action, action === "start" ? crop : null).then(
      (r) => setSaid(r.say),
      (e: unknown) => setSaid(`${GLYPH.warn} ${message(e)}`),
    );
  };

  const plan = data?.plan ?? null;
  return (
    <Panel label="Breeding station" testId="crops-station">
      {error && (
        <p className="gos-sub" data-testid="crops-station-error">
          {GLYPH.warn} {error}
        </p>
      )}
      {data && data.status.length === 0 && (
        <p className="gos-sub" data-testid="crops-station-none">
          {GLYPH.waiting} No OC computer with a breeding station has reported.
        </p>
      )}
      {data?.status.map((s) => (
        <div className="crop-station-row" key={s.ref} data-testid="crops-station-row">
          <StatusGlyph
            status={s.stale ? "warn" : (STATE[s.state] ?? "info")}
            label={s.stale ? `${s.state} (stale, ${s.age_s} s old)` : s.state}
          />
          {s.steps ? ` · step ${s.step ?? "-"} of ${s.steps}` : ""} · {s.runs} runs, {s.fail} failed
          {s.target ? ` · ${s.target}: ${s.stock?.[s.target] ?? 0} of ${s.count}` : ""}{" "}
          <CitationChip cite={s.ref} />
          {(s.note || s.doing) && <p className="gos-sub">{s.note ?? s.doing}</p>}
        </div>
      ))}
      {plan && (
        <div data-testid="crops-station-plan">
          <p className="gos-sub">
            Plan {plan.id}: {plan.target_name} in {plan.steps.length} Crop Breeder step(s), stocking {plan.count}.
          </p>
          <ul className="crop-lines">
            {plan.lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </div>
      )}
      {data?.error && (
        <p className="gos-sub" data-testid="crops-station-plan-error">
          {GLYPH.warn} {data.error}
        </p>
      )}
      <div className="crop-station-actions">
        <Button variant="primary" onClick={() => propose("start")} testId="crops-station-start" disabled={!plan}>
          Propose start
        </Button>
        <Button onClick={() => propose("stop")} testId="crops-station-stop">
          Propose stop
        </Button>
      </div>
      {said && (
        <p className="answer-text" data-testid="crops-station-said">
          {said}
        </p>
      )}
      <p className="gos-sub">
        Starting or stopping is a world action: GOS reads it back, and you confirm it on the approval card or by saying
        confirm.
      </p>
    </Panel>
  );
}
