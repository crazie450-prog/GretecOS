"""Pre-retrieval and unsure-match questions (plan 4.4, QA:D11, D151).

Before the fast lane's one model call, :func:`prefetch` runs the tools a routed request needs,
through the same registry and lane check the model would use, and keeps their text: the model
gets them as quoted data in the user turn, so a cited answer usually needs no tool round trip.

- An item subject is resolved with ``item_resolve``. When it is ambiguous (the top score under
  0.5, or the second within 0.15 of it: :mod:`gregtec.agents.tools.items`), nothing else runs:
  the result is a :class:`Clarify`, one short question naming the top three candidates, and the
  card lists them. No model is asked, so no answer about the wrong item can be made up.
- A clear item gets the tool its intent needs: ``recipe_find`` (with ``tier_max`` for "at LV"),
  ``recipe_uses``, ``quest_for_item``, or for a GT machine's item ``machine_info``.
- Quest words go to ``quest_find``; "what quests can I do" to ``quest_frontier``.
- Recipe and item questions also get Clayton's tier (the head of ``quest_frontier``), so "the
  simplest recipe at his tier" can be picked from the card; no quest data, no tier line.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.agents import router
from gregtec.agents.tools.registry import FAST, Registry
from gregtec.agents.tools.tsv import Parsed, parse
from gregtec.cards import build as cards_build

ASK_TOP = 3  # candidates named in the question (plan 4.4: the top 3 on the dashboard)
RESOLVE_LIMIT = 5
FRONTIER_N = 10
MACHINE_ITEM = re.compile(r"gregtech:gt\.blockmachines@\d+")


@dataclass(frozen=True)
class Block:
    """One tool call made for the model, and its text as the model sees it."""

    tool: str
    args: dict[str, Any]
    text: str
    is_error: bool = False

    @property
    def parsed(self) -> Parsed:
        return parse(self.text)

    def card(self, db: Path | None = None) -> dict[str, Any]:
        """The result as a catalog card (data only; the dashboard draws it, D160). ``db``:
        the DB the recipe tools read, so a recipe card's slots come structured from it."""
        return cards_build.from_tool(self.tool, self.text, self.is_error, db)


@dataclass(frozen=True)
class Candidate:
    key: str
    name: str
    kind: str
    mod: str


@dataclass(frozen=True)
class Clarify:
    """The question to ask instead of answering."""

    subject: str
    question: str
    candidates: list[Candidate]

    def card(self) -> dict[str, Any]:
        return {
            "type": "choices",
            "subject": self.subject,
            "question": self.question,
            "candidates": [
                {"n": n, "key": c.key, "name": c.name, "kind": c.kind, "mod": c.mod}
                for n, c in enumerate(self.candidates, 1)
            ],
        }


@dataclass
class Prefetch:
    blocks: list[Block] = field(default_factory=list)
    main: Block | None = None  # the block the card shows
    target: Candidate | None = None  # the item the request is about, once resolved
    clarify: Clarify | None = None
    tier: str | None = None  # Clayton's tier line, when quests are loaded
    unresolved: str | None = None  # an item subject nothing matched

    @property
    def sources(self) -> list[str]:
        """Every text the answer may take numbers from."""
        out = [b.text for b in self.blocks]
        if self.tier:
            out.append(f"tier: {self.tier}")
        return out


def candidates(parsed: Parsed) -> list[Candidate]:
    return [
        Candidate(r["key"], r["name"], r["kind"], r.get("mod", ""))
        for r in parsed.rows
        if r.get("key")
    ]


def _natural(name: str) -> list[tuple[int, int | str]]:
    """A sort key that reads digit runs as numbers: "1x Copper Wire" before "12x ..."."""
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", name)
        if part
    ]


def question_names(found: list[Candidate]) -> list[str]:
    """The top three distinct names, by score; names that differ only in their numbers
    ("12x, 16x and 1x Copper Wire") are spoken in number order instead."""
    names = list(dict.fromkeys(c.name or c.key for c in found))[:ASK_TOP]
    if len({re.sub(r"\d+", "#", n) for n in names}) == 1:
        names.sort(key=_natural)
    return names


def _pick(found: list[Candidate], name: str) -> Candidate:
    """The candidate a spoken name stands for: the first (best scored) of that name without
    an NBT hash, else the first; a charged battery or a filled cell is rarely what was meant."""
    named = [c for c in found if (c.name or c.key) == name]
    return next((c for c in named if "#" not in c.key), named[0])


def clarify(subject: str, found: list[Candidate]) -> Clarify:
    """The one clarifying question for an ambiguous ``subject`` and the three it names."""
    names = question_names(found)
    return Clarify(
        subject, question_for(subject, found), [_pick(found, n) for n in names]
    )


def question_for(subject: str, found: list[Candidate]) -> str:
    """ "Which steel did you mean: Steel Ingot, Steel Plate or Stainless Steel Ingot?" The
    first three distinct names are spoken; a name two candidates share (an item and its NBT
    variant) stands for the one without NBT (:func:`_pick`), and the card holds those three."""
    labels = question_names(found)
    if len(labels) == 1:
        return f"Did you mean {labels[0]}?"
    listed = ", ".join(labels[:-1]) + f" or {labels[-1]}"
    return f"Which {subject} did you mean: {listed}?"


def _call(reg: Registry, pre: Prefetch, tool: str, args: dict[str, Any]) -> Block:
    out = reg.call(tool, args, lane=FAST)
    block = Block(tool, args, out.text, out.is_error)
    pre.blocks.append(block)
    return block


def tier_line(reg: Registry) -> str | None:
    out = reg.call("quest_frontier", {"n": 1}, lane=FAST)
    return None if out.is_error else parse(out.text).head.get("tier")


def _singular(words: str) -> str:
    """The last word without an English plural ending ("resistors", "batteries", "boxes")."""
    head, _, last = words.rpartition(" ")
    for plural, single in (("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("xes", "x")):
        if last.endswith(plural) and len(last) > len(plural) + 1:
            last = last[: -len(plural)] + single
            break
    else:
        if last.endswith("s") and not last.endswith("ss") and len(last) > 3:
            last = last[:-1]
    return f"{head} {last}".strip()


def resolve(reg: Registry, pre: Prefetch, subject: str) -> Candidate | None:
    """The item ``subject`` names, or None (``pre.clarify`` or ``pre.unresolved`` set)."""
    block = _call(reg, pre, "item_resolve", {"text": subject, "limit": RESOLVE_LIMIT})
    if block.is_error:
        pre.main = block  # the card and the no-model answer say why (e.g. no DB yet)
        return None
    parsed = block.parsed
    found = candidates(parsed)
    if not found and (singular := _singular(subject)) != subject:
        # "resistors": the FTS prefix of a plural doesn't reach the singular name
        block = _call(
            reg, pre, "item_resolve", {"text": singular, "limit": RESOLVE_LIMIT}
        )
        parsed = block.parsed
        found = [] if block.is_error else candidates(parsed)
    if not found:
        pre.unresolved = subject
        return None
    if parsed.head.get("ambiguous") == "1":
        pre.clarify = clarify(subject, found)
        pre.main = block
        return None
    pre.target = found[0]
    return found[0]


def prefetch(reg: Registry, route: router.Route, *, tier: bool = True) -> Prefetch:
    """The tool results for ``route`` (see the module docstring)."""
    pre = Prefetch()
    intent, subject = route.intent, route.subject
    if intent == router.FRONTIER:
        pre.main = _call(reg, pre, "quest_frontier", {"n": FRONTIER_N})
        return pre
    if intent == router.QUESTS and subject:
        pre.main = _call(reg, pre, "quest_find", {"text": subject})
        return pre
    if tier and intent in (router.RECIPE, router.ITEM, router.GENERAL):
        pre.tier = tier_line(reg)
    if not subject:
        return pre
    target = resolve(reg, pre, subject)
    if target is None:
        return pre
    if intent in (router.RECIPE, router.RECIPES):
        args: dict[str, Any] = {"item": target.key}
        if route.tier:
            args["tier_max"] = route.tier
        pre.main = _call(reg, pre, "recipe_find", args)
    elif intent == router.USES:
        pre.main = _call(reg, pre, "recipe_uses", {"item": target.key})
    elif intent == router.QUEST_ITEM:
        pre.main = _call(reg, pre, "quest_for_item", {"item": target.key})
    else:
        if MACHINE_ITEM.fullmatch(
            target.key
        ):  # a GT machine: its facts, then its recipes
            info = _call(reg, pre, "machine_info", {"machine": target.key})
            pre.main = None if info.is_error else info
        recipes = _call(reg, pre, "recipe_find", {"item": target.key})
        if pre.main is None or (not pre.main.parsed.rows and recipes.parsed.rows):
            pre.main = recipes
    return pre


def quoted(pre: Prefetch) -> str:
    """The pre-retrieved results as the user turn's data blocks."""
    parts = []
    if pre.tier:
        parts.append(f"<context>\nClayton's tier: {_safe(pre.tier)}\n</context>")
    for b in pre.blocks:
        args = json.dumps(b.args, ensure_ascii=False, sort_keys=True)
        # attribute-escaped: an apostrophe in the args can't end the quoted value
        args = html.escape(args, quote=False).replace("'", "&#x27;")
        parts.append(
            f"<tool_result tool=\"{b.tool}\" args='{args}'>\n"
            f"{_safe(b.text).rstrip()}\n</tool_result>"
        )
    return "\n\n".join(parts)


def _safe(text: str) -> str:
    """Pack, quest and note text can't close the blocks it is quoted in."""
    return re.sub(
        r"</(?=\s*(?:tool_result|context|request|vault_index)\b)", "< /", text
    )
