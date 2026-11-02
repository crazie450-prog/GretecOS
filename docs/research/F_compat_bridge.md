# F — Addon compatibility with GTNH 2.9.0, the OC file mailbox, pause-on-focus-loss, OCGlasses and the Chat Box

Researched 2026-11-01. I read the source at the exact tags the pack or the release uses (shallow git clones), plus GitHub release and issue pages. C_live_bridge.md covers the OC basics; this file only adds to it.
Legend: **CONFIRMED [Sx]** = verified in the cited source. **UNCERTAIN** = not verified, with the reason given.

---

## 1. Addon compatibility with GTNH 2.9.0

### 1.0 Pack baseline (needed to judge each addon)

| Mod | 2.9.0-RC-2 (2026-10-04) | **2.9.0 stable (2026-10-31)** | daily (2026-11-01 10:39 UTC) |
|---|---|---|---|
| GT5-Unofficial | 5.09.54.205 | **5.09.54.265** | 5.09.54.265 |
| NotEnoughItems | 2.8.155-GTNH | **2.8.171-GTNH** | 2.8.171-GTNH |
| GTNHLib / ModularUI2 | 0.11.52 / 2.3.92 | 0.11.54 / 2.3.94 | same |
| OpenComputers / Computronics / OCGlasses | 1.12.64 / 1.9.10 / 1.6.9 | 1.12.68 / 1.9.13 / 1.6.9 | 1.12.69 / 1.9.13 / 1.6.9 |
| Hodgepodge / lwjgl3ify / Angelica | 2.7.215 / 3.0.37 / 2.2.28 | 2.7.221 / 3.0.42 / 2.2.43 | 2.7.222 / 3.0.42 / 2.2.43 |
| ID extender | — | **EndlessIDs 1.7.4** (2.8.4 had NotEnoughIds 2.1.10) | — |
| ArchaicFix (external) | — | 0.8.2 | — |

All values: CONFIRMED [M1][M2][M3][M4].

- **There are 60 GT builds between RC-2 and stable** (.205 → .265). Three of the addons below were built against RC-2.
- **GT changed APIs in that gap.** GTNL had to change code to run on stable (§1.2). So a jar built against RC-2 is not automatically safe on stable. CONFIRMED [GTNL-c].

### 1.1 Twist Space Technology (TST)
- **Latest:** release `0.8.0-RC2.9`, 2026-10-27, marked "Latest" (the RC in the name is TST's own label). Six more commits on `main` since then (to 2026-11-01), all recipe or bug fixes. CONFIRMED [TST-r][git].
- **Build target:** `setGtnhVersion("2.9.0-RC-2")`, i.e. GT .205 / NEI 2.8.155, both at the tag and on `main` HEAD. CONFIRMED [TST-d].
- **Hard dependencies:** gregtech, bartworks, gtnhintergalactic (all in the pack). CONFIRMED (source, `TwistSpaceTechnology.java`).
- **Works on 2.9.0 stable: UNCERTAIN.** There is no release built against stable, and no notes mention stable.
  - I grepped TST for the GT APIs that broke GTNL (`getPowerPanelMaxParallel`, `setMaxParallelForPanel`, the CropsNH `HydrationRegistry`, the NEI `recipeHandler.y`). There are no hits, which is reassuring but not proof.
- **Open issues:** #1316 "Updating for GTNH 2.9.0" (2026-09-24) is a to-do list (COMET deprecation, late-game rebalance) with no crash reported. CONFIRMED [TST-i].
- **Cross-addon issue:** GTNL #575 (open, 2026-07-22) reports a memory leak when GTNL is loaded with TST "or other mods". CONFIRMED [GTNL-i].

### 1.2 GT-Not-Leisure (GTNL)
- **Latest release:** `0.2.7-rc2.1`, 2026-10-31, published by github-actions. The tag is commit `5f54ba5` on `dev-290`. CONFIRMED [GTNL-r].
  - The repo now shows as GTNotLeisure/GT-Not-Leisure (it was renamed or moved from ABKQPO).
- **That release builds against RC-2:** GT 5.09.54.205, NEI 2.8.155. CONFIRMED [GTNL-d].
- **Not built for stable.** On 2026-10-31, after that tag, commit `28007498` "适配 GTNH 2.9.0 正式版" ("adapt to GTNH 2.9.0 final", #666) moved the build to GT .265 / NEI 2.8.171. It also changed code against APIs that changed in stable:
  - CropsNH `HydrationRegistry.getPotency` now returns `IPotencyData`.
  - GT `setMaxParallelForPanel` and `getPowerPanelMaxParallel` were replaced by `getTrueParallel`.
  - NEI `recipeHandler.y` was replaced by `getGuiXOffset()` / `getGuiYOffset()`.

  CONFIRMED [GTNL-c].
  - **Effect:** rc2.1 still calls the removed methods (`IGreenHouse.java`, `TreeDiagramGui.java`, `FOGModuleGui.java`, `NEIGTNLGalaxySpaceConfig.java`). CONFIRMED (grep of the tag).
  - On stable those paths will most likely throw `NoSuchMethodError` when used. UNCERTAIN (not run).
  - **Wait for the first release after `28007498`** (none as of 2026-11-01), or build `dev-290`.
- **Also needs GTNLLib** (`gtnllib`, separate jar, 1.1.1 at the tag). CONFIRMED [GTNL-d].
- **Open issues** (33 open; none mention 2.9 by title): #575 memory leak with TST; #426 freeze on export from the singularity data hub; #435 PV stations damaged on world load. CONFIRMED [GTNL-i].

### 1.3 Programmable Hatches (PH)
- **Latest:** `v0.2.1p1-beta`, 2026-11-01, marked "Latest". Branch `290-daily-latest`. CONFIRMED [PH-r][git].
- **Build target is the closest to stable of the four addons:** GT 5.09.54.**264**, NEI 2.8.171, AE2 rv3-beta-1095 (= stable), GTNHLib 0.11.54, Hodgepodge 2.7.221, OC 1.12.67, Computronics 1.9.13. CONFIRMED [PH-d].
- **Works on 2.9.0 stable: likely.** It is one GT build behind stable. UNCERTAIN (not run).
- **Hard dependencies:** appliedenergistics2, gregtech. CONFIRMED (source, `MyMod.java`).
- **Open issues:**
  - #298 "GTNH 2.9.0-beta1 crash" (2026-06-14). The author blames GT and points to GT PR #7143; workaround is to delete `config/txloader/load/gregtech/inflection`. Whether it is fixed in stable is UNCERTAIN.
  - #304 MixinTargetAlreadyLoadedException with the Translation-of-GTNH resource pack.
  - #323 crash placing a programmable dual hatch in a GTNL large centrifuge (a cross-addon issue).

  CONFIRMED [PH-i].

### 1.4 123Technology
- **Latest:** release `2.2.0.1`, 2026-10-29, marked "Latest". The release notes cover content only. CONFIRMED [123-r].
- **Build target:** `gtnhVersion = "2.9.0-RC-2"` (GT .205). CONFIRMED [123-d].
- The feature branch `bd_new` switched to `gtnhVersion = "2.9.0"` (stable) in commit `812d2e0` on 2026-11-01. It is not merged and not released. CONFIRMED [git].
- **Works on 2.9.0 stable: UNCERTAIN**, for the same reason as TST. The grep for the APIs that broke GTNL found nothing.
- **Hard dependencies:** IC2, structurelib, Avaritia, eternalsingularity, modularui, GalacticraftCore, bartworks, miscutils, dreamcraft, gregtech, GalaxySpace (all in the pack).
  - It compiles against worldedit-gtnh v0.0.8, but WorldEdit is **not** a required dependency.

  CONFIRMED (source, `OTHTechnology.java`, `dependencies.gradle`).
- **Open issues:** 9, none about crashes or 2.9. CONFIRMED [123-i].

### 1.5 WorldEdit (GTNH fork)
- **Not in the 2.9.0 pack**, so it is a separate install. CONFIRMED [M1].
- **Latest:** `v0.0.10`, 2026-07-19 ("Custom Rotation Mappings").
  - v0.0.9 (2026-03-06) fixed loading of large IDs.
  - v0.0.8 (2025-04-20) added ID conversion and support for larger IDs and data in schematics.

  CONFIRMED [WE-r][git].
- **Dependencies:** none required. `mcmod.info` lists only ForgeMultipart, as an ordering hint. The build depends only on CodeChickenCore. CONFIRMED (source).
- **Risk with 2.9:** 2.9.0 replaced NotEnoughIds with **EndlessIDs** [M1][M4].
  - Open #30 (2026-07-28): on 2.9 beta-2 with WE 0.0.10, `//set` of any block with **meta > 15** errors (example: GT ore `3034:824`). A maintainer suspects code "hardcoded to only go up to 15". CONFIRMED [WE-30].
  - Open #24: EndlessIDs support for schematic save and load.
  - Open #28: chunk corruption when placing a meta value that does not exist.
  - Open #21: ProjectRed circuitry copied wrongly or crashing.
  - Open #10: crash with Et Futurum Requiem.

  CONFIRMED [WE-i].
  - **Impact on the schematic pipeline:** this matters for img2schem, because GT ore and machine blocks use high meta values. Test `//schem` round-trips on 2.9 before relying on it. UNCERTAIN (not tested).

### 1.6 PlanNH
- **Not bundled.** It is in neither the 2.9.0 manifest nor today's daily manifest. CONFIRMED [M1][M3].
- **Client-side only.** CONFIRMED [P-readme].
- **Latest:** `0.1.9`, 2026-10-22. CONFIRMED [P-r].
  - 0.1.8 (2026-10-12) was "Fix 2.9 mixin crash".
  - 0.1.9 adds a startup check (PR #61). If the pack is older than **2.9.0-RC-2**, the game stops with an error screen. The check needs GT ≥ 5.09.54.192, NEI ≥ 2.8.148-GTNH, ModularUI2 ≥ 2.3.55 and GTNHLib ≥ 0.11.5. Without it, an older pack freezes on the loading screen.

  CONFIRMED [P-src].
  - 2.9.0 stable passes the check (GT .265, NEI 2.8.171, MUI2 2.3.94, GTNHLib 0.11.54). CONFIRMED (versions); runtime UNCERTAIN.
- **Keys:** F8 opens the planner. With the optional **Offscreen** 0.3-alpha jar, F7 opens it in a separate window. CONFIRMED [P-readme].
- **Open issues:** 3; #24 "NEI pages sometimes show the wrong count". No crash reports. CONFIRMED [P-i].

### 1.7 NEI recipe exporter
- 2.9.0 ships **NEI 2.8.171-GTNH**, and so does the daily manifest of 2026-11-01 10:39 UTC. CONFIRMED [M1][M3].
- So **the export instance needs a manual jar swap to 2.8.172-GTNH.** (A_recipe_data.md already found that 2.8.172 is a single commit on top of 2.8.171.)

---

## 2. OC "file mailbox" (managed HDD folder), OC 1.12.68-GTNH as shipped in 2.9.0

### 2.1 Where the files live
- A managed HDD (or RAID) is the folder `DimensionManager.getCurrentSaveRootDirectory()/opencomputers/<fs-node-address>/`. In single-player that is `.minecraft/saves/<world>/opencomputers/<address>/`.
- The folder is created (`mkdirs`) when the drive's environment is first created. CONFIRMED [OC-fs][OC-drv][OC-set] (`savePath = "opencomputers/"`).
- **Unmanaged drives cannot be used as a mailbox.** They are a gzipped `opencomputers/<address>.bin` sector image. CONFIRMED [OC-drive].
- **`/tmp` cannot be used either.** It is a RAM filesystem. CONFIRMED [OC-fs].

### 2.2 GTNH settings in 2.9.0
CONFIRMED [GT-occfg]:
- `bufferChanges=false`
- `hddSizes=[1024,2048,4096]` KB
- `fileCost=512`
- `maxHandles=16`
- `maxReadBuffer=2048` bytes per `read` call
- `tmpSize=64` KB

With `bufferChanges=false`, HDDs use `ReadWriteFileSystem`, which talks to the disk directly. CONFIRMED [OC-fs][OC-drv]. (Had it been true, the `BufferedFileSystem` would load the folder into memory and on save delete and rewrite files from memory, so external edits would be lost. CONFIRMED [OC-buf].)

### 2.3 Visibility and caching
- **No content cache.**
  - Every `open` for reading creates `new RandomAccessFile(file,"r").getChannel`.
  - `exists`, `size`, `isDirectory`, `lastModified` and `list` call `java.io.File` each time.
  - So a file written by Python is visible to OC on the **next** call or open. CONFIRMED [OC-fis].
- **OC writes reach the OS immediately.** Opening for write uses `RandomAccessFile(...,"rw")`. Mode `w` does `setLength(0)` (truncate in place), and each `write` goes straight to the file. A reader can therefore see a truncated or half-written file. CONFIRMED [OC-fos].
- **Space accounting is stale for external files.** `used` is recomputed only at load and close, and adjusted for OC's own operations. Files added from outside are not counted until then, so they can push past the drive size. Nothing in that code path errors. CONFIRMED [OC-cap].
- **Open handles survive save and load.** They are stored with path and position and reopened on load. A file that disappeared in the meantime is silently dropped. CONFIRMED [OC-isfs].

### 2.4 Call costs (how often to poll)
- **Cost per tick:** `open` (limit 4/tick), `read` (limit 15/tick), `write`, `exists`, `size`, `lastModified` and `close` are *direct* calls.
- **`list`, `makeDirectory`, `remove` and `rename` are *not* direct.** Each costs at least one server tick (50 ms).

CONFIRMED [OC-comp]. So poll with `exists` or `lastModified` on a fixed file name, not with `list`.

### 2.5 Windows locking
- **What OC holds open.** OC's handles are `java.io.RandomAccessFile`. On Windows the JDK opens them with share mode `FILE_SHARE_READ | FILE_SHARE_WRITE`, without `FILE_SHARE_DELETE`. This is the same in JDK 8u and current JDK. CONFIRMED [JDK-io][JDK8-io].
  - So while an OC program holds a file open, an external process **cannot delete it or rename over it.** A Python `os.replace(tmp, target)` uses `MoveFileExW(..., MOVEFILE_REPLACE_EXISTING)` [PY-posix] and will fail with PermissionError. Retry in that case. UNCERTAIN (Windows semantics inferred, not run).
- **How OC renames.** OC's `rename` is `Files.move(..., REPLACE_EXISTING)` [OC-fos]. On Windows, without `ATOMIC_MOVE`, the JDK does **`DeleteFile(target)` and then `MoveFileEx(src,target,0)`**. CONFIRMED [JDK-move].
  - **(a)** For a moment the target does not exist.
  - **(b)** If Python holds the target open, the delete fails and OC's `rename` returns `false`. CPython opens files with `_wopen` [PY-fileio], and I believe its default share mode lacks DELETE. UNCERTAIN (MS CRT behaviour, not checked against source).
- **Linux and macOS:** none of these locking problems apply.

### 2.6 Recommended protocol (design, from the facts above)
- **Two one-way files.**
  - OC → PC: `outbox.json`.
  - PC → OC: `inbox.json`.
- **Write to a temp file, then rename.**
  - OC writes `outbox.tmp`, closes it, then calls `fs.rename("outbox.tmp","outbox.json")`. It retries if that returns false.
  - Python writes `inbox.tmp`, flushes, fsyncs, closes, then calls `os.replace`. It retries on PermissionError.
- **Keep files open briefly.**
  - Readers open, read everything, and close at once.
  - OC reads in 2,048-byte chunks.
  - Python opens, reads and closes within one call.
- **Add a sequence number** inside each message, so a missed or repeated read is harmless.
- **Clean up** by having the receiver delete or rename the file to acknowledge it. The `.tmp` files are outside OC's capacity count until the next load.
- **Pause:** if the integrated server pauses, OC is paused too (C §4), so the mailbox stops. See §3.

---

## 3. Pause on lost focus in 1.7.10 GTNH 2.9.0

- **The vanilla toggle exists.** `GameSettings.pauseOnLostFocus` is a vanilla field, and `Minecraft.runTick` writes it in the F3 key handler.
  - Hodgepodge 2.7.221 hooks right after that field write and prints "Pause on lost focus: On/Off" in chat (`addToggleDebugMessage`, default **true**).
  - Its F3+Q help lists "F3 + P = Pause on lost focus".

  CONFIRMED [HP-dbg][HP-lang][HP-cfg].
- **options.txt key:** `pauseOnLostFocus:true|false`. UNCERTAIN: this is the standard vanilla key name, but I could not inspect vanilla bytecode (the Mojang download host is blocked here). F3+P saves to options.txt in vanilla. Same caveat.
- **What happens on focus loss.**
  - When the window is not active and `pauseOnLostFocus` is true (and not touchscreen with right mouse held), the game opens the in-game menu after more than 500 ms unfocused. That pauses the single-player integrated server.
  - Angelica 2.2.43 contains an exact copy of this check (`FpsReducer.onRenderSkipped`), which it runs when it skips the vanilla `EntityRenderer.updateCameraAndRender` while minimized. That implies the vanilla check lives in `updateCameraAndRender`.

  CONFIRMED that the copy exists [ANG-fps][ANG-icon]; the vanilla location is UNCERTAIN (inferred).
- **What GTNH mods change:**
  - **Hodgepodge `fixUnfocusedFullscreen` (default true) and ArchaicFix 0.8.2** both force `Display.isActive()` to return true **only inside `Minecraft.runGameLoop`**. This exists to stop fullscreen exiting on alt-tab. It does not touch the pause check, if that check is in `updateCameraAndRender` as above. CONFIRMED [HP-fs][HP-fixcfg][AF].
  - **lwjgl3ify 3.0.42**: `Display.isActive()` returns an SDL window-focus flag (cleared on `SDL_EVENT_WINDOW_FOCUS_LOST`). Its `EntityRenderer` mixin only changes mouse deltas. So focus detection still works, and pausing is not altered. CONFIRMED [L3-disp][L3-er].
  - **Angelica's FPS reducer** only caps FPS or volume when unfocused, and skips rendering when minimized. It keeps the pause behaviour even when minimized. Angelica's own flyby benchmark sets `pauseOnLostFocus=false` to avoid pausing. CONFIRMED [ANG-fps][ANG-fly].
- **Result:** with `pauseOnLostFocus:false` (or F3+P), alt-tab no longer pauses the game. **The Esc menu and other GUIs that pause the game still pause the server and OC.**
  - "Open to LAN" stops pausing: Hodgepodge's copy of vanilla logic treats `GuiIngameMenu && isSingleplayer() && !getPublic()` as paused [HP-snd]. UNCERTAIN; test it.

---

## 4. OCGlasses 1.6.9-GTNH and the Computronics Chat Box (2.9.0)

### 4.1 OCGlasses
- **Widget types:** 2D Rect, Dot, Text, Triangle, Quad and ItemIcon; 3D Cube, FloatingText, Triangle, Quad, Dot and Line.
- **Terminal methods:** `add*` for each widget, `removeObject`, `removeAll`, `getObjectCount`, `getBindPlayers`, `newUniqueKey`.
- **2.9-only methods:** `sendChatAs`, `sendMessageTo`.

CONFIRMED [OG-type][OG-te].

- **Text widgets** are drawn with Minecraft's `FontRenderer.drawString`.
  - Settable: position, colour, alpha, scale and rotation.
  - FloatingText adds 3D position, view distance and look-at. CONFIRMED [OG-text].
  - Whether § colour codes render: UNCERTAIN (vanilla FontRenderer supports them; not tested).
- **Update rate:** there is no fixed rate.
  - Every add, remove or setter immediately sends a `WidgetUpdatePacket` to every player bound to that terminal. CONFIRMED [OG-te][OG-srv].
  - The `add*` calls are `direct=true` with no explicit per-tick limit. CONFIRMED [OG-te]. In practice the limit is OC's call budget plus packet volume, so batch changes.
- **Power:** the terminal draws `widgets/10 × energyMultiplier` (default 1) OC energy per tick. CONFIRMED [OG-te][OG-cfg].
- **Range:** I found no distance or dimension check in `ServerSurface.sendToUUID`. UNCERTAIN in practice.
- **Input signals:** `glasses_on(user,w,h)`, `glasses_off`, `hud_click`, `hud_drag`, `hud_keyboard`, `block_interact`, `overlay_opened`, `overlay_closed`, `glasses_resized`, `chat_message`. CONFIRMED [OG-te].
- **Crafting (NHCore 2.9.99): HV Assembler** (`RecipeTier HV`, 15 s). CONFIRMED [NHC-asm].
  - Terminal: OC Geolyzer + OC item meta 43 + meta 38 + 144 mB molten Polyethylene.
  - Glasses: 2× Tier-3 Screen + OC item meta 10 + meta 13 + 72 mB Polyethylene.
  - The original recipes are removed. CONFIRMED [NHC-rm].
  - Which components metas 43/38/10/13 are: UNCERTAIN (probably T3 CPU/APU, high-tier RAM, T3 GPU and wireless card; mapping not verified).
- **ChatBox upgrade for the glasses** (needed for `sendChatAs`/`sendMessageTo`): its own recipe class `RecipeOpenGlassesChatBoxUpgrade` and NEI handler. CONFIRMED (source tree). Inputs not checked.

### 4.2 Computronics Chat Box (1.9.13-GTNH)
- **Recipe: LV, shaped crafting** in GT recipe mode (`gtRecipeMode=true` in GTNH) [CT-cfg]. NHCore does not override it. CONFIRMED [CT-gt5][NHC-grep].

  | Row | Items |
  |---|---|
  | Top | Glass Plate, LV Sensor, Glass Plate |
  | Middle | Basic Circuit, LV Emitter, Basic Circuit |
  | Bottom | 1× Tin Cable, LV Machine Hull, 1× Tin Cable |

- **Chat Upgrade** (`computronics.ocParts:1`): HV/2 Assembler from a Chat Box + Advanced Epoxy Board + 2× OC meta 25 + 2× SMD Transistor + 4× Aluminium Plate + solder. CONFIRMED [NHC-asm].
- **`say(text[,distance])`** is **not direct** (one tick per call).
  - Format: grey italic `[prefix] ` followed by grey text.
  - Range is the same dimension and within the distance, capped at `maxDistance=40`.
  - `prefix=ChatBox`; `makeMagical=false`; creative chat boxes are enabled.
  - `setName`, `getName`, `getDistance` and `setDistance` exist.

  CONFIRMED [CT-tile][CT-utils][CT-cfg].
- **`chat_message(user,msg)`** fires on `ServerChatEvent`, i.e. player chat, not commands. CONFIRMED [CT-tile].

---

## Sources
| ID | URL |
|---|---|
| M1 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/manifests/2.9.0.json |
| M2 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/manifests/2.9.0-RC-2.json |
| M3 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/manifests/daily.json |
| M4 | https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/manifests/2.8.4.json |
| git | `git for-each-ref` on tags and branches of each addon repo (clones made 2026-11-01) |
| TST-r | https://github.com/Nxer/Twist-Space-Technology-Mod/releases/tag/0.8.0-RC2.9 |
| TST-d | https://github.com/Nxer/Twist-Space-Technology-Mod/blob/0.8.0-RC2.9/dependencies.gradle |
| TST-i | https://github.com/Nxer/Twist-Space-Technology-Mod/issues/1316 , https://github.com/Nxer/Twist-Space-Technology-Mod/issues |
| GTNL-r | https://github.com/ABKQPO/GT-Not-Leisure/releases/tag/0.2.7-rc2.1 |
| GTNL-d | https://github.com/ABKQPO/GT-Not-Leisure/blob/0.2.7-rc2.1/dependencies.gradle |
| GTNL-c | https://github.com/ABKQPO/GT-Not-Leisure/commit/28007498 |
| GTNL-i | https://github.com/ABKQPO/GT-Not-Leisure/issues (incl. #575) |
| PH-r | https://github.com/reobf/Programmable-Hatches-Mod/releases/tag/v0.2.1p1-beta |
| PH-d | https://github.com/reobf/Programmable-Hatches-Mod/blob/v0.2.1p1-beta/dependencies.gradle |
| PH-i | https://github.com/reobf/Programmable-Hatches-Mod/issues/298 , https://github.com/reobf/Programmable-Hatches-Mod/issues |
| 123-r | https://github.com/CallmeSHaobe/123Technology/releases/tag/2.2.0.1 |
| 123-d | https://github.com/CallmeSHaobe/123Technology/blob/2.2.0.1/dependencies.gradle (and commit 812d2e0 on `bd_new`) |
| 123-i | https://github.com/CallmeSHaobe/123Technology/issues |
| WE-r | https://github.com/GTNewHorizons/worldedit-gtnh/releases |
| WE-30 | https://github.com/GTNewHorizons/worldedit-gtnh/issues/30 |
| WE-i | https://github.com/GTNewHorizons/worldedit-gtnh/issues |
| P-r | https://github.com/GTNewHorizons/PlanNH/releases |
| P-src | https://github.com/GTNewHorizons/PlanNH/blob/0.1.9/src/main/java/com/sbancuz/plannh/client/UnsupportedPackException.java |
| P-readme | https://github.com/GTNewHorizons/PlanNH/blob/master/README.md |
| P-i | https://github.com/GTNewHorizons/PlanNH/issues |
| OC-fs | https://github.com/GTNewHorizons/OpenComputers/blob/1.12.68-GTNH/src/main/scala/li/cil/oc/server/fs/FileSystem.scala |
| OC-fos | …/1.12.68-GTNH/src/main/scala/li/cil/oc/server/fs/FileOutputStreamFileSystem.scala |
| OC-fis | …/1.12.68-GTNH/src/main/scala/li/cil/oc/server/fs/FileInputStreamFileSystem.scala |
| OC-isfs | …/1.12.68-GTNH/src/main/scala/li/cil/oc/server/fs/InputStreamFileSystem.scala |
| OC-cap | …/1.12.68-GTNH/src/main/scala/li/cil/oc/server/fs/Capacity.scala |
| OC-buf | …/1.12.68-GTNH/src/main/scala/li/cil/oc/server/fs/Buffered.scala |
| OC-comp | …/1.12.68-GTNH/src/main/scala/li/cil/oc/server/component/FileSystem.scala |
| OC-drive | …/1.12.68-GTNH/src/main/scala/li/cil/oc/server/component/Drive.scala |
| OC-drv | …/1.12.68-GTNH/src/main/scala/li/cil/oc/integration/opencomputers/DriverFileSystem.scala |
| OC-set | …/1.12.68-GTNH/src/main/scala/li/cil/oc/Settings.scala |
| GT-occfg | https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/config/OpenComputers.cfg |
| JDK-io | https://github.com/openjdk/jdk/blob/master/src/java.base/windows/native/libjava/io_util_md.c |
| JDK8-io | https://github.com/openjdk/jdk8u/blob/master/jdk/src/windows/native/java/io/io_util_md.c |
| JDK-move | https://github.com/openjdk/jdk/blob/master/src/java.base/windows/classes/sun/nio/fs/WindowsFileCopy.java |
| PY-posix | https://github.com/python/cpython/blob/main/Modules/posixmodule.c |
| PY-fileio | https://github.com/python/cpython/blob/main/Modules/_io/fileio.c |
| HP-dbg | https://github.com/GTNewHorizons/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/mixins/early/minecraft/MixinMinecraft_ToggleDebugMessage.java |
| HP-fs | …/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/mixins/early/minecraft/MixinMinecraft_UnfocusedFullscreen.java |
| HP-cfg / HP-fixcfg | …/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/config/TweaksConfig.java , …/config/FixesConfig.java |
| HP-lang | …/Hodgepodge/blob/2.7.221/src/main/resources/assets/hodgepodge/lang/en_US.lang |
| HP-snd | …/Hodgepodge/blob/2.7.221/src/main/java/com/mitchej123/hodgepodge/mixins/early/minecraft/MixinMinecraft_FixDuplicateSounds.java |
| AF | https://github.com/embeddedt/ArchaicFix/blob/0.8.2/src/main/java/org/embeddedt/archaicfix/mixins/client/core/MixinMinecraft.java |
| ANG-icon | https://github.com/GTNewHorizons/Angelica/blob/2.2.43/src/mixin/java/com/gtnewhorizons/angelica/mixins/early/angelica/MixinMinecraft_IconifyGuard.java |
| ANG-fps | https://github.com/GTNewHorizons/Angelica/blob/2.2.43/src/main/java/com/gtnewhorizons/angelica/rendering/FpsReducer.java |
| ANG-fly | https://github.com/GTNewHorizons/Angelica/blob/2.2.43/src/main/java/com/gtnewhorizons/angelica/debug/flyby/FlybyRunner.java |
| L3-disp | https://github.com/GTNewHorizons/lwjgl3ify/blob/3.0.42/src/main/java/org/lwjglx/opengl/Display.java |
| L3-er | https://github.com/GTNewHorizons/lwjgl3ify/blob/3.0.42/src/main/java/me/eigenraven/lwjgl3ify/mixins/early/game/MixinEntityRenderer.java |
| OG-type | https://github.com/GTNewHorizons/OCGlasses/blob/1.6.9-GTNH/src/main/java/com/bymarcin/openglasses/surface/WidgetType.java |
| OG-te | https://github.com/GTNewHorizons/OCGlasses/blob/1.6.9-GTNH/src/main/java/com/bymarcin/openglasses/tileentity/OpenGlassesTerminalTileEntity.java |
| OG-srv | …/OCGlasses/blob/1.6.9-GTNH/src/main/java/com/bymarcin/openglasses/surface/ServerSurface.java |
| OG-text | …/OCGlasses/blob/1.6.9-GTNH/src/main/java/com/bymarcin/openglasses/surface/widgets/component/face/Text.java |
| OG-cfg | …/OCGlasses/blob/1.6.9-GTNH/src/main/java/com/bymarcin/openglasses/OpenGlasses.java |
| NHC-asm | https://github.com/GTNewHorizons/NewHorizonsCoreMod/blob/2.9.99/src/main/java/com/dreammaster/gthandler/recipes/AssemblerRecipes.java |
| NHC-rm | https://github.com/GTNewHorizons/NewHorizonsCoreMod/blob/2.9.99/src/main/java/com/dreammaster/recipes/RecipeRemover.java |
| NHC-grep | grep of NewHorizonsCoreMod 2.9.99 for `chatBox` (only use: Chat Upgrade input) |
| CT-gt5 | https://github.com/GTNewHorizons/Computronics/blob/1.9.13-GTNH/src/main/java/pl/asie/computronics/integration/gregtech/gregtech5/GregTech5Recipes.java |
| CT-tile | https://github.com/GTNewHorizons/Computronics/blob/1.9.13-GTNH/src/main/java/pl/asie/computronics/tile/TileChatBox.java |
| CT-utils | https://github.com/GTNewHorizons/Computronics/blob/1.9.13-GTNH/src/main/java/pl/asie/computronics/util/ChatBoxUtils.java |
| CT-cfg | https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/blob/2.9.0/config/computronics.cfg |
