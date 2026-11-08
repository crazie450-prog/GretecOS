"""Recipe cards (plan 7.3, 7.14 rule 1; WO-2.7, D172): the dashboard's structured card for the
recipes a ``recipe_find`` or ``recipe_uses`` result holds.

Agents return cards, never HTML, and a card's numbers come from code, never from a model: GOS
builds this one from the tool's own text (:func:`recipe_card`), the same rows the model read and
cited, so the card and the spoken answer can't disagree. It is version 0 of the shape, for
WO-2.6's dashboard to adopt (docs/contracts/recipe-card-v0.md): a card holds the item, the run,
how many recipes match in all, and one entry per recipe row on the page with its ``r:`` id,
machine, tier, EU/t, amperage, ticks and seconds, inputs and outputs (amount, unit, chance,
oredict, alternatives, circuit, consumed or not), conditions and flags. Every value is the
tool's (the DB's, D131); nothing is rounded or added up here.
"""

from __future__ import annotations

import re
from typing import Any

from gregtec.agents.tools.tsv import Parsed

CARD = "recipe"
VERSION = 0
RECIPE_TOOLS = frozenset({"recipe_find", "recipe_uses"})
# A slot as recipes.slot_text writes it: [amount unit ]name [key][ circuit N][ (not
# consumed)][ (any ore:X) | (or N others)][ P% chance]. The name is greedy, so the key is the
# last bracket group ("Energy Core [ULV] [miscutils:...]").
SLOT = re.compile(
    r"^(?:(?P<amount>\d+)(?P<unit>[xL]) )?(?:(?P<name>.*) )?\[(?P<key>[^\[\]]+)\]"
    r"(?: circuit (?P<circuit>\d+))?(?P<kept> \(not consumed\))?"
    r"(?: \(any ore:(?P<ore>[^)]*)\)| \(or (?P<alts>\d+) others?\))?"
    r"(?: (?P<chance>\d+(?:\.\d+)?)% chance)?$"
)


def slot(text: str) -> dict[str, Any]:
    """One input or output as data; a text that isn't in the tool's format keeps its text."""
    m = SLOT.match(text)
    if m is None:
        return {"text": text}
    return {
        "key": m["key"],
        "name": m["name"] or "",
        "amount": int(m["amount"]) if m["amount"] else 0,  # 0: NEI shows no amount
        "unit": {"x": "item", "L": "L"}.get(m["unit"] or "", None),
        "chance_pct": m["chance"] or "100",
        "consumed": m["kept"] is None,
        "circuit": int(m["circuit"]) if m["circuit"] else None,
        "ore": m["ore"],
        "alternatives": int(m["alts"]) if m["alts"] else 0,
    }


def slots(cell: str) -> list[dict[str, Any]]:
    """A recipe row's ``in`` or ``out`` cell (slots joined by ``"; "``)."""
    return [slot(part) for part in cell.split("; ")] if cell else []


def _int(text: str) -> int | None:
    return int(text) if re.fullmatch(r"-?\d+", text or "") else None


def recipe(row: dict[str, str]) -> dict[str, Any]:
    """One recipe row of a recipe tool's result as data."""
    conditions = []
    for part in (row.get("cond") or "").split("; "):
        key, sep, value = part.partition("=")
        if sep:
            conditions.append({"key": key, "value": value})
    return {
        "ref": row.get("ref", ""),
        "machine": row.get("machine", ""),
        "tier": row.get("tier") or None,
        "eut": _int(row.get("eut", "")),
        "amps": _int(row.get("amps", "")),
        "dur_t": _int(row.get("dur_t", "")),
        "dur_s": row.get("dur_s") or None,
        "inputs": slots(row.get("in", "")),
        "outputs": slots(row.get("out", "")),
        "conditions": conditions,
        "flags": (row.get("flags") or "").split(),
    }


def recipe_card(tool: str, parsed: Parsed) -> dict[str, Any] | None:
    """The recipe card of a recipe tool's result; None for another tool or an answer with
    no recipe table (candidates when the item was ambiguous, an error)."""
    if tool not in RECIPE_TOOLS or "ref" not in parsed.columns:
        return None
    item = parsed.head.get("item", "")
    name, _, key = item.rpartition(" [")
    if not key:
        name, key = "", item.strip("[]")
    return {
        "type": CARD,
        "v": VERSION,
        "tool": tool,  # recipe_find: what makes the item; recipe_uses: what takes it
        "item": {"key": key.removesuffix("]"), "name": name},
        "run": parsed.head.get("run"),
        "filters": parsed.head.get("filters"),
        "total": parsed.total,  # every matching recipe; the page holds the first ones
        "recipes": [recipe(r) for r in parsed.rows],
    }
