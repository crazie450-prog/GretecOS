# OpenComputers bridge contract: `gos-oc-bridge/1`

What the OpenComputers agent (`oc/gos-agent.lua`, WO-7.2) sends to the core and what the core (WO-7.1, `/oc/telemetry`)
answers. Plan §7.9, §8 rows 7.1–7.3; research [C§1]–[C§5], [F§2]; decisions D24, D25, D46, D59, D213–D215.

**Principle: OC sends raw readings; the core interprets them.** The agent never parses sensor lines, never computes
a Problems count, an EU rate or a percentage, and never invents a value a driver didn't give: a reading that failed
is left out and reported in `errors`. Every reading carries the OC computer's `uptime`, so the core can tell a fresh
value from a stale one; gaps (a paused game, a broken link) are marked stale, never extrapolated (plan §7.9).

- **Schema:** [`oc_bridge.schema.json`](oc_bridge.schema.json) (JSON Schema 2020-12). A message is valid against
  `#/$defs/telemetry`, a reply against `#/$defs/reply`. Both are strict (`additionalProperties: false`): a new field
  is a contract change, made in the schema, this document and the agent together.
- **Examples:** [`tests/fixtures/oc/`](../../tests/fixtures/oc/): `telemetry-first.json` (the first message of the
  test base: an EBF, a battery buffer, an LSC, an ME controller, a chat box, a tps card), `telemetry-results.json` (a
  message with command results and a craft in progress), `reply-commands.json`, `reply-empty.json`,
  `reply-refused.json`. `tests/oc/test_oc_agent.py` checks they validate and that the agent still sends exactly them.
- **Transports:** M2, HTTP (this WO), and M1, the file mailbox (WO-7.3), carry the same two documents.

## M2: HTTP

| | |
|---|---|
| Request | `POST http://127.0.0.1:<port>/oc/telemetry`, `<port>` = gos.toml `[core] port` (8765). Always the literal `127.0.0.1`: OpenComputers.cfg's first filtering rule allows only it, and `localhost` may resolve to `::1` (plan §5.6, Appendix C). The agent refuses a `localhost` URL in its config. |
| Headers | `Content-Type: application/json` and `Authorization: Bearer <token>` (custom headers need OpenComputers.cfg's `enableHttpHeaders=true`, GTNH's default [C§1.1]). OC adds its own `User-Agent` and `Host: 127.0.0.1:<port>`; there is no `Origin`, cookie or `Sec-Fetch-*` header, so D117's Host check passes as is. |
| Body | One telemetry document. JSON, **ASCII only**: the agent writes every non-ASCII character as `\uXXXX` (a surrogate pair above U+FFFF) and an invalid UTF-8 byte as `�`, because OC writes the POST body with the JVM's default charset (an `OutputStreamWriter` without a charset in OC 1.12.68's `InternetCard.scala`), which isn't UTF-8 on every Java build. Object keys are sorted. |
| Reply | **Always HTTP 200** with one reply document, `Content-Type: application/json`, **at most 2048 bytes**. OC can't read the body of a non-2xx response (its `getInputStream` throws, [C§1.4]), so a refusal is `200` with `ok: false`. 2048 bytes is what OC returns from one read in GTNH's config (`maxReadBuffer`). The agent reads 2048-byte chunks until the stream ends and refuses a reply over 8192 bytes (`reply_max`) as a failed exchange, executing nothing from it. |
| Time | The agent gives one exchange 12 s (`request_timeout_s`) from the request to the last byte, then closes it and counts a failure. OC's own `requestTimeout=10` (plan §5.6) bounds the read. |
| Connections | The agent closes its handle on every path, success or not: OC allows 4 open connections per internet card, HTTP requests included [C§1.4]. |
| Token | The bearer token is a client token for this agent (D117's named client tokens, e.g. `gos token new oc`; WO-7.1 decides the name and how the core checks it). It sits in plain text in the agent's config on the OC drive, inside the world's save, which gos-dev can read (plan §4.6, D75: Lab modify, Play read only), so it must grant nothing beyond `/oc/telemetry` (D117's client tokens can also read health and usage; WO-7.1 scopes this one to `/oc/telemetry` alone). Whoever holds it can pose as the agent: take the commands waiting for it (they then never run) or post false readings and results. So the core treats everything posted with it as data (shown, alerted on, recorded as a command's outcome) and never starts an action because of it; `gos token new` replaces a token that leaked, `gos token revoke` ends it. |

A failed exchange (refused connection, a non-2xx code, a timeout, a reply that isn't one JSON object, an
`ok: false` reply) leaves everything that was waiting for an ack waiting (see Acknowledgement) and is retried with
backoff. The one exception is `ok: false` with `err: "bad_payload"`: resending what the core can't read would be
refused forever, so the agent drops everything waiting for an ack (results, errors, finished crafts, sensor lines,
the watchlist), says so on the OC screen and in its log, and its next message is a fresh snapshot carrying the
refusal as an error. The agent never builds a message the schema refuses on purpose (see Size and limits); this is
the way back if one slips through. Results dropped this way never reach the core, so the commands they answered end
in the core's timeout (see Delivery).

## M1: the file mailbox (WO-7.3)

The same documents travel as `outbox.json` (OC → core) and `inbox.json` (core → OC) on a managed drive (plan §7.9,
[F§2.6]). The agent sees M1 as a transport with the same interface as HTTP, which WO-7.3's `oc/mailbox.lua` provides:

```lua
local mailbox = { new = function(cfg, deps) ... end }  -- deps.json, deps.log(level, msg)
-- the transport it returns:
t.name          -- "mailbox"
t.implicit_ack  -- false: a reply does not mean the core read this message; inbox.json says what it read
t:exchange(body, seq)  -- write one message out, return the core's reply table (or nil, err)
t:close()
```

With no `inbox.json` waiting, `exchange` returns `{v = 1, ok = true, cmds = {}}` with no `ack`. A core reply in
`inbox.json` carries `ack`, the highest `seq` it has read. The config's `mailbox` table holds M1's own options; with
`transport = "auto"` the agent uses HTTP when an internet card is in and M1 otherwise.

## Cadence

| What | When | Cost (non-direct calls take one server tick each, direct calls none) |
|---|---|---|
| Power: battery buffers (`gt_batterybuffer`) | every message | direct `getEUStored`, `getEUMaxStored`, `getEUInputAverage`, `getEUOutputAverage` |
| Power: storage (`gt_machine` named in `power_names`, default the LSC `multimachine.supercapacitor`) | every message | `getStoredEUString` and `getEUCapacityString`, 1 tick each; the averages direct |
| Machines (every other `gt_machine`) | every message | direct `isMachineActive`, `isWorkAllowed`, `hasWork`, `getWorkProgress`, `getWorkMaxProgress`, `getEUStored`, `getEUMaxStored` |
| Sensor lines | each entry when its lines are `sensor_s` (60 s) old, oldest first, at most `sensor_per_cycle` (2) a message | `getSensorInformation`, 1 tick each |
| AE2 crafting CPUs | every message | `getCpus`, 1 tick |
| AE2 watchlist | every `ae2_s` (60 s) | one `getItemInNetwork(name, damage)` per watched entry, 1 tick each: an exact lookup that returns one small table. **Never** `getItemsInNetworkById` (it matches the item only, so `gregtech:gt.metaitem.01` returns every variant the network holds, converted on the server thread and held in the computer's memory at once), `getItemsInNetwork` or `allItems` [C§2.5] (D213) |
| Tick time (`tick_ms`) | every message, with a tps card in | `getOverallTickTime`, 1 tick |
| Crafts in progress | every message | `isComputing`/`hasFailed` while AE2 plans (and `isDone` when `hasFailed` answers "no link", see Craft), then `isDone`/`isCanceled`, 1 tick each |
| The exchange | every message | `request`, `finishConnect`, `read`: 3 to 5 ticks |

- A message goes every `cycle_s` (5 s; 5 to 10 allowed), measured from the start of one cycle to the next. After a
  reply with commands the next one goes 1 s later, to report their results; a reply's `poll_s` asks for the next
  one sooner (1 to `cycle_s`).
- After a failed exchange the next try waits 5, 10, 20, 40, then 60 s (`backoff_max_s`); after `unauthorized`,
  `unsupported_version` or a reply with another `v`, 60 s at once. While the link is down the agent skips sensor and
  watchlist reads; power and machines are still read for the next try.
- In the test base (an EBF, a battery buffer, an LSC, AE2 with a 4-entry watchlist, a tps card) a cycle costs about
  9.6 server ticks on average (`tests/oc/test_cadence.lua`).
- Discovery reruns on `component_added` / `component_removed` and every `discover_s` (300 s), with direct calls only.

## The telemetry document

Every message is a full snapshot of power, machines, AE2 CPUs and crafts, so a missed one is harmless. Sensor lines
and the watchlist are sent when read and repeated until the core acknowledges a message that carried them.

**Size and limits.** Every list is bounded, so a message is too: at most 32 power and 64 machine entries
(`max_power` 16 and `max_machines` 32 by default), 32 crafting CPUs (more are left out and reported in `errors`
once a discovery), 16 crafts, 16 results, 8 errors, 64 watchlist items (`max_watch`, also the default) and 8 KB of
sensor lines (see Sensor lines). A typical message is 2 to 8 KB. The agent keeps a message under **96 KB** (98,304
bytes) when it can: a message over that goes without its sensor lines and, if still over, without the watchlist too;
what it leaves out waits for a later message, and it says so in `errors` (`a message would be over 98304 bytes:
sensor lines wait for a smaller one`). What remains, the snapshot and the records waiting for an ack, is bounded
by the limits above. In `tests/oc/test_payload.lua`, with every limit at the config's maximum: game-like text
acknowledged as the core does, 43,823 bytes at most, nothing left out; game-like text with nothing acknowledged,
97,597 bytes at most, sensor lines and watchlist waiting; every string at its limit in its longest JSON form (6
bytes for each byte), 155,485 bytes. **The core accepts bodies up to 256 KB** (262,144 bytes). Readings outside
the schema's ranges are left out, never sent: a name over 128 bytes, a negative stored EU, a decimal string over 80
digits, a watchlist size that isn't a whole number ≥ 0; a component type that isn't letters, digits and `_` isn't
counted in `components`.

| Field | Type | Meaning |
|---|---|---|
| `v` | 1 | Contract version |
| `kind` | `"telemetry"` | |
| `agent` | string | The agent's version, `0.1.0` |
| `boot` | 8 hex digits | Random per agent start. `seq` starts again at 1 with a new `boot`; anything the core tracks per agent run (acks, crafts) belongs to one `boot` |
| `seq` | integer ≥ 1 | Message number within this `boot` |
| `uptime` | seconds | `computer.uptime()` when the message was built. In-game time: it stops while the game is paused |
| `transport` | `"http"` \| `"mailbox"` | |
| `cycle_s` | 5–10 | The configured cadence |
| `sample_s` | seconds | How long this message's readings took (the ticks the agent waited for) |
| `computer` | object | `address` (the OC computer), `arch` (`Lua 5.3`), `mem_free` and `mem_total` (bytes), `energy` and `energy_max` (the computer's own buffer) |
| `tick_ms` | number, optional | The server's mean tick time in ms from a tps card (`getOverallTickTime`); TPS = min(20, 1000 / `tick_ms`) |
| `components` | object | How many components of each type the computer sees, by type, ignored ones included |
| `power` | array | Power entries, below |
| `machines` | array | Machine entries, below |
| `ae2` | object, optional | Present when an ME controller or ME interface block is found |
| `crafts` | array | Crafts the agent started and is tracking, below |
| `results` | array | Command results not yet acknowledged, below |
| `errors` | array | Errors not yet acknowledged, below |

**Power entry** (`power[]`): `addr` (the component address), `type` (`gt_batterybuffer`, `gt_machine`, or `lsc` when
the LSC shows without Computronics), `kind`, `name` (GT's meta tile entity name from `getName()`, when it answered),
`xyz` (`getCoordinates()`: GTNH enables machine coordinates), `eu_in` and `eu_out` (the block's average EU/t in and
out, Computronics' `getEUInputAverage`/`getEUOutputAverage`), and:
- `kind: "battery_buffer"`: `eu`, `eu_max`, the buffer's own store. Batteries in its slots are only in its sensor
  lines (`GT5U.infodata.battery_buffer.stored_items`, the total with them; `getBatteryCharge` costs a tick per slot).
- `kind: "storage"`: `eu_str`, `eu_max_str`, exact decimal strings (the LSC's driver returns its BigInteger; beyond
  64 bits these are the only exact values). Never `eu`/`eu_max`.
- `sensor` and `sensor_at` when fresh lines are waiting for an ack (see Sensor lines).

**Machine entry** (`machines[]`): `addr`, `type` (`gt_machine`), `name`, `xyz`, `active` (`isMachineActive`),
`allowed` (`isWorkAllowed`), `work` (`hasWork`: it has a job), `progress` and `progress_max` (ticks), `eu` and
`eu_max` (the controller's own EU store; 0 on most multiblocks, whose energy is in hatches), `sensor`, `sensor_at`.
A field whose call failed is left out.

**`ae2`**: `addr`, `type` (`me_controller` or `me_interface`), `cpus` (the crafting CPUs, at most 32: `name`,
`storage` in bytes, `coprocessors`, `busy`), and when the watchlist was read and isn't acknowledged yet, `items` and
`items_at`.
`items` has one entry per watched (`name`, `damage`) in config order, from one exact AE2 lookup
(`getItemInNetwork(name, damage)`): `size` is how many of the stack with that registry name and damage and **no
NBT** the network holds (OC sends an empty tag, which AE2 reads as none; a variant with NBT is another item and
isn't counted), `craftable` whether AE2 has a pattern for that stack. An entry with `size` 0 was looked up and is
not stored. A watched name the game doesn't know also gives 0. A lookup that failed is left out of `items` and
reported in `errors`. The core resolves names through the DB by registry name and damage with no NBT, the same key.

**Craft** (`crafts[]`): `id` (the command's id), `name`, `damage`, `amount`, `state`, `err` (why it failed), `at`
(uptime of the last change). `state` goes `computing` (AE2 is planning) → `crafting` (submitted) → `done` or
`canceled`, or `computing` → `failed`. A craft is listed while it runs and once more in its final state, until a
message that carried that final state is acknowledged. The agent runs at most `max_crafts` (8) at once and lists at
most 16: a new craft with 16 listed drops the oldest finished one and reports that in `errors`. After a restart (a
new `boot`) it no longer reports the crafts it started before.

**Lost track.** OC keeps a running program and its crafting statuses across a world reload, but not their link to
AE2's job (OC 1.12.68 `CraftingStatus.save`/`load`): the status then answers "no link", with `isCanceled` true and
`isDone` false for a craft that may well still run. The agent reports such a craft as `failed`, from `computing` or
from `crafting`, with `err` exactly `lost track of it after a world reload (OC keeps no AE2 link); the craft may
still run, check AE2`, never as `canceled`. An autosave alone also marks a status that is still planning as failed
with "no link"; there the link appears once AE2 takes the job, so the agent asks `isDone` (which answers from the
link) before calling a craft lost. A `failed` craft whose `err` says `may still run` is not known to have failed:
the core shows it as "check AE2", not as a failure.

**Result** (`results[]`): `id`, `kind`, `ok`, `state`, `err`, `at`. One per command, final:

| `state` | `ok` | When |
|---|---|---|
| `done` | true | `say` said it; `machine_pause`/`machine_resume` set it and `isWorkAllowed` read it back on the next tick |
| `submitted` | true | `craft`: AE2 planned it and accepted the job (its progress is in `crafts`) |
| `failed` | false | It was allowed and tried, and didn't work, or the agent can't tell: `err` says why (AE2 "missing resources", no pattern, no chat box, the machine didn't change, AE2 still planning after 300 s; "the agent restarted before AE2 answered; the craft may still run" and the lost-track text under Craft, which say `may still run`) |
| `refused` | false | It never ran: a kind not allowed, a `confirmed_via` that isn't one of the four, bad arguments, a machine this agent didn't find. `err` says why |

**Error** (`errors[]`): `at`, `where` (`discover`, `power`, `machines`, `sensor`, `ae2`, `tps`, `command`,
`transport`, `state`, `cycle`), `msg` (at most 200 bytes), `count` (how often since it was last acknowledged). At most
8 are kept; a repeat increments `count`. A transport error reaches the core with the first message that gets through.

## The reply document

| Field | Type | Meaning |
|---|---|---|
| `v` | 1 | Contract version. The agent treats a reply with another `v` as a failed exchange |
| `ok` | boolean | `true`: the core took the message. `false`: it didn't (nothing in it was recorded, nothing is acknowledged) |
| `cmds` | array, ≤ 8 | Commands to run, below. Empty when `ok` is false; the agent ignores commands in a refused reply, and any after the 8th |
| `err` | string or null | With `ok: false`, one of `unauthorized` (missing or wrong token), `bad_payload` (not valid by the schema), `unsupported_version`, `internal`. With `ok: true`, an optional note the agent shows and logs once, and again when it changes |
| `ack` | integer, optional | The highest `seq` of this `boot` the core has. HTTP may leave it out: a 200 with `ok: true` acknowledges the message it answers |
| `poll_s` | integer 1–10, optional | Send the next message this soon (e.g. 1 while more commands wait) |

**Command** (`cmds[]`): `id`, `kind`, `args`, `confirmed_via`.
- `id`: the core's `action.id`.
- `confirmed_via`: how Clayton confirmed it (D59): `voice`, `dashboard`, `phone` or `discord`, exactly. The core
  sends only confirmed actions; the agent refuses a command whose `confirmed_via` isn't one of these four as a
  second line of defence, never as the check itself (agents can't confirm, plan §7.9).
- `kind` and `args`, the plan §7.9 safe actions (`waypoint` is the core's own file, not OC's):

| `kind` | `args` | What the agent does |
|---|---|---|
| `craft` | `name` (registry name), `damage` (0–32767), `amount` (1 to the config's `craft_max`, default 1,000,000), `cpu` (optional CPU name) | `getCraftable({name, damage}, "item")` on the AE2 component (one exact lookup of the pattern for that stack with no NBT; never `getCraftables`, which converts every craftable in the network), then `request(amount[, true, cpu])` on it, then tracks the status |
| `machine_pause` | `addr`: a `machines[]` address from this agent | `setWorkAllowed(false)`, then checks `isWorkAllowed` on the next tick |
| `machine_resume` | `addr` | `setWorkAllowed(true)`, checked the same way |
| `say` | `text`: 1–256 bytes | `chat_box.say(text)` with control characters and section-sign colour codes removed. 40 blocks around the chat box, in its dimension [F§4.2] |

Only a `machines[]` address can be paused or resumed: never a power entry (a battery buffer, an LSC) or any other
component. No destructive kind exists (plan §7.9).

**Delivery.** The core sends each confirmed command in one reply, marks it `sent`, and waits for its result; it
doesn't resend it (at most once). A command with no result after a few minutes is the core's to time out, and the
core reports it as "timed out; may have run": the agent may have run it and lost its result (a `bad_payload` drop,
a computer that stopped before the result went out), or never got it (a stolen token, see Token). The agent
also never runs an id twice: it remembers the last 32 ids it ran, with their results, in `gos-agent.state` next to
its config, across restarts; a command that arrives again is answered with the stored result. A craft still planning
when the agent stopped is reported as failed after the restart ("the agent restarted before AE2 answered; the craft
may still run").

## Acknowledgement

The core has message `n` when a reply says `ack: n`, or, over HTTP, when a reply with `ok: true` answers message `n`.
On an ack the agent drops what it had sent in messages up to `n`: results, errors, finished crafts, sensor lines and
the watchlist. What arrives after an ack (a new reading, a repeated error) waits for the next one. Everything waiting
goes in every message, except sensor lines over the 8 KB budget and, in a message over 96 KB, the sensor lines and
then the watchlist (see Size and limits); what is left out of a message loses its mark, so an ack drops only lines
and watchlists that went in every message since they were first sent.

## Sensor lines

`getSensorInformation()` lines are passed on **exactly** as the driver returned them, at most 32 lines of at most
200 bytes per entry. A message carries the waiting lines of as many entries as fit in 8 KB of line text, in list
order (power, then machines); the others go in a later message, each with its own `sensor_at`. An LSC's 24 lines are
about 1.8 KB. On GT 5.09.54.265 (GTNH 2.9.0) they come in two forms, often in one machine, both with section-sign
(`§`, U+00A7) colour codes:

- **Encoded** by `IGregTechDeviceInformation.encode`: the translation key, then each argument after **two backslash
  characters** (`\\`), e.g. `GT5U.infodata.battery_buffer.stored_items\\§a1,234,567§r\\§e2,000,000§r`. An argument
  that starts with U+0001 is itself a translation key. The LSC encodes all of its 24 lines this way
  (`kekztech.infodata.*`, e.g. `kekztech.infodata.multi.maintenance_status.ok` or `.bad`). In JSON each backslash
  is escaped, so the text holds `\\\\`.
- **Localised on the server** with `StatCollector` (single-player: the client's language), e.g. a multiblock's
  `Problems: §c0` (`GT5U.multiblock.scanner.problems=Problems: §c%s`), `Progress: §a6 §rs / §e20 §rs`.

The core parses them (WO-7.1/7.5): strip `§x` codes, split encoded lines on the two backslashes, match keys, and read
the Problems count from either form. Numbers in the lines are locale-formatted (`1,234,567`).

## Identity

- **Machines** are identified by `addr` (stable while the adapter or MFU stays; a new one gives a new address), and
  by `name` + `xyz`. No OC or Computronics method gives the `mte_id`, so the core maps GT's meta name to `mte_id`s
  through the export's `meta_name` in `gt_machines.jsonl` (`docs/contracts/gregteclink-export-v1.md`). Most names
  are one machine, but not all: in the 2.9.0 harness export 3,041 of 3,094 names are one `mte_id`; 48 are a
  controller and its deprecated twin (tooltip `DEPRECATED - Controller will be removed in next major update!`, the
  `deprecated` machine fact, and a pointer to a conversion recipe; 47 of them `*Legacy` classes), and there the core
  takes the one that isn't deprecated; 5 are TecTech's Tesla coils, one name per tier across four sizes (they show as
  `gt_batterybuffer`), which the core keeps as a set rather than guess a size.
  `tests/integration/test_oc_names_harness.py` checks on every harness run that no other kind of sharing appears.
  The DB's `machine` table has no `meta_name` column yet, so the core adds one or reads the export. No numeric
  block or item ID is ever sent (EndlessIDs).
- **Items** are registry name + damage, with no NBT. The watchlist accepts only names (OC's `getItemInNetwork`
  would also take a numeric id; the agent never sends one). The core looks them up in the DB's `item` table by
  `registry_name` and `meta` with no NBT (`nbt_hash` empty); the same harness test checks the examples' items resolve
  that way.

## Security

- Payload text is data (plan §0.1 principle 8): sensor lines, machine names, CPU names, error messages and item
  names come from the game and from mods. The core shows and parses them; no agent treats them as instructions.
- The agent runs only the four kinds above, with `confirmed_via` one of the four and arguments checked, and has no
  free-form code path (no `load` of anything it receives).
- Everything posted with the agent's token is data too (see Token): the core never starts an action because of it.

## Versioning

`v` is 1. Any change to either document changes this file, the schema, the agent and the golden examples in one PR;
a change an older agent or core can't read raises `v`. The agent refuses a reply with another `v`; the core answers a
message with a `v` it doesn't speak with `ok: false`, `err: "unsupported_version"`.
