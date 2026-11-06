# Crop data check (WO-1.10)

WO-1.10's acceptance, on Clayton's PC: **every crop in NEI's Crop Breeding tab is in the DB; 10 spot checks against NEI in the Lab; V33 resolved**. Claude Code has run CropsNH 2.0.140's dumpers in the dev runtime's client harness (GT 5.09.54.265 and its dependencies plus CropsNH, not the pack): 131 crops dumped and loaded, NEI's Crop Breeding tab 91 recipes with 104 crops, every one in the DB (D134, D136). Only a full-pack export says the same for the pack: more crops register when their mods are there (Thaumcraft's, Natura's, Biomes O' Plenty's). The export needs the merged GregtecLink in Export; the spot checks take about 30 minutes.

**Where commands run.** `gos deploy` and the sync run in **your own account** from your clone and GOS_APP, as for every Export update (D91, D98). The `gos` commands after that run as **gos-dev** in its dev clone: they read Export's newest run and write only gos-dev's own `GOS_HOME`. The game parts are in Export (one click) and in the **Lab** (looking at NEI). Nothing here touches Play.

## A. A full-pack export with the crop step (after the PR is merged)

1. In your own account, from your clone: `git pull`, then `uv run gos deploy` (D91, D98).
2. Close Export, then sync it from GOS_APP (it installs the new `gregteclink-<commit>.jar`):

   ```powershell
   & "C:\GregtecOS-app\current\.venv\Scripts\python.exe" "C:\GregtecOS-app\current\scripts\sync_instances.py" --only export
   ```

3. Click **Launch** on GTNH-Export (the autoexport, D100) and let it finish and quit. The crop step adds well under a second to the NEI step.
4. In the newest `GTNH-Export\.minecraft\gregteclink\exports\<run_id>\manifest.json`:
   - `parts` has `"nei": "ok"` and `"crops": "ok"`, and there is no `part_errors.crops`;
   - `counts.crop_dumpers` and `counts.crop_files` are equal (11 with CropsNH 2.0.140);
   - `files` lists `crops/cropRegistry.csv`, `crops/deterministicMutations.csv`, `crops/mutationPools.csv`, `crops/soilRegistry.txt` and `crops/alternateSeed.csv` (and six more of CropsNH's: fertilizer, hydration, Weed-EX and boot protection);
   - `java.charset` is there (`UTF-8` on Java 18 and later; Play and Export run Java 25, D88).
5. As gos-dev:

   ```powershell
   cd C:\GregtecOS-dev\GretecOS
   git switch main
   git pull
   uv sync
   uv run gos export collect
   uv run gos db build
   ```

6. **Expect** in `gos db build`'s output:
   - a `Crops: <N> crops (<M> on NEI's crop pages), <R> parent-combination recipes (<B> in NEI's Crop Breeding tab, <C> for the Crop Breeder only), ...` line, with `<N>` at least 131 and every drop keyed (`<D> drops (<D> keyed by item)`);
   - `NEI's CropsNH tabs: Crop Breeding <B> recipes with <K> crops, ... ; all <S> crops they show are in the crop dump` with `<B>`, `<K>` and `<S>` all above 0 (**this is the WO's first criterion and V33**). `they show no crop, so nothing was checked` instead means NEI's dump has none of CropsNH's tabs: the criterion is not met, send the lines back;
   - no `WARNING: crop data:` line, and no `requirement texts not recognised` line (that one means Export's game language isn't English: CropsNH's requirement texts are then kept, but not sorted into light, sub-soil and Crop Breeder kinds, D137).
   - The saved report (`GOS_HOME\data\reports\db-<run_id>.txt`, gos-dev's `GOS_HOME`) ends with every crop: `crop:<id>`, name, tier, soil, whether NEI shows it.

## B. Ten spot checks against NEI in the Lab

1. As gos-dev, print the DB's facts for the ten crops:

   ```powershell
   uv run gos crops show wheat allium "rubber bonsai" "stone lily" tine redstraw "aluminium oreberry" necrobloom "oil berry" trollplant
   ```

2. Launch **GTNH-Lab** and open any world (`lab-structures` is fine). For each crop, open NEI and search its seeds, e.g. `Wheat Seeds` (CropsNH's seeds are named `<crop> Seeds`). Press **R** on the seeds for the recipe tabs, **U** for usages.
3. **Compare** `gos crops show`'s output with NEI, crop by crop. Compare the two with each other, not with any value written here: the full pack can differ from the dev runtime (more sub-soil blocks, pools or drops).

   | Crop | NEI tabs to open | What to compare |
   |---|---|---|
   | Wheat | Crops, Alternate Seeds | tier, soil, the light requirement, the drop's amount, item and chance; the alternate seed |
   | Allium | Crops, Crop Breeding | tier, each drop's item and chance; the parents |
   | Rubber Bonsai | Crops, Crop Breeding | every drop (three in the dev runtime) with its amount and chance; the parents |
   | Stone Lily | Crops, Crop Breeding | the drop's amount and chance; the sub-soil blocks NEI cycles under the soil slot; the parents |
   | Tine | Crops, Crop Breeding | tier, the block-under requirement; the parents |
   | Redstraw | Crops, Crop Breeding | both light limits and the block under it; the parents |
   | Aluminium Oreberry | Crops, Crop Breeding | the light limit, the block under it, the drop's amount; the parents |
   | Necrobloom | Crops, Crop Breeding | tier, each drop's chance; the parents. (`gos crops show` also prints the Growth above which it spreads weeds, which NEI doesn't show: that is V30's Lab test, WO-6.7) |
   | Oil Berry | Crops, **Crop Breeder** (a GT machine tab) | every soil block NEI cycles in the soil slot; the parents, and that the recipe is Crop Breeder only (`gos crops show` says so; it is not in the Crop Breeding tab) |
   | Trollplant | Crops, **Crop Breeder** | the soil, every drop with its chance; the parents and each catalyst with its amount. `gos crops show` prints a catalyst as CropsNH's dump names it (an item's display name, or an oredict name such as `ore:dustSmallPlutonium241`); NEI shows one item of it |

   NEI shows chances such as `12.50%`; `gos crops show` prints the same basis points as a percentage with two decimals. Amounts and item names should match exactly; tiers are on the Crops page.
4. **Coverage (optional, a few minutes).** Open any Crop Breeding recipe (R on Allium Seeds) and click the lines that join the parents to the child: NEI then lists every recipe of the tab. Scroll through a few pages: every child and parent there is in the saved report's crop list, and the page count times 3 recipes per page is about `<B>`.

## What to send back

- The `Crops:` lines of `gos db build` from part A step 5, and the manifest's `parts`, `counts.crop_*` and any `part_errors`.
- For each spot check: match, or what differs (NEI's value and `gos crops show`'s).
- If part A says `NOT in the crop dump` for some crops, or `WARNING: crop data:` lines: those lines. They mean the pack has crops or recipes the dumpers don't list, which V33 is about.
