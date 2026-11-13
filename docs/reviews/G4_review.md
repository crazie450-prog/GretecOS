# Gregtec OS: G4 gate review (Phase 4, power designer)

2026-11-13 · independent gate reviewer (D41, D158 (3)) · reviewed: `origin/main` at `b169669` (PR #96), which holds all six Phase 4 PRs: #77 (WO-4.1), #81 (WO-4.2), #85 (WO-4.3), #88 (WO-4.4), #91 (WO-4.5) and #96 (WO-4.6).

## Verdict

**Every Phase 4 WO is merged, and each WO row is met or met with fakes. The G4 row itself is not met: two of its four D65 lines don't give a usable line on the dev runtime.**

1. **Two D65 lines are not met in code (finding 1).** The light-fuel loop (MV) and the benzene gas turbines (EV) come out with the fuel chain using up the generator's whole output: 127.99995 of 128 EU/t for light fuel (so every candidate is rejected, "net EU/t is not above 0") and 971.999 of 972 EU/t for benzene (one option, net 0.0006 EU/t). The cause is in the solver, not the data: the chain's staged MILP (WO-3.4) minimises gates, then the gated quantity, then machines, and never counts the EU the chain draws. In a power network the generator's EU is free to the chain, so the solver spends all of it to cut its gated imports: it takes as little gated input as the EU budget allows, and the net is 0. This isn't a small fix inside one acceptance criterion (it changes how WO-4.2 costs a power line in `solve.py`, D192-D195, D233), so it is a finding, not a change in this review. The new test pins both lines as strict xfails.
2. **The tree-farm steam boilers (EV, `self_sustaining`) are met on the dev runtime:** proven closed with a bootstrap kit, accepted into the EU ledger, and the ledger's nets went through the distribution checks. **The naquadah line is met only on its generator side (D255):** it gives 3 ranked options at IV (2 048 to 8 192 EU/t) that reach the ledger, but every option imports its enriched naquadah rods (or bolts, or long rods) through a gate, so no naquadah fuel chain is designed. GoodGenerator's Large Naquadah Reactor, which burns the Naquadah Fuel MkI-VI fluids, is `not_modelled` (D232), and the MkI fluid line waits on modelling it (finding 2).
3. **G3 handed three fuel-chain checks to G4** (`docs/reviews/G3_review.md`, G3 row: each distinct (machine, recipe, tier, OC, parallel) within ±2% of NEI, line totals within ±2% of a reference). The tree-farm chain's recipes are checked against NEI here; benzene's waits on finding 1 and naquadah has no chain to check. Line totals have no reference for power lines (gtnhplanner.com designs production lines), so the tree farm's totals are a Lab step.
4. **What's left for Clayton:** the PC and Lab steps below (turbines in the Lab, the tree-farm line in the Lab, the four lines on the full pack), his review of the 12 power-mode cases (the WO-4.6 acceptance), the Power view's board, and his answers to the questions. G4 can't be signed until finding 1 is fixed or Clayton accepts the two lines as they are.

No product code was changed by this review. Added: `tests/integration/test_g4_lines_harness.py` (the four D65 lines end to end, D255) and decision row D255.

Evidence tags, as in G0-G3:
- **[repo]:** checked in the repo, the git history or GitHub's CI logs.
- **[here]:** run in this reviewer's build container (Linux) on `review/g4` (`b169669` plus the new test).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC, the full pack, the Lab or his eye. Nothing from the PC is in the evidence yet for Phase 4.

### This reviewer's runs [here]

| Check | Result |
|---|---|
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 456 files already formatted (457 with the new test) |
| `uv run --locked pytest -q` | **8949 passed, 276 skipped** before the new test (10 min 28 s); **8949 passed, 285 skipped** with it after the review fixes (the 9 G4 tests skip without `GOS_REQUIRE_HARNESS_RUN=1`; 7 min 22 s) |
| `./gradlew --no-daemon --max-workers=2 runServer -Pharness` | `harness passed: 35 checks, run 20261113T141208Z` (3 min 39 s) |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest -v` on `test_power_harness`, `test_power_objectives_harness`, `test_power_effort_harness`, `test_ledger_harness`, `test_distribution_harness`, `test_power_view_harness` (CI's LV+HV subset of `gos eval power-modes`) | **29 passed** (14 min 23 s) |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest -v tests/integration/test_g4_lines_harness.py` (new) | **5 passed, 2 xfailed** (strict, finding 1; 1 min 21 s) |
| After the review's fixes, on fresh harness runs (server `20261113T145552Z`, 35 checks; client `20261113T145833Z`, 56 checks): the same test with the G3 hand-off check and the full D255 (3) rule in the xfails | **7 passed, 2 xfailed** (1 min 55 s); the four designs give the same numbers as below |
| `gos power design` on the four D65 lines (scratch home built from that run with `collect` + `db build`) | below, "The four D65 lines" |
| `gos power design ... --accept 1` (tree farm, naquadah), then `gos ledger`, `gos dist ledger --window 600` | 2 sources (EV 35.8549306, IV 8191.8824 EU/t), 0 warnings; "generation covers the base's peak: no buffer needed" |
| `gos power turbine gas --rotor Titanium --size large --dynamo IV --fuel benzene` | optimal 2 L/t → 972 EU/t, maximum 6 L/t → 1749 EU/t (`rotor:Titanium/large`, `r:gt.recipe.gasturbinefuel/28b9a71641df`) |
| `pnpm -C dashboard install --frozen-lockfile`, `build` (`tsc --noEmit` + `vite build`) | clean |
| `pnpm -C dashboard test` (vitest) | **303 passed** (16 files) |
| `playwright test e2e/power.spec.ts --workers=1` (own ports 18795/18797/18799) | **8 passed** (2560x1440 and phone, every theme at WCAG contrast) |
| `playwright test --workers=1` (full suite, own ports) | **62 passed** (2.8 min) |

The client harness wasn't re-run: no Phase 4 check needs it (WO-4.3's `test_rank_harness::test_effort_on_a_run_with_crafting_recipes` is Phase 3's and ran in G3 and on CI).

### CI on `main` [repo]

| Commit | PR | Run | Result |
|---|---|---|---|
| `90b6782` | #77 WO-4.1 | 43215106534 (ubuntu, windows, dashboard, mod) + 43215106614 mod (Windows) | all success |
| `055926b` | #81 WO-4.2 | 43266071858 (ubuntu, windows, dashboard, mod) | all success |
| `7df7a45` | #85 WO-4.3 | 43309351692 (ubuntu, windows, dashboard, mod) | all success |
| `d0460a4` | #88 WO-4.4 | 43368388009 (ubuntu, windows, dashboard, mod) + 43368388079 dashboard (Windows) | all success |
| `2da27de` | #91 WO-4.5 | 43426982336 (ubuntu, windows, dashboard, mod) + 43426982413 mod (Windows) | all success |
| `b169669` | #96 WO-4.6 (head) | 43439764174 (ubuntu, windows, dashboard, mod) + 43439764405 dashboard (Windows) | all success |

---

## Phase 4 work orders

### WO-4.1: Fuel and generator catalog; turbine port; legacy/new controllers

> **Acceptance:** "Turbine output matches the Lab for 3 rotor setups"

**Verdict: met with fakes. The Lab half is a PC step.**

PR #77, merge `90b6782`, D230-D232 [repo].

| Part | Status | Evidence |
|---|---|---|
| Turbine port equals GT | met | `test_turbine_vectors.py` replays `turbine.tsv` (2 908 rows: 3 rotor set-ups per controller, tight and loose, from GT's own classes) and `rotor.tsv` (912) [here, full pytest]. `test_power_harness::test_every_rotor_matches_gts_calculator_in_the_game`, `test_the_committed_turbine_vectors_are_what_gts_own_classes_compute` [here] |
| 3 rotor set-ups size from the DB | met | `test_power_harness::test_three_rotor_setups_per_turbine_kind_size_from_the_db` for steam, HP steam, SC steam, gas and plasma: 5 passed [here] |
| Fuel catalog decoded | met | `test_every_fuel_map_is_decoded`, `test_single_blocks_match_gts_fuel_values_in_the_game` [here] |
| New and legacy controllers tracked apart | met | `test_the_catalog_tracks_new_and_legacy_controllers` [here]; `gos power generators` lists both Large Naquadah Reactors (`mte:15537` new, `mte:12732` legacy) |
| Output matches the Lab | **PC step** | `docs/runbooks/turbines_lab.md` §1-3 (step 2 below) |

Licence: GT5-U is LGPL-3.0, read for behaviour only; nothing copied (D230) [PR].

### WO-4.2: Four objective modes; self-sustaining validator and bootstrap kit

> **Acceptance:** "The validator proves zero non-free imports"

**Verdict: met.** The four modes and the validator work as specified. Finding 1 (EU isn't costed in the chain's MILP) sits in WO-4.2's design and shows up in the G4 row.

PR #81, merge `055926b`, D233-D235 [repo].

| Part | Status | Evidence |
|---|---|---|
| Validator proves zero non-free imports | met | `test_power_objectives_harness::test_the_d65_tree_farm_boiler_line_is_proven_closed` and `test_the_same_line_without_its_farm_fails` (one hidden import, the logs, is caught) [here]. `test_sustain.py::test_a_truly_closed_line_is_proven`, `test_a_line_with_one_hidden_import_fails`, `test_a_fuel_from_another_map_is_refused` [here, full pytest]. This review's tree-farm line: `closed: no import outside the free set (validator)` on both options |
| Recipes from nothing refused | met | `test_the_validator_refuses_a_recipe_from_nothing` [here] |
| Four modes, 2-3 ranked options | met at HV | `test_every_mode_returns_two_or_three_ranked_options_at_hv[best, max_output, output_per_effort, self_sustaining]`: 4 passed [here]. LV and IV under WO-4.6 |
| Bootstrap kit | met | Tree farm: sapling, `gt.integrated_circuit@1` x10, buzzsaw, EV dynamo hatch, throttle circuit, rotor, starter creosote (an upper bound, D234) [here] |
| Boiler and tree farm equal GT | met | `GtBoilerOracle.java` vectors, `test_boiler_vectors.py` [here, full pytest] |

### WO-4.3: Effort model applied to power, with breakdown UI

> **Acceptance:** "Hand-checked on 2 lines"

**Verdict: met.** The breakdown's place in the Power view needs Clayton's board approval (D92).

PR #85, merge `7df7a45`, D236-D237 [repo].

| Part | Status | Evidence |
|---|---|---|
| Hand-checked on 2 lines | met | `tests/unit/planner/test_power_effort.py::test_worked_example_1_gas_line_build_only` and `test_worked_example_2_turbine_line_with_rotor_hatch_and_unlocks` (rotor with NBT, dynamo hatch, unlocks; 194 s build + 68 s unlock) [here, full pytest] |
| On real data | met | `test_power_effort_harness`: 3 passed [here] |
| Breakdown UI | met with fakes | the `table` card (`tool: effort_estimate`), e2e "the effort breakdown opens as the catalog's table card, its ids as chips" [here] |

On the D65 lines the effort is mostly a lower bound: the turbine and naquadah reactor controllers have no route at the tier on this DB, so they are `uncounted`, and all three naquadah options show `effort 0 s` with 3-12 items not counted (finding 4; PR #85 Q1 and Q3, PR #96 Q9).

### WO-4.4: EU ledger (`base/power.md`), real `ledger_update`, `line_outputs`

> **Acceptance:** "Ledger updates when a plan is accepted"

**Verdict: met.** The telemetry half waits for OC on Play (WO-7.1, a later gate).

PR #88, merge `d0460a4`, D238-D239 [repo].

| Part | Status | Evidence |
|---|---|---|
| Ledger updates on accept | met | `test_ledger_harness::test_accepting_real_plans_updates_the_ledger` (a real steel line and a real power line through the accept path, note checked against `_gos/schema.md`) [here]; this review's G4 test accepts the tree-farm and naquadah lines and a steel MV line [here]; CLI run above [here] |
| Warnings (deficit, tier mismatch, amp overload) | met | `test_ledger.py` unit tests (full pytest); the G4 test sees `tier_mismatch` at MV with sources only at EV and IV [here] |
| `line_outputs` feed My base | met | `test_running_line_outputs_reach_the_designer`, harness test's running steel line [here] |
| Telemetry reconcile | met with fakes | `test_reconcile_reads_fresh_power_entries_only` [here, full pytest]; `gos ledger` prints `tel: not reconciled: no fresh OC power telemetry` [here]; live check after WO-7.1 on Play |

### WO-4.5: Storage and distribution

> **Acceptance:** "3 test scenarios"

**Verdict: met.** V10 resolved.

PR #91, merge `2da27de`, D240-D242 [repo].

| Part | Status | Evidence |
|---|---|---|
| Scenario 1: deficit window → buffer | met | `test_distribution_harness::test_scenario_a_deficit_window_is_sized_to_a_buffer` [here] |
| Scenario 2: amp overload on a cable run | met | `test_scenario_an_amp_overload_is_caught_on_a_cable_run` [here] |
| Scenario 3: tier mismatch → transformers | met | `test_scenario_a_tier_mismatch_needs_a_transformer` [here]; with real accepted lines in the G4 test (an EV→MV chain from the ledger) [here] |
| Cable stats source (VERIFY V10) | met | `test_every_gt_cable_has_its_stats_from_its_fields` (588 cables from GT's fields; the en_US tooltip fallback reads the same numbers), `test_the_storage_rows_match_gts_voltages`, `test_ic2s_wildcard_batteries_are_storage` [here] |

### WO-4.6: Power view, `power-designer` skill, balanced default weights

> **Acceptance:** "All 4 modes at LV/HV/IV reviewed, each returning 2-3 ranked options"

**Verdict: met with fakes. "Reviewed" is Clayton's review of the 12 cases (PC step), and the Power view needs his board approval.**

PR #96, merge `b169669`, D243-D245 [repo].

| Part | Status | Evidence |
|---|---|---|
| 12 cases, each 2-3 ranked options | met | `docs/reviews/WO-4.6_power_modes.md`: 12 of 12 pass (PR's run with `GOS_POWER_EVAL_FULL=1`) [PR]. Here: `test_power_view_harness::test_all_four_modes_at_lv_hv_iv_return_two_or_three_ranked_options`, CI's LV+HV subset (8 cases), passed [here] |
| Models explain, never reorder | met with fakes | the eval's `explain` and `plan_explain` checks (a reordering fake is refused, the template is used) [here]; `test_in_order` [full pytest] |
| Balanced default weights (D67) | met | unit test pins `vault-template/me/prefs.md`; every case prints "weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1" |
| Power view | met with fakes | `e2e/power.spec.ts`: 8 passed [here] |
| Reviewed by Clayton | **PC step** | step 6 below. The reviewer's notes on the 12 cases are finding 3 |

---

## G4 row

> **G4:** "4 power benchmark lines (D65), end to end: light-fuel loop, benzene gas turbines, steam boilers on a tree farm (self-sustaining), naquadah fuel" · "Reviewer report; Clayton sign-off"

How the lines are asked for and judged is D255 (judgement call, below).

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Light-fuel loop | **not met** | No line: both candidates rejected, "net EU/t is not above 0 after the chain's own use". The chain draws 127.99995 of the generator's 128 EU/t (finding 1). Strict xfail `test_the_chain_leaves_the_generator_some_output[light_fuel]` [here] |
| Benzene gas turbines | **not met** | One option, net 0.000595903 EU/t of 972 (finding 1). Strict xfail `[benzene]` [here] |
| Steam boilers on a tree farm (self-sustaining) | **met on the dev runtime; Lab PC step** | 2 options, both proven closed, net 35.85 and 29.58 EU/t, with their kits; accepted into the ledger [here]. Lab: `power_objectives.md` (step 4) |
| Naquadah fuel | **met for generator → ledger only; fuel chain not designed** | 3 options at IV, 2 048-8 192 EU/t; accepted into the ledger [here]. Every option imports its fuel item through a gate (option 1: `gregtech:gt.metaitem.01@23326` at 0.00065536/s), so the line covers the generator, ledger and distribution, not the fuel. The MkI fluid line waits on GoodGenerator's Large Naquadah Reactor, `not_modelled` (D232, finding 2, Q-G4-1) |
| G3 hand-off: fuel chains ±2% (G3 row, `G3_review.md`) | **tree farm: recipes met on the dev runtime, totals a Lab step; benzene: not met; naquadah: not met (no chain)** | Tree farm: its chain recipe, the pyrolyse oven's `r:gt.recipe.pyro/40f1964c203d`, equals NEI's line with G3's check (`test_the_g3_hand_off_chain_recipes_match_nei[tree_farm]`, [here], client run `20261113T145833Z`). Its farm recipe (`r:gt.recipe.treefarm/1a6a43234da5`) isn't an NEI-checkable line (NEI shows the Tree Growth Simulator with its tools as inputs) and is checked against GT's own class by WO-4.2's `GtBoilerOracle` vectors. OC and parallel are `gtmath`'s, equal to GT's classes (D174). No power line has a ±2% total reference (gtnhplanner.com and PlanNH design production lines, D66), so the tree farm's totals are judged by the Lab run (step 4, [QA:D12]). Benzene: blocked by finding 1 (its chain is the solver's EU-budget artefact, not a line to check). Naquadah: the options import the fuel, so there is no fuel chain to check (finding 2); their side recipes on NEI-registered maps (12 of 15) equal NEI (`[naquadah]`, [here]). The 3 cutting-saw recipes can't be checked against NEI at all: GT doesn't register `gt.recipe.cuttingsaw` with NEI (`recipe_map.nei_visible` 0), and the test skips such maps by that flag |
| End to end (design → effort → validator → accept → ledger → distribution) | met for the tree farm; for naquadah, generator → ledger → distribution only | `test_g4_lines_harness.py::test_accepted_lines_reach_the_ledger_and_the_distribution_checks` [here] |
| Reviewer report | met | this report |
| Clayton's sign-off | PC | after finding 1 and the steps below |

### The four D65 lines [here]

Designed with `gos power design` on the server harness run `20261113T141208Z`, balanced weights (the template's), the default rotor (Steel large), nothing owned or stocked. The numbers are the designer's, not this page's author's; the option ids are this review's scratch designs.

**1. Light-fuel loop:** `gos power design best --tier MV --kind single_block --fuel fluid:liquid_light_fuel` (2.7 s, exit 1)

```
no power line at MV for mode best
  gen:single/mte:1111/r:gt.recipe.dieselgeneratorfuel/3f787facee1e: net EU/t is not above 0 after the chain's own use
  gen:single/mte:1111/r:gt.recipe.dieselgeneratorfuel/3f787facee1e: net EU/t is not above 0 after the chain's own use
```

The two rejected lines, built before the check (Advanced Combustion Generator, `mte:1111`, 128 EU/t gross):

| Line | Chain EU/t | Net | Gated import | Biggest draws |
|---|---|---|---|---|
| graph's own chain (`pw-473a8126d6`) | 127.99995 | 0 | `fluid:liquid_heavy_fuel` 10.96/s | 3 Basic Distilleries on `r:gt.recipe.distillery/59350ed72a00` (59.5 EU/t), 2 on `r:gt.recipe.distillery/813594843786` (44.8), Industrial Centrifuge `r:gt.recipe.multicentrifuge/593da9866c5c` (13.9) |
| pinned alternative (`pw-134ef09e28`) | 127.999993 | 0 | `fluid:lightlyhydrocracked.heavyfuel` 2.40/s | 4 Basic Distilleries on `r:gt.recipe.distillery/59350ed72a00` (79.0), Industrial Centrifuge (18.5), Industrial Electrolyzer `r:gt.recipe.multielectro/599bf7b376e7` (17.9) |

The generator burns 9.34306569 L/s of light fuel. The production designer's gate-free light-fuel route (`gos plan fluid:liquid_light_fuel --tier MV --qty 1/s`, from oilsands ore and leaves) draws 38.8 EU/t on average per L/s, so at 9.34 L/s a gate-free chain would draw about 362 EU/t, more than the generator gives. The solver closes the gap with as little of one gated input (heavy fuel) as it can, so the chain ends up drawing exactly 128 EU/t. If EU were valued, it would take more of that same gated input (still one gate) and keep the EU as net output.

**2. Benzene gas turbines:** `gos power design best --tier EV --kind gas_turbine --fuel fluid:benzene` (32.7 s)

| # | Plan | Generator | Net EU/t | Gross | Chain | Score | Effort | Not counted | Upkeep /h | Machines |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `plan:pw-586b3b1ce0` | Large Gas Turbine (`mte:15527`) on benzene, Steel large rotor | 0.000595903 | 972 | 971.999 | -0.4 | 31.3 min (build; 66 rows) | 40 | 10 486.3 | 41 |

Fuel 40 L/s (`r:gt.recipe.gasturbinefuel/28b9a71641df`). Imports: `fluid:moderatelysteamcracked.lightfuel` 115.43/s (gated), leaves, ore `gregtech:gt.blockores2@878`. 19 Basic Distilleries on `r:gt.recipe.distillery/59350ed72a00` draw 447.6 EU/t. Validator: not closed (the imports). 1 candidate rejected; only 1 option (D11 asks for 2-3).

**3. Steam boilers on a tree farm:** `gos power design self_sustaining --tier EV --kind boiler_steam --fuel fluid:creosote` (21.2 s)

| # | Plan | Generator | Net EU/t | Gross | Chain | Score | Effort | Not counted | Upkeep /h | Machines | Validator |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `plan:pw-e220d5bfda` | 1 x Large Bronze Boiler (`mte:15529`) on creosote → Large Steam Turbine (`mte:15524`), Steel large rotor | 35.8549 | 607 | 571.145 | -0.2 | 4.5 min | 21 | 0 | 13 | closed |
| 2 | `plan:pw-c6f9cc5e8d` | 1 x Large Steel Boiler (`mte:15530`) → Large Steam Turbine, Steel large rotor | 29.5786 | 607 | 577.421 | -0.23501 | 4.5 min | 21 | 0 | 13 | closed |

Chain: 10 pyrolyse ovens (`r:gt.recipe.pyro/40f1964c203d`), the Tree Growth Simulator (`farm:r:gt.recipe.treefarm/1a6a43234da5`), Rainforest Oak logs. Creosote 592.4 and 599.0 L/s. Kit: Rainforest Oak sapling, `gt.integrated_circuit@1` x10, buzzsaw, EV 1 A dynamo hatch, throttle circuit 1 (option 2: 25), Steel large rotor, starter creosote 40 879 L (option 2: 41 329; upper bounds). 6 candidates rejected.

**4. Naquadah fuel:** `gos power design best --tier IV --kind single_block --fuel gregtech:gt.metaitem.01@23326 gregtech:gt.metaitem.01@26326 gregtech:gt.metaitem.02@22326` (22.0 s)

| # | Plan | Generator | Fuel | Net EU/t | Gross | Chain | Score | Effort | Not counted | Upkeep /h | Machines |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `plan:pw-a5827e719e` | Naquadah Reactor Mk-II (`mte:1191`) | Enriched Naquadah Rod 0.00065536/s (`r:gt.recipe.largenaquadahreactor/84de510b24c3`) | 8 191.88 | 8 192 | 0.1176 | -0.0454568 | 0 s | 3 | 2.3593 | 3 |
| 2 | `plan:pw-49ca6ff578` | Naquadah Reactor Mk-I (`mte:1190`) | Enriched Naquadah Bolt 0.001024/s (`r:gt.recipe.smallnaquadahreactor/e523e3be2ec8`) | 2 047.99 | 2 048 | 0.0101 | -0.224629 | 0 s | 9 | 0.923929 | 9 |
| 3 | `plan:pw-153b841bdd` | Naquadah Reactor Mk-II (`mte:1191`) | Enriched Naquadah Rod, from long rods | 8 191.98 | 8 192 | 0.0236 | -0.237081 | 0 s | 12 | 1.18811 | 10 |

None is closed, and none designs a naquadah fuel chain: each imports its fuel item through a gate, as a `best` line may (`--json` boundary: option 1 `gregtech:gt.metaitem.01@23326` at 0.00065536/s, option 2 the same rod at 0.000256/s, cut into bolts, option 3 `gregtech:gt.metaitem.02@22326` at 0.000327681/s). Their other recipes are side processing at 1e-7 to 1e-6 crafts/s. Option 1's chain holds a zero-sum loop (finding 5): `r:gt.recipe.hammer/9ccf2db95014` makes the long rod from 2 rods and `r:gt.recipe.cuttingsaw/e42b5fab4a2a` cuts it back into 2 rods, each at 0.001 crafts/s, more than the generator's 0.00065536 rods/s, using 0.1176 EU/t and the saw's fluid for nothing. 0 candidates rejected.

### Accepted into the ledger [here]

`--accept 1` on lines 3 and 4, then `gos ledger`: `sources: 2`, `gen_eut: 8227.73733`, `warnings: 0`; `plan:pw-a5827e719e` IV 8191.8824 (gross 8192, 1 A) and `plan:pw-e220d5bfda` EV 35.8549306 (gross 607, 1 A). `gos dist ledger --window 600`: "generation covers the base's peak: no buffer needed". In the G4 test, an accepted steel line at MV adds a `tier_mismatch` warning at MV, and `distribution.from_ledger` chains transformers down from EV.

---

## Findings

1. **HIGH: a power line's fuel chain spends the generator's EU without limit (WO-4.2, D233; `solve.py` stages, D192-D195).** The power network makes EU an output of the generator and an input of every chain machine, never imported (`banned`). But the staged MILP's objectives are gates, gated quantity (with a 1e-6 raw-input tiebreak that leaves EU out) and machines; EU isn't in any of them. So whenever processing in the chain can replace a gated import, the solver keeps doing more of it, cutting the import, until the chain draws exactly the generator's output. The EU budget is the only thing that stops it. On the dev runtime this kills two of the four D65 lines (above). It may also lower the net of lines that pass. `best` and `output_per_effort` rank on net EU/t afterwards, but they only see what the solver already chose. Also, stage 2 compares gated quantities in row-scaled units (each divided by its row's scale c_k), so two different gated items (light fuel and heavy fuel, say) are weighed on different scales; a fix that adds an EU stage should look at this too. **Suggested fix (a follow-up WO, not done here):** cost EU in power networks. For example, add a stage after the gate count that maximises net EU/t. Or price EU in stage 2 at the fuel's own EU per unit. Either way, re-run the G4 lines and the 12 power-mode cases. The two xfails in `test_g4_lines_harness.py` (now the whole of D255 (3)'s pass rule) flip when it lands. **The follow-up WO also has to say what the "light-fuel loop" benchmark means:** the gate-free light-fuel chain draws about 362 EU/t against the generator's 128 (above), so once EU is priced the likely outcome is "import light fuel or heavy fuel through one gate", which is not a loop. A net-positive self-fed loop at MV may not exist on this data (Q-G4-4). Clayton's call is only whether to fix it before he signs G4 (Q-G4-2).
2. **MEDIUM: the naquadah line is met on its generator side only; no naquadah fuel chain is designed.** GoodGenerator's Large Naquadah Reactor (`mte:15537`, `gg.recipe.naquadah_reactor`), which burns the Naquadah Based Liquid Fuel MkI-VI fluids (all in the DB), is `not_modelled` (D232). The G4 line used GT's single-block Naquadah Reactors, and every option imports its enriched naquadah rod (or long rod) through a gate, so "end to end" covers generator → ledger → distribution, not the fuel. Fixing finding 1 won't change this: a `best` line can always take the gate on the rod. A follow-up WO would model that reactor (its fuel map, its output and its depleted-fuel return) and design the MkI fuel chain. Q-G4-1.
3. **MEDIUM (for Clayton's review of the 12 cases): imported late-game fuels rank high.** On the dev runtime, `hv-best` ranks first a Master Magic Energy Converter on Infinity Plate (`gregtech:gt.metaitem.01@17397`), and `lv-best`'s options 2-3 are a Novice Magic Energy Converter on Infinity Plate and on Infinity Dust. `lv-max` offers a Large Plasma Turbine on Bismuth Plasma at LV, and `iv-max` and `iv-effort` rank Bismuth Plasma first. These lines import their fuel, so the chain costs nothing, and the 26-row scarcity table (D197) doesn't cover these items. Whether to exclude imported fuels above the tier cap, or score them as scarce, is a gameplay preference (Q-G4-3). PR #96 Q7 (multiblock turbines at LV) is the same question.
4. **LOW: effort is a lower bound on every D65 line.** The controllers have no route at the tier on this DB (all 3 naquadah options: 0 s effort, 3-12 uncounted; tree farm 4.5 min, 21 uncounted; benzene 31.3 min, 40 uncounted). `output_per_effort` ranks incomplete efforts after complete ones (D236). This is WO-3.5's controller-route follow-up (PR #85 Q3) and should be checked on the full pack (step 3).
5. **LOW: zero-sum loops survive in a chain.** Naquadah option 1 keeps a hammer/cutting-saw loop (rod → long rod → rods) at 0.001 crafts/s, faster than the generator burns rods (0.00065536/s). It costs 0.1176 EU/t and a fluid for nothing, and adds `loops: 1` to the option's complexity term, so it affects the ranking. Likely cause: the stage-4 floor (`FLOOR_MIN = 1e-6` row units, `solve.py`) set against a target normalised to 1, which beats a very small fuel rate. Worth fixing with finding 1.
6. **LOW: decision rows D233-D237 and D243-D245 still read "Pending reviewer".** The per-PR reviewers agreed, with the changes the PR bodies list. The log is append-only, so the PM folds the verdicts at the next plan version. This reviewer agrees with D230-D245 as built, with findings 1 (D233) and 3 (D235) as open points.

---

## VERIFY items Phase 4 touched

| Item | Relation to Phase 4 | Status |
|---|---|---|
| **V10** (cable-stat source) | WO-4.5 | **Resolved** (D240): exported GT fields, en_US tooltip only as a checked fallback; `core/gregtec/verify.tsv` and plan §13.2 updated [repo]; `test_every_gt_cable_has_its_stats_from_its_fields` passed [here] |
| D231's hand-off (`extremeDieselFuels`, `denseLiquidFuels`, `largeBoilerFakeFuels` vs GT) | WO-4.1 → WO-4.2 | Done by `GtBoilerOracle` vectors (PR #81); not a V row |
| Steam per tick read from GT's bytecode (`lEUt x 2 x efficiency / 10000`) | WO-4.2 (D233) | Not called through the oracle; the Lab tree-farm check (step 4) is its running-world check |
| HiGHS presolve off (D245) | WO-4.6 | A follow-up re-enable check, not a V row |

No other `verify.tsv` row names a Phase 4 WO [repo].

---

## Open questions from Phase 4's PRs

The defaults stand, reversible, until Clayton answers (D158). "Agreed" means the PR's own reviewer agreed; Clayton still decides.

**PR #77 (WO-4.1)**, https://github.com/crazie450-prog/GretecOS/pull/77
- D230: plasma's 20-tick cycle is part of the port's contract (`cycle_ticks`). Agree?
- D230/D231: `rtgFuels` is EU per pellet as GT++'s RTG makes it, not NEI's special x 365 "days". OK?
- D231 narrowed: three maps checked by structure only (done by WO-4.2's oracle since). Rocket fuels at x1, hidden duplicates kept, the IC2 lava cell counted.
- D232: single blocks are sized only on maps read as EU; XL turbines, LCE/ECE, the GoodGenerator reactor and others stay `not_modelled`; legacy controllers are separate entries. OK? (See finding 2.)

**PR #81 (WO-4.2)**, https://github.com/crazie450-prog/GretecOS/pull/81
1. Default rotor Steel large.
2. Tree farm defaults to an LV Tree Growth Simulator with a buzzsaw.
3. Chain machines picked for the least EU per craft (departs from D11's "simplest sub-step first"). Finding 1 is the solver-level side of this.
4. Solid boiler fuels not sized until the export carries `getFuelValue`.
5. `data/curated/farms.tsv` ships empty.
6. Pruning heuristics (top 4 fuels per generator, closure levels, up to 3 side routes, one farm per product).
7. Recipes with no consumed input are left out (export fix filed).
8. `self_sustaining` ranked by the `best` score; zero-effort lines last in `output_per_effort`.
9. The free set per request or `--free`, not in gos.toml yet.
10. Single blocks: highest tier at the cap, plus the most efficient tier for `self_sustaining` and `output_per_effort`.
11. Starter fuel as an upper bound.
12. Keep the fallback when `generator_gt` has no row, or make it mandatory?

**PR #85 (WO-4.3)**, https://github.com/crazie450-prog/GretecOS/pull/85
1. D236: incomplete efforts rank after complete ones in `output_per_effort`. Should `best` and `self_sustaining` penalise `uncounted` too?
2. D236: other hatches, pipes, circuits and cables aren't counted. Follow-up: export each controller's hatch elements.
3. WO-3.5 follow-ups: routes for the turbine controller and others with none at EV; the alloy smelter's ingot → nugget → ingot loop. (Finding 4.)
4. D196 still pending: with an empty My base, `unlock_h` is always 0.
5. D237: the `table` card, one row per recipe; placement in the Power view needs board approval.
6. Fixture choice (an uncounted turbine structure on purpose).

**PR #88 (WO-4.4)**, https://github.com/crazie450-prog/GretecOS/pull/88
1. D238/D239 as agreed (status joined, supersede in one step, base-wide deficit, tier mismatch, amp overload; line outputs exclude the request's own target; telemetry freshness per sample).
2. Should My base **stock** skip the request's own target too (as running lines now do)?
3. `line_outputs` counts running lines' targets only; the Base "Running lines" panel needs board approval.
4. Rates on the Base panel shown with `plain()`, not rounded.
5. Power plans' exports and plan notes deferred to WO-4.6 (done there for accept).

**PR #91 (WO-4.5)**, https://github.com/crazie450-prog/GretecOS/pull/91
1. Buffer vs LSC switch at `MAX_BUFFERS = 4`?
2. Battery pick: the smallest that fits in `max_buffers` buffers, charge standing in for cost. Right?
3. Follow-up: a read-only `distribution_check` agent tool and a ledger card section as their own WO?
4. Ledger shortfall shared in proportion to each tier's deficit.

**PR #96 (WO-4.6)**, https://github.com/crazie450-prog/GretecOS/pull/96
1. D243 (1): options saved as proposed plans at design time; old proposed power plans pile up until a cleanup.
2. D243 (2): the Power board, marked for Clayton's approval.
3. D243 (3): buffers and LSC only once a peak window is given; Now and Glance still show "no power data".
4. D243 (4): the e2e covers "no storage data" only; storage checks run in the harness test.
5. D244: `bound` is a string, `kind`/`fuel` take one value each; the order guard on model text is a heuristic, the structural order the guarantee.
6. D245: HiGHS presolve off (SciPy 1.17/1.18 crashes).
7. Multiblock turbines at LV: should LV exclude multiblocks? (Finding 3.)
8. The generator label still embeds the raw fuel key (e.g. "Naquadah Reactor Mk-II on gregtech:gt.metaitem.01@23326", as in this report's tables).
9. Several options show 0 h effort with parts not counted. (Finding 4.)

**New from this review**
- **Q-G4-1:** D255, how the four lines are asked for (modes, tiers, kinds, fuels). The naquadah line is recorded as "generator side only": it imports its rod and designs no fuel chain, and the MkI fluid line waits on modelling GoodGenerator's Large Naquadah Reactor (finding 2). Model that reactor in a follow-up WO before G4, or after?
- **Q-G4-2:** fix finding 1 (EU costed in power networks) before signing G4? The reviewer recommends yes: two of the four benchmark lines depend on it, and every mode's chain choice does too.
- **Q-G4-3:** imported late-game fuels (Infinity Plate, Bismuth Plasma) at LV-IV: exclude fuels above the tier cap, score them as scarce, or leave them as options (finding 3)? Until you answer, they stay listed and ranked as now.
- **Q-G4-4:** what does the "light-fuel loop" benchmark mean? Once EU is priced (finding 1), the likely best MV light-fuel line imports light or heavy fuel through one gate; a self-fed loop with net EU/t above 0 may not exist at MV on this data. Is "one gate, net > 0" a pass, or must it be self-fed (then at which tier)?

---

## Clayton's PC steps before signing G4

These run after G3's PC steps (`docs/reviews/G3_review.md`, which build on G2's and `phase1_pc_checks.md`) and don't repeat them. They share G1's Lab sitting (`docs/runbooks/g1_golden_lab.md`): same gos-dev clone, same Lab world. Commands marked **gos-dev** run in `runas /user:gos-dev powershell` in `C:\GregtecOS-dev\GretecOS`, against gos-dev's own GOS_HOME. Commands with `$gos` run in Clayton's own normal PowerShell window (`$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe`). Use `$ProgressPreference = 'SilentlyContinue'`. Nothing here writes to Play. Lab parts (steps 2 and 4) are ordinary play, about 2 to 3 h.

**0. Deploy `main` with CI green**, at `b169669` or later, or later still once finding 1's fix merges (Q-G4-2). In the profile clone: `git pull`, then `uv run gos deploy`. As gos-dev: `git pull` in `C:\GregtecOS-dev\GretecOS`.

**1. A fresh export with this GregtecLink, both homes.** WO-4.1 added `gt_power.json` and WO-4.5 its `storage` (schema 5), so older DBs have no power or storage data. Re-run `phase1_pc_checks.md` steps 4a and 4b (the Export run, then `gos db build` for gos-dev and for Clayton). Then, gos-dev:
```powershell
uv run gos power generators | Select-Object -First 5
uv run gos dist cable --tier EV --amps 2 --segment mte:1200=3
uv run gos dist transformer --from EV --to LV --amps 8
```
- [ ] Generators list. `dist cable` reports the overvoltage on 1x Cobalt Wire (`mte:1200`) and lists EV cable options. The transformer chain prints EV → HV → MV → LV. Neither says "no storage data".

**2. Turbines in the Lab (WO-4.1 acceptance):** `docs/runbooks/turbines_lab.md` §1-3 as written, gos-dev for the 15 `gos power turbine` commands, the Lab for the builds.
- [ ] Every scanner reading equals the saved output exactly (plasma regulated per second, settle times as the runbook says).

**3. The four D65 lines on the full pack, gos-dev** (about 5-15 min; note any design over 60 s). gos-dev's My base empty and balanced weights, as for G3 step 3.
```powershell
uv run gos power design best --tier MV --kind single_block --fuel fluid:liquid_light_fuel
uv run gos power design best --tier EV --kind gas_turbine --fuel fluid:benzene
uv run gos power design self_sustaining --tier EV --kind boiler_steam --fuel fluid:creosote
uv run gos power design best --tier IV --kind single_block --fuel gregtech:gt.metaitem.01@23326 gregtech:gt.metaitem.01@26326 gregtech:gt.metaitem.02@22326
uv run gos power design best --tier EV
```
- [ ] Paste each output and its time. For lines 1 and 2: is option 1's chain use (`the chain uses`) close to its `out`? If so, finding 1 reproduces on the full pack.
- [ ] Line 3: `closed: no import outside the free set (validator)` on every option.
- [ ] The last run (`best` at EV, every generator): are these options you'd build? (Q-G4-3.)
- [ ] Note the `items not counted` count on each line's effort line (finding 4).

**4. The tree-farm line in the Lab:** `docs/runbooks/power_objectives.md`, "Checking the D65 tree-farm boiler line in the Lab", steps 1-3, using step 3's option 1.
- [ ] The turbine's scanner output equals the line's `out` (gross EU/t), and the line keeps running on water only.

**4b. The G3 hand-off for the tree-farm chain (G3 row, ±2%),** in the same Lab sitting as step 4. No web planner designs power lines, so the Lab run is the line-total reference [QA:D12].
- [ ] In NEI on the full pack, the Pyrolyse Oven's recipe for the line's log (`r:gt.recipe.pyro/40f1964c203d` on the dev runtime: look it up from the log's "Recipes" page) shows the same EU/t, duration, inputs and outputs as `uv run gos power design ... --json` prints for that node.
- [ ] In the Lab build: the pyrolyse ovens' creosote per second and the turbine's EU/t are within ±2% of the line's numbers (creosote rate and `out`).

**5. Ledger and distribution, Clayton** (writes only his GOS home and vault, not Play):
```powershell
& $gos ledger --write
& $gos power design self_sustaining --tier EV --kind boiler_steam --fuel fluid:creosote --accept 1
& $gos ledger
& $gos dist ledger --window 600
```
- [ ] `ledger --write` reports the plans accepted before WO-4.4 (if any) joining the ledger (PR #88).
- [ ] The accepted line is a source at EV with its net EU/t; `base/power.md` is in the vault.
- [ ] Retire it afterwards, unless you mean to build it: your vault keeps it as a ledger source until you do. `& $gos plan --status <pw-id> retired`, then `& $gos ledger` shows it gone.

**6. Power view and the 12 power-mode cases (WO-4.6 acceptance):** read `docs/reviews/WO-4.6_power_modes.md`, then open the dashboard, press 5, at 2560x1440 and on the phone. To see the cases on your own pack, gos-dev:
```powershell
uv run gos eval power-modes --report "$HOME\power_modes_fullpack.md"
```
- [ ] Each case gives 2-3 options you'd consider (finding 3 lists the ones this reviewer would question).
- [ ] Approve or mark up the Power board (D92, D243 (2)), including where the effort card sits (PR #85 Q5) and the Base "Running lines" panel (PR #88 Q3).

**Later, not before G4:** the telemetry reconcile (`gos ledger` showing `tel_eu_in`/`tel_eu_out`) once OC telemetry is live on Play (WO-7.1, PR #88).

**Required before Clayton signs G4:**
1. Finding 1 fixed (or Clayton's explicit "accept the two lines as they are", Q-G4-2), with this review's xfails turned into plain tests.
2. Steps 0 to 6 (with 4b) and their paste-backs.
3. His answers, or "keep as built", to Q-G4-1 to Q-G4-4 and the Phase 4 questions above (D158: the defaults stand and are reversible).

---

## Risks

1. **Finding 1 changes the numbers of every power line**, not only the two that fail: each chain may get cheaper in EU. The 12 power-mode cases and the WO-4.6 report should be re-run after the fix. (Likely.)
2. **The full pack has many more fuel routes,** so the chain the solver picks may differ from the dev runtime's; finding 1 could be larger or smaller there. (Size unknown.)
3. **Imported fuels dominate `best` and `max_output`** at some tiers (finding 3) until Clayton sets a rule. (Confirmed on the dev runtime.)
4. **Effort is uncounted for controllers** on this DB, so `output_per_effort` and the effort term in `best` rest on lower bounds (finding 4). (Confirmed.)

## After the review

- Added: `tests/integration/test_g4_lines_harness.py`. It designs the four D65 lines on the server harness DB with the effort model (D255). It checks every option's numbers (net = gross - chain), citations, validator verdict and effort breakdown. It checks the tree-farm and naquadah chain recipes against the client harness's NEI dump with G3's check (the G3 hand-off). It accepts the tree-farm and naquadah lines and a steel MV line into the ledger, and runs the ledger's nets through `distribution.from_ledger`. The light-fuel and benzene lines are strict xfails (`raises=AssertionError`) on the whole of D255 (3)'s pass rule (2-3 ranked options, net > 0, chain < 99.9%) until finding 1 is fixed. CI's mod job runs it with the other integration tests (about 1.5 min).
- Decision D255 records how the four lines are asked for and judged.

---

## Addendum, 2026-11-13: WO-4.7 (findings 1 and 5)

Added after the review by WO-4.7 (branch `wo/4.7-power-chain-eu`, decisions D323-D325). The report above is unchanged; this section records the G4 rows as they stand with WO-4.7's solver. Numbers are the designer's on a fresh server harness run (`20261113T181059Z`, 35 checks; client run `20261113T181231Z`, 56 checks), balanced weights, default rotor, nothing owned [here, WO-4.7's container].

**What changed.** Finding 1: a power network now costs its EU (D323). After the gate count the solver minimises the EU the chain draws plus the EU an import of the fuel stands for (the generator's EU per unit), with the gated quantity only as a tiebreak, so the chain makes or converts fuel only where that takes less EU than the fuel gives, and the gate is chosen in EU (one scale for every gated item: the stage-2 row-scaling issue no longer decides anything in a power line). Finding 5: a recipe idle at that optimum is left out of the line instead of the every-machine-runs replay, which had forced the naquadah option's hammer/cutting-saw loop (its floor was 1e-3 x the generator's 1 craft/s, not `FLOOR_MIN`). Also: each chain pinned to another recipe for the fuel is now its own option (D325: before, they shared one id and only the last was kept), and net EU/t = gross - the chain's nodes. Production lines solve exactly as before (`test_g3_lines_harness::test_production_lines_solve_exactly_as_before_the_eu_stage`; a one-off run of the old `solve.py` gave byte-identical plan JSON for the 13 G3 lines, base and base+addons, 74 plans).

| Criterion | Verdict now | Evidence |
|---|---|---|
| Light-fuel loop | **met under D324's default reading (one gated import, `best` at MV); the self-fed reading: not at MV, at HV** | 2 options on the Advanced Combustion Generator (`mte:1111`, `r:gt.recipe.dieselgeneratorfuel/3f787facee1e`, 128 EU/t): toluene distilled into light fuel (`r:gt.recipe.distillery/8b48f69a9eb6`, net 122.020438 EU/t, chain 5.98) and lightly hydrocracked heavy fuel distilled into light fuel (`r:gt.recipe.distillery/f6d0602fa385`, net 122.394161, chain 5.61). No gate-free line exists at MV; at HV the Turbo Combustion Generator (`mte:1112`) has one from oilsands ore and leaves, net 23.5501883 of 512 EU/t. Q-G4-4 stays Clayton's (D324). Tests: `test_the_line_designs_two_or_three_ranked_options[light_fuel]`, `test_the_self_fed_reading_of_the_light_fuel_loop` |
| Benzene gas turbines | **met on the dev runtime** | 3 options on the Large Gas Turbine (`mte:15527`, `r:gt.recipe.gasturbinefuel/28b9a71641df`, 972 EU/t): net 924 (chain 48: `r:gt.recipe.distillery/5030c8579d45` from moderately steam-cracked light fuel), 900 and 900 (chain 72: `r:gt.recipe.distillery/6e096a692f9c` from moderately steam-cracked heavy fuel, `r:gt.recipe.distillery/412211f0fdfb` from lightly steam-cracked light fuel); each imports one gated cracked fuel. `[benzene]` and `test_the_benzene_chain_draws_well_under_the_turbines_output` (chain < 25% of gross) |
| Steam boilers on a tree farm | unchanged: met on the dev runtime; Lab PC step | same 2 options (35.8549306 and 29.5786111 EU/t), both proven closed |
| Naquadah fuel | unchanged: generator → ledger only (finding 2) | 3 options, each importing its rod or long rod through one gate; no hammer/cutting-saw loop in any option (`test_no_option_keeps_a_zero_sum_loop`, all four lines) |
| G3 hand-off (±2% of NEI) | **light fuel and benzene: chain recipes met on the dev runtime** (as the tree farm); naquadah: side recipes only | `test_the_g3_hand_off_chain_recipes_match_nei` now runs on all four lines: passed |
| End to end | unchanged | `test_accepted_lines_reach_the_ledger_and_the_distribution_checks`: passed |

`GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration/test_g4_lines_harness.py`: **17 passed** (1 min 41 s; the two strict xfails are plain tests now). The 12 power-mode cases were re-run (`gos eval power-modes`, all 12): **12 of 12 pass**; `docs/reviews/WO-4.6_power_modes.md` is regenerated. Clayton's PC step 3 commands are unchanged; for lines 1 and 2, the chain's use should now be far below `out`.

Required before Clayton signs G4, item 1 ("finding 1 fixed ... with this review's xfails turned into plain tests"): done in code, pending WO-4.7's review and merge; Q-G4-4 (D324) is his.

## Addendum, 2026-11-13 (later): WO-4.7 after its review (the direct-import line)

Added by WO-4.7's review round (PR #101; D323-D325 updated). The two sections above are unchanged. WO-4.7's review (H1) found that the EU stage prices only the fuel: an import one recipe short of the fuel costs no EU, so every chain imported a near-fuel and converted it rather than the fuel itself (naquadah Mk-II: long rods sawn on `r:gt.recipe.cuttingsaw/392f28a54a60`, with a 5-recipe lubricant chain for the saw). The fix keeps the EU stage and adds, for every candidate outside `self_sustaining`, its **direct-import line** (the generator alone, the fuel imported as it is; `power.direct` in the plan, its own id), which `planner/rank.py` ranks with the chains. Same harness runs as above, balanced weights, nothing owned.

| Criterion | Verdict now | Evidence |
|---|---|---|
| Light-fuel loop | **met under D324's default reading (one gated import, `best` at MV); self-fed: not at MV, at HV** (unchanged verdict; Q-G4-4 stays Clayton's) | 3 options on the Advanced Combustion Generator (128 EU/t): light fuel imported as it is (net 128), toluene distilled 1:1 (`r:gt.recipe.distillery/8b48f69a9eb6`, net 122.020438), lightly hydrocracked heavy fuel distilled (`r:gt.recipe.distillery/f6d0602fa385`, net 122.394161). None is a loop. "None self-fed at MV" is now shown on the chains themselves: every MV light-fuel chain's fewest gates over all its routes is 1 (`fewest_gates`, review L5). At HV: direct 512, one-gate chain 488.277992, gate-free chain 23.5501883 (the gate count still comes first, D323) |
| Benzene gas turbines | **met on the dev runtime** | 3 options on the Large Gas Turbine (972 EU/t): benzene imported (net 972), distilled from moderately steam-cracked light fuel (net 924, chain 48), from moderately steam-cracked heavy fuel (net 900, chain 72) |
| Steam boilers on a tree farm | unchanged | `self_sustaining` has no direct-import line; same 2 options (35.8549306, 29.5786111 EU/t) |
| Naquadah fuel | unchanged: generator → ledger only (finding 2) | 3 options: Mk-II on rods imported (`@23326`, net 8192), Mk-I on rods sawn into bolts (net 2047.98), Mk-I on bolts imported (net 2048); no zero-sum loop. `test_the_naquadah_mk2_line_offers_its_rod_as_imported` |
| G3 hand-off (±2% of NEI) | unchanged | the naquadah line's ranked options now run only an NEI-hidden saw, so the check also takes each fuel's own design (the rod's runs the lubricant chain): passed |
| End to end | unchanged | `test_accepted_lines_reach_the_ledger_and_the_distribution_checks` (naquadah's first option is now the direct Mk-II line): passed |

`GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration/test_g4_lines_harness.py`: **18 passed** (one test added). The 12 power-mode cases re-run: **12 of 12 pass**; `docs/reviews/WO-4.6_power_modes.md` regenerated again. For Clayton's PC step 3, the first option of lines 1, 2 and 4 is now the fuel bought as it is; the chain options follow it.
