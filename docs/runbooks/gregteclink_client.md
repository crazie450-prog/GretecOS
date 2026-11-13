# GregtecLink's client module: Lab check and Play install (WO-8.1, D285-D287)

Clayton's PC steps. Everything here runs as Clayton from `GOS_APP`; gos-dev never runs it, and nothing here
touches Play until step C. What the module does: [mod/README.md](../../mod/README.md#client-module-wo-81); the
protocol: [docs/contracts/gregteclink-client-v1.md](../contracts/gregteclink-client-v1.md).

`$app` below is `C:\GregtecOS-app\current` (your GOS_APP's `current`).

## A. Deploy

1. From your own clone, after the PR is merged: `uv run gos deploy`. It builds the jar into
   `$app\mod-dist\` (D98).
2. Run `sync_instances.py` once as usual (`$app\.venv\Scripts\python.exe $app\scripts\sync_instances.py`). From this
   version on, Lab's default `[sync.lab] exclude` keeps `mods/gregteclink*` and `config/gregteclink*`, so Lab's
   GregtecLink is its own and never Play's.

## B. Lab check (the acceptance on the PC)

1. Close every GTNH client. The core must be running (`gos status`).
2. Dry run, then install:

   ```powershell
   & "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance lab
   & "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance lab --apply
   ```

   It prints the jar, the config and `token: gregteclink-lab -> ...\GregtecOS\config\gregteclink-lab.token`.
3. Launch Lab (Prism, or `gos launch lab`), open a Lab world. In Options, Controls, GregtecLink, bind
   **What's this? (Gregtec OS)** to a key nothing else uses and that types nothing: F13-F24, Pause, or a mouse
   button (as D180 does for the push-to-talk key). It is unbound on purpose (GTNH binds many keys). A letter works
   but is a poor pick: the key is ignored while you type into chat, a sign, a book, a focused text field (an anvil,
   an AE2 terminal's search) or NEI's search box, but a text box some mod draws its own way could still fire it.
4. See the link: in PowerShell,

   ```powershell
   $core = Get-Content "$env:USERPROFILE\GregtecOS\run\core.json" | ConvertFrom-Json
   Invoke-RestMethod "http://127.0.0.1:$($core.port)/api/game" -Headers @{ Authorization = "Bearer $($core.token)" }
   ```

   `links` shows `binding: lab` with your player name; `status` your position, updated every second.
5. **10 items, addons included.** For each, press the key: GOS answers in chat (`[GOS] <name>, from <mod>. ...`) and
   the card shows on monitor 2. Hover in your inventory and in NEI's item list over: a GT ingot, a GT circuit, a
   bartworks or GT++ material, a GoodGenerator or TecTech part, an AE2 item, an IC2 battery (charged), a vanilla item;
   then look (no GUI) at a placed GT machine, a GT casing and an AE2 or other mod's block. Each answer must name what
   the tooltip shows. Note any that doesn't. NEI's item list is checked only here (the harness hovers chest slots),
   so do at least three of the hovers in it and one on an NEI recipe page. Screens that aren't containers (the quest
   book, the Thaumonomicon) are not read: there the key answers that nothing is pointed at.
6. **By voice** (with `gos voice run`): hover over an item and say "what's this?". The answer is spoken and the card
   shows. Do it once with no GUI open, looking at a block.
7. **`!gos`**: type `!gos recipes for steel ingot` in chat. A `[GOS]` reply comes back. Only your own name works, and only in your own world (single player or a LAN world you host): on a server it is ignored.
8. **Reconnect**: `gos stop`, wait 5 s, `gos serve`. Within about 15 s, step 4 shows the link again and the key works.
9. **Toggles**: close Lab, set `B:chat=false` in Lab's `config\gregteclink.cfg`, start Lab: chat lines no longer
   appear in step 4's `chat`. Set it back (or run the install again: a feed you turned off stays off, so edit it).
   The same for `position` (no `status`), `target` (the key does nothing; "what's this?" says the feed is off) and
   `notices` (no `[GOS]` lines).
10. **Paused** (V29 evidence): press Esc; step 4's `status.paused` turns `true` within a second.

Report the results (and anything odd) in the PR or to the PM.

## C. Play install

Only after B passes:

1. Close Play. The core must be running.
2. ```powershell
   & "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance play
   & "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance play --apply
   ```
   It installs the jar with the listener and client modules on and export off (the listener registers nothing until
   WO-6.2 lands). Play's fingerprint ignores these files (they match Export's exclude list), so Play doesn't turn
   stale.
3. Bind the key in Play's Controls as in B.3.

To remove it: delete `mods\gregteclink-*.jar` and `config\gregteclink.cfg` from the instance, and
`gos token revoke gregteclink-play`.
