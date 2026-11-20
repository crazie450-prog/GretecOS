"""Shell designs and their versions: revise, undo, compare, and the 3D viewer's data (WO-10.10,
plan 7.10 "Shell designer", 7.14, D314, D316-D318, D367-D370).

A **design** is the chain of shells Clayton iterates on around one set of plans. Each version is a
saved shell build (:mod:`gregtec.build.shell_store`: ``data/build/shells/<sh-id>/``, which keeps
its operations, the request words, theme, layout and parameters it came from, and is never
changed once written); the design (``data/build/designs/<ds-id>.json``) keeps the chain:

- every version's build id, its ``parent`` (the version it was revised from; none for the first),
  ``asked`` (a short note of what was asked, in his words), ``by`` (``procedural``: GOS's own
  generator or reviser, free; ``architect``: the model through ``build_revise``), the time, the
  ``cost_usd`` (0 for GOS's own; none for the architect's, whose cost is its deep-lane task's,
  on the Costs view), its status and block count;
- ``current``: the version the workbench shows, the router revises and ``gos shell export`` is
  pointed at by the runbook; ``history``: every change of ``current`` (a revision, a "make
  current", an undo), so nothing moves silently.

**Nothing is ever deleted** (as themes keep their versions, D314): undo is making an older
version current again, and a later revision starts from whichever version is current, its
parent recorded. A refused revision (the gate, :func:`gregtec.build.shell.design`, or words the
reviser can't act on) writes nothing and leaves the current version current.

**Revise** reads only the saved version's files (its ``design.json`` and ``layout.json``) and
the block catalog for the palette rule: never the plans, the footprints or the ``structure`` rows
(a real dump's rows are large, and the layout is the one the version was designed against; its
export re-checks it as before, D317).

A build made before WO-10.10 belongs to no design file: it is listed as a one-version design
(the same id it will have once written) until it is revised or made current.

Nothing here writes a world or a ``.schematic``: that is still only Clayton's ``gos shell export``
(D318).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from gregtec.build import palette, shell, shell_revise, shell_store
from gregtec.build import themes as theme_lib
from gregtec.ingest.fingerprint import atomic_write
from gregtec.planner import progress
from gregtec.vault.io import VaultIO

SCHEMA = "gos-shell-design"
VERSION = 1
ROOT = Path("data", "build", "designs")
CURRENT = "current.json"
DESIGN_ID = re.compile(r"ds-[0-9a-f]{12}")
BY = ("procedural", "architect")
MAX_VERSIONS = (
    200  # a design this long is started again (nothing is dropped to make room)
)
MAX_ASKED = 200
MAX_HISTORY = 1000
MAX_VIEW_CELLS = (
    60_000  # the 3D viewer's cells (shell.MAX_BLOCKS is far above any shell asked)
)
_LOCK = (
    threading.Lock()
)  # one read-modify-write of a design file at a time in this process


class VersionError(shell.ShellError):
    """A design or version that isn't there, or a request the chain can't take."""


def design_id(first_build: str) -> str:
    """``ds-`` + 12 hex of the design's first build: the same for a build listed before its
    design file exists and after."""
    digest = hashlib.sha256(f"design|{first_build}".encode()).hexdigest()
    return "ds-" + digest[:12]


def revise_task(ds: str, said: str) -> str:
    """The deep lane's task for a revision, as the router hands it (D369): the design named, so
    the architect calls build_revise on it and never starts over. ``said`` is Clayton's words."""
    return (
        f"Revise Clayton's current shell design {ds} with build_revise (design {ds}): change "
        f"only what he asked and keep the rest; never start a new shell. He said: {said}"
    )


def _path(home: Path, ds: str) -> Path:
    if not DESIGN_ID.fullmatch(ds):
        raise VersionError(f"{ds!r} is not a shell design id (ds-<12 hex>)")
    return home / ROOT / f"{ds}.json"


def _json(data: object) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def clip(text: str, limit: int = MAX_ASKED) -> str:
    one = " ".join(str(text).split())
    return one if len(one) <= limit else one[: limit - 1] + "…"


def _version(
    n: int,
    rec: Mapping[str, Any],
    parent: str | None,
    asked: str,
    by: str,
    cost_usd: float | None,
    at: float,
) -> dict[str, Any]:
    if by not in BY:
        raise ValueError(by)
    return {
        "n": n,
        "build": rec["id"],
        "parent": parent,
        "asked": clip(asked) or "(no words)",
        "by": by,
        "at": round(at, 3),
        "cost_usd": cost_usd,
        "status": rec["status"],
        "blocks": rec["blocks"],
        "theme": rec.get("theme"),
        "headroom": rec["headroom"],
        "max_blocks": rec["max_blocks"],
    }


def _virtual(home: Path, build: str) -> dict[str, Any]:
    """A build with no design file, as the one-version design it will be once written."""
    rec = shell_store.load(home, build)
    at = (shell_store.folder(home, build) / "design.json").stat().st_mtime
    v = _version(
        1,
        rec,
        None,
        rec.get("prompt") or "",
        "architect" if rec["source"] == "architect" else "procedural",
        0.0 if rec["source"] == "procedural" else None,
        at,
    )
    return {
        "schema": SCHEMA,
        "v": VERSION,
        "id": design_id(build),
        "plans": list(rec.get("plans") or []),
        "created": v["at"],
        "current": build,
        "versions": [v],
        "history": [{"at": v["at"], "current": build, "why": "designed"}],
        "written": False,
    }


def _read(home: Path, ds: str) -> dict[str, Any] | None:
    path = _path(home, ds)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise VersionError(f"design {ds} can't be read: {exc}") from None
    if not isinstance(d, dict) or d.get("schema") != SCHEMA or d.get("id") != ds:
        raise VersionError(f"{path.name} is not a {SCHEMA} record")
    d["written"] = True
    return d


def _written(home: Path) -> list[dict[str, Any]]:
    root = home / ROOT
    out = []
    if root.is_dir():
        for f in sorted(root.glob("ds-*.json")):
            if not DESIGN_ID.fullmatch(f.stem):
                continue
            try:
                d = _read(home, f.stem)
            except VersionError:
                continue
            if d is not None:
                out.append(d)
    return out


def _write(home: Path, d: Mapping[str, Any]) -> None:
    body = {k: v for k, v in d.items() if k != "written"}
    atomic_write(_path(home, str(d["id"])), _json(body))


def _all(home: Path) -> list[dict[str, Any]]:
    """Every design: the written ones and a one-version design per build in none of them."""
    found = _written(home)
    held = {v["build"] for d in found for v in d["versions"]}
    ids = {d["id"] for d in found}
    for s in shell_store.builds(home):
        if s["id"] not in held and design_id(s["id"]) not in ids:
            try:
                found.append(_virtual(home, s["id"]))
            except (shell.ShellError, OSError):
                continue
    return found


def _touched(d: Mapping[str, Any]) -> float:
    return max(float(h["at"]) for h in d["history"]) if d["history"] else d["created"]


def get(home: Path, ds: str) -> dict[str, Any]:
    d = _read(home, ds)
    if d is not None:
        return d
    for x in _all(home):
        if x["id"] == ds:
            return x
    raise VersionError(f"no shell design {ds}")


def of_build(home: Path, build: str) -> dict[str, Any]:
    """The design a build is a version of (its own one-version design if none)."""
    for d in _written(home):
        if any(v["build"] == build for v in d["versions"]):
            return d
    return _virtual(home, build)


def designs(home: Path) -> list[dict[str, Any]]:
    """Every design's summary, the one touched last first."""
    out = sorted(_all(home), key=lambda d: (-_touched(d), d["id"]))
    return [summary(d) for d in out]


def summary(d: Mapping[str, Any]) -> dict[str, Any]:
    cur = next(v for v in d["versions"] if v["build"] == d["current"])
    return {
        "id": d["id"],
        "plans": d["plans"],
        "current": d["current"],
        "current_n": cur["n"],
        "versions": len(d["versions"]),
        "asked": d["versions"][0]["asked"],
        "theme": cur["theme"],
        "blocks": cur["blocks"],
        "touched": _touched(d),
    }


def current_id(home: Path) -> str | None:
    """The design Clayton is working on: the one made current last (``current.json``), else
    the one touched last; none when there is no shell at all."""
    try:
        got = json.loads((home / ROOT / CURRENT).read_text(encoding="utf-8"))
        ds = got.get("design") if isinstance(got, dict) else None
        if isinstance(ds, str) and DESIGN_ID.fullmatch(ds):
            get(home, ds)
            return ds
    except (OSError, ValueError, shell.ShellError):
        pass
    found = designs(home)
    return found[0]["id"] if found else None


def any_shell(home: Path) -> bool:
    """Whether there is a shell at all: ``current.json`` or a saved build, from the folders'
    names only (no file is read), so the router can ask it on every request."""
    if (home / ROOT / CURRENT).is_file():
        return True
    root = home / shell_store.ROOT
    try:
        return any(
            shell_store.BUILD_ID.fullmatch(d.name) and (d / "design.json").is_file()
            for d in root.iterdir()
        )
    except OSError:  # no shells folder yet
        return False


def _point(home: Path, ds: str) -> None:
    atomic_write(
        home / ROOT / CURRENT, _json({"design": ds, "at": round(time.time(), 3)})
    )


def _history(d: dict[str, Any], build: str, why: str) -> None:
    d["current"] = build
    d["history"].append(
        {"at": round(time.time(), 3), "current": build, "why": clip(why)}
    )
    del d["history"][:-MAX_HISTORY]


def start(
    home: Path,
    build: str,
    *,
    asked: str,
    by: str,
    cost_usd: float | None = None,
) -> dict[str, Any]:
    """A new design whose first version is the saved ``build`` (or, when it is already a
    design's first version, that design with it made current). It becomes the current design."""
    with _LOCK:
        ds = design_id(build)
        d = _read(home, ds)
        if d is None:
            rec = shell_store.load(home, build)
            now = time.time()
            v = _version(
                1, rec, None, asked, by, 0.0 if by == "procedural" else cost_usd, now
            )
            d = {
                "schema": SCHEMA,
                "v": VERSION,
                "id": ds,
                "plans": list(rec.get("plans") or []),
                "created": v["at"],
                "current": build,
                "versions": [v],
                "history": [],
            }
            _history(d, build, "designed")
        elif d["current"] != build:
            _history(d, build, f"designed again: {asked}")
        _write(home, d)
        _point(home, ds)
        d["written"] = True
        return d


def make_current(
    home: Path, ds: str, build: str, why: str = "made current"
) -> dict[str, Any]:
    """Make version ``build`` of design ``ds`` current (undo is this with an older one)."""
    with _LOCK:
        d = get(home, ds)
        if not any(v["build"] == build for v in d["versions"]):
            raise VersionError(f"{build} is not a version of design {ds}")
        if d["current"] != build:
            n = next(v["n"] for v in d["versions"] if v["build"] == build)
            _history(d, build, f"{why} (v{n})")
        _write(home, d)
        _point(home, ds)
        d["written"] = True
        return d


def undo(home: Path, ds: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """The current version's parent made current again: (design, the version now current)."""
    ds = ds or current_id(home)
    if ds is None:
        raise VersionError("there is no shell yet: nothing to undo")
    d = get(home, ds)
    cur = next(v for v in d["versions"] if v["build"] == d["current"])
    if cur["parent"] is None:
        raise VersionError(
            f"v{cur['n']} of design {ds} is its first version: nothing to undo to "
            "(every version is kept; make any one current from the Build view)"
        )
    d = make_current(home, ds, cur["parent"], "undo")
    return d, next(v for v in d["versions"] if v["build"] == d["current"])


@dataclass(frozen=True)
class Revised:
    design: str
    parent: str
    build: str  # the new version's id (or the one it would have had, refused)
    outcome: shell.Outcome
    ops: tuple[dict[str, Any], ...]
    changes: tuple[str, ...]
    reasons: tuple[
        str, ...
    ]  # why refused (the reviser's or the gate's); empty when saved
    saved: bool
    version: dict[str, Any] | None  # the chain's new entry, when saved
    record: (
        dict[str, Any] | None
    )  # the new build's design.json, when it passed the gate

    @property
    def ok(self) -> bool:
        return self.saved


def _layout_of(home: Path, build: str) -> dict[str, Any]:
    try:
        lay: dict[str, Any] = json.loads(
            (shell_store.folder(home, build) / "layout.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError) as exc:
        raise VersionError(
            f"build {build}'s layout.json can't be read: {exc}"
        ) from None
    return lay


def revise(
    conn: sqlite3.Connection,
    home: Path,
    vault: VaultIO | None,
    ds: str | None,
    words: str,
    *,
    ops: Sequence[Mapping[str, Any]] | None = None,
    by: str = "procedural",
    theme_id: str | None = None,
    max_blocks: int | None = None,
    headroom: int | None = None,
    cost_usd: float | None = None,
    asked: str | None = None,
    save_it: bool = True,
) -> Revised:
    """A new version of design ``ds`` (the current design if none) from its current version and
    ``words``: the model's operations (``ops``, ``by`` architect) or GOS's reviser's (``ops``
    none), checked by the same gate as every design. Refused (nothing written, the current
    version stays current) when the reviser can't act on the words, the gate refuses the result
    or it is the version already there. ``max_blocks``, ``headroom`` and ``theme_id`` (the
    workbench's inputs) apply on top of the words. ``asked`` is the chain's note (default the
    words). Reads only the current version's files and the block catalog."""
    if by not in BY:
        raise ValueError(by)
    ds = ds or current_id(home)
    if ds is None:
        raise VersionError("there is no shell yet: design one first")
    d = get(home, ds)
    if len(d["versions"]) >= MAX_VERSIONS:
        raise VersionError(
            f"design {ds} has {MAX_VERSIONS} versions, the most one design keeps: start a new "
            "design (every version stays saved)"
        )
    parent = d["current"]
    rec = shell_store.load(home, parent)
    lay = _layout_of(home, parent)
    words = " ".join(words.split())
    if len(words) > shell_revise.MAX_WORDS:
        raise VersionError(f"say it in at most {shell_revise.MAX_WORDS} characters")
    progress.stage("palette")
    theme = None
    if (
        theme_id is not None
        and theme_id != str(rec.get("theme") or "").partition("@")[0]
    ):
        found = theme_lib.Themes(vault).load()[0] if vault is not None else []
        theme = next((t for t in found if t.id == theme_id), None)
        if theme is None:
            raise VersionError(f"no theme {theme_id!r} (theme_list)")
        swatches = shell.swatches_of(theme)
    else:
        swatches = [
            shell.Swatch(r["role"], palette.parse_entry(r["block"]), r.get("note", ""))
            for r in rec["palette"]
        ]
    try:
        shell.check_palette(conn, swatches)
    except palette.CatalogError as exc:
        raise VersionError(str(exc)) from None
    progress.stage("compose")
    changes: list[str] = []
    hr = rec["headroom"]
    if ops is None:
        r = shell_revise.revise(rec, words) if words else None
        if r is not None and not r.ok:
            why = [f"GOS's reviser can't do {u!r}" for u in r.unknown] + list(
                r.impossible
            )
            if not r.unknown and not r.impossible:
                why = [f"nothing in {words!r} changes the shell"]
            return _refused(ds, parent, rec, why, r.changes)
        new_ops = tuple(dict(o) for o in (r.ops if r is not None else rec["ops"]))
        changes = list(r.changes) if r is not None else []
        hr = r.headroom if r is not None else hr
        source = rec["source"] if r is None else "procedural"
    else:
        new_ops = tuple(dict(o) for o in ops if isinstance(o, Mapping))
        if len(new_ops) != len(ops):
            return _refused(ds, parent, rec, ["every operation is an object"], ())
        source = "architect"
        changes = shell_revise.describe(rec["ops"], new_ops)
    if headroom is not None:
        hr = headroom
        changes.append(f"headroom {headroom}")
    mb = rec["max_blocks"] if max_blocks is None else max_blocks
    if max_blocks is not None:
        changes.append(f"max_blocks {max_blocks}")
    if theme is not None:
        changes.append(f"theme {theme.name} ({theme.id}@v{theme.v})")
    line_type = rec.get("line_type", theme_lib.GENERAL)
    try:
        spec = shell.spec_from_layout(
            lay,
            swatches,
            theme=theme,
            prompt=rec.get("prompt", ""),
            line_type=line_type,
            max_blocks=mb,
            headroom=hr,
        )
    except shell.ShellError as exc:
        return _refused(ds, parent, rec, [str(exc)], tuple(changes))
    if theme is None:
        spec = replace(spec, theme=rec.get("theme"), theme_name=rec.get("theme_name"))
    progress.stage("gate")
    outcome = shell.design(conn, spec, new_ops, source)
    build = shell.build_id(spec, new_ops)
    if not outcome.ok:
        return Revised(
            ds,
            parent,
            build,
            outcome,
            new_ops,
            tuple(changes),
            outcome.reasons,
            False,
            None,
            None,
        )
    brief = theme_lib.Brief(
        theme_lib.THEME_MODE
        if theme is not None
        else str(rec.get("mode") or theme_lib.THEME_MODE),
        line_type,
        theme_lib.LINE_TYPES[line_type],
        theme,
        spec.prompt,
        theme.adaptation(line_type) if theme is not None else None,
        f"the version revised ({parent})"
        if theme is None
        else "chosen in the revision",
    )
    made = shell_store.Made(spec, new_ops, outcome, brief, build, None)
    record = made.record
    if build == parent or any(v["build"] == build for v in d["versions"]):
        same = next(v for v in d["versions"] if v["build"] == build)
        again = (
            f"the revision gives the current v{same['n']} ({build}) again: nothing "
            "changes (nothing was written)"
            if build == parent
            else f"the revision gives v{same['n']} ({build}) again: make it current "
            "instead (nothing was written)"
        )
        return Revised(
            ds,
            parent,
            build,
            outcome,
            new_ops,
            tuple(changes),
            (again,),
            False,
            None,
            record,
        )
    if not save_it:
        return Revised(
            ds, parent, build, outcome, new_ops, tuple(changes), (), False, None, record
        )
    progress.stage("saving")
    if not (shell_store.folder(home, build) / "design.json").is_file():
        shell_store.save(home, made, lay)
    with _LOCK:
        d = get(home, ds)  # again, under the lock: another revision may have landed
        if d["current"] != parent:
            raise VersionError(
                f"design {ds} moved on while this ran (now {d['current']}): revise it again"
            )
        v = _version(
            len(d["versions"]) + 1,
            record,
            parent,
            asked if asked is not None else words or "; ".join(changes),
            by,
            0.0 if by == "procedural" else cost_usd,
            time.time(),
        )
        d["versions"].append(v)
        _history(d, build, f"revised: {v['asked']}")
        _write(home, d)
        _point(home, ds)
    return Revised(
        ds, parent, build, outcome, new_ops, tuple(changes), (), True, v, record
    )


def _refused(
    ds: str,
    parent: str,
    rec: Mapping[str, Any],
    why: Sequence[str],
    changes: Sequence[str],
) -> Revised:
    out = shell.Outcome("refused", rec["source"], {}, {}, tuple(why))
    return Revised(
        ds, parent, parent, out, (), tuple(changes), tuple(why), False, None, None
    )


# --- compare ---------------------------------------------------------------------------------


def compare(home: Path, a: str, b: str) -> dict[str, Any]:
    """Two versions side by side: each one's summary and the differences in materials (per
    ``registry@meta``: the count in each and the change), block counts and operations."""
    ra, rb = shell_store.load(home, a), shell_store.load(home, b)

    def side(rec: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "build": rec["id"],
            "status": rec["status"],
            "source": rec["source"],
            "theme": rec.get("theme"),
            "size": rec["bounds"]["size"],
            "blocks": rec["blocks"],
            "max_blocks": rec["max_blocks"],
            "headroom": rec["headroom"],
            "types": rec["types"],
        }

    ma = {m["block"]: m for m in ra["materials"]}
    mb = {m["block"]: m for m in rb["materials"]}
    rows = []
    for key in sorted(set(ma) | set(mb)):
        x, y = ma.get(key), mb.get(key)
        ca, cb = (x or {}).get("count", 0), (y or {}).get("count", 0)
        rows.append(
            {
                "block": key,
                "name": (x or y or {}).get("name"),
                "role": (x or y or {}).get("role"),
                "a": ca,
                "b": cb,
                "delta": cb - ca,
            }
        )
    rows.sort(key=lambda r: (-abs(r["delta"]), r["block"]))
    return {
        "a": side(ra),
        "b": side(rb),
        "blocks_delta": rb["blocks"] - ra["blocks"],
        "materials": rows,
        "ops": shell_revise.describe(ra["ops"], rb["ops"]),
    }


# --- the 3D viewer's data -------------------------------------------------------------------


def viewer(conn: sqlite3.Connection | None, home: Path, build: str) -> dict[str, Any]:
    """One version for the build guide's 3D viewer (D319's drawing): its cells as ``[x, y, z,
    palette index, -1]`` from the shell's min corner, the palette (``registry@meta``, the
    catalog's name and face colours, role, count), per-layer materials, and the floor plan's
    machines and keep-outs as ghosts in the same frame. The cells come from the build's own
    files (:func:`gregtec.build.shell_store.cells_of`); the catalog (``conn``, none: no DB yet)
    gives only the names and colours."""
    from gregtec.build import guide_data

    rec = shell_store.load(home, build)
    spec, placed = shell_store.cells_of(home, build)
    lay = _layout_of(home, build)
    order = [m["block"] for m in rec["materials"]]
    index = {k: i for i, k in enumerate(order)}
    lo = spec.bounds.lo
    cells = sorted(
        (
            [c[0] - lo[0], c[1] - lo[1], c[2] - lo[2], index[e.key], -1]
            for c, e in placed.items()
        ),
        key=lambda c: (c[1], c[2], c[0]),
    )
    caveats = []
    if len(cells) > MAX_VIEW_CELLS:
        caveats.append(
            f"the shell has {len(cells)} blocks, more than the viewer draws ({MAX_VIEW_CELLS}): "
            "the lowest layers are shown"
        )
        cells = cells[:MAX_VIEW_CELLS]
    layers: dict[int, dict[str, int]] = {}
    for x in cells:
        mats = layers.setdefault(x[1], {})
        mats[order[x[3]]] = mats.get(order[x[3]], 0) + 1
    look = guide_data.looks(conn, order) if conn is not None else {}
    pal = []
    for m in rec["materials"]:
        lk = look.get(m["block"])
        pal.append(
            {
                "block": m["block"],
                "name": m["name"] if m["name"] else (lk.name if lk else None),
                "role": m["role"],
                "count": m["count"],
                "top": lk.top if lk else None,
                "side": lk.side if lk else None,
            }
        )

    def rel(box: Mapping[str, Any]) -> dict[str, list[int]]:
        b = shell_store.layout_lib.box_of(box)
        return {
            "lo": [b.lo[i] - lo[i] for i in range(3)],
            "hi": [b.hi[i] - lo[i] for i in range(3)],
        }

    machines = [
        {
            "id": p["id"],
            "name": p.get("name"),
            "ref": f"mte:{p['mte']}" if p.get("mte") is not None else None,
            **rel(p),
        }
        for p in lay.get("placements", [])
    ]
    keepouts = [{"id": k["id"], **rel(k)} for k in lay.get("keepouts", [])]
    size = list(spec.bounds.size)
    return {
        "build": build,
        "size": size,
        "palette": pal,
        "cells": cells,
        "layers": [
            {
                "y": y,
                "materials": dict(sorted(m.items(), key=lambda kv: (-kv[1], kv[0]))),
                "blocks": sum(m.values()),
            }
            for y, m in sorted(layers.items())
        ],
        "ghosts": {"machines": machines, "keepouts": keepouts},
        "caveats": caveats,
    }
