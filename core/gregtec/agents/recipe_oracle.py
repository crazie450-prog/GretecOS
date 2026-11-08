"""The fake models of ``gos eval recipe-lookup --fake`` (WO-2.7, D172): oracles for both lanes.

An oracle knows each eval row's references (its :class:`~gregtec.agents.recipe_eval.Step`
list) and the words the question names each step's item by (the file's ``words`` cell), as a
test oracle does, and does what the recipe-lookup skill says a careful model does:

- **The item from the question's words, never the reference's key.** The fast lane's oracle
  uses the pre-retrieved results when the router resolved the words, else passes the words to
  the lookup tool, which resolves them as ``item_resolve`` does; the deep lane's oracle calls
  ``item_resolve`` first (the skill's step 1). A step after the first takes its key from the
  ``in`` cell of the row the previous step chose when a slot there is named by its words (the
  skill's "what makes X, then its input Y"), else resolves its own words. Words that resolve to
  another item lead to a wrong answer, which the scorer fails, as it would a model's.
- **The row by the skill's rule.** Of the rows the step means (its filters, read off the tools'
  text, never the DB) the first without a flag (``fake?``, ``recycling``); a flagged one only
  when no other row matches, after the lookup's last page.
- **Ask on** ``ambiguous: 1``, naming the top three, and answer otherwise by copying the row's
  numbers next to its id.

- :class:`OracleClient` is the fast lane's model (a :class:`gregtec.llm.fake.FakeClient`), so
  the real router, pre-retrieval, ``FastLane`` loop (at most 3 calls a turn), tools and number
  check run around it.
- :func:`deep_policy` is the deep lane's: the real Claude Code CLI runs against
  :class:`gregtec.llm.fake_api.FakeMessagesAPI` with it, so the skill must deploy, be in the
  skill list the CLI gives the model, and load (``Skill`` ``gos:recipe-lookup``, first; a model
  loads only a skill it's told about), and the tools come through the ``gos`` MCP server
  and the hooks. Its answer is the deep lane's shape: a spoken first sentence without ids, then
  one ``key: value`` line per cited recipe (the skill's section 4).

The scorer (:mod:`gregtec.agents.recipe_eval`) reads the DB itself, so the oracle can only pass
by finding the right rows through the tools. It checks the plumbing, item resolution from the
question's words and the scoring, not a model: a question the oracle has no row for gets
EchoClient's answer.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO

from gregtec import config
from gregtec.agents import cards, recipe_eval
from gregtec.agents.ask import Asker
from gregtec.agents.deep import DeepLaneService
from gregtec.agents.deep_config import Settings
from gregtec.agents.deep_eval import FAKE_KEY
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.recipe_eval import FLAGS, MACHINE, MAKE, Question, Step
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.items import FLUID_KEY, ITEM_KEY, MTE_KEY, words
from gregtec.agents.tools.mcp import PREFIX
from gregtec.agents.tools.tsv import Parsed, parse
from gregtec.db import access
from gregtec.llm import fake
from gregtec.llm.fake import FakeClient, Turn
from gregtec.llm.fake_api import ApiTurn, FakeMessagesAPI, Policy
from gregtec.vault import deploy as vault_deploy

SKILL = "gos:recipe-lookup"
SKILL_HEADING = "# Recipe lookup"  # the first line of the loaded skill's body
PREFETCHED = re.compile(
    r'<tool_result tool="(?P<tool>[a-z_]+)" args=\'(?P<args>[^\']*)\'>\n'
    r"(?P<text>.*?)\n</tool_result>",
    re.DOTALL,
)
REQUEST = re.compile(r"<request>\n(?P<text>.*?)\n</request>", re.DOTALL)
_REF_IN = re.compile(r" \((?:r|mte):[^)]*\)")  # "(r:...)" in a sentence: not spoken
_WIRE_SIZE = re.compile(r"\d+x")  # "1x Copper Wire": GT's wire size, part of the name
TIERS = recipe_eval.TIERS
# A cited recipe's detail line (the skill's section 4): the recipe tools' own columns
DETAIL = (
    "ref",
    "machine",
    "tier",
    "eut",
    "amps",
    "dur_t",
    "dur_s",
    "in",
    "out",
    "cond",
)
Call = tuple[str, dict[str, Any]]


@dataclass
class Seen:
    """One tool result the model has: the call and its text."""

    tool: str
    args: dict[str, Any]
    text: str

    @property
    def parsed(self) -> Parsed:
        return parse(self.text)


@dataclass
class Found:
    """The row a step means, and the result it came from."""

    seen: Seen
    row: dict[str, str] | None  # None: the lookup answered, and no row matches


def head_key(p: Parsed) -> str:
    """The key a result is about: ``item: Name [key]`` or ``machine: mte:<id>``."""
    if "machine" in p.head:
        return p.head["machine"]
    m = re.search(r"\[([^\]]+)\]$", p.head.get("item", ""))
    return m[1] if m else ""


def head_name(p: Parsed) -> str:
    if "name" in p.head:
        return p.head["name"]
    return p.head.get("item", "").rsplit(" [", 1)[0]


def is_key(text: str) -> bool:
    """A key the tools take as it is (an item, a fluid or a machine), not words."""
    return any(k.fullmatch(text) for k in (ITEM_KEY, FLUID_KEY, MTE_KEY))


def flagged(row: Mapping[str, str]) -> bool:
    """A row the skill uses only when no other answers: ``fake?`` or ``recycling``."""
    return bool(set(row.get("flags", "").split()) & set(FLAGS))


def _slot(cell: str, key: str) -> dict[str, Any] | None:
    """The slot of a recipe row's ``in`` or ``out`` cell that holds ``key``."""
    return next((s for s in cards.slots(cell) if s.get("key") == key), None)


def matches(step: Step, row: dict[str, str]) -> bool:
    """Whether a recipe row is one ``step`` means (its filters, read off the row's text)."""
    if step.op == MAKE and _slot(row.get("out", ""), step.key) is None:
        return False
    for name, value in step.filters:
        if name == "map" and not row.get("ref", "").startswith(f"r:{value}/"):
            return False
        if (
            name == "tier"
            and row.get("tier")
            and (
                row["tier"] not in TIERS
                or TIERS.index(row["tier"]) > TIERS.index(value)
            )
        ):
            return False
        if name == "in" and _slot(row.get("in", ""), value) is None:
            return False
        if name == "out" and _slot(row.get("out", ""), value) is None:
            return False
        if name == "ore" and f"(any ore:{value})" not in row.get("in", ""):
            return False
    return True


def lookup(step: Step, item: str | None = None) -> Call:
    """The call the skill names for ``step``, about ``item`` (words or a key; default the
    step's own key)."""
    item = item or step.key
    if step.op == MACHINE:
        return "machine_info", {"machine": item}
    if step.op == MAKE:
        args: dict[str, Any] = {"item": item}
        if (machine := step.filter("map")) is not None:
            args["machine"] = machine
        if (tier := step.filter("tier")) is not None:
            args["tier_max"] = tier
        return "recipe_find", args
    return "recipe_uses", {"item": item}


def _cursor(seen: Seen) -> str | None:
    """The result's ``more:`` cursor (the line after its last ``rows:`` line)."""
    lines = seen.text.split("\n")
    rows = [i for i, line in enumerate(lines) if line.startswith("rows: ")]
    if rows and rows[-1] + 1 < len(lines) and lines[rows[-1] + 1].startswith("more: "):
        return lines[rows[-1] + 1].removeprefix("more: ")
    return None


def _pages(call: Call, seen: Sequence[Seen]) -> list[Seen]:
    """The results of ``call``: its first page, then the ``more`` pages that continue it (a
    page repeats the head of the call it continues)."""
    tool, args = call
    first = [s for s in seen if s.tool == tool and s.args == args]
    if not first:
        return []
    head = first[0].parsed.head
    return first + [s for s in seen if s.tool == "more" and s.parsed.head == head]


def _name_words(text: str) -> list[str]:
    """A name's words as compared: no wire size in front, the last word singular."""
    found = words(text)
    if found and _WIRE_SIZE.fullmatch(found[0]):
        found = found[1:]
    if (
        found
        and len(found[-1]) > 3
        and found[-1].endswith("s")
        and found[-1][-2] != "s"
    ):
        found[-1] = found[-1][:-1]
    return found


def chained(said: str, row: Mapping[str, str]) -> str | None:
    """The key of the slot of ``row``'s ``in`` cell that ``said`` names (the skill: take Y's
    key from the chosen row's ``in`` cell), or None when no slot has that name (an oredict
    slot may show another item of it)."""
    want = _name_words(said)
    for s in cards.slots(row.get("in", "")):
        if s.get("key") and want and _name_words(s.get("name", "")) == want:
            return str(s["key"])
    return None


def _resolution(said: str, kind: str | None, seen: Sequence[Seen]) -> Seen | None:
    """The ``item_resolve`` result for these words (the pre-retrieved one included)."""
    want = " ".join(said.split()).casefold()
    for s in seen:
        text = " ".join(str(s.args.get("text", "")).split()).casefold()
        if (
            s.tool == "item_resolve"
            and text == want
            and s.args.get("kind") in (None, kind)
        ):
            return s
    return None


def _top(p: Parsed) -> list[str]:
    """The top three distinct candidate names of an ambiguous result."""
    return list(dict.fromkeys(r.get("name", "") for r in p.rows if r.get("name")))[:3]


@dataclass
class State:
    """Where one step is: the item to look up, a call to make, a question, or its row."""

    item: str | None = None
    call: Call | None = None
    ask: list[str] | None = None
    found: Found | None = None


def _item(
    step: Step, said: str, prev: Found | None, seen: Sequence[Seen], resolve_first: bool
) -> State:
    """The item a step's lookup is about: chained from the previous row, resolved from the
    words, or the words themselves for the lookup tool to resolve."""
    if (
        prev is not None
        and prev.row is not None
        and (key := chained(said, prev.row)) is not None
    ):
        return State(item=key)
    if not said:
        return State(item=step.key)  # a file without words: the step's own key
    kind = "machine" if step.op == MACHINE else None
    resolved = _resolution(said, kind, seen)
    if resolved is None:
        if resolve_first:
            return State(
                call=("item_resolve", {"text": said} | ({"kind": kind} if kind else {}))
            )
        return State(item=said)
    p = resolved.parsed
    if p.head.get("ambiguous") == "1":
        return State(ask=_top(p))
    if not p.rows:
        return State(found=Found(resolved, None))  # nothing in the DB matches the words
    return State(item=p.rows[0]["key"])


def _rows(step: Step, sources: Sequence[Seen]) -> list[tuple[Seen, dict[str, str]]]:
    """The rows of the results that ``step`` means, in the tools' order."""
    out = []
    for s in sources:
        p = s.parsed
        own = head_key(p) == step.key
        for row in p.rows:
            takes = step.op == MAKE or own or _slot(row.get("in", ""), step.key)
            if takes and matches(step, row):
                out.append((s, row))
    return out


def step_state(
    step: Step,
    said: str,
    prev: Found | None,
    seen: Sequence[Seen],
    *,
    tools: bool,
    resolve_first: bool,
) -> State:
    got = _item(step, said, prev, seen, resolve_first)
    if got.item is None:
        return got
    call = lookup(step, got.item)
    pages = _pages(call, seen)
    if pages and pages[0].parsed.head.get("ambiguous") == "1":
        return State(ask=_top(pages[0].parsed))  # the tool resolved the words: unclear
    if step.op == MACHINE:
        return State(found=Found(pages[0], None)) if pages else State(call=call)
    key = got.item if is_key(got.item) else (head_key(pages[0].parsed) if pages else "")
    sources = list(pages)
    if key and "count" not in step.want:  # a count is the step's own lookup's total
        tool = call[0]
        sources += [
            s
            for s in seen
            if s.tool == tool and s not in pages and head_key(s.parsed) == key
        ]
    rows = _rows(step, sources)
    if plain := next(((s, r) for s, r in rows if not flagged(r)), None):
        return State(found=Found(*plain))
    cursor = _cursor(pages[-1]) if pages else None
    if cursor and tools:
        return State(call=("more", {"cursor": cursor}))
    if rows:
        return State(found=Found(*rows[0]))
    if pages:
        return State(found=Found(pages[0], None))
    return State(call=call)


def _value(step: Step, want: str, found: Found) -> str:
    """The words that state ``want`` for the found row, copied from its cells."""
    row = found.row or {}
    name, _, key = want.partition("=")
    if found.seen.tool == "machine_info" or step.op == MACHINE:
        if name == "fact":
            fact = next((r for r in found.seen.parsed.rows if r["key"] == key), None)
            return f"{key} {fact['value']}" if fact else ""
        if want == "tier":
            return f"at {found.seen.parsed.head.get('tier', '')}"
        return ""
    if want == "eut":
        return f"{row.get('eut', '')} EU/t"
    if want == "amps":
        return f"{row.get('amps', '')} A"
    if want == "dur":
        return f"{row.get('dur_t', '')} ticks ({row.get('dur_s', '')} s)"
    if want == "tier":
        return f"at {row['tier']}" if row.get("tier") else "with no tier"
    if want == "count":
        total = found.seen.parsed.total
        return f"{total} recipes in all" if total is not None else ""
    if want == "machine":
        return f"in the {row.get('machine', '')}"
    if name == "ore":
        slot = next(
            (s for s in cards.slots(row.get("in", "")) if s.get("ore") == key), None
        )
        return f"any ore:{key} item fits ({slot['name']} shown)" if slot else ""
    if name in recipe_eval.SLOT_WANTS:
        slot = _slot(row.get("out" if name in ("out", "chance") else "in", ""), key)
        if slot is None:
            return ""
        if name == "chance":
            return f"{slot['chance_pct']}% chance of {slot['name']}"
        if name == "alts":
            if slot["ore"]:
                return f"any {slot['name']} (ore:{slot['ore']})"
            return f"{slot['name']} or {slot['alternatives']} other stacks"
        unit = "L" if slot["unit"] == "L" else "x"
        return f"{slot['amount']}{unit} {slot['name']}"
    if name == "cond":
        cond = dict(
            c.split("=", 1) for c in row.get("cond", "").split("; ") if "=" in c
        )
        return f"{key} {cond.get(key, '')}"
    return ""  # simplest: the row itself is the answer


def sentence(step: Step, found: Found | None) -> str:
    """One step's answer: the item first, the row's id, the wanted values copied."""
    if found is None:
        return f"I couldn't find the row for {step.text()} through the tools."
    p = found.seen.parsed
    if found.seen.tool == "item_resolve":
        return f"Nothing in the DB matches {p.head.get('text', step.key)!r}."
    name = head_name(p) or step.key
    if found.seen.tool == "machine_info":
        parts = [v for w in step.want if (v := _value(step, w, found))]
        maps = p.head.get("maps", "")
        said = ", ".join(parts) if parts else f"it runs {maps.split(' [')[0]}"
        return f"{name} ({head_key(p)}): {said}."
    if found.row is None:
        if p.rows:  # its lookup has rows, none of them the one asked about
            return f"{name}: I couldn't find the recipe asked about among its rows."
        verb = "makes" if step.op == MAKE else "takes"
        return f"{name}: no recipe in the DB {verb} it."
    row = found.row
    if step.op != MAKE and (used := _slot(row.get("in", ""), step.key)):
        name = used["name"] or name  # a use found through its output's lookup
    verb = "made in" if step.op == MAKE else "used in"
    parts = [v for w in step.want if w != "machine" and (v := _value(step, w, found))]
    tail = f", {', '.join(parts)}" if parts else ""
    return f"{name}: {verb} the {row['machine']} ({row['ref']}){tail}."


def detail(found: Found) -> str:
    """A cited recipe as the skill's ``key: value`` line (copied cells), or a machine's."""
    p = found.seen.parsed
    if found.seen.tool == "machine_info":
        facts = ", ".join(f"{r['key']}: {r['value']}" for r in p.rows)
        return f"machine: {head_key(p)}, name: {head_name(p)}" + (
            f", {facts}" if facts else ""
        )
    row = found.row or {}
    return ", ".join(f"{k}: {row[k]}" for k in DETAIL if row.get(k))


@dataclass
class Plan:
    """What the oracle does next for a question: tool calls, or its answer."""

    calls: list[Call] = field(default_factory=list)
    found: list[Found | None] = field(default_factory=list)
    ask: list[str] | None = None


def decide(
    q: Question, seen: Sequence[Seen], tools: bool, *, resolve_first: bool = False
) -> Plan:
    """The next calls, or the answer's rows. ``resolve_first``: ``item_resolve`` before a
    lookup (the deep lane, as the skill's step 1 says); otherwise the lookup tools resolve the
    words themselves (the fast lane, whose three calls a turn leave no round to spare).
    Steps are taken in order: a later one may take its item from an earlier one's row."""
    plan = Plan()
    if not q.steps:  # an ask row: its words should be unclear
        said = q.words[0] if q.words else ""
        resolved = _resolution(said, None, seen) if said else None
        if resolved is None:
            if said and tools:
                plan.calls.append(("item_resolve", {"text": said}))
        elif resolved.parsed.head.get("ambiguous") == "1":
            plan.ask = _top(resolved.parsed)
        return plan
    prev: Found | None = None
    waiting = False  # an earlier step is still being looked up
    for i, step in enumerate(q.steps):
        if waiting:
            plan.found.append(None)
            continue
        said = q.words[i] if i < len(q.words) else ""
        state = step_state(
            step, said, prev, seen, tools=tools, resolve_first=resolve_first
        )
        if state.ask is not None:
            plan.ask = state.ask
            return plan
        if state.call is not None:
            if tools:
                plan.calls.append(state.call)
            plan.found.append(None)
            waiting = True
            continue
        plan.found.append(state.found)
        prev = state.found
    return plan


def ask_text(names: list[str]) -> str:
    names = [n for n in names if n] or ["one of them"]
    listed = ", ".join(names[:-1]) + f" or {names[-1]}" if len(names) > 1 else names[0]
    return f"Which did you mean: {listed}?"


def fast_answer(q: Question, plan: Plan) -> str:
    if plan.ask is not None:
        return ask_text(plan.ask)
    if not q.steps:
        return "That names one item, so there is nothing to ask."
    return " ".join(sentence(s, f) for s, f in zip(q.steps, plan.found, strict=True))


def deep_answer(q: Question, plan: Plan) -> str:
    """The deep lane's shape (its prompt and the skill): a spoken first sentence without ids,
    then one ``key: value`` line per cited row (a step with no row says so instead)."""
    if plan.ask is not None:
        return ask_text(plan.ask)
    if not q.steps:
        return "That names one item, so there is nothing to ask."
    said = [
        _REF_IN.sub("", sentence(s, f)).rstrip(". ")
        for s, f in zip(q.steps, plan.found, strict=True)
    ]
    lines = ["; ".join(said) + ".", ""]
    for step, found in zip(q.steps, plan.found, strict=True):
        cited = found is not None and (found.row or found.seen.tool == "machine_info")
        lines.append(detail(found) if cited and found else sentence(step, found))
    return "\n".join(lines)


# --- the fast lane's oracle -------------------------------------------------------------


def _blocks(content: object) -> list[dict[str, Any]]:
    return (
        [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    )


def fast_seen(messages: Sequence[Mapping[str, Any]]) -> tuple[str, list[Seen]]:
    """(the request, the results) of a fast-lane conversation: the pre-retrieved blocks of
    the first user turn, then each tool call with its result."""
    first = messages[0].get("content", "") if messages else ""
    text = first if isinstance(first, str) else ""
    found = REQUEST.search(text)
    seen = []
    for m in PREFETCHED.finditer(text):
        try:
            args = json.loads(html.unescape(m["args"]))
        except ValueError:
            args = {}
        seen.append(Seen(m["tool"], args if isinstance(args, dict) else {}, m["text"]))
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for message in messages[1:]:
        for b in _blocks(message.get("content")):
            if b.get("type") == "tool_use":
                calls[str(b.get("id"))] = (
                    str(b.get("name")),
                    dict(b.get("input") or {}),
                )
            elif b.get("type") == "tool_result":
                name, args = calls.get(str(b.get("tool_use_id")), ("", {}))
                seen.append(Seen(name, args, str(b.get("content", ""))))
    return (" ".join(found["text"].split()) if found else ""), seen


@dataclass
class OracleClient(FakeClient):
    """The fast lane's model in ``--fake``: :func:`decide` over the conversation."""

    plans: Mapping[str, Question] = field(default_factory=dict)

    def turn(self, request: dict[str, Any]) -> Turn:
        messages = request.get("messages") or []
        last = fake._texts(messages[-1].get("content", "")) if messages else []
        if any(t.startswith("Gregtec OS didn't send") for t in last):
            return Turn("I don't know that number; I can look it up.")
        said, seen = fast_seen(messages)
        q = self.plans.get(said)
        if q is None:
            return Turn(fake.answer([(s.tool, s.text) for s in seen]))
        tools = (request.get("tool_choice") or {}).get("type") != "none"
        plan = decide(q, seen, tools)
        if plan.calls and plan.ask is None:
            return Turn(
                "Looking that up.", [{"name": n, "input": a} for n, a in plan.calls]
            )
        return Turn(fast_answer(q, plan))


# --- the deep lane's oracle -------------------------------------------------------------


def deep_seen(body: Mapping[str, Any]) -> tuple[list[Seen], bool, bool]:
    """(the gos tools' results, whether the skill is loaded, whether loading it failed)."""
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    seen, loaded, failed = [], False, False
    for message in body.get("messages") or []:
        for b in _blocks(message.get("content")):
            kind = b.get("type")
            if kind == "tool_use":
                calls[str(b.get("id"))] = (
                    str(b.get("name")),
                    dict(b.get("input") or {}),
                )
            elif kind == "tool_result":
                name, args = calls.get(str(b.get("tool_use_id")), ("", {}))
                text = fake_text(b)
                if name == "Skill":
                    failed = failed or bool(b.get("is_error")) or SKILL not in text
                elif name.startswith(PREFIX):
                    seen.append(Seen(name.removeprefix(PREFIX), args, text))
            elif kind == "text" and message.get("role") == "user":
                loaded = loaded or SKILL_HEADING in str(b.get("text", ""))
    return seen, loaded, failed and not loaded


def offered(body: Mapping[str, Any]) -> bool:
    """Whether the CLI told the model about the skill (its skill list: the system prompt, a
    tool's description, or the text of a turn that isn't the model's own). A model loads only
    a skill it's told about, and the CLI leaves out one whose front matter it can't read."""
    texts = [json.dumps(body.get("system")), json.dumps(body.get("tools"))]
    for message in body.get("messages") or []:
        if message.get("role") == "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        texts += [
            str(b.get("text", "")) for b in _blocks(content) if b.get("type") == "text"
        ]
    return any(SKILL in text for text in texts)


def fake_text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in _blocks(content))


def deep_policy(plans: Mapping[str, Question]) -> Policy:
    """The deep lane's model in ``--fake``: load the skill, resolve, call its tools, answer."""

    def policy(body: dict[str, Any]) -> ApiTurn:
        from gregtec.llm import fake_api

        found = REQUEST.search(fake_api.request_text(body))
        q = plans.get(" ".join(found["text"].split())) if found else None
        if q is None:
            return ApiTurn(text="Done.")
        seen, loaded, failed = deep_seen(body)
        if failed:
            return ApiTurn(
                text="The recipe-lookup skill didn't load, so I can't answer."
            )
        if not loaded and not offered(body):
            return ApiTurn(
                text="The recipe-lookup skill isn't offered, so I can't answer."
            )
        if not loaded:
            return ApiTurn(
                text="Loading the recipe-lookup skill.",
                tools=[("Skill", {"skill": SKILL})],
            )
        plan = decide(q, seen, tools=True, resolve_first=True)
        if plan.calls and plan.ask is None:
            return ApiTurn(
                text="Looking it up.",
                tools=[(PREFIX + name, args) for name, args in plan.calls],
            )
        return ApiTurn(text=deep_answer(q, plan))

    return policy


# --- the run ----------------------------------------------------------------------------


def run_fake(
    cfg: config.Config,
    questions: Sequence[Question],
    ground: recipe_eval.Ground,
    out: TextIO,
) -> tuple[int, list[recipe_eval.Result]]:
    """Both oracles over GOS_HOME's DB: no key, no cost, nothing recorded or written to the
    vault (the registry has none; the skill comes from the repo's vault-template)."""
    plans = recipe_eval.plans(questions)
    db = access.db_path(cfg.gos_home)
    glossary = Glossary.find(cfg.gos_home / "vault")
    with FakeMessagesAPI(deep_policy(plans)) as api:
        lane = DeepLaneService(
            None,
            Settings.from_config(cfg),
            home=cfg.data_dir / "agent-eval-recipe-lookup",  # not deep-lane eval's
            key=lambda: FAKE_KEY,
            meter=None,  # simulated costs never reach llm_usage
            env=api.env(),
            task_label=recipe_eval.TASK,
            template=vault_deploy.DEFAULT_TEMPLATE,
        )
        registry = build_registry(db, glossary, deep=lane)
        lane.registry = registry
        fast = FastLane(OracleClient(plans=plans), registry, meter=None)
        try:
            return recipe_eval.run(
                lambda: Asker(registry, lane, fast),
                questions,
                ground,
                out,
                fake=True,
                deep=lane,
            )
        finally:
            lane.stop()
