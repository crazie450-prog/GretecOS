// The design system (docs/design/system/, D79, D92) as the app's CSS, read at build time.
//
// - tokens.json -> one :root block of custom properties (--brass-bg, --tier-lv, --space-5, ...):
//   no colour, size or radius is ever copied into the app by hand.
// - components/bundle.css -> used as it is (the 12 .theme-<slug> mappings onto the semantic
//   variables and the nine core components), except its Google Fonts @import: the app never loads
//   a font from Google at runtime. That import line names every family and weight the themes use,
//   so it is turned into imports of the same fonts from local @fontsource packages (OFL-1.1),
//   whose licence files go into the build (fonts/licenses/).
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(HERE, "..", "..");
export const SYSTEM_DIR = join(REPO_ROOT, "docs", "design", "system");
export const TOKENS_FILE = join(SYSTEM_DIR, "tokens.json");
export const BUNDLE_FILE = join(SYSTEM_DIR, "components", "bundle.css");

export interface Token {
  name: string;
  value: string;
  usage?: string;
}
export interface TypeStyle {
  name: string;
  fontSize: string;
  lineHeight: number;
  fontWeight: number;
  letterSpacing?: string;
  family?: string;
}
export interface Tokens {
  color: { tokens: Token[] };
  type: { families: Record<string, string>; groups: { family: string; styles: TypeStyle[] }[] };
  spacing: { tokens: Token[] };
  radius: { tokens: Token[] };
  size: { tokens: Token[] };
}
export interface Font {
  family: string;
  pkg: string; // the @fontsource package: @fontsource/<pkg>
  weights: number[];
}

export function readTokens(file = TOKENS_FILE): Tokens {
  return JSON.parse(readFileSync(file, "utf-8")) as Tokens;
}

export function readBundle(file = BUNDLE_FILE): string {
  return readFileSync(file, "utf-8");
}

/** Every token as a custom property on :root, the type scale as --type-<name>-size and so on. */
export function tokensCss(tokens: Tokens): string {
  const lines: string[] = [];
  for (const group of [tokens.color, tokens.spacing, tokens.radius, tokens.size]) {
    for (const t of group.tokens) lines.push(`  --${t.name}: ${t.value};`);
  }
  for (const group of tokens.type.groups) {
    for (const s of group.styles) {
      lines.push(`  --${s.name}-size: ${s.fontSize};`);
      lines.push(`  --${s.name}-line: ${s.lineHeight};`);
      lines.push(`  --${s.name}-weight: ${s.fontWeight};`);
      lines.push(`  --${s.name}-tracking: ${s.letterSpacing ?? "normal"};`);
    }
  }
  return `:root {\n${lines.join("\n")}\n}\n`;
}

const GOOGLE_IMPORT = /^@import url\(['"]?(https:\/\/fonts\.googleapis\.com\/[^'")]+)['"]?\);\s*$/m;

/** The families and weights bundle.css asks Google Fonts for. */
export function fontsOf(bundle: string): Font[] {
  const m = GOOGLE_IMPORT.exec(bundle);
  if (!m) return [];
  const url = new URL(m[1]);
  return url.searchParams.getAll("family").map((spec) => {
    const [name, axes] = spec.split(":");
    const weights = axes?.startsWith("wght@")
      ? axes.slice(5).split(";").map(Number)
      : [400];
    return { family: name, pkg: name.toLowerCase().replace(/ /g, "-"), weights };
  });
}

/** bundle.css without its Google Fonts @import. */
export function localBundle(bundle: string): string {
  return bundle.replace(GOOGLE_IMPORT, "/* Fonts: bundled locally from @fontsource (D162). */");
}

/** The app's design CSS: the local font imports, the tokens, then bundle.css. */
export function designCss(tokens: Tokens, bundle: string): string {
  const imports = fontsOf(bundle).flatMap((f) =>
    f.weights.map((w) => `@import "@fontsource/${f.pkg}/latin-${w}.css";`),
  );
  return `${imports.join("\n")}\n${tokensCss(tokens)}\n${localBundle(bundle)}`;
}

/** The semantic variables each .theme-<slug> block of bundle.css sets. */
export function themeBlocks(bundle: string): Map<string, Map<string, string>> {
  const out = new Map<string, Map<string, string>>();
  const block = /([^{}]*\.theme-([a-z]+)[^{}]*)\{([^}]*)\}/g;
  for (const m of bundle.matchAll(block)) {
    if (!/^\s*(:root,\s*)?\.theme-[a-z]+\s*$/.test(m[1].replace(/\/\*[\s\S]*?\*\//g, ""))) continue;
    const vars = new Map<string, string>();
    for (const d of m[3].matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) vars.set(d[1], d[2].trim());
    out.set(m[2], vars);
  }
  return out;
}
