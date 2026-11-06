"""VisualProspecting's ore-vein caches, read-only (plan sections 5.4 and 7.6, WO-1.6, D127).

The format is VisualProspecting 1.5.42's (the version GTNH 2.9.0 ships; MIT, licence checked),
re-implemented from its source (``DimensionCache``, ``WorldCache``, ``ClientCache``,
``ServerCache``, ``WorldIdHandler``, ``VeinSource``, ``Utils``) [B§4.1]. Nothing here writes a
file; every file is read whole through ``gamestate.safe_open.read_bytes`` (share-delete, one
read, handle closed at once, 3 tries with backoff, which also covers VP's in-place writes) and
parsed from memory. A dimension file still unreadable after the tries is listed in
``Cache.unreadable``, apart from the files skipped on purpose, so a caller can refuse a partial
picture (``gos ores load`` does, D128).

- **World id.** VP names a world's caches by its world id, saved in the world as
  ``<world>/data/visualprospecting.dat`` (``{data: {wId: "<folder>_<uuid>"}}``), so a copied or
  renamed world still finds its caches. Without that file, a single ``<folder>_<uuid>``
  cache folder is used (the UUID part keeps ``New World`` off ``New World_2``'s caches).
- **Paths**, relative to the game folder (``.minecraft``): the **server cache**, every vein
  generated (D27), ``visualprospecting/server/<world id>/DIM<n>.dat``, saved on every autosave;
  the **client cache**, the veins one player knows (prospected, or shared by his team),
  ``visualprospecting/client/<player uuid>/<world id>/DIM<n>.dat``, saved only when he leaves
  the world or changes dimension. In single-player the server cache is in the game folder
  (``mcDataDir``) and the client cache's path is relative to the game's working folder, which
  Prism sets to the game folder, so both are next to ``saves/``, not in the world.
- **Format v3**, gzip NBT, one file per dimension: ``{version: 3, dim, ores: {palette: [mix
  name], chunkX: int[], chunkZ: int[], veinTypeIndex: int[], depleted: byte[], source:
  byte[]}, fluids: {...}}``. Only ``ores`` is read here. A vein is keyed by its centre chunk
  (VP stores the centre; the vein's centre block is ``chunk * 16 + 8``). ``source`` is VP's
  ``VeinSource`` byte. As VP loads a file: every ``*.dat`` in the folder, the dimension taken
  from ``dim`` (not the file name); an index outside the palette and the ``ore.mix.none``
  sentinel are skipped; two entries for one chunk collapse by VP's trust order (a source as
  trusted or more replaces, depleted flags join). A file without ``version`` (VP 1.4, GTNH
  2.8) or newer than 3 is skipped, with a note. VP's repair of misaligned coordinates, which
  needs the world's ore-vein pattern, is not re-implemented: VP saves only under a
  confirmed pattern, so the chunks on disk are centres.
- **Depleted** is the player's own flag (the client cache); the server cache writes ``false``.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import nbt
from gregtec.sync import _is_link

VP_DIR = "visualprospecting"
CLIENT_DIR = "client"
SERVER_DIR = "server"
WORLD_ID_FILE = Path("data", "visualprospecting.dat")  # in the world folder
FORMAT_VERSION = 3
NO_VEIN = "ore.mix.none"
FILE_LIMIT = 64 << 20  # one dimension's cache; far more than a world of veins takes
# VeinSource: the byte on disk, and its trust (a source replaces one it trusts as much or less).
SOURCES = {0: "unknown", 1: "generated", 2: "rescan", 3: "api", 4: "admin"}
TRUST = {"unknown": 0, "rescan": 1, "api": 2, "generated": 3, "admin": 3}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DAT = re.compile(r".+\.dat")


class VpError(RuntimeError):
    """The caches can't be found or read safely."""


class FormatError(ValueError):
    """Not a file VP could have written (a partial write, a bad tag); read_bytes retries it."""


@dataclass(frozen=True)
class Vein:
    dim: int
    cx: int  # the vein's centre chunk
    cz: int
    mix: str  # GT's ore mix name, e.g. ore.mix.naquadah
    depleted: bool
    source: str  # VeinSource, lower case


@dataclass
class Cache:
    """One cache folder (one world, server or one player), loaded as VP loads it."""

    path: Path
    veins: dict[tuple[int, int, int], Vein] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)  # read and used
    # "<name>: why", for files skipped on purpose (another format, a link, too big).
    problems: list[str] = field(default_factory=list)
    # "<path>: why", for files not read in 3 tries (caught mid-write, or damaged).
    unreadable: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Located:
    world: Path
    game: Path  # the game folder whose visualprospecting/ holds the caches
    world_id: str | None
    server: Path | None  # the server cache folder, if it exists
    client: Path | None  # the tracked player's client cache folder, if it exists
    player: str | None  # that player's UUID
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Merged:
    """A vein from the server cache, the client cache or both (server first)."""

    vein: Vein
    known: bool  # in the player's client cache
    cache: str  # "server" or "client": where ``vein`` came from


# --- parsing -------------------------------------------------------------------------------


def _ints(ores: dict[str, nbt.Tag], key: str) -> list[int]:
    value = ores.get(key, [])
    if not isinstance(value, list) or not all(type(v) is int for v in value):
        raise FormatError(f"ores.{key} is not an int array")
    return value


def _bytes(ores: dict[str, nbt.Tag], key: str) -> bytes:
    value = ores.get(key, b"")
    if not isinstance(value, bytes):
        raise FormatError(f"ores.{key} is not a byte array")
    return value


def parse_dimension(data: bytes) -> tuple[int | None, int, list[Vein]]:
    """(format version or None, dimension, veins in file order) of one ``DIM<n>.dat``.

    Raises ``ValueError`` on anything VP couldn't have written (a partial write, a bad tag)."""
    _, root = nbt.read(data)
    version = root.get("version")
    if version is not None and type(version) is not int:
        raise FormatError("version is not an int")
    dim = root.get("dim")
    if type(dim) is not int:
        raise FormatError("no int dim")
    if version != FORMAT_VERSION:
        return version, dim, []
    ores = root.get("ores", {})
    if not isinstance(ores, dict):
        raise FormatError("ores is not a compound")
    if (
        "palette" not in ores
    ):  # VP writes an empty compound for a dimension with no veins
        return version, dim, []
    palette = ores["palette"]
    if not isinstance(palette, list) or not all(isinstance(p, str) for p in palette):
        raise FormatError("ores.palette is not a list of strings")
    xs, zs, idx = (_ints(ores, k) for k in ("chunkX", "chunkZ", "veinTypeIndex"))
    depleted, source = _bytes(ores, "depleted"), _bytes(ores, "source")
    if not len(xs) == len(zs) == len(idx) == len(depleted) == len(source):
        raise FormatError(
            f"ores arrays differ in length ({len(xs)}, {len(zs)}, {len(idx)},"
            f" {len(depleted)}, {len(source)})"
        )
    veins = []
    for x, z, i, d, s in zip(xs, zs, idx, depleted, source, strict=True):
        if not 0 <= i < len(palette) or palette[i] == NO_VEIN:
            continue
        veins.append(Vein(dim, x, z, palette[i], d == 1, SOURCES.get(s, "unknown")))
    return version, dim, veins


def _put(veins: dict[tuple[int, int, int], Vein], vein: Vein) -> None:
    """VP's collapse of two entries for one chunk: a source as trusted or more replaces the
    stored one; either way the depleted flags join."""
    key = (vein.dim, vein.cx, vein.cz)
    old = veins.get(key)
    if old is None:
        veins[key] = vein
    elif TRUST[vein.source] >= TRUST[old.source]:
        veins[key] = Vein(*key, vein.mix, vein.depleted or old.depleted, vein.source)
    elif vein.depleted and not old.depleted:
        veins[key] = Vein(*key, old.mix, True, old.source)


def _read_parsed[T](path: Path, parse: Callable[[bytes], T]) -> T:
    """``parse`` of ``path``'s bytes, read whole through ``read_bytes`` (3 tries, a parse error
    retried like a failed read). The validator keeps what it parsed, so a file is parsed once."""
    parsed: list[T] = []
    read_bytes(path, validate=lambda d: parsed.append(parse(d)), limit=FILE_LIMIT + 1)
    return parsed[-1]


def _check_dir(path: Path) -> bool:
    """Whether ``path`` is a real folder; a link or junction is refused."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if _is_link(st):
        raise VpError(f"{path} is a link or junction; refusing it")
    return stat.S_ISDIR(st.st_mode)


def read_cache(folder: Path) -> Cache:
    """Every ``*.dat`` in ``folder`` (regular files only), as VP's ``loadVeinCache`` reads it."""
    cache = Cache(folder)
    for name in sorted(os.listdir(folder)):
        if not _DAT.fullmatch(name):
            continue
        path = folder / name
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue  # gone since the listing
        if _is_link(st) or not stat.S_ISREG(st.st_mode):
            cache.problems.append(f"{name}: not a regular file; skipped")
            continue
        if st.st_size > FILE_LIMIT:
            cache.problems.append(f"{name}: {st.st_size:,} bytes; skipped")
            continue
        try:
            version, _, veins = _read_parsed(path, parse_dimension)
        except FileNotFoundError:
            continue  # gone since the listing
        except (OSError, ValueError) as exc:
            cache.unreadable.append(f"{path}: unreadable after 3 tries ({exc})")
            continue
        if version != FORMAT_VERSION:
            what = "no version (VP 1.4, GTNH 2.8)" if version is None else version
            cache.problems.append(
                f"{name}: format {what}, not {FORMAT_VERSION}; skipped"
            )
            continue
        cache.files.append(name)
        for vein in veins:
            _put(cache.veins, vein)
    return cache


def merge(server: Cache | None, client: Cache | None) -> tuple[list[Merged], int]:
    """Every vein of both caches, by dimension and chunk, and how many chunks the two caches
    give different mixes for. The server's entry wins (it is what generated); a vein is known
    when the player's client cache has that chunk, and depleted when he marked it."""
    s = server.veins if server else {}
    c = client.veins if client else {}
    out, differ = [], 0
    for key in sorted(s.keys() | c.keys()):
        sv, cv = s.get(key), c.get(key)
        if sv is not None and cv is not None and sv.mix != cv.mix:
            differ += 1
        vein = sv or cv
        assert vein is not None
        depleted = vein.depleted or (cv is not None and cv.depleted)
        if depleted != vein.depleted:
            vein = Vein(*key, vein.mix, depleted, vein.source)
        out.append(Merged(vein, cv is not None, "server" if sv else "client"))
    return out, differ


# --- finding the caches --------------------------------------------------------------------


def _root(data: bytes) -> dict[str, nbt.Tag]:
    return nbt.read(data)[1]


def _world_id_from(root: dict[str, nbt.Tag]) -> str | None:
    """``data.wId`` of a ``visualprospecting.dat``, if it can name a folder: one path part, no
    control characters (a NUL would make every path built from it raise ``ValueError``)."""
    inner = root.get("data")
    wid = inner.get("wId") if isinstance(inner, dict) else None
    if (
        not isinstance(wid, str)
        or wid in ("", ".", "..")
        or any(c in "/\\:" or ord(c) < 32 or c == "\x7f" for c in wid)
    ):
        return None
    return wid


def world_id(world: Path) -> str | None:
    """The world id VP saved in ``world`` (``data/visualprospecting.dat``), or None."""
    path = world / WORLD_ID_FILE
    if not _check_dir(world / WORLD_ID_FILE.parent):
        return None
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if _is_link(st) or not stat.S_ISREG(st.st_mode):
        raise VpError(f"{path} is not a regular file; refusing it")
    try:
        root = _read_parsed(path, _root)  # a bad read is retried; a bad wId is not
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise VpError(f"{path}: unreadable after 3 tries ({exc})") from None
    wid = _world_id_from(root)
    if wid is None:
        raise VpError(
            f"{path}: no usable data.wId (VisualProspecting's world id, a folder name)"
        )
    return wid


def _subdirs(folder: Path) -> list[str]:
    """The real folders in ``folder`` (no links or junctions; one gone since the listing is
    skipped)."""
    if not _check_dir(folder):
        return []
    out = []
    for n in sorted(os.listdir(folder)):
        try:
            st = os.lstat(folder / n)
        except FileNotFoundError:
            continue
        if not _is_link(st) and stat.S_ISDIR(st.st_mode):
            out.append(n)
    return out


def locate(world: Path, game: Path, player: str | None = None) -> Located:
    """Find ``world``'s caches in ``game/visualprospecting/``. ``player`` (a UUID) picks the
    client cache; by default the only player with one for this world. Raises
    :class:`VpError` when that is ambiguous or unsafe (a link or junction on the way)."""
    notes: list[str] = []
    root = game / VP_DIR
    servers = root / SERVER_DIR
    clients = root / CLIENT_DIR
    if _check_dir(root):
        _check_dir(servers)
        _check_dir(clients)
    wid = world_id(world)
    if player is not None:
        player = player.lower()
    if wid is None:
        # WorldIdHandler names a world <folder>_<random UUID>; the UUID part keeps "New World"
        # from taking "New World_2"'s caches.
        named = re.compile(re.escape(world.name) + "_" + _UUID.pattern)
        guesses = sorted(
            {
                n
                for folder in (servers, *(clients / p for p in _subdirs(clients)))
                for n in _subdirs(folder)
                if named.fullmatch(n)
            }
        )
        if len(guesses) == 1:
            wid = guesses[0]
            notes.append(
                f"{world / WORLD_ID_FILE} is missing; using the only cache folder named"
                f" after the world, {wid}"
            )
        elif guesses:
            raise VpError(
                f"{world / WORLD_ID_FILE} is missing and several VP cache folders are named"
                f" after this world ({', '.join(guesses)}); can't tell which is its"
            )
        elif player is not None:
            raise VpError(
                f"player {player} has no client cache for this world: VisualProspecting has"
                f" no data for it yet ({world / WORLD_ID_FILE} is missing)"
            )
        else:
            notes.append(
                f"VisualProspecting has no data for this world yet ({world / WORLD_ID_FILE}"
                " is missing): open the world in the game once"
            )
            return Located(world, game, None, None, None, None, tuple(notes))
    server = servers / wid if _check_dir(servers / wid) else None
    if server is None:
        notes.append(f"no server cache yet ({servers / wid})")
    have = [
        p
        for p in _subdirs(clients)
        if _UUID.fullmatch(p) and _check_dir(clients / p / wid)
    ]
    if player is not None:
        if player not in have:
            listed = ", ".join(have) or "none"
            raise VpError(
                f"player {player} has no client cache for {wid} (players with one: {listed})"
            )
    elif len(have) == 1:
        player = have[0]
    elif have:
        raise VpError(
            f"several players have a client cache for {wid} ({', '.join(have)}): name one"
            " with --player <uuid>"
        )
    else:
        notes.append(
            "no client cache for this world yet: VP saves it when you leave the world or"
            " change dimension"
        )
    client = clients / player / wid if player else None
    return Located(world, game, wid, server, client, player, tuple(notes))


def read_all(
    located: Located, server: bool = True
) -> tuple[Cache | None, Cache | None]:
    """(server cache, client cache) of ``located``; ``server=False`` skips the server cache
    ([policy] ore_server_cache)."""
    s = read_cache(located.server) if server and located.server else None
    c = read_cache(located.client) if located.client else None
    return s, c


def counts(veins: Iterable[Merged]) -> dict[int, tuple[int, int, int]]:
    """{dim: (veins, known, depleted)}."""
    out: dict[int, tuple[int, int, int]] = {}
    for m in veins:
        n, k, d = out.get(m.vein.dim, (0, 0, 0))
        out[m.vein.dim] = (n + 1, k + m.known, d + m.vein.depleted)
    return out
