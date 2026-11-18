"""Backup manifests, the vault snapshot, and the restore that checks them (WO-11.2; plan 4.3,
6.6, 10 "Ops"; D139, D142 (g), D332).

The daily ``backup`` job (:func:`gregtec.vault.curator.backup`) writes into
``GOS_HOME/data/backups/``:

- ``gos-YYYYMMDD.sqlite``: ``VACUUM INTO`` of the core DB (WO-2.5), and beside it
  ``gos-YYYYMMDD.sqlite.manifest.json``: its size, SHA-256 and every table's row count;
- ``vault-YYYYMMDD.zip``: every file of the vault, ``.git`` included (so history comes back),
  except :func:`gregtec.vault.io.atomic_write`'s temporaries and git's ``*.lock`` files (a
  restored lock would be a stuck lock); and ``vault-YYYYMMDD.zip.manifest.json``: the zip's
  size and SHA-256 and each file's size and SHA-256.

**Order, so a power loss never leaves a backup that looks whole:** the data file is written to
``<name>.tmp`` and fsynced, its manifest is written (temporary file, fsync, replace), and only
then is the ``.tmp`` renamed to its name. A backup under its own name therefore always has its
manifest; a manifest whose file never arrived is an orphan the next run removes, and a ``.tmp``
is removed by the next run too. A day's DB backup made before this WO (no manifest) gets one the
next time the job runs that day.

:func:`restore` restores the newest DB backup and the newest vault backup (or a given day's)
into a folder that is not GOS_HOME's own data or vault and checks them: the files' hashes against
their manifests, ``PRAGMA integrity_check``, every table's row count, every vault file's hash, and
no file missing or extra. A check that fails says what failed; ``gos backup restore`` exits 1.
It never writes into the live data or vault: putting a restored copy in place is Clayton's
manual step with GOS stopped (docs/runbooks/ops.md).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT = 1
MANIFEST = ".manifest.json"
DB_BACKUP = re.compile(r"gos-(\d{8})\.sqlite")
VAULT_BACKUP = re.compile(r"vault-(\d{8})\.zip")
VAULT_TMP = re.compile(r"vault-(\d{8})\.zip\.tmp")
_CHUNK = 1 << 20


class BackupError(RuntimeError):
    """A backup that can't be made or read; the message says why."""


class CutShort(BackupError):
    """The deadline passed while the vault was being zipped (nothing was left behind)."""


# --- hashing and manifests --------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def manifest_path(data_file: Path) -> Path:
    """The manifest beside a backup (``gos-20261106.sqlite`` -> ``...sqlite.manifest.json``);
    for a ``.tmp`` file, the manifest of the name it will get."""
    name = data_file.name.removesuffix(".tmp")
    return data_file.with_name(name + MANIFEST)


def _fsync_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_manifest(data_file: Path, manifest: dict[str, Any]) -> Path:
    target = manifest_path(data_file)
    _fsync_write(
        target, (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")
    )
    return target


def read_manifest(data_file: Path) -> dict[str, Any]:
    path = manifest_path(data_file)
    if not path.is_file():
        raise BackupError(f"{data_file.name} has no manifest ({path.name})")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupError(f"{path.name} can't be read: {exc}") from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise BackupError(f"{path.name} is not a format-{FORMAT} manifest")
    return doc


def _now_utc() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_counts(db: Path) -> dict[str, int]:
    """Every table's row count (virtual tables and their shadow tables included)."""
    conn = sqlite3.connect(db)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            n: int(conn.execute(f"SELECT COUNT(*) FROM {_quote(n)}").fetchone()[0])
            for n in names
        }
    finally:
        conn.close()


def _sidecars_gone(db: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        db.with_name(db.name + suffix).unlink(missing_ok=True)


def db_manifest(db: Path) -> dict[str, Any]:
    """The manifest of a DB backup file: counted first, hashed after (nothing reopens it)."""
    tables = table_counts(db)
    _sidecars_gone(db)
    return {
        "format": FORMAT,
        "kind": "db",
        "file": db.name.removesuffix(".tmp"),
        "bytes": db.stat().st_size,
        "sha256": sha256_file(db),
        "tables": tables,
        "made_utc": _now_utc(),
    }


# --- the vault snapshot -----------------------------------------------------------------------


def _skipped(rel: PurePosixPath) -> bool:
    """io.atomic_write's temporaries and git's lock files never go into a snapshot."""
    if rel.name.endswith(".gos-tmp") or rel.name.endswith(".tmp"):
        return True
    return bool(rel.parts) and rel.parts[0] == ".git" and rel.name.endswith(".lock")


def vault_files(vault: Path) -> Iterator[tuple[PurePosixPath, Path]]:
    """The vault's regular files in a stable order, links never followed."""
    for root, dirs, files in os.walk(vault):
        here = Path(root)
        dirs[:] = sorted(d for d in dirs if not (here / d).is_symlink())
        for name in sorted(files):
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = PurePosixPath(path.relative_to(vault).as_posix())
            if not _skipped(rel):
                yield rel, path


def snapshot_vault(
    vault: Path, folder: Path, today: dt.date, deadline: float | None = None
) -> str:
    """Write ``vault-YYYYMMDD.zip`` and its manifest (see the module docstring). Returns what it
    did. :class:`CutShort` past ``deadline`` (a ``time.monotonic()`` value), with the ``.tmp``
    removed."""
    folder.mkdir(parents=True, exist_ok=True)
    for stale in [p for p in folder.iterdir() if VAULT_TMP.fullmatch(p.name)]:
        stale.unlink(missing_ok=True)
    target = folder / f"vault-{today:%Y%m%d}.zip"
    if target.exists():
        if not manifest_path(target).is_file():
            write_manifest(target, vault_manifest(target))
        return f"{target.name} already exists"
    if not vault.is_dir():
        raise BackupError(f"no vault at {vault}")
    tmp = target.with_name(target.name + ".tmp")
    files: dict[str, dict[str, Any]] = {}
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel, path in vault_files(vault):
                if deadline is not None and time.monotonic() > deadline:
                    raise CutShort("out of time")
                try:
                    data = path.read_bytes()
                except FileNotFoundError:  # replaced or removed while we walked
                    continue
                zf.writestr(str(rel), data)
                files[str(rel)] = {
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        manifest = {
            "format": FORMAT,
            "kind": "vault",
            "file": target.name,
            "bytes": tmp.stat().st_size,
            "sha256": sha256_file(tmp),
            "files": files,
            "made_utc": _now_utc(),
        }
        write_manifest(target, manifest)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return f"wrote {target.name} ({len(files)} files)"


def vault_manifest(zip_path: Path) -> dict[str, Any]:
    """The manifest of an existing vault zip (one made before its manifest could be)."""
    files: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            data = zf.read(info)
            files[info.filename] = {
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
    return {
        "format": FORMAT,
        "kind": "vault",
        "file": zip_path.name,
        "bytes": zip_path.stat().st_size,
        "sha256": sha256_file(zip_path),
        "files": files,
        "made_utc": _now_utc(),
    }


def prune(folder: Path, keep: int) -> int:
    """Keep the newest ``keep`` DB and vault backups (with their manifests); remove orphan
    manifests. Returns how many backups went."""
    if not folder.is_dir():
        return 0
    gone = 0
    for pattern in (DB_BACKUP, VAULT_BACKUP):
        kept = sorted(
            (p for p in folder.iterdir() if pattern.fullmatch(p.name)), reverse=True
        )
        for old in kept[keep:]:
            old.unlink()
            manifest_path(old).unlink(missing_ok=True)
            gone += 1
    for m in folder.iterdir():
        if m.name.endswith(MANIFEST):
            data = m.with_name(m.name.removesuffix(MANIFEST))
            if not data.exists() and not data.with_name(data.name + ".tmp").exists():
                m.unlink(missing_ok=True)
    return gone


# --- listing and restoring --------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    what: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Listed:
    kind: str  # db | vault
    day: str  # YYYYMMDD
    path: Path
    has_manifest: bool


def listing(folder: Path) -> list[Listed]:
    """Every backup in ``folder``, newest first within each kind."""
    out: list[Listed] = []
    if not folder.is_dir():
        return out
    for kind, pattern in (("db", DB_BACKUP), ("vault", VAULT_BACKUP)):
        found = sorted(
            (p for p in folder.iterdir() if pattern.fullmatch(p.name)), reverse=True
        )
        for p in found:
            m = pattern.fullmatch(p.name)
            assert m is not None
            out.append(Listed(kind, m[1], p, manifest_path(p).is_file()))
    return out


def pick(folder: Path, kind: str, day: str | None = None) -> Path | None:
    for item in listing(folder):
        if item.kind == kind and (day is None or item.day == day):
            return item.path
    return None


def _safe_member(name: str) -> PurePosixPath | None:
    rel = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(part in ("", ".", "..") for part in rel.parts)
    ):
        return None
    return rel


def restore_db(backup: Path, dest: Path) -> list[Check]:
    """Copy ``backup`` to ``dest/data/gos.sqlite`` and check it against its manifest."""
    checks: list[Check] = []
    try:
        manifest = read_manifest(backup)
    except BackupError as exc:
        return [Check("db manifest", False, str(exc))]
    checks.append(Check("db manifest", True, manifest_path(backup).name))
    target = dest / "data" / "gos.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(backup, target)
    digest = sha256_file(target)
    checks.append(
        Check(
            "db file hash",
            digest == manifest.get("sha256"),
            "matches the manifest"
            if digest == manifest.get("sha256")
            else f"SHA-256 {digest[:12]}... but the manifest says "
            f"{str(manifest.get('sha256'))[:12]}...: the file changed after the backup",
        )
    )
    try:
        conn = sqlite3.connect(target)
        try:
            rows = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        rows = [f"{type(exc).__name__}: {exc}"]
    ok = rows == ["ok"]
    checks.append(
        Check("db integrity_check", ok, "ok" if ok else "; ".join(rows[:5])[:400])
    )
    if not ok:
        return checks
    try:
        counts = table_counts(target)
    except sqlite3.DatabaseError as exc:
        return [*checks, Check("db row counts", False, f"{exc}")]
    want = manifest.get("tables") or {}
    wrong = [
        f"{t}: {counts.get(t, 'missing')} rows, manifest {n}"
        for t, n in sorted(want.items())
        if counts.get(t) != n
    ]
    extra = sorted(set(counts) - set(want))
    if extra:
        wrong.append(f"tables not in the manifest: {', '.join(extra[:5])}")
    checks.append(
        Check(
            "db row counts",
            not wrong,
            f"{len(want)} tables, {sum(counts.values())} rows match"
            if not wrong
            else "; ".join(wrong[:5])[:400],
        )
    )
    return checks


def restore_vault(backup: Path, dest: Path) -> list[Check]:
    """Extract ``backup`` into ``dest/vault`` and check every file against its manifest."""
    try:
        manifest = read_manifest(backup)
    except BackupError as exc:
        return [Check("vault manifest", False, str(exc))]
    checks = [Check("vault manifest", True, manifest_path(backup).name)]
    digest = sha256_file(backup)
    if digest != manifest.get("sha256"):
        return [
            *checks,
            Check(
                "vault zip hash",
                False,
                f"SHA-256 {digest[:12]}... but the manifest says "
                f"{str(manifest.get('sha256'))[:12]}...: the zip changed after the backup",
            ),
        ]
    checks.append(Check("vault zip hash", True, "matches the manifest"))
    root = dest / "vault"
    root.mkdir(parents=True, exist_ok=True)
    want: dict[str, Any] = manifest.get("files") or {}
    got: dict[str, tuple[int, str]] = {}
    bad: list[str] = []
    try:
        with zipfile.ZipFile(backup) as zf:
            for info in zf.infolist():
                rel = _safe_member(info.filename)
                if rel is None or info.is_dir():
                    bad.append(f"unsafe or odd entry {info.filename!r}")
                    continue
                data = zf.read(info)  # zipfile checks each entry's CRC here
                out = root.joinpath(*rel.parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                got[str(rel)] = (len(data), hashlib.sha256(data).hexdigest())
    except (zipfile.BadZipFile, zlib.error, OSError, EOFError) as exc:
        return [*checks, Check("vault zip", False, f"{type(exc).__name__}: {exc}")]
    checks.append(
        Check("vault zip", not bad, "every entry read" if not bad else bad[0])
    )
    wrong = []
    for name, entry in sorted(want.items()):
        have = got.get(name)
        if have is None:
            wrong.append(f"{name} missing")
        elif have != (entry.get("bytes"), entry.get("sha256")):
            wrong.append(f"{name} differs")
    wrong += [f"{name} not in the manifest" for name in sorted(set(got) - set(want))]
    checks.append(
        Check(
            "vault file hashes",
            not wrong,
            f"{len(want)} files match"
            if not wrong
            else f"{len(wrong)} wrong: " + "; ".join(wrong[:5]),
        )
    )
    return checks


def refuse_dest(dest: Path, live: tuple[Path, ...]) -> str | None:
    """Why ``dest`` can't take a restore: not empty, or in or around the live data or vault."""
    resolved = dest.resolve()
    for place in live:
        p = place.resolve()
        if resolved == p or p in resolved.parents or resolved in p.parents:
            return (
                f"{dest} is in or around {place}: a restore never goes over live data"
            )
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        return f"{dest} is not an empty folder"
    return None


def restore(folder: Path, dest: Path, day: str | None = None) -> list[Check]:
    """Restore the newest (or ``day``'s) DB and vault backups into ``dest`` and check them."""
    checks: list[Check] = []
    db = pick(folder, "db", day)
    when = f" for {day}" if day else ""
    if db is None:
        checks.append(Check("db backup", False, f"no DB backup{when} in {folder}"))
    else:
        checks.append(Check("db backup", True, db.name))
        checks += restore_db(db, dest)
    vault = pick(folder, "vault", day)
    if vault is None:
        checks.append(
            Check(
                "vault backup",
                False,
                f"no vault backup{when} in {folder}: the daily backup job makes one "
                "(gos vault maintain --job backup)",
            )
        )
    else:
        checks.append(Check("vault backup", True, vault.name))
        checks += restore_vault(vault, dest)
    return checks
