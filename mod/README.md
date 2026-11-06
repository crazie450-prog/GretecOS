# GregtecLink (`mod/`)

Gregtec OS's Forge mod for GTNH 2.9.0 (Minecraft 1.7.10, GT 5.09.54.265). It has three modules, switched per instance:

| Module | Instance | Status |
|---|---|---|
| `export` | GTNH-Export only | WO-1.1: GT recipe-map dump, machine pass, item/fluid tables, manifest. NEI trigger and autoexport are PR 1b (V22) |
| `listener` | Play | WO-6.2; registers nothing yet |
| `client` | Play (client side) | WO-8.1; registers nothing yet |

It adds **no items, blocks, mixins, recipes, commands or packets** (`usesMixins=false`).

## Commands

Run from `mod/`. Gradle runs on JDK 25 (`gradle/gradle-daemon-jvm.properties`; Gradle fetches it if it is missing);
Minecraft runs on Java 8. The code compiles to Java 8 bytecode (`enableModernJavaSyntax=jabel`), so use Java 8 APIs only.

```
./gradlew build                   # compile, Spotless, Checkstyle, JUnit, jar -> build/libs/gregteclink-<version>.jar
./gradlew spotlessApply           # format Java and Gradle files the way `build` checks them
./gradlew runServer -Pharness     # headless harness: dev server, scripted export, checks, exit (see below)
./gradlew runServer               # the template's plain dev server (stays up; type `stop`)
```

The jar to install is `build/libs/gregteclink-<version>.jar` (not `-dev` or `-sources`). Its name matches the
`mods/gregteclink*` entry of Export's `sync_exclude`.

## Module config

`config/gregteclink.cfg` in the instance:

```
modules {
    B:client=false
    B:export=true
    B:listener=false
}
```

Every module defaults to off, and the mod never creates or rewrites this file: a jar without a config does nothing.
The mod parses the file itself, read-only, because Forge's `Configuration` renames a malformed file to `.errored` and
writes a new one. A malformed file turns every module off.
Export's copy (`export=true`) is written by `sync_instances.py` and is in Export's `sync_exclude`
(`config/gregteclink*`).

## Export module

When a world opens in Export (the integrated server's `FMLServerStartedEvent`, after GT's recipe unification), the
module writes `<.minecraft>/gregteclink/exports/<run_id>/`. Opening Export's void world is the one click. The file
contract is [docs/contracts/gregteclink-export-v1.md](../docs/contracts/gregteclink-export-v1.md).

- Every write goes through `PathGuard`, rooted at `<gameDir>/gregteclink/exports/`. It never changes the world. A link
  or junction at `gregteclink` or `exports` stops the export before anything is written.
- Files go into `<run_id>.partial/`; `manifest.json` is written last, then the folder is renamed.
- After the rename, the module keeps the newest 10 complete runs (D98): it deletes older complete runs and every
  `.partial` folder left by an earlier run, inside its own `exports/` folder only. It also keeps the newest 3 incomplete runs
  (manifest `"complete": false`) and deletes older ones. Each doomed run is renamed to `.partial` before anything in
  it is deleted, so a delete that fails halfway never leaves a "complete" run with files missing. Runs with no
  readable manifest are kept.
- One deliberate runtime change, with `export=true` only: GT's recipe-owner capture is turned on in preInit (the same
  switch as GT's `NEIRecipeOwner` client option), so each recipe records the mod that added it. NEI in Export then
  shows "added by" lines.
- `-Dgregtec.runId=<id>` names the run (`[A-Za-z0-9._-]`, up to 64 characters); otherwise it is the UTC time.
- `-Dgregtec.autoexport=true` is only reported as `not_built` until PR 1b.

### Installing into GTNH-Export

Export runs as Clayton, so its jar comes from GOS_APP, which `gos-dev` can't change (D75, D98):

1. `gos deploy` (Clayton, from his clone) builds `mod/` in the staged release with the Gradle wrapper, keeps
   `gregteclink-<commit>.jar` in `GOS_APP\current\mod-dist\` with its SHA256 in `release.json`, and deletes the build
   output. The first deploy also downloads Gradle, JDKs and Minecraft, so it takes a few minutes.
2. `sync_instances.py` (run from GOS_APP) then installs that jar into Export only: it checks the SHA256, replaces any
   other `mods/gregteclink*.jar`, and writes the config above (`export=true`). Both paths stay in Export's
   `sync_exclude`.

Never install it into Play with `export=true`.

## Headless harness

`./gradlew runServer -Pharness`:

1. `prepareHarness` deletes `run/server/harness/` (the world) and `run/server/gregteclink/`, then copies `harness/`
   (`eula.txt`, `server.properties` for a fresh superflat world, and a config with `export=true`) into `run/server/`.
2. `runServer` starts the dev server with `-Dgregtec.harness=true`. On server start the mod seeds `exports/` with 11
   old complete runs, 4 incomplete ones and a stale `.partial` (for the pruning check), exports in mode `harness`,
   reads the files back, runs the scripted checks, writes `run/server/gregteclink/harness-result.json`, shuts the
   server down and exits 0 (all checks passed) or 1.
3. `checkHarness` prints each check and fails the build unless the result says `"pass": true`.
4. `removeHarnessConfig` deletes `run/server/config/gregteclink.cfg` again (pass or fail), so a later plain
   `./gradlew runServer` exports nothing.

The harness acts only in the deobfuscated dev environment, so `-Dgregtec.harness=true` does nothing in a real
instance. Its dev runtime is GT's own transitive mod set (GT5-Unofficial with GT++, bartworks, GoodGenerator,
TecTech and others, IC2, AE2, ...), not the pack, so its thresholds are loose. CI runs it in the `mod` job.

## Licences

- GregtecLink's own code (`src/`, `harness/`, `addon.gradle.kts`, `addon.late.gradle.kts`) is MIT, Copyright (c)
  2026 crazie450-prog: see [LICENSE](LICENSE) (D98).
- The build scaffolding (`build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`, `repositories.gradle`,
  `dependencies.gradle`, `.editorconfig`, `.gitattributes`, `.gitignore`) comes from
  [GTNewHorizons/ExampleMod1.7.10](https://github.com/GTNewHorizons/ExampleMod1.7.10) `starter.zip` and is MIT;
  its notice is in [LICENSE-ExampleMod](LICENSE-ExampleMod). That notice covers only those files.
- The Gradle wrapper (`gradlew`, `gradlew.bat`, `gradle/wrapper/`) is Gradle's own and is Apache-2.0 (see the SPDX
  header in `gradlew`).
- GT5-Unofficial and NEI are linked at runtime only; nothing of theirs is vendored.
- Gregtorio's GPL-3 dumper (`tools/gtnh_dump`) is not copied. The recipe fields follow the "format 1" list in its
  README, as summarised in research A§1.7. The value encoding in `MetaJson` was first written in the same shape as
  Gregtorio's `writeValue` (the same type-check order, a depth-4 cutoff, a GT material as its name, public simple
  fields as the fallback). Review found no copied code, only that resemblance. The fallback has since been changed:
  every public field is encoded recursively, and `str` is kept only for a class's own `toString()`, without identity
  hashes.
