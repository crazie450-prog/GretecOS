"""``gos eval recipe-lookup`` (WO-2.7 acceptance, D171): 50 recipe and item questions.

Each row of ``tests/evals/recipe_lookup.tsv`` (``id kind expect question truth want``) is asked
through :class:`gregtec.agents.ask.Asker` as ``POST /api/ask`` asks it (router, instant
commands, pre-retrieval, fast lane; a request the router or the fast lane hands to the deep lane
is waited for there), one fresh conversation per row, and scored against the DB itself:

- **Ground truth from the DB.** ``truth`` holds references to DB rows, never game numbers: steps
  such as ``make gregtech:gt.metaitem.01@11305 map=gt.recipe.blastfurnace`` (the newest run's
  enabled, non-fake recipes that output that item in that map), ``use <key>`` (recipes that take
  it: shown, any meta, an oredict it is in, or an accepted alternative) and ``machine mte:<id>``
  (a GT machine and its facts); filters ``map=``, ``tier=``, ``in=``, ``out=`` and ``ore=``
  narrow a step, and ``;`` separates the steps of a multi-step question. :class:`Ground` turns
  each step into the set of rows it means, with every number each row holds, by its own SQL
  over the tables (not through the tools, whose answers are what is being checked). ``want``
  names, per step, what the answer must state: ``eut``, ``amps``, ``dur`` (ticks or seconds),
  ``tier``, ``machine``, ``count`` (how many rows), ``in=<key>``/``out=<key>`` (an amount),
  ``chance=<key>``, ``cond=<key>`` (a recipe condition) or ``fact=<key>`` (a machine fact).
- **Correct** (``expect`` answer): the reply answers (doesn't ask back), cites at least one row
  of every step (``r:`` or ``mte:``), every id it cites exists in the DB, every wanted value is
  stated as a cited row of its step holds it, and no number is uncited. ``ask``: the reply is a
  clarifying question (QA:D11) and states no number. ``none``: no recipe in the DB matches the
  step (the row is checked on this DB), and the reply cites none and states no number.
- **Uncited numbers** (WO-2.3's number check, D152, read per number): every number in the text,
  less citations, DB keys and the digits of item, fluid and machine names the rows hold, must be
  a value of a row the text cites (an amount, chance, EU/t, amperage, ticks or seconds,
  condition or fact), or the count of a step whose rows it cites. Any other number is uncited,
  so an answer with numbers and no id at all counts all of them.

The report gives each row's lane, pass, uncited numbers, citations and why, then ``correct: N
of M`` (plan 8: at least 95%) and ``uncited numbers: N`` (plan 8: 0), by kind and lane; exit 0
when both targets hold. ``--fake`` runs :mod:`gregtec.agents.recipe_oracle`: an oracle fast-lane
model and, behind the real Claude Code CLI, an oracle deep-lane model that loads the
recipe-lookup skill and calls the tools it names; no key, no cost, nothing recorded. That checks
the logic (routing, tools, truth, scoring), not a model. The real run (stored key; ``llm_usage``
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
from gregtec.agents.tools.items import FLUID_KEY, ITEM_KEY, MTE_KEY, WILDCARD
from gregtec.agents.tools.recipes import TIERS
from gregtec.db import access

REPO_ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = REPO_ROOT / "tests" / "evals" / "recipe_lookup.tsv"
COLUMNS = ("id", "kind", "expect", "question", "truth", "want")
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
PLAIN_WANTS = ("eut", "amps", "dur", "tier", "machine", "count")
KEYED_WANTS = ("in", "out", "chance", "alts", "cond", "fact")
SLOT_WANTS = ("in", "out", "chance", "alts")  # keyed by an item or fluid key
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


def is_key(text: str) -> bool:
    """An item or fluid key as the tools write them (spaces aren't allowed in this file)."""
    return " " not in text and bool(
        ITEM_KEY.fullmatch(text) or FLUID_KEY.fullmatch(text)
    )


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
        name, sep, value = w.partition("=")
        if sep:
            ok = name in KEYED_WANTS and bool(value)
            ok = ok and (name != "fact") == (op != MACHINE)
            ok = ok and (name not in SLOT_WANTS or is_key(value))
        else:
            ok = w in PLAIN_WANTS and (op != MACHINE or w in ("tier", "machine"))
        if not ok:
            raise EvalError(f"{where}: {w!r} can't be wanted of a {op} step")
    return Step(op, key, tuple(filters), wants)


def question(
    id_: str,
    kind: str,
    expect: str,
    text: str,
    truth: str = "",
    want: str = "",
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
    return Question(id_, kind, expect, text.strip(), steps)


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
            # Editors strip trailing tabs, so empty truth and want cells may be missing.
            if not 4 <= len(cells) <= len(COLUMNS):
                raise EvalError(f"{path}:{n}: {len(cells)} cells, want {len(COLUMNS)}")
            cells += [""] * (len(COLUMNS) - len(cells))
            id_, kind, expect, text, truth, want = (c.strip() for c in cells)
            out.append(question(id_, kind, expect, text, truth, want, f"{path}:{n}"))
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


@dataclass
class Row:
    """A row a citation names, with what it states: a recipe of the newest run or a machine."""

    cite: str  # r:<map>/<sig12> or mte:<id>
    tier: int | None = None  # a recipe's voltage tier (None: crafting and the like)
    fields: dict[str, set[str]] = field(default_factory=dict)  # want -> its values
    numbers: set[str] = field(default_factory=set)  # every number the row holds
    names: set[str] = field(default_factory=set)  # item, fluid, machine and map names

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


@dataclass
class Truth:
    """The rows one step means (by cite) and the counts an answer may give for them."""

    step: Step
    rows: dict[str, Row]
    counts: set[str] = field(default_factory=set)


def _tier_counts(rows: Iterable[Row]) -> set[str]:
    """How many rows there are, and how many at each tier cap (a lookup with ``tier_max``)."""
    tiers = [r.tier for r in rows]
    counts = {value(len(tiers))}
    for cap in range(len(TIERS)):
        counts.add(value(sum(1 for t in tiers if t is None or t <= cap)))
    return counts


_RECIPE = """
SELECT r.id, r.ref, r.eut, r.amperage, r.duration_ticks, r.voltage_tier, r.special_value,
  m.name_en AS map_name, h.name_en AS handler_name, m.unloc_name, h.handler_name AS handler,
  r.recipe_map_id
FROM recipe AS r
LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id
LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id
WHERE r.run_id = ? AND r.ref = ?
"""
_SLOTS = """
SELECT 'in' AS part, s.slot, s.amount, s.chance_bp, s.circuit, s.item_id, s.fluid_id,
  s.ore_id, coalesce(i.name_en, f.name_en) AS name
FROM recipe_input AS s
LEFT JOIN item AS i ON i.id = s.item_id
LEFT JOIN fluid AS f ON f.id = s.fluid_id
WHERE s.recipe_id = ?
UNION ALL
SELECT 'out', s.slot, s.amount, s.chance_bp, NULL, s.item_id, s.fluid_id, NULL,
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
        rows = {}
        for ref in refs:
            row = self.row(ref)
            if row is not None:
                rows[ref] = row
        return Truth(step, rows, _tier_counts(rows.values()))

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
        row = Row(ref, r["voltage_tier"])
        row.add("eut", r["eut"])
        row.add("amps", r["amperage"])
        if r["duration_ticks"] is not None:
            row.add("dur", r["duration_ticks"], exact(r["duration_ticks"], 20))
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
        row.name("machine", machines)
        alts: dict[int, list[tuple[int | None, int | None]]] = {}
        for a in self.conn.execute(_ALTS, (r["id"],)):
            alts.setdefault(a["slot"], []).append((a["item_id"], a["fluid_id"]))
            if a["name"]:
                row.names.add(a["name"])
        for s in self.conn.execute(_SLOTS, (r["id"], r["id"])):
            shown = (s["item_id"], s["fluid_id"])
            keys = self._key_of(*shown)
            others = 0
            if s["part"] == "in":
                for alt in alts.get(s["slot"], []):
                    keys |= self._key_of(*alt)
                    others += (
                        alt != shown
                    )  # the stacks it accepts besides the one shown
                if s["ore_id"] is not None:
                    keys |= {
                        k
                        for m in self.conn.execute(
                            "SELECT item_id FROM ore_member WHERE ore_id = ?",
                            (s["ore_id"],),
                        )
                        for k in self._key_of(m[0], None)
                    }
                if s["circuit"] is not None:
                    row.numbers.add(value(s["circuit"]))
            if s["name"]:
                row.names.add(s["name"])
            chance = exact(s["chance_bp"], 100)
            row.numbers |= {value(s["amount"]), chance}
            if others:
                row.numbers.add(value(others))
            for key in keys:
                row.add(f"{s['part']}={key}", s["amount"])
                if s["part"] == "out":
                    row.add(f"chance={key}", chance)
                elif others:
                    row.add(f"alts={key}", others)
        for m in self.conn.execute(
            "SELECT key, value_num, value_text FROM recipe_meta WHERE recipe_id = ? "
            "AND key NOT IN ('nei_desc', 'can_be_buffered')",
            (r["id"],),
        ):
            got = m["value_text"] if m["value_num"] is None else m["value_num"]
            row.add(f"cond={m['key']}", got)
        return row

    def _machine(self, mte: int) -> Row | None:
        m = self.conn.execute(
            "SELECT m.name_en, m.tier, i.name_en AS item_name FROM machine AS m "
            "LEFT JOIN item AS i ON i.id = m.item_id WHERE m.mte_id = ?",
            (mte,),
        ).fetchone()
        if m is None:
            return None
        row = Row(f"mte:{mte}")
        row.name("machine", [m["name_en"], m["item_name"]])
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


# --- scoring ----------------------------------------------------------------------------


@dataclass
class Score:
    passed: bool
    why: str = ""
    uncited: list[str] = field(default_factory=list)  # numbers no cited row backs
    cited: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)  # cited, real, but no step's row


def _mask(text: str, names: Iterable[str]) -> str:
    """``text`` without the names it quotes that have digits in them ("1x Copper Wire",
    "Programmed Circuit 1"): their digits are part of a name, not numbers."""
    for name in sorted(
        {n for n in names if n and _DIGIT.search(n)}, key=len, reverse=True
    ):
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    return text


def _has(text: str, want: str, values: set[str], numbers: set[str]) -> bool:
    """Whether ``text`` states one of a cited row's ``values`` for ``want`` (words are read
    outside citations and keys: ``r:gt.recipe.blastfurnace/...`` doesn't name a machine)."""
    words = fast_lane.KEY.sub(" ", fast_lane.REF.sub(" ", text))
    if want == "tier":
        return any(TIER_WORD[v].search(words) for v in values if v in TIER_WORD)
    folded = words.casefold()
    if want == "machine":
        return any(v.casefold() in folded for v in values if v)
    hit = {fast_lane.value(n) for v in values for n in fast_lane.NUMBER.findall(v)}
    if hit:
        return bool(hit & numbers)
    # a fact that is a word ("heat", "standard")
    return any(v.casefold() in folded for v in values)


def asks(reply: Reply, text: str) -> bool:
    """A clarifying question: the asker's (no model) or one the model wrote, uncited."""
    return reply.asked or ("?" in text and not fast_lane.refs(text))


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
        for c in t.rows.keys() & real.keys():
            shown |= t.rows[c].names
    # Citations and keys first: a name with digits can be part of one ("r:ic2.nei.../...").
    bare = fast_lane.KEY.sub(" ", fast_lane.REF.sub(" ", text))
    stated = fast_lane.numbers(_mask(bare, shown), None)
    backed = set().union(*(r.numbers for r in real.values()))
    for t in truths:
        if t.rows.keys() & real.keys():
            backed |= t.counts
    uncited = sorted(stated - backed, key=lambda n: (len(n), n))
    out = Score(False, uncited=uncited, cited=cited, extra=extra)

    def fail(why: str) -> Score:
        out.why = why
        return out

    if q.expect == ASK:
        if not asked:
            return fail("answered instead of asking which item")
        if stated:
            return fail(f"asked, but with numbers: {', '.join(sorted(stated))}")
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
        if stated:
            return fail(f"numbers where no recipe exists: {', '.join(sorted(stated))}")
        out.passed = True
        return out
    for t in truths:
        hits = [t.rows[c] for c in cited if c in t.rows]
        if not t.rows:
            return fail(f"the DB has no row for {t.step.text()}")
        if not hits:
            return fail(f"cites no row of {t.step.text()} ({len(t.rows)} in the DB)")
        for want in t.step.want:
            if want == "count":
                if not t.counts & stated:
                    return fail(f"doesn't give the count for {t.step.text()}")
                continue
            values = set().union(*(r.fields.get(want, set()) for r in hits))
            if not values:
                return fail(f"no cited row of {t.step.text()} has {want}")
            if not _has(text, want, values, stated):
                return fail(f"doesn't state {want} ({', '.join(sorted(values)[:3])})")
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
    print("id\tkind\tlane\tpass\tuncited\tcited\twhy\ttext", file=out)
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
            f"{','.join(result.cited[:4])}\t{result.why}\t{shown}",
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
            ["id", "kind", "lane", "pass", "uncited", "cited", "why", "text"]
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
