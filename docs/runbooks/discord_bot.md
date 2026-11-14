# Discord bot: setup and the from-the-phone checks (WO-9.1)

Clayton runs these on the gaming PC and his phone after the WO-9.1 PR is merged and deployed. About 20 minutes. Decisions: D295 (the design-job queue), D296 (the bot process, settings, secrets, the 10 s bound), D297 (Clayton only, confirm buttons, alerts, journal). Plan 7.12, §8 row 9.1.

`gos` below means the release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91). Nothing here costs money: a Discord bot and a private server are free. The bot never reads messages; it answers only your slash commands and buttons, and only from your user id.

```powershell
$ProgressPreference = 'SilentlyContinue'
$gos = 'C:\GregtecOS-app\current\.venv\Scripts\gos.exe'
```

## A. Make the bot (once, in a browser)

1. Open <https://discord.com/developers/applications>, **New Application**, name it `Gregtec OS`.
2. **Bot** tab: **Reset Token**, copy the token (you'll paste it in step B2; don't paste it anywhere else). Under **Privileged Gateway Intents** leave all three **off** (GOS uses no intents). Turn **Public Bot** off.
3. **Installation** tab (or **OAuth2 > URL Generator**): scopes `bot` and `applications.commands`; bot permissions `Send Messages` and `Attach Files` only. Open the generated URL and add the bot to **your private server** (make one first if needed: `+` > Create My Own > For me and my friends; keep it to yourself). Replies to `/ask`, `/plan` and `/status` are visible to everyone who can read the channel (base telemetry, the ledger, plans), so use a server or channel only you can read.
4. In Discord: **Settings > Advanced > Developer Mode** on. Right-click your own name > **Copy User ID** (your `owner_id`); right-click the server icon > **Copy Server ID** (`guild_id`).
5. Let the bot DM you: in the server, right-click its icon > **Privacy Settings** > **Direct Messages** on (results of long jobs come by DM when the command's 15 minutes are up). If DMs are off, the bot tries a result 3 times over about 2 minutes, then leaves it on the dashboard and tries again only when the bot restarts.

## B. Tell GOS (once, on the PC)

1. `%USERPROFILE%\GregtecOS\config\gos.toml`, add (your ids, digits only):
   ```toml
   [discord]
   owner_id = 123456789012345678     # your user id from A4: the only user the bot answers
   guild_id = 234567890123456789     # your server's id: commands appear there at once
   # dashboard_url = "https://<pc>.<tailnet>.ts.net"   # default: the ts.net name in [core] allowed_hosts
   ```
   The phone links in `/plan` and `/power` replies need the tailnet name in `[core] allowed_hosts` (WO-9.3 sets it up); until then replies say how to turn them on.
2. The bot token from A2 into the keyring (input hidden; it's stored as `gregtec-os/discord`):
   ```powershell
   & $gos key set discord
   ```
3. The bot's own token for the core (stored as `gregtec-os/token-discord`, D117):
   ```powershell
   & $gos token new discord
   ```
4. Check (prints `owner_id: set`, the phone-link base, and any problem; never a secret):
   ```powershell
   & $gos discord check
   ```
5. Start it: the supervisor left the bot stopped while it wasn't set up (exit 2). `& $gos serve` starts what was stopped; `& $gos status` shows `Discord bot` running. If it isn't, `%USERPROFILE%\GregtecOS\logs\discord.log` says why (e.g. "Discord refused the bot token": redo A2 and B2).

## C. Acceptance from the phone (§8 row 9.1)

On the phone, on **cellular** (Wi-Fi off), in your private server, with GOS running on the PC. Time each answer with the phone's stopwatch: each command must show Discord's "thinking" at once and a message **within 10 s** (a slow answer shows "Still working on it" first).

1. `/ask text: how do I make steel?`: an answer with `r:` citations within 10 s.
2. `/ask text: compare my steel routes` (a deep-lane question): "Handing this to the deep lane" within 10 s, the result later in the same thread.
3. `/plan target: steel ingot amount: 2/s`: "Plan job j-... queued" within 10 s; within a few minutes a reply with the routes and `plan:` ids, **flow.png** and **bom.tsv** attached, and the phone-dashboard link. Open the PNG and the BOM on the phone; tap the link (after WO-9.3, it opens the plan in the phone dashboard).
4. `/power mode: best`: "queued", then the options.
5. `/status`: AI state, OC telemetry (with `tel:` citations once the OC agent reports) and the EU ledger.
6. `/alerts mode: quiet hours: 2`, then `/alerts mode: on`.
7. `/persona` lists the packs; `/persona name: plain` switches (and back).
8. **Confirm button:** ask by voice or dashboard for a Lab action (e.g. "set a waypoint at the Lab base"), then `/status` on the phone: the read-back appears with **Confirm** / **Cancel**. Tap Confirm within 60 s: the buttons disappear and the desk's answer shows; the action is done in Lab. Let a second one expire and tap Confirm: "expired", nothing sent.
9. **Other users are ignored:** ask a friend (or a second account of yours) to join the server for the test, give them a role that can see the commands (Server Settings > Integrations > Gregtec OS > allow them), and have them run `/ask text: confirm`, `/status`, and tap a Confirm button you got in step 8 (repeat step 8 first). Each shows only Discord's own "The application did not respond" / "This interaction failed"; nothing happens on the PC, the pending action stays pending, and `discord.log` has only "ignored an interaction from another user". Remove the friend's access afterwards.
10. **Restarts:** run `/plan target: steel ingot amount: 1/s`, and while it is queued restart the bot (`& $gos stop`, then `& $gos serve`). The result arrives once, by DM. Do the same restarting during the design: it arrives once.
11. **No-AI mode:** with the AI off (Settings > AI, or `gos key delete anthropic` then put it back), `/ask` says what needs the AI and still answers lookups; `/status` says "AI: offline"; `/plan` still works.

Note in the PR: each command's time, and that 9 and 10 behaved as described.

## D. If something's wrong

- Commands don't appear: `guild_id` wrong, or the bot wasn't added with `applications.commands` (A3). Without `guild_id`, global commands take up to an hour.
- "Phone link: set [discord] dashboard_url": add the tailnet name to `[core] allowed_hosts` (WO-9.3) or set `dashboard_url`.
- Turn the bot off: remove `[discord] owner_id` (it stays stopped), or revoke its token (`& $gos token revoke discord`) and delete the key (`& $gos key delete discord`); reset the token in the developer portal if it ever leaked.
