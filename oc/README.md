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
  base grew its heap by under 1 KB); a watchlist name like `gregtech:gt.metaitem.01` makes one large temporary
  table every minute, because `getItemsInNetworkById` returns every damage value of a name.
- **Components** (adapters, or MFUs bound to the controller, count toward the CPU's component limit, 8/12/16):
  - an adapter touching each GT machine **controller** to watch (casings have no tile entity), each battery buffer
    and the LSC controller;
  - an adapter next to the **ME controller**, or an ME interface **block** (an interface on a cable has no network
    methods in this OC version);
  - optional: a Computronics **chat box** (for `say`), a **tps card** (the server tick time in every message);
  - **M2 (HV/EV):** an internet card, and the plan 5.6 OpenComputers.cfg tweak: `"allow ip:127.0.0.1"` first in
    `filteringRules`, `requestTimeout=10`. `scripts/check_tweaks.py` checks both.

## Install

Put `gos-agent.lua`, `json.lua` and `gos-agent.cfg` (from `gos-agent.cfg.example`) in one folder on the drive, e.g.
`/home/gos/`. A managed drive is the folder `<world>/opencomputers/<drive address>/` in the save, so the files can be
copied there from Windows while the game runs (GTNH sets `bufferChanges=false`; OC sees them at once).
docs/runbooks/oc_agent.md has the exact steps for the Lab.

In `gos-agent.cfg`, set `token` to the agent's token from the core. Every other key has a default (the example lists
them): `cycle_s` (5), `ae2_s` (60), `sensor_s` (60), `sensor_per_cycle` (2), `watch` (the AE2 watchlist: registry
name and damage, never numeric ids), `power_names`, `ignore`, `me`, `url`. Unknown keys are refused, so a typo
can't pass silently.

## Run

```
cd /home/gos
gos-agent --list     # what it found; GT machines show as gt_machine (VERIFY V20)
gos-agent --once     # one message to the core and its answer
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
