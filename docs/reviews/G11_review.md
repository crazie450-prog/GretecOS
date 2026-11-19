# Gregtec OS: G11 gate review (day one)

2026-11-19 · independent gate reviewer (D41, D158 (2)) · reviewed: `origin/main`, brought up to date at `bb27a1b` (the verdict and the CI table rest on it; first reviewed at `2a961ed`: PR [#128](https://github.com/crazie450-prog/GretecOS/pull/128), WO-11.3, merged at 06:42 UTC; every work order of Phases 0-11, WO-10.9 and WO-3.9 on it; first written at `49c9e70` while #128 was open, and brought up to date after it merged), and the deployment checklist this branch stacked on (PR [#131](https://github.com/crazie450-prog/GretecOS/pull/131), head `1584f1e`). This branch (`review/g11`) is `1584f1e` plus this report, with `origin/main` merged in. Before this report's PR could merge, `main` moved to `bb27a1b`: PR #130 (WO-3.9's cache-key tests, tests only), #131 (the checklist, as reviewed here, plus D355's row in its decision list) and #132 (the core reads a refused request's body before it answers, a Windows fix, D355) merged; all three are in this branch. Open beside it, from other sessions: #114, #115, #117. The format follows G0-G10.

G11 in plan §8 is **"Day one: Clayton starts his survival 2.9 world"**, and WO-11.3's acceptance is "§10 checklist all green". This report gives, for every §10 line and every Phase 11 row, the evidence and one status:

- **met**: checked in the repo, CI or this container, nothing left on the PC;
- **met with fakes**: everything that can run without his PC is green; the real check is a named step of [`docs/deployment_checklist.md`](../deployment_checklist.md) (cited as "checklist 3.x.y");
- **not met**: something in code or in the repo is missing;
- **not merged**: built and green, but not on `main` yet.

Evidence tags, as in G0-G10: **[repo]** the repo, the git history or GitHub's CI logs; **[here]** run by this reviewer in the build container (Linux, 4 shared CPUs, Xvfb and Mesa, no Minecraft client of his, no voice, no model key); **[PR]** a PR's own description, not re-checked; **[PC]** needs Clayton's PC. Nothing from his PC is in the evidence yet.

## Verdict

**G11 is not met yet, and it can't be met in the cloud: day one is Clayton starting his survival world, after the PC sitting.** What the build can deliver is delivered: `main` is green, every Phase 0-11 work order except WO-10.0 (never run, see G10 below) is merged, the automated fake day one is **GREEN** (in CI on #128's and #131's heads, and here), and every §10 line is met with fakes **except two that are not met in code**:

1. **Q&A (§10 line 5): plan §9's wiki (15) and hover-item (10) evals don't exist** (`tests/evals` holds neither; D337 F3, G8 should-do 1). Either they are written (each case citing real `w:`/`r:` ids), or Clayton accepts the substitutes in checklist 5.5.1.
2. **Power (§10 line 3): a designed naquadah MkI chain at IV is not met** (G4, WO-4.8 acceptance item 2): MkI's only recipe is LuV fusion, and fusion isn't sized (D176, D328). Not needed on day one (late game); Clayton signs G4 knowing it, or holds it for a fusion-sizing WO (checklist 5.7.1).

And one new **should-do before Play's watchers go on** (finding 1 below): on Windows CI, the read-safety soak twice saw a BetterQuesting quest-progress save refused (`WinError 5`) while a watcher read the file. It is rare, needs a slow machine to show, and is exactly what open VERIFY V27 and checklist 3.10.3's 2-hour session test, but the PC session is the only thing between it and Play.

**What stands between `main` and day one, in order:**

1. **Merge this report** (WO-11.3 #128 and the checklist #131 are on `main` since `2a961ed` and `1127ee9`); deploy only a commit whose CI is all green (checklist 2.2).
2. **Close or accept the two red lines above** (Clayton's answers 5.5.1 and 5.7.1, or two small WOs).
3. **Decide finding 1** (a short follow-up WO, or accept it on the strength of checklist 3.10.3's 2-hour session and V27).
4. **The PC sitting**: checklist sections 2 and 3 (deploy, the wizard, every gate's Lab and PC checks, the paid evals: about 30-35 hours over several days, up to about $80 of API spend), then section 4, the 2-hour fake day one in the Lab (§10's last line).
5. **His decisions** (checklist section 5, over 130 rows, each with its default in force, and 14 board approvals, 5.10) and **the gate sign-offs G1-G10** (checklist section 6), in the same sitting (D158 (2)).
6. **Then** the steps that touch Play (checklist 3.15, his scripts from GOS_APP), and he starts the survival world.

### This reviewer's runs [here]

All on this branch (`1584f1e` plus this report and its guard test). #128 merged into `main` without changing its tree since `fe3641b`, the head these runs held; ruff and the full pytest were run again after `origin/main` (`2a961ed`) was merged in ("After the review").

| Check | Result |
|---|---|
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 668 files already formatted (before this report's test; re-run at the end, below) |
| `./gradlew --no-daemon --max-workers=2 build` | ok (1 min 8 s) |
| `./gradlew ... runServer -Pharness` | `harness passed: 37 checks, run 20261119T045853Z` |
| `./gradlew ... runServer -Pharness -PharnessListener` | `harness passed: 19 checks, run listener` |
| `xvfb-run ./gradlew ... runClient25 -Pharness` | `harness passed: 63 checks, run 20261119T050112Z` |
| **The automated fake day one**: `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration/test_fake_day_one.py -s` (the real supervisor, core, watchers, voice client and Discord bot on a fake Prism tree and a GOS_HOME built from the client run above; the full 600 s soak; the G3, G4, pack-update and supervisor hard-kill suites beside it) | **1 passed in 823.70 s; `Rehearsal: GREEN`.** Every automated item green: `suite_g3` 5 passed, `suite_g4` 19 passed, `suite_pack_update` 1 passed, `suite_supervisor_crash` 1 passed, `backup_restore`, `costs`, `shutdown` (every process stopped), `play_untouched` (the fake Play instance byte-identical). The report is the same, line for line, as CI's below |
| WO-11.1 on the harness DB: `GOS_ADDON_WATCHER_HARNESS=1 pytest tests/integration/test_addon_watcher_harness.py` | 1 passed (2 min 26 s): `test_an_addon_jar_swap_gives_the_diff_note_and_flags_the_plans_it_affects` |
| WO-11.2 on the harness DB: `pytest tests/integration/test_backup_restore_harness.py` | 2 passed: `test_the_real_dbs_backup_restores_and_checks`, `test_a_corrupted_copy_of_the_real_backup_fails` |
| `gos doctor` in a scratch GOS_HOME | exit 0; VERIFY: 26 open items (V03-V07, V13-V16, V18-V20, V22-V35; V01/V02 read "open" here only because the container has no Prism), the same 26 as checklist 3.16 |
| full `pytest -n 2 -m "not serial"`, then `-m serial` (as CI) | see "After the review" |

No DB, export, token or keyring entry from these runs is committed; they stay in the session's scratch folder and the worktree's ignored `mod/run/`.

### CI [repo]

| Commit | Run(s) | ubuntu | windows | mod | dashboard | dashboard (Windows) |
|---|---|---|---|---|---|---|
| `2ce2724` PR #112 head (11.1) | 44391014397, 44391014847 | success | success | success | success | success |
| `b5b5005` #112 merged | 44401852782, 44401852764 | success | success | success | success | success |
| `cd46648` PR #123 head (11.2) | 44395526177, 44395526797 | success | success | success | success | success |
| `e61c437` #123 merged | 44401892676, 44401892712 | success | **failure** (`test_soak`, finding 1) | success | success | success |
| `49c9e70` `main`'s head then | 44450704739, 44450704650 | **success** | **success** | **success** | **success** | **success** |
| `1584f1e` PR #131 head (holds #128) | 44458253790, 44458253784 | success | success | success (the fake day one ran in it; artifact `fake-day-one`: `Rehearsal: GREEN`) | success | success |
| `fe3641b` PR #128 head | 44460564006 | success | success | success (24 min) | success | |
| `2a961ed` `main`'s head then, #128 merged (same tree as `fe3641b`) | 44474143125 | success | success | success (27 min; the fake day one ran in it, artifact `fake-day-one`) | success | |
| `7de85a7` this report's PR #133 (`2a961ed` + #130 + this report) | 44477661631 | success | success | success (re-run: the first attempt hung 38 min in `apt-get update` before its tests and was cancelled; the re-run took 25 min) | success | |

**Is `main` green? Yes at its head** (`bb27a1b`, #132 merged: all four jobs, run 44485084155; before it `1127ee9` (#131) run 44479240411 and `8d1aee6` (#130) run 44475783181, all green; `2a961ed` (#128): all four jobs, run 44474143125). `49c9e70` had all five jobs green; `fe3641b`, whose tree `2a961ed` is, had all four of its jobs green. But `checks (windows-latest)` failed on 4 of the last 9 pushes to `main`, each time on a different single test (none failed twice): `test_soak_no_handle_held_no_write_blocked` (`e61c437`, finding 1), `test_ask_errors_say_whether_the_core_answered` (`6aa5d6f`, `WinError 10053`), `test_the_real_hook_sees_an_injected_key` (`c2624a6`), `test_saying_confirm` (`fa2f8ff`, `tests/security/test_oc_bridge_api.py`). A scan of the last 150 failed CI runs on any branch adds these repeat offenders: `tests/unit/watchers/test_census.py::test_a_chunk_too_big_for_one_event_is_split_and_reaches_my_base` (Windows, `2000 == 2003`, twice), `tests/unit/alerts/test_core_routing.py::test_the_paused_game_comes_from_the_watchers_feed` (Ubuntu, twice), `tests/unit/discordbot/test_core_api.py::test_errors_carry_the_core_s_status_and_reason` (Windows), `tests/unit/test_extractor.py::test_run_tree_stops_what_the_command_started` (Ubuntu, twice), `tests/unit/test_appwindow.py::test_the_foreground_program_on_windows` (Windows). Checklist 7.3 now lists all of them (should-do 3, done in this PR). Checklist 2.2's rule stands: a red job is re-run until green, never deployed over.

---

## Plan §10, line by line

The fake day one's report (C1-C12) gives each line's automated items green and the rest "PC only"; a line is done when every automated item is green **and** its PC steps pass (fake_day_one.md J). Here, every automated item is green on CI (`1584f1e`) and [here].

| # | §10 line | Status | Evidence | The real check |
|---|---|---|---|---|
| 1 | All 4 addons on 2.9-compatible releases; Play built (WO-0.7), its fingerprint matches the export; the pack-update playbook passed its dry run (WO-1.9) | **met with fakes** | `suite_pack_update` green (a simulated 2.9.x walked end to end, L10); WO-1.9 #47, WO-11.1 #112; G0 signed (D99) with Play built; `gos pack watch` (V18 stays a weekly watch, D146) [repo, here] | checklist 3.1, 3.4.1-3.4.2, 3.15.1 (a recorded Play export, so A3 compares a fingerprint), 4.A |
| 2 | Designer: G3 benchmark lines; pins and deterministic ranking; exports open in gtnhplanner.com; plan → vault → ledger; batch; My base feeds the boundary | **met with fakes** | `suite_g3` 5 passed; `design_pin_rank`, `design_export`, `design_accept`, `design_batch`, `my_base` green [here, CI]; G3 report; WO-3.9 #129 (design jobs and the planner cache). Open from G3: `task_max_usd` (a paid run), 3 lines outside ±2% of gtnhplanner.com (polyethylene, silicon, platline: the Lab decides) | checklist 3.6, 4.D |
| 3 | Power: the 4 power benchmarks end to end (G4); 4 modes reviewed at LV/HV/IV; ledger and storage sizing | **not met** (one part); the rest met with fakes | `suite_g4` 19 passed; `power_design` green; G4 finding 1 fixed by WO-4.7 (#101, D323: the light-fuel and benzene strict xfails are gone from `tests/integration/test_g4_lines_harness.py`); 12 of 12 power-mode cases pass (WO-4.6, `gos eval power-modes`). **Not met: a designed naquadah MkI chain at IV** (G4 addendum 2026-11-14, WO-4.8 acceptance item 2, D328). The light-fuel loop passes only under D324's reading A (decision 5.1.1) | checklist 3.7, 4.D; Clayton's answers 5.1.1, 5.7.1 |
| 4 | Voice: wake word ≤ 1 false wake/h with ≥ 38/40 hits; talk key backup; entity accuracy ≥ 95%; latency per PM14; pronunciation and speech filter; personas switch (`gt-veteran` default) | **met with fakes** (synthetic runs below target: risk, decided on the PC, checklist 3.8) | `voice_wake`, `voice_talk`, `persona_switch` green; G5 report (all seven WOs met with fakes). Risks carried from G5: synthetic speech gave 34/40 wakes (target 38/40), Whisper base.en on the CPU 86% entity accuracy, Kokoro on the CPU missed 400 ms (Piper met it) | checklist 3.8, 4.C, 4.H, 4.I |
| 5 | Q&A: recipe, quest, wiki, screen, hover and ambiguous-item evals pass; no-AI mode works (L9) | **not met** (two evals missing); the rest met with fakes | `ask_recipe`, `ask_quest`, `ask_deep`, `l9_key` green; eval files: see §9 below. **No wiki and no hover-item eval file exists** (checked here; D337 F3). The real-model runs are paid PC steps; a revoked (not removed) key is PC only (4.H3b) | checklist 3.5, 3.9.5, 3.10.7, 4.C, 4.H3/H3b, 4.I3; Clayton's answer 5.5.1 |
| 6 | Context: watchers stable for 2 h with no save errors; listener and census agree; journal and advisor work; default notices fire | **met with fakes**, with finding 1 | `soak` (600 s time-compressed, no save blocked on Linux), `game_events`, `journal` green [here, CI]; G6 report. Finding 1: on Windows CI the soak twice saw a quest save refused. Known gap D337 F1 (lines lost if a log rotates while the watchers are down); D259 L7 (a supervisor restart mid-session leaves the watchers stopped) | checklist 3.10.2-3.10.6, 3.10.11-3.10.12, 4.B, 4.E, 4.I1 |
| 7 | Crops: guides for his 5 target crops reviewed; one followed to its target in `lab-progress`; Crop Manager and Crop Breeder steps checked; the breeding station bred a target unattended in `lab-ops` (WO-7.6) | **met with fakes** | `crop_step` green; G6 (WO-6.7) and G7 (WO-7.6) reports. His five crops are still the PM's picks (decision 5.1.4) | checklist 3.10.13, 3.11.9, 4.F |
| 8 | OC bridge: config tweak, Lua agent, M1 mailbox and confirmed actions (read-back, one pending, 60 s) in `lab-ops`; AFK alerts while unpaused (L4); in survival M1 at MV, M2 at HV/EV | **met with fakes** | `oc_action`, `oc_expiry`, `alert_discord` green; G7 report (WO-7.1 met, 7.2-7.6 met with fakes). M2 on Play stays off by default (D214, decision 5.2.3) | checklist 3.11, 4.E; Play: 3.15.5 |
| 9 | Discord and phone: commands, alerts, credit DMs and away-only offline DMs from his phone; phone dashboard over Tailscale (L8) | **met with fakes** | `discord_commands`, `phone` green; G9 report | checklist 3.12, 4.G, 4.H |
| 10 | Build: footprints for the planned multis; 3D guide; ≥ 2 themes; a Claude-designed shell pasted in the Lab | **met with fakes** | `build_guide` green; G10 report; **G10's defect 1 is fixed on `main`** by WO-10.9 (#127, `fa2f8ff`, D350-D351): `gos footprints capture` takes a worldedit `.schematic` (`core/gregtec/build/capture.py` `from_schematic`). Two starter themes ship (`th-foundry`, `th-marble-works`) | checklist 3.13, 4.D, 4.I |
| 11 | Ops: supervisor with login start and Play-linked voice and watchers; DB and off-instance world backups restored once; Claude Code isolation re-tested; cost dashboard | **met with fakes** | `processes_up`, `play_linked`, `session`, `crash_core`, `crash_watchers`, `crash_voice`, `crash_discord`, `suite_supervisor_crash`, `backup_restore`, `costs`, `play_untouched` green; WO-11.2 (#123) | checklist 3.14.1-3.14.6, 4.A |
| 12 | A 2-hour "fake day one" in the Lab with everything on | automated: **met with fakes**; the 2-hour session: PC | the automated rehearsal GREEN on CI (`1584f1e`, run 44458253790, artifact `fake-day-one`; `fe3641b`, run 44460564006) and here (823.7 s); WO-11.3 (PR #128) merged as `2a961ed` | checklist section 4 ([`fake_day_one.md`](../runbooks/fake_day_one.md) A-J) |

Plan §9's Lab scenarios, from the same report: L2-L6 and L8-L10 are green in the rehearsal (their real runs are PC); L1 (projector builds and captures) and L7 (performance with everything on) are PC only (checklist 3.13.2, 3.8.11).

## Phase 11 work orders

### WO-11.1: Addon watcher end to end

> **Acceptance:** "Swapping an addon jar version gives a correct diff note and flags the plans it affects"

**Verdict: met with fakes.** PR #112, head `2ce2724`, merged as `b5b5005` (CI green on both). Decisions D329-D331. Runbook [`addon_watcher.md`](../runbooks/addon_watcher.md).

| Part | Status | Evidence |
|---|---|---|
| A jar swap gives the diff note | met with fakes | `test_addon_watcher.py::test_an_addon_jar_swap_end_to_end`; `test_addon_watcher_harness.py::test_an_addon_jar_swap_gives_the_diff_note_and_flags_the_plans_it_affects` on the harness DB, 1 passed [here] |
| Flags the plans it affects; clears a plan fine again | met with fakes | the same; `test_a_plan_fine_again_is_cleared` [repo] |
| In Lab, then in Play | **PC** | checklist 3.4.2 (Lab, closes V18's watcher half), 3.15.2 (Play, optional, only while Play has no survival world) |

Open: D329-D331's questions (checklist 5.2.7, 5.6.11). Gate lows still true on `main`: `export_diff` not a registered tool (checklist 7.2, checked).

### WO-11.2: Ops

> **Acceptance:** "A restore from backup works; the relocation procedure is documented (no server work before day one, D45)"

**Verdict: met with fakes.** PR #123, head `cd46648`, merged as `e61c437`. Decisions D332-D335. Runbooks [`ops.md`](../runbooks/ops.md), [`relocation.md`](../runbooks/relocation.md).

| Part | Status | Evidence |
|---|---|---|
| **A restore from backup works** (DB and vault) | met with fakes | `gos backup restore --verify`; `tests/unit/ops/test_backups.py` (restore, flipped byte, truncated DB, row counts, manifests, damaged vault zip, escaping entries); `test_backup_restore_harness.py` 2 passed on the harness DB [here]; the rehearsal's `backup_restore` green [here] |
| World backups restore | met with fakes; **PC** | `scripts/verify_world_backup.py`, `tests/unit/ops/test_world_backup.py` [repo]; on his PC: checklist 3.14.2-3.14.3 (Lab's world only) |
| Full crash recovery | met | `tests/unit/ops/test_crash_recovery.py` (every writer killed, the supervisor killed hard); the rehearsal's `crash_*` steps [here] |
| Cost alerts | met with fakes; **PC** | D334 (50% heads-up, $10 day cap, task cap); the Costs view's caps card (board, 5.10.14); checklist 3.14.5 |
| **The relocation procedure documented** | **met** | `docs/runbooks/relocation.md`, `gos relocate check` (read only), `tests/unit/ops/test_relocation.py`; no server code (D335, D45) [repo] |

Gate lows still true on `main`: the curator's manifest backfill ignores the deadline and catches only `sqlite3.DatabaseError`; the `PRAGMA synchronous=FULL` test passes without the line; the task-cap alert shows the deep lane's estimate (checklist 7.2).

### WO-11.3: Full Lab rehearsal

> **Acceptance:** "§10 checklist all green"

**Verdict: not met.** The rehearsal is met with fakes and merged; "§10 all green" is not met (two lines not met in code, above; every PC item still to come). PR #128, head `fe3641b` (gate passed at `fd12d2d`), merged as `2a961ed`. Decisions D336-D337. Runbook [`fake_day_one.md`](../runbooks/fake_day_one.md).

| Part | Status | Evidence |
|---|---|---|
| The automated fake day one (`tests/rehearsal/day_one.py`, `tests/integration/test_fake_day_one.py`, 10-minute soak in the `mod` job) | **met with fakes** (merged) | `Rehearsal: GREEN` on CI (`1584f1e`, `fe3641b`) and here; the smoke on Ubuntu and Windows (`tests/unit/test_fake_day_one_smoke.py`, `test_fake_day_one_parts.py`) green on `1584f1e` [repo] |
| Its report gives each §10 line as green, red or PC only | met | the `fake-day-one` artifact; reproduced here identically |
| The real 2-hour Lab rehearsal | **PC** | checklist section 4 |
| §10 all green | **not met** | §10 lines 3 and 5 above; D337 F1 and F3 |

`mod` job time on #131's head: 27 min 44 s of its 60 (the rehearsal takes about 14 min on its own here (823.7 s) and about 20 min of one of the job's 4 xdist workers in CI, as D337 (9) says; it runs beside the other integration files).

### G11: Day one

> **Acceptance:** "Clayton starts his survival 2.9 world"

**Verdict: not met** (his). The path is "What stands between `main` and day one" above, and the checklist.

---

## Plan §9's eval list against `tests/evals`

Counted here (data rows, comment and header lines left out).

| §9 eval | Size asked | Where it is | Size there | Status |
|---|---|---|---|---|
| recipe/item Q&A | 50 | `tests/evals/recipe_lookup.tsv` (`gos eval recipe-lookup`) | 50 | exists |
| quest | 20 | `tests/evals/quest_guide.tsv` (`gos eval quest-guide`) | 20 | exists |
| designer | 10 | `tests/evals/design_tasks.tsv` (`gos eval design-cost`); also `deep_lane_tasks.tsv` (10) | 10 | exists |
| screen-reading | 5 | `tests/evals/screen_cases.tsv` (`gos eval screen`) | 5 | exists |
| hover-item | 10 | none. The client-link harness's `whats_this_10_items` check (mod `ClientLinkHarness.java`) looks at 10 items in the dev runtime, deterministic, no model | 0 | **gap** (D337 F3; decision 5.5.1) |
| wiki | 15 | none (the wiki tools have unit tests under `tests/unit/wiki/`, no eval) | 0 | **gap** (D337 F3; decision 5.5.1) |
| ambiguous-item | 10 | `tests/evals/fast_lane_questions.tsv`, the 10 rows of set `ambiguous` (`gos eval fast-lane`: "ambiguous rows that asked: 10 of 10") | 10 | exists (inside the fast-lane file, not its own) |
| validator | 20 | not a `tests/evals/*.tsv`: the twenty normalisation cases in `tests/unit/planner/test_validate.py` (`test_twenty_normalisation_cases`, WO-3.5), run in every CI job | 20 | exists (as a unit test; it needs no model, so it is not an eval TSV) |
| voice utterances (entity accuracy, WER secondary) | 50 | `tests/evals/stt_utterances.tsv` (`gos voice eval`) | 50 | exists |

Also there, beyond §9: `fast_lane_questions.tsv` (60), `power_modes.tsv` (12, WO-4.6), `tts_phrases.tsv` (40), `wake_phrases.tsv` (8), `wake_chatter.txt`, `advisor_scenarios.py` (3), `design_tokens.tsv`. **Every model-scored eval has run only against fakes**: no real-model result exists on his key (checklist 7.1 item 7; 5.3.1 lists the paid runs). §9's rule that `gos eval` runs for every PR touching agents, prompts or skills could not be followed by any cloud PR (no key, D158): checklist 3.5 is the first real run.

## Every gate report's "not met" items, and what closed them

| Gate | Not met (or blocking) in its report | Closed since? |
|---|---|---|
| G0 | backup job unproven; Claude Code's refusal unevidenced; V06 | G0 **signed** (D99, 2026-11-06); the backup job is checklist 3.2.5 (D97: before the survival world); isolation re-test 3.14.4; V06 moved to WO-6.1 (still open, checklist 3.10.4) |
| G1 | WO-1.8 "≥ 250 base controllers" never shown (V09) | **closed** in the dev container by WO-10.2 (#116): 297 controllers, 455 variants, V09 resolved; his own dump is checklist 3.3.5 |
| G2 | nothing in code | — (real-model numbers and boards are PC) |
| G3 | `task_max_usd` (paid); 3 lines outside ±2% of gtnhplanner.com | **open**: both are PC (3.6.3, 3.6.6-3.6.7); QA's slow-design defect closed by WO-3.9 (#129) |
| G4 | finding 1 (EU not costed: light-fuel and benzene lines); finding 2 (naquadah generator-side only) | finding 1 **closed** by WO-4.7 (#101, D323); finding 2 **closed for the reactor** by WO-4.8 (#105, D326-D327); **a designed MkI chain at IV stays not met** (D328) |
| G5 | nothing in code (synthetic STT 86%, wake 34/40 are risks, not code) | — |
| G6-G9 | nothing in code | — (G7 should-do 3, the deep-lane import cycle, fixed by #125) |
| G10 | defect 1 (a `.schematic` capture can't become a footprint); WO-10.0 not run | defect 1 **closed** by WO-10.9 (#127, D350-D351); WO-10.0 still not run (its SOW isn't in the repo; decision 5.11.9) |

## Findings

1. **Should-do (before checklist 3.15.4 turns Play's watchers on): on Windows a watcher read can refuse a game save.** `tests/unit/watchers/test_soak.py::test_soak_no_handle_held_no_write_blocked` failed on Windows CI twice within 20 minutes (run 44399148587 on `wo/10.8-build-guide`, two errors; run 44401892676 on `main` at `e61c437`, one), both times `quests: PermissionError [WinError 5] Access is denied` when the fake game replaced `betterquesting\QuestProgress\<uuid>.json`. `safe_open` opens with read, write and delete sharing (`core/gregtec/gamestate/safe_open.py`), which lets the game rename the file being read, but Windows still refuses a replace **onto** a file someone has open; the soak's docstring names this case. A read only collides with a save that starts while it is open (milliseconds, after the file held still for 1 s, D257), which is why it shows only on a loaded runner, and BetterQuesting saves again at the next autosave. But a refused save on Play is a lost write, and V27 ("`safe_open` lets save-by-rename succeed while GOS reads") is open. Not fixed here: it isn't a small defect inside one acceptance, and the fix is a design choice (for example, for the files games replace, read only when the parent folder's newest change is old enough and retry the read rather than holding it; or a copy through a backup-semantics handle). Recommend: a short follow-up WO that first establishes how GTNH's BetterQuesting really writes `QuestProgress` (in place, or a replace, and whether it retries; reading its source to learn the behaviour is fine): the fake game models a replace, so an in-place write makes the risk on Play smaller and a replace with no retry makes it larger; then reproduces it on Windows (the soak with a forced long read) and picks the fix, before 3.15.4; until then checklist 3.10.3's 2-hour Lab session plus a check of Lab's `fml-client-latest.log` for BetterQuesting save errors is the evidence. The checklist now carries it: the G11 row's known-red list, a precondition on 3.15.4 ("G11 Q2 answered") and decision 5.2.11, whose default is the follow-up WO.
2. **Should-do: §10 line 5 can't be green without the two eval files** (above). Writing them needs real ids from the DB (`w:` revids from his wiki crawl, which is itself gated on the licence, checklist 3.9.6), so the wiki one can only be finished after 3.9.6; the hover one can be written against the harness DB now.
3. **Should-do, done in this PR: the flake list in checklist 7.3 was short of what CI shows.** Added `test_soak` (Windows, finding 1), `test_saying_confirm` (Windows), `test_census.py::test_a_chunk_too_big_for_one_event_is_split_and_reaches_my_base` (Windows), `test_the_paused_game_comes_from_the_watchers_feed` (Ubuntu), `test_errors_carry_the_core_s_status_and_reason` (Windows), `test_run_tree_stops_what_the_command_started` (Ubuntu), `test_the_foreground_program_on_windows` (Windows), and, new with WO-3.9, `tests/unit/planner/test_design_job_api.py::test_the_designer_job_api` (Windows: the core's slowest health-plus-status pair while a design job's busy loop runs took 1.032 s, then 1.125 s, then 1.015 s on three pushes in a row, against the test's 1.0 s bound; runs 44490420135, 44493853838 (first attempt) and 44498321042 on this report's PR, with no product code changed between them and the run before, 44487516906, green; `main` at `bb27a1b` green. It failed on three pushes in a row, so it is more than a rare flake: a timing bound that a loaded Windows runner with 4 xdist workers can miss. Recommend a short WO-3.9 follow-up that measures this in a `serial` test or widens the bound, rather than re-running it.). Also new on this PR: `tests/unit/tools/test_tool_ops.py::test_telemetry_get` (Windows, the re-run of run 44493853838: the telemetry reading's expected `\t0\t0\t` age and stale columns were not there on a slow runner; it passed on every other run). `test_run_tree_stops_what_the_command_started` failed again on Ubuntu (run 44498321042). Each passed on re-run; none is in a §10 acceptance path except finding 1. `test_ask_errors_say_whether_the_core_answered` (`WinError 10053`, then `10054` on #128's head `fe3641b`) was fixed by PR #132 (D355, the core reads a refused request's body before it answers), merged as `bb27a1b`; its checklist row says so.
4. **Noted, not a defect:** WO-11.3 (#128) and the checklist (#131) are both on `main` now (`2a961ed`, `1127ee9`), so `main` has `tests/rehearsal/`, the `fake-day-one` artifact and `docs/deployment_checklist.md`; this branch adds only the report, its guard test, D339 and the checklist's G11 row link.

No product code was changed by this review. The report's judgement calls are D339; its guard test is `tests/unit/test_g11_review.py`, and the checklist's G11 row now links it.

## The project manager's follow-up notes, against `main`

Each item the PM listed (note 6) that is still true on `main`, and where it is accounted for:

| Item | Still true? | Where |
|---|---|---|
| G4 not met on the naquadah MkI chain (fusion not sized, D176/D328) | yes | §10 line 3; checklist 5.7.1, 6 (G4), 7.1 item 1 |
| WO-4.8's acceptance item 2 (a designed MkI chain at IV) | yes, not met | the same |
| The agent-model choice (`[models]`, D155) | yes, pending Clayton | checklist 5.3.2 |
| The D223 Play-safety threat model | yes, pending Clayton | checklist 5.2.1 |
| The D268 spoiler rule (closed set, "now" filler, whole-word names) | yes (`core/gregtec/quests/spoilers.py` keeps "now"; `quest_search.sql` orders locked quests by bm25) | checklist 5.1.5, 7.2 |
| D298-D303 rows still "Pending reviewer" | yes (D298-D301, D303; D302 is a board question) | checklist 7.4 (the log is append-only; a later PM row records the verdicts) |
| Board approvals owed for views built without a board (D92) | yes, 14 rows | checklist 5.10 |
| The paid eval runs | yes, none run | checklist 1, 5.3.1; §9 above |
| The GPL phonemizer/espeak-ng runtime dependency | yes: `pyproject.toml`'s `tts` group (`kokoro-onnx`) pulls them in at install, never vendored | checklist 5.4.5 |
| RTG fuel values (EU per pellet as GT++ makes it, not NEI's x 365 "days") | yes, as built (D233) | checklist 5.1.13 |
| The usage and pace question (credit reset day, $10 day cap, 50% heads-up, D334) | yes, defaults in force | checklist 5.3.3 |
| Unverified gate lows (followups list) | each still-true one re-checked by the checklist's author | checklist 7.2 (**checked** rows), 7.3 |

## VERIFY items G11 touched

None closed here; every open item is a PC result. The 26 open items (V03-V07, V13-V16, V18-V20, V22-V35) and the checklist step that closes each are checklist 3.16; `gos doctor` [here] lists the same 26. V27 gains finding 1 as Windows CI evidence that the risk is real, not just theoretical.

## Open questions for Clayton

Every question still pending is in checklist section 5 (with its default) and section 6 (sign-offs). This review adds three:

1. **(5.5.1, sharpened)** The wiki (15) and hover-item (10) evals: have them written (the hover one now, the wiki one after your crawl), or accept `whats_this_10_items` plus `gregteclink_client.md` B.5 and the wiki answer of 3.9.7 in their place?
2. **(finding 1, checklist 5.2.11)** Open the Windows save-collision WO before Play's watchers go on (3.15.4; the default), or accept it on the 2-hour Lab session's evidence?
3. **(5.7.1, unchanged)** Sign G4 with the MkI chain as a known late-game gap, or hold it for a fusion-sizing WO?

## Clayton's PC steps

All of them are [`docs/deployment_checklist.md`](../deployment_checklist.md): section 2 (deploy and the wizard), section 3 (every gate's PC and Lab steps, in dependency order), section 4 (the 2-hour fake day one: [`fake_day_one.md`](../runbooks/fake_day_one.md) A-J, with the `fake-day-one` artifact of the deployed commit's CI run beside you for step J), then 3.15 (Play, last). Paste each step's output into this report's PR, or a follow-up, in step order; J ticks §10 line by line against the table above.

## Risks

1. **Windows-only behaviour is checked only by CI's Windows runner** (no Windows machine here): finding 1 and the Windows flakes are where it shows. The PC sitting is the first long Windows run.
2. **Every quality number is from fakes** (G2 risk 2): the first real-model runs are in the sitting, and a regression there blocks only the line it belongs to.
3. **The sitting is long** (about 30-35 h over several days, up to about $80, checklist 1): a redeploy mid-way means the fake day one's reuse rule (checklist 4) no longer holds for what changed.
4. **Unmerged work at review time:** other sessions' #114, #115, #117 may change things before they land; re-check CI on the commit he deploys.

## After the review

- On this branch with `origin/main` (`2a961ed`) merged in [here]: `ruff check .` all checks passed; `ruff format --check .` 669 files already formatted; `pytest -n 2 -m "not serial"` **11137 passed, 346 skipped** (7 min 52 s); `pytest -m serial` **7 passed, 4 skipped** (2 min 9 s). `tests/unit/test_g11_review.py` and `tests/unit/test_deployment_checklist.py`: 12 passed.
- Again after `origin/main` at `bb27a1b` (#131, #132) was merged in [here]: ruff clean; 670 files already formatted; `pytest -n 2 -m "not serial"` **11179 passed, 346 skipped**; `pytest -m serial` **7 passed, 4 skipped**.
- After the review of PR #133 (finding 1 carried into the checklist, 7.3 completed, the status check made exact) [here]: ruff clean; 670 files already formatted; `pytest -n 2 -m "not serial"` **11179 passed, 346 skipped**; `pytest -m serial` **7 passed, 4 skipped**.
