"""GT's ore mixes from an export run's ``gt_ore_mixes.json`` (plan section 5.2 step 6, D126).

GregtecLink writes GT 5.09.54.265's worldgen registry (``WorldgenGTOreLayer.sList``): each ore
layer's name (the key VisualProspecting stores veins by), its heights after GT's own fix-up,
weight, density, size, dimensions by name, per-dimension height overrides, and its four ores
(primary, secondary, between, sporadic), each by GT material name and by the ore block's
registry name + meta in plain stone (never a numeric ID). It also writes the instance's
dimension IDs with the names GT's worldgen matches a layer's dimensions by, so a vein (by
dimension ID) finds its mix's heights. See docs/contracts/gregteclink-export-v1.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

FILE = "gt_ore_mixes.json"
LAYERS = ("primary", "secondary", "between", "sporadic")


class FormatError(ValueError):
    """Not the shape GregtecLink writes."""


@dataclass(frozen=True)
class Ore:
    layer: str  # primary, secondary, between or sporadic
    material: (
        str | None
    )  # GT's material name (Materials.mName, a Werkstoff or GT++ name)
    name: str | None  # its display name
    registry_name: str | None  # the ore block in plain stone, where it has one
    meta: int | None


@dataclass(frozen=True)
class Mix:
    id: str  # GT's name, e.g. ore.mix.naquadah
    name: str  # display name, e.g. Naquadah
    ymin: int
    ymax: int
    weight: int
    density: int
    size: int
    dims: tuple[str, ...]  # dimension names, as GT's worldgen matches them
    heights: dict[str, tuple[int, int]]  # per-dimension (ymin, ymax) overrides
    ores: tuple[Ore, ...]  # in LAYERS order
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dimension:
    dim: int
    name: str | None
    provider: str | None


@dataclass
class OreMixes:
    mixes: list[Mix]
    dimensions: list[Dimension]
    problems: list[str] = field(default_factory=list)

    def ids_by_name(self) -> dict[str, list[int]]:
        """{dimension name: its dimension IDs}; several IDs can share a name."""
        out: dict[str, list[int]] = {}
        for d in self.dimensions:
            if d.name is not None:
                out.setdefault(d.name, []).append(d.dim)
        return out


def _int(o: dict[str, Any], key: str, where: str) -> int:
    v = o.get(key)
    if type(v) is not int:
        raise FormatError(f"{where}: {key} is not a whole number")
    return v


def _str(o: dict[str, Any], key: str) -> str | None:
    v = o.get(key)
    return v if isinstance(v, str) and v else None


def _ore(layer: str, o: object, where: str) -> Ore:
    if not isinstance(o, dict):
        raise FormatError(f"{where}: ores.{layer} is not an object")
    ref = o.get("ore")
    reg = meta = None
    if isinstance(ref, dict) and isinstance(ref.get("item"), str):
        reg = ref["item"]
        meta = _int(ref, "meta", f"{where} ores.{layer}.ore")
    return Ore(layer, _str(o, "material"), _str(o, "name"), reg, meta)


def _mix(o: object, n: int) -> Mix:
    if not isinstance(o, dict) or not isinstance(o.get("name"), str) or not o["name"]:
        raise FormatError(f"layer {n}: no name")
    where = o["name"]
    dims = o.get("dims")
    if not isinstance(dims, list) or not all(isinstance(d, str) for d in dims):
        raise FormatError(f"{where}: dims is not a list of names")
    heights: dict[str, tuple[int, int]] = {}
    raw = o.get("dim_heights", {})
    if not isinstance(raw, dict):
        raise FormatError(f"{where}: dim_heights is not an object")
    for dim, pair in raw.items():
        if not (
            isinstance(pair, list)
            and len(pair) == 2
            and all(type(v) is int for v in pair)
        ):
            raise FormatError(f"{where}: dim_heights.{dim} is not [min, max]")
        heights[dim] = (pair[0], pair[1])
    ores = o.get("ores")
    if not isinstance(ores, dict):
        raise FormatError(f"{where}: no ores")
    errors = o.get("errors", [])
    return Mix(
        id=where,
        name=_str(o, "localized") or where,
        ymin=_int(o, "min_y", where),
        ymax=_int(o, "max_y", where),
        weight=_int(o, "weight", where),
        density=_int(o, "density", where),
        size=_int(o, "size", where),
        dims=tuple(dims),
        heights=heights,
        ores=tuple(
            _ore(layer, ores.get(layer), where) for layer in LAYERS if layer in ores
        ),
        errors=tuple(str(e) for e in errors) if isinstance(errors, list) else (),
    )


def parse(data: bytes) -> OreMixes:
    """``gt_ore_mixes.json``; ``ValueError`` if it isn't the shape GregtecLink writes."""
    try:
        root = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormatError(f"{FILE} is not JSON: {exc}") from None
    if not isinstance(root, dict) or not isinstance(root.get("layers"), list):
        raise FormatError(f"{FILE} has no layers")
    mixes = [_mix(o, n) for n, o in enumerate(root["layers"])]
    seen: set[str] = set()
    for m in mixes:
        if m.id in seen:
            raise FormatError(f"{FILE}: {m.id} is listed twice")
        seen.add(m.id)
    dims = []
    for d in root.get("dimensions", []):
        if not isinstance(d, dict) or type(d.get("dim")) is not int:
            raise FormatError(f"{FILE}: a dimension without an int dim")
        dims.append(Dimension(d["dim"], _str(d, "name"), _str(d, "provider")))
    out = OreMixes(mixes, dims)
    for m in mixes:
        out.problems.extend(f"{m.id}: {e}" for e in m.errors)
        missing = [o.layer for o in m.ores if o.material is None]
        if len(m.ores) < len(LAYERS) or missing:
            out.problems.append(
                f"{m.id}: ores missing ({', '.join(missing) or 'a layer'})"
            )
    out.problems.extend(f"dimension {d.dim}: no name" for d in dims if d.name is None)
    return out
