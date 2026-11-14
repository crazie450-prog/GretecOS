# Machine events and the census (WO-6.2): the Lab check

What WO-6.2 adds (D260–D262) and the check Clayton runs on the PC (V26). Claude Code built and
tested it in GregtecLink's server harness (`./gradlew runServer -Pharness -PharnessListener`: a
fake player places 10 GT machine kinds, the EBF and a hatch among them, and breaks four) and on
fake region files. Everything below is **GTNH-Lab only**; Play is not touched by this WO.

```powershell
$gos  = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"
$app  = "C:\GregtecOS-app\current"
$lab  = "C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft"
$ProgressPreference = 'SilentlyContinue'
```

## How it works

- **GregtecLink's listener module** (`B:listener=true` in the instance's `config/gregteclink.cfg`;
  refused when `export` is on, so never in Export) notes each player placement and break of GT's
  machine block and, at the end of that server tick, checks the world: a GT machine now stands
  there (`machine_built`) or is gone (`machine_removed`). It appends one line per event to
  `.minecraft\gregteclink\events\machines.jsonl` with the machine's `mID` (`mte:<id>`) and its
  item key, GT's machine block registry name + meta (contract:
  `docs/contracts/gregteclink-events-v1.md`). It only reads the world (never loads a chunk, never
  creates a tile entity) and writes nothing else.
- **The watchers** (WO-6.1) tail that file and read the world's region files after each save:
  the **census** reads only the tile entities of the chunks whose save time changed (GT's
  `BaseMetaTileEntity` with `mID`, `mActive`, `mWasShutdown`), so it also sees machines placed or
  removed without a player (explosions, other mods). Both go to the core as events.
- **My base** counts each machine kind by D211's precedence: your manual count, else the census,
  else the events. Only **Play's** watchers feed My base; Lab's events and census show in the event
  feed and in `gos watch census`, never as your base (D261).

## 1. Install the listener in Lab (once per deploy)

Since WO-8.1 this is `scripts/install_gregteclink.py` (D287, `gregteclink_client.md` B.1-B.2). It
copies the deployed jar (D98: `gos deploy` keeps it in `mod-dist`, with its SHA256 in
`release.json`) and writes Lab's `config\gregteclink.cfg` with the listener and client modules on
and export off, plus the link's token. Lab's default `[sync.lab] exclude` already keeps
`mods/gregteclink*` and `config/gregteclink*` (if your gos.toml has its own `[sync.lab] exclude`,
it must keep both). Close every GTNH client, then:

```powershell
& "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance lab
& "$app\.venv\Scripts\python.exe" "$app\scripts\install_gregteclink.py" --instance lab --apply
```

If you already ran it for `gregteclink_client.md` B, it is done: don't run it again, and don't
write Lab's `gregteclink.cfg` by hand (an older version of this step did, with `B:client=false`,
which turns WO-8.1's client module off).

## 2. The check (V26): 10 machines placed and broken

1. Launch GTNH-Lab and open `lab-structures` (superflat creative). `latest.log` shows
   `listener module on: GT machine place/break events go to gregteclink/events`.
2. Place 10 different GT machines, among them a multiblock controller (e.g. the Electric Blast
   Furnace) and a hatch (e.g. an input bus). Note each one's NEI id: hover it with F3+H on, or
   NEI's item id line; the GT machine item is `gregtech:gt.blockmachines` with damage = the id.
3. Break four of them, the controller and the hatch among them.
4. `Get-Content "$lab\gregteclink\events\machines.jsonl"`: 10 `machine_built` lines then 4
   `machine_removed` lines, each `mte_id` (= `meta`) the id you noted, `registry`
   `gregtech:gt.blockmachines`, the positions where you put them. No line has an `id` field.
5. Press Esc (the game saves), then `& $gos watch census --instance lab --world lab-structures`:
   the 6 machines still standing, by `mte:<id>` and name, plus whatever else the world already had.
   Compare with `& $gos watch census --instance lab --world lab-structures --json` for positions.
6. Optional, the census catching what the listener can't: blow one machine up (TNT in creative)
   or `/setblock ~ ~ ~ air` on one, Esc, run the census again: it is gone from the census and no
   `machine_removed` line was written for it.

With GOS running and the watchers started by Lab's launch (D259), the same events show in the
dashboard's event feed (`GET /api/game/events?kind=machine_built`) within a few seconds, and a
`machine_census` event follows each save. The census part needs `[paths] lab_world =
"lab-structures"` in gos.toml when Lab is launched (the watchers' census reads only that world);
steps 4 and 5 above don't.

Record the result (pass, or what differed) in the PR / VERIFY V26.

## 3. Turning it off

Set `B:listener=false` in Lab's `config\gregteclink.cfg` (or delete the file). The events file
stays until you delete `$lab\gregteclink\events`.
