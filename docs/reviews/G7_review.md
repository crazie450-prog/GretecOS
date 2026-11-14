# Gregtec OS: G7 gate review (Phase 7, OpenComputers bridge, in-game display, safe actions)

2026-11-14 · independent gate reviewer (D41, D158 (2)) · reviewed: `origin/main` at `e481ca4` (PR #98 merged), plus the two small fixes on `review/g7` below. It covers the Phase 7 PRs #55 (WO-7.2, merged as `710318a`), #62 (WO-7.3, `fc898cb`), #64 (WO-7.1, `7250243`), #95 (WO-7.4, `2541381`), #99 (WO-7.5, `92a5380`) and #103 (WO-7.6, `3845486`). #98 (WO-6.5, `e481ca4`) is Phase 6's, but it is the merge that introduced the import cycle fixed here.

## Verdict

**G7 is ready for Clayton's sign-off at the deployment test, with the fake OC world, the harness DBs and fakes standing in for his `lab-ops` world.** All six Phase 7 work orders are built and merged. Every acceptance criterion that can be checked without the real game is met here or on CI. Each §8 acceptance in this phase is a Lab check ("1 h stable in `lab-ops`", "readable in game", "alerts fire in `lab-ops`", "at MV, it breeds ..."), except WO-7.1's, which is fully met in code. So WO-7.1 is **met**, and WO-7.2 to WO-7.6 are **met with fakes**: the PC steps below are their acceptance.

This review found and fixed one defect inside the phase's acceptance evidence, and one CI flake G8 had already reported (both on `review/g7`, no behaviour change in the running core):
1. **An import cycle** (`d7b7241`). WO-6.5's `advisor/checks.py` imported `place()` from `gregtec.alerts.engine`, and the engine's `cards` import reaches the tool catalog and the advisor again. Imported first in a fresh interpreter, `gregtec.alerts.engine` and `gregtec.bridge.ops` failed with `ImportError: cannot import name 'place' from partially initialized module`, and `gregtec.advisor.steps` with `AttributeError: partially initialized module 'gregtec.advisor.steps' has no attribute 'STEPS_MAX'`. So WO-7.1's confirm-flow property test (`tests/unit/test_oc_actions.py`) and WO-7.5's rule matrix (`tests/unit/test_alert_rules.py`) could not be collected when run alone; the full suite and `gos serve` hid it, because something else imports the catalog first. `place()` now lives in `gregtec.alerts.rules` (which imports nothing of GOS), the engine re-exports it, and `tests/unit/test_alert_imports.py` imports four modules (the engine, `bridge.ops`, `advisor.checks` and `advisor.steps`) in a fresh interpreter (3 of its 4 cases fail on `e481ca4`, all 4 pass now).
2. **WO-7.3's stress-test floor** (`553979c`, G8 risk 1). `test_concurrent_writer_and_core` asserted more than 20 exchanges in a fixed 1.5 s, which a coarse-sleeping Windows runner missed on `a428fff`. `run()` now keeps going, up to 20 times the time, until the agent has made 21 exchanges. The checks it then makes are unchanged.

Evidence tags, as in G0 to G8:
- **[repo]:** checked in the repo, the git history or GitHub's CI logs.
- **[here]:** run by this reviewer in the build container (Linux, 4 shared CPUs, no GPU, no Windows, no Minecraft world with OC).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC. Nothing from the PC is in the evidence yet for Phase 7.

### This reviewer's runs [here]

| Check | Result |
|---|---|
| `uv run --locked ruff check .` / `ruff format --check .` | All checks passed / 537 files already formatted (after the fixes) |
| `GOS_REQUIRE_LUA=1 uv run --locked pytest` on `e481ca4` + the floor fix | **9349 passed, 316 skipped**, exit 0 (7 min 28 s) |
| the same after the import fix (`d7b7241`) | **9352 passed, 316 skipped**, exit 0 (7 min 18 s): the 3 new import cases added |
| `lua5.3 tests/oc/run.lua` (the fake OC world: agent, mailbox, display, station) | **161 passed, 0 failed** |
| `luac5.2 -p` and `luac5.3 -p` on `oc/*.lua oc/crop/*.lua` | ok |
| Phase 7 Python tests, each file also collected on its own (`test_oc_actions`, `_ingest`, `_mailbox`, `_mailbox_stress`, `_telemetry`, `_display`, `test_alert_rules`, `test_alert_imports`, `crops/test_station*`, `security/test_oc_bridge_api`, `tests/oc`, `test_advisor*`) | **365 passed, 4 skipped**. Before the import fix, `test_oc_actions.py` and `test_alert_rules.py` gave 2 collection errors when run in this set |
| `GOS_MAILBOX_SOAK_S=120 pytest tests/unit/test_oc_mailbox_stress.py::test_accelerated_soak` (the real `MailboxService` against the Lua-exact writer) | **6682 exchanges, 6682 recorded, 0 lost, 0 half-read**, 6671 replies written and read, 4118 commands, 0 repeated at OC, 0 withdrawn |
| `./gradlew --no-daemon --max-workers=2 runServer -Pharness` | `harness passed: 35 checks, run 20261114T074310Z` |
| `xvfb-run ... ./gradlew --no-daemon --max-workers=2 runClient25 -Pharness` | `harness passed: 56 checks, run 20261114T074431Z` |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest` on those runs: `test_oc_bridge_harness` (1), `test_oc_live_harness` (1), `test_oc_names_harness` (4), `test_station_harness` (4), `test_crop_guide_harness` (9), `test_my_base_harness` (2) | **21 passed** |
| `gos crops station --parts` on a DB collected and built from the client run | Crop Breeder and Seed Generator `craftable at MV` (`r:codechicken.nei.recipe.ShapedRecipeHandler/b63980a92518`, `/d2e058ccc4f8`); the transposer and the OC parts `not in this DB` (the dev runtime has no OC). As PR #103 reported |
| dashboard: `pnpm install --frozen-lockfile`, `typecheck`, `vitest`, `build` | ok; **332 passed** (19 files); built |
| Playwright, the full suite on private ports and homes | **71 passed**, including `oc_displays.spec.ts` (4: the stalled machine first, cited; the next message within 10 s; contrast and phone fit; the WO-7.5 low-store alert on Base and Now) and `crops.spec.ts` (3) |

No export, DB, world or Playwright output is committed. They stay in the session's scratch folder and `mod/run/` (ignored).

### CI on `main` [repo]

| Commit | Run | ubuntu | windows | mod | dashboard | dashboard (Windows) |
|---|---|---|---|---|---|---|
| `710318a` (#55, WO-7.2) | 42948886639 | success | success | success | success | (path filter) |
| `fc898cb` (#62, WO-7.3) | 43072341905 | success | success | success | success | (path filter) |
| `7250243` (#64, WO-7.1) | 43072382704, 43072382668 | success | success | success | success | success |
| `2541381` (#95, WO-7.4) | 43501550024, 43501549750 | success | success | success | success | success |
| `92a5380` (#99, WO-7.5) | 43503983769, 43503983687 | success | success | success | success | success |
| `3845486` (#103, WO-7.6) | 43560633501, 43560633531 | success | success | success | success | success |
| `95034ff` (#101) | 43566610090 | success | success | success | success | (path filter) |
| `e481ca4` (#98, head) | 43571468861, 43571468424 | success | success | success (finished 08:22Z, after this was first written) | success | success |

---

## Phase 7 work orders

### WO-7.1: Bridge endpoints, token, action table, confirm flow

> **Acceptance:** "Unconfirmed actions **never** run (property test); a second proposal cancels the first out loud; an expired action can't be confirmed"

**Verdict: met.** PR #64, merge `7250243`. Decisions: D219-D221. The deliverable's real-world half (a `say`, a craft and a pause confirmed in `lab-ops`) is in the PC steps.

| Part (plan §8 row 7.1) | Status | Evidence |
|---|---|---|
| `POST /oc/telemetry`, always 200, `{ok, cmds, err}` ≤ 2 KB | met | `tests/security/test_oc_bridge_api.py` (41): no token, wrong, revoked or other-client token, the admin token, oversize, chunked oversize, a lying Content-Length, non-JSON, a wrong content type, schema-invalid, `v 2` and the rate limit all get 200 with `err`, and nothing is processed; the token never reaches a log [here]. The property test's `deliver_in_a_reply` rule checks every reply ≤ 2048 bytes [here] |
| Token | met | OC tokens `oc-play` / `oc-lab[-label]` open `/oc/telemetry` only and are never principals (D221); `test_admin_and_other_clients_cant_confirm` [here] |
| `action` table | met | `ActionDesk` is its only writer; `test_each_change_is_journalled_in_the_note_grammar`, `test_a_restart_drops_what_wasnt_sent`, `test_the_module_lists_no_destructive_kind` [here] |
| **Unconfirmed actions never run (property test)** | **met** | `tests/unit/test_oc_actions.py::TestConfirmFlow`, a Hypothesis state machine (100 examples x 40 steps): propose, plain/by-id/wrong-id confirms inside and outside the 3 s settle, cancel, clock steps 0-301 s, delivery by `take` and by `Bridge.reply` to three agents, give-backs, results. Invariants: at most one pending; nothing unconfirmed moves forward [here, and now runnable on its own: fix 1] |
| **A second proposal cancels the first out loud** | **met** | `test_a_second_proposal_cancels_the_first_out_loud` [here] |
| **An expired action can't be confirmed** | **met** | `test_an_expired_action_cant_be_confirmed`; the API's 409 in `test_an_old_card_or_an_expired_action_cant_be_confirmed` [here] |
| Read-back; buttons by action ID | met | `test_a_proposal_is_read_back_and_waits`, `test_read_backs_of_every_kind`, `test_a_button_confirms_only_its_own_action`, `test_a_plain_confirm_waits_for_the_read_back`, `test_a_dashboard_or_phone_button_confirms_by_id` [here] |
| Dashboard and voice confirmation | met with fakes; **PC step** | the approval card (Playwright, WO-2.6's ApprovalCard) and `test_saying_confirm`, `test_voice_and_discord_confirm_as_themselves`, `test_only_claytons_ways_can_confirm` [here]. Phone (WO-9.3) and Discord (WO-9.1) use the same confirm-by-id route later |
| Agents cannot confirm | met | `test_nothing_but_the_routes_and_the_router_calls_confirm`, `test_an_oc_message_cant_confirm_or_run_a_proposal`; `action_propose` is deep lane only [here] |
| On a real harness DB | met | `test_oc_bridge_harness.py::test_a_craft_from_telemetry_to_the_reply` [here] |
| Waypoint action (V23) | met with fakes; **PC step** | `test_a_confirmed_waypoint_is_written_by_the_core` and the two failure tests [here]. The JourneyMap check is `ore_intel_check.md` B, WO-6.6's runbook. It belongs to G6, but G6's gate report isn't written yet, so step 10 below runs it unless G6's steps are already done |

Open: PR #64 Q11-Q14 (below).

### WO-7.2: Lua agent `gos-agent.lua`

> **Acceptance:** "1 h stable in `lab-ops`; no TPS drop > 1"

**Verdict: met with fakes. The hour and the tick time are PC steps (`oc_agent.md` A-G).** PR #55, merge `710318a`. Decisions: D213-D215.

| Part | Status | Evidence |
|---|---|---|
| OpenOS, Lua 5.3, our own JSON encoder (MIT, ASCII-only) | met | `oc/gos-agent.lua`, `oc/json.lua`; `luac5.2/5.3 -p` [here]; nothing vendored (D213) |
| Component discovery, 2.9 drivers | met with fakes; **PC step** (V20) | `tests/oc` on `fakeoc.lua`, modelled on OC 1.12.68 and AE2 rv3-beta-1095-GTNH [here]; `test_oc_names_harness.py` (4) checks GT meta names and watched item keys against a real export [here]. `gt_machine` on 2.9.0 is `oc_agent.md` step 7 |
| 5-10 s cadence; sensor lines rare | met with fakes | `run.lua --dump`: 120 messages in 600 s, 30 sensor reads [PR]; part of the 161 Lua tests [here] |
| ID watchlists | met | exact `getItemInNetwork(name, damage)` per entry (D213, replacing §7.9's `getItemsInNetworkById`); the fake fails if `getItemsInNetworkById`, `getItemsInNetwork`, `allItems` or `getCraftables` is called [here]. Keys are registry name + damage, never numeric ids |
| Every message valid against the contract | met | `tests/oc/test_oc_agent.py` (18) validates every dumped message and reply against `docs/contracts/oc_bridge.schema.json` [here] |
| Memory steady | met with fakes | heap +0.7 KB between 10 min and 2 h on the fake clock [PR] |
| **1 h stable in `lab-ops`; no TPS drop > 1** | **PC step** | `oc_agent.md` E (and G's stress check). The container has no OC world |

Open: PR #55 Q1-Q7.

### WO-7.3: M1 file mailbox

> **Acceptance:** "1 h in `lab-ops` with no lost or half-read files"

**Verdict: met with fakes. The real hour is a PC step (`oc_agent.md` H).** PR #62, merge `fc898cb`. Decisions: D216-D218.

| Part | Status | Evidence |
|---|---|---|
| OC writer/poller (`oc/mailbox.lua`) | met with fakes | `tests/oc/test_mailbox.lua` (16) on the fake's managed drive with OC's tick costs and space count [here] |
| Core reader/writer, Windows retry, `safe_open()` | met with fakes; Windows on CI | `tests/unit/test_oc_mailbox.py` (45): temp-dir round trips, held-file retries, the path guard, a folder swapped for a link mid-step; the held-handle cases run on the Windows job [repo, here] |
| No lost or half-read files | **met with fakes; PC step** | The 120 s soak above: 6682 of 6682 recorded, 0 half-read [here]. `test_concurrent_writer_and_core` (3 seeds, now with a run-length floor: fix 2), `test_withdrawn_replies_under_concurrency`, `test_held_files_under_concurrency` [here]. D218 calls these a regression guard, not proof: the real hour is the acceptance |
| Shared ingest for WO-7.1; agent identity (binding, address) | met | `tests/unit/test_oc_ingest.py` (42), including the dedupe property over 12 seeds and `test_lab_naming_plays_computer_is_a_different_agent` [here] |
| gos-dev refused `play` | met | the `[mailbox.play]` refusal tests in `test_oc_mailbox.py` [here] |

Open: PR #62 Q1-Q6.

### WO-7.4: In-game displays and the Base view

> **Acceptance:** "Readable in game; updates ≤ 10 s"

**Verdict: met with fakes. "Readable" is only a PC check (`oc_agent.md` I); the Base view needs Clayton's board.** PR #95, merge `2541381`. Decisions: D277-D279.

| Part | Status | Evidence |
|---|---|---|
| OC status-board screen | met with fakes; **PC step** | `tests/oc/test_display.lua`; `tests/unit/test_oc_display.py` (29): ≤ 12 x 48 printable ASCII, fits 50 x 16, `NO REPLY` and grey after 30 s, the shell's resolution given back [here]. Readable from 3 blocks: step 30 |
| OCGlasses HUD | met with fakes; **PC step** (VERIFY) | 4 widgets, M2 only, the fake modelled on OCGlasses 1.6.9-GTNH's sources [here]. The method names are runbook step 34's VERIFY |
| Chat box | met with fakes; **PC step** | fixed templates from the desk's outcome, once per wording, from one computer: `test_craft_notices_follow_the_desk_outcome`, `test_a_failed_result_carries_no_agent_text`, `test_only_the_running_computer_says_a_notice`, `test_a_runner_without_a_chat_box_leaves_its_notices_to_the_chat_box` [here]. Step 33 |
| **Updates ≤ 10 s** | **met with fakes; PC step** | `tests/oc/test_display_pipeline.py::test_changes_reach_the_displays_within_10_s` (the real agent on fakeoc against the real core, over HTTP and the mailbox) [here]; Playwright `the next message reaches the view within 10 s` [here]. The guarantee is stated at `cycle_s` 5 (D277, Q1). Step 31 times it |
| Dashboard Base view | met with fakes; **board approval** | `BaseLive.tsx`, `GET /api/base/live` (in the OC-token and no-session route lists of `test_oc_bridge_api.py`), `oc_displays.spec.ts` at 2560x1440 and 390x844 with WCAG contrast in every theme [here]. `docs/design/README.md` lists the Base view under "No board yet" (D92) |
| Machine status `stopped` vs `paused` | met with fakes; **PC step** (V34) | `test_a_shut_down_multiblock_is_a_problem_until_gos_pauses_it` [here]; `test_oc_live_harness.py` maps GT meta names to the DB's machines on a real export [here]. GT disabling work on shutdown is V34, step 31 |

Open: PR #95 Q1-Q5.

### WO-7.5: Telemetry alert rules

> **Acceptance:** "Alerts fire in `lab-ops`, including with the game unfocused and no menu open (the AFK case, D46)"

**Verdict: met with fakes. The AFK run is a PC step (`oc_agent.md` J).** PR #99, merge `92a5380`. Decisions: D280-D281.

| Part | Status | Evidence |
|---|---|---|
| Power low, maintenance, machine stalled, crafting done | met with fakes | `tests/unit/test_alert_rules.py` (32 functions, 46 cases): each rule fires once, clears, doesn't flap, goes stale on pause or silence; `test_one_reading_then_silence_never_fires`, `test_lsc_maintenance_reads_its_own_lines_not_the_cut_problems`, `test_a_restart_marks_the_old_firing_rows_lost` [here, and now runnable on its own: fix 1] |
| Stale, never alerted, while paused (D258) or OC silent | met with fakes | `test_paused_holds_alerts_instead_of_firing`, `test_a_firing_alert_goes_stale_on_pause_and_is_not_cleared`, `test_silent_oc_marks_alerts_stale_without_a_pause` [here] |
| Where alerts show | met with fakes | `tests/oc/test_alert_pipeline.py` (a fakeoc post through to `/api/state`, `/ws`, the voice line, the board and the `alert` table); Playwright `a low store raises an alert on the Base view and on Now` [here] |
| One owner for a stalled machine (D281, PR #99 Q1) | met | WO-6.5 (#98, merged second) reads the `alert` table's firing `machine_stalled` rows (`advisor/checks.py`) instead of its own check [repo] |
| **Fires in `lab-ops`, unfocused, no menu (AFK)** | **PC step** | `oc_agent.md` J, steps 36-40. It relies on `pauseOnLostFocus` off (D85) and on Lab's log watchers for the pause line (D258, WO-6.1) |
| Threshold defaults | **Clayton's answer** | D280: 20/30 %, 30 s, 0 s, 60 s, `clear_s` 30 s, cooldown 600 s, done shown 300 s, "pending Clayton" |

Open: PR #99 Q2-Q7 (Q1 is settled by #98).

### WO-7.6: Breeding station

> **Acceptance:** "In `lab-ops` at MV, it breeds a 3-step target from starter seeds unattended and stocks 4 copies, never uses the last seed of a parent, and stops cleanly mid-run; V31 resolved"

**Verdict: met with fakes. The Lab run, the full parts answer and V31's Lab half are PC steps (`breeding_station.md`).** PR #103, merge `3845486`. Decisions: D282-D284.

| Part | Status | Evidence |
|---|---|---|
| OC program in `oc/crop/`, transposers, a Crop Breeder and Seed Generators | met with fakes | `oc/crop/station.lua` (MIT, ours), a module of the agent; `tests/oc/test_station.lua` (21 of the 161 Lua tests) [here] |
| Plan in code, every number cited (`r:`/`crop:`/`mte:`) | met | `gregtec.crops.station`; `tests/unit/crops/test_station.py` (17) and `test_station_surfaces.py` (11) [here]; `test_station_harness.py::test_three_step_plans_at_mv` on a real export: each plan's command fits the contract and one reply, every id it cites is in the DB [here] |
| Seeds identified by name, never numeric ids | met | `cropsnh:genericSeed` + label; `test_seed_labels_are_crop_name_seeds` on the real export [here]; an unknown or renamed seed is reported as `false` and its generator never used (review H1) [here] |
| **Breeds a 3-step target unattended, stocks 4** | **met with fakes; PC step** | Lua `3-step plan bred from starter seeds with failures injected, stocking 4`; `tests/oc/test_station_pipeline.py::test_unattended_from_starters_to_4_stocked` (the real core drives the real agent) [here] |
| **Never the last seed of a parent** | **met with fakes; PC step** | Lua `never the last seed: short starters wait, never loaded`, `parking is checked first ...`, and the 40-run property `never the last seed, nothing in transit after a stop, seeds conserved` [here] |
| **Stops cleanly mid-run** | **met with fakes; PC step** | `test_a_confirmed_stop_mid_run_leaves_nothing_in_transit`; Lua `a confirmed stop mid-run through the agent` and the restart-while-copying cases (review H2) [here] |
| Start and stop are confirmed actions; status over the bridge | met | `crop_start`/`crop_stop` on WO-7.1's desk; Lua `an unconfirmed start or stop never runs; nothing moves` [here]; `crop_status` and `/api/crops/station` [here] |
| **V31 resolved** | **partly** (from source, D284); **PC step** | `verify.tsv` V31 stays "Partly resolved": labels readable, stats not (gzip `tag`), progress via `hasWork`, tank via `getTankLevel`. Still open: the full parts answer from his export and the Lab reads (`breeding_station.md` A.3, C.7) |

Open: PR #103 Q1-Q5.

---

## G7 row

> **G7:** "Reviewer report; Clayton sign-off"

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Reviewer report | met | this report |
| The Phase 7 acceptances | WO-7.1 **met**; WO-7.2 to 7.6 **met with fakes** | above. Nothing is not met in code |
| Phase 7 end to end | met with fakes | the real agent on fakeoc against the real core over HTTP and M1 (`tests/oc/test_*_pipeline.py`), the harness-DB tests, and Playwright's Base view [here] |
| §10 G11 lines for Phase 7 ("OC bridge ... tested in `lab-ops`; AFK alerts while unpaused (L4)"; "the breeding station bred a target unattended in `lab-ops`") | open until the PC steps | G11 re-checks them from the paste-backs below. L4's "alerts reach Discord" waits for WO-9.1/9.2 |
| Clayton's sign-off | **PC step** | the steps below, then his sign-off |

**Required before Clayton signs G7:**
1. The PC steps below, with their paste-backs.
2. His answers, or "keep as built", to the Phase 7 questions below. Under D158 the defaults stand and are reversible, except question 5, which must be answered before an OC computer goes on Play over M2 (its default until then is no M2 on Play: Play stays on M1).
3. `main` green on every CI job at the commit he deploys, with `review/g7` merged (or both fixes otherwise in).

Should do (not blocking):
1. **Update plan §7.9's payload bullet.** It still says "watchlist items via `getItemsInNetworkById`", which D213 replaced with exact `getItemInNetwork` lookups. The "As built" notes don't mention it. Owner: the PM.
2. **A Play OC runbook.** Phase 7's runbooks are all Lab. Putting the agent, displays and station on Play (M1 at MV) is Clayton's own copy to Play's drive (breeding_station.md's intro says so) and a `[mailbox.play]` table he adds; no runbook spells out the order (token `oc-play`, `check_tweaks.py` on Play, the files, `[mailbox.play]`). It should come after question 5's answer. G4's telemetry reconcile (`gos ledger` with `tel_eu_in`/`tel_eu_out`) waits for it too.
3. **Break the `deep_lane` import cycle** (outside Phase 7, from WO-2.3 `8a53ad5`). `python -c "import gregtec.agents.deep_lane"` fails with `ImportError: cannot import name 'HANDOFF_TOOL' from partially initialized module`: deep_lane imports `gregtec.agents.tools`, whose `catalog.py` imports `HANDOFF_TOOL` from deep_lane. The core and the full suite import the catalog first, so nothing fails today. A follow-up WO could move `HANDOFF_TOOL` to a module with no GOS imports (as fix 1 did for `place`) and replace `test_alert_imports.py`'s list with a test that imports every `gregtec` module first in a fresh interpreter. Owner: the PM.

---

## VERIFY items Phase 7 touched

| ID | WO | Status now | What closes it |
|---|---|---|---|
| **V19** (Open to LAN avoids the pause freeze?) | 7.2 | Open | Step 4 below (`oc_agent.md` F) |
| **V20** (`gt_machine` on 2.9.0 stable) | 7.2 | Open | Step 2 below (`gos-agent --list`, `oc_agent.md` step 7) |
| **V23** (JourneyMap picks up waypoint files) | 7.1 (writer), 6.6 (owner) | Partly resolved from source (D271): read at the next dimension change or rejoin | Step 10 below (`ore_intel_check.md` B), unless G6's steps already ran it |
| **V29** (a reliable "game paused" signal) | 7.5 (consumer), 6.1 (owner) | Resolved from the log line (D258) | Step 8 below (`oc_agent.md` step 40) is extra evidence that the alerts hold on pause |
| **V31** (OC reads for the breeding station) | 7.6 | Partly resolved from source (D284) | Step 9 below (`breeding_station.md` A.3 parts, C.7 reads) |
| **V34** (a GT shutdown reads as `isWorkAllowed` false, so `stopped`) | 7.4/7.5 (evidence), 6.5 (row's WO) | Open | Step 7 below (`oc_agent.md` step 31) |
| runbook VERIFYs (no `verify.tsv` row) | 7.4 | Open | OCGlasses method names (step 34) and the sensor lines' `Problems: N` format (step 35), both in step 7 below |

`tests/unit/test_verify_list.py` keeps `verify.tsv` and plan §13.2 in step (it ran in the full run). No row was changed here.

---

## Open questions from Phase 7's PRs

No later decision answers them, except PR #99 Q1 (settled by #98, above) and PR #62 Q1 (adopted by WO-7.1 in D221). D213-D221 are recorded with the advisor's agreement; D277-D281 say "Pending reviewer (PR questions)"; D282-D284 say "Clayton's later review open". The defaults stand, reversible, until Clayton answers (D158). PR text is quoted here as data.

**PR #55** (WO-7.2), https://github.com/crazie450-prog/GretecOS/pull/55
1. Q1 (D213): one exact `getItemInNetwork` lookup per watched entry replaces plan §7.9's `getItemsInNetworkById`; an NBT-tagged stack is another item and isn't counted. Confirm?
2. Q2 (D213): `max_watch` at most 64 (was 256); more costs a slower `ae2_s`. OK?
3. Q3 (D214): the agent keeps a message under 96 KB; the core accepts 256 KB. OK?
4. Q4 (D214): a craft OC lost track of is `failed` with a fixed "may still run" text, shown as "check AE2", not a new state. OK?
5. **Q5 (D214, also PR #64 Q11): nothing tells the agent a reply came from the core.** A program that binds the core's port while the core is down gets the token and can make the agent run an allowed command unconfirmed. **May Play's agent use M2, or should Play stay on M1 (gos-dev can't write its `inbox.json`), or should the core sign each command (a contract `v` bump)?** This one must be answered before Play gets M2. **Default until he answers: no M2 on Play.** Play stays on M1 (gos-dev can't write its `inbox.json`); "M2 as built" is not a default for Play. Lab keeps M2.
6. Q6 (D213): `--once` runs on for at most 30 s when the reply carried commands, to report their results. OK?
7. Q7: the earlier calls the advisor agreed with (Lua 5.3 CPU, ASCII JSON, LSC by meta name, cost limits, 8 KB sensor budget, 32 CPUs and 16 crafts, at-most-once delivery, Lua on Linux CI only, sensor parsing in Python). Keep?

**PR #62** (WO-7.3), https://github.com/crazie450-prog/GretecOS/pull/62
8. Q1 (D217): an agent is (binding, address); WO-7.1 passes its token's instance as the binding (done, D221). Right?
9. Q2 (D217): a folder swap is caught after the step and stops the mailbox, but one step can land elsewhere first (no POSIX-only `dir_fd`). Acceptable?
10. Q3 (D217): a 2 s quiet period after a withdrawn reply. OK?
11. Q4 (D216): OC's used-space count falls a little each exchange; the Lab `df` lines check it.
12. Q5: the runbook restarts the core before the hour, rather than per-boot stats in the core. OK?
13. Q6: the 30 s reply TTL, the ack as the newest boot's highest seq, and acknowledging by renaming back to `outbox.tmp`. Keep?

**PR #64** (WO-7.1), https://github.com/crazie450-prog/GretecOS/pull/64
14. Q1-Q10: earlier calls the advisor agreed with (`internal` for the rate limit; samples on change plus a 60 s heartbeat, kept 7 days; `action_propose` deep lane only; a confirmed action not collected in 5 min fails unrun; the computer chosen only from fresh telemetry; admin can cancel but not confirm; OC tokens aren't principals; `tel:` citations; `mte_id`/`dim` filled by WO-7.4; the journal line format). Keep?
15. Q11: = question 5.
16. Q12 (D220): a plain "confirm" within 3 s of a proposal is read back again instead of taken. Is 3 s right for the TTS length? Buttons aren't affected.
17. Q13: at most 16 OC computers per instance until the core restarts. Enough?
18. Q14: an unauthenticated POST still costs one token-table read, so bad requests can't lock the real computers out. OK?

**PR #95** (WO-7.4), https://github.com/crazie450-prog/GretecOS/pull/95
19. Q1 (D277): `v` stays 1; displays sized for 50 x 16; HUD on M2 only; `poll_s` 2 while an action waits; the ≤ 10 s guarantee is at `cycle_s` 5. Acceptable, or refuse `cycle_s` 10 with displays on?
20. Q2 (D278): notices are fixed templates from the desk's outcome, never OC error text, said by the running computer if it has a chat box, else the binding's one chat-box computer, else the first that hears it; none for waypoints. OK?
21. Q3 (D279): `stopped` (work disabled that GOS didn't cause) is a warn problem, so a machine he turns off with a soft mallet shows too. Warn or info?
22. Q4 (D279): `ActionDesk.paused` scans the binding's finished pause/resume actions on each read, with no index. OK?
23. Q5: `oc_displays.spec.ts` relies on running after the chip specs; a test-only telemetry reset could follow. OK for now?

**PR #99** (WO-7.5), https://github.com/crazie450-prog/GretecOS/pull/99
24. Q2 (D280): holds run on message time; a machine missing from a full snapshot counts as good at that time; a gap under `STALE_S` between two readings counts toward the hold. Agree?
25. Q3 (D281): rows left firing by a previous engine become `lost`, not `cleared`. Agree?
26. Q4 (D281): one combined voice line per tick; quiet hours and dedupe across channels stay with WO-9.2. OK?
27. Q5 (D280): setting only `power_low_pct` moves the clear end to +10 points. OK?
28. Q6: stopped counts as stalled; crafting done isn't an AlertCard; no `tel:` in a card's `ref`. Keep?
29. Q7: commit attribution followed the session's reminder. Noted.
30. The `[alerts]` defaults (D280, listed under WO-7.5 above): keep or change?

**PR #103** (WO-7.6), https://github.com/crazie450-prog/GretecOS/pull/103
31. Q1 (D282): a starter is parked only while `keep`+1 are in the chest; a crop the plan breeds may be parked as its only seed (it can be bred again). Is that the trade you want?
32. Q2 (D282): every configured copier is paused each wind-down or wait step, which overrides a generator switched on by hand while a plan runs. OK?
33. Q3: `copy_fert` and `cat_left` are optional contract additions (v 1). Should `cat_left` get a line in the station panel (a board change, D92)?
34. Q4: the station reads the tank's level, not the fluid's name. Read `getFluidInTank` names in a follow-up once the Lab run shows what OC reports?
35. Q5: D282-D284 record the advisor's verdicts. Noted.

**Boards (D92):** the Base view and its Alerts list (WO-7.4, 7.5) have no approved board; the approval card (WO-7.1) is WO-2.6's ApprovalCard; the Crops view is approved, but its station panel (WO-7.6) is new. Each "needs Clayton's board approval" (step 11).

---

## Clayton's PC steps before signing G7

These run after G2's PC steps 0 to 3 (`docs/reviews/G2_review.md`: Node.js and pnpm, `phase1_pc_checks.md` steps 1 to 12 with the export and the DB build, the setup wizard and the API key), G5's steps 0, 1 and 6 (`docs/reviews/G5_review.md`: the voice token, devices and keys, `gos voice run` answering). G6's gate report is not written yet, so nothing here relies on its steps: the one Lab watcher precondition is stated where it is used (step 8: `[startup] watchers_with_play = "lab"`, the default in `config.py`, with the Lab watchers running as `oc_agent.md` step 36 says), and WO-7.1's JourneyMap check is step 10. They don't repeat those steps, and they don't repeat the runbooks: each step names the runbook section to follow. `$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe`, as in the runbooks. Use a normal PowerShell window in his own account (never "Run as administrator"), with `$ProgressPreference = 'SilentlyContinue'`. **Everything is in GTNH-Lab's `lab-ops` world; nothing touches Play.** In all, about 6 h, most of it unattended (two OC hours and the breeding run).

Spending: the deep-lane proposals in steps 3, 7, 8 and 9 are a few cents each (`action_propose` and `crop_station` are deep lane). Nothing else calls a model.

**0. Deploy a green `main`** with `review/g7` merged, every CI job green on that commit. In the profile clone: `git pull`, then `uv run gos deploy` (this also covers G8's step 0 if he does them together). `& $gos status` shows the core `ok`. The OC files are then in `C:\GregtecOS-app\current\oc\` (with `oc\crop\station.lua`).

**1. Tweaks and token** (`oc_agent.md` A, steps 2-3). With the game closed:
```powershell
& C:\GregtecOS-app\current\.venv\Scripts\python.exe C:\GregtecOS-app\current\scripts\check_tweaks.py C:\Games\PrismLauncher\instances\GTNH-Lab
& $gos token new oc-lab --show
```
`oc_loopback`, `oc_timeout` and `oc_rules` must read `ok` (else the same line with `--apply`, Lab only). Keep the token window open.

**2. The computer and the first run** (`oc_agent.md` B-D, steps 4-8, **V20**). Build the T3 computer with a Lua 5.3 CPU, an internet card, adapters on an EBF, a battery buffer, the LSC and the ME controller, a chat box and a tps card; copy the agent with step 6's PowerShell; `gos-agent --list`, then `gos-agent --once`. Note whether `gt_machine` appears and what type the LSC shows as.

**3. One hour on M2, and the confirm flow** (`oc_agent.md` E, steps 9-15; the WO-7.2 acceptance). `/forge tps` three times before, then every 15 min during the hour. During the hour, check WO-7.1's flow on the dashboard and by voice (with `& $gos voice run` up), **in place of `oc_agent.md` step 12** (these checks cover its say, pause/resume and craft, so don't do both):
- Ask the deep lane "say hello in Lab chat". It reads back `Say "hello" in chat in Lab. Say confirm.` **Don't confirm:** after 60 s the card says it expired, and saying "confirm" then does nothing.
- Ask again, then, before confirming, ask "pause the EBF": GOS says the first was cancelled and reads back the second. Say "confirm" more than 3 s after the read-back: the EBF pauses within about 10 s and the action shows `done`. Resume it the same way.
- Confirm one small craft by the card's button.

**Pass:** the agent still runs after the hour, no `too long without yielding` or `not enough memory`, `failed` 0 in the `alive:` lines, the watched ingot counts match the ME terminal, no TPS reading drops by more than 1, and each check above behaves as written.

**4. Open to LAN** (`oc_agent.md` F, steps 16-17, **V19**).

**5. A big network** (`oc_agent.md` G, steps 18-21).

**6. One hour on M1** (`oc_agent.md` H, steps 22-28; the WO-7.3 acceptance). Copy `mailbox.lua`, `transport = "mailbox"`, `[mailbox.lab]` in gos.toml, `df` and `--once`, then **restart the core** (step 26) before the hour. **Pass** (step 27): the agent's `failed` 0 and `written` about 720, the core's `accepted` equal to `written`, `seq_gaps` equal to the agent's waits, `refused_bad_payload`, `withdrawn` and `poll_errors` 0. Set `transport` back to `"auto"` after (step 28).

**7. Displays** (`oc_agent.md` I, steps 29-35; the WO-7.4 acceptance, **V34** and the two runbook VERIFYs). Copy `display.lua`; check the board from 3 blocks away; time the EBF's `stopped` and its return to `OK` over M2 and M1 at `cycle_s` 5 (each ≤ 10 s); a starved basic machine reads `stalled`; Esc for 45 s gives `NO REPLY`; the pause and craft notices come once each; the HUD over M2 (OCGlasses at HV); a broken maintenance part gives `1 maintenance problem`.

**8. Alerts, AFK** (`oc_agent.md` J, steps 36-40; the WO-7.5 acceptance). `pauseOnLostFocus` off, `[startup] watchers_with_play = "lab"` (the default) and the Lab watchers running (`oc_agent.md` step 36), the voice client on, the dashboard on monitor 2; leave the game **unfocused with no menu open** while the LSC falls below 20 % (or set only `[alerts] power_low_pct` above the fill and restart the core). **Pass:** Power low, Machine stalled (or stopped) and Maintenance each fire within their hold and are said once; nothing flaps for 10 minutes; crafting done is said once; on Esc they are held, not cleared.

**9. The breeding station** (`breeding_station.md` A-F; the WO-7.6 acceptance and **V31**). `gos quests tier --set MV` (Lab only; `--clear` after), `gos crops have ...`, then `gos crops station "<target>"` until a plan says `in 3 step(s)`; `gos crops station --parts` (note any `NOT`, `MV unknown` or `not in this DB`); build the MV breeder, three MV generators, the transposer and the chest; copy `crop\station.lua`; the V31 reads in the OC shell; an unconfirmed start expires with nothing moved; a confirmed run to 4 stocked, unattended; a stop mid-run; a restart while copying. **Pass:** 4 or more of the target, no parent's chest count ever at 0 because of the breeder, `stopped; nothing of the station's is left in transit`, and the generators paused after the restart.

**10. The waypoint (V23), only if G6's PC steps haven't run it** (`ore_intel_check.md` B, in `lab-ores`; WO-7.1's waypoint action). **Pass:** the confirmed waypoint shows in JourneyMap after the next dimension change or rejoin (D271).

**11. Boards.** On the dashboard, look at the Base view with its Alerts list, the action approval card and the Crops view's station panel, and approve each or redline it (D92, D158 (5)).

**12. Sign-off.** Answer question 5, and say whether to keep each other default listed above.

**What to paste back for G7:**
- the runbooks' own "What to send back" lists: `oc_agent.md` (sections D to J) and `breeding_station.md`;
- step 3's three confirm-flow outcomes;
- `ore_intel_check.md`'s B outcome if step 10 ran;
- the board verdicts (step 11) and his answers to the questions.

---

## Risks

1. **An unauthenticated reply can run an allowed command on the agent** (question 5, D214). Only while the core is down and another program holds its port, and only `craft` up to `craft_max`, a pause or resume, a say or a station start or stop. It is a residual of D117 (4) inside D82. Lab only for now; Play must not get M2 until he answers. (Possible, only with a hostile local program)
2. **Pause freezes OC.** In single player the Esc menu pauses the integrated server and OC (V19 may say Open to LAN avoids it), so AFK alerts work only with no menu open and `pauseOnLostFocus` off (D46, R7). The alerts hold rather than fire while paused, by design. (Confirmed by design)
3. **The tick cost is only measured in Lab.** The fake counts ticks; real AE2 lookups grow with the network's item types. Step 5 measures it on 300+ types. (Unknown until step 5)
4. **OCGlasses and the sensor-line format are modelled from sources, not seen in game** (runbook VERIFYs in step 7). A wrong method name turns the HUD off with `display: no HUD ...`; a wrong Problems format leaves maintenance alerts silent. (Possible)
5. **The station keeps a parked seed in each generator for good** (GT lets automation take only from outputs, D282), and breeding at MV is 50 %, so a 3-step run can take an hour or more. The runbook says to use spare generators. (Confirmed, by design)
6. **Import-order defects hide in the full suite.** Fix 1 was invisible to CI because another test imported the catalog first. `test_alert_imports.py` now guards the four modules involved. One more cycle exists outside Phase 7: `gregtec.agents.deep_lane` imported first fails (deep_lane → `agents/tools/__init__` → `catalog.py` → `from gregtec.agents.deep_lane import HANDOFF_TOOL`), from WO-2.3 (`8a53ad5`); a fresh-interpreter import of all 276 `gregtec` modules finds no other (should-do 3). (Fixed for these modules; deep_lane open, not a G7 blocker)

## After the review

- The full `pytest` after both fixes finished green (9352 passed, 316 skipped).
- CI, final conclusions: run 43571468861 on `e481ca4` (`main`'s head): ubuntu, windows, mod and dashboard all **success** (mod finished 08:22Z). Run 43574239243 on `aae4046` (PR #106): ubuntu, windows, mod and dashboard all **success**; its Windows job is the first real check of the stress-floor fix on Windows, and it passed.
- Review of this report (`a093ef8`): `test_alert_imports.py` now also covers `gregtec.advisor.steps` (it failed the same way on `e481ca4`); the report no longer leans on a G6 report that isn't written yet (V23 is step 10 here, conditional on G6's steps not having run it, and the watcher precondition is stated in step 8); step 3 replaces `oc_agent.md` step 12; question 5 states its default (no M2 on Play); the `deep_lane` cycle is named in Risk 6 and should-do 3. Full `pytest` after it: 9353 passed, 316 skipped.
- Fixes on `review/g7`: `553979c` (the stress-test floor) and `d7b7241` (the import cycle and its test). No decision row was added (D293 unused): both are test-evidence fixes inside the phase's acceptance, with no behaviour change in the running core.
