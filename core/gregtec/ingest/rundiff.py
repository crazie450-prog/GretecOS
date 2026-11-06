"""Diff two GregtecLink export runs at manifest and file-hash level (WO-1.4, D122).

``gos export diff [OLD NEW]`` and the export pipeline write it. It compares what each run's
``manifest.json`` says (``docs/contracts/gregteclink-export-v1.md``): versions, Export's mods,
counts, part status and every listed file's ``sha256``, ``bytes`` and ``lines``, plus what changed
in **Play** between the two exports, from the fingerprint listings GOS recorded for them (D120).
Only manifests are read, through ``safe_open``; the files themselves are not opened.

File hashes can differ for an unchanged pack: GT lists some recipes' outputs in a different order
on each run (the contract's "slot order" note), so ``gt_recipes.jsonl`` alone changing with equal
counts is usually nothing. The recipe-level diff (added, removed and changed recipes per mod, by
identity and content hash) needs the DB (WO-1.3) and is not here.

A run is named by a folder path, or by a run id looked up in ``GOS_HOME/data/exports/`` (runs
collected by WO-1.2), then in Export's ``gregteclink/exports/``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec import config
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import fingerprint

FORMAT = "gregteclink-export/1"
MANIFEST = "manifest.json"
MANIFEST_LIMIT = 16 << 20
EXPORTS = Path("gregteclink", "exports")  # in Export's game folder
DATA_EXPORTS = Path("data", "exports")  # in GOS_HOME (plan 4.3, WO-1.2)
VERSION_KEYS = ("pack_version", "gt_version", "nei_version", "minecraft", "forge")
FILES_LISTED = 40  # per kind of change; NEI alone writes about 180 files


class RunError(Exception):
    """A run that can't be read or isn't a GregtecLink export run."""


@dataclass(frozen=True)
class Run:
    path: Path
    manifest: Mapping[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id"))

    def label(self) -> str:
        m = self.manifest
        extra = "" if m.get("complete") is True else ", INCOMPLETE"
        return f"{self.run_id} ({m.get('created_utc')}, {m.get('mode')}, {m.get('side')}{extra})"


def load_run(folder: Path) -> Run:
    try:
        data = read_bytes(folder / MANIFEST)
    except OSError as exc:
        raise RunError(f"can't read {folder / MANIFEST}: {exc}") from exc
    if len(data) > MANIFEST_LIMIT:
        raise RunError(f"{folder / MANIFEST} is over {MANIFEST_LIMIT >> 20} MiB")
    try:
        manifest = json.loads(data)
    except ValueError as exc:
        raise RunError(f"{folder / MANIFEST} isn't JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise RunError(f"{folder / MANIFEST} isn't a {FORMAT} manifest")
    return Run(folder, manifest)


def export_runs_folder(cfg: config.Config) -> Path:
    return fingerprint.game_folder(cfg.instance("export")) / EXPORTS


def find_run(cfg: config.Config, ref: str) -> Path:
    """A run folder: ``ref`` itself if it is a folder, else the run id ``ref`` in GOS_HOME's
    collected runs, then in Export's runs."""
    path = Path(ref)
    if (path / MANIFEST).is_file():
        return path
    if not fingerprint.RUN_ID.fullmatch(ref):
        raise RunError(f"{ref} is neither a run folder nor a run id")
    places = [cfg.gos_home / DATA_EXPORTS / ref]
    try:
        places.append(export_runs_folder(cfg) / ref)
    except fingerprint.FingerprintError:
        pass
    for place in places:
        if (place / MANIFEST).is_file():
            return place
    raise RunError(f"no run {ref} in {' or '.join(str(p.parent) for p in places)}")


@dataclass
class Diff:
    old: Run
    new: Run
    versions: list[tuple[str, Any, Any]] = field(default_factory=list)
    mods_added: list[str] = field(default_factory=list)
    mods_removed: list[str] = field(default_factory=list)
    mods_changed: list[str] = field(default_factory=list)
    counts: list[tuple[str, Any, Any]] = field(default_factory=list)
    parts: list[tuple[str, Any, Any]] = field(default_factory=list)
    files_added: list[str] = field(default_factory=list)
    files_removed: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    files_same: int = 0
    play: fingerprint.Changes | None = None  # None: a listing wasn't recorded
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        play = (
            "Play's changes unknown"
            if self.play is None
            else f"Play {self.play.summary()}"
        )
        mods = len(self.mods_added) + len(self.mods_removed) + len(self.mods_changed)
        files = (
            len(self.files_added) + len(self.files_removed) + len(self.files_changed)
        )
        recipes = next((c for c in self.counts if c[0] == "recipes"), None)
        rec = f", recipes {recipes[1]} -> {recipes[2]}" if recipes else ""
        parts = "".join(f"; {k} {_fmt(x)} -> {_fmt(y)}" for k, x, y in self.parts)
        return (
            f"{self.old.run_id} -> {self.new.run_id}: {play}; Export mods {mods} changed; "
            f"files {files} changed{rec}{parts}"
        )


def _mods(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    mods = manifest.get("mods")
    out: dict[str, Mapping[str, Any]] = {}
    for m in mods if isinstance(mods, list) else []:
        if not isinstance(m, dict):
            continue
        key = str(m.get("modid"))
        if key in out:  # two jars with one modid: tell them apart by file
            key = f"{key} ({m.get('file')})"
        out[key] = m
    return out


def _mod(key: str, mod: Mapping[str, Any]) -> str:
    return f"{key} {mod.get('version')} ({mod.get('file')})"


def _files(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    files = manifest.get("files")
    return {
        str(k): v
        for k, v in (files.items() if isinstance(files, dict) else ())
        if isinstance(v, dict)
    }


def _table(manifest: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = manifest.get(key)
    return value if isinstance(value, dict) else {}


def _versions(manifest: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: manifest.get(k) for k in VERSION_KEYS}
    out["gregteclink"] = _table(manifest, "gregteclink").get("version")
    out["java"] = _table(manifest, "java").get("version")
    return out


def _changed(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[tuple[str, Any, Any]]:
    return [(k, a.get(k), b.get(k)) for k in sorted({*a, *b}) if a.get(k) != b.get(k)]


def diff(
    old: Run,
    new: Run,
    play_old: fingerprint.Entries | None = None,
    play_new: fingerprint.Entries | None = None,
) -> Diff:
    a, b = old.manifest, new.manifest
    d = Diff(old, new)
    d.versions = _changed(_versions(a), _versions(b))

    ma, mb = _mods(a), _mods(b)
    d.mods_added = [_mod(k, mb[k]) for k in sorted(mb.keys() - ma.keys())]
    d.mods_removed = [_mod(k, ma[k]) for k in sorted(ma.keys() - mb.keys())]
    for k in sorted(ma.keys() & mb.keys()):
        x, y = ma[k], mb[k]
        if x.get("version") != y.get("version"):
            d.mods_changed.append(f"{k} {x.get('version')} -> {y.get('version')}")
        elif x.get("sha256") != y.get("sha256") or x.get("file") != y.get("file"):
            d.mods_changed.append(
                f"{k} {y.get('version')}: jar changed ({y.get('file')})"
            )

    d.counts = _changed(_table(a, "counts"), _table(b, "counts"))
    d.parts = _changed(_table(a, "parts"), _table(b, "parts"))
    for key in ("part_errors", "part_warnings"):
        for part, why in _table(b, key).items():
            kind = "error" if key == "part_errors" else "warning"
            d.notes.append(f"new run's {kind} for {part}: {why}")

    fa, fb = _files(a), _files(b)
    d.files_added = sorted(fb.keys() - fa.keys())
    d.files_removed = sorted(fa.keys() - fb.keys())
    for name in sorted(fa.keys() & fb.keys()):
        x, y = fa[name], fb[name]
        if x.get("sha256") == y.get("sha256"):
            d.files_same += 1
            continue
        d.files_changed.append(
            f"{name}: lines {x.get('lines')} -> {y.get('lines')}, "
            f"bytes {x.get('bytes')} -> {y.get('bytes')}"
        )

    if play_old is not None and play_new is not None:
        d.play = fingerprint.changes(play_old, play_new)
    counts_same = not d.counts
    if counts_same and d.files_changed and not (d.files_added or d.files_removed):
        d.notes.append(
            "every count is the same: GT lists some outputs in a different order on each run, "
            "so changed file hashes alone are usually not changed recipes (the recipe-level "
            "diff comes with the DB, WO-1.3)"
        )
    if "harness" in (a.get("mode"), b.get("mode")):
        d.notes.append("a harness run (dev runtime): not game data")
    return d


def _fmt(value: object) -> str:
    return "none" if value is None else str(value)


def report(d: Diff) -> str:
    lines = [
        "Export diff (manifest and file-hash level)",
        f"  old: {d.old.label()}",
        f"       {d.old.path}",
        f"  new: {d.new.label()}",
        f"       {d.new.path}",
        "",
    ]
    if d.play is None:
        lines.append("Play: no fingerprint listing recorded for one of the runs")
    elif not d.play:
        lines.append("Play: no change in its fingerprinted files")
    else:
        lines.append(f"Play: {d.play.summary()}")
        lines += [f"  {row}" for row in d.play.lines()]
    lines.append("")
    lines.append("Versions:" if d.versions else "Versions: unchanged")
    lines += [f"  {k}: {_fmt(x)} -> {_fmt(y)}" for k, x, y in d.versions]
    mods = (
        [f"  + {m}" for m in d.mods_added]
        + [f"  - {m}" for m in d.mods_removed]
        + [f"  ~ {m}" for m in d.mods_changed]
    )
    lines.append(
        f"Export's mods: {len(_mods(d.old.manifest))} -> {len(_mods(d.new.manifest))}"
        + ("" if mods else ", unchanged")
    )
    lines += mods
    lines.append("Counts:" if d.counts else "Counts: unchanged")
    for k, x, y in d.counts:
        delta = f" ({y - x:+d})" if isinstance(x, int) and isinstance(y, int) else ""
        lines.append(f"  {k}: {_fmt(x)} -> {_fmt(y)}{delta}")
    lines.append("Parts:" if d.parts else "Parts: unchanged")
    lines += [f"  {k}: {_fmt(x)} -> {_fmt(y)}" for k, x, y in d.parts]
    lines.append(
        f"Files: {d.files_same} unchanged, {len(d.files_changed)} changed, "
        f"{len(d.files_added)} added, {len(d.files_removed)} removed"
    )
    for mark, names in (
        ("~", d.files_changed),
        ("+", d.files_added),
        ("-", d.files_removed),
    ):
        lines += [f"  {mark} {f}" for f in names[:FILES_LISTED]]
        if len(names) > FILES_LISTED:
            lines.append(
                f"  {mark} ... and {len(names) - FILES_LISTED} more (see the manifests)"
            )
    if d.notes:
        lines.append("Notes:")
        lines += [f"  {n}" for n in d.notes]
    return "\n".join(lines) + "\n"


def diff_runs(home: Path, old: Run, new: Run) -> Diff:
    """:func:`diff` with Play's listings as GOS recorded them for the two runs."""
    return diff(
        old,
        new,
        fingerprint.run_entries(home, old.run_id),
        fingerprint.run_entries(home, new.run_id),
    )
