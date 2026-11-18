# Gregtec OS: G9 gate review (Phase 9, Discord, alerts and phone)

2026-11-18 · independent gate reviewer (D41, D158 (2)) · reviewed: `origin/main` at `7f0b4a1` (PR #119 merged). It covers the Phase 9 PRs #107 (WO-9.1, merged as `601c6ef`), #111 (WO-9.2, `42366b6`) and #119 (WO-9.3, `7f0b4a1`). `main` has since moved to `705c88a` (#109, WO-10.4) and then `ee7449c` (#118, WO-10.6, the current head). Neither touches a Phase 9 file (`ee7449c` changes the shared `cli.py`, `verify.tsv` and `decisions.md`, none of it Phase 9's); this review's branch merges `ee7449c` and everything was re-run on it (below).

## Verdict

**G9 is ready for Clayton's sign-off at the deployment test, with fakes standing in for Discord, Tailscale, his phone and his keyboard.** All three Phase 9 work orders are built and merged. Every acceptance criterion that can be checked without a real Discord bot, a real tailnet, a phone on cellular and Windows' input-idle counter is met, either here or on CI. No criterion is "not met" in code. Each §8 acceptance is a check from his phone, so every row below is **met with fakes**, and the PC steps below are the acceptance.

What the PC steps must show (D158 (2) batches them with the other gates):
1. **WO-9.1:** each Discord command answers from his phone, on cellular, within 10 s (long design jobs are acknowledged within 10 s); a second account's commands and button presses are ignored ([QA:V4], [QA:V9]).
2. **WO-9.2:** the routing checks in `alert_routing.md`, including Esc-then-walk-away (one DM about 10 minutes after Esc), with V35 (GOS's windowless processes see his real input) first.
3. **WO-9.3:** on cellular through the tailnet, a Discord plan link opens, a Lab action is confirmed on the phone, status updates live, and with Tailscale off the dashboard is unreachable (L8, V28).

Evidence tags, as in G0 to G8:
- **[repo]:** checked in the repo, the git history or GitHub's CI logs.
- **[here]:** run by this reviewer in the build container (Linux, 4 shared CPUs, no Windows, no Discord, no Tailscale, no phone).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC or phone. Nothing from the PC is in the evidence yet for Phase 9.

### This reviewer's runs [here]

The runs in this table are on `7f0b4a1`. The re-run on this branch's head after merging `ee7449c` (the PR reviewer's at `b18609e`, then this reviewer's on the final head) is at the end of the table.

| Check | Result |
|---|---|
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 596 files already formatted |
| Phase 9 unit and security tests (`tests/unit/alerts`, `tests/unit/discordbot`, `tests/unit/remote`, `tests/security/test_discord_foreign_users.py`, `tests/security/test_phone_dashboard.py`, `tests/unit/test_alert_imports.py`, `test_alert_rules.py`, `test_verify_list.py`) | **508 passed, 2 skipped** (the two skips are the Windows-only `GetLastInputInfo` tests, which ran green on CI's Windows job) |
| full `pytest` (default groups, as CI) | **10751 passed, 329 skipped**, exit 0 (9 min 2 s) |
| `./gradlew --no-daemon --max-workers=2 runServer -Pharness` (fresh, in this worktree) | `harness passed: 37 checks, run 20261118T160409Z` (2 min 15 s) |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration/test_discord_harness.py` on that run | **1 passed** (2 min 19 s): a fake interaction from Clayton through the bot's door and its real HTTP client to a real core on the harness DB; `/plan` is queued at once, the result carries the routes in rank order with `plan:` ids, the PNG, the BOM and the phone link; after a core restart on the same DB the job is neither run nor delivered again |
| `pnpm test` (vitest) | 20 files, **337 passed** |
| `pnpm run build` | ok |
| `pnpm exec playwright test` (the whole suite, 2560x1440 and 390x844, on spare ports, with Gradle running beside it) | **77 passed** (2.5 min), `phone.spec.ts` (5 tests) and `designer.spec.ts` among them |
| `gos tailnet` with `tests/fixtures/fake_tailscale.py` as the `tailscale` on `PATH`, scratch `GOS_HOME` | `setup --funnel`: "Refused: GOS never turns on Tailscale Funnel", exit 3. `setup`: "https://gamingpc.tail1234.ts.net/ now proxies to http://127.0.0.1:8765", "Funnel is off", and the `allowed_hosts` line to add (gos.toml untouched). `check` before the name was added: four `ok`, one `BAD ... isn't in [core] allowed_hosts`, exit 1; after: five `ok`, exit 0. `off`: removed; `check` then `BAD ... isn't served to the core`. The fake CLI's call log has `serve --bg --https=443 http://127.0.0.1:8765` and `serve --https=443 off`, and no `funnel` call anywhere |
| `gos discord check` in a scratch `GOS_HOME` | with no `[discord]`: "owner_id is missing: your Discord user id (...)", exit 2. With `owner_id`, `guild_id` and the tailnet name in `allowed_hosts`: `owner_id: set; guild_id: set`, `phone links: https://gamingpc.tail1234.ts.net`, and the two missing secrets named (`gos key set discord`, `gos token new discord`), never a value; exit 1 |
| `gos alerts routed` with no core | "The core isn't running: start it (gos serve).", exit 1 |
| **Re-run after merging `ee7449c`** (PR reviewer, at `b18609e`) | ruff and format clean (608 files); full `pytest` **10828 passed, 338 skipped**; `pnpm test` 337 passed, build ok; Playwright **77 of 77**; server harness 37 checks; `test_discord_harness.py` 1 passed |
| **Re-run on the final head** (this reviewer; it differs from `b18609e` only in this report) | ruff and format clean (608 files); full `pytest` **10828 passed, 338 skipped**, exit 0 (8 min 30 s). Gradle, the harness and Playwright were not re-run: no code changed since `b18609e` |

No DB, export, token or keyring entry from these runs is committed. They stay in the session's scratch folder.

### CI [repo]

| Commit | Run | ubuntu | windows | mod | dashboard | dashboard (Windows) |
|---|---|---|---|---|---|---|
| PR #107 head `af55e84` | 43595053747 | success | success | success | success | (path filter) |
| `601c6ef` (#107 merged, WO-9.1) | 43603891662 | success | success | **cancelled** | success | (path filter) |
| `8a07a60` (#110, WO-10.1, next on `main`) | 44215802439, 44215802456 | success | success | success | success | success |
| PR #111 head `e1a2904` | 44318439175 | success | success | success | success | (path filter) |
| `42366b6` (#111 merged, WO-9.2) | 44328264420 | success | success | success | success | (path filter) |
| PR #119 head `a0e9fcc` | 44323082821, 44323082842 | success | success | success | success | success |
| `7f0b4a1` (#119 merged, WO-9.3) | 44332258659, 44332258528 | success (10758 passed, 322 skipped) | success (10775 passed, 305 skipped) | success | **failure** (risk 1) | success |
| `705c88a` (#109, WO-10.4) | 44342011407 | success | (running at writing) | (running at writing) | success | n/a |
| `ee7449c` (#118, WO-10.6, current head) | 44344126201 | (running at writing) | (running at writing) | (running at writing) | (running at writing) | n/a |

The `mod` job on `7f0b4a1` (job 134899498413) ran the server harness (37 checks), the listener harness (19), two client runs (63 each), `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration` (**237 passed, 21 skipped**, `test_discord_harness.py` among them) and the client-link run (80 checks).

`601c6ef`'s `mod` job was cancelled when the next push to `main` superseded it; `8a07a60`, which contains it, is green on every job.

`7f0b4a1`'s `dashboard` failure is not a Phase 9 test. It is WO-3.7's `designer.spec.ts:135` ("every theme reads at WCAG contrast, and nothing is cut off on the phone"): `.designer-route .react-flow__node` was still `hidden` after 10 s at 390x844. The same test failed the same way on `d896ee1` (run 43588012389, 2026-11-14, before any Phase 9 merge), passed on PR #119's head, on `7f0b4a1`'s Windows dashboard job, on `705c88a`, and here (77 of 77). It is an intermittent wait on the React Flow layout, not a regression (risk 1).

---

## Phase 9 work orders

### WO-9.1: Discord bot, commands, job queue

> **Acceptance:** "Each command answers from his phone within 10 s (long design jobs acknowledged within 10 s); commands and clicks from other users are ignored"

**Verdict: met with fakes. The from-the-phone timing and the second-account check are PC steps (`discord_bot.md` C).** PR #107, merge `601c6ef`. Decisions: D295-D297 (each "Reviewer agreed").

| Part (plan §8 row 9.1, §7.12) | Status | Evidence |
|---|---|---|
| The bot as its own process, supervised, stopped until set up | met with fakes; **PC step** | `core/gregtec/discordbot/` (`python -m gregtec.discordbot run --supervised`, D296); `tests/unit/discordbot/test_settings.py`; `gos discord check` here [here]. The real start is `discord_bot.md` B4-B5 |
| No gateway intents; reads no messages | met | D296; only `adapter.py` imports discord.py; `test_adapter.py` [here] |
| Commands: `/ask`, `/plan`, `/power`, `/status`, `/alerts`, `/persona` | met with fakes | `test_handlers.py` [here]; `test_discord_harness.py` on the harness DB [here] |
| **Each command answers within 10 s; long design jobs acknowledged within 10 s** | **met with fakes; PC step** | `test_every_command_answers_within_10_s_even_when_the_core_is_slow` and `test_a_long_design_job_is_acknowledged_at_once_and_its_result_follows` (fake clock: acknowledged at once, "Still working" at 8 s) [here]. On his phone on cellular: `discord_bot.md` C1-C7 (step 3 below) |
| Durable job queue (never completes twice; re-run once after a crash) | met | `gregtec.remote.jobs`, table `remote_job` (D295); `tests/unit/remote/test_jobs.py`, `test_routes.py`; `test_a_job_queued_before_a_bot_restart_is_delivered_once_by_dm`; the harness test's restart [here] |
| Replies attach the PNG diagram and BOM plus a phone-dashboard plan link [QA:V4] | met with fakes; **PC step** | `test_discord_harness.py`: route 1's `flow.png`, `bom.tsv` and the `#/designer/<id>` link, routes in rank.py's order with `plan:` ids [here]. The link opening on the phone is WO-9.3's E.1 (step 5 below) |
| **Clayton only on every command and interaction** [QA:V9]; text relayed to `/api/ask` only from him (D220) | **met with fakes; PC step** | `tests/security/test_discord_foreign_users.py`: every interaction kind × every command and button from another user, a stranger's word-for-word "confirm", a stranger pressing Clayton's button; each dropped before any core call, with no response [here]. With a second account: `discord_bot.md` C9 |
| Confirm buttons (read-back, one pending, 60 s, confirmed as `discord`) | met with fakes; **PC step** | D297; buttons call `/api/actions/{id}/confirm|cancel` with the `discord` token, so the action desk's rules apply (WO-7.1) [repo]. `discord_bot.md` C8 |
| No-AI mode | met with fakes; **PC step** | `test_no_ai_mode_says_what_needs_a_model_and_lookups_still_work` [here]; `discord_bot.md` C11 |
| Answers to the journal through `VaultIO` | met | D297; `test_handlers.py` [here] |

Open: PR #107's eight judgement calls (advisor agreed) and its two later notes (below).

### WO-9.2: Alert routing

> **Acceptance:** "Test matrix passes, including Esc-then-walk-away for the paused DM"

**Verdict: met with fakes. The test matrix passes; the real DMs and V35 are PC steps (`alert_routing.md`).** PR #111, merge `42366b6`. Decisions: D298-D300.

| Part (plan §8 row 9.2, §7.12 "Alerts") | Status | Evidence |
|---|---|---|
| One router: Discord DM, dashboard, voice, in game | met | `gregtec.alerts.policy.decide` (one table) and `gregtec.alerts.router.AlertRouter`; channels `dashboard`, `voice`, `in_game`, `discord`; `test_core_routing.py` (voice line to the voice client only, away → the DM outbox, paused from the watchers' feed, who may use the routes, the health strip's Presence) [here] |
| **The test matrix** | **met** | `tests/unit/alerts/test_routing_matrix.py::test_matrix` (kind × present/away × quiet × repeat), `test_matrix_bot_up_or_down`, `test_a_base_alert_goes_again_after_its_cooldown`, `test_every_alert_has_a_level_and_a_key` [here] |
| **Esc-then-walk-away (the paused DM, D76)** | **met with fakes; PC step** | `test_esc_then_walk_away_dms_once_at_ten_minutes`, `test_away_then_paused_dms_at_the_pause`, `test_back_and_away_again_in_one_pause_is_still_one_dm`, `test_esc_then_walk_away_through_the_watchers_real_feed` (`GameFeed.receive` at the watchers' 10 s heartbeat), `test_watchers_unheard_for_a_while_dont_lose_the_paused_dm`, `test_no_paused_dm_again_after_a_core_restart` [here]. Real: `alert_routing.md` §3 |
| Dedupe (once per occurrence, also after a restart), cooldown, quiet hours, `/alerts off|quiet` | met | the `alert_route` table; `test_delivered_once`, `test_after_a_restart_a_key_is_adopted_until_its_binding_settles`, `test_quiet_hours_hold_a_warn_until_they_end`, `test_quiet_hours_wrap_midnight`, `test_alerts_off_stops_every_dm`, `test_alerts_quiet_holds_all_but_err` [here] |
| Never both voice and DM for one occurrence | met | `test_said_while_present_is_not_dmed_when_he_walks_away`, `test_dmed_while_away_is_not_said_when_he_comes_back` [here] |
| AFK base alerts (D46) | met with fakes; **PC step** | the matrix's base rows; `test_a_held_telemetry_alert_says_so_in_its_dm` [here]. Real: `alert_routing.md` §4 |
| Credit DMs with buttons (D48) | met with fakes; **PC step** | `test_the_credit_dm_carries_the_banners_buttons`, `tests/unit/discordbot/test_alerts.py` (No-AI through the decision API, Open billing, Stop asks once more, an old cycle's button, a foreign user's click ignored) [here]. The button calls `POST /api/credits/decision` as the `discord` client, which answers G2's Q38 [repo]. Real: `alert_routing.md` §5 |
| "GOS offline" while away (D76), from the bot | met with fakes; **PC step** | `test_offline_while_away_is_dmed_once_after_a_minute`, `test_stopped_on_purpose_is_not_offline`, `test_present_never_dms_offline`, `test_no_second_offline_dm_after_a_bot_restart`, `test_the_bot_reports_a_real_core_that_died` (a real HTTP core shut down) [here]. Real: `alert_routing.md` §6 |
| Away = 10 minutes without input (`gregtec.presence`, V35) | met with fakes; **PC step** | `test_away_is_ten_minutes_without_input`, `test_off_windows_presence_is_unknown`; the two Windows-only tests (`test_on_windows_get_last_input_info_answers`, `test_windows_idle_leaves_the_schedulers_input_check_working`) ran on CI's Windows job; `test_windows_idle_never_touches_the_shared_windll` on every OS [here, repo]. Real: `alert_routing.md` §1 (V35) |
| WO-2.5's idle maintenance unharmed (the PR's HIGH fix) | met with fakes; **PC step** | the regression tests above. On the PC: no "maintenance tick failed" in the core's log (step 6 below) |

Open: PR #111 Q1-Q9 (below). The `[notify]` defaults are pending Clayton.

### WO-9.3: Phone dashboard

> **Acceptance:** "On cellular via the tailnet: a Discord plan link opens, a Lab action is confirmed, status updates live; outside the tailnet the dashboard is unreachable; the security tests pass"

**Verdict: met with fakes. "The security tests pass" is met; the four phone checks are PC steps (`phone_dashboard.md` E, L8).** PR #119, merge `7f0b4a1`. Decisions: D301-D303.

| Part (plan §8 row 9.3) | Status | Evidence |
|---|---|---|
| Tailscale Serve to `127.0.0.1:8765` (V28), never Funnel | met with fakes; **PC step** | `gregtec.tailnet`; the fake-CLI run above [here]; `test_setup_serves_the_core_to_the_tailnet_and_checks_funnel_is_off`, `test_funnel_is_refused`, `test_setup_refuses_while_funnel_is_on`, `test_funnel_in_a_foreground_session_counts_too`, `test_setup_undoes_a_handler_that_came_up_with_funnel`, `test_a_raw_tcp_forward_to_the_core_is_caught`, `test_gos_dev_cant_change_clayton_s_tailscale` [here]. Real: `phone_dashboard.md` A-B |
| The core still listens on loopback only | met | `test_the_core_listens_on_loopback_only` [here] |
| **"The security tests pass"** | **met** | `tests/security/test_phone_dashboard.py` (26 functions, parametrised): foreign Host and Origin, a look-alike name, the name with another port, missing, forged, unpaired and revoked sessions (the socket closes with 1008), the name refused until it is in `allowed_hosts`, the Funnel header refused even with an admin token [here]; green on Ubuntu and Windows CI at `7f0b4a1` [repo] |
| The WO-2.0 phone layout; five tabs plus More (D159, D302) | met with fakes; **board approval** | `phone.spec.ts` "five tabs and More; every other view opens through More" (all 8 views, More lit, no sideways scroll, contrast) [here]. D302's tab choice and the More view have no approved board (step 7 below) |
| Device pairing (cookie Secure, HttpOnly, host-only, SameSite=Strict); revoke | met with fakes; **PC step** | `test_the_phone_pairs_once_and_then_reads_live_status`, `test_a_device_can_revoke_itself_from_the_phone`; `phone.spec.ts` checks the cookie [here]. Real: `phone_dashboard.md` C |
| **A Discord plan link opens** | **met with fakes; PC step** | `phone.spec.ts` "a Discord plan link opens the plan once the phone has paired over the tailnet" (fake TLS Serve on a tailnet name) [here]. Real: E.1 |
| **A Lab action is confirmed** (as `phone`, D220) | **met with fakes; PC step** | `phone.spec.ts` "a Lab action proposed on the phone is confirmed on the phone" (`confirmed_via: phone`) [here]. Real: E.2 |
| **Status updates live** | **met with fakes; PC step** | `phone.spec.ts` "status updates live on the phone" [here]. Real: E.3 |
| **Outside the tailnet it is unreachable** | **met with fakes; PC step** | `phone.spec.ts` "outside the tailnet the dashboard is refused" proves only that a request under another tailnet *name* gets a 403; with the Host tests above it covers the core's side [here]. Real unreachability rests on the loopback bind (`test_the_core_listens_on_loopback_only`) and on Serve being tailnet-only (V28), and E.4 (Tailscale off on the phone) is the only real network-level check |

Open: PR #119's D301-D303 questions (below).

---

## G9 row

> **G9:** "Reviewer report; Clayton sign-off"

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Reviewer report | met | this report |
| The Phase 9 acceptances | **met with fakes** | all three WOs above. Nothing is not met in code |
| Phase 9 end to end | met with fakes | `test_discord_harness.py` (bot → real core → designer on the harness DB → PNG, BOM, phone link) and `phone.spec.ts` (a plan link through a fake Serve, a phone confirm, live status) [here] |
| Clayton's sign-off | **PC step** | the steps below, then his sign-off |
| §10's G11 line "Discord and phone: commands, alerts, credit DMs and away-only offline DMs from his phone; phone dashboard over Tailscale (L8)" | open until the PC steps | steps 3 to 5 below are that line |

**Required before Clayton signs G9:**
1. The PC steps below, with their paste-backs.
2. His answers, or "keep as built", to the Phase 9 questions below. Under D158 the defaults stand and are reversible.
3. `main` green on every CI job at the commit he deploys. `7f0b4a1` is not: its `dashboard` job failed on the WO-3.7 flake (risk 1). Deploy a later commit whose jobs are all green.

Should do (not blocking):
1. **Make `designer.spec.ts:135` wait for the layout** (risk 1). It has now failed `main`'s dashboard job twice (`d896ee1`, `7f0b4a1`). Owner: WO-3.7's, or the PM. This review leaves it unchanged: it is not a Phase 9 file.
2. **Record the reviewers' verdicts on D298, D299, D300, D301 and D303.** Their rows on `main` still open with "Pending reviewer (PR questions)", while PRs #111 and #119 say the reviewer agreed. The log is append-only, so a later row (the PM's) should record those verdicts; this report lists them under the questions.
3. **L8 says "confirm a craft"; the runbook offers a waypoint or a craft.** Step 5 below asks for the craft when the OC agent is up in `lab-ops` (G7 steps 1-3), and the waypoint otherwise. The PM may align L8's wording.
4. **One `GetLastInputInfo` reader** (PR #111 Q9): the scheduler could use `presence.system_idle()`. Cleanup only.

---

## VERIFY items Phase 9 touched

| ID | WO | Status now | What closes it |
|---|---|---|---|
| **V28** (Tailscale Serve: tailnet only, Host kept, Funnel refused) | 9.3 | Partly resolved from Tailscale's docs and source (D301; tailscale.com/kb/1242, kb/1312, `ipn/ipnlocal/serve.go`) | Step 2 (`gos tailnet check`, five `ok`) and step 5 (E.4: unreachable with Tailscale off). Then V28 and L8 are resolved |
| **V35** (the windowless core and bot see his real input) | 9.2 | Open; `presence` reads `unknown` off Windows, and the health strip shows it | Step 4, `alert_routing.md` §1: `present` while he types, `away: true` after 11 idle minutes |
| **[QA:V4]** (the Discord plan link must be reachable) | 9.1, 9.3 | Met with fakes (the harness test's link; `phone.spec.ts`'s plan link) | Step 3 (C3) and step 5 (E.1) |
| **[QA:V9]** (Clayton-only checks on buttons too) | 9.1, 9.2 | Met with fakes (`test_discord_foreign_users.py`, the credit-click test) | Step 3 (C9) and step 4 (§5.3) with a second account |
| **L8** (plan §9 Lab scenario: phone over Tailscale on cellular) | 9.3 | Open | Step 5 (`phone_dashboard.md` E) |
| **L4** ("alerts reach Discord", the `lab-ops` AFK scenario) | 9.2 (Discord part), 7.5 (owner) | The routing is met with fakes; the AFK telemetry is G7's step 8 | Step 4 (`alert_routing.md` §4) |

Phase 9 added V35 and rewrote V28 in `core/gregtec/verify.tsv`; `tests/unit/test_verify_list.py` keeps it and plan §13.2 in step (it ran here).

---

## Open questions from Phase 9's PRs

No later decision answers them: D295-D303 are the last Phase 9 rows on `main`. The defaults stand, reversible, until Clayton answers (D158). The PR reviewers (advisors) agreed with each judgement call, as the PRs say; Clayton still decides. PR text is quoted here as data.

**PR #107** (WO-9.1), https://github.com/crazie450-prog/GretecOS/pull/107
1. The package is `core/gregtec/discordbot/` rather than Appendix B's `discord_bot/` (Appendix B updated). OK?
2. The job queue lives in the core, and an interrupted job is re-run once (D295). OK?
3. The `discord` client may queue design jobs. They write proposed plans only; accepting, pinning and moving stay his. OK?
4. Other users get no response at all (D297). OK?
5. No deep-lane cap approval from Discord (D156, D297). OK?
6. The PNG and BOM attached are route 1's (D296). OK?
7. "/" in the amount means a rate, otherwise a quantity, checked before queueing. OK?
8. discord.py is a default uv group; its `audioop` warning only matters on a move to Python 3.13. OK?
9. A result that can't be posted is tried 3 times (30 s, then 60 s apart), then a "not sent; on the dashboard" notice, and no more tries until the bot restarts (D295). OK?
10. Replies are not ephemeral, so anyone who can read the channel sees them; the runbook asks for a private server or channel. OK?

**PR #111** (WO-9.2), https://github.com/crazie450-prog/GretecOS/pull/111
11. Q1 (D298): the `[notify]` defaults: no quiet hours, `quiet_min_level` err, `discord_min_level` warn, `cooldown_s` 1800, `offline_after_s` 60. Right for him?
12. Q2 (D298 (1)): voice and DM are exclusive per occurrence; "DMed" means a bot actually read the outbox. OK?
13. Q3 (D298 (3)): `/alerts off` also stops the credit DM. Or should credit DMs ignore `/alerts off`?
14. Q4 (D300): the "GOS is back" DM is kept, under the offline DM's rules. Wanted at all?
15. Q5 (D298 (2)): unknown presence counts as present, shown as `presence: unknown`. OK?
16. Q6 (D300): Stop asks a second time; Open billing offers the other buttons again. OK?
17. Q7 (D299): a DM dropped before it was posted doesn't count as sent; if its alert is active again it is DMed again. OK?
18. Q8 (D299): after a restart, a DM whose alert is neither active nor still adoptable is dropped as stale. OK?
19. Q9: two `GetLastInputInfo` callers remain (presence's and the scheduler's); they no longer interfere. A later cleanup could merge them (should-do 4). OK?

**PR #119** (WO-9.3), https://github.com/crazie450-prog/GretecOS/pull/119
20. D301: `gos tailnet setup` never writes gos.toml; the name counts only once he adds it to `[core] allowed_hosts`. OK?
21. D301: Funnel on any port (foreground sessions too) blocks setup. OK?
22. D301: the core refuses HTTP Funnel requests only; a TCP Funnel or a raw TCP forward is caught by `check` and `setup`, not by the core. OK?
23. D301: Tailscale's identity headers are not checked; the pairing cookie is the credential. OK?
24. D301: a `<name>:443` entry in `allowed_hosts` also allows the bare name. OK?
25. D302 (**needs his board approval**): the phone tabs are D159's approved five (Now, Chat, Search, Costs, Settings) plus More, with Base inside More. Should Base replace Costs as a tab (D53: "what's broken" from the phone)? The More view has no approved board.
26. D303: in CI, Tailscale is a fake CLI and Serve a fake TLS proxy; the e2e core raises the pairing limit for the harness only (the real limit is in `tests/security` only); the fake Serve uses `cryptography`, in the lock only through keyring on Linux. Noted.

---

## Clayton's PC steps before signing G9

These run after G2's PC steps 0 to 4 (`docs/reviews/G2_review.md`: Node.js and pnpm, `phase1_pc_checks.md`, the setup wizard with the API key, and the dashboard paired on monitor 2), G6's steps 1 and 2 (`docs/reviews/G6_review.md`: the watchers and "paused" in Lab, which §3 below needs) and G7's steps 1 to 3 (`docs/reviews/G7_review.md`: the OC agent reporting as `oc-lab` in `lab-ops`, which §4 and the craft in step 5 need). They don't repeat those steps. `$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe`, as in the runbooks. Use a normal PowerShell window in his own account (never "Run as administrator", never as gos-dev: `gos tailnet setup` refuses gos-dev), with `$ProgressPreference = 'SilentlyContinue'`. **Nothing here touches Play.** Nothing costs money except the `/ask` answers in step 3 (a few cents of fast lane, and one deep-lane question capped at $2). In all, about 2 h, most of it waiting away from the keyboard in step 4.

Order: Tailscale (step 2) comes before the Discord checks (step 3), so the plan links in `/plan` replies work the first time.

**0. Deploy a green `main`:** `705c88a` or later, with every CI job green on that commit. Not `7f0b4a1` (its `dashboard` job is red, risk 1). In the profile clone: `git pull`, then `uv run gos deploy`. `& $gos status` shows the core `ok`.

**1. The Discord bot** (`discord_bot.md` A1-A5 and B1-B5):
- A: make the bot (no privileged intents, Public Bot off, scopes `bot` + `applications.commands`, permissions Send Messages + Attach Files), add it to his **private** server, copy his user id and the server id, allow DMs from the server.
- B: add `[discord] owner_id = ...` and `guild_id = ...` to `%USERPROFILE%\GregtecOS\config\gos.toml`, then:
  ```powershell
  & $gos key set discord
  & $gos token new discord
  & $gos discord check
  & $gos serve
  & $gos status
  ```
  **Pass:** `discord check` prints `owner_id: set; guild_id: set` and no `problem:` line; `status` shows `Discord bot` running. (The phone-link line says to set the tailnet name until step 2 is done.)

**2. Tailscale and the phone pairing** (`phone_dashboard.md` A, B, C; V28):
- A: Tailscale on the PC and the phone, same account; MagicDNS and HTTPS certificates on in the admin console.
- B:
  ```powershell
  & $gos tailnet setup
  ```
  If it prints a `login.tailscale.com/f/serve?...` link, open it, enable Serve, and run it again. Add the printed name to `[core] allowed_hosts` in `gos.toml`, then:
  ```powershell
  & $gos stop
  & $gos serve
  & $gos tailnet check
  & $gos discord check
  ```
  **Pass:** five `ok` lines, `Funnel is off` among them; `discord check` now prints `phone links: https://<pc>.<tailnet>.ts.net`.
- C: on the phone open `https://<pc>.<tailnet>.ts.net/`, **Ask to pair**, type the code from monitor 2's rail. Reload: no second pairing. Settings + Health on monitor 2 lists `Phone`.

**3. Discord from the phone, on cellular** (`discord_bot.md` C1-C11, the WO-9.1 acceptance, [QA:V4], [QA:V9]). Wi-Fi off. Time each with the phone's stopwatch: "thinking" at once and a message **within 10 s**.
- C1-C7: `/ask` (fast and deep), `/plan target: steel ingot amount: 2/s` (queued at once; later the routes, `flow.png`, `bom.tsv` and the link, which opens the plan in the phone dashboard with More lit), `/power mode: best`, `/status`, `/alerts`, `/persona`.
- C8, with Lab running (`& $gos launch lab`), since the confirmed action is done in Lab: a Lab action's Confirm button within 60 s, then an expired one ("expired", nothing sent).
- C9: a second account presses and types the same: Discord's own "This interaction failed"; nothing on the PC; `discord.log` says "ignored an interaction from another user". Remove its access after.
- C10: restart the bot while a `/plan` is queued, and again while it is designing: the result arrives once, by DM.
- C11 (no-AI mode), on its own here (or in G2 step 8's sitting if that hasn't run yet; G2's step 8 alone never checks Discord, since the bot only exists from step 1 above):
  ```powershell
  & $gos key delete anthropic
  ```
  Once monitor 2 shows "AI offline": `/ask` says what needs the AI and still answers lookups, `/status` says "AI: offline", `/plan` still works. Then put the key back:
  ```powershell
  & $gos key set anthropic
  ```

**4. Alert routing** (`alert_routing.md` §1-§7, the WO-9.2 acceptance, V35). Lab running, the watchers up (G6 step 1), the OC agent up for §4 (G7 steps 1-3):
- §1 (V35): `& $gos alerts routed` right after typing shows `"presence": "present"`; `Start-Sleep -Seconds 660; & $gos alerts routed > $env:TEMP\routed.json`, hands off, shows `"away": true`.
- §2: `& $gos alerts test`: one "Test alert from GOS" DM.
- §3 (**Esc, then walk away**): Esc in Lab and leave: one DM about 10 minutes after Esc, none 5 minutes later; Esc while staying at the PC: no DM; then the other order (away first, the pause at 11 minutes): one DM within seconds of the pause.
- §4: a stall while at the PC is said by voice and not DMed later; a stall while away is DMed once, with its `tel:` cite, and not said when he comes back.
- §5: the simulated 80% credit row (core stopped while editing the DB, as the runbook shows): one DM with **Open billing**, **No-AI mode**, **Stop**; the second account's press on a bot message does nothing; **No-AI mode** turns it on; delete the row and **Resume AI**.
- §6: "GOS offline" while away (the `host = "0.0.0.0"` trick and the `Get-CimInstance` kill in the runbook), one DM; remove the line after.
- §7: `/alerts quiet 1`, `/alerts off`, and optionally `[notify] quiet_hours`.

**5. The phone on cellular** (`phone_dashboard.md` D and E, the WO-9.3 acceptance, L8). Wi-Fi off, Tailscale on:
- E.1: the `/plan` reply's link opens the plan, More lit.
- E.2: a Lab action confirmed on the phone. Ask for a **craft** in Chat when the OC agent is up in `lab-ops` (L8's wording), otherwise More → Ores → a waypoint. The approval leaves monitor 2's rail too.
- E.3: quit Lab: within about 30 s the phone shows "Game not running" without a reload; start it again.
- E.4: Tailscale off on the phone, reload: the page doesn't load. `& $gos tailnet check` still says `Funnel is off`.

**6. The idle-maintenance check** (PR #111 step 3): after the core has run for a few minutes, `Select-String -Path "$env:USERPROFILE\GregtecOS\logs\*.log" -Pattern "maintenance tick failed"` finds nothing.

**7. Boards** (D92, D158 (5)): on the phone, the tab bar (Now, Chat, Search, Costs, Settings, More) and the More view. Approve, redline, or say whether Base should replace Costs (question 25).

**8. Sign-off.** Answer questions 11, 13, 14 and 25, and say whether to keep each other default listed above. To stop serving the dashboard to the phone at any time: `& $gos tailnet off`.

**What to paste back for G9:**
- step 1's `discord check` output;
- step 2's `tailnet check` output;
- step 3's time per command, and what C9, C10 and C11 showed;
- step 4's result per section, and V35's `routed.json` (`away`, `presence`);
- step 5's four results;
- step 6's `Select-String` output (empty is a pass);
- his answers to the questions above.

---

## Risks

1. **`main` is red on the dashboard job at `7f0b4a1`.** WO-3.7's `designer.spec.ts:135` waits 10 s for a React Flow node at 390x844 and sometimes finds it still hidden. It failed `d896ee1` the same way before Phase 9, and passes on rerun and here, so any merge can turn it red (should-do 1). It is not a Phase 9 file. (Confirmed twice)
2. **V35 may fail.** If the windowless core can't read his input, `presence` stays `unknown`, which counts as present (Q5): away-only DMs (paused, offline, AFK base alerts) never go, and voice carries base alerts. The health strip shows it. (Unknown until step 4)
3. **Tailscale Serve or HTTPS certificates may need the admin console first.** `setup` then times out and prints the link (the PR's L1 fix). (Likely on the first run)
4. **Replies in a shared channel are visible to its members** (PR #107 note 10). Base telemetry, the ledger and plans show to anyone who can read the channel. The runbook asks for a private server. (By design)
5. **A TCP Funnel is invisible to the core** (D301 (2)). Only `gos tailnet check` and `setup` catch it, so he should run `check` after any Tailscale change. Nothing in GOS ever turns Funnel on. (Low)
6. **"GOS offline" depends on the bot outliving the core.** Both run under the supervisor as separate processes (D296); if the supervisor itself dies, nothing DMs. `alert_routing.md` §6 checks the core-only outage. (Low)

## After the review

- The full `pytest` here (10751 passed, 329 skipped) and the Playwright suite (77 passed) finished green on `7f0b4a1`, as did CI's Ubuntu, Windows and `mod` jobs on it. Only its `dashboard` job is red, on the WO-3.7 flake (risk 1), so deploy `705c88a` or later once all its jobs are green (step 0).
- After merging `ee7449c` (#118, WO-10.6, no Phase 9 file), the full `pytest` (10828 passed, 338 skipped), Playwright (77 of 77), the server harness and the Discord harness test were green again on this branch.
- The PR review's fixes: C11 now stands on its own in step 3, C8 says Lab must be running, E.4 is named as the only real network-level check of "unreachable", and the head is `ee7449c`.
- No product code was changed, and no decision row was added (D321 unused).
