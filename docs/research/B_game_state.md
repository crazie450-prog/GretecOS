# B: On-disk game-state sources for Gregtec OS (GTNH, MC 1.7.10)

Researched 2026-11-01. Scope: files a local Python program can read **without adding mods**. Most claims come from the source code at the exact versions GTNH ships, not from wiki pages.

Legend: **CONFIRMED** = checked in source code, config or docs (URL given). **UNCERTAIN** = inferred or unverified (reason given).

Version pins used (from the DreamAssemblerXXL release manifests, CONFIRMED: https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/manifests/2.9.0.json and `.../2.8.4.json`):

| Component | GTNH 2.8.4 (tag dated 2025-12-23) | GTNH 2.9.0 (tag dated 2026-10-31) |
|---|---|---|
| BetterQuesting | 3.7.15-GTNH | 3.8.92-GTNH |
| VisualProspecting | 1.4.8 | 1.5.42 |
| Extended IDs | NotEnoughIds 2.1.10 | **EndlessIDs 1.7.4** (NEID disabled) |
| Hodgepodge | 2.6.112 | 2.7.221 |
| ServerUtilities | 2.2.2 | 2.4.22 |
| JourneyMap | 5.2.10-fairplay | 5.2.23-fairplay |
| GT5-Unofficial | 5.09.51.482 | 5.09.54.265 |
| GuideNH | (not in list) | 1.3.55 |

- **2.9.0 is now the stable release.** The modpack README says "Version 2.9.0 is out 2026-10-31" (CONFIRMED: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/master/README.md). The player's "2.9 beta" has a stable successor.
- **Instance root on Windows (Prism):** `%APPDATA%\PrismLauncher\instances\<instance>\.minecraft\`
  - CONFIRMED for the data directory and the `instances` folder: https://prismlauncher.org/wiki/getting-started/data-location
  - CONFIRMED for the `.minecraft` subfolder: https://rubenerd.com/where-does-prism-store-the-minecraft-folder and https://gtnh.miraheze.org/wiki/Migrating
  - UNCERTAIN: portable installs or custom instance folders move this, and MultiMC-imported instances may use `minecraft` without the dot.
- Below, `MC\` means that `.minecraft` folder and `W\` means `MC\saves\<WorldFolder>\`.

---

## 1. BetterQuesting (quest book)

### 1.1 Where quest definitions live

- **Defaults shipped with the pack use the split format.** CONFIRMED at the 2.8.4, 2.9.0 and master tags: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/tree/2.9.0/config/betterquesting/DefaultQuests
  - `MC\config\betterquesting\DefaultQuests\QuestSettings.json`: global settings. The `format:8` value is `"3.1.0"`. It also holds `pack_name`, `pack_version`, `editMode`, `hardcore` and `party_enable`.
  - `...\DefaultQuests\QuestLinesOrder.txt`: one line per quest line, formatted `<base64 id>: <name>`. This is the tab order: "And So, It Begins", "Tips and Tricks", "Tier 0 - Stone Age" … "Tier 12 - UMV", then the side lines.
  - `...\DefaultQuests\Quests\<QuestLineName>-<base64 id>\<QuestName>-<base64 id>.json`: one file per quest. There are 3,814 in 2.9.0 and 3,739 in 2.8.4. The special folders `Quests\MultipleQuestLine\` and `Quests\NoQuestLine\` hold quests that belong to several lines or to none. CONFIRMED: `MULTI_QUEST_LINE_DIRECTORY` and `NO_QUEST_LINE_DIRECTORY` in https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/commands/admin/QuestCommandDefaults.java
  - `...\DefaultQuests\QuestLines\<QuestLineName>-<id>\QuestLine.json`: the line's name, description, icon and visibility.
  - `...\DefaultQuests\QuestLines\<QuestLineName>-<id>\<QuestName>-<id>.json`: one file per quest placed in that line, holding `questIDHigh/Low`, `x`, `y`, `sizeX` and `sizeY`.
  - BQ can still read a legacy single `DefaultQuests.json`. GTNH does not ship one. CONFIRMED: SaveLoadHandler `loadConfig()`, https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/handlers/SaveLoadHandler.java
- **The world keeps its own copy, and that copy is authoritative for the world:** `W\betterquesting\QuestDatabase.json`. It is a single file containing `questSettings`, `questDatabase`, `questLines`, `format` and `build`.
  - CONFIRMED (same SaveLoadHandler): if `QuestDatabase.json` exists, BQ loads the world copy. Otherwise it loads `DefaultQuests` and writes the world copy on the next save.
  - GTNH sets `"Load the default quest DB on world startup." = false` (CONFIRMED: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/config/betterquesting.cfg). Defaults are therefore re-imported only when `hasUpdate()` is true: same `pack_name` and a higher `pack_version` (CONFIRMED: `CommonProxy.serverStarting`, https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/core/proxies/CommonProxy.java).
  - The release workflow stamps `pack_version` as 2110 plus the number of quest commits since a base commit, so each release with quest changes re-imports automatically on world load. CONFIRMED: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/.github/workflows/release-tags.yml and https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/.github/scripts/stamp-qb-version.py
  - The copy in the git repo still says 2110. The stamped value exists only in release zips.
- **Recommendation:** for "what quests does *this* world have", read `W\betterquesting\QuestDatabase.json`. For per-quest retrieval and RAG chunking, the split `DefaultQuests` files are easier. Compare both against `pack_version` to detect drift.

### 1.2 JSON encoding (applies to every BQ file)

- Keys carry the NBT type id as a suffix: `"name:8"`, `"questIDLow:4"`, `"tasks:9"`.
  - The type ids are 1 byte, 2 short, 3 int, 4 long, 5 float, 6 double, 7 byte[], 8 string, 9 list, 10 compound, 11 int[].
  - Lists are written as objects keyed `"0:10"`, `"1:10"`, and so on.
  - To parse, split on the **last** colon.
  - CONFIRMED: https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/api/utils/NBTConverter.java and the sample files above.

### 1.3 Quest ID format

- Each ID is a UUID stored as two signed 64-bit longs: `questIDHigh:4` (most significant bits) and `questIDLow:4` (least significant). Quest lines use `questLineIDHigh/Low`. CONFIRMED: `NBTConverter.UuidValueType`.
- Legacy integer IDs map to `UUID(0, legacyId)`, so old quests read high=0, low=<old id>. CONFIRMED: https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/api/utils/UuidConverter.java
- The compact string form is the **URL-safe base64 of the 16 big-endian bytes**, with `==` padding in file names and padding stripped in lang keys. Examples: `AAAAAAAAAAAAAAAAAAABug==` is quest 442; `D_aFb1hFQH2PDkN0_KvAEQ==` is the Tier 10 line. CONFIRMED: `UuidConverter.encodeUuid` and `encodeUuidStripPadding` (via QuestTranslation).
- File-name prefixes are truncated, sanitised quest names such as `AlmostAutomatedE-...`. **Use the id, not the name.**

### 1.4 Quest text

- **English text sits in the quest JSON** as `properties:10 → betterquesting:10 → name:8 / desc:8`, with the same fields in `QuestLine.json`. CONFIRMED: sample files in the repo.
- **Lookup order:** BQ first tries the lang keys `betterquesting.quest.<b64-no-pad>.name` / `.desc` and `betterquesting.questline.<id>.name` / `.desc`. It falls back to the JSON property only if the key is missing. CONFIRMED: https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/api2/utils/QuestTranslation.java
- **Lang file locations:**
  - 2.9.0: `MC\config\txloader\forceload\betterquesting\lang\template.lang`, about 2.1 MB with 3,814 quest `.name` keys and 46 quest-line `.name` keys, each with a matching `.desc` (CONFIRMED by counting the file at tag 2.9.0). Newlines are written as `%n`.
  - 2.8.4: the same file was at `MC\config\txloader\load\betterquesting\lang\template.lang` (CONFIRMED by tree listing at tag 2.8.4).
  - The pack's own Readme says `template.lang` "by itself does nothing". Minecraft loads only `<locale>.lang`, so translations go in, for example, `zh_CN.lang` in the same folder (CONFIRMED: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/config/betterquesting/Readme.md).
  - For an `en_US` client the JSON text is what is displayed. `template.lang` is a convenient flat English dump: one key per line, good for search indexing.
  - `/bq_admin default exportlang` regenerates it from the *live* database into `config/betterquesting/DefaultQuests/template.lang`. Same Readme, CONFIRMED.
- **Markup to strip:** `§x` colour codes and the tags `[warn]…[/warn]`, `[note]…[/note]` and `[url]…[/url]`. CONFIRMED: same Readme and the sample quests.
- **Item references in tasks and rewards use registry names plus damage, not numeric IDs.** Example from the "Baked Potatoes" quest: `{"id:8":"gregtech:gt.metaitem.02","Damage:2":32201,"OreDict:8":""}`. CONFIRMED: sample quest. This lets quests join against NEI dumps (§3.6). GT machines in quests appear as `gregtech:gt.blockmachines` with `Damage` equal to the machine's `mID`. CONFIRMED: the 2.9.0 "Assembly Line" quest uses Damage 1170 (= `ASSEMBLING_LINE_CONTROLLER(1170)`) and 51 (= `INPUT_HATCH_LV(51)`) in https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.54.265/src/main/java/gregtech/api/enums/MetaTileEntityIDs.java. Quests therefore give a partial `mID`→name map for free.

### 1.5 Prerequisites, tasks and logic

- **Prerequisites:** `preRequisites:9`, a list of `{questIDHigh, questIDLow, optional type:1}`.
  - `type` is a display hint (`RequirementType`).
  - The combine rule is `properties…questLogic:8`, one of `AND | NAND | OR | NOR | XOR | XNOR`.
  - CONFIRMED: https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/questing/QuestInstance.java and `api/enums/EnumLogic.java`.
- **Tasks:** `tasks:9`. Each entry has a `taskID:8` and an `index:3`.
  - The `taskID:8` values are `bq_standard:retrieval`, `optional_retrieval`, `crafting`, `fluid`, `hunt`, `block_break`, `location`, `checkbox`, `xp`, `scoreboard`, `meeting`, `interact_*` and similar (CONFIRMED: class list in `src/main/java/bq_standard/tasks/`).
  - The combine rule is `taskLogic:8`. Retrieval tasks carry `requiredItems`, `consume`, `ignoreNBT` and `partialMatch`.
- **Other useful properties:**
  - `visibility:8` takes `HIDDEN`, `SECRET`, `UNLOCKED`, `NORMAL`, `COMPLETED`, `CHAIN` or `ALWAYS` (CONFIRMED: `api/enums/EnumQuestVisibility.java`).
  - Also useful: `isMain:1`, `repeatTime:3` (-1 means not repeatable), `globalShare:1` and `partySingleReward:1`.
- **"Unlocked" and "available" are not stored.** They must be computed: prerequisites evaluated under `questLogic` against the completion set. UNCERTAIN in the details: re-implement and spot-check against the in-game book.

### 1.6 Player progress

- **Single-player:** `W\betterquesting\`. **Dedicated server:** `<server>\<level-name>\betterquesting\`. CONFIRMED: `loadDatabases()` uses `saves/<folder>/betterquesting` on the client and `<folder>/betterquesting` on the server.
- **Files in that folder** (all CONFIRMED in SaveLoadHandler at 3.8.92 and 3.7.15, so both 2.8.4 and 2.9.0):
  - `QuestProgress\<player-uuid>.json`: **one file per player**, root `questProgress:9`. Each entry contains:
    - `questIDHigh/Low`
    - `completed:9`: a list of `{uuid:8, claimed:1, timestamp:4}`
    - `tasks:9`: a list of `{taskID, index, completeUsers:9 [uuid strings], userProgress:9 [{uuid, …per-task data}]}`
    - CONFIRMED: `QuestInstance.writeProgressToNBT`, plus https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/bq_standard/tasks/base/TaskBase.java and `TaskProgressableBase.java`.
  - `timestamp` is epoch milliseconds. It is set at completion and **overwritten when the reward is claimed**. CONFIRMED: `setComplete` and `claimReward` both call `System.currentTimeMillis()`. Sorting by timestamp gives a usable "recently completed or claimed" feed.
  - The legacy single `QuestProgress.json` is migrated to the per-player files and moved to `QuestProgress.backup.json`. CONFIRMED.
  - `QuestingParties.json`: `parties:9 → {partyID, members:[{uuid, status}], properties}`. CONFIRMED: `PartyInstance.writeToNBT`.
  - `NameCache.json`: UUID↔name, used to find which progress file belongs to the player. CONFIRMED.
  - `LifeDatabase.json` (hardcore lives) and `backup\<oldBQversion>\…` (copies made when the BQ version changes). CONFIRMED.
- **Parties:** completion is still stored **per member UUID**. When a player joins a party, BQ copies completions to them ("force party quest sync"). CONFIRMED: https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/questing/party/PartyManager.java
- **Multiplayer client:** BQ saves only from the server-side world-save hook (`!event.world.isRemote`). CONFIRMED: `EventHandler.onWorldSave`. A client on a server therefore has **no local progress file**. Inferred from the code: no client-side writer was found.

### 1.7 Freshness and write safety

- **When progress is written:** on Forge `WorldEvent.Save` for dimension 0, so on every autosave, pause-save and quit. CONFIRMED: https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/handlers/EventHandler.java
- **How it is written:** only dirty players' files, on BQ's IO thread. The data goes to `<file>.tmp`, is read back to validate it, then `Files.move(..., ATOMIC_MOVE, REPLACE_EXISTING)`. A reader sees either the old or the new complete file. CONFIRMED: `JsonHelper.WriteToFile2`, https://github.com/GTNewHorizons/BetterQuesting/blob/3.8.92-GTNH/src/main/java/betterquesting/api/utils/JsonHelper.java
- **Windows caveat:** see §6.
- **No quest-completion log line.** No chat or logger call was found in BQ's completion or claim paths, and completion is shown as a GUI toast. The only BQ log lines are `Loaded N quests / quest lines / parties / names` at world load, in `fml-client-latest.log`. CONFIRMED (code search).

---

## 2. Client logs (`MC\logs\`)

### 2.1 Which file gets what

The Forge 1.7.10 `log4j2.xml` is what GTNH runs; the last change was in 2017 and only touched `ignoreExceptions`. CONFIRMED: https://github.com/MinecraftForge/MinecraftForge/blob/1.7.10/fml/src/main/resources/log4j2.xml

- **`latest.log`** receives only the `net.minecraft` and `com.mojang` loggers, which are `additivity="false"`. Its line pattern is `[HH:mm:ss] [thread/LEVEL]: msg`.
- **`fml-client-latest.log`** receives the root logger at level **ALL**: every mod, FML, and redirected STDOUT. Its pattern is `[HH:mm:ss] [thread/LEVEL] [logger/mod]: msg`.
- **Consequence:** vanilla `[CHAT]` lines are in `latest.log`, and mod log lines (BQ, GT, BetterCrashes) are in `fml-client-latest.log`. Inferred from the config. UNCERTAIN until checked on a real GTNH log, since the classpath order of `log4j2.xml` under lwjgl3ify/RFB was not verified.
- GTNH's own crash template asks for `logs/fml-client-latest.log` / `fml-server-latest.log`, which is consistent with the above. CONFIRMED: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/master/.github/ISSUE_TEMPLATE/000-report-crash.yml
- The GTNH Java 17+ Prism instance launches Forge 10.13.4.1614 through `com.gtnewhorizons.retrofuturabootstrap.Main`. CONFIRMED: https://github.com/GTNewHorizons/lwjgl3ify/blob/master/prism-libraries/patches/net.minecraftforge.json
  - *Correction 2026-11-03 (PR #4 source check):* that file's `mainClass` is only half the story. The 2.9.0 Java 17–26 zip also ships `patches/me.eigenraven.lwjgl3ify.launchargs.json` (order 100), which sets `mainClass` to `com.gtnewhorizons.retrofuturabootstrap.MainStartOnFirstThread`. Which of the two Prism ends up using wasn't checked: UNCERTAIN. Source: https://github.com/GTNewHorizons/lwjgl3ify/tree/3.0.42/prism-libraries/patches

### 2.2 Useful lines

| Event | Line | Status |
|---|---|---|
| Chat, system messages, achievements | `[..] [Client thread/INFO]: [CHAT] <text>` | CONFIRMED in 1.7.10 logs (https://pastebin.com/iz8Rw4Sm, https://forums.minecraftforge.net/topic/34123-forge-1710-server-broken). Hodgepodge `cleanChatLogs=true` in 2.9.0 strips `§` codes from exactly this log call (CONFIRMED: https://github.com/GTNewHorizons/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/mixins/early/minecraft/MixinGuiNewChat_CleanChatLogs.java and `config/hodgepodge.cfg` at 2.9.0). 2.8.4 has no such option, so strip `§.` yourself. |
| Deaths | death message as `[CHAT]` | UNCERTAIN: inferred because death messages are chat broadcasts. Exact wording varies with mobs, e.g. Infernal Mobs. |
| Ore vein discovered (VisualProspecting) | `[CHAT] You found a %s vein %s!`, `You may find: %s`, `You found %s new ore veins!`, `…oil fields!` | Text CONFIRMED: VP `en_US.lang` and `ClientCache.notifyNewOreVein`, which calls `thePlayer.addChatMessage` (https://github.com/GTNewHorizons/VisualProspecting/blob/1.5.42/src/main/java/com/sinthoras/visualprospecting/database/ClientCache.java). That this reaches the `[CHAT]` log is inferred from vanilla. |
| World start / login position | `Starting integrated minecraft server version 1.7.10`; `<name>[local:E:…] logged in with entity id N at (x, y, z)` | CONFIRMED (pastebin above) |
| Pause-save (single-player Esc menu) | `Saving and pausing game...` then `Saving chunks for level '<World>'/<Dim>` | CONFIRMED (pastebin above). Autosaves are silent: inferred, because vanilla's autosave calls `saveAllWorlds(true)`, which suppresses logging. |
| Quit | `Stopping server`, then `Stopping!` (client) | CONFIRMED (pastebin above and the forum post) |
| BQ loaded | `Loaded N quests` etc. (fml log) | CONFIRMED (SaveLoadHandler) |
| ServerUtilities backup | chat broadcast `cmd.backup_start`, plus logger lines `Suspended world saving…` / `Restored…` | CONFIRMED: https://github.com/GTNewHorizons/ServerUtilities/blob/2.4.22/src/main/java/serverutils/task/backup/BackupTask.java |
| Dimension change | **No vanilla log line found** | UNCERTAIN. Infer it from `playerdata` `Dimension`, or from which JourneyMap `DIM<n>` tiles change. |
| Quest completion | **None** (see §1.7) | CONFIRMED (code search) |

- **Live tailing:** tailing `latest.log` live for chat is an established practice (https://gist.github.com/redwallhp/d4004884925f3ffcb6c7). Flush latency was not measured; UNCERTAIN.
- **Encoding:** UNCERTAIN. The log may be in the platform charset on Java 17 versus UTF-8 on Java 18+. Decode as UTF-8 with `errors="replace"`.

### 2.3 Rotation (from the same `log4j2.xml`, CONFIRMED)

- **`latest.log`** rolls at every game start (`OnStartupTriggeringPolicy`) and at midnight (`TimeBasedTriggeringPolicy` with `%d{yyyy-MM-dd}`). Old files become `logs/YYYY-MM-DD-N.log.gz`.
- **`fml-client-latest.log`** rolls at every start to `fml-client-<N>.log`, keeping 3 (`max="3" fileIndex="max"`).
  - Which index is newest is UNCERTAIN. Pick by modification time.
  - Before log4j has a context, early output goes to `logs/fml-junk-earlystartup.log`.

### 2.4 Crashes and other logs

- **Crash reports:** `MC\crash-reports\crash-YYYY-MM-DD_HH.MM.SS-client.txt` (or `-server`).
  - BetterCrashes also logs `Minecraft ran into a problem! Report saved to: …` with the full report at FATAL, and can return the player to the title screen instead of exiting.
  - CONFIRMED: https://github.com/GTNewHorizons/BetterCrashes/blob/master/src/main/java/vfyjxf/bettercrashes/utils/CrashUtils.java
  - GTNH caps this at 30 crash logs per restart (`crashLogLimitClient=30`, CONFIRMED in `config/bettercrashes.cfg` at 2.9.0).
- **JourneyMap** writes its own `MC\journeymap\journeymap.log`. CONFIRMED: https://github.com/TeamJM/journeymap-legacy/blob/master/src/main/java/journeymap/client/log/JMLogger.java

---

## 3. World save files

### 3.1 `W\level.dat` (gzip NBT)

- **Single-player host data is in `Data.Player`.** The host's player data (position, dimension, inventory) lives in `level.dat → Data → Player` and overrides `playerdata\<uuid>.dat` for that player.
  - CONFIRMED: https://minecraft.fandom.com/wiki/Player.dat_format
  - Also confirmed by a ServerUtilities comment: player data must be saved "so level.dat is written with the current host inventory" (https://github.com/GTNewHorizons/ServerUtilities/blob/2.4.22/src/main/java/serverutils/task/backup/BackupTask.java).
  - **In single-player, read the host's state from `level.dat` first.**
- **FML registry, root tag `FML`** (CONFIRMED: https://github.com/MinecraftForge/MinecraftForge/blob/1.7.10/fml/src/main/java/cpw/mods/fml/common/FMLContainer.java, `FMLCommonHandler.handleWorldDataSave`, `meta.modId="FML"`):
  - `ModList`: `[{ModId, ModVersion}]`. Useful to detect the pack version and addon presence.
  - `ItemData`: `[{K, V}]`. `K = "\u0001modid:name"` is a **block** and `"\u0002modid:name"` is an **item**; `V` is the numeric ID. CONFIRMED: prefix chars in https://github.com/MinecraftForge/MinecraftForge/blob/1.7.10/fml/src/main/java/cpw/mods/fml/common/registry/GameData.java
  - Also `BlockedItemIds`, `BlockAliases`, `ItemAliases`, `BlockSubstitutions` and `ItemSubstitutions`.
- **IDs are per world.** Always build the ID map from the target world's `level.dat`.
- **`.schematic` files:**
  - MCEdit/WorldEdit `.schematic` files store numeric IDs in `Blocks` plus `AddBlocks`, which gives 12 bits and so tops out at 4,095.
  - With EndlessIDs, block IDs can go up to 2^24 (CONFIRMED: README limits table, https://github.com/FalsePattern/EndlessIDs/blob/1.7.4/README.MD).
  - UNCERTAIN whether any GTNH block in his world has an ID above 4095, and how the WorldEdit build he uses encodes those. Check `max(V)` for blocks in `ItemData`.
- **Write safety:** on GTNH 2.9.0, Hodgepodge (`threadedWorldDataSaving=true`) writes `level.dat` atomically. It writes a hidden `.level.dat-*.tmp`, fsyncs it, `ATOMIC_MOVE`s it into place, and keeps the previous version as `level.dat_old`.
  - CONFIRMED: https://github.com/GTNewHorizons/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/util/WorldDataSaver.java and `MixinSaveHandler_threadedIO.java`.
  - On 2.8.4 (Hodgepodge 2.6.112), it renames to `_old` and then writes in place, so readers can see a missing or partial `level.dat`. CONFIRMED: https://github.com/GTNewHorizons/Hodgepodge/blob/2.6.112/src/main/java/com/mitchej123/hodgepodge/util/WorldDataSaver.java

### 3.2 `W\playerdata\<uuid>.dat` (gzip NBT)

- **Path:** CONFIRMED: ServerUtilities reads `playerdata/<uuid>.dat` (https://github.com/GTNewHorizons/ServerUtilities/blob/2.4.22/src/main/java/serverutils/lib/data/ForgePlayer.java). Baubles uses `playerdata/<name>.baub`, as seen in the ServerUtilities `invsee` code.
- **Keys:** `Pos` (3 doubles), `Dimension` (int), `Inventory` / `EnderItems` (lists of `{Slot, id, Count, Damage, tag}`). CONFIRMED: ServerUtilities `CmdTplast`, `MainInventory` and `EnderInventory`.
- **Numeric item `id` values** resolve through `level.dat` `ItemData` (`\u0002` prefix).
- **With EndlessIDs**, an item stack whose ID is above vanilla's maximum writes `id:short = 0` plus `idExt:int`. Read `idExt` first. CONFIRMED: https://github.com/FalsePattern/EndlessIDs/blob/1.7.4/src/main/java/com/falsepattern/endlessids/util/DataUtil.java
- **Write pattern:** vanilla writes a temp file, deletes the old one, then renames. UNCERTAIN: from memory of the 1.7.10 code; not covered by Hodgepodge's threaded saver.

### 3.3 `W\stats\<uuid>.json`

- **Location:** CONFIRMED: https://github.com/maruohon/mcstatsconverter and https://bugs-legacy.mojang.com/browse/MC-71885 (`./world/stats/<uuid>.json`).
- **Vanilla block and item stats use numeric IDs** in 1.7.x (CONFIRMED: mcstatsconverter "numerical block and item IDs"), for example `stat.mineBlock.<id>`.
- **Modded items get name-based keys** because GTNH enables Hodgepodge `addModItemStats=true` (CONFIRMED in both 2.8.4 and 2.9.0 `config/hodgepodge.cfg`):
  - The keys are `stat.mineBlock.autogen.<modid:name>`, `stat.craftItem.autogen.<modid:name>`, `stat.useItem.autogen.<modid:name>` and `stat.breakItem.autogen.<modid:name>`.
  - CONFIRMED: https://github.com/GTNewHorizons/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/mixins/early/fml/MixinGameRegistry.java, and the same code at 2.6.112.
- **Stats ignore metadata.** The stat arrays are indexed by item ID only, so all GT machines collapse into `gregtech:gt.blockmachines` and all GT parts into `gt.metaitem.0x`. CONFIRMED: same file.
- **General stats** (`stat.playOneMinute`, `stat.deaths`, …) and `achievement.*` keys are UNCERTAIN: not verified for 1.7.10.
- **Diff the file between saves** to get "crafted, mined or used since last check".
- **Write pattern:** UNCERTAIN. From memory, vanilla writes this file directly (non-atomically), so retry on a JSON error.

### 3.4 Region files `W\region\r.X.Z.mca` (other dimensions in `W\DIM<n>\region\`)

- **Container format** (CONFIRMED: https://minecraft.fandom.com/wiki/Region_file_format):
  - An 8 KiB header made of a location table (3-byte sector offset plus 1-byte count) and a **timestamp table** (big-endian epoch seconds, "last modification time of a chunk").
  - Each chunk is a 4-byte length, a 1-byte compression type (2 = zlib) and NBT.
- **The timestamp table is the cheapest "where things changed recently" index:** 8 KiB per region and no decompression. UNCERTAIN in meaning: chunks with ticking machines or entities may be re-saved at every autosave, so this tracks loaded and active areas, not only areas he built in.
- **Chunk NBT (pre-1.13):** `Level{xPos, zPos, LastUpdate, InhabitedTime, Sections[{Y, …}], Entities, TileEntities, TileTicks, …}`.
  - CONFIRMED in part: https://wiki.vg/Map_Format and https://minecraft.fandom.com/nl/wiki/Anvil_bestandsformaat (YZX index order: `y*256 + z*16 + x`).
  - `InhabitedTime` (cumulative player-presence ticks) is a strong "where he spends time" signal. UNCERTAIN in exact semantics; the field exists but was not documented in the sources checked.
- **Block storage differs by pack version, and both forms can coexist after an upgrade:**
  - **2.8.x (NEID 2.1.10):** `Blocks16` = 8,192 bytes, i.e. 4,096 big-endian uint16 IDs. `Data16` = 4,096 big-endian uint16 metadata values. Optional vanilla `Blocks`, `Add` and `Data` arrays may also be present. CONFIRMED: https://github.com/GTNewHorizons/NotEnoughIds/blob/2.1.10/src/main/java/com/gtnewhorizons/neid/mixins/early/minecraft/MixinAnvilChunkLoader.java
  - **2.9.0 (EndlessIDs 1.7.4):**
    - Block ID = `Blocks` (bits 0–7) | `Add` nibble (bits 8–11) | `BlocksB2Hi` nibble (12–15) | `BlocksB3` byte (16–23).
    - Metadata = `Data` nibble (0–3) | `Data1High` nibble (4–7) | `Data2` byte (8–15).
    - Biomes are `Biomes16v2` (uint16).
    - Nibble order matches vanilla: even index in the low nibble.
    - EndlessIDs **reads NEID `Blocks16`/`Data16` and rewrites in its own format when the chunk is next saved**, so an upgraded world mixes both formats until every chunk is re-saved.
    - CONFIRMED: https://github.com/FalsePattern/EndlessIDs/blob/1.7.4/src/main/java/com/falsepattern/endlessids/managers/BlockIDManager.java, `BlockMetaManager.java`, `BiomeManager.java`, README "NotEnoughIDs world compatibility".
- **GT machines are tile entities and cheap to find:** filter `Level.TileEntities` for `id == "BaseMetaTileEntity"` (machines, hatches, multiblock controllers) or `"BaseMetaPipeEntity"` (pipes and cables).
  - CONFIRMED: `GameRegistry.registerTileEntity(..., "BaseMetaTileEntity")` in https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.54.265/src/main/java/gregtech/loaders/preload/LoaderGTBlockFluid.java
  - Useful keys (CONFIRMED: https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.54.265/src/main/java/gregtech/api/metatileentity/BaseMetaTileEntity.java):
    - `mID` (MetaTileEntity id; same as the item damage of `gregtech:gt.blockmachines`)
    - `mActive`
    - `mWorks` (**stored inverted**: true means disabled)
    - `mWasShutdown`, `shutDownReasonID`
    - `mStoredEnergy`, `mOwnerName`, `mFacing`
    - plus the MetaTileEntity's own data saved into the same compound.
  - Read `mID` as any integer type; older GT versions used a short (UNCERTAIN for 2.8.4).
  - The `mID`→name map for core GT is in source: https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/api/enums/MetaTileEntityIDs.java, e.g. `EBF_CONTROLLER(1000)`.
  - **Addon machines** (TST, GT-Not-Leisure, Programmable Hatches, 123Technology) are UNCERTAIN: probably the same TE type with their own `mID` ranges. Use the NEI dump (§3.6) for names.
  - **Cheap pre-filter:** search the decompressed bytes for `b"BaseMetaTileEntity"` before a full NBT parse.
- **Python libraries** (versions from PyPI, CONFIRMED 2026-11-01):
  - **No maintained high-level Python library handles NEID or EndlessIDs.** Plan on roughly 60 lines of custom section decoding with numpy.
  - The **region container** is trivial with `struct` + `zlib`. A synthetic self-test passed on Python 3.13 with `nbtlib` 2.0.4: EndlessIDs IDs 1/4120/70000, NEID 4120 and a `BaseMetaTileEntity` TE all decoded (script at `/home/claude/scripts/region_selftest.py`). This test did not use real GTNH files.
  - `nbtlib` 2.0.4 (2021): pure Python. Its README says 2.0 "is not stable" and recommends 1.12.1.
  - `NBT` (twoolie) 1.5.1 (2021): includes a `RegionFile` reader and supports "pre-flattened" Anvil. It officially lists Python ≤3.7, but it worked on 3.13 in the self-test.
  - `amulet-nbt` 2.1.8 (2026-04): fast Cython. Windows wheels are listed for cp311/cp312 only, so use Python 3.12 on Windows.
  - `anvil-parser2` 0.10.6 (2024): "tested 1.14.4–1.19"; not suitable.
  - UNCERTAIN: NBT strings are Java modified UTF-8, and supplementary characters can break plain UTF-8 decoding. The `mutf8` 1.1.0 package can decode them.

### 3.5 ServerUtilities data and logs

- **Per-player data:** `W\serverutilities\players\*.dat` (NBT with `UUID`, `Name`, `LastTimeSeen`, and mod data such as `Homes` and `LastDeath` as `[x,y,z,dim]`), plus `teams\` and `universe.dat`. CONFIRMED: https://github.com/GTNewHorizons/ServerUtilities/blob/2.4.22/src/main/java/serverutils/lib/data/Universe.java and `ServerUtilitiesPlayerData.java`.
- **Opt-in event log (config change only, no new mod):**
  - Settings: `world.logging.enabled`, `block_placed`, `block_broken`, `item_clicked_in_air`, `entity_attacked` and `chat_enable`. All are off in GTNH's `MC\serverutilities\serverutilities.cfg` (CONFIRMED: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/serverutilities/serverutilities.cfg).
  - When enabled, it writes `YYYY-MM-DD HH:MM:SS : <player> placed <Localized Block Name>:<meta> at [x, y, z] in <Dimension>` to `logs/world.log`, and chat to `logs/chat.log`. Both are appended on each universe save.
  - CONFIRMED: https://github.com/GTNewHorizons/ServerUtilities/blob/2.4.22/src/main/java/serverutils/data/ServerUtilitiesUniverseData.java and `handlers/ServerUtilitiesPlayerEventHandler.java`.
  - Creative mode and fake players are excluded by default.
  - In single-player this should resolve to `MC\logs\world.log`. UNCERTAIN: `server.getFile` relative path not checked on the integrated server.
- **Backups (on by default):**
  - Settings: `backup_timer=30m`, `backups_to_keep=12`, `need_online_players=true`, `backup_folder_path=./backups/`. Files are named `YYYY-MM-DD-HH-mm-ss.zip`. That `./backups/` resolves to `MC\backups\` in single-player is inferred from the relative path.
  - Extra paths included: `journeymap/data/sp/$WORLDNAME/**`, `visualprospecting/client/*/$WORLDNAME_*/**`, `visualprospecting/server/$WORLDNAME_*/**` and `saves/NEI/local/$WORLDNAME/**`.
  - Before zipping, it saves all player data and all chunks, drains the threaded IO queue, and suspends world saving, so the world part is a consistent snapshot.
  - CONFIRMED: config above, plus https://github.com/GTNewHorizons/ServerUtilities/blob/2.4.22/src/main/java/serverutils/task/backup/BackupTask.java and `ThreadBackup.java`.

### 3.6 Other useful local files

- **NEI dumps** (one manual click in NEI options → Tools → Data dumps; no mod needed):
  - `MC\dumps\itempanel.csv` has the columns `Item Name, Item ID, Item meta, Has NBT, Display Name`. Example: `gregtech:gt.blockmachines` with meta 1000 → "Electric Blast Furnace" (the example row itself is UNCERTAIN). This covers addon machines too.
  - `MC\dumps\item.csv` and `MC\dumps\block.csv` are the registry dumps.
  - CONFIRMED: https://github.com/GTNewHorizons/NotEnoughItems/blob/master/src/main/java/codechicken/nei/config/ItemPanelDumper.java, `DataDumper.java`, `api/ItemInfo.java`.
  - **This is the best on-disk ID/meta→name table.**
- **NEI bookmarks:** `MC\saves\NEI\<worldPath>\bookmarks.ini` (world-specific by default) or `saves\NEI\global\bookmarks.ini`. CONFIRMED: https://github.com/GTNewHorizons/NotEnoughItems/blob/master/src/main/java/codechicken/nei/BookmarkPanel.java
  - This shows what he has bookmarked, which is a goal or intent signal.
  - Line format UNCERTAIN: one JSON item string per line, with groups.
- **`MC\GregTech.lang`:** GT-generated English strings in the instance root (`initLocalization(configDir.getParentFile())`). CONFIRMED: https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/GTMod.java and `loaders/preload/GTPreLoad.java`. It is incomplete, because keys already provided by normal lang files are skipped.
- **`MC\config\guidenh\DefaultGuide.zip` (2.9.0):** GuideNH documentation pack, mostly Markdown pages under `assets/<mod>/guidenh/_en_us/…`. The source repo has 692 `.md` files (gregtech, AE2, changelog, …). It is useful for mechanics Q&A.
  - CONFIRMED: release workflow https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/.github/workflows/release-tags.yml and https://github.com/GTNewHorizons/GTNH-Guide-Pack
  - The zip's internal layout is UNCERTAIN; it is assumed to mirror the repo's `assets/` tree.

---

## 4. Ore and location data

### 4.1 VisualProspecting

- **World ID:** stored as WorldSavedData `visualprospecting` with key `wId = "<WorldFolder>_<random UUID>"`. CONFIRMED: https://github.com/GTNewHorizons/VisualProspecting/blob/1.5.42/src/main/java/com/sinthoras/visualprospecting/database/WorldIdHandler.java and `Tags.java`.
  - The file should be `W\data\visualprospecting.dat` with root `data.wId`. UNCERTAIN: vanilla MapStorage naming, not checked.
  - Globbing `<WorldFolder>_*` also works, and ServerUtilities' own backup patterns rely on it.
- **Paths** (relative to `MC\`; CONFIRMED: `Tags.java`, `ClientCache.getStorageDirectory`, `WorldCache.loadVeinCache`, `HooksEventBus.java`):
  - Client cache = **veins the player knows about** (prospected, interacted with, or shared by his team): `visualprospecting\client\<playerUUID>\<WorldFolder>_<uuid>\DIM<n>.dat`
  - Server cache = **every generated vein**, a spoiler: `visualprospecting\server\<WorldFolder>_<uuid>\DIM<n>.dat`
  - The server cache is filled from GT worldgen events and an initial region scan. CONFIRMED: `ServerCache.java` and `cachebuilder/DimensionAnalysis.java`.
- **Format: gzip NBT** (`CompressedStreamTools.writeCompressed`):
  - **v3 (VP 1.5.42 = GTNH 2.9.0):**
    - `{version:3, dim, ores:{palette:[str], chunkX:int[], chunkZ:int[], veinTypeIndex:int[], depleted:byte[], source:byte[]}, fluids:{palette, chunkX, chunkZ, fluidTypeIndex, chunkData:int[N*chunkDataSize], chunkDataSize}}`
    - `source` values: 0 UNKNOWN, 1 GENERATED, 2 RESCAN, 3 API, 4 ADMIN.
    - CONFIRMED: https://github.com/GTNewHorizons/VisualProspecting/blob/1.5.42/src/main/java/com/sinthoras/visualprospecting/database/DimensionCache.java and `VeinSource.java`.
  - **v2 (VP 1.4.8 = GTNH 2.8.4):** no `version` key. `ores:{chunkX[], chunkZ[], depleted[], veinTypeNames:[str per entry]}` and `fluids:{chunkX[], chunkZ[], chunkData[], chunkDataSize, fluidNames}`. CONFIRMED: https://github.com/GTNewHorizons/VisualProspecting/blob/1.4.8/src/main/java/com/sinthoras/visualprospecting/database/DimensionCache.java
  - There is also an older binary format in `ore/DIM<n>` and `fluids/DIM<n>`, which is migrated automatically (`LegacyDimensionCacheLoader`).
- **Coordinates:** the vein centre in blocks is `chunk*16 + 8`. CONFIRMED: `OreVeinPosition.getBlockX`.
- **Vein names:** GT ore-mix names, e.g. the sentinel `ore.mix.none`. That other names follow the `ore.mix.*` pattern is UNCERTAIN.
- **Freshness:**
  - The server cache saves on every `WorldEvent.Save`, i.e. on autosave.
  - The **client cache saves only on world unload or disconnect**: dimension change or quit. CONFIRMED: `HooksEventBus.java` and `HooksFML.java`.
  - For live updates, use the `[CHAT] You found a … vein (x,z)!` log lines.
- **Write safety:** files are written **in place** (`FileOutputStream` on ThreadedFileIO, no temp file), so a reader can see a truncated file. Retry on a gzip or NBT error. CONFIRMED: `Utils.writeNBT`.

### 4.2 JourneyMap 5.2.23 (1.7.10; source is public as `journeymap-legacy`)

- **Waypoints:** `MC\journeymap\data\sp\<WorldFolder>\waypoints\<name>_<x>,<y>,<z>.json`, one file per waypoint, with illegal characters replaced by `_`.
  - On a server: `journeymap\data\mp\<server[_world]>\…`.
  - Fields: `id, name, icon, x, y, z, r, g, b, enable, type (Normal|Death), origin, dimensions[int]`.
  - **Nether waypoints store x and z ×8** (overworld scale); divide by 8 for dimension −1.
  - **`type: "Death"` waypoints give death locations.**
  - CONFIRMED: https://github.com/TeamJM/journeymap-legacy/blob/master/src/main/java/journeymap/client/model/Waypoint.java, `waypoint/WaypointStore.java`, `io/FileHandler.java`, `Constants.java`, `data/WorldData.java`. `project.properties` = 5.2.23, the exact GTNH 2.9.0 version.
- **Map tiles:** `journeymap\data\sp\<World>\DIM<n>\<mapType subdir>\<regionX>,<regionZ>.png`. CONFIRMED: `io/RegionImageHandler.java`.
  - File modification times show where he has explored or been mapping recently.
  - The subfolder names are UNCERTAIN.
- **Write pattern:** in place (Guava `Files.write`), small files.
- **Upcoming 6.0.0:** JourneyMap 6.0.0 for 1.7.10 is in beta (https://modrinth.com/mod/journeymap/version/1.7.10-6.0.0-beta.2+forge). Waypoint storage may change there; UNCERTAIN.

---

## 5. Event-logging mods and existing tools

- **No mod in GTNH logs "what the player did" by default.** The closest is ServerUtilities world logging, which is opt-in config (§3.5). Real-time signals otherwise are the `[CHAT]` lines (§2.2).
- **No existing GTNH-specific save parser was found.** GTNH's own tooling is aimed at pack building (DreamAssemblerXXL) and quest editing.
  - `mcstatsconverter` (1.7→1.8 stats) exists, but it is for migration.
  - Web search found no maintained Python BQ progress parser; the formats in §1 are simple enough to implement directly.

---

## 6. Safety: reading while the game runs

### 6.1 When data reaches disk

- **Autosave every 900 ticks** (45 s at 20 TPS; longer if TPS drops). The constant is configurable as Hodgepodge `autoSaveInterval`, and GTNH ships the default **900** in both 2.8.4 and 2.9.0.
  - CONFIRMED: https://github.com/GTNewHorizons/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/mixins/early/minecraft/server/MixinMinecraftServer_AutoSaveInterval.java (`@ModifyConstant(intValue=900)` in `MinecraftServer.tick`), plus `config/hodgepodge.cfg` at both tags and https://github.com/GTNewHorizons/Hodgepodge/pull/323
  - Autosave writes player data and all worlds. Chunks go through the ThreadedFileIO queue, so they land shortly after the save.
- **Pausing in single-player forces a full save**, logged as `Saving and pausing game...` (CONFIRMED: pastebin log).
  - **Alt-tabbing out** probably triggers this too via vanilla `pauseOnLostFocus`. UNCERTAIN: check `options.txt` and the log.
  - While paused, the integrated server does not tick, so nothing changes.
- **Quitting the world saves everything**, including the VP client cache.
- **ServerUtilities backups** (every 30 minutes) force a full, consistent flush and produce a zip.

### 6.2 How each file is written

| File | Write method | Reader risk |
|---|---|---|
| BQ `*.json` | temp file, then `ATOMIC_MOVE` | none on POSIX; Windows sharing issue, see §6.3 |
| `level.dat` (2.9.0) | temp file, then `ATOMIC_MOVE` | same as above |
| `level.dat` (2.8.4) | rename to `_old`, then write in place | missing or partial file |
| `playerdata` | temp file, delete, rename (UNCERTAIN) | brief gap |
| `stats` JSON | direct overwrite (UNCERTAIN) | partial JSON |
| VP `DIM*.dat` | in place | partial gzip |
| JourneyMap JSON | in place | partial JSON |
| Region files | in-place sector writes via `RandomAccessFile` | a chunk can be read mid-rewrite: bad zlib, or a stale offset pointing at reused sectors |
| Logs | append | rotate on launch |

### 6.3 Windows sharing (the real hazard)

- **Only rename and delete are affected; reads are not.** A handle opened without `FILE_SHARE_DELETE` blocks other processes from opening that file for delete access, and "Delete access allows both delete and rename operations." CONFIRMED: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
- **CPython does not expose `FILE_SHARE_DELETE` for `open()`.** The feature request is still open: https://github.com/python/cpython/issues/59449. That its `open()` omits the flag is inferred from that issue.
- **The danger:** if Python holds a BQ progress file, `level.dat`, or `latest.log` open while Java renames over it (or log4j rolls the log at launch), the Java side can fail. BQ logs "An error occurred while saving JSON to file (Temp copy)" and that save is lost until the next cycle. The failure itself is UNCERTAIN as to exact behaviour; it is inferred from the Microsoft doc above.
- **Mitigations:**
  - Open, read everything and close immediately. Never keep handles open.
  - Tail logs by polling: open, seek to the saved offset, read, close. Detect rotation by the file shrinking or its creation time changing.
  - Optionally open through `CreateFileW` with `FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE` via `ctypes`/`pywin32`.
  - **Never write** into the save, and never touch `session.lock`.

### 6.4 Recommended read protocol

- **Quest and player JSON:** copy to a temp location, parse, retry up to 3 times on error.
- **Region scans:** first pick changed chunks from the timestamp tables; validate length, zlib and NBT; retry on failure.
- **For "bulk truth"** (machine census, full inventory diff), prefer:
  1. the newest `MC\backups\*.zip` (consistent snapshot), or
  2. a read right after a `Saving and pausing game...` line, or
  3. a read after the game exits.

---

## 7. Recommendation by use case

| Use case | Primary source | Secondary sources |
|---|---|---|
| **Quest Q&A** | `W\betterquesting\QuestDatabase.json`: the world's actual book, with English name/desc, tasks with registry-name items, and the prerequisite graph. Fall back to `MC\config\betterquesting\DefaultQuests\` for per-quest chunks and `QuestLinesOrder.txt` for tab order. | `template.lang` as a flat text index; `config\guidenh\DefaultGuide.zip` (2.9.0) for mechanics; NEI `itempanel.csv` to name items in tasks |
| **Quest progress** | `W\betterquesting\QuestProgress\<uuid>.json` (find the UUID via `NameCache.json` or `level.dat`), plus `QuestingParties.json`. Compute available = prerequisites satisfied under `questLogic`, not completed, visibility allows. Sort `completed.timestamp` for "recently done". At most ~45 s stale. | |
| **Recent-activity summary** | (a) poll-tail `latest.log`: `[CHAT]` (deaths, VP veins, achievements), login position, pause, quit; (b) diffs of QuestProgress timestamps; (c) diffs of `level.dat` `Data.Player` (single-player) or `playerdata` for position, dimension and inventory; (d) diffs of `stats\<uuid>.json` (modded keys `…autogen.<modid:name>`); (e) region timestamp tables, then a GT `BaseMetaTileEntity` scan of the changed chunks (`mActive`, `mWasShutdown`, `shutDownReasonID`); (f) JourneyMap tile modification times and death waypoints; (g) NEI `bookmarks.ini` as an intent signal. | **Best single upgrade:** ask him to set ServerUtilities `world.logging.enabled/block_placed/block_broken=true`, which yields exact "placed X at [x,y,z]" lines with no mod added. |
| **Ore lookup** | VP **client** cache `visualprospecting\client\<uuid>\<World>_*\DIM<n>.dat` (known veins, v2 or v3 NBT), plus live `[CHAT] You found a … vein` lines for discoveries since the last dimension change. | JourneyMap waypoints for named spots. Use the server cache only if he explicitly wants spoilers (it lists every generated vein). |

---

## 8. Open risks

- **2.8 → 2.9 format changes:** NEID→EndlessIDs (mixed chunk formats after upgrade), VP v2→v3, template.lang moved from `load` to `forceload`. A parser must support both sides of each.
- **Addon machine names** (TST, GTNL, Programmable Hatches, 123Tech) are not in GT5U's `MetaTileEntityIDs`. Use the NEI dump.
- **Log routing** (vanilla only in `latest.log`, mods only in the fml log) comes from the Forge config. It has not been verified on a real GTNH 2.9 log.
- **Windows rename and sharing interactions** with a live Java writer; mitigations in §6.3.
- **Stats ignore metadata**, so GT machines and parts are not distinguishable there.
- **Server play:** BQ progress, stats, playerdata, the VP server cache and region files are on the server, not on the client. A client has only logs, the VP client cache, JourneyMap `mp` data and NEI files.
- **JourneyMap 6.0** (1.7.10 beta) may change waypoint storage.
- **Modified-UTF-8 NBT strings.**

## 9. Questions only the player can answer

1. Which exact GTNH version is he running now (2.8.x, a 2.9 beta, or 2.9.0 stable), and has this world been opened in 2.9 (EndlessIDs migration)?
2. Single-player, or a server (his own or public)? If a server, can the assistant read the server's world folder?
3. Instance path and world folder name. Microsoft account (online UUID) or offline?
4. Is he willing to:
   - enable ServerUtilities world logging (`MC\serverutilities\serverutilities.cfg`),
   - do a one-time NEI item-panel dump,
   - change Hodgepodge `autoSaveInterval` if fresher data is wanted?
5. Game language: `en_US` (JSON text is shown) or another locale (lang keys win)?
6. Should ore answers use only veins he has found (client cache), or every generated vein (server cache, a spoiler)?
7. Is `pauseOnLostFocus` on (an alt-tab save trigger), and are ServerUtilities backups enabled with enough disk space?

## 10. Sources (all accessed 2026-11-01)

GitHub source at the pinned tags listed inline. Web sources:
- https://prismlauncher.org/wiki/getting-started/data-location
- https://rubenerd.com/where-does-prism-store-the-minecraft-folder
- https://gtnh.miraheze.org/wiki/Migrating
- https://minecraft.fandom.com/wiki/Region_file_format
- https://minecraft.fandom.com/wiki/Player.dat_format
- https://minecraft.fandom.com/nl/wiki/Anvil_bestandsformaat
- https://wiki.vg/Map_Format
- https://github.com/maruohon/mcstatsconverter
- https://bugs-legacy.mojang.com/browse/MC-71885
- https://pastebin.com/iz8Rw4Sm
- https://forums.minecraftforge.net/topic/34123-forge-1710-server-broken
- https://gist.github.com/redwallhp/d4004884925f3ffcb6c7
- https://github.com/GTNewHorizons/Hodgepodge/pull/323
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
- https://github.com/python/cpython/issues/59449
- https://modrinth.com/mod/journeymap/version/5.2.19
- https://modrinth.com/mod/journeymap/version/1.7.10-6.0.0-beta.2+forge
- https://pypi.org/project/nbtlib/
- https://pypi.org/project/NBT/
- https://pypi.org/project/amulet-nbt/
- https://pypi.org/project/anvil-parser2/
- https://pypi.org/project/mutf8/
