"""The player's position, dimension and inventory from the world's save (plan 5.4, [B§3.1-3.2]).

Single-player keeps the host's data in ``level.dat`` -> ``Data.Player`` (it overrides
``playerdata/<uuid>.dat``); a world without it (a server's) has one ``playerdata/<uuid>.dat`` per
player, and the tracked one is the only file there or the UUID asked for. Both are gzip NBT,
written on every save (autosave every 45 s, the pause save, quit). The file is read whole through
``safe_open`` once it has held still, and diffed with the last read:

- ``dim_change`` ``{from, to}`` and ``location`` ``{x, y, z, dim}`` when the player moved at least
  :data:`MOVE_MIN` blocks or changed dimension, at most once per :data:`LOCATION_EVERY_S`.
- ``item_delta`` ``{items: [{key, registry, meta, nbt_hash, delta}], via: "inventory"}``: what
  came into and left the main inventory. An item is keyed by registry name, meta and NBT hash
  (D104, NEI's typing: :mod:`gregtec.watchers.nbtjson`), never a numeric ID: the numeric ``id``
  (EndlessIDs' ``idExt`` first, [B§3.2]) goes through this world's own FML ``ItemData`` registry
  in level.dat, and an ID it doesn't name is counted as ``unnamed`` and left out.

The first read is the baseline: nothing that was already there becomes an event.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import normalize
from gregtec.watchers import files, nbtjson
from gregtec.watchers.events import Event

MOVE_MIN = 8.0  # blocks
LOCATION_EVERY_S = 30.0
LEVEL_LIMIT = 64 << 20

ItemKey = tuple[str, int, str]  # registry name, meta, NBT hash


@dataclass(frozen=True)
class Snapshot:
    pos: tuple[float, float, float] | None
    dim: int | None
    items: dict[ItemKey, int]
    unnamed: int  # stacks whose numeric ID this world's registry doesn't name


def _validated(data: bytes) -> dict[str, nbtjson.Json]:
    return nbtjson.read(data)


def read_nbt(path: Path) -> dict[str, nbtjson.Json]:
    """A gzip NBT save file, typed; a partial or damaged read is retried (``safe_open``)."""
    parsed: list[dict[str, nbtjson.Json]] = []
    read_bytes(path, validate=lambda d: parsed.append(_validated(d)), limit=LEVEL_LIMIT)
    return parsed[-1]


def id_names(level: dict[str, nbtjson.Json]) -> dict[int, str]:
    """Numeric item ID -> registry name from this world's ``FML.ItemData`` (``\\u0002`` items,
    then ``\\u0001`` blocks for an ID with no item entry) [B§3.1]."""
    fml = level.get("FML")
    entries = fml.get("ItemData") if isinstance(fml, dict) else None
    items: dict[int, str] = {}
    blocks: dict[int, str] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        key, value = entry.get("K"), nbtjson.num(entry.get("V"))
        if not isinstance(key, str) or not isinstance(value, int) or len(key) < 2:
            continue
        if key[0] == "\u0002":
            items[value] = key[1:]
        elif key[0] == "\u0001":
            blocks.setdefault(value, key[1:])
    return {**blocks, **items}


def stack_key(
    stack: dict[str, nbtjson.Json], names: dict[int, str]
) -> tuple[ItemKey, int] | None:
    """(key, count) of one saved ItemStack, or None if its ID isn't named here."""
    raw_id = nbtjson.num(stack.get("idExt"))
    if not isinstance(raw_id, int) or raw_id == 0:
        raw_id = nbtjson.num(stack.get("id"))
    count, meta = nbtjson.num(stack.get("Count")), nbtjson.num(stack.get("Damage"))
    if not isinstance(raw_id, int) or raw_id not in names:
        return None
    tag = stack.get("tag")
    digest = ""
    if isinstance(tag, dict) and tag:
        digest = normalize.nbt_hash(
            normalize.normalize_nbt(tag, {i: n for i, n in names.items()})
        )
    return (names[raw_id], int(meta or 0), digest), int(count or 0)


def snapshot(player: dict[str, nbtjson.Json], names: dict[int, str]) -> Snapshot:
    pos_raw = player.get("Pos")
    pos = None
    if isinstance(pos_raw, list) and len(pos_raw) == 3:
        nums = [nbtjson.num(v) for v in pos_raw]
        if all(isinstance(n, int | float) for n in nums):
            pos = (float(nums[0] or 0), float(nums[1] or 0), float(nums[2] or 0))
    dim = nbtjson.num(player.get("Dimension"))
    items: Counter[ItemKey] = Counter()
    unnamed = 0
    inventory = player.get("Inventory")
    for stack in inventory if isinstance(inventory, list) else []:
        if not isinstance(stack, dict):
            continue
        found = stack_key(stack, names)
        if found is None:
            unnamed += 1
            continue
        items[found[0]] += found[1]
    return Snapshot(
        pos, int(dim) if isinstance(dim, int) else None, dict(items), unnamed
    )


def item_entry(key: ItemKey, delta: int) -> dict[str, object]:
    reg, meta, digest = key
    text = f"{reg}@{meta}" + (f"#{digest[:8]}" if digest else "")
    return {
        "key": text,
        "registry": reg,
        "meta": meta,
        "nbt_hash": digest,
        "delta": delta,
    }


def diff_items(
    old: dict[ItemKey, int], new: dict[ItemKey, int]
) -> list[dict[str, object]]:
    out = []
    for key in sorted(set(old) | set(new)):
        delta = new.get(key, 0) - old.get(key, 0)
        if delta:
            out.append(item_entry(key, delta))
    return out


class PlayerWatch:
    """level.dat (or playerdata) -> dim_change, location, item_delta; also tells the pause
    tracker when the world was saved."""

    source = "player"

    def __init__(
        self,
        world: Path,
        clock: Callable[[], float],
        player: str | None = None,
        settle_s: float = 1.0,
    ) -> None:
        self.world = world
        self.clock = clock
        self.player = player
        self.settle = files.Settle(settle_s)
        self.last: Snapshot | None = None
        self.last_location: float | None = None
        self.anchor: tuple[float, float, float] | None = (
            None  # where the last location was
        )
        self.names: dict[int, str] = {}
        self.saved = False  # level.dat was written since the last poll
        self.problem: str | None = None

    def _player_file(self) -> Path | None:
        folder = self.world / "playerdata"
        if self.player:
            path = folder / f"{self.player}.dat"
            return path if path.is_file() else None
        found = sorted(folder.glob("*.dat")) if folder.is_dir() else []
        return found[0] if len(found) == 1 else None

    def poll(self) -> list[Event]:
        now = self.clock()
        level_path = self.world / "level.dat"
        level_stamp = files.stamp(level_path)
        self.saved = False
        if level_stamp is None:
            return []
        if not self.settle.changed("level", level_stamp, now):
            return []
        first = self.last is None
        self.saved = not first
        try:
            level = read_nbt(level_path)
        except (OSError, ValueError) as exc:
            self.problem = f"level.dat unreadable: {exc}"
            return []
        self.names = id_names(level) or self.names
        data = level.get("Data")
        player = data.get("Player") if isinstance(data, dict) else None
        if not isinstance(player, dict):
            path = self._player_file()
            if path is None:
                self.problem = "no player in level.dat and no single playerdata file"
                return []
            try:
                player = read_nbt(path)
            except (OSError, ValueError) as exc:
                self.problem = f"{path.name} unreadable: {exc}"
                return []
        self.problem = None
        snap = snapshot(player, self.names)
        old, self.last = self.last, snap
        if first or old is None:
            self.last_location, self.anchor = now, snap.pos
            return []
        events: list[Event] = []
        moved_dim = snap.dim is not None and old.dim is not None and snap.dim != old.dim
        if moved_dim:
            events.append(
                Event("dim_change", now, self.source, {"from": old.dim, "to": snap.dim})
            )
        if snap.pos is not None and (
            moved_dim
            or (
                self.anchor is not None
                and math.dist(snap.pos, self.anchor) >= MOVE_MIN
                and now - (self.last_location or 0) >= LOCATION_EVERY_S
            )
        ):
            self.last_location, self.anchor = now, snap.pos
            x, y, z = (round(v, 1) for v in snap.pos)
            events.append(
                Event(
                    "location",
                    now,
                    self.source,
                    {"x": x, "y": y, "z": z, "dim": snap.dim},
                )
            )
        delta = diff_items(old.items, snap.items)
        if delta:
            events.append(
                Event(
                    "item_delta",
                    now,
                    self.source,
                    {"items": delta, "via": "inventory", "unnamed": snap.unnamed},
                )
            )
        return events
