// Activity (plan 7.5, 7.14: "What happened this session?"; WO-6.3). The event store's sessions
// (from the login line to the quit, a crash or 15 minutes idle), one session's timeline, its counts
// (computed by the core, D142 (f): never added up here), the session scribe's journal lines and
// the finished ages' recaps (D162, D265). Game text (chat, death messages, vein names) is data:
// shown as plain text, never followed. No approved board (D92): designed to the system, needs
// Clayton's board approval (D158 (5)).
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ActivityEvent, ActivitySession, ActivityView } from "../api/types";
import { Button, CitationChip, Panel, StatusGlyph, type Status } from "../components/core";
import { clock } from "../state/format";
import { GLYPH } from "../state/states";
import { RecapFigures, ageName, day } from "./Recap";

export const KIND_LABEL: Record<string, string> = {
  quest_done: "Quest done",
  machine_built: "Machine built",
  machine_removed: "Machine removed",
  death: "Death",
  vein_found: "Vein found",
  dim_change: "Dimension",
  item_delta: "Items",
  block_placed: "Block placed",
  block_broken: "Block broken",
  location: "Moved",
  chat: "Chat",
  game_paused: "Pause",
  crash: "Crash",
  error: "Error",
  export_stale: "Export stale",
  world_closed: "Quit",
};
// The counts table's order: what changed first, the rest after (any kind the core adds goes last).
const COUNT_ORDER = Object.keys(KIND_LABEL);

const DIMS: Record<number, string> = { 0: "Overworld", [-1]: "Nether", 1: "End" };

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

function hm(ts: number): string {
  return clock(new Date(ts * 1000)).hm;
}

function dimName(dim: unknown): string {
  return typeof dim === "number" ? (DIMS[dim] ?? `dim ${dim}`) : "?";
}

function str(v: unknown, max = 200): string {
  const s = typeof v === "string" ? v : v === null || v === undefined ? "" : String(v);
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** One event's words, from its data (game text as it came, cut short). */
export function describe(e: ActivityEvent): string {
  const d = e.data;
  switch (e.kind) {
    case "chat":
      return str(d.text);
    case "death":
      return d.same_as ? `the same death, seen by ${str(d.via) || "another reader"}` : str(d.message) || "died";
    case "vein_found":
      return str(d.name ?? d.mix ?? d.what) || "a vein";
    case "block_placed":
    case "block_broken":
      return `${str(d.block, 80)}${d.meta !== undefined && d.meta !== null ? `:${str(d.meta)}` : ""}`;
    case "machine_built":
    case "machine_removed":
      return str(d.key) || str(d.registry);
    case "item_delta": {
      const items = Array.isArray(d.items) ? (d.items as Record<string, unknown>[]) : [];
      const shown = items
        .slice(0, 4)
        .map((i) => `${str(i.key, 60)} ${typeof i.delta === "number" && i.delta > 0 ? "+" : ""}${str(i.delta)}`);
      const more = items.length > 4 ? ` (+${items.length - 4} more)` : "";
      const what = str(d.action ?? d.via);
      return `${what ? `${what}: ` : ""}${shown.join(", ")}${more}`;
    }
    case "dim_change":
      return `${dimName(d.from)} to ${dimName(d.to)}`;
    case "location":
      return d.via === "login" ? "logged in" : "";
    case "game_paused":
      return d.paused === true ? "paused" : d.paused === false ? "unpaused" : "world closed";
    case "crash":
      return str(d.report) || "the game crashed";
    case "error":
      return str(d.message);
    case "world_closed":
      return "left the world";
    default:
      return "";
  }
}

function where(e: ActivityEvent): string {
  const w = e.where;
  if (!w) return "";
  return `${w.x}${w.y !== null ? `, ${w.y}` : ""}, ${w.z}${w.dim !== null ? ` · ${dimName(w.dim)}` : ""}`;
}

/** How a session began and ended, as a status glyph and a word. */
export function sessionState(s: ActivitySession): { status: Status; label: string } {
  if (s.open) return { status: "ok", label: "playing" };
  switch (s.end_why) {
    case "crash":
      return { status: "error", label: "crashed" };
    case "idle":
      return { status: "off", label: "ended idle" };
    case "relogin":
      return { status: "info", label: "logged in again" };
    default:
      return { status: "ok", label: "quit" };
  }
}

function minutes(s: number): string {
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

function instanceName(i: string | null): string {
  return i === "play" ? "Play" : i === "lab" ? "Lab" : "?";
}

export function countRows(counts: Record<string, number>): [string, number][] {
  return Object.entries(counts).sort(([a], [b]) => {
    const ia = COUNT_ORDER.indexOf(a);
    const ib = COUNT_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
}

function SessionPanel({ s }: { s: ActivitySession }) {
  const state = sessionState(s);
  const rows = countRows(s.counts);
  return (
    <Panel
      label={s.open ? "This session" : `Session s${s.id}`}
      testId="activity-session"
      aside={<StatusGlyph status={state.status} label={state.label} />}
    >
      <p className="gos-sub" data-testid="activity-session-when">
        {instanceName(s.instance)}
        {s.world ? ` · ${s.world}` : ""} · {day(s.start_ts)} <span className="num">{hm(s.start_ts)}</span> to{" "}
        {s.end_ts !== null ? <span className="num">{hm(s.end_ts)}</span> : "now"} ·{" "}
        <span className="num" data-testid="activity-played">
          {minutes(s.played_s)}
        </span>{" "}
        played
      </p>
      {s.start_why === "resume" && (
        <p className="gos-sub">Started from activity, not a login line (back from idle, or GOS started mid-game).</p>
      )}
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table className="base-table" data-testid="activity-counts">
            <thead>
              <tr>
                <th>what happened</th>
                <th className="num">count</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([kind, n]) => (
                <tr key={kind} data-kind={kind}>
                  <td>{KIND_LABEL[kind] ?? kind}</td>
                  <td className="num">{n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty">{GLYPH.todo} Nothing happened yet.</p>
      )}
      <p className="gos-src">Counted by GOS from the event store; a death several readers saw is one death.</p>
    </Panel>
  );
}

function Timeline({ events }: { events: ActivityEvent[] }) {
  if (events.length === 0) return <p className="empty">{GLYPH.todo} No events in this session.</p>;
  return (
    <ol className="activity-timeline" data-testid="activity-events">
      {events.map((e) => {
        const text = describe(e);
        const at = where(e);
        return (
          <li key={e.id} data-kind={e.kind} data-event={e.id}>
            <span className="num activity-time">{hm(e.ts)}</span>
            <span className="activity-kind">{KIND_LABEL[e.kind] ?? e.kind}</span>
            <span className="activity-what">
              {text && <span className={e.kind === "chat" ? "activity-game-text" : undefined}>{text}</span>}
              {e.ref && <CitationChip cite={e.ref} />}
              {at && <span className="gos-sub activity-where">{at}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function Activity() {
  const [data, setData] = useState<ActivityView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    const load = () =>
      api.activity(picked).then(
        (v) => {
          if (!live) return;
          setData(v);
          setError(null);
        },
        (e: unknown) => live && setError(message(e)),
      );
    load();
    const t = setInterval(load, 15_000); // an open session goes on
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [picked]);

  const s = data?.session ?? null;
  return (
    <div className="view-activity" data-testid="view-activity">
      <div className="view-head">
        <h1 className="page-title">Activity</h1>
        <p className="lede">What happened this session? Every event the game watchers saw, in sessions from the login line to quitting.</p>
      </div>
      {error && (
        <p className="empty" data-testid="activity-empty">
          {GLYPH.waiting} {error}
        </p>
      )}
      {!data && !error && <p className="empty">{GLYPH.progress} Loading the sessions</p>}
      {data && (
        <div className="activity-grid">
          <div className="col">
            {s ? (
              <SessionPanel s={s} />
            ) : (
              <Panel label="This session" testId="activity-session">
                <p className="empty">{GLYPH.waiting} No session yet: one starts when the game watchers see the login line.</p>
              </Panel>
            )}
            <Panel
              label="Timeline"
              testId="activity-timeline"
              aside={<span className="gos-src">newest first · game text is shown as written</span>}
            >
              <Timeline events={data.events} />
            </Panel>
          </div>
          <div className="col">
            <Panel label="Sessions" testId="activity-sessions">
              {data.sessions.length === 0 ? (
                <p className="empty">{GLYPH.todo} No sessions yet.</p>
              ) : (
                <ul className="activity-sessions">
                  {data.sessions.map((r) => {
                    const st = sessionState(r);
                    return (
                      <li key={r.id} className={s && r.id === s.id ? "active" : undefined}>
                        <Button onClick={() => setPicked(r.id)} testId={`activity-pick-${r.id}`}>
                          s{r.id} · {instanceName(r.instance)} · {day(r.start_ts)} {hm(r.start_ts)}
                        </Button>
                        <span className="gos-sub">
                          {minutes(r.played_s)} · <StatusGlyph status={st.status} label={st.label} />
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
              {data.outside_sessions > 0 && (
                <p className="gos-sub">{data.outside_sessions} events came with no session (the game was closed).</p>
              )}
            </Panel>
            <Panel label="Journal" testId="activity-journal" aside={<span className="gos-src">written by GOS</span>}>
              {s && s.instance !== "play" ? (
                <p className="empty">{GLYPH.todo} Lab sessions stay out of the journal: it is Play's.</p>
              ) : data.journal && data.journal.lines.length > 0 ? (
                <>
                  <ul className="activity-journal" data-testid="activity-journal-lines">
                    {data.journal.lines.map((line, i) => (
                      <li key={`${i}-${line}`}>{line}</li>
                    ))}
                  </ul>
                  <p className="gos-src">{data.journal.paths.join(", ")}</p>
                </>
              ) : (
                <p className="empty">{GLYPH.waiting} Not written yet: once an hour while you play, and when the session ends.</p>
              )}
            </Panel>
            <Panel label="Finished ages" testId="activity-ages">
              {data.recaps.length === 0 ? (
                <p className="empty">{GLYPH.todo} No age finished yet. A tier-up keeps the age's recap here.</p>
              ) : (
                data.recaps.map((r) => (
                  <div key={r.age} className="activity-recap" data-age={r.age}>
                    <h3 className="gos-title">{ageName(r.age)}</h3>
                    <p className="gos-sub">
                      {day(r.start_ts)} to {day(r.end_ts)}
                    </p>
                    <RecapFigures recap={r} />
                  </div>
                ))
              )}
            </Panel>
            {data.problems.length > 0 && (
              <Panel label="Problems" testId="activity-problems">
                {data.problems.map((p) => (
                  <p key={p} className="gos-sub">
                    {GLYPH.warn} {p}
                  </p>
                ))}
              </Panel>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
