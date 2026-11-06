"""Machine facts (WO-1.7, D109-D111): parallels, speed, EU use and overclock rules, each with a
confidence, for the ``machine_fact`` table.

GT keeps these numbers in each machine's Java code, not in a registry [A§4.1], so they come from
two places, and ``gos db build`` writes both:

- **tooltip**: :func:`parse_tooltip` reads a multiblock's tooltip lines from the machine pass.
  Lines that GT's ``MultiblockTooltipBuilder`` writes from its own templates (``8 Parallels per
  Voltage Tier``, ``200% Speed``, ``80% EU Usage``, ``Machine does not lose efficiency when
  overclocked``, ...) get 0.85; free-text lines GT uses for the same facts 0.7 (0.8 for "Performs
  4/4 Perfect Overclocks"); two lines that disagree 0.6. What a tooltip doesn't say gets GT's
  ProcessingLogic default (parallel 1, speed 1, EU 1, standard overclocks) at 0.5, or at 0.3 when
  a line talks about it in a form no rule reads. Tooltips count coil tiers from 1 (Cupronickel),
  ``HeatingCoilLevel.getTier()`` from 0, and some machines' code counts them from 0 too, so a
  fact over coil tiers gets 0.5 with the other reading in its note. The parser reads English
  (en_US) tooltips; another language matches nothing and leaves only the defaults.
- **curated**: ``data/curated/machine_facts.tsv`` (:func:`read_curated`), written by hand from
  GT's source at the pinned tag (5.09.54.265) or from the tooltips, each row citing where its
  numbers come from. ShadowTheAge's ``machines.ts`` (MIT, Copyright (c) 2025 ShadowTheAge) was a
  reference for which machines have special rules; no number was taken from it, because it
  follows an older GT.

For one (machine, key) the planner takes the best row: ``probe`` (a Lab measurement, later WOs)
over ``curated`` over ``code`` over ``tooltip``, then the highest confidence (the named query
``machine_facts``). A key may carry a recipe map, ``parallel[gt.recipe.recycler]``, for a
machine whose numbers differ per mode; the planner prefers it to the plain key on that map.

Values are short strings: an enum (``oc``: ``standard``, ``perfect``, ...) or a formula in
:data:`VARIABLES` (``8*vt``, ``4 + 2*coil_tier``), checked by :func:`check_formula`; ``n/a`` for
machines that run no recipes. Every variable is named and says where its value comes from
(:data:`KINDS`): how the machine is built, its state while running (``ramp``, ``overheated``),
or the recipe it runs (``recipe_fusion_tier``). See :data:`KEYS` for the vocabulary.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from gregtec.ingest.machines import Machine

REPO = Path(__file__).resolve().parents[3]
CURATED = REPO / "data" / "curated" / "machine_facts.tsv"
HEADER = (
    "machine",
    "name",
    "processing",
    "parallel",
    "speed",
    "eu_modifier",
    "oc",
    "extra",
    "confidence",
    "cite",
    "note",
)
NA = "n/a"
# WO-1.7's acceptance: parallels and OC rules at this confidence, or flagged.
THRESHOLD = 0.8
SOURCE_RANK = {"probe": 3, "curated": 2, "code": 1, "tooltip": 0}


@dataclass(frozen=True)
class Variable:
    """A formula variable: where its value comes from, and what it is in GT's code."""

    kind: str  # one of KINDS
    doc: str


# build: how the machine is built and equipped (hatches, coils, casings, parts in the controller
# or a special hatch), chosen by the player; state: changes while the machine runs, so the
# planner assumes a value; recipe: a property of the recipe it runs (EU/t or recipe metadata).
KINDS = ("build", "state", "recipe")


# Formula variables: what each stands for in GT's code (names are GT's accessors).
VARIABLES: dict[str, Variable] = {
    "vt": Variable(
        "build",
        "GTUtility.getTier(getMaxInputVoltage()): voltage tier of the summed input voltage of "
        "the energy hatches (exotic hatches included on extended-power multis)",
    ),
    "vt_avg": Variable(
        "build",
        "GTUtility.getTier(getAverageInputVoltage()): voltage tier of the average hatch",
    ),
    "vt_eu": Variable(
        "build",
        "GTUtility.getTierExtended(getMaxInputEu()): voltage tier of the total input EU/t, "
        "amperage included (mega machines)",
    ),
    "coil_tier": Variable(
        "build", "HeatingCoilLevel.getTier(): Cupronickel 0, Kanthal 1, ... Eternal 13"
    ),
    "coil_heat": Variable(
        "build", "HeatingCoilLevel.getHeat(): 1 + 900 * ordinal (Cupronickel 1801 K)"
    ),
    "pipe_tier": Variable(
        "build",
        "the machine's pipe casing tier, as its own code counts it (see the row's note)",
    ),
    "item_pipe_tier": Variable(
        "build",
        "item pipe casing tier, 1 (Tin) to 8 (GTStructureUtility.chainItemPipeCasings)",
    ),
    "solenoid_tier": Variable(
        "build",
        "voltage tier of the solenoid superconductor coils (GTStructureUtility.ofSolenoidCoil)",
    ),
    "glass_tier": Variable(
        "build", "the machine's glass tier, as its own code counts it"
    ),
    "casing_tier": Variable(
        "build",
        "the machine's tiered casing, as its own code counts it (see the row's note)",
    ),
    "machine_tier": Variable(
        "build", "the machine's own structure or controller tier (see the row's note)"
    ),
    "width": Variable(
        "build", "the machine's structure width or length setting (see the row's note)"
    ),
    "height": Variable("build", "the machine's structure height (see the row's note)"),
    "turbine_tier_sum": Variable(
        "build", "the sum of the tiers of the turbines in the structure"
    ),
    "frc_tier": Variable(
        "build", "Naquadah Fuel Refinery field restriction coil tier (1-4)"
    ),
    "cal_casing_tier": Variable("build", "component assembly line casing tier"),
    "laser_amps": Variable(
        "build", "amperage of the laser source hatch (see the row's note)"
    ),
    "laser_tier": Variable(
        "build", "voltage tier of the laser source hatch (laserSource.mTier)"
    ),
    "mode": Variable(
        "build",
        "the machine mode set with a screwdriver or the GUI (see the row's note)",
    ),
    "sawblade_tier": Variable(
        "build",
        "Industrial Cutting Factory sawblade in the controller (SawbladeTiers): 1 "
        "Tungsten-Titanium Carbide, 2 Mysterious Crystal, 3 Neutronium, 4 Transcendent Metal",
    ),
    "magnet_tier": Variable(
        "build",
        "Magnetic Flux Exhibitor electromagnet in its magnet hatch (MagnetTiers): 1 Iron, "
        "2 Steel, 3 Neodymium, 4 Samarium, 5 Tengam",
    ),
    "singularity": Variable(
        "build",
        "1 with L.A.T.E.X.'s rubber singularity in the controller (hasSingularity), else 0",
    ),
    "nanites": Variable(
        "build", "PCB Factory: nanites in the nanite bus (numberOfNanites)"
    ),
    "roughness": Variable(
        "build",
        "PCB Factory: trace size setting, mRoughnessMultiplier (duration x its square)",
    ),
    "structures": Variable(
        "build",
        "PCB Factory: 1 + a bio chamber + a cooling tower (createOverclockCalculator)",
    ),
    "ramp": Variable(
        "state",
        "0 when the machine starts, 1 once it has run long enough to reach its maximum "
        "momentum, speed-up or heat-up (see the row's note); it falls back while idle",
    ),
    "overheated": Variable(
        "state", "1 while the Hot Isostatic Pressurization Unit is overheated, else 0"
    ),
    "wind_level": Variable(
        "state",
        "Windmill wind level (WindLevel ordinal): 1 below 10 wind strength, 2 below 20, 3 below "
        "30, 4 below 50; it stops below 1 and at 50 or more",
    ),
    "recipe_tier": Variable(
        "recipe", "GTUtility.getTier(recipe EU/t): the recipe's voltage tier"
    ),
    "recipe_fusion_tier": Variable(
        "recipe",
        "FusionSpecialValueFormatter.getFusionTier(startup EU, EU/t): the fusion Mk (1-5) the "
        "recipe needs",
    ),
    "recipe_startup_eu": Variable(
        "recipe", "the recipe's fusion startup EU (metadata FUSION_THRESHOLD)"
    ),
    "recipe_casing_tier": Variable(
        "recipe",
        "the casing tier the recipe asks for (Component Assembly Line: mSpecialValue; "
        "Draconic Evolution Fusion Crafter: metadata DEFC_CASING_TIER, default 1)",
    ),
    "recipe_frc_tier": Variable(
        "recipe",
        "the field restriction coil tier a Naquadah Fuel Refinery recipe asks for "
        "(mSpecialValue)",
    ),
}
FUNCTIONS: dict[str, Callable[..., float]] = {
    "min": min,
    "max": max,
    "floor": math.floor,
    "ceil": math.ceil,
    "sqrt": math.sqrt,
}


@dataclass(frozen=True)
class KeySpec:
    unit: str | None
    values: frozenset[str] = frozenset()  # an enum's values; empty for a formula
    formula: bool = False
    doc: str = ""


def _enum(*values: str, doc: str = "") -> KeySpec:
    return KeySpec(None, frozenset(values), False, doc)


KEYS: dict[str, KeySpec] = {
    "processing": _enum(
        "logic",
        "custom",
        "generator",
        "none",
        doc="logic: GT's ProcessingLogic runs its recipe maps; custom: its own recipe check; "
        "generator: burns fuel for EU or steam; none: runs no recipes (drills, storage, ...)",
    ),
    "parallel": KeySpec("recipes", formula=True, doc="maximum parallel recipes"),
    "speed": KeySpec(
        "x", formula=True, doc="duration is divided by this (2 = 200% speed)"
    ),
    "eu_modifier": KeySpec("x", formula=True, doc="recipe EU/t is multiplied by this"),
    "oc": _enum(
        "standard",
        "perfect",
        "heat",
        "heat_no_discount",
        "limited_perfect",
        "none",
        "custom",
        doc="standard: EU x4, time /2 per overclock; perfect: EU x4, time /4; heat: EBF rules "
        "(perfect per 1800 K over the recipe, EU x0.95 per 900 K); heat_no_discount: the perfect "
        "overclocks only; limited_perfect: the first perfect_overclocks are perfect, the rest "
        "standard; none: never overclocks; custom: oc_eu_mul and oc_time_div, or see the note",
    ),
    "perfect_overclocks": KeySpec(
        None, formula=True, doc="how many overclocks are perfect"
    ),
    "oc_eu_mul": KeySpec(
        "x", formula=True, doc="EU/t multiplier per overclock (custom)"
    ),
    "oc_time_div": KeySpec(
        "x", formula=True, doc="duration divisor per overclock (custom)"
    ),
    "max_overclocks": KeySpec(None, formula=True, doc="cap on overclocks"),
    "tier_skips": KeySpec(
        None,
        formula=True,
        doc="recipe tiers above the hatch tier it may run (GT default 1)",
    ),
    "power": _enum(
        "average",
        "total_eu",
        "fixed",
        "custom",
        doc="average: GT's default (average hatch voltage, summed amps, 1 A with one plain "
        "hatch); total_eu: all input EU as one amp; fixed: a set voltage (see the note)",
    ),
    "machine_heat": KeySpec("K", formula=True, doc="heat compared with the recipe's"),
    "exotic_hatches": _enum(
        "multiamp", "laser", "any", doc="exotic energy hatches it takes"
    ),
    "deprecated": _enum("true", doc="a legacy controller GT will remove"),
    "flag": _enum("review", doc="needs a curator: the note says why"),
}
# Keys a curated row may give per recipe map, ``key[gt.recipe.x]``.
PER_MAP = frozenset(
    {"parallel", "speed", "eu_modifier", "oc", "max_overclocks", "perfect_overclocks"}
)
_KEY = re.compile(r"([a-z_]+)(?:\[([A-Za-z0-9_.:\-]+)\])?")


@dataclass(frozen=True)
class Fact:
    key: str  # may carry a recipe map: parallel[gt.recipe.x]
    value: str
    source: str  # tooltip | curated | code | probe
    confidence: float
    note: str

    @property
    def base_key(self) -> str:
        return self.key.split("[", 1)[0]

    @property
    def unit(self) -> str | None:
        return KEYS[self.base_key].unit


# --- formulas ------------------------------------------------------------------------------

_ALLOWED_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow, ast.Mod)
_COMPARE: dict[type[ast.cmpop], Callable[[float, float], bool]] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}


def _walk(node: ast.AST, env: Mapping[str, float] | None) -> float:
    if isinstance(node, ast.Expression):
        return _walk(node.body, env)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in VARIABLES:
            raise ValueError(f"unknown variable {node.id!r}")
        return 1.0 if env is None else float(env[node.id])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _walk(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_OPS):
        a, b = _walk(node.left, env), _walk(node.right, env)
        if env is None:
            return 1.0
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        if isinstance(node.op, ast.FloorDiv):
            return float(a // b)
        if isinstance(node.op, ast.Mod):
            return a % b
        return float(a**b)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in FUNCTIONS
        and not node.keywords
        and node.args
    ):
        args = [_walk(a, env) for a in node.args]
        return 1.0 if env is None else float(FUNCTIONS[node.func.id](*args))
    # "a if machine_tier == 2 else b": modes and upgrades.
    if isinstance(node, ast.IfExp):
        test = _walk(node.test, env)
        yes, no = _walk(node.body, env), _walk(node.orelse, env)
        return 1.0 if env is None else (yes if test else no)
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and type(node.ops[0]) in _COMPARE
    ):
        a, b = _walk(node.left, env), _walk(node.comparators[0], env)
        return 1.0 if env is None else float(_COMPARE[type(node.ops[0])](a, b))
    raise ValueError(f"not allowed in a formula: {ast.dump(node)[:60]}")


def _parse(text: str) -> ast.Expression:
    try:
        return ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        raise ValueError(f"not a formula: {text!r}") from None


def check_formula(text: str) -> None:
    """Raise ValueError unless ``text`` is arithmetic over :data:`VARIABLES` and numbers."""
    _walk(_parse(text), None)


def variables(text: str) -> set[str]:
    return {n.id for n in ast.walk(_parse(text)) if isinstance(n, ast.Name)} - set(
        FUNCTIONS
    )


def evaluate(text: str, env: Mapping[str, float]) -> float:
    """The value of a checked formula with ``env`` giving every variable it uses."""
    missing = variables(text) - set(env)
    if missing:
        raise ValueError(f"no value for {', '.join(sorted(missing))}")
    return _walk(_parse(text), env)


def check_value(key: str, value: str) -> None:
    """Raise ValueError unless ``value`` is valid for ``key`` (which may name a map)."""
    m = _KEY.fullmatch(key)
    if m is None or m.group(1) not in KEYS:
        raise ValueError(f"unknown key {key!r}")
    if m.group(2) is not None and m.group(1) not in PER_MAP:
        raise ValueError(f"{m.group(1)} can't be given per recipe map")
    spec = KEYS[m.group(1)]
    if value == NA and m.group(1) in ("parallel", "speed", "eu_modifier", "oc"):
        return
    if spec.formula:
        if m.group(1) == "tier_skips" and value == "unlimited":
            return
        check_formula(value)
    elif value not in spec.values:
        raise ValueError(
            f"{key} is one of {', '.join(sorted(spec.values))}, not {value!r}"
        )


def _sample(text: str, env: Mapping[str, float]) -> float | None:
    try:
        return evaluate(text, env)
    except (ZeroDivisionError, OverflowError, ValueError):  # ValueError: sqrt(-1)
        return None


def same_value(key: str, a: str, b: str) -> bool:
    """Whether two values of ``key`` say the same, formulas compared at sample points.

    The points are every variable at 0 to 5 (so a ``casing_tier == 0`` branch is compared),
    then every variable apart (1, 2, 3, ... in name order, shifted). A point where neither
    formula has a value (a division by zero) is skipped; one where only one has is a difference.
    """
    if a == b:
        return True
    spec = KEYS.get(key.split("[", 1)[0])
    if spec is None or not spec.formula or NA in (a, b) or "unlimited" in (a, b):
        return False
    try:
        names = sorted(variables(a) | variables(b))
    except ValueError:
        return False
    points = [dict.fromkeys(names, float(p)) for p in range(6)]
    points += [{n: float(p + i) for i, n in enumerate(names)} for p in range(1, 5)]
    compared = 0
    for env in points:
        va, vb = _sample(a, env), _sample(b, env)
        if va is None and vb is None:
            continue
        if va is None or vb is None or not math.isclose(va, vb, rel_tol=1e-9):
            return False
        compared += 1
    return compared > 0


# --- tooltips ------------------------------------------------------------------------------

_CODES = re.compile("§.")
_NUM = r"\d+(?:[.,]\d+)*"
# Tier names of GT's TooltipTier (GT5U.MBTT.Tiers.*, en_US) -> formula variables.
# Tooltips count coils from 1 (Cupronickel), HeatingCoilLevel.getTier() from 0.
COIL_FROM_1 = "(coil_tier + 1)"
TIER_VARIABLES = {
    "voltage": "vt",
    "heating coil": COIL_FROM_1,
    "coil": COIL_FROM_1,
    "pipe casing": "pipe_tier",
    "fluid pipe casing": "pipe_tier",
    "item pipe casing": "item_pipe_tier",
    "solenoid": "solenoid_tier",
    "glass": "glass_tier",
    "machine casing": "casing_tier",
    "metal machine casing": "casing_tier",
    "machine": "machine_tier",
    "sum of turbine": "turbine_tier_sum",
    "total turbine": "turbine_tier_sum",
    "field restriction coil": "frc_tier",
    "component assembly line casing": "cal_casing_tier",
}
STANDARD = 0.85  # a line in one of GT's MultiblockTooltipBuilder templates
FREE = 0.7  # free text that says the same
DEFAULT = 0.5  # nothing in the tooltip: GT's ProcessingLogic default
# A per-coil-tier line, or a speed bonus with no base line: the tooltip's number is read right,
# but the machine's code may count the tier from 0, and the bonus may start from 0%, not 100%.
# Three of the six such lines in the dev runtime matched GT's code.
COIL = 0.5


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


UNREAD = 0.3  # the default, but the tooltip talks about the key in a form the parser can't read
SPEED_INC = "speed+"  # internal: a per-tier speed bonus, added to the base speed line
DEFAULTS = {"parallel": "1", "speed": "1", "eu_modifier": "1", "oc": "standard"}
# A line that talks about a key the parser didn't read: the default is then less sure.
MENTIONS = {
    "parallel": _rx(r"parallel"),
    "speed": _rx(r"speed|faster|slower|duration"),
    "eu_modifier": _rx(r"\bEU\b(?!/t\b)|EU usage|EU cost|discount"),
    "oc": _rx(r"overclock|\bOC\b"),
}


def clean(line: str) -> str:
    """A tooltip line without colour codes, spaces collapsed."""
    return re.sub(r"\s+", " ", _CODES.sub("", line)).strip()


def number(text: str) -> float:
    """``1,800`` and ``1.800`` are 1800 (a group of three digits); ``8,33`` is 8.33."""
    parts = re.split(r"[.,]", text)
    if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
        return float("".join(parts))
    if len(parts) == 2:
        return float(f"{parts[0]}.{parts[1]}")
    if len(parts) == 1:
        return float(parts[0])
    raise ValueError(f"not a number: {text!r}")


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else repr(round(v, 6))


def _tier(name: str) -> str | None:
    return TIER_VARIABLES.get(name.strip().lower())


_Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], list[tuple[str, str, float]]]]


def _per_tier(base: float, per: float, tier: str) -> str | None:
    var = _tier(tier)
    if var is None:
        return None
    inc = f"{_fmt(per)}*{var}"
    return inc if base == 0 else f"{_fmt(base)} + {inc}"


def _r_parallel(m: re.Match[str]) -> list[tuple[str, str, float]]:
    return [("parallel", _fmt(number(m.group(1))), STANDARD)]


def _r_parallel_tier(m: re.Match[str]) -> list[tuple[str, str, float]]:
    value = _per_tier(0, number(m.group(1)), m.group(2))
    if value is None:
        return []
    return [("parallel", value, STANDARD if value.endswith("*vt") else FREE)]


def _r_speed(m: re.Match[str]) -> list[tuple[str, str, float]]:
    return [("speed", _fmt(number(m.group(1)) / 100), STANDARD)]


def _r_speed_tier(m: re.Match[str]) -> list[tuple[str, str, float]]:
    if m.group(1) is None:
        # "+50% Speed per X Tier" adds to the machine's base speed line ("100% Speed").
        value = _per_tier(0, number(m.group(2)) / 100, m.group(3))
        return [] if value is None else [(SPEED_INC, value, FREE)]
    value = _per_tier(number(m.group(1)) / 100, number(m.group(2)) / 100, m.group(3))
    return [] if value is None else [("speed", value, FREE)]


def _r_eu(m: re.Match[str]) -> list[tuple[str, str, float]]:
    return [("eu_modifier", _fmt(number(m.group(1)) / 100), STANDARD)]


RULES: list[_Rule] = [
    # Parallels: GT5U.MBTT.Parallel.Base / .Singular / .Additional, and older wordings.
    (_rx(rf"({_NUM}) Parallels?"), _r_parallel),
    (_rx(rf"Has ({_NUM}) parallels?"), _r_parallel),
    (_rx(rf"({_NUM}) Parallels? per (.+?) Tier"), _r_parallel_tier),
    (_rx(rf"Processes ({_NUM}) items? per (.+?) Tier"), _r_parallel_tier),
    # Speed: GT5U.MBTT.Speed.Base / .Additional, and "X% Speed +Y% per Z Tier".
    (_rx(rf"({_NUM})% Speed"), _r_speed),
    (
        _rx(rf"(?:({_NUM})% Speed )?\+({_NUM})% (?:Speed )?per (.+?) Tier"),
        _r_speed_tier,
    ),
    (
        _rx(rf"({_NUM})% Speed of single ?block machines of the same voltage"),
        lambda m: [("speed", _fmt(number(m.group(1)) / 100), FREE)],
    ),
    # EU: GT5U.MBTT.EuDiscount.Base and the older "Uses X% of the EU normally required".
    (_rx(rf"({_NUM})% EU Usage"), _r_eu),
    (
        _rx(rf"Uses ({_NUM})% of the EU(?:/t)? normally required"),
        lambda m: [("eu_modifier", _fmt(number(m.group(1)) / 100), FREE)],
    ),
    # Overclocks.
    (
        _rx(r"Machine does not lose efficiency when overclocked"),
        lambda m: [("oc", "perfect", STANDARD)],
    ),
    (
        _rx(r"Performs 4/4 Perfect Overclocks?"),
        lambda m: [("oc", "perfect", 0.8)],
    ),
    (
        _rx(
            rf"Every ({_NUM})K (?:above|over) the recipe requirement grants 1 Perfect Overclock"
        ),
        lambda m: [("oc", "heat", FREE)] if number(m.group(1)) == 1800 else [],
    ),
    (
        _rx(r"(?:This (?:multiblock|machine) does not overclock|Can't overclock)\.?"),
        lambda m: [("oc", "none", FREE)],
    ),
    # Tier skips: GT5U.MBTT.Structure.NoTierSkips / MaxTierSkips / UnlimitedTierSkips.
    (
        _rx(
            r"This machine can only run recipes matching its average energy hatch tier"
        ),
        lambda m: [("tier_skips", "0", STANDARD)],
    ),
    (
        _rx(r"This machine can only run recipes up to energy hatch tier \+ (\d+)"),
        lambda m: [("tier_skips", m.group(1), STANDARD)],
    ),
    (
        _rx(r"Runs any recipe regardless of tier, if given enough energy"),
        lambda m: [("tier_skips", "unlimited", STANDARD)],
    ),
    # Exotic hatches: GT5U.MBTT.SupportAny / SupportMultiAmp / SupportLaser.
    (
        _rx(r"Supports Multi-Amp and Laser Hatches!?"),
        lambda m: [("exotic_hatches", "any", STANDARD)],
    ),
    (
        _rx(r"Supports Multi-Amp (?:Energy )?Hatches!?"),
        lambda m: [("exotic_hatches", "multiamp", STANDARD)],
    ),
    (
        _rx(r"Supports Laser Hatches!?"),
        lambda m: [("exotic_hatches", "laser", STANDARD)],
    ),
    # Heat (EBF family): "Heat increases by +100K for every Voltage Tier past MV".
    (
        _rx(
            rf"(?:Heat increases by \+?({_NUM})K|Increases Heat by ({_NUM})K) for every Voltage "
            r"Tier past MV"
        ),
        lambda m: [
            (
                "machine_heat",
                f"coil_heat + {_fmt(number(m.group(1) or m.group(2)))}*(vt - 2)",
                FREE,
            )
        ],
    ),
    (
        _rx(r"DEPRECATED - .*"),
        lambda m: [("deprecated", "true", 0.9)],
    ),
]


def parse_tooltip(lines: Sequence[str], recipe_maps: bool = True) -> list[Fact]:
    """Facts a multiblock's tooltip states, plus GT's defaults (0.5) for what it doesn't say.

    A rule must match a whole line. Where two lines give one key different values, the first
    wins at 0.6 with the other in its note, so a tooltip that contradicts itself shows. A
    per-tier speed bonus line adds to the base speed line (to 100% when there is none). A fact
    over coil tiers, or a bonus with no base line, is at most :data:`COIL` (0.5), with the
    other reading in its note. Defaults are added only when the machine has recipe maps, at 0.3
    when a line talks about the key in a form no rule reads.
    """
    found: dict[str, Fact] = {}
    unread: dict[str, str] = {}
    for raw in lines:
        line = clean(raw)
        for pattern, rule in RULES:
            m = pattern.fullmatch(line)
            if m is None:
                continue
            read = rule(m)
            if not read:  # a tier the parser has no variable for
                for key, mention in MENTIONS.items():
                    if mention.search(line):
                        unread.setdefault(key, line)
            for key, value, confidence in read:
                if key not in found:
                    found[key] = Fact(key, value, "tooltip", confidence, line)
                elif found[key].value != value:
                    old = found[key]
                    found[key] = Fact(
                        key, old.value, "tooltip", min(old.confidence, 0.6),
                        f"{old.note} | also: {line}",
                    )  # fmt: skip
            break
        else:
            for key, mention in MENTIONS.items():
                if mention.search(line):
                    unread.setdefault(key, line)
    inc = found.pop(SPEED_INC, None)
    if inc is not None:
        base = found.get("speed")
        if base is None:
            value, confidence = f"1 + {inc.value}", COIL
            note = f"{inc.note} (no base speed line: read as 100% + the bonus; or {inc.value})"
        else:
            value, confidence = f"{base.value} + {inc.value}", FREE
            note = f"{base.note} | {inc.note}"
        found["speed"] = Fact("speed", value, "tooltip", confidence, note)
    for key, f in list(found.items()):
        if COIL_FROM_1 in f.value:
            alt = f.value.replace(COIL_FROM_1, "coil_tier")
            note = f"{f.note} (coil tiers counted from 1; or, from 0: {alt})"
            found[key] = Fact(key, f.value, f.source, min(f.confidence, COIL), note)
    if recipe_maps:
        for key, value in DEFAULTS.items():
            if key in found:
                continue
            if key in unread:
                why = f"the tooltip says, in a form the parser doesn't read: {unread[key]}"
                found[key] = Fact(key, value, "tooltip", UNREAD, f"GT's default; {why}")
            else:
                why = "the tooltip doesn't say; GT's ProcessingLogic default"
                found[key] = Fact(key, value, "tooltip", DEFAULT, why)
    return [found[k] for k in sorted(found)]


# --- curated -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Curated:
    machine: str  # the machine pass's class name
    # its mte_id (gt.blockmachines meta), only when a class has several machines
    meta: int | None
    name: str
    facts: tuple[Fact, ...]
    line: int


def _split_extra(text: str) -> list[tuple[str, str]]:
    out = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        key, eq, value = part.partition("=")
        if not eq:
            raise ValueError(f"extra {part!r} is not key=value")
        out.append((key.strip(), value.strip()))
    return out


def read_curated(path: Path = CURATED) -> list[Curated]:
    """Read and check the curated table; ValueError names the line of the first problem.

    One machine may have several rows (each with its own confidence and citation), but a key
    appears once per machine: a ``class`` row covers every machine of the class, so it and a
    ``class@meta`` row can't give the same key either. A machine whose ``processing`` is
    ``generator`` or ``none`` gives ``n/a`` (or nothing) for parallel, speed, EU and OC.
    """
    rows: list[Curated] = []
    seen: dict[tuple[str, int | None], set[str]] = {}
    header_seen = False
    for number_, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        cells = raw.split("\t")
        if not header_seen:
            if tuple(cells) != HEADER:
                want = " | ".join(HEADER)
                raise ValueError(f"{path.name}:{number_}: header is not {want} (tabs)")
            header_seen = True
            continue
        try:
            rows.append(_curated_row(cells, number_, seen))
        except ValueError as exc:
            raise ValueError(f"{path.name}:{number_}: {exc}") from None
    if not header_seen:
        raise ValueError(f"{path.name}: no header")
    return rows


def _curated_row(
    cells: list[str], line: int, seen: dict[tuple[str, int | None], set[str]]
) -> Curated:
    if len(cells) != len(HEADER):
        raise ValueError(f"{len(cells)} columns, not {len(HEADER)}")
    row = dict(zip(HEADER, (c.strip() for c in cells)))
    machine, _, meta_text = row["machine"].partition("@")
    if not re.fullmatch(r"[A-Za-z_$][\w$.]*", machine):
        raise ValueError(f"machine {row['machine']!r} is not a class name")
    if meta_text and not meta_text.isdigit():
        raise ValueError(f"meta {meta_text!r} in {row['machine']!r} is not an mte_id")
    meta = int(meta_text) if meta_text else None
    try:
        confidence = float(row["confidence"])
    except ValueError:
        raise ValueError(f"confidence {row['confidence']!r} is not a number") from None
    if not 0 <= confidence <= 1:
        raise ValueError(f"confidence {confidence} is not between 0 and 1")
    if not row["cite"]:
        raise ValueError("no citation: every curated number names its source")
    pairs = [
        (k, row[k])
        for k in ("processing", "parallel", "speed", "eu_modifier", "oc")
        if row[k]
    ]
    pairs += _split_extra(row["extra"])
    if not pairs:
        raise ValueError("no facts")
    keys = seen.setdefault((machine, meta), set())
    # The class row's keys for a class@meta row, and every class@meta row's for a class row.
    other = {
        key: f"{cls}@{m}" if m is not None else cls
        for (cls, m), ks in seen.items()
        if cls == machine and (m is None) != (meta is None)
        for key in ks
    }
    note = f"{row['cite']}" + (f"; {row['note']}" if row["note"] else "")
    facts = []
    for key, value in pairs:
        check_value(key, value)
        if key in keys:
            raise ValueError(f"{key} is given twice for {row['machine']}")
        if key in other:
            raise ValueError(
                f"{key} is given for both {other[key]} and {row['machine']}"
            )
        keys.add(key)
        facts.append(Fact(key, value, "curated", confidence, note))
    processing = dict(pairs).get("processing")
    if processing in ("generator", "none"):
        for key, value in pairs:
            if (
                key.split("[")[0] in ("parallel", "speed", "eu_modifier", "oc")
                and value != NA
            ):
                raise ValueError(
                    f"processing {processing} runs no recipes: {key} is n/a"
                )
    return Curated(machine, meta, row["name"], tuple(facts), line)


# --- assembling ----------------------------------------------------------------------------


@dataclass
class FactSet:
    """Every tooltip and curated fact of one run's machines, by mte_id."""

    facts: dict[int, list[Fact]]
    unmatched: list[Curated]  # curated rows for a class the run doesn't have
    bad_maps: list[tuple[Curated, str]]  # a per-map key naming a map the machine lacks


def assemble(machines: Iterable[Machine], curated: Sequence[Curated]) -> FactSet:
    by_class: dict[str, list[Machine]] = {}
    out: dict[int, list[Fact]] = {}
    for m in machines:
        by_class.setdefault(m.class_name, []).append(m)
        if m.multiblock:
            out[m.mte_id] = parse_tooltip(m.tooltip, bool(m.recipe_maps))
    unmatched: list[Curated] = []
    bad: list[tuple[Curated, str]] = []
    for c in curated:
        targets = [
            m
            for m in by_class.get(c.machine, [])
            if c.meta is None or m.mte_id == c.meta
        ]
        if not targets:
            unmatched.append(c)
            continue
        for m in targets:
            for f in c.facts:
                mm = _KEY.fullmatch(f.key)
                if mm and mm.group(2) and mm.group(2) not in m.recipe_maps:
                    bad.append((c, f.key))
            out.setdefault(m.mte_id, []).extend(c.facts)
    return FactSet(out, unmatched, bad)


def inputs(facts: Iterable[Fact]) -> set[str]:
    """The kinds of variable (:data:`KINDS`) that the formulas among ``facts`` use."""
    out: set[str] = set()
    for f in facts:
        if KEYS[f.base_key].formula and f.value not in (NA, "unlimited"):
            out |= {VARIABLES[n].kind for n in variables(f.value)}
    return out


def best(facts: Iterable[Fact]) -> dict[str, Fact]:
    """The fact the planner uses for each key (see the module docstring)."""
    out: dict[str, Fact] = {}
    for f in facts:
        cur = out.get(f.key)
        if cur is None or (SOURCE_RANK[f.source], f.confidence) > (
            SOURCE_RANK[cur.source],
            cur.confidence,
        ):
            out[f.key] = f
    return out


# --- coverage ------------------------------------------------------------------------------


def base_mods(manifest: Mapping[str, object]) -> set[str]:
    """Mod ids that come from GT's own jar (GT5-Unofficial): the base pack's GT family.

    Matched by the jar's sha256 (else its file name) against modid ``gregtech``'s, so the dev
    runtime's single GT jar and the pack's ``gregtech-*.jar`` both work. Empty when GT is not in
    the manifest's mod list.
    """
    mods = manifest.get("mods")
    if not isinstance(mods, list):
        return set()
    entries = [
        m for m in mods if isinstance(m, dict) and isinstance(m.get("modid"), str)
    ]
    gt = next((m for m in entries if m["modid"] == "gregtech"), None)
    if gt is None:
        return set()
    if gt.get("sha256"):
        return {m["modid"] for m in entries if m.get("sha256") == gt["sha256"]}
    return {
        m["modid"] for m in entries if gt.get("file") and m.get("file") == gt["file"]
    }


@dataclass(frozen=True)
class Status:
    machine: Machine
    state: str  # covered | n/a | flagged | missing
    parallel: Fact | None
    oc: Fact | None
    why: str


def _per_map(b: Mapping[str, Fact], key: str, maps: Sequence[str]) -> list[Fact | None]:
    """``key``'s fact on each of the machine's maps (``key[map]``, else ``key``)."""
    return [b.get(f"{key}[{m}]") or b.get(key) for m in maps] or [b.get(key)]


def _weakest(found: Sequence[Fact | None]) -> Fact | None:
    """The least sure of the facts, None if a map has none."""
    if any(f is None for f in found):
        return None
    return min((f for f in found if f is not None), key=lambda f: f.confidence)


def status(machine: Machine, facts: Sequence[Fact]) -> Status:
    """Whether the planner can trust ``machine``'s parallels and OC rules (WO-1.7 acceptance).

    ``flagged``: a ``flag`` fact says why not, on a machine with recipe maps or a curated
    ``processing custom`` one without (it runs its own recipe check). A Lab probe clears the
    flag: parallel and OC ``probe`` facts at the threshold on every map. ``n/a``: it runs no
    recipes (no recipe map, or curated ``processing`` generator/none at the threshold).
    ``covered``: on every recipe map, its best parallel and OC facts (the map's own ``key[map]``
    or the plain key) are at the threshold. ``missing``: none of these.
    """
    b = best(facts)
    maps = machine.recipe_maps
    pars, ocs = _per_map(b, "parallel", maps), _per_map(b, "oc", maps)
    par, oc = _weakest(pars), _weakest(ocs)
    proc = b.get("processing")
    flag = b.get("flag")
    if flag is not None and all(
        f is not None and f.source == "probe" and f.confidence >= THRESHOLD
        for f in (*pars, *ocs)
    ):
        flag = None
    custom = proc is not None and proc.value == "custom"
    if not maps and not (flag is not None and custom):
        return Status(machine, "n/a", par, oc, "no recipe map")
    if proc and proc.value in ("generator", "none") and proc.confidence >= THRESHOLD:
        return Status(machine, "n/a", par, oc, f"processing {proc.value}")
    if flag is not None:
        return Status(machine, "flagged", par, oc, flag.note)
    if (
        par is not None
        and oc is not None
        and min(par.confidence, oc.confidence) >= THRESHOLD
    ):
        return Status(machine, "covered", par, oc, "")
    low = ", ".join(
        f"{k} {f.confidence:.2f} ({f.source})" if f else f"{k} none"
        for k, f in (("parallel", par), ("oc", oc))
    )
    return Status(machine, "missing", par, oc, low)


def disagreements(
    facts: Sequence[Fact], per_map: bool = False
) -> list[tuple[Fact, Fact]]:
    """(tooltip, curated) pairs that give one key different values (formulas compared at
    sample points); tooltip facts at 0.5 or less (defaults, coil-tier readings) and curated
    ``n/a`` are left out. A new pair after a GT update is a sign the curated row is stale.

    ``per_map``: the curated ``key[map]`` facts against the tooltip's plain ``key`` instead.
    A tooltip mixes a machine's modes, so these pairs are only worth a check: one mode of a
    machine with two usually differs from the tooltip's number.
    """
    tips = {f.key: f for f in facts if f.source == "tooltip" and f.confidence > DEFAULT}
    out = []
    for f in facts:
        if f.source != "curated" or f.value == NA or (f.key != f.base_key) != per_map:
            continue
        t = tips.get(f.base_key)
        if t is not None and not same_value(f.key, t.value, f.value):
            out.append((t, f))
    return out
