"""``item_resolve`` and the item argument every recipe and quest tool takes (plan 4.4, QA:D11).

An item is named by text, which is resolved here, or by its key:

- ``registry@meta`` (``gregtech:gt.metaitem.01@11305``), with ``#<hex>`` for an NBT variant
  (the first 8 or more hex digits of its NBT hash; ``normalize.Ref.text``'s form);
- ``fluid:<FluidRegistry name>``;
- ``mte:<id>`` for a machine (a recipe tool takes the machine's item).

**Resolving text.** The text, then each glossary variant (:mod:`.glossary`), is searched in
``name_fts`` (named query ``item_names``: every word, the last one also as a prefix) and each
match is scored against that form: 1.0 when its name is the form's words exactly, else the share
of words they have in common (the form's words matched over all distinct words of both; the last
form word matching only the start of a name word counts half). A form that names a GT material
as a whole ("stainless steel", "steel") gives that material's ingot or gem +0.25 (at most 0.95):
said alone, a material means its ingot. The first form, in order, with a match of at least
:data:`STRONG` decides; matches found only through other forms count at :data:`DEMOTE` of their
score. When candidates share a name and a score and one of them is GregTech's, the others drop
by :data:`CLOSE`: GT unifies materials to its own items, so TConstruct's "Steel Ingot" next to
GT's doesn't make "steel ingot" ambiguous. Candidates are ranked by score, then item before
fluid, then FTS rank, name and key.

**Ambiguous** when nothing scores :data:`LOW` or more, or the second candidate is within
:data:`CLOSE` of the first. A tool that needs one item then returns the candidates instead of
answering, so the router (WO-2.3) asks "Steel or stainless steel?" and shows the top three.
Scores rank matches; they are not game numbers.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, replace
from typing import Any

from gregtec.agents.tools.glossary import Entry
from gregtec.agents.tools.registry import Call, ToolError
from gregtec.agents.tools.tsv import Result

STRONG = 0.9
LOW = 0.5
CLOSE = 0.15
DEMOTE = 0.8
MATERIAL_BONUS = 0.25
PREFIX_WEIGHT = 0.5  # the last word matching only the start of a name word
GREGTECH = "gregtech"
POOL = 300  # FTS matches rescored per form
MAX_WORDS = 10
WILDCARD = 32767
KINDS = ("item", "fluid", "machine")
_KIND_ORDER = {k: n for n, k in enumerate(KINDS)}
WORD = re.compile(r"[^\W_]+")
_FORMAT = re.compile("\u00a7.", re.DOTALL)
# Registry and fluid names can have spaces ("TConstruct:Clay Cast", "fluid:jet fuel a"); a key
# is looked up exactly, so a text that only looks like one finds nothing.
ITEM_KEY = re.compile(
    r"(?P<reg>[^@#\t\r\n]+:[^@#\t\r\n]+)@(?P<meta>[0-9]{1,9})(?:#(?P<nbt>[0-9a-f]{8,64}))?"
)
FLUID_KEY = re.compile(r"fluid:(?P<name>[^\t\r\n]{1,200})")
MTE_KEY = re.compile(r"mte:(?P<mte>[0-9]{1,9})")


@dataclass(frozen=True)
class Candidate:
    kind: str  # item, fluid or machine
    ref_id: int  # item.id, fluid.id or machine.id
    key: str
    name: str
    mod: str | None
    score: float
    form: str  # the text or glossary variant it matched
    rank: float = 0.0  # FTS bm25 (lower is better)

    def label(self) -> str:
        return f"{self.name} [{self.key}]" if self.name else f"[{self.key}]"


@dataclass(frozen=True)
class Resolution:
    text: str
    candidates: list[Candidate]
    ambiguous: bool
    via: Entry | None = None  # the glossary entry of the form that decided
    capped: bool = False  # a form had more FTS matches than POOL: more candidates exist

    @property
    def best(self) -> Candidate | None:
        if self.ambiguous or not self.candidates:
            return None
        return self.candidates[0]


def clean(name: str | None) -> str:
    """A display name without Minecraft's formatting codes (``§a``)."""
    return _FORMAT.sub("", name or "").strip()


def words(text: str) -> list[str]:
    return WORD.findall(clean(text).casefold())


def fts_query(found: list[str]) -> str:
    """An FTS5 query for ``found`` words (letters and digits only, so nothing in it is an
    operator): every word, the last one also as a prefix."""
    found = found[:MAX_WORDS]
    quoted = [f'"{w}"' for w in found]
    quoted[-1] += "*"
    return " ".join(quoted)


def _compact(text: str | None) -> str:
    return "".join(words(text or ""))


def score(form_words: list[str], row: sqlite3.Row) -> float:
    name = words(row["name"]) or words(row["key"])
    if not name or not form_words:
        return 0.0
    if name == form_words:
        return 1.0
    have, want = set(name), set(form_words)
    matched = have & want
    weight = float(len(matched))
    last = form_words[-1]
    if last not in have and len(last) >= 2 and any(w.startswith(last) for w in have):
        matched.add(last)
        weight += PREFIX_WEIGHT  # "steel" in "Steeleaf" is not the word steel
    if not matched:
        return 0.0
    value = weight / (len(have) + len(want - matched))
    material = _compact(row["gt_material"])
    if (
        material
        and material == "".join(form_words)
        and row["gt_prefix"] in ("ingot", "gem")
    ):
        value = min(value + MATERIAL_BONUS, 0.95)
    return value


def _candidate(row: sqlite3.Row, value: float, form: str) -> Candidate:
    rank = row["rank"] if "rank" in row.keys() else 0.0  # noqa: SIM118 (a Row)
    return Candidate(
        row["kind"],
        row["ref_id"],
        row["key"],
        clean(row["name"]),
        row["mod"],
        value,
        form,
        rank,
    )


def _sort_key(c: Candidate) -> tuple[Any, ...]:
    return (-c.score, _KIND_ORDER.get(c.kind, 9), c.rank, c.name.casefold(), c.key)


def _prefer_gregtech(found: list[Candidate]) -> list[Candidate]:
    """Same name, same score, and one of them is GregTech's: the others drop by CLOSE. GT
    unifies materials to its own items, so another mod's "Steel Ingot" never makes a request
    ambiguous; it stays listed."""
    groups: dict[tuple[str, str, float], list[Candidate]] = {}
    for c in found:
        groups.setdefault((c.kind, c.name.casefold(), c.score), []).append(c)
    out = []
    for c in found:
        group = groups[(c.kind, c.name.casefold(), c.score)]
        gt = [g for g in group if g.mod == GREGTECH]
        if len(group) > 1 and len(gt) == 1 and c.mod != GREGTECH:
            c = replace(c, score=max(c.score - CLOSE, 0.0))
        out.append(c)
    return out


def _judge(
    text: str, found: list[Candidate], via: Entry | None = None, capped: bool = False
) -> Resolution:
    found = _prefer_gregtech(found)
    found.sort(key=_sort_key)
    if not found:
        return Resolution(text, [], False, via, capped)
    top = found[0].score
    close = len(found) > 1 and top - found[1].score < CLOSE
    return Resolution(text, found, top < LOW or close, via, capped)


def by_key(call: Call, text: str) -> Resolution | None:
    """``text`` as an item, fluid or machine key; None when it isn't one. A fluid key can also
    have an item key's shape (``fluid:a@1``): both are looked up."""
    item, fluid = ITEM_KEY.fullmatch(text), FLUID_KEY.fullmatch(text)
    if item or fluid:
        found = []
        if fluid:
            rows = call.query("fluid_by_name", {"name": fluid["name"]})
            found += [_candidate(r, 1.0, text) for r in rows]
        if item:
            nbt = item["nbt"] or ""
            rows = call.query(
                "item_by_key",
                {"registry": item["reg"], "meta": int(item["meta"]), "nbt": nbt},
            )
            found += [_candidate(r, 1.0, text) for r in rows]
    elif m := MTE_KEY.fullmatch(text):
        rows = call.query("machine_by_mte", {"mte_id": int(m["mte"])})
        found = [
            Candidate(
                "machine",
                r["id"],
                f"mte:{r['mte_id']}",
                clean(r["name"]),
                r["mod"],
                1.0,
                text,
            )
            for r in rows
        ]
    else:
        return None
    return Resolution(text, found, len(found) > 1)


def resolve(call: Call, text: str, kind: str | None = None) -> Resolution:
    """Resolve ``text`` (a key, or words) to ranked candidates of ``kind`` (None: an item or a
    fluid; ``machine``: machine rows, keyed ``mte:<id>``)."""
    keyed = by_key(call, text.strip())  # a key as it is: names keep their spaces
    if keyed is not None:
        return keyed
    text = " ".join(text.split())
    if not words(text):
        raise ToolError("name an item, fluid or machine (words or a key)")
    per_form: list[tuple[str, Entry | None, dict[str, Candidate]]] = []
    capped = False
    for form, entry in call.env.glossary.variants(text):
        form_words = words(form)
        if not form_words:
            continue
        rows = call.query(
            "item_names", {"q": fts_query(form_words), "kind": kind, "limit": POOL}
        )
        capped = capped or len(rows) >= POOL
        scored: dict[str, Candidate] = {}
        for row in rows:
            value = score(form_words, row)
            if value > 0 and (
                row["key"] not in scored or scored[row["key"]].score < value
            ):
                scored[row["key"]] = _candidate(row, value, form)
        per_form.append((form, entry, scored))
    decided = next(
        (p for p in per_form if p[2] and max(c.score for c in p[2].values()) >= STRONG),
        None,
    )
    best: dict[str, Candidate] = {}
    for form, _, scored in per_form:
        for key, c in scored.items():
            if decided is not None and form != decided[0]:
                if key in decided[2]:
                    continue
                c = replace(c, score=c.score * DEMOTE)
            if key not in best or best[key].score < c.score:
                best[key] = c
    return _judge(text, list(best.values()), decided[1] if decided else None, capped)


def choices(res: Resolution, limit: int, what: str = "item") -> Result:
    """The candidates as a result: what a tool returns instead of guessing."""
    shown = res.candidates[:limit]
    head: list[tuple[str, object]] = [
        ("text", res.text),
        ("ambiguous", int(res.ambiguous)),
        ("matches", f"{len(res.candidates)}{'+' if res.capped else ''}"),
    ]
    if res.via is not None and shown and shown[0].form != res.text:
        head.append(("glossary", f"{res.text} -> {shown[0].form} ({res.via.kind})"))
    notes = []
    if not res.candidates:
        notes.append(f"no {what} matches; try other words or item_resolve")
    elif res.ambiguous:
        notes.append(
            f"ambiguous: ask which {what} is meant (top three), or call again with its key"
        )
    return Result(
        ("n", "kind", "key", "name", "mod", "score", "via"),
        [
            (
                n,
                c.kind,
                c.key,
                c.name,
                c.mod,
                round(c.score, 3),
                "" if c.form == res.text else c.form,
            )
            for n, c in enumerate(shown, 1)
        ],
        head=head,
        notes=notes,
    )


@dataclass(frozen=True)
class Target:
    """The one item or fluid a recipe or quest tool is about."""

    kind: str  # item or fluid
    ref_id: int
    key: str
    name: str

    def label(self) -> str:
        return f"{self.name} [{self.key}]" if self.name else f"[{self.key}]"

    def item_ids(self, call: Call) -> list[int]:
        """The item and, for an item, its wildcard-meta row (what a 32767 slot matches)."""
        if self.kind != "item":
            return []
        m = ITEM_KEY.fullmatch(self.key)
        assert m is not None
        ids = [self.ref_id]
        if int(m["meta"]) != WILDCARD:
            wild = call.query(
                "item_by_key", {"registry": m["reg"], "meta": WILDCARD, "nbt": ""}
            )
            ids += [r["ref_id"] for r in wild]
        return ids

    def ores(self, call: Call) -> list[sqlite3.Row]:
        ids = self.item_ids(call)
        return call.query("item_ores", {"items": json.dumps(ids)}) if ids else []


def target(call: Call, text: str) -> Target | Result:
    """The item or fluid ``text`` names (a machine key means the machine's item), or the
    candidates when it isn't clear which."""
    res = resolve(call, text)
    best = res.best
    if best is None:
        return choices(res, 10)
    if best.kind == "machine":
        rows = call.query(
            "machine_by_mte", {"mte_id": int(best.key.removeprefix("mte:"))}
        )
        if not rows or rows[0]["item_id"] is None:
            raise ToolError(f"machine {best.key} has no item in the DB")
        r = rows[0]
        return Target("item", r["item_id"], r["item_key"], clean(r["item_name"]))
    return Target(best.kind, best.ref_id, best.key, best.name)


ITEM_RESOLVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": 'what was asked for: words ("stainless steel", "EBF") or a key',
            "minLength": 1,
            "maxLength": 200,
        },
        "kind": {
            "type": "string",
            "description": "only this kind (default: items and fluids; machine: GT machines)",
            "enum": list(KINDS),
        },
        "limit": {
            "type": "integer",
            "description": "candidates to return (default 10)",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}


def item_resolve(call: Call, args: dict[str, Any], offset: int) -> Result:
    res = resolve(call, args["text"], args.get("kind"))
    return choices(res, args["limit"], args.get("kind") or "item")
