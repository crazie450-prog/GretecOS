// Age themes (D81, docs/design/age_themes.md): the age follows the detected tier, any reached age
// can be pinned, ages beyond stay hidden, comfort modes replace the age theme, a tier-up fades in.
import { describe, expect, it } from "vitest";
import { applyThemeClass } from "../src/theme/useTheme";
import {
  AGES,
  COMFORT,
  DAY_ONE,
  DEFAULT_PREFS,
  THEMES,
  activeTheme,
  ageOf,
  isTierUp,
  loadPrefs,
  reachedAges,
  savePrefs,
} from "../src/theme/themes";

describe("ages", () => {
  it("are ten, plus two comfort modes", () => {
    expect(AGES.map((t) => t.slug)).toEqual(["brass", "machine", "alu", "hud", "circuit", "tung", "holo", "fusion", "naq", "deep"]);
    expect(COMFORT.map((t) => t.slug)).toEqual(["calm", "manual"]);
    expect(THEMES).toHaveLength(12);
    expect(DAY_ONE).toEqual(["brass", "machine", "alu", "calm", "manual"]);
  });
  it("follow the detected stage (D64)", () => {
    expect(ageOf("stone")).toBe(1);
    expect(ageOf("steam")).toBe(1);
    expect(ageOf("LV")).toBe(2);
    expect(ageOf("MV")).toBe(3);
    expect(ageOf("HV")).toBe(4);
    expect(ageOf("LuV")).toBe(7);
    expect(ageOf("UV")).toBe(9);
    for (const stage of ["UHV", "UEV", "UIV", "UMV", "UXV"]) expect(ageOf(stage)).toBe(10);
    expect(ageOf(null)).toBe(1); // no quest data yet: a new world starts in the Stone Age
    expect(ageOf("nonsense")).toBe(1);
  });
});

describe("the active theme", () => {
  it("is the age of the tier", () => {
    expect(activeTheme("stone", DEFAULT_PREFS)).toBe("brass");
    expect(activeTheme("MV", DEFAULT_PREFS)).toBe("alu");
  });
  it("can be pinned to any age he has reached, and no later one", () => {
    expect(activeTheme("MV", { ...DEFAULT_PREFS, pinnedAge: 1 })).toBe("brass");
    expect(activeTheme("MV", { ...DEFAULT_PREFS, pinnedAge: 7 })).toBe("alu");
    expect(reachedAges("MV").map((t) => t.slug)).toEqual(["brass", "machine", "alu"]);
  });
  it("is replaced by a comfort mode until he switches back", () => {
    expect(activeTheme("MV", { ...DEFAULT_PREFS, comfort: "manual", pinnedAge: 1 })).toBe("manual");
    expect(activeTheme("LV", { ...DEFAULT_PREFS, comfort: "calm" })).toBe("calm");
  });
  it("fades in once on a tier-up, not on the first load", () => {
    expect(isTierUp(null, 3)).toBe(false);
    expect(isTierUp(2, 3)).toBe(true);
    expect(isTierUp(3, 3)).toBe(false);
    expect(isTierUp(3, 2)).toBe(false);
  });
});

describe("preferences", () => {
  it("live per device and survive bad storage", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    savePrefs(storage, { ...DEFAULT_PREFS, pinnedAge: 2, comfort: "calm", pauseMotion: true });
    expect(loadPrefs(storage)).toEqual({ ...DEFAULT_PREFS, pinnedAge: 2, comfort: "calm", pauseMotion: true });
    store.set("gos.theme", "{not json");
    expect(loadPrefs(storage)).toEqual(DEFAULT_PREFS);
    store.set("gos.theme", JSON.stringify({ comfort: "neon", pinnedAge: "2" }));
    expect(loadPrefs(storage)).toEqual(DEFAULT_PREFS);
    const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(loadPrefs(throwing)).toEqual(DEFAULT_PREFS);
    expect(() => savePrefs(throwing, DEFAULT_PREFS)).not.toThrow();
    expect(loadPrefs(null)).toEqual(DEFAULT_PREFS);
  });
});

describe("the theme class", () => {
  it("is one class on the element, with motion and the fade, and keeps other classes", () => {
    const el = document.createElement("body");
    el.className = "keep theme-hud motion-paused";
    applyThemeClass(el, "brass", { ...DEFAULT_PREFS, pauseMotion: false }, true);
    expect(el.className.split(" ").sort()).toEqual(["keep", "theme-brass", "theme-fade"]);
    applyThemeClass(el, "manual", { ...DEFAULT_PREFS, pauseMotion: true }, false);
    expect(el.className.split(" ").sort()).toEqual(["keep", "motion-paused", "theme-manual"]);
  });
});
