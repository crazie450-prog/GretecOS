# Gregtec OS: G1 gate review (Phase 1)

2026-11-11 · independent gate reviewer (D41; reviewer reports now, sign-offs batched, D158 (3)) · reviewed: `origin/main` at `610780e`, then re-checked after merging `main` at `d60edbf` and `b7b88a0` and applying the gate review's findings (2026-11-12) (Phase 1 PRs #21, #22, #25–#29, #31–#33, #35, #37, #40, #42–#47, #52), on branch `review/g1`

## Verdict

**G1's cloud half is met; G1 is not ready for Clayton's sign-off until the PC steps below are done.** Every Phase 1 acceptance check that can run without the PC passes on today's code, on harness runs made for this review. What is left is the Lab and full-pack half that §8 asks for by name, and one Phase 1 deliverable (WO-1.8's ≥ 250 controllers) that has never run anywhere.

G1's three criteria (§8):

| Criterion | Cloud | PC / Lab | Status |
|---|---|---|---|
| 30 golden recipes checked against NEI in the Lab (100%) | **30 of 30** match NEI's own dump of the same run, on three client harness runs (below) | `docs/runbooks/g1_golden_lab.md`: the full pack, and NEI's pages in the Lab | met in the cloud; the Lab check is a named PC step (G1-A) |
| Stale detection works | real-run test of the whole stale chain passes (new `test_g1_stale_harness.py`), plus WO-1.4's unit acceptance test | `phase1_pc_checks.md` step 10 (V25) | met in the cloud; the Lab and Play check is a named PC step |
| Playbook dry run passes | WO-1.9's simulated GT 5.09.54.266 dry run passes on today's harness run, with the right plans flagged | `pack_update.md` "Clayton's PC steps for WO-1.9" (G1-B) | met in the cloud; the Lab dry run is a named PC step |

The open items before sign-off, none of which needs new design work:

1. **The PC sitting** (`docs/runbooks/phase1_pc_checks.md` steps 1–8 and 10) has not happened yet. It closes V03, V07, V09, V22, V25 and V33 and every "in the Lab" clause of WO-1.1–1.10.
2. **WO-1.8 is not met:** no gtnh-extractor dump has been made, in the cloud or on the PC, so "≥ 250 base controllers" is unshown (V09). PR #32's Question 4 (may a cloud session run the extractor?) has no answer.
3. **Two new PC checks** that `phase1_pc_checks.md` does not have: the golden-recipe Lab check (G1-A) and WO-1.9's Lab dry run (G1-B). They are in the ordered list at the end.
4. **23 Phase 1 PR questions have no recorded answer** (listed below). Only PR #32 Q4 bears on the gate, through WO-1.8. Each needs a verdict or a decision row.

Evidence tags:
- **[run]:** this reviewer ran it in this session, on the code at `review/g1` (= `origin/main` `610780e` plus this branch's test and doc files; no product code changed).
- **[repo]:** checked in the repo, the git history or GitHub's CI and PR records.
- **[PR]:** the implementer's PR text, not re-checked here.
- **[PC]:** nothing Clayton has run for Phase 1 has been relayed yet, so no item carries this tag.

### This reviewer's runs

- **Harness runs** [run], made here from `mod/` at `610780e` (GregtecLink `b073f91`, the branch head with `origin/main` merged; `mod/` is unchanged since `4053b4b`):
  - client harness `./gradlew runClient25 -Pharness` under Xvfb: **52 checks passed**, run `20261111T181015Z`, 2 min 12 s. Its manifest: 172 recipe maps, 99,692 GT recipes, 0 recipe errors, 3,157 machines, NEI 190 handlers with 142,541 lines = `nei_reported`, `nei_serial_load` 1, `nei_lookup_errors` 0, 122 ore mixes, 40,718 oredict names, 11 crop dumpers.
  - a second client run for the two-export checks (copied first to `run/client-b`, as CI does): **52 checks passed**, run `20261111T181723Z`.
  - server harness `./gradlew runServer -Pharness`: **31 checks passed**, run `20261111T181912Z`.
- **DB build** of run `20261111T181015Z` [run]: 143,635 citation keys (GT 6,335, GT+NEI 92,391, NEI only 44,909); `Collision test: passed` (22 allow-listed); NEI-only oredict slots 48,286 keyed by name; base multiblocks 299: covered 132, n/a 107, flagged 60, **missing 0**; crops 131, NEI's Crop Breeding tab 91 recipes with 104 crops, all 129 crops NEI shows are in the dump. The four Clayton-pack addons (TST, GT-Not-Leisure, 123Technology, Programmable Hatches) are listed as `not loaded`, as expected in the dev runtime.
- **Tests** [run]:
  - `uv run ruff check .`: all checks passed; `uv run ruff format --check .`: 284 files already formatted.
  - `uv run pytest`: **4520 passed, 194 skipped** on `610780e`, and **4676 passed, 194 skipped** after merging `main` at `d60edbf` (Linux; the skips are the harness, model and Windows-only tests). `ruff check` and `ruff format --check` are clean on both.
  - `GOS_REQUIRE_HARNESS_RUN=1 GOS_CLIENT_RUN_B=mod/run/client-b uv run pytest tests/integration` (CI's mod-job invocation): see "Integration suite" at the end.
  - `GOS_QUESTS_PACK=<2.9.0's .minecraft> uv run pytest tests/integration/test_quests_pack.py`: **7 passed**, on GTNH 2.9.0's real quest book (only `config/betterquesting/` read out of the release zip by HTTP range requests; CC BY-NC-SA game data, kept outside the repo).
- **CI on this branch** [repo]: run 43079682732 (`d5ec780`). The **mod** job passed: both harnesses ran on CI's runners and `tests/integration` ran with `test_golden_recipes` and `test_g1_stale_harness`, so the fixture's keys resolve in CI's own runs too. `checks (windows-latest)` and `dashboard` passed. `checks (ubuntu-latest)` failed on one test outside Phase 1, `tests/unit/voice/test_tts.py::test_a_new_utterance_stops_the_last` (WO-5.3, merged in #65; it passes locally). The open branch `fix/tts-test-race` addresses that race. Run 43081712006 (`990ecd1`) failed the same way.
- **Re-run after the gate review's fixes** [run], 2026-11-12, branch head merged with `main` at `b7b88a0`: client harness **52 checks passed**, run `20261112T045235Z`; a fresh `db build` of it gives **30 of 30 match NEI (100%)** with the stricter item match, sources 26 `gt+nei`, 1 `gt`, 2 `crafting`, 1 `furnace`, and `select` equal to the fixture. Mutations on a DB copy are all caught: g02's first item input swapped for `minecraft:dirt` ("no line of NEI's gt.recipe.arcfurnace has its stacks"; the old match missed it), g13 EU/t +1 and g30 output amount +1 ("numbers differ"). `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration/test_golden_recipes.py test_g1_stale_harness.py test_pack_update_harness.py`: **11 passed**. `ruff check`, `ruff format --check` (307 files) clean; `pytest`: **4772 passed, 195 skipped**.
- **CI on `main`** [repo]: `CI` on `main` is green at `54d1e6f` (run 43072400497; checks on Ubuntu and Windows, the mod job with both harnesses and `tests/integration`), and `dashboard (Windows)` and `mod (Windows)` are green at `610780e` (runs 43074629777, 43074630203).

---

## G1 criteria

### 1. Thirty golden recipes against NEI

> "30 golden recipes checked against NEI in the Lab (100%)" (§8 G1; §9 "Golden data: 30 recipes")

**Verdict: met in the cloud (30 of 30); the Lab half is PC step G1-A.**

Phase 1 left no golden set (nothing in `tests/` or `docs/` named one), so this review picked one by a written rule and recorded it as **D222** (pending the reviewer verdict, as D158 asks):

- **Rule** (`select` in `tests/integration/test_golden_recipes.py`): from a run's recipes that are enabled, not hidden, not fake, not GT recycling, with an output, and in a map GT shows in NEI or an NEI-only handler, in citation-key order (a content hash, so a fixed shuffle), each pick from a map or handler not used yet. Strata: 9 GT recipes, one per tier ULV–UV, from GregTech single-block maps; 5 from GregTech maps only multiblocks run; 3 with a fluid in and a fluid out; 3 with a chanced output; 3 with an oredict input slot; 4 from addon-owned maps, one per mod (GoodGenerator, TecTech, bartworks, gtnhintergalactic); and NEI's shaped, shapeless and furnace handlers.
- **Fixture:** `tests/fixtures/golden/g1_recipes.tsv`: 30 rows of `r:` refs, NEI handler ids, first-output registry keys and names. No game number. The rule run on today's fresh DB reproduces it byte for byte [run].
- **The check** reads NEI's JSON on its own (not WO-1.3's merge), pairs every DB slot with an NEI stack that shows or lists (`anyOf`) the item the DB shows for that slot, and compares EU/t, duration, every item and fluid amount and every chance. TecTech's condensate fluids and the Beamcrafter's particles, which GT keeps in recipe metadata, count as slots. The slot's `recipe_input_alt` items and oredict members don't count for pairing: WO-1.3's merge fills them from NEI's own `anyOf`, so pairing on them would compare NEI with itself (the gate review's finding 1: with them, a copper-dust input swapped for dirt in a DB copy still passed; now it fails).
- **How independent each check is**, by the recipe's `source` in the DB: 26 are `gt+nei`, g25 is `gt`, g28 and g29 are `crafting`, g30 is `furnace`. **g28–g30 test ingest fidelity only** (the DB's recipe is the NEI line it is compared with); **GT-vs-NEI agreement is shown by g01–g27**. For the 26 `gt+nei` recipes the merge's identity hash already includes the slot amounts, so their cross-source checks are EU/t, duration and chances; g25 is checked on everything, its condensate amounts included.
- **Result** [run]: `golden recipes: 30 of 30 match NEI (100%)` on run `20261111T181015Z` (fresh DB) and on `20261111T145556Z` (an earlier run from today's code). `test_golden_recipes_match_nei_in_a_real_run` passes in the CI-style integration run on `20261111T181723Z`. g12 (Beamcrafter) and g25 (condensate assembler) matched with their `recipe_meta` inputs. No mismatch to report.
- **Re-running it:** the test is in `tests/integration/`, so CI's mod job runs it after every client harness run. `GOS_GOLDEN_HOME=<GOS_HOME>` runs it on any built DB. `python tests/integration/test_golden_recipes.py report|select|show <GOS_HOME> [--reselect]` prints the report, the rule's pick, or the Lab sheet. Seven synthetic unit tests in the same file cover the pairing, a wrong shown item whose alternative overlaps NEI's line (it fails), every kind of difference, wildcard meta and `anyOf`, and the fixture's shape (30 unique keys, strata counts). A second harness test, `test_the_rule_picks_30_recipes_by_stratum_from_distinct_maps`, checks that `select` still gives 30 picks in the strata's order and counts from 30 distinct handlers (not row for row, since a dev-runtime bump may change the picks). On a full-pack DB use the command line with `--reselect`, not pytest: the fixture is the dev runtime's, so pytest fails with "not in" for every key that differs.
- **What the cloud half can't see:** values NEI's dump doesn't carry (amperage, coil heat, cleanroom, special items, additives), NBT (NEI's `tag` holds numeric IDs), and the full pack (the harness is the dev runtime: GT 5.09.54.265 and its dependencies, not Clayton's addons). G1-A covers all three, with `--reselect` for any dev-runtime recipe the full pack lacks.

### 2. Stale detection

> "stale detection works"

**Verdict: met in the cloud; the Lab and Play check is `phase1_pc_checks.md` step 10.**

- `tests/unit/test_playhooks.py::test_acceptance_flow` (WO-1.4's acceptance on fakes) and the rest of that file pass [run].
- New here, `tests/integration/test_g1_stale_harness.py::test_stale_detection_on_a_real_export_run` runs the chain on the harness's real run through the real hooks, with fake Prism instances [run]:
  - no export yet: pre-launch says `recipe data is stale: no export is recorded yet`, the quit asks, and No starts nothing;
  - the export records Play's fingerprint, then pre-launch says `recipe data matches Play (export g1-1 ...` and an unchanged quit asks nothing;
  - Play's GT jar swapped for a "5.09.54.266" jar: `stale: Play changed since export g1-1`, `1 added, 1 removed, 0 changed`; the quit asks once, naming the change; `state.json` has `stale: true`;
  - the next export (Play synced into Export first) clears it and writes `export-diff-g1-1-g1-2.txt` with `Play: 1 added, 1 removed, 0 changed`.
- `tests/integration/test_export_harness.py` (WO-1.4's pipeline on both real runs) passes [run].
- Affected-plan detection is criterion 3.

### 3. Playbook dry run

> "playbook dry run passes"; WO-1.9: "A dry run with a simulated 2.9.x (a changed GT jar in Lab) produces the prompt, runs the whole checklist and flags the right fixture plans"

**Verdict: met in the cloud; the Lab dry run is PC step G1-B (and L10 repeats it before G11).**

`tests/integration/test_pack_update_harness.py::test_a_simulated_gt_update_through_the_whole_playbook` passes on run `20261111T181015Z` [run]. A copy of the run is altered as a GT 5.09.54.266 jar would change it: one EBF recipe removed, one rebalanced (ticks × 4/5), one added; one crop's tier raised; one oredict name added. The manifest is re-hashed, and the changed jar sits in a temp Lab. Then:

- the walk stops at `ADOPT? Adopt GTNH 2.9.1?` with `GT PIN MISMATCH`;
- `diff.txt` has `1 added, 1 removed, 1 changed`, the ticks change, the crop and the oredict name;
- the plan flags name exactly `pl-ebf`, `pl-crop`, `pl-removed` and `pl-rebalanced`, never `pl-same` or `pl-ebf-kept`;
- `adopt yes` is refused until the pin is bumped. Clayton's `update_play` script then runs on the temp Play, Play's export is built, and `plan.affected_run` is set on exactly the four;
- the Lab run never reaches the main DB.

---

## Phase 1 work orders

### WO-1.1: GregtecLink and the export module

> **Acceptance:** "Export run creates every file; Play is untouched; one-click export works (unattended if V22 passes); the harness runs a scripted test"

**Verdict: met in the cloud; the full-pack Export run, Play untouched and V22 are `phase1_pc_checks.md` step 3.**

**Evidence**
- PRs #21 (`3d2d99f`), #22 (`362a22e`, D98), #25 (`5ac0270`, D100: NEI trigger and autoexport), #33 (`296f457`, CI XDG fix), #44 (`3e7d6fd`, D145: full oredict), #52 (`cb664cb`, D188: NEI handlers loaded one by one) [repo].
- **Every file:** the client harness's `files_listed_and_hashed` (208 files match the manifest), `nei_files_listed`, `crop_files_listed`, `ore_mixes_ok` and `oredict_ok` all pass, with `no_numeric_ids` and `nei_no_numeric_ids` across every file [run].
- **Play untouched (harness half):** `writes_only_under_gregteclink` and `nei_step_writes_only_gregteclink_and_dumps` pass, and `decoy_world_untouched` passes [run]. The harness can't stand in for Play itself.
- **Unattended (V22, harness half):** `autoexport_ok`, `autoexport_world_rebuilt`, `server_stopped_before_shutdown` and `world_saved_on_quit` pass. The client quits by itself [run].
- **Scripted harness:** server 31 checks and client 52 checks pass [run]. CI runs both on every push, plus a D188 run with a failing handler [repo].

**Gaps and notes**
- No full-pack Export run exists yet, so the export's full-pack size (D102, D107) and the NEI freeze time (D188) are unmeasured. That is step 3 [PR #45].
- V03 (the NEI 2.8.172 swap on 2.9.0) and V22's Prism command-line launch are PC-only.

### WO-1.2: NEI 2.8.172 in Export; dump collection

> **Acceptance:** "JSONL captured"

**Verdict: met in the cloud; V03 (the swap in Export) is `phase1_pc_checks.md` steps 2–4.**

- PR #26 (merged into #27's branch, reaching `main` through #29 at `3c38b12`; D101, D102) [repo].
- `tests/integration/test_collect_harness.py::test_a_real_harness_run_is_collected_whole` (server and client) passes. In the client run, `nei/` holds 190 files and 142,541 lines, which equals NEI's own count [run].

### WO-1.3: normalizer, schema, keys, collision test, counts report

> **Acceptance:** "Counts per mod; recipe maps > 0 for every addon that loads (missing ones listed); stable `r:<map>/<sig12>` keys; the collision test passes on the full export"

**Verdict: met in the cloud on the dev runtime; the full pack's addon lines and collision test are `phase1_pc_checks.md` step 4a.**

- PR #27 (reaching `main` through #29; D104–D108), with follow-ups #44 (D145) and #52 [repo].
- Counts per mod and the addon section are in the report above. The four pack addons don't load in the dev runtime and are listed `not loaded`; every addon that loads owns maps with recipes (bartworks, GoodGenerator, gtnhlanth, gtnhintergalactic, cropsnh, ...) [run].
- `test_db_harness.py` [run]:
  - stable keys: `test_two_client_exports_give_the_same_keys_for_every_recipe` (on `20261111T181015Z` vs `20261111T181723Z`), `test_two_exports_of_one_pack_give_the_same_gt_keys` (server vs client) and `test_the_same_run_again_is_byte_identical`;
  - `test_a_harness_run_builds_with_unique_keys`, the collision test with the repo's allow-list;
  - `test_no_numeric_item_id_in_any_item_key`.
- The golden fixture's 30 keys, picked from a run made before this session's harness runs, resolve in all three of today's runs. That is a second, independent check that keys are stable.

### WO-1.4: fingerprint, hooks, stale flag, ask-on-quit, lock, diff

> **Acceptance:** "A changed mod jar → stale → prompt on quit → yes → export → diff; after an export, Play's next pre-launch reports **not stale**; Play won't launch while Export holds the lock; an unchanged quit runs nothing"

**Verdict: met in the cloud; the Lab and Play halves (V25) are `phase1_pc_checks.md` step 10.**

- PR #31 (`d651784`; D120–D122) [repo].
- `test_acceptance_flow` (every clause, on fakes), `test_end_to_end_with_the_real_commands` (Linux, a fake Prism and a fake Java), `test_pre_launch_refuses_while_the_lock_is_held` and the rest of `test_playhooks.py` pass [run]. So does G1's real-run chain (criterion 2) [run].
- The Windows parts (the timed message box, the detached export outliving the hook, the PowerShell bootstrap) run only on the PC. PR #31's Question 3 says so [PR].

### WO-1.5: quests, progress, frontier, tier

> **Acceptance:** "Frontier matches the in-game book in `lab-progress`; tier correct for 3 progression states"

**Verdict: met in the cloud on 2.9.0's real book; the in-game comparison is `phase1_pc_checks.md` step 5.**

- PR #29 (`3c38b12`; D112–D114) [repo].
- `test_quests_pack.py`, 7 of 7, on GTNH 2.9.0's own `config/betterquesting/` [run]:
  - `test_the_runbook_states`: the runbook's stone (one quest, *Your First Night*), LV (4 quests, tier LV) and HV (17 quests, 4 in HV, tier HV) states, and the repeatable step with Chef Training repeatable;
  - `test_tier_and_frontier_at_every_stage`.
- Unit tests in `tests/unit/` pass [run]. Comparing with the book in the game is the acceptance's "in `lab-progress`" clause.

### WO-1.6: ore mixes and VisualProspecting

> **Acceptance:** "Veins listed for 2 dimensions in Lab"

**Verdict: met in the cloud on the dev runtime; the Lab check (V07) is `phase1_pc_checks.md` step 6.**

- PR #35 (reaching `main` through #37 at `44a4417`; D126–D129), follow-up #42 (`9a48ee4`, ore ranking) [repo].
- `test_ores_harness.py` passes on both runs [run]:
  - `test_the_registrys_mixes_load_and_find_veins`;
  - `test_every_nei_vein_page_is_a_registry_layer` (V08).
  The client harness has 122 ore layers, all keyed, and 4 dimensions named [run].
- VisualProspecting's cache reader is tested on a self-written v3 file that PR #35 matched byte for byte to a real cache [PR]. No Lab world with veins has been read yet.

### WO-1.7: machine facts

> **Acceptance:** "All base multis have parallels/OC rules at confidence ≥ 0.8, or are flagged"

**Verdict: met in the cloud on GT's jar; the full-pack report line is `phase1_pc_checks.md` step 4a.**

- PR #28 (reaching `main` through #29; D109–D111), follow-up #43 (`4a2f3a9`, D144) [repo].
- Today's build: `Base multiblocks (GT's jar): 299: covered 132, ... flagged 60, missing 0`, with no "neither the facts nor a flag" warning [run]. `test_db_harness.py::test_every_base_multiblock_has_parallels_and_oc_rules_or_a_flag` passes [run].
- D142 (a) lets flagged machines into automatic routes, with a badge. That is WO-3.x's to implement, not Phase 1's.

### WO-1.8: structures from gtnh-extractor

> **Acceptance:** "≥ 250 base controllers with dimensions and cells"

**Verdict: not met.** The loader, reader and extractor driver are merged and unit-tested, but no extractor dump exists, so the count has never been shown (V09).

- PR #32 (`4dd049b`; D123–D125) [repo]. Unit tests pass [run]. `tests/integration/test_structures_dump.py` skips without `GOS_STRUCTURES_DATASET` [run].
- **Missing:** `phase1_pc_checks.md` step 8: gos-dev runs `gos structures extract` (several GB, up to 4 h), then the dump test, then Clayton runs `gos structures load`. The alternative, PR #32's Question 4 (let a cloud session run the extractor's Gradle build), is unanswered. This review didn't run it, because the question is open.

### WO-1.9: pack-update playbook

> **Acceptance:** "A dry run with a simulated 2.9.x (a changed GT jar in Lab) produces the prompt, runs the whole checklist and flags the right fixture plans; L10 repeats it with real plans before G11"

**Verdict: met in the cloud (criterion 3); the Lab dry run with a real GT jar is PC step G1-B; L10 is before G11.**

- PR #47 (`9db798b`; D146–D149) [repo]. `tests/unit/test_pack_update.py` and the harness test pass [run].
- The release watcher reads DreamAssemblerXXL's manifests live. The github.com release feeds were tested with a fake: the PR's container got 403 [PR]. V18 stays with WO-11.1.

### WO-1.10: crop data

> **Acceptance:** "Every crop in NEI's Crop Breeding tab is in the DB; 10 spot checks against NEI in the Lab; V33 resolved"

**Verdict: the first clause is met in the cloud. The spot checks and V33's full-pack half are `phase1_pc_checks.md` steps 4a and 7.**

- PR #37 (`44a4417`; D134–D137) [repo].
- Today's build: `NEI's CropsNH tabs: Crop Breeding 91 recipes with 104 crops, 129 crop pages, 60 mutation pool pages (60 match a dumped pool); all 129 crops they show are in the crop dump` [run]. The client harness checks `crop_nei_tabs_covered` and `crop_registry_lists_crops` pass [run]. So does `test_crops_harness.py`, including `test_two_client_exports_give_the_same_crop_data` [run].

---

## VERIFY items Phase 1 touched

From `core/gregtec/verify.tsv` (= §13.2), with today's status:

| ID | Item | Status | Closes at |
|---|---|---|---|
| V03 | NEI 2.8.172 swap with 2.9.0 in Export | open | `phase1_pc_checks.md` steps 2–3 |
| V07 | VisualProspecting cache paths and format | partly resolved (D127); Lab check open | step 6 |
| V08 | ore-mix source | resolved (D126); NEI's vein pages all match the registry today [run] | — |
| V09 | gtnh-extractor re-pinned to 2.9.0 builds and runs | open; never run | step 8 |
| V18 | addon releases on 2.9.0 stable | wording only (WO-1.9); stays with WO-11.1 | — |
| V22 | unattended export, Prism CLI launch | harness half shown [run]; Prism half open | step 3 |
| V25 | a failing pre-launch command stops the launch | resolved from Prism's source (D121); Lab confirmation open | step 10 |
| V33 | CropsNH dumps cover NEI's crops | partly resolved (D134); dev runtime shown again today [run]; full pack open | steps 4a, 7 |

This review changes no VERIFY row: nothing it ran is a PC result.

---

## Open questions from Phase 1's PRs

Answered: #21 and #25 by D98 and D100; #22 in its own text; #26–#32 and #35 by D142, except the items below; #33 and #40 had none. PR text is quoted as data.

| PR | Question | State |
|---|---|---|
| [#27](https://github.com/crazie450-prog/GretecOS/pull/27) Q3 | Confirm leaving five non-recipe NEI handlers out (the full pack may have more whose output changes between runs) | built as D105 records; no answer recorded |
| [#31](https://github.com/crazie450-prog/GretecOS/pull/31) Q3 | Windows bootstrap, timed message box and the detached export are untested in CI | a note, not a question; covered by step 10 |
| [#32](https://github.com/crazie450-prog/GretecOS/pull/32) Q4 | May a cloud session run the extractor's Gradle build to close V09 sooner? | **open; blocks WO-1.8 without the PC** |
| [#37](https://github.com/crazie450-prog/GretecOS/pull/37) | A dumper file cut short in the wrong place could pass the line check (D134). Stricter check? | open |
| [#42](https://github.com/crazie450-prog/GretecOS/pull/42) Q1 | Should `ore_find` list veins of an unknown mix at the end of every search? | open |
| [#42](https://github.com/crazie450-prog/GretecOS/pull/42) Q2 | `catalog.LATER` still credits `ore_find` to WO-1.6 | open (doc drift) |
| [#43](https://github.com/crazie450-prog/GretecOS/pull/43) Q1 | Should a `partial` machine (the EIG) get the flagged badge? D144 assumes yes | open |
| [#43](https://github.com/crazie450-prog/GretecOS/pull/43) Q2 | IOF chemical-bath fluid as a named planner variable? | open |
| [#44](https://github.com/crazie450-prog/GretecOS/pull/44) Q1 | Should GT's own slots and Crop Breeder slots also take `ore:` keys (one-time key change)? | open |
| [#44](https://github.com/crazie450-prog/GretecOS/pull/44) Q2 | Two oredict names with identical members: alphabetical first, or a curated preferred-name list? | open |
| [#44](https://github.com/crazie450-prog/GretecOS/pull/44) Q3 | Refuse building a run whose oredict part failed, or build with the previous run's keys? | open |
| [#44](https://github.com/crazie450-prog/GretecOS/pull/44) Q4 | A task for the intermittent NEI handler failure | settled by #52 (D188) |
| [#45](https://github.com/crazie450-prog/GretecOS/pull/45) Q1–Q3 | Merge #41–#44 first; sign-out last; the new step 4b | #41–#44 are merged; Q2 and Q3 open |
| [#47](https://github.com/crazie450-prog/GretecOS/pull/47) Q1 | Flag draft plans too (§7.13 says accepted)? Built: every plan | open |
| [#47](https://github.com/crazie450-prog/GretecOS/pull/47) Q2 | Should GOS schedule `gos pack watch` itself? | open |
| [#47](https://github.com/crazie450-prog/GretecOS/pull/47) Q3 | Is a Play world backup from the last 24 h the right window? | open |
| [#47](https://github.com/crazie450-prog/GretecOS/pull/47) Q4 | A Lab export leaves Export holding Lab's mods until the next export | open |
| [#47](https://github.com/crazie450-prog/GretecOS/pull/47) Q5 | `update_play.py` mirrors Lab's whole `config/` into Play (backed up first) | open |
| [#47](https://github.com/crazie450-prog/GretecOS/pull/47) Q6 | Pin bump before `adopt yes` (built) or after (§7.13)? | open |
| [#47](https://github.com/crazie450-prog/GretecOS/pull/47) Q7 | Should rollback of an adopted update restore the previous adopted record? | open |
| [#52](https://github.com/crazie450-prog/GretecOS/pull/52) Q1 | Draft an upstream GT5-Unofficial issue about the race? (nothing posted without OK) | open |
| [#52](https://github.com/crazie450-prog/GretecOS/pull/52) Q2 | NEI's fuel search still runs in parallel during the dump (GOS doesn't read fuels) | open |
| [#52](https://github.com/crazie450-prog/GretecOS/pull/52) Q3, Q4 | Serial handler load instead of the narrower fix; `nei_serial_load` 0 stays `ok` with a warning | built as D188; merge-gate questions, no answer recorded |

New from this review: **D222** (the golden-set rule, the check and the Lab half), pending the reviewer verdict.

---

## Clayton's PC steps before signing G1

In order. Steps that are already in [`phase1_pc_checks.md`](../runbooks/phase1_pc_checks.md) are named, not repeated. **G1-A** and **G1-B** are new.

1. `phase1_pc_checks.md` **steps 1–3**: deploy and sync, the NEI swap and autoexport flag, and one Export run with Play-untouched check (2). Closes V03 and V22, and WO-1.1.
2. `phase1_pc_checks.md` **step 4a** (gos-dev collect and build), then **4b**. Closes WO-1.2, the full-pack halves of WO-1.3 and WO-1.7, and V33's coverage line.
3. **G1-A, the golden recipes in the Lab** (gos-dev plus the Lab, about 50 min, after step 4a): [`g1_golden_lab.md`](../runbooks/g1_golden_lab.md) §1–3.
   ```powershell
   cd C:\GregtecOS-dev\GretecOS
   $h = if ($env:GOS_HOME) { $env:GOS_HOME } else { "$HOME\GregtecOS" }
   uv run python tests/integration/test_golden_recipes.py report $h
   uv run python tests/integration/test_golden_recipes.py show $h > "$HOME\g1_golden.txt"
   ```
   Use `--reselect` on both if `report` says `not in the DB's run`. Then compare all 30 with NEI's pages in the Lab.
4. `phase1_pc_checks.md` **step 5** (quests), **step 6** (ores, V07) and **step 7** (crops, V33): one Lab session can cover all three, plus G1-A.
5. `phase1_pc_checks.md` **step 8** (structures, V09; 8a runs unattended from the end of step 3). This is the only way WO-1.8 closes unless PR #32 Q4 is answered yes.
6. `phase1_pc_checks.md` **step 10** (Play's hooks, V25; WO-1.4 and G1's stale detection). Its last export leaves Play's export recorded and in your DB, which G1-B needs.
7. **G1-B, WO-1.9's Lab dry run** (Clayton, about 40 min, after step 10): [`pack_update.md` "Clayton's PC steps for WO-1.9"](../runbooks/pack_update.md#claytons-pc-steps-for-wo-19) steps 1–4. In order: `gos pack watch --force` and `gos pack stock`; `gos export collect` and `gos db build` if step 10's export isn't built yet; then the fixture plans; Lab's GT jar swapped plus `gos_dryrun.zs`; `gos pack update --start 2.9.1-sim --simulate --fixture-plans $fx`; `--confirm backup`; `gos pack adopt yes`; `update_play.py` dry run only. Last, put Lab back.
8. Paste back: the items in `phase1_pc_checks.md` "What to paste back" 1–8 and 10, G1-A's paste-back, and G1-B's `gos pack update` output with `diff.txt`. Then answer the open questions above (at least PR #32 Q4) and sign off G1.

Not needed for G1: `phase1_pc_checks.md` steps 9, 11 and 13 (Phase 2: WO-2.1, WO-2.5) and step 12 (WO-0.8's backup job, due before G11, D97).

---

## Notes for the PM

- `phase1_pc_checks.md` "Before you start" still treats #41–#44 as open. All four are merged, so the `[#41]` and `[#44]` lines simply apply. It also has no step for G1-A or G1-B; this review lists them instead of editing the runbook.
- §9's "Golden data: 30 recipes" now has a home: `tests/fixtures/golden/g1_recipes.tsv` (D222). G3 and G4's 18 benchmark lines are still to come.
- No Phase 1 product defect was found, so no product code changed in this review.

## Integration suite

`GOS_REQUIRE_HARNESS_RUN=1 GOS_CLIENT_RUN_B=mod/run/client-b uv run pytest -rs tests/integration` on today's three harness runs [run]: **95 passed, 18 skipped, 10 errors in 20 min**. All ten errors were `sqlite3.OperationalError: database or disk is full` at fixture setup in `test_tools_harness.py`, `test_vault_harness.py` and `test_voice_harness.py`: the container's disk, shared with two other agents, filled up. After freeing space, those three files gave **9 passed, 1 skipped** [run]. Every Phase 1 file passed in the full run: `test_collect_harness`, `test_crops_harness`, `test_db_harness` (13, the two-client checks on `20261111T181015Z` vs `20261111T181723Z`), `test_export_harness`, `test_g1_stale_harness`, `test_golden_recipes` (7), `test_ores_harness`, `test_pack_update_harness` and `test_oc_names_harness`.

The skips are only tests that need data this container doesn't have: the quest book (run on its own above, 7 passed), a structures dump (WO-1.8, V09), and the speech models (Phase 5).
