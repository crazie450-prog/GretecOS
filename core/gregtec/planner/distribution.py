"""Storage and distribution (WO-4.5, plan §7.2 "Storage and distribution", D240-D242).

Three checks, every game number from the DB (cited ``mte:``, ``item:`` or ``lsc:``) or from GT's
own voltage table (:data:`gregtec.planner.gtmath.tiers.V`); this module only applies GT's rules
to them:

- **Buffer sizing** (:func:`size_buffer`, D241): a deficit window is ``(peak - generation) x
  duration`` EU at a tier. A battery-buffer option (the tier's buffer with the most slots, each
  slot holding the tier's battery with the most charge: GT's buffer takes only batteries of its
  own tier) is sized for both the energy and the EU/t (a buffer gives one amp per battery at its
  voltage); an LSC option uses the biggest cell at or below the tier cap. Battery buffers are
  recommended while the window fits in ``max_buffers`` of them, the LSC beyond that.
- **Cable runs** (:func:`check_run`, D242): each cable's max voltage, max amperage and loss per
  metre per amp from the export's ``cable`` table (GT's own fields; V10 resolved by D240), or,
  for a run made before it, parsed from the cable's tooltip (``machine.tooltip``), checked
  against GT's voltages, with the unreadable ones counted in the catalog's notes. GT's power path: a packet's voltage drops by every cable's loss on the way, cables rated
  below the voltage burn, and the run carries at most its weakest cable's amps.
- **Transformer placement** (:func:`place_transformers`, D242): the chain of transformers between
  a source tier and a line tier, each step's units from the DB's transformers in the mode the
  step needs (``down``, GT's default, or ``up``), counted from the line back to the source.

:func:`from_ledger` applies the buffer sizing and transformer placement to the EU ledger's
tiers (WO-4.4): the ledger's amp and tier warnings get their distribution answer here.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from gregtec.db import access
from gregtec.planner.gtmath.tiers import VN, V

TICKS_PER_S = 20
MAX_BUFFERS = (
    4  # D241: battery buffers while the window fits in this many, the LSC beyond
)
_TIER = {name.lower(): i for i, name in enumerate(VN)}
_COLOUR = re.compile("§.")  # Minecraft's colour codes
_THICKNESS = re.compile(r"^\d+x ")
# A number in any locale's digit grouping (2,048 / 2.048 / 2 048 with a (narrow) no-break space /
# 2'048): GT formats a cable's tooltip numbers in the JVM's locale.
_NUM = r"(\d(?:[\d.,' \t\u00a0\u202f]*\d)?)"
_CABLE = (
    re.compile(r"Max Voltage:[ \t]*" + _NUM + r"(?:[ \t]*\(([A-Za-z]+)\))?"),
    re.compile(r"Max Amperage:[ \t]*" + _NUM),
    re.compile(r"Loss/Meter/Ampere:[ \t]*" + _NUM),
)
CABLE_CLASSES = (
    "gregtech.api.metatileentity.implementations.MTECable",
    "gtPlusPlus.xmod.gregtech.api.metatileentity.implementations.GTPPMTECable",
)


class DistributionError(ValueError):
    """A request the catalog can't answer (an unknown tier or cable, no storage data)."""


def tier_index(name: str | int) -> int:
    if isinstance(name, int):
        if not 0 <= name < len(VN):
            raise DistributionError(f"no tier {name}")
        return name
    try:
        return _TIER[name.strip().lower()]
    except KeyError:
        raise DistributionError(f"unknown tier {name!r}") from None


def voltage(tier: str | int) -> int:
    """One amp of ``tier`` (GT's ``GTValues.V``)."""
    return V[tier_index(tier)]


# --- the catalog -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cable:
    mte_id: int
    name: str
    voltage: int  # max voltage
    amps: int  # max amperage
    loss: int  # EU lost per metre per amp

    @property
    def ref(self) -> str:
        return f"mte:{self.mte_id}"


@dataclass(frozen=True)
class Battery:
    registry_name: str
    meta: int
    name: str
    tier: int
    max_charge: float

    @property
    def ref(self) -> str:
        return f"item:{self.registry_name}@{self.meta}"


@dataclass(frozen=True)
class Buffer:
    mte_id: int
    name: str
    tier: int
    slots: int
    eu_out: int
    eu_store: int

    @property
    def ref(self) -> str:
        return f"mte:{self.mte_id}"


@dataclass(frozen=True)
class Transformer:
    mte_id: int
    name: str
    tier: int
    mode: str  # down or up
    eu_in: int
    amps_in: int
    eu_out: int
    amps_out: int

    @property
    def ref(self) -> str:
        return f"mte:{self.mte_id}"


@dataclass(frozen=True)
class Cell:
    name: str  # the cell's tier name (kekztech's Capacitor)
    registry_name: str
    meta: int
    glass_tier: int
    capacity: int

    @property
    def ref(self) -> str:
        return f"item:{self.registry_name}@{self.meta}"


@dataclass(frozen=True)
class Catalog:
    run_id: int | None
    cables: tuple[Cable, ...]
    batteries: tuple[Battery, ...]
    buffers: tuple[Buffer, ...]
    transformers: tuple[Transformer, ...]
    cells: tuple[Cell, ...]
    cable_source: str = (
        "none"  # export (the cable table), tooltip (an older run) or none
    )
    notes: tuple[str, ...] = ()


def parse_cable_tooltip(lines: Sequence[str]) -> tuple[int, int, int] | None:
    """(max voltage, max amperage, loss per metre per amp) from a GT cable's en_US tooltip lines
    (raw, with colour codes, as the export has them), in any number locale's digit grouping; None
    for any other tooltip, or when the voltage isn't one of GT's or doesn't match the tier named
    beside it (a misread number)."""
    text = _COLOUR.sub("", "\n".join(lines))
    found = [p.search(text) for p in _CABLE]
    if not all(found):
        return None
    v, a, loss = (int(re.sub(r"\D", "", m.group(1))) for m in found if m)
    named = found[0].group(2) if found[0] else None
    if v not in V or (named is not None and _TIER.get(named.lower()) != V.index(v)):
        return None
    return v, a, loss


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _current_run(conn: sqlite3.Connection, tables: set[str]) -> int | None:
    """The run the recipe tools answer from (``current_run``), if the DB has one."""
    if "export_run" not in tables:
        return None
    rows = access.query(conn, "current_run")
    return int(rows[0]["id"]) if rows else None


def _has_rows(
    conn: sqlite3.Connection, tables: set[str], table: str, run_id: int
) -> bool:
    if table not in tables:  # a DB from before schema 5 (read-only: never upgraded)
        return False
    sql = f"SELECT 1 FROM {table} WHERE run_id = ? LIMIT 1"
    return conn.execute(sql, (run_id,)).fetchone() is not None


def _cables(
    conn: sqlite3.Connection, tables: set[str], run_id: int | None
) -> tuple[list[Cable], str, list[str]]:
    if "machine" not in tables:
        return [], "none", []
    mte_names = {
        int(r[0]): str(r[1])
        for r in conn.execute(
            "SELECT mte_id, name_en FROM machine WHERE mte_id IS NOT NULL"
        )
    }
    if run_id is not None and _has_rows(conn, tables, "cable", run_id):
        rows = conn.execute(
            "SELECT mte_id, voltage, amperage, loss FROM cable WHERE run_id = ?"
            " ORDER BY mte_id",
            (run_id,),
        )
        cables = [
            Cable(int(r[0]), mte_names.get(int(r[0]), f"mte {r[0]}"), *map(int, r[1:]))
            for r in rows
        ]
        return cables, "export", []
    cables, unreadable = [], 0
    for mte_id, name, tooltip in conn.execute(
        "SELECT mte_id, name_en, tooltip FROM machine WHERE class_name IN"
        f" ({','.join('?' * len(CABLE_CLASSES))}) ORDER BY mte_id",
        CABLE_CLASSES,
    ):
        lines = str(tooltip or "").split("\n")  # lines joined by build
        stats = parse_cable_tooltip(lines)
        if stats is None:
            unreadable += 1
        else:
            cables.append(Cable(int(mte_id), str(name), *stats))
    notes = []
    if unreadable:
        notes.append(
            f"{unreadable} cable tooltips unreadable (not en_US?), their cables left out:"
            " an export from WO-4.5's GregtecLink has every cable's own fields"
        )
    return cables, "tooltip" if cables else "none", notes


def catalog(conn: sqlite3.Connection, run_id: int | None = None) -> Catalog:
    """The cables and storage tables of the current export run (``current_run``, or
    ``run_id``'s). With no storage rows in that run, or a DB from before schema 5, ``run_id`` is
    None and the storage tuples are empty; cables then come from their tooltips."""
    tables = _tables(conn)
    if run_id is None:
        run_id = _current_run(conn, tables)
    cables, source, notes = _cables(conn, tables, run_id)
    if run_id is None or not _has_rows(conn, tables, "battery_buffer", run_id):
        return Catalog(None, tuple(cables), (), (), (), (), source, tuple(notes))
    names = {
        (r[0], int(r[1])): str(r[2])
        for r in conn.execute(
            "SELECT registry_name, meta, name_en FROM item WHERE nbt_hash = ''"
        )
    }
    mte_names = {
        int(r[0]): str(r[1])
        for r in conn.execute("SELECT mte_id, name_en FROM machine")
    }
    batteries = tuple(
        Battery(
            r[0],
            int(r[1]),
            names.get((r[0], int(r[1])), f"{r[0]}@{r[1]}"),
            int(r[2]),
            r[3],
        )
        for r in conn.execute(
            "SELECT registry_name, meta, tier, max_charge FROM battery"
            " WHERE run_id = ? AND rechargeable AND nbt_hash = '' AND meta != 32767"
            " ORDER BY registry_name, meta",
            (run_id,),
        )
    )
    buffers = tuple(
        Buffer(int(r[0]), mte_names.get(int(r[0]), f"mte {r[0]}"), *map(int, r[1:]))
        for r in conn.execute(
            "SELECT mte_id, tier, slots, eu_out, eu_store FROM battery_buffer WHERE run_id = ?"
            " ORDER BY mte_id",
            (run_id,),
        )
    )
    transformers = tuple(
        Transformer(
            int(r[0]),
            mte_names.get(int(r[0]), f"mte {r[0]}"),
            int(r[1]),
            str(r[2]),
            *map(int, r[3:]),
        )
        for r in conn.execute(
            "SELECT mte_id, tier, mode, eu_in, amps_in, eu_out, amps_out FROM transformer"
            " WHERE run_id = ? ORDER BY mte_id, mode",
            (run_id,),
        )
    )
    cells = tuple(
        Cell(str(r[0]), str(r[1]), int(r[2]), int(r[3]), int(r[4]))
        for r in conn.execute(
            "SELECT name, registry_name, meta, glass_tier, capacity FROM lsc_cell"
            " WHERE run_id = ? ORDER BY meta",
            (run_id,),
        )
    )
    return Catalog(
        run_id,
        tuple(cables),
        batteries,
        buffers,
        transformers,
        cells,
        source,
        tuple(notes),
    )


def find_cable(cat: Catalog, key: str) -> Cable:
    """A cable by ``mte:<id>`` or its exact name (any case)."""
    key = key.strip()
    if key.lower().startswith("mte:"):
        try:
            mte = int(key[4:])
        except ValueError:
            raise DistributionError(f"bad cable ref {key!r}") from None
        hits = [c for c in cat.cables if c.mte_id == mte]
    else:
        hits = [c for c in cat.cables if c.name.lower() == key.lower()]
    if len(hits) != 1:
        raise DistributionError(
            f"no cable {key!r}"
            if not hits
            else f"{key!r} names {len(hits)} cables: use mte:<id>"
        )
    return hits[0]


# --- buffer sizing (D241) ----------------------------------------------------------------------


@dataclass(frozen=True)
class BufferOption:
    kind: str  # battery_buffer or lsc
    count: int  # buffers, or LSC cells
    unit: str  # what one is
    unit_ref: str
    unit_eu: int  # EU one holds
    capacity_eu: int  # count x unit_eu
    detail: str
    refs: tuple[str, ...]
    eut_max: int | None = None  # what they give at once (battery buffers), EU/t


@dataclass(frozen=True)
class BufferSizing:
    tier: str
    peak_eut: float
    gen_eut: float
    window_s: float
    deficit_eut: float
    energy_eu: int
    battery_buffer: BufferOption | None
    lsc: BufferOption | None
    recommend: str  # battery_buffer, lsc or none
    reason: str
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _battery_option(
    cat: Catalog, tier: int, energy: int, deficit: float, max_buffers: int
) -> BufferOption | None:
    """The tier's buffer with the most slots, filled with the tier's smallest battery that keeps
    the count at or under ``max_buffers`` (else the one with the most charge): a bigger battery
    costs more to make, and the endgame ones hold ~2^63 EU (D241)."""
    bufs = [b for b in cat.buffers if b.tier == tier]
    bats = [b for b in cat.batteries if b.tier == tier and b.max_charge > 0]
    if not bufs or not bats:
        return None
    buf = min(bufs, key=lambda b: (-b.slots, b.mte_id))
    unit_eut = buf.slots * buf.eu_out  # one amp per battery at the buffer's voltage
    by_power = math.ceil(deficit / unit_eut)
    smallest_first = sorted(bats, key=lambda b: (b.max_charge, b.registry_name, b.meta))
    for bat in smallest_first:
        unit_eu = buf.slots * int(bat.max_charge) + buf.eu_store
        by_energy = math.ceil(energy / unit_eu)
        count = max(by_energy, by_power, 1)
        if count <= max_buffers:
            break
    else:
        bat = min(bats, key=lambda b: (-b.max_charge, b.registry_name, b.meta))
        unit_eu = buf.slots * int(bat.max_charge) + buf.eu_store
        by_energy = math.ceil(energy / unit_eu)
        count = max(by_energy, by_power, 1)
    return BufferOption(
        "battery_buffer",
        count,
        f"{buf.name} with {buf.slots} x {bat.name}",
        buf.ref,
        unit_eu,
        count * unit_eu,
        f"{buf.slots} x {int(bat.max_charge)} EU + {buf.eu_store} EU own store each;"
        f" {buf.slots} A at {buf.eu_out} EU/t each; {by_energy} for the energy,"
        f" {by_power} for the EU/t",
        (buf.ref, bat.ref),
        count * unit_eut,
    )


def _cell_tier(cell: Cell) -> int | None:
    return _TIER.get(cell.name.lower())


def _lsc_option(cat: Catalog, tier_cap: int, energy: int) -> BufferOption | None:
    cells = [
        c
        for c in cat.cells
        if c.capacity > 0 and (t := _cell_tier(c)) is not None and t <= tier_cap
    ]
    if not cells:
        return None
    cell = min(cells, key=lambda c: (-c.capacity, c.meta))
    count = max(math.ceil(energy / cell.capacity), 1)
    return BufferOption(
        "lsc",
        count,
        f"LSC with {cell.name} capacitor cells (glass tier {cell.glass_tier})",
        cell.ref,
        cell.capacity,
        count * cell.capacity,
        f"{cell.capacity} EU per {cell.name} cell; in and out through energy hatches and"
        " dynamos at the tier",
        (cell.ref,),
    )


def size_buffer(
    cat: Catalog,
    tier: str | int,
    peak_eut: float,
    gen_eut: float,
    window_s: float,
    *,
    tier_cap: str | int | None = None,
    max_buffers: int = MAX_BUFFERS,
) -> BufferSizing:
    """Storage for a deficit window at ``tier``: ``(peak - generation) x window`` EU."""
    t = tier_index(tier)
    cap = t if tier_cap is None else tier_index(tier_cap)
    if not all(math.isfinite(x) and x >= 0 for x in (window_s, peak_eut, gen_eut)):
        raise DistributionError(
            "peak, generation and window must be finite, not negative"
        )
    if max_buffers < 1:
        raise DistributionError("max buffers must be at least 1")
    deficit = max(0.0, peak_eut - gen_eut)
    energy = math.ceil(deficit * window_s * TICKS_PER_S)
    name = VN[t]
    if energy <= 0:
        return BufferSizing(
            name,
            peak_eut,
            gen_eut,
            window_s,
            deficit,
            0,
            None,
            None,
            "none",
            "generation covers the peak: no buffer needed",
        )
    notes: list[str] = []
    bb = _battery_option(cat, t, energy, deficit, max_buffers)
    lsc = _lsc_option(cat, cap, energy)
    if bb is None:
        notes.append(f"no {name} battery buffer with a {name} battery in the DB")
    if lsc is None:
        notes.append(f"no LSC cell at or below {VN[cap]} in the DB")
    else:
        notes.append("an LSC loses 1% of its capacity a day (its tooltip); not counted")
    if bb is not None and bb.count <= max_buffers:
        rec, why = (
            "battery_buffer",
            f"fits in {bb.count} of at most {max_buffers} buffers",
        )
    elif lsc is not None:
        why = (
            f"needs {bb.count} buffers, over {max_buffers}"
            if bb
            else "no battery buffer option"
        )
        rec = "lsc"
    elif bb is not None:
        rec, why = (
            "battery_buffer",
            f"{bb.count} buffers: no LSC cell at or below {VN[cap]}",
        )
    else:
        rec, why = "none", "no storage at this tier in the DB"
    return BufferSizing(
        name,
        peak_eut,
        gen_eut,
        window_s,
        deficit,
        energy,
        bb,
        lsc,
        rec,
        why,
        tuple(notes),
    )


# --- cable runs (D242) -------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    cable: Cable
    length: int  # blocks


@dataclass(frozen=True)
class CableCheck:
    voltage: int  # in
    amps: float
    segments: tuple[Segment, ...]
    max_voltage: int  # the run's weakest cable's
    max_amps: int
    loss_per_amp: int  # EU/t lost per amp over the whole run
    voltage_out: int
    loss_eut: float
    delivered_eut: float
    problems: tuple[str, ...]
    burns: tuple[str, ...]  # refs of the cables that burn

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["segments"] = [
            {**asdict(s.cable), "ref": s.cable.ref, "length": s.length}
            for s in self.segments
        ]
        d["ok"] = self.ok
        return d


def check_run(segments: Sequence[Segment], voltage: int, amps: float) -> CableCheck:
    """A cable run carrying ``amps`` at ``voltage`` (GT's power path: the packet loses each
    cable's loss per metre on the way; a cable rated below the voltage burns; the run carries
    at most its weakest cable's amps)."""
    if not segments:
        raise DistributionError("a run needs at least one segment")
    if voltage <= 0 or not 0 <= amps < math.inf or any(s.length <= 0 for s in segments):
        raise DistributionError(
            "voltage and lengths must be positive, amps finite and not negative"
        )
    problems: list[str] = []
    burns: list[str] = []
    for s in segments:
        c = s.cable
        if voltage > c.voltage:
            problems.append(
                f"overvoltage: {c.name} ({c.ref}) takes {c.voltage} V, the run carries {voltage} V"
            )
            burns.append(c.ref)
        if amps > c.amps:
            problems.append(
                f"amp overload: {c.name} ({c.ref}) carries {c.amps} A, the run needs {_n(amps)} A"
            )
            if c.ref not in burns:
                burns.append(c.ref)
    loss = sum(s.cable.loss * s.length for s in segments)
    v_out = max(0, voltage - loss)
    if v_out == 0:
        problems.append(
            f"the run loses {loss} V per amp: nothing arrives at {voltage} V"
        )
    return CableCheck(
        voltage,
        amps,
        tuple(segments),
        min(s.cable.voltage for s in segments),
        min(s.cable.amps for s in segments),
        loss,
        v_out,
        min(loss, voltage) * amps,
        v_out * amps,
        tuple(problems),
        tuple(burns),
    )


def cables_for(cat: Catalog, voltage: int, amps: float, limit: int = 5) -> list[Cable]:
    """Cables of the voltage's own tier (rated at least ``voltage``, under four times it) that
    carry ``amps``, lowest loss first (then fewest amps, mte id), the thinnest of each kind (its
    name without the ``Nx`` count). Options, not a choice: what a cable costs to make is not here
    (D242)."""
    top = voltage * 4
    fits = [c for c in cat.cables if voltage <= c.voltage < top and c.amps >= amps]
    out: dict[str, Cable] = {}
    for c in sorted(fits, key=lambda c: (c.loss, c.amps, c.mte_id)):
        out.setdefault(_THICKNESS.sub("", c.name).lower(), c)
    return list(out.values())[:limit]


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


# --- transformer placement (D242) --------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    high: str  # the step's tiers
    low: str
    mode: str  # down: high -> low; up: low -> high
    unit: Transformer
    count: int
    amps_in: int  # what the step draws on its input side, in total
    amps_out: float  # what it must give on its output side
    candidates: tuple[
        tuple[str, str, int], ...
    ]  # (ref, name, count) for every unit of the tier


@dataclass(frozen=True)
class TransformerPlan:
    source: str
    line: str
    amps: float  # needed at the line's tier
    steps: tuple[Step, ...]
    source_amps: int  # what the chain draws at the source's tier
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "line": self.line,
            "amps": self.amps,
            "source_amps": self.source_amps,
            "steps": [
                {
                    "high": s.high,
                    "low": s.low,
                    "mode": s.mode,
                    "unit": {**asdict(s.unit), "ref": s.unit.ref},
                    "count": s.count,
                    "amps_in": s.amps_in,
                    "amps_out": s.amps_out,
                    "candidates": [list(c) for c in s.candidates],
                }
                for s in self.steps
            ],
            "notes": list(self.notes),
        }


def place_transformers(
    cat: Catalog, source_tier: str | int, line_tier: str | int, amps: float
) -> TransformerPlan:
    """The transformers between a source at ``source_tier`` and lines drawing ``amps`` at
    ``line_tier``. Counted from the lines back: each step needs ``ceil(amps out / unit's amps
    out)`` units, and draws ``ceil(amps out x V out / V in)`` amps upstream (transformers keep
    the EU). At each step the unit needing the fewest is used (then the fewest amps, mte id)."""
    s, ln = tier_index(source_tier), tier_index(line_tier)
    if not 0 <= amps < math.inf:  # also NaN
        raise DistributionError("amps must be finite, not negative")
    if s == ln:
        return TransformerPlan(
            VN[s], VN[ln], amps, (), math.ceil(amps), ("same tier: none",)
        )
    mode = "down" if s > ln else "up"
    # the steps from the line's side to the source's: (tier the unit is, high, low)
    if mode == "down":
        tiers = [(k, k + 1, k) for k in range(ln, s)]
    else:
        tiers = [(k, k + 1, k) for k in range(ln - 1, s - 1, -1)]
    need = amps
    steps: list[Step] = []
    for unit_tier, high, low in tiers:
        units = [
            t
            for t in cat.transformers
            if t.tier == unit_tier
            and t.mode == mode
            and (t.eu_in > t.eu_out if mode == "down" else t.eu_out > t.eu_in)
        ]
        if not units:
            raise DistributionError(
                f"no {VN[unit_tier]} transformer ({mode}) in the DB"
            )
        counts = [
            (max(math.ceil(need / u.amps_out), 1 if need > 0 else 0), u) for u in units
        ]
        count, unit = min(counts, key=lambda cu: (cu[0], cu[1].amps_out, cu[1].mte_id))
        upstream = math.ceil(need * unit.eu_out / unit.eu_in) if need > 0 else 0
        steps.append(
            Step(
                VN[high],
                VN[low],
                mode,
                unit,
                count,
                upstream,
                need,
                tuple(
                    (u.ref, u.name, c)
                    for c, u in sorted(counts, key=lambda cu: cu[1].mte_id)
                ),
            )
        )
        need = upstream
    steps.reverse()  # from the source to the lines
    return TransformerPlan(VN[s], VN[ln], amps, tuple(steps), int(need))


# --- from the EU ledger ------------------------------------------------------------------------


@dataclass
class LedgerDistribution:
    scope: str
    window_s: float
    buffers: list[BufferSizing] = field(default_factory=list)
    transformers: list[TransformerPlan] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "window_s": self.window_s,
            "buffers": [b.as_dict() for b in self.buffers],
            "transformers": [t.as_dict() for t in self.transformers],
            "notes": list(self.notes),
        }


def from_ledger(
    cat: Catalog,
    tiers: Iterable[Any],
    window_s: float,
    *,
    scope: str = "all",
    max_buffers: int = MAX_BUFFERS,
) -> LedgerDistribution:
    """Buffers and transformers for the ledger's per-tier nets (``ledger.TierNet``s of one
    scope). A tier's deficit is its lines' peak over its own sources' generation; when those add
    up to more than the base's shortfall (its whole peak over its whole generation: transformers
    bring the rest from other tiers), the base's shortfall is shared out between the short tiers
    in proportion to their deficits, so the buffers together store no more than it. Lines at a tier with no source get the transformer chain from the nearest
    sourced tier above (else below), sized for the lines' amps."""
    nets = [t for t in tiers]
    out = LedgerDistribution(scope, window_s)
    if not nets:
        out.notes.append("the ledger has no lines or sources in this scope")
        return out
    base_short = max(
        0.0,
        math.fsum(t.use_eut_peak for t in nets) - math.fsum(t.gen_eut for t in nets),
    )
    cap = max(tier_index(t.tier) for t in nets)
    sourced = sorted(tier_index(t.tier) for t in nets if t.gen_eut > 0 or t.amps_in > 0)
    tier_short = math.fsum(
        max(0.0, t.use_eut_peak - t.gen_eut) for t in nets if t.use_eut_peak > 0
    )
    share = min(1.0, base_short / tier_short) if tier_short > 0 else 0.0
    for t in nets:
        if t.use_eut_peak <= 0:
            continue
        short = max(0.0, t.use_eut_peak - t.gen_eut) * share
        if short > 0:
            # size_buffer takes peak and generation: give it the share this tier must store
            out.buffers.append(
                size_buffer(
                    cat,
                    t.tier,
                    t.use_eut_peak,
                    t.use_eut_peak - short,
                    window_s,
                    tier_cap=cap,
                    max_buffers=max_buffers,
                )
            )
        ti = tier_index(t.tier)
        if ti not in sourced and sourced:
            above = [s for s in sourced if s > ti]
            src = above[0] if above else sourced[-1]
            try:
                out.transformers.append(place_transformers(cat, src, ti, t.amps_out))
            except DistributionError as exc:
                out.notes.append(f"{t.tier}: {exc}")
    if base_short == 0:
        out.notes.append("generation covers the base's peak: no buffer needed")
    return out
