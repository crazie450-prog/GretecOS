"""Anvil region files for the census tests (WO-6.2): the writer side, as Minecraft 1.7.10's
``RegionFile`` lays them out (an 8 KB header of locations and save times, then each chunk as a
4-byte length, a compression byte and the data, in 4 KB sectors). Test code only."""

from __future__ import annotations

import gzip
import struct
import zlib
from pathlib import Path
from types import ModuleType
from typing import Any

SECTOR = 4096


def machine_te(
    fg: ModuleType, x: int, y: int, z: int, mte: int, active: bool = False
) -> dict[str, Any]:
    """A GT ``BaseMetaTileEntity`` as GT 5.09.54.265's writeToNBT leaves it (the fields the census
    reads, and a few it ignores)."""
    return {
        "id": "BaseMetaTileEntity",
        "x": x,
        "y": y,
        "z": z,
        "mID": mte,
        "mActive": fg.T(fg.BYTE, int(active)),
        "mWasShutdown": fg.T(fg.BYTE, 0),
        "mFacing": fg.T(fg.SHORT, 2),
        "mOwnerName": "Clayton",
    }


def chunk_bytes(
    fg: ModuleType,
    cx: int,
    cz: int,
    tes: list[dict[str, Any]],
    compression: int = 2,
    x_pos: int | None = None,
) -> bytes:
    level = {
        "xPos": cx if x_pos is None else x_pos,
        "zPos": cz,
        "LastUpdate": fg.T(fg.LONG, 100),
        "Sections": [{"Y": fg.T(fg.BYTE, 0), "Blocks": bytes(4096)}],
        "Entities": [],
        "TileEntities": tes,
    }
    raw = fg.nbt_file({"Level": level}, compressed=False)
    body = gzip.compress(raw) if compression == 1 else zlib.compress(raw)
    return struct.pack(">ib", len(body) + 1, compression) + body


def region_file(path: Path, chunks: dict[tuple[int, int], tuple[bytes, int]]) -> None:
    """Writes ``path`` with ``(cx, cz) -> (chunk bytes, save time)``, chunks in index order."""
    locations = [0] * 1024
    times = [0] * 1024
    body = b""
    sector = 2
    for (cx, cz), (data, ts) in sorted(
        chunks.items(), key=lambda kv: (kv[0][1] & 31, kv[0][0] & 31)
    ):
        i = (cx & 31) + 32 * (cz & 31)
        padded = data + bytes(-len(data) % SECTOR)
        n = len(padded) // SECTOR
        locations[i] = (sector << 8) | n
        times[i] = ts
        body += padded
        sector += n
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        struct.pack(">1024I", *locations) + struct.pack(">1024i", *times) + body
    )
