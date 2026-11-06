# GregtecLink (`mod/`)

Gregtec OS's Forge mod for GTNH 2.9.0 (Minecraft 1.7.10, GT 5.09.54.265). It has three modules, switched per instance:

| Module | Instance | Status |
|---|---|---|
| `export` | GTNH-Export only | WO-1.1: GT recipe-map dump, machine pass, item/fluid tables, NEI trigger, autoexport (V22), manifest; WO-1.6: ore mixes (`gt_ore_mixes.json`, D126) |
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
./gradlew runClient25 -Pharness   # client harness: dev client (Java 25, lwjgl3ify), autoexport with NEI's dump, checks, exit
./gradlew runServer               # the template's plain dev server (stays up; type `stop`)
```

On Linux without a display, run the client harness under Xvfb (CI does):
`LIBGL_ALWAYS_SOFTWARE=1 xvfb-run -a -s "-screen 0 1280x720x24" ./gradlew runClient25 -Pharness`. It needs `xvfb`,
`x11-xserver-utils` (LWJGL 2 crashes at startup without `xrandr`) and Mesa's `libgl1-mesa-dri`. `runClient` (Java 8,
LWJGL 2) works the same way.

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
module writes the GT files into `<.minecraft>/gregteclink/exports/<run_id>.partial/`. Then, on the client thread, it
waits until NEI has loaded (`NEIClientConfig.isLoaded()` and `ItemList.loadFinished`) plus 100 ticks, calls NEI
2.8.172's `RecipeJsonWriter.dumpAll(true)` by reflection (the same files as Data Dumps, All Handler Recipes,
Separate; the game freezes while it runs, as with the manual click), copies the files NEI wrote into `nei/`, writes
the manifest and renames the folder. With autoexport (below) the game then quits. The file contract is
[docs/contracts/gregteclink-export-v1.md](../docs/contracts/gregteclink-export-v1.md).

- Every write of the export goes through `PathGuard`, rooted at `<gameDir>/gregteclink/exports/`. The export never
  changes a world; only autoexport (below) deletes and recreates its own `saves/gregteclink-autoexport`. A link or
  junction at `gregteclink` or `exports` stops the export before anything is written.
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
- **NEI step.** NEI returns normally even when its dump fails, so GregtecLink judges it by NEI's own log lines,
  captured during the call from the logger object NEI writes to (`NEIClientConfig.logger`, named `NotEnoughItems`),
  and by the files: `parts.nei` is `ok` only if the call threw nothing, NEI logged no `Error dumping recipes`, it
  wrote at least one file, every copy succeeded, and the lines copied equal NEI's `Writing N recipes`. Otherwise it
  is `failed`, with the reason in `part_errors.nei`, and the GT files are still a complete run. An `ok` step whose
  NEI dropped a recipe handler (it logged `Error while looking up crafting recipe`; the handler's recipes are then
  missing, and its stack trace is only on stderr) also gets `part_warnings.nei`. NEI deletes every file in
  `dumps/recipes/` first, so GregtecLink refuses a link or junction at `dumps` or `dumps/recipes`. It copies only
  regular `.jsonl` files changed since the call started (minus 2 s), through `PathGuard`, and never moves or changes
  NEI's files. NEI not ready 10 minutes after the
  world opened (NEI disabled, for example) is `failed`; closing the world before then finishes the run without NEI.
- Without autoexport (a world opened by hand) the module posts two local chat lines, "GregtecLink: exporting NEI's
  recipes; ..." just before the freeze and "GregtecLink: export <run_id> finished: ..." after it, and stays in the
  world.

### NEI 2.8.172 in Export (manual swap, D100)

GTNH 2.9.0 ships NEI 2.8.171, which has no `RecipeJsonWriter`; without the swap every run says `parts.nei: absent`.
2.8.172-GTNH is 2.8.171 plus only PR #1100, the recipe exporter. Export runs exactly that jar, swapped in by hand once:

- `https://github.com/GTNewHorizons/NotEnoughItems/releases/download/2.8.172-GTNH/NotEnoughItems-2.8.172-GTNH.jar`,
  SHA256 `9362a063c04aaf11a668291542223725db1147e76f483936ad965530e084104f` (1,804,603 bytes). It replaces
  `mods/NotEnoughItems-2.8.171-GTNH.jar` (SHA256 starts `9a822134`, ends `88b5a3`) in GTNH-Export only.
- Sync's Export exclude `mods/NotEnoughItems-*.jar` keeps the swapped jar, and every manifest records Export's NEI
  version and jar hash (`nei_version`, `mods`), so each run shows whether the swap is in place.
- Not 2.8.173 (it drops six vanilla display-name overrides, so Export's names would differ from Play's) and not
  2.9.x (it reworks the recipe-handler API and is not a drop-in for pack 2.9.0).
- In the dev runtime, `dependencies.gradle` puts NEI 2.8.172 on the run classpath; the mod still compiles against
  GT's NEI 2.8.160 and reaches NEI only by reflection.

### Autoexport (V22)

`-Dgregtec.autoexport=true` in GTNH-Export's Prism settings (Edit, Settings, Java, JVM arguments) makes every launch
of Export an unattended export. It does nothing without `export=true`, so never in Play.

1. On the main menu (`GuiOpenEvent` for `GuiMainMenu` at normal priority, which still sees the vanilla menu before
   Custom Main Menu swaps it), GregtecLink opens its own world on the next tick: folder
   `saves/gregteclink-autoexport`, shown as "GregtecLink autoexport", creative, cheats on, superflat with one layer
   of bedrock (`2;7;1;`). If no `GuiMainMenu` has come 30 s after the game finished loading (another first screen),
   it opens the world from whatever screen is up and logs that screen's class.
2. The world is rebuilt on every run, so a pack update never stops the run at FML's "missing blocks" prompt.
   GregtecLink touches only that folder, only when its marker file `gregteclink-autoexport.txt` is in it, never
   through a link or junction, and refuses (logs, quits, exports nothing) otherwise. It first renames the old world
   to `gregteclink-autoexport.old-<epoch ms>` in one atomic step (three tries), so the marker moves with it, then
   deletes that copy with the marker last (three tries). A delete that fails partway (Windows: a scanner still
   holds a file) leaves a marked copy, which the next run deletes, never a world folder without its marker. Other
   worlds are never touched.
3. The export runs as above, and `parts.autoexport` is `ok`. Then the game quits in two steps: it leaves the world
   as "Save and Quit to Title" does, which waits until the integrated server has stopped and saved the world, and on
   the next tick calls `Minecraft.shutdown()` (exit code 0). `shutdown()` alone, called from inside the world, would
   not wait: Forge's wait for the server throws once the game has stopped running, and the JVM could exit while the
   server thread was still saving.
4. 30 minutes after GregtecLink's preInit, a run still waiting is written with what it has (`nei` and `autoexport`
   `failed`) and the game quits. This works only while the client thread ticks; a hang that blocks it needs WO-1.4's
   outside watchdog. A launch that quits with no run (the world not prepared, never opened, or no export started by
   the deadline) logs why at ERROR in `logs/fml-client-latest.log`, as `autoexport: no run: <why>`.

The one click is Prism's Launch button (or a desktop shortcut from Create Shortcut). From the command line,
`prismlauncher --launch GTNH-Export` does the same; it returns at once when Prism is already open, so a caller (WO-1.4)
detects the end by a new complete run in `exports/`. Prism's `--world` does nothing on 1.7.10, and it has no way to add
JVM arguments for a single launch, so the flag lives in the instance settings: to use Export by hand, remove it.

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
TecTech and others, IC2, AE2, ...) plus NEI 2.8.172, not the pack, so its thresholds are loose. CI runs it in the
`mod` job.

## Client harness

`./gradlew runClient25 -Pharness` (Java 25 with lwjgl3ify, as GTNH's Java 17+ build; `runClient` is Java 8) runs the
whole autoexport flow in the dev client:

1. `prepareClientHarness` deletes `run/client/saves/gregteclink-autoexport/`, the decoy world, `run/client/gregteclink/`
   and `run/client/dumps/`, then copies `harness/config/gregteclink.cfg` and `harness/client/` into `run/client/`:
   an old marked autoexport world with a stale file in it, and a decoy world with a marker but another name.
2. The client starts with `-Dgregtec.harness=true -Dgregtec.autoexport=true`, opens the autoexport world from the
   main menu, seeds old runs at server start, exports in mode `harness`, runs NEI's dump, finishes the run, checks
   it, leaves the world, and once the integrated server has stopped writes `run/client/gregteclink/harness-result.json`
   and quits.
3. `checkClientHarness` fails the build unless the result says `"pass": true`; `removeClientHarnessConfig` deletes
   the config again.

Its checks are the server harness's (with `side` client) plus: `nei` and `autoexport` `ok`, with no
`part_warnings`; every `nei/` file listed
with matching sha256, bytes and lines and none unlisted; at least 150 handler files, as many as NEI loaded; lines
copied equal NEI's count and exceed 50,000; one handler id per file and at least one `gt.recipe*` handler; no numeric
`id` in any stack, `anyOf` or `returns`; NEI's files left in `dumps/recipes`; the old marked world rebuilt (no
`.old-*` copy left) and the decoy untouched; the integrated server stopped before `Minecraft.shutdown()`, and the
world's `level.dat` written after the run finished (the save on quitting). Writes are checked in the two windows where the export's code runs, `saves/` aside: while the GT
parts are written at server start nothing outside `gregteclink/` may change, and from just before NEI's dump to the
end of the run nothing outside `gregteclink/` and `dumps/recipes/`. Between them, joining the world makes the game
and other mods write their own files (Forge's `usernamecache.json`, ServerUtilities, NEI's, GT's and AE2's configs),
as for a world opened by hand. NEI counts vary with config state between run folders (AE2 rewrites its
`Facades.cfg` on joining a world, and "Facade Crafting" grows from 2,403 to 2,631 recipes), so only loose thresholds
are checked. A run takes about 2 minutes, and each kept run is about 145 MB (10 are kept).

`-PharnessNeiFail` makes `run/client/dumps/recipes` a file, so NEI's dump fails; the harness then expects
`"nei": "failed"` with `Error dumping recipes` in `part_errors.nei`, in a run that is still complete. CI does not
run it.

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
