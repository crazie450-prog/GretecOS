"""Play's fingerprint and the stale flag (WO-1.4, plan section 5.2 step 4, [A§5.5], D120).

The fingerprint is the SHA-256 of a listing: one ``<relpath>\\t<sha256>\\n`` line per file, sorted
by relpath, UTF-8. The files are, in **Play** (never Export, whose NEI and GregtecLink differ):

- ``mmc-pack.json`` in the instance folder (Minecraft, Forge and the LWJGL components);
- every ``*.jar`` and ``*.zip`` under ``mods/``, at any depth (Prism's ``.disabled`` files are not
  loaded, so they are not in it);
- every ``*.zs`` under ``scripts/``;
- ``config/GTNewHorizons/dreamcraft.cfg`` and the whitelisted recipe configs, ``[fingerprint]
  configs`` in gos.toml (default: GT's recipe-affecting files; the whitelist is a VERIFY).

Relpaths are posix, relative to the game folder (``mmc-pack.json`` to the instance folder).
Anything that matches Export's ``[sync.export] exclude`` is left out: sync never copies it to
Export, so it can't change an export, and an export never makes Play look stale. Links and
junctions are never followed or hashed; they are noted. The same files give the same fingerprint
on any PC, whatever the mtimes.

Hashes are cached per file by (size, mtime) in ``GOS_HOME/data/cache/fingerprint.json``
[A§5.5], so a launch hashes only what changed. Play is only read, through ``safe_open``.

State, all in ``GOS_HOME/data/fingerprint/`` (written atomically, by Clayton's processes only):

- ``runs/<run_id>.tsv``: the listing Play had when that export ran (its SHA-256 is the
  fingerprint), computed before sync. The newest 20 are kept, and always the last export.
- ``play.tsv``: Play's listing at the last check (pre-launch, post-exit or an export).
- ``state.json``: the exports recorded (newest last), the last check and the **stale flag**:
  stale when Play's fingerprint differs from the last export's, or no export is recorded yet.

WO-1.3's ``export_run.fingerprint`` takes the same value once the export pipeline loads its runs
into the DB.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec import config
from gregtec.gamestate.safe_open import read_bytes, sha256_file
from gregtec.sync import _is_link, excluded
from gregtec.tweaks import GAME_DIRS

FORMAT = "gos-fingerprint/1"
MMC_PACK = "mmc-pack.json"  # in the instance folder
DREAMCRAFT = "config/GTNewHorizons/dreamcraft.cfg"  # pack version (plan 5.2 step 4)
MOD_SUFFIXES = (".jar", ".zip")
SCRIPT_SUFFIX = ".zs"
FP_DIR = Path("data", "fingerprint")  # in GOS_HOME
STATE = FP_DIR / "state.json"
PLAY_LISTING = FP_DIR / "play.tsv"
RUNS = FP_DIR / "runs"
CACHE = Path("data", "cache", "fingerprint.json")
KEEP_RUNS = 20
# GregtecLink's run id (see the export contract), so a run id is always one safe file name.
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}(?:-[0-9]+)?")
MAX_STATE = 4 << 20  # bytes; a state file is a few KB

Entries = tuple[tuple[str, str], ...]  # (relpath, sha256), sorted by relpath


class FingerprintError(Exception):
    """Play can't be fingerprinted (no instance, a file can't be read)."""


def listing(entries: Entries) -> bytes:
    return "".join(f"{rel}\t{sha}\n" for rel, sha in entries).encode("utf-8")


def parse_listing(data: bytes) -> Entries:
    """The entries of a listing; a line that isn't ``relpath\\tsha256`` raises ValueError."""
    out = []
    for line in data.decode("utf-8").splitlines():
        rel, sep, sha = line.partition("\t")
        if not sep or not rel or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"not a fingerprint line: {line!r}")
        out.append((rel, sha))
    return tuple(out)


@dataclass(frozen=True)
class Fingerprint:
    digest: str
    entries: Entries
    notes: tuple[str, ...] = ()

    @classmethod
    def of(cls, entries: Entries, notes: Sequence[str] = ()) -> Fingerprint:
        entries = tuple(sorted(entries))
        return cls(hashlib.sha256(listing(entries)).hexdigest(), entries, tuple(notes))

    def counts(self) -> str:
        mods = sum(1 for rel, _ in self.entries if rel.startswith("mods/"))
        scripts = sum(1 for rel, _ in self.entries if rel.startswith("scripts/"))
        configs = sum(1 for rel, _ in self.entries if rel.startswith("config/"))
        other = len(self.entries) - mods - scripts - configs
        return f"{mods} mods, {scripts} scripts, {configs} configs, {other} other"


@dataclass(frozen=True)
class Changes:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def summary(self) -> str:
        return (
            f"{len(self.added)} added, {len(self.removed)} removed, "
            f"{len(self.changed)} changed"
        )

    def lines(self, limit: int | None = None) -> list[str]:
        rows = [
            *(f"+ {p}" for p in self.added),
            *(f"- {p}" for p in self.removed),
            *(f"~ {p}" for p in self.changed),
        ]
        if limit is not None and len(rows) > limit:
            rows = [*rows[:limit], f"... and {len(rows) - limit} more"]
        return rows


def changes(old: Entries, new: Entries) -> Changes:
    a, b = dict(old), dict(new)
    return Changes(
        added=tuple(sorted(set(b) - set(a))),
        removed=tuple(sorted(set(a) - set(b))),
        changed=tuple(sorted(p for p in set(a) & set(b) if a[p] != b[p])),
    )


# --- hashing --------------------------------------------------------------------------------


class HashCache:
    """sha256 per absolute path, reused while the file's size and mtime are the same."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.entries: dict[str, list[Any]] = {}
        self.used: dict[str, list[Any]] = {}
        if path is None:
            return
        try:
            data = json.loads(read_bytes(path))
        except (OSError, ValueError):
            return  # missing or damaged: start empty
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            self.entries = {
                k: v
                for k, v in data["files"].items()
                if isinstance(v, list)
                and len(v) == 3
                and all(isinstance(x, int) for x in v[:2])
                and isinstance(v[2], str)
                and re.fullmatch(r"[0-9a-f]{64}", v[2])
            }

    def sha256(self, path: Path, st: os.stat_result) -> str:
        key = str(path)
        hit = self.entries.get(key)
        if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            sha = str(hit[2])
        else:
            sha = sha256_file(path)
        self.used[key] = [st.st_size, st.st_mtime_ns, sha]
        return sha

    def save(self, root: Path) -> None:
        """Keep this computation's entries and every entry outside ``root``."""
        if self.path is None:
            return
        prefix = os.path.join(str(root), "")
        kept = {k: v for k, v in self.entries.items() if not k.startswith(prefix)}
        kept.update(self.used)
        body = {
            "format": "gos-fingerprint-cache/1",
            "files": dict(sorted(kept.items())),
        }
        atomic_write(self.path, json.dumps(body, indent=0).encode("utf-8"))


def game_folder(instance: Path) -> Path:
    """Prism's game folder: ``minecraft``, else ``.minecraft``."""
    for name in GAME_DIRS:
        if (instance / name).is_dir():
            return instance / name
    raise FingerprintError(f"{instance} has no minecraft or .minecraft folder")


def _walk(root: Path, rel: str, out: list[tuple[str, Path]], notes: list[str]) -> None:
    """Every regular file under ``root/rel``; links and junctions are noted, never followed."""
    try:
        names = sorted(os.listdir(root / rel))
    except FileNotFoundError:
        return
    except OSError as exc:
        raise FingerprintError(f"can't list {root / rel}: {exc}") from exc
    for name in names:
        child = f"{rel}/{name}"
        try:
            st = os.lstat(root / child)
        except OSError as exc:
            raise FingerprintError(f"can't read {root / child}: {exc}") from exc
        if _is_link(st):
            notes.append(
                f"{child} is a link or junction: not followed, not in the fingerprint"
            )
        elif stat.S_ISDIR(st.st_mode):
            _walk(root, child, out, notes)
        elif stat.S_ISREG(st.st_mode):
            out.append((child, root / child))


def files(
    instance: Path, exclude: Sequence[str], configs: Sequence[str]
) -> tuple[list[tuple[str, Path]], list[str]]:
    """The (relpath, path) of every file in the fingerprint, and notes. Reads only names."""
    game = game_folder(instance)
    notes: list[str] = []
    out: list[tuple[str, Path]] = []
    if (instance / MMC_PACK).is_file():
        out.append((MMC_PACK, instance / MMC_PACK))
    else:
        notes.append(f"{instance / MMC_PACK} is missing")
    found: list[tuple[str, Path]] = []
    for folder in ("mods", "scripts", "config"):
        _walk(game, folder, found, notes)
    wanted = (DREAMCRAFT, *configs)
    for rel, path in found:
        name = rel.lower()
        if rel.startswith("mods/"):
            keep = name.endswith(MOD_SUFFIXES)
        elif rel.startswith("scripts/"):
            keep = name.endswith(SCRIPT_SUFFIX)
        else:
            keep = excluded(rel, wanted)  # rel or a folder above it is whitelisted
        if keep and not excluded(rel, exclude):
            out.append((rel, path))
    if not any(rel == DREAMCRAFT for rel, _ in out):
        notes.append(f"{DREAMCRAFT} is missing")
    return out, notes


def compute(
    instance: Path,
    exclude: Sequence[str],
    configs: Sequence[str],
    cache: HashCache | None = None,
) -> Fingerprint:
    """Fingerprint the Prism instance folder ``instance``. Raises FingerprintError."""
    cache = cache or HashCache(None)
    paths, notes = files(instance, exclude, configs)
    entries = []
    for rel, path in paths:
        try:
            st = os.stat(path)
            entries.append((rel, cache.sha256(path, st)))
        except OSError as exc:
            raise FingerprintError(f"can't hash {path}: {exc}") from exc
    try:
        cache.save(instance)
    except OSError as exc:
        notes.append(f"hash cache not saved: {exc}")
    return Fingerprint.of(tuple(entries), notes)


def compute_play(cfg: config.Config, use_cache: bool = True) -> Fingerprint:
    """Play's fingerprint as gos.toml defines it (before any sync; plan 5.2 step 4)."""
    cache = HashCache(cfg.gos_home / CACHE if use_cache else None)
    return compute(
        cfg.instance("play"),
        cfg.sync_exclude.get("export", ()),
        cfg.fingerprint_configs,
        cache,
    )


# --- state ----------------------------------------------------------------------------------


def atomic_write(path: Path, data: bytes) -> None:
    """Write a new temp file beside ``path`` (O_EXCL, random name), then rename it onto it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(home: Path) -> dict[str, Any]:
    """``state.json``, or an empty state when it is missing or damaged."""
    try:
        data = json.loads(read_bytes(home / STATE)[:MAX_STATE])
    except (OSError, ValueError):
        return {"format": FORMAT, "exports": []}
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return {"format": FORMAT, "exports": []}
    exports = data.get("exports")
    data["exports"] = [
        e
        for e in (exports if isinstance(exports, list) else [])
        if isinstance(e, dict)
        and isinstance(e.get("run_id"), str)
        and RUN_ID.fullmatch(e["run_id"])
        and isinstance(e.get("fingerprint"), str)
    ]
    return data


def _save_state(home: Path, state: dict[str, Any]) -> None:
    atomic_write(home / STATE, json.dumps(state, indent=1).encode("utf-8"))


def last_export(home: Path) -> dict[str, Any] | None:
    exports = load_state(home)["exports"]
    return exports[-1] if exports else None


def run_entries(home: Path, run_id: str) -> Entries | None:
    """Play's listing recorded for export ``run_id``, if GOS still has it."""
    if not RUN_ID.fullmatch(run_id):
        return None
    try:
        return parse_listing(read_bytes(home / RUNS / f"{run_id}.tsv"))
    except (OSError, ValueError):
        return None


@dataclass
class Status:
    """Play against the last export: the stale flag (plan 5.2 step 4)."""

    stale: bool
    play: Fingerprint
    export_run: str | None = None
    export_fingerprint: str | None = None
    changes: Changes = field(default_factory=Changes)

    def line(self) -> str:
        fp = self.play.digest[:12]
        if self.export_run is None:
            return (
                "recipe data is stale: no export is recorded yet "
                f"(Play's fingerprint {fp})"
            )
        if self.stale:
            what = self.changes.summary() if self.changes else "its listing isn't kept"
            old = (self.export_fingerprint or "?")[:12]
            return (
                f"recipe data is stale: Play changed since export {self.export_run} "
                f"({what}; fingerprint {old} -> {fp})"
            )
        return f"recipe data matches Play (export {self.export_run}, fingerprint {fp})"


def status(home: Path, play: Fingerprint) -> Status:
    """Compare ``play`` with the last recorded export. Reads only."""
    last = last_export(home)
    if last is None:
        return Status(stale=True, play=play)
    run_id, digest = last["run_id"], last["fingerprint"]
    old = run_entries(home, run_id)
    return Status(
        stale=digest != play.digest,
        play=play,
        export_run=run_id,
        export_fingerprint=digest,
        changes=changes(old, play.entries) if old is not None else Changes(),
    )


def check(home: Path, play: Fingerprint, by: str) -> Status:
    """:func:`status`, then record it: ``play.tsv`` and the stale flag in ``state.json``."""
    result = status(home, play)
    atomic_write(home / PLAY_LISTING, listing(play.entries))
    state = load_state(home)
    state["play"] = {"fingerprint": play.digest, "checked_utc": utc_now(), "by": by}
    state["stale"] = result.stale
    _save_state(home, state)
    return result


def record_export(
    home: Path, run_id: str, exported: Fingerprint, play_now: Fingerprint
) -> Status:
    """Record that export ``run_id`` ran on Play as ``exported`` (computed before sync), then
    check ``play_now`` against it."""
    if not RUN_ID.fullmatch(run_id):
        raise ValueError(f"not a run id: {run_id!r}")
    atomic_write(home / RUNS / f"{run_id}.tsv", listing(exported.entries))
    state = load_state(home)
    exports = [e for e in state["exports"] if e["run_id"] != run_id]
    exports.append(
        {"run_id": run_id, "fingerprint": exported.digest, "recorded_utc": utc_now()}
    )
    state["exports"] = exports[-KEEP_RUNS:]
    _save_state(home, state)
    _prune_runs(home, {e["run_id"] for e in state["exports"]})
    return check(home, play_now, "export")


def _prune_runs(home: Path, keep: set[str]) -> None:
    folder = home / RUNS
    for path in folder.glob("*.tsv"):
        if path.stem not in keep:
            path.unlink(missing_ok=True)
