# Dashboard: install, first pairing, monitor 2 and the PC checks (WO-2.6)

Clayton runs these on the gaming PC at the deployment test (D158). They take about 30 minutes, plus the theme review in part D. Decisions: D159 (serving, deploy and development), D160 (the card catalog), D161 (glance and workbench), D162 (themes and fonts), D163 (the Chrome app window).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), as in [core_service.md](core_service.md).

## A. Once: Node.js and pnpm

`gos deploy` builds the dashboard, so the PC that deploys needs Node.js and pnpm.

1. In a normal (not elevated) PowerShell window:
   ```powershell
   winget install OpenJS.NodeJS.LTS
   ```
   Then, in a new normal window (so `npm` is on PATH):
   ```powershell
   npm install -g pnpm@10.28.0
   pnpm --version
   ```
   Expect `10.28.0`. Any Node.js LTS from 22 on works.

What deploy does with them: `pnpm install --frozen-lockfile` and `pnpm run build` in the staged release, then it keeps only `dashboard\dist\` and deletes `node_modules` and the build's caches. pnpm's package store stays in your profile. Without pnpm on PATH, or if the build fails, deploy stops before it swaps anything, and `GOS_APP` keeps the release it had.

## B. Deploy, open the window, pair it

2. In the profile clone, in a normal window: `git pull`, then `uv run gos deploy`. Expect `Building the dashboard with pnpm ...` (the first deploy downloads packages: a minute or two), then `Built the dashboard: C:\GregtecOS-app\current\dashboard\dist`.
3. The supervisor opens the dashboard when the core first answers after it starts (a login start, `gos serve`, or the restart at the end of a deploy), unless the window is already open. It is a Chrome app window on `http://gos.localhost:8765/`, in its own Chrome profile (`%USERPROFILE%\GregtecOS\browser\dashboard`), on monitor 2, filling its work area. Press F11 for full screen.
   - If it didn't open: `gos dashboard` opens it now. `gos dashboard --print` shows the command without running it.
   - Chrome must be installed where its installer puts it (`Program Files` or `%LOCALAPPDATA%`). `[startup] dashboard_window = false` in `gos.toml` stops the automatic window.
4. **Pair it** (once per Chrome profile, D117). The window shows "Pair this device". Leave the name as `Monitor 2` and click **Ask to pair**. In a normal PowerShell window run `gos pair`; it prints `Type the code on the device that asked:` and the 8-digit code. Type the code into the window and click **Pair**. The Now view appears. From now on, a device that asks to pair (your phone, WO-9.3) shows its code on this window's right rail under "Waiting for you", with **Deny**.

## C. Acceptance checks (§8 WO-2.6)

5. **Usable on monitor 2 at 2560×1440.** The workbench fills the screen without scroll bars: the header (wordmark, search, state chips, tier, clock), the nav on the left with number keys, the view, the right rail (Agent, Waiting for you, Jobs) and the voice strip at the bottom with the core art. Press `1` to `9`: each switches the view (Designer, Power, Quests and Crops say which work order builds them). Press `Ctrl+K`, type `steel` and pick **Recipes for Steel Ingot**: Search opens on its recipe card, with an `r:` chip on each recipe. Click a chip: its recipe card opens full size.
6. **Ask something.** In the voice strip's box type `recipe for steel` and press Enter. The answer and its recipe card come first on Now ("Right after your question"), and Chat (`2`) keeps it; **Open full size** re-opens any past card.
7. **Glance mode is clickable (D49).** Start Play and click into the game. Within about two seconds the dashboard switches to glance: large type, no nav and no rail, the latest answer, alerts, net power and the next step, and the core art. Then:
   - Open the inventory (the cursor is free) and move the mouse onto monitor 2: within half a second it is the workbench again.
   - Move back, click into the game: glance again after about 1.5 s.
   - In glance, click **open ›** on the latest answer: Chat opens, in the workbench.
   - If it never switches to glance, open Settings + Health (`9`): the Services panel says `focus signal: GTNH in front` while the game has focus. `not available here` means the core can't see the foreground window (it runs elevated or in another session): note it in the deployment checklist.
8. **No frame drops (plan 7.14 rule 6).** While in glance, nothing moves unless the voice is busy, and then at most 30 frames a second. Play for a few minutes with the dashboard open; L7 (WO-11.x) measures FPS properly.
9. **Settings + Health.** `9`: the health card lists the supervisor, the core, the database, the key and the credits; Data shows the last export and the recipe DB; the VERIFY list shows doctor's statuses. **Re-run doctor** prints doctor's output. Paired devices lists `Monitor 2 · this device`.
10. **Costs.** `8`: this cycle's spend against the $100 of included credits, by lane and by model (D118).

## D. Themes (D81): review on the real screen

The look follows your tier (D64): Brass and Steam until LV is detected. Settings → **Look** pins any age you have reached, picks a comfort mode (Calm Ops, Field Manual) or pauses motion. Ages past your tier stay hidden until **Show later ages**.

11. Day one needs ages 1 to 3 and both comfort modes (age_themes.md). Review each with the tier override, which the theme follows:
    ```powershell
    gos quests tier --set LV     # Machine GUI; the new theme fades in once (1.5 s)
    gos quests tier --set MV     # Brushed Aluminium
    gos quests tier --set HV     # Command HUD, and so on up to UHV (Deep Space)
    gos quests tier --clear      # back to the tier your quests show
    ```
    For each, check that nothing on the screen moves when the theme changes, that every text is easy to read, that approvals (magenta "Waiting for you") never look like a status, and that the tier chips show GT's colours with the tier name. Then click **Calm Ops** and **Field Manual** in Settings → Look. Note anything you would change in the deployment checklist.
12. **Needs your board approval (D92, D158 (5)):** Chat, the Ctrl+K palette and Costs had no board on the canvas; they are designed to the design system. Approve them, or say what to change, at the deployment test.

## E. Development (gos-dev only)

- `pnpm -C dashboard install` once, then `pnpm -C dashboard build` (the core serves `dashboard\dist` at `/`), `pnpm -C dashboard test` (vitest) and `pnpm -C dashboard e2e` (Playwright, against its own core on `gos-e2e.localhost:8795` with a fake fast lane; screenshots go to `dashboard\test-results\`, which git ignores).
- `pnpm -C dashboard dev` serves the sources on `localhost:5173` and proxies `/api` and `/ws` to gos-dev's own core (`GOS_CORE_URL`, default `http://127.0.0.1:8775`), presenting itself as `GOS_DASHBOARD_HOST` (default `gos-dev.localhost`). Never point it at Clayton's core and never serve anything on `gos.localhost` (D115, D117).
- The card types come from `core/gregtec/cards/schema.json`: after changing it run `uv run python -m gregtec.cards.typescript --write`; a unit test fails while `dashboard/src/cards/types.gen.ts` is out of date.

## Turning it off

- The window: close it; `gos dashboard` opens it again.
- At startup: `[startup] dashboard_window = false` in `gos.toml`.
- Unpair a device: Settings + Health → Paired devices → **Revoke**, or `gos devices revoke <id>`.
