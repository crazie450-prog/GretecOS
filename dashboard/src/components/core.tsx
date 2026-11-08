// The design system's nine core components (docs/design/system/README.md): Panel, AlertCard,
// ApprovalCard, TierChip, PowerRow, StatusGlyph, CitationChip, Button and NavItem. Each reads only
// the semantic variables through bundle.css's gos-* classes, so a theme never touches it.
import { type ReactNode, useEffect, useState } from "react";
import type { AlertCard, ApprovalCard, Tier } from "../cards/types.gen";
import { plain, secondsLeft, signed } from "../state/format";
import { GLYPH } from "../state/states";

export function Panel(props: {
  label?: string;
  title?: string;
  className?: string;
  children?: ReactNode;
  testId?: string;
  aside?: ReactNode;
}) {
  return (
    <section className={`gos-panel ${props.className ?? ""}`} data-testid={props.testId}>
      {(props.label || props.aside) && (
        <div className="panel-head">
          {props.label && <span className="gos-label">{props.label}</span>}
          {props.aside}
        </div>
      )}
      {props.title && <h2 className="gos-title">{props.title}</h2>}
      {props.children}
    </section>
  );
}

export function Button(props: {
  variant?: "primary" | "confirm" | "plain";
  onClick?: () => void;
  disabled?: boolean;
  children: ReactNode;
  title?: string;
  testId?: string;
  type?: "button" | "submit";
}) {
  const variant = props.variant && props.variant !== "plain" ? ` ${props.variant}` : "";
  return (
    <button
      type={props.type ?? "button"}
      className={`gos-btn${variant}`}
      onClick={props.onClick}
      disabled={props.disabled}
      title={props.title}
      data-testid={props.testId}
    >
      {props.children}
    </button>
  );
}

const TIER_CLASS: Record<string, string> = {
  Steam: "tier-steam",
  ULV: "tier-ulv",
  LV: "tier-lv",
  MV: "tier-mv",
  HV: "tier-hv",
  EV: "tier-ev",
  IV: "tier-iv",
  LuV: "tier-luv",
  ZPM: "tier-zpm",
  UV: "tier-uv",
  UHV: "tier-uhv",
  UEV: "tier-uev",
  UIV: "tier-uiv",
};

/** A tier in GT's own colour, always with its name (UMV and up have no colour token). */
export function TierChip(props: { tier: Tier | string }) {
  const cls = TIER_CLASS[props.tier] ?? "tier-none";
  return <span className={`gos-tier ${cls}`}>{props.tier}</span>;
}

/** The tier name for a detected stage (D64): stone and steam show as Steam. */
export function stageTier(stage: string): string {
  return stage === "stone" || stage === "steam" ? "Steam" : stage;
}

export type Status = "ok" | "warn" | "err" | "error" | "off" | "info";

const STATUS_GLYPH: Record<string, string> = {
  ok: GLYPH.ok,
  warn: GLYPH.warn,
  err: GLYPH.warn,
  error: GLYPH.warn,
  off: GLYPH.paused,
  info: GLYPH.waiting,
};

/** Status is a glyph and a word in the status colour, never colour alone. */
export function StatusGlyph(props: { status: Status; label: string; glyph?: string }) {
  const tone = props.status === "error" ? "err" : props.status === "off" || props.status === "info" ? "" : props.status;
  return (
    <span className={`gos-status ${tone}`} data-status={props.status}>
      <span aria-hidden="true">{props.glyph ?? STATUS_GLYPH[props.status]}</span> <span className="word">{props.label}</span>
    </span>
  );
}

/** A citation id (plan 0.1 principle 2). When recipe data is stale the "data stale" badge
 * replaces it (design system); the id stays in its title and opens the same way. */
export function CitationChip(props: { cite: string; stale?: boolean; onOpen?: (cite: string) => void }) {
  const opens = props.onOpen !== undefined;
  const text = props.stale ? `${GLYPH.warn} data stale` : props.cite;
  const title = props.stale ? `${props.cite}: recipe data doesn't match Play (export again)` : props.cite;
  if (!opens) {
    return (
      <span className={`gos-chip cite${props.stale ? " stale" : ""}`} title={title} data-cite={props.cite}>
        {text}
      </span>
    );
  }
  return (
    <button
      type="button"
      className={`gos-chip cite${props.stale ? " stale" : ""}`}
      title={title}
      data-cite={props.cite}
      onClick={() => props.onOpen?.(props.cite)}
    >
      {text}
    </button>
  );
}

/** Net power of one tier: a bar from the middle, out to the right when positive. */
export function PowerRow(props: { tier: Tier; net: number; scale: number; cite?: string | null; stale?: boolean }) {
  const share = props.scale > 0 ? Math.min(50, (Math.abs(props.net) / props.scale) * 50) : 0;
  return (
    <div className="gos-power" data-tier={props.tier}>
      <TierChip tier={props.tier} />
      <div className="gos-track" role="img" aria-label={`${props.tier} net ${signed(props.net)} EU/t`}>
        <span className="mid" />
        {props.net >= 0 ? <span className="pos" style={{ width: `${share}%` }} /> : <span className="neg" style={{ width: `${share}%` }} />}
      </div>
      <span className={`gos-value${props.net < 0 ? " neg" : ""}`}>
        {signed(props.net)}
        {props.cite && <CitationChip cite={props.cite} stale={props.stale} />}
      </span>
    </div>
  );
}

export function NavItem(props: {
  label: string;
  keyHint: string | null;
  active: boolean;
  badge?: string | null;
  bad?: boolean;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      className={`gos-nav${props.active ? " on" : ""}`}
      aria-current={props.active ? "page" : undefined}
      onClick={props.onClick}
      data-testid={props.testId}
    >
      <kbd aria-hidden={props.keyHint === null}>{props.keyHint ?? "·"}</kbd>
      <span>{props.label}</span>
      {props.badge && <em className={props.bad ? "bad" : undefined}>{props.badge}</em>}
    </button>
  );
}

/** Something wrong: what it is, then the number (design system: content fundamentals). */
export function AlertView(props: { card: AlertCard; big?: boolean }) {
  const c = props.card;
  const level = c.level === "err" ? "Alert" : "Warning";
  return (
    <section className={`gos-panel gos-alert alert-${c.level}`} data-testid="alert" data-alert={c.id}>
      <span className="gos-label">
        {GLYPH.warn} {level}
        {c.source ? ` · ${c.source}` : ""}
      </span>
      <h3 className="gos-title">{c.title}</h3>
      {c.value !== null && (
        <div className={`gos-big${c.value < 0 ? " neg" : ""}${props.big ? "" : " small"}`}>
          {plain(c.value)}
          {c.unit && <span className="unit"> {c.unit}</span>}
        </div>
      )}
      <p className="gos-sub">{c.body}</p>
      {c.ref && <CitationChip cite={c.ref} />}
    </section>
  );
}

function useNow(everyMs: number, on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs, on]);
  return now;
}

/** Waiting for you: reads back exactly what will happen, with a countdown when it expires. */
export function ApprovalView(props: {
  card: ApprovalCard;
  onAction: (card: ApprovalCard, action: string) => void;
  busy?: boolean;
}) {
  const c = props.card;
  const now = useNow(1000, c.expires_at !== null);
  const left = c.expires_at !== null ? secondsLeft(c.expires_at, now) : null;
  return (
    <section className="gos-approval" data-testid="approval" data-approval={c.id} data-kind={c.kind}>
      <div className="gos-rail-head">
        <span className="gos-label approval-head">
          <span className="needs-glyph" aria-hidden="true">
            {GLYPH.warn}
          </span>{" "}
          Waiting for you
        </span>
        {left !== null && (
          <span className="gos-src countdown" data-testid="countdown">
            {left} s
          </span>
        )}
      </div>
      <h3 className="gos-title approval-title">{c.title}</h3>
      <p className="readback">
        {c.readback}
        {c.kind === "action" ? " Or just say confirm." : ""}
      </p>
      {c.detail && <p className="gos-sub">{c.detail}</p>}
      <div className="actions">
        {c.actions.map((a) => (
          <Button
            key={a.id}
            variant={a.primary ? "confirm" : "plain"}
            disabled={props.busy || left === 0}
            onClick={() => props.onAction(c, a.id)}
            testId={`action-${a.id}`}
          >
            {a.label}
          </Button>
        ))}
      </div>
    </section>
  );
}
