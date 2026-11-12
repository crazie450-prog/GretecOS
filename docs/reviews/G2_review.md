# Gregtec OS: G2 gate review (Phase 2, text Q&A)

2026-11-12 · independent gate reviewer (D41, D158 (3)) · reviewed: `origin/main` at `b7b88a0` (PR #67), covering the Phase 2 PRs #17, #30, #34, #36, #38, #39, #41, #48, #49, #53, #54, #58 and #60, plus open PR #71 where it fixes a Phase 2 defect

## Verdict

**G2 is ready for Clayton's sign-off at the deployment test, with fakes standing in for every real-key and real-screen check.** All ten Phase 2 work orders are built and merged, and every acceptance criterion that can be checked without the Anthropic key, the PC's monitor or the real export is met here, on CI or both. Nothing is "not met" in code. What remains is the PC sitting that D158 (3) batches:

1. **Real-model numbers, all paid PC runs:** WO-2.3's P50 time to first token (target ≤ 1.2 s), WO-2.4's 10-task cost table, WO-2.7's ≥ 95% correct with 0 uncited numbers, and WO-2.5's D141 token count (free, but needs the key). Each passes with fakes here; none has a real-model run yet.
2. **Real-screen and board checks:** WO-2.6 "matches the approved mockups" can't be checked in the cloud. D158 (5) had the dashboard built from `docs/design/system/` without the canvas handoff, so Clayton compares it with the boards. Chat, the Ctrl+K palette, Costs, the no-AI and credit banners and the wizard's look all need his board approval (D92).
3. **Fix before sign-off:** the phone Settings + Health view is 711 px wide on a 390 px screen, a WO-2.6 defect found by the post-W4 QA sweep. Open PR #71 fixes it with an e2e check. Merge #71 first.
4. **Main's CI is not green on `b7b88a0`:** `checks (windows-latest)` was cancelled after 6 h (run 43084303461). pytest had finished with 1 failure in a WO-5.2 timing test (`test_stt_cli.py::test_record_saves_one_talk_key_segment_per_line`), then the process never exited. This is Phase 5 code, not Phase 2. The commit before it (`efaef2f`, run 43084265062) is green on all four jobs. Still, G0's lesson holds: don't merge over a red Windows job.

Evidence tags, as in G0:
- **[repo]:** this reviewer checked it in the repo, the git history or GitHub's CI logs.
- **[here]:** this reviewer ran it in the build container on `b7b88a0` (Linux).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC. Nothing from the PC is in the evidence yet for Phase 2.

### This reviewer's runs on `b7b88a0` [here]

| Check | Result |
|---|---|
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 305 files already formatted |
| `uv run --locked pytest -q` | **4765 passed, 192 skipped** (4 min 17 s); the supervisor test printed `core restarted 3.78 s after the kill` |
| Phase 2 suites with `-rA` (`test_fast_lane_eval`, `test_recipe_eval`, `test_deep_lane_cli`, `test_no_ai`, `tests/security/test_core_api.py`, `test_supervisor`, `tests/unit/setup`) | 471 passed, 0 skipped |
| `pnpm -C dashboard install --frozen-lockfile`, then `build` (`tsc --noEmit` + `vite build`) | clean |
| `pnpm -C dashboard test` (vitest) | 256 passed |
| `pnpm -C dashboard e2e` (Playwright, Chromium, own ports 8895 and 8897 and own homes) | **32 passed** (21 dashboard, 11 setup wizard) |
| Client and server harness, then `GOS_REQUIRE_HARNESS_RUN=1 pytest` on the six Phase 2 harness test files | harnesses passed (52 and 31 checks); 18 passed, 1 skipped (see "Harness run" at the end) |

`gos eval fast-lane --fake` and `gos eval recipe-lookup --fake` could not be started from this session: its sandbox refuses any shell command containing the word `eval`. The same code paths ran instead through the tests that call them: `evals.run(..., fake=True)` in `test_fast_lane_harness.py` and `recipe_eval` with the oracles in `test_recipe_lookup_harness.py`, which assert the report's pass lines. Both passed on CI's harness DB (below) and here.

### CI on `main` [repo]

| Commit | Run | ubuntu | windows | mod | dashboard |
|---|---|---|---|---|---|
| `d60edbf` (#60, last Phase 2 merge) | 43077351047 | success | success | success | success |
| `efaef2f` (#70) | 43084265062 | success | success | success | success |
| `b7b88a0` (#67, head) | 43084303461 | success | **cancelled** (pytest: 1 failed, 4792 passed, 164 skipped, then hung to the 6 h limit) | success | success |
| `b7b88a0` dashboard (Windows) | 43084303147 | | success | | |

The `mod` job on `b7b88a0` (job 130744570026) ran the server harness (31 checks, run `20261111T191015Z`), two client harness runs (52 checks each, `20261111T191119Z` and `20261111T191222Z`) and `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration`: **96 passed, 19 skipped**. These include every Phase 2 harness test: `test_tools_harness` (6 passed, 1 skipped), `test_vault_harness` (2), `test_fast_lane_harness` (1), `test_deep_lane_harness` (1), `test_dashboard_harness` (4) and `test_recipe_lookup_harness` (4). The skips are the GTNH quest pack, the structures dump and downloaded voice models, none of them Phase 2.

---

## Phase 2 work orders

### WO-2.0: Dashboard design

> **Acceptance:** "Clayton approves the mockups before WO-2.6 starts; the approval is logged in `docs/decisions.md`"

**Verdict: met.**

- D92 (2026-11-05, PR #17, merge `aca5b30`) logs the approval: the Now, Glance, Designer, Power, Quests, Settings + Health, Crops, first-run setup, States and two phone boards, the ten age themes and both comfort modes [repo]. The "Feature concepts" row is not approved.
- The Design System copy is in `docs/design/system/` (`tokens.json`, `components/bundle.css`, `README.md`) [repo].
- D158 (5) later refines "board before build": views with no board are designed to the system and marked for Clayton's approval at the deployment test. That covers Chat, Ctrl+K and Costs (WO-2.6), the banners (WO-2.8) and, from Phase 5, the persona dropdown.

Open: PR #17 Q1 (Feature concepts in scope?).

### WO-2.1: Core service and supervisor

> **Acceptance:** "Health and auth tests; a killed process restarts within 10 s and shows on the strip; requests with a foreign Host or Origin are rejected; an unpaired device is refused"

**Verdict: met with fakes.** Every part runs on CI. The PC part is the login start (D116), pairing on `gos.localhost` and `gos launch` (V24).

| Part | Status | Evidence |
|---|---|---|
| Health and auth tests | met | PR #30 (merge `b82d792`), lint fix #34. `tests/unit/test_core_service.py`, `tests/security/test_core_api.py` [repo, here] |
| Killed process restarts within 10 s and shows on the strip | met | `tests/unit/test_supervisor.py::test_killed_core_restarts_within_10_s_and_shows_on_the_strip`; 3.78 s here, 1.46–1.82 s in PR #30 [here, PR]. The strip itself is Settings + Health's health card (WO-2.6), and e2e `Settings + Health shows the processes ...` checks it [here] |
| Foreign Host or Origin rejected | met | `test_foreign_host_is_rejected`, `test_foreign_origin_is_rejected`, `test_conflicting_host_headers_are_rejected`, `test_websocket_with_foreign_origin_or_host_is_rejected`; the UI keeps the checks (`test_dashboard_api.py::test_the_ui_still_checks_host_and_origin`) [here] |
| Unpaired device refused | met | `test_unpaired_device_is_refused`, `test_unpaired_websocket_is_refused`; e2e `an unpaired browser gets the pairing screen and nothing else` [here] |
| Login start (D77, D116), `gos launch lab\|export` (V24, D80 carry-over from WO-0.8) | **PC step** | `phase1_pc_checks.md` steps 9 and 13; `core_service.md` B and C |

Open: PR #30 Q2 (does Prism's Java command line carry Play's instance path? A runbook check). D142 (9), (12) and "UAC is on" answer Q1, Q3, Q4 and Q5.

### WO-2.2: Tool layer

> **Acceptance:** "Unit tests per tool"

**Verdict: met.**

- PR #36 (merge `c101172`) and fix #39 (`fface09`, the crop tables in `db_sql`'s whitelist) [repo].
- The registry has 17 Phase 2 tools. Each has unit tests in `tests/unit/tools/` (`test_tool_items`, `test_tool_recipes`, `test_tool_quests`, `test_tool_sql`, `test_tool_notes`, `test_tool_registry`, `test_tool_format`), plus guards, TSV and the signed cursor [repo, here]. The later `action_propose`, `action_status` and `telemetry_get` are WO-7.1's.
- `test_tools_harness.py` passes on CI's harness DB [repo].

Open: PR #36 Q6 (the 64 KiB value cap makes `SELECT *` on `export_run` fail; OK?). D142 (2), (5), (13) and (14) answer Q1 to Q5.

### WO-2.3: Router, instant commands, pre-retrieval, fast lane

> **Acceptance:** "P50 time to first token ≤ 1.2 s on the 50-question set; 10 ambiguous-item cases ask instead of answering"

**Verdict: met with fakes. The P50 is a paid PC step.**

| Part | Status | Evidence |
|---|---|---|
| 10 ambiguous-item cases ask | met | `test_fast_lane.py::test_ambiguous_cases_ask_without_a_model_call`; `test_fast_lane_harness.py` asserts `passed: 60 of 60` and `ambiguous rows that asked: 10 of 10` with 40 model calls on CI's harness DB; the e2e `an unsure match asks` [repo, here]. PR #48, merge `178480d` |
| P50 TTFT ≤ 1.2 s | **PC step (real key)** | The eval computes it (`test_a_slow_model_fails_the_real_run` checks the gate) [here]. The fake's timings aren't a model's. V21's Phase 0 smoke (917 ms median, 5 runs) is only a hint. `fast_lane.md` C |

Note: D153 takes the P50 over the 40 `core` rows the model answers, leaving out instant and hand-off rows, which have no model call and would only flatter it. That reading is stricter than "the 50-question set" and is reasonable.

Open: PR #48 Q1 to Q3. Q1 matters for voice: D152 speaks only checked text, so the wait Clayton hears is `total_ms`, not TTFT. WO-5.3's sentence streaming (D204) may already cover it; Clayton should say which number gates.

### WO-2.4: Deep lane

> **Acceptance:** "The init message lists only `mcp__gos__*` tools and `Skill`; a shell request and a write outside the vault both fail; the path guard blocks a write to `me/`; hitting the budget cap pauses the task and asks; cost table recorded"

**Verdict: met with fakes. The cost table is a paid PC step.**

The deep-lane tests run the real Claude Code CLI from the Agent SDK against `gregtec.llm.fake_api.FakeMessagesAPI` (D157) [repo]. PR #49, merge `6e6a943`.

| Part | Status | Evidence |
|---|---|---|
| Init lists only `mcp__gos__*` and `Skill` | met | `test_deep_lane_cli.py::test_init_lists_only_gos_tools_and_skill` [here] |
| Shell request fails | met | `test_a_shell_request_fails` [here] |
| Write outside the vault fails | met | `test_a_write_outside_the_vault_fails` [here] |
| Path guard blocks `me/` | met | `test_the_path_guard_blocks_a_write_to_me`; `test_deep_lane.py::test_path_guard` [here] |
| Budget cap pauses and asks | met | `test_the_budget_cap_pauses_and_asks`, `test_the_cap_pauses_and_asks_then_continues`, `test_a_paused_task_can_be_stopped`, `test_a_paused_task_stops_by_itself_after_a_while` [here] |
| Cost table recorded | **PC step (paid, up to 10 × $2 cap)** | The fake run works (`test_the_cost_eval_with_the_fake_model`, `test_the_task_file_has_ten_representative_tasks`) [here]. No real table is recorded, so plan 4.4's $0.12 sketch stands. `deep_lane.md` C |

Also checked by tests: hooks or MCP servers written into the vault's `_gos/` never run, and no inherited `CLAUDE*`/`ANTHROPIC_*` setting reaches the lane.

Open: PR #49 Q1 to Q4. Q3 (run the paid eval on a copy of the vault?) touches the live vault, so it should be answered before the PC run.

### WO-2.5: Vault I/O, curator jobs, INDEX builder, token re-measurement

> **Acceptance:** "Format decision recorded"

**Verdict: met with fakes. The decision itself waits on a free PC run.**

- PR #38 (merge `6b94bb6`) and PR #41 (`628554d`, D142 (d) to (g), recorded as D143) [repo].
- D141 fixes the rule in advance (which format wins, by how much), and `gos vault measure` computes it. D141 is still **"Pending Clayton (PC step)"**: no counts are recorded. The stand-in count in PR #38 (4 characters a token) is a sanity check only [PR].
- Tests: `test_vault_io.py`, `test_curator.py`, `test_maintenance.py`, `test_measure.py` (no `me/` text reaches the fake counter), and `test_vault_harness.py` on CI's harness DB [repo, here].

PC: `phase1_pc_checks.md` step 11 (`vault maintain` twice, `vault measure`, `powercfg`, the session-end backup). #41 is merged, so its [#41] lines apply.

Open: PR #41 Q1 to Q4. PR #38's questions are answered by D142 (d), (e), (f) and, in part, (g).

### WO-2.6: Dashboard shell

> **Acceptance:** "Matches the approved mockups; usable on monitor 2; glance mode clickable (D49)"

**Verdict: met with fakes, with one open defect (fixed in open PR #71). "Matches the approved mockups" is Clayton's check.**

PR #53 (merge `2dcabfb`), D159 to D163 [repo].

| Part | Status | Evidence |
|---|---|---|
| Matches the approved mockups | **PC step (Clayton's eye)** | Built from `docs/design/system/` and the D92 list without the canvas handoff (D158 (5)), so no pixel comparison exists. Every colour comes from `tokens.json` through `bundle.css`. Chat, Ctrl+K and Costs **need his board approval** |
| Usable on monitor 2 (2560×1440) | met with fakes | e2e `the workbench fills monitor 2 at 2560x1440` (exact regions, no scroll bars), `every view reads at WCAG contrast in every theme, on monitor 2 and on the phone`, number keys and Ctrl+K [here]. On the real monitor: display scaling and the window's position (D163) |
| Glance mode clickable | met with fakes | e2e `glance comes with the game's focus, is clickable, and the mouse brings the workbench back` and `a held glance always has a way back` [here]. The real focus signal (`GetForegroundWindow`) is Windows only and has a Linux fake |
| Phone layout (D53) | **not met on `main`; fixed in open PR #71** | The QA sweep found the phone Settings + Health view 711 px wide on a 390 px screen (484 px of sideways overflow) and long NEI citation chips overflowing their cards [PR #71]. `main`'s e2e `the phone layout fits 390x844` passes because it doesn't check sideways scroll on every tab. #71 adds that check and the CSS fix |

Also on CI's harness DB: recipe cards of every recipe map and NEI handler fit the catalog (`test_dashboard_harness.py`) [repo].

Open: PR #53 Q1 to Q7. Q1 is the board approval. Q6 (no number check in the deep lane) is a real gap for "0 uncited numbers" outside WO-2.7's fast lane: deep-lane answers in Chat are marked "numbers not checked".

### WO-2.7: `recipe-lookup` skill and 50-question eval

> **Acceptance:** "≥ 95% correct, **0 uncited numbers**"

**Verdict: met with fakes. The real-model run is a paid PC step.**

- PR #54 (merge `00896f2`), D170 to D172 [repo].
- With the oracle fakes (the fast lane's model behind the real router; the deep lane's behind the real CLI loading `gos:recipe-lookup`), `test_recipe_lookup_harness.py::test_the_question_set_passes_with_the_oracles` requires 50 of 50 and 0 uncited, rows in every lane and no flagged citation. It passed on CI's harness DB [repo]. PR #54's container report: `correct: 50 of 50`, `uncited numbers: 0`, `lanes: deep 4, fast 38, instant 8`, `Result: PASS` [PR].
- The truth is references to DB rows, never game numbers (a unit test fails on any digit left in the truth), and the skill holds no game numbers (a test fails on any `r:` id or `eut:`/`dur_t:` digit) [repo]. This keeps CLAUDE.md's rule that numbers come only from the DB.
- `test_recipe_eval.py` covers the scorer, including the review's wrong answers [here].

PC: `recipe_lookup.md` A and B, about 15 minutes, under $10 in the worst case.

Open: PR #54 Q1 to Q7. Q5 (two kinds of wrong answer still pass the heuristic scorer) and Q6 (`item_resolve("sticks")` gives Crop Sticks with no ambiguity flag) are real limits to keep in mind when reading the PC run.

### WO-2.8: No-AI mode and the credits flow

> **Acceptance:** "With the API key revoked: instant commands, Search, recipe cards, BOMs and saved plans work, and 'AI offline' shows; simulated 80% and 100% credit use trigger the banner and the switch"

**Verdict: met with fakes.**

- PR #58 (merge `d97a1fc`), D164 to D166 [repo].
- e2e `with the key revoked: instant commands, Search and recipe cards work, and AI offline shows` and `simulated 80% and 100% credit use raise the banner and switch to no-AI mode` [here]. `test_no_ai.py` covers saved plans and BOMs with no model call (`client.requests == []`), the pause at 100% with `[80]` and `[80, 90]`, the 409 with no alert open, and maintenance asking the mode first [here].
- The Discord buttons are WO-9.2's, as the plan row says.

PC: `dashboard.md` "No-AI mode and the credit alert" 1 to 4, including the Console billing URL (`BILLING_URL`), which may have moved. The banners **need his board approval**.

Open: PR #58 Q1 to Q8.

### WO-2.9: First-run setup wizard

> **Acceptance:** "A fresh install is configured from the wizard alone"

**Verdict: met with fakes.**

- PR #60 (merge `d60edbf`), D167 to D169 and D223 [repo].
- e2e `setup.spec.ts` (11 tests) runs against `serve_core.py --fresh`: an empty GOS_HOME, a fake Prism tree and a file keyring. It walks paths, the key with its Test call, the Discord token, the headset, the talk key and the wake phrase, then the checklist and Finish. `gos.toml reads back, the keyring holds the secrets, doctor passes` checks that `gos doctor --no-hash` exits 0 on the result [here].
- `tests/unit/setup` covers the TOML edits, steps, secrets, routes and the Play record [here].
- **Play safety** (D223): the wizard never moves Play once it is fixed, Export or Lab at a current or former Play is an error, and sync refuses a former Play (`test_play_record.py`). That keeps CLAUDE.md's Play rule. Open PR #71 closes one more spelling hole ('GTNH-Straße' vs 'GTNH-Strasse').

PC: `setup_wizard.md` and PR #60's ten steps: the real WASAPI device list, the Arctis Chat endpoint, the key Test call, the `former_plays` record and the board comparison.

Open: PR #60 Q1 to Q14. Q1 (the threat model) and Q10 (Play never moves by a click) touch Play, so CLAUDE.md's Ask rule applies.

---

## G2 row

> **G2:** "Text Q&A ready" (Reviewer report; Clayton sign-off)

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Text Q&A works end to end | **met with fakes** | A typed question goes through the router to an instant card, the fast lane or the deep lane, with numbers cited and checked, from `gos ask`, `/api/ask` and the dashboard's voice-strip box (e2e `Chat keeps every answer as a card, and a citation opens its recipe card`). Both evals pass on a real harness DB with fakes |
| The Phase 2 acceptances | **met with fakes** except WO-2.6's phone layout | See above. #71 fixes the phone layout |
| Real-model quality and latency | **PC step** | WO-2.3 P50, WO-2.4 cost table, WO-2.7 ≥ 95% / 0 uncited, D141 counts |
| Plan §9 security tests that Phase 2 owns | met | `tests/security/test_core_api.py`: foreign Host or Origin, missing cookie, unpaired device. Discord, `!gos` and injected-text tests belong to later phases |
| L9 (revoke the key mid-session, then restore it) | met with fakes; **PC step** | e2e "with the key revoked" and `dashboard.md` no-AI step 1 |

**Required before Clayton signs G2:**
1. Merge open PR #71 (the phone layout and citation-chip overflow; the Straße/Strasse Play guard).
2. Get `main`'s Windows CI green again: the hung run on `b7b88a0` and the WO-5.2 timing failure. It is outside Phase 2, but a red or hanging Windows job hides Windows regressions in the core.
3. The PC steps below, with their paste-backs.
4. His answers, or "keep as built", to the Phase 2 questions below. The defaults are reversible and pending him (D158).

Should do (not blocking):
- Mark V24 "9/9" instead of "8/8" in §13.2 and `verify.tsv` (left over from G0).
- PR #53 Q6: decide whether the deep lane gets D152's number check.
- PR #54 Q2: the suggested task to sort `fake?` rows after known ones in `recipes_for_item.sql`.

---

## VERIFY items Phase 2 touched

| ID | WO | Status now | What closes it |
|---|---|---|---|
| **V24** (`gos launch` starts Lab and Export) | 2.1 (from WO-0.8, D80) | Partly resolved; the `gos launch` remainder is open | `phase1_pc_checks.md` step 9: each `gos launch` result as gos-dev, and the refusal of a second client (D142 (9)) |
| **V21** (Haiku TTFT) | 2.3 re-measures | Resolved in Phase 0 by `gos smoke` (917 ms median) | Nothing to re-open. WO-2.3's eval gives the 40-row P50 on the PC |
| **V29** ("game paused" signal) | 2.6 reads it | Open; the dashboard has a designed "Game paused" state that reads `game.paused` | WO-6.1 brings the signal |

No other VERIFY item names a Phase 2 WO. QA:V8 (talk key) is checked by the wizard against Play's `options.txt`, read only. It is not on the §13.2 list.

---

## Open questions from Phase 2's PRs

Questions that D142 already answers are left out. Everything else is open. The defaults stand, reversible, until Clayton answers (D158).

**PR #17** (WO-2.0), https://github.com/crazie450-prog/GretecOS/pull/17
1. Are any of the five Feature concepts (What's this?, Shopping list, Hazard watch, Tier ladder, Boots like a multiblock) in scope?

**PR #30** (WO-2.1), https://github.com/crazie450-prog/GretecOS/pull/30
2. Q2: does Prism's Java command line contain Play's instance path? (A runbook check, `core_service.md` C.) Q1 and Q3 to Q5 are answered by D142.

**PR #36** (WO-2.2), https://github.com/crazie450-prog/GretecOS/pull/36
3. Q6: the 64 KiB value cap makes `SELECT *` on `export_run` fail (its `sources_json`), with an error that says so. OK? Q1 to Q5 are answered by D142.

**PR #38** (WO-2.5), https://github.com/crazie450-prog/GretecOS/pull/38
4. Q2, in part: should backups also run when the PC never sits idle for 30 minutes? D142 (g) adds only the session-end fallback after 2 missed days. Q1, Q3 and Q4 are answered by D142 (d), (e) and (f).

**PR #41** (D143), https://github.com/crazie450-prog/GretecOS/pull/41
5. Q1: the session-end backup may almost always be cut short on a large DB (3.9 s for 133 MB in a 4.5 s window). Run an overdue backup at the next core start instead, or as well?
6. Q2: "missed for 2 days" means no daily run today or yesterday. Should it be 3 days?
7. Q3: every daily-log fact key except `note:`, `period:` and `from:` counts as an event kind, `goal:` included. Use a fixed list instead?
8. Q4: should the summariser be refused when it states a count outside the computed `n_` lines?

**PR #45** (Phase 1/2 PC checklist), https://github.com/crazie450-prog/GretecOS/pull/45
9. Q2: is it OK that the login-start step (sign out and in) comes last? Q1 is moot (#41 to #44 are merged).
10. Q3: step 4b builds his own DB from GOS_APP. OK?

**PR #48** (WO-2.3), https://github.com/crazie450-prog/GretecOS/pull/48
11. Q1: should §8's gate become P50 `total_ms`, since only checked text is spoken, or should voice check and speak sentence by sentence?
12. Q2: "how many X does the EBF need" and "what does the plan say" go to the fast lane with no pre-retrieval. OK for now?
13. Q3: is the wording OK when a number check fails twice and there's no card?

**PR #49** (WO-2.4), https://github.com/crazie450-prog/GretecOS/pull/49
14. Q1: is 30 minutes right before a paused task stops by itself?
15. Q2: skill edits in the live vault are ignored; skills come from `vault-template/_gos/`. OK?
16. Q3: should the paid eval run on a copy of the vault instead of the live one?
17. Q4: should the researcher subagent get web tools before WO-8.3?

**PR #53** (WO-2.6), https://github.com/crazie450-prog/GretecOS/pull/53
18. Q1: approve the Chat, Ctrl+K palette and Costs designs (no board).
19. Q2: themes per device, or one setting in the core?
20. Q3: `gos deploy` now needs Node.js and pnpm, and stops before the swap without them. OK?
21. Q4: the glance timings, 300 ms in and 1.5 s out, are defaults to tune on the real screen.
22. Q5: the tier-up recap moves to WO-6.3. OK?
23. Q6: should the deep lane get D152's number check?
24. Q7: the merge call in D160: the board's `ask.reply` and the catalog recipe card replace WO-5.1's and WO-2.7's.

**PR #54** (WO-2.7), https://github.com/crazie450-prog/GretecOS/pull/54
25. Q1: the skill's body (about 1.4k tokens) is in every fast-lane request's cached prompt. OK?
26. Q2: instant replies skip flagged rows. The root fix (sorting `fake?` rows last in `recipes_for_item.sql`) is a separate task.
27. Q3: "simplest" follows plan 7.3's tier rule, so bronze resolves to "Smelting: Long Bronze Rod". OK?
28. Q4: tier wants are strict. "You're at LV; it needs MV" fails r15. OK?
29. Q5: two kinds of wrong answer still pass the scorer.
30. Q6: plurals can resolve to the wrong item ("sticks" gives Crop Sticks).
31. Q7: the real-key eval's cost, under $10 worst case, is his call.

**PR #58** (WO-2.8), https://github.com/crazie450-prog/GretecOS/pull/58
32. Q1: deep-lane tasks stop, and compaction waits, while the AI is off. OK?
33. Q2: a plain 429 or other 4xx doesn't switch to no-AI mode.
34. Q3: Stop leaves the core stopped through exit code 5.
35. Q4: the pause is at 100% whatever `credit_alerts_pct` lists. A resume at 80 doesn't skip it.
36. Q5: `on_credits_out` other than `"no_ai"` stops the core from starting. OK?
37. Q6: `billing` and `stop` get a 409 with no alert open.
38. Q7: the decision API accepts the client token named `discord`. WO-9.2 must use that name.
39. Q8: gos-plan/0 gains an optional `bom` list. Plan questions that name no saved plan go to the AI when it is on.

**PR #60** (WO-2.9), https://github.com/crazie450-prog/GretecOS/pull/60
40. Q1: confirm the threat model of D223 (touches Play).
41. Q2: refuse to write over an unreadable `former_plays.txt`, or move it aside?
42. Q3: keep the former-Plays record in two places, cleared by hand?
43. Q4: should `config.load` refuse relative `[paths]`?
44. Q5: gos.toml inside a guarded folder is an ERROR that blocks saving. OK?
45. Q6: the recording failures (logged at core start; a refusal in sync and export). OK?
46. Q7: the upgrade limit: an earlier hand edit of Play can't be known. PC step 2 covers it.
47. Q8: case-only differences are compared case-folded on every OS. Keep it?
48. Q9: only a missing gos.toml forces the wizard. OK?
49. Q10: Play never moves by a click. OK, or a guarded "move Play" flow later? (Touches Play.)
50. Q11: add `world_backups` to the gate when that key lands?
51. Q12: the WARN and ERROR severities as listed.
52. Q13: keep the GTNH binding row, which reads Play's `options.txt`?
53. Q14: the key test is one metered call, only on Test, and refused while the AI is off. OK?

PRs #34, #39, #40 and #50 had no questions.

---

## Clayton's PC steps before signing G2

These go in order and don't repeat `docs/runbooks/phase1_pc_checks.md`. Where a Phase 1 step already covers a Phase 2 check, it is named, not copied. `$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe`, as in that runbook's variable block. Use a normal PowerShell window (not "Run as administrator"), with `$ProgressPreference = 'SilentlyContinue'`.

**0. Merge #71 and get `main` green on Windows first** (see "Required before Clayton signs G2"). Deploy that commit, not `b7b88a0`.

**1. Install Node.js and pnpm. This must come before Phase 1's step 1.** Since WO-2.6, `gos deploy` builds the dashboard and stops before the swap without them (PR #53 Q3, `dashboard.md` A):
```powershell
winget install OpenJS.NodeJS.LTS
npm install -g pnpm@10.28.0
```
Open a new window afterwards, so `pnpm` is on PATH.

**2. Run `phase1_pc_checks.md` steps 1 to 12 as written,** with these Phase 2 notes:
- **Step 1 (deploy):** expect `Building the dashboard with pnpm ...`, then `Built the dashboard: C:\GregtecOS-app\current\dashboard\dist`. The Chrome app window then opens on monitor 2 (D163).
- **Step 4 (DB build) and step 5 (quests load)** must be done before steps 5 to 7 below: every eval needs the DB, and the quest rows need the quest load.
- **Step 9 (core service, pairing, `gos launch`):** pair the dashboard window here (`dashboard.md` B4: click **Ask to pair**, run `& $gos pair`, type the code). This closes WO-2.1 and V24.
- **Step 11 (vault):** `& $gos vault measure` closes WO-2.5's D141 format decision. #41 is merged, so the [#41] lines apply.
- Leave step 13 (login start) for the very end, as that runbook says.

**3. Setup wizard (WO-2.9; `setup_wizard.md`, PR #60 steps 2 to 10).**
- Run `& $gos stop`, then `& $gos serve` once on the new version, **before any hand edit of `[paths] play`**.
- In the dashboard: **Open setup** (or Settings → Setup). Paths must be all green, with no `paths.config` error.
- Store the Anthropic key and press **Test**. Expect "the API answered on claude-haiku-5-5": one metered call, a fraction of a cent.
- Headset: pick the Arctis Nova Elite **Chat** endpoint and check that the mic row is green on the real WASAPI list.
- Talk key: the "Talk key in GTNH" row should say it is free. Check Discord's Keybinds by hand.
- **Finish**, then `& $gos stop` and `& $gos serve`.
- Check the Play record:
  ```powershell
  Get-Content "$env:USERPROFILE\GregtecOS\config\former_plays.txt"
  Select-String -Path "$env:USERPROFILE\GregtecOS\config\gos.toml" -Pattern 'former_plays'
  ```
  Both should list `C:\Games\PrismLauncher\instances\GTNH-Play`.

**4. Dashboard acceptance (WO-2.6; `dashboard.md` C5 to C10 and D11).**
- Monitor 2 at 2560×1440 with no scroll bars. Keys 1 to 9 switch views. Ctrl+K, then `steel`, then **Recipes for Steel Ingot**.
- Ask `recipe for steel` in the voice strip. Chat keeps it across `& $gos stop` / `& $gos serve`.
- Glance with Play running: click into the game, open the inventory and move onto monitor 2, then click **open ›**.
- Settings + Health (`9`) and Costs (`8`).
- Themes:
  ```powershell
  & $gos quests tier --set LV
  & $gos quests tier --set MV
  & $gos quests tier --set HV
  & $gos quests tier --clear
  ```
  Also try **Calm Ops** and **Field Manual**.
- Open the dashboard on the phone's width only if WO-9.3's Tailscale is set up. Otherwise #71's e2e stands in.
- Note display scaling, window position and glance timing in the deployment checklist.

**5. Fast lane (WO-2.3; `fast_lane.md` B3 to B5 and C7), about 1 US cent:**
```powershell
& $gos ask "recipe for steel"
& $gos ask "how do I make steel"
& $gos ask "how do I make copper wire"
New-Item -ItemType Directory -Force "$env:USERPROFILE\GregtecOS\data\reports" | Out-Null
& $gos eval fast-lane | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\fast-lane-eval.txt"
```
Pass: `passed: 60 of 60`, `p50 ttft ... <= 1200 ms`, `ambiguous rows that asked: 10 of 10`, `Result: PASS`.

**6. Recipe lookup (WO-2.7; `recipe_lookup.md` A2 and B3), under $10 in the worst case (his call, PR #54 Q7).** Run it while no deep-lane task is running:
```powershell
Test-Path "$env:USERPROFILE\GregtecOS\vault\_gos\skills\recipe-lookup\SKILL.md"
(& $gos ask "how do I make steel" --json | Out-String | ConvertFrom-Json).card.type
& $gos eval recipe-lookup --out "$env:USERPROFILE\GregtecOS\data\reports\recipe-lookup.tsv" | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\recipe-lookup-eval.txt"
```
Expect `True`, then `recipe`. (This is #71's corrected A2 check; on `main` the runbook still greps for `"recipe"`.) Pass: `correct: >= 48 of 50`, `uncited numbers: 0`.

**7. Deep lane (WO-2.4; `deep_lane.md` A2, B3 and B4, then C5), up to $20 in the worst case (10 × $2 cap).** It writes into the live vault (PR #49 Q3), so answer that question first.
```powershell
& $gos status
& $gos ask "plan a steel ingot line that makes 2 ingots per second at MV"
& $gos ask "change my goals to finishing the EBF this week"
& $gos eval deep-lane | Tee-Object -FilePath "$env:USERPROFILE\GregtecOS\data\reports\deep-lane-eval.txt"
git -C "$env:USERPROFILE\GregtecOS\vault" log --stat -5
```
Expect **Deep lane: ok, warm**, the cold-start wall time noted, `vault\me\goals.md` unchanged and an `inbox\` proposal instead, then the cost table with `Result: PASS`. Optionally run `deep_lane.md` D (the $0.05 cap pause).

**8. No-AI mode and the credit flow (WO-2.8, L9; `dashboard.md` "No-AI mode" 1 to 4).** Do this after steps 5 to 7, because it deletes the key.
```powershell
& $gos key delete anthropic     # within 30 s: "AI offline"; "recipe for steel" and Search still answer
& $gos key set anthropic        # clears within a minute
```
Then:
- Wi-Fi off, ask `how do I make steel`. Expect "AI offline · the API can't be reached", clearing within a minute of reconnecting.
- **Open billing** goes to the Console's billing page. Note the URL if it moved.
- **Stop** on a credit banner leaves the core stopped in `& $gos status`, and `& $gos serve` starts it again.

**9. Board approvals (D92, D158 (5)).** Approve, or say what to change:
- Chat, the Ctrl+K palette and Costs (WO-2.6);
- the AI offline and credit banners (WO-2.8);
- the wizard and its banner, against the approved "First-run setup wizard" board (WO-2.9).

**10. `phase1_pc_checks.md` step 13 (login start), last.** This closes WO-2.1's D77 check.

**What to paste back for G2:**
- Phase 1's list (V24, D141 and the core lines in particular);
- the `former_plays` lines;
- `fast-lane-eval.txt`;
- `recipe-lookup-eval.txt` and `recipe-lookup.tsv`;
- `deep-lane-eval.txt` and `deep-lane-costs-<date>.tsv`;
- the deep lane's cold-start time;
- the no-AI observations and the billing URL;
- display scaling and glance notes;
- his board verdicts and answers to the questions above.

---

## Risks

1. **`main`'s Windows job hangs.** On `b7b88a0` pytest finished (1 failed) but the job ran to the 6 h limit and was cancelled. A hang costs CI minutes and leaves no result, and it can be mistaken for a flake, as red Windows runs were at G0. The failing test is WO-5.2's, and the hang is likely a thread or child process that doesn't end (not diagnosed here). (Confirmed: run 43084303461)
2. **No real model has answered a Phase 2 eval yet.** All quality numbers come from oracle fakes, which resolve from the question's words and follow the skill's rules. They check plumbing and scoring, not model behaviour. PR #54 Q5 names two wrong-answer kinds the scorer still lets through. (Confirmed)
3. **The deep lane's numbers aren't checked** (PR #53 Q6). The fast lane refuses uncited numbers (D152); deep-lane answers reach Chat marked "numbers not checked". That is consistent with the plan as written, but it is the main way an uncited number can reach Clayton in Phase 2. (Confirmed)
4. **The dashboard was built without the canvas handoff** (D158 (5)). If the handoff arrives later, the views are reconciled to it, so some rework is expected. (Likely)
5. **Spending at the PC test.** Steps 5 to 7 cost up to about $30 in the worst case (mostly the deep-lane caps), plus metered setup tests. All of them are Clayton's call under CLAUDE.md's spending rule. (Confirmed: figures from the runbooks)
6. **Plural item names** can resolve to the wrong item with no ambiguity flag (PR #54 Q6). Voice makes plurals common, so this matters more once Phase 5's speech-to-text is live. (Likely)

## Harness run (this review)

Run here on `b7b88a0`, in the build container, with `--max-workers=2` [here]:

| Step | Result |
|---|---|
| `xvfb-run ... ./gradlew --no-daemon --max-workers=2 runClient25 -Pharness` | `harness passed: 52 checks, run 20261112T051252Z` (1 min 57 s) |
| `./gradlew --no-daemon --max-workers=2 runServer -Pharness` | `harness passed: 31 checks, run 20261112T052903Z` |
| `GOS_REQUIRE_HARNESS_RUN=1 uv run --locked pytest -v` on `test_tools_harness`, `test_fast_lane_harness`, `test_deep_lane_harness`, `test_dashboard_harness` and `test_recipe_lookup_harness` (client run) | 16 passed, 1 skipped (`test_the_quest_tools_on_the_real_book`, which needs `GOS_QUESTS_PACK`) |
| the same on `test_vault_harness` (server run) | 2 passed |

The Phase 2 tests that ran on the harness DB:
- `test_fast_lane_harness::test_the_question_set_passes_with_the_fake_model`: `passed: 60 of 60`, `ambiguous rows that asked: 10 of 10`, 40 model calls, each with `ttft_ms`.
- `test_recipe_lookup_harness::test_the_question_set_passes_with_the_oracles`: 50 of 50, 0 uncited, every lane used, no flagged citation.
- `test_deep_lane_harness::test_the_deep_lane_reads_the_harness_db`.
- the four `test_dashboard_harness` card and state checks.
- six `test_tools_harness` checks: every recipe row cited with the DB's numbers, the `db_sql` guards on the real file, and every named query on the real schema.
- both `test_vault_harness` checks: the curator, and `measure` sampling the real DB.

The harness output under `mod/run/` is git-ignored and not committed.
