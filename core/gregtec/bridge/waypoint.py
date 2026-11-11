"""The ``waypoint`` safe action: a JourneyMap waypoint file the core writes (plan 7.9, V23).

JourneyMap 5.2.23 (GTNH 2.9.0) keeps one JSON file per waypoint in
``<instance>/<minecraft|.minecraft>/journeymap/data/sp/<world folder>/waypoints/``, named
``<name>_<x>,<y>,<z>.json`` with characters a file name can't hold replaced by ``_``; its fields
are ``id, name, icon, x, y, z, r, g, b, enable, type, origin, dimensions``, and a Nether
waypoint stores x and z at overworld scale (x 8) [B§4.2]. The format is re-implemented from
that description; no JourneyMap code is used. Whether a running JourneyMap picks up a file
written while the game runs is VERIFY V23 (a Lab test).

The core writes only into that one ``waypoints`` folder of the world the action targets, never
through a link or junction (gos-dev can change Lab's folders, D75), and only a new file: a
temporary file in the folder, then ``os.replace`` (retried while Windows reports the target
held). An existing waypoint with the same name and position is left alone. As gos-dev, Play's
JourneyMap folder is never written (D75): :func:`place` refuses it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec import config
from gregtec.bridge.mailbox import _identity
from gregtec.config import Config
from gregtec.tweaks import GAME_DIRS

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.'-]{0,31}$")
_FILE_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
COORD_MAX = 30_000_000  # the world border
Y_MAX = 255
DIM_RANGE = (-1000, 1000)
NETHER = -1
REPLACE_TRIES = 3
# The args the waypoint action takes (the action_propose tool's schema for it, D220).
ARGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "x", "y", "z", "dim"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 32},
        "x": {"type": "integer", "minimum": -COORD_MAX, "maximum": COORD_MAX},
        "y": {"type": "integer", "minimum": 0, "maximum": Y_MAX},
        "z": {"type": "integer", "minimum": -COORD_MAX, "maximum": COORD_MAX},
        "dim": {"type": "integer", "minimum": DIM_RANGE[0], "maximum": DIM_RANGE[1]},
    },
}


class WaypointError(ValueError):
    """Why a waypoint wasn't written (shown to Clayton as the action's failure)."""


def check(args: Mapping[str, Any]) -> None:
    """Refuse waypoint args :data:`ARGS_SCHEMA` allows but a waypoint file can't hold."""
    if not NAME.fullmatch(str(args.get("name", ""))):
        raise WaypointError(
            "a waypoint name is 1 to 32 letters, digits, spaces and _ . ' -, "
            "starting with a letter or digit"
        )


@dataclass(frozen=True)
class Place:
    """Where one world's JourneyMap waypoints are."""

    instance: Path
    world: str

    def folder(self) -> Path | None:
        """``.../journeymap/data/sp/<world>/waypoints`` (None: no game folder)."""
        for name in GAME_DIRS:
            game = self.instance / name
            if game.is_dir():
                return game / "journeymap" / "data" / "sp" / self.world / "waypoints"
        return None


def place(cfg: Config, target: str, *, as_dev: bool | None = None) -> Place:
    """The waypoints of ``target``'s world (``play``: ``[paths] world``; ``lab``: ``lab_world``)."""
    dev = config.running_as_dev() if as_dev is None else as_dev
    if target == "play" and dev:
        raise WaypointError(
            f"as {config.DEV_ACCOUNT}, Play's JourneyMap folder is never written (D75); use lab"
        )
    world = (
        cfg.world if target == "play" else cfg.lab_world if target == "lab" else None
    )
    if target not in ("play", "lab"):
        raise WaypointError(f"no instance {target!r}")
    if not world:
        key = "world" if target == "play" else "lab_world"
        raise WaypointError(
            f"gos.toml has no [paths] {key}, so there is no world to mark"
        )
    return Place(cfg.instance(target), world)


def document(args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """(file name, JSON document) of the waypoint ``args`` describe."""
    check(args)
    name, dim = str(args["name"]), int(args["dim"])
    x, y, z = int(args["x"]), int(args["y"]), int(args["z"])
    if dim == NETHER:  # JourneyMap keeps Nether waypoints at overworld scale [B§4.2]
        x, z = x * 8, z * 8
    wid = f"{name}_{x},{y},{z}"
    doc = {
        "id": wid,
        "name": name,
        "icon": "waypoint-normal.png",
        "x": x,
        "y": y,
        "z": z,
        "r": 0,
        "g": 200,
        "b": 255,
        "enable": True,
        "type": "Normal",
        "origin": "journeymap",
        "dimensions": [dim],
    }
    return _FILE_UNSAFE.sub("_", wid) + ".json", doc


def write(where: Place, args: Mapping[str, Any]) -> str:
    """Write the waypoint; returns what was done, for the action's result."""
    file_name, doc = document(args)
    folder = where.folder()
    if folder is None or not folder.parent.is_dir():
        raise WaypointError(
            f"JourneyMap has no data for the world {where.world!r} yet (open it once in game)"
        )
    _real(where, folder.parent)  # no link leads elsewhere before anything is created
    folder.mkdir(exist_ok=True)
    real = _real(where, folder)
    target = real / file_name
    if os.path.lexists(target):
        return f"waypoint {file_name} is already there"
    tmp = real / f".gos-{secrets.token_hex(4)}.tmp"
    data = json.dumps(doc, indent=2).encode("utf-8")
    with open(tmp, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                if attempt == REPLACE_TRIES - 1:
                    raise WaypointError(
                        "JourneyMap holds the waypoint file; try again"
                    ) from None
                time.sleep(0.05 * (attempt + 1))
    finally:
        if tmp.exists():
            tmp.unlink()
    return f"wrote waypoint {file_name}"


def _real(where: Place, path: Path) -> Path:
    """``path`` resolved, if no link or junction on the way leads it out of its own place
    under the instance; else WaypointError (nothing is written or created)."""
    try:
        real_instance = where.instance.resolve(strict=True)
        real = path.resolve(strict=True)
    except OSError as exc:
        raise WaypointError(
            f"can't reach the waypoints folder ({type(exc).__name__})"
        ) from None
    want = real_instance / path.relative_to(where.instance)
    if (
        os.path.normcase(real) != os.path.normcase(want)
        or _identity(real_instance, real) is None
    ):
        raise WaypointError(
            "a link or junction leads the waypoints folder elsewhere; nothing written"
        )
    return real
