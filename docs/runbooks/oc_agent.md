# OC agent: Lab steps (WO-7.2, WO-7.3)

Clayton runs these in Lab's `lab-ops` world once WO-7.2 and WO-7.1 (the core's `/oc/telemetry` endpoint) are merged
and deployed: about 1.5 hours, most of it the agent running on its own. They check what the build container can't:
the real drivers (V20), the real tick cost, and an hour of uptime (plan §8 WO-7.2: **1 h stable in `lab-ops`; no TPS
drop > 1**). The open-to-LAN check (V19) adds 5 minutes, the stress check (G) 20. Decisions: D213 (the agent), D214
(the contract), D215 (the tests). Contract: [docs/contracts/oc_bridge.md](../contracts/oc_bridge.md).

TPS stays at 20 until the server's mean tick time passes 50 ms, and a TPS drop over 1 means a mean tick over
52.6 ms, so in a light creative world TPS alone says little. The evidence to write down is the **mean tick time in
ms**, with and without the agent; the acceptance is still no TPS drop over 1.

`gos` below is Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), in a normal
PowerShell window (not "Run as administrator"). Nothing here touches Play.

## A. Before the game

1. In the profile clone: `git pull`, then `uv run gos deploy`. The agent's files are then in
   `C:\GregtecOS-app\current\oc\`.
2. Check Lab has the OpenComputers tweaks (plan §5.6: `"allow ip:127.0.0.1"` first in `filteringRules`,
   `requestTimeout=10`), with the game closed:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\python.exe C:\GregtecOS-app\current\scripts\check_tweaks.py C:\Games\PrismLauncher\instances\GTNH-Lab
   ```
   Expect the `oc_loopback`, `oc_timeout` and `oc_rules` rows `ok`. If one isn't, run the same line with `--apply`
   (Lab only; it asks first).
3. Make the agent's token and keep the window open (it is printed once, D117):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe token new oc-lab --show
   ```
   If WO-7.1's runbook names another token command for the OC endpoint, use that one.

## B. The computer in `lab-ops`

4. Launch Lab (`gos launch lab` or Prism) and open `lab-ops`. In creative, build next to a few machines:
   - a computer case T3 with a **CPU T3 switched to Lua 5.3** (hold it, shift-right-click until the tooltip says
     Lua 5.3), 2× memory T3.5, a GPU T3, an **internet card**, a hard drive T3 (leave it in managed mode, the default)
     with OpenOS installed on it (OpenOS floppy in a disk drive, boot, `install`, reboot), a screen and a keyboard;
     power (an OC capacitor on a power converter, or a creative capacitor);
   - **adapters** (cables or touching blocks) on: two or three GT multiblock **controllers** (one EBF at least), one
     **battery buffer** with batteries in it, the **LSC controller** if `lab-ops` has one, and the **ME controller**;
   - a Computronics **chat box** and, optional but useful for step 14, a **tps card** in the case.
5. In the OC shell, mark the drive so its folder is easy to find:
   ```
   mkdir /home/gos
   echo marker > /home/gos/where-am-i.txt
   ```

## C. Put the agent on the drive

6. In PowerShell, find the drive's folder by the marker and copy the agent there:
   ```powershell
   $ProgressPreference = 'SilentlyContinue'
   $oc = 'C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft\saves\lab-ops\opencomputers'
   $drive = Get-ChildItem $oc -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'home\gos\where-am-i.txt') }
   $drive.FullName          # exactly one folder
   $gos = Join-Path $drive.FullName 'home\gos'
   Copy-Item C:\GregtecOS-app\current\oc\gos-agent.lua, C:\GregtecOS-app\current\oc\json.lua $gos
   Copy-Item C:\GregtecOS-app\current\oc\gos-agent.cfg.example (Join-Path $gos 'gos-agent.cfg')
   notepad (Join-Path $gos 'gos-agent.cfg')
   ```
   In Notepad, replace `paste the OC agent token here` with the token from step 3, add a watchlist of 3 to 5 items
   the ME system holds (registry name and damage, as NEI shows them with advanced tooltips, F3+H), save. Keep the
   example's iron and steel ingots, and put a stack of each in the ME system: step 13 compares their counts.
   OC sees the files at once (GTNH's `bufferChanges=false`).

## D. First run

7. In the OC shell:
   ```
   cd /home/gos
   gos-agent --list
   ```
   Expect a line per GT block: `machine  gt_machine ... multimachine.<name> at x,y,z` for the multiblocks,
   `power    gt_batterybuffer ... (battery_buffer)`, the LSC as `power    gt_machine ... multimachine.supercapacitor
   ... (storage)`, `ae2      me_controller ...`, and an `all:` line with every component type.
   **V20:** write down whether `gt_machine` appears, and what type the LSC shows as. If a GT block is missing, say
   which (and whether its adapter touches the controller).
8. `gos-agent --once` should end with `sent N bytes; the core answered` (it sends one message, or a few if the core
   had commands waiting: it reports their results before it exits). A failure names the reason: `unauthorized`
   (token, step 3), `Connection refused: connect` (the core isn't running: `gos status`), `address is not allowed`
   (step 2's tweak is missing or the game wasn't restarted after it), `http request headers are unavailable` (Lab's
   `config\OpenComputers.cfg` has `enableHttpHeaders=false`; GTNH ships `true`).

## E. One hour

9. Before starting the agent, read the baseline: in chat, `/forge tps` (cheats on; `lab-ops` has them, or open
   to LAN with cheats). Note the `Overall` line's mean tick time (ms) and TPS. Do it three times, a minute apart.
10. Start the agent and note the time:
    ```
    gos-agent
    ```
11. Leave the game running for an hour, unpaused: no Esc menu, `pauseOnLostFocus:false` is set (D85), so alt-tab is
    fine. Every 15 minutes, run `/forge tps` again and note it.
12. During the hour, try the commands through the core (WO-7.1's confirm flow): one `say`, one `machine_pause` then
    `machine_resume` on the EBF, one small `craft` the ME system has a pattern for. Each should show its result in
    the core within about 10 s, and `say` should print `[ChatBox] ...` in chat.
13. What to watch:
    - the OC screen: warnings only; no `too long without yielding`, no `not enough memory`, no stack trace. The
      agent must still be running at the end of the hour;
    - `gos-agent.log` (in the drive folder from step 6, `home\gos\gos-agent.log`, readable from Windows): an
      `alive:` line every 5 minutes. Cycles should rise by about 60 per 5 minutes, `failed` stay 0 (or a handful if
      the core restarted), `bytes free` stay roughly level (a steady fall means a leak), `s sampling` stay under 1;
    - the core's OC status (WO-7.1's runbook): a message every 5 s with power, machines and AE2 CPUs, the watchlist
      every 60 s. The iron and steel ingot counts there match the ME terminal's (the agent counts the stack with no
      NBT; a renamed or otherwise tagged stack is another item and isn't in that count).
14. **Tick time:** compare step 11's mean tick times with step 9's: write down both sets of numbers and the
    difference in ms. The acceptance is no drop over 1 TPS in any reading. With a tps card in, every message also
    carries `tick_ms` (the same mean tick time; TPS = min(20, 1000 / tick_ms)), so the core can show the whole hour.
15. Stop it with Ctrl+C: it prints `stopped` within a second or two. Run `gos-agent` again; it starts with a new
    boot id and the core keeps getting messages.

## F. Open to LAN (V19, 5 minutes)

16. With the agent running and the game not open to LAN, press Esc and leave the menu open for 60 s, then close it.
    The log and the core should show a 60 s gap (the integrated server, and so OC, was paused; plan §7.9).
17. Esc, Open to LAN, Start LAN World. Press Esc again and leave the menu open for 60 s. **V19:** if messages kept
    arriving every 5 s through that minute, Open to LAN avoids the pause freeze; if there is a gap again, it doesn't.

## G. A big network (stress, 20 minutes)

The watchlist and `craft` make exact AE2 lookups (D213), whose server cost grows with how many item types the
network holds; the Lab's small ME system can't show it. This makes it bigger.

18. Stop the agent (Ctrl+C). Give the ME system a few hundred item types, most of them GregTech's: in NEI (cheat
    mode), search `@gregtech dust`, shift-click items into your inventory (a full stack each), then shift-click them
    into the ME terminal; repeat with `ingot`, `plate` and `rod` until the terminal shows 300 types or more.
19. In `gos-agent.cfg`, watch five `gregtech:gt.metaitem.01` damages the network now holds (F3+H shows the damage;
    11305 is the steel ingot), plus the iron ingot, and save.
20. Without the agent, read `/forge tps` three times, a minute apart. Start `gos-agent`, wait two minutes, and read
    it three times again, a minute apart. In those minutes, send one small `craft` through the core, as in step 12.
21. Write down both sets of mean tick times. Check: the craft's result arrives; the core's counts for the five
    watched items match the terminal's; the OC screen shows no `not enough memory`; `bytes free` in the log's
    `alive:` lines stays roughly level.

## H. M1: the file mailbox, one hour (WO-7.3, about 70 minutes)

The acceptance of plan §8 WO-7.3: **1 h in `lab-ops` with no lost or half-read files**. The same computer, with the
agent switched to the mailbox; the internet card can stay in. Contract: "M1" in
[oc_bridge.md](../contracts/oc_bridge.md); decisions D216-D218. The core then writes in `lab-ops`'s OC folder only
(`inbox.tmp`, `inbox.json`, and the rename of `outbox.json` back to `outbox.tmp`); nothing here touches Play.

22. With WO-7.3 deployed (step 1), copy the mailbox transport next to the agent. If the step-6 window is closed, run
    its first three lines again to set `$oc`, `$drive` and `$gos`:
    ```powershell
    Copy-Item C:\GregtecOS-app\current\oc\mailbox.lua $gos
    $drive.Name              # the drive's address, for step 24
    ```
23. In `gos-agent.cfg` (step 6), change `transport = "auto",` to `transport = "mailbox",` and save.
24. Add this to `%USERPROFILE%\GregtecOS\config\gos.toml`, with step 22's address, and save:
    ```toml
    [mailbox.lab]
    world = "lab-ops"
    drive = "<the address from step 22>"
    dir = "home/gos/mailbox"
    ```
    Restart the core so it reads it, then check it watches the folder:
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe stop
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe serve --detach
    Select-String -Path $env:USERPROFILE\GregtecOS\logs\core.log -Pattern 'OC mailbox' | Select-Object -Last 3
    ```
    Expect `OC mailbox watched` (and, once the agent has made the folder, `OC mailbox ready`). **A mistake in this
    table stops the whole GOS, not only the mailbox** (like any other service key in gos.toml): `gos serve` prints
    `Not starting GOS: <the reason>` and exits, and `core.log` has `refusing to start: fix gos.toml` with the same
    reason. Fix the line it names (or remove the table), then run `gos serve --detach` again.
25. In the OC shell, stop the agent if it runs (Ctrl+C), then run `df` and note the line of the drive whose address
    starts like step 22's (its used and available space), then `gos-agent --once`. Expect `sent N bytes; the core
    answered`. The agent's log starts with `mailbox /home/gos/mailbox on drive ...; in gos.toml: drive = "...", dir =
    "home/gos/mailbox"`: the drive and dir must match step 24. `no reply from the core yet` means the core doesn't
    watch this folder (step 24).
26. Run `gos-agent` and note the time. Leave the game running for an hour, unpaused, as in step 11. Halfway, send one
    `say` through the core (step 12); it should print in chat within about 10 s.
27. After the hour, compare the two sides' **final** counts. The `alive:` lines and the core's 5-minute stats lines
    come on different clocks, so don't compare those; each side writes its final counts when it stops:
    1. In the OC shell, stop the agent (Ctrl+C). Its last line in `gos-agent.log` is `stopped (interrupted); N cycles,
       N sent, N failed; mailbox: N written, N replies, N waits for the core, ...`. Then run `df` again and note the
       same drive's line.
    2. Wait 5 seconds (the core takes the last message), then stop the core, which writes its final `OC mailbox
       stats` line as it stops:
       ```powershell
       & C:\GregtecOS-app\current\.venv\Scripts\gos.exe stop
       ```
    3. Read both lines and the folder:
       ```powershell
       Select-String -Path (Join-Path $gos 'gos-agent.log') -Pattern 'stopped' | Select-Object -Last 1
       Select-String -Path $env:USERPROFILE\GregtecOS\logs\core.log -Pattern 'OC mailbox stats' | Select-Object -Last 1
       Get-ChildItem (Join-Path $gos 'mailbox') | Select-Object Name, Length, LastWriteTime
       ```
    The pass, from those two lines: the agent's `failed` 0, `written` about 720 (one per 5 s) and `removes failed` 0;
    the core's `refused_bad_payload` absent or 0 (a half-read `outbox.json`), `accepted` equal to the agent's
    `written` (none lost; a few `repeats` are harmless), `seq_gaps` equal to the agent's `waits for the core`,
    `withdrawn` 0, `poll_errors` absent. The folder holds `outbox.tmp` and at most one `inbox.json` or `outbox.json`,
    no `inbox.tmp`. Then `gos serve --detach` to start GOS again.

    The two `df` lines are not a pass or fail: send them back. OC never learns of a file the core writes, so its
    count of the drive's used space is expected to fall a little each exchange (by about 512 bytes plus the reply's
    size when OC deletes `inbox.json`), not to grow (D216); the two lines check that against the real OC.
28. Set `transport` back to `"auto"` in `gos-agent.cfg` if section E should use HTTP again. Leave `[mailbox.lab]` in
    gos.toml or remove it; with it, the core keeps watching the folder.

## What to send back

- Step 7's `--list` output (V20) and step 8's line.
- The mean tick times and TPS from steps 9 and 11 (and the core's `tick_ms` graph if a tps card was in).
- The last three `alive:` lines of `gos-agent.log`, and any warning lines.
- Steps 12, 13 (the ingot counts), 16 and 17's outcomes, and any step whose output differed from what is written here.
- Section G's two sets of mean tick times and step 21's checks.
- Section H: step 25's line, both `df` lines (steps 25 and 27), and step 27's output.
