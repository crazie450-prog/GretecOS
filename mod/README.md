# GregtecLink (`mod/`)

Gregtec OS's Forge mod for GTNH 2.9.0 (Minecraft 1.7.10, GT 5.09.54.265). It has three modules, switched per instance:

| Module | Instance | Status |
|---|---|---|
| `export` | GTNH-Export only | WO-1.1: GT recipe-map dump, machine pass, item/fluid tables, NEI trigger, autoexport (V22), manifest; WO-1.6: ore mixes (`gt_ore_mixes.json`, D126); WO-1.10: CropsNH's crop dumpers (`crops/`, D134); the full ore dictionary (`oredict.jsonl`, D145) |
| `listener` | Play, Lab | WO-6.2: GT machine place/break events (`gregteclink/events/machines.jsonl`); refused with `export` on |
| `client` | Play and Lab (client side) | WO-8.1: the link to the core (below) |

It adds **no items, blocks, mixins, recipes, commands or packets** (`usesMixins=false`). The client module adds one
key binding (What's this?, unbound until chosen in Controls, GregtecLink) and event handlers.

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
  NEI still dropped a recipe handler (it logged `Error while looking up crafting recipe`; with the serial load below,
  the handler failed on the client thread too, so its recipes are missing, and its stack trace is only on stderr),
  or didn't take its handlers from the serial list below, also gets `part_warnings.nei`. NEI deletes every file in
  `dumps/recipes/` first, so GregtecLink refuses a link or junction at `dumps` or `dumps/recipes`. It copies only
  regular `.jsonl` files changed since the call started (minus 2 s), through `PathGuard`, and never moves or changes
  NEI's files. NEI not ready 10 minutes after the
  world opened (NEI disabled, for example) is `failed`; closing the world before then finishes the run without NEI.
- **Serial handler load** (D188). NEI loads most recipe handlers in parallel on its own worker threads, and GT
  5.09.54.265's are not safe to load that way: the first GT handler to load builds GT's oredict table
  (`GTOreDictUnificator.getNonUnifiedStacks`) while the others already read it. A run then dropped a GT handler
  (`part_warnings.nei`, 4 of 93 client harness runs on CI) or, more often, silently left out some of a GT handler's
  oredict alternatives (`anyOf`). So for the `dumpAll` call GregtecLink moves every crafting handler into NEI's serial
  list (`GuiCraftingRecipe.serialCraftingHandlers`, NEI's own serial ones first, so NEI's order and file names stay
  the same) and empties the parallel one (`craftinghandlers`), then puts NEI's own lists back. Every handler then
  loads on the client thread, one after another; in the dev runtime that took a median 0.87 s for 190 handlers,
  against 0.63 s in parallel. NEI's own switch, `config/NEI/serialhandlers.cfg`, can't reach GT's handlers: GT adds
  them to the list directly. Lists that can't be swapped fail the step. The call's two lists note the threads that
  read them: `counts.nei_serial_load` is 1 when NEI read the serial list on the client thread and the empty parallel
  one, else 0 with `part_warnings.nei` (NEI took its handlers from somewhere else, so GT's may have loaded in
  parallel). Just before the call GregtecLink also has GT build its NEI recipe caches again
  (`GTMod.proxy.reloadNEICache()`): GT keeps each recipe category's list until then, and a recipe lookup earlier in
  the session (R or U in a world opened by hand, which NEI runs in parallel) may have built them short.
- **Crop step** (WO-1.10, D134). Right after NEI's dump, still on the client thread, GregtecLink runs every data
  dumper CropsNH registered in NEI's option list (`tools.dump.cropsnh.*`: crops, parent-combination recipes,
  mutation pools, soils, alternate seeds and six more), as clicking each Dump button under NEI's Tools, Data Dumps,
  Crops NH does, then copies the files they wrote to `dumps/` into the run's `crops/`. CropsNH is reached through
  NEI's API by reflection; nothing is compiled against it. Judged as NEI's step is: `parts.crops` is `ok` only if
  the five dumpers Python reads are registered and, for each, the call threw nothing, NEI's logger got no
  `Error dumping` line during it, it wrote its file, not empty, and the copy succeeded; otherwise `failed`
  (`part_errors.crops`), and `absent` without CropsNH. One of the other six failing only adds
  `part_warnings.crops`. A file in `dumps/` that is a link is refused, since the dumper writes through it. It takes
  about 25 ms.
- **Palette pass** (WO-10.3, D307). Last on the client thread (after the crop step, or when the run finishes without
  NEI), GregtecLink writes `palette.jsonl`: one line per placeable block state (a block with an item, at each meta
  its item's creative variants place), keyed by registry name + meta, with the display name, Forge's
  `hasTileEntity(meta)`, opacity, full cube, light, falls, liquid, creative tab, mod and the six faces' icon names;
  and `textures.jsonl`: each icon name's PNG in the loaded resource packs with its size, sha256 and average colour.
  No numeric block or item ID is written. `parts.palette` is `ok`, `failed` or `skipped_server`; it is not part of
  `complete`. The dev client takes under half a second for it. The client harness checks that GT, GT addons (bartworks,
  GoodGenerator) and other mods (vanilla, AE2, IC2) are in it, that `gregtech:gt.blockmachines`, a chest and AE2's
  drive are flagged in every state and that GT's casings, coils and glass, bartworks' glass, a GoodGenerator casing and
  AE2's sky stone are not, and that the faces' textures resolve.
- Without autoexport (a world opened by hand) the module posts two local chat lines, "GregtecLink: exporting NEI's
  recipes; ..." just before the freeze and "GregtecLink: export <run_id> finished: ..." after it, and stays in the
  world. CropsNH's dumpers add a chat line each.

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
  GT's NEI 2.8.160 and reaches NEI only by reflection. It also puts CropsNH 2.0.140 (2.9.0's) there, with
  BetterBuildersWands 0.13.12-GTNH, whose classes CropsNH's postInit loads; CropsNH brings GTNHLib 0.11.54, the
  pack's version.

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

## Client module (WO-8.1)

The link from Clayton's game to the core (plan 7.8, 7.9, D76, D285; protocol:
[docs/contracts/gregteclink-client-v1.md](../docs/contracts/gregteclink-client-v1.md)). Client side only, with
`client=true`:

- **The link.** A WebSocket to `ws://127.0.0.1:<port>/mod/ws` on its own daemon thread, with the instance's token
  (`gregteclink-play` or `gregteclink-lab`) read from `token_file` at each attempt and never logged. The client is
  GregtecLink's own (`WsClient`, RFC 6455, Java 8; no dependency). It reconnects after 1, 2, 4, 8, then every 15 s, so
  a core restart (the supervisor's, D115) costs a few seconds; while it is down nothing is queued.
- **Position and dimension** at 1 Hz in a world, with the game's paused state.
- **What's this?** On the key (polled on the client tick, so it works in every GUI; not while typing in chat), or when
  the core asks because Clayton said "what's this?": in a container GUI the item under the mouse, through NEI's
  `GuiContainerManager.getStackMouseOver` (its item panel, bookmarks and recipe pages) by reflection, else the slot
  under the mouse; with no GUI, the block looked at and its pick item (a GT machine's item carries its mte id).
  Registry name and meta, plus the NBT without numeric IDs; the core hashes it as the export did. The core's answer
  comes back as a local chat line.
- **Chat lines** as received, with the sender and text when the line is a player's chat message.
- **Notices**: GOS's answers, `!gos` replies and failures as `[GOS] ...` lines printed on this client only, never sent
  to the server (D76).

Its settings are the `client` category of the same file, read only like `modules`:

```
client {
    B:position=true
    B:target=true
    B:chat=true
    B:notices=true
    I:port=8765
    S:token_file=C:/Users/<you>/GregtecOS/config/gregteclink-play.token
}
```

Each feed defaults to on and turns off with `false`: off, `target` answers the core's probe with `kind: off` and
nothing else, and the key sends nothing. With no `token_file` the module does nothing. A malformed file turns
everything off.

### Installing into GTNH-Play or GTNH-Lab

`scripts/install_gregteclink.py` (Clayton, from GOS_APP, the game closed and the core running; D287) installs the
deployed jar with the listener and client modules on and export off: the core issues the instance's token, the
script writes it to `GOS_HOME\config\gregteclink-<instance>.token`, replaces any other `mods/gregteclink*.jar`,
and writes the config above (a feed already set to false stays off). Without `--apply` it only shows what it would
do. `--instance lab` does the same for Lab, which `[sync.lab] exclude` now keeps out of Play's mirror
(`mods/gregteclink*`, `config/gregteclink*`). Steps: [docs/runbooks/gregteclink_client.md](../docs/runbooks/gregteclink_client.md).

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

### Listener harness (WO-6.2)

`./gradlew runServer -Pharness -PharnessListener` runs the server harness with
`harness/listener/gregteclink.cfg` instead (listener on, export off). It deletes only the world, `gregteclink/events/`,
`gregteclink/listener-harness/` and the old result, so the export harness's runs in `gregteclink/exports/` stay for
the integration tests. On server start a Forge fake player, on the superflat bedrock next to spawn:

1. places 10 GT machine kinds through the item's real path (`ItemStack.tryPlaceItemIntoWorld`, which fires Forge's
   `PlaceEvent`): the Electric Blast Furnace (mte 1000, a multiblock controller), hatch 66 and the first 8
   single-block machine classes in GT's registry; one more placement is cancelled by another handler at `HIGHEST`
   (nothing may be left and no event written), and one machine is placed with no `PlaceEvent` (`Item.onItemUse`
   directly, as some mods' placers do);
2. saves the world (`saveAllChunks`, then waits for the chunk writer) and copies its region files to
   `gregteclink/listener-harness/census-a/region/`;
3. breaks four of them (the controller, the hatch, two machines) through `ItemInWorldManager.tryHarvestBlock`
   (`BreakEvent`), posts one more `BreakEvent` that another handler cancels (the machine stays, no event), and
   removes one machine with `setBlockToAir` (no event: only the census can see it);
4. at least 1.1 s of wall time after the first save (Anvil's chunk save times are whole seconds, and the census
   sees a chunk as changed by its slot, save time included), saves and copies again (`census-b/`), checks that each
   chunk phase 3 changed has a later save time there than in `census-a/`, checks the events file and writes `listener-harness/expected.json` (the
   base after each phase) and `harness-result.json`.

Its checks: the module on with export off; 10 kinds with a controller and a hatch; the events file exactly
`machine_built` x10 then `machine_removed` x4 in order, each with the right `mte_id`, `meta` equal to it,
`registry` `gregtech:gt.blockmachines`, one `boot` and `seq` 1..14, and no numeric id key; nothing from the cancelled
place and break; `mteAt` on an unloaded chunk leaves it unloaded (the listener never loads chunks); nothing written
outside `gregteclink/`. `tests/integration/test_machine_events_harness.py` then checks the census of each phase's
region files against `expected.json` and runs the events and the census through the watchers into My base on a DB
built from the export harness's run. CI runs it in the `mod` job, after the export harness.

## Listener module (WO-6.2)

With `B:listener=true` (and `export` off: the module refuses to start beside the export module, so Export never
listens), GregtecLink registers one server-side handler object (D260):

- `BlockEvent.PlaceEvent` and `BlockEvent.BreakEvent` at `LOWEST` priority, not receiving cancelled events, for GT's
  machine block (`GregTechAPI.sBlockMachines`) only. A break notes the `mID` of the `BaseMetaTileEntity` there.
- At the end of the server tick each note is checked against the world: a placement is a `machine_built` if a GT
  machine (`BaseMetaTileEntity`, `mID > 0`) stands there now; a break is a `machine_removed` if that machine is no
  longer there. So a placement or break another mod cancels later is never reported. Cables and pipes
  (`BaseMetaPipeEntity`) are not machines.
- Each event is one JSON line in `<gameDir>/gregteclink/events/machines.jsonl` (open, append, close, through
  `PathGuard`; renamed to `machines.1.jsonl` at start when over 1 MiB): `mte_id`, the block's registry name, `meta`
  (= `mte_id`, the machine item's damage), dimension and position, `boot` and `seq`. Contract:
  [docs/contracts/gregteclink-events-v1.md](../docs/contracts/gregteclink-events-v1.md). GOS's game watchers tail it.
- **Read only.** It reads a loaded chunk's tile-entity map directly (not `World.getTileEntity`, which creates a
  missing tile entity) and never loads a chunk. Placements and removals without a player event (explosions, other
  mods) are not seen here; GOS's tile-entity census of the saved chunks catches them.

V26 (the listener sees GT placements with `mID`) passes in the harness; the Lab check is
[docs/runbooks/machine_events.md](../docs/runbooks/machine_events.md).

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
`part_warnings`; NEI's two crafting handler lists back after the NEI step, the same lists with the same sizes as
before it (D188); every recipe handler loaded on the NEI step's thread (D188: the harness puts a probe handler at the
end of NEI's parallel list, where GT adds its own, and NEI must ask it for its recipes on that thread only, and
`counts.nei_serial_load` must be 1); every `nei/` file listed
with matching sha256, bytes and lines and none unlisted; at least 150 handler files, as many as NEI loaded; lines
copied equal NEI's count and exceed 50,000; one handler id per file and at least one `gt.recipe*` handler; no numeric
`id` in any stack, `anyOf` or `returns`; NEI's files left in `dumps/recipes`; the crop step `ok` with as many files
as CropsNH registered dumpers, the five Python reads among them, all left in `dumps/`, and at least 50 crops in the
crop registry dump; every crop id in NEI's CropsNH tabs (Crop Breeding, crop pages, mutation pools) in that dump
(V33); the palette pass `ok` with no error lines and at least 1,000 states, sorted and once each, with no numeric
id key, states from GT, bartworks, GoodGenerator, vanilla, AE2 and IC2, every state of `gregtech:gt.blockmachines`,
`minecraft:chest` and AE2's drive flagged `has_te` and none of GT's casings, coils (`gt.blockcasings5`) and glass,
bartworks' glass, GoodGenerator's `MAR_Casing` and AE2's sky stone, and a `textures.jsonl` line for every face icon,
at least 70% with a colour, GT's LV casing side and vanilla stone among them (WO-10.3); the old marked world rebuilt (no
`.old-*` copy left) and the decoy untouched; the integrated server stopped before `Minecraft.shutdown()`, and the
world's `level.dat` written after the run finished (the save on quitting). Writes are checked in the two windows where the export's code runs, `saves/` aside: while the GT
parts are written at server start nothing outside `gregteclink/` may change, and from just before NEI's dump to the
end of the run nothing outside `gregteclink/`, `dumps/recipes/` and the crop dumpers' files in `dumps/`. Between them, joining the world makes the game
and other mods write their own files (Forge's `usernamecache.json`, ServerUtilities, NEI's, GT's and AE2's configs),
as for a world opened by hand. NEI counts vary with config state between run folders (AE2 rewrites its
`Facades.cfg` on joining a world, and "Facade Crafting" grows from 2,403 to 2,631 recipes), so only loose thresholds
are checked. A run takes about 2 minutes, and each kept run is about 145 MB (10 are kept).

`-PharnessNeiFail` makes `run/client/dumps/recipes` a file, so NEI's dump fails; the harness then expects
`"nei": "failed"` with `Error dumping recipes` in `part_errors.nei`, in a run that is still complete. CI does not
run it.

Two more modes test D188's NEI step, and CI runs them together as a third client run, after the integration tests
(it replaces `run/client`'s export, which they read):

- `-PharnessNeiHandlerFail` adds a recipe handler that always fails to load to NEI's parallel list for the NEI step.
  Instead of `nei_ok` the harness then expects `nei_handler_failure_reported`: `"nei": "ok"`, one handler lookup
  error, and `part_warnings` with only `nei`, which says a handler failed to load. The other NEI checks still apply.
- `-PharnessNeiLookupFirst` looks up an iron ingot's recipes, as pressing R does (NEI runs it in parallel), just
  before the NEI step, so GT builds its NEI recipe caches first, as in a world opened by hand. The harness then
  expects `nei_gt_caches_rebuilt`: none of the lists GT cached for its recipe categories during that lookup is still
  in GT's cache after the dump.

### Client link harness (WO-8.1, D287)

`-PharnessClientLink` turns the client module on as well (`harness/config-client-link/gregteclink.cfg`: port 18765,
token file `gregteclink-harness/core.token` in `run/client`) and needs a real core: start
`uv run python tests/harness/client_link_core.py --game mod/run/client --home <tmp>` first, from the repo root, and
wait for `<tmp>/ready`. It builds a DB from the newest export in `run/client` (as `gos export collect` and
`gos db build` do; the run must be there before Gradle's clean step wipes it), issues `gregteclink-lab`, writes the
token file and runs the core with fake lanes (no key, no cost). After the export has been checked, still in the
autoexport world, the harness:

1. waits for the link (`client_link_connected`);
2. places four blocks next to the player on the server thread (GT's EBF through its item, so it gets its tile
   entity; a GT casing; GoodGenerator's Field Restriction Casing; AE2's Sky Stone Block), looks at each and presses
   What's this? through the key's own code path;
3. asks the core to restart (`gregteclink-harness/restart-core`, which the runner watches) and waits for the link to
   come back (`reconnected_after_core_restart`);
4. opens a chest GUI with six items (GT steel ingot, bartworks zirconium ingot, GT++ hot arcanite ingot, AE2
   engineering processor, vanilla iron ingot, an IC2 RE-Battery with its charge NBT), moves the mouse onto each slot
   and presses What's this?;
5. checks each of the 10 answers: the item the game reports is the one scripted, the core keys it by registry name
   and meta (the NBT hash for the battery) and names it as the game shows it (`whats_this_10_items`);
6. sends `!gos recipes for steel ingot` as the player's chat (answered) and as `Mallory` (ignored), and counts the
   1 Hz status for 5 s.

lwjgl3ify's `Mouse.setCursorPosition` counts y from the top, LWJGL 2's from the bottom; the harness moves the mouse
again when the first move reads back mirrored. CI runs this mode with the third client run (after the integration
tests). A run adds about 30 s.

## Licences

- GregtecLink's own code (`src/`, `harness/`, `addon.gradle.kts`, `addon.late.gradle.kts`) is MIT, Copyright (c)
  2026 crazie450-prog: see [LICENSE](LICENSE) (D98).
- The build scaffolding (`build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`, `repositories.gradle`,
  `dependencies.gradle`, `.editorconfig`, `.gitattributes`, `.gitignore`) comes from
  [GTNewHorizons/ExampleMod1.7.10](https://github.com/GTNewHorizons/ExampleMod1.7.10) `starter.zip` and is MIT;
  its notice is in [LICENSE-ExampleMod](LICENSE-ExampleMod). That notice covers only those files.
- The Gradle wrapper (`gradlew`, `gradlew.bat`, `gradle/wrapper/`) is Gradle's own and is Apache-2.0 (see the SPDX
  header in `gradlew`).
- GT5-Unofficial, NEI and CropsNH (LGPL-3.0) are linked at runtime only; nothing of theirs is vendored. CropsNH's
  dump formats were read from its source to write the Python reader, which re-implements them. The listener and the
  census use GT's public API (`BaseMetaTileEntity.getMetaTileID`) and the tile-entity NBT names its `writeToNBT`
  writes (`mID`, `mActive`, `mWasShutdown`), read from GT's classes at the pin; no GT code is copied.
- Gregtorio's GPL-3 dumper (`tools/gtnh_dump`) is not copied. The recipe fields follow the "format 1" list in its
  README, as summarised in research A§1.7. The value encoding in `MetaJson` was first written in the same shape as
  Gregtorio's `writeValue` (the same type-check order, a depth-4 cutoff, a GT material as its name, public simple
  fields as the fallback). Review found no copied code, only that resemblance. The fallback has since been changed:
  every public field is encoded recursively, and `str` is kept only for a class's own `toString()`, without identity
  hashes.
