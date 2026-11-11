// Settings + Health (plan 7.14): is everything running? Process status, the last export, the
// VERIFY list, re-running doctor, paired devices, and this screen's own look (D81).
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { CoreState, Device, Session, Verify } from "../api/types";
import { Card } from "../cards/Card";
import { Button, Panel, StatusGlyph, TierChip, stageTier } from "../components/core";
import type { ModePin } from "../state/mode";
import { GLYPH } from "../state/states";
import { AGES, COMFORT, type ThemePrefs, reachedAges, theme } from "../theme/themes";
import type { Slug } from "../theme/themes";

function plainText(md: string): string {
  return md.replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");
}

function when(ts: number | null): string {
  return ts ? new Date(ts * 1000).toLocaleString() : "—";
}

/** His own no-AI switch (plan 7.16, WO-2.8): off holds until he turns it back on. */
function AiSwitch(props: { on: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const flip = (): void => {
    setBusy(true);
    setError(null);
    api
      .aiSwitch(!props.on)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setBusy(false));
  };
  return (
    <>
      <Button disabled={busy} onClick={flip} testId="ai-switch">
        {props.on ? "Turn the AI off" : "Turn the AI on"}
      </Button>
      {error && <span className="gos-src">{error}</span>}
    </>
  );
}

function ThemeSettings(props: {
  stage: string | null;
  slug: Slug;
  prefs: ThemePrefs;
  setPrefs: (p: Partial<ThemePrefs>) => void;
  pin: ModePin;
  setPin: (p: ModePin) => void;
}) {
  const reached = new Set(reachedAges(props.stage).map((t) => t.slug));
  return (
    <Panel label="Look · age themes (D81)" testId="theme-settings">
      <p className="gos-sub">
        Now: <strong>{theme(props.slug).name}</strong>. The theme follows your tier; pin any age you have reached,
        or pick a comfort mode.
      </p>
      <div className="theme-grid">
        <Button variant={props.prefs.pinnedAge === null && !props.prefs.comfort ? "primary" : "plain"} onClick={() => props.setPrefs({ pinnedAge: null, comfort: null })} testId="theme-follow">
          Follow my tier
        </Button>
        {AGES.map((t) => {
          const open = reached.has(t.slug);
          const shown = open || props.prefs.revealAges;
          return (
            <Button
              key={t.slug}
              variant={props.prefs.pinnedAge === t.age && !props.prefs.comfort ? "primary" : "plain"}
              disabled={!open}
              onClick={() => props.setPrefs({ pinnedAge: t.age, comfort: null })}
              title={open ? `Pin ${t.name}` : "Reach this age to pin it"}
              testId={`theme-age-${t.age}`}
            >
              <span className={shown ? undefined : "spoiler-text"}>
                {t.age} · {shown ? t.name : "Later age"}
              </span>
            </Button>
          );
        })}
      </div>
      <div className="theme-grid">
        {COMFORT.map((t) => (
          <Button
            key={t.slug}
            variant={props.prefs.comfort === t.slug ? "primary" : "plain"}
            onClick={() => props.setPrefs({ comfort: props.prefs.comfort === t.slug ? null : (t.slug as "calm" | "manual") })}
            testId={`theme-${t.slug}`}
          >
            {t.name}
          </Button>
        ))}
        <Button variant={props.prefs.pauseMotion ? "primary" : "plain"} onClick={() => props.setPrefs({ pauseMotion: !props.prefs.pauseMotion })} testId="pause-motion">
          {props.prefs.pauseMotion ? `${GLYPH.paused} Motion paused` : "Pause motion"}
        </Button>
        <Button variant="plain" onClick={() => props.setPrefs({ revealAges: !props.prefs.revealAges })} testId="reveal-ages">
          {props.prefs.revealAges ? "Hide later ages" : "Show later ages"}
        </Button>
      </div>
      <div className="theme-grid" role="radiogroup" aria-label="Mode">
        {(["auto", "glance", "workbench"] as const).map((p) => (
          <Button key={p} variant={props.pin === p ? "primary" : "plain"} onClick={() => props.setPin(p)} testId={`mode-${p}`}>
            {p === "auto" ? "Mode: automatic" : `Hold ${p}`}
          </Button>
        ))}
      </div>
    </Panel>
  );
}

/** A doctor run from the dashboard (App owns it: the Ctrl+K palette starts one too). */
export interface DoctorRun {
  running: boolean;
  exit?: number;
  output?: string;
  error?: string;
}

/** Run `gos doctor` again through the core, reporting each step to `set`. */
export async function runDoctor(set: (d: DoctorRun) => void): Promise<void> {
  set({ running: true });
  try {
    const r = await api.doctor();
    set({ running: false, exit: r.exit, output: r.output });
  } catch (e) {
    set({ running: false, error: e instanceof ApiError ? e.message : String(e) });
  }
}

export function Settings(props: {
  core: CoreState | null;
  session: Session | null;
  slug: Slug;
  prefs: ThemePrefs;
  setPrefs: (p: Partial<ThemePrefs>) => void;
  pin: ModePin;
  setPin: (p: ModePin) => void;
  doctor: DoctorRun;
  runDoctor: () => void;
}) {
  const trusted = props.session?.role === "admin" || props.session?.role === "device";
  const [verify, setVerify] = useState<Verify | null>(null);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const doctor = props.doctor;

  const load = useCallback(() => {
    api.verify().then(setVerify, () => setVerify(null));
    if (trusted) api.devices().then(setDevices, () => setDevices(null));
  }, [trusted]);
  // On the way in, and again when a doctor run ends (its statuses are new).
  useEffect(() => {
    if (!doctor.running) load();
  }, [doctor.running, load]);

  const revoke = async (id: string): Promise<void> => {
    if (confirmRevoke !== id) {
      setConfirmRevoke(id);
      return;
    }
    setConfirmRevoke(null);
    await api.revokeDevice(id).catch(() => undefined);
    load();
  };

  const core = props.core;
  const data = core?.data;
  return (
    <div className="view-settings" data-testid="view-settings">
      <div className="view-head">
        <h1 className="page-title">Settings + Health</h1>
        <p className="lede">Is everything running?</p>
      </div>
      <div className="settings-grid">
        <div className="col">
          {/* A card is drawn on a panel: in the light-panel themes (Machine GUI, Brushed
              Aluminium) its text colours are for the panel, not the page behind it. */}
          <Panel testId="health">
            {core?.health ? <Card card={core.health} /> : <p className="empty">{GLYPH.progress} Waiting for the health strip</p>}
          </Panel>
          <Panel label="Data · last export" testId="last-export">
            <dl className="facts">
              <dt>Last export</dt>
              <dd>{data?.last_export ? `${data.last_export.run_id} · ${data.last_export.recorded_utc ?? ""}` : "none recorded yet"}</dd>
              <dt>Recipe DB</dt>
              <dd>{data?.db_run ? `${data.db_run.run_key} · ${data.db_run.created_at}` : "not built yet (gos db build)"}</dd>
              <dt>Matches Play</dt>
              <dd>
                {data?.stale === true ? (
                  <StatusGlyph status="warn" label="no: data stale" />
                ) : data?.stale === false ? (
                  <StatusGlyph status="ok" label="yes" />
                ) : (
                  <StatusGlyph status="info" label="not checked yet" />
                )}
              </dd>
              <dt>Tier</dt>
              <dd>
                {core?.tier ? (
                  <>
                    <TierChip tier={stageTier(core.tier.stage)} /> {core.tier.how === "override" ? "your override" : "from quests"}
                  </>
                ) : (
                  "unknown"
                )}
              </dd>
            </dl>
          </Panel>
          <Panel label="Services" testId="services">
            <ul className="services">
              <li>
                <StatusGlyph status={core?.ai.online ? "ok" : "warn"} label={core?.ai.online ? "AI online" : "AI offline"} />
                <span className="gos-src">{core?.ai.detail}</span>
                {core?.ai.reason !== undefined && (core.ai.online || core.ai.reason === "choice") && (
                  <AiSwitch on={core.ai.online} />
                )}
              </li>
              <li>
                <StatusGlyph status={core?.oc.status === "connected" ? "ok" : core?.oc.status === "disconnected" ? "err" : "info"} label={`OC bridge: ${(core?.oc.status ?? "unknown").replace(/_/g, " ")}`} />
                <span className="gos-src">{core?.oc.detail}</span>
              </li>
              <li>
                <StatusGlyph
                  status="info"
                  label={core?.game.running === null ? "Game: can't tell" : core?.game.running?.length ? `Game: ${core.game.running.join(", ")} running` : "Game not running"}
                />
                <span className="gos-src">
                  focus signal: {core?.game.focused === null ? "not available here" : core?.game.focused ? "GTNH in front" : "another window in front"}
                </span>
              </li>
            </ul>
          </Panel>
          <ThemeSettings stage={core?.tier?.stage ?? null} slug={props.slug} prefs={props.prefs} setPrefs={props.setPrefs} pin={props.pin} setPin={props.setPin} />
        </div>
        <div className="col">
          <Panel
            label="VERIFY list · plan 13.2"
            testId="verify"
            aside={
              trusted ? (
                <Button onClick={props.runDoctor} disabled={doctor.running} testId="run-doctor">
                  {doctor.running ? `${GLYPH.progress} Running doctor` : "Re-run doctor"}
                </Button>
              ) : null
            }
          >
            <p className="gos-src">Statuses from doctor's last run{verify?.generated ? ` (${verify.generated})` : ": not run yet"}.</p>
            {doctor.error && <p className="gos-status err">{GLYPH.warn} {doctor.error}</p>}
            {doctor.output !== undefined && (
              <details className="doctor-output" open>
                <summary>doctor exited {doctor.exit}</summary>
                <pre>{doctor.output}</pre>
              </details>
            )}
            <div className="table-wrap verify-table">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Item</th>
                    <th>Status</th>
                    <th>WO</th>
                  </tr>
                </thead>
                <tbody>
                  {(verify?.items ?? []).map((v) => (
                    <tr key={v.id} data-verify={v.id}>
                      <td className="num">{v.id}</td>
                      <td>{plainText(v.item)}</td>
                      <td className="gos-src">{v.status ?? "not checked"}</td>
                      <td className="gos-src">{v.wo}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel label="Paired devices" testId="devices">
            {!trusted && <p className="gos-src">Only paired devices see this list.</p>}
            {trusted && devices === null && <p className="empty">{GLYPH.progress} Loading</p>}
            <ul className="devices">
              {(devices ?? []).map((d) => (
                <li key={d.id} data-device={d.id}>
                  <StatusGlyph status={d.revoked ? "off" : "ok"} label={d.name} />
                  <span className="gos-src">
                    paired {when(d.paired)} · seen {when(d.last_seen)}
                    {d.id === props.session?.device_id ? " · this device" : ""}
                  </span>
                  {!d.revoked && (
                    <Button onClick={() => void revoke(d.id)} testId={`revoke-${d.id}`}>
                      {confirmRevoke === d.id ? "Revoke: sure?" : "Revoke"}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </Panel>
          <Panel label="me/ settings">
            <p className="gos-src">Your ranking weights (vault/me/prefs.md) arrive with the Designer (WO-3.5).</p>
          </Panel>
        </div>
      </div>
    </div>
  );
}
