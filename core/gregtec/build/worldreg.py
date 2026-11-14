"""A world's block and item registry, from its ``level.dat`` (WO-10.3, plan 7.10 "Palette rule").

Forge 1.7.10 saves the registry it ran with in the world's ``level.dat``: the root's ``FML``
compound holds ``ItemData``, a list of ``{K: <name>, V: <numeric id>}``, where a block's name
starts with ``\\x01`` and an item's with ``\\x02`` (``GameData.writeItemData``). A block name
missing there doesn't exist in that world, so a palette naming it is refused. Only the names are
kept: the numeric ids are per-world (EndlessIDs) and GOS never keys on them.

The file is read through :func:`gregtec.gamestate.safe_open.read_bytes` (share-delete, copied to
memory, then parsed; 3 tries) and never held. Minecraft saves by renaming ``level.dat`` to
``level.dat_old`` and the new file into place, so a read that lands in that gap waits once and
tries again, then reads ``level.dat_old``. Read-only: nothing here writes to a world.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import nbt

LEVEL_DAT = "level.dat"
LEVEL_DAT_OLD = "level.dat_old"
LEVEL_MAX = 64 << 20  # a modded level.dat with its registry is well under 1 MB
SAVE_WAIT_S = 0.25
BLOCK_PREFIX = "\x01"
ITEM_PREFIX = "\x02"


class WorldRegistryError(RuntimeError):
    """Why the world's registry can't be read (shown as is)."""


@dataclass(frozen=True)
class WorldRegistry:
    world: str  # the world folder's name
    blocks: frozenset[str]  # registry names of the blocks in the world's ItemData
    items: frozenset[str]
    source: str  # level.dat, or level.dat_old when a save was renaming it


def parse(
    root: Mapping[str, Any], world: str, source: str = LEVEL_DAT
) -> WorldRegistry:
    """The registry names in a ``level.dat``'s root compound."""
    fml = root.get("FML")
    if not isinstance(fml, dict):
        raise WorldRegistryError(
            f"the world {world!r} has no FML data in its level.dat (not a Forge world, or never "
            "opened with Forge)"
        )
    data = fml.get("ItemData")
    if not isinstance(data, list):
        raise WorldRegistryError(f"the world {world!r}'s level.dat has no FML ItemData")
    blocks: set[str] = set()
    items: set[str] = set()
    for entry in data:
        key = entry.get("K") if isinstance(entry, dict) else None
        if not isinstance(key, str) or len(key) < 2:
            raise WorldRegistryError(
                f"the world {world!r}'s FML ItemData has an entry without a name"
            )
        if key[0] == BLOCK_PREFIX:
            blocks.add(key[1:])
        elif key[0] == ITEM_PREFIX:
            items.add(key[1:])
        else:
            raise WorldRegistryError(
                f"the world {world!r}'s FML ItemData has a name without a block or item "
                f"marker: {key[:80]!r}"
            )
    if not blocks:
        raise WorldRegistryError(f"the world {world!r}'s FML ItemData lists no blocks")
    return WorldRegistry(world, frozenset(blocks), frozenset(items), source)


def block_ids(root: Mapping[str, Any], world: str) -> dict[int, str]:
    """A ``level.dat``'s block ids to registry names (``FML.ItemData``'s ``V`` to ``K``).

    Only to read a capture whose schematic has no ``BlockMapping``/``SchematicaMapping``
    (:mod:`gregtec.build.schematic`): its numeric ids are those of the world it was copied in, and
    are turned into names as the file is read, never kept (WO-10.4, D311)."""
    parse(root, world)  # the same refusals as the names
    out: dict[int, str] = {}
    for entry in root["FML"]["ItemData"]:
        key, value = entry["K"], entry.get("V")
        if key[0] == BLOCK_PREFIX and isinstance(value, int):
            out[value] = key[1:]
    return out


def _read(path: Path) -> dict[str, Any]:
    data = read_bytes(path, validate=nbt.read, limit=LEVEL_MAX)
    return nbt.read(data)[1]


def read(world: Path) -> WorldRegistry:
    """The registry of the world folder ``world`` (its ``level.dat``)."""
    root, source = _root(world)
    return parse(root, world.name, source)


def read_block_ids(world: Path) -> dict[int, str]:
    """:func:`block_ids` of the world folder ``world`` (its ``level.dat``, read-only)."""
    return block_ids(_root(world)[0], world.name)


def _root(world: Path) -> tuple[dict[str, Any], str]:
    path = world / LEVEL_DAT
    source = LEVEL_DAT
    try:
        try:
            root = _read(path)
        except FileNotFoundError:
            time.sleep(SAVE_WAIT_S)
            try:
                root = _read(path)
            except FileNotFoundError:
                source = LEVEL_DAT_OLD
                root = _read(world / LEVEL_DAT_OLD)
    except FileNotFoundError:
        raise WorldRegistryError(
            f"the world {world.name!r} has no level.dat (never opened in game, or it is "
            "being saved: try again in a moment)"
        ) from None
    except (OSError, ValueError) as exc:
        raise WorldRegistryError(
            f"can't read the world {world.name!r}'s level.dat ({type(exc).__name__}); try "
            "again in a moment"
        ) from None
    return root, source
