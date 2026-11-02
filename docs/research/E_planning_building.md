# E — Planning math, planner interop, building pipeline (GTNH 2.8.x / 2.9)

Date: 2026-11-01. Method: cloned and read source at the exact tags each GTNH release ships (GT5-Unofficial, StructureLib, StructureCompat, worldedit-gtnh, Schematica-GTNH, Forge 1.7.10, PlanNH, gtnh-flow, web planners), plus two small verification runs (a Python port of GT's overclock calculator checked against GT's own unit tests; a toy lexicographic MILP on gtnh-flow fixtures). GTNH wiki pages mostly refused automated fetch (client_error); only two were read.

Legend: **CONFIRMED** = read in source/doc at the cited URL (or reproduced by running code). **UNCERTAIN** = inferred, not verifiable here, or behaviour not tested in-game.

---

## 0. Version context (drives everything below)

| Pack | Manifest date | GT5-Unofficial | StructureLib | NEI | ModularUI2 | ID-extension mod |
|---|---|---|---|---|---|---|
| 2.8.0 | 2025-09-27 | 5.09.51.470 | 1.4.23 | 2.7.91-GTNH | 2.2.18 | NotEnoughIds 2.1.10 |
| 2.8.4 | 2025-12-23 | 5.09.51.482 | 1.4.23 | 2.8.44-GTNH | 2.2.18 | NotEnoughIds 2.1.10 |
| 2.9.0-beta-1 | 2026-06-07 | 5.09.52.594 | 1.4.38 | 2.8.101-GTNH | 2.3.73 | EndlessIDs 1.7.3 |
| 2.9.0-beta-3 | 2026-09-06 | 5.09.54.133 | 1.4.42 | 2.8.130-GTNH | 2.3.88 | EndlessIDs 1.7.4 |
| 2.9.0 | 2026-10-31 | 5.09.54.265 | 1.4.47 | 2.8.171-GTNH | 2.3.94 | EndlessIDs 1.7.4 |

- CONFIRMED (manifests `releases/manifests/2.8.0.json`, `2.8.4.json`, `2.9.0-beta-1.json`, `2.9.0-beta-3.json`, `2.9.0.json`): https://github.com/GTNewHorizons/DreamAssemblerXXL/tree/master/releases/manifests
- CONFIRMED: tag `2.9.0` exists in https://github.com/GTNewHorizons/GT-New-Horizons-Modpack (along with 2.9.0-RC-1/RC-2, beta-1..3). UNCERTAIN: whether 2.9.0 has been publicly announced as stable. The manifest is dated yesterday, so the player's "2.9 beta" may already be out of date.
- CONFIRMED: WorldEdit is not part of either manifest (the player added it), and Schematica `1.12.6-GTNH` ships in both 2.8.4 and 2.9.0 (same manifests).
- CONFIRMED: the GT 2.8 line branched from master at commit `000a4d9b2` (2025-09-27, = tag 5.09.51.470). Every GT behaviour change merged to master after that date is 2.9-only unless backported (checked with `git merge-base`).

---

## 1. GTNH planners: status and which formats an AI can read or write

### 1.1 gtnh-flow (OrderedSet86)
- **Status.** CONFIRMED: last commit on master is 2024-10-25. Side branches are `autoSolver` (2022-12, message "not currently working as intended") and `better-rank` (2024-06, a refactor). The README says v2 is "in pre-alpha", and that its author is "trying to avoid the way Factorio solvers work, which is to define an 'objective' value for every item". https://github.com/OrderedSet86/gtnh-flow
- **v2 status.** UNCERTAIN (inference): v2 never shipped in this repo. Instead, OrderedSet86 is a top contributor to PlanNH since 2026-07-12 and is credited as the author of its "production balancer". That is effectively the successor solver (see 1.3). Nobody states this outright.
- **Input format** (CONFIRMED, `src/data/loadMachines.py`, `src/gtnh/overclocks.py`, `projects/*.yaml`): a YAML list of machine dicts:
  - `m` machine name; `tier` (the voltage the machine runs at, lower-case lv…max); `I` and `O` maps of ingredient name → amount; `eut`; `dur`. `dur` is in seconds because the repo config sets `DUR_FORMAT: sec`.
  - Optional keys: `number` (machine count pin), `target` (`{ingredient: rate/s}` pin), `group`, `heat`, `coils`, `pipe_casings`, `saw_type`, `material`, `size` (turbines), `mk`/`start` (fusion), `do_not_overclock`, `catalyst`.
  - Any other key is attached to the recipe as an attribute.
  - Chanced outputs are written as fractional amounts (e.g. `refined platinum salt dust: 0.95` in `projects/228_platline.yaml`).
  - Ingredients are pooled by name. Bracket prefixes such as `[recycle] chlorine` are stripped for display (`STRIP_BRACKETS`), and renaming ("chlorine 1") is the documented way to split a pool.
  - There are no item IDs, no item/fluid distinction, and no circuit field.
- **Output.** CONFIRMED: a Graphviz PNG or PDF in `output/` plus console logs. Nothing machine-readable is written. To get numbers out, an AI would have to import the Python modules (`src/graph/_solver.py`) and read the solved graph in memory.
- **Solver behaviour.** CONFIRMED, `src/graph/_solver.py`, `_preProcessing.py`:
  - Builds a linear equality system in sympy and calls `linsolve`. There is no objective.
  - One variable per (machine, product, direction). Per-machine ratio equations; machine→machine edge equalities; extra variables for multi-in/multi-out pools; pins come from `number`/`target`.
  - Cycles are broken before solving: a DFS finds back edges and redirects each to sink plus source (`removeBackEdges`).
  - An item goes to source or sink only if no machine in the chart produces or consumes it.
  - An over-determined system (EmptySet) triggers an inconsistency search and asks the user to rename ingredients. An under-determined one raises an error.
  - README admits it "cannot solve certain parallel edge product situations".
- **Stale or wrong math.** CONFIRMED by reading `src/gtnh/overclocks.py` and `data/*.yaml`:
  - EBF bonus heat is computed as `100 * min(0, user_voltage - 2)`. That can only be ≤ 0, and the index is also off by one (lv = 0). GT uses `+100 K × (tier − 2)` with MV = 2.
  - GT++ multis use a pre-2024 "speed boost then 4×/2× loop" model, with no sub-tick parallels and no amperage rules.
  - Mega multis are flagged `# FIXME: This is not how they work`.
  - `data/turbine_data.yaml` says "last updated 2.2.3".
  - Standard OC has no 1-tick floor. That accidentally mimics multiblock sub-tick parallels but is wrong for single-block machines.
- **Interop value.** CONFIRMED: PlanNH's test harness loads gtnh-flow YAML (`src/test/java/.../harness/GtnhFlowLoader.java`, 16 fixtures in `src/test/resources/gtnh-flow/`). That makes the YAML a de-facto exchange format between the two (tests only, not exposed in-game).

### 1.2 gtnh-velo → gregtech-flow (velolib)
- CONFIRMED: there is no PyPI package `gtnh-velo` (404). The actual names are `gtnhvelo` (0.0.0 → 0.2.0, 2023-01-03 to 2023-01-09) and then `gregtech-flow` (0.4.4 → 1.0.0, last upload 2023-02-12). Both need Python ≥3.11,<4 and are MIT. Repo `velolib/gtnh-velo` redirects to `velolib/gregtech-flow`; last commit 2023-02-14. https://pypi.org/project/gtnhvelo/ · https://pypi.org/project/gregtech-flow/ · https://github.com/velolib/gregtech-flow
- CONFIRMED format: the same YAML recipe list, plus an optional header document (`title`, `version`, `creator`, `description`). It ships JSON Schemas (`src/gregtech/flow/resources/project_schema.json`, `header_schema.json`) usable to validate AI-generated projects. Output is Graphviz images. The solver and data are inherited from gtnh-flow circa early 2023, so the same staleness applies.
- Recommendation: don't depend on it. At most, borrow `project_schema.json` to validate YAML the AI writes.

### 1.3 PlanNH (in-game NEI flowchart planner)
- **Two repos.** CONFIRMED: https://github.com/Sersium/PlanNH (43 commits, 2026-06-07 → 2026-06-13) shares its root commit with https://github.com/GTNewHorizons/PlanNH. The GTNH one is the active upstream: 416 commits, tags 0.1.0 … 0.1.9 (0.1.9 = 2026-10-22), branch activity up to 2026-10-31. Main authors are sbancuz, OrderedSet, TheYoingLad. MIT.
- **Pack requirement.** CONFIRMED (`client/UnsupportedPackException.java`, also in tag 0.1.9): it refuses to load below **GTNH 2.9.0-RC-2** (GT ≥ 5.09.54.192, NEI ≥ 2.8.148-GTNH, ModularUI2 ≥ 2.3.55, GTNHLib ≥ 0.11.5). The 2.9.0 manifest satisfies this; 2.8.4 does not. Even 0.1.0 compiled against GT 5.09.52.594 / NEI 2.8.101 / MUI2 2.3.73, which are 2.9-beta-era libraries. UNCERTAIN: whether any PlanNH build runs on 2.8.x (very likely not). It is not in any GTNH manifest, so it is a separate install.
- **Features.** CONFIRMED (README): F8 opens it. It has groups, notes, an "ojAlgo" balancer (`org.ojalgo:ojalgo:57.3.1` shaded), a summary panel, mod integrations, per-node machine configuration, chat/clipboard sharing, and Mermaid export. Optional "Offscreen" separate window (F7).
- **Save files.** CONFIRMED, `api/PlanAPI.java`, `data/serialization/Serializer.java`:
  - Primary file: `<.minecraft>/saves/NEI/<NEI worldPath>/plannh/plannh.dat`, or `<.minecraft>/plannh/plannh.dat` when no world is loaded. Written atomically with a `.bak` copy.
  - `plannh.dat` is a JSON "Plan" whose `graphs[]` entries are **base64(gzip(Gson JSON of Graph))**. The decoder also accepts graphs given as plain JSON objects.
  - `DEBUG_MODE = true` is hard-coded in master and 0.1.9, so every save also writes **`plannh_debug.json`** next to it, with graphs as plain JSON.
  - CONFIRMED (NEI `ClientHandler.java`): the NEI worldPath for multiplayer is `remote/<ip~port>`. UNCERTAIN: the exact singleplayer worldPath string (set by NEI's server handshake; likely `local/<folder>`).
- **Graph JSON shape.** CONFIRMED (`data/flowchart/*.java`):
  - Top-level arrays: `nodes`, `edges`, `notes`, `groups`. Fields: `name`, `zoom`, `panX/Y`, `balanceMode` (default `AUTO`).
  - A node stores `id`, position, `machineName`, `recipeId`, `machineConfig` {`profileId`, `settings`, `targetKind`} and `extractorIndex`.
  - Node **inputs/outputs are `transient`** (not saved). `recipeId` is NEI's `Recipe.RecipeId` {`handlerName`, `ingredients[]`, `result`}, with item stacks as NBT-JSON. On load the node is re-resolved through NEI (`RecipeHandlerRef.of(recipeId)`); unresolvable nodes are kept as "unavailable".
  - Edges are {`id`, `sourceNodeId`, `targetNodeId`, `sourceOutputIndex`/`targetInputIndex` as `{left,right}` pairs, `inactive`}.
- **Import paths.** CONFIRMED (`PlanAPI.java`, `client/ImportCommand.java`):
  - Clipboard import of one graph (base64(gzip(JSON))).
  - NEI chat item-link share. The `/plannh_import <nbt>` client command takes an ItemStack NBT whose `tag.plannh_data` holds the encoded graph.
  - Mermaid export carries node labels and notes only.
- **What an AI can do with it.**
  - *Read:* parse `plannh_debug.json`. It has topology, machine settings and RecipeIds, but no rates, so a recipe database is needed to rebuild rates.
  - *Write:* emit Graph JSON → gzip → base64 → Windows clipboard → player clicks import. This only works if every RecipeId exactly matches NEI's handler name and ingredient stacks in the player's install.
  - UNCERTAIN: format stability. It changed twice in October 2026 ("Finish edge migration" 10-03, "Simplify graph serialization" 10-18), so any writer must be pinned to a PlanNH version.
- **Math.** CONFIRMED:
  - The old (June) GT provider called GT's own `gregtech.api.util.OverclockCalculator` in-game, with per-node settings: voltage, amps, speed, parallels, perfect/laser OC, heat, discount, max OCs, tier skips.
  - Current master still does: `data/effect/steps/GTOverclockStep.java` builds a GT `OverclockCalculator` under a single "GT Unified" profile. The OC math therefore always matches the installed GT version.
  - Machine bonuses are per-node settings, not auto-detected, in the versions read.
  - Branches `auto-oc`, `gt-oc-api`, `gt-structure-params`, `gt-processing-spec` (2026-09/10) suggest auto-detection is in progress. UNCERTAIN: their state.
- **Balancer.** CONFIRMED (`data/flowchart/balancer/*`): see §3.2.

### 1.4 Web / YAFC-style planners (2025–2026)

| Tool | GTNH data version | Solver | AI-writable / readable format | Status |
|---|---|---|---|---|
| **GTNH Factory Flow** — https://gtnhplanner.com (repo https://github.com/jackwrichards/gtnh-factory-flow, MIT) | **2.9 only** ("2.8.4 is no longer supported", `src/lib/datasets/remote.ts`) | LP on HiGHS (`highs` npm), equation-based, "nothing from nowhere" doctrine (`docs/solver-equations.md`) | **Project JSON**, `schemaVersion: 1`, zod schema `factoryProjectSchema` in `src/lib/model/schemas.ts`. Recipes are self-contained in the file (`recipes[]`, `nodes[]`, `storages[]`, `edges[]`, …), so no NEI IDs are needed. Also share links and browser storage. Datasets come from an in-repo Forge "GTNH Calculation Oracle" mod run on a real client. | Very active (v4.7.0, 2026-10-26). CONFIRMED |
| GTNH Factory Flow (original fork) https://github.com/Samiracle64/gtnh-factory-flow; deployed gtnh.samiracle.fr / gtnh.denissov.tech | per manifest | TS solver | plan JSON import/export | Last commit 2026-06-03. CONFIRMED |
| **gtcalc** https://gtcalc.app/gtnh (https://github.com/syhros/gtcalc, MIT) | "exported straight from a GT New Horizons 2.8 instance" (site text) | "Auto Ratio / Max Flow / Best Route" | JSON import/export, **including GTNH Planner (gtnhplanner.com) project files** (`src/importers.ts`) | Active (2026-10-31). CONFIRMED |
| **ShadowTheAge GTNH Calculator** (author of YAFC) https://shadowtheage.github.io/gtnh/ (https://github.com/ShadowTheAge/gtnh, MIT) | 2.8.0 main, 2.7.4 archive (`Other versions.md`) | `javascript-lp-solver`, objective = minimise the sum of recipe counts (`src/solver.ts`) | Page JSON in localStorage. Share link = URL fragment of URL-safe base64(deflate(JSON)) (`src/menu.ts`). Data built with the NESQL exporter. | Last commit 2026-08-09; README says "won't be an actively maintained project". CONFIRMED |
| lgrones "GTNH Planner" https://github.com/lgrones/gtnh | 2.8.4 (GT 5.09.51.482) | **TypeScript port of GT `OverclockCalculator` + `ParallelHelper`**, plus a Java-source extractor that produced `src/data/machines.json` (214 controllers: parallel formula, EU/speed modifiers, perfect OC, coil params) | Closed-access Firebase/Yjs app; admin export/import scripts in `tools/` | 2026-09-27. **No LICENSE file**, so reuse needs the author's permission. CONFIRMED |
| GTNH Atlas https://milky-way-galaxy.github.io/gtnhnei/ | not stated | "calculators", "routes", shows "gtnh-flow diagrams" | not stated | CONFIRMED page exists; details UNCERTAIN (no source link) |
| "GTNH Power Planner" spreadsheet ("Version(s) 2.9, Made by Fox") | 2.9 | spreadsheet | xlsx (Google Sheets export) | UNCERTAIN: only seen referenced in gtnhplanner `docs/power-planner-math.md`; URL not found |

### 1.5 Recommendation (planner interop)
1. Keep Gregtec OS's **own canonical plan JSON** as the single source of truth. Each recipe node holds: item/fluid IDs, chances, non-consumed flags, circuit number, base EU/t, base ticks, special value (heat), GT version, and the machine configuration.
2. Solve it with your own solver (§3). Write exporters, in priority order:
   - (a) gtnh-flow YAML: for Graphviz rendering and because PlanNH's harness reads it.
   - (b) gtnhplanner.com project JSON: self-contained recipes, so it is the easiest for an AI to write. It only targets 2.9.
   - (c) PlanNH clipboard string: only on 2.9, only when RecipeIds come from the same install, pinned to a PlanNH version.
   - (d) Mermaid, for chat and voice summaries.
3. Read PlanNH via `plannh_debug.json` rather than decoding `plannh.dat`.
4. Do not build on gregtech-flow.

---

## 2. Planning math (GT5U at the pack's tag)

Primary sources, pinned per pack. Prefix `https://github.com/GTNewHorizons/GT5-Unofficial/blob/<tag>/` (2.9.0 = `5.09.54.265`, 2.8.4 = `5.09.51.482`):
- `src/main/java/gregtech/api/util/OverclockCalculator.java` (OC rules)
- `src/main/java/gregtech/api/util/ParallelHelper.java` (parallels, batch, chances)
- `src/main/java/gregtech/api/logic/ProcessingLogic.java` (wiring)
- `src/main/java/gregtech/api/metatileentity/implementations/MTEMultiBlockBase.java` (power intake, parallels API)
- `src/main/java/gregtech/api/metatileentity/implementations/MTEBasicMachine.java` (single blocks)
- `src/main/java/gregtech/common/tileentities/machines/multi/MTEElectricBlastFurnace.java`, `src/main/java/gregtech/api/enums/HeatingCoilLevel.java`
- `src/main/java/gregtech/api/util/GTRecipe.java`, `GTRecipeConstants.java`
- `src/main/java/gregtech/api/enums/GTValues.java`
- Test oracle: `src/test/java/gregtech/overclock/GT_OverclockCalculator_UnitTest.java` (50 tests)
- 2.9 tooltip templates: `src/main/resources/assets/gregtech/lang/en_US/tooltip/*.md` (429 files; numbers injected from Java).

### 2.1 Tiers
CONFIRMED (`GTValues.java`): `V = {8, 32, 128, 512, 2048, 8192, 32768, 131072, 524288, 2097152, 8388608, 33554432, 134217728, 536870912, 2^31−8}` for ULV…MAX. `VP = V·30/32` (integer). `GTUtility.getTier(v)` is the smallest tier with `v ≤ V[tier]`. `log4` and `log4ceil` are bitwise integer functions.

### 2.2 Overclock rules (2.9.0 `OverclockCalculator.calculateOverclock`)
CONFIRMED (code):
- `recipePower = recipeEUt × parallel × eutModifier × heatDiscount`
- `machinePower = voltage × (amperageOC ? amps : min(amps, parallel))`
- `overclocks = min(maxOverclocks, tiersAbove(machinePower, recipePower))`, with recipe power floored at 32 (ULV treated as LV).
- If `amperageOC` is off, overclocks are also capped by `log4ceil(V/8) − log4ceil(recipeEUt/8)`. Overclocks are never negative.
- **Standard OC:** EU ×4, duration ÷2. **Perfect OC** (`enablePerfectOC`): duration ÷4.
- **Heat:** `heatOverclocks = min(floor(surplus/1800), overclocks)` are ÷4. The heat discount is `0.95^floor(surplus/900)`. Since 2.9 (#8321) the surplus is clamped at ≥0.
- Consumption is `ceil(recipePower × 4^OCs)`. Duration is truncated with a 1-tick floor.
- **Laser OC** (`setLaserOC`): regular OCs first, then each extra OC multiplies EU by `(4 + 0.3·n)`.
- **Tier skipping** (#4318, 2025-05): a recipe is valid when `recipeEUt ≤ voltage × 4^maxTierSkips`. The default is 1, so a multi with enough amps can run one tier above its average hatch voltage.
- **Sub-tick:** for multis, `ParallelHelper` multiplies max parallel by `calculateMultiplierUnderOneTick()`. Overclocks beyond 1 tick buy parallels, so throughput keeps scaling with power. This applies to every multiblock; lgrones' notes reach the same conclusion.

**Single blocks.** CONFIRMED (`MTEBasicMachine.calculateOverclockedNess`): `setEUt(V[tier] × mAmperage)` with amperageOC left false. There is no ParallelHelper, so the 1-tick floor is real.

**OverclockCalculator commits:**
- Included in 2.8.x (CONFIRMED ancestors of 5.09.51.482): refactor #4038 (2025-03-16), sub-tick multiplier #4146 (2025-05-19), sub-tick correction #4346, tier-skip mechanic #4318 (2025-05-25), speed-overflow fix #4598 (2025-07-13).
- **2.9-only** (CONFIRMED not ancestors of 5.09.51.482; in 5.09.54.265): correction-multiplier fix #5015 (2025-10-08), overflow fix #5467 (2025-12-01), unsafe-cast guard #6675 (2026-05-13), AAL may OC under 1 t + laser-OC refactor #7433 (2026-07-25), power-tier vs recipe-tier distinction #7788 (2026-08-18), floating-point `getTiersAbove` fix #8012 (2026-10-04), no negative heat surplus #8321 (2026-10-05). Commit messages are on GT5U master; PR links follow `https://github.com/GTNewHorizons/GT5-Unofficial/pull/<n>`.

### 2.3 Multiblock power intake
CONFIRMED (`MTEMultiBlockBase.setProcessingLogicPower`):
- With **exactly one energy hatch and no exotic hatch, the multi uses 1 A**, even though the hatch accepts 2 A. Otherwise amps = `ExoticEnergyInputHelper.getMaxWorkingInputAmpsMulti` (summed working amps).
  - "2 A per standard hatch" comes from lgrones' `MULTIBLOCK_PORT.md` and is consistent with GT unit test `doubleEnergyHatchOC_Test` (amps = 4). UNCERTAIN: the helper itself was not opened.
- Voltage = average hatch voltage. **amperageOC is always true for multis.**
- `getMaxInputVoltage()` (the sum of hatch voltages) is a different quantity. It feeds per-tier parallel formulas and the EBF heat bonus.

### 2.4 Parallels and batch mode
CONFIRMED (`ParallelHelper.determineParallel`):
- `parallel = min(maxParallel × subTickMultiplier, floor(availableEU / ceil(recipeEUt × eutModifier × heatDiscount)), inputs available, output space if void protection is on)`.
- The OC is then computed on the whole parallel group.
- Batch mode (max batch 128; only when duration < 128 t) adds parallels and stretches duration by the same factor, so **throughput is unchanged**.
- Per-machine `getMaxParallelRecipes()`:
  - The base class returns 1. lgrones' extraction at 5.09.51.482 found 144 of 214 controllers at 1 (EBF included), 32 formula-based, 15 constant.
  - Example formula: 2.8.4 Industrial Centrifuge `6 × tier`, EU ×0.9, speed ÷2.25 (from `machines.json`).
  - **2.9.0 changes it:** "momentum" 4→8 parallels per tier and 2×→3× speed (`MTEIndustrialCentrifuge.java` constants `BASE_PARALLEL_PER_TIER=4`, `SPEED=2.0`, `EXTRA_SPEED=1.0`, `EU_EFFICIENCY=0.9`).
- So per-machine tables must be built per pack version. CONFIRMED.
- **Addons.** CONFIRMED: Twist Space Technology multis extend GT `MTEExtendedPowerMultiBlockBase` with their own `GTCM_ProcessingLogic` and per-machine `getEuModifier` / `getSpeedBonus` / `getMaxParallelRecipes` / `enablePerfectOverclock`, plus a "wireless mode" drawing from GT's global EU network (`GTCM_MultiMachineBase.java`, `WirelessEnergyMultiMachineBase.java`, https://github.com/Nxer/Twist-Space-Technology-Mod). They fit the same parameter family but need their own table. UNCERTAIN: GT-Not-Leisure and 123Technology were not read.

### 2.5 EBF heat and coils
- CONFIRMED (`HeatingCoilLevel.getHeat() = 1 + 900 × ordinal`): Cupronickel 1801 … Eternal 13501.
- CONFIRMED (`MTEElectricBlastFurnace.java`): EBF heat = coil heat + 100 × (tier(getMaxInputVoltage) − MV). The formula is identical at 5.09.51.482.
- CONFIRMED wiki statement: "With an HV Energy Hatch or two MV Hatches, the EBF has a 100K heat bonus." https://wiki.gtnewhorizons.com/wiki/Electric_Blast_Furnace
- CONFIRMED: recipe heat is `mSpecialValue` (metadata `COIL_HEAT`). Gas-boosted variants are separate recipes in the map.

### 2.6 Chanced outputs
CONFIRMED:
- Chances are integers out of 10000 for item and fluid outputs and inputs (`mOutputChances`, `mFluidOutputChances`, … in `GTRecipe.java`). Values above 10000 give guaranteed copies plus a fractional chance.
- The game rolls a binomial (normal approximation for large parallels) in `ParallelHelper.calculateChancedOutputMultiplier`.
- A planner should use the expected value `amount × chance/10000`.

### 2.7 Programmed circuits and non-consumed inputs
- CONFIRMED: `GTUtility.getIntegratedCircuit(n)` returns a **stack size 0** item with damage = config n, i.e. non-consumed. Model it as a catalyst: coefficient 0, but a constraint on bus layout (one circuit per input bus or recipe).
- CONFIRMED (README, https://github.com/reobf/Programmable-Hatches-Mod): Programmable Hatches adds hatches that handle "virtual circuits" from AE2 patterns. This removes much of the bus-separation constraint if the player uses them.

### 2.8 Throughput
- CONFIRMED: rate per machine = `amount × parallels × chance × 20 / ticks`.
- CONFIRMED (`MTEHatchInput.getCapacity`): input hatch capacity is `8000 × 2^tier` L.
- CONFIRMED: multis truncate outputs to `getItemOutputLimit()` / `getFluidOutputLimit()`.
- UNCERTAIN: pipe capacities and cover rates were only located, not tabulated. They are set per registration in `MTEFluidPipe` (`mCapacity`), `MTEItemPipe`, `CoverPump`, `CoverConveyor`.

### 2.9 Verification run
CONFIRMED (ran locally): a ~40-line Python port of the 2.9.0 non-laser path reproduced **10/10** of the first GT unit tests exactly: EBF perfect/imperfect/heat-discount cases, general perfect/imperfect, and 4 A / 256 A amperage OC, including ULV. So porting is low-risk if pinned to the tag and checked against GT's own tests.

### 2.10 Recommendation (math)
1. Port `OverclockCalculator` + `ParallelHelper` (+ `setProcessingLogicPower` rules) to Python per pack tag. Gate the port with GT's 50 unit tests.
2. Build a per-controller parameter table for the player's exact GT version. Extract it from source the way lgrones did, or harvest it at runtime.
3. Hand-author entries for TST, GT-Not-Leisure and 123Technology machines the player actually builds.
4. Where possible, cross-check one machine per family against the in-game NEI/tooltip numbers.

---

## 3. Solver approach (byproducts, loops)

### 3.1 How gtnh-flow avoids per-item objective values
CONFIRMED (§1.1): it never optimises.
- The user pins one or more machines and every other quantity follows from equality constraints.
- Loops are cut into sink/source pairs before solving. Boundary items are only those with no internal producer or consumer.
- The cost is brittleness: over- or under-determined charts fail, and identical product names on parallel edges break it.

### 3.2 How the 2026 successors do it
- **PlanNH `AUTO`.** CONFIRMED (`balancer/BalanceMode.java`, `Solver.java`, `stages/*.java`): a lexicographic LP/MILP chain with fractional machine extents, flow variables per edge, external variables per connected port, and conservation rows. The row scaling `1/max(1, qtyPerCraft)` is as described in gtnhplanner's study of PlanNH (`docs/solver-equations.md`), not re-read in PlanNH.
  1. Fast path: a zero-gate LP.
  2. **Gate count:** MILP with big-M, minimising how many connected ports touch the outside (LP deletion filter, then a certified MILP within a node budget).
  3. **External quantity**, with gates capped at the previous optimum.
  4. **Internal flow**, then a canonicalisation solve.
  5. **"Every machine runs"** audit: idle machines are floored at a rate the chart already achieves, and the chain re-runs.
  - Pins: fixed copies, target rate, extent.
  - OUTPUT/INPUT modes use integer counts (MILP).
  - Determinism is tested by solving the same chart 12 times.
  - Ground truth (`GroundTruthTest.java`): 230 platline → all machines run, ≤2 externals. Nanocircuits (394 machines) → zero gates via the fast path.
- **gtnhplanner.com.** CONFIRMED (`docs/solver-equations.md`): it studied PlanNH and adopted the same variable families and staged ties on HiGHS. It is a *descriptive* solver: machine counts are inputs and the outside world exists only where the player put a source, drawer or trash.
- **ShadowTheAge.** CONFIRMED: a single LP minimising the sum of recipe counts, which is a surrogate cost.

### 3.3 Toy reproduction (CONFIRMED by running; SciPy 1.18.1 `scipy.optimize.milp` / HiGHS, on gtnh-flow fixtures from PlanNH's test resources)
- `light_fuel_hydrogen_loop.yaml` (CR ↔ electrolyser hydrogen loop):
  - With naive "minimise imports, then machines", it degenerates: it **exports the intermediate** (sulfuric light fuel) and leaves the loop idle.
  - With gtnh-flow's boundary rule (only terminal items may cross the boundary; connected items need a gate), it closes the loop with 0 gates: CR 0.0167, electrolyser 0.015, distillery 1.
- `230_platline.yaml`:
  - Gate minimisation alone found a **1-gate shortcut**: import an intermediate, leave 27 machines idle.
  - Adding the every-machine-runs replay with a small relative floor (10⁻³ of the smallest running rate) gave **2 gates with all machines running**, matching PlanNH's "≤2" ground truth.
  - A large floor (1×) gave 3 gates and inflated machine counts.
  - big-M = 10⁷ produced spurious infeasibility in HiGHS; 10⁴–10⁵ worked.
  - UNCERTAIN: the toy is not PlanNH's exact algorithm (its floors are "pinned-connected" and rate-derived).

### 3.4 Recommended formulation (Python)
- **Variables:** `x_r ≥ 0` crafts/s per recipe node, with overclocked capacity per machine from §2; `imp_k, exp_k ≥ 0` per item and port; binaries `g_k` for gates on connected items only.
- **Rows:** conservation `Σ_r a_rk x_r + imp_k − exp_k = 0`. Terminal items are free (raw inputs import only, products export only). Connected items: `imp_k + exp_k ≤ M·g_k`, with M scaled per row.
- **Pins:** machine count `x_r·t_r = N`, or output rate. EU is a pseudo-item: consumers use `−EU/t × ticks` per craft; generators supply (§4).
- **Stages:** (1) min Σg; (2) min gated quantity (+ tiny raw-input tiebreak); (3) min Σ machines `x_r·t_r` (or internal flow); (4) every-machine-runs replay. Then integerise: `ceil` of machine counts and report utilisation, or run an integer-count MILP when the player wants buildable counts.
- **Chanced outputs:** expected values. **Circuits:** non-consumed (layout constraint only). **Alternative recipes:** let the LP choose, but expose ties as "choices" the way PlanNH does.
- **Library:** `scipy.optimize.milp` (HiGHS, no extra install on Windows) is enough. OR-Tools (CP-SAT/SCIP) or PuLP+HiGHS are alternatives. Use single-threaded solves for determinism.
- **Do not** assign per-item costs. The structural lexicographic objectives above are what both modern planners use.

---

## 4. Power lines

### 4.1 How fuels and generators are represented in GT5U
- CONFIRMED (`gregtech/api/recipe/RecipeMaps.java` @ 5.09.54.265): fuels are `GTRecipe`s in dedicated maps:
  - `dieselFuels`, `extremeDieselFuels`, `gasTurbineFuels`, `hotFuels`, `denseLiquidFuels` (semi-fluid), `plasmaFuels`, `magicFuels`
  - `small/large/huge/extreme/ultraHugeNaquadahReactorFuels`, `rocketFuels`, `rtgFuels`, `largeBoilerFakeFuels`
  - Each is a one-item (cell) input, EU/t = duration = 0 (`FuelBackend`), and **`mSpecialValue` = `FUEL_VALUE`**. Generic `GTRecipeConstants.Fuel` dispatches by `FUEL_TYPE` ordinal (DieselFuel, GasTurbine, HotFuel, SemiFluid, PlasmaTurbine, Magic).
- CONFIRMED: `FUEL_VALUE` is EU per litre for liquid engines and turbines. The Large Combustion Engine computes `fuelConsumption = nominalOutput / fuelValue` (L/t) (`MTELargeCombustionEngine.java`). The gas turbine's `getFuelValue` returns the recipe's `mSpecialValue`.
- **Gas turbine math.** CONFIRMED (`turbines/MTELargeTurbineGas.fluidIntoPower`, `api/util/TurbineStatCalculator.java`):
  - optimal flow (L/t) = `optimalGasFlow / fuelValue`, where `optimalFlow = speedMultiplier × toolSpeed × 50`. Loose fit uses `2 × 1.05^((eff−0.8)·20)` × optimal, with efficiency ×0.95.
  - Flow is allowed up to 150/300/450 % of optimal by the rotor overflow tier, with an overflow-efficiency penalty.
  - EU = `flow × fuelValue × efficiency`, capped by dynamo output.
  - The TurbineStatCalculator formulas are identical at 5.09.51.482. 2.9's new gas turbine `fluidIntoPower` is a cleanup of 2.8.4's, and the 2.9 "Legacy" copy is byte-identical to 2.8.4.
- **2.9 rework.** CONFIRMED:
  - New controllers `turbines/MTELargeTurbine{Gas,Steam,HPSteam,SCSteam,Plasma}`, plus new `MTELargeCombustionEngine` and `MTELargeBoilerBase` families.
  - The **old controller IDs remain registered as `*Legacy` classes** (`LoaderMetaTileEntities.java`, "structure deprecated" tooltip). Existing builds keep old structures; new builds use new IDs and structures.
  - UNCERTAIN: exact LCE/boiler numeric differences legacy vs new (not diffed).
  - 2.9 tooltips (`tooltip/large-combustion-engine.md`): lubricant L/h, optional oxygen boost, default vs boosted output and efficiency.
- **In planners.** CONFIRMED:
  - gtnh-flow models EU as an output ingredient (`modifyTurbine` sets `O = {EU: …}`) and auto-burns leftovers via `POWER_LINE` using `data/power_data.yaml` (EU per 1000 L; stale).
  - PlanNH (June) had a `gregtech:generator` profile (fuel efficiency, parallels, machines).
  - gtnhplanner bases its power sector on the community "GTNH Power Planner" 2.9 spreadsheet (UNCERTAIN: the sheet itself was not seen).

### 4.2 GTNH wiki power references
- CONFIRMED read: https://wiki.gtnewhorizons.com/wiki/Power_Generation. It is a tiered overview (steam → LCE/benzene → HOG/CBD → turbines → plasma → naquadah). It cites 2.3.3 / 2.4.0 / "up to 2.6.0" and has **no fuel tables**, so treat it as dated.
- Existing per search, content UNCERTAIN (fetch refused):
  - https://wiki.gtnewhorizons.com/wiki/Large_Combustion_Engine
  - https://wiki.gtnewhorizons.com/wiki/Large_Gas_Turbine
  - https://wiki.gtnewhorizons.com/wiki/XL_Turbo_Gas_Turbine
  - https://wiki.gtnewhorizons.com/wiki/UCFE
  - https://wiki.gtnewhorizons.com/wiki/Singleblock_Generators
  - https://wiki.gtnewhorizons.com/wiki/Category:Generators
  - https://wiki.gtnewhorizons.com/wiki/Fusion_Reactor
- gtnh-flow's `projects/power/{benzene,oil,naqfuel,rocketfuel,hog,fish,offmeta}` are worked examples but are 2.2–2.6 era.

### 4.3 Recommendation (power)
1. Take fuel values from the player's own recipe dump (the fuel maps above), not from wiki or gtnh-flow tables.
2. Model generators as recipes producing EU, with turbine output computed from rotor stats (port `TurbineStatCalculator` + `fluidIntoPower`).
3. Track legacy vs new controllers on 2.9.
4. Size cables and hatches from the summed EU/t per tier.

---

## 5. Building pipeline (1.7.10)

### 5.1 Schematic formats
- CONFIRMED: worldedit-gtnh supports **only MCEdit `.schematic`** (`ClipboardFormat.SCHEMATIC("mcedit","mce","schematic")`). Saves go to `config/worldedit/schematics` (working dir `config/worldedit` + `saveDir="schematics"`). It is a fork of EngineHub WorldEdit `forge-archive/1.7.10`. https://github.com/GTNewHorizons/worldedit-gtnh
- CONFIRMED **tags written** (`extent/clipboard/io/SchematicWriter.java`):
  - `Width/Height/Length` (shorts), `Materials="Alpha"`, `WEOriginX/Y/Z`, `WEOffsetX/Y/Z`, `Blocks` (low 8 bits), `Data` (metadata).
  - `AddBlocks` (bits 8–11; **even index = low nibble**), `AddBlocks2` (bits 12–15, for >4095 IDs), `AddData` (high byte of 16-bit meta).
  - `TileEntities` (x/y/z relative), `Entities`.
  - **`BlockMapping` / `ItemMapping`** (name → source short ID). The reader remaps those IDs to the current world by name, including item IDs inside tile-entity inventories.
  - Mapping and large IDs arrived in **v0.0.8 (2025-04-20)**; the large-ID shift fix is in **v0.0.9 (2026-03-06)**. Latest is v0.0.10 (2026-07-19). Upstream EngineHub 6.x has none of this.
- CONFIRMED: Sponge `.schem` (palette of block-state strings + varint data; v1 2016-08-23, v2 2019, v3 2021-05-04) is a 1.13+-era format and cannot be read here. https://github.com/SpongePowered/Schematic-Specification
- CONFIRMED (https://github.com/GTNewHorizons/Schematica, `SchematicAlpha.java`): Schematica-GTNH reads `SchematicaMapping` (not `BlockMapping`). It decodes `AddBlocks` with **even index = high nibble**, the opposite of WorldEdit's writer. With `useSchematicplusFormat` it treats `AddBlocks` as a full byte per block. UNCERTAIN: practical impact. Likely wrong IDs for modded blocks when a WorldEdit-written file is opened in Schematica; test before relying on it.

### 5.2 Block IDs and level.dat
- CONFIRMED (Forge 1.7.10 `FMLContainer.getDataForWriting`, `FMLCommonHandler.handleWorldDataSave`, `GameData`; https://github.com/MinecraftForge/MinecraftForge/tree/1.7.10/fml/src/main/java/cpw/mods/fml/common):
  - `level.dat` root has a compound **`FML`** with list `ItemData` of `{K: string, V: int}`. Keys are prefixed `\u0001` for blocks and `\u0002` for items (e.g. `\u0001gregtech:gt.blockcasings`).
  - Also `BlockAliases`, `ItemAliases`, `BlockSubstitutions`, `ItemSubstitutions`.
  - Vanilla Forge ranges are blocks 0–4095 and items 4096–31999; ID mods extend these.
- CONFIRMED (https://github.com/FalsePattern/EndlessIDs, `BaseBlockMixin.java`, `GeneralConfig.java`): EndlessIDs (2.9.0) defaults to 32k block IDs (configurable to 16M). It **mixes into WorldEdit's `BaseBlock`** to accept extended IDs and 16-bit metadata.
- worldedit-gtnh's own 16-bit-meta switch keys on `Loader.isModLoaded("neid")` (`ForgeWorldEdit.java`). That is moot under EndlessIDs because of the mixin. UNCERTAIN: not tested in-game.
- Recommendation: read the target world's `FML.ItemData` (Python `nbtlib`), write schematics whose numeric IDs match that world, **and** include `BlockMapping`/`ItemMapping`. Then a worldedit-gtnh ≥0.0.9 paste is correct even if IDs differ, e.g. a footprint captured in a creative test world.

### 5.3 GT machines inside schematics
- CONFIRMED (`BaseMetaTileEntity.writeToNBT` @ 5.09.54.265): GT machines are tile entities carrying `mID` (GT meta-tile ID, not a Forge ID), `mFacing` (ForgeDirection ordinal), owner fields, etc. Casings and coils are plain blocks with metadata.
- CONFIRMED: worldedit-gtnh's custom rotation mappings (v0.0.10, `forge/compat/rotation/*`) are **metadata-only JSON**.
- UNCERTAIN: whether `//rotate` updates GT tile-entity facing. Assume not. Generate schematics already rotated, or re-face controllers after pasting.

### 5.4 Hologram projector (StructureLib) behaviour
- **Creative.** CONFIRMED (`alignment/constructable/ConstructableUtility.java`, https://github.com/GTNewHorizons/StructureLib): sneak-use on a controller calls `construct(stack, false)` and places everything for free. The **Debug Hologram Projector** always does a creative build (StructureLib 1.4.47 = pack 2.9.0; PR #79, 2026-10-20). Non-sneak use shows hint particles and a text description.
- **Channels.** CONFIRMED (`ChannelDataAccessor.withChannel`; GT `GTStructureChannels.java`): tiers and sizes come from per-channel ints stored in the projector NBT `channels` compound (e.g. `coil`, `glass`, `height`, `length`, `pipe`, `gt_hatch`). Without a channel the **projector stack size** is used.
- **Survival.** CONFIRMED:
  - `survivalConstruct` places at most `autoPlaceBudget` elements per use (default **25**, range 1–200, server overrides). The default minimum interval between uses is 300 ms (`autoPlaceInterval`, `ConfigurationHandler.java`).
  - It pulls from the player's main inventory and ender chest (`InventoryUtility.java`).
  - **StructureCompat** (in-pack, https://github.com/GTNewHorizons/StructureCompat) adds **AE2 wireless terminals** (linked, in range of a Wireless Access Point, terminal power consumed, view-cell filtered; `WirelessTerminalStackExtractor.java`), AE2 portable cells, AE2FC and WCT terminals, backpacks, Baubles, Forestry, Railcraft.
- **Hatches.** CONFIRMED (`HatchElementBuilder.java`):
  - **2.8.4:** creative build places no hatches (`placeBlock` returns false, "TODO"). Survival auto-places hatches unless the `NO_HATCH` channel is set.
  - **2.9.0:** hatches are placed only if the `gt_hatch` channel is set. In creative they come from `GTCreativeHatchSource`: debug energy, auto-taping maintenance, UHV muffler, debug input bus/hatch, max-capacity ME output bus/hatch, then any GT MTE.
  - UNCERTAIN: how many hatch-eligible positions get filled in creative.
- CONFIRMED: TST multis implement `IConstructable`/`ISurvivalConstructable`, so the projector works for them.

### 5.5 Capturing exact multiblock footprints
**A. Creative test-world capture** (feasible now; CONFIRMED components, UNCERTAIN end-to-end):
1. Use a superflat creative world in the same instance.
2. Place the controller with a known facing.
3. Use a debug projector or creative sneak-use, with channels set via `/give … {channels:{coil:N,glass:M,gt_hatch:1}}`. UNCERTAIN: the exact item registry name.
4. Select the bounding box, then `//copy` and `//schem save`.
5. Store one footprint per (controller, channel values, facing). On 2.8.4, hatch spots stay air: record them from hint particles or mark manually.
6. Pasting into the survival world relies on `BlockMapping` remap (§5.2).
7. On 2.9, capture the legacy and new turbine/LCE/boiler variants separately.

**B. Automated exporter mod** (more work, fully automatic):
- BlockRenderer6343 (LGPL-3.0, in-pack; https://github.com/GTNewHorizons/BlockRenderer6343, `GTGuiMultiblockHandler.java`) already builds every GT multiblock in a client-side dummy world, using `survivalConstruct` or `construct(trigger,false)` for NEI previews.
- A small client mod could iterate controllers and channel values and dump each dummy world as `.schematic`. UNCERTAIN: effort is estimated, not prototyped.

### 5.6 Recommendation (building)
1. Target **MCEdit `.schematic` + `BlockMapping`/`ItemMapping` + `AddBlocks2`/`AddData`**, for worldedit-gtnh ≥ v0.0.9.
2. Read the world's `FML.ItemData` for IDs and the palette. This matches the plan in memory for his separate img2schem tool: WorldEdit schematic output, with the block palette pulled from his own client.
3. Build a footprint library with approach A first; move to B if many tiers or variants are needed.
4. Generate final building schematics around those footprints, pre-rotated.
5. For survival placement, either paste with WorldEdit (if he accepts creative-style pasting) or place controllers and let the projector auto-build from inventory or an AE2 wireless terminal, 25 elements per click by default.

---

## 6. Questions only the player can answer
1. Exact pack version: 2.8.x, a 2.9 beta/RC, or 2.9.0 (manifest dated 2026-10-31)? This decides OC math, PlanNH availability, turbine/LCE/boiler variants, and NEID vs EndlessIDs.
2. Which "GregTech planner": gtnh-flow (local Python), PlanNH (in-game F8; needs ≥2.9.0-RC-2), gtnhplanner.com (2.9 data only), gtcalc.app (2.8 data), ShadowTheAge's calculator (2.8.0), or the Power Planner spreadsheet?
3. Which WorldEdit jar is installed: worldedit-gtnh (version?) or EngineHub 6.1.x? Is Schematica used for survival building?
4. Singleplayer or server? This affects the PlanNH save path, WorldEdit permissions and the auto-place budget.
5. On 2.9: are existing turbines, LCEs and boilers legacy builds or new ones?
6. Will he use AE2 wireless-terminal auto-place in survival, and Programmable Hatches for circuits?
7. Is a separate creative test world acceptable for footprint capture? Is pasting with WorldEdit in survival acceptable to him, or only projector/manual builds?
8. Which addon multis (TST, GT-Not-Leisure, 123Technology) does he actually build? Each needs a parameter entry.

## 7. Open risks
- PlanNH writing depends on exact NEI RecipeIds and an internal, recently changed JSON shape. It does not run on 2.8.x.
- GT behaviour differs 2.8.4 vs 2.9.0: OC fixes, centrifuge momentum, new vs legacy power multis, hatch auto-place semantics. One table per version is mandatory.
- Addon multiblock parameters have no generated data. lgrones' 2.8.4 extraction has no licence.
- Schematic conventions conflict between WorldEdit and Schematica (`AddBlocks` nibble order, mapping tag names). GT tile-entity facing likely won't rotate.
- LP degeneracy: free boundaries export intermediates, and gate minimisation shortcuts by importing them. Every-machine-runs plus scaled big-M are required (reproduced in the toy).
- gtnh-flow data and formulas are stale or buggy (EBF heat, GT++ model, turbine data 2.2.3).

## 8. Sources (all accessed 2026-11-01)
- GTNH manifests: https://github.com/GTNewHorizons/DreamAssemblerXXL/tree/master/releases/manifests · modpack tags: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack
- GT5-Unofficial @5.09.54.265 / @5.09.51.482: https://github.com/GTNewHorizons/GT5-Unofficial (files listed in §2, §4)
- gtnh-flow: https://github.com/OrderedSet86/gtnh-flow · gregtech-flow: https://github.com/velolib/gregtech-flow · https://pypi.org/project/gtnhvelo/ · https://pypi.org/project/gregtech-flow/
- PlanNH: https://github.com/GTNewHorizons/PlanNH · https://github.com/Sersium/PlanNH
- Web planners: https://gtnhplanner.com · https://github.com/jackwrichards/gtnh-factory-flow · https://github.com/Samiracle64/gtnh-factory-flow · https://gtcalc.app/gtnh · https://github.com/syhros/gtcalc · https://shadowtheage.github.io/gtnh/ · https://github.com/ShadowTheAge/gtnh · https://github.com/lgrones/gtnh · https://milky-way-galaxy.github.io/gtnhnei/
- Building: https://github.com/GTNewHorizons/worldedit-gtnh · https://github.com/GTNewHorizons/Schematica · https://github.com/MinecraftForge/MinecraftForge/tree/1.7.10 · https://github.com/FalsePattern/EndlessIDs · https://github.com/SpongePowered/Schematic-Specification · https://github.com/GTNewHorizons/StructureLib · https://github.com/GTNewHorizons/StructureCompat · https://github.com/GTNewHorizons/BlockRenderer6343
- Addons: https://github.com/Nxer/Twist-Space-Technology-Mod · https://github.com/reobf/Programmable-Hatches-Mod
- GTNH wiki: https://wiki.gtnewhorizons.com/wiki/Electric_Blast_Furnace · https://wiki.gtnewhorizons.com/wiki/Power_Generation (read); other pages listed in §4.2 (not readable)
