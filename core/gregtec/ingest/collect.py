"""``gos export collect``: copy Export's newest complete export run into GOS_HOME (WO-1.2).

D100 (5) re-scoped WO-1.2 to this collector; D101 records how it works. The run's files are
GregtecLink's, as ``docs/contracts/gregteclink-export-v1.md`` describes them.

1. **Find.** Export's game folder comes from gos.toml (``[paths] prism_instances`` and ``export``;
   ``minecraft``, else ``.minecraft``), checked as ``sync_instances.py`` checks it, and refused if it
   is, holds or is inside Play or Lab. The runs are the folders directly in
   ``<game>/gregteclink/exports/``; a link or junction at ``gregteclink`` or ``exports`` is refused.
   A folder is skipped (and reported) when it ends in ``.partial``, is a link or junction, has a
   name that is no run id, or has no readable ``manifest.json`` (a file, not a link or hard link,
   of at most 16 MiB) with ``format`` ``gregteclink-export/1``, ``"complete": true``, ``run_id``
   equal to the folder's name, ``created_utc`` and ``files``. ``"mode": "harness"``
   (dev-environment data) is skipped too, except in tests, and so is every run listed in
   ``GOS_HOME/data/packupdate/lab-runs.txt`` (:data:`LAB_RUNS`: the pack-update playbook's Lab
   exports, never Play's data, WO-1.9). The run collected is the newest by ``created_utc``,
   then name: the order the mod prunes by.
2. **Verify before writing.** Every file the manifest lists must be a file of the listed size,
   then is read whole (never more than its listed size plus one byte) through
   ``gamestate.safe_open.read_bytes`` (share-delete, one read, handle closed at once, 3 tries with
   backoff) and checked against its ``sha256``, ``bytes`` and ``lines`` in memory, so a file that
   doesn't match never reaches GOS_HOME. A name that leaves the run folder, a link, junction or
   hard link on the way to a file, and a missing file are refused. A run that fails is not
   collected, and no older run is taken in its place. Files the manifest doesn't list are not
   copied (reported).
3. **Write atomically.** The files go into a new ``GOS_HOME/data/exports/.gos-staging-<run>-<hex>/``,
   each flushed to disk, ``manifest.json`` (the bytes read from Export) last; then the folder is
   renamed to ``<run_id>/``, with 3 tries waiting 1 s then 2 s as the mod does, because Windows'
   indexer or a virus scanner can hold a file just written. Nothing is ever written to Export,
   Play or Lab.
4. **Idempotent.** If ``<run_id>/`` is already there with the same manifest bytes and every file
   matches, nothing is written. With the same manifest but a damaged or missing file, the run is
   collected again and swapped in. Another manifest under the same run id is refused, and so is a
   ``<run_id>/`` with no readable manifest (damaged, or not a collected run).
5. **Prune.** Keeps the newest ``[export] keep_collected`` collected runs (default 5, D102), and
   always the run just collected. A run to delete is renamed to ``.gos-trash-<run>-<hex>/`` first,
   so a delete that fails halfway (a file in use) never leaves a run with files missing; the next
   collect deletes what is left, with any staging folder an interrupted collect left. Folders that
   aren't collected runs are never deleted.

``GOS_HOME``, ``data`` and ``data/exports`` must not be links or junctions, and ``data/exports``
must not be, hold or be inside the Prism instances folder or an instance. One collect runs at a
time: steps 3 to 5 delete staging folders and swap runs, so a second collect while one holds
``GOS_HOME/data/locks/collect.lock`` is refused. It is an OS file lock (``fcntl.flock``,
``msvcrt.locking``), which the OS drops when the process ends, so a crashed collect leaves no
stale lock.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from gregtec import config
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest.fingerprint import atomic_write

# sync's own checks, so collect finds and link-checks Export exactly as sync does.
from gregtec.sync import _instance, _is_link, _overlap

FORMAT = "gregteclink-export/1"
MANIFEST = "manifest.json"
# Bytes. A dev-runtime manifest, listing 187 files, is about 30 KB.
MANIFEST_LIMIT = 16 << 20
PARTIAL = ".partial"
EXPORTS = Path("gregteclink", "exports")  # in Export's game folder
DATA_EXPORTS = Path("data", "exports")  # in GOS_HOME (plan 4.3)
LOCK = Path("data", "locks", "collect.lock")  # in GOS_HOME (plan 4.3's data\locks\)
# Runs the pack-update playbook exported from Lab (WO-1.9, D148): never Play's data, so a
# collect or a build into this GOS_HOME skips them (an update's sandbox GOS_HOME has no list).
LAB_RUNS = Path("data", "packupdate", "lab-runs.txt")
LAB_RUN = "a pack update's Lab run, never Play's data (D148)"
# The mod's run id (-Dgregtec.runId or the UTC time), plus the -2, -3, ... it adds on a clash.
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}(?:-[0-9]+)?")
SHA256 = re.compile(r"[0-9a-f]{64}")
STAGING, TRASH = ".gos-staging-", ".gos-trash-"
RENAME_TRIES, RENAME_DELAY = 3, 1.0  # waits 1 s, then 2 s: the mod's own rename retry
UNCHANGED, COLLECTED, REPAIRED = "unchanged", "collected", "repaired"


class CollectError(RuntimeError):
    """Refused or failed. GOS_HOME is as it was, apart from pruning leftovers."""


@dataclass(frozen=True)
class Run:
    run_id: str
    path: Path
    created_utc: str
    manifest: dict[str, Any]
    raw: bytes  # manifest.json as read from Export; GOS_HOME gets these exact bytes


@dataclass(frozen=True)
class Entry:
    """One file of a run's manifest ``files``."""

    rel: str  # posix, relative to the run folder
    sha256: str
    size: int
    lines: int


@dataclass
class Result:
    source: Path  # Export's gregteclink/exports
    run: Run
    dest: Path  # GOS_HOME/data/exports/<run_id>
    status: str  # UNCHANGED, COLLECTED or REPAIRED
    files: int  # listed files, manifest.json not counted
    size: int  # their bytes
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (folder, why)
    unlisted: list[str] = field(default_factory=list)  # in the run, not copied
    pruned: list[str] = field(default_factory=list)
    leftovers: list[Path] = field(default_factory=list)  # couldn't delete yet


def export_game(cfg: config.Config) -> Path:
    """Export's game folder. Refuses a missing or linked Export, or one that overlaps Play or Lab."""
    refusals: list[str] = []
    found = _instance(cfg.instance("export"), "Export", refusals)
    if found is not None:
        refusals += [
            f"Export: {found[0]} is the {role} instance, is inside it or holds it"
            for role in ("play", "lab")
            if _overlap(found[0], cfg.instance(role))
        ]
    if found is None or refusals:
        raise CollectError("; ".join(refusals))
    return found[1]


def exports_root(game: Path) -> Path:
    """``<game>/gregteclink/exports``, refused if either folder is missing or a link."""
    for path in (game / EXPORTS.parent, game / EXPORTS):
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            raise CollectError(
                f"{path} doesn't exist, so Export has made no run yet (GregtecLink writes "
                "it; sync_instances.py installs GregtecLink into Export)"
            ) from None
        if _is_link(st):
            raise CollectError(f"{path} is a link or junction; refusing it")
        if not stat.S_ISDIR(st.st_mode):
            raise CollectError(f"{path} is not a folder")
    return game / EXPORTS


def dest_root(gos_home: Path, forbidden: Sequence[Path] = ()) -> Path:
    """``GOS_HOME/data/exports``, made if missing; refused if linked or inside ``forbidden``."""
    root = gos_home / DATA_EXPORTS
    for path in (gos_home, gos_home / DATA_EXPORTS.parent, root):
        if os.path.lexists(path) and (_is_link(os.lstat(path)) or not path.is_dir()):
            raise CollectError(f"{path} is a link, a junction or not a folder")
    for other in forbidden:
        if _overlap(root, other):
            raise CollectError(f"{root} overlaps {other}, which collect never writes")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_manifest(path: Path) -> tuple[object, bytes]:
    # A half-written read is retried; one past the limit is cut short, so it fails json.loads.
    raw = read_bytes(path, validate=json.loads, limit=MANIFEST_LIMIT)
    return json.loads(raw), raw


def _manifest_problem(m: object, name: str, allow_harness: bool) -> str | None:
    if not isinstance(m, dict):
        return f"its {MANIFEST} is not a JSON object"
    if m.get("format") != FORMAT:
        return f"format {_clean(repr(m.get('format')))}, not {FORMAT}"
    if m.get("complete") is not True:
        return 'incomplete (manifest "complete" is not true)'
    if m.get("mode") == "harness" and not allow_harness:
        return "a harness run (dev-environment data, never game data)"
    if m.get("run_id") != name:
        return (
            f"manifest run_id {_clean(repr(m.get('run_id')))} isn't the folder's name"
        )
    if not isinstance(m.get("created_utc"), str) or not isinstance(
        m.get("files"), dict
    ):
        return "manifest has no created_utc or files"
    return None


def _candidate(root: Path, name: str, allow_harness: bool) -> Run | str:
    """The run in ``root/name``, or why it isn't one."""
    if name.endswith(PARTIAL):
        return "still being written, or its rename failed (readers skip it)"
    path = root / name
    try:
        st = os.lstat(path)
        if _is_link(st):
            return "a link or junction"
        if not stat.S_ISDIR(st.st_mode):
            return "not a folder"
        if not RUN_ID.fullmatch(name):
            return "not a run id"
        mst = os.lstat(path / MANIFEST)
        if _is_link(mst) or not stat.S_ISREG(mst.st_mode) or mst.st_nlink > 1:
            return f"its {MANIFEST} is a link, a hard link or not a file"
        if mst.st_size > MANIFEST_LIMIT:
            return f"its {MANIFEST} is over {MANIFEST_LIMIT:,} bytes"
        manifest, raw = _read_manifest(path / MANIFEST)
    except FileNotFoundError:
        return f"no {MANIFEST}"
    except (OSError, ValueError, RecursionError) as exc:  # Recursion: JSON too deep
        return f"{MANIFEST} unreadable: {exc}"
    problem = _manifest_problem(manifest, name, allow_harness)
    if problem is not None:
        return problem
    assert isinstance(manifest, dict)
    return Run(name, path, manifest["created_utc"], manifest, raw)


def lab_runs(gos_home: Path) -> frozenset[str]:
    """The Lab runs recorded in ``gos_home`` (:data:`LAB_RUNS`). An unreadable list raises
    ``OSError``, so nothing is collected or built while it can't be read."""
    try:
        text = (gos_home / LAB_RUNS).read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    return frozenset(
        x for x in map(str.strip, text.splitlines()) if RUN_ID.fullmatch(x)
    )


def mark_lab_run(gos_home: Path, run_id: str) -> None:
    """Record ``run_id`` as a Lab run: never collected or built into ``gos_home``."""
    if not RUN_ID.fullmatch(run_id):
        raise ValueError(f"not a run id: {run_id!r}")
    ids = sorted(lab_runs(gos_home) | {run_id})
    atomic_write(gos_home / LAB_RUNS, "".join(f"{x}\n" for x in ids).encode("utf-8"))


def find_runs(
    root: Path, allow_harness: bool = False, skip: frozenset[str] = frozenset()
) -> tuple[list[Run], list[tuple[str, str]]]:
    """The complete runs in ``root``, newest first, and (name, why) for every other entry
    (``skip``: run ids never taken, the Lab runs)."""
    runs: list[Run] = []
    skipped: list[tuple[str, str]] = []
    for name in sorted(os.listdir(root)):
        found = LAB_RUN if name in skip else _candidate(root, name, allow_harness)
        if isinstance(found, Run):
            runs.append(found)
        else:
            skipped.append((name, found))
    runs.sort(key=lambda r: (r.created_utc, r.run_id), reverse=True)
    return runs, skipped


def _safe_rel(rel: str) -> bool:
    """A posix path that stays inside the run folder: no empty, ``.`` or ``..`` part, no
    backslash, colon (an NTFS stream) or control character."""
    return all(
        part not in ("", ".", "..")
        and not any(c in "\\:" or ord(c) < 32 or ord(c) == 127 for c in part)
        for part in rel.split("/")
    )


def entries(run: Run) -> list[Entry]:
    """The manifest's ``files``, each a safe name with a valid sha256, bytes and lines."""
    out = []
    for rel, e in run.manifest["files"].items():
        if not _safe_rel(rel) or rel == MANIFEST:
            raise CollectError(
                f"run {run.run_id}: its manifest lists {_clean(repr(rel))}, which is not a "
                "file name inside the run"
            )
        if not (
            isinstance(e, dict)
            and isinstance(e.get("sha256"), str)
            and SHA256.fullmatch(e["sha256"])
            and all(type(e.get(k)) is int and e[k] >= 0 for k in ("bytes", "lines"))
        ):
            raise CollectError(
                f"run {run.run_id}: the manifest entry of {_clean(rel)} has no valid "
                "sha256, bytes and lines"
            )
        out.append(Entry(rel, e["sha256"], e["bytes"], e["lines"]))
    return out


def _mismatch(data: bytes, entry: Entry) -> str | None:
    digest = hashlib.sha256(data).hexdigest()
    lines = data.count(b"\n")
    if (len(data), digest, lines) == (entry.size, entry.sha256, entry.lines):
        return None
    return (
        f"{len(data):,} bytes, sha256 {digest[:12]}..., {lines:,} lines; the manifest "
        f"says {entry.size:,}, {entry.sha256[:12]}..., {entry.lines:,}"
    )


def read_verified(run: Run, entry: Entry) -> bytes:
    """``entry``'s bytes from Export, refused unless they match the manifest after 3 reads."""
    path = run.path
    try:
        for part in entry.rel.split("/"):
            path = path / part
            st = os.lstat(path)
            if _is_link(st):
                raise CollectError(
                    f"run {run.run_id}: {_clean(str(path))} is a link or junction; "
                    "refusing the run"
                )
    except FileNotFoundError:
        raise CollectError(
            f"run {run.run_id}: {_clean(entry.rel)} is missing; not collected"
        ) from None
    if not stat.S_ISREG(st.st_mode):
        raise CollectError(
            f"run {run.run_id}: {_clean(str(path))} is not a file; not collected"
        )
    if st.st_nlink > 1:  # as sync refuses them in Export: its bytes live elsewhere too
        raise CollectError(
            f"run {run.run_id}: {_clean(str(path))} is a hard link; refusing the run"
        )
    if st.st_size != entry.size:  # checked before reading, so a huge file is never read
        raise CollectError(
            f"run {run.run_id}: {_clean(entry.rel)} is {st.st_size:,} bytes; the "
            f"manifest says {entry.size:,}; not collected"
        )

    def check(data: bytes) -> None:
        problem = _mismatch(data, entry)
        if problem is not None:
            raise ValueError(problem)

    try:
        # One byte past the listed size, so a file that grew since lstat fails the check.
        return read_bytes(path, validate=check, limit=entry.size + 1)
    except FileNotFoundError:
        raise CollectError(
            f"run {run.run_id}: {_clean(entry.rel)} went away while it was read"
        ) from None
    except ValueError as exc:
        raise CollectError(
            f"run {run.run_id}: {_clean(entry.rel)} doesn't match the manifest in 3 "
            f"reads ({exc}); not collected"
        ) from None


def _unlisted(run: Run, listed: Sequence[Entry]) -> list[str]:
    """Entries of the run folder that are neither listed nor manifest.json. Links aren't followed."""
    files = {e.rel for e in listed} | {MANIFEST}
    dirs = {e.rel.rsplit("/", 1)[0] for e in listed if "/" in e.rel}
    dirs |= {d.rsplit("/", i)[0] for d in list(dirs) for i in range(d.count("/") + 1)}
    out: list[str] = []

    def walk(rel: str) -> None:
        for name in sorted(os.listdir(run.path / rel)):
            child = f"{rel}/{name}" if rel else name
            st = os.lstat(run.path / child)
            if child in dirs and stat.S_ISDIR(st.st_mode) and not _is_link(st):
                walk(child)
            elif child not in files:
                out.append(child)

    walk("")
    return out


def _damage(dest: Path, listed: Sequence[Entry]) -> str | None:
    """None if every listed file in ``dest`` (GOS's own copy) matches; else the first that doesn't."""
    for entry in listed:
        path = dest / entry.rel
        try:
            st = os.lstat(path)
            if _is_link(st) or not stat.S_ISREG(st.st_mode) or st.st_size != entry.size:
                return f"{entry.rel} is not a file of the listed size"
            with open(path, "rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != entry.sha256:
                    return f"{entry.rel} doesn't match its sha256"
        except OSError as exc:
            return f"{entry.rel}: {exc}"
    return None


def _write_new(staging: Path, rel: str, data: bytes) -> None:
    """Write ``staging/rel`` and flush it to disk before the run's rename.

    Folders are made one level at a time, never ``staging`` itself: if the staging folder was
    deleted under this collect, the write fails instead of quietly making a new one."""
    path = staging
    *folders, name = rel.split("/")
    for part in folders:
        path = path / part
        path.mkdir(exist_ok=True)
    with open(path / name, "xb") as f:  # a fresh staging folder: nothing is there yet
        f.write(data)
        f.flush()
        os.fsync(f.fileno())  # else a power cut can keep the rename but not the data


def _rename(src: Path, dst: Path) -> None:
    for attempt in range(RENAME_TRIES):
        try:
            os.rename(src, dst)
            return
        except PermissionError:  # Windows: a scanner still holds a file in it
            if attempt == RENAME_TRIES - 1:
                raise
            time.sleep(RENAME_DELAY * 2**attempt)


def _delete(path: Path) -> bool:
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return True


def _cleanup(root: Path) -> list[Path]:
    """Delete staging and trash folders an earlier collect left; return those still in use."""
    left = []
    for name in sorted(os.listdir(root)):
        path = root / name
        if (
            name.startswith((STAGING, TRASH))
            and not _is_link(os.lstat(path))
            and path.is_dir()
            and not _delete(path)
        ):
            left.append(path)
    return left


def prune(root: Path, keep: int, current: str) -> tuple[list[str], list[Path]]:
    """Delete collected runs past the newest ``keep``, never ``current``: (deleted, leftovers)."""
    runs = []
    for name in sorted(os.listdir(root)):
        path = root / name
        if not RUN_ID.fullmatch(name):
            continue
        try:
            if _is_link(os.lstat(path)) or not path.is_dir():
                continue
            m = json.loads((path / MANIFEST).read_bytes())
        except (OSError, ValueError, RecursionError):
            continue  # not a collected run, so never deleted
        if (
            isinstance(m, dict)
            and m.get("format") == FORMAT
            and m.get("run_id") == name
            and isinstance(m.get("created_utc"), str)
        ):
            runs.append((m["created_utc"], name))
    runs.sort(reverse=True)
    pruned: list[str] = []
    left: list[Path] = []
    for _created, name in runs[keep:]:
        if name == current:
            continue
        trash = root / f"{TRASH}{name}-{secrets.token_hex(4)}"
        try:
            _rename(root / name, trash)  # never a run with files missing
        except OSError:
            left.append(root / name)
            continue
        pruned.append(name)
        if not _delete(trash):
            left.append(trash)
    return pruned, left


@contextlib.contextmanager
def lock(gos_home: Path) -> Iterator[None]:
    """Hold ``GOS_HOME/data/locks/collect.lock`` for one collect or ``gos db build``; refuse if
    another holds it."""
    path = gos_home / LOCK
    for p in (path.parent, path):
        if os.path.lexists(p) and _is_link(os.lstat(p)):
            raise CollectError(f"{p} is a link or junction; refusing it")
    path.parent.mkdir(exist_ok=True)  # data/ is there: dest_root made data/exports
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags | getattr(os, "O_BINARY", 0))
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # byte 0; the file stays empty
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise CollectError(
                f"another gos export collect or gos db build is running (it holds {path}); "
                "try again when it has finished"
            ) from None
        try:
            yield
        finally:
            if sys.platform == "win32":
                # Windows asks for an unlock before the close.
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def collect(
    game: Path,
    gos_home: Path,
    keep: int,
    *,
    forbidden: Sequence[Path] = (),
    allow_harness: bool = False,
) -> Result:
    """Collect the newest complete run of ``game``'s exports into ``gos_home``, never one of
    its :func:`lab_runs`.

    ``allow_harness`` is for tests only: the contract keeps harness runs out of game data.
    Raises :class:`CollectError` (or ``OSError``); GOS_HOME then holds no part of the run.
    """
    source = exports_root(game)
    root = dest_root(gos_home, forbidden)
    with lock(gos_home):
        return _collect(source, root, keep, allow_harness, lab_runs(gos_home))


def _collect(
    source: Path, root: Path, keep: int, allow_harness: bool, skip: frozenset[str]
) -> Result:
    leftovers = _cleanup(root)
    runs, skipped = find_runs(source, allow_harness, skip)
    if not runs:
        why = "".join(f"\n  skipped {_clean(n)}: {w}" for n, w in skipped) or " (empty)"
        raise CollectError(f"no complete run in {source}{why}")
    run = runs[0]
    listed = entries(run)
    dest = root / run.run_id
    status = COLLECTED
    if os.path.lexists(dest):
        if _is_link(os.lstat(dest)) or not dest.is_dir():
            raise CollectError(f"{dest} is a link, a junction or not a folder")
        try:
            held = (dest / MANIFEST).read_bytes()
        except OSError as exc:
            raise CollectError(
                f"{dest} is there but has no readable {MANIFEST} ({exc}), so it is damaged "
                f"or no collected run. Move it out of {root}, then collect again"
            ) from None
        if held != run.raw:
            raise CollectError(
                f"{dest} holds another run with the id {run.run_id}. Move it out of "
                f"{root}, then collect again"
            )
        status = UNCHANGED if _damage(dest, listed) is None else REPAIRED
    if status != UNCHANGED:
        unlisted = _unlisted(run, listed)
        staging = root / f"{STAGING}{run.run_id}-{secrets.token_hex(4)}"
        # Not mkdtemp: its mode 0o700 gives the folder an owner-only ACL on Windows, and
        # gos-dev may read GOS_HOME's data (D82).
        os.mkdir(staging)
        try:
            for entry in listed:
                _write_new(staging, entry.rel, read_verified(run, entry))
            _write_new(staging, MANIFEST, run.raw)  # last, as the mod writes it
            if status == REPAIRED:
                trash = root / f"{TRASH}{run.run_id}-{secrets.token_hex(4)}"
                _rename(dest, trash)
                try:
                    _rename(staging, dest)
                except BaseException:
                    _rename(trash, dest)
                    raise
                if not _delete(trash):
                    leftovers.append(trash)
            else:
                _rename(staging, dest)
        except BaseException:  # Ctrl+C too: no half-collected run is left behind
            _delete(staging)
            raise
    else:
        unlisted = []
    pruned, left = prune(root, keep, run.run_id)
    return Result(
        source=source,
        run=run,
        dest=dest,
        status=status,
        files=len(listed),
        size=sum(e.size for e in listed),
        skipped=skipped,
        unlisted=unlisted,
        pruned=pruned,
        leftovers=leftovers + left,
    )


def _clean(text: str) -> str:
    """Export's text (gos-dev can write Export) without C0 or C1 control characters, for the
    terminal."""
    return "".join("?" if ord(c) < 32 or 127 <= ord(c) < 160 else c for c in text)


def _count(counts: object, key: str) -> str:
    value = counts.get(key) if isinstance(counts, dict) else None
    return f"{value:,}" if type(value) is int else "?"


def _nei(m: dict[str, Any]) -> str:
    parts = m.get("parts") if isinstance(m.get("parts"), dict) else {}
    nei = parts.get("nei")
    problems = m.get("part_errors"), m.get("part_warnings")
    error, warning = (p.get("nei") if isinstance(p, dict) else None for p in problems)
    counts = m.get("counts")
    if nei == "ok":
        line = (
            f"ok, {_count(counts, 'nei_files')} files, "
            f"{_count(counts, 'nei_recipes')} recipes"
        )
        return line + (f"; warning: {_clean(str(warning))}" if warning else "")
    if nei == "absent":
        return "absent: Export has no NEI 2.8.172 (mod/README.md, the NEI swap)"
    if nei == "skipped_server":
        return "none (a dedicated-server run)"
    return f"{_clean(str(nei))}" + (f": {_clean(str(error))}" if error else "")


def report(result: Result, out: TextIO) -> None:
    run, m = result.run, result.run.manifest
    counts = m.get("counts")
    link = m.get("gregteclink")
    version = link.get("version") if isinstance(link, dict) else None
    print(f"Export: {result.source}", file=out)
    for name, why in result.skipped:
        print(f"  skipped {_clean(name)}: {why}", file=out)
    print(
        f"Run {run.run_id}: created {_clean(run.created_utc)}, mode "
        f"{_clean(str(m.get('mode')))}, GregtecLink {_clean(str(version))}, GT "
        f"{_clean(str(m.get('gt_version')))}, NEI {_clean(str(m.get('nei_version')))}",
        file=out,
    )
    print(
        f"  GT: {_count(counts, 'recipe_maps')} recipe maps, "
        f"{_count(counts, 'recipes')} recipes, {_count(counts, 'machines')} machines, "
        f"{_count(counts, 'items')} items, {_count(counts, 'fluids')} fluids",
        file=out,
    )
    print(f"  NEI: {_nei(m)}", file=out)
    size = f"{result.files} files, {result.size / 1e6:,.1f} MB"
    if result.status == UNCHANGED:
        print(
            f"Already collected: {result.dest} holds this run and every file matches "
            "its manifest; nothing written.",
            file=out,
        )
    else:
        if result.status == REPAIRED:
            print(f"Collected again into {result.dest}", file=out)
            print("  (the copy there didn't match its manifest)", file=out)
        else:
            print(f"Collected into {result.dest}", file=out)
        print(
            f"  {size}, each matching the manifest's sha256, size and line count.",
            file=out,
        )
    for rel in result.unlisted:
        print(f"  not copied (not in the manifest): {_clean(rel)}", file=out)
    for name in result.pruned:
        print(f"  pruned {name} (keeping the newest collected runs)", file=out)
    for path in result.leftovers:
        print(f"  couldn't delete {path} (in use?); the next collect retries", file=out)


def main(config_path: Path | None, out: TextIO) -> int:
    """0: collected, or already collected; 1: refused or failed; 2: bad gos.toml."""
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    forbidden = [cfg.prism_instances, *map(cfg.instance, ("play", "export", "lab"))]
    try:
        game = export_game(cfg)
        result = collect(game, cfg.gos_home, cfg.keep_collected, forbidden=forbidden)
    except (CollectError, OSError) as exc:
        print(f"Not collected: {exc}", file=out)
        return 1
    report(result, out)
    return 0
