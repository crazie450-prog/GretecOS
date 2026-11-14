# Gregtec OS: G6 gate review (Phase 6, context awareness, quests, advisor, ores, crops)

2026-11-14 · independent gate reviewer (D41, D158 (2)) · reviewed: `origin/main` at `d896ee1` (every Phase 6 PR merged; WO-6.5 last, as PR #98). It covers PRs #87 (WO-6.1, merged as `9005c6e`), #89 (WO-6.2, `d78d24b`), #93 (WO-6.3, `7415665`), #82 (WO-6.4, `bdc74cd`), #98 (WO-6.5, `e481ca4`), #83 (WO-6.6) and #86 (WO-6.7, `8de78f4`). #83 has no merge commit of its own: it was stacked under #86, and its head `61bc3bf` reached `main` inside #86's merge `8de78f4` (GitHub records `721d73e`, a commit on 6.7's branch, as its merge sha).

## Verdict

**G6 is ready for Clayton's sign-off at the deployment test, with fakes and the harnesses standing in for his Lab and the real model.** All seven Phase 6 work orders are built and merged. Every acceptance criterion that can be checked without Clayton's game, his API key and his own judgement is met, here or on CI. Nothing is "not met" in code. Every §8 acceptance in Phase 6 is a Lab check, a real-model run or a choice of Clayton's, so every row is **met with fakes**, and the PC steps below are the acceptance.

What the PC steps must show (D158 (2) batches them with the other gates):
1. **WO-6.1:** a 2 h Lab session with no save errors (V27), and "Game paused" on the dashboard within 5 s of Esc (V29).
2. **WO-6.2:** 10 GT machines placed and 4 broken in `lab-structures` give the right `mte:` ids, and the census matches (V26).
3. **WO-6.3:** the scripted Lab session gives at least 95% recall.
4. **WO-6.4:** `gos eval quest-guide` with the real model: 20 of 20, spoiled 0.
5. **WO-6.5:** Clayton rates the 3 scenarios "useful", and both default notices fire in `lab-ops`.
6. **WO-6.6:** nearest-vein answers check out in `lab-ores`, and the confirmed waypoint shows after a dimension change (V23).
7. **WO-6.7:** Clayton picks his 5 target crops (the PM's defaults stand until he does) and reviews the guides; one is followed to its target in `lab-progress`; V30 and V32 Lab checks.

Evidence tags, as in G0 to G8:
- **[repo]:** checked in the repo, the git history or GitHub's CI logs.
- **[here]:** run by this reviewer in the build container (Linux, 4 shared CPUs, no GPU, no Windows, no game window, no API key).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC. Nothing from the PC is in the evidence yet for Phase 6.

### This reviewer's runs [here]

All runs are on `79c63c1` (`main` at `d896ee1` plus this branch's runbook commit `05ecf3e`; no product code differs from `main`).

| Check | Result |
|---|---|
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 546 files already formatted |
| full `pytest` (default groups, as CI) | **9378 passed, 321 skipped**, exit 0 (10 min 58 s) |
| Phase 6 unit tests (`tests/unit/watchers`, `tests/unit/events`, `tests/unit/crops`, `test_advisor.py`, `test_quest_guide.py`, `test_ore_intel.py`, `tools/test_gamelink.py`, `voice/test_one_stall.py`) | **3668 passed, 1 skipped** (the Windows-only share-mode control) |
| `./gradlew --no-daemon --max-workers=2 runServer -Pharness` | `harness passed: 36 checks, run 20261114T111729Z` |
| `./gradlew ... runServer -Pharness -PharnessListener` (WO-6.2) | `harness passed: 19 checks`: `ten_kinds` [1000, 66, 103, 104, 106, 107, 109, 110, 112, 113], `kinds_include_controller_and_hatch`, `cancelled_place_left_nothing`, `silent_place`, `read_never_loads_chunks`, `saved_census-a` (11 machines), four `broke_*` (the EBF 1000 and hatch 66 among them), `cancelled_break_kept_machine`, `silent_remove`, `saved_census-b` (6 machines), `census_b_save_times_later`, `events_match_actions` (14 events, `gregtech:gt.blockmachines`), `event_lines_valid`, `writes_only_under_gregteclink` |
| `xvfb-run ... ./gradlew ... runClient25 -Pharness` | `harness passed: 62 checks, run 20261114T112028Z` |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest` on the Phase 6 integration tests over those runs (`test_machine_events_harness`, `test_my_base_harness`, `test_event_store_harness`, `test_ore_intel_harness`, `test_ores_harness`, `test_quest_guide_harness`, `test_advisor_harness`, `test_crop_guide_harness`, `test_crops_harness`) | **29 passed, 2 skipped** (3 min 29 s). Skips: `test_l0_never_spoils_the_real_book` (needs `GOS_QUESTS_PACK`, an extracted 2.9.0 pack: PC step 8) and `test_two_client_exports_give_the_same_crop_data` (needs a second client run; CI runs it) |
| `gos eval quest-guide --fake` (scratch GOS_HOME) | `passed: 20 of 20`, `spoiled: 0`, `model calls: 38`, `cost: $0.008689 (simulated)`, `Result: PASS` |
| `gos eval advisor --fake` (scratch GOS_HOME, the real Claude Code CLI with the fake model) | `advisor: 3 of 3 scenarios pass` |
| `pnpm -C dashboard typecheck`, `test`, `build` | ok; vitest **19 files, 332 passed** |
| Playwright `activity`, `crops`, `ores`, `quests` and `dashboard` specs (own ports 18961-18965, own homes) | **36 passed** (1.5 min), at 2560x1440 and 390x844 |

The sandbox refuses a shell command that contains the word "eval", as at G2, G5 and G8. Both evals above ran through the same CLI entry point (`gregtec.cli.main(["eval", <name>, "--fake"])`) from a small script in the session's scratch folder. No DB, export, world or eval report from these runs is committed.

### CI on `main` [repo]

| Commit | Run | ubuntu | windows | mod | dashboard | other |
|---|---|---|---|---|---|---|
| `bdc74cd` (#82, WO-6.4) | 43320057221 | success | success | success | success | dashboard (Windows) 43320056914 success |
| `8de78f4` (#86, WO-6.7 with WO-6.6) | 43330642066 | success | success | success | success | dashboard (Windows) 43330642027 success |
| `9005c6e` (#87, WO-6.1) | 43364900465 | success | success | success | success | |
| `d78d24b` (#89, WO-6.2) | 43366372947 | success | success | success | success | mod (Windows) 43366373005 success |
| `7415665` (#93, WO-6.3) | 43501506142 | success | success | success | success | dashboard (Windows) 43501506129 success |
| `e481ca4` (#98, WO-6.5) | 43571468861 | success | success | success | success | dashboard (Windows) 43571468424 success |
| `3b3a830` (#106, the G7 report) | 43581883932 | success | success | success | success | |
| `d896ee1` (#104, WO-10.3, head) | 43588012389 | success | success | (running when checked) | **failure** (risk 1) | mod (Windows) 43588012426 success |

Every Phase 6 merge commit is green on every job. `d896ee1`'s dashboard failure is not Phase 6: `e2e/designer.spec.ts:135` (WO-3.7's Designer contrast test) timed out waiting for a react-flow node, and the other 70 Playwright tests passed. PR #86 saw the same test fail once on a loaded runner. The newest commit green on every job is `3b3a830`.

---

## Phase 6 work orders

### WO-6.1: Watchers, paused state, watchers and voice with Play

> **Acceptance:** "No game save errors in a 2 h session; a pause shows on the dashboard within 5 s"

**Verdict: met with fakes. The 2 h session and the 5 s pause are PC steps on Lab.** PR #87, merge `9005c6e`. Decisions: D257-D259.

| Part (plan §8 row 6.1) | Status | Evidence |
|---|---|---|
| Watchers: log tail, quest progress, level.dat/playerdata, stats, ServerUtilities world log, VP, JourneyMap | met with fakes | `core/gregtec/watchers/` (`logparse`, `player`, `sources`, `files`, `runner`); `tests/unit/watchers/test_readers.py` over `fakegame.py` [here] |
| Every game file through `safe_open()` (V27); no held handle | met with fakes; **PC step** | `test_soak.py`: a fake game saves by rename, rotates logs and locks files while every reader runs; no fd left open (`/proc`), no write blocked. The Windows half ("a held plain handle blocks a save; `safe_open` does not") runs on Windows CI [here, repo]. The game's own save loop is PC step 3 |
| Started and stopped with Play by the supervisor (D77), the voice client with them (D249) | met with fakes | `test_with_play.py`, including `test_play_is_watched_only_once_clayton_turns_it_on` (D259's `[startup] watchers_with_play`, default `"lab"`), `/restart` 409 while nothing plays, the crash fallback [here] |
| Paused-state detection (V29), `game_paused` events | met with fakes; **PC step** | D258's rule: `test_a_pause_stays_paused_through_its_slow_save`, `test_pause_grace_is_measured_by_the_logs_own_stamps` (`test_readers.py`) [here] |
| A pause shows on the dashboard within 5 s | met with fakes; **PC step** | `test_pause_push.py::test_a_slow_save_file_poll_never_holds_back_the_pause` (queued in under 1 s with a 3 s file poll) [here]. On the PC: `watchers.md` §2 |
| No game save errors in a 2 h session | **PC step** | `watchers.md` §4 |
| Game text is data | met | events marked `untrusted` [PR]; no watcher event reaches a prompt as an instruction [repo] |

Open: PR #87's questions (below). Known gap (D259, L7): a supervisor restart in the middle of a session leaves the watchers stopped until the next launch.

### WO-6.2: Machine events (listener, census, My base counts)

> **Acceptance:** "Placing and breaking 10 different GT machines in the Lab yields events with the correct IDs; the census matches the Lab base"

**Verdict: met with fakes. The harness drives real GT 5.09.54.265 in the dev runtime; the Lab in the pack is a PC step (V26).** PR #89, merge `d78d24b`. Decisions: D260-D262 (D287 later replaced D262's hand install with `install_gregteclink.py`).

| Part | Status | Evidence |
|---|---|---|
| GregtecLink server-side place/break listener reporting `mID` (V26) | met in the harness; **PC step** | listener harness 19 checks [here]; `test_events_say_what_the_player_did`, `test_kinds_include_a_controller_and_a_hatch` [here]; `PendingTest`, `EventFileTest` [CI `mod`] |
| Tile-entity-only census of changed chunks | met in the harness | `test_census_matches_the_base[a, b]` (11, then 6 machines, including the one the listener never saw) [here]; `tests/unit/watchers/test_census.py` (a 2,000-machine chunk, a deleted region) [here] |
| My base owned-machine counts (D211 precedence) | met | `test_my_base_counts_through_the_watchers` [here]; only Play's watchers feed My base (D261) |
| No numeric ids (CLAUDE.md); no EndlessIDs decoder | met | registry name + meta (`gregtech:gt.blockmachines@<mID>`) and `mte:<id>` only [repo] |
| 10 different machines in the Lab, correct ids; census matches the Lab base | **PC step** | `machine_events.md` §2 (PC step 4) |

Open: PR #89 Q1-Q9.

### WO-6.3: Event store, sessions, `session-scribe` journal, Activity view, tier-up recap

> **Acceptance:** "A scripted Lab session gives ≥ 95% event recall"

**Verdict: met with fakes. The real Lab session is a PC step; the Activity view and the recap panel need Clayton's board approval.** PR #93, merge `7415665`. Decisions: D263-D265.

| Part | Status | Evidence |
|---|---|---|
| Event store (idempotent by event id; a bad event skipped, a DB error 503) | met | `tests/unit/events/test_event_store.py` [here] |
| Sessions (login to quit, crash or 15 min idle; GregtecLink's timer never activity, D264) | met | `test_event_store.py`; `tools/test_gamelink.py::test_an_afk_hour_with_the_link_connected_ends_idle_at_the_last_activity` and the restart-grace tests [here] |
| `session-scribe` journal (Play only, through `gregtec.vault`, counts by code) | met with fakes | `test_journal.py` (including `test_the_end_block_waits_for_the_quits_own_save_files`); `test_event_store_harness.py::test_listener_events_in_a_session_and_its_journal` on the listener harness's real events [here] |
| Activity view; the finished age's recap on a tier-up (D162) | met with fakes; **board approval** | `test_ages.py` (provisional ages, H2), vitest `activity.test.tsx`, `e2e/activity.spec.ts` at both sizes [here]. No board (docs/design/README.md "No board yet": Activity) |
| ≥ 95% recall on a scripted Lab session | met with fakes; **PC step** | `tests/unit/watchers/test_session_recall.py`: real watchers over fake Lab files and listener lines, 28 ground-truth events, 100% recall; the control without the listener file drops below 95% [here]. On the PC: `event_store.md` §2 (PC step 5) |

Finding 1 (below) is in this WO's runbook and is fixed on this branch. Open: PR #93 Q1-Q7.

### WO-6.4: `quest-guide` skill, spoiler levels, Quests view

> **Acceptance:** "20-question eval; L0 never spoils"

**Verdict: met with fakes. The real-model eval, GTNH's real book and the board check are PC steps.** PR #82, merge `bdc74cd`. Decisions: D268-D270.

| Part | Status | Evidence |
|---|---|---|
| 4 functions (requirements, frontier, item<->quest, `qs-<line>.md` summaries) | met | `quests/guide.py`, `test_quest_guide.py` [here]; `test_quest_items_link_to_the_real_export` [here] |
| Global spoiler levels (D72), whole-utterance commands (D268) | met | `quests/spoilers.py`; `test_no_dashboard_tool_call_shows_another_locked_quest_above_its_level`, `test_a_step_up_tap_never_names_a_hidden_prerequisite[1, 2]` [here] |
| Quests view with spoiler blur | met with fakes; **board check** | vitest `quests.test.tsx`, `e2e/quests.spec.ts` (4 passed) [here]. The Quests board is approved (D92) but the view was built without the canvas handoff: compare it (`quest_guide.md` E) |
| 20-question eval | met with fakes; **PC step** (real model, about $0.01) | `gos eval quest-guide --fake`: 20 of 20, spoiled 0, with the adversarial fake that asks for full spoilers on every question [here]; `test_the_eval_passes_with_the_adversarial_fake` on the harness DB [here] |
| L0 never spoils | met with fakes; **PC step** on the real book | `test_l0_never_spoils_the_view_payload` on the harness DB [here]; `test_l0_never_spoils_the_real_book` needs an extracted 2.9.0 pack (PC step 8) |

Open: PR #82 Q1-Q14.

### WO-6.5: `advisor` and proactive notices

> **Acceptance:** "Clayton rates 3 scenarios 'useful'; both default notices fire in a Lab scenario"

**Verdict: met with fakes. Clayton's rating and the Lab notices are PC steps.** PR #98, merge `e481ca4`. Decisions: D266-D267 (with D280-D281 from WO-7.5 for the stall).

| Part | Status | Evidence |
|---|---|---|
| `advisor` (code-ranked steps, cited, nudges under the spoiler level) | met | `advisor/checks.py`, `steps.py`; `test_advisor.py` [here]; `test_advisor_harness.py` (a plan of real machines buildable from their items; a real multiblock needs its whole structure) [here] |
| Proactive notices with the D71 defaults on | met with fakes; **PC step** | `test_both_default_notices_fire_in_the_lab_scenario` (the OC contract's telemetry through the real `oc-lab` ingest), `test_a_stall_alone_holds_an_opportunity_back`, `test_a_failed_check_or_a_quiet_oc_is_unknown_not_cleared` [here]; `voice/test_one_stall.py` (one card, one line) [here]; vitest `advisor.test.tsx` (an `alert` push refreshes Next steps) [here]. On the PC: `advisor.md` C (PC step 12) |
| Clayton rates 3 scenarios "useful" | **PC step** (real model, under $0.50) | `gos eval advisor --fake`: 3 of 3 pass on structure, citations and spoilers [here]. The rating is his alone (`advisor.md` B) |
| Now's and Glance's "Next steps" | met with fakes; **board approval** | vitest [here]; PR #98 marks it for board approval (D92) |

Open: PR #98 Q1-Q8.

### WO-6.6: `ore-intel`, Ores view, waypoint action (V23)

> **Acceptance:** "Nearest-vein answers check out in the Lab"

**Verdict: met with fakes. The Lab check and V23's Lab half are PC steps; the Ores view needs board approval.** PR #83, on `main` through #86 (`8de78f4`). Decisions: D271-D272.

| Part | Status | Evidence |
|---|---|---|
| `ore_find`, `player_pos` (both lanes), `ore_waypoint` (deep lane, proposes only) | met | `test_ore_intel.py` (hand-derived ranking `EXPECTED`: 114, 134, 28 and 76 blocks; `test_the_fast_lane_says_where_he_is_from_player_pos`; `test_a_read_during_a_save_waits_then_reads_the_save_before`) [here]; `test_the_primary_vein_ranks_before_a_nearer_lower_layer_one` on the harness DB [here] |
| Ranking by code, never by a model | met | WO-1.6's `ore_find` order; the card keeps it [repo] |
| Waypoint through WO-7.1's action desk; only the veins' own Lab or Play world; refused for Play as gos-dev | met with fakes; **PC step** (V23) | `test_oc_actions.py::test_a_confirmed_waypoint_is_written_by_the_core`, `test_the_view_never_cancels_a_pending_action_of_another_kind` [here]; `e2e/ores.spec.ts` (proposed, read back, written only after confirm) [here]. JourneyMap is `ore_intel_check.md` B |
| Ores view | met with fakes; **board approval** | vitest `ores.test.tsx`, `e2e/ores.spec.ts` [here]. No board (docs/design/README.md "No board yet": Ores) |
| Nearest-vein answers check out in the Lab | **PC step** | `ore_intel_check.md` A (PC step 9) |

Licence: JourneyMap (LGPL-2.1) was read, not copied (D271) [PR]. Open: PR #83 Q1-Q10.

### WO-6.7: Crop breeding guide

> **Acceptance:** "Guides for 5 target crops Clayton picks (at least one needing a sub-soil block and one needing the Crop Breeder), reviewed by him; one followed to its target in `lab-progress`; every number cites `crop:`, `r:` or `plan:`; V30 and V32 resolved"

**Verdict: met with fakes for what code can show. Clayton's picks and review, the followed guide and the V30/V32 Lab checks are PC steps.** PR #86, merge `8de78f4`. Decisions: D273-D275.

| Part | Status | Evidence |
|---|---|---|
| `crops/rules.py` re-implemented from CropsNH 2.0.140's behaviour (LGPL read, nothing vendored) | met | `tests/unit/crops/test_rules_oracle.py` replays 3,310 rows CropsNH's own code produced (`oracle/CropsNHOracle.java`) [here]; tooltips match: `test_crop_breeder_tooltips_match_the_rules`, `test_seed_generator_and_crop_manager_tooltips_match`, `test_every_crop_breeder_recipe_is_a_parent_recipe_at_the_rules_fertilizer` [here] |
| `crops/planner.py`: paths, layouts, requirement checks, odds, times, stat plans; Crop Manager and Crop Breeder steps at the tier | met | `test_planner.py` [here] |
| `crop-breeder` skill, Crops view, voice "next crop step" | met with fakes; **board check** | `test_crop_surfaces.py`, vitest `crops.test.tsx`, `e2e/crops.spec.ts` [here]. The Crops board is approved (D92); compare the view (PC step 14) |
| Every number cites `crop:`, `r:` or `plan:` | met | `test_the_five_picks_every_number_cited[5]` (0 problems each on the harness DB) [here] |
| At least one needing a sub-soil block, one needing the Crop Breeder | met for the PM defaults | `test_the_picks_cover_a_sub_soil_block_and_the_crop_breeder` [here]: Ferrofern (iron ore under it), Coppon (copper), Bauxia (Crop Breeder only) |
| 5 targets **Clayton picks**, reviewed by him | **PC step** (his choice) | D275's five are the PM's defaults pending his picks (PR #86 Q4) |
| One followed to its target in `lab-progress` | **PC step** | `crop_guide.md` §1 (PC step 13) |
| V30 and V32 resolved | partly resolved from source; **PC step** | `crop_guide.md` §2-§3 (PC step 13) |

Open: PR #86 Q1-Q6.

---

## G6 row

> **G6:** "Reviewer report; Clayton sign-off"

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Reviewer report | met | this report |
| The Phase 6 acceptances | **met with fakes** | all seven WOs above. Nothing is not met in code |
| Phase 6 end to end | met with fakes | the listener harness through the watchers into My base and the event store (`test_my_base_counts_through_the_watchers`, `test_listener_events_in_a_session_and_its_journal`); the session-recall fixture (real watchers, real Sender, real route); both evals with adversarial fakes [here] |
| Clayton's sign-off | **PC step** | the steps below, then his sign-off |
| §10's G11 "Context" line (watchers 2 h with no save errors; listener and census agree; journal and advisor work; default notices fire) | met with fakes; G11 re-checks it | PC steps 3, 4, 5, 12 and 16 cover each part. The journal is Play only (D263 (2)), so "journal works" is checked on Play's first watched session (step 16) |
| §10's G11 "Crops" line (guides for his 5 targets; one followed; Crop Manager and Crop Breeder steps checked) | PC step | step 13; the breeding station half is G7's step 9 |
| §9's Lab scenario L3 (`lab-progress` survival, 30 min, 5 machines, the Nether and the Twilight Forest, die once, a vein, chat; recall, listener ids, journal) | partly; finding 2 | `event_store.md` §2 is a shorter script. Step 5 adds L3's extra parts so one Lab session covers both |

**Required before Clayton signs G6:**
1. The PC steps below, with their paste-backs.
2. His answers, or "keep as built", to the Phase 6 questions below. Under D158 the defaults stand and are reversible. His five crop picks are an answer, not a default he can leave (WO-6.7's acceptance names them).
3. His board approvals or redlines: Activity and its recap panel, Ores, and Now's Next steps (no board yet); the Quests and Crops views against their approved boards.
4. `main` green on every CI job at the commit he deploys. `d896ee1` is not (risk 1); `3b3a830` is (run 43581883932).

---

## Findings

1. **The Lab watchers read only `[paths] lab_world`'s save files, and two runbooks didn't say so. Fixed on this branch (`7688309`).** `watchers/runner.py` watches the world `[paths] lab_world` names (`args.world or cfg.lab_world`); with no world, only the log readers run, and with another world named, the quest, level.dat, playerdata, stats and census readers watch the wrong world. `event_store.md` §2 said only "open a survival test world (e.g. `lab-progress`)": with `lab_world` still at `lab-ores` (from `ores_check.md`) the quest, dimension and item events are missed and WO-6.3's recall drops below 95% for a reason that isn't the code's. `event_store.md` §1 now says to set `lab_world` to the session's world before launching Lab, and `machine_events.md` says the dashboard's census feed needs `lab_world = "lab-structures"` (its steps 4 and 5 don't). The PC steps below set it before each Lab world. This is inside WO-6.3's acceptance (the recall check), and only runbook text changed.
2. **Plan §9's L3 is longer than WO-6.3's runbook session, and asks for a journal that Lab doesn't write.** L3: `lab-progress` in survival, 30 minutes, 5 machines, the Nether and the Twilight Forest, "journal accurate". `event_store.md` §2: 3 machines, the Nether only, and D263 (2) keeps Lab out of the journal. WO-6.3's §8 acceptance (≥ 95% recall) is met by either. Step 5 below adds 2 machines and a Twilight Forest visit, and step 16 checks the journal on Play's first watched session. No code change. Owner: the PM, if L3's wording should follow D263.
3. **G8's should-do 2 (a runbook clash) is fixed on this branch (`05ecf3e`).** `machine_events.md` §1 now installs GregtecLink with `install_gregteclink.py` and says not to write Lab's `gregteclink.cfg` by hand. Writing it with `B:client=false` would turn WO-8.1's client module off. `ore_intel_check.md` §0 now switches to `main`, not the merged `wo/6.6-ore-intel` branch.
4. **`crop_guide.md` uses `uv run gos` without saying which account.** The other dev-clone runbooks (`ore_intel_check.md`, G4's steps) run `uv run gos` as gos-dev in `C:\GregtecOS-dev\GretecOS`, and `gos crops have` and `gos crops target` write the home's crop state. This report runs the guide as gos-dev, so a Lab run's seeds never land in Clayton's own crop list for Play (judgement 2). Harmless; the PM may make the runbook say so.
5. **WO-6.6 has no merge commit of its own.** PR #83 was stacked under #86 and closed as merged when #86 landed. Its head `61bc3bf` is on `main`. This is a record-keeping note, not a defect.

---

## VERIFY items Phase 6 touched

| ID | WO | Status now | What closes it |
|---|---|---|---|
| **V05** (chat-line routing: `latest.log` vs `fml-client-latest.log`) | 6.1 (owner), 8.1 | Either works: both logs are read, a line in both counts once (D257) | Step 4 (`watchers.md` §3.3): he records which log carries `[CHAT]` |
| **V06** (ServerUtilities `world.log` path and the broken-block wording) | 6.1 | Keys confirmed (PR #6); the path is `MC\logs\world.log` in code (D257) | Step 4 (`watchers.md` §3.4), in survival |
| **V23** (JourneyMap picks up waypoint files) | 6.6 (owner), 7.1 | Partly resolved from source (D271): read at the next dimension change or rejoin, only with the Waypoint Manager on | Step 10 (`ore_intel_check.md` B). This is G7's step 10: G7 can skip it once step 10 here has run |
| **V26** (the listener sees GT placements with `mID`) | 6.2 | Passes in the listener harness (D260, D262) | Step 6 (`machine_events.md` §2) |
| **V27** (`safe_open` lets the save-by-rename succeed) | 6.1 | Soak-tested on Linux and Windows CI (D257) | Step 3 (`watchers.md` §4) |
| **V29** (a reliable "game paused" signal) | 6.1 (owner), 8.1 | Resolved from the log line (D258); WO-8.1 adds `status.paused` as a second source | Step 2 (`watchers.md` §2): the line's delay, 60 s still paused, unpause within about 45 s |
| **V30** (CropsNH on crop sticks) | 6.7 | Partly resolved from source (D273) | Step 13 (`crop_guide.md` §2) |
| **V32** (the Crop Breeder) | 6.7 | Partly resolved (D273): odds and tanks match CropsNH's tooltips in the harness export | Step 13 (`crop_guide.md` §3), at LV and MV |
| **V34** (a GT multiblock's own shutdown reads `stopped`) | 6.5 (touches), 7.4/7.5 | Open, a Lab test | Step 12 (`advisor.md` C7). G7's steps 7 and 8 check it too; one paste of the EBF's `telemetry_get` row closes it for both |

No Phase 6 PR added a `verify.tsv` row. #83, #86, #87, #89 and #98 changed the text of V23, V26, V27, V29, V30, V32 and V34. `tests/unit/test_verify_list.py` keeps `verify.tsv` and plan §13.2 in step (it ran in the full run).

---

## Open questions from Phase 6's PRs

No later decision answers any of them. D257-D275 each say "Clayton's later review open", and the later rows only build on them (D280-D281 for the stall, D287 for the GregtecLink install). The defaults stand, reversible, until Clayton answers (D158). Each PR's reviewer (advisor) gave a verdict on its judgement calls, but Clayton still decides. PR text is quoted here as data.

**PR #87** (WO-6.1), https://github.com/crazie450-prog/GretecOS/pull/87
1. D257: per-file dedupe; a save file read once it has held still 1 s; log lines read within about 1 s. OK?
2. D258: unpausing uses an allow-list of ticking lines (`<name> `, `* name `, `[name: ...]`, joined/left/lost connection, login), with the grace measured by the log's stamps. Are those line shapes right for 1.7.10 single player? If none appears, unpausing falls back to the 45 s autosave.
3. D258: the runbook's `/time query` check was dropped (it may not exist in 1.7.10); the optional chat-line check replaces it. OK?
4. D259: `[startup] watchers_with_play` (`"lab"` | `"play"`, default `"lab"`); a bad value is a service problem. OK?
5. D259: `/restart` returns 409 while nothing plays. Adopting a client that is already running at supervisor start is not done (L7, known gap). OK?

**PR #89** (WO-6.2), https://github.com/crazie450-prog/GretecOS/pull/89
6. Q1 (D260): events reach the core through a file the watchers tail, not an HTTP post from the game. OK?
7. Q2 (D260): cables and pipes are not counted. OK?
8. Q3 (D261): a census count of 0 is kept, an event count of 0 is deleted, so My base can be about 45 s stale. Newer-event-wins would need a D211 change. OK?
9. Q4 (D261): only Play's watchers feed My base. OK?
10. Q5 (D262): Lab's install by hand; Play's listener later by a script. **Superseded:** `install_gregteclink.py` does both (D287; G8 steps 1 and 11).
11. Q6: oversized chunks are sent as `append: true` census events. OK?
12. Q7: the machine readers wait once 1,000 events are queued; more than 1,000 log events while the core is down can still push queued events out (documented in D261). OK?
13. Q8: known chunk slots aren't saved across watcher starts, so each start re-reads the whole world. Keep that as the safety net, or save the slots if the Lab shows the cost?
14. Q9 (retarget the PR to `main`): **done**; it merged into `main`.

**PR #93** (WO-6.3), https://github.com/crazie450-prog/GretecOS/pull/93
15. Q1: the Activity view and the tier-up recap panel have no approved board (step 15).
16. Q2 (D265 (4)): an override above the detected tier opens a provisional age, taken back with its recap when the override is. OK?
17. Q3 (D264 (4)): after a core restart the idle close waits for the watchers' first post or 15 minutes; a quit's or crash's end block waits 3 minutes. OK?
18. Q4 (D263 (5)/(6)): a bad event is skipped and logged; `session`, `session_end` and `age_` vault keys belong to the scribe. OK?
19. Q5: the earlier calls in D263-D265 have the reviewer's agree verdicts. Noted.
20. Q6: Activity has no number key and is not a phone tab. Add one once the board is approved?
21. Q7: a chat line seen by both the log watcher and GregtecLink is stored twice (`source` `log` and `link`). Drop the link's chat from the store, or skip one copy?

**PR #82** (WO-6.4), https://github.com/crazie450-prog/GretecOS/pull/82
22. Q1 (R1): "what comes after X? spoil it" no longer raises the level; "spoil it" must be its own utterance. OK? Should "spoil it bro", "spoil it already", "you can spoil it", "go ahead spoil it", "tell me everything" or "spoil everything" join the closed set?
23. Q2 (R2): a bare "spoil it" after a general question (no quest card) asks which quest. OK?
24. Q3 (R2): a bare command opens at most 3 locked quests from the last card; "spoil <name>" needs the exact name or `q:` id. OK?
25. Q4 (R2): "spoil the secret alloy quest bro" asks back. OK?
26. Q5 (B1): in a tool-built quest card, every locked quest carries the tool's level. OK?
27. Q6 (D268): the deep lane never spoils. OK?
28. Q7 (D268): `db_sql` refuses quest text, task logic and repeat times below the full level (changes D133). OK?
29. Q8 (D268): at level 0, a locked quest whose name holds every word he said is pointed at by id, never by name. OK?
30. Q9 (D269): goal matching and the rank order. OK?
31. Q10 (D270): the Quests view needs his board check (step 15).
32. Q11: after a refusal, `quest_for_item` still lists a locked quest at L0 when he named the item (no name, no amount). Keep the carve-out?
33. Q12: an available quest the book hides is shown in full, because it is in reach. Blur it?
34. Q13: when several quests share a name, "spoil <name>" asks back. Pick the one nearest the frontier instead?
35. Q14: yes-fillers ("yes spoil it", "yeah, a bit more") keep the level. Add them?

**PR #98** (WO-6.5), https://github.com/crazie450-prog/GretecOS/pull/98
36. Q1 (D267 (5), D281): the advisor reads the alert engine's firing rows from the `alert` table. OK?
37. Q2: `[policy] notices` without `machine_stalled` no longer turns the stall off; `[alerts] rules` controls it (D280). Keep, or have the core drop the rule when the policy omits it?
38. Q3: the drain notice and `power_low` can both speak about one LSC. Should the drain notice skip a store whose `power_low` is firing?
39. Q4: stall steps and the priority gate read every binding's alert rows, Lab included. Filter to Play later?
40. Q5: if the engine's clearing write fails, the advisor keeps a cleared stall while the OC entry is fresh. A WO-7.5 follow-up (retry next tick)?
41. Q6: any `alert` push refreshes Next steps (one cheap extra read). OK?
42. Q7: the 60 s holds and the engine thresholds are still pending him (D280). His call.
43. Q8 (commit trailers): noted; history was not rewritten.

**PR #83** (WO-6.6), https://github.com/crazie450-prog/GretecOS/pull/83
44. Q1: his position from `level.dat` at the last save, labelled with its age; a read in a save's rename gap waits 0.25 s once, then reads `level.dat_old`. OK?
45. Q2: citations `vein:<dim>/<cx>/<cz>` and `pos:<world>@<save epoch>`. Confirm the `pos:` prefix and shape.
46. Q3: an `ore_waypoint(vein)` tool, deep lane only, instead of `action_propose` with model-given coordinates. OK?
47. Q4: waypoint y is the middle of the mix's heights; the name is `<mix> vein`, at most 32 characters. OK?
48. Q5: waypoints only into the configured Lab or Play world the veins came from. OK?
49. Q6: `ore-intel` is in the fast lane's cached prompt. OK?
50. Q7: the Ores view needs his board approval (step 15).
51. Q8 (L7): paired browsers can propose world actions; the dashboard replaces only a pending waypoint (409 for any other kind), an agent's proposal replaces any. Is that the split he wants?
52. Q9: `ore_find` gains a `mix_id` column and a JSON `ores` cell; the catalog ore card says "blocks". OK?
53. Q10 (commit trailers): noted.

**PR #86** (WO-6.7), https://github.com/crazie450-prog/GretecOS/pull/86
54. Q1 (D274): times are the best case for water and fertilizer and the worst case for the biome; without a Crop Manager the stats plan uses the low list and shows the ±2 alternative. Right default?
55. Q2 (D274): the faster-pool note appears when a pool path takes at most half the guide's time. Is 2 the right threshold?
56. Q3 (D274): rule constants cite the crop or machine they apply to; no `rule:` id kind. OK?
57. Q4 (D275): **his five target crops.** The PM's defaults are Sticky Cane, Ferrofern, Coppon, Bauxia and Fertilia. This one is part of WO-6.7's acceptance (step 13).
58. Q5 (D273): a chance is shown as its lower bound when a smaller recipe of the same parents may also match. OK?
59. Q6: clearing the target with no crop data saves and replies 404 "saved; ...". OK?

---

## Clayton's PC steps before signing G6

These run after G2's PC steps 0 to 3 (`docs/reviews/G2_review.md`: Node.js and pnpm, `phase1_pc_checks.md` steps 1 to 12 with the export and the DB build, the setup wizard and the API key), G4's step 1 (`docs/reviews/G4_review.md`: a fresh export and `gos db build` for gos-dev and for Clayton), G5's steps 0, 1 and 6 (`docs/reviews/G5_review.md`: the voice token, devices and keys, `gos voice run` answering), and G8's steps 0 and 1 (`docs/reviews/G8_review.md`: a green `main` deployed, then GregtecLink in Lab with `install_gregteclink.py`, which also turns WO-6.2's listener on). They don't repeat those steps, and they don't repeat the runbooks: each step names the runbook section to follow.

**Order with G7.** Run steps 0 to 11 here before G7's step 8: it needs the watchers token from step 1. Step 12 needs G7's steps 1 to 3 (the OC computer in `lab-ops`). **Step 9-10 here runs `ore_intel_check.md` A then B, as gos-dev with `lab_world = "lab-ores"`, so G7's step 10 (the waypoint, V23) is then skipped**, as G7's report allows.

`$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe`, `$app` is `C:\GregtecOS-app\current` and `$lab` is `C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft`, as in the runbooks. Clayton's steps use a normal PowerShell window in his own account (never "Run as administrator"); gos-dev's use gos-dev's dev clone `C:\GregtecOS-dev\GretecOS` with gos-dev's own `gos.toml` (its own `[core] port`, `supervisor_port` and `dashboard_host`, D115/D117). Every window: `$ProgressPreference = 'SilentlyContinue'`. **Everything up to step 15 is in GTNH-Lab.** Only step 16 touches Play, and only by reading it: the watchers that Play's own hook starts. In all, about 6 h, half of it the 2 h session and the crop guide.

Spending: steps 7 (quest eval, about $0.01, at most $0.10) and 11 (advisor eval, under $0.50) use the real model. Step 12's "what should I do next?" is one lane answer. "Next crop step" and the watchers make no model call.

**`[paths] lab_world`.** The Lab watchers read the save files of the world `[paths] lab_world` names in his `gos.toml` (finding 1). Each step below says which world; change it with Lab closed, before the launch.

**0. Deploy a green `main`** with `review/g6` merged, every CI job green on that commit (`d896ee1` is not, risk 1). In the profile clone: `git pull`, then `uv run gos deploy` (this covers G7's and G8's step 0 if he does them together). As gos-dev: `git pull` in `C:\GregtecOS-dev\GretecOS`, then `uv sync`.

**1. Watchers token and Lab hooks** (`watchers.md` §1). As himself, with GOS running:
```powershell
& $gos token new watchers
```
Leave `[startup] watchers_with_play` unset (the default `"lab"`). Lab's hooks are in (`play_hooks.md` step 3, `--instance lab`; skip if done for G7). Set `[paths] lab_world = "lab-progress"` for steps 2 to 5.

**2. Paused** (`watchers.md` §2, the WO-6.1 5 s acceptance, **V29**). Launch Lab, open `lab-progress`: `& $gos status` shows Game watchers and Voice running. Esc: **pass** "Game paused" within 5 s; note when `Saving and pausing game...` appears in `latest.log`. Stay in the menu 60 s: the chip stays. Close it: the chip clears within about 45 s. Quit: both stopped.

**3. The 2 h session** (`watchers.md` §4, the WO-6.1 acceptance, **V27**). Play Lab for 2 hours with the watchers running, pausing now and then; this can be the same sitting as steps 4 and 5. Then, with Lab closed, the runbook's `Select-String` over both logs and over `watchers.log`. **Pass:** no lines.

**4. Events** (`watchers.md` §3, **V05**, **V06**), in survival in `lab-progress`. Chat a line, pick up and drop items, change dimension, complete a quest, die once, prospect a vein; the day's spool shows them. Record which log carries `[CHAT]` (V05). With ServerUtilities logging on (`check_tweaks.py` shows `su_logging ok`), place and break a block, Esc: `Test-Path "$lab\logs\world.log"` is `True` and the spool has `block_placed` and `block_broken` (V06; paste a line if it differs).

**5. The scripted session** (`event_store.md` §2, the WO-6.3 acceptance; plus plan §9's L3). In survival in `lab-progress` (`lab_world = "lab-progress"`), do §2's steps 1 to 11, and to cover L3 too: place **5** GT machines (one a multiblock controller) and break 1, and visit the **Twilight Forest** as well as the Nether. Then `& $gos tool session_summary`, `& $gos tool events_recent '{\"window\": \"session\"}'` and the Activity view. **Pass:** recall ≥ 95% (each machine, quest, death, vein, dimension change, chat line and pause is one event), the session starts at the login and ends `quit`, the death counts once, and the machine events carry the ids NEI shows.

**6. Machines** (`machine_events.md` §2, the WO-6.2 acceptance, **V26**). Set `lab_world = "lab-structures"` with Lab closed. Launch Lab, open `lab-structures`: `latest.log` shows `listener module on`. Place 10 different GT machines (the EBF and an input bus among them), noting each NEI id; break 4 (the controller and the hatch among them). `Get-Content "$lab\gregteclink\events\machines.jsonl"`: 10 `machine_built`, then 4 `machine_removed`, each `mte_id` the id he noted. Esc, then:
```powershell
& $gos watch census --instance lab --world lab-structures
```
**Pass:** the 6 machines still standing, by `mte:` and name, plus what the world already had. Optional: §2 step 6 (TNT or `/setblock` air: gone from the census, no event).

**7. The quest eval with the real model** (`quest_guide.md` A-B, the WO-6.4 acceptance; about $0.01). Check the skill arrived (`quest_guide.md` step 2), then:
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\GregtecOS\data\reports" | Out-Null
& $gos eval quest-guide | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\quest-guide-eval.txt"
```
**Pass:** `passed: 20 of 20`, `spoiled: 0`, `Result: PASS`. A `spoiled` row is a guard bug: paste it.

**8. Quests on the Lab, and the real book** (`quest_guide.md` C-D). Set `lab_world` back to `"lab-progress"` if he changed it. With Lab running and paused:
```powershell
& $gos quests load --instance lab --world lab-progress
```
Then the Quests view (key 6) checks in `quest_guide.md` step 5: the blur, Spoil it and Back to nudge, "a bit more" and "spoil it" on their own opening only the asked quest, "what comes after it? spoil it" opening nothing. Then the real book, as gos-dev, with an extracted 2.9.0 pack (`quest_guide.md` step 6):
```powershell
$env:GOS_QUESTS_PACK = "D:\GTNH-2.9.0\.minecraft"   # his extracted pack
uv run pytest tests/integration/test_quest_guide_harness.py -k real_book
```
**Pass:** `1 passed`.

**9. Nearest veins** (`ore_intel_check.md` §0 and A, the WO-6.6 acceptance), **as gos-dev**, gos-dev's `gos.toml` with `[paths] lab_world = "lab-ores"`. §0 (switch to `main`, `uv sync`, the dashboard build), then A steps 1 to 3: `player_pos` and `ore_find` for Iron in the Overworld and the Nether, and fly to two rows. **Pass:** the order is primary, secondary, between, sporadic, each nearest first; F3's distance on arrival matches `distance` within a block or two; digging into `y_min`..`y_max` reaches the mix's ores.

**10. The waypoint** (`ore_intel_check.md` B, **V23**; WO-7.1's waypoint action, so G7's step 10 is skipped), as gos-dev in the same sitting. JourneyMap's **Enable Waypoint Manager** on first. `uv run gos serve`, Ores, Iron, Find; **Propose waypoint** on row 1, **Confirm**. **Pass:** the file appears in `GTNH-Lab\.minecraft\journeymap\data\sp\lab-ores\waypoints\`; the waypoint is not in the game until he goes through a portal and back, then it is, at the vein's centre. Then the Nether scale (B step 6).

**11. The advisor's three scenarios** (`advisor.md` A-B, half of the WO-6.5 acceptance; under $0.50). Check the skill arrived, then:
```powershell
& $gos eval advisor | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\advisor-eval.txt"
```
**Pass:** `advisor: 3 of 3 scenarios pass`. Then he writes `yes` or `no` in the `useful` column of `data\reports\advisor-<date>.tsv` for early, trouble and stocked, with a few words on any `no`. **The acceptance is three `yes`.**

**12. The notices in `lab-ops`** (`advisor.md` C, steps 5-10; the other half of WO-6.5, **V34**), after G7's steps 1 to 3 (the OC computer reporting as `oc-lab`). `[paths] lab_world = "lab-ops"`, `[policy] notices` left out, the dashboard open, the voice client running. C6 power deficit (the voice line and the Now alert within about 70 s); C7 machine stalled (**one** line and **one** card, Next steps updates without a reload both ways) and the EBF power cut (paste its `telemetry_get` row: V34; G7's steps 7-8 need no second paste); C8 buildable from stock; C9 "what should I do next?"; C10 the notice log lines.

**13. The crop guide** (`crop_guide.md` §0-§3, the WO-6.7 acceptance, **V30**, **V32**), **as gos-dev** in the dev clone (judgement 2), `uv run gos ...` as the runbook writes it. First he picks his **five target crops** (question 57; at least one needing a block under it and one needing the Crop Breeder), or keeps the PM's five. Then:
```powershell
uv run gos crops check
uv run gos crops check "<each of his picks>"
uv run gos crops guide "<each of his picks>"
```
**Pass:** 0 problems each, and he reads each guide (that is his review). Then §1: follow one guide to its target in `lab-progress` (water and fertilizer kept topped up), noting the times seen against the guide's. Then the V30 checks in §2 (weeds at Growth 24/25, extra neighbours, identical seeds, the Crop Manager past 100 with fertilizing switched on in its GUI, upkeep) and the V32 checks in §3 (the NEI page, the LV and MV tank caps, ten LV runs, Bauxia never from sticks). Once, in his own account with the voice client up, say "next crop step": it answers (from his own crop state, which this step left alone).

**14. The Crops and Quests views against their boards** (`quest_guide.md` E; WO-6.7's board check). Both boards are approved (D92), but both views were built without the canvas handoff: compare each with its board in Claude Design and note any differences.

**15. Boards still to approve** (D92, D158 (5)): the Activity view and its tier-up recap panel (after step 5's session; `event_store.md` §3 shows the recap: set the override one stage up, then Clear, and check Activity lists no new finished age and today's log has no `age_done:` line), the Ores view (step 10) and Now's Next steps (step 12). Approve each or redline it.

**16. Turn the watchers on for Play** (`watchers.md` §6), only after steps 2 to 4 passed. Add `[startup] watchers_with_play = "play"` to `gos.toml`, then `& $gos stop --all` and sign out and in (or `install_login_start.ps1 -StartNow`). The next Play launch prints `GOS: the game watchers and voice start (play).` After that first Play session: `watchers.md` §4.2's `Select-String` over Play's logs finds nothing, and `vault\log\<date>.md` has the session's block, whose `n_` counts match what he did (the journal, D263). If G8's step 11 has installed GregtecLink in Play, My base's machine counts follow his placements within a save (D261).

**17. Sign-off.** Answer question 57 (the crop picks, step 13), and say whether to keep each other default listed above.

**What to paste back for G6:**
- step 2's pause timings and when the log line appeared (V29);
- step 3's `Select-String` result (V27);
- step 4's chat log (V05) and `world.log` result (V06);
- step 5's recall (and any misses), the session's start and end;
- step 6's `machines.jsonl` lines and census output (V26);
- `quest-guide-eval.txt` (step 7) and anything in step 8 that differed;
- `ore_intel_check.md`'s "What to send back" (steps 9-10, V23);
- the rated `advisor-<date>.tsv` (step 11) and step 12's notice lines and the EBF row (V34);
- his five picks, the followed guide's notes and the V30/V32 results (step 13);
- the board notes (steps 14-15);
- step 16's first Play journal block;
- his answers to the questions above.

---

## Risks

1. **`main`'s head is red on the dashboard job.** `d896ee1`'s run 43588012389 failed `e2e/designer.spec.ts:135` (WO-3.7's contrast test: a react-flow node not visible within 10 s on a loaded runner). PR #86 saw it fail once too. It is not a Phase 6 test, but it can make the deploy commit's CI red. The fix belongs to WO-3.7's owner: wait for the layout before the contrast pass, or retry. (Confirmed twice)
2. **The wrong `lab_world` makes a Lab check fail for a reason that isn't the code's** (finding 1). The runbooks and the steps above now say so. (Possible, if steps are run out of order)
3. **Unpausing relies on a ticking line or the next autosave** (D258, PR #87 Q2). If 1.7.10 single player writes none of the allow-list's lines, the chip clears only at the autosave, up to 45 s after the menu closes. Correct, but slow. (Unknown until step 2)
4. **A chat line is stored twice** when GregtecLink is in (log and link copies, PR #93 Q7). Counts of chat events in a session read double. (Confirmed, by design until he answers)
5. **A supervisor restart mid-session leaves the watchers stopped** until the next launch (D259, known gap L7). (Confirmed, by design)
6. **My base reads Play only** (D261): until step 16, My base's counts come from manual entries and the census of Play's saves never runs. (Confirmed, by design)
7. **The crop guide's times are the best case** (D274): if water or fertilizer run dry, real times are longer, and the guide says so on each nutrients line. (Confirmed, by design)

## After the review

- The full `pytest`, the three harness runs, the Phase 6 integration tests, both fake evals, vitest and the Phase 6 Playwright specs all finished green here (tables above).
- No product code was changed. Runbook text was: `machine_events.md` and `ore_intel_check.md` (`05ecf3e`, finding 3), `event_store.md` and `machine_events.md` (`7688309`, finding 1). No decision row was added (D276 unused).
