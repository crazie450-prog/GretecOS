# Core service: first start and acceptance checks (WO-2.1)

Clayton runs these on the gaming PC after the WO-2.1 PR is merged. They take about 15 minutes. Decisions: D115 (service), D116 (login start), D117 (auth), D118 (usage meter).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91). `curl.exe` is the one that ships with Windows.

## A. Deploy and start at login (as Clayton)

1. In the profile clone, in a normal window: `git pull`, then `uv run gos deploy`.
2. Turn on the login start and start GOS now, in a normal PowerShell window (not "Run as administrator": the script refuses an elevated window, and GOS refuses to run elevated, because everything it starts would be elevated too):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\scripts\install_login_start.ps1 -StartNow
   ```
   It prints `GOS starts at login: ...\Run\GregtecOS = "C:\GregtecOS-app\current\.venv\Scripts\gos-supervisor.exe" --login`. No window opens: the supervisor runs in the background.
3. `gos status`. Expect `overall: ok` and these lines (spacing aside; `warn` on the key line, and so on `overall`, if no Anthropic key is stored):
   ```text
   Supervisor     ok     running  (pid ...)
   Core           ok     running  (pid ...)
   Database       ok     gos.sqlite
   Anthropic key  ok     stored
   API credits    ok     $0.00 of $100.00 this cycle (0%)
   ```

   If it says `GOS isn't running`, read `%USERPROFILE%\GregtecOS\logs\supervisor.log` and `core.log` (one JSON object per line) and `core.stderr.log`. A `refusing to start` line names the gos.toml key to fix: Appendix C's placeholder `"<pc>.<tailnet>.ts.net"` in `[core] allowed_hosts` is one (delete it until WO-9.3). `gos status` and `gos deploy` also print such problems as a `Warning:` line.

## B. Acceptance checks (§8 WO-2.1)

4. **A killed process restarts within 10 s and shows on the strip.** Run `gos status` and note the Core line's pid. It is the core's own pid, the one in `run\core.json` and `/health`; Task Manager also shows a second `python.exe`, the venv's launcher, whose pid is `launcher_pid` in `gos status --json`. `gos status` prints the same data the dashboard's health strip will show (`GET /api/health`). Then:
   ```powershell
   Stop-Process -Id <core pid> -Force; Start-Sleep 10; & C:\GregtecOS-app\current\.venv\Scripts\gos.exe status
   ```
   Expect `Core  ok  running  (pid <a new pid>)  restarts: 1  last exit: code <non-zero>`. In the Linux container the core answered again 1.8 s after the kill.
5. **A foreign Host or Origin is rejected.**
   ```powershell
   curl.exe -s -H "Host: evil.example" http://127.0.0.1:8765/health
   curl.exe -s -H "Origin: http://evil.example" http://127.0.0.1:8765/health
   curl.exe -s http://127.0.0.1:8765/health
   ```
   Expect `{"error": "host not allowed"}`, then `{"error": "origin not allowed"}`, then `{"status":"ok","service":"gregtec-core",...}`.
6. **An unpaired device is refused.** `curl.exe -s http://127.0.0.1:8765/api/health` prints `{"error":"not paired: pair this device first"}`. Opening `http://gos.localhost:8765/api/health` in Chrome shows the same. The dashboard's address is `http://gos.localhost:8765/` (Chrome resolves every `*.localhost` name to this PC), and pairing works only there or on the tailnet name, never on `127.0.0.1` or `localhost`: cookies ignore ports, so a cookie for `localhost` would also go to every other local web server, gos-dev's dev servers included (D117). Pairing itself gets a screen in WO-2.6: until then, `gos pair` lists waiting devices with their codes, and `gos devices` lists paired ones.
7. **Stop and start.** `gos stop` prints `Stopped (the supervisor keeps running; ...)` and a `core  stopped` line. `gos status` then says `The core isn't answering; the supervisor says:` and shows it stopped. `gos serve` starts it again within a few seconds. `gos stop --all` stops the supervisor too; step 2's `-StartNow` (or logging off and on) brings GOS back.
8. **Login start.** Sign out and in again (or reboot). `gos status` shows Supervisor and Core running, with no window opened.

## C. V24: `gos launch` from gos-dev

9. As gos-dev, add the following to gos-dev's own `gos.toml` (in its GOS_HOME's `config\`):
   ```toml
   [launch]
   home = "C:/Users/<you>/GregtecOS"

   [core]                              # gos-dev's own core, never Clayton's (D115)
   port = 8775
   supervisor_port = 8776
   dashboard_host = "gos-dev.localhost"
   ```
   Use your own profile path. gos-dev reads only `data\launch.json` there, which holds the supervisor's port and a token that can only launch Lab or Export. The `[core]` lines keep a `uv run gos serve` of gos-dev's off your ports and your dashboard's name: as gos-dev, `gos serve` refuses 8765, 8766 and `gos.localhost`.
10. As gos-dev, in `C:\GregtecOS-dev\<clone>`: `uv run gos launch lab`. Expect `Asked Prism Launcher to launch GTNH-Lab`, and Lab starts through Prism, as you. Close it, then `uv run gos launch export` (with the autoexport flag set, this runs an export and quits). `uv run gos launch play` is refused (`invalid choice`), and the supervisor refuses anything but `lab` and `export` too. A second launch within 30 s of the first is refused (`wait`), whichever instance it is.
11. **Never two clients at once [A§5.5].** Start Play yourself and wait for its main menu. As gos-dev, `uv run gos launch lab` must be refused with `refused: GTNH-Play is running`. This checks that Prism's Java command line names Play's instance folder, which the supervisor looks for (Win32_Process, as you). If Lab starts instead, close it, note it in the PR, and don't use `gos launch` while Play runs until it is fixed.
12. If all of this works, V24 is resolved.

## D. Next deploys

`gos deploy` now stops GOS just before it swaps `current\`, because Windows can't rename the folder while the supervisor runs from it, and starts it again afterwards with the same gos.toml (the Run key's `--config`, if any). It prints `Stopping GOS for the swap ...` and `Restarted GOS: ...`. GOS is down for a few seconds, not for the staging and the GregtecLink build, and a deploy that is refused (uncommitted changes, a ref not in `origin/main`) doesn't stop it. Deploy from a normal window: a GOS restarted from an elevated one would refuse to start.

## Turning it off

- Until the next `gos serve`: `gos stop` (keeps the supervisor) or `gos stop --all`.
- At login: set `[startup] core_at_login = false` in `gos.toml`, or remove the entry:
  `powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\scripts\install_login_start.ps1 -Remove`.
