r"""Verify an off-instance WORLD backup by restoring it into a scratch folder (WO-11.2; plan 4.6,
10 "Ops"; D75, D333). **Clayton runs this** from GOS_APP: gos-dev can't read
``GOS_HOME\backups\world`` (D75)::

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\verify_world_backup.py
    ... verify_world_backup.py [--zip FILE] [--backups DIR] [--work DIR] [--keep] [--config PATH]

It takes the newest ``*.zip`` in ``GOS_HOME\backups\world`` (the copies ``backup_world.ps1``
makes of ServerUtilities' backups; ``--zip`` names one), copies it through
:func:`gregtec.gamestate.safe_open.safe_open` into a new scratch folder (the system temp folder,
or ``--work``), and checks the copy:

1. every zip entry's CRC (``zipfile``), and no entry that would land outside the folder;
2. one world in it: ``level.dat`` (the shortest path, so a world folder at the zip's root or one
   level down both work), gzip'd NBT whose root is a compound;
3. every region file (``region/`` and each ``DIM*/region/``, ``.mca`` and ``.mcr``): a whole
   number of 4 KiB sectors, a location table whose chunks all lie inside the file, and every
   chunk's length and compression byte (1 gzip, 2 zlib) right and its data inflating to an NBT
   compound.

It extracts the world into ``<work>\world`` and, unless ``--keep``, removes the scratch folder.
It **never writes into a Prism instance** (``--work`` in or around ``[paths] prism_instances``
or the backups folder is refused) and never restores over Play: putting a backup in place is
Clayton's manual step with the game closed (docs/runbooks/ops.md "World restore"). Exit 0 when
every check passes, 1 when any fails, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import struct
import sys
import tempfile
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TextIO

from gregtec import config
from gregtec.gamestate.safe_open import copy_to

SECTOR = 4096
TAG_COMPOUND = 10
REGION_SUFFIXES = (".mca", ".mcr")
WORLD_BACKUPS = Path("backups", "world")  # plan 4.3, as playbook.WORLD_BACKUPS


@dataclass
class Report:
    zip_name: str = ""
    entries: int = 0
    regions: int = 0
    chunks: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def newest_zip(folder: Path) -> Path | None:
    zips = [p for p in folder.glob("*.zip") if p.is_file()] if folder.is_dir() else []
    return max(zips, key=lambda p: (p.stat().st_mtime, p.name), default=None)


def copy_in(src: Path, dest: Path, tries: int = 3) -> None:
    """Copy ``src`` with share-delete reads, retried (plan 5.5); the handle is closed after."""
    last: OSError | None = None
    for _ in range(tries):
        try:
            with open(dest, "wb") as out:
                copy_to(src, out)
            return
        except OSError as exc:
            last = exc
    assert last is not None
    raise last


def _safe(name: str) -> PurePosixPath | None:
    rel = PurePosixPath(name.rstrip("/"))
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(p in ("", ".", "..") for p in rel.parts)
    ):
        return None
    return rel


def nbt_root_is_compound(data: bytes) -> bool:
    """An NBT document starts with a named compound: tag 10, a 2-byte name length, the name."""
    if len(data) < 3 or data[0] != TAG_COMPOUND:
        return False
    (n,) = struct.unpack(">H", data[1:3])
    return len(data) >= 3 + n + 1  # at least its end tag after the name


def check_level_dat(path: Path) -> str | None:
    try:
        data = gzip.decompress(path.read_bytes())
    except (OSError, EOFError, zlib.error) as exc:
        return f"level.dat isn't gzip: {exc}"
    if not nbt_root_is_compound(data):
        return "level.dat isn't an NBT compound"
    return None


def check_region(path: Path) -> tuple[int, list[str]]:
    """(chunks checked, problems) for one region file."""
    data = path.read_bytes()
    name = path.name
    if not data:
        return 0, []  # the game makes empty region files; they hold nothing
    if len(data) % SECTOR or len(data) < 2 * SECTOR:
        return 0, [f"{name}: {len(data)} bytes is not a whole region file"]
    sectors = len(data) // SECTOR
    problems: list[str] = []
    chunks = 0
    for i in range(1024):
        (loc,) = struct.unpack(">I", data[i * 4 : i * 4 + 4])
        offset, count = loc >> 8, loc & 0xFF
        if loc == 0:
            continue
        if offset < 2 or count == 0 or offset + count > sectors:
            problems.append(f"{name}: chunk {i} points outside the file")
            continue
        start = offset * SECTOR
        (length,) = struct.unpack(">I", data[start : start + 4])
        kind = data[start + 4]
        if length < 1 or length > count * SECTOR - 4:
            problems.append(f"{name}: chunk {i} has a bad length")
            continue
        body = data[start + 5 : start + 4 + length]
        try:
            if kind == 1:
                raw = gzip.decompress(body)
            elif kind == 2:
                raw = zlib.decompress(body)
            else:
                problems.append(f"{name}: chunk {i} has compression {kind}")
                continue
        except (OSError, EOFError, zlib.error):
            problems.append(f"{name}: chunk {i} doesn't inflate")
            continue
        if not nbt_root_is_compound(raw):
            problems.append(f"{name}: chunk {i} isn't NBT")
            continue
        chunks += 1
    return chunks, problems


def verify_zip(zip_path: Path, into: Path) -> Report:
    """Check the zip, extract its world into ``into`` (an empty folder) and check that."""
    report = Report(zip_name=zip_path.name)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        report.problems.append(f"not a readable zip: {exc}")
        return report
    with zf:
        infos = zf.infolist()
        report.entries = len(infos)
        try:
            bad = zf.testzip()  # reads every entry, checking its CRC
        except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as exc:
            report.problems.append(f"an entry can't be read: {exc}")
            return report
        if bad is not None:
            report.problems.append(f"entry {bad!r} fails its CRC")
            return report
        levels = []
        for info in infos:
            rel = _safe(info.filename)
            if rel is None:
                report.problems.append(f"unsafe entry {info.filename!r}")
                continue
            if rel.name == "level.dat" and not info.is_dir():
                levels.append(rel)
        if report.problems:
            return report
        if not levels:
            report.problems.append("no level.dat: not a world backup")
            return report
        level = min(levels, key=lambda r: (len(r.parts), str(r)))
        top = level.parent
        for info in infos:
            rel = _safe(info.filename)
            assert rel is not None
            if info.is_dir() or (
                top.parts and rel.parts[: len(top.parts)] != top.parts
            ):
                continue
            out = into.joinpath(*rel.parts[len(top.parts) :])
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    why = check_level_dat(into / "level.dat")
    if why is not None:
        report.problems.append(why)
    for region in sorted(into.rglob("*")):
        if region.suffix not in REGION_SUFFIXES or region.parent.name != "region":
            continue
        report.regions += 1
        n, problems = check_region(region)
        report.chunks += n
        report.problems += [
            f"{region.relative_to(into).as_posix()}: {p}" for p in problems
        ]
    return report


def no_room(folder: Path, need: int) -> str | None:
    """Why ``folder``'s drive can't take ``need`` more bytes (None: it can)."""
    free = shutil.disk_usage(folder).free
    if free >= need:
        return None
    gib = 1 << 30
    return (
        f"only {free / gib:.1f} GiB free where the check works ({folder}); it needs "
        f"{need / gib:.1f} GiB: free some space or pass --work on a bigger drive"
    )


def check_copy(src: Path, scratch: Path) -> Report:
    """Copy ``src`` into ``scratch`` and check it there, with room checked first: twice the zip
    for the copy, then twice its uncompressed size for the world (review L4). A file that can't
    be read (locked, access denied) is a problem, not a traceback."""
    report = Report(zip_name=src.name)
    try:
        why = no_room(scratch, 2 * src.stat().st_size)
        if why is None:
            copy = scratch / src.name
            copy_in(src, copy)
            try:
                with zipfile.ZipFile(copy) as zf:
                    size = sum(i.file_size for i in zf.infolist())
            except zipfile.BadZipFile:
                size = 0  # verify_zip says why
            why = no_room(scratch, 2 * size)
            if why is None:
                world = scratch / "world"
                world.mkdir()
                report = verify_zip(copy, world)
            copy.unlink(missing_ok=True)
    except OSError as exc:
        why = f"can't read or copy {src.name}: {exc}"
    if why is not None:
        report.problems.append(why)
    return report


def refuse_work(work: Path, forbidden: Sequence[Path]) -> str | None:
    resolved = work.resolve()
    for place in forbidden:
        p = place.resolve()
        if resolved == p or p in resolved.parents or resolved in p.parents:
            return f"{work} is in or around {place}: this never writes there"
    if work.exists() and (not work.is_dir() or any(work.iterdir())):
        return f"{work} is not an empty folder"
    return None


def run(
    zip_path: Path | None,
    backups: Path,
    work: Path | None,
    keep: bool,
    forbidden: Sequence[Path],
    out: TextIO,
) -> int:
    src = zip_path or newest_zip(backups)
    if src is None:
        print(f"FAIL no world backup zip in {backups}", file=out)
        return 1
    if work is not None:
        why = refuse_work(work, [*forbidden, backups])
        if why is not None:
            print(f"Refused: {why}.", file=out)
            return 2
        work.mkdir(parents=True, exist_ok=True)
        scratch = work
    else:
        scratch = Path(tempfile.mkdtemp(prefix="gos-world-check-"))
    try:
        report = check_copy(src, scratch)
    finally:
        if not keep:
            shutil.rmtree(scratch, ignore_errors=True)
    print(
        f"{src.name}: {report.entries} entries, {report.regions} region files, "
        f"{report.chunks} chunks read",
        file=out,
    )
    for problem in report.problems[:50]:
        print(f"FAIL {problem}", file=out)
    if report.problems:
        print(
            f"WORLD BACKUP CHECK FAILED ({len(report.problems)} problems): don't rely on "
            "this backup; check an older one with --zip.",
            file=out,
        )
        return 1
    kept = f" (kept in {scratch / 'world'})" if keep else ""
    print(f"ok   the world restores and reads{kept}", file=out)
    return 0


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    p = argparse.ArgumentParser(
        prog="verify_world_backup.py",
        description="Restore a world backup into a scratch folder and check it (WO-11.2).",
    )
    p.add_argument("--zip", type=Path, default=None, help="this backup zip")
    p.add_argument(
        "--backups",
        type=Path,
        default=None,
        help="the world backups folder (default: GOS_HOME/backups/world)",
    )
    p.add_argument(
        "--work", type=Path, default=None, help="a new or empty scratch folder"
    )
    p.add_argument("--keep", action="store_true", help="keep the restored world")
    p.add_argument("--config", type=Path, default=None)
    args = p.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    backups = args.backups or cfg.gos_home / WORLD_BACKUPS
    # never in Prism, GOS_APP, the vault or the core's data (review L2)
    forbidden = [
        cfg.prism_instances,
        cfg.prism_root,
        cfg.gos_app,
        cfg.gos_home / "vault",
        cfg.data_dir,
    ]
    return run(args.zip, backups, args.work, args.keep, forbidden, out)
