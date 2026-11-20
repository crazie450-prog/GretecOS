# Gregtec OS: what to do next

(short version of [`docs/deployment_checklist.md`](deployment_checklist.md))

Marks: \$ = spends API money (worst case). LAB = Lab game open. PLAY = touches Play. [n] = checklist row.

Whole run, worst case: about \$130 (task cap C = \$2; about \$157 if C becomes \$3). If the AI pauses at \$100 of monthly credit, click Resume AI. The Console's \$150/month backstop is the real hard stop: at C = \$3, spread the paid steps over two months. [1]

## Every new window

Open a normal PowerShell (never "Run as administrator") and paste:

```powershell
$ProgressPreference = 'SilentlyContinue'
$app  = "C:\GregtecOS-app\current"
$gos  = "$app\.venv\Scripts\gos.exe"
$py   = "$app\.venv\Scripts\python.exe"
$gh   = "$env:USERPROFILE\GregtecOS"                                   # your GOS_HOME
$lab  = "C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft"
$exp  = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
$hooks = "$app\scripts\install_prism_hooks.py"
$sync  = "$app\scripts\sync_instances.py"
$prism = "C:\Games\PrismLauncher\prismlauncher.exe"
$home_ = $gh                                                         # watchers.md's name for GOS_HOME
```

Also paste "function Show-NewestRun { ... }" from docs/runbooks/phase1_pc_checks.md, "Before you start".

A "gos-dev" step means: runas /user:gos-dev powershell, then cd C:\GregtecOS-dev\GretecOS. "uv run gos ..." runs there.

In your own window, a runbook's bare "gos ..." (or "uv run gos" in themes.md / shells_lab.md) means "& \$gos ...".

## Session 1: deploy and set up (about 1 hour)

1. winget install OpenJS.NodeJS.LTS, then npm install -g pnpm@10.28.0, open a new window. Worked: pnpm --version says 10.28.0. [2.1]
2. Check main is at e0e0d46 or later with every CI job green (a red flake: re-run, never deploy over it). Worked: all green. [2.2]
3. Your own clone: git pull, then uv run gos deploy. Worked: "Built the dashboard" and the swap reported done. [2.3]
4. gos-dev: git pull, then uv sync. Worked: clean. [2.4]
5. & \$py "\$app\scripts\sync_instances.py"   Worked: "Refused" never printed. [2.5]
6. & \$gos stop, then & \$gos serve --detach. Worked: & \$gos status shows supervisor and core ok. [2.6]
7. Monitor 2's window: Ask to pair, then & \$gos pair, type the code. Worked: a reload doesn't ask again. [2.7]
8. \$ (one tiny call) Setup wizard: Paths, Anthropic key + Test, (Discord token now or at step 74), Headset, F13 talk key, leave the wake phrase default, Finish; then & \$gos stop and & \$gos serve. Worked: Test names claude-haiku-5-5. [2.8]
9. Get-Content "\$gh\config\former_plays.txt" and Select-String -Path "\$gh\config\gos.toml" -Pattern 'former_plays'   Worked: both list GTNH-Play. [2.9]

## Session 2: recipe data and core service (2 hours or more: 31 idle minutes, 30+ minutes Lab play)

10. NEI swap and autoexport flag on GTNH-Export: phase1_pc_checks.md step 2. Worked: jar hash matches. [3.1.1]
11. Launch GTNH-Export in Prism, wait for it to quit, run Show-NewestRun. Worked: complete: True, no part_errors. [3.1.2]
12. gos-dev: uv run gos export collect, then uv run gos db build. Worked: a "Block catalog:" line, palette_errors 0. [3.1.3]
13. & \$gos export collect; & \$gos db build   Worked: the same "Block catalog:" line. [3.1.4]
14. gos-dev: the four power commands in row 3.1.5, then palette_export.md B and C. Worked: "5 of 8 entries accepted", exit 1 (expected). [3.1.5-3.1.6]
15. PLAY (main menu only, load no world) phase1 step 9 and core_service.md B-C, you first, then gos-dev's gos launch lab and gos launch export. SKIP every install_login_start.ps1 / -StartNow line (that is step 89); use & \$gos stop --all; & \$gos serve --detach instead. Answer PR #30 Q2. Worked: second client refused. [3.2.1]
16. Lab closed: & \$py "\$app\scripts\check_tweaks.py" C:\Games\PrismLauncher\instances\GTNH-Lab, then LAB F3+P. Worked: pauseOnLostFocus:false. [3.2.2]
17. Lab hooks: phase1 step 10 Lab part and play_hooks.md §3 1-5; never --uninstall. Worked: "Pre-Launch command failed with code 3." [3.2.3]
18. LAB Vault: phase1 step 11, & \$gos vault maintain, & \$gos vault measure, Lab (not Play) at menu 31 min. Worked: measure table prints. [3.2.4]
19. LAB After 30+ minutes of Lab play: phase1 step 12 backup job. Keep \$env:TEMP\gos-lab-backup-test until after session 15. Worked: "0 removed." [3.2.5]

## Session 3: Lab checks and pack updates (about 3 hours)

20. gos-dev, leave running: uv run gos structures extract --out C:\GregtecOS-dev\structures\2.9.0-pc1 [3.3.5]
21. When it ends: & \$gos structures load C:\GregtecOS-dev\structures\2.9.0-pc1   Worked: at least 250 controllers. [3.3.5]
22. LAB gos-dev: golden recipes (g1_golden_lab.md §1-3), then phase1 steps 5, 6 (world lab-ores), 7. Worked: 30 of 30; quests, ores, crops match. [3.3.1-3.3.4]
23. LAB pack_update.md "Clayton's PC steps for WO-1.9" 1-4; update_play.py dry run only; put Lab back. Worked: right plans flagged. [3.4.1]
24. LAB addon_watcher.md WO-11.1 step 1, then & \$gos pack adopt no, put the jar back. Worked: diff.txt lists the addon. [3.4.2]

## Session 4: dashboard, questions and answers, first paid evals (\$ about \$34)

25. FIRST: in \$gh\config\gos.toml under [budget] set day_max_usd = 140.0, then & \$gos stop; & \$gos serve [3.5]
26. LAB Dashboard checks: dashboard.md C5-C10 and D11. Worked: as the runbook says. [3.5.1]
27. \$0.01 & \$gos ask "recipe for steel", "how do I make steel", "how do I make copper wire"; make \$gh\data\reports, then & \$gos eval fast-lane | Tee-Object -FilePath "\$gh\data\reports\fast-lane-eval.txt"  Worked: Result: PASS. [3.5.2]
28. \$10 recipe_lookup.md A2 (Test-Path, the --json card check), then & \$gos eval recipe-lookup --out "\$gh\data\reports\recipe-lookup.tsv"   Worked: correct: >= 48 of 50. [3.5.3]
29. \$24 Answer PR #49 Q3, then deep_lane.md A2-C5 (& \$gos eval deep-lane). Worked: Result: PASS. [3.5.4]
30. dashboard.md "No-AI mode" 1-4: & \$gos key delete anthropic, check, & \$gos key set anthropic; Wi-Fi off test; Open billing; Stop then & \$gos serve. Worked: "AI offline" within 30 s. [3.5.5]

## Session 5: production planner (time not stated)

31. G3 step 1, then & \$gos plan "steel ingot" --tier MV --json   Worked: steel under boundary.in; then clear stock and tick. [3.6.1]
32. \$2 Designer: designer.md A, B and D steps 9-12. Worked: a repeat design is much faster. [3.6.2]
33. \$24 designer.md C's two design-cost commands. If the new cap is \$3, set day_max_usd = 165.0, then & \$gos stop; & \$gos serve. [3.6.3]
34. gos-dev: G3 steps 3, 4 and 5 command blocks (first time: pnpm exec playwright install chromium in dashboard). Worked: exit 0, no DIFF line, every "problems":[]. [3.6.4-3.6.6]
35. LAB G3 steps 6 and 7, then plan_outputs.md checks 2-4. Worked: within ±2% of the plan. [3.6.7-3.6.9]
36. Compare the gtnh-flow MIT block in NOTICE.txt with upstream's LICENSE. Worked: same, or note the difference. [3.6.10]

## Session 6: power (time not stated)

37. LAB turbines_lab.md §1-3, then §4 in lab-ops. Worked: every reading equals the saved output. [3.7.1-3.7.2]
38. gos-dev: G4 step 3's four lines (naquadah command as in row 3.7.3). Worked: bought fuel ranks first. [3.7.3]
39. LAB Tree-farm boiler: power_objectives.md tree-farm steps 1-3 and G4 step 4b. Worked: runs on water only. [3.7.4]
40. & \$gos ledger --write, then the rest of row 3.7.5, retire after. Worked: base/power.md exists. [3.7.5]
41. Press 5; gos-dev: uv run gos eval power-modes --report "\$HOME\power_modes_fullpack.md" (the phone part of 3.7.6 after step 75). Worked: sensible options per case. [3.7.6]

## Session 7: voice, part 1 (about 2 hours; all voice about 6.5 hours)

42. & \$gos token new voice; & \$gos voice devices; GTNH closed: & \$gos voice keys play; & \$gos voice keys lab [3.8.1]
43. powershell -NoProfile -ExecutionPolicy Bypass -File "\$app\scripts\set_comm_ducking.ps1", restart Discord and GTNH. Worked: "Do nothing". [3.8.2]
44. & \$gos voice stt --engine whisper-gpu, then stt.md A-D. Worked: verdict: met. [3.8.3]
45. & \$gos voice tts-bench, then tts.md B-D; set tts and tts_device in gos.toml. Worked: P50 <= 400 ms. [3.8.4]
46. LAB & \$gos voice test --seconds 600, then & \$gos voice run (row 3.8.6). Worked: blocks dropped: 0. [3.8.5-3.8.6]

## Session 8: voice, part 2 (about 4.5 hours)

47. LAB Wake word: set [voice] wake_phrase, then wake.md A-E (40 tries, 120-minute recording). Keep recordings for session 15. Worked: asked >= 38/40. [3.8.7]
48. & \$gos persona, then say "persona ship AI". Worked: switches in about a second. [3.8.8]
49. LAB discord_coexistence.md A-D with a friend, one hour. Worked: no Compare-Object output. [3.8.9-3.8.10]
50. LAB voice_benchmarks.md A-D, then a normal Lab session by voice. Worked: Result: PASS. [3.8.11-3.8.12]

## Session 9: GregtecLink, screen reading, wiki (time not stated)

51. Close clients: & \$py "\$app\scripts\install_gregteclink.py" --instance lab, then again with --apply. [3.9.1]
52. LAB Bind What's this? to F18; gregteclink_client.md B.4-B.10. Worked: answers match the tooltips. [3.9.1-3.9.3]
53. & \$gos voice keys lab; & \$gos voice keys play; restart voice client; LAB screen_reading.md B.1-B.5. Worked: "source": "dxcam" in voice.log. [3.9.4]
54. \$1.40 LAB F2 shots s01-s05 to \$gh\data\evals\screen\, then & \$gos eval screen --images "\$gh\data\evals\screen", type yes. Worked: 5 of 5. [3.9.5]
55. \$2 & \$gos wiki licence; if fine, y, then & \$gos wiki crawl; ask the pyrolyse oven question. If refused, stop. [3.9.6-3.9.7]
56. \$0.15 & \$gos eval hover --fake, then & \$gos eval hover | Tee-Object -FilePath "\$gh\data\reports\hover-eval.txt", type yes. Worked: 10 of 10. [3.9.9]
57. \$30-45 wiki.md E: write 15 cases, & \$gos eval wiki --fake, then & \$gos eval wiki. Skip if no crawl. Worked: 15 of 15. [3.9.10]
58. A day later, no restart: dir "\$gh\data\cache\screens"   Worked: only the last 24 hours. [3.9.8]

## Session 10: watchers, events, quests (about 3 hours or more)

59. & \$gos token new watchers; with Lab closed set lab_world = "lab-progress". [3.10.1]
60. LAB Open lab-progress, press Esc. Worked: "Game paused" within 5 s. [3.10.2]
61. LAB Play 2 hours, then watchers.md §4.1 checks. Must pass before step 94. Worked: oplock line, no save failures. [3.10.3]
62. LAB watchers.md §3, then event_store.md §2 steps 1-11. Worked: recall >= 95%. [3.10.4-3.10.5]
63. LAB lab_world = "lab-structures"; machine_events.md §2; & \$gos watch census --instance lab --world lab-structures [3.10.6]
64. \$0.01 & \$gos eval quest-guide | Tee-Object -FilePath "\$gh\data\reports\quest-guide-eval.txt"   Worked: 20 of 20, spoiled: 0. [3.10.7]
65. LAB & \$gos quests load --instance lab --world lab-progress, quest_guide.md C; gos-dev: set GOS_QUESTS_PACK, run the real_book test (D). Worked: 1 passed. [3.10.8]
66. LAB gos-dev, its own lab_world = "lab-ores": ore_intel_check.md §0, A, then B. Worked: waypoint shows. [3.10.9-3.10.10]
67. \$6 & \$gos eval advisor | Tee-Object -FilePath "\$gh\data\reports\advisor-eval.txt", fill the useful column. Worked: 3 of 3. [3.10.11]

## Session 11: OpenComputers (about 6 hours, mostly unattended)

68. Game closed: check_tweaks.py on Lab as row 3.11.1, then & \$gos token new oc-lab --show [3.11.1]
69. LAB oc_agent.md B-D steps 4-8, then E steps 9-15 (one hour). Worked: failed 0. [3.11.2-3.11.3]
70. \$2 LAB lab_world = "lab-ops"; advisor.md C5-C10. Worked: alerts within about 70 s. [3.10.12]
71. LAB gos-dev: pick five crops; crop_guide.md §0-§3. Worked: 0 problems; V30, V32 checks pass. [3.10.13]
72. LAB oc_agent.md F to J, steps 16-40 (includes one hour on M1). Worked: as each section says. [3.11.4-3.11.8]
73. LAB breeding_station.md A-F. Worked: 4 or more of the target. [3.11.9]

## Session 12: Discord and phone (about 2 hours; your own account only)

74. discord_bot.md A1-A5 and B1-B5, then & \$gos discord check   Worked: owner_id: set; guild_id: set. [3.12.1]
75. & \$gos tailnet setup, add the name to [core] allowed_hosts, & \$gos stop; & \$gos serve, & \$gos tailnet check, pair phone. Worked: Funnel is off. [3.12.2]
76. \$2 LAB Phone on cellular: discord_bot.md C1-C11. Worked: each reply within 10 s. [3.12.3]
77. alert_routing.md §1-§7. Worked: as each section says. [3.12.4]
78. LAB phone_dashboard.md D-E on cellular, then row 3.12.6's Select-String. Worked: all four pass, nothing found. [3.12.5-3.12.6]

## Session 13: building design (time not stated)

79. LAB check worldedit-gtnh-v0.0.10.jar is in Lab's mods; gos-dev + your window: schematic_roundtrip.md A-D. Worked: identical, exit 0. [3.13.1]
80. LAB & \$gos footprints coverage, then footprints_lab.md B-D captures (A was steps 20-21). Worked: every planned multi covered. [3.13.2]
81. gos-dev: G10 step 4 layout test (row 3.13.3), then & \$gos layout `<plan> <power plan>`. Worked: 1 passed. [3.13.3]
82. \$6 & \$gos theme list, & \$gos theme check, then themes.md A-C by voice. Worked: th-copper-works v3 vault. [3.13.5]
83. \$2 LAB shells_lab.md B-D, build_guide_lab.md A-C. Worked: shell pasted, controller forms. [3.13.6]
84. LAB /guidenhc exportsite, copy to \$gh\data\guide-site\. Worked: In-game look renders, no 404s. [3.13.7]

## Session 14: backups and safety

85. & \$gos vault maintain --job backup, then & \$gos backup restore --verify   Worked: ok db row counts. [3.14.1]
86. & \$py "\$app\scripts\verify_world_backup.py" --backups "\$env:TEMP\gos-lab-backup-test", then ops.md 4 rehearsal on Lab's world (--keep --work C:\Temp\world-check). Worked: ok the world restores and reads. [3.14.2-3.14.3]
87. gos-dev: the isolation test in row 3.14.4. Worked: every test passes; backups listing refused. [3.14.4]
88. & \$gos alerts test   Worked: a Discord DM arrives. [3.14.5]
89. powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\scripts\install_login_start.ps1 -StartNow, sign out and in. Worked: GOS starts at login. [3.14.6]

## Session 15: the fake day one (about 3 hours)

90. PLAY (the checklist wants this before day one) & \$py "\$app\scripts\install_prism_hooks.py" --instance play, launch and quit Play once. Worked: "recipe data matches Play". [3.15.1]
91. \$11.50 + \$6 LAB Download the fake-day-one CI artifact, then fake_day_one.md A to J (reuse earlier eval results where allowed). Worked: J ticks every line. [4]
92. Set day_max_usd back to what you want for day one, then & \$gos stop; & \$gos serve. [3.5, 3.14.5]

## Session 16: Play (your scripts from GOS_APP, last)

93. PLAY Close Play: & \$py "\$app\scripts\install_gregteclink.py" --instance play, then --apply; bind What's this? in Play. Worked: /api/game shows binding: play. [3.15.3]
94. PLAY Add [startup] watchers_with_play = "play", & \$gos stop --all, sign out and in; after the first Play session run row 3.15.4's Select-String. Worked: "start (play).", nothing found. [3.15.4]

## Session 17: addon jars, the Lab sitting then Play (about 2 hours, in one go)

GT-Not-Leisure 0.2.7 (new), TST 0.8.2 and PlanNH 0.2.4 (updates). Run no other Lab step between step 96 and step 99 or 100: the Lab's mods differ from Play's until then. Details and every line to paste: docs/runbooks/addon_lab_test.md.

95. powershell -NoProfile -ExecutionPolicy Bypass -File "\$app\scripts\fetch_play_files.ps1"   Worked: ok for the four new jars, no FAILED. [3.4.3]
96. Prism closed: addon_lab_test.md steps 3-6: the four [sync.lab] exclude entries in gos.toml, copy lab-ops aside, delete TwistSpaceTechnology-0.8.0.jar and plannh-0.2.0.jar from Lab's mods, copy the four new jars in, & \$py \$sync --dry-run --only lab   Worked: no delete or copy line names them. [3.4.3]
97. LAB addon_lab_test.md steps 7-17: Mods list, lab-ops, NEI, one TST, one 123Technology and one GTNL multi each run a recipe, the PH #323 hatch test, PlanNH opens an old chart, memory for 20-30 minutes. Worked: no crash. A crash: steps 18-21 (send the crash report and fml-client-latest.log, take the new jars out one at a time). [3.4.3]
98. Answer: GT-Not-Leisure into Play? TST 0.8.2 and PlanNH 0.2.4 into Play? [5.2.12, 5.2.13]
99. PLAY Only what you said yes to, before the survival world starts: addon_lab_test.md steps 22-28 (old jar out, new jar in, main menu, quit, Export now, take the exclude entries out, & \$py \$sync). Worked: one jar per mod; "Refused" never printed. [3.15.8, 3.15.9]
100. A no or not yet: addon_lab_test.md steps 29-30 (take the exclude entries out, & \$py \$sync --only lab). Worked: the Lab has Play's jars again. [3.4.3]

Not ready, nothing to do (& \$gos pack watch keeps reporting them): 123Technology 2.2.1.3, Programmable Hatches v0.2.1p9-beta, worldedit-gtnh 1.0.1. [7.1]

## Later

Later (optional or not needed for day one): wizard board and display notes [2.10, 2.11]; deep-lane cap test D [3.5.4]; board approvals [3.10.14, 3.11.10, 3.12.7, 3.13.4]; relocation [3.14.7]; addon watcher on Play [3.15.2]; OC on Play, M1 only [3.15.5]; update_play.py real run only on a real adopted pack update [3.15.6]; remove Lab hooks [3.15.7]; ElevenLabs [1]; decisions and sign-offs [5, 6].
