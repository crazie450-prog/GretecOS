"""The "what's this?" answer for a hovered item or a looked-at block (WO-8.1, plan 7.8, D286).

GregtecLink's client module reports what Clayton points at as registry name + meta (+ NBT),
never a numeric ID (docs/contracts/gregteclink-client-v1.md). :func:`keys_for` turns that into
the DB's item keys, most exact first: ``registry@meta#<nbt hash>`` (the NBT hashed as the export
was, :func:`gregtec.ingest.normalize.nbt_hash`), then the plain item, then its wildcard-meta row.
:func:`answer` resolves the first key the DB has and reads, through the same tools the lanes use
(``item_resolve``, ``machine_info``, ``recipe_find``, ``recipe_uses``), its name, mod, what makes
it and what it goes into. No model is asked and every number comes from those tool results:
the counts, and the first recipe's ``r:`` id (or the machine's ``mte:``) as the citation.
Outside text (a display name from the game) is never put in the answer as fact: only the DB's
name is, and the game's name is shown only when the DB has no such item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from gregtec.agents import prefetch
from gregtec.agents.tools.items import WILDCARD
from gregtec.agents.tools.registry import Registry
from gregtec.agents.tools.tsv import Parsed
from gregtec.ingest.normalize import nbt_hash, normalize_nbt

REGISTRY = re.compile(r"[^@#\t\r\n:]+:[^@#\t\r\n]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f§]")
MAX_META = 2**31 - 1
NO_LINK = "The game isn't connected, so I can't see what you're pointing at."
NOTHING = "You aren't pointing at anything: hover over an item or look at a block."
TARGET_OFF = "The game's target feed is off, so I can't see what you're pointing at."
WHATS_THIS = "What's this?"


class Pointer(Protocol):
    """Where :func:`answer`'s target comes from: the core's game link."""

    def current_target(self) -> tuple[dict[str, Any] | None, bool]:
        """(what Clayton points at now, or None; whether a game is connected)."""
        ...


@dataclass
class Answer:
    text: str
    key: str | None = None  # the DB key answered for
    name: str | None = None  # the DB's name for it
    kind: str | None = None  # item or fluid, from item_resolve
    card: dict[str, Any] | None = None
    refs: list[str] = field(default_factory=list)
    found: bool = False


def item_of(target: dict[str, Any]) -> dict[str, Any] | None:
    """The reported item (a hovered stack, or a looked-at block's pick item), if well formed."""
    item = target.get("item")
    if not isinstance(item, dict):
        return None
    name, meta = item.get("item"), item.get("meta")
    if not isinstance(name, str) or not REGISTRY.fullmatch(name):
        return None
    if not isinstance(meta, int) or isinstance(meta, bool) or not 0 <= meta <= MAX_META:
        return None
    return item


def keys_for(item: dict[str, Any]) -> list[str]:
    """The DB keys to try for a reported item, most exact first."""
    base = f"{item['item']}@{item['meta']}"
    out = []
    nbt = item.get("nbt")
    if isinstance(nbt, dict) and nbt:
        out.append(f"{base}#{nbt_hash(normalize_nbt(nbt))}")
    out.append(base)
    if item["meta"] != WILDCARD:
        out.append(f"{item['item']}@{WILDCARD}")
    return out


def _call(reg: Registry, tool: str, args: dict[str, Any]) -> prefetch.Block:
    out = reg.call(tool, args, lane="fast")
    return prefetch.Block(tool, args, out.text, out.is_error)


def _resolved(reg: Registry, key: str) -> dict[str, str] | None:
    block = _call(reg, "item_resolve", {"text": key, "limit": 2})
    if block.is_error:
        return None
    rows = block.parsed.rows
    return rows[0] if len(rows) == 1 else None


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _plain_first(p: Parsed) -> dict[str, str] | None:
    """The first row without a ``fake?`` or ``recycling`` flag, else the first (D172)."""
    for row in p.rows:
        if not {"fake?", "recycling"} & set(row.get("flags", "").split()):
            return row
    return p.rows[0] if p.rows else None


def answer(
    reg: Registry, target: dict[str, Any] | None, *, linked: bool = True
) -> Answer:
    """What ``target`` (a ``target`` message from the client module) is, from the DB."""
    if target is None:
        return Answer(NO_LINK if not linked else NOTHING)
    if target.get("kind") == "off":
        return Answer(TARGET_OFF)
    item = item_of(target)
    if item is None:
        return Answer(NOTHING)
    keys = keys_for(item)
    found = None
    for key in keys:
        found = _resolved(reg, key)
        if found is not None:
            break
    if found is None:
        plain = f"{item['item']}@{item['meta']}"
        shown = target.get("display")
        if isinstance(shown, str) and (shown := _CONTROL.sub("", shown).strip()[:80]):
            return Answer(f"{shown} ({plain}) isn't in the recipe DB.", key=plain)
        return Answer(f"{plain} isn't in the recipe DB.", key=plain)
    key, name = found["key"], found.get("name") or found["key"]
    parts = [f"{name}, from {found.get('mod') or 'an unknown mod'}."]
    if "#" in keys[0] and "#" not in key:
        parts.append("That exact NBT isn't in the DB; this is the plain item.")
    refs: list[str] = []
    card = None
    if prefetch.MACHINE_ITEM.fullmatch(key):
        info = _call(reg, "machine_info", {"machine": key})
        if not info.is_error:
            mte = info.parsed.head.get("machine", "")
            parts.append(f"It's a GT machine ({mte}); the card lists its facts.")
            refs.append(mte)
            card = info.card(reg.env.db)
    made = _call(reg, "recipe_find", {"item": key})
    if not made.is_error:
        p = made.parsed
        first = _plain_first(p)
        total = p.total if p.total is not None else len(p.rows)
        if first is None and "#" in key:
            # an NBT variant (a charged battery) is rarely made as such: say what makes the
            # plain item instead of "no recipe" (review LOW-6)
            parts.append("No recipe in the DB makes it with this NBT.")
            plain = _call(reg, "recipe_find", {"item": key.split("#", 1)[0]})
            base = None if plain.is_error else _plain_first(plain.parsed)
            if base is not None:
                n = plain.parsed.total
                n = n if n is not None else len(plain.parsed.rows)
                parts.append(
                    f"For the plain item, {_count(n, 'recipe')} {'makes' if n == 1 else 'make'} it; "
                    f"the first is {base['machine']} ({base['ref']})."
                )
                refs.append(base["ref"])
        elif first is None:
            parts.append("No recipe in the DB makes it.")
        else:
            parts.append(
                f"{_count(total, 'recipe')} {'makes' if total == 1 else 'make'} it; the first is {first['machine']} "
                f"({first['ref']})."
            )
            refs.append(first["ref"])
        if card is None:
            card = made.card(reg.env.db)
    uses = _call(reg, "recipe_uses", {"item": key})
    if not uses.is_error:
        p = uses.parsed
        total = p.total if p.total is not None else len(p.rows)
        parts.append(
            f"It goes into {_count(total, 'recipe')}."
            if total
            else "No recipe in the DB takes it."
        )
    return Answer(
        " ".join(parts),
        key=key,
        name=name,
        kind=found.get("kind"),
        card=card,
        refs=[r for r in refs if r],
        found=True,
    )
