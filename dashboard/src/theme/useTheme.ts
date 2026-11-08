// The theme on the page: the age of his detected tier (D64), or a pinned age, or a comfort mode,
// per device (localStorage, D162). The class goes on <body>, so switching moves nothing. A
// tier-up fades the new theme in once over 1.5 s, unless motion is paused.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Slug, type ThemePrefs, FADE_MS, activeTheme, ageOf, isTierUp, loadPrefs, savePrefs } from "./themes";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export interface ThemeControl {
  slug: Slug;
  prefs: ThemePrefs;
  setPrefs: (patch: Partial<ThemePrefs>) => void;
  fading: boolean;
}

export function applyThemeClass(root: HTMLElement, slug: Slug, prefs: ThemePrefs, fading: boolean): void {
  const keep = [...root.classList].filter((c) => !c.startsWith("theme-") && c !== "motion-paused");
  root.className = [...keep, `theme-${slug}`, ...(prefs.pauseMotion ? ["motion-paused"] : []), ...(fading ? ["theme-fade"] : [])].join(" ");
}

export function useTheme(stage: string | null | undefined): ThemeControl {
  const [prefs, setPrefsState] = useState<ThemePrefs>(() => loadPrefs(storage()));
  const [fading, setFading] = useState(false);
  // The age this page last showed: only a rise while it is open is a tier-up, never one that
  // happened while it was closed (so the first load sets it, and nothing about it is stored).
  const shownAge = useRef<number | null>(null);
  const slug = useMemo(() => activeTheme(stage, prefs), [stage, prefs]);

  const setPrefs = useCallback((patch: Partial<ThemePrefs>) => {
    setPrefsState((p) => {
      const next = { ...p, ...patch };
      savePrefs(storage(), next);
      return next;
    });
  }, []);

  // The tier-up moment: only when the detected age goes up while he watches.
  useEffect(() => {
    if (stage === null || stage === undefined) return;
    const age = ageOf(stage);
    const up = isTierUp(shownAge.current, age) && !prefs.pauseMotion && !prefs.comfort && prefs.pinnedAge === null;
    shownAge.current = age;
    if (up) setFading(true);
  }, [stage, prefs.pauseMotion, prefs.comfort, prefs.pinnedAge]);

  useEffect(() => {
    if (!fading) return;
    const t = setTimeout(() => setFading(false), FADE_MS);
    return () => clearTimeout(t);
  }, [fading]);

  useEffect(() => {
    applyThemeClass(document.body, slug, prefs, fading);
  }, [slug, prefs, fading]);

  return { slug, prefs, setPrefs, fading };
}
