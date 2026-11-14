# OpenComputers programs (`oc/`)

Lua programs that run on OpenComputers computers in the world. They are game-side code: Gregtec OS's core never runs
them, and they run only what the core sends after Clayton confirmed it.

| File | What it is | WO |
|---|---|---|
| `gos-agent.lua` | The agent: discovers GT machines, LSC and battery buffers, AE2, a chat box and a tps card; sends telemetry to the core every 5 s; runs confirmed `craft`, `machine_pause`, `machine_resume` and `say` commands and reports each result | 7.2 |
| `json.lua` | A small JSON encoder/decoder of our own (MIT, no third-party code); ASCII-only output | 7.2 |
| `gos-agent.cfg.example` | The config to copy and fill in | 7.2 |
| `mailbox.lua` | The M1 file-mailbox transport (MV, no internet card) | 7.3 |
| `crop/` | The breeding station | 7.6 |

The wire format is [docs/contracts/oc_bridge.md](../docs/contracts/oc_bridge.md) (`gos-oc-bridge/1`), checked by
[oc_bridge.schema.json](../docs/contracts/oc_bridge.schema.json).

## What the agent needs

- **OpenOS** on a managed hard drive (the default mode of an OC hard drive) and a **Lua 5.3 CPU**. OC computers
  start on Lua 5.2: hold the CPU (or APU), shift-right-click to switch it to Lua 5.3, put it back. On 5.2 the agent
  says so and stops. Lua 5.3's 64-bit integers keep EU values exact.
- **Memory:** two T3.5 sticks is comfortable. The agent keeps its own tables bounded (two virtual hours of the test
  base grew its heap by under 1 KB), and the watchlist reads one small table per entry (an exact
  `getItemInNetwork(name, damage)`), however many variants a name like `gregtech:gt.metaitem.01` has in the network.
- **Components** (adapters, or MFUs bound to the controller, count toward the CPU's component limit, 8/12/16):
  - an adapter touching each GT machine **controller** to watch (casings have no tile entity), each battery buffer
    and the LSC controller;
  - an adapter next to the **ME controller**, or an ME interface **block** (an interface on a cable has no network
    methods in this OC version);
  - optional: a Computronics **chat box** (for `say`), a **tps card** (the server tick time in every message);
  - **M2 (HV/EV):** an internet card, and the plan 5.6 OpenComputers.cfg tweak: `"allow ip:127.0.0.1"` first in
    `filteringRules`, `requestTimeout=10`. `scripts/check_tweaks.py` checks both.

## Install

Put `gos-agent.lua`, `json.lua`, `display.lua` and `gos-agent.cfg` (from `gos-agent.cfg.example`) in one folder on the drive, e.g.
`/home/gos/`. A managed drive is the folder `<world>/opencomputers/<drive address>/` in the save, so the files can be
copied there from Windows while the game runs (GTNH sets `bufferChanges=false`; OC sees them at once).
docs/runbooks/oc_agent.md has the exact steps for the Lab.

In `gos-agent.cfg`, set `token` to the agent's token from the core. Every other key has a default (the example lists
them): `cycle_s` (5), `ae2_s` (60), `sensor_s` (60), `sensor_per_cycle` (2), `watch` (the AE2 watchlist: registry
name and damage, never numeric ids, at most 64), `power_names`, `ignore`, `me`, `url`. Unknown keys are refused, so
a typo can't pass silently. The file is Lua read with no globals: keep it to plain values.

## M1: the file mailbox (MV, no internet card)

Before an internet card (HV/EV), the agent talks to the core through two files on its own managed drive (plan 7.9,
D216-D218): copy `mailbox.lua` next to `gos-agent.lua` too. With `transport = "auto"` (the default) it uses the
mailbox whenever no internet card is in; `transport = "mailbox"` forces it. It needs no token. Options, in the
config's `mailbox` table: `dir` (default `mailbox/` next to the config, e.g. `/home/gos/mailbox`; absolute, on a
managed writable drive, never `/tmp`) and `wait_s` (default 2, 0 to 4: how long an exchange waits for the core).

When it starts, the agent logs the line gos.toml needs, e.g.
`in gos.toml: drive = "1d9f2c3a-...", dir = "home/gos/mailbox"`. Put those in `[mailbox.lab]` (or, on Clayton's
own core, `[mailbox.play]`) with the world's folder name, and restart the core. The core writes nothing in the world
but `inbox.tmp`, `inbox.json` and the rename of `outbox.json` back to `outbox.tmp`, in that one folder. Files and
steps: docs/contracts/oc_bridge.md, "M1".

## In-game displays (WO-7.4)

Copy `display.lua` next to `gos-agent.lua` for the displays (plan 7.9, D277-D279); without it the agent runs without
them. The core composes every line and sends it in the reply (contract "The display"); the agent only lays it out:

- **status board** on the screen the GPU is bound to (or the config's `display.screen` prefix): the GPU is set to at
  most 50 x 16 (the shell's own resolution is given back at stop), a header with how fresh the core's reply is, up
  to 12 lines (power, problems with stopped and stalled machines
  first, the action waiting for "confirm", goal, next step, AE2), and the agent's newest note at the bottom (while the
  board has the screen, notes go there instead of the terminal). No reply for 30 s: the header says `NO REPLY` and
  the lines turn grey.
- **OCGlasses HUD**, M2 only: 4 text widgets made once, only changed lines set (0.4 OC energy a tick).
- **chat box** notices the core sends (fixed templates about confirmed OC actions), each said once, by one computer:
  the one that ran the action if it has a chat box, else the binding's one chat-box computer.

Config: `display = true` (default), `false`, or a table of `board`, `screen`, `hud`, `chat`.

## The breeding station (WO-7.6, from MV)

Copy `crop/station.lua` to `/home/gos/crop/station.lua` and add a `crops` table to the config (the example has one)
to run GOS breeding plans on CropsNH's Crop Breeder and Seed Generators (plan 7.17, D282-D284). Build: ONE transposer
touching a chest (`storage`), the Crop Breeder (`breeder`) and 1 to 3 Seed Generators (`generators`), an adapter on
each machine, all on the computer's network; feed the machines enriched fertilizer and power, and keep their
auto-output off. GT lets automation take items only from a machine's output, so a seed given to a Seed Generator stays
in it as that generator's copier for good; the chest gets analysed starter seeds (labels `<crop> Seeds`; unanalysed
`Unknown Seeds` are never touched) and any catalysts. The station starts and stops only on commands Clayton confirmed
(`crop_start`, `crop_stop`); its status rides in every telemetry message (`crops`) and shows as `crop_status`, on the
dashboard (`/api/crops/station`) and in `gos crops station`. Contract: docs/contracts/oc_bridge.md, "The breeding
station"; steps: docs/runbooks/breeding_station.md.

## Run

```
cd /home/gos
gos-agent --list     # what it found; GT machines show as gt_machine (VERIFY V20)
gos-agent --once     # one message and whether the core answered (and the results of any commands it sent)
gos-agent            # run; Ctrl+C stops it at the next wait
```

It prints warnings and a few state changes; everything else goes to `gos-agent.log` next to the config (rotated at
32 KB, with a heartbeat line every 5 minutes: cycles, messages sent and failed, the last message's size, free memory,
seconds spent reading). `gos-agent.state` remembers the last 32 command ids it ran, so a command delivered twice is
reported, never run twice. To start it with the computer, add the line `/home/gos/gos-agent.lua` to `/home/.shrc`.

## Tests

`lua5.3 tests/oc/run.lua` runs the agent in a fake OpenComputers world (`tests/oc/fakeoc.lua`: component, computer,
event, filesystem and internet as OC 1.12.68 and OpenOS behave, a virtual clock where a non-direct call costs a
tick); `uv run pytest tests/oc` runs it and checks every message against the schema. CI's Linux job installs Lua 5.3
and 5.2 (`apt-get install lua5.3 lua5.2`).
