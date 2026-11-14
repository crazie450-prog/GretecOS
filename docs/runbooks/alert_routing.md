# Alert routing: the real-DM checks (WO-9.2)

Clayton runs these on the gaming PC, in Lab, with his phone, after the WO-9.2 PR is merged and deployed (`gos deploy`) and the Discord bot works (`docs/runbooks/discord_bot.md` A–C). About 45 minutes, most of it waiting 10 minutes away from the keyboard. Decisions: D298 (where each alert goes), D299 (the router, once per occurrence, the outbox), D300 (the bot's DMs, credit buttons, "GOS offline"). Plan 7.12 "Alerts", §8 row 9.2. Nothing here costs money and nothing touches Play.

`gos` below means the release copy (D91):

```powershell
$ProgressPreference = 'SilentlyContinue'
$gos = 'C:\GregtecOS-app\current\.venv\Scripts\gos.exe'
```

What goes where (defaults pending your answer, D298): base alerts (power low, maintenance, a stalled machine, crafting done, the advisor's notices) are said by voice while you're at the PC and DMed while you're away (no keyboard or mouse input for 10 minutes); never both. Credit alerts are DMed whatever you're doing. "Game paused" and "GOS offline" are DMed only while you're away, once each. `/alerts off` stops DMs; `/alerts quiet <h>` and `[notify] quiet_hours` hold everything below `err` until they end.

## 1. Presence (V35)

1. With the core running, type something in any window, then at once:
   ```powershell
   & $gos alerts routed
   ```
   `"away": false` under `context`.
2. Typing a command is input, so check "away" with a delay. Start this, then leave the keyboard and mouse alone (a video playing is fine; a game pad doesn't count):
   ```powershell
   Start-Sleep -Seconds 660; & $gos alerts routed > $env:TEMP\routed.json
   ```
   Come back after 12 minutes and open `$env:TEMP\routed.json`: `"away": true`. If it says false, V35 failed: note it in the PR; away-only DMs won't go.

## 2. A test DM

```powershell
& $gos alerts test
```
Within a few seconds the bot DMs "Test alert from GOS". Nothing else in your DMs.

## 3. Esc, then walk away (the paused DM, D76)

1. Launch Lab (`& $gos launch lab`), open a creative world, wait until the dashboard shows the game (and the watchers run: `docs/runbooks/watchers.md`).
2. Press **Esc** (the game menu: single-player pauses) and leave the PC alone.
3. Exactly about 10 minutes after the Esc press, your phone gets one DM: "Lab is paused since HH:MM ...". Wait 5 more minutes: no second DM.
4. Come back, close the menu (unpause). Press Esc again and stay at the PC for 5 minutes: no DM. Unpause.
5. The other order: leave the PC alone for 10 minutes with the game running and focused (set a 12-minute timer), then have something steal focus... simplest: before you leave, run `Start-Sleep -Seconds 660; (New-Object -ComObject WScript.Shell).AppActivate('Windows PowerShell')` in a PowerShell window, then click back into the game and walk away. With `pauseOnLostFocus` on, the game pauses when focus moves at 11 minutes; the DM comes within a few seconds of that. (With it off, nothing pauses and no DM comes: also right.)

## 4. A base alert while away (D46)

Needs the Lab OC agent running (`docs/runbooks/oc_agent.md`) and a machine that can stall.

1. At the PC: stall a machine (take its input away). Within `[alerts] stalled_s` (60 s) GOS says "Lab: <machine> has stalled." Walk away for 11 minutes: no DM (you were told once, by voice).
2. Fix it and let the alert clear. Use a second machine (a new key, so no cooldown) whose input runs out on its own after 15 minutes or more (a buffer of a few stacks), and walk away before it runs dry. One DM when it stalls: "[warn] Machine stalled: ...", with its `tel:` cite. Come back: it isn't said by voice too.
3. `& $gos alerts routed` shows where each active alert went (`sent`) and why not (`held`).

## 5. Credit DM buttons (D48)

Simulated: no money is spent; the cycle's spend is the meter's own rows.

1. Make the meter cross 80% with a fake usage row (the core must be stopped while you edit the DB):
   ```powershell
   & $gos stop
   $db = "$env:USERPROFILE\GregtecOS\data\gos.sqlite"
   & C:\GregtecOS-app\current\.venv\Scripts\python.exe -c "import sqlite3,time,sys; c=sqlite3.connect(sys.argv[1]); c.execute('INSERT INTO llm_usage(ts,lane,model,in_tok,out_tok,cache_read,cache_write,usd,task) VALUES (?,?,?,?,?,?,?,?,?)',(int(time.time()),'test','test',0,0,0,0,80.5,'runbook')); c.commit()" $db
   & $gos serve
   ```
2. One DM: "API credits: 81% ..." with **Open billing**, **No-AI mode**, **Stop**. Press **Open billing**: the reply has the Console link and the other two buttons again.
3. Ask a friend (or a second account) in your private server to press a button on a message the bot posts there (e.g. a `/ask` reply's confirm button): nothing happens for them and nothing changes on the dashboard. (DM buttons are only in your DMs, so others can't reach those at all.)
4. Press **No-AI mode**: "No-AI mode is on ..."; the dashboard banner shows it.
5. Undo: delete the row (`DELETE FROM llm_usage WHERE task = 'runbook'`, core stopped), `& $gos serve`, then **Resume AI** on the dashboard.
6. Optional, the Stop path: repeat 1, press **Stop**, then **Yes, stop GOS**: the core stops (the supervisor leaves it stopped, D165); `& $gos serve` starts it. No "GOS offline" DM comes for that, even when you're away (you stopped it).

## 6. "GOS offline" (D76, sent by the bot)

The supervisor restarts a crashed core in about 2 seconds, so a plain kill isn't an outage. Make the core fail to start instead, and leave the PC:

1. In `gos.toml`, under `[core]`, set `host = "0.0.0.0"` (the core refuses to start with it; the bot and the supervisor don't mind).
2. `Get-Process python | Where-Object { $_.CommandLine -like '*gregtec.api*' } | Stop-Process` (the supervisor restarts it; it now exits at once and backs off).
3. Walk away. About 10 minutes after your last input, one DM: "GOS offline: the core on your PC hasn't answered for ... s".
4. Come back, remove the `host` line, wait a minute: "GOS is back" arrives once the core's next try starts it (or run `& $gos serve`).

## 7. Quiet hours and /alerts

1. In Discord, `/alerts quiet 1`. Repeat §3 (Esc, walk away): no DM for an hour (a `warn` waits). `/alerts on` from your phone: the DM comes within seconds if the game is still paused and you're still away.
2. `/alerts off`, repeat §2's `gos alerts test`: no DM. `/alerts on`.
3. Quiet hours (optional): `[notify] quiet_hours = "HH:MM-HH:MM"` around the next half hour, restart (`& $gos stop; & $gos serve`), repeat §3 inside it: no DM until it ends, then one.

## Report

In the PR: for each section, pass or what you saw; V35's result; and your answers to the PR's Questions (the `[notify]` defaults, whether "GOS is back" is wanted, whether credit DMs should ignore `/alerts off`).
