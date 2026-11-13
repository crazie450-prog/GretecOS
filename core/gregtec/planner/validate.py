"""The numeric validator for planner explanations (WO-3.5, plan §7.1 step 11, [QA:D4], D199).

A model writes route explanations from the metrics JSON only (:mod:`.explain`); :func:`check`
reads every number in its text and accepts it only when the JSON holds it (plan 0.1 principle
1). Before comparing, it normalises:

- **Masks**: citations and DB keys (``r:``, ``mte:``, ``plan:``... and ``registry@meta``), plan
  ids (``pl-...``), and the resolved names (items, machines, routes: ``names``), so the digits
  and Roman numerals in "Circuit 1" or "Quantum Tank IV" are not read as numbers.
- **Prefixes and units**: ``k`` and ``M`` (``1.2k`` is 1200, and glued to EU as GT writes
  it: ``1.2 kEU/t``); ``x64`` or ``×64`` as the count 64; a rate unit (``/s``, ``/min``,
  ``/h``, ``per second|minute|hour``, with one word allowed before the slash: ``2 ingots/min``)
  is compared per second against the JSON's rates (``*rate_s``, ``crafts_s``, ``per_h / 3600``);
  a duration unit (``s``, ``min``, ``h`` and their words) in seconds against its times only
  (keys ending ``_s`` or ``_h``, and ``h``: "1500 seconds" doesn't pass on a 1500 EU/t peak);
  ``%`` as a fraction or as is; a bare number against every number the JSON holds, in its own
  unit.
- **Rounding**: within 1% of a JSON number; within 5% after "about" or "~"; 0 only as 0.
- **Tiers**: a tier name (LV ... MAX) must be one the JSON names, and "tier N" must be one of
  their indices.
- **Sums** (and, between routes, differences, D199): per list in the JSON, the total of each
  key, and for counts (:data:`COUNTS`) the sum of any two entries; per scalar metric, the sum
  and the difference of any two routes'. Sums of any two rates or hours are not allowed: in a
  big design they would cover so many values that a wrong number would often pass.

Exponent forms (``1e9``) and digits glued after letters that masking left (``MV2``, ``H2O``)
are refused outright. Numbers written as words aren't read (as in WO-2.3's check, D152). The
result lists the offending numbers as written, for the retry prompt.
"""

from __future__ import annotations

import bisect
import itertools
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gregtec.planner.gtmath.tiers import VN

# Citations and DB keys, as gregtec.agents.fast_lane reads them (copied: importing the lane
# would load the Anthropic SDK into every `gos` command, and `gos plan` imports this module).
# A wiki citation (D291) is w:<title>@<revid>, and a title may hold any character but space
# and @ (parentheses, commas, quotes, accents): it is read whole, up to its revid.
REF = re.compile(
    r"\bw:[^\s@]+@\d+"
    r"|\b(?:r|q|mte|crop|plan|w|tel|vein|pos):[A-Za-z0-9_./@#:+-]*[A-Za-z0-9_/#-]"
)
KEY = re.compile(
    r"\bfluid:[A-Za-z0-9_.]+|[A-Za-z0-9_.|-]+:[A-Za-z0-9_.|-]+@\d+(?:#[0-9a-f]{8,64})?"
)

COUNTS = frozenset(
    {"count", "machines", "crafts", "nodes", "fluids", "loops", "new_types"}
)
TOLERANCE = 0.01  # §7.1 step 11: ±1% rounding
APPROX_TOLERANCE = 0.05  # after "about" or "~"
PLAN_ID = re.compile(r"\bpl-[a-z0-9._-]+")
APPROX = re.compile(r"(?:\babout|~)\s*$", re.IGNORECASE)
TIER_WORD = re.compile(
    r"(?<![A-Za-z0-9])("
    + "|".join(sorted(VN, key=len, reverse=True))
    + r")(?![A-Za-z0-9])"
)
TIER_N = re.compile(r"\btier\s+(\d+)\b", re.IGNORECASE)
_RATE_UNIT = {"s": 1.0, "sec": 1.0, "second": 1.0, "min": 60.0, "minute": 60.0}
_RATE_UNIT |= {"h": 3600.0, "hr": 3600.0, "hour": 3600.0}
_TIME_UNIT = {"s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0}
_TIME_UNIT |= {"min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0}
_TIME_UNIT |= {
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
}
NUMBER = re.compile(
    r"(?:(?<![A-Za-z0-9_])(?P<x>[x\u00d7])|(?<![A-Za-z0-9_.,]))"
    r"(?P<num>\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"(?:\s?(?P<prefix>[kM])(?:(?=EU)|(?![A-Za-z])))?"
    r"(?P<tail>"
    r"(?:\s*(?:[A-Za-z]+\s*)?/\s*(?P<rate>second|sec|minute|min|hour|hr|s|h)\b)"
    r"|(?:\s*(?:[A-Za-z]+\s+)?per\s+(?P<per>second|sec|minute|min|hour|hr|h)\b)"
    r"|(?:\s*(?P<time>seconds|second|secs|sec|minutes|minute|mins|min|hours|hour|hrs|hr|s|h)\b(?!\s*/))"
    r"|(?P<pct>\s*%)"
    r"|(?P<ord>st|nd|rd|th)\b"
    r")?"
)
_PREFIX = {"k": 1e3, "M": 1e6}
# Read as offending outright: exponent forms ("1e9", "2.5E+3"), whose mantissa would otherwise
# pass alone, and digits glued after letters ("MV2", "H2O") that masking left: a count like
# "x64" is read by NUMBER instead, so it is skipped here.
EXPONENT = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?[eE][+-]?\d+(?![A-Za-z0-9_])")
GLUED = re.compile(r"(?<![A-Za-z0-9_])(?![x\u00d7]\d)[A-Za-z_]+\d[A-Za-z0-9_]*")


@dataclass(frozen=True)
class Stated:
    text: str  # as written, unit included
    value: float  # normalised: per second, seconds, or as is
    kind: str  # rate | time | pct | plain
    approx: bool


@dataclass(frozen=True)
class Result:
    ok: bool
    offending: tuple[
        str, ...
    ]  # numbers and tier names the JSON doesn't hold, as written
    checked: int  # numbers read


class Allowed:
    """The numbers one metrics JSON holds, by kind, with their sums (sorted, for bisect)."""

    def __init__(self, doc: object) -> None:
        rate: list[float] = []
        time: list[float] = []
        every: list[float] = []
        tiers: set[str] = set()
        groups: list[tuple[str, list[float]]] = []
        _walk(doc, None, rate, time, every, tiers, groups)
        derived_rate: list[float] = []
        derived_time: list[float] = []
        derived: list[float] = []
        for key, values in groups:
            vals = [abs(v) for v in values]
            name = key.removeprefix("route:")
            pairs = key.startswith("route:") or name in COUNTS
            extra = [math.fsum(vals)]
            if pairs:
                extra += [a + b for a, b in itertools.combinations(vals, 2)]
            if key.startswith("route:"):
                extra += [abs(a - b) for a, b in itertools.combinations(vals, 2)]
            kind = _kind(name)
            if kind == "rate":
                scale = 1 / 3600 if name.endswith("per_h") else 1.0
                derived_rate.extend(v * scale for v in extra)
            elif kind == "time":
                scale = 3600.0 if name == "h" or name.endswith("_h") else 1.0
                derived_time.extend(v * scale for v in extra)
            derived.extend(extra)
        self.rate = sorted(rate + derived_rate)
        self.time = sorted(time + derived_time)
        self.plain = sorted(every + derived)
        self.tiers = frozenset(tiers)

    def has(self, s: Stated) -> bool:
        tol = APPROX_TOLERANCE if s.approx else TOLERANCE
        if s.kind == "rate":
            return _near(self.rate, s.value, tol)
        if s.kind == "time":
            return _near(self.time, s.value, tol)
        if s.kind == "pct":
            return _near(self.plain, s.value / 100, tol) or _near(
                self.plain, s.value, tol
            )
        return (
            _near(self.plain, s.value, tol)
            or _near(self.rate, s.value, tol)
            or _near(self.time, s.value, tol)
        )


def _kind(key: str | None) -> str:
    if key is None:
        return "plain"
    if key.endswith(("rate_s", "per_h")) or key == "crafts_s":
        return "rate"
    if key == "h" or key.endswith(("_h", "_s")):
        return "time"
    return "plain"


def _walk(
    node: object,
    key: str | None,
    rate: list[float],
    time: list[float],
    every: list[float],
    tiers: set[str],
    groups: list[tuple[str, list[float]]],
) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, int | float):
        v = abs(float(node))
        if not math.isfinite(v):
            return
        every.append(v)
        kind = _kind(key)
        if kind == "rate":
            rate.append(v / 3600 if key and key.endswith("per_h") else v)
        elif kind == "time":
            time.append(v * 3600 if key and (key == "h" or key.endswith("_h")) else v)
        return
    if isinstance(node, str):
        if node in VN:
            tiers.add(node)
        return
    if isinstance(node, Mapping):
        for k in sorted(node):
            _walk(node[k], str(k), rate, time, every, tiers, groups)
        if isinstance(node.get("routes"), list):
            _route_groups(node["routes"], groups)
        return
    if isinstance(node, list):
        for item in node:
            _walk(item, key, rate, time, every, tiers, groups)
        dicts = [d for d in node if isinstance(d, Mapping)]
        if len(dicts) > 1:
            keys = sorted({k for d in dicts for k in d})
            for k in keys:
                vals = [float(d[k]) for d in dicts if _num(d.get(k))]
                if len(vals) > 1:
                    groups.append((str(k), vals))


def _route_groups(routes: list[Any], groups: list[tuple[str, list[float]]]) -> None:
    """Per scalar metric, the routes' values (their sums and differences are allowed)."""
    metrics: list[Mapping[str, Any]] = [
        r["metrics"]
        for r in routes
        if isinstance(r, Mapping) and isinstance(r.get("metrics"), Mapping)
    ]
    keys = sorted({k for m in metrics for k, v in m.items() if _num(v)})
    for k in keys:
        vals = [float(m[k]) for m in metrics if _num(m.get(k))]
        if len(vals) > 1:
            groups.append((f"route:{k}", vals))


def _num(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _near(sorted_values: Sequence[float], v: float, tol: float) -> bool:
    if v == 0:
        i = bisect.bisect_left(sorted_values, 0.0)
        return i < len(sorted_values) and sorted_values[i] == 0
    lo, hi = v / (1 + tol), v / (1 - tol) if tol < 1 else math.inf
    i = bisect.bisect_left(sorted_values, lo)
    return i < len(sorted_values) and sorted_values[i] <= hi


def mask(text: str, names: Iterable[str] = ()) -> str:
    """``text`` with citations, keys, plan ids and the resolved names blanked."""
    text = REF.sub(" ", text)
    text = KEY.sub(" ", text)
    text = PLAN_ID.sub(" ", text)
    for name in sorted({n for n in names if n.strip()}, key=len, reverse=True):
        # A plural goes with its name: left behind, the "s" of "4 Mixers" would read as seconds.
        text = re.sub(re.escape(name) + r"(?:e?s\b)?", " ", text, flags=re.IGNORECASE)
    return text


def stated(text: str) -> list[Stated]:
    """The numbers in already-masked ``text``, normalised (see the module docstring)."""
    out: list[Stated] = []
    for m in NUMBER.finditer(text):
        raw = m["num"].replace(",", "")
        v = float(raw) * _PREFIX.get(m["prefix"] or "", 1.0)
        approx = APPROX.search(text[max(0, m.start() - 12) : m.start()]) is not None
        unit = m["rate"] or m["per"]
        if unit:
            out.append(Stated(m[0].strip(), v / _RATE_UNIT[unit], "rate", approx))
        elif m["time"]:
            out.append(Stated(m[0].strip(), v * _TIME_UNIT[m["time"]], "time", approx))
        elif m["pct"]:
            out.append(Stated(m[0].strip(), v, "pct", approx))
        else:
            out.append(Stated(m[0].strip(), v, "plain", approx))
    return out


def check(text: str, doc: object, names: Iterable[str] = ()) -> Result:
    """Whether every number (and tier) in ``text`` is one ``doc`` holds (see the module
    docstring); ``names`` are the resolved names to mask."""
    return check_against(text, Allowed(doc), names)


def check_against(text: str, allowed: Allowed, names: Iterable[str] = ()) -> Result:
    masked = mask(text, names)
    bad: list[str] = []
    for m in TIER_N.finditer(masked):
        n = int(m[1])
        if n >= len(VN) or VN[n] not in allowed.tiers:
            bad.append(m[0])
    masked = TIER_N.sub(" ", masked)
    for m in TIER_WORD.finditer(masked):
        if m[1] not in allowed.tiers:
            bad.append(m[1])
    masked = TIER_WORD.sub(" ", masked)
    for pattern in (EXPONENT, GLUED):
        bad.extend(m[0] for m in pattern.finditer(masked))
        masked = pattern.sub(" ", masked)
    numbers = stated(masked)
    bad.extend(s.text for s in numbers if not allowed.has(s))
    return Result(not bad, tuple(dict.fromkeys(bad)), len(numbers))
