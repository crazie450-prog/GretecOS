"""The planner request and the My base interface (WO-3.3, plan §7.1 Request, D11-D14, D61-D64).

A :class:`Request` is what the designer is asked for, as frozen, validated values with one
canonical serialisation (:meth:`Request.canonical`: JSON with sorted keys and no spaces), so the
same request always has the same text and digest, and the graph built from it the same ids:

- ``target``: the item or fluid, by its stable key (``registry@meta``, ``#<nbt8>`` for an NBT
  variant, or ``fluid:<name>``; never a numeric id). Text is resolved first
  (:func:`gregtec.planner.graph.resolve_target`, plan §7.1 step 1);
- ``mode`` ``rate`` (``qty`` per ``unit``: ``/s``, ``/min``, ``/h``) or ``batch`` (``qty`` items or
  L in all, optionally ``within_s`` seconds; D14);
- ``tier_cap``: a GT voltage tier index (0 = ULV) that overrides My base's (D64); None takes
  My base's;
- ``machines``: ``base`` or ``base+addons`` (D12);
- ``boundary``: more stable keys to treat as supplied from outside, on top of raw inputs, My base
  stock and the outputs of accepted lines (D63);
- ``constraints``: ``max_eut``, ``exclude`` (``r:`` recipes, ``mte:`` machines, item or fluid
  keys), ``require_owned``, ``cleanroom``, ``coil_max``;
- ``pins``: ``{node, recipe}``, a node being an item's stable key and the recipe its ``r:`` key
  (D61); at most one pin per node;
- ``routes``: how many ranked routes to return, 1 to 3 (D11).

**My base** (:class:`BaseInputs`) is the interface WO-3.3 and WO-3.8 both code to: the player's
tier cap, owned machines (by GT mte id), stock and the outputs of accepted lines (by stable
key). :class:`DefaultBaseInputs` is WO-3.3's: the tier from WO-1.5's quest tier detection,
``tier.override`` included, and nothing owned, stocked or produced (D63, D64); WO-3.8 ships the
real one.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from gregtec.planner.gtmath.tiers import VN

Mode = Literal["rate", "batch"]
Machines = Literal["base", "base+addons"]
MODES: tuple[str, ...] = ("rate", "batch")
MACHINES: tuple[str, ...] = ("base", "base+addons")
UNITS: dict[str, int] = {"/s": 1, "/min": 60, "/h": 3600}
MAX_TIER = 14  # MAX: GT's last real voltage tier (VN[15] is its error tier)
MAX_COIL = 13  # tiers.COILS: Cupronickel 0 .. Eternal 13
MAX_ROUTES = 3
VERSION = 1  # of the canonical form

# Stable keys (the tool layer's, gregtec.agents.tools.items): items registry@meta[#nbt],
# fluids fluid:<name>; requests and pins never name numeric ids.
ITEM_KEY = re.compile(r"[^@#\t\r\n]+:[^@#\t\r\n]+@[0-9]{1,9}(?:#[0-9a-f]{8,64})?")
FLUID_KEY = re.compile(r"fluid:[^\t\r\n]{1,200}")
RECIPE_KEY = re.compile(r"r:[^/\s]+/[0-9a-f]{12}(?:-[0-9]+)?")
MTE_KEY = re.compile(r"mte:[0-9]{1,9}")
_QTY = re.compile(
    r"\s*([0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)\s*(/s|/min|/h)?\s*", re.IGNORECASE
)
_DURATION = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(s|min|h)\s*", re.IGNORECASE)
_SECONDS = {"s": 1, "min": 60, "h": 3600}


class RequestError(ValueError):
    """A request that can't be planned; the message says what is wrong."""


def is_stable_key(key: str) -> bool:
    """An item or fluid key as the DB and the tool layer name them."""
    return bool(ITEM_KEY.fullmatch(key) or FLUID_KEY.fullmatch(key))


def tier_index(value: int | str) -> int:
    """A voltage tier from its index or GT name (``IV``, ``luv``), checked."""
    if isinstance(value, str):
        names = {n.casefold(): i for i, n in enumerate(VN[: MAX_TIER + 1])}
        if value.casefold() not in names:
            raise RequestError(f"unknown voltage tier {value!r}")
        return names[value.casefold()]
    if isinstance(value, bool) or not 0 <= value <= MAX_TIER:
        raise RequestError(f"tier_cap {value!r} is not a voltage tier (0..{MAX_TIER})")
    return value


def parse_qty(text: str) -> tuple[float, str | None]:
    """``"2/s"`` -> (2.0, "/s"); ``"64"`` -> (64.0, None) for a batch."""
    m = _QTY.fullmatch(text)
    if m is None:
        raise RequestError(f"qty {text!r}: a number, then /s, /min or /h for a rate")
    unit = m[2].lower() if m[2] else None
    return float(m[1]), unit


def parse_duration(text: str) -> float:
    """``"2h"``, ``"30min"``, ``"90s"`` -> seconds."""
    m = _DURATION.fullmatch(text)
    if m is None:
        raise RequestError(f"within {text!r}: a number then s, min or h")
    return float(m[1]) * _SECONDS[m[2].lower()]


def _positive(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RequestError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise RequestError(f"{name} must be a finite number above 0, not {value!r}")
    return value


def _keys(name: str, values: Iterable[str]) -> tuple[str, ...]:
    out = tuple(sorted(set(values)))
    for v in out:
        if not is_stable_key(v):
            raise RequestError(f"{name}: {v!r} is not an item or fluid key")
    return out


@dataclass(frozen=True)
class Pin:
    """A fixed recipe choice for one node (D61): ``node`` is the item's stable key."""

    node: str
    recipe: str

    def __post_init__(self) -> None:
        if not is_stable_key(self.node):
            raise RequestError(f"pin node {self.node!r} is not an item or fluid key")
        if not RECIPE_KEY.fullmatch(self.recipe):
            raise RequestError(f"pin recipe {self.recipe!r} is not an r: key")


@dataclass(frozen=True)
class Constraints:
    max_eut: int | None = None  # a recipe's own EU/t above this is left out
    exclude: tuple[str, ...] = ()  # r: recipes, mte: machines, item or fluid keys
    require_owned: bool = False
    cleanroom: bool = True  # D177 (5): a cleanroom can be built
    coil_max: int | None = None  # tiers.COILS index

    def __post_init__(self) -> None:
        if self.max_eut is not None and (
            isinstance(self.max_eut, bool)
            or not isinstance(self.max_eut, int)
            or self.max_eut < 1
        ):
            raise RequestError(f"max_eut {self.max_eut!r} must be a whole EU/t above 0")
        if self.coil_max is not None and (
            isinstance(self.coil_max, bool) or not 0 <= self.coil_max <= MAX_COIL
        ):
            raise RequestError(f"coil_max {self.coil_max!r} is not a coil tier (0..13)")
        excluded = tuple(sorted(set(self.exclude)))
        for x in excluded:
            if not (
                is_stable_key(x) or RECIPE_KEY.fullmatch(x) or MTE_KEY.fullmatch(x)
            ):
                raise RequestError(
                    f"exclude {x!r}: an r: recipe, an mte: machine or an item or fluid key"
                )
        object.__setattr__(self, "exclude", excluded)

    def excluded_mtes(self) -> frozenset[int]:
        return frozenset(int(x[4:]) for x in self.exclude if MTE_KEY.fullmatch(x))

    def excluded_recipes(self) -> frozenset[str]:
        return frozenset(x for x in self.exclude if x.startswith("r:"))

    def excluded_keys(self) -> frozenset[str]:
        return frozenset(x for x in self.exclude if is_stable_key(x))


@dataclass(frozen=True)
class Request:
    """A planner request (plan §7.1); see the module docstring."""

    target: str
    mode: Mode = "rate"
    qty: float = 1.0
    unit: str | None = "/s"  # rate only
    within_s: float | None = None  # batch only
    tier_cap: int | None = None  # None: My base's (D64)
    machines: Machines = "base"
    boundary: tuple[str, ...] = ()
    constraints: Constraints = field(default_factory=Constraints)
    pins: tuple[Pin, ...] = ()
    routes: int = MAX_ROUTES

    def __post_init__(self) -> None:
        if not is_stable_key(self.target):
            raise RequestError(
                f"target {self.target!r} is not an item or fluid key: resolve it first"
            )
        if self.mode not in MODES:
            raise RequestError(f"mode {self.mode!r}: rate or batch")
        _positive("qty", self.qty)
        object.__setattr__(self, "qty", float(self.qty))
        if self.mode == "rate":
            if self.unit not in UNITS:
                raise RequestError(
                    f"a rate needs a unit /s, /min or /h, not {self.unit!r}"
                )
            if self.within_s is not None:
                raise RequestError("within is for batch mode only")
        else:
            if self.unit is not None:
                raise RequestError("a batch quantity has no /unit")
            if self.within_s is not None:
                object.__setattr__(self, "within_s", _positive("within", self.within_s))
        if self.tier_cap is not None:
            tier_index(self.tier_cap)
        if self.machines not in MACHINES:
            raise RequestError(f"machines {self.machines!r}: base or base+addons")
        object.__setattr__(self, "boundary", _keys("boundary", self.boundary))
        pins = tuple(sorted(self.pins, key=lambda p: (p.node, p.recipe)))
        nodes = [p.node for p in pins]
        if len(set(nodes)) != len(nodes):
            raise RequestError("at most one pin per node")
        object.__setattr__(self, "pins", pins)
        if (
            isinstance(self.routes, bool)
            or not isinstance(self.routes, int)
            or not 1 <= self.routes <= MAX_ROUTES
        ):
            raise RequestError(f"routes {self.routes!r}: 1 to {MAX_ROUTES}")

    @property
    def rate_per_s(self) -> float | None:
        """The target rate per second (rate mode)."""
        return self.qty / UNITS[self.unit] if self.unit is not None else None

    @property
    def addons(self) -> bool:
        return self.machines == "base+addons"

    def pin_for(self, node: str) -> str | None:
        return next((p.recipe for p in self.pins if p.node == node), None)

    def as_dict(self) -> dict[str, Any]:
        c = self.constraints
        return {
            "v": VERSION,
            "target": self.target,
            "mode": self.mode,
            "qty": self.qty,
            "unit": self.unit,
            "within_s": self.within_s,
            "tier_cap": self.tier_cap,
            "machines": self.machines,
            "boundary": list(self.boundary),
            "constraints": {
                "max_eut": c.max_eut,
                "exclude": list(c.exclude),
                "require_owned": c.require_owned,
                "cleanroom": c.cleanroom,
                "coil_max": c.coil_max,
            },
            "pins": [{"node": p.node, "recipe": p.recipe} for p in self.pins],
            "routes": self.routes,
        }

    def canonical(self) -> str:
        """The request as canonical JSON: sorted keys, no spaces, UTF-8 text."""
        return json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Request:
        """The inverse of :meth:`as_dict`; unknown keys are refused."""
        known = {
            "v",
            "target",
            "mode",
            "qty",
            "unit",
            "within_s",
            "tier_cap",
            "machines",
            "boundary",
            "constraints",
            "pins",
            "routes",
        }
        extra = sorted(set(data) - known)
        if extra:
            raise RequestError(f"unknown request fields: {', '.join(extra)}")
        if data.get("v", VERSION) != VERSION:
            raise RequestError(
                f"request version {data.get('v')!r}; this knows {VERSION}"
            )
        cons = dict(data.get("constraints") or {})
        extra = sorted(
            set(cons) - {"max_eut", "exclude", "require_owned", "cleanroom", "coil_max"}
        )
        if extra:
            raise RequestError(f"unknown constraints: {', '.join(extra)}")
        mode = data.get("mode", "rate")
        try:
            return cls(
                target=data["target"],
                mode=mode,
                qty=data.get("qty", 1.0),
                unit=data.get("unit", "/s" if mode == "rate" else None),
                within_s=data.get("within_s"),
                tier_cap=data.get("tier_cap"),
                machines=data.get("machines", "base"),
                boundary=tuple(data.get("boundary") or ()),
                constraints=Constraints(
                    max_eut=cons.get("max_eut"),
                    exclude=tuple(cons.get("exclude") or ()),
                    require_owned=bool(cons.get("require_owned", False)),
                    cleanroom=bool(cons.get("cleanroom", True)),
                    coil_max=cons.get("coil_max"),
                ),
                pins=tuple(
                    Pin(str(p["node"]), str(p["recipe"]))
                    for p in data.get("pins") or ()
                ),
                routes=data.get("routes", MAX_ROUTES),
            )
        except (KeyError, TypeError) as exc:
            raise RequestError(f"malformed request: {exc}") from None

    @classmethod
    def from_json(cls, text: str) -> Request:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise RequestError(f"request JSON: {exc}") from None
        if not isinstance(data, dict):
            raise RequestError("a request is a JSON object")
        return cls.from_dict(data)


# --- My base (D63, D64): the interface WO-3.3 and WO-3.8 share ------------------------------


@runtime_checkable
class BaseInputs(Protocol):
    """What the designer reads of My base. Keys are stable item/fluid keys (registry_name +
    meta + NBT hash, or the fluid name), never numeric block/item ids or the DB's surrogate
    ids, which change on every ``gos db build``."""

    def tier_cap(self) -> int:
        """The GT voltage tier index (0 = ULV), after any override."""
        ...

    def owned_machines(self) -> Mapping[int, int]:
        """GT mte id -> how many he has."""
        ...

    def stock(self) -> Mapping[str, float]:
        """Stable key -> quantity on hand."""
        ...

    def line_outputs(self) -> Mapping[str, float]:
        """Stable key -> rate per second from accepted lines."""
        ...


@dataclass(frozen=True)
class DefaultBaseInputs:
    """WO-3.3's My base: the quest-detected tier (WO-1.5, ``tier.override`` first), and
    nothing owned, stocked or produced by lines (D63)."""

    tier: int

    def __post_init__(self) -> None:
        tier_index(self.tier)

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> DefaultBaseInputs:
        from gregtec.db import quests as quest_db
        from gregtec.ingest import quests as bq

        stage, _, _ = quest_db.effective_tier(conn)
        return cls(bq.STAGES[stage])

    def tier_cap(self) -> int:
        return self.tier

    def owned_machines(self) -> Mapping[int, int]:
        return {}

    def stock(self) -> Mapping[str, float]:
        return {}

    def line_outputs(self) -> Mapping[str, float]:
        return {}
