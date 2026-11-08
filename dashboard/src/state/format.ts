// Number and time formats (design system: content fundamentals). Numbers carry a sign or a unit;
// a negative uses the true minus sign. Game numbers are shown as the DB holds them, never rounded.
export const MINUS = "−";

export function signed(n: number): string {
  if (n > 0) return `+${n}`;
  if (n < 0) return `${MINUS}${Math.abs(n)}`;
  return "0";
}

export function plain(n: number): string {
  return n < 0 ? `${MINUS}${Math.abs(n)}` : String(n);
}

export function usd(n: number): string {
  return `${n < 0 ? MINUS : ""}$${Math.abs(n).toFixed(2)}`;
}

export function pct(n: number): string {
  return `${Number.isInteger(n) ? n : n.toFixed(1)}%`;
}

/** A countdown: "45 s" under a minute, else minutes and seconds ("29:59"). */
export function countdown(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function clock(d: Date): { hm: string; s: string } {
  const two = (n: number) => String(n).padStart(2, "0");
  return { hm: `${two(d.getHours())}:${two(d.getMinutes())}`, s: two(d.getSeconds()) };
}

/** Seconds left until `expiresAt` (unix seconds), never below 0. */
export function secondsLeft(expiresAt: number, nowMs: number): number {
  return Math.max(0, Math.ceil(expiresAt - nowMs / 1000));
}

export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
