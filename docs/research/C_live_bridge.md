# C — Live bridge: GTNH game state → local program (and back)

Scope: live telemetry (power, machine status and maintenance, AE2 stock and crafting CPUs, player position and looked-at block) and in-game actions (AE2 craft requests, in-game messages). Researched 2026-11-01 against the GTNH sources on GitHub, at the exact versions shipped in the packs.

Legend: **CONFIRMED [Sx]** = verified in the cited source (URLs in the table at the end). **UNCERTAIN** = not verified, with the reason given.

---

## 0. Version context (read this first)

| Item | GTNH 2.8.4 | GTNH 2.9.0 |
|---|---|---|
| Release | 2025-12-23 manifest | **Stable, 2026-10-31** |
| OpenComputers | 1.11.20-GTNH | 1.12.68-GTNH |
| GT5-Unofficial | 5.09.51.482 | 5.09.54.265 |
| AE2-Unofficial | rv3-beta-695-GTNH | rv3-beta-1095-GTNH |
| Computronics | 1.9.3-GTNH | 1.9.13-GTNH |
| OCGlasses | 1.6.1-GTNH | 1.6.9-GTNH |
| Hodgepodge / ServerUtilities | 2.6.112 / 2.2.2 | 2.7.221 / 2.4.22 |
| JourneyMap (client, 3rd-party) | 5.2.10-fairplay | 5.2.23-fairplay |

- GTNH **2.9.0 is now the stable release**, dated 2026/10/31, with Java 17-26 and Java 8 builds. CONFIRMED [S1]. The player's "2.8.x or 2.9 beta" may be out of date, so ask.
- Mod versions in the table: CONFIRMED [S2][S3].
- Prism builds are named `…_Java_8.zip` and `…_Java_17-26.zip`. Game files sit under the instance's `.minecraft/` folder. CONFIRMED [S5][S60].
- The OpenComputers (OC), Computronics, GT and AE2 APIs **differ between 2.8.4 and 2.9.0** (see §2). Any Lua code and parser has to know which version it is running on.

---

## 1. OpenComputers internet card in GTNH

### 1.1 Availability
- The internet card exists and is enabled. GTNH config, 2.8.4 and 2.9.0: `enableHttp=true`, `enableHttpHeaders=true`, `enableTcp=true`. CONFIRMED [S6][S7].
- GTNH turns off OC's built-in recipe (`internetCard = true` in `gtnh.recipes`, which keeps the item visible in NEI). The real recipe comes from NewHorizonsCoreMod. CONFIRMED [S8][S61] (in `Recipes.scala`, a boolean `true` means no recipe but kept in NEI).

### 1.2 Where in progression
- **A basic computer is MV tier.** These recipes all run at MV recipe power (`TierEU.RECIPE_MV` or `RECIPE_MV/2`, which is above LV's 32 V):
  - Circuit Assembler: CPU T1, RAM T1, GPU T1, EEPROM.
  - Assembler: Computer Case T1, HDD T1, Screen T1, Keyboard, Disk Drive, **Adapter**, Cable.
  - CONFIRMED [S16][S17].
- **The internet card is HV tier, and needs an EV-tier circuit.**
  - Recipe: Circuit Assembler at `TierEU.RECIPE_HV`, 20 s, solder. Inputs: Wireless Network Card T2 (OC item 13), Interweb (OC item 48), Microchip T3 (OC item 26), an obsidian plate and a ProjectRed light button. The source comments label each item. CONFIRMED for 2.9.0 [S16] and 2.8.4, which has the same recipe [S18].
  - Microchip T3 itself needs an **EV circuit** plus an advanced fiberglass board, at HV recipe power. CONFIRMED [S16].
  - WLAN Card T2 needs Microchip T3 plus an HV emitter and HV sensor, at `RECIPE_HV/2`. CONFIRMED [S16].
  - "Roughly mid/late HV" is UNCERTAIN: it depends on which EV circuit line the player can make. Check NEI in-game.
- The **MFU** (remote adapter) is an HV Assembler recipe. CONFIRMED [S17].

### 1.3 Filtering: localhost is blocked by default
- **How rules work:** each target address is checked against the rules in order, and the first match decides. If nothing matches, the request is denied. CONFIRMED [S10][S11].
  - OC's own default is `["removeme","deny private","deny bogon","allow default"]`.
  - `private` matches any-local, loopback, link-local and site-local addresses. `bogon` includes 127.0.0.0/8.
  - The unit tests check that `http://127.0.0.1` is blocked. CONFIRMED [S14].
- **GTNH's shipped config** (identical in 2.8.4 and 2.9.0). CONFIRMED [S6][S7]:
  ```
  filteringRules=[ "deny ip:127.0.0.0/8", "deny ip:10.0.0.0/8", "deny ip:192.168.0.0/16",
                   "deny ip:172.16.0.0/12", removeme, "deny private", "deny bogon", "allow default" ]
  ```
  So localhost and all private ranges are blocked.
- **Exact key:** `opencomputers { internet { filteringRules=[…] } }` in `config/OpenComputers.cfg`, which is HOCON. OC reads it as `internet.filteringRules`. CONFIRMED [S13].
- **Common pitfall:** the old `blacklist` and `whitelist` keys are still in the file but marked "No longer used! See internet.filteringRules". OC migrates them into `filteringRules`. Advice to "empty the blacklist" therefore does nothing on current GTNH. CONFIRMED [S6][S13]. The README of the community dashboard gtnh-craft-monitor still gives this outdated advice [S46].
- **How to allow 127.0.0.1:** put an allow rule **first**:
  ```
  filteringRules=[
    "allow ip:127.0.0.1",
    "deny ip:127.0.0.0/8", … (rest unchanged)
  ]
  ```
  - Works on both OC versions. In 1.11.20, `ip:` rules also required the address to be local, which loopback satisfies; 1.12.x removed that extra check. CONFIRMED [S15].
  - A malformed rule makes `internetAccessAllowed()` return false, which **disables the card entirely**. CONFIRMED [S13][S12].
  - Restart the game after editing. UNCERTAIN: the settings are read when the game starts, but I saw no runtime reload.
- Use the literal `127.0.0.1` in Lua URLs, not `localhost`. UNCERTAIN, a precaution: `localhost` may resolve to `::1`, which the IPv4 allow rule would not cover.
- **Where requests come from:** the card is a server-side component. Requests run on the server's thread pool (`RequestSender`), using the server's proxy. CONFIRMED [S12].
  - In single-player, the integrated server runs in the game process, so 127.0.0.1 is the player's PC.
  - On a dedicated server, 127.0.0.1 is the server host.
- **Security:** the config comment says private addresses are blocked so that players on a hosted game can't reach the host's local network. CONFIRMED [S10]. Allowing 127.0.0.1 opens the local service to every OC computer in that world. In single-player this is acceptable; use a token anyway.

### 1.4 HTTP, TCP and limits (OC 1.12.68 and 1.11.20; `InternetCard.scala` is unchanged between them [S15])
- **HTTP:** `internet.request(url[, postData[, headers[, method]]])`. CONFIRMED [S12][S19].
  - Only `http`/`https`.
  - POST when `postData` is given. Custom `method` (PUT, DELETE…) and headers are allowed.
  - `handle.response()` → code, message, headers. `handle.read(n)` returns at most `maxReadBuffer=2048` bytes per call (GTNH value [S6]).
  - The body of a non-2xx response cannot be read (there is a TODO in the code).
  - OpenOS `internet.request` accepts a string or a table; a table is form-encoded.
- **TCP:** `internet.connect(addr[, port])` returns a socket with `finishConnect`, `read`, `write`, `close` and `id`.
  - OC pushes an `internet_ready` signal when data arrives, so the Python side can push commands. CONFIRMED [S12].
- **No listening sockets.** OC can only dial out, so the Python program must be the server. CONFIRMED: only `request` and `connect` exist [S12][S39 internet dump].
- **Limits.** CONFIRMED [S12][S6][S10][S21]:
  - There is no request-rate limiter.
  - `maxTcpConnections=4` concurrent connections per card. HTTP requests count toward this limit too.
  - `threads=4`.
  - `requestTimeout=0`, meaning requests never time out. Set it to something like 10 seconds.
  - `request`, `connect`, `read` and `write` are **non-direct calls**. They run on the server thread during the computer's tick, so at most about one per tick (~20/s) per computer.
- **Other GTNH OC settings:**
  - `maxSignalQueueSize=1024`; raised in OC 1.12.64-GTNH per the 2.9.0 changelog [S4].
  - CPU component limits `[8,12,16]` (T1/T2/T3). Every adapter-exposed machine counts toward this limit.
  - Lua 5.2 is the default (`defaultLua53=false`); Lua 5.3 is enabled (`enableLua53=true`).
  - CONFIRMED [S6].
  - Lua 5.3 uses 64-bit integers, which keeps EU values exact. CONFIRMED [S22].
- **JSON:** OpenOS ships no JSON library; there is `serialization.lua` for Lua tables and a GTNH-added `nbt.lua`. CONFIRMED [S20]. Bundle a pure-Lua JSON encoder, or parse Lua-table output in Python.

---

## 2. GT5-Unofficial ↔ OC, and AE2 ↔ OC

### 2.1 Who provides the GT components
- **`gt_machine`, `gt_batterybuffer` and the digital-chest components come from Computronics (GTNH fork), not from GT5U or OC.** CONFIRMED [S23][S24][S25].
  - Drivers: `DriverBaseMetaTileEntity`, `DriverMachine`, `DriverDeviceInformation`, `DriverBatteryBuffer`, `DriverDigitalChest`.
  - Enabled in GTNH by `computronics.cfg`: `enableGregTechMachines=true`, `GregtechMachineCoordinates=true` [S9].
  - GT5U contains no GT machine drivers; its only OC code is the bartworks data-server block. CONFIRMED [S62] (searched the GT5U source tree for OC API imports).
- **OC (GTNH fork) adds:**
  - `gt_energyContainer` (any `IBasicEnergyContainer`). CONFIRMED [S27].
  - **2.9.0 only:** `LSC`, the BEC drivers and `DriverMEInputHatch`. CONFIRMED [S29]. OC 1.11.20 (2.8.4) has only the energy-container driver.
- **Naming:** when one block matches several drivers, OC merges them into one component. It takes the name of the highest-priority driver, and the methods are combined. CONFIRMED [S30].
  - Computronics `DriverMachine` has priority 1, OC `LSC` 0 and `gt_energyContainer` -1 [S24][S28][S27].
  - So an LSC will probably show up as `gt_machine` with the LSC methods merged in. UNCERTAIN: check in-game with `component.list()` and `component.methods(addr)`.

### 2.2 `gt_machine` methods (2.9 in-game dump [S39] matches the source)
- **`direct=true` (cheap)** [S23][S24]:
  - Energy and steam: `getEUStored`, `getEUMaxStored`, `getEUInputAverage`, `getEUOutputAverage`, `getSteamStored`, `getSteamMaxStored`.
  - Status: `getOwnerName`, `hasWork`, `getWorkProgress`, `getWorkMaxProgress`, `isWorkAllowed`, `isMachineActive`, `getCoordinates`, `getName`.
  - Control: `setWorkAllowed(bool)`. In 1.9.13 it is queued to the next server tick.
- **`getSensorInformation()` is `direct=false`**, so each call costs one tick. CONFIRMED [S25][S26].
- **Merged in from OC `gt_energyContainer`** [S27]:
  - `getStoredEU`, `getStoredEUString`, `getEUCapacity`, `getEUCapacityString`.
  - `getInputVoltage`, `getOutputVoltage`, `getOutputAmperage`.
  - `getAverageElectricInput/Output`, `getStoredSteam`, `getSteamCapacity`.
- **`gt_batterybuffer`** adds `getBatteryCharge(slot)` and `getMaxBatteryCharge(slot)`. CONFIRMED [S39].
- **Addon machines:** the drivers match any `BaseMetaTileEntity` / `IMachineProgress` / `IGregTechDeviceInformation`, so addon GT multiblocks (Twist Space Technology, GT-Not-Leisure, etc.) should expose `gt_machine`. The class matching is CONFIRMED [S23-S25]. How each addon formats its sensor text is UNCERTAIN (not inspected).
- **Hook-up:** an Adapter must touch the machine's **controller**. Casings have no tile entity, and the driver needs one. Alternatively, put an MFU bound to the controller into the adapter (range `mfuRange=16` [S6][S56]). The driver semantics are CONFIRMED [S23]. The OC docs describe the MFU as a remote adapter [S56].

### 2.3 Lapotronic Supercapacitor (LSC)
- **2.9.0:** OC's `LSC` driver has:
  - `getStoredEU()` (clamped to Long.MAX) and `getStoredEUString()` (exact BigInteger).
  - `getEUCapacity()` and `getEUCapacityString()`.
  - CONFIRMED [S28].
- **2.8.4:** no LSC driver. Use `gt_machine` and `getSensorInformation()`. CONFIRMED [S29].
- **`getSensorInformation()` lines for the LSC:** operational header, EU stored (in full and in standard form), used %, total capacity, passive loss, EU in/out last tick, average EU in/out (seconds window, 5 min, 1 h), time to full/empty, maintenance status, wireless mode, UHV/UEV/UIV/UMV capacitor counts, and **wireless-network EU**. CONFIRMED [S31].
- **Format changed between versions:**
  - 2.8.4 returns localized strings, e.g. `EU Stored: 1,234 EU`. CONFIRMED [S32].
  - 2.9.0 returns encoded lines `translationKey\\arg1\\arg2…`, e.g. `kekztech.infodata.lapotronic_super_capacitor.eu_stored\\1,234,567`. CONFIRMED [S31][S33].
  - The community parser splits each line on backslashes [S46 `power_monitor.lua`].
  - The English templates are in GT5U's lang files [S36].
- **Huge values:** use the `*String` methods, or a Lua 5.3 CPU.

### 2.4 Maintenance detection
- **Multiblocks:** `getSensorInformation()` includes a "Problems" line. CONFIRMED [S34][S35][S36].
  - 2.9.0: key `GT5U.multiblock.scanner.problems` → `Problems: §c<n>`.
  - 2.8.4: `GT5U.multiblock.problems` + `": "` + count.
  - Lines carry `§` colour codes; strip them.
- **LSC:** `kekztech.infodata.multi.maintenance_status.ok` or `.bad`. CONFIRMED [S31][S36].
- **Structured data:** GT5U has `getInfoMap()` with keys `progressTime`, `maxProgressTime`, `energy`, `maxEnergy`, `energyUsage`, `maxEnergyUsage`, `minEnergyTier`, **`maintenanceIssues`**, `energyEfficiency` and `pollution`. CONFIRMED [S34].
  - **No OC or Computronics driver exposes it.** I searched the OC 1.12.68 and Computronics 1.9.13 sources. Only a Java mod could read it.
- **Machines that override `getInfoData()`** may leave out the Problems line. UNCERTAIN (not audited).
- **Redstone fallback:** GT has a "Needs Maintenance" cover, which an OC redstone I/O could read. The class exists [S37]. Its exact behaviour is UNCERTAIN (not read).
- **Known regression:** issue #25071 reported `gt_machine` disappearing on 2.9 dailies. A maintainer says Computronics PR #40 fixed it. CONFIRMED that the report and reply exist [S40]. Status in 2.9.0 stable is UNCERTAIN.

### 2.5 AE2 (drivers live in the GTNH OC fork; AE2-Unofficial has no OC module)
- **Where the drivers are:** `li.cil.oc.integration.appeng` in OC. CONFIRMED [S41][S45].
- **Which blocks expose the network:** network-wide methods (`NetworkControl`) come from:
  - **`me_controller`**: an Adapter next to the ME Controller.
  - **`me_interface`**: an Adapter next to an ME Interface **block**.
  - The cable-part interface (also named `me_interface`) does **not** have `NetworkControl` in 1.12.68.
  - CONFIRMED [S44].
- **2.9.0 methods** [S41][S39]:
  - Crafting: `getCpus`, `getCraftable(detail,type)`, `getCraftables([filter])`.
  - Items: `getItemsInNetwork([filter])`, `getItemInNetwork(name|id[,damage[,nbt]] | detail)`, `getItemsInNetworkById(idArray)`, `allItems()`, `store(...)`.
  - Fluids: `getFluidInNetwork`, `getFluidsInNetwork`.
  - Power: `getAvgPowerInjection`, `getAvgPowerUsage`, `getIdlePowerUsage`, `getMaxStoredPower`, `getStoredPower`.
  - Change events: `setItemEventSubscription` / `setFluidEventSubscription`, which push `network_item_changed` / `network_fluid_changed` signals.
  - With Thaumic Energistics also `getEssentiaInNetwork` and essentia events.
- **2.8.4** has a subset: no `getCraftable`, no `getFluidInNetwork`, **no event subscriptions**. The craftable's `getItemStack()` was **renamed to `getStack()`** in 2.9.0. CONFIRMED [S42][S41].
- **CPUs:** `getCpus()` → `{name, storage, coprocessors, busy, cpu}`. The `cpu` object has `isBusy`, `isActive`, `activeItems`, `pendingItems`, `storedItems`, `finalOutput` and `cancel`. CONFIRMED [S41][S42].
- **Crafting (action):** `craftable.request([amount[, prioritizePower=true[, cpuName]]])` returns a status object with `isComputing`, `hasFailed`, `isCanceled` and `isDone`. CONFIRMED [S41]. The amount is a Long in 2.9.0 and an Int in 2.8.4 [S42].
- **Cost:** all these calls are non-direct, so one per tick, run on the server thread. CONFIRMED [S41][S21].
- **Event subscriptions** send one signal per change batch [S43]. On a busy network they may flood the 1024-signal queue. UNCERTAIN: this is inferred, not measured.
- **Field experience** (community project, not official docs) [S46]:
  - `getItemsInNetwork()` ran a computer with 2×T3.5 RAM out of memory at about 6,000 item types.
  - `allItems()` returned as little as 22% of items when the network changed mid-scan.
  - What works: `getItemsInNetworkById` in batches capped at about 500 variants per call.

---

## 3. Alternatives and complements

### 3.1 Things GTNH already ships
- **Computronics Chat Box.** CONFIRMED [S47][S9].
  - `say(text[, distance])` sends grey italic `[ChatBox] text` (or the name set with `setName`) to players in range.
  - Default and maximum range is 40 blocks (`maxDistance=40`, `makeMagical=false`). Creative chat boxes are enabled and unlimited.
  - Emits a `chat_message(user, msg)` signal when players chat nearby, so it is also an input channel.
  - Its recipe tier is UNCERTAIN: it comes from Computronics' own GT-style recipes, not checked.
- **OCGlasses** (GTNH fork of OpenGlasses). Terminal and glasses are HV Assembler recipes [S17].
  - **HUD widgets** (`addTextLabel`, `addRect`…) are direct calls: an on-screen text overlay. CONFIRMED [S48][S39].
  - **2.9.0 only (1.6.9):** CONFIRMED [S48]; absent in 1.6.1 [S48b].
    - `sendMessageTo(player, msg)` sends a private chat line.
    - `sendChatAs(player, msg)`: commands are limited to `/msg`, `/w` and `/tell` by config.
    - Both need Computronics plus a **ChatBox upgrade on the glasses**.
    - Signal `chat_message` for linked players.
  - **Looked-at block on demand:** pressing the overlay key while looking at a block within 5 blocks sends `block_interact(user, x, y, z, side)`. CONFIRMED [S48c].
- **OpenSecurity entity detector:** `scanPlayers([range])` returns players' x/y/z (absolute or relative), within range of the block only. CONFIRMED [S49]. The default range is UNCERTAIN.
- **OC `tps_card`:** server tick time per dimension (GTNH addition). CONFIRMED [S58][S39].
- **OC debug card** (creative-only): `runCommand`, `getPlayer`. GTNH sets `debugCardAccess=allow` [S6]. Using it means giving yourself a creative item; not recommended.
- **ServerUtilities "Aurora"** web server. CONFIRMED [S50]:
  - Disabled by default; port 48574.
  - Binds on all interfaces (`bootstrap.bind(port)`).
  - JSON pages: world info (time, weather) and player list (name, UUID — **no positions**).
  - Another mod can add pages through `AuroraPageEvent`.
  - Whether it starts on a single-player integrated server is UNCERTAIN.
  - Low value for this project.
- **JourneyMap 5.2.x-fairplay web map** (client) as a source of player position: UNCERTAIN. JourneyMap is closed-source and third-party; I could not verify that the 1.7.10 5.2 builds still serve a web map or JSON.

### 3.2 Not shipped by GTNH
- `cpburnz/minecraft-prometheus-exporter` has a 1.7.10 Forge build. It exports server, tick, dimension, entity and player metrics; no positions or machines. CONFIRMED [S54]. Compatibility with GTNH is UNCERTAIN.
- DebugBridge (a localhost WebSocket game-state mod) has no 1.7.10 build, so it is only a design reference. CONFIRMED [S55].
- **Existing GTNH dashboard:** `Maselkov/gtnh-craft-monitor`, very active (commit 2026-11-01). OC scripts POST AE2, crafting and power data to a Flask server on 127.0.0.1:8421. CONFIRMED [S46]. Good reference, or a possible base to reuse.

### 3.3 Custom Forge 1.7.10 bridge mod
- **Template:** `GTNewHorizons/ExampleMod1.7.10` (use its `starter.zip`). CONFIRMED [S52][S53]:
  - Forge 1.7.10-10.13.4.1614.
  - GTNHGradle convention plugin (settings plugin 2.0.33), which applies **RetroFuturaGradle**.
  - Gradle 9.7.1, with the Gradle daemon toolchain pinned to **Java 25** (auto-provisioned).
  - `enableModernJavaSyntax = jabel` compiles to Java-8 bytecode, so one jar runs on both GTNH Java 8 and Java 17-26.
- **What it could expose (design):**
  - Client side: player position, dimension and looked-at block (`objectMouseOver`). It can print chat lines locally or send chat.
  - In single-player, it can also reach the integrated server and read `getInfoMap()` and AE2 grids on the server thread, the same way OC's `NetworkControl` does.
  - UNCERTAIN: these are standard Forge/MC APIs, but untested here.
- **Transport:** Netty is available, and Aurora uses its `HttpServerCodec`. CONFIRMED [S50]. Whether the JDK `HttpServer` is present on every GTNH Java runtime is UNCERTAIN.
- **Effort** (UNCERTAIN, my estimate):
  - A client-only MVP (localhost WebSocket/HTTP with position, look and chat) is a few days for a Python developer new to Forge.
  - Single-player server-side GT and AE2 reads plus crafting: another 1-3 weeks.
  - Re-test on every GTNH update.
- **Quest and addon safety:** a mod that registers **no items, blocks or recipes** and uses **no mixins** leaves item registries and BetterQuesting data untouched. UNCERTAIN: this is reasoning, not a test.

### 3.4 Reading chat from the log (no in-game cost)
- The 1.7.10 client logs every chat line through log4j in `GuiNewChat.printChatMessageWithOptionalDeletion`. GTNH's Hodgepodge (`cleanChatLogs`, default **true**) strips colour codes from those lines. CONFIRMED [S51].
- The exact prefix (`[CHAT] `), and whether `latest.log` or `fml-client-latest.log` is the better file, are UNCERTAIN. Check one line in `<instance>/.minecraft/logs/`.

### 3.5 Fallback without an internet card (MV tier)
- OC hard drives are real folders at `<world save>/opencomputers/<fs-address>/`. CONFIRMED [S57].
- GTNH sets `filesystem.bufferChanges=false` (2.8.4 and 2.9.0), so writes reach disk immediately. CONFIRMED [S6][S7].
- So an MV OC computer can write a JSON file that Python watches.
- Two parts are UNCERTAIN (untested): whether a reader can catch a half-written file (write a temp file, then rename), and whether files Python writes back are seen by OC.

---

## 4. Risks

- **Pausing stops everything in single-player.** CONFIRMED [S21].
  - OC computers enter `Paused` whenever `Minecraft.isGamePaused` is true on an integrated server.
  - Alt-tabbing pauses by default unless pause-on-lost-focus is off (F3+P / `pauseOnLostFocus:false`). The article is general Java Edition advice [S59]; 1.7.10 behaviour is UNCERTAIN, so test.
  - "Open to LAN" may avoid pausing. UNCERTAIN.
- **Chunk loading:** base telemetry stops when the base chunks unload, e.g. on a space trip, unless chunk-loaded. UNCERTAIN: general OC behaviour, not re-verified.
- **Server-thread cost and memory:**
  - Non-direct calls run on the server thread; large AE2 queries can drop TPS. CONFIRMED [S21][S41].
  - Memory exhaustion on big networks is a field report [S46].
  - Component limits per CPU tier (8/12/16) [S6].
  - Mitigations: poll slowly, use watchlists, batches and Lua 5.3; use servers with component buses or several computers.
- **Version drift (2.8.4 vs 2.9.0):** sensor-text format, LSC driver, AE2 events and `getStack` vs `getItemStack`. CONFIRMED (§2). Detect the version at startup.
- **Pack updates may overwrite `config/OpenComputers.cfg`**, removing the 127.0.0.1 rule. UNCERTAIN: depends on how the player updates. Re-check after each update.
- **Single-player vs server:**
  - On a dedicated server, the OC config is server-side and 127.0.0.1 is the server, so the assistant would need a reachable LAN IP and firewall rules.
  - A client-only mod sees only client-synced state.
  - CONFIRMED for OC [S12]; the client-mod limitation is general MC architecture (UNCERTAIN wording).
- **Quests and addons:**
  - Config edits, building OC hardware and AE2 crafts started from OC do not touch quest files.
  - A custom mod is safe if it adds no items or mixins.
  - The debug card is a creative item.
  - UNCERTAIN: these are judgements, not tests.
- **Security:** the 127.0.0.1 allowance applies world-wide. Have the Python app bind only to 127.0.0.1 and require a token header. The config rationale is CONFIRMED [S10]; the mitigation is a recommendation.

---

## 5. Recommended phased approach

- **Phase 0 — no game changes.**
  - Python tails the client log for chat lines (§3.4). This gives a typed-command input and a way to confirm actions.
  - Optional MV-tier file-drop prototype (§3.5).
- **Phase 1 — MVP telemetry (needs HV for the internet card).**
  1. Add `"allow ip:127.0.0.1"` as the first `filteringRules` entry. Set `requestTimeout` to about 10. Restart.
  2. Build one OC computer (T2/T3 CPU set to Lua 5.3) with an internet card. Add adapters, or MFUs, on the LSC or battery buffers, 3-10 key multiblock controllers, and the ME Controller or an ME Interface block.
  3. The Lua agent loop:
     - Every 5-10 s: power (`getStoredEUString` / LSC sensor lines), machine status (`isMachineActive`, `getWorkProgress`/`getWorkMaxProgress`, Problems count from `getSensorInformation`) and AE2 CPUs (`getCpus`).
     - Every 60 s: a watchlist of items via `getItemsInNetworkById`.
     - POST the JSON to the Python server on `http://127.0.0.1:<port>`.
     - The **response carries queued commands**: `craft` → `getCraftable`/`getCraftables(...)[1].request(n)`, then track the status object; `say` → `chat_box.say`.
  4. Python owns all parsing: strip `§` codes, split on backslashes, branch on the 2.8 vs 2.9 format.
- **Phase 2:**
  - Persistent TCP socket with `internet_ready` for push commands.
  - AE2 event subscriptions (2.9 only).
  - OCGlasses HUD text plus private `sendMessageTo`.
  - `chat_message` signals as a chat-command input.
- **Phase 3 (optional):** a custom client mod (ExampleMod template) for player position, dimension and looked-at block, plus local chat lines. Extend it to single-player server-side `getInfoMap()` and AE2 reads only if OC scanning hits its memory or TPS limits.

## 6. Questions only the player can answer
1. Which GTNH version (2.8.4 or the new 2.9.0 stable), and which Java build (8 or 17-26)?
2. Single-player, single-player opened to LAN, or a dedicated server (whose)?
3. Current tier: HV Circuit Assembler and an EV circuit available? Any OC computers, adapters or MFUs built yet?
4. Size of the AE2 network (number of item types), and which machines matter most (LSC, battery buffers, wireless EU)?
5. OK to edit `OpenComputers.cfg` and re-apply after updates? OK to install a self-built client mod later?
6. Preferred in-game channel: chat box (range-limited, public), glasses HUD/PM (HV, 2.9), or a client mod?
7. Does he alt-tab a lot (pause), and is the base chunk-loaded while he travels?

---

## Sources
| ID | URL |
|---|---|
| S1 | https://www.gtnewhorizons.com/version-history/ |
| S2 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/manifests/2.9.0.json |
| S3 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/manifests/2.8.4.json |
| S4 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/changelogs/changelog%20from%202.8.4%20to%202.9.0.md |
| S5 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/src/daxxl/assembler/platforms/prism.py |
| S60 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/src/daxxl/defs.py |
| S6 | https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/config/OpenComputers.cfg |
| S7 | https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.8.4/config/OpenComputers.cfg |
| S8 | https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/config/opencomputers/gtnh.recipes |
| S9 | https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/config/computronics.cfg |
| S10 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/resources/application.conf |
| S11 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/util/InternetFilteringRule.scala |
| S12 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/server/component/InternetCard.scala |
| S13 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/Settings.scala |
| S14 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/test/scala/InternetFilteringRuleTest.scala |
| S15 | https://github.com/GTNewHorizons/OpenComputers/compare/1.11.20-GTNH...1.12.68-GTNH |
| S16 | https://github.com/GTNewHorizons/NewHorizonsCoreMod/blob/2.9.99/src/main/java/com/dreammaster/gthandler/recipes/CircuitAssemblerRecipes.java |
| S17 | https://github.com/GTNewHorizons/NewHorizonsCoreMod/blob/2.9.99/src/main/java/com/dreammaster/gthandler/recipes/AssemblerRecipes.java |
| S18 | https://github.com/GTNewHorizons/NewHorizonsCoreMod/blob/2.7.268/src/main/java/com/dreammaster/gthandler/recipes/CircuitAssemblerRecipes.java |
| S19 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/resources/assets/opencomputers/loot/openos/lib/internet.lua |
| S20 | https://github.com/GTNewHorizons/OpenComputers/tree/1.12.68-GTNH/src/main/resources/assets/opencomputers/loot/openos/lib |
| S21 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/server/machine/Machine.scala |
| S22 | https://www.lua.org/manual/5.3/manual.html#2.1 |
| S23 | https://github.com/GTNewHorizons/Computronics/blob/1.9.13-GTNH/src/main/java/pl/asie/computronics/integration/gregtech/gregtech5/DriverBaseMetaTileEntity.java |
| S24 | https://github.com/GTNewHorizons/Computronics/blob/1.9.13-GTNH/src/main/java/pl/asie/computronics/integration/gregtech/gregtech5/DriverMachine.java |
| S25 | https://github.com/GTNewHorizons/Computronics/blob/1.9.13-GTNH/src/main/java/pl/asie/computronics/integration/gregtech/gregtech5/DriverDeviceInformation.java |
| S26 | https://github.com/GTNewHorizons/Computronics/blob/1.9.3-GTNH/src/main/java/pl/asie/computronics/integration/gregtech/gregtech5/DriverDeviceInformation.java |
| S27 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/integration/gregtech/DriverEnergyContainer.java |
| S28 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/integration/gregtech/DriverLSC.scala |
| S29 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/integration/gregtech/ModGregtech.scala and https://github.com/GTNewHorizons/OpenComputers/tree/1.11.20-GTNH/src/main/scala/li/cil/oc/integration/gregtech |
| S30 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/server/driver/CompoundBlockDriver.scala |
| S31 | https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.54.265/src/main/java/kekztech/common/tileentities/MTELapotronicSuperCapacitor.java |
| S32 | https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.51.482/src/main/java/kekztech/common/tileentities/MTELapotronicSuperCapacitor.java |
| S33 | https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.54.265/src/main/java/gregtech/api/interfaces/tileentity/IGregTechDeviceInformation.java |
| S34 | https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.54.265/src/main/java/gregtech/api/metatileentity/implementations/MTEMultiBlockBase.java |
| S35 | https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.51.482/src/main/java/gregtech/api/metatileentity/implementations/MTEMultiBlockBase.java |
| S36 | https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/resources/assets/gregtech/lang/en_US.lang and https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/resources/assets/kekztech/lang/en_US.lang |
| S37 | https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/common/covers/CoverNeedMaintainance.java |
| S39 | https://github.com/Navatusein/GTNH-OC-Lua-Documentation/tree/main/dumps/computer (in-game 2.9 dumps: gt_machine, gt_batterybuffer, me_controller, internet, chat_box, glasses, tps_card, debug, os_entdetector) |
| S40 | https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/issues/25071 |
| S41 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/integration/appeng/NetworkControl.scala |
| S42 | https://github.com/GTNewHorizons/OpenComputers/blob/1.11.20-GTNH/src/main/scala/li/cil/oc/integration/appeng/NetworkControl.scala |
| S43 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/integration/appeng/internal/SubscriptionBase.scala |
| S44 | https://github.com/GTNewHorizons/OpenComputers/tree/1.12.68-GTNH/src/main/scala/li/cil/oc/integration/appeng (DriverController, DriverBlockInterface, DriverPartInterface) |
| S45 | https://github.com/GTNewHorizons/Applied-Energistics-2-Unofficial/tree/master/src/main/java/appeng/integration/modules |
| S46 | https://github.com/Maselkov/gtnh-craft-monitor (README.md, docs/how-it-works.md, oc/power_monitor.lua) |
| S47 | https://github.com/GTNewHorizons/Computronics/blob/1.9.13-GTNH/src/main/java/pl/asie/computronics/tile/TileChatBox.java and .../util/ChatBoxUtils.java |
| S48 | https://github.com/GTNewHorizons/OCGlasses/blob/1.6.9-GTNH/src/main/java/com/bymarcin/openglasses/tileentity/OpenGlassesTerminalTileEntity.java |
| S48b | https://github.com/GTNewHorizons/OCGlasses/blob/1.6.1-GTNH/src/main/java/com/bymarcin/openglasses/tileentity/OpenGlassesTerminalTileEntity.java |
| S48c | https://github.com/GTNewHorizons/OCGlasses/blob/1.6.9-GTNH/src/main/java/com/bymarcin/openglasses/event/ClientKeyboardEvents.java and .../OpenGlasses.java (allowedCommands) |
| S49 | https://github.com/GTNewHorizons/OpenSecurity/blob/master/src/main/java/pcl/opensecurity/tileentity/TileEntityEntityDetector.java |
| S50 | https://github.com/GTNewHorizons/ServerUtilities/tree/2.4.22/src/main/java/serverutils/aurora (AuroraConfig, AuroraServer, AuroraPageEvent, mc/PlayerListJson, mc/WorldInfoJson) |
| S51 | https://github.com/GTNewHorizons/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/mixins/early/minecraft/MixinGuiNewChat_CleanChatLogs.java and .../config/TweaksConfig.java |
| S52 | https://github.com/GTNewHorizons/ExampleMod1.7.10 (README.md, gradle.properties, gradle/gradle-daemon-jvm.properties, settings.gradle.kts, gradle/wrapper/gradle-wrapper.properties, docs/FAQ.md) |
| S53 | https://github.com/GTNewHorizons/GTNHGradle |
| S54 | https://github.com/cpburnz/minecraft-prometheus-exporter |
| S55 | https://www.curseforge.com/minecraft/mc-mods/debugbridge |
| S56 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/resources/assets/opencomputers/doc/en_US/item/mfu.md |
| S57 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/server/fs/FileSystem.scala |
| S58 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/server/component/TpsCard.scala |
| S59 | https://9meters.com/entertainment/games/prevent-minecraft-from-auto-pausing-when-alt-tab-or-lose-focus |
| S61 | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/common/recipe/Recipes.scala |
| S62 | https://github.com/GTNewHorizons/GT5-Unofficial/tree/master/src/main/java/bwcrossmod/openComputers |
