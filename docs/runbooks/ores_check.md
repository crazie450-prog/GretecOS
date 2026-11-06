# Ore veins check (WO-1.6)

WO-1.6's acceptance, on Clayton's PC: **veins listed for 2 dimensions in Lab**. Claude Code has checked the reader against self-authored caches in VisualProspecting's real layout and against a cache VisualProspecting 1.5.42 wrote in the dev runtime (D127), and the ore mixes against harness exports (D126). Only the real pack can say where VP writes its caches, and only a real full-pack export can say how Galacticraft's dimensions are named. Part A takes about 20 minutes and can be done before the PR is merged; part B needs the merged GregtecLink in Export.

**Where commands run.** The game parts are in the Lab. The `gos` commands run as **gos-dev** in its dev clone. They only read the Lab world, its VisualProspecting caches and (part B) Export's newest run, and write only to gos-dev's own `GOS_HOME`. Nothing here touches Play.

## 0. Set up (once)

In gos-dev's PowerShell:

```powershell
cd C:\GregtecOS-dev\GretecOS
git fetch origin
git switch wo/1.6-ores
uv sync
```

## A. Veins in two dimensions

1. Launch **GTNH-Lab** and create a world for this test: **Create New World**, name `lab-ores`, Game Mode **Creative**, More World Options: World Type **Default**, Allow Cheats **On**. (`lab-structures` is superflat, so it has no ore veins; a new world keeps `lab-progress` clean for the quest tests. Any Lab world with normal terrain works: use its folder name below instead.)
2. **Overworld.** Fly about 300 blocks in a few directions so chunks generate. Then find one GT ore vein (dig down near you, or look in a cave or ravine) and **right-click** one of its large ore blocks with an empty hand, **without holding Shift** (in Creative, GT takes Shift + right-click with an empty hand as a toggle of the ore's natural flag instead, and VisualProspecting may then miss the vein: if chat says `Set ore natural flag to false`, Shift + right-click the block once more so it says `true`, then right-click it without Shift). Chat says `You found a <mix> vein (x,z)!` and `You may find: <ores>`. Note the mix and the position. (Small ores don't count; right-click doesn't break the block.)
3. **Nether.** Build a Nether portal (a 4 × 5 obsidian frame, lit with flint and steel), go through, fly about 200 blocks, and right-click one GT ore block there the same way (empty hand, no Shift). (The Moon works too, if you'd rather go there; use its dimension ID below instead of `-1`.)
4. **Save and Quit to Title.** VisualProspecting writes the server cache when the world saves and the client cache only when you leave the world (or change dimension), so the veins you found show only after this.
5. As gos-dev:

   ```powershell
   uv run gos ores load --world lab-ores
   uv run gos ores list
   uv run gos ores list --dim 0
   uv run gos ores list --dim -1
   ```

6. **Expect.**
   - `load` prints `VisualProspecting world id: lab-ores_<uuid>`, a `server cache:` line with at least 2 dimension files and a `client cache (player <your uuid>):` line, both under `GTNH-Lab\.minecraft\visualprospecting\`, then `Veins:` with a `dim -1` and a `dim 0` line, each with veins and `1 known` (or more: VP adds the veins your team or a prospector found).
   - Before part B, the first line is `Ore mixes: not loaded (...)`: that is expected while Export's newest run predates this GregtecLink, and veins then show only their mix's GT name (`ore.mix.copper`).
   - `list` has a row for `-1` and one for `0` (and `1` only if you went to the End).
   - In `list --dim 0` the vein you clicked has `known` 1, its `mix` is the one chat named, and its `x`/`z` is 8 blocks west and north of the chat's position (VP's chat line adds 8 to the vein's centre block; GOS lists the centre block itself, `chunk × 16 + 8`). The same for `--dim -1`.
7. **Optional: depleted.** In the Lab, open the fullscreen map with VisualProspecting's ore-vein layer on and hover the vein you clicked: its tooltip ends with `Mark depleted with <key>`. Press that key, Save and Quit, and load again: its row shows `depleted` 1.

## B. Ore mixes from a full-pack export (after the PR is merged)

1. In your own account, from your clone: `git pull`, then `uv run gos deploy` (D91, D98).
2. Close Export, then sync it from GOS_APP (it installs the new `gregteclink-<commit>.jar`):

   ```powershell
   & "C:\GregtecOS-app\current\.venv\Scripts\python.exe" "C:\GregtecOS-app\current\scripts\sync_instances.py" --only export
   ```

3. Click **Launch** on GTNH-Export (the autoexport, D100) and let it finish and quit.
4. In the newest `GTNH-Export\.minecraft\gregteclink\exports\<run_id>\manifest.json`: `parts` has `"ore_mixes": "ok"` and `counts.ore_mixes` is above 100 with `ore_mix_errors` 0, and `gt_ore_mixes.json` is listed under `files`.
5. As gos-dev (on `main` now, or still on the branch):

   ```powershell
   uv run gos export collect
   uv run gos ores load --world lab-ores
   uv run gos ores list
   uv run gos ores list --dim 0
   ```

6. **Expect.**
   - `load` starts with `Ore mixes: <N> from export run <run_id> (<M> dimensions named)` and no `note: dimension <id>: no name` lines, and the `Veins:` lines name their dimensions (`dim -1 (Nether)`, `dim 0 (Overworld)`). There is no `warning: veins of mixes this export doesn't have` line.
   - `list` fills the `name` column.
   - `list --dim 0` shows each vein's display name, its four ores (primary, secondary, between, sporadic) and a height range `y`. For the vein you clicked, the ores are the ones chat listed after `You may find:`.
7. **Optional spot check.** Fly to one listed vein you haven't clicked (its `x`, `z`) and dig down into its `y` range: you reach that mix's ores.
8. **Optional: Galacticraft names.** In `gt_ore_mixes.json`, the `dimensions` list near the end names Galacticraft's planets and moons in lower case (`moon`, `mars`): those are the names GT's worldgen matches.

## What to send back

- The output of `gos ores load` and `gos ores list` from part A step 5 (and part B step 5 later).
- For any mismatch: the dimension, the chat lines for the vein you clicked, and its row in `gos ores list --dim <N>`.
- If `load` says `no server cache yet` or `no client cache for this world yet`, the folder names under `GTNH-Lab\.minecraft\visualprospecting\` (`server\` and `client\<uuid>\`). That would mean VP writes somewhere else in the real pack (V07).
