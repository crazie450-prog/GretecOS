// Where the pointer and the keyboard focus are, for the glance/workbench switch (D49, D161).
import { useEffect, useState } from "react";
import { ENTER_DWELL_MS, LEAVE_HOLD_MS } from "./mode";

export interface Presence {
  pointerInside: boolean;
  leftAt: number; // performance.now() when the pointer last left; -Infinity: never
  windowFocused: boolean;
}

export function usePresence(gameFocused: boolean | null): Presence & { msSinceLeave: number } {
  const [p, setP] = useState<Presence>(() => ({
    pointerInside: false,
    leftAt: -Infinity,
    windowFocused: typeof document !== "undefined" && document.hasFocus(),
  }));
  const [, setTick] = useState(0);

  useEffect(() => {
    let entering: ReturnType<typeof setTimeout> | undefined;
    const inside = (): void => {
      if (entering !== undefined) return;
      entering = setTimeout(() => {
        entering = undefined;
        setP((s) => (s.pointerInside ? s : { ...s, pointerInside: true }));
      }, ENTER_DWELL_MS);
    };
    const leave = (e: MouseEvent): void => {
      if (e.relatedTarget !== null) return;
      clearTimeout(entering);
      entering = undefined;
      setP((s) => ({ ...s, pointerInside: false, leftAt: performance.now() }));
    };
    const focus = (): void => setP((s) => ({ ...s, windowFocused: true }));
    const blur = (): void => setP((s) => ({ ...s, windowFocused: false }));
    document.addEventListener("pointermove", inside, { passive: true });
    document.addEventListener("pointerdown", inside, { passive: true });
    document.documentElement.addEventListener("mouseleave", leave);
    document.addEventListener("mouseout", leave);
    window.addEventListener("focus", focus);
    window.addEventListener("blur", blur);
    return () => {
      clearTimeout(entering);
      document.removeEventListener("pointermove", inside);
      document.removeEventListener("pointerdown", inside);
      document.documentElement.removeEventListener("mouseleave", leave);
      document.removeEventListener("mouseout", leave);
      window.removeEventListener("focus", focus);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // GTNH takes the cursor without the window ever seeing it leave: when the core says the game
  // came to the front, the pointer is no longer here.
  useEffect(() => {
    if (gameFocused === true) setP((s) => (s.pointerInside ? { ...s, pointerInside: false, leftAt: performance.now() } : s));
  }, [gameFocused]);

  // Re-check once the leave hold is over.
  useEffect(() => {
    if (p.pointerInside || p.leftAt === -Infinity) return;
    const wait = LEAVE_HOLD_MS - (performance.now() - p.leftAt);
    if (wait <= 0) return;
    const t = setTimeout(() => setTick((n) => n + 1), wait + 20);
    return () => clearTimeout(t);
  }, [p.pointerInside, p.leftAt]);

  return { ...p, msSinceLeave: performance.now() - p.leftAt };
}

export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => typeof window !== "undefined" && window.matchMedia?.(query).matches === true);
  useEffect(() => {
    const m = window.matchMedia?.(query);
    if (!m) return;
    const change = (): void => setOn(m.matches);
    m.addEventListener("change", change);
    setOn(m.matches);
    return () => m.removeEventListener("change", change);
  }, [query]);
  return on;
}
