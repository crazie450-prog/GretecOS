# Ore intel check (WO-6.6)

WO-6.6's acceptance, on Clayton's PC: **nearest-vein answers check out in the Lab**, and the Lab half of **V23** (does JourneyMap pick up a waypoint file GOS writes while the game runs). Claude Code checked the ranking against WO-1.6's self-authored fixture with every expected vein worked out by hand (`tests/unit/test_ore_intel.py`), the Ores view and a confirmed waypoint end to end against a fake Lab (`dashboard/e2e/ores.spec.ts`), and JourneyMap 5.2.23's own source for when it reads waypoint files (D271). Only the game can show the rest. About 30 minutes; it reuses runbook `ores_check.md`'s `lab-ores` world.

**Where commands run.** As **gos-dev** in its dev clone, with gos-dev's own `gos.toml` (its own `[core] port`, `supervisor_port` and `dashboard_host`, D115/D117) naming `[paths] lab_world = "lab-ores"`. The only file written outside gos-dev's `GOS_HOME` is the confirmed waypoint, in `GTNH-Lab\.minecraft\journeymap\data\sp\lab-ores\waypoints\`. Nothing here touches Play: as gos-dev, Play's JourneyMap folder is refused in code (D75).

## 0. Set up (once)

```powershell
cd C:\GregtecOS-dev\GretecOS
git fetch origin
git switch wo/6.6-ore-intel
uv sync
pnpm -C dashboard install
pnpm -C dashboard build
```

## A. Nearest veins

1. Launch **GTNH-Lab**, open `lab-ores`, and stand somewhere in the Overworld. Note your position (F3: `XYZ`). Press **Esc**, wait 45 s (an autosave), or **Save and Quit to Title** (the position GOS uses is the one in the last save).
2. As gos-dev:

   ```powershell
   uv run gos export collect
   uv run gos ores load --world lab-ores
   uv run gos tool player_pos
   uv run gos tool ore_find '{\"material\": \"Iron\"}'
   uv run gos tool ore_find '{\"material\": \"Iron\", \"dim\": -1}'
   ```

3. **Expect.**
   - `player_pos`: your x, y, z (whole blocks) and `dim: 0 (Overworld)`, `saved_s_ago` a few seconds to a minute.
   - `ore_find`: a `from:` line with the same position; rows with `ref` `vein:0/<cx>/<cz>`; first all veins where Iron is in the **primary** layer (`layer` starts with `primary`), nearest first, then `secondary`, `between`, `sporadic`, each nearest first; a vein you marked depleted last.
   - Pick the first row and fly to its `x`, `z`: the distance F3 shows when you get there matches `distance` (horizontal, within a block or two of the vein's centre). Dig down into `y_min`..`y_max`: you reach the mix's ores. Do this for one more row.
   - The Nether call has no `from:` distances unless you stand in the Nether ("he is in another dimension").

## B. The Ores view and a confirmed waypoint (V23)

Before you start: in JourneyMap's options (**J**, then Options), **Waypoints > Enable Waypoint Manager** must be on (the default). JourneyMap loads waypoint files only while it is on (`WaypointProperties.managerEnabled`, D271).

1. As gos-dev: `uv run gos serve` and open the dashboard on gos-dev's own dashboard name and port; pair it. Open **Ores** (nav), type `Iron`, **Find**.
2. **Expect.** The list in the same order as `ore_find` in A, the map with you at the centre, "Where you are" with your position, "Veins from lab-ores (Lab)".
3. In the game, stay in the Overworld of `lab-ores` (the game running). In the view press **Propose waypoint** on row 1. The rail shows **Confirm a world action** with `Add waypoint "<mix> vein" at x, y, z in dimension 0 in Lab`. Press **Confirm**.
4. **Expect.** A file `<mix> vein_<x>,<y>,<z>.json` in `GTNH-Lab\.minecraft\journeymap\data\sp\lab-ores\waypoints\`. In the game the waypoint is **not** there yet (JourneyMap reads the folder only when mapping starts, D271).
5. Go through a Nether portal and back (or Save and Quit, then open the world again). **Expect** the waypoint in JourneyMap's minimap, its fullscreen map and the Waypoint Manager (**B** by default), at the vein's centre, with the name GOS read back.
6. **Nether scale.** In the Nether, `ore_find` a Nether ore (`Nether Quartz`), propose and confirm row 1, change dimension twice (back to the Overworld and into the Nether again). **Expect** the waypoint at the vein's Nether position (JourneyMap stores it ×8; GOS writes it so).
7. **Optional.** In the Waypoint Manager delete the waypoint: its file goes away. Propose the same vein again and confirm: the file comes back.

## What to send back

- The output of A step 2.
- For each vein you flew to: its row and the F3 distance on arrival.
- Whether JourneyMap's Waypoint Manager was on (B, before you start).
- V23: whether the waypoint appeared before the dimension change (it shouldn't), after it (it should), and at the right place (B step 5 and 6). If it never appears, the `journeymap.log` lines about waypoints (`GTNH-Lab\.minecraft\journeymap\journeymap.log`, `Loaded N waypoints from ...`).
