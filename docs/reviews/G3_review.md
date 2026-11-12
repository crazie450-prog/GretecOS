# Gregtec OS: G3 gate review (Phase 3, planner engine and production designer)

2026-11-12 · independent gate reviewer (D41, D158 (3)) · reviewed: `origin/main` at `90b6782` (PR #77), which holds the Phase 3 PRs #57 (WO-3.1), #59 (WO-3.2), #63 (WO-3.3), #66 (WO-3.4), #72 (WO-3.5), #78 (WO-3.6) and #68 (WO-3.8). **WO-3.7 (PR #80) is not merged:** it is open at `8bdaaba`, mergeable, with all five CI jobs green on that head. This report marks its rows "not merged" and doesn't review its code.

## Verdict

**G3 is not ready to sign yet, for two reasons, and only one of them is code.**

1. **WO-3.7 is not merged.** The Designer view, the `line-designer` skill, the text → accepted plan path and `task_max_usd` are all in PR #80. Until it merges, the gate's "Designer" line (§10) can't be checked on `main`. Its CI is green, so this is an ordering problem, not a defect.
2. **The G3 row's reference checks are partly a PC job by design.** Every check that can run without the full pack, the site and the Lab ran here:
   - **Per machine (NEI):** all 129 distinct recipes behind the 182 distinct (machine, recipe, tier, OC, parallel) tuples of the 13 benchmark designs the dev runtime has match NEI's own dump exactly: EU/t, ticks, every amount and every chance. The OC and parallel half equals GT's own classes bit for bit (WO-3.1's oracle).
   - **Per line (gtnhplanner.com):** all 13 designs import into the live site with no problems. 10 of 13 machine totals are within ±2% (9 equal). **3 are not:** polyethylene, silicon and the platline. GOS sizes 6–14% more machines than the site. By [QA:D12] the Lab decides those three, and that's a PC step.
   - **Not doable here:** nanocircuits and wetware circuits (the dev runtime lacks GTNH's circuit recipes), the Lab runs, and "at most 2 small lines fully built in the Lab".

Everything else in Phase 3 is met, or met with fakes where a PC or site check remains. Nothing in a merged WO is "not met" in code. No product code was changed by this review. One test was added: `tests/integration/test_g3_lines_harness.py`, which makes the NEI half of the G3 row a repeatable check (D254).

Evidence tags, as in G0–G2:
- **[repo]:** checked in the repo, the git history or GitHub's CI logs.
- **[here]:** run in this reviewer's build container (Linux) on `review/g3` (= `90b6782` plus the new test).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC, the full pack, the Lab or his eye. Nothing from the PC is in the evidence yet for Phase 3.

### This reviewer's runs [here]

| Check | Result |
|---|---|
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 356 files already formatted (357 with the new test) |
| `uv run --locked pytest -q` | **5116 passed, 234 skipped** (5 min 27 s) |
| Phase 3 unit suites (`tests/unit/gtmath`, `tests/unit/planner`, `tests/unit/tools/test_my_base*.py`) | 2102 passed. `test_gt_unit_tests.py` 105, `test_machines.py` 88, `test_validate.py` 50 |
| `./gradlew --no-daemon --max-workers=2 runServer -Pharness` | `harness passed: 34 checks, run 20261112T160126Z` (1 min 35 s) |
| `xvfb-run ... ./gradlew --no-daemon --max-workers=2 runClient25 -Pharness` | `harness passed: 55 checks, run 20261112T160300Z` (1 min 44 s) |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest -v` on `test_gtmath_harness`, `test_machines_harness`, `test_graph_harness`, `test_plan_harness`, `test_rank_harness`, `test_outputs_harness`, `test_my_base_harness`, `test_golden_recipes` | **76 passed, 1 skipped** (13 min 46 s). The skip is `test_benchmarks_return_two_or_three_ranked_routes[fluid:sulfuricacid-2]`, which the test itself skips: "the dev runtime offers one recipe per primary choice" |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration/test_g3_lines_harness.py` (new) | **2 passed** (2 min 53 s) |
| `pnpm -C dashboard install --frozen-lockfile`, `build` (`tsc --noEmit` + `vite build`) | clean |
| `pnpm -C dashboard test` (vitest) | 262 passed (11 files) |
| `pnpm -C dashboard e2e --workers=1` (own ports and homes) | **39 passed**, including the 7 My base specs |
| `node dashboard/e2e/gtnhplanner-import.cjs` on the 13 benchmark designs (live gtnhplanner.com) | 13 of 13 import with no problems and no page errors. Machine totals in "G3 row" below |
| `uv run python tests/integration/test_g3_lines_harness.py report <client home with the 13 plans>` | 182 tuples, 182 `ok` |

PR #78 reported two `test_plan_harness` failures (light fuel and epoxid) on a local 2026-11-06 export. They don't reproduce on fresh runs: 17 of 17 passed here and on CI's mod job on `90b6782`. They came from a stale shared harness folder, not from the code.

### CI on `main` [repo]

| Commit | PR | Run | Result |
|---|---|---|---|
| `67da7f0` | #57 WO-3.1 | 43004469888 | success |
| `9c17ee4` | #59 WO-3.2 | 43004565234 | success |
| `54d1e6f` | #63 WO-3.3 | 43072400497 | success |
| `d313f2c` | #66 WO-3.4 | 43074602386 | success |
| `46067e8` | #68 WO-3.8 | 43172842301 (+ dashboard Windows 43172842217) | success |
| `9c8d5ce` | #72 WO-3.5 | 43193002904 | success |
| `b5899c6` | #78 WO-3.6 | 43207194916 (+ dashboard Windows 43207194949) | success |
| `90b6782` | #77 (head) | 43215106534: ubuntu, windows, dashboard, mod; 43215106614: mod (Windows) | all success |

On `90b6782` the `mod` job ran `pytest tests/integration` on fresh server and client harness runs: **142 passed, 20 skipped**. That includes every Phase 3 harness file: graph 19, gtmath 5, machines 2, my_base 2, outputs 4, plan 17, rank 18 + 1 skip [repo]. The 20 skips are the quest pack, the structures dump, downloaded voice models and the sulfuric-acid route skip; none is a failing Phase 3 check.

---

## Phase 3 work orders

### WO-3.1: GT math port

> **Acceptance:** "All ported GT unit tests pass (≥ 50) [E§2.10]"

**Verdict: met.**

- PR #57, merge `67da7f0`, D173–D175 [repo].
- `tests/unit/gtmath/test_gt_unit_tests.py`: **105 passed**. They cover all 50 test methods of GT's own test file at the pin, transcribed as data [here, PR].
- Beyond the criterion, the port was checked two more ways. `test_oracle_vectors.py` has 1 546 vectors from GT's real classes, matched bit for bit. `test_gtmath_harness.py` replays the oracle and checks every GT recipe of a real run (5 passed here and on CI) [here, repo].
- Pinned: `gtmath.GT_VERSION` must equal `config.GT_PIN`, or the suite fails [repo].
- Licence: GT is LGPL-3.0. Its source was read only to learn the behaviour, and no code was copied (D173) [PR].

Open: PR #57 Q8 (a recipe's circuit is `Supply(0, n)`); WO-3.3 applied it (`RecipeNode.supplies()`), so it is answered in code.

### WO-3.2: Machine model and selection

> **Acceptance:** "Table tests"

**Verdict: met.**

- PR #59, merge `9c17ee4`, D176–D178 [repo].
- `tests/unit/planner/test_machines.py`: **88 passed**. Its table rows (recipe × machine × tier) cover single blocks, steam, multiblocks, coils, cleanroom, low gravity, batch mode, badges and state inputs [here].
- `test_machines_harness.py` (EBF heat rule, a sample of every GT recipe): 2 passed [here, repo].
- Known divergence (D177): GT keeps some speed bonuses as `float`, so a duration can be one tick off. The G3 NEI check below doesn't see this (NEI shows base values); the gtnhplanner comparison and the Lab do.

### WO-3.3: Graph builder, pruning, chain templates

> **Acceptance:** "Builds the platline graph in under 2 s"

**Verdict: met.**

- PR #63, merge `54d1e6f`, D189–D191 [repo].
- `test_graph_harness.py::test_platline` asserts the platline (platinum dust at IV) builds in under 2 s on a real harness DB. It passed here and on CI. PR #63 measured 0.33 s; here the whole three-route design (graph + MILP) of the platline took 1.22 s [here, repo, PR].
- All 19 graph harness tests passed: templates hold, epoxid stays out of other chains, acids stay out of the platline, assembly-line research [here].
- PC (optional): `gos graph --check-chains` on the full pack (step 3 below).

### WO-3.4: Staged MILP, alternatives, pins, integerisation

> **Acceptance:** "Platline: all machines run, ≤ 2 externals. Light-fuel loop: 0 gates. 12 identical re-solves. Pinning a non-primary node changes only its branch"

**Verdict: met.**

PR #66, merge `d313f2c`, D192–D195 [repo].

| Part | Status | Evidence |
|---|---|---|
| Platline: all run, ≤ 2 externals | met | `test_plan_harness::test_platline_all_machines_run_with_at_most_two_externals`. On the PlanNH fixture: `test_solve::test_platline_all_machines_run_with_at_most_two_externals` [here]. This review's best platline route: 1 gate, every machine running (`plan:pl-f4b6d68761`, server run `20261112T160126Z`) |
| Light-fuel loop: 0 gates | met | `test_plan_harness::test_light_fuel_closes_with_no_gates` and `test_solve::test_light_fuel_loop_closes_with_no_gates` [here]. This review: `plan:pl-5cd5a7f08f`, 0 gates |
| 12 identical re-solves | met | `test_plan_harness::test_twelve_identical_resolves` (byte-identical plan JSON) [here] |
| Pin a non-primary node: only its branch changes | met | `test_plan_harness::test_pinning_a_non_primary_node_changes_only_its_branch` (≥ 3 platline nodes) and `test_plan::test_pinning_a_non_primary_node_changes_only_its_branch` [here] |
| Fixtures from PlanNH's test resources | met | `tests/fixtures/plannh/` (MIT, in `NOTICE.txt`) [repo] |

Open: PR #66's gtnh-flow `NOTICE.txt` block should be compared with upstream's LICENSE (step 9 below).

### WO-3.5: Metrics, effort, scarcity, deterministic ranking, batch, explanations, validator

> **Acceptance:** "The same request gives the same order every run; the validator catches injected wrong numbers and passes 20 normalisation cases; each benchmark returns 2–3 routes differing in ≥ 1 primary choice; ≥ 2 benchmarks show an addon column with `machine_fact` confidence; for 3 targets, batch totals equal rate × time within ±0.1%"

**Verdict: met with fakes. The addon column on real addon machines is a PC step.**

PR #72, merge `9c8d5ce`, D196–D199 [repo].

| Part | Status | Evidence |
|---|---|---|
| Same order every run | met | `test_rank_harness::test_same_request_same_order` (12 targets, byte-identical), `test_rank::test_same_order_whatever_the_input_order`, `test_float_noise_cannot_decide_the_order` [here]. PR #72's reviewer checked `PYTHONHASHSEED` 1, 2, 3 and 77 [PR] |
| Validator: injected wrong numbers caught | met | `test_validate::test_injected_wrong_numbers_are_caught`, `test_metrics::test_template_with_injected_wrong_numbers_is_caught`; on real designs, near misses in `test_rank_harness` [here] |
| Validator: 20 normalisation cases | met | `test_validate::test_twenty_normalisation_cases` [here] |
| 2–3 routes differing in ≥ 1 primary choice | met | `test_rank_harness::test_benchmarks_return_two_or_three_ranked_routes`: 11 of 12 targets. Sulfuric acid gives 1, because the dev runtime has one recipe per primary choice. The test checks this, it doesn't assume it [here]. Worth re-checking on the full pack (step 3) |
| ≥ 2 benchmarks with an addon column with `machine_fact` confidence | **met with fakes** | `test_metrics::test_addon_column_shows_machine_fact_confidence` and `test_plan::test_addon_column` on the fixture DB. The dev runtime has no addon multiblocks [here]. PC: step 3's `--addons` runs |
| Batch totals = rate × time ±0.1% for 3 targets | met | `test_rank_harness::test_batch_totals_equal_rate_times_time` (steel, nitric acid, platinum dust), `test_plan_harness::test_batch_totals` [here] |

Explanations go through `explain.explain()` and the validator. The model never orders routes (D199). WO-3.7 adds the real planner-model call (`plan_explain`, not merged).

### WO-3.6: Outputs

> **Acceptance:** "Exports open in gtnhplanner.com and render in gtnh-flow; BOM sums equal the plan JSON; footprints equal `structure`"

**Verdict: met with fakes. gtnh-flow rendering and footprints on real structure data are PC steps.**

PR #78, merge `b5899c6`, D200–D202 [repo].

| Part | Status | Evidence |
|---|---|---|
| Exports open in gtnhplanner.com | met | This review imported all 13 benchmark designs into the live site with `dashboard/e2e/gtnhplanner-import.cjs`: every card, drawer and tab shown, no page errors [here]. PR #78 did steel, light fuel and the platline [PR] |
| Render in gtnh-flow | **PC step** | The YAML passes gtnh-flow's published schema (D202) and `test_outputs::test_gtnhflow_yaml` [here]. Running the tool was refused in the build container, so the render is `plan_outputs.md` check 2 |
| BOM sums equal the plan JSON | met | `test_outputs_harness::test_bom_sums_equal_the_plan_json`, `test_outputs::test_bom_sums_equal_the_plan` [here] |
| Footprints equal `structure` | **met with fakes** | `test_outputs::test_footprints_equal_structure` on synthetic structure rows. On the harness DB every multiblock is `footprint: unknown`, because the harness has no structure dump [here]. PC: `plan_outputs.md` check 3, after a run with the structure dump (WO-1.8, V09) |

The flow card (React Flow + elkjs, D159) and the PNG need Clayton's eye on a real plan (`plan_outputs.md` check 4).

### WO-3.7: Designer view

> **Acceptance:** "End-to-end: text request → accepted plan in the vault (voice after G5); a pin made in the Designer survives reload and re-solve; `task_max_usd` is set from the measurements"

**Verdict: not merged.** PR #80 is open at `8bdaaba`, with D250–D253. GitHub reports it mergeable and clean, and all five check runs are green on that head (ubuntu, windows, dashboard, dashboard-windows, mod) [repo]. Not reviewed here. From its PR text [PR]:

| Part | Status |
|---|---|
| Text request → accepted plan in the vault | not merged (PR: `test_designer_harness.py` on the harness DB; the real-key run is `designer.md` B) |
| A pin made in the Designer survives reload and re-solve | not merged (PR: e2e `a pin made in the Designer survives a reload and a re-solve`) |
| `task_max_usd` set from the measurements | not merged, and **PC step (paid)** after merge: `designer.md` C. PR #80 says it stays open until Clayton's real run |

Once #80 merges, a short addendum to this report should re-run its e2e and harness test on `main`.

### WO-3.8: My base panel

> **Acceptance:** "A plan's boundary changes when stock or owned machines change; a tier override sticks"

**Verdict: met. The Base view needs Clayton's board approval (D92).**

PR #68, merge `46067e8`, D210–D212 [repo].

| Part | Status | Evidence |
|---|---|---|
| Boundary changes with stock | met | `test_my_base_designer::test_stock_makes_a_boundary` [here] |
| Boundary changes with owned machines | met | `test_my_base_designer::test_owned_machines_change_the_chosen_recipes`; on real data `test_my_base_harness::test_the_designer_follows_my_base_on_real_data` [here] |
| Tier override sticks | met | `test_my_base_api::test_a_tier_override_sticks_across_restarts_and_db_rebuilds`, `test_my_base_harness::test_my_base_outlives_a_rebuild_on_real_data`, `test_the_tier_override_sets_the_cap`, e2e `the tier override sticks, shows in the header and clears` [here] |
| Base view look | **Clayton's board approval** | Built to `docs/design/system/` with no board (D158 (5)); e2e checks WCAG contrast in every theme and the phone fit [here] |

---

## G3 row

> **G3:** "14 production benchmark lines (D65), PM draft for Clayton to edit at G2. [...] Plus the fuel chains of the 4 power lines (G4). Every distinct (machine, recipe, tier, OC, parallel) within ±2% of NEI or a single-machine Lab run; line totals within ±2% of gtnhplanner.com or PlanNH; when references disagree, the Lab run decides and the report names the reference used [QA:D12]; at most 2 small lines fully built in the Lab" · "Reviewer report; Clayton sign-off"

How the references are read is D254 (judgement call, below).

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| The 14 lines (PM draft; Clayton edits it) | **partly; question** | 12 of 14 design on the dev runtime. Nanocircuits and wetware circuits don't: GTNH's circuit recipes are in its core mod, which the harness doesn't load (PR #63) → step 3. No record shows Clayton edited the PM draft (Q-G3-1) |
| Fuel chains of the 4 power lines | partly; G4 | Light fuel is designed and checked here (0 gates). Benzene, the tree-farm steam and naquadah fuel chains belong to WO-4.2 (PR #81, open, not merged) and G4 |
| Every distinct (machine, recipe, tier, OC, parallel) within ±2% of NEI | **met on the dev runtime; full pack is a PC step** | 13 designs, 182 distinct tuples, 129 distinct recipes: every recipe matches NEI's own line exactly (`test_g3_lines_harness`, [here]). The OC and parallel come from `gtmath`, which equals GT's classes bit for bit (WO-3.1). Step 4 re-runs this on the full pack |
| Line totals within ±2% of gtnhplanner.com (or PlanNH) | **10 of 13 met; 3 not met → Lab** | Table below. PlanNH isn't a reference: D66 dropped the PlanNH spike |
| When references disagree, the Lab decides, and the report names the reference | **PC step** | Polyethylene, silicon and the platline (step 6) |
| At most 2 small lines fully built in the Lab | **PC step** | Step 7 suggests steel (EBF) and sulfuric acid |
| Reviewer report | met | this report |
| Clayton's sign-off | PC | after the steps below |

### The benchmark designs [here]

Designed on the server harness run `20261112T160126Z`: `qty 1/s`, the tier caps of `test_plan_harness.py` / `test_rank_harness.py`, base machines, nothing owned or stocked, best-ranked route. Machine totals are GOS's own (`count` summed over nodes) and the site's MACHINES total after import. Plan ids are this review's scratch saves; the numbers come from the planner, not from this page's author.

| Line | Tier | Plan | Routes | Gates | All run | GOS machines | Site MACHINES | GOS vs site |
|---|---|---|---|---|---|---|---|---|
| steel (EBF) | MV | `plan:pl-9aa8fb4aee` | 3 | 0 | yes | 53 | 53 | equal |
| polyethylene | MV | `plan:pl-ca0e919a14` | 3 | 1 | yes | 52 | 49 | **+6.1%** |
| sulfuric acid | MV | `plan:pl-f3098410f6` | 1 | 0 | yes | 8 | 8 | equal |
| silicon (boule) | HV | `plan:pl-7d825962b6` | 3 | 0 | yes | 492 | 453 | **+8.6%** |
| rubber (sheet) | MV | `plan:pl-b14a26d07d` | 3 | 0 | yes | 14 | 14 | equal |
| platline (platinum dust) | IV | `plan:pl-f4b6d68761` | 3 | 1 | yes | 128 | 112 | **+14.3%** |
| rare earth (neodymium) | LuV | `plan:pl-9feb26cd07` | 3 | 1 | yes | 77 | 76 | +1.3% |
| epoxid | EV | `plan:pl-424dc0cb92` | 3 | 3 | yes | 62 | 62 | equal |
| stainless steel | HV | `plan:pl-a68c1d294d` | 3 | 1 | yes | 4 | 4 | equal |
| nitric acid | HV | `plan:pl-abad9ea15f` | 3 | 1 | yes | 5 | 5 | equal |
| polybenzimidazole | IV | `plan:pl-ce99feb1d4` | 3 | 1 | yes | 2 | 2 | equal |
| naquadah | ZPM | `plan:pl-c07af822a0` | 3 | 7 | yes | 28 | 28 | equal |
| light fuel (G4 chain) | MV | `plan:pl-5cd5a7f08f` | 3 | 0 | yes | 7 | 7 | equal |
| nanocircuits | — | not in the dev runtime | | | | | | step 3 |
| wetware circuits | — | not in the dev runtime | | | | | | step 3 |

Where GOS and the site differ, GOS always sizes more machines. That fits GOS's conservative defaults (D177: one or two plain hatches, the lowest coil that runs the recipe, the worst state) against the site's own overclock math with no machine bonuses (D202). Re-designing silicon and the platline as single-route designs with `coil_max` raised moved their totals by under 3%, so coils are not the main cause [here]. The site shows no per-card breakdown the import script can read, so which nodes differ is left to step 6. **The reference for these three is the Lab, not the site** [QA:D12].

---

## Findings

1. **WO-3.7 isn't merged** (above). Merge PR #80, then add an addendum to this report.
2. **The naquadah benchmark's best route uses the Elemental Duplicator** (`gt.recipe.replicator`) to make sodium and fluorine from UU matter (two nodes, `r:gt.recipe.replicator/17da663e76ff` and `r:gt.recipe.replicator/17f9ad1cdf79`, in `plan:pl-c07af822a0`). It also imports 7 items through gates. PR #63 Q5 already asked whether to rank the replicator last everywhere. This review recommends answering that before the full-pack naquadah design is judged (Q-G3-3). It is a gameplay preference, so it's Clayton's call, not a defect.
3. **Three lines differ from gtnhplanner.com by more than 2%** (table above). The Lab decides (step 6).
4. **Footprints are unproven on real data:** no harness run has the structure dump (`plan_outputs.md` check 3, V09).
5. **Design time on a full pack:** on the server harness DB the 13 designs took 0.2–23 s each (rare earth 23 s, epoxid 20 s). PR #72 measured about 90 s for steel on the client DB, with effort included (D196). The full pack will be slower. WO-3.7's Designer depends on this. Watch it in step 3 and report the times.
6. **The ranked best route isn't always the smallest:** silicon asked for 1 route gives a 402-machine route; asked for 3 routes, the top-ranked one has 492 machines. D198's balanced weights trade machines against effort, power, scarcity and complexity, so this is intended. Clayton should see it when he reviews `me/prefs.md` weights (PR #72's PC note).
7. **Decision rows D189–D202 still read "Pending reviewer"**, though the per-PR reviewers agreed (see their PR bodies). The log is append-only; the PM folds the verdicts at the next plan version. This reviewer agrees with D189–D202 as built, with finding 2 as the open point on D190 and D198.

---

## VERIFY items Phase 3 touched

No Phase 3 PR added or changed a row in `core/gregtec/verify.tsv`, and no §13.2 row names a Phase 3 WO [repo].

| Item | Relation to Phase 3 | Status |
|---|---|---|
| [E§2.3] "2 A per standard hatch" (UNCERTAIN in the research) | WO-3.1 confirmed it from GT's source (D175): one plain hatch runs at 1 A, two at 4 A | Resolved in D175; not a V row |
| **V09** (gtnh-extractor re-pinned to 2.9.0 builds and runs) | WO-3.6's footprints need its structure dump to be proven on real data | Open (WO-1.8). Blocks "footprints equal `structure`" on real data only |

---

## Open questions from Phase 3's PRs

None is answered by a later decision (D200–D253 hold no answers to them). The defaults stand, reversible, until Clayton answers (D158). "Agreed" means the PR's own reviewer agreed; Clayton still decides.

**PR #57 (WO-3.1)**, https://github.com/crazie450-prog/GretecOS/pull/57
- Q1–Q7 (D173–D175: oracle vectors in the repo, a correctly rounded `log`, the EU-budget tolerance, what isn't oracle-checked, laser OC with power ≤ 0, the module list, exotic-only hatches give 0 V, 0 A): all agreed.
- Q8: a recipe's circuit is passed as `Supply(0, n)`. WO-3.3 does this; answered in code.

**PR #59 (WO-3.2)**, https://github.com/crazie450-prog/GretecOS/pull/59
- Q1: should fixed- and custom-power machines (steam multis, Windmill, fusion, Precise Assembler, electric implosion compressor) get a follow-up WO for `voltage`/`amps` facts?
- Q2: keep `processing custom` machines with a map (the Assembly Line) on GT's generic rules with `custom` + `low_confidence` badges, or curate each?
- Q3, Q4: D177's defaults (hatch layouts, coils, batch off, worst state, cleanroom on, build-input defaults): agreed by the reviewer; **pending Clayton**.
- Q5: D178's cleanroom/low-gravity and heat reading: agreed.
- Q6: data gaps for a curation row (Industrial Cutting Factory sawblade cap, Endothermic Fridge with no `machine_heat`).
- Q7: assembly-line research left to WO-3.3 (done there).
- Q8: licence note (no GT code copied).

**PR #63 (WO-3.3)**, https://github.com/crazie450-prog/GretecOS/pull/63
- Q1: D189 side routes (ranked last, reverse use or byproduct) stay listed and pinnable. OK?
- Q2: the reverse-use rule (a use's product counts only without its own main-product recipe). OK?
- Q3: a template's loop item met off the route uses the heuristic. OK?
- Q4: D189/D190 reworded in place before merge. OK?
- Q5: keep `basic-chemicals`, or **rank the replicator (UU matter) last globally**? See finding 2.
- Q6: D191, one fluid in several slots is refused (0 such recipes in the run).

**PR #66 (WO-3.4)**, https://github.com/crazie450-prog/GretecOS/pull/66
- D192–D195 changes as the reviewer asked; new: when one route runs every machine, routes that idle machines are dropped (§7.1 step 4). Epoxid takes about 11 s, the platline 1–1.5 s.
- Compare `NOTICE.txt`'s gtnh-flow MIT block with upstream's LICENSE (step 9).

**PR #68 (WO-3.8)**, https://github.com/crazie450-prog/GretecOS/pull/68
- Q1: D210, My base in the core's tables keyed by mte id and item/fluid key; Appendix D's `base_machine`/`base_stock` unused until a migration drops them. OK?
- Q2: D211, manual first, then census, then events; a manual 0 means none. OK?
- Q3: D212, devices and admin edit, client tokens read, agents never write. OK?
- Q4: which WO generates `base/tier.md`, `machines.md` and `storage.md` (plan 6.1)? No §8 row names them.
- Q5: does the Base view need changes (board approval, D92)?

**PR #72 (WO-3.5)**, https://github.com/crazie450-prog/GretecOS/pull/72
- D196: should component-making prefer maps the route or My base already uses (it sometimes picks fish pond or bacterial vat paths)?
- D196 cost: cache component walks per DB run and tier, or share WO-3.3's reader (about 90 s for steel on the client DB)? See finding 5.
- D197: look over the 26 scarcity rows (PM defaults).
- D198: a machine is "new" when no owned machine runs its map (an LV mixer covers an MV mixer). OK?
- D199: WO-3.7 must call `explain.explain()` and show routes in `rank.position` order; numbers in words aren't read; irregular plurals aren't masked.
- Commit attribution differs between some commits (no action possible without a force push).

**PR #78 (WO-3.6)**, https://github.com/crazie450-prog/GretecOS/pull/78
- Q1: D200 clearance (1 block each side + 1 above, smallest variant; `≥` sums when some are unknown). OK?
- Q2: D201, Pillow for the PNG, elkjs as the only layout, a Mermaid fence in plan notes only; real notes point to `flow.mmd` under §6.1's 40 lines. Amend §6.1 if diagrams should sit in notes?
- Q3: D202, gtnhplanner format from the site's schema with GOS sizing in `metadata.gos`; gtnh-flow with one target. OK?
- Q4: the lifecycle is one step at a time, no backward moves. WO-3.7 adds "reject"/"undo accept" by decision if needed.
- Q5: boundary rates stay in the flow card's `machine` text; add a `rate` field to `FlowNode` in WO-3.7?
- Q6: commit trailers differ between rounds (no action).

**PR #80 (WO-3.7, not merged)**, https://github.com/crazie450-prog/GretecOS/pull/80
- `task_max_usd` stays open until Clayton's paid run (`designer.md` C).
- D251: `plan_explain`'s planner-model calls are metered but outside the lane's per-task cap (D156). OK?
- D253 (cap = worst × 1.5, rounded up to $0.25, at least $0.50): pending Clayton.
- Designer view board approval (D92).

**New from this review**
- **Q-G3-1:** the 14 lines are the PM draft (D65, plan §8 G3). Keep them as they are, or edit the list? This review used the target keys and tier caps of `test_plan_harness.py`.
- **Q-G3-2:** the tier caps for nanocircuits and wetware circuits on the full pack (step 3 suggests IV and LuV; change them to the tiers you'd really build at).
- **Q-G3-3:** rank the replicator last, or exclude it by default, before the naquadah line is judged (finding 2; PR #63 Q5)?
- **Q-G3-4:** D254, how the G3 references are read (below).

---

## Clayton's PC steps before signing G3

These run after G2's PC steps 0 to 3 (`docs/reviews/G2_review.md`: Node.js and pnpm; `phase1_pc_checks.md` steps 1 to 12, which include the full-pack Export run, the DB build for gos-dev (4a) and for Clayton (4b), and the quests load; the setup wizard). They don't repeat them, and they share G1's Lab sitting (`docs/runbooks/g1_golden_lab.md`): same gos-dev clone, same Lab world. Commands marked **gos-dev** run in `runas /user:gos-dev powershell` in `C:\GregtecOS-dev\GretecOS`, against gos-dev's own GOS_HOME. Commands with `$gos` run in Clayton's own normal PowerShell window (`$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe`, as in `phase1_pc_checks.md`). Use `$ProgressPreference = 'SilentlyContinue'`. Nothing here writes to Play. The Lab parts (steps 6 and 7) are ordinary play, about 3 to 5 h in all.

**0. Deploy a green `main` that contains WO-3.7** (PR #80), with all CI jobs green on that commit. Today `main` is green at `90b6782` (runs 43215106534 and 43215106614), but that doesn't have WO-3.7. In the profile clone: `git pull`, then `uv run gos deploy`. As gos-dev: `git pull` in `C:\GregtecOS-dev\GretecOS`.

**1. My base (WO-3.8), Clayton:** in the dashboard's Base view, set a tier override, tick one machine you own (for example an EBF), and enter some stock of one item (for example 64 steel ingots). Then:
```powershell
& $gos stop; & $gos serve
& $gos plan "steel ingot" --tier MV --json
```
- [ ] The override is still shown after the restart.
- [ ] Steel is listed under `boundary.in` (stock).
- [ ] Clear the stock and the tick afterwards. Approve or mark up the Base view's board (D92, PR #68 Q5).

**2. Designer (WO-3.7):** `docs/runbooks/designer.md` A and B as written (design, pin, reload, re-solve, accept, then the line note in the vault; a text request through the deep lane on the stored key). Approve or mark up the Designer board. Step C (paid, sets `task_max_usd`) is your call on spending.

**3. Design the 14 benchmark lines on the full pack, gos-dev** (about 15–30 min; note any design over 60 s):
```powershell
cd C:\GregtecOS-dev\GretecOS
$h = if ($env:GOS_HOME) { $env:GOS_HOME } else { "$HOME\GregtecOS" }
uv run gos graph --check-chains
$lines = @(
  @('gregtech:gt.metaitem.01@11305','MV'),   # steel (EBF)
  @('fluid:molten.plastic','MV'),            # polyethylene
  @('fluid:sulfuricacid','MV'),
  @('gregtech:gt.metaitem.03@32030','HV'),   # silicon boule
  @('gregtech:gt.metaitem.01@17880','MV'),   # rubber sheet
  @('gregtech:gt.metaitem.01@2085','IV'),    # platline (platinum dust)
  @('gregtech:gt.metaitem.01@2067','LuV'),   # rare earth (neodymium)
  @('fluid:molten.epoxid','EV'),
  @('gregtech:gt.metaitem.01@11306','HV'),   # stainless steel
  @('fluid:nitricacid','HV'),
  @('fluid:molten.polybenzimidazole','IV'),
  @('gregtech:gt.metaitem.01@2324','ZPM'),   # naquadah
  @('fluid:liquid_light_fuel','MV')          # light fuel (G4 chain)
)
foreach ($l in $lines) { uv run gos plan $l[0] --tier $l[1] --qty 1/s --save }
uv run gos plan "nanoprocessor" --tier IV --qty 1/s --save
uv run gos plan "wetware processor" --tier LuV --qty 1/s --save
uv run gos plan "steel ingot" --tier MV --addons --json
uv run gos plan "platinum dust" --tier IV --addons --json
```
- [ ] `--check-chains` exits 0.
- [ ] Every line saves at least one plan. If a name asks a clarifying question, pick the item and re-run with its key. Change the two circuit tiers if you'd build them elsewhere (Q-G3-2).
- [ ] The two `--addons` runs show an addon column with a `machine_fact` confidence (WO-3.5). If one doesn't, try another line you'd build with an addon machine.
- [ ] Paste each plan's id, machine total and the time it took.

**4. Every machine against NEI, full pack, gos-dev** (2 min):
```powershell
uv run python tests/integration/test_g3_lines_harness.py report $h > "$HOME\g3_lab_sheet.tsv"
Select-String -Path "$HOME\g3_lab_sheet.tsv" -Pattern "`tDIFF`t"
```
- [ ] Exit code 0 and no `DIFF` line. Paste any `DIFF` lines as they are.

**5. Line totals against gtnhplanner.com, gos-dev** (10 min; needs the internet; the first time, run `pnpm exec playwright install chromium` in `dashboard`):
```powershell
cd dashboard
pnpm install --frozen-lockfile
node e2e/gtnhplanner-import.cjs (Get-ChildItem "$h\data\plans\*\gtnhplanner.json").FullName > "$HOME\g3_site.jsonl"
cd ..
```
- [ ] Every line has `"problems":[]`.
- [ ] For each best-ranked plan, compare `site_machines` with `file_machines`. List every line outside ±2%. On the dev runtime these were polyethylene, silicon and the platline.

**6. Lab single-machine runs for the lines outside ±2%** (`uv run gos launch lab`, as in `g1_golden_lab.md` step 3):
- Open the line's `gtnhplanner.json` on the site and find the cards whose machine count differs from GOS's `≤` box. Look each one up in the plan's `bom.tsv`: machine, tier, OC, parallel, EU/t, `r:` id.
- For each one, build that machine in the Lab at the BOM's tier, with the hatches and coil the plan names. Run the recipe and time 10 crafts (or read the GT scanner's progress). Compare the time per craft and the EU/t with `bom.tsv`.
- [ ] For each line, write down which reference the Lab agrees with (GOS or the site) and by how much. This is the "report names the reference used" part [QA:D12].

**7. Build two small lines in the Lab** (suggested: steel (EBF) at MV and sulfuric acid at MV, or any two of your choice). Build each from its saved plan's BOM and let it run for 10 minutes.
- [ ] Output per minute is within ±2% of the plan's rate.
- [ ] Every machine runs.
- [ ] No input runs short except the plan's boundary inputs.

**8. WO-3.6 PC checks:** `docs/runbooks/plan_outputs.md` checks 2 (gtnh-flow render of one saved plan), 3 (footprint vs `structure`, after an export with the structure dump) and 4 (the flow card on a real plan; board approval). Check 1 (the gtnhplanner import) is covered by step 5.

**9. Licence text:** compare the gtnh-flow MIT block in `NOTICE.txt` with https://github.com/OrderedSet86/gtnh-flow/blob/master/LICENSE (PR #66).

**Required before Clayton signs G3:**
1. PR #80 (WO-3.7) merged and green, and steps 0 to 8 with their paste-backs.
2. His answers, or "keep as built", to Q-G3-1 to Q-G3-4 and to the Phase 3 questions above (D158: the defaults stand and are reversible). Q-G3-3 should come before the naquadah line is judged.

---

## Risks

1. **The Lab may side with gtnhplanner.com.** GOS sizes more machines on 3 lines. If the Lab agrees with the site, D177's conservative defaults over-build; the fix would be a defaults change, not a math change, since `gtmath` equals GT's classes. (Likely: the deviations are all in the conservative direction.)
2. **Full-pack design times** may make the Designer feel slow (finding 5). (Likely, size unknown.)
3. **Real-model explanations** are untested: `plan_explain` (WO-3.7) runs on fakes only; the validator guards the numbers, with the template as the fallback. (Confirmed)
4. **Addon machines are untested on real data:** the dev runtime has none, so the addon column and its confidence badges are fixture-only until step 3. (Confirmed)

## After the review

- Added: `tests/integration/test_g3_lines_harness.py`. It designs the 13 dev-runtime lines on the server harness DB and checks every recipe of every distinct tuple against the client run's NEI dump. It reuses G1's check from `test_golden_recipes.py`, plus one rule: an NEI stack equal to the recipe's own `special_items` (the Bacterial Vat's culture, the replicator's data orb) is left out of the pairing, because the DB keeps it in `recipe_meta`. Its `report` mode is step 4. CI's mod job runs it with the other integration tests (about 3 min).
- Decision D254 records how the references are read.
