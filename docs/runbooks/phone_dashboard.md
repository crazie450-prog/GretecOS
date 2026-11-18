# Phone dashboard: Tailscale Serve, pairing the phone, the cellular check (WO-9.3)

Clayton runs these on the gaming PC and his phone, after the WO-9.3 PR is merged and deployed (`gos deploy`), with the core running and the dashboard paired on monitor 2 (`docs/runbooks/dashboard.md` B). The plan-link step needs the Discord bot (`docs/runbooks/discord_bot.md` A–C). About 30 minutes. Decisions: D301 (Tailscale Serve, never Funnel, the tailnet name), D302 (the phone's tabs and More), D303 (the phone e2e and its fake Serve). Plan 4.6, 7.12 "Phone dashboard", §8 row 9.3, V28, L8. Nothing here costs money (Tailscale's Personal plan is free) and nothing touches Play.

What it does: Tailscale Serve, on this PC, answers `https://<pc>.<tailnet>.ts.net/` for devices **in your tailnet only** and passes each request to the core on `127.0.0.1:8765`. The core still listens on loopback only. **Funnel is never turned on**: `gos tailnet` refuses it, refuses to set up while it is on, and the core refuses every request Tailscale marks as having come through Funnel.

`gos` below means the release copy (D91):

```powershell
$ProgressPreference = 'SilentlyContinue'
$gos = 'C:\GregtecOS-app\current\.venv\Scripts\gos.exe'
```

## A. Tailscale on the PC and the phone (once)

1. On the PC: install Tailscale from https://tailscale.com/download (Windows), sign in with your own account. In the tray icon it shows **Connected**.
2. In the admin console (https://login.tailscale.com/admin/dns): **MagicDNS** on, and under **HTTPS Certificates** click **Enable HTTPS**. Serve needs both: "Tailscale Serve requires you to enable HTTPS certificates in your tailnet" (tailscale.com/kb/1312/serve).
3. On the phone: install the Tailscale app, sign in with the **same** account, turn the VPN on.
4. Keep the PC awake while you want phone access (plan 4.2: Windows power plan, no sleep while GOS runs).

## B. Serve the dashboard to the tailnet

1. In a normal (not elevated) PowerShell window:
   ```powershell
   & $gos tailnet setup
   ```
   Expect:
   ```
   https://<pc>.<tailnet>.ts.net/ now proxies to http://127.0.0.1:8765
   Funnel is off; the dashboard is for your tailnet only.
   Next: add "<pc>.<tailnet>.ts.net" to [core] allowed_hosts in gos.toml, then restart GOS ...
   ```
   The first run may stop for a minute while Tailscale gets the certificate. It runs `tailscale serve --bg --https=443 http://127.0.0.1:8765`, which Tailscale keeps across reboots.
   - `Refused: Tailscale Funnel is on for ...`: something on this PC has Funnel on. Run the `tailscale funnel --https=<port> off` it prints (or `tailscale funnel reset` if nothing else of yours uses Serve), then run setup again.
   - `https://... already serves something else`: another of your services has port 443 on this PC. Setup won't replace it; remove it (`tailscale serve --https=443 off`) only if GOS should have it.
   - `Tailscale isn't running and logged in`: step A.1.
2. `%USERPROFILE%\GregtecOS\config\gos.toml`, `[core]`: add the name it printed (no `https://`, no port):
   ```toml
   allowed_hosts = ["<pc>.<tailnet>.ts.net"]
   ```
   Until this line is there, the core refuses the tailnet name ("host not allowed"), so the dashboard is unreachable from the phone even with Serve on. The Discord bot's phone links use the same name (`[discord] dashboard_url` defaults to it).
3. Restart GOS so it reads the change: `& $gos stop`, then `& $gos serve` (or sign out and in).
4. Check:
   ```powershell
   & $gos tailnet check
   ```
   Four `ok` lines: Tailscale running (with the name), `Funnel is off`, the proxy to `127.0.0.1:8765`, the name in `[core] allowed_hosts`. Any `BAD` line says what to do. Run it again whenever you change Tailscale.

To stop serving the dashboard to the phone: `& $gos tailnet off` (the core keeps running; the phone gets nothing).

## C. Pair the phone (once per phone)

1. On the phone, on Wi-Fi or cellular with Tailscale on, open `https://<pc>.<tailnet>.ts.net/` in the browser you'll use (bookmark it or add it to the home screen).
2. It shows **Pair this device** with the name `Phone`. Tap **Ask to pair**.
3. Monitor 2 shows the 8-digit code on the right rail under "Waiting for you" (or run `& $gos pair`). Type it on the phone, tap **Pair**. The phone shows Now. The code never appears on the phone itself.
4. Reload the page on the phone: it opens straight away, no second pairing. The pairing cookie is `Secure`, HttpOnly and kept only for the tailnet name.
5. Settings + Health on monitor 2 → Paired devices lists `Phone`. **Revoke** there (or `& $gos devices revoke <id>`) and the phone's page drops to "Pair this device" at its next request; its live connection closes at once.

## D. The phone layout

Tabs at the bottom: **Now, Chat, Search, Settings, Base, More**. More lists every other view (Designer, Power, Quests, Crops, Costs, Activity, Ores, Build); tapping one opens it with More lit. Approvals come first on Now (the phone has no right rail), with the same Confirm and Cancel buttons as monitor 2.

## E. The cellular check (L8, V28)

Turn Wi-Fi **off** on the phone (cellular only), Tailscale on. With Lab running (`& $gos launch lab`, a creative world, the OC agent from `docs/runbooks/oc_agent.md` if you want a craft):

1. **A Discord plan link opens.** In Discord on the phone run `/plan target: steel ingot amount: 1/s`. The reply's link `https://<pc>.<tailnet>.ts.net/#/designer/pl-...` opens the plan in the phone's browser (More is lit, the routes show).
2. **A Lab action is confirmed.** On the phone: More → Ores → type `Ferrite` (or any ore in the Lab world) → Find → **Add waypoint** on a vein (or ask for a craft in Chat). Go to Now: the approval reads it back; tap **Confirm**. On the PC: the waypoint appears in JourneyMap in Lab (or the craft starts), and the approval is gone from monitor 2's rail too. (The core records it as confirmed via `phone`, D220; the phone e2e test checks that.)
3. **Status updates live.** Keep Now open on the phone. On the PC, quit Lab: within about 30 s the phone's header shows "Game not running" without a reload. Start Lab again: it goes away.
4. **Outside the tailnet it is unreachable.** Turn Tailscale **off** on the phone and reload: the page doesn't load at all (the name doesn't resolve, or times out). From any device not in your tailnet, the same. `& $gos tailnet check` still says `Funnel is off`.

Write in the PR (or tell Claude) what each step showed. Then V28 and L8 are resolved.

## Troubleshooting

- The phone gets `{"error": "host not allowed"}`: the name isn't in `[core] allowed_hosts` (B.2), or you restarted before saving it.
- `{"error": "not paired: pair this device first"}` from a bookmark of `/api/...`: open `/` instead and pair.
- `{"error": "funnel request"}`: something turned Funnel on. Run `& $gos tailnet check`, then the `tailscale funnel ... off` command it names.
- The page loads on Wi-Fi but not cellular: Tailscale on the phone is off or "on demand" excludes cellular (Tailscale app → Settings).
