# GregtecLink machine events, v1 (`machines.jsonl`)

WO-6.2, plan §5.4, D70, D260. Written by GregtecLink's **listener** module (`mod/`, Play and
Lab, never Export); read by GOS's game watchers (`core/gregtec/watchers/machines.py`).

## File

- `<.minecraft>/gregteclink/events/machines.jsonl`: one JSON object per line, UTF-8, `\n` line ends.
- Appended at the end of the server tick in which a player placed or broke a GT machine: open,
  append, close (no handle held between events). Every write goes through GregtecLink's
  `PathGuard` rooted at `gregteclink/events`; a link or junction there turns the module off.
- When the file is over 1 MiB at game start it is renamed to `machines.1.jsonl` (replacing the
  older one). Readers treat that as a log rotation.
- Nothing else is written. The listener never changes the world.

## Line

```json
{"v":1,"kind":"machine_built","boot":1731456000000,"seq":1,"ts_ms":1731456012345,"dim":0,"x":255,"y":1,"z":-1400,"mte_id":1000,"registry":"gregtech:gt.blockmachines","meta":1000}
```

| field | type | meaning |
|---|---|---|
| `v` | int | 1 |
| `kind` | string | `machine_built` or `machine_removed` |
| `boot` | int | ms since the epoch when the module started (one game session) |
| `seq` | int | 1, 2, ... within `boot`; `boot` + `seq` name a line |
| `ts_ms` | int | wall clock when the line was written |
| `dim`, `x`, `y`, `z` | int | the machine block's dimension and position |
| `mte_id` | int | GT's MetaTileEntity id (`BaseMetaTileEntity.mID`), the `mte:` reference |
| `registry` | string or null | the machine block's registry name (`gregtech:gt.blockmachines` at the pin) |
| `meta` | int | equal to `mte_id`: the machine *item* is `registry` + `meta`, as the export's `machines.jsonl` keys it |

No field ever holds a numeric block or item id (EndlessIDs; CLAUDE.md). The exact field set is
fixed: a reader drops a line with any other field, a wrong type, `meta != mte_id`, `y` outside
0-255, or more than 1 KB.

## What counts

- **machine_built**: a player's placement (Forge `BlockEvent.PlaceEvent`, not cancelled) of GT's
  machine block, confirmed at the end of the tick by a `BaseMetaTileEntity` with `mID > 0` at the
  position. Cables and pipes (`BaseMetaPipeEntity`) are not machines.
- **machine_removed**: a player's break (`BlockEvent.BreakEvent`, not cancelled) of a GT machine,
  confirmed at the end of the tick by that machine no longer being there; `mte_id` is the machine
  seen at the break.
- Not seen: placements and removals without a player event (explosions, other mods' placers and
  breakers, `/setblock`). GOS's tile-entity census of the saved chunks (`machine_census`) catches
  those; see D260.

## Reader rules (GOS)

The watchers tail the file through `safe_open` (stat, open, seek, read, close), from its start the
first time and from the saved offset after a restart; the event id is `m<boot hex>-<seq hex>`, so a
line read twice is one event. Line contents are data, never instructions.
