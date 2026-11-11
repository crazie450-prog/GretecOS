// The design system as the app reads it (docs/design/system): every token becomes a custom
// property, every theme maps every semantic role, fonts come from local packages with their
// licences, never from Google.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { contrastOn, contrastOver, distance, parseColor, ratio } from "../design/contrast";
import { REPO_ROOT, designCss, fontsOf, localBundle, readBundle, readTokens, themeBlocks, tokensCss } from "../design/designSystem";
import { fontLicences } from "../design/plugin";
import { THEMES } from "../src/theme/themes";

const tokens = readTokens();
const bundle = readBundle();
const value = new Map(tokens.color.tokens.map((t) => [t.name, t.value]));
const SLUGS = THEMES.map((t) => t.slug);
const ROLES = [
  "bg", "panel", "border", "rule", "text", "text-2", "text-3", "accent", "primary", "primary-ink", "track",
  "ok", "warn", "err", "needs", "needs-border", "needs-tint", "confirm", "confirm-ink", "shell-bg", "shell-text", "shell-text-2",
];

describe("tokens", () => {
  it("become one :root block of custom properties", () => {
    const css = tokensCss(tokens);
    for (const t of [...tokens.color.tokens, ...tokens.spacing.tokens, ...tokens.radius.tokens, ...tokens.size.tokens]) {
      expect(css).toContain(`--${t.name}: ${t.value};`);
    }
    expect(css).toContain("--type-big-number-size: 110px;");
    expect(css).toContain("--type-label-tracking: 0.12em;");
  });
});

describe("bundle.css", () => {
  it("maps all 12 themes onto every semantic role, from that theme's own tokens", () => {
    const blocks = themeBlocks(bundle);
    expect([...blocks.keys()].sort()).toEqual([...SLUGS].sort());
    for (const slug of SLUGS) {
      const vars = blocks.get(slug)!;
      for (const role of ROLES) {
        expect(vars.get(role), `${slug} ${role}`).toBe(`var(--${slug}-${role})`);
        expect(value.has(`${slug}-${role}`), `${slug}-${role} token`).toBe(true);
      }
      for (const v of ["font-display", "font-body", "font-mono", "label-case", "radius-panel", "radius-control"]) {
        expect(vars.has(v), `${slug} ${v}`).toBe(true);
      }
    }
  });

  it("loses only its Google Fonts import", () => {
    const local = localBundle(bundle);
    expect(local).not.toContain("googleapis");
    expect(local.length).toBeGreaterThan(bundle.length - 2000);
    expect(designCss(tokens, bundle)).not.toMatch(/https?:\/\//);
  });
});

describe("fonts", () => {
  const fonts = fontsOf(bundle);
  const manifest = JSON.parse(readFileSync(join(REPO_ROOT, "dashboard", "package.json"), "utf-8")) as {
    dependencies: Record<string, string>;
  };

  it("are every family a theme names", () => {
    const named = new Set<string>();
    for (const vars of themeBlocks(bundle).values()) {
      for (const role of ["font-display", "font-body", "font-mono"]) {
        named.add(vars.get(role)!.split(",")[0].replace(/'/g, "").trim());
      }
    }
    expect(new Set(fonts.map((f) => f.family))).toEqual(named);
    expect(fonts.find((f) => f.family === "Barlow Condensed")?.weights).toEqual([500, 600, 700, 800]);
    expect(fonts.find((f) => f.family === "Michroma")?.weights).toEqual([400]);
  });

  it("come from pinned local packages, each with its OFL licence", () => {
    for (const f of fonts) {
      expect(manifest.dependencies[`@fontsource/${f.pkg}`], f.pkg).toMatch(/^\d+\.\d+\.\d+$/);
    }
    const licences = fontLicences(join(REPO_ROOT, "dashboard"));
    expect(licences).toHaveLength(fonts.length);
    for (const l of licences) {
      expect(l.license).toBe("OFL-1.1");
      expect(l.text).toContain("SIL Open Font License");
    }
  });

  it("are imported as latin subsets in every weight", () => {
    const css = designCss(tokens, bundle);
    for (const f of fonts) for (const w of f.weights) expect(css).toContain(`@import "@fontsource/${f.pkg}/latin-${w}.css";`);
  });
});

describe("contrast (design system README)", () => {
  const t = (slug: string, role: string): string => value.get(`${slug}-${role}`)!;
  // Each text colour over the surfaces under it, bottom first: the page, then the surface
  // (translucent panels composited over the page), then an approval card's tint.
  const pairs: [string, (s: string) => [string, ...string[]]][] = [
    ["text on panel", (s) => [t(s, "text"), t(s, "bg"), t(s, "panel")]],
    ["text-2 on panel", (s) => [t(s, "text-2"), t(s, "bg"), t(s, "panel")]],
    ["shell-text on shell", (s) => [t(s, "shell-text"), t(s, "bg"), t(s, "shell-bg")]],
    ["shell-text-2 on shell", (s) => [t(s, "shell-text-2"), t(s, "bg"), t(s, "shell-bg")]],
    ["needs on shell", (s) => [t(s, "needs"), t(s, "bg"), t(s, "shell-bg")]],
    ["primary-ink on primary", (s) => [t(s, "primary-ink"), t(s, "bg"), t(s, "primary")]],
    ["confirm-ink on confirm", (s) => [t(s, "confirm-ink"), t(s, "bg"), t(s, "confirm")]],
    ["err on panel", (s) => [t(s, "err"), t(s, "bg"), t(s, "panel")]],
    // An approval card is a panel tinted with "needs" (app.css), wherever it sits.
    ["text on the approval fill", (s) => [t(s, "text"), t(s, "bg"), t(s, "panel"), t(s, "needs-tint")]],
  ];
  for (const slug of SLUGS) {
    for (const [name, pick] of pairs) {
      it(`${slug}: ${name} is at least 4.5:1`, () => {
        const [fg, ...layers] = pick(slug);
        expect(contrastOver(fg, ...layers)).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  it("matches the README's table where it lists a number", () => {
    expect(contrastOn(t("hud", "text"), t("hud", "panel"), t("hud", "bg"))).toBeCloseTo(15.9, 0);
    expect(contrastOn(t("alu", "err"), t("alu", "panel"), t("alu", "bg"))).toBeCloseTo(4.7, 1);
    expect(ratio(parseColor("#000000"), parseColor("#ffffff"))).toBeCloseTo(21, 5);
  });

  it("keeps approvals apart from status in every theme", () => {
    // "needs you" never reuses a status colour (the closest pair, Calm Ops' magenta and coral
    // error, is 73 apart in sRGB). Beyond colour, approvals have their own fill, border and
    // "Waiting for you" heading, and status always has a glyph and a word (components.test.tsx);
    // where a tier colour comes close (LuV pink, UHV red) bundle.css doubles the approval border.
    for (const slug of SLUGS) {
      for (const status of ["ok", "warn", "err"]) {
        expect(distance(t(slug, "needs"), t(slug, status)), `${slug} needs vs ${status}`).toBeGreaterThan(70);
      }
    }
    expect(bundle).toMatch(/\.theme-holo \.gos-approval \{ border: 4px double/);
  });
});
