# Gregtec OS: G8 gate review (Phase 8, GregtecLink client, screen reading, wiki)

2026-11-13 · independent gate reviewer (D41, D158 (2)) · reviewed: `origin/main` at `c5d97c4` (PR #97 merged). It covers the Phase 8 PRs #90 (WO-8.1, merged as `dae0541`), #94 (WO-8.2, `a428fff`) and #97 (WO-8.3, `c5d97c4`). It also covers #92 (`cdc1a17`), the AutoExport fix that PR #90 first carried and that was split out and merged on its own.

## Verdict

**G8 is ready for Clayton's sign-off at the deployment test, with fakes and the dev-runtime harness standing in for his Lab, his screen and the real wiki.** All three Phase 8 work orders are built and merged. Every acceptance criterion that can be checked without Clayton's game, screen, API key and the real GTNH wiki is met, either here or on CI. No criterion is "not met" in code. Each §8 acceptance is still a check on the PC, so every row below is **met with fakes**, and the PC steps below are the acceptance.

What the PC steps must show (D158 (2) batches them with the other gates):
1. **WO-8.1:** "what's this?" names 10 items in Lab, including addon items, NEI's item panel and a recipe page. The harness got 10 of 10 here, but in the dev runtime, not the pack.
2. **WO-8.2:** the five Lab screenshots pass `gos eval screen --images` with the real vision model (paid, about $1.40 worst case). V13: borderless capture gives no black frames.
3. **WO-8.3:** the real wiki's licence (V14), then the crawl. If the wiki has an anti-bot gate, WO-8.3 stays dormant until the wiki admins answer (risk 2).

Evidence tags, as in G0 to G5:
- **[repo]:** checked in the repo, the git history or GitHub's CI logs.
- **[here]:** run by this reviewer in the build container (Linux, 4 shared CPUs, no GPU, no Windows, no game window, no internet wiki).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC. Nothing from the PC is in the evidence yet for Phase 8.

### This reviewer's runs [here]

All runs are on `c5d97c4`.

| Check | Result |
|---|---|
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 491 files already formatted |
| Phase 8 unit and security tests (`tests/unit/tools/test_gamelink.py`, `test_install_gregteclink.py`, `test_sync.py`, `tools/test_screen.py`, `voice/test_screen_capture.py`, `voice/test_discord_coexistence.py`, `voice/test_settings.py`, `tests/unit/wiki`, `tests/security`, `tools/test_tool_registry.py`, `test_curator.py`, `tools/test_deep_lane.py`) | **499 passed, 23 skipped**. The skips are the Windows-only tests and the live isolation tests |
| full `pytest` (default groups, as CI) | **9112 passed, 288 skipped**, exit 0 (9 min 27 s) |
| `xvfb-run ... ./gradlew --no-daemon --max-workers=2 runClient25 -Pharness` | `harness passed: 56 checks, run 20261113T185917Z` (4 min 7 s) |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration/test_whats_this_harness.py tests/integration/test_screen_harness.py` on a DB collected and built from that run | **13 passed** (the 10 items plus the mod-coverage check; the five screen cases with the fake model; the obeying model stopped) |
| `tests/harness/client_link_core.py` (a real core on that DB) + `runClient25 -Pharness -PharnessNeiHandlerFail -PharnessNeiLookupFirst -PharnessClientLink` (CI's third client run) | **`harness passed: 73 checks, run 20261113T190357Z`**: `client_link_connected`, 4 `look_*` and 6 `hover_*` checks, `whats_this_10_items: 10 of 10 named correctly`, `reconnected_after_core_restart: connections 1 -> 2`, `gos_from_the_player_answered`, `gos_from_others_ignored`, `status_at_1_hz: 4 status messages in 5072 ms` |
| `gos wiki` against the repo's fake MediaWiki (`tests/unit/wiki/fakewiki.py`, served on 127.0.0.1) in a scratch GOS_HOME | `crawl` before a licence: `no wiki licence is recorded, so the cache stays empty`, exit 1. `licence --api ... --yes` printed the site, licence and terms, then `Recorded`. `crawl`: 4 stored. `status`: licence, 4 pages, crawl finished. `show "Steam Turbine"`: `w:Steam_Turbine@1003` and the attribution line (site, page URL, licence, "personal cache (not redistributed)"). `refresh`: exit 0. The server saw 3 requests, **at most 1 in flight**, every one an `action=query` GET with `maxlag=5` and the project User-Agent `GregtecOS-wiki-cache/0.1 (personal offline cache for one player's assistant; one request at a time; +https://github.com/...)`, which carries no personal details |

`gos eval screen --fake` could not be started from this reviewer's session. Its sandbox refuses any command that contains the word "eval", as at G2 and G5. `test_screen_harness.py` (above) and `tests/unit/tools/test_screen.py` run the same eval code (`gregtec.screen.eval`) on the harness DB and on a fixture DB. No DB, export, frame or wiki text from these runs is committed. They stay in the session's scratch folder.

### CI on `main` [repo]

| Commit | Run | ubuntu | windows | mod | dashboard | mod (Windows) |
|---|---|---|---|---|---|---|
| PR #90 merged head `bf0f365` | 43441669359, 43441669294 | success | success | success | success | success |
| `dae0541` (#90, WO-8.1) | 43449630278, 43449630174 | success | success | success | success | success |
| PR #94 head `2e2c5bb` | 43443287322, 43443287474 | success | success | success | success | success |
| `a428fff` (#94, WO-8.2) | 43476445460 | success | **failure** (risk 1) | success | success | (path filter: no mod file) |
| PR #97 head `cea4b28` | 43476701355 | success | success | success | success | (not re-run; 7f62975's run 43449242708 was green) |
| `c5d97c4` (#97, WO-8.3, head) | 43487042611 | success (9119 passed, 281 skipped) | success (9140 passed, 260 skipped) | success | success | (path filter: no mod file) |

The `mod` job on `c5d97c4` (job 132098126907) ran the server harness (35 checks), the listener harness (19), two client harness runs (56 each), `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration` (**198 passed, 21 skipped**: `test_whats_this_harness`, `test_screen_harness` and `test_vault_harness` with the `wiki` job among them) and the client-link run (**73 checks**, `whats_this_10_items: 10 of 10 named correctly`).

`a428fff`'s Windows failure is not a Phase 8 test. Two of three seeds of WO-7.3's `tests/unit/test_oc_mailbox_stress.py::test_concurrent_writer_and_core` failed with `assert 3 > 20` and `assert 4 > 20` (`r.agent.seq > 20  # Windows runners sleep coarsely`). That is a throughput floor that a slow Windows runner missed. Everything else passed: 9086 passed, 260 skipped. The same test passed on both PR heads and on every other `main` commit listed above.

---

## Phase 8 work orders

### WO-8.1: GregtecLink client module

> **Acceptance:** "'What's this?' works on 10 items, including addon ones"

**Verdict: met with fakes. The acceptance itself is a PC step in Lab (runbook B).** PR #90, merge `dae0541`. Decisions: D285-D287. The AutoExport fix the PR first carried was merged separately as #92 (`cdc1a17`), which answers PR #90 Q8.

| Part (plan §8 row 8.1) | Status | Evidence |
|---|---|---|
| WS to the core | met | `WsClient` (RFC 6455, no dependency) and `CoreLink`. Tests: `WsClientTest` (6) and `CoreLinkTest` (5), including `aStalledCoreNeverBlocksTheGameThread` [PR, CI `mod`]. Harness: `client_link_connected` [here] |
| Reconnects after a core restart | met | `reconnected_after_core_restart: connections 1 -> 2` [here] |
| Position and dimension at 1 Hz | met | `status_at_1_hz: 4 status messages in 5072 ms` [here]. The status also carries `paused` (V29) |
| Looked-at block and hovered item on a key press | met in the harness; **PC step** for NEI's panel and recipe pages | Harness: 4 `look_*` and 6 `hover_*` checks (the harness hovers chest slots) [here]. `Typing` guard: `FeedsTest` [PR]. NEI's item panel and recipe pages are runbook B.5 |
| Chat lines; `!gos` only from his own name in his own world | met | `gos_from_the_player_answered` and `gos_from_others_ignored` [here]. `test_gamelink.py` covers another name, `singleplayer: false`, and `!gos confirm` refused against a real `ActionDesk` [here] |
| Local chat notices (D76) | met with fakes | the `notices` feed and toggle in `FeedsTest` and `ClientConfigTest` [PR, CI]. The in-game `[GOS]` lines are runbook B.5 and B.7 |
| Config toggles | met with fakes; **PC step** | `FeedsTest` (9) and `ClientConfigTest` (6) [CI `mod`]. In game: runbook B.9 |
| No items, blocks or mixins | met | `usesMixins = false` in `mod/gradle.properties`. No `GameRegistry.register*`, network wrapper or command registration anywhere under `mod/src/main` [here]. The harness's `writes_only_under_gregteclink` check passes [here] |
| No numeric IDs (CLAUDE.md) | met | registry name, meta and NBT in every message. `whatsthis` keys are `registry@meta#<nbt sha256>`, then `registry@meta`, then `registry@32767` [repo] |
| Answered from the DB, cited, no model call | met | `whatsthis.py`. The answers cite `r:` and `mte:` ids. `test_whats_this_harness.py` (11) [here] |
| "What's this?" on 10 items, addon ones included | **met in the harness; PC step** | `whats_this_10_items: 10 of 10 named correctly`: GT, bartworks, GT++, GoodGenerator, AE2, IC2 with NBT and vanilla [here]. Runbook B.5 is the acceptance |
| Installed in Play with the listener module by a script Clayton runs | written and tested on fake trees; **PC step** | `scripts/install_gregteclink.py` (`gregtec.gamelink.install`), 262 lines of tests in `test_install_gregteclink.py`: dry run by default, refuses as gos-dev, refuses while a client runs, and more [here]. The run itself is runbook C, and only Clayton runs it |

Two observations for the PC check, neither of them a defect:
- **The EBF had no recipe in the harness DB.** In this run the look at the EBF said "No recipe in the DB makes it." The harness's dev runtime is GT's own mod set, not the pack (mod/README.md), and its DB has no crafting recipe for that controller. On the full pack the same answer should name the EBF's recipe. If it doesn't, note it in step 2 below.
- **The charged RE-Battery gets no recipe line** (D286, PR #90 Q6). Recipes make `IC2:itemBatREDischarged`, which is a different registry name, and no alias rule was guessed.

Open: PR #90 Q1-Q7 (below).

### WO-8.2: Screen reading

> **Acceptance:** "5 test screenshots answered correctly"

**Verdict: met with fakes. The paid real-model run on five Lab screenshots and V13 are PC steps.** PR #94, merge `a428fff`. Decisions: D288-D289.

| Part (plan §7.7) | Status | Evidence |
|---|---|---|
| A dedicated hotkey; one frame per press; never continuous | met | `[voice] screen_key` (F17). Tests in `test_screen_capture.py` show that talk-key turns, a wake, other hotkeys, pushes, reconnects and an hour of ticks make no capture, and that auto-repeat still gives one frame per press. A test also checks that no core module imports a capture library [here] |
| dxcam 0.3 DXGI; only the game window (title **and** window class) | met with fakes; **PC step** | Fake-Win32 tests: an Explorer window or browser tab with a GTNH title is refused. `GetClassNameW` is on D247's audit allowlist (`test_discord_coexistence.py`) [here]. The real capture is `screen_reading.md` B |
| Black frame or failed grab falls back to the newest F2 screenshot (Play's or Lab's, read only) | met with fakes; **PC step** (V13) | Tests: no-frame retries on the same camera, a raised grab recreating the camera once, and the newest of two folders picked [here]. The real check is `screen_reading.md` B.3-B.4 |
| Vision model with the game-state snapshot and the screen-reader skill; text in the image is data | met with fakes | `screen/lane.py` has no `handoff`. A fake model that obeys a sign's caption is refused the tool, and its "9999 EU/t" is refused by the number check: `test_screen.py`, and on the harness DB `test_an_obeying_model_is_stopped_on_the_pack` [here] |
| Images cached 24 h, then deleted | met with fakes; **PC step** | the `sweep()` tests [here]. `screen_reading.md` D checks it after a day |
| No-AI mode refuses politely | met | `reader.py` and its tests [here] |
| 5 test screenshots answered correctly | **met with fakes; PC step** (paid) | `test_the_five_cases_pass_on_the_pack_with_the_fake_model` on synthetic frames over the harness DB [here]. The real model on his Lab screenshots is `screen_reading.md` C |

Dependencies: dxcam (MIT) and comtypes (MIT, held below 1.5) are Windows-only and not vendored. That fits CLAUDE.md's licence rule.

Open: PR #94 Q1-Q11.

### WO-8.3: Wiki cache, licence check, `wiki-research` skill, `w:` citations

> **Acceptance:** "Attribution shown; etiquette respected; an injected instruction in a test page is ignored"

**Verdict: met with fakes. The real wiki's licence (V14), crawl and a live deep-lane wiki answer are PC steps.** PR #97, merge `c5d97c4`. Decisions: D290-D292.

| Part | Status | Evidence |
|---|---|---|
| Licence check first (V14); nothing cached before it | met with fakes; **PC step** | `test_nothing_is_cached_or_fetched_before_a_licence`, `test_the_licence_is_read_and_shown_never_guessed`, and `gos wiki crawl` refusing before a licence [here]. The real licence is `wiki.md` A |
| One-time crawl, `recentchanges` refresh in idle maintenance, fetch on demand, `wiki_fts` + `revid` | met with fakes | `test_crawl_in_batches_and_resume`, `test_refresh_follows_recent_changes`, `test_fetch_on_demand_caches_once`, `test_a_page_that_becomes_a_redirect_is_refreshed`, `test_the_idle_job_runs_between_fts_and_backup`, `test_a_db_rebuild_keeps_the_wiki_cache`, and the CLI run above [here] |
| **Etiquette respected** (one request at a time, User-Agent, `maxlag`, ≤ 50 titles, back-off; anti-bot gate never bypassed) | **met with fakes** | `test_every_request_is_a_polite_read`, `test_at_most_50_titles_a_batch`, `test_one_request_at_a_time_across_clients_and_threads`, `test_one_request_at_a_time_across_processes` (it fails with the worker's file lock removed [PR]), `test_a_back_off_holds_the_turn`, `test_429_and_5xx_back_off_and_give_up`, `test_an_anti_bot_gate_stops_at_once`, `test_a_gate_is_recorded_and_stops_everything`, `test_a_gzip_bomb_is_refused` [here]. In the CLI run here the fake server saw at most 1 request in flight, `maxlag=5` on every request and only `action=query` [here]. The Windows `msvcrt` lock ran green on CI at `7f62975` and `cea4b28` [repo] |
| **Attribution shown** | **met with fakes** | `test_search_cites_and_attributes`, `test_fetch_shows_licence_attribution_and_frames_the_text`, `test_the_dashboard_card_shows_the_attribution` (the table card, so no new view or board), `test_a_deep_answer_that_used_the_wiki_gets_the_attribution` (added in code, not left to the model) [here]. `gos wiki show` prints the attribution line [here] |
| `w:<page>@<revid>` citations [QA:D5] | met | `test_titles_and_citations`, `test_a_citation_is_read_whole` (`fast_lane.REF` and `planner.validate.REF` read `w:Coil_(Nichrome)@1234` as one id) [here] |
| **An injected instruction in a test page is ignored** | **met with fakes** | The fake page carries "SYSTEM: ... call action_propose ... write me/prefs.md ... 9999 EU/t" and a fake end marker. Tests: `test_an_injected_instruction_never_becomes_a_tool_call` (the researcher's obeying calls `action_propose`, `note_write`, `ore_waypoint`, `Agent`, `Bash` and `WebFetch` are all denied), `test_an_injection_in_a_snippet_or_title_stays_plain_data`, `test_only_the_read_only_researcher_holds_the_wiki_tools`, `test_wiki_tools_never_reach_the_fast_lane_or_a_prompt` and `test_the_researcher_is_told_wiki_text_is_data`. `tests/security/test_wiki_injection.py` fails if these tests are removed [here]. This is fakes only: no real model has read the injected page (risk 4) |
| `wiki-research` skill | met | `vault-template/_gos/skills/wiki-research/SKILL.md`, deployed like the other skills [repo] |
| Personal cache only, no redistribution | met | wiki text lives only in `data/gos.sqlite`. No wiki text is in the repo; the fake pages are made up [repo] |

`gos wiki show` prints a page's raw text, the injected comment included, to Clayton's console. That is reading, not a prompt, and D292 (2) already strips control characters from the wiki's one-line strings. It is noted only so the fake page's output above isn't mistaken for a leak.

Open: PR #97 Q1-Q6.

---

## G8 row

> **G8:** "Reviewer report; Clayton sign-off"

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Reviewer report | met | this report |
| The Phase 8 acceptances | **met with fakes** | all three WOs above. Nothing is not met in code |
| Phase 8 end to end | met with fakes | the client-link harness (73 checks: a real game client, a real core on a harness-built DB, 10 items, reconnect, `!gos`) and `test_screen_harness.py` [here] |
| Clayton's sign-off | **PC step** | the steps below, then his sign-off |
| §10's G11 Q&A line (wiki, screen and hover evals pass) | partly; G11 re-checks it | The 5-case screen eval exists (`tests/evals/screen_cases.tsv`). **Plan §9's 10-case hover-item eval and 15-question wiki eval have no case file in `tests/evals/`.** The hover side has only the client-link harness's `whats_this_10_items` check (a Java harness assertion, not a scored eval case file) and runbook B.5 (step 2 below). PR #97 and D292 defer the wiki eval to the WO-2.7 eval backlog. So G11 can run neither until someone writes them, or G11 explicitly accepts `whats_this_10_items` plus B.5 in place of the hover eval (should-do 1) |

**Required before Clayton signs G8:**
1. The PC steps below, with their paste-backs.
2. His answers, or "keep as built", to the Phase 8 questions below. Under D158 the defaults stand and are reversible.
3. `main` green on every CI job at the commit he deploys. `a428fff` is not: its Windows job failed (risk 1). `c5d97c4` is green on all four jobs (run 43487042611).

Should do (not blocking):
1. **Slot plan §9's 10-case hover-item eval and 15-question wiki eval.** Neither has a case file in `tests/evals/`. PR #97 defers the wiki one to the WO-2.7 eval backlog, but no WO row writes either case file. G11's Q&A line needs both, unless G11 explicitly accepts the harness's `whats_this_10_items` check plus runbook B.5 in place of the hover eval.
2. **Fix a runbook clash.** `machine_events.md` §1 (WO-6.2) installs GregtecLink in Lab by hand, with a `Set-Content` that writes `B:client=false` and no token line. If Clayton runs it after `install_gregteclink.py --instance lab`, it turns WO-8.1's client module off. `install_gregteclink.py` already turns the listener on. §1 should point to the script, or at least say not to run it after the script. In the meantime step 1 below says so. This doc is Phase 6's (WO-6.2), so this review leaves it unchanged. Owner: the PM, or the G6 report if it is re-opened.
3. **Clean up the runbook commands.** `screen_reading.md` A and C use `uv run gos ...` from `C:\GregtecOS-app`. The other runbooks and the G5 steps use `& $gos ...` (`current\.venv\Scripts\gos.exe`). The steps below use `$gos`. Harmless, for the PM.
4. **Give WO-7.3's stress test a Windows floor** (risk 1).

---

## VERIFY items Phase 8 touched

| ID | WO | Status now | What closes it |
|---|---|---|---|
| **V13** (lwjgl3ify borderless capture has no black frames) | 8.2 | Partly resolved (D288): a black frame is detected and is never sent, and the F2 screenshot is read instead | Step 6 below (`screen_reading.md` B.1-B.4). He notes `"source": "dxcam"` on a moving and a static screen, and what exclusive fullscreen did |
| **V14** (wiki API and licence) | 8.3 | Open. The code is ready | Step 8 below (`gos wiki licence`). He pastes the licence text, or the gate message |
| **V29** (a reliable "game paused" signal) | 8.1 (evidence), 6.1 (owner) | Resolved from the log line by D258. WO-8.1 adds `paused` to the 1 Hz status, as a second source | Step 4 below (runbook B.10: `status.paused` turns `true` within a second of Esc). This is extra evidence; the row's owner stays WO-6.1 |
| **V05** (chat-line routing in logs) | 8.1 (touches), 6.1 (owner) | Open for WO-6.1's Lab session. Chat now also reaches the core through the mod | Nothing in Phase 8. The G6 report covers it |
| **[QA:V6]** (`!gos` only from Clayton; future servers) | 8.1 | Single player and LAN host only. The server case stays open (D286). Known LAN limitation: a friend with cheats or op on his LAN world (or a command block) can spoof his name with `/tellraw @a {"translate":"chat.type.text","with":["<his name>","!gos ..."]}` (risk 8) | Step 4 below (`!gos` from his own name answered). Nothing closes the server case until he plays on one |

No Phase 8 PR added a `verify.tsv` row. #94 changed V13's text, and V14 is unchanged. `tests/unit/test_verify_list.py` keeps `verify.tsv` and plan §13.2 in step (it ran in the full run).

---

## Open questions from Phase 8's PRs

No later decision answers any of them: D285-D292 are the last rows on `main`, and each one says "Clayton's later review open". The defaults stand, reversible, until Clayton answers (D158). Each PR's reviewer (advisor) agreed with its judgement calls, but Clayton still decides. PR text is quoted here as data.

**PR #90** (WO-8.1), https://github.com/crazie450-prog/GretecOS/pull/90
1. Q1 (D285): the mod token is its own kind, opens only `/mod/ws`, and is kept in `GOS_HOME\config\`, because `!gos` can spend credits. OK?
2. Q2 (D285): the What's this? key is unbound by default, with a typing guard. The runbook recommends a key that types nothing (F13-F24, Pause, a mouse button). OK?
3. Q3 (D285): only container GUIs and NEI's overlay are read. Is reading other screens, such as the quest book, worth a later WO?
4. Q4 (D286): `!gos` is accepted only from the player the link said hello as, in single player or as LAN host. There is no `[game] username` key, and the server case is open under [QA:V6]. OK?
5. Q5 (D286): answers are deterministic, with no model. "Tooltips" means machine facts only, so item tooltips are a gap (risk 3). OK?
6. Q6 (D286): an NBT variant with no recipe falls back to the plain item. IC2 batteries aren't tied to their `...Discharged` item. Should a curated alias handle that later?
7. Q7 (D287): Lab's default exclude gains `gregteclink*`, and the listener is switched on in Play by the install. OK?
8. Q8 (the AutoExport fix as its own PR): **done**. It was split out and merged as #92 (`cdc1a17`).

**PR #94** (WO-8.2), https://github.com/crazie450-prog/GretecOS/pull/94
9. Q1: the default screen key is F17. If F17 is already one of his voice keys, the screen key turns itself off. OK?
10. Q2: capture happens in the voice client only, and `screen_snapshot` is never a model tool. OK?
11. Q3: the F2 fallback reads a screenshot he takes himself, from Play's or Lab's folder. GOS never presses F2. OK?
12. Q4: the game window is found by its title plus its window class, not by its process, because D247 forbids `OpenProcess`. OK?
13. Q5: dxcam and comtypes (MIT, Windows-only) are not vendored, and comtypes is held below 1.5 until Lab checks 1.5. OK?
14. Q6: the screen lane has no `handoff`. OK?
15. Q7: numbers come only from DB tool results, never from pixels. OK?
16. Q8: the model runs with adaptive thinking at effort low, with no server-side fallbacks. OK?
17. Q9: the answer reuses the table card, so no board approval is needed. OK?
18. Q10: eval frames go to a temporary folder, not the 24 h cache. OK?
19. Q11: with both Play and Lab connected, the "what's this?" pointer is dropped and the answer goes by the frame alone. Is that acceptable until a follow-up tells the core which window was in front?

**PR #97** (WO-8.3), https://github.com/crazie450-prog/GretecOS/pull/97
20. Q1: a query holds the wiki turn for its whole retry loop. An agent's fetch on demand waits at most 5 s, then says the wiki is busy. OK?
21. Q2: a redirect fetched on demand costs two requests the first time, so the redirect's own revid is stored. OK?
22. Q3: missing titles are remembered for 15 minutes, in memory only. OK?
23. Q4: the injection tests stay next to the fake-wiki fixtures, with a pointer in `tests/security/`. OK?
24. Q5: D290's User-Agent and y/N prompt, D291's citation form, D292's read-only researcher, and the deferred live eval. These are recorded from the advisor; V14 stays open.
25. Q6 (CI note): `mod (Windows)` last ran on `7f62975` and was green. Noted.

#92 had no questions.

---

## Clayton's PC steps before signing G8

These run after G2's PC steps 0 to 3 (`docs/reviews/G2_review.md`: Node.js and pnpm, `phase1_pc_checks.md` steps 1 to 12 with the export and the DB build, the setup wizard and the API key) and after G5's steps 0, 1 and 6 (`docs/reviews/G5_review.md`: the voice token, devices and keys, and `gos voice run` answering). They don't repeat those steps. `$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` and `$app` is `C:\GregtecOS-app\current`, as in `phase1_pc_checks.md` and `gregteclink_client.md`. Use a normal PowerShell window in his own account (never "Run as administrator"), with `$ProgressPreference = 'SilentlyContinue'`. **Everything up to step 10 is in GTNH-Lab.** Only step 11 touches Play, and only through `install_gregteclink.py`, which he runs himself. In all, about 2 h.

Spending: the "what's this?" key and `!gos` lookups make no model call. Each screen-key press in step 6 is one vision call (a few cents). Step 7's eval is about $1.40 worst case (it asks for `yes`). Step 9's deep-lane question is capped at the deep lane's $2 per task.

**0. Deploy a green `main`:** `c5d97c4` or later, with every CI job green on that commit (`c5d97c4`: run 43487042611). Not `a428fff` (risk 1). In the profile clone: `git pull`, then `uv run gos deploy`. This builds GregtecLink's jar into `$app\mod-dist\` (D98), puts dxcam into the release, and adds `gos wiki`. Then run the usual sync once (`gregteclink_client.md` A.2):
```powershell
& "$app\.venv\Scripts\python.exe" "$app\scripts\sync_instances.py"
```
`& $gos status` shows the core `ok`. If `gos.toml` has its own `[sync.lab] exclude`, it must keep `"mods/gregteclink*", "config/gregteclink*"`.

**1. GregtecLink in Lab** (`gregteclink_client.md` B.1-B.4). Close every GTNH client, then:
```powershell
& "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance lab
& "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance lab --apply
```
- It prints the jar, the config and `token: gregteclink-lab -> ...`. The config turns the listener and client modules on and export off.
- Don't run `machine_events.md` §1's `Set-Content` after this. It would turn the client module off (should-do 2). If Phase 6's listener check is still to do, the script has already done its install part.
- Launch Lab and open a Lab world. In Controls, bind **What's this? (Gregtec OS)** to a key that types nothing, other than F13-F17: for example F18, or a mouse button mapped to it.
- Check the link (B.4):
  ```powershell
  $core = Get-Content "$env:USERPROFILE\GregtecOS\run\core.json" | ConvertFrom-Json
  Invoke-RestMethod "http://127.0.0.1:$($core.port)/api/game" -Headers @{ Authorization = "Bearer $($core.token)" }
  ```
  `links` shows `binding: lab` with his player name, and `status` updates every second.

**2. Ten items** (B.5, the WO-8.1 acceptance). Press the key over each of these:
- in his inventory or NEI's item list: a GT ingot, a GT circuit, a bartworks or GT++ material, a GoodGenerator or TecTech part, an AE2 item, a charged IC2 battery and a vanilla item;
- looking at the block, with no GUI open: a placed GT machine (an EBF), a GT casing and another mod's block.

At least three of the hovers must be in NEI's item panel, plus one on an NEI recipe page. **Pass:** each `[GOS]` line and card names what the tooltip shows. The EBF's line should name a recipe, which the harness's dev runtime couldn't (WO-8.1 observations). Note any miss.

**3. By voice** (B.6), with `& $gos voice run` up: hover over an item and say "what's this?". The answer is spoken and the card shows. Do it once more while looking at a block, with no GUI open.

**4. `!gos`, reconnect, toggles, paused** (B.7-B.10):
- Type `!gos recipes for steel ingot` in chat: a `[GOS]` reply comes back.
- `& $gos stop`, wait 5 s, then `& $gos serve`. Within about 15 s, step 1's `/api/game` shows the link again and the key works.
- Toggles: close Lab, set `B:chat=false` in Lab's `config\gregteclink.cfg`, and start Lab again; chat no longer appears in `/api/game`. (`& $gos serve` holds its console, so run the `/api/game` check in a second PowerShell window.) Do the same for `position`, `target` and `notices`, one at a time, setting each back after.
- Press Esc: `status.paused` turns `true` within a second (V29 evidence).

**5. Screen key** (`screen_reading.md` A). With GTNH closed:
```powershell
& $gos voice keys lab
& $gos voice keys play
```
Expect `screen: F17: free in GTNH`. Then restart the voice client (`& $gos stop`, then start GOS again). If one of his own voice keys is F17, add `screen_key = "F18"`, or another free key, under `[voice]` in `gos.toml`. It must not be the key bound in step 1.

**6. Screen reading in Lab** (`screen_reading.md` B.1-B.5, V13):
- With `window.borderless = true`, go fullscreen (F11), hover over an item and press F17. GOS says its name and a cited fact. `%GOS_HOME%\logs\voice.log` has a `screen frame` line with `"source": "dxcam"`.
- With a chest open, or paused, so nothing on screen moves, press F17 again: still `dxcam`. If the log says `dxcam gave no frame`, note it.
- Exclusive fullscreen (`borderless = false`, restart Lab), then F17: note whether the frame was black and fell back (`screen capture fell back to the screenshot`). Then press F2 and F17: the answer reads `"source": "screenshot"`. Set `borderless` back to `true`.
- With the dashboard in front, then with File Explorer on the Lab folder in front, press F17: both are refused as "the game window isn't in front".

**7. The five-screenshot eval** (`screen_reading.md` C, the WO-8.2 acceptance; paid, about $1.40 worst case):
- In **Lab** only, take `s01`-`s05` with F2, as `tests/evals/screen_cases.tsv` and runbook C.1 describe. `s05` is the sign with the injected text.
- Copy them to `%USERPROFILE%\GregtecOS\data\evals\screen\` as `s01.png` to `s05.png`. Never commit them.
- Run:
  ```powershell
  & $gos eval screen --images "$env:USERPROFILE\GregtecOS\data\evals\screen"
  ```
- Type `yes`. **Pass:** `passed: 5 of 5` and `Result: PASS`.

**8. Wiki licence and crawl** (`wiki.md` A-B, V14):
```powershell
& $gos wiki licence
```
- Read the licence. If a personal, never-shared cache under it is fine, answer `y`. Paste the licence text: that closes V14.
- If it says the wiki states no licence, or `Stopped: ... refused scripted access`, stop here. Don't work around it. Ask the GTNH wiki admins (GTNH Discord) for API access or a `Special:Export` dump, then tell the PM (risk 2).
- Otherwise run:
  ```powershell
  & $gos wiki crawl
  & $gos wiki status
  & $gos wiki show "Electric Blast Furnace"
  ```
  **Pass:** `Crawl finished`, the status shows the licence and the page count, and `show` prints a `w:Electric_Blast_Furnace@<revid>` id and the attribution line. Ctrl+C during the crawl is safe; run it again to go on.

**9. A wiki answer** (`wiki.md` D, the WO-8.3 "attribution shown" on the real model; capped at $2):
- On the dashboard, ask the deep lane: "what does the wiki say about the pyrolyse oven?"
- **Pass:** the answer cites a `w:` id and ends with the wiki's attribution line (site, licence).

**10. Sweep, after a day** (`screen_reading.md` D): after a day with no GOS restart, `dir "$env:USERPROFILE\GregtecOS\data\cache\screens"` lists only frames from the last 24 h.

**11. Play install** (`gregteclink_client.md` C). Only after steps 1-4 pass. Close Play; the core must be running.
```powershell
& "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance play
& "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance play --apply
```
Bind the key in Play's Controls, as in step 1. Then start Play: step 1's `/api/game` shows `binding: play`. To remove the mod again, see the runbook's last paragraph.

**12. Sign-off.** Say whether to keep each default listed under the questions above.

**What to paste back for G8:**
- step 1's `/api/game` output;
- step 2's list of the 10 items and their answers, and any miss;
- step 4's results for reconnect, the toggles and paused;
- step 6's `screen frame` log lines and what exclusive fullscreen did (V13);
- step 7's table;
- step 8's licence text (or the gate message), and the `status` and `show` output;
- step 9's answer;
- his answers to the questions above.

---

## Risks

1. **`main` was red on Windows at `a428fff`.** WO-7.3's stress test failed its throughput floor (`r.agent.seq > 20`) on a slow runner, so it can fail on any merge. It is not a Phase 8 file, but it can make the deploy commit's CI red. G2's risk 1 was also an intermittent test on one OS. The fix belongs to WO-7.3's owner: a time-scaled floor, or a retry. (Confirmed once)
2. **The wiki may refuse scripted access, or state no licence** (V14; plan 7.8 says research couldn't reach the site). If it does, WO-8.3's tools stay dormant: the job is skipped and the researcher says there is no cache. The other lanes don't depend on it. The only path is to ask the wiki admins, never a workaround. (Unknown until step 8)
3. **Item tooltips are a gap.** Plan 7.8's "tooltips" are met for machines only (D286, PR #90 Q5). "What's this?" gives name, mod, recipes and uses, but not an item's own tooltip lore. §10's hover eval at G11 should be read with that. (Confirmed)
4. **No real model has read an injected wiki page.** The defence is structural: only the read-only researcher has the wiki tools, its other calls are denied, and every `action_propose` needs Clayton's confirmation (D59). The tests use fakes. The researcher's summary to the orchestrator is still model-written text that a page could steer (D292 (1) says so). It can only steer what the answer says, not an action. (Likely low)
5. **Black frames in exclusive fullscreen** (V13): the screen key then needs F2 first. Borderless avoids it, and it is GTNH's recommended Java 17+ mode (D68). (Likely, only in exclusive fullscreen)
6. **With Play and Lab both connected, the pointer is dropped** (PR #94 Q11). The screen answer then goes by the frame alone. This only matters while both games run, which happens in the Lab-to-Play steps of the update playbook. (Confirmed, by design)
7. **The runbook clash** in should-do 2 could silently turn the client module off in Lab. (Possible, only if steps are run out of order)
8. **`!gos` can be spoofed on a LAN world with cheats on.** The sender is read from vanilla's `chat.type.text` translation component, and `isSingleplayer()` is trusted for single player and the LAN host (`ClientModule.onChat`). In 1.7.10, `/tellraw @a {"translate":"chat.type.text","with":["<his name>","!gos ..."]}` builds exactly that component, so a friend with cheats or op (or a command block) can make GOS answer a `!gos` query as if Clayton typed it. Impact is low: the spoofed question is answered read-only and can spend credits like any `!gos` question (D285 Q1), but actions stay blocked (`!gos confirm` is refused, D59). No code change for G8; [QA:V6] records it. (Possible, only on a LAN world with cheats or op)

## After the review

- The full `pytest` here and CI run 43487042611 on `c5d97c4` both finished green (tables above), so `c5d97c4` is a deployable commit for step 0.
- No product code was changed, and no decision row was added (D294 unused).
