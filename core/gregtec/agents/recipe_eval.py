"""``gos eval recipe-lookup`` (WO-2.7 acceptance, D171): 50 recipe and item questions.

Each row of ``tests/evals/recipe_lookup.tsv`` (``id kind expect question truth want words``)
is asked through :class:`gregtec.agents.ask.Asker` as ``POST /api/ask`` asks it (router,
instant commands, pre-retrieval, fast lane; a request the router or the fast lane hands to the
deep lane is waited for there), one fresh conversation per row, and scored against the DB:

- **Ground truth from the DB.** ``truth`` holds references to DB rows, never game numbers: steps
  such as ``make gregtech:gt.metaitem.01@11305 map=gt.recipe.blastfurnace`` (the newest run's
  enabled, non-fake recipes that output that item in that map), ``use <key>`` (recipes that take
  it: shown, any meta, an oredict it is in, or an accepted alternative) and ``machine mte:<id>``
  (a GT machine and its facts); filters ``map=``, ``tier=``, ``in=``, ``out=`` and ``ore=``
  narrow a step, and ``;`` separates the steps of a multi-step question. :class:`Ground` turns
  each step into the rows it means, with every number each row holds, by its own SQL over the
  tables (not through the tools, whose answers are what is being checked). Rows flagged
  ``fake?`` (an NEI handler that doesn't say whether it is real) or ``recycling`` are left out
  of a step's rows when it has rows without a flag (the skill's rule: prefer a row without a
  flag); the count stays the tools' total, flagged rows included. ``want`` names, per step,
  what the answer must state: ``eut``, ``amps``, ``dur`` (ticks or seconds), ``tier``,
  ``machine``, ``count`` (how many recipes), ``simplest`` (a row at the step's lowest tier: "how
  do I make X", plan 7.3), ``in=<key>``/``out=<key>`` (an amount), ``chance=<key>``,
  ``alts=<key>``, ``ore=<oredict>``, ``cond=<key>`` (a recipe condition) or ``fact=<key>`` (a
  machine fact). ``words`` (per step) are the words the question names the step's item by; only
  the fake models read them (:mod:`gregtec.agents.recipe_oracle`).
- **Numbers in context.** Each number in the answer is read with its unit or label: EU/t or
  ``eut:``; A or amps; ticks, t or ``dur_t:``; s, seconds or ``dur_s:``; %; L, mB or x (an
  amount); "N others" (accepted alternatives); "N recipes" or "N in all" (a count); anything else
  is a plain number. A wanted value counts only in its own unit (120 stated as ticks is not the
  EU/t), an amount or a chance only next to its slot's name (the nearest name after it in its
  clause, else before it), a condition or a fact only next to its key, and a tier only when no
  other tier is named.
- **Correct** (``expect`` answer): the reply answers (doesn't ask back), cites a row of every
  step (``r:`` or ``mte:``), every id it cites exists in the DB, every wanted value is stated as
  above, and no number is uncited. ``ask``: the reply is a clarifying question (QA:D11) and
  states no number. ``none``: no recipe in the DB matches the step (the row is checked on this
  DB), and the reply says so, cites none and states no number.
- **Uncited numbers** (WO-2.3's number check, D152, read per number): every number in the text,
  less citations, DB keys and the digits of item, fluid and machine names the rows hold, must be
  a value of a row the text cites in its unit (an EU/t its EU/t, ticks its ticks, an amount one
  of its amounts, a chance one of its chances, a plain number any number it holds), or, in a
  count phrase, the count of a step whose rows it cites. Any other number is uncited, so an
  answer with numbers and no id at all counts all of them.

The report gives each row's lane, pass, uncited numbers, citations, extra citations (real ids
no step means) and why, then ``correct: N of M`` (plan 8: at least 95%) and ``uncited numbers:
N`` (plan 8: 0), by kind and lane; exit 0 when both targets hold. ``--fake`` runs
:mod:`gregtec.agents.recipe_oracle`: an oracle fast-lane model and, behind the real Claude Code
CLI, an oracle deep-lane model that loads the recipe-lookup skill and calls the tools it names;
no key, no cost, nothing recorded. That checks the logic (routing, item resolution from the
question's words, tools, truth, scoring), not a model. The real run (stored key; ``llm_usage``
task ``eval:recipe-lookup``) is Clayton's PC step (docs/runbooks/recipe_lookup.md).
"""

from __future__ import annotations

import csv
import datetime as dt
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.agents import fast_lane
from gregtec.agents.ask import Asker, Reply, setup
from gregtec.agents.deep import DONE, NEEDS_APPROVAL, DeepLaneService
from gregtec.agents.tools.items import FLUID_KEY, ITEM_KEY, MTE_KEY, WILDCARD, clean
from gregtec.agents.tools.recipes import TIERS
from gregtec.db import access

REPO_ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = REPO_ROOT / "tests" / "evals" / "recipe_lookup.tsv"
COLUMNS = ("id", "kind", "expect", "question", "truth", "want", "words")
KINDS = (
    "recipe",
    "uses",
    "machine",
    "power",
    "time",
    "chance",
    "fluid",
    "oredict",
    "multistep",
    "ambiguous",
    "none",
)
ANSWER, ASK, NONE = "answer", "ask", "none"
EXPECTS = (ANSWER, ASK, NONE)
MAKE, USE, MACHINE = "make", "use", "machine"
OPS = (MAKE, USE, MACHINE)
FILTERS = ("map", "tier", "in", "out", "ore")
PLAIN_WANTS = ("eut", "amps", "dur", "tier", "machine", "count", "simplest")
KEYED_WANTS = ("in", "out", "chance", "alts", "ore", "cond", "fact")
SLOT_WANTS = ("in", "out", "chance", "alts")  # keyed by an item or fluid key
FLAGS = ("fake?", "recycling")  # rows the skill uses only when no other row answers
CORRECT_TARGET = 0.95  # plan 8: at least 95% correct
TASK = "eval:recipe-lookup"
DEEP_WAIT_S = 1800.0  # a deep-lane task, as gos ask waits
STEP_SPLIT = re.compile(r"\s*;\s*")  # steps and their want parts; keys have no ";"
TIER_WORD = {t: re.compile(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])") for t in TIERS}
_DIGIT = re.compile(r"\d")


class EvalError(ValueError):
    """The question file is malformed."""


# --- the question file ------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One reference to DB rows: ``op key [filter=value ...]``, and what the answer must state."""

    op: str  # make, use or machine
    key: str  # an item or fluid key (make, use) or mte:<id> (machine)
    filters: tuple[tuple[str, str], ...] = ()
    want: tuple[str, ...] = ()

    def filter(self, name: str) -> str | None:
        return next((v for k, v in self.filters if k == name), None)

    def text(self) -> str:
        return " ".join([self.op, self.key, *(f"{k}={v}" for k, v in self.filters)])


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    expect: str
    question: str
    steps: tuple[Step, ...] = ()
    words: tuple[
        str, ...
    ] = ()  # per step (an ask row: one): how the question names its item


def is_key(text: str) -> bool:
    """An item or fluid key as the tools write them (spaces aren't allowed in this file)."""
    return " " not in text and bool(
        ITEM_KEY.fullmatch(text) or FLUID_KEY.fullmatch(text)
    )


def _want_ok(op: str, want: str) -> bool:
    name, sep, value = want.partition("=")
    if not sep:
        if op == MACHINE:
            return want in ("tier", "machine")
        return want in PLAIN_WANTS and (want != "simplest" or op == MAKE)
    if name not in KEYED_WANTS or not value or (name == "fact") != (op == MACHINE):
        return False
    return name not in SLOT_WANTS or is_key(value)


def _step(text: str, want: str, where: str) -> Step:
    words = text.split()
    if len(words) < 2 or words[0] not in OPS:
        raise EvalError(f"{where}: a step is 'make|use|machine <key> [k=v ...]'")
    op, key, rest = words[0], words[1], words[2:]
    if op == MACHINE:
        if not MTE_KEY.fullmatch(key) or rest:
            raise EvalError(f"{where}: machine takes mte:<id> and no filters")
    elif not is_key(key):
        raise EvalError(f"{where}: {key!r} is not an item or fluid key")
    filters = []
    for word in rest:
        name, sep, value = word.partition("=")
        if not sep or name not in FILTERS or not value:
            raise EvalError(
                f"{where}: bad filter {word!r} (one of {', '.join(FILTERS)})"
            )
        if name == "tier" and value not in TIERS:
            raise EvalError(f"{where}: tier={value} is not a GT tier")
        if name in ("in", "out") and not is_key(value):
            raise EvalError(f"{where}: {name}={value} is not an item or fluid key")
        filters.append((name, value))
    wants = tuple(w.strip() for w in want.split(",") if w.strip())
    for w in wants:
        if not _want_ok(op, w):
            raise EvalError(f"{where}: {w!r} can't be wanted of a {op} step")
    return Step(op, key, tuple(filters), wants)


def question(
    id_: str,
    kind: str,
    expect: str,
    text: str,
    truth: str = "",
    want: str = "",
    words: str = "",
    *,
    where: str = "",
) -> Question:
    """One row of the file, checked (``where`` names it in an error)."""
    where = where or id_
    if kind not in KINDS or expect not in EXPECTS or not text.strip():
        raise EvalError(f"{where}: bad kind, expect or question")
    texts = STEP_SPLIT.split(truth.strip()) if truth.strip() else []
    wants = STEP_SPLIT.split(want.strip()) if want.strip() else []
    if len(wants) > len(texts):
        raise EvalError(f"{where}: more want parts than truth steps")
    wants += [""] * (len(texts) - len(wants))
    steps = tuple(_step(t, w, where) for t, w in zip(texts, wants, strict=True))
    if (expect == ASK) != (not steps):
        raise EvalError(f"{where}: ask rows have no truth; the others need one")
    if expect == NONE and (len(steps) != 1 or steps[0].op == MACHINE):
        raise EvalError(f"{where}: a none row has one make or use step")
    named = tuple(" ".join(w.split()) for w in STEP_SPLIT.split(words.strip()))
    named = named if words.strip() else ()
    if len(named) > max(len(steps), 1):
        raise EvalError(f"{where}: more words parts than truth steps")
    return Question(id_, kind, expect, text.strip(), steps, named)


def load(path: Path = QUESTIONS) -> list[Question]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, None)
        if header is None or tuple(header) != COLUMNS:
            raise EvalError(f"{path}: the header must be {' '.join(COLUMNS)}")
        out = []
        for n, cells in enumerate(reader, 2):
            if not cells:
                continue
            # Editors strip trailing tabs, so empty truth, want and words cells may be missing.
            if not 4 <= len(cells) <= len(COLUMNS):
                raise EvalError(f"{path}:{n}: {len(cells)} cells, want {len(COLUMNS)}")
            cells += [""] * (len(COLUMNS) - len(cells))
            id_, kind, expect, text, truth, want, words = (c.strip() for c in cells)
            out.append(
                question(
                    id_, kind, expect, text, truth, want, words, where=f"{path}:{n}"
                )
            )
    if len({q.id for q in out}) != len(out):
        raise EvalError(f"{path}: ids repeat")
    return out


# --- the ground truth -------------------------------------------------------------------


def value(number: object) -> str:
    """A number as the scorer compares it (fast_lane.value: no commas or trailing zeros)."""
    if isinstance(number, float) and number.is_integer():
        number = int(number)
    return fast_lane.value(str(number))


def exact(numerator: int, denominator: int) -> str:
    """``numerator / denominator`` written out exactly (ticks to seconds, basis points to %)."""
    return value(format(Decimal(numerator) / Decimal(denominator), "f"))


@dataclass(frozen=True)
class Slot:
    """One input or output of a recipe row, as an answer may state it."""

    part: str  # in or out
    keys: frozenset[
        str
    ]  # the shown item, its accepted alternatives, its oredict's members
    names: frozenset[str]  # the names it is shown by (the item, its alternatives)
    amount: str
    chance: str  # percent, exactly (100: certain)
    alts: int  # other stacks it accepts besides the one shown
    ore: str | None  # the oredict whose items fit it


@dataclass
class Row:
    """A row a citation names, with what it states: a recipe of the newest run or a machine."""

    cite: str  # r:<map>/<sig12> or mte:<id>
    tier: int | None = None  # a recipe's voltage tier (None: crafting and the like)
    flags: frozenset[str] = frozenset()  # fake? (the handler doesn't say), recycling
    fields: dict[str, set[str]] = field(default_factory=dict)  # want -> its values
    numbers: set[str] = field(default_factory=set)  # every number the row holds
    names: set[str] = field(default_factory=set)  # item, fluid, machine and map names
    slots: list[Slot] = field(default_factory=list)

    def add(self, want: str, *values: object) -> None:
        """Values the row states for ``want``; their numbers are the row's numbers too (a
        fact's formula "8*vt" states 8)."""
        for v in values:
            if v is None:
                continue
            text = value(v) if isinstance(v, int | float) else str(v)
            self.fields.setdefault(want, set()).add(text)
            self.numbers |= {fast_lane.value(n) for n in fast_lane.NUMBER.findall(text)}

    def name(self, want: str, names: Iterable[str]) -> None:
        """Names the row goes by for ``want`` (a machine, a tier): text, not numbers."""
        found = {n for n in names if n}
        self.fields.setdefault(want, set()).update(found)
        if want == "machine":
            self.names |= found


def _lowest(tier: int | None) -> int:
    return -1 if tier is None else tier  # no tier (crafting, smelting) is the lowest


@dataclass
class Truth:
    """The rows one step means (by cite) and the counts an answer may give for them."""

    step: Step
    rows: dict[
        str, Row
    ]  # the rows an answer may cite: those without a flag when any exist
    counts: set[str] = field(default_factory=set)  # flagged rows included, per tier cap
    flagged: dict[str, Row] = field(
        default_factory=dict
    )  # left out: unflagged rows exist

    @property
    def all(self) -> dict[str, Row]:
        """Every row the step matches, as the tools list them."""
        return {**self.rows, **self.flagged}

    def simplest(self) -> set[str]:
        """The rows at the step's lowest tier (no tier first): plan 7.3's simplest recipe."""
        low = min((_lowest(r.tier) for r in self.rows.values()), default=None)
        return {c for c, r in self.rows.items() if _lowest(r.tier) == low}


def _tier_counts(rows: Iterable[Row]) -> set[str]:
    """How many rows there are, and how many at each tier cap (a lookup with ``tier_max``)."""
    tiers = [r.tier for r in rows]
    counts = {value(len(tiers))}
    for cap in range(len(TIERS)):
        counts.add(value(sum(1 for t in tiers if t is None or t <= cap)))
    return counts


_RECIPE = """
SELECT r.id, r.ref, r.eut, r.amperage, r.duration_ticks, r.voltage_tier, r.special_value,
  r.fake, coalesce(r.category, '') LIKE 'gt.recipe.category.%recycling' AS recycling,
  m.name_en AS map_name, h.name_en AS handler_name, m.unloc_name, h.handler_name AS handler,
  r.recipe_map_id
FROM recipe AS r
LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id
LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id
WHERE r.run_id = ? AND r.ref = ?
"""
_SLOTS = """
SELECT 'in' AS part, s.slot, s.amount, s.chance_bp, s.circuit, s.item_id, s.fluid_id,
  s.ore_id, o.name AS ore, coalesce(i.name_en, f.name_en) AS name
FROM recipe_input AS s
LEFT JOIN item AS i ON i.id = s.item_id
LEFT JOIN fluid AS f ON f.id = s.fluid_id
LEFT JOIN ore AS o ON o.id = s.ore_id
WHERE s.recipe_id = ?
UNION ALL
SELECT 'out', s.slot, s.amount, s.chance_bp, NULL, s.item_id, s.fluid_id, NULL, NULL,
  coalesce(i.name_en, f.name_en)
FROM recipe_output AS s
LEFT JOIN item AS i ON i.id = s.item_id
LEFT JOIN fluid AS f ON f.id = s.fluid_id
WHERE s.recipe_id = ?
"""
_ALTS = """
SELECT a.slot, a.item_id, a.fluid_id, coalesce(i.name_en, f.name_en) AS name
FROM recipe_input_alt AS a
LEFT JOIN item AS i ON i.id = a.item_id
LEFT JOIN fluid AS f ON f.id = a.fluid_id
WHERE a.recipe_id = ?
"""


class Ground:
    """The DB the questions are scored against, read-only (``ground.close()`` when done)."""

    def __init__(self, db: Path) -> None:
        if not db.is_file():
            raise FileNotFoundError(f"no DB at {db}: run gos db build")
        self.conn = access.connect(db, readonly=True)
        try:
            run = access.query(self.conn, "current_run")
            if not run:
                raise LookupError(f"{db} has no export run: run gos db build")
            self.run_id: int = run[0]["id"]
            self.run_key: str = run[0]["run_key"]
        except BaseException:
            self.conn.close()
            raise
        self._rows: dict[str, Row | None] = {}
        self._keys: dict[str, tuple[list[int], int | None, list[int]]] = {}

    def close(self) -> None:
        self.conn.close()

    # keys -> ids

    def ids(self, key: str) -> tuple[list[int], int | None, list[int]]:
        """(item ids: the item and its any-meta row; fluid id; ids of the oredicts they are
        in) for an item or fluid key; an item the DB doesn't have gives no ids."""
        if key in self._keys:
            return self._keys[key]
        items: list[int] = []
        fluid = None
        if m := FLUID_KEY.fullmatch(key):
            row = self.conn.execute(
                "SELECT id FROM fluid WHERE name = ?", (m["name"],)
            ).fetchone()
            fluid = row[0] if row else None
        if m := ITEM_KEY.fullmatch(key):
            nbt = m["nbt"] or ""
            row = self.conn.execute(
                "SELECT id FROM item WHERE registry_name = ? AND meta = ? AND "
                "(nbt_hash = ? OR (? != '' AND substr(nbt_hash, 1, length(?)) = ?))",
                (m["reg"], int(m["meta"]), nbt, nbt, nbt, nbt),
            ).fetchone()
            if row:
                items.append(row[0])
                if not nbt and int(m["meta"]) != WILDCARD:
                    wild = self.conn.execute(
                        "SELECT id FROM item WHERE registry_name = ? AND meta = ? AND "
                        "nbt_hash = ''",
                        (m["reg"], WILDCARD),
                    ).fetchone()
                    items += [wild[0]] if wild else []
        ores = [
            r[0]
            for r in self.conn.execute(
                f"SELECT DISTINCT ore_id FROM ore_member WHERE item_id IN "
                f"({','.join('?' * len(items))})",
                items,
            )
        ]
        self._keys[key] = (items, fluid, ores)
        return self._keys[key]

    def _takes(self, key: str) -> tuple[str, list[object]]:
        """SQL for the recipe ids that take ``key`` (shown, any meta, oredict, alternative)."""
        items, fluid, ores = self.ids(key)
        marks = ",".join("?" * len(items)) or "NULL"
        ore_marks = ",".join("?" * len(ores)) or "NULL"
        sql = (
            f"SELECT recipe_id FROM recipe_input WHERE item_id IN ({marks}) "
            "UNION SELECT recipe_id FROM recipe_input WHERE fluid_id = ? "
            f"UNION SELECT recipe_id FROM recipe_input WHERE ore_id IN ({ore_marks}) "
            f"UNION SELECT recipe_id FROM recipe_input_alt WHERE item_id IN ({marks}) "
            "UNION SELECT recipe_id FROM recipe_input_alt WHERE fluid_id = ?"
        )
        return sql, [*items, fluid, *ores, *items, fluid]

    def _makes(self, key: str) -> tuple[str, list[object]]:
        items, fluid, _ = self.ids(key)
        item = items[0] if items else None
        sql = (
            "SELECT recipe_id FROM recipe_output WHERE item_id = ? "
            "UNION SELECT recipe_id FROM recipe_output WHERE fluid_id = ?"
        )
        return sql, [item, fluid]

    # steps -> rows

    def truth(self, step: Step) -> Truth:
        if step.op == MACHINE:
            row = self.row(step.key)
            rows = {row.cite: row} if row is not None else {}
            return Truth(step, rows, {value(len(rows))})
        where = ["r.run_id = ?", "r.enabled = 1", "coalesce(r.fake, 0) = 0"]
        args: list[object] = [self.run_id]
        sql, part = (self._makes if step.op == MAKE else self._takes)(step.key)
        where.append(f"r.id IN ({sql})")
        args += part
        for name, val in step.filters:
            if name == "map":
                where.append(
                    "(r.recipe_map_id IN (SELECT id FROM recipe_map WHERE unloc_name = ?) "
                    "OR r.nei_handler_id IN "
                    "(SELECT id FROM nei_handler WHERE handler_name = ?))"
                )
                args += [val, val]
            elif name == "tier":
                where.append("(r.voltage_tier IS NULL OR r.voltage_tier <= ?)")
                args.append(TIERS.index(val))
            elif name == "in":
                sql, part = self._takes(val)
                where.append(f"r.id IN ({sql})")
                args += part
            elif name == "out":
                sql, part = self._makes(val)
                where.append(f"r.id IN ({sql})")
                args += part
            elif name == "ore":
                where.append(
                    "r.id IN (SELECT recipe_id FROM recipe_input WHERE ore_id IN "
                    "(SELECT id FROM ore WHERE name = ?))"
                )
                args.append(val)
        refs = [
            r[0]
            for r in self.conn.execute(
                f"SELECT r.ref FROM recipe AS r WHERE {' AND '.join(where)} ORDER BY r.ref",
                args,
            )
        ]
        found = {ref: row for ref in refs if (row := self.row(ref)) is not None}
        plain = {c: r for c, r in found.items() if not r.flags}
        flagged = {c: r for c, r in found.items() if r.flags} if plain else {}
        return Truth(step, plain or found, _tier_counts(found.values()), flagged)

    def row(self, cite: str) -> Row | None:
        """The row ``cite`` names (a recipe of the newest run, or a machine), or None."""
        if cite not in self._rows:
            if cite.startswith("r:"):
                self._rows[cite] = self._recipe(cite)
            elif m := MTE_KEY.fullmatch(cite):
                self._rows[cite] = self._machine(int(m["mte"]))
            else:
                self._rows[cite] = None
        return self._rows[cite]

    def _key_of(self, item_id: int | None, fluid_id: int | None) -> set[str]:
        """The keys a slot's item or fluid goes by (an any-meta row matches any meta)."""
        out = set()
        if item_id is not None:
            row = self.conn.execute(
                "SELECT registry_name, meta FROM item WHERE id = ?", (item_id,)
            ).fetchone()
            if row is not None:
                out.add(f"{row[0]}@{row[1]}")
        if fluid_id is not None:
            row = self.conn.execute(
                "SELECT name FROM fluid WHERE id = ?", (fluid_id,)
            ).fetchone()
            if row is not None:
                out.add(f"fluid:{row[0]}")
        return out

    def _recipe(self, ref: str) -> Row | None:
        r = self.conn.execute(_RECIPE, (self.run_id, ref)).fetchone()
        if r is None:
            return None
        flags = {"fake?"} if r["fake"] is None else set()
        flags |= {"recycling"} if r["recycling"] else set()
        row = Row(ref, r["voltage_tier"], frozenset(flags))
        row.add("eut", r["eut"])
        row.add("amps", r["amperage"])
        if r["duration_ticks"] is not None:
            seconds = exact(r["duration_ticks"], 20)
            row.add("dur", r["duration_ticks"], seconds)
            row.add("dur_t", r["duration_ticks"])
            row.add("dur_s", seconds)
        if r["voltage_tier"] is not None and 0 <= r["voltage_tier"] < len(TIERS):
            row.name("tier", [TIERS[r["voltage_tier"]]])
        if r["special_value"]:
            row.numbers.add(value(r["special_value"]))
        # Every name the recipe tools may show for its machine: the map's or handler's NEI
        # name, a machine that runs the map, else the map's or handler's own id.
        machines = {r["map_name"], r["handler_name"], r["unloc_name"], r["handler"]}
        if r["recipe_map_id"] is not None:
            machines |= {
                m[0]
                for m in self.conn.execute(
                    "SELECT mc.name_en FROM machine_map AS mm JOIN machine AS mc "
                    "ON mc.id = mm.machine_id WHERE mm.recipe_map_id = ?",
                    (r["recipe_map_id"],),
                )
            }
        machines.discard(None)
        row.name("machine", {clean(m) for m in machines})
        alts: dict[int, list[tuple[int | None, int | None, str]]] = {}
        for a in self.conn.execute(_ALTS, (r["id"],)):
            alts.setdefault(a["slot"], []).append(
                (a["item_id"], a["fluid_id"], clean(a["name"]))
            )
        for s in self.conn.execute(_SLOTS, (r["id"], r["id"])):
            row.slots.append(self._slot(row, s, alts.get(s["slot"], [])))
        for m in self.conn.execute(
            "SELECT key, value_num, value_text FROM recipe_meta WHERE recipe_id = ? "
            "AND key NOT IN ('nei_desc', 'can_be_buffered')",
            (r["id"],),
        ):
            got = m["value_text"] if m["value_num"] is None else m["value_num"]
            row.add(f"cond={m['key']}", got)
        return row

    def _slot(
        self,
        row: Row,
        s: sqlite3.Row,
        alts: list[tuple[int | None, int | None, str]],
    ) -> Slot:
        """One slot of a recipe row, its values added to the row's fields and numbers."""
        shown = (s["item_id"], s["fluid_id"])
        keys = self._key_of(*shown)
        names = {clean(s["name"])}
        others = 0
        if s["part"] == "in":
            for item_id, fluid_id, name in alts:
                keys |= self._key_of(item_id, fluid_id)
                names.add(name)
                others += (item_id, fluid_id) != shown  # stacks besides the one shown
            if s["ore_id"] is not None:
                keys |= {
                    k
                    for m in self.conn.execute(
                        "SELECT item_id FROM ore_member WHERE ore_id = ?",
                        (s["ore_id"],),
                    )
                    for k in self._key_of(m[0], None)
                }
                # A name, not a number: "wireGt01AnyCopper" states no 1.
                row.fields.setdefault(f"ore={s['ore']}", set()).add(s["ore"])
                row.names.add(s["ore"])
            if s["circuit"] is not None:
                row.numbers.add(value(s["circuit"]))
        names.discard("")
        row.names |= names
        amount, chance = value(s["amount"]), exact(s["chance_bp"], 100)
        row.numbers |= {amount, chance}
        if others:
            row.numbers.add(value(others))
        for key in keys:
            row.add(f"{s['part']}={key}", s["amount"])
            if s["part"] == "out":
                row.add(f"chance={key}", chance)
            elif others:
                row.add(f"alts={key}", others)
        return Slot(
            s["part"],
            frozenset(keys),
            frozenset(names),
            amount,
            chance,
            others,
            s["ore"],
        )

    def _machine(self, mte: int) -> Row | None:
        m = self.conn.execute(
            "SELECT m.name_en, m.tier, i.name_en AS item_name FROM machine AS m "
            "LEFT JOIN item AS i ON i.id = m.item_id WHERE m.mte_id = ?",
            (mte,),
        ).fetchone()
        if m is None:
            return None
        row = Row(f"mte:{mte}")
        row.name("machine", [clean(m["name_en"]), clean(m["item_name"])])
        if m["tier"] is not None and 0 <= m["tier"] < len(TIERS):
            row.name("tier", [TIERS[m["tier"]]])
        for name in access.query(self.conn, "machine_maps", {"mte_id": mte}):
            row.names |= {name["name"], name["map"]}
        # One fact per key, as the planner and machine_info pick it (D109).
        for f in access.query(self.conn, "machine_facts", {"mte_id": mte}):
            row.add(f"fact={f['key']}", f["value"])
            if f["confidence"] is not None:
                row.numbers.add(value(f["confidence"]))
        return row


# --- numbers in context -----------------------------------------------------------------

# The unit after a number, in this order (EU/t before t; "N others" and "N recipes" are words).
UNITS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "eut",
        re.compile(r"\s*EU\s*(?:/\s*t(?:ick)?|per\s+tick|a\s+tick)\b", re.IGNORECASE),
    ),
    ("pct", re.compile(r"\s*(?:%|per\s*cent\b)", re.IGNORECASE)),
    ("amps", re.compile(r"\s*(?:A\b|(?i:amps?|amperes?|amperage)\b)")),
    ("dur_t", re.compile(r"\s*(?:ticks?|t)\b", re.IGNORECASE)),
    ("dur_s", re.compile(r"\s*(?:s|secs?|seconds?)\b", re.IGNORECASE)),
    (
        "amount",
        re.compile(r"\s*(?:L|mB|litres?|liters?|x|×)(?![A-Za-z])", re.IGNORECASE),
    ),
    (
        "alts",
        re.compile(r"\s+(?:others?|alternatives?|other\s+[A-Za-z]+)\b", re.IGNORECASE),
    ),
    (
        "count",
        re.compile(
            r"\s+(?:[A-Za-z]+\s+)?(?:recipes?|ways)\b|\s+in\s+all\b", re.IGNORECASE
        ),
    ),
)
# The key before a number ("eut: 120", "coil_heat=1000"), as the skill's detail lines write it
_LABEL = re.compile(r"([A-Za-z_]+)\s*[:=]\s*$")
_LABEL_ROLE = {
    "eut": "eut",
    "amps": "amps",
    "amperage": "amps",
    "dur_t": "dur_t",
    "ticks": "dur_t",
    "dur_s": "dur_s",
    "seconds": "dur_s",
    "chance": "pct",
}
# Where a number's clause ends: a list or line break, or a sentence's full stop
_CLAUSE = re.compile(r"[;,\n]|\.(?=\s|$)")
# A reply that says no recipe makes the item (a none row)
NONE_SAID = re.compile(
    r"\bno (?:\w+ )?recipes?\b|\bnot in the (?:db|database)\b|\bnothing in the "
    r"(?:db|database)\b|\b(?:can't|cannot|can not) be (?:made|crafted)\b|\bno way to "
    r"(?:make|craft)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Mention:
    """A number in an answer, with what it is said as."""

    value: str  # as compared (fast_lane.value)
    role: str  # eut, pct, amps, dur_t, dur_s, amount, alts, count, or other (a plain number)
    label: str  # the key before it ("eut", "coil_heat"), lower case, or ""
    before: str  # its clause before it, back to the previous number
    after: str  # its clause after its unit, up to the next number


def mentions(text: str) -> list[Mention]:
    """The numbers of ``text`` (citations, keys and names with digits already out)."""
    found = list(fast_lane.NUMBER.finditer(text))
    out = []
    start = 0  # the end of the previous number's unit
    for i, m in enumerate(found):
        role, end = "other", m.end()
        for name, unit in UNITS:
            if u := unit.match(text, m.end()):
                role, end = name, u.end()
                break
        head = text[start : m.start()]
        label = ""
        if lm := _LABEL.search(head):
            label = lm[1].casefold()
            if role == "other":
                role = _LABEL_ROLE.get(label, "other")
        after = text[
            end : max(end, found[i + 1].start() if i + 1 < len(found) else len(text))
        ]
        if c := _CLAUSE.search(after):
            after = after[: c.start()]
        before = head
        if cuts := list(_CLAUSE.finditer(before)):
            before = before[cuts[-1].end() :]
        out.append(Mention(fast_lane.value(m[0]), role, label, before, after))
        start = end
    return out


def _mask(text: str, names: Iterable[str]) -> tuple[str, dict[str, str]]:
    """``text`` with the names it quotes that have digits or a tier in them ("1x Copper
    Wire", "Programmed Circuit 1", "Electric Motor (LV)") replaced by tokens without either,
    so their digits aren't numbers and their tiers aren't stated tiers; and the tokens."""
    tokens: dict[str, str] = {}
    marked = {n for n in names if n and (_DIGIT.search(n) or _tiers(n))}
    for i, name in enumerate(sorted(marked, key=len, reverse=True)):
        token = f" {chr(0xE100 + i)} "
        text, hits = re.subn(re.escape(name), token, text, flags=re.IGNORECASE)
        if hits:
            tokens[name] = token.strip()
    return text, tokens


def _tiers(text: str) -> set[str]:
    return {t for t, word in TIER_WORD.items() if word.search(text)}


def _nearest(
    window: str, names: Iterable[str], tokens: dict[str, str], last: bool
) -> str:
    """The name in ``window`` nearest the number: the first one (after it) or the last one
    (before it); the longer name when two start or end at the same place."""
    folded = window.casefold()
    best, where = "", None
    for name in names:
        needle = tokens.get(name, name.casefold())
        at = folded.rfind(needle) if last else folded.find(needle)
        if at < 0:
            continue
        pos = (-(at + len(needle)), -len(needle)) if last else (at, -len(needle))
        if where is None or pos < where:
            best, where = name, pos
    return best


# --- scoring ----------------------------------------------------------------------------


@dataclass
class Score:
    passed: bool
    why: str = ""
    uncited: list[str] = field(default_factory=list)  # numbers no cited row backs
    cited: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)  # cited, real, but no step's row


def _role_values(rows: Iterable[Row], role: str) -> set[str]:
    """What cited rows hold that a number said in ``role`` may be."""
    out: set[str] = set()
    for r in rows:
        if role in ("eut", "amps", "dur_t", "dur_s"):
            out |= r.fields.get(role, set())
        elif role == "pct":
            out |= {s.chance for s in r.slots}
        elif role == "amount":
            out |= {s.amount for s in r.slots}
        elif role == "alts":
            out |= {value(s.alts) for s in r.slots if s.alts}
        else:
            out |= r.numbers
    return out


def asks(reply: Reply, text: str) -> bool:
    """A clarifying question: the asker's (no model) or one the model wrote, uncited."""
    return reply.asked or ("?" in text and not fast_lane.refs(text))


class _Answer:
    """An answer as the scorer reads it: its words, numbers and the names around them."""

    def __init__(self, text: str, shown: set[str]) -> None:
        # Citations and keys first: a name with digits can be part of one ("r:ic2.nei.../...").
        self.bare = fast_lane.KEY.sub(" ", fast_lane.REF.sub(" ", text))
        self.masked, self.tokens = _mask(self.bare, shown)
        self.said = mentions(self.masked)
        self.stated = {m.value for m in self.said}
        self.tiers = _tiers(self.masked)

    def owner(self, m: Mention, names: Iterable[str]) -> str:
        """The name a number is about: the nearest after it in its clause, else before it."""
        names = list(names)
        return _nearest(m.after, names, self.tokens, False) or _nearest(
            m.before, names, self.tokens, True
        )

    def near(self, m: Mention, key: str) -> bool:
        """Whether a condition's or fact's key is said next to the number."""
        forms = {key.casefold(), key.replace("_", " ").casefold()}
        window = f"{m.label} {m.before} {m.after}".casefold()
        return any(f in window for f in forms)


def _slot_names(rows: Iterable[Row]) -> set[str]:
    return {n for r in rows for s in r.slots for n in s.names}


def _want(
    want: str, t: Truth, hits: list[Row], a: _Answer, cited_tiers: set[str]
) -> str:
    """Why the answer doesn't state ``want`` for ``t``'s step ('' when it does)."""
    step = t.step.text()
    name, _, key = want.partition("=")
    if want == "count":
        if any(m.role == "count" and m.value in t.counts for m in a.said):
            return ""
        return f"doesn't give the count (N recipes) for {step}"
    if want == "simplest":
        if any(r.cite in t.simplest() for r in hits):
            return ""
        return f"doesn't give the simplest recipe (lowest tier, no flag) of {step}"
    values = set().union(*(r.fields.get(want, set()) for r in hits))
    if not values:
        return f"no cited row of {step} has {want}"
    shown = ", ".join(sorted(values)[:3])
    if want in ("eut", "amps"):
        ok = any(m.role == want and m.value in values for m in a.said)
    elif want == "dur":
        ticks = set().union(*(r.fields.get("dur_t", set()) for r in hits))
        secs = set().union(*(r.fields.get("dur_s", set()) for r in hits))
        ok = any(
            (m.role == "dur_t" and m.value in ticks)
            or (m.role == "dur_s" and m.value in secs)
            for m in a.said
        )
    elif want == "tier":
        if other := a.tiers - cited_tiers:
            return f"states a tier no cited row has: {', '.join(sorted(other))}"
        ok = bool(a.tiers & values)
    elif want == "machine":
        ok = any(v.casefold() in a.bare.casefold() for v in values if v)
    elif name == "ore":
        ok = key.casefold() in a.bare.casefold()
    elif name in SLOT_WANTS:
        part = "out" if name in ("out", "chance") else "in"
        slots = [s for r in hits for s in r.slots if s.part == part and key in s.keys]
        names = _slot_names(hits)
        ok = any(
            (
                (name in ("in", "out") and m.role in ("amount", "other"))
                and m.value == s.amount
                and a.owner(m, names) in s.names
            )
            or (
                name == "chance"
                and m.role == "pct"
                and m.value == s.chance
                and a.owner(m, names) in s.names
            )
            or (name == "alts" and m.role == "alts" and m.value == value(s.alts))
            for s in slots
            for m in a.said
        )
    else:  # cond= and fact=: the value next to its key, or a word value ("standard")
        numeric = {
            fast_lane.value(n) for v in values for n in fast_lane.NUMBER.findall(v)
        }
        if numeric:
            ok = any(m.value in numeric and a.near(m, key) for m in a.said)
        else:
            ok = any(v.casefold() in a.bare.casefold() for v in values)
    return "" if ok else f"doesn't state {want} ({shown})"


def score(
    q: Question,
    text: str,
    asked: bool,
    truths: Sequence[Truth],
    ground: Ground,
    names: Iterable[str] = (),
) -> Score:
    """Score ``text`` (the reply, or the deep lane's answer) for ``q``; ``names``: names the
    reply itself shows (its item, a question's candidates), masked like the rows'."""
    cited = list(dict.fromkeys(fast_lane.refs(text)))
    rows = {c: ground.row(c) for c in cited}
    unknown = [c for c, r in rows.items() if r is None and c.startswith(("r:", "mte:"))]
    real = {c: r for c, r in rows.items() if r is not None}
    known = {c for t in truths for c in t.rows}
    extra = [c for c in real if c not in known]
    shown = set(names)
    for r in real.values():
        shown |= r.names
    for t in truths:
        for c in t.all.keys() & real.keys():
            shown |= t.all[c].names
    a = _Answer(text, shown)
    counts: set[str] = set()
    for t in truths:
        if t.all.keys() & real.keys():
            counts |= t.counts
    uncited = sorted(
        {
            m.value
            for m in a.said
            if m.value
            not in (
                counts if m.role == "count" else _role_values(real.values(), m.role)
            )
        },
        key=lambda n: (len(n), n),
    )
    out = Score(False, uncited=uncited, cited=cited, extra=extra)

    def fail(why: str) -> Score:
        out.why = why
        return out

    if q.expect == ASK:
        if not asked:
            return fail("answered instead of asking which item")
        if a.stated:
            return fail(f"asked, but with numbers: {', '.join(sorted(a.stated))}")
        out.passed = True
        return out
    if asked:
        return fail("asked back instead of answering")
    if unknown:
        return fail(f"cites ids the DB doesn't have: {', '.join(unknown[:3])}")
    if q.expect == NONE:
        t = truths[0]
        if t.rows:
            return fail(
                f"the DB has {len(t.rows)} rows for {t.step.text()}: not a none row here"
            )
        if any(c.startswith("r:") for c in cited):
            return fail(f"cites recipes where none exists: {', '.join(cited[:3])}")
        if a.stated:
            return fail(
                f"numbers where no recipe exists: {', '.join(sorted(a.stated))}"
            )
        if not NONE_SAID.search(a.bare):
            return fail("doesn't say that no recipe in the DB makes it")
        out.passed = True
        return out
    cited_tiers = set().union(*(r.fields.get("tier", set()) for r in real.values()))
    for t in truths:
        hits = [t.rows[c] for c in cited if c in t.rows]
        if not t.rows:
            return fail(f"the DB has no row for {t.step.text()}")
        if not hits:
            if flagged := [c for c in cited if c in t.flagged]:
                return fail(
                    f"cites only flagged rows of {t.step.text()} ({flagged[0]}, "
                    f"{' '.join(sorted(t.flagged[flagged[0]].flags))}); "
                    f"{len(t.rows)} have no flag"
                )
            return fail(f"cites no row of {t.step.text()} ({len(t.rows)} in the DB)")
        for want in t.step.want:
            if why := _want(want, t, hits, a, cited_tiers):
                return fail(why)
    if uncited:
        return fail(f"uncited numbers: {', '.join(uncited[:5])}")
    out.passed = True
    return out


# --- the run ----------------------------------------------------------------------------


@dataclass
class Result:
    q: Question
    reply: Reply
    text: str  # what was scored: the reply's text, or the deep lane's answer
    lane: str
    score: Score
    deep_cost: float = 0.0


def _names(reply: Reply) -> set[str]:
    names = {(reply.target or {}).get("name", "")}
    card = reply.card or {}
    for c in card.get("candidates", []) if card.get("type") == "choices" else []:
        names.add(c.get("name", ""))
    return {n for n in names if n}


def _deep_answer(
    deep: DeepLaneService, before: set[str], wait_s: float
) -> tuple[str, float, str | None]:
    """(the answers of the tasks this row handed off, their cost, why none answered)."""

    texts, cost, why = [], 0.0, None
    for task in deep.tasks():
        if task.id in before:
            continue
        found = deep.wait(task.id, wait_s)
        if found is not None and found.status == NEEDS_APPROVAL:
            deep.stop_task(found.id)  # never approve in an eval (D157)
            found = deep.wait(found.id, 30)
            why = "the deep-lane task reached its cap and was stopped"
        if found is None:
            why = why or "the deep-lane task didn't finish"
            continue
        cost += found.cost_usd
        if found.text:
            texts.append(found.text)
        elif found.status != DONE:
            why = why or f"deep lane: {found.status}: {found.error or ''}".strip()
    return "\n\n".join(texts), cost, why


def run(
    asker_for: Callable[[], Asker],
    questions: Sequence[Question],
    ground: Ground,
    out: TextIO,
    *,
    fake: bool,
    deep: DeepLaneService | None = None,
    wait_s: float = DEEP_WAIT_S,
) -> tuple[int, list[Result]]:
    """Ask and score every question (``asker_for()`` makes a fresh :class:`Asker` per row);
    ``deep``: the deep lane the askers hand off to, waited for (None: a handoff fails)."""
    results: list[Result] = []
    print(f"# DB run {ground.run_key}", file=out)
    print("id\tkind\tlane\tpass\tuncited\tcited\textra\twhy\ttext", file=out)
    for q in questions:
        before = {t.id for t in deep.tasks()} if deep is not None else set()
        reply = asker_for().ask(q.question, task=TASK)
        text, lane, cost, why = reply.text, reply.lane, 0.0, None
        if reply.handoff is not None:
            if deep is None:  # the answer never comes: score nothing
                text, why = "", "handed to the deep lane, which doesn't run here"
            else:
                text, cost, why = _deep_answer(deep, before, wait_s)
                lane = "deep"
        truths = [ground.truth(s) for s in q.steps]
        result = score(q, text, asks(reply, text), truths, ground, _names(reply))
        if why and not text:
            result.passed, result.why = False, why
        results.append(Result(q, reply, text, lane, result, cost))
        shown = text.replace("\t", " ").replace("\n", " ")[:200]
        print(
            f"{q.id}\t{q.kind}\t{lane}\t{int(result.passed)}\t{len(result.uncited)}\t"
            f"{','.join(result.cited[:4])}\t{','.join(result.extra[:4])}\t{result.why}\t"
            f"{shown}",
            file=out,
        )
    return summary(results, out, fake=fake), results


def summary(results: Sequence[Result], out: TextIO, *, fake: bool) -> int:
    n = len(results)
    correct = sum(r.score.passed for r in results)
    uncited = sum(len(r.score.uncited) for r in results)
    share = correct / n if n else 0.0
    print(file=out)
    print(
        f"correct: {correct} of {n} ({share:.1%}; target >= {CORRECT_TARGET:.0%})",
        file=out,
    )
    print(f"uncited numbers: {uncited} (target 0)", file=out)
    extra = sum(len(r.score.extra) for r in results)
    print(
        f"extra citations (real ids no step means; reported, not failed): {extra}",
        file=out,
    )
    by_kind: Counter[str] = Counter()
    ok_kind: Counter[str] = Counter()
    for r in results:
        by_kind[r.q.kind] += 1
        ok_kind[r.q.kind] += r.score.passed
    print(
        "by kind: "
        + ", ".join(f"{k} {ok_kind[k]}/{by_kind[k]}" for k in KINDS if by_kind[k]),
        file=out,
    )
    lanes = Counter(r.lane for r in results)
    print("lanes: " + ", ".join(f"{k} {v}" for k, v in sorted(lanes.items())), file=out)
    calls = sum(r.reply.calls for r in results)
    usd = sum(r.reply.usd + r.deep_cost for r in results)
    print(
        f"fast-lane model calls: {calls}; cost: ${usd:.6f}"
        + (" (simulated: the fake model)" if fake else ""),
        file=out,
    )
    ok = n > 0 and share >= CORRECT_TARGET and uncited == 0
    print("Result: " + ("PASS" if ok else "FAIL"), file=out)
    return 0 if ok else 1


def write_table(results: Sequence[Result], path: Path) -> None:
    """The per-row report as TSV (for a PR or the PC run's record)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ["id", "kind", "lane", "pass", "uncited", "cited", "extra", "why", "text"]
        )
        for r in results:
            writer.writerow(
                [
                    r.q.id,
                    r.q.kind,
                    r.lane,
                    int(r.score.passed),
                    " ".join(r.score.uncited),
                    " ".join(r.score.cited),
                    " ".join(r.score.extra),
                    r.score.why,
                    " ".join(r.text.split()),
                ]
            )


def main(
    config_path: Path | None,
    questions_path: Path | None,
    fake: bool,
    limit: int | None,
    table_path: Path | None,
    out: TextIO,
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        questions = load(questions_path or QUESTIONS)
    except (OSError, EvalError) as exc:
        print(f"Can't read the questions: {exc}", file=out)
        return 2
    if limit is not None:
        questions = questions[:limit]
    try:
        ground = Ground(access.db_path(cfg.gos_home))
    except (OSError, LookupError, sqlite3.Error, access.SchemaError) as exc:
        print(f"Not run: {exc}", file=out)
        return 1
    try:
        if fake:
            from gregtec.agents import recipe_oracle

            code, results = recipe_oracle.run_fake(cfg, questions, ground, out)
        else:
            code, results = _run_real(cfg, questions, ground, out)
    finally:
        ground.close()
    if table_path is not None and results:
        write_table(results, table_path)
        print(f"# table written to {table_path}", file=out)
    return code


def _run_real(
    cfg: config.Config, questions: Sequence[Question], ground: Ground, out: TextIO
) -> tuple[int, list[Result]]:
    """The real models: the stored key, ``llm_usage`` task ``eval:recipe-lookup``."""

    agents = setup(cfg)
    try:
        fast = agents.lane()
        if fast is None:
            print(
                "Not run: no Anthropic key is stored in this account (uv run gos key set "
                "anthropic), or use --fake.",
                file=out,
            )
            return 1, []
        deep = agents.deep if isinstance(agents.deep, DeepLaneService) else None
        if deep is not None:
            deep.task_label = TASK
        print(
            f"# {dt.datetime.now().isoformat(timespec='seconds')}: real models",
            file=out,
        )
        return run(
            lambda: Asker(agents.registry, agents.deep, fast),
            questions,
            ground,
            out,
            fake=False,
            deep=deep,
        )
    finally:
        agents.close()


def plans(questions: Iterable[Question]) -> dict[str, Question]:
    """The questions by their text as a lane receives it (whitespace collapsed)."""
    return {" ".join(q.question.split()): q for q in questions}
