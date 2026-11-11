// A Vite plugin that serves the design system as `virtual:gos-design.css` (see designSystem.ts)
// and puts each bundled font's licence in the build: fonts/licenses/<package>.txt, plus an
// index naming every family, its package, version and licence.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import type { Plugin } from "vite";
import { BUNDLE_FILE, TOKENS_FILE, designCss, fontsOf, readBundle, readTokens } from "./designSystem.ts";

const ID = "virtual:gos-design.css";
const RESOLVED = `\0${ID}`;

export interface FontLicence {
  family: string;
  pkg: string;
  version: string;
  license: string;
  text: string;
}

/** The licence of every font bundle.css uses, read from its installed @fontsource package. */
export function fontLicences(root: string): FontLicence[] {
  const require = createRequire(join(root, "package.json"));
  return fontsOf(readBundle()).map((f) => {
    const manifest = require.resolve(`@fontsource/${f.pkg}/package.json`);
    const meta = JSON.parse(readFileSync(manifest, "utf-8")) as { version: string; license: string };
    return {
      family: f.family,
      pkg: f.pkg,
      version: meta.version,
      license: meta.license,
      text: readFileSync(join(dirname(manifest), "LICENSE"), "utf-8"),
    };
  });
}

export function gosDesign(root: string): Plugin {
  return {
    name: "gos-design",
    resolveId(id) {
      return id === ID ? RESOLVED : null;
    },
    load(id) {
      if (id !== RESOLVED) return null;
      this.addWatchFile(TOKENS_FILE);
      this.addWatchFile(BUNDLE_FILE);
      return designCss(readTokens(), readBundle());
    },
    generateBundle() {
      const licences = fontLicences(root);
      const index = [
        "Fonts bundled with the Gregtec OS dashboard (D162). Each is a Google Font under the",
        "SIL Open Font License 1.1, from its @fontsource package; its licence is in this folder.",
        "",
        ...licences.map((l) => `${l.family}\t@fontsource/${l.pkg}@${l.version}\t${l.license}\t${l.pkg}.txt`),
        "",
      ].join("\n");
      this.emitFile({ type: "asset", fileName: "fonts/licenses/README.txt", source: index });
      for (const l of licences) {
        this.emitFile({ type: "asset", fileName: `fonts/licenses/${l.pkg}.txt`, source: l.text });
      }
    },
  };
}
