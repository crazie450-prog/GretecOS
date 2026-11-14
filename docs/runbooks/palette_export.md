# Block catalog check (WO-10.3)

WO-10.3's acceptance, on Clayton's PC: **the catalog includes addon blocks; TE blocks are flagged** (plan 8 row 10.3), for the full pack. Claude Code has run the palette pass in the dev runtime's client harness (GT 5.09.54.265 and its dependencies, not the pack): 871 blocks, 7,565 placeable states from 21 mods (GT, its merged addons bartworks, GoodGenerator, TecTech, kekztech and more, and vanilla, AE2, IC2, Tinkers' Construct, Draconic Evolution), every state of `gregtech:gt.blockmachines`, the vanilla chest and AE2's drive flagged, GT's casings, coils and glass and bartworks' glass not (D307). Only a full-pack export says the same for the pack, where every mod adds its blocks and their display names are localised. It takes about 15 minutes after the export.

**Where commands run.** `gos deploy` and the sync run in **your own account** from your clone and GOS_APP, as for every Export update (D91, D98). The `gos` commands after that run as **gos-dev** in its dev clone: they read Export's newest run and the Lab world's `level.dat`, and write only gos-dev's own `GOS_HOME`. The game part is in Export (one click). Nothing here touches Play.

## A. A full-pack export with the palette pass (after the PR is merged)

1. In your own account, from your clone: `git pull`, then `uv run gos deploy` (D91, D98).
2. Close Export, then sync it from GOS_APP (it installs the new `gregteclink-<commit>.jar`):

   ```powershell
   & "C:\GregtecOS-app\current\.venv\Scripts\python.exe" "C:\GregtecOS-app\current\scripts\sync_instances.py" --only export
   ```

3. Click **Launch** on GTNH-Export (the autoexport, D100) and let it finish and quit. The palette pass runs last, after the crop step, and adds under a second in the dev runtime (more in the full pack).
4. In the newest `GTNH-Export\.minecraft\gregteclink\exports\<run_id>\manifest.json`:
   - `parts` has `"palette": "ok"`, and there is no `part_errors.palette`;
   - `files` lists `palette.jsonl` and `textures.jsonl`;
   - `counts.palette_errors` is 0 (any other number: send the `"error"` lines of `palette.jsonl` back), and `counts.palette_states` is well above the dev runtime's 7,565.
5. As gos-dev:

   ```powershell
   cd C:\GregtecOS-dev\GretecOS
   git switch main
   git pull
   uv sync
   uv run gos export collect
   uv run gos db build
   ```

6. **Expect** in `gos db build`'s output a line `Block catalog: <S> block states (<V> item variants) from <M> mods, <T> with a tile entity (never in a shell); <X> textures, <C> with an average colour`, then `by mod:` with the pack's mods and their state counts, and no `WARNING: block catalog not loaded` or `blocks didn't read` line.

## B. Addon blocks are in it, TE blocks are flagged

1. As gos-dev, list a few blocks from GT, a GT addon and non-GT mods (`--plain` leaves tile-entity states out; without it they show `1` in the fifth column, `te`):

   ```powershell
   uv run gos palette blocks casing --mod gregtech
   uv run gos palette blocks glass --mod bartworks
   uv run gos palette blocks --mod GoodGenerator
   uv run gos palette blocks chest --mod minecraft
   uv run gos palette blocks --mod chisel --plain
   uv run gos palette blocks machine --mod gregtech
   ```

2. **Expect:** GT's casings (`gregtech:gt.blockcasings@<meta>`), coils (`gregtech:gt.blockcasings5@<meta>`) and glass, bartworks' glass and GoodGenerator's casings have `te` 0; `minecraft:chest@0` and `gregtech:gt.blockmachines@0` have `te` 1; Chisel (a non-GT mod the dev runtime doesn't have) lists its blocks. A casing or glass with `te` 1, or a mod you expected missing, is the criterion not met: send the lines back.

## C. The palette rule against the Lab world

1. As gos-dev, write a small palette and check it against the Lab world (`[paths] lab_world` in gos-dev's gos.toml, or name it with `--world <folder in Lab's saves\>`). The check only reads the world's `level.dat` (through `safe_open`, copied to memory) and the DB:

   ```powershell
   @"
   gregtech:gt.blockcasings@2
   gregtech:gt.blockcasings5@0
   gregtech:gt.blockglass1@0
   bartworks:BW_GlasBlocks@0
   minecraft:stone@0
   gregtech:gt.blockmachines@0
   minecraft:chest@0
   notamod:fancy_brick@0
   "@ | Set-Content -Encoding utf8 "$env:TEMP\palette-test.txt"
   uv run gos palette check "$env:TEMP\palette-test.txt"
   ```

2. **Expect:** the first five `ok`; `gregtech:gt.blockmachines@0` and `minecraft:chest@0` `REFUSED` (machine block; tile entity); `notamod:fancy_brick@0` `REFUSED` (not in the world's registry, not in the catalog); the last line `5 of 8 entries accepted; the palette is refused` and exit code 1 (`$LASTEXITCODE`). `Can't read the world's registry` means the world was never opened in the Lab or is being saved: open and close it once, then run the check again.

Send back the `Block catalog:` and `by mod:` lines, the six listings' first few lines and the check's output; the reviewer marks the criterion met from them.
