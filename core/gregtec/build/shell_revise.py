"""GOS's own reviser: Clayton's words applied to a shell's operations, with no model (WO-10.10,
plan 7.10 "Shell designer", D316, D367, D368).

The fallback when no AI is available (No-AI mode, no key, the workbench's free Revise) and the
test baseline for revisions, as :func:`gregtec.build.shell.procedural` is for designs. It reads
the saved version's operations (the building vocabulary, :data:`gregtec.build.shell.OPS`) and
changes only what the words name; everything else is copied as it was. What it understands:

- the roof: ``steeper`` / ``pitched`` / ``gable`` (flat becomes a gable, a gable's ``pitch``
  rises by one, at most 3), ``flatter`` / ``shallower`` (the other way), ``flat roof``,
  ``parapet`` / ``no parapet``;
- the height: ``taller`` / ``higher`` and ``shorter`` / ``lower`` (by 2 blocks, or ``by N``):
  the walls' top, the roof and the lights move together, the headroom with them; never lower
  than the machines' space (the layout's top: the walls must close it);
- windows, lights, pillars: ``no ...`` / ``without ...`` / ``remove ...`` (optionally ``on the
  north (and east) wall(s)`` for windows), ``add ...`` / ``... on the north wall``, ``more`` /
  ``fewer``, and for windows ``bigger`` / ``wider`` / ``smaller`` / ``narrower`` / ``taller``;
- the plinth or trim, the eaves or band: ``no ...`` / ``add ...``.

A request it can't act on, in full, is never half done: :attr:`Revision.unknown` lists the
parts it didn't understand and the caller refuses the revision (nothing written), naming them.
The gate (:func:`gregtec.build.shell.design`) checks the result like any other design.

Pure: no DB, no files, no model; the same words on the same operations give the same result.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gregtec.build import shell

MAX_WORDS = 400  # characters of one instruction
STEP = 2  # blocks "taller" or "shorter" means without a number
MAX_PITCH = 3
SIDES = shell.SIDES

_SPLIT = re.compile(r"\s*(?:[,;.!?]|\band\b|\bbut\b|\bthen\b|\balso\b)\s*")
_FILLER = frozenset(
    [
        "please",
        "make",
        "it",
        "the",
        "a",
        "an",
        "bit",
        "little",
        "slightly",
        "much",
        "more",
        "lot",
        "of",
        "can",
        "you",
        "could",
        "would",
        "i",
        "want",
        "like",
        "do",
        "with",
        "be",
        "is",
        "are",
        "this",
        "that",
        "let's",
        "lets",
        "ok",
        "okay",
        "so",
        "also",
        "just",
        "too",
        "gos",
        "building",
        "shell",
        "walls",
        "wall",
    ]
)
_NO = re.compile(r"\b(?:no|without|remove|drop|lose|delete|get rid of|take out|none)\b")
_ADD = re.compile(r"\b(?:add|put|give it|with|some)\b")
_MORE = re.compile(r"\b(?:more|closer|denser|extra)\b")
_FEWER = re.compile(r"\b(?:fewer|less|sparser|further apart)\b")
_BIGGER = re.compile(r"\b(?:bigger|wider|larger|broader)\b")
_SMALLER = re.compile(r"\b(?:smaller|narrower|thinner|slimmer)\b")
_TALLER = re.compile(r"\b(?:taller|higher|raise|raised)\b")
_SHORTER = re.compile(r"\b(?:shorter|lower|lowered)\b")
_STEEPER = re.compile(r"\b(?:steeper|pitched|peaked|pointed|sloped|sloping|gabled?)\b")
_FLATTER = re.compile(r"\b(?:flatter|shallower|less steep|gentler)\b")
_FLAT = re.compile(r"\bflat\b")
_BY = re.compile(r"\bby (\d{1,2})\b|\b(\d{1,2}) blocks?\b")
_SIDE = re.compile(r"\b(north|south|east|west)(?:ern)?\b")
_PARTS = {
    "window": re.compile(r"\bwindows?\b|\bglass\b|\bglazing\b"),
    "light": re.compile(r"\blights?\b|\blamps?\b|\blighting\b"),
    "pillar": re.compile(r"\bpillars?\b|\bcolumns?\b|\bposts?\b"),
    "trim": re.compile(r"\bplinth\b|\btrim\b|\bbase course\b"),
    "accent": re.compile(r"\beaves\b|\bband\b|\baccent\b|\bstripe\b"),
    "parapet": re.compile(r"\bparapets?\b"),
    "roof": re.compile(r"\broofs?\b"),
}


@dataclass(frozen=True)
class Revision:
    ops: tuple[dict[str, Any], ...]
    headroom: int
    changes: tuple[str, ...]  # what was changed, in words, one line each
    unknown: tuple[str, ...]  # the parts of the words it could not act on (refuse then)
    impossible: tuple[
        str, ...
    ]  # understood, but the vocabulary or the layout can't (refuse)

    @property
    def ok(self) -> bool:
        return bool(self.changes) and not self.unknown and not self.impossible


def clauses(words: str) -> list[str]:
    """The instruction split into its requests: "taller, and no windows on the north wall" is
    two; "no windows on the north and east walls" stays one (a piece with nothing to change
    joins the one before it)."""
    text = " ".join(words.replace("’", "'").casefold().split())[:MAX_WORDS]
    out: list[str] = []
    for piece in (p.strip() for p in _SPLIT.split(text)):
        if not piece:
            continue
        verbless = not any(
            rx.search(piece)
            for rx in (_NO, _ADD, _MORE, _FEWER, _BIGGER, _SMALLER, _TALLER)
            + (_SHORTER, _STEEPER, _FLATTER, _FLAT)
        )
        if out and verbless and (_SIDE.search(piece) or not _part(piece)):
            out[-1] = f"{out[-1]} and {piece}"
        else:
            out.append(piece)
    return out


def _part(clause: str) -> str | None:
    for name, rx in _PARTS.items():
        if rx.search(clause):
            return name
    return None


def _filler(clause: str) -> bool:
    return all(w in _FILLER for w in re.findall(r"[a-z']+", clause))


class _Ops:
    """The operations being revised, with the geometry they default to."""

    def __init__(self, rec: Mapping[str, Any]) -> None:
        self.ops: list[dict[str, Any]] = [copy.deepcopy(dict(o)) for o in rec["ops"]]
        self.headroom = int(rec["headroom"])
        self.wall_top = int(rec["wall_top"])
        self.roof_y = int(rec["roof_y"])
        self.floor = int(rec["bounds"]["lo"][1])
        lo, hi = rec["bounds"]["lo"], rec["bounds"]["hi"]
        self.short = min(hi[0] - lo[0], hi[2] - lo[2]) + 1
        self.roles = {str(p["role"]) for p in rec["palette"]}

    def of(self, kind: str, role: str | None = None) -> list[dict[str, Any]]:
        return [
            o
            for o in self.ops
            if o.get("op") == kind and (role is None or o.get("role") == role)
        ]

    def y(self, op: Mapping[str, Any], y0: int, y1: int) -> list[int]:
        got = op.get("y", [y0, y1])
        if isinstance(got, int):
            return [got, got]
        return [int(got[0]), int(got[1])]

    def roof(self) -> dict[str, Any] | None:
        found = self.of("roof")
        return found[-1] if found else None

    def roof_at(self) -> int:
        r = self.roof()
        return int(r.get("y", self.roof_y)) if r is not None else self.roof_y

    def raise_by(self) -> int:
        """How far the roof stands above the machines' space today."""
        return self.roof_at() - self.roof_y

    def need_headroom(self) -> int:
        """The headroom the roof (with its slope and parapet) needs to stand whole."""
        r = self.roof()
        top = self.roof_at()
        if r is not None and r.get("style") == "gable":
            steps = (self.short - 1) // 2 + 1
            top += steps * int(r.get("pitch", 1)) - 1
        if r is not None:
            top += int(r.get("parapet", 0))
        return top - self.wall_top

    def insert_before_roof(self, op: dict[str, Any]) -> None:
        at = next(
            (
                i
                for i, o in enumerate(self.ops)
                if o.get("op") in ("roof", "lights", "clear")
            ),
            len(self.ops),
        )
        self.ops.insert(at, op)


def revise(rec: Mapping[str, Any], words: str) -> Revision:
    """``words`` applied to the saved version ``rec`` (its ``design.json`` record)."""
    o = _Ops(rec)
    changes: list[str] = []
    unknown: list[str] = []
    impossible: list[str] = []
    for c in clauses(words):
        if _filler(c):
            continue
        before = len(changes) + len(impossible)
        part = _part(c)
        if part == "parapet" or (part == "roof" and "parapet" in c):
            _parapet(o, c, changes, impossible)
        elif part == "roof" or (
            part is None
            and (_STEEPER.search(c) or _FLATTER.search(c) or _FLAT.search(c))
        ):
            _roof(o, c, changes, impossible)
        elif part == "window":
            _windows(o, c, changes, impossible)
        elif part == "light":
            _grid(o, c, "lights", "light", 6, 2, 32, changes, impossible)
        elif part == "pillar":
            _grid(o, c, "pillars", "pillar", 8, 2, 64, changes, impossible)
        elif part in ("trim", "accent"):
            _band(o, c, part, changes, impossible)
        elif _TALLER.search(c) or _SHORTER.search(c):
            _height(o, c, changes, impossible)
        if len(changes) + len(impossible) == before:
            unknown.append(c)
    # The roof must fit: raise the headroom to what it needs (at most 16), never lower it.
    need = o.need_headroom()
    _, _, top = shell.LIMITS["headroom"]
    if need > o.headroom and not impossible:
        if need > top:
            o.headroom = top
            changes.append(
                f"headroom {top} (the most): the roof is closed flat where it reaches it"
            )
        else:
            o.headroom = need
            changes.append(f"headroom {need}, so the roof fits")
    return Revision(
        tuple(o.ops), o.headroom, tuple(changes), tuple(unknown), tuple(impossible)
    )


def _by(c: str) -> int:
    m = _BY.search(c)
    if m is None:
        return STEP
    return max(1, int(m.group(1) or m.group(2)))


def _roof(o: _Ops, c: str, changes: list[str], impossible: list[str]) -> None:
    r = o.roof()
    if r is None:
        impossible.append(f"{c!r}: this version has no roof operation")
        return
    style = r.get("style", "flat")
    if _FLATTER.search(c) or (_FLAT.search(c) and not _STEEPER.search(c)):
        if style == "flat":
            impossible.append(f"{c!r}: the roof is flat already")
        elif (_FLAT.search(c) and not _FLATTER.search(c)) or int(
            r.get("pitch", 1)
        ) == 1:
            r["style"] = "flat"
            r.pop("pitch", None)
            changes.append("roof: flat")
        else:
            r["pitch"] = int(r.get("pitch", 1)) - 1
            if r["pitch"] == 1:
                r.pop("pitch")
            changes.append(f"roof: gable, pitch {r.get('pitch', 1)}")
        return
    if _STEEPER.search(c) or _TALLER.search(c):
        if style != "gable":
            r["style"] = "gable"
            r.pop("parapet", None)  # a parapet stands on a flat roof's edge
            r.pop("parapet_role", None)
            r.pop("parapet_n", None)
            changes.append("roof: gable (from flat), pitch 1")
        elif int(r.get("pitch", 1)) >= MAX_PITCH:
            impossible.append(
                f"{c!r}: the roof is at the steepest pitch the vocabulary has ({MAX_PITCH})"
            )
        else:
            r["pitch"] = int(r.get("pitch", 1)) + 1
            changes.append(f"roof: gable, pitch {r['pitch']}")


def _parapet(o: _Ops, c: str, changes: list[str], impossible: list[str]) -> None:
    r = o.roof()
    if r is None:
        impossible.append(f"{c!r}: this version has no roof operation")
        return
    if _NO.search(c):
        if not r.get("parapet"):
            impossible.append(f"{c!r}: the roof has no parapet")
            return
        for k in ("parapet", "parapet_role", "parapet_n"):
            r.pop(k, None)
        changes.append("roof: no parapet")
        return
    if r.get("style", "flat") != "flat":
        impossible.append(
            f"{c!r}: a parapet stands on a flat roof's edge; the roof is a gable"
        )
        return
    n = min(3, int(r.get("parapet", 0)) + 1)
    if n == r.get("parapet"):
        impossible.append(f"{c!r}: the parapet is 3 high already, the most")
        return
    r["parapet"] = n
    if "wall" in o.roles and "parapet_role" not in r:
        r["parapet_role"] = "wall"
    changes.append(f"roof: parapet {n} high")


def _height(o: _Ops, c: str, changes: list[str], impossible: list[str]) -> None:
    k = _by(c)
    if _SHORTER.search(c):
        up = o.raise_by()
        if up <= 0:
            impossible.append(
                f"{c!r}: the walls already stop at the machines' height (the layout's top); "
                "lower would leave the line open"
            )
            return
        k = -min(k, up)
    _, _, top = shell.LIMITS["headroom"]
    if o.headroom + k > top:
        impossible.append(
            f"{c!r}: {k} higher needs headroom {o.headroom + k}, more than {top}"
        )
        return
    old_top = o.wall_top + o.raise_by()
    for w in o.of("walls"):
        y0, y1 = o.y(w, o.floor, o.wall_top)
        if y1 >= old_top:  # the walls' top course (and the eaves band on it) moves up
            w["y"] = [y0 + k if y0 == y1 else y0, y1 + k]
    for p in o.of("pillars"):
        y0, y1 = o.y(p, o.floor, o.wall_top)
        if y1 >= old_top:
            p["y"] = [y0, y1 + k]
    for op in (*o.of("roof"), *o.of("lights")):
        op["y"] = int(op.get("y", o.roof_y)) + k
    o.headroom += k
    changes.append(
        f"{abs(k)} block{'s' if abs(k) != 1 else ''} {'taller' if k > 0 else 'shorter'}: "
        f"walls to {old_top + k}, roof at {o.roof_at()}, headroom {o.headroom}"
    )


def _sides(c: str) -> list[str]:
    return [s for s in SIDES if s in _SIDE.findall(c)]


def _windows(o: _Ops, c: str, changes: list[str], impossible: list[str]) -> None:
    found = o.of("windows")
    sides = _sides(c)
    if _NO.search(c):
        if not found:
            impossible.append(f"{c!r}: this version has no windows")
            return
        if not sides:
            o.ops = [x for x in o.ops if x.get("op") != "windows"]
            changes.append("windows: none")
            return
        for w in found:
            w["sides"] = [s for s in w.get("sides", list(SIDES)) if s not in sides]
        o.ops = [x for x in o.ops if not (x.get("op") == "windows" and not x["sides"])]
        changes.append(
            f"windows: none on the {' and '.join(sides)} side{'s' * (len(sides) > 1)}"
        )
        return
    if not found:
        if not (_ADD.search(c) or sides or _MORE.search(c)):
            return
        if "window" not in o.roles:
            impossible.append(f"{c!r}: the palette has no window role")
            return
        op: dict[str, Any] = {
            "op": "windows",
            "role": "window",
            "y": [1, max(1, min(o.wall_top, 2))],
            "every": 5,
            "width": 2,
        }
        if sides:
            op["sides"] = sides
        o.insert_before_roof(op)
        changes.append(
            "windows: added" + (f" on the {' and '.join(sides)}" if sides else "")
        )
        return
    if sides and (_ADD.search(c) or not (_MORE.search(c) or _FEWER.search(c))):
        for w in found:
            have = w.get("sides", list(SIDES))
            w["sides"] = [s for s in SIDES if s in have or s in sides]
            if w["sides"] == list(SIDES):
                w.pop("sides")
        changes.append(f"windows: on the {' and '.join(sides)} too")
        return
    for w in found:
        every, width = int(w.get("every", 4)), int(w.get("width", 2))
        if _MORE.search(c):
            every = max(width + 1, every - 1)
        elif _FEWER.search(c):
            every = min(64, every + 2)
        elif _BIGGER.search(c):
            width += 1
            every = max(every, width + 1)
        elif _SMALLER.search(c):
            width = max(1, width - 1)
        elif _TALLER.search(c):
            y0, y1 = o.y(w, 1, o.wall_top)
            w["y"] = [max(o.floor + 1, y0 - 1), min(o.wall_top, y1 + 1)]
        elif _SHORTER.search(c):
            y0, y1 = o.y(w, 1, o.wall_top)
            if y1 > y0:
                w["y"] = [y0, y1 - 1]
        else:
            return
        w["every"], w["width"] = every, width
    changes.append(f"windows: {c}")


def _grid(
    o: _Ops,
    c: str,
    kind: str,
    role: str,
    default: int,
    lo: int,
    hi: int,
    changes: list[str],
    impossible: list[str],
) -> None:
    found = o.of(kind)
    if _NO.search(c):
        if not found:
            impossible.append(f"{c!r}: this version has no {kind}")
            return
        o.ops = [x for x in o.ops if x.get("op") != kind]
        changes.append(f"{kind}: none")
        return
    if not found:
        if not (_ADD.search(c) or _MORE.search(c)):
            return
        if role not in o.roles:
            impossible.append(f"{c!r}: the palette has no {role} role")
            return
        op: dict[str, Any] = {"op": kind, "role": role, "every": default}
        if kind == "pillars":
            op["y"] = [o.floor, o.wall_top + o.raise_by()]
            o.insert_before_roof(op)
        else:
            op["y"] = o.roof_at()
            o.ops.insert(
                next(
                    (i for i, x in enumerate(o.ops) if x.get("op") == "clear"),
                    len(o.ops),
                ),
                op,
            )
        changes.append(f"{kind}: added, every {default}")
        return
    step = 2 if kind == "pillars" else 1
    for g in found:
        every = int(g.get("every", default))
        if _MORE.search(c):
            g["every"] = max(lo, every - step)
        elif _FEWER.search(c):
            g["every"] = min(hi, every + 2 * step)
        else:
            return
    changes.append(f"{kind}: every {found[0]['every']}")


def _band(
    o: _Ops, c: str, role: str, changes: list[str], impossible: list[str]
) -> None:
    what = "plinth" if role == "trim" else "eaves band"
    found = o.of("walls", role)
    if _NO.search(c):
        if not found:
            impossible.append(f"{c!r}: this version has no {what}")
            return
        o.ops = [
            x for x in o.ops if not (x.get("op") == "walls" and x.get("role") == role)
        ]
        changes.append(f"{what}: none")
        return
    if found or not _ADD.search(c):
        return
    if role not in o.roles:
        impossible.append(f"{c!r}: the palette has no {role} role")
        return
    y = o.floor if role == "trim" else o.wall_top + o.raise_by()
    at = next(
        (i for i, x in enumerate(o.ops) if x.get("op") != "walls"), len(o.ops)
    )  # after the walls it sits on
    o.ops.insert(at, {"op": "walls", "role": role, "y": [y, y]})
    changes.append(f"{what}: added")


def describe(
    ops_a: Sequence[Mapping[str, Any]], ops_b: Sequence[Mapping[str, Any]]
) -> list[str]:
    """What changed between two versions' operations, by kind: ``roof: flat -> gable``."""
    out = []
    kinds = sorted({str(o.get("op")) for o in (*ops_a, *ops_b)})
    for k in kinds:
        a = [o for o in ops_a if o.get("op") == k]
        b = [o for o in ops_b if o.get("op") == k]
        if a == b:
            continue
        if not a:
            out.append(f"{k}: added ({len(b)})")
        elif not b:
            out.append(f"{k}: removed ({len(a)})")
        elif len(a) != len(b):
            out.append(f"{k}: {len(a)} -> {len(b)} operations")
        else:
            for x, y in zip(a, b, strict=True):
                keys = sorted(set(x) | set(y))
                diff = [
                    f"{key} {x.get(key)} -> {y.get(key)}"
                    for key in keys
                    if x.get(key) != y.get(key)
                ]
                if diff:
                    out.append(f"{k} ({x.get('role', '')}): " + ", ".join(diff))
    return out
