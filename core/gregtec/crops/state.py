"""What Clayton has and what he is breeding toward (WO-6.7, D274), in the core DB's ``kv``.

- ``crops.have``: the crops he has seeds of, each with its best Growth, Gain and Resistance
  (``{"cropsnh:wheat": [1, 1, 1]}``), keyed by CropsNH's crop id (a stable string, never a
  numeric id). Crops an alternate seed plants (a wheat seed, a carrot) count as had without
  being listed (the guide's obtain steps).
- ``crops.target``: the crop the guide and "next crop step" follow, and the stats aimed at.

Edits are Clayton's (the Crops view, ``gos crops have`` / ``gos crops target``), as My base's are
(D212): no agent tool writes them. Every write is one ``BEGIN IMMEDIATE`` transaction on an
autocommit connection (:func:`gregtec.store.connect`).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass

from gregtec.crops import rules
from gregtec.crops.planner import DEFAULT_TARGET, Stats

KV_HAVE = "crops.have"
KV_TARGET = "crops.target"
MAX_HAVE = 500
ID = 120  # characters in a crop id, at most


class CropStateError(ValueError):
    """An edit the crop state refuses; the message says why."""


@dataclass(frozen=True)
class Target:
    crop: str
    aim: Stats = DEFAULT_TARGET


def _kv(conn: sqlite3.Connection, key: str) -> object:
    try:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:  # a DB the core never opened: no kv yet
        return None
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def _put(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE"
            " SET value = excluded.value, upd = excluded.upd",
            (key, json.dumps(value, sort_keys=True), int(time.time())),
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def stats(value: object) -> Stats:
    """Growth, Gain, Resistance from a list of three whole numbers 1..31."""
    if (
        not isinstance(value, list | tuple)
        or len(value) != 3
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        or not all(rules.MIN_STAT <= v <= rules.MAX_STAT for v in value)
    ):
        raise CropStateError(
            f"stats are three whole numbers {rules.MIN_STAT} to {rules.MAX_STAT} "
            "(Growth, Gain, Resistance)"
        )
    return (int(value[0]), int(value[1]), int(value[2]))


def _crop_id(value: object, known: Mapping[str, object] | None) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= ID:
        raise CropStateError("crop must be a CropsNH crop id (cropsnh:wheat)")
    crop = value.removeprefix("crop:")
    if known is not None and crop not in known:
        raise CropStateError(f"no crop {crop!r} in the crop data")
    return crop


def have(conn: sqlite3.Connection) -> dict[str, Stats]:
    raw = _kv(conn, KV_HAVE)
    out: dict[str, Stats] = {}
    if isinstance(raw, dict):
        for crop, value in raw.items():
            try:
                out[str(crop)] = stats(value)
            except CropStateError:
                continue  # a hand-edited row that doesn't read: left out, not fatal
    return dict(sorted(out.items()))


def set_have(
    conn: sqlite3.Connection,
    crop: object,
    value: object,
    known: Mapping[str, object] | None = None,
) -> dict[str, Stats]:
    """Record that he has ``crop`` at these stats (replacing what was there)."""
    cid = _crop_id(crop, known)
    current = have(conn)
    current[cid] = stats(value)
    if len(current) > MAX_HAVE:
        raise CropStateError(f"at most {MAX_HAVE} crops")
    _put(conn, KV_HAVE, {k: list(v) for k, v in sorted(current.items())})
    return have(conn)


def remove_have(conn: sqlite3.Connection, crop: object) -> dict[str, Stats]:
    cid = _crop_id(crop, None)
    current = have(conn)
    current.pop(cid, None)
    _put(conn, KV_HAVE, {k: list(v) for k, v in sorted(current.items())})
    return have(conn)


def target(conn: sqlite3.Connection) -> Target | None:
    raw = _kv(conn, KV_TARGET)
    if not isinstance(raw, dict) or not isinstance(raw.get("crop"), str):
        return None
    try:
        aim = stats(raw.get("aim", list(DEFAULT_TARGET)))
    except CropStateError:
        aim = DEFAULT_TARGET
    return Target(raw["crop"], aim)


def set_target(
    conn: sqlite3.Connection,
    crop: object,
    aim: object = None,
    known: Mapping[str, object] | None = None,
) -> Target | None:
    """Follow ``crop`` (None clears the target)."""
    if crop is None:
        _put(conn, KV_TARGET, None)
        return None
    cid = _crop_id(crop, known)
    stats_aim = DEFAULT_TARGET if aim is None else stats(aim)
    _put(conn, KV_TARGET, {"crop": cid, "aim": list(stats_aim)})
    return target(conn)


def stock_keys(conn: sqlite3.Connection) -> frozenset[str]:
    """My base's stocked item keys (``registry@meta``, NBT hash dropped), for sub-soil checks."""
    try:
        rows = conn.execute("SELECT key FROM my_base_stock").fetchall()
    except sqlite3.OperationalError:
        return frozenset()
    return frozenset(str(r[0]).split("#", 1)[0] for r in rows)
