# GregtecLink's client module: Lab check and Play install (WO-8.1, D285-D287)

Clayton's PC steps. Everything here runs as Clayton from `GOS_APP`; gos-dev never runs it, and nothing here
touches Play until step C. What the module does: [mod/README.md](../../mod/README.md#client-module-wo-81); the
protocol: [docs/contracts/gregteclink-client-v1.md](../contracts/gregteclink-client-v1.md).

`$app` below is `C:\GregtecOS-app\current` (your GOS_APP's `current`). **`gos` in your own window** is GOS_APP's copy: paste `$gos = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"` once, then type `& $gos ...` wherever this page writes `gos ...` for you (nothing puts `gos.exe` on your PATH, and `uv run gos` in your clone would run the clone's code, not the deployed one).

## A. Deploy

1. From your own clone, after the PR is merged: `uv run gos deploy`. It builds the jar into
   `$app\mod-dist\` (D98).
2. Run `sync_instances.py` once as usual (`& "$app\.venv\Scripts\python.exe" "$app\scripts\sync_instances.py"`). From this
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

## D. The hover-item eval (plan 9's 10 cases; WO-8.4, D362)

`tests/evals/hover_items.tsv` holds 10 hovered items exactly as the client module reports them (registry name, meta,
NBT; never a numeric id) and the question said while pointing: "what's this?", "how do I make this?", "what is this
used for?", "which tier is this?", an ambiguous name (IC2's Iron Plate), an item with NBT variants (a charged
RE-Battery), a fluid cell with NBT (water in IC2's Universal Fluid Cell), an item in no recipe (the Debug Fluid Tank),
an addon item (Good Generator's Field Restriction Casing) and a looked-at EBF. What a right answer must cite is read
from the DB at run time, so the file runs on your DB as it does on the client harness's (CI runs `--fake` on all 10).

1. **Free, any time after 3.1** (no key, no model): `& $gos eval hover --fake`. Pass: `passed: 10 of 10`,
   `Result: PASS`. A line `not in this DB: ...` means your Export run lacks that item: say which in the paste.
2. **Paid** (the fast lane, about $0.15 worst case; it prints the estimate first): `& $gos eval hover`, read the
   `worst case $` line, type `yes` (through `Tee-Object` the prompt may not show until you press Enter; type it after
   that line). It refuses while the AI is paused or off, or when today's spend plus the estimate would pass
   `[budget] day_max_usd`: then raise it for the sitting (checklist 3.14.5) or run it another day. Pass: `passed: 10 of 10`, `Result: PASS`. Paste: the table (`Tee-Object` it into
   `$gh\data\reports\hover-eval.txt`). A failing row says why (a wrong or missing citation, an uncited number, the
   wrong variant, the hovered item ignored); report it as it is, don't edit the case to make it pass.

This scores the core's answer to a reported hover; that the game reports the right item is B.5-B.6.
