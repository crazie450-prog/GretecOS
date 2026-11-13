// The Base view's live half (WO-7.4; plan 7.14 Base: "What's broken?", stalled machines first,
// pending actions): OC telemetry as the core reads it (GET /api/base/live, gregtec.bridge.live),
// refreshed every LIVE_POLL_MS, so a change shows within 10 s of the core hearing it. Power,
// machines, problems and AE2 crafting per instance (Play first), each number with its tel:
// citation and age. A computer that went quiet (the game paused, the agent stopped) is shown as
// stale with its last readings greyed, never extrapolated (plan 7.9). Built from the design system
// (Panel, StatusGlyph, CitationChip, tables); no approved board (D92): needs Clayton's approval
// (D158 (5)).
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ActionRow, LiveAlert, LiveBase, LiveBinding, LiveMachine, LivePower, LiveProblem, LiveStatus } from "../api/types";
import { CitationChip, Panel, StatusGlyph, type Status } from "../components/core";
import { GLYPH } from "../state/states";

export const LIVE_POLL_MS = 5000;

const MACHINE_STATUS: Record<LiveStatus, { status: Status; label: string; glyph: string }> = {
  stopped: { status: "warn", label: "stopped", glyph: GLYPH.warn },
  stalled: { status: "warn", label: "stalled", glyph: GLYPH.warn },
  paused: { status: "off", label: "paused", glyph: GLYPH.paused },
  unknown: { status: "info", label: "no reading", glyph: GLYPH.waiting },
  stale: { status: "off", label: "stale", glyph: GLYPH.waiting },
  idle: { status: "info", label: "idle", glyph: GLYPH.todo },
  running: { status: "ok", label: "running", glyph: GLYPH.ok },
};

const PROBLEM_STATUS: Record<LiveProblem["level"], Status> = { bad: "err", warn: "warn", info: "info" };

const PLACE: Record<string, string> = { play: "Play", lab: "Lab" };

/** An EU amount readable at a glance: grouped below a million, then 3 significant figures. */
export function euText(n: number | string | null | undefined): string {
  if (n === null || n === undefined) return "–";
  if (typeof n === "string") {
    if (!/^\d+$/.test(n)) return "–";
    const digits = n.replace(/^0+(?=\d)/, "");
    if (digits.length <= 6) return Number(digits).toLocaleString("en-US");
    return `${digits[0]}.${digits.slice(1, 3)}e${digits.length - 1}`;
  }
  if (Math.abs(n) < 1e6) return Math.round(n).toLocaleString("en-US");
  return n.toExponential(2).replace("e+", "e");
}

export function ageText(s: number | null | undefined): string {
  if (s === null || s === undefined) return "never";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

function Problems(props: { problems: LiveProblem[]; status: LiveBinding["status"] }) {
  if (props.problems.length === 0) {
    return (
      <p className="empty" data-testid="live-no-problems">
        {GLYPH.ok} No problems reported.
      </p>
    );
  }
  return (
    <ul className="live-problems" data-testid="live-problems">
      {props.problems.map((p, i) => (
        <li key={`${p.cite}-${p.kind}-${i}`} data-kind={p.kind} data-level={p.level}>
          <StatusGlyph status={PROBLEM_STATUS[p.level]} label={p.level === "bad" ? "problem" : p.level === "warn" ? "check" : "note"} />{" "}
          <span className="base-name">{p.text}</span> <CitationChip cite={p.cite} />
          {p.age_s !== undefined && <span className="gos-src"> {ageText(p.age_s)}</span>}
        </li>
      ))}
    </ul>
  );
}

const ALERT_STATUS: Record<LiveAlert["level"], Status> = { err: "err", warn: "warn", info: "ok" };

/** WO-7.5's alerts for this instance: firing ones (held ones marked as such) and crafting done. */
function Alerts(props: { alerts: LiveAlert[]; held: LiveBinding["held"] }) {
  if (props.alerts.length === 0) {
    return (
      <p className="empty" data-testid="live-no-alerts">
        {GLYPH.ok} No alerts.
      </p>
    );
  }
  return (
    <>
      {props.held && (
        <p className="empty" data-testid="live-alerts-held">
          {GLYPH.paused} Alerts held: {props.held === "paused" ? "the game is paused" : "OC is silent"}. Nothing fires or clears until
          fresh readings come.
        </p>
      )}
      <ul className="live-problems" data-testid="live-alerts">
        {props.alerts.map((a) => (
          <li key={a.key} data-alert={a.rule} data-level={a.level} data-stale={a.stale ?? undefined}>
            <StatusGlyph status={a.stale ? "off" : ALERT_STATUS[a.level]} label={a.stale ? "held" : a.level === "info" ? "done" : "alert"} />{" "}
            <span className="base-name">{a.title}</span> <CitationChip cite={a.cite} />
          </li>
        ))}
      </ul>
    </>
  );
}

function PowerTable(props: { power: LivePower[]; net: number | null }) {
  if (props.power.length === 0) return <p className="empty">{GLYPH.todo} No power readings: no battery buffer or LSC on this computer.</p>;
  return (
    <div className="table-wrap">
      <table className="base-table live-table" data-testid="live-power">
        <thead>
          <tr>
            <th>store</th>
            <th className="num">full</th>
            <th className="num">EU</th>
            <th className="num">in EU/t</th>
            <th className="num">out EU/t</th>
          </tr>
        </thead>
        <tbody>
          {props.power.map((p) => (
            <tr key={p.id} data-power={p.id} className={p.stale ? "stale" : undefined}>
              <td>
                <span className="base-name">{p.name}</span> <CitationChip cite={p.cite} />
              </td>
              <td className="num">{p.pct === null ? "–" : `${p.pct.toFixed(1)}%`}</td>
              <td className="num" title={p.eu_str ?? (p.eu ?? undefined)?.toString()}>
                {euText(p.kind === "storage" ? p.eu_str : p.eu)}
              </td>
              <td className="num">{euText(p.eu_in)}</td>
              <td className="num">{euText(p.eu_out)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {props.net !== null && (
        <p className="gos-src" data-testid="live-net">
          Net at the store: <span className="num">{props.net > 0 ? "+" : ""}{euText(props.net)}</span> <span className="unit">EU/t</span>
        </p>
      )}
    </div>
  );
}

function progressText(m: LiveMachine): string {
  if (m.progress === null || m.progress_max === null || m.progress_max <= 0) return "–";
  return `${(m.progress / 20).toFixed(0)} / ${(m.progress_max / 20).toFixed(0)} s`;
}

function MachineTable(props: { machines: LiveMachine[] }) {
  if (props.machines.length === 0) return <p className="empty">{GLYPH.todo} No GT machines on this computer's adapters.</p>;
  return (
    <div className="table-wrap">
      <table className="base-table live-table" data-testid="live-machines">
        <thead>
          <tr>
            <th>machine</th>
            <th>status</th>
            <th className="num">progress</th>
            <th className="num">problems</th>
          </tr>
        </thead>
        <tbody>
          {props.machines.map((m) => {
            const s = MACHINE_STATUS[m.status] ?? MACHINE_STATUS.unknown;
            return (
              <tr key={m.id} data-machine-live={m.id} data-status={m.status} className={m.stale ? "stale" : undefined}>
                <td>
                  <span className="base-name">{m.name}</span> <CitationChip cite={m.cite} />
                </td>
                <td>
                  <StatusGlyph status={s.status} label={s.label} glyph={s.glyph} />
                </td>
                <td className="num">{progressText(m)}</td>
                <td className="num">{m.problems === null ? "–" : m.problems}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const CRAFT_WORD: Record<string, string> = {
  computing: "planning",
  crafting: "crafting",
  done: "done",
  canceled: "cancelled",
  failed: "failed",
  check_ae2: "check AE2 (may still run)",
};

function Ae2(props: { ae2: NonNullable<LiveBinding["ae2"]> }) {
  const { ae2 } = props;
  return (
    <div className="live-ae2" data-testid="live-ae2">
      <p className="gos-src">
        Crafting CPUs: <span className="num">{ae2.busy}</span> of <span className="num">{ae2.cpus}</span> busy
      </p>
      {ae2.crafts.length === 0 ? (
        <p className="empty">{GLYPH.todo} No crafts GOS started are running.</p>
      ) : (
        <ul className="live-problems">
          {ae2.crafts.map((c) => (
            <li key={c.id} data-craft={c.id}>
              <StatusGlyph
                status={c.state === "failed" || c.state === "check_ae2" ? "warn" : c.state === "done" ? "ok" : "info"}
                label={CRAFT_WORD[c.state] ?? c.state}
              />{" "}
              <span className="num">{c.amount}</span> <span className="base-name">{c.name}</span> <CitationChip cite={c.cite} />
            </li>
          ))}
        </ul>
      )}
      {ae2.items.length > 0 && (
        <div className="table-wrap">
          <table className="base-table live-table">
            <thead>
              <tr>
                <th>watched</th>
                <th className="num">in AE2</th>
              </tr>
            </thead>
            <tbody>
              {ae2.items.map((it) => (
                <tr key={it.key}>
                  <td>
                    <span className="base-name">{it.name}</span> <CitationChip cite={it.cite} />
                  </td>
                  <td className="num">{it.size === null ? "–" : it.size.toLocaleString("en-US")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function BindingView(props: { b: LiveBinding }) {
  const { b } = props;
  const place = PLACE[b.binding] ?? b.binding;
  const transports = [...new Set(b.agents.map((a) => (a.transport === "http" ? "M2 HTTP" : "M1 mailbox")))].join(", ");
  return (
    <section className="live-binding" data-binding={b.binding} data-status={b.status} data-testid={`live-${b.binding}`}>
      <div className="live-head">
        <h2 className="gos-label">{place}</h2>
        {b.status === "stale" ? (
          <StatusGlyph status="warn" label={`silent, ${ageText(b.age_s)}: readings are stale`} />
        ) : (
          <StatusGlyph status="ok" label={`live, ${ageText(b.age_s)}`} />
        )}
        <span className="gos-src">
          {b.agents.length} OC {b.agents.length === 1 ? "computer" : "computers"} · {transports}
        </span>
      </div>
      {b.status === "stale" && (
        <p className="empty" data-testid="live-stale">
          {GLYPH.waiting} No OC report for {b.age_s} s: the game may be paused (Esc or alt-tab) or the agent stopped. These are the last
          readings, not current ones.
        </p>
      )}
      <div className="live-grid">
        <div className="col">
          <span className="gos-label">Alerts</span>
          <Alerts alerts={b.alerts ?? []} held={b.held ?? null} />
          <span className="gos-label">Problems · stopped and stalled first</span>
          <Problems problems={b.problems} status={b.status} />
          {b.ae2 && (
            <>
              <span className="gos-label">AE2</span>
              <Ae2 ae2={b.ae2} />
            </>
          )}
        </div>
        <div className="col">
          <span className="gos-label">Power</span>
          <PowerTable power={b.power} net={b.net_eu_t} />
        </div>
        <div className="col">
          <span className="gos-label">Machines</span>
          <MachineTable machines={b.machines} />
        </div>
      </div>
    </section>
  );
}

function Actions(props: { pending: ActionRow | null; recent: ActionRow[] }) {
  const recent = props.recent.filter((a) => a.id !== props.pending?.id).slice(0, 5);
  return (
    <div className="live-actions" data-testid="live-actions">
      <span className="gos-label">World actions</span>
      {props.pending ? (
        <p data-testid="live-pending">
          <StatusGlyph status="warn" label="waiting for you" /> <span className="base-name">{props.pending.readback}</span>{" "}
          <span className="gos-src">Say confirm, or use its card in the right rail.</span>
        </p>
      ) : (
        <p className="empty">{GLYPH.todo} Nothing waiting for your confirmation.</p>
      )}
      {recent.length > 0 && (
        <ul className="live-problems">
          {recent.map((a) => (
            <li key={a.id} data-action={a.id}>
              <StatusGlyph status={a.state === "done" ? "ok" : a.state === "failed" ? "err" : "info"} label={a.state} />{" "}
              <span className="base-name">{a.readback}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function BaseLivePanel() {
  const [live, setLive] = useState<LiveBase | null>(null);
  const [actions, setActions] = useState<{ pending: ActionRow | null; recent: ActionRow[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    const load = (): void => {
      api.baseLive().then(
        (l) => {
          if (!on) return;
          setLive(l);
          setError(null);
        },
        (e: unknown) => on && setError(e instanceof ApiError ? e.message : String(e)),
      );
      api.actions().then(
        (a) => on && setActions(a),
        () => on && setActions(null),
      );
    };
    load();
    const timer = window.setInterval(load, LIVE_POLL_MS);
    return () => {
      on = false;
      window.clearInterval(timer);
    };
  }, []);
  const bindings = live?.bindings ?? [];
  return (
    <Panel label="Base · live" testId="base-live" aside={<span className="gos-src">from OpenComputers, every {LIVE_POLL_MS / 1000} s</span>}>
      {error && (
        <p className="empty" data-testid="live-error">
          {GLYPH.warn} {error}
        </p>
      )}
      {!live && !error && <p className="empty">{GLYPH.progress} Loading the live base</p>}
      {live && bindings.length === 0 && (
        <p className="empty" data-testid="live-not-set-up">
          {GLYPH.waiting} No OC computer has reported yet. Power, machines, problems and crafting show here once gos-agent runs in game
          (from MV: an OC computer and an adapter; docs/runbooks/oc_agent.md).
        </p>
      )}
      {bindings.map((b) => (
        <BindingView key={b.binding} b={b} />
      ))}
      {actions && <Actions pending={actions.pending} recent={actions.recent} />}
    </Panel>
  );
}
