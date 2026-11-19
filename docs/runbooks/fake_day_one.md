# Runbook: the fake day one (WO-11.3, plan §8 row 11.3, §9 L1-L10, §10; D336, D337)

A 2-hour session in the **Lab** with every part of Gregtec OS running, as day one will be, then
the §10 checklist ticked line by line. It is the last check before G11 (Clayton starts his
survival 2.9 world). Every step here is **Clayton's**, in his own account, on GOS as deployed
from `main` into GOS_APP. Nothing here touches Play: the game is `GTNH-Lab` throughout.

The automated version runs in CI on every PR (`tests/integration/test_fake_day_one.py` on a
harness DB, and a short smoke on Ubuntu and Windows); its report says which §10 items it checked
(**green**), and which are **PC only**: those are this runbook's steps. Each step names the
runbook it comes from; when a step fails, that runbook's troubleshooting section is the next
place to look.

**Time:** about 30 minutes of setup (A), the 2-hour session (B-H, with the game running the
whole time), then 30 minutes of after-checks (I-J). Paste what each **Paste** line asks for into
the WO-11.3 follow-up issue or the G11 report, in step order.

**Before you start**

- `main` deployed (`uv run gos deploy` from your clone, [core_service.md](core_service.md) D);
  `gos status` shows the supervisor, core and Discord bot running.
- The Lab instance has GregtecLink's listener (machine_events.md 1), OC in `lab-ops`
  (oc_agent.md A-C) and the breeding station built (breeding_station.md B) from earlier runbooks.
- Your phone has Tailscale and the paired dashboard (phone_dashboard.md A-C), and Discord.
- `[startup] watchers_with_play = "lab"` (the default until watchers.md 6 is done).
- Note the time you start: `Get-Date -Format o`. **Paste:** the time.

## A. Setup checks (15-30 minutes, before launching the game)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| A1 | `gos doctor` | every line `ok` or an explained `warn`; the addon versions printed | C1 addons | addon_watcher.md |
| A2 | `gos pack watch --force` | "no new 2.9.x release" or the new version's checklist; every addon on a 2.9-compatible release | C1 | addon_watcher.md, pack_update.md |
| A3 | Dashboard Health card (and `gos doctor`) | Play's last export fingerprint = the DB's; no "recipe data is stale" | C1 Play fingerprint | phase1_pc_checks.md 3-4 |
| A4 | `gos backup restore --verify` | exit 0, `ok   db row counts`, `ok   vault file hashes` | C11 backups | ops.md 1 |
| A5 | `& $py scripts\verify_world_backup.py` (as in ops.md 3) | the newest world backup opens and checks in a scratch folder | C11 world backup | ops.md 3 |
| A6 | The isolation re-test, as gos-dev (`$env:GOS_LIVE_ISOLATION = '1'`, `$env:GOS_CLAYTON_HOME`, `uv run pytest tests/security/test_isolation_live.py -v`, as ops.md's PC step 4 gives it) | every write to Play, GOS_APP and `backups\world` refused | C11 isolation | ops.md (Clayton's PC steps 4), phase1_pc_checks.md |
| A7 | Reboot, log in, wait 1 minute | the supervisor, core and bot started at login; monitor 2 shows the dashboard in glance mode | C11 login start | core_service.md A-B, phase1_pc_checks.md 13 |
| A8 | `gos status` | core and discord `running`; watchers and voice `starts when Play launches` | C11 | core_service.md B |

**Paste:** `gos doctor`, `gos status`, the last 10 lines of `gos backup restore --verify`, the
world backup check's verdict and the isolation re-test's summary.

## B. Launch the Lab (minute 0)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| B1 | `gos launch lab`, load `lab-ops` | within 30 s `gos status` shows watchers and voice `running` | C11 Play-linked | watchers.md 2, voice_client.md |
| B2 | Press Esc, wait 5 s, close the menu | the dashboard's pause chip shows within 5 s and clears | C6 | watchers.md 2 |
| B3 | Glance at monitor 2 | AI online; telemetry fresh (OC posts every 5 s); no red cards | C8 | oc_agent.md D |

## C. Questions, both lanes and every input (minutes 5-25)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| C1 | Say the wake phrase, then "how do I make steel" | spoken answer naming Steel Ingot first; card on monitor 2 with `r:` citations | C4 wake, C5 | wake.md E, fast_lane.md B |
| C2 | Hold the talk key: "what quests can I do next" | frontier answered, no spoilers past the nudge (D26) | C5, L2 | quest_guide.md C |
| C3 | Hover an item, ask "what's this" | its card; the hover id matches (registry@meta) | C5 hover | gregteclink_client.md |
| C4 | Press the screen key on a GT GUI, ask about it | a screen answer with what it read | C5 screen | screen_reading.md |
| C5 | Ask a wiki question ("what does the wiki say about the EBF coils") | `w:` citation | C5 wiki | wiki.md |
| C6 | Ask about an ambiguous item ("how do I make a circuit") | GOS asks which one | C5 ambiguous | recipe_lookup.md |
| C7 | "Switch persona to ship AI", then "switch back to GT veteran" | the voice changes and back; gt-veteran is the default after a restart | C4 personas | voice_client.md |
| C8 | Throughout: record the session's open mic, `gos voice capture "$env:USERPROFILE\GregtecOS\data\evals\wake\play\day-one.wav" --minutes 120` in a second window (Discord on, as in play) | the recording for I8; it is your voice: it stays in GOS_HOME, never committed or sent | C4 wake, L5 | wake.md C |

**Paste:** `gos voice benchmark --last 20` (latency per PM14, C4; `bench` is its alias).

## D. Design and build (minutes 25-55)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| D1 | "Design a steel line, 2 per second at MV" | handed to the deep lane; routes on monitor 2 in rank order | C2 | designer.md B |
| D2 | Pin a node to another recipe, re-solve | the pin stays; the order is the same for the same request | C2 pins/ranking | designer.md A |
| D3 | Accept route 1 | `plans\`, `base\lines\` notes; the ledger shows the line | C2 plan → vault → ledger | designer.md A |
| D4 | Export it for gtnhplanner.com and open it there | the line opens with the same machines | C2 exports | plan_outputs.md |
| D5 | Batch: "640 steel within an hour" | routes for the quantity | C2 batch | designer.md A |
| D6 | Add a machine you placed to My base; re-design D1 | the boundary uses it | C2 My base | designer.md A |
| D7 | Power view: Best mode at LV, HV and IV; then each of the 4 modes once | the 4 modes reviewed; storage sized; accept one into the ledger | C3 | power_objectives.md |
| D8 | Build view: open the guide for the EBF; build one in `lab-structures` from it | the controller forms | C10, L6 | build_guide_lab.md B-C |
| D9 | Paste a Claude-designed shell around it (two themes tried) | no tile entities pasted; the shell fits | C10, L6 | shells_lab.md C-D |

**Paste:** the accepted plan's id, the ledger line, a phone or screen photo of D8's formed
controller and D9's shell.

## E. Base, actions and alerts (minutes 55-85)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| E1 | "Craft 64 steel ingots" | read back "Craft 64 Steel Ingot in Lab. Say confirm."; one pending | C8 | oc_agent.md I |
| E2 | Ask for a second action before confirming | the first is cancelled and said so | C8 one pending | oc_agent.md I |
| E3 | Say "confirm" | the craft starts in AE2; the journal has it | C8 | oc_agent.md I |
| E4 | Propose one more, say nothing for 60 s | it expires | C8 60 s | oc_agent.md I |
| E5 | Pause a machine and resume it by voice (confirmed) | the machine stops and runs again | L4 | oc_agent.md I |
| E6 | Drain the LSC below 20 % (creative), tab out of the game without Esc, leave the keyboard 10 min | power-low alert: voice while present, Discord DM once away; the game was not paused | C8 AFK, C9 alerts, L4 | alert_routing.md 4, oc_agent.md J |
| E7 | Place 3 GT machines, break 1 | the event feed shows them; `gos watch census --instance lab` agrees | C6 listener/census, L3 | machine_events.md 2 |
| E8 | Die once, find a vein, chat a line, visit the Nether | each in the journal (`vault\log\<today>.md`) | C6 journal, L3 | event_store.md |
| E9 | "What should I do next?" | a sensible next step for lab-progress's state | C6 advisor, L2 | advisor.md B |

**Paste:** the DM's text, `gos watch census --instance lab` and today's journal note.

## F. Crops (minutes 85-100)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| F1 | Crops view: review each of the 5 target guides | each makes sense (Crop Manager and Breeder steps included) | C7 | crop_guide.md 0-1 |
| F2 | Follow one guide's next step; ask "next crop step" | the step said and shown | C7 | crop_guide.md 1 |
| F3 | Check the station's state in `lab-ops` | it is breeding its target unattended | C7 station | breeding_station.md D |

## G. Off the PC: phone and Discord (minutes 100-110)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| G1 | Phone on cellular (Wi-Fi off): open the dashboard's tailnet name | the phone layout; the plan from D3 opens from a Discord link | C9, L8 | phone_dashboard.md D-E |
| G2 | From Discord: `/ask how do I make steel`, `/status`, `/plan steel 1/s` | answers within 10 s; `/plan` queued, PNG + BOM later | C9 | discord_bot.md C |
| G3 | Confirm a proposed craft from the phone | confirmed via phone | C9, L8 | phone_dashboard.md E |
| G4 | The tailnet name from a device outside the tailnet | unreachable | L8 | phone_dashboard.md E |
| G5 | Credit DM buttons, as alert_routing.md 5 sends them | the buttons answer | C9 credit DMs | alert_routing.md 5 |

## H. Failure drills (minutes 110-120)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| H1 | Task Manager: end the core's python.exe | restarted within 5 s; voice quiet (a short outage); the next question answered | C11 crash | ops.md 5, core_service.md B |
| H2 | End the watchers', the voice client's and the bot's processes in turn | each back within its back-off; no event twice in the feed | C11 crash | ops.md 5 |
| H3 | `gos key delete anthropic` mid-question, ask "how do I make steel", then `gos key set anthropic` | "AI offline" on monitor 2; the lookup still answered; AI back within 30 s | C5 no-AI, L9 | fast_lane.md, setup_wizard.md |
| H3b | The revoke itself (a stored key the API refuses, plan 9 L9): `gos key set anthropic` with a wrong value (e.g. your key with its last character changed), ask "how do I make steel", then `gos key set anthropic` with the real key | "AI offline" on monitor 2 with an authentication reason (not "no key"); the lookup still answered; AI back within 30 s of the real key. A 401 costs nothing | C5 no-AI, L9 | fast_lane.md, setup_wizard.md |
| H4 | Walk away, `gos stop` (core only) for 2 minutes | the bot's "GOS offline" DM once; nothing while you are present | C9 offline DMs | alert_routing.md 6 |
| H5 | Throughout: Task Manager's GPU and RAM, F3's FPS, vs the baseline | FPS drop ≤ 10 %, TPS unaffected, VRAM headroom ≥ 1 GB | L7 | voice_benchmarks.md B-C |

Then quit the game normally. `gos status`: watchers and voice stopped with Lab.

**Paste:** `gos status`, `logs\supervisor.log`'s restart lines and the L7 numbers.

## I. After the session (30 minutes)

| # | Do | Expect | §10 / L | From |
|---|---|---|---|---|
| I1 | `logs\watchers.log` for the 2 hours | no save errors, no blocked write | C6 2 h | watchers.md 4 |
| I2 | Costs view | the session's spend under the day cap; caps card filled | C11 costs | ops.md 6 |
| I3 | The paid evals, each as its runbook runs it: `gos eval fast-lane` (60 rows, the 10 ambiguous-item ones included), `gos eval recipe-lookup --out ...`, `gos eval quest-guide`, `gos eval screen --images "$env:USERPROFILE\GregtecOS\data\evals\screen"` (each costs cents; the runbooks give the estimate) | `passed: 60 of 60`; `correct: 50 of 50` and `uncited numbers: 0`; `passed: 20 of 20` and `spoiled: 0`; the screen cases pass. **The wiki (15) and hover-item (10) evals of plan §9 have no case file** (G8 review should-do 1): write "no eval: open" for them in the G11 report unless one has landed by then | C5 evals | fast_lane.md 7, recipe_lookup.md, quest_guide.md, screen_reading.md |
| I4 | The Lab dry run of a 2.9.x update | prompt, checklist and plan flags correct | C1, L10 | pack_update.md ("The checklist", Clayton's PC steps) |
| I5 | `/bq_admin complete` the LV, HV and IV sequences in `lab-progress` | frontier and tier match the book | L2 | quests_check.md, phase1_pc_checks.md 5 |
| I6 | The projector: build every tracked multi, capture the addon multis | cells match; captures stored | L1 | footprints_lab.md |
| I7 | `gos voice eval` (the 50 recorded questions, stt.md B-C, if not recorded yet) | `entity accuracy: ... target >= 95%`, `verdict: met` | C4 entity accuracy | stt.md D 8 |
| I8 | `gos voice wake-eval --audio "$env:USERPROFILE\GregtecOS\data\evals\wake"` with C8's recording in `play\` (and the 40 attempts of wake.md B) | `asked` ≥ 38/40 and `per h` ≤ 1.0 over ≥ 120 minutes, exit code 0 | C4 wake, L5 | wake.md D 6 |
| I9 | `gos voice say --phrases` and listen | each of the 40 terms and phrases said the way you say it; nothing the speech filter should drop is spoken | C4 pronunciation and filter | tts.md D 7 |
| I10 | The two default notices in `lab-ops` (power deficit, machine stalled) and a plan buildable from stock | each fires once, in GOS's order | C6 default notices, L2 | advisor.md C 5-10 |

## J. Tick the checklist

Go through §10 with the CI report beside you: the `fake-day-one` artifact of the PR's CI run
(the `mod` job uploads it green or red; or rerun the integration test locally with
`GOS_REHEARSAL_REPORT=<file>` or `-rP`): a line is done when the CI report has it green and every PC-only item above
passed. Lines with a red item block G11; write each in the G11 report with its step number.

| §10 line | here (steps) |
|---|---|
| Addons, Play, pack update | A1-A3, I4 |
| Designer | D1-D6 |
| Power | D7 |
| Voice | C1, C2 (talk key), C7, C8, I7-I9, the benchmark |
| Q&A | C1-C6, H3, H3b, I3 (wiki and hover: no eval yet) |
| Context | B2, E7-E9, I1, I10 |
| Crops | F1-F3 |
| OC bridge | E1-E6 |
| Discord and phone | E6, G1-G5, H4 |
| Build | D8-D9 |
| Ops | A4-A8, B1, H1-H2, I2 |
| Lab scenarios L1-L10 | L1 I6; L2 C2, E9, I5, I10; L3 E7-E8; L4 E1-E6; L5 C8, I8, H5; L6 D8-D9; L7 H5; L8 G1-G4; L9 H3, H3b; L10 I4 |
| Fake day one | this runbook |

§10's OC line ends "In survival, M1 switches on at MV and M2 at HV/EV": that is how Play's base
grows, not a Lab check; nothing here ticks it.

**Paste:** for each line: done, or the step that failed and what it showed.
