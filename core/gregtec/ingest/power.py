"""The power catalog's game data from an export run's ``gt_power.json`` (WO-4.1, D230).

GregtecLink's power pass writes what no recipe carries (docs/contracts/gregteclink-export-v1.md):

- ``sizes``: GT's four turbine rotor sizes (``ToolTurbine*``: speed multiplier, base damage,
  durability multiplier), by the turbine item's meta (170, 172, 174, 176);
- ``materials``: every material GT makes turbine blades of, with its tool stats as the instance
  has them, and for each size what GT's own ``TurbineStatCalculator`` gives for a new rotor;
- ``generators``: every single-block generator (``MTEBasicGenerator``): GT's fuel efficiency,
  voltage and amps out, and for each fuel of its map what GT's own ``getFuelValue`` gives;
- ``containers``: the fluid in each fluid container a fuel map lists as its input (a cell of a
  fuel is that fuel: GT's fuel maps index fuels by fluid);
- ``storage`` (WO-4.5, D240; absent from runs made before it): ``cables`` (every GT and GT++
  cable's ``mVoltage``, ``mAmperage`` and ``mCableLossPerMeter``; absent from the first WO-4.5
  runs), ``batteries`` (every ``battery*`` oredict member that is an IC2 electric item: tier, max
  charge, transfer limit, rechargeable; a wildcard member at meta 0), ``buffers``
  (every GT battery buffer: tier, slots, voltage in and out, own EU store), ``transformers``
  (every GT transformer, GT's power and GT++'s hi-amp ones too: voltage and amps in and out in
  both modes, ``down`` and ``up``) and ``lsc`` (the Lapotronic Supercapacitor's cells: block,
  glass tier, capacity as a decimal string).

Floats were written as the double of GT's ``float``, so they read back to the bit. Read only from
runs whose ``parts.power`` is ``ok``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.ingest.gtdump import item_ref

FILE = "gt_power.json"
# TurbineStatCalculator values the pass writes for each rotor (all floats but overflow and
# max_durability).
ROTOR_VALUES = (
    "max_durability",
    "base_efficiency",
    "loose_efficiency",
    "optimal_flow",
    "optimal_steam_flow",
    "optimal_gas_flow",
    "optimal_plasma_flow",
    "optimal_loose_steam_flow",
    "optimal_loose_gas_flow",
    "optimal_loose_plasma_flow",
    "overflow",
)


class FormatError(ValueError):
    """Not the shape GregtecLink writes."""


@dataclass(frozen=True)
class RotorSize:
    size: int  # the turbine item's meta: 170 small, 172 normal, 174 large, 176 huge
    name: str
    speed_multiplier: float
    base_damage: float
    durability_multiplier: float


@dataclass(frozen=True)
class GtRotor:
    """What GT's own TurbineStatCalculator gave for one new rotor (in the game)."""

    size: int
    values: Mapping[str, float]  # ROTOR_VALUES


@dataclass(frozen=True)
class RotorMaterial:
    name: str  # GT's material name (Materials.mName)
    tool_speed: float
    tool_quality: int
    durability: int
    steam_multiplier: float
    gas_multiplier: float
    plasma_multiplier: float
    rotors: tuple[GtRotor, ...]


@dataclass(frozen=True)
class GtFuelValue:
    """What a generator gives for one fuel of its map (GT's ``getFuelValue``). For a fluid (also
    the fluid inside a container input): ``eu`` per operation burning ``consumed`` litres (the
    int GT's generator uses: 0 past ``Integer.MAX_VALUE``) and ``eu_long`` before that cap. For
    an item input (``item``: registry name, meta): ``item_eu`` and ``item_eu_long`` per item."""

    special: int
    fluid: str | None = None
    eu: int | None = None
    eu_long: int | None = None
    consumed: int | None = None
    item: tuple[str, int] | None = None
    item_eu: int | None = None
    item_eu_long: int | None = None


@dataclass(frozen=True)
class Container:
    """A fluid container a fuel map lists as input, and the fluid in it."""

    registry_name: str
    meta: int
    nbt_hash: str
    fluid: str
    mb: int


@dataclass(frozen=True)
class Generator:
    mte_id: int
    class_name: str
    tier: int
    efficiency: int
    eu_out: int
    amps_out: int
    recipe_map: str | None
    fuels: tuple[GtFuelValue, ...]


@dataclass(frozen=True)
class CableStats:
    """A GT cable's own fields (``MTECable``), so no tooltip's language or number locale."""

    mte_id: int
    voltage: int  # mVoltage
    amperage: int  # mAmperage
    loss: int  # mCableLossPerMeter: EU per metre per amp


@dataclass(frozen=True)
class Battery:
    """An item a battery buffer of its tier takes (GT: the item's tier equals the buffer's)."""

    registry_name: str
    meta: int
    nbt_hash: str
    ores: tuple[str, ...]  # the battery* oredict names it is in
    tier: int
    max_charge: float  # EU (IC2's double)
    transfer_limit: float  # EU/t
    rechargeable: bool  # takes a charge (GT's one-use ZPM module doesn't)


@dataclass(frozen=True)
class Buffer:
    mte_id: int
    class_name: str
    tier: int
    slots: int
    eu_in: int  # voltage in and out
    eu_out: int
    eu_store: int  # the buffer's own store, besides its batteries


@dataclass(frozen=True)
class TransformerMode:
    eu_in: int
    amps_in: int
    eu_out: int
    amps_out: int


@dataclass(frozen=True)
class Transformer:
    mte_id: int
    class_name: str
    tier: int
    down: TransformerMode  # allowed to work (GT's default): in at the tier above, out at its own
    up: TransformerMode  # disabled (soft mallet): in at its own tier, out at the one above


@dataclass(frozen=True)
class LscCell:
    name: str  # kekztech's Capacitor name (IV, LuV, ..., None, EV, ...)
    registry_name: str
    meta: int
    glass_tier: int
    capacity: int  # EU


@dataclass(frozen=True)
class Storage:
    batteries: tuple[Battery, ...]
    buffers: tuple[Buffer, ...]
    transformers: tuple[Transformer, ...]
    lsc: tuple[LscCell, ...]
    cables: tuple[CableStats, ...] = ()


@dataclass(frozen=True)
class Power:
    sizes: tuple[RotorSize, ...]
    materials: tuple[RotorMaterial, ...]
    generators: tuple[Generator, ...]
    containers: tuple[Container, ...] = ()
    storage: Storage | None = None


def status(manifest: Mapping[str, Any]) -> str:
    """``parts.power``; ``absent`` for a run made before WO-4.1."""
    parts = manifest.get("parts")
    value = parts.get("power") if isinstance(parts, dict) else None
    return value if isinstance(value, str) else "absent"


def _num(d: Mapping[str, Any], key: str) -> float:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise FormatError(f"{key} is not a number")
    return float(v)


def _int(d: Mapping[str, Any], key: str) -> int:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise FormatError(f"{key} is not an integer")
    return v


def _opt(d: Mapping[str, Any], key: str) -> int | None:
    return _int(d, key) if key in d else None


def _fuel(d: Mapping[str, Any]) -> GtFuelValue:
    fluid = d.get("fluid")
    item = d.get("input")
    if not isinstance(fluid, str) and not isinstance(item, dict):
        raise FormatError("a fuel has neither a fluid nor an item")
    ref = None
    if isinstance(item, dict):
        ref = (str(item.get("item")), _int(item, "meta"))
    return GtFuelValue(
        special=_int(d, "special"),
        fluid=fluid if isinstance(fluid, str) else None,
        eu=_opt(d, "eu"),
        eu_long=_opt(d, "eu_long"),
        consumed=_opt(d, "consumed"),
        item=ref,
        item_eu=_opt(d, "item_eu"),
        item_eu_long=_opt(d, "item_eu_long"),
    )


def _container(d: Mapping[str, Any]) -> Container:
    ref, _nbt = item_ref(d["input"])
    return Container(ref.name, ref.meta, ref.nbt_hash, str(d["fluid"]), _int(d, "mb"))


def _mode(d: Mapping[str, Any]) -> TransformerMode:
    return TransformerMode(
        _int(d, "eu_in"), _int(d, "amps_in"), _int(d, "eu_out"), _int(d, "amps_out")
    )


def _storage(d: Mapping[str, Any]) -> Storage:
    batteries = []
    for b in d["batteries"]:
        if "error" in b:
            continue
        ref, _nbt = item_ref(b["battery"])
        batteries.append(
            Battery(
                ref.name,
                ref.meta,
                ref.nbt_hash,
                tuple(str(o) for o in b["ore"]),
                _int(b, "tier"),
                _num(b, "max_charge"),
                _num(b, "transfer_limit"),
                b.get("rechargeable") is True,
            )
        )
    buffers = tuple(
        Buffer(
            _int(b, "mte_id"),
            str(b["class"]),
            _int(b, "tier"),
            _int(b, "slots"),
            _int(b, "eu_in"),
            _int(b, "eu_out"),
            _int(b, "eu_store"),
        )
        for b in d["buffers"]
        if "error" not in b
    )
    transformers = tuple(
        Transformer(
            _int(t, "mte_id"),
            str(t["class"]),
            _int(t, "tier"),
            _mode(t["down"]),
            _mode(t["up"]),
        )
        for t in d["transformers"]
        if "error" not in t
    )
    cells = []
    for c in d["lsc"]:
        if "error" in c:
            continue
        ref, _nbt = item_ref(c["block"])
        capacity = c.get("capacity")
        if not isinstance(capacity, str) or not capacity.isdigit():
            raise FormatError("an LSC cell's capacity is not a decimal string")
        cells.append(
            LscCell(
                str(c["name"]), ref.name, ref.meta, _int(c, "glass_tier"), int(capacity)
            )
        )
    cables = tuple(
        CableStats(
            _int(c, "mte_id"), _int(c, "voltage"), _int(c, "amperage"), _int(c, "loss")
        )
        for c in d.get("cables", ())
    )
    return Storage(tuple(batteries), buffers, transformers, tuple(cells), cables)


def parse(root: Mapping[str, Any]) -> Power:
    try:
        sizes = tuple(
            RotorSize(
                _int(s, "size"),
                str(s.get("name")),
                _num(s, "speed_multiplier"),
                _num(s, "base_damage"),
                _num(s, "durability_multiplier"),
            )
            for s in root["sizes"]
        )
        materials = tuple(
            RotorMaterial(
                name=str(m["name"]),
                tool_speed=_num(m, "tool_speed"),
                tool_quality=_int(m, "tool_quality"),
                durability=_int(m, "durability"),
                steam_multiplier=_num(m, "steam_multiplier"),
                gas_multiplier=_num(m, "gas_multiplier"),
                plasma_multiplier=_num(m, "plasma_multiplier"),
                rotors=tuple(
                    GtRotor(_int(r, "size"), {k: _num(r, k) for k in ROTOR_VALUES})
                    for r in m["rotors"]
                    if "error" not in r
                ),
            )
            for m in root["materials"]
        )
        generators = tuple(
            Generator(
                mte_id=_int(g, "mte_id"),
                class_name=str(g["class"]),
                tier=_int(g, "tier"),
                efficiency=_int(g, "efficiency"),
                eu_out=_int(g, "eu_out"),
                amps_out=_int(g, "amps_out"),
                recipe_map=g.get("recipe_map")
                if isinstance(g.get("recipe_map"), str)
                else None,
                fuels=tuple(_fuel(f) for f in g.get("fuels", ()) if "error" not in f),
            )
            for g in root["generators"]
            if "error" not in g
        )
        containers = tuple(_container(c) for c in root.get("containers", ()))
        storage = _storage(root["storage"]) if "storage" in root else None
    except (KeyError, TypeError, ValueError) as exc:
        raise FormatError(f"{FILE}: {type(exc).__name__}: {exc}") from None
    return Power(sizes, materials, generators, containers, storage)


def read(run: Path) -> Power:
    """``gt_power.json`` of a run; ``FormatError`` if it isn't the shape GregtecLink writes."""
    try:
        root = json.loads((run / FILE).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FormatError(f"{FILE}: {exc}") from None
    if not isinstance(root, dict):
        raise FormatError(f"{FILE}: not an object")
    return parse(root)
