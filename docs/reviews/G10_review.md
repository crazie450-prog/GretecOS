# Gregtec OS: G10 gate review (Phase 10, building design)

2026-11-18 · independent gate reviewer (D41, D158 (2)) · reviewed: `origin/wo/10.8-build-guide` at `76dd152` (PR #124, WO-10.8), which contains `origin/main` at `6aa5d6f` (PR #121 merged). WO-10.8 had passed its merge gate but was not on `main` when this review started, so this branch (`review/g10`) is based on `76dd152`; see "After the review" for its merge commit.

Phase 10 PRs and where they landed on `main`:

| WO | PR | Head reviewed by its PR | On `main` as |
|---|---|---|---|
| 10.0 | none | not run | the img2schem SOW is not in the repo (D158 (6)); Phase 10 was built from plan 7.10 as written |
| 10.1 | #110 | `dc0fe37` | `8a07a60` |
| 10.2 | #116 | `769b31d` | inside #119 (`7f0b4a1`, merged into `wo/9.3` as `a0e9fcc`) |
| 10.3 | #104 | `68296d4` | `d896ee1` |
| 10.4 | #109 | `f0268ac` | `705c88a` |
| 10.5 | #120 | `4333042` | inside #121 (`6aa5d6f`) |
| 10.6 | #118 | `1480d51` | `ee7449c` |
| 10.7 | #121 | `a8250c3` | `6aa5d6f` |
| 10.8 | #124 | `76dd152` | not yet merged at writing (see "After the review") |

## Verdict

**G10 is ready for Clayton's sign-off at the deployment test, with fakes standing in for his Lab, his voice, the real model and his GuideNH export, and with one defect to fix first (defect 1: a Lab capture saved as a `.schematic` can't become a footprint).** All eight built work orders have their code merged or merge-ready, every acceptance criterion that can be checked without Clayton's Lab, his catalog and his voice is met here or on CI, and nothing is "not met" in code except defect 1, which is inside WO-10.2's acceptance. Every §8 acceptance in Phase 10 is a Lab or voice check, so each WO is **met with fakes** and the PC steps below are the acceptance.

What the PC steps must show (D158 (2) batches them with the other gates):
1. **WO-10.3:** the full pack's block catalog holds addon blocks, and tile-entity blocks are flagged (`palette_export.md`).
2. **WO-10.4:** paste → `//copy` → identical in the Lab, with ids above 4095; what meta above 15 does (V16, `schematic_roundtrip.md`).
3. **WO-10.2:** `gos footprints coverage` shows a footprint for every multi in his accepted plans; any `needs capture` multi is captured in the Lab (after defect 1 is fixed).
4. **WO-10.6:** he creates and refines a theme by voice (`themes.md` B).
5. **WO-10.7 and 10.8 (L6):** a Claude-designed shell pasted in the Lab with no tile entities and clear keep-outs and corridors (`shells_lab.md`), and one multi built from the guide alone whose controller forms (`build_guide_lab.md`).
6. **WO-10.1 (V15):** his GuideNH export renders in the Build view's frame, from its own origin.

Evidence tags, as in G0 to G9:
- **[repo]:** checked in the repo, the git history or GitHub's CI logs.
- **[here]:** run by this reviewer in the build container (Linux, 4 shared CPUs, Xvfb and Mesa, no Minecraft client of his, no voice, no model key).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC. Nothing from the PC is in the evidence yet for Phase 10.

### This reviewer's runs [here]

All on this branch (`76dd152` plus this report).

| Check | Result |
|---|---|
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 644 files already formatted |
| Phase 10 unit tests (`test_build_footprints`, `test_build_guide`, `test_build_guide_data`, `test_build_layout`, `test_palette`, `test_schematic`, `test_shell`, `test_themes`, `voice/test_theme_by_voice`, `test_verify_list`) | **217 passed** (36 s) |
| Phase 10 tool, router and security tests (`tools/test_router`, `tools/test_shell_architect`, `tools/test_tool_registry`, `test_vault_io`, `security/test_core_api`) | **258 passed, 1 skipped** |
| full `pytest -n 2` (default groups, as CI) | **11039 passed, 346 skipped**, exit 0 (4 min 38 s; the Phase 10 integration files ran in it on the harness runs below) |
| `./gradlew --no-daemon --max-workers=2 runServer -Pharness` (fresh, in this worktree) | `harness passed: 37 checks, run 20261118T220913Z` (1 min 32 s) |
| `./gradlew ... runServer -Pharness -PharnessSchematic` | `harness passed: 7 checks`: `pasted_at_offset_shell.schematic: 48 solid cells at player (64, 100, 0) + WEOffset (-2, 0, 1)`, `nothing_outside_*: 0 non-air cells around the box`, both copies written (1 min 4 s) |
| `xvfb-run ./gradlew ... runClient25 -Pharness` | `harness passed: 63 checks, run 20261118T221117Z` (1 min 13 s), the palette checks among them |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest` on the eight Phase 10 integration files over those runs (`test_build_guide_harness`, `test_footprints_harness`, `test_layout_harness`, `test_palette_harness`, `test_schematic_db`, `test_schematic_harness`, `test_shell_harness`, `test_themes_harness`) | **20 passed, 0 skipped** (3 min 48 s). The layout test laid out G3's steel line (71 machines; on the harness DB its designer uses Alloy Blast Smelters, mte:810), G4's benzene line (1 Large Gas Turbine, mte:15527) and both together (72 machines, every front and hatch kind reached, one transformer warning `eu:MV ... fed from EV generator(s) m72`). The shell test put both starter themes around each: e.g. `th-foundry around pl-4bd0aeb0be: 3904 blocks, 65 x 41 x 9, 1 file(s)`, every rule held, no tile-entity state, written and read back identical. Both multis use the fixture's stand-in 3x3x3 shape (no real extractor dump here; risk 2) |
| `pnpm test` (vitest) | 23 files, **375 passed** |
| `pnpm run build` | ok |
| Playwright `build_guide.spec.ts` and `designer.spec.ts` (2560x1440 and 390x844) | the whole suite on spare ports and homes: **78 passed** (2.1 min), `build_guide.spec.ts` (the guide, the frame on its own origin, the planted cookie) and `designer.spec.ts` (Floor plan, shell button) among them |
| CLI evals in a scratch `GOS_HOME` built from the client run (`collect` + `db build`) | `gos palette blocks casing --mod gregtech`: `gregtech:gt.blockcasings@0 ULV Machine Casing ... te 0`; `gos palette blocks chest --mod minecraft`: `minecraft:chest@0 ... te 1`, `minecraft:ender_chest@0 ... te 1`. `gos theme list`: `th-foundry v1 shipped`, `th-marble-works v1 shipped`; `gos theme check`: two `ok`, exit 0; `gos theme select --line-type chemistry`: `mode: blank`, `th-marble-works (fits chemistry)`. `gos schematic read` on the harness's `//copy`: `5x3x4, written by worldedit, AddBlocks low-first, WEOffset (-2, 0, 1)`; `gos schematic compare` of GOS's file and that copy: `identical`, exit 0. `gos footprints capture` on that `.schematic`: `Not a capture: not a JSON capture ...`, exit 1 (defect 1) |

No DB, export, schematic, token or keyring entry from these runs is committed. They stay in the session's scratch folder and the worktree's ignored `mod/run/`.

### CI [repo]

| Commit | Run | ubuntu | windows | mod | dashboard | dashboard (Windows) |
|---|---|---|---|---|---|---|
| PR #104 head `68296d4` (10.3) | 43582938456 | success | success | success | success | (path filter) |
| `d896ee1` (#104 merged) | 43588012389 | **failure** (WO-3.7's designer flake, G9 risk 1) | | | | |
| PR #110 head `dc0fe37` (10.1) | 43604913306 | success | success | success | success | success |
| `8a07a60` (#110 merged) | 44215802439, 44215802456 | success | success | success | success | success |
| PR #116 head `769b31d` (10.2) | 44320677908 | cancelled | | | | |
| PR #119 head `a0e9fcc` (9.3 with 10.2) | 44323082821, 44323082842 | success | success | success | success | success |
| PR #109 head `f0268ac` (10.4) | 44328388622 | success | success | success | success | success |
| `705c88a` (#109 merged) | 44342011407 | success | success | success | success | n/a |
| PR #118 head `1480d51` (10.6) | 44330621699 | success | success | success | success | n/a |
| `ee7449c` (#118 merged) | 44344126201 | success | success | success | success | n/a |
| PR #120 head `4333042` (10.5) | 44353103011, 44353102883 | success | success | success | success | success |
| PR #121 head `a8250c3` (10.7) | 44400693722, 44400693740 | success | success | success | success | success |
| `6aa5d6f` (#121 merged, `main`'s head) | 44405075345, 44405075353 | success | **failure** (risk 1) | success | success | success |
| PR #124 head `76dd152` (10.8) | 44402344686, 44402344764 | success | success | success | success | success |

`769b31d`'s run was cancelled by a newer push; WO-10.2's code ran green inside `a0e9fcc`, which carries it. Every Phase 10 harness test ran in the `mod` jobs above with `GOS_REQUIRE_HARNESS_RUN=1`.

`6aa5d6f`'s Windows failure is not a Phase 10 test: `tests/unit/voice/test_core_client.py::test_ask_errors_say_whether_the_core_answered` (WO-5.1) got `[WinError 10053] An established connection was aborted` where it expected a 401/403 refusal (1 failed, 11039 passed). Neither file has changed since WO-5.1's review (`f7af765`), and the same test passed on Windows at `a8250c3` and `76dd152`. It is a Windows timing flake (risk 1).

---

## Phase 10 work orders

### WO-10.0: Re-scope from the img2schem SOW

> **Acceptance:** "Updated Phase 10 WOs and hours approved by Clayton"

**Verdict: not run.** `docs/img2schem_sow.md` is not in the repo, so there was nothing to re-scope from. Under D158 (6) every Phase 10 WO was built from plan 7.10 as written, and each PR lists what the SOW might change (questions 44 to 50 below). V17 ("img2schem merged, D74") stays resolved; the re-scope itself waits for Clayton to add the SOW. Nothing in G10 depends on it: plan R14's fallback, the procedural shell, is built.

### WO-10.1: GuideNH spike

> **Acceptance:** "Decision recorded (≤ 2 h)"

**Verdict: met.** PR #110, merge `8a07a60`. Decision D304 (pending Clayton's Lab export). Report `docs/reviews/WO-10.1_guide_spike.md` (time box 2 h stated in its header).

| Part | Status | Evidence |
|---|---|---|
| **Decision recorded** | **met** | D304: GuideNH 1.3.55's `/guidenhc exportsite`, framed, never embedded or vendored (LGPL-3.0); MrBruh's previewer (Apache-2.0) the fallback [repo] |
| The export served and framed under `/guide/` | met with fakes; **PC step** | `core/gregtec/build/guide.py`, `api/guide_routes.py`; the stand-in export `tests/fixtures/guide_site/` (no GuideNH code); `build_guide.spec.ts` [here]. The real export: step 8 below |
| Trust boundary | met (by WO-10.8) | D304 required `/guide/` on its own origin; WO-10.8 did it (D319, below) |
| Path guard | met | percent-encoded `..` forms in `tests/unit/test_build_guide.py` [here] |

### WO-10.2: Footprint library

> **Acceptance:** "Footprints exist for every multi Clayton plans to build"

**Verdict: met with fakes for the base pack; not met for addon multis until defect 1 is fixed.** PR #116, on `main` through #119. Decisions D305, D306. Runbook `footprints_lab.md`.

| Part (plan §8 row 10.2, §7.10) | Status | Evidence |
|---|---|---|
| Base footprints from the extractor; one per (controller, tier channels, facing); rotation in code | met | `gregtec.build.footprints` (from the `structure` table, no files); `test_build_footprints.py` (44), `test_footprints_harness.py::test_a_footprint_for_every_structure_row` [here]. With the real `gos structures extract` dump at the GT pin: 297 controllers, 455 footprints, 13 of 13 benchmark multis [PR, from the PR's review] |
| Legacy and new 2.9 variants kept apart | met | `lineage`/`family`; the coverage report marks `legacy` [here] |
| Hatch-slot markers | met | each footprint's hatch cells and kinds; used by WO-10.5's reach check and WO-10.8's markers [here] |
| Coverage report (`footprint` / `capture` / `needs capture`) | met with fakes; **PC step** | `test_the_coverage_report_on_the_benchmark_plans` [here]; with the real dump, 296 of 300 catalog multis have a footprint, 4 need a capture [PR]. On his plans: step 3 |
| **Addon multis captured in the Lab** | **not met (defect 1)** | `gos footprints capture` reads only GOS's own `gos-capture` JSON, and nothing writes that format. D306 handed the `.schematic` path to WO-10.4 ("WO-10.4's reader must expose each tile entity's `mID` by position"), but WO-10.4's `schematic.read` keeps only the tile entities' positions (`Capture.tile_entities`), and no command passes a `.schematic` to `capture.from_cells`. Run here: `gos footprints capture <a worldedit //copy .schematic>` → `Not a capture: not a JSON capture`, exit 1. `capture.py`'s docstring and `footprints_lab.md` C.3-C.4 and D still say "until WO-10.4". So a `needs capture` multi can't get a footprint, and with it no guide and no place in a layout (it goes to `unplaced`) |

Open: PR #116 questions (below), defect 1.

### WO-10.3: Palette and texture export, block catalog

> **Acceptance:** "Catalog includes addon blocks; TE blocks flagged"

**Verdict: met with fakes (the dev runtime's catalog); the full pack's catalog is a PC step (`palette_export.md`).** PR #104, merge `d896ee1`. Decisions D307, D308.

| Part | Status | Evidence |
|---|---|---|
| Client pass: registry, meta, names, icon/texture refs, `hasTileEntity` per meta, no numeric ids | met | `PalettePass.java`; the client harness's palette checks (`palette_sorted_once_no_numeric_ids`, `palette_tile_entities_flagged`, `palette_casings_coils_glass_plain`, `textures_cover_faces_and_resolve`) in the 63 checks [here] |
| **Catalog includes addon blocks** | **met with fakes; PC step** | `test_palette_harness.py::test_the_catalog_has_addon_blocks_and_flags_tile_entities` [here]; dev runtime: 871 blocks, 4,362 states from 21 mods [PR]. Full pack: step 1 |
| **TE blocks flagged** | **met with fakes; PC step** | the same test; `gos palette blocks` here: casings `te 0`, `minecraft:chest@0` and `ender_chest@0` `te 1` [here]. Full pack: step 1 |
| Palette rule (`gos palette check`, against the Lab world's `level.dat`) | met with fakes; **PC step** | `test_the_palette_rule_on_the_harness_world` [here]; `palette_export.md` C |
| `block_find` (deep lane only) | met | `test_block_find_on_the_harness_db`, `test_tool_registry.py` [here] |

Open: PR #104 Q3 (no doors), Q4 (orientation metas), Q7 (meta > 15).

### WO-10.4: Schematic I/O

> **Acceptance:** "Paste → `//copy` → identical blocks, including IDs > 4095; meta > 15 behaviour documented (worldedit-gtnh #30)"

**Verdict: met with fakes for ids below 4096; ids above 4095 and meta above 15 are PC steps (V16, `schematic_roundtrip.md`).** PR #109, merge `705c88a`. Decisions D309-D311.

| Part | Status | Evidence |
|---|---|---|
| Writer exactly per plan 7.10 (mappings, no `AddBlocks`, `AddData` only above 255, empty `TileEntities`, `WEOffset`) | met | `gregtec.build.schematic`; golden-bytes test in `test_schematic.py` [here] |
| `write_shell` is the only shell path and applies WO-10.3's rule | met | `test_schematic_db.py` on the real catalog: a refused state refuses the whole shell [here] |
| **Paste → `//copy` → identical** | **met with fakes (ids < 4096, meta ≤ 15); PC step** | the schematic harness above (worldedit-gtnh v0.0.10 pastes, copies back, nothing outside the box) and `test_schematic_harness.py::test_copy_holds_the_pasted_blocks` [here]; `gos schematic compare` → `identical` [here] |
| **Including ids > 4095** | **met with fakes; PC step** | the dev runtime has no EndlessIDs (D310); `AddBlocks`/`AddBlocks2` read in both nibble orders in `test_schematic.py` [here]. In game: step 2 |
| **Meta > 15 documented** | **PC step** | refused in shells until #30 is resolved (D309 (4)); `schematic_roundtrip.md` D asks for the in-game result |
| Reader for captures | met; **see defect 1** | worldedit-gtnh and Schematica saves, nibble order detected or refused (D311) [here]. It does not read the tile entities' `mID`, which WO-10.2's captures need |
| Never writes into Play or the Lab | met | `--out` refused inside an instance (`doctor.touch_problem`), tested [here] |

Open: PR #109 Q7 (EndlessIDs in the dev runtime), Q9 (the mod job's 90-min limit).

### WO-10.5: Line layout planner

> **Acceptance:** "Two benchmark lines laid out; no overlaps; corridors reach every machine"

**Verdict: met with fakes. Both lines are laid out on the harness DB, with the fixture's stand-in multiblock size; the real sizes are a PC step.** PR #120, on `main` through #121. Decisions D312, D313 (the panel needs his board).

| Part | Status | Evidence |
|---|---|---|
| **Two benchmark lines laid out** | **met with fakes; PC step** | `test_layout_harness.py::test_two_benchmark_lines_are_laid_out` (G3 steel, G4 benzene, and both together) [here]. Stand-in shape: risk 2; real sizes: step 3 |
| **No overlaps** | met | the test's own overlap and bounds check from the JSON; `test_build_layout.py` (30, with a hypothesis test over random plans) [here] |
| **Corridors reach every machine** | met | flood fill from the entrance to every front and hatch kind (`reached` per machine in the run above) [here] |
| Multis with no footprint listed, never sized by guess | met | `unplaced`; the test asserts it empty on the benchmark lines [here] |
| Layout JSON (`gos-layout` v1), `gos layout`, `GET /api/plans/{id}/layout` | met | `layout_schema.json`; the unit tests [here] |
| Floor plan panel | met with fakes; **board approval** | `designer.spec.ts` [here]; D313 |

Open: PR #120 Q1-Q3.

### WO-10.6: Theme engine

> **Acceptance:** "Clayton creates and refines a theme by voice"

**Verdict: met with fakes. The voice path runs end to end with a fake model; his voice is a PC step (`themes.md` B).** PR #118, merge `ee7449c`. Decisions D314, D315.

| Part | Status | Evidence |
|---|---|---|
| **Create and refine by voice** | **met with fakes; PC step** | `tests/unit/voice/test_theme_by_voice.py`: the voice client's turn → router → deep lane (the real Claude Code CLI against `FakeMessagesAPI`) → `theme_save` → spoken result; create, refine twice, a chest refused with nothing written [here]. Real: step 6 |
| All 3 selection modes | met | `test_themes.py`; `gos theme select` here (`mode: blank`, `fits chemistry`) [here]. "Used nearby" is fed since WO-10.7 calls `record_use` (`shell_store.py`) [repo] |
| 2 starter themes | met with fakes; **PC step** | `th-foundry`, `th-marble-works`; `test_themes_harness.py` and `gos theme check` (two `ok`) on the real client catalog [here]. On his catalog: `themes.md` A |
| Writes only through `VaultIO`'s `theme` op | met | `test_vault_io.py` [here] |

Open: PR #118 Q5, Q7, Q8 (Clayton's taste).

### WO-10.7: Shell designer

> **Acceptance:** "A Claude-designed shell is pasted in the Lab with no tile entities, and respects keep-outs and corridors"

**Verdict: met with fakes. Every design passes one gate, checked by property tests and on the harness DB; the Lab paste of a real model's shell is a PC step (L6).** PR #121, merge `6aa5d6f`. Decisions D316-D318.

| Part | Status | Evidence |
|---|---|---|
| Architect designs in a building vocabulary; code makes the blocks; no code execution | met with fakes | `tests/unit/tools/test_shell_architect.py` (real CLI, fake model): good design saved, chest refused, keep-out breach repaired, too many blocks refused [here] |
| **No tile entities** | **met with fakes; PC step** | the gate refuses any state outside the palette; `test_shell_harness.py::test_real_tile_entity_blocks_are_refused` (`gt.blockmachines`, the chest) on the real catalog [here]. In game: step 7 (`//count gregtech:gt.blockmachines` → 0) |
| **Respects keep-outs and corridors** | **met with fakes; PC step** | `test_shell.py` property tests (`test_the_procedural_shell_always_holds_every_rule`, `test_whatever_the_validator_lets_through_holds_every_rule`, `test_the_interior_below_the_roof_is_never_the_shells`); both themes around both benchmark layouts on the harness DB, every rule held [here]. In game: step 7 |
| **Pasted in the Lab** | **PC step** | the schematic harness pastes a GOS-written test shell (WO-10.4), not a designed one; the designed shell's file goes through the same `write_shell` and reads back identical (`test_shell_harness.py`) [here]. The paste: step 7 |
| Procedural fallback (No-AI path, baseline) | met | the harness run above [here] |
| Export refused when the layout changed | met | `shell_store.check_layout`; `test_make_saves_a_build_and_export_rechecks_it` [here] |
| Shells panel, Designer button | met with fakes; **board approval** | vitest; `designer.spec.ts` [here] |

Open: PR #121's questions.

### WO-10.8: 3D build guide UI

> **Acceptance:** "Clayton builds one multi in the Lab from the guide alone"

**Verdict: met with fakes. Every viewer feature in plan 7.10 is built and tested; the build itself is a PC step (L6).** PR #124, branch head `76dd152`. Decisions D319, D320.

| Part (plan 7.10 "Viewer features") | Status | Evidence |
|---|---|---|
| Rotate, zoom, hover (name + registry id), layer slider, materials per layer, checklist, hatch-slot markers, BOM | met with fakes; **board approval** | `BuildGuide.tsx`, `guideMesh.ts` (SVG, no new dependency); vitest (375 in all) and `build_guide.spec.ts` [here] |
| The guide equals the footprint | met | `test_build_guide_data.py`; `test_build_guide_harness.py::test_the_guide_for_every_harness_structure_matches_its_footprint` (every controller, variant, facing) [here] |
| Controller front, arrow and compass (the PR review's M1) | met | vitest and e2e [PR, here] |
| GuideNH frame on its own origin (closes D304's gap) | met with fakes; **PC step** | `guide-<dashboard_host>:<port>`, a sibling name, path token; `test_the_guide_host_serves_the_guide_only`, `test_a_repeated_device_cookie_is_rejected`; e2e plants a cookie from the frame and the dashboard stays paired [here]. Real: step 8 |
| **Clayton builds one multi from the guide alone** | **PC step** | `build_guide_lab.md` C (step 7) |
| Keyboard access to the 3D scene | not built | PR #124 L4, question 42 |

Open: PR #124's questions.

---

## G10 row

> **G10:** "Reviewer report; Clayton sign-off"

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Reviewer report | met | this report |
| The Phase 10 acceptances | **met with fakes, except defect 1** | the eight WOs above; WO-10.0 not run |
| Phase 10 end to end | met with fakes | on the harness DB: plans → layout → shell (both themes) → `.schematic` → read back identical, and footprint → guide [here] |
| Clayton's sign-off | **PC step** | the steps below, then his sign-off |
| §10's G11 line "Build: footprints for the planned multis; 3D guide; ≥ 2 themes; a Claude-designed shell pasted in the Lab" | open until the PC steps | steps 3, 7 and 6; "footprints for the planned multis" also needs defect 1 fixed if any planned multi needs a capture |

**Required before Clayton signs G10:**
1. **Fix defect 1** (owner: the PM; a small WO-10.2/10.4 follow-up). Until then step 3's captures can't be loaded.
2. The PC steps below, with their paste-backs.
3. His answers, or "keep as built", to the Phase 10 questions below. Under D158 the defaults stand and are reversible.
4. `main` green on every CI job at the commit he deploys, with WO-10.8 merged. `6aa5d6f`'s Windows job is red on a WO-5.1 flake (risk 1).

Should do (not blocking):
1. **Make `test_ask_errors_say_whether_the_core_answered` robust on Windows** (risk 1): a refused request whose connection the core closes first reads as `WinError 10053`. Not a Phase 10 file; owner WO-5.1's or the QA lane.
2. **Record the reviewers' verdicts on D305, D306 and D309-D320.** Their rows still open "Pending reviewer (PR questions)" while each PR says the advisor agreed. The log is append-only, so a later row records them (as G9's should-do 2).
3. **Appendix B's `build/` line is stale.** It names `shells.py` and `shell_designer.py`; Phase 10 built `shell.py`, `shell_store.py`, `shell_cli.py`, `layout.py`, `capture.py`, `coverage.py`, `guide.py`, `guide_data.py` and `worldreg.py`. Docs only.
4. **Keyboard access to the guide's 3D scene** (PR #124 L4): decide with the board.

---

## Defects found

1. **A Lab capture saved as a `.schematic` can't become a footprint** (WO-10.2's acceptance; D306's hand-off to WO-10.4 not done). Details in WO-10.2's table. The fix is: `schematic.read` keeps each tile entity's `mID` by position (GT's `BaseMetaTileEntity` saves it in the tile entity's NBT); `gos footprints capture` takes a `.schematic` with the controller's position and facing (or finds the one controller cell among the tile entities) and passes the cells through `capture.from_cells`; `footprints_lab.md` C.3-C.4 and D, and `capture.py`'s docstring, drop "until WO-10.4". This review doesn't make that change: it is a new input path with its own tests and runbook, not a small fix, and the reviewer shouldn't review its own feature. Until it lands, the coverage report still says which multis need a capture.
2. **Not a defect, noted:** `gos palette blocks ... | head` ends with a Python `BrokenPipeError` traceback when the pipe closes early. Cosmetic; PowerShell's `Select-Object -First` doesn't close the pipe that way.

No defect was found in the QA lane's area (W5-W9) beyond risk 1, which is listed, not fixed.

---

## VERIFY items Phase 10 touched

| ID | WO | Status now | What closes it |
|---|---|---|---|
| **V09** (gtnh-extractor builds and runs at the pin) | 10.2 (owner 1.8) | Resolved in the dev container (297 controllers, 455 variants) | On his PC it is G2's `phase1_pc_checks.md` step 8; step 3 below uses that dump |
| **V15** (GuideNH `exportsite` embeds in the dashboard) | 10.1, 10.8 | Partly: framed on its own origin, proven with a stand-in export | Step 8 (his export renders; no 404s; the isolation check). Then V15 and D304 are resolved |
| **V16** (worldedit-gtnh #30, #24, #28; `WEOffset` in game) | 10.3, 10.4, 10.7 | The harness covers ids < 4096 and meta ≤ 15 and the offset on the dev server; meta > 15 is refused in shells | Step 2 (`schematic_roundtrip.md` B-D) and step 7 (the shell's offset from the entrance cell) |
| **V17** (img2schem merged) | 10.0 | Resolved (D74); the re-scope itself is WO-10.0, not run | Clayton adds the SOW |
| **L1** (plan §9: build every tracked multi with the projector; captures stored) | 10.2 | Open; captures blocked by defect 1 | Step 3 after the fix |
| **L6** (paste a Claude-designed themed shell; build a machine from the 3D guide) | 10.7, 10.8 | Open | Step 7 |

`core/gregtec/verify.tsv` and plan §13.2 agree for these rows (`test_verify_list.py` ran here).

---

## Open questions from Phase 10's PRs

No later decision answers them: D304-D320 are the Phase 10 rows on `main` and on #124. The defaults stand, reversible, until Clayton answers (D158). The PR reviewers (advisors) agreed with each judgement call, as the PRs say; Clayton still decides. PR text is quoted here as data.

**PR #110** (WO-10.1), https://github.com/crazie450-prog/GretecOS/pull/110
1. D304 (pending Clayton): GuideNH export, framed, MrBruh as the fallback. Confirm after the Lab run (step 8).
2. `/guide/` relied on "only your own unmodified export in that folder" until WO-10.8. Now answered by WO-10.8's own origin (D319). Keep?
3. A fixed folder `GOS_HOME/data/guide-site`, no config key. OK?
4. `/guide/` needed the dashboard's credential; on the own origin it has a path token (D319). Noted.
5. Unverified: the export for a `DefaultGuide/gos/...` guide starts with `gos/`; else the Build view opens the first page. Step 8 checks it.
6. Unverified: the extra `_res` suffixes; step 8's 404 check catches a missing one.
7. Not tried in the client harness (the export is a chat command). Noted.

**PR #116** (WO-10.2), https://github.com/crazie450-prog/GretecOS/pull/116
8. D305: no footprint files; the key includes the tier values. OK?
9. D305: metas are not rotated; no up/down facings (`minecraft:anvil@1` the one directional base cell). OK?
10. D305: "legacy" = class name ends in `Legacy`; the default variant is the smallest by volume (for WO-3.6's estimate; WO-10.5 places the largest). OK?
11. D306: a capture of a controller the extractor has is refused; "needed" = counted plans plus My base. OK?
12. **Should the planner prefer non-legacy controllers?** A G3 plan picked `mte:811` `MTEIndustrialMixerLegacy`. Gameplay preference.
13. The SOW might change: footprints as files or ghosts; whether GuideNH region-wand exports must be read as well as `.schematic`.

**PR #104** (WO-10.3), https://github.com/crazie450-prog/GretecOS/pull/104
14. D307: texture PNGs not copied (licence, size); only path, sha256, average colour. OK?
15. D307: `block_find` is deep lane only. OK?
16. **D307: blocks with no item of their own (doors, beds, crops) are not in the catalog, so a shell can't have doors.** Acceptable, or add them by their placing item?
17. **D308: every orientation meta is refused** (stairs facing 1-7, upside-down slabs, sideways logs), so a shell can't use them yet. Add orientation families (proposed for WO-10.4, not done there: D309 (5) passed it to WO-10.7, which didn't add it either)?
18. D308: at most 255 block types per palette. OK?
19. D308: `gos palette check` defaults to the Lab world. OK?
20. **D308: meta > 15 refused outright until #30 is shown fixed** (step 2). A warning after that?
21. D307: the newest run with a catalog is used; a newer run without one is reported. OK?

**PR #109** (WO-10.4), https://github.com/crazie450-prog/GretecOS/pull/109
22. D309 (1): one id per registry name, its metas share it. OK?
23. D309 (2): above 255 types the shell is split by layer, row, column; `AddBlocks` only on request. OK?
24. D309 (3): `write_shell` is the only shell path; an all-air shell is refused. OK?
25. D309 (6): GOS never pastes and never writes into the Lab (enforced). OK?
26. D310: the harness covers ids < 4096, meta ≤ 15. Add EndlessIDs to the dev runtime later?
27. D311: nibble order from the writer, detected, or refused; a file without mappings is named only from its own world's `level.dat`. OK?
28. The mod job's limit was raised to 90 min instead of splitting it. Split later? (D340 since moved integration tests to 4 workers.)

**PR #120** (WO-10.5), https://github.com/crazie450-prog/GretecOS/pull/120
29. **D312: a multi with several variants is placed at its largest**, with a warning; nothing picks the variant a recipe needs (e.g. a distillation tower's height). Acceptable for now?
30. D312: a tier with no generator is fed through a transformer (a warning); no transformer cell is reserved. Reserve one?
31. D312: low machines' pipe and cable drops pass through corridor headroom. Handle in a shell WO?
32. The geometry defaults (corridors 3 wide, 3 high; clearance 1; chases 2 high; rows ≤ 64) and the reach rule (front plus one cell of each hatch kind). OK?
33. D313 (**needs his board**): the `layout` card and the Designer's Floor plan panel.

**PR #118** (WO-10.6), https://github.com/crazie450-prog/GretecOS/pull/118
34. D314: `themes/` is written only through `theme_save`; his own Obsidian edits are checked by `gos theme check`. OK?
35. D314: the fixed role list (wall, trim, pillar, floor, roof, window, light, accent; four required). OK?
36. **D314: refining a starter writes his copy to `themes/`, which then replaces the starter.** OK?
37. **D315: the six line types and the 256-block "nearby" radius are fixed in code.** Tune them (`[build]` in gos.toml)?
38. **The starter designs are his taste:** Foundry uses GT machine casings as decoration (`gregtech:gt.blockcasings2@0`, `gregtech:gt.blockcasings@11`), which cost more than plain blocks. Casings, or plain blocks only?
39. Concurrency: a save refuses if the theme changed under it; no vault lock file (Obsidian would show it). OK?

**PR #121** (WO-10.7), https://github.com/crazie450-prog/GretecOS/pull/121
40. **D316: nothing inside the layout below the roof; walls only in the 1-block ring.** Inner partitions or mezzanines later?
41. D317: the 25% repair share counts placed cells only. OK?
42. D318: Clayton pastes from entrance e1's `lo` cell; the cost is an estimate bounded by `task_max_usd` (2.0). OK? Does the e2e core need a block catalog to show a successful Designer shell?
43. `build_request`'s `save` defaults to true; the architect looks first with false. OK?
44. **Board approval** (D92): the Build view's Shells panel and the Designer's shell button.

**PR #124** (WO-10.8), https://github.com/crazie450-prog/GretecOS/pull/124
45. D319: GOS's own SVG viewer beside the framed export, instead of three.js. OK?
46. D319: the guide host is derived (`guide-<dashboard_host>:<port>`), and cross-site loads are accepted only as iframe navigations. OK?
47. D319: the frame is PC only (the tailnet name has no guide host). OK?
48. D319: path tokens last 6 h, renewed 30 min before expiry and after a core restart. OK?
49. **D320: one checklist per multi type, per layer.** A per-placed-machine checklist later?
50. **Keyboard access to the 3D scene:** a per-layer cell list, or arrow keys through the cells? (With the board.)
51. **Board approval** (D92): the guide view has no approved board.

**WO-10.0 (every Phase 10 PR):** each lists what the img2schem SOW might change (reference-image input, palette and output formats, viewer choice, roof styles, interior detail, defaults). They wait for the SOW.

---

## Clayton's PC steps before signing G10

These run after G2's PC steps 0 to 4 (`docs/reviews/G2_review.md`: Node.js and pnpm, `phase1_pc_checks.md` with the export, the DB build and step 8's **`gos structures extract` and `gos structures load`**, which the footprints need, the setup wizard with the API key, and the dashboard paired on monitor 2) and G5's voice steps (`docs/reviews/G5_review.md`: the voice client and talk key, which step 6 needs). They don't repeat those steps. The commands are the runbooks' own: `uv run gos ...` runs as **gos-dev** in its dev clone where the runbook says so; `$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` in his own account. PowerShell with `$ProgressPreference = 'SilentlyContinue'`. **Nothing here touches Play: every paste and build is in a Lab world, by his own hand. GOS never pastes.** Money: steps 6 and 7 spend a few deep-lane tasks (each capped at `[budget] task_max_usd`, 2.0). In all, about 3 h plus the multiblock build.

**0. Deploy a green `main`** with WO-10.8 (#124) and the fix for defect 1 merged, every CI job green on that commit (not `6aa5d6f`, risk 1). In the profile clone: `git pull`, `uv run gos deploy`. `& $gos status` shows the core `ok`. In the Lab instance's `mods`, `worldedit-gtnh-v0.0.10.jar` (as Play has, D88).

**1. The block catalog** (`palette_export.md` A-C, WO-10.3's acceptance). Sync Export from GOS_APP (A.2), click **Launch** on GTNH-Export, then as gos-dev `uv run gos export collect` and `uv run gos db build`. **Pass:** `parts.palette: "ok"`, `palette_errors` 0; a `Block catalog:` line; in B, casings and glass `te` 0, `minecraft:chest@0` and `gregtech:gt.blockmachines@0` `te` 1, Chisel listed; in C, `5 of 8 entries accepted`, exit 1.

**2. The schematic round trip** (`schematic_roundtrip.md` A-D, WO-10.4's acceptance, V16). As gos-dev, a palette of blocks with Lab ids **above 4095** (F3+H), then:
```powershell
uv run gos schematic sample "$env:TEMP\roundtrip.txt" --out "$env:TEMP\gos_roundtrip.schematic"
```
Copy it into the Lab's `config\worldedit\schematics\` yourself; in a creative flat Lab world `//schem load gos_roundtrip`, `//paste`, check the offset, `//copy`, `//schem save gos_roundtrip_copy`; then:
```powershell
uv run gos schematic compare "$env:TEMP\gos_roundtrip.schematic" "$lab\gos_roundtrip_copy.schematic"
uv run gos schematic read "$lab\gos_roundtrip_copy.schematic"
```
**Pass:** `identical`, exit 0, and the C3 one-liner shows ids above 4095. D: a GT frame box with meta above 15, `//copy`, `//schem save meta_test`, `uv run gos schematic read` on it: send the output (meta > 15 behaviour, #30).

**3. Footprints for his planned multis** (`footprints_lab.md` A-D, WO-10.2's acceptance, L1). With step 8 of `phase1_pc_checks.md` done:
```powershell
& $gos footprints list | Select-Object -Last 3
& $gos footprints coverage
```
**Pass:** no structure row without a footprint; every multi in his accepted plans and My base is `footprint` or `capture`. For each `needs capture`: build it in `lab-structures` with the projector (`/sl_channel set` first), `//copy` + `//schem save` **with tile entities**, then load it with the command defect 1's fix adds and run `coverage` again. Then one base multi through `gos footprints compare` (D).

**4. The layout at real sizes** (PR #120 steps 1 and 3). As gos-dev in its dev clone, with the real dump from `phase1_pc_checks.md` step 8; the test builds its DB from a fresh server-harness run (`mod/README.md`), so run that first:
```powershell
cd C:\GregtecOS-dev\GretecOS\mod; .\gradlew --no-daemon runServer -Pharness; cd ..
$env:GOS_STRUCTURES_DATASET = "C:\GregtecOS-dev\structures\2.9.0-pc1"; $env:GOS_REQUIRE_HARNESS_RUN = "1"
uv run pytest tests/integration/test_layout_harness.py -s
```
**Pass:** 1 passed; each "largest variant" warning names the variant he'd build. Then `& $gos layout <a production plan> <its power plan>` on one of his lines: a transformer warning when the tiers differ.

**5. Boards** (D92, D158 (5)), at 2560x1440 and on the phone: the Designer's **Floor plan** panel and **Design a shell around it** button, the Build view's **Shells** panel and the **3D build guide** (and question 50, keyboard access). Approve or redline.

**6. Themes by voice** (`themes.md` A-C, WO-10.6's acceptance). As gos-dev `uv run gos theme list` and `uv run gos theme check` (two `ok`, exit 0). With the core and voice client running, by voice: make "copper works" (v1), refine it twice (v2, v3), then ask for chests as trim (refused out loud, no new version unless he accepts a substitute). `uv run gos theme list` shows `th-copper-works v3 vault`; then the three `gos theme select` modes.

**7. L6: a Claude-designed shell and a multi from the guide** (`shells_lab.md` B-D and `build_guide_lab.md` A-C, WO-10.7's and WO-10.8's acceptances).
- `uv run gos shell design <plan-id> --theme th-foundry --line-type smelting`, then `uv run gos shell show <sh-id>` (the procedural fallback).
- Ask: "Design a foundry shell around my steel line with pillars every four blocks and a gable roof." `uv run gos shell list` shows it as `architect`.
- `uv run gos shell export <sh-id>`; copy the `.schematic` (and any `-part-N`) into the Lab's `config\worldedit\schematics\`.
- In `lab-structures`: build or mark the layout, stand in the cell `gos shell show` names (entrance e1), `//schem load <sh-id>`, `//paste`, never `//rotate`.
- **Pass (10.7):** `//count gregtech:gt.blockmachines` in the shell's region is 0, no block with a GUI appeared, every corridor and every machine's front, sides and top are open, the doorway is open; select it, `//copy` from the same block, `//schem save <sh-id>-copy`, and `uv run gos schematic compare` says `identical`.
- In the Build view, open the guide for one multi in that line (a footprint from step 3), set **Front faces** to match, and build it layer by layer from the guide alone, ticking the checklist; reload once to see the ticks kept.
- **Pass (10.8):** the controller forms (no structure error in its GUI or the Scanner).

**8. His GuideNH export** (`docs/reviews/WO-10.1_guide_spike.md` "Lab steps" 1-5 and `build_guide_lab.md` B.5, V15, D304). In a creative Lab world: the guide page with the EBF controller's meta read in NEI (F3+H, the number after the slash), `/guidenhc exportsite`, copy the folder's contents unmodified to `GOS_HOME\data\guide-site\`. In the Build view's **In-game look**: it renders, hover and layers work, no 404s under `/guide/` in DevTools; the frame's address starts `http://guide-gos.localhost:8765/guide/<token>/`; `window.parent.fetch` from the frame throws; `document.cookie = "gos_device=x; domain=gos.localhost; path=/"` in the frame, then reload the dashboard: still paired.

**9. Sign-off.** Answer the questions in bold above (12, 16, 17, 20, 29, 36, 37, 38, 40, 49, 50) and the board items (33, 44, 51), and say whether to keep each other default.

**What to paste back for G10:**
- step 1's `Block catalog:` and `by mod:` lines, the listings' first lines and the check's output;
- step 2's `compare` and `read` outputs, the ids above 4095 and the meta > 15 result;
- step 3's `coverage` output before and after the captures, and `compare`'s result;
- step 4's pytest output and the `gos layout` warnings;
- step 6's spoken lines and `gos theme list`;
- step 7's results per check, the shell id, the multi built, its facing and tier, and anything the guide got wrong;
- step 8's render, size, time, 404s, page count and the isolation checks;
- his answers and board verdicts.

---

## Risks

1. **`main` is red on Windows at `6aa5d6f`.** WO-5.1's `test_ask_errors_say_whether_the_core_answered` saw `WinError 10053` instead of a refusal; it passed on Windows at `a8250c3` and `76dd152`. A timing flake outside Phase 10 (should-do 1). (Seen once)
2. **Real multiblock sizes are untested here.** Without the extractor dump in this container, WO-10.5's and WO-10.7's harness tests use the fixture's 3x3x3 stand-in for both benchmark multis, and the benchmark "steel (EBF)" line is built from Alloy Blast Smelters on the harness DB. The 13-line run with the real dump was green in PR #116's review; step 4 re-checks it on his dump. (Likely fine)
3. **Addon multis have no footprint until defect 1 is fixed.** They are listed, never guessed: the layout puts them in `unplaced`, the shell leaves no room for them, and the guide has nothing to show. (Certain until fixed)
4. **worldedit-gtnh on 2.9 may still mishandle ids above 4095 or orientation metas** (V16, #24, #30). Shells keep meta ≤ 15 and refuse every orientation meta (question 17), so a shell can look plainer than the theme intends. (Unknown until step 2)
5. **The real architect may need several tries.** The gate refuses anything outside the palette and repairs only small breaches, so a real model's first design may be refused; the procedural shell is the fallback, and each try is capped at `task_max_usd`. (Possible)
6. **GuideNH's export layout is assumed** (the `gos/` page, the `_res` suffixes), from source reading, not a run. Step 8 checks it; the SVG guide works without it. (Low)

## After the review

- Everything above ran green on `76dd152` (WO-10.8's merge-gate head, which contains `main` at `6aa5d6f`). No product code was changed: defect 1 is listed for the PM, not fixed here.
- No decision row was added (D322 unused): this review makes no judgement call that needs one.
- WO-10.8 (#124) was still open when this report was written (`origin/main` at `6aa5d6f`). This branch carries its head `76dd152`, so this PR shows WO-10.8 too until #124 merges; after that only this report remains in its diff.
