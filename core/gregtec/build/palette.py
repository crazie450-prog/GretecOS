"""Shell palettes and the palette rule (WO-10.3, plan 7.10, D307, D308).

A palette is a list of block states, each written ``<registry name>@<meta>``
(``gregtech:gt.blockcasings@2``): the key the schematic writer (WO-10.4) maps to
``BlockMapping``/``SchematicaMapping``, never a numeric id. :func:`check` applies plan 7.10's
palette rule to every entry and refuses it when:

- it is ``minecraft:air`` (the schematic's implicit 0) or GT's machine block
  ``gregtech:gt.blockmachines`` (GOS never pastes machines);
- its block is not in the target world's registry (``level.dat`` ``FML.ItemData``,
  :mod:`gregtec.build.worldreg`): a name the world doesn't know can't be pasted there;
- the state is not in the block catalog (the newest export run's ``block_state``): an unknown
  meta can corrupt a chunk when pasted (worldedit-gtnh #28);
- the state has a tile entity (Forge's ``hasTileEntity(meta)``): a pasted machine arrives free
  and working, and its covers break (plan 7.10);
- its meta is above 15 (worldedit-gtnh #30 on 2.9, V16; until the Lab shows otherwise).

Accepted entries can still carry warnings (falls, liquid, out of every creative tab, listed
twice). A palette with more than :data:`MAX_TYPES` entries is refused as a whole (the schematic
numbers its palette 1..255, plan 7.10). Everything here only reads: the catalog through a
read-only connection and the world's ``level.dat`` through ``safe_open``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from gregtec.build.worldreg import WorldRegistry
from gregtec.db import blocks as block_db

AIR = "minecraft:air"
GT_MACHINES = "gregtech:gt.blockmachines"
MAX_META = 15  # worldedit-gtnh #30 (V16): meta > 15 fails to set on 2.9
MAX_TYPES = 255  # the schematic's palette numbers 1..255 (0 is air), plan 7.10
_ENTRY = re.compile(r"(?P<block>[^\s:@#]+:[^\s@#]+)@(?P<meta>[0-9]{1,5})")


class PaletteError(ValueError):
    """A palette that can't be read (a line that isn't ``registry@meta``)."""


@dataclass(frozen=True)
class Entry:
    block: str
    meta: int

    @property
    def key(self) -> str:
        return f"{self.block}@{self.meta}"


def parse_entry(text: str) -> Entry:
    """``registry:name@meta`` to an :class:`Entry`; ``PaletteError`` for anything else."""
    m = _ENTRY.fullmatch(text.strip())
    if m is None or int(m["meta"]) > 32767:
        raise PaletteError(
            f"{text.strip()[:120]!r} is not <registry name>@<meta> "
            "(e.g. gregtech:gt.blockcasings@2; never a numeric id)"
        )
    return Entry(m["block"], int(m["meta"]))


def parse(text: str) -> list[Entry]:
    """A palette file: one entry per line, its first field; blank lines and ``#`` comments
    are skipped, and anything after the first tab or space is a note."""
    out: list[Entry] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        first = line.split(None, 1)[0]
        try:
            out.append(parse_entry(first))
        except PaletteError as exc:
            raise PaletteError(f"line {n}: {exc}") from None
    return out


@dataclass(frozen=True)
class Verdict:
    entry: Entry
    name: str | None  # the catalog's display name
    refused: tuple[str, ...]  # why it can't be in a shell (empty: accepted)
    warnings: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.refused


@dataclass(frozen=True)
class Report:
    run_key: str  # the export run whose catalog was used
    world: str  # the world whose registry was used
    verdicts: tuple[Verdict, ...]
    problems: tuple[str, ...] = field(default=())  # about the palette as a whole

    @property
    def ok(self) -> bool:
        return not self.problems and all(v.accepted for v in self.verdicts)

    @property
    def accepted(self) -> list[Entry]:
        return [v.entry for v in self.verdicts if v.accepted]


class CatalogError(RuntimeError):
    """No block catalog in the DB yet."""


def check(
    conn: sqlite3.Connection, entries: Iterable[Entry], world: WorldRegistry
) -> Report:
    """Apply the palette rule to ``entries`` with the newest block catalog in ``conn`` and the
    target world's registry."""
    found = block_db.catalog_run(conn)
    if found is None:
        raise CatalogError(
            "no block catalog in the DB: run an export with the palette pass (WO-10.3), "
            "`gos export collect` and `gos db build`"
        )
    run_id, run_key = found
    verdicts: list[Verdict] = []
    seen: set[str] = set()
    for e in entries:
        refused: list[str] = []
        warnings: list[str] = []
        name: str | None = None
        if e.key in seen:
            warnings.append("listed twice")
        seen.add(e.key)
        if e.block == AIR:
            refused.append("air is the schematic's 0, not a palette entry")
        elif e.block == GT_MACHINES:
            refused.append("GT's machine block: GOS never pastes machines (plan 7.10)")
        if e.block not in world.blocks and e.block != AIR:
            refused.append(
                f"not in the world {world.world!r}'s registry (level.dat FML ItemData): "
                "that world has no such block"
            )
        state = block_db.state(conn, run_id, e.block, e.meta)
        if state is None and e.block != AIR:
            metas = block_db.metas(conn, run_id, e.block)
            if metas:
                shown = ", ".join(str(m) for m in metas[:16]) + (
                    ", ..." if len(metas) > 16 else ""
                )
                refused.append(
                    f"meta {e.meta} is not a placeable state of {e.block} in the catalog "
                    f"(metas: {shown}); an unknown meta can corrupt the chunk (worldedit-gtnh #28)"
                )
            else:
                refused.append(f"not in the block catalog (run {run_key})")
        if state is not None:
            name = state.name
            if state.has_te and e.block != GT_MACHINES:
                refused.append(
                    "has a tile entity (hasTileEntity): a shell holds plain blocks only (plan 7.10)"
                )
            if state.falls:
                warnings.append("falls (gravity): needs support under it")
            if state.liquid:
                warnings.append("a liquid")
            if state.hidden:
                warnings.append("in no creative tab")
        if e.meta > MAX_META:
            refused.append(
                f"meta {e.meta} > {MAX_META}: worldedit-gtnh #30 fails to set it on 2.9 (V16)"
            )
        verdicts.append(Verdict(e, name, tuple(refused), tuple(warnings)))
    problems: list[str] = []
    if len(seen) > MAX_TYPES:
        problems.append(
            f"{len(seen)} block types: a schematic's palette holds at most {MAX_TYPES}; split "
            "the shell (plan 7.10)"
        )
    if not seen:
        problems.append("the palette is empty")
    return Report(run_key, world.world, tuple(verdicts), tuple(problems))


def render(report: Report) -> list[str]:
    """The report as lines: one per entry (accepted or refused, with why), then the problems."""
    out = [
        f"Palette check against the world {report.world!r} and block catalog run {report.run_key}"
    ]
    for v in report.verdicts:
        mark = "ok     " if v.accepted else "REFUSED"
        name = f" ({v.name})" if v.name else ""
        out.append(f"{mark} {v.entry.key}{name}")
        out.extend(f"          refused: {r}" for r in v.refused)
        out.extend(f"          warning: {w}" for w in v.warnings)
    out.extend(f"PROBLEM: {p}" for p in report.problems)
    accepted = sum(v.accepted for v in report.verdicts)
    out.append(
        f"{accepted} of {len(report.verdicts)} entries accepted"
        + ("" if report.ok else "; the palette is refused")
    )
    return out
