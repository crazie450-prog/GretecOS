# Pack updates: the WO-1.9 playbook

What WO-1.9 adds (plan §7.13, D44, D87, D146–D149) and the steps Clayton runs on the PC. GOS
never adopts a pack by itself, and nothing here writes to Play except `update_play.py`, which
Clayton runs himself from GOS_APP after he has answered **yes**.

Paths below assume `GOS_APP = C:\GregtecOS-app` and GOS_HOME at `%USERPROFILE%\GregtecOS`:

```powershell
$gos = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"
$py  = "C:\GregtecOS-app\current\.venv\Scripts\python.exe"
$upd = "C:\GregtecOS-app\current\scripts\update_play.py"
```

## The pieces

| Command | What it does |
|---|---|
| `gos pack watch [--force]` | The release watcher (D146): a stable GTNH newer than the adopted pack (2.9.0 until one is adopted), from the modpack's GitHub releases feed and DAXXL's release manifests, with a summary (GT and NEI versions, mods added, removed and changed); and the newest release of each addon in `data/curated/addons.tsv` that Play has (or that the D02 gate waits for: GT-Not-Leisure). Plain HTTPS, no API key, each URL at most once a day, backing off 1 h → 24 h after a failure. Records what it saw in `data\packupdate\watch.json`; the report goes to `data\reports\pack-watch-<date>.txt`. |
| `gos pack stock [VERSION]` | The stock-pack check (D87): Play's mod files against a release's (DAXXL's manifest + `gtnh-assets.json`): stock jars present, missing, in another version, and the jars that aren't stock (the addons). Only lists Play's `mods\` names. |
| `gos pack update --start VERSION [--simulate] [--fixture-plans DIR]` | Opens an update and walks its checklist (below). Run `gos pack update` again after each thing you do; it goes on from where it stopped and says what's next. `--redo STEP`, `--confirm backup`, `--abandon`. |
| `gos pack adopt yes\|no` | Your answer to "adopt?" (D44). Refused as gos-dev, and `yes` is refused while the export's GT version differs from the GT pin. |
| `gos pack status` | The checklist, each step's state and the pending question. |
| `gos pack diff [OLD NEW]` | The recipe-level diff of two runs in the DB (D147): added, removed and changed recipes by `r:` key and content hash, grouped by mod, the top changes, the crop diff (WO-1.10), the oredict diff (D145), WO-1.4's manifest diff, and the plans it would flag. |
| `gos pack pin [RUN]` | Exits 1, loudly, when a run's GT version isn't the pin (`config.GT_PIN`, 5.09.54.265). |
| `gos pack fixture-plans DIR SPEC...` | Writes `gos-plan/0` fixture plans from the DB's newest run, for dry runs: `pl-x=minecraft:chest@0` cites every recipe making that item, `pl-y=crop:cropsnh:allium` a crop. |
| `update_play.py [--apply \| --rollback]` | Clayton's Play update (D149), from GOS_APP. Without `--apply` it prints the plan; `--rollback` puts Play back as it was before `--apply`. |

## The checklist (`gos pack update`)

1. **release**: the release summary from DAXXL's manifests.
2. **backup**: a Play world backup from the last 24 h in `GOS_HOME\backups\world\` (the backup
   job's copies). Make one in game with `/backup start`, wait for the job (30 min), or
   `gos pack update --confirm backup --note "..."` for a copy you made another way. A confirm
   is recorded with its time and, like a backup file, counts for 24 h: `update_play.py --apply`
   checks again, so if you play on for days between here and step 7, make a new backup first.
3. **lab**: you update **GTNH-Lab** to the new pack in Prism: the new pack's `mods\`, `config\`
   and `scripts\` (and the addons that load on it, D02); Play stays as it is. Checked: Lab now
   differs from Play; missing plan 5.6 tweaks in Lab are warned about.
4. **lab_export**: Lab is mirrored into Export (Lab's own test-only files left out, GregtecLink
   installed as always), Export autoexports (D100), and the run is built into the update's own
   sandbox DB (`data\packupdate\updates\<id>\home\`), never the main one. The run stays in
   Export's `gregteclink\exports\`, but its id is listed in `data\packupdate\lab-runs.txt`, and
   `gos export collect` and `gos db build` skip every run listed there, so running the normal
   pipeline before Play is updated still leaves the main DB on Play's own run.
5. **diff**: Lab's run against Play's newest run in the main DB, the GT pin check and the plans
   it would flag; the report is `updates\<id>\diff.txt` (and `data\reports\pack-diff-*.txt`),
   plus an `inbox\` note in the vault. Then **"adopt?"** is pending.
6. **adopt**: `gos pack adopt yes` or `no`. No ends the update; Play stays where it is (put Lab
   back with `sync_instances.py --only lab` when you like). An update that is finished
   (declined, abandoned, simulated or adopted) takes no more answers, `--redo` or `--confirm`;
   start a new one instead.
7. **play**: close Play, run `& $py $upd` (dry run), then `& $py $upd --apply`. It refuses unless
   it runs as you (not gos-dev), the update is open and adopted, there is a world backup from
   the last 24 h, Play wasn't already updated for it, Lab still has the tested fingerprint, no
   GTNH client runs and the instance lock is free. It copies every Play file it replaces or
   deletes to `updates\<id>\play-before\`, with `manifest.json` listing those and every file
   and folder the update adds or removes, then mirrors Lab's `mods\`, `config\` and `scripts\`
   into Play. It never touches worlds, saves, options or Prism's components: match Play's
   Forge/LWJGL/Java components to Lab's in Prism if it says they differ. Checked: Play's
   fingerprint now equals Lab's tested one, and Play has the plan 5.6 tweaks
   (`check_tweaks.py --apply` on Play if not).

   **The way back**: `& $py $upd --rollback` (Play closed) restores the copied files, deletes
   the files and folders the update added and re-makes the folders it removed, so Play's
   `mods\`, `config\` and `scripts\` are exactly as before (no second GT jar). It refuses,
   before writing anything, if a file the update added was changed since. The folder is then
   renamed `play-before-rolled-back-<UTC>` and the play step waits again; `--apply` works
   again, or `gos pack update --abandon`. A finished update needs `--update <id>`. Worlds come
   back from the world backup, not from this.
8. **play_export**: the normal export pipeline on Play, built into the main DB with Play's
   fingerprint.
9. **flag**: every plan citing a recipe or crop that changed or vanished gets
   `plan.affected_run`; the pack is recorded as adopted (`data\packupdate\adopted.json`).
   Re-check the G1 golden recipes in the Lab when they exist.
   Then run `gos graph --check-chains`: it lists the line designer's chain templates whose
   recipes the new run changed or dropped (WO-3.3, D190); a stale row is a WO for the template.

A **simulated** update (`--simulate`) walks the same steps, but `update_play.py` only plans,
`play_export` is skipped, the flags are the diff's preview, and nothing is adopted.

## GT pin bump

GOS's GT math (WO-3.1, `planner/gtmath`) is a port of GT 5.09.54.265, gated by GT's own unit
tests, so an export of another GT version is never planned with it quietly: the diff and
`gos pack pin` print **GT PIN MISMATCH**, and `gos pack adopt yes` is refused. When a pack update
changes GT, before answering yes:

1. In a Claude Code session (a WO branch, as any change): set `GT_PIN` (and `PACK_PIN`) in
   `core/gregtec/config.py` to the new version, and every copy of the pin with it:
   - `mod/dependencies.gradle`: the `GT5-Unofficial:<version>:dev` dependency, which is the GT
     the dev runtime and both harnesses export (and its comment);
   - `core/gregtec/ingest/extractor.py`: `PINS[GT]` and `PACK_VERSION` (and the other pins from
     the new release's DAXXL manifest), which label and re-pin gtnh-extractor's dataset;
   - the help text of `gos structures extract` and `load` in `core/gregtec/cli.py`;
   - `CLAUDE.md` ("GT pin ... (GTNH ...)") and plan §0.1/§3;
   - Java and docstring comments that name the version (`grep -rn 5.09.54.265`), checked
     against the new GT source where they describe its code.

   `tests/unit/test_pack_update.py::test_the_pin_has_no_stale_copies` fails while
   `extractor.py`, `dependencies.gradle` or CLAUDE.md disagree with `config.py`. Re-run both
   harnesses (`mod/README.md`) so their runs carry the new GT, then the integration tests.
2. Move the GT math port (`core/gregtec/planner/gtmath`, D173–D175) to the new GT, in the same
   PR. Until this is done `tests/unit/gtmath` fails, which is the gate:
   - Read GT's source at the new tag (GT is LGPL-3.0: read it, never copy it) and compare with
     the old tag: `gregtech/api/util/OverclockCalculator.java`, `ParallelHelper.java`,
     `VoidProtectionHelper.java`, `GTUtility.java` (`powInt`, `log4`, `getTier`, ...),
     `GTRecipe.java` (`maxParallelCalculatedByInputs`), `gregtech/api/logic/ProcessingLogic.java`,
     `MTEMultiBlockBase.java` (`setProcessingLogicPower`, `postCheckRecipe`),
     `MTEExtendedPowerMultiBlockBase.java`, `MTEBasicMachine.java`, `ExoticEnergyInputHelper.java`,
     `GTValues.java`, `HeatingCoilLevel.java`, `MTEElectricBlastFurnace.java`. Re-port what
     changed.
   - Set `GT_VERSION` and `GT_COMMIT` (the commit the tag names) in
     `core/gregtec/planner/gtmath/__init__.py`.
   - Re-vendor GT's unit tests into `tests/unit/gtmath/test_gt_unit_tests.py` as data (inputs
     and expected outputs): `src/test/java/gregtech/overclock/GT_OverclockCalculator_UnitTest.java`,
     the helpers' tests in `gregtech/api/util/GTUtilityTest.java` and
     `gregtech/api/metatileentity/implementations/MTEMultiBlockBaseTest.java`.
   - Regenerate the oracle vectors from the new GT's own classes (a JDK on PATH; the first run
     builds the mod's classpath, a few minutes):
     `uv run python tests/unit/gtmath/oracle/make_vectors.py`. It refuses to write them while
     GT's classes disagree with a ported unit-test expectation. Then the turbine vectors (WO-4.1,
     D231): `uv run python tests/unit/gtmath/oracle/make_turbine_vectors.py`; if a turbine
     controller's `fluidIntoPower` changed, port the change into `gtmath/turbine.py` first.
     Then the boiler and tree-farm vectors (WO-4.2, D233):
     `uv run python tests/unit/gtmath/oracle/make_boiler_vectors.py`; if a 2.9 large boiler or
     `MTETreeFarm` changed, port it into `gtmath/boiler.py` or `gtmath/treefarm.py` first.
     Commit `tests/unit/gtmath/vectors/`.
   - `uv run pytest tests/unit/gtmath` must pass (GT's unit tests, the vectors replayed through
     the port bit for bit, the property tests), then, after the harnesses,
     `GOS_REQUIRE_HARNESS_RUN=1 uv run pytest tests/integration/test_gtmath_harness.py
     tests/integration/test_power_harness.py` (the vectors re-made from GT's jar, every recipe of
     the harness DB, and every rotor and single-block fuel value against GT's in-game values).
3. Merge, `gos deploy`, then `gos pack adopt yes`. `gos pack pin` exits 0 again once Play's new
   export is in the DB. Between the deploy and Play's new export, `gos pack pin`, `gos db build`
   and `gos doctor` report Play's current run as a mismatch: expected, and the reason to update
   Play soon after adopting.

`gos db build` and `gos doctor` exit 1 with **GT PIN MISMATCH** whenever the DB's newest run
has another GT, so a Play updated outside this playbook doesn't go unnoticed.

## Daily watcher (optional)

Until the dashboard shows it, run `& $gos pack watch` when you like, or let Windows run it daily
(as you, once):

```powershell
$a = New-ScheduledTaskAction -Execute $gos -Argument "pack watch"
$t = New-ScheduledTaskTrigger -Daily -At 7:41pm
Register-ScheduledTask -TaskName "GregtecOS pack watch" -Action $a -Trigger $t
```

The report is in `%USERPROFILE%\GregtecOS\data\reports\pack-watch-<date>.txt`.

## Clayton's PC steps for WO-1.9

After the PR is merged and deployed (`gos deploy` from your clone):

1. **Watcher and stock check** (read only):
   ```powershell
   & $gos pack watch --force
   & $gos pack stock
   ```
   Expect "No stable release newer than 2.9.0" (or a real 2.9.x), your addons with their newest
   tags, and a stock check listing TST, 123Technology, Programmable Hatches, worldedit-gtnh and
   PlanNH as not stock and NEI 2.8.171 present.
2. **The Lab dry run** (WO-1.9 acceptance, the shape of L10). Play's export must be in the main
   DB first (`gos export run`, `gos export collect`, `gos db build`). Then:
   ```powershell
   $fx = "$env:USERPROFILE\GregtecOS\data\packupdate\fixtures"
   & $gos pack fixture-plans $fx pl-chest=minecraft:chest@0 pl-furnace=minecraft:furnace@0 pl-allium=crop:cropsnh:allium
   ```
   In Prism (Play closed), change **Lab only**: swap Lab's GT jar for a different GT build
   (`mods\gregtech-5.09.54.265.jar` → another 5.09.54.x jar from GT5-Unofficial's releases that
   loads on 2.9.0; keep the original to put back), and add `scripts\gos_dryrun.zs` with the one
   line `recipes.remove(<minecraft:chest>);` so at least one known recipe vanishes. Then:
   ```powershell
   & $gos pack update --start 2.9.1-sim --simulate --fixture-plans $fx
   # Play has no world yet (WO-0.7), so there is no world backup to find:
   & $gos pack update --confirm backup --note "dry run; Play has no world yet"
   ```
   At "ADOPT?": check `diff.txt` lists the chest recipes as removed, the GT jar's changes by mod,
   `GT PIN MISMATCH` if the jar's version differs, and **pl-chest flagged** (pl-furnace and
   pl-allium only if the diff shows their recipes or crop changed). Then:
   ```powershell
   & $gos pack adopt yes
   & $gos pack update          # waits on the Play step
   & $py $upd                  # dry run only: a simulated update never changes Play
   & $gos pack update          # finishes: "every step done (simulated)"
   ```
3. **Put Lab and Export back**: put the original GT jar back in Lab and delete
   `scripts\gos_dryrun.zs`, or mirror Play into Lab:
   `& $py C:\GregtecOS-app\current\scripts\sync_instances.py --only lab`. Export is mirrored from
   Play again by the next `gos export run`.
4. Send the `gos pack update` output and `diff.txt` back for the PR.
