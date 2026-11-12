"""A fake GTNH game folder and a fake game that writes it the way Minecraft 1.7.10 does (WO-6.1).

Used two ways:

- imported (through ``conftest.py``) by the watcher unit tests, to build a Prism-shaped tree
  (``instances/GTNH-Lab/.minecraft``, a world in ``saves/``) and write its files once;
- run as a script, ``python fakegame.py <game> <world> <seconds> <result.json>``, by the soak
  test: a separate process that keeps writing, renaming and holding its files as the game does
  while the watchers read them, and records every write the OS refused (a blocked save).

How each file is written (research B section 6.2, plan 5.5):

- logs: appended by a handle held open (log4j; on Windows Python's ``open`` shares read and
  write but not delete, like Java's), rolled by close, rename, reopen;
- level.dat: Hodgepodge's atomic save (temp file, then a replace) and vanilla's save
  (``level.dat_new``, ``level.dat`` -> ``level.dat_old``, ``level.dat_new`` -> ``level.dat``), in
  turn;
- BetterQuesting's progress and name cache: a temp file, then a replace;
- stats, VisualProspecting's caches, JourneyMap's waypoints: written in place;
- ``session.lock``: held open (and, where the OS has them, byte-locked) the whole session.

Nothing here imports GOS: the writer is the game, not the reader.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import struct
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

PLAYER = "Clayton"
PLAYER_UUID = "0f6a3c3e-9a1b-4e8f-8b2a-111122223333"

# --- NBT (big-endian named tags), the writer side ---------------------------------------------

(
    END,
    BYTE,
    SHORT,
    INT,
    LONG,
    FLOAT,
    DOUBLE,
    BYTE_ARRAY,
    STRING,
    LIST,
    COMPOUND,
    INT_ARRAY,
) = range(12)


class T:
    """A typed tag value: ``T(SHORT, 5)``. Plain ints are INT, str STRING, dict COMPOUND, list
    LIST, bytes BYTE_ARRAY, float DOUBLE."""

    def __init__(self, kind: int, value: object) -> None:
        self.kind, self.value = kind, value


def _kind(v: object) -> int:
    if isinstance(v, T):
        return v.kind
    if isinstance(v, dict):
        return COMPOUND
    if isinstance(v, str):
        return STRING
    if isinstance(v, bytes):
        return BYTE_ARRAY
    if isinstance(v, list):
        return LIST
    if isinstance(v, float):
        return DOUBLE
    if isinstance(v, int):
        return INT
    raise TypeError(v)


def _string(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _payload(v: Any) -> bytes:  # noqa: ANN401 - any tag value
    kind = _kind(v)
    raw = v.value if isinstance(v, T) else v
    fmt = {BYTE: ">b", SHORT: ">h", INT: ">i", LONG: ">q", FLOAT: ">f", DOUBLE: ">d"}
    if kind in fmt:
        return struct.pack(fmt[kind], raw)
    if kind == STRING:
        return _string(raw)
    if kind == BYTE_ARRAY:
        return struct.pack(">i", len(raw)) + bytes(raw)
    if kind == INT_ARRAY:
        return struct.pack(f">i{len(raw)}i", len(raw), *raw)
    if kind == LIST:
        inner = _kind(raw[0]) if raw else END
        return struct.pack(">bi", inner, len(raw)) + b"".join(_payload(x) for x in raw)
    if kind == COMPOUND:
        body = b"".join(
            bytes((_kind(x),)) + _string(k) + _payload(x) for k, x in raw.items()
        )
        return body + b"\x00"
    raise TypeError(kind)


def nbt_file(root: dict[str, Any], compressed: bool = True) -> bytes:
    data = bytes((COMPOUND,)) + _string("") + _payload(root)
    return gzip.compress(data) if compressed else data


# --- the files' contents ----------------------------------------------------------------------

ITEM_DATA = [
    ("\u0001minecraft:stone", 1),
    ("\u0002minecraft:stone", 1),
    ("\u0002minecraft:iron_ingot", 265),
    ("\u0002gregtech:gt.metaitem.01", 4097),
    ("\u0002gregtech:gt.metatool.01", 4100),
    ("\u0002extra:big_id_item", 70000),
]


def stack(
    slot: int, item_id: int, count: int, damage: int, tag: dict[str, Any] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "Slot": T(BYTE, slot),
        "id": T(SHORT, item_id if item_id < 32768 else 0),
        "Count": T(BYTE, count),
        "Damage": T(SHORT, damage),
    }
    if item_id >= 32768:
        out["idExt"] = item_id  # EndlessIDs (B section 3.2)
    if tag is not None:
        out["tag"] = tag
    return out


def level_dat(
    pos: tuple[float, float, float] = (100.5, 64.0, -200.5),
    dim: int = 0,
    inventory: list[dict[str, Any]] | None = None,
) -> bytes:
    player = {
        "Pos": [float(pos[0]), float(pos[1]), float(pos[2])],
        "Dimension": dim,
        "Inventory": inventory if inventory is not None else [],
    }
    return nbt_file(
        {
            "Data": {"LevelName": "lab", "Player": player, "Time": T(LONG, 12345)},
            "FML": {"ItemData": [{"K": k, "V": v} for k, v in ITEM_DATA]},
        }
    )


def _id_parts(text: str) -> tuple[int, int]:
    raw = uuid.UUID(text).bytes
    high, low = struct.unpack(">qq", raw)
    return high, low


def quest_id(n: int) -> tuple[int, int, str]:
    """BQ's ids: (questIDHigh, questIDLow, the compact text GOS cites)."""
    raw = struct.pack(">qq", 0, n)
    text = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return 0, n, text


def quest_progress(done: list[int], ts: int = 1_762_000_000_000) -> bytes:
    entries = {}
    for i, n in enumerate(done):
        high, low, _ = quest_id(n)
        entries[f"{i}:10"] = {
            "questIDHigh:4": high,
            "questIDLow:4": low,
            "completed:9": {
                "0:10": {"uuid:8": PLAYER_UUID, "timestamp:4": ts + n, "claimed:1": 0}
            },
            "tasks:9": {},
        }
    return json.dumps({"questProgress:9": entries}).encode()


def name_cache() -> bytes:
    return json.dumps(
        {"nameCache:9": {"0:10": {"uuid:8": PLAYER_UUID, "name:8": PLAYER}}}
    ).encode()


def vp_cache(dim: int, veins: list[tuple[int, int, str]]) -> bytes:
    palette = sorted({m for _, _, m in veins})
    return nbt_file(
        {
            "version": 3,
            "dim": dim,
            "ores": {
                "palette": palette,
                "chunkX": T(INT_ARRAY, [cx for cx, _, _ in veins]),
                "chunkZ": T(INT_ARRAY, [cz for _, cz, _ in veins]),
                "veinTypeIndex": T(INT_ARRAY, [palette.index(m) for _, _, m in veins]),
                "depleted": bytes(len(veins)),
                "source": bytes([1] * len(veins)),
            },
        }
    )


def stamp_line(
    msg: str,
    thread: str = "Client thread",
    level: str = "INFO",
    logger: str | None = None,
) -> str:
    when = time.strftime("%H:%M:%S")
    tag = f" [{logger}]" if logger else ""
    return f"[{when}] [{thread}/{level}]{tag}: {msg}\n"


# --- the tree ----------------------------------------------------------------------------------


class Tree:
    """``<root>/instances/GTNH-Lab/.minecraft`` with a world, written once per call."""

    def __init__(self, root: Path, world: str = "lab-world") -> None:
        self.root = root
        self.instance = root / "instances" / "GTNH-Lab"
        self.game = self.instance / ".minecraft"
        self.world = self.game / "saves" / world
        self.world_id = f"{world}_7d3c1a2e-0000-4000-8000-000000000001"
        for d in (
            self.game / "logs",
            self.world / "betterquesting" / "QuestProgress",
            self.world / "stats",
            self.world / "data",
            self.game / "crash-reports",
            self.game / "visualprospecting" / "client" / PLAYER_UUID / self.world_id,
            self.game / "journeymap" / "data" / "sp" / world / "waypoints",
        ):
            d.mkdir(parents=True, exist_ok=True)
        (self.instance / "instance.cfg").write_text(
            "[General]\nname=GTNH-Lab\n", encoding="utf-8"
        )
        (self.world / "data" / "visualprospecting.dat").write_bytes(
            nbt_file({"data": {"wId": self.world_id}})
        )

    def log(self, line: str, name: str = "latest.log") -> None:
        with open(self.game / "logs" / name, "a", encoding="utf-8") as f:
            f.write(line)

    def rotate(self, name: str = "latest.log", n: int = 1) -> None:
        path = self.game / "logs" / name
        if path.exists():
            os.replace(path, self.game / "logs" / f"2026-11-11-{n}.log")
        path.touch()

    def save_level(self, data: bytes, vanilla: bool = False) -> None:
        target = self.world / "level.dat"
        if vanilla:  # 1.7.10's SaveHandler: _new, then rename the old away, then rename
            new, old = self.world / "level.dat_new", self.world / "level.dat_old"
            new.write_bytes(data)
            if old.exists():
                old.unlink()
            if target.exists():
                os.rename(target, old)
            os.rename(new, target)
        else:  # Hodgepodge's threaded saver: a temp file, then an atomic replace
            tmp = self.world / f".level.dat-{uuid.uuid4().hex[:8]}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, target)

    def save_quests(self, done: list[int]) -> None:
        folder = self.world / "betterquesting"
        for path, data in (
            (folder / "QuestProgress" / f"{PLAYER_UUID}.json", quest_progress(done)),
            (folder / "NameCache.json", name_cache()),
        ):
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)

    def save_stats(self, stats: dict[str, int]) -> None:
        with open(
            self.world / "stats" / f"{PLAYER_UUID}.json", "w", encoding="utf-8"
        ) as f:
            f.write(json.dumps(stats))

    def save_vp(self, dim: int, veins: list[tuple[int, int, str]]) -> None:
        folder = (
            self.game / "visualprospecting" / "client" / PLAYER_UUID / self.world_id
        )
        with open(folder / f"DIM{dim}.dat", "wb") as f:
            f.write(vp_cache(dim, veins))

    def waypoint(
        self, name: str, x: int, y: int, z: int, dim: int, kind: str = "Normal"
    ) -> None:
        folder = (
            self.game / "journeymap" / "data" / "sp" / self.world.name / "waypoints"
        )
        body = {
            "id": f"{name}_{x},{y},{z}",
            "name": name,
            "x": x,
            "y": y,
            "z": z,
            "type": kind,
            "dimensions": [dim],
            "enable": True,
        }
        with open(folder / f"{name}_{x},{y},{z}.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(body))

    def crash(self, n: int) -> None:
        name = f"crash-2026-11-11_12.00.{n:02d}-client.txt"
        (self.game / "crash-reports" / name).write_text(
            "---- Minecraft Crash Report ----\n// Oops.\n\nTime: now\nDescription: Ticking tile entity\n",
            encoding="utf-8",
        )


# --- the soak writer ---------------------------------------------------------------------------


class Game:
    """The soak's game: holds session.lock and its logs open, saves once per :meth:`cycle`."""

    def __init__(self, game_root: Path) -> None:
        self.tree = Tree(game_root)
        self.errors: list[str] = []
        self.writes = 0
        self.done: list[int] = [1]
        logs = self.tree.game / "logs"
        # held the whole session, as the game holds them
        self.lock = open(self.tree.world / "session.lock", "wb")  # noqa: SIM115
        self.lock.write(struct.pack(">q", int(time.time() * 1000)))
        self.lock.flush()
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 8)
        else:
            import fcntl

            fcntl.lockf(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.log = open(logs / "latest.log", "a", encoding="utf-8")  # noqa: SIM115
        self.fml = open(logs / "fml-client-latest.log", "a", encoding="utf-8")  # noqa: SIM115
        self.world_log = logs / "world.log"
        self.log.write(
            stamp_line(
                f"{PLAYER}[local:E:1a2b3c4d] logged in with entity id 7 at (100.5, 64.0, -200.5)",
                "Server thread",
            )
        )
        self.log.flush()

    def attempt(self, what: str, fn: Callable[..., object], *args: object) -> None:
        try:
            fn(*args)
            self.writes += 1
        except OSError as exc:  # the OS refused a save: what the soak must never see
            self.errors.append(f"{what}: {type(exc).__name__} {exc}")

    def logs(self, n: int) -> None:
        self.log.write(stamp_line(f"[CHAT] <{PLAYER}> hello {n}"))
        self.log.write(
            stamp_line(f"[CHAT] You found a Copper vein at ({n * 16}, {-n * 16})!")
        )
        if n % 4 == 0:
            self.log.write(stamp_line("Saving and pausing game...", "Server thread"))
        self.log.flush()
        self.fml.write(stamp_line(f"tick {n}", "Client thread", "INFO", "FML"))
        self.fml.flush()
        with open(self.world_log, "a", encoding="utf-8") as w:
            w.write(
                f"2026-11-11 12:00:{n % 60:02d} : {PLAYER} placed Stone:0 at [{n}, 64, 0] in Overworld\n"
            )

    def roll(self) -> None:
        """log4j's rollover: close, rename, reopen."""
        logs = self.tree.game / "logs"
        self.log.close()
        os.replace(logs / "latest.log", logs / "2026-11-11-1.log")
        self.log = open(logs / "latest.log", "a", encoding="utf-8")  # noqa: SIM115

    def cycle(self, n: int) -> None:
        tree = self.tree
        x = 100.5 + n * 20
        inv = [stack(0, 265, min(64, n), 0), stack(1, 4097, 1, 11305)]
        data = level_dat((x, 64.0, -200.5), 0 if n % 4 else -1, inv)
        self.attempt("level.dat", tree.save_level, data, n % 2 == 0)
        self.done.append(100 + n)
        self.attempt("quests", tree.save_quests, self.done)
        stats = {"stat.deaths": n // 3, "stat.craftItem.autogen.minecraft:stone": n}
        self.attempt("stats", tree.save_stats, stats)
        self.attempt(
            "vp", tree.save_vp, 0, [(c, -c, "ore.mix.copper") for c in range(n)]
        )
        if n % 3 == 0:
            self.attempt(
                "waypoint", tree.waypoint, f"Death{n}", int(x), 64, -200, 0, "Death"
            )
        if n % 5 == 0:
            self.attempt("crash", tree.crash, n)
        self.attempt("logs", self.logs, n)
        if n == 6:
            self.attempt("log rollover", self.roll)

    def close(self) -> None:
        self.log.close()
        self.fml.close()
        self.lock.close()


def soak(game_root: Path, seconds: float, result: Path, interval: float) -> None:
    """Write like a game for ``seconds``; record each refused write in ``result``."""
    game = Game(game_root)
    end = time.monotonic() + seconds
    n = 0
    while time.monotonic() < end:
        n += 1
        game.cycle(n)
        time.sleep(interval)
    game.close()
    result.write_text(
        json.dumps({"errors": game.errors, "writes": game.writes, "cycles": n}),
        encoding="utf-8",
    )


if __name__ == "__main__":
    soak(Path(sys.argv[1]), float(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4]))
