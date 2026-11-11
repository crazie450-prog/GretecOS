// The 12 themes (D81, docs/design/age_themes.md): ten voltage ages and two comfort modes. Only
// tokens, frames, textures and core art change with the theme; layout never does.
export type Slug =
  | "brass"
  | "machine"
  | "alu"
  | "hud"
  | "circuit"
  | "tung"
  | "holo"
  | "fusion"
  | "naq"
  | "deep"
  | "calm"
  | "manual";

export interface Theme {
  slug: Slug;
  name: string;
  age: number | null; // 1-10; null for a comfort mode
  stages: string[]; // the detected stages (D64) that belong to this age
  tierColor: string; // the token whose colour the core art uses
}

export const THEMES: readonly Theme[] = [
  { slug: "brass", name: "Brass and Steam", age: 1, stages: ["stone", "steam"], tierColor: "tier-steam" },
  { slug: "machine", name: "Machine GUI", age: 2, stages: ["LV"], tierColor: "tier-lv" },
  { slug: "alu", name: "Brushed Aluminium", age: 3, stages: ["MV"], tierColor: "tier-mv" },
  { slug: "hud", name: "Command HUD", age: 4, stages: ["HV"], tierColor: "tier-hv" },
  { slug: "circuit", name: "Circuit Board", age: 5, stages: ["EV"], tierColor: "tier-ev" },
  { slug: "tung", name: "Tungstensteel", age: 6, stages: ["IV"], tierColor: "tier-iv" },
  { slug: "holo", name: "Hologram Lab", age: 7, stages: ["LuV"], tierColor: "tier-luv" },
  { slug: "fusion", name: "Fusion", age: 8, stages: ["ZPM"], tierColor: "tier-zpm" },
  { slug: "naq", name: "Naquadah", age: 9, stages: ["UV"], tierColor: "tier-uv" },
  { slug: "deep", name: "Deep Space", age: 10, stages: ["UHV", "UEV", "UIV", "UMV", "UXV"], tierColor: "tier-uhv" },
  { slug: "calm", name: "Calm Ops", age: null, stages: [], tierColor: "accent" },
  { slug: "manual", name: "Field Manual", age: null, stages: [], tierColor: "accent" },
];

export const AGES = THEMES.filter((t) => t.age !== null);
export const COMFORT = THEMES.filter((t) => t.age === null);
export const FADE_MS = 1500; // the tier-up moment

export function theme(slug: Slug): Theme {
  return THEMES.find((t) => t.slug === slug) ?? THEMES[3];
}

/** The age of a detected stage; Stone and Steam (and an unknown stage) are age 1. */
export function ageOf(stage: string | null | undefined): number {
  const found = AGES.find((t) => t.stages.includes(stage ?? ""));
  return found?.age ?? 1;
}

export function themeOfAge(age: number): Theme {
  return AGES.find((t) => t.age === age) ?? AGES[0];
}

export interface ThemePrefs {
  pinnedAge: number | null; // an age he has reached, held whatever his tier
  comfort: "calm" | "manual" | null; // replaces the age theme until he switches back
  pauseMotion: boolean;
  revealAges: boolean; // ages beyond his tier stay hidden like spoilers until he asks
}

export const DEFAULT_PREFS: ThemePrefs = {
  pinnedAge: null,
  comfort: null,
  pauseMotion: false,
  revealAges: false,
};

/** The theme to show for a detected stage and the device's preferences. */
export function activeTheme(stage: string | null | undefined, prefs: ThemePrefs): Slug {
  if (prefs.comfort) return prefs.comfort;
  const reached = ageOf(stage);
  if (prefs.pinnedAge !== null && prefs.pinnedAge <= reached) return themeOfAge(prefs.pinnedAge).slug;
  return themeOfAge(reached).slug;
}

/** The ages he may pin: every age up to his own. */
export function reachedAges(stage: string | null | undefined): Theme[] {
  const reached = ageOf(stage);
  return AGES.filter((t) => (t.age ?? 0) <= reached);
}

/** A tier-up: his age went up since the last one this page showed (null: its first load). */
export function isTierUp(lastAge: number | null, nowAge: number): boolean {
  return lastAge !== null && nowAge > lastAge;
}

const KEY = "gos.theme";

export function loadPrefs(storage: Pick<Storage, "getItem"> | null): ThemePrefs {
  try {
    const raw = storage?.getItem(KEY);
    if (!raw) return DEFAULT_PREFS;
    const p = JSON.parse(raw) as Partial<ThemePrefs>;
    return {
      pinnedAge: typeof p.pinnedAge === "number" ? p.pinnedAge : null,
      comfort: p.comfort === "calm" || p.comfort === "manual" ? p.comfort : null,
      pauseMotion: p.pauseMotion === true,
      revealAges: p.revealAges === true,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function savePrefs(storage: Pick<Storage, "setItem"> | null, prefs: ThemePrefs): void {
  try {
    storage?.setItem(KEY, JSON.stringify(prefs));
  } catch {
    // private window or blocked storage: the choice lasts until the page reloads
  }
}
