# GregtecLink client link, `gregteclink-client/1` (WO-8.1, D285, D286)

GregtecLink's client module (`mod/src/main/java/.../client/`) and the core (`core/gregtec/gamelink/`) talk over
one WebSocket. This is the whole protocol.

## Connection

- `ws://127.0.0.1:<[core] port>/mod/ws`, loopback only. `Host: 127.0.0.1:<port>`, no `Origin` (the core's Host/Origin
  guard, D117, applies as to every request).
- `Authorization: Bearer <token>`. The token is `gregteclink-play` or `gregteclink-lab` (`Auth.link_binding`), issued
  by `install_gregteclink.py` through the core and kept in `GOS_HOME\config\gregteclink-<instance>.token`, which
  gos-dev can't read. It is not a client token: it opens `/mod/ws` and nothing else, and `/mod/ws` takes no other
  credential. Its instance is the link's binding. A wrong or missing token: the handshake is closed with 1008.
- The core checks the token again every 30 s and closes the link (1008) once it is revoked or replaced.
- Messages are JSON objects in text frames, at most 4096 bytes each (the core's `ws_max_size`). Each has `type`;
  the mod's have `v: 1`. Anything else, or a type a side doesn't know, is ignored.
- The mod reconnects after 1, 2, 4, 8, then every 15 s, reading the token file again each time. It sends nothing
  while it is disconnected (no queue of stale positions). It takes the core as gone after 60 s with no frame (the core's
  WebSocket pings come every 20 s).

## Mod to core

| type | when | fields |
|---|---|---|
| `hello` | first, on every connection | `player` (the logged-in name), `mod_version`, `feeds` (`position`, `target`, `chat`, `notices`: booleans) |
| `status` | 1 Hz in a world (`position` feed) | `dim` (int), `dim_name`, `x`, `y`, `z` (blocks, one decimal), `paused` (the game is paused) |
| `target` | the What's this? key (`why: key`), or the core's `probe` (`why: probe`, `probe`: its id) | `id` (this client's report number), `kind`, `item`, `block`, `display` |
| `chat` | each chat line received (`chat` feed) | `text` (formatting codes stripped, at most 500 characters); `sender` and `body` only for a player's chat message (vanilla's `chat.type.text`, whose arguments are the name and the text); `singleplayer` (bool: the world is this client's own, single player or the LAN host) |

`target.kind` is `item` (hovered in a container GUI, NEI's overlay on it included: its item panel, bookmarks and recipe pages; other screens, such as a quest book, report `none`), `block` (looked at;
`item` is its pick item, what middle-click gives, so a GT machine's item carries its meta tile entity id), `none`
(nothing pointed at) or `off` (the `target` feed is off: sent only to answer a probe, and says nothing else).

`item` is `{"item": <registry name>, "meta": <int>, "nbt": <object>?}`. `nbt` is the stack's NBT in the export's typing
(`StackJson.nbt`: sorted keys, typed scalars such as `"10000.0D"`), minus the numeric `id` of every compound that also
has `__item` (its registry name), so nothing in a message is a numeric ID. NBT over 2048 characters is left out and
`nbt_dropped: true` sent instead; so is NBT that would make the message over 3800 UTF-8 bytes (then `display` goes too
if it still doesn't fit). `block` is `{"block": <registry name>, "meta", "x", "y", "z", "dim"}`. `display` is
the game's own name for the item, formatting codes stripped (data only: the core never states it as fact).

## Core to mod

| type | when | fields |
|---|---|---|
| `hello` | first | `v: 1` |
| `probe` | Clayton said "what's this?" (the router's instant command) | `id`; the mod answers with `target` `why: probe` on its next client tick |
| `notice` | a local chat line for Clayton (D76) | `kind` (`answer`, `gos`, `notice`, `failure`), `text`; an `answer` also has `re` (the `target` id it answers), `key`, `name` and `refs` |

The mod shows a `notice` as `[GOS] <text>` with `printChatMessage`, on this client only (never sent to the server),
when the `notices` feed is on.

## What the core does with them

- **What's this?** The item's DB keys, most exact first: `registry@meta#<sha256 of the normalised NBT>` (as the export
  hashes it), `registry@meta`, `registry@32767`. The first the DB has is answered from `item_resolve`, `machine_info`
  (a GT machine), `recipe_find` and `recipe_uses`: its name and mod, how many recipes make it and the first one's
  `r:` id (the machine's `mte:` for a machine), how many it goes into. No model is asked. A key press is answered at
  once as an `answer` notice and as an `ask.reply` card on the dashboards (`by: game`); "what's this?" said aloud (or
  typed anywhere) probes the game and answers the request itself; if the game doesn't answer in 1.5 s, that game's own key report
  from the last minute stands in. A `target` answers a `probe` only from the link it was sent to.
- **`!gos <text>`** in chat is a question like a typed one, only when `sender` equals the `hello` player (Clayton's own
  client) and `singleplayer` is true (on a server a chat bridge or team prefix could fake the name: open, [QA:V6]). Its answer is a `gos` notice and a dashboard card. It can't confirm an action (D59). One at a time per link.
  Every other chat line is data (plan 0.1 principle 8): kept (the newest 50, `GET /api/game`) and recorded as a
  `chat` event.
- **Status** is kept for `GET /api/game`; a dimension change is a `dim_change` event, the position a `location` event
  at most once a minute (Appendix D's `event` table, when the DB has it).
- A health strip item that turns to error, and its recovery, is sent as a `failure` / `notice` (D76).
