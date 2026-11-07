"""The fake models of ``gos eval recipe-lookup --fake`` (WO-2.7, D172): oracles for both lanes.

An oracle knows each eval row's references (its :class:`~gregtec.agents.recipe_eval.Step`
list), as a test oracle does, and does what the recipe-lookup skill says a careful model does
with them: it uses the pre-retrieved results first, calls the tools the skill names
(``recipe_find``, ``recipe_uses``, ``machine_info``, ``more``) for what they don't cover,
picks the row each step means from the tools' text (never from the DB), asks when a result is
ambiguous, and answers by copying that row's numbers next to its id.

- :class:`OracleClient` is the fast lane's model (a :class:`gregtec.llm.fake.FakeClient`), so
  the real router, pre-retrieval, ``FastLane`` loop (at most 3 calls a turn), tools and number
  check run around it.
- :func:`deep_policy` is the deep lane's: the real Claude Code CLI runs against
  :class:`gregtec.llm.fake_api.FakeMessagesAPI` with it, so the skill must deploy and load
  (``Skill`` ``gos:recipe-lookup``, first) and the tools come through the ``gos`` MCP server
  and the hooks. Its answer is the deep lane's shape: a spoken first sentence without ids, then
  one ``key: value`` line per cited recipe (the skill's section 4).

The scorer (:mod:`gregtec.agents.recipe_eval`) reads the DB itself, so the oracle can only pass
by finding the right rows through the tools. It checks the plumbing, not a model: a question
the oracle has no row for gets EchoClient's answer.
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
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.recipe_eval import MACHINE, MAKE, Question, Step
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.mcp import PREFIX
from gregtec.agents.tools.tsv import Parsed, parse
from gregtec.db import access
from gregtec.llm import fake
from gregtec.llm.fake import FakeClient, Turn
from gregtec.llm.fake_api import ApiTurn, FakeMessagesAPI, Policy
from gregtec.vault import deploy as vault_deploy

SKILL = "gos:recipe-lookup"
SKILL_HEADING = "# Recipe lookup"  # the first line of the loaded skill's body
FAKE_KEY = "sk-ant-fake-eval-000000000000000000000000000000"
PREFETCHED = re.compile(
    r'<tool_result tool="(?P<tool>[a-z_]+)" args=\'(?P<args>[^\']*)\'>\n'
    r"(?P<text>.*?)\n</tool_result>",
    re.DOTALL,
)
REQUEST = re.compile(r"<request>\n(?P<text>.*?)\n</request>", re.DOTALL)
_REF_IN = re.compile(r" \((?:r|mte):[^)]*\)")  # "(r:...)" in a sentence: not spoken
TIERS = recipe_eval.TIERS


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


def lookup(step: Step) -> tuple[str, dict[str, Any]]:
    """The call that answers ``step``: the tool the skill names for it."""
    if step.op == MACHINE:
        return "machine_info", {"machine": step.key}
    if step.op == MAKE:
        args: dict[str, Any] = {"item": step.key}
        if (machine := step.filter("map")) is not None:
            args["machine"] = machine
        if (tier := step.filter("tier")) is not None:
            args["tier_max"] = tier
        return "recipe_find", args
    out, machine = step.filter("out"), step.filter("map")
    if (
        out is not None and machine is not None
    ):  # uses in one machine: its recipes for out
        return "recipe_find", {"item": out, "machine": machine}
    return "recipe_uses", {"item": step.key}


def _filters(tool: str, args: Mapping[str, Any]) -> str:
    """The ``filters:`` head line a recipe tool writes for ``args`` ('' for none)."""
    if tool != "recipe_find":
        return ""
    pairs = (("tier_max", args.get("tier_max")), ("machine", args.get("machine")))
    return " ".join(f"{k}={v}" for k, v in pairs if v)


def _about(step: Step, seen: Seen) -> bool:
    """Whether a result is a page of ``step``'s own lookup (its item and filters; a ``more``
    page repeats the head of the call it continues)."""
    tool, args = lookup(step)
    p = seen.parsed
    if tool == "machine_info":
        return seen.tool == tool and head_key(p) == step.key
    if seen.tool not in (tool, "more") or head_key(p) != args["item"]:
        return False
    return p.head.get("filters", "") == _filters(tool, args)


def _cursor(seen: Seen) -> str | None:
    """The result's ``more:`` cursor (the line after its last ``rows:`` line)."""
    lines = seen.text.split("\n")
    rows = [i for i, line in enumerate(lines) if line.startswith("rows: ")]
    if rows and rows[-1] + 1 < len(lines) and lines[rows[-1] + 1].startswith("more: "):
        return lines[rows[-1] + 1].removeprefix("more: ")
    return None


def _source(step: Step, seen: Seen) -> bool:
    """Whether a result can hold rows of ``step``: a recipe result about its item (or, for a
    use step with ``out=``, about that output). A step that wants a count takes its rows
    only from its own lookup, whose total is that count."""
    p = seen.parsed
    if seen.tool not in ("recipe_find", "recipe_uses", "more") or not p.columns:
        return False
    if "count" in step.want:
        return _about(step, seen)
    key = head_key(p)
    if step.op == MAKE:
        return key == step.key and seen.tool != "recipe_uses"
    return key in (step.key, step.filter("out"))


def find(step: Step, seen: Sequence[Seen]) -> Found | None:
    """The row ``step`` means among the results, or its lookup's answer when that has no
    row (and no further page): None while the oracle should look further."""
    if step.op == MACHINE:
        info = [s for s in seen if _about(step, s)]
        return Found(info[0], None) if info else None
    empty = None
    for s in seen:
        if not _source(step, s):
            continue
        p = s.parsed
        own = head_key(p) == step.key
        for row in p.rows:
            takes = step.op == MAKE or own or _slot(row.get("in", ""), step.key)
            if takes and matches(step, row):
                return Found(s, row)
        if _about(step, s) and _cursor(s) is None:
            empty = empty or Found(s, None)
    return empty


def next_call(step: Step, seen: Sequence[Seen]) -> tuple[str, dict[str, Any]] | None:
    """The next call for a step with no row yet: its lookup, else that lookup's next page;
    None when the pages ran out."""
    mine = [s for s in seen if _about(step, s)]
    if not mine:
        return lookup(step)
    cursor = _cursor(mine[-1])
    return ("more", {"cursor": cursor}) if cursor else None


def ambiguous(seen: Sequence[Seen]) -> list[str] | None:
    """The top three names when a result says ambiguous (the skill: ask, don't guess)."""
    for s in seen:
        p = s.parsed
        if p.head.get("ambiguous") == "1":
            return [r.get("name", "") for r in p.rows[:3]]
    return None


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
    if name in recipe_eval.SLOT_WANTS:
        slot = _slot(row.get("out" if name in ("out", "chance") else "in", ""), key)
        if slot is None:
            return ""
        if name == "chance":
            return f"{slot['chance_pct']}% chance of {slot['name']}"
        if name == "alts":
            return f"{slot['name']} or {slot['alternatives']} other stacks"
        unit = "L" if slot["unit"] == "L" else "x"
        return f"{slot['amount']}{unit} {slot['name']}"
    if name == "cond":
        cond = dict(
            c.split("=", 1) for c in row.get("cond", "").split("; ") if "=" in c
        )
        return f"{key} {cond.get(key, '')}"
    return ""


def sentence(step: Step, found: Found | None) -> str:
    """One step's answer: the item first, the row's id, the wanted values copied."""
    if found is None:
        return f"I couldn't find the row for {step.text()} through the tools."
    p = found.seen.parsed
    name = head_name(p) or step.key
    if found.seen.tool == "machine_info":
        parts = [v for w in step.want if (v := _value(step, w, found))]
        maps = p.head.get("maps", "")
        said = ", ".join(parts) if parts else f"it runs {maps.split(' [')[0]}"
        return f"{name} ({head_key(p)}): {said}."
    if found.row is None:
        verb = "makes" if step.op == MAKE else "takes"
        return f"{name}: no recipe in the DB {verb} it."
    row = found.row
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
    keys = (
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
    return ", ".join(f"{k}: {row[k]}" for k in keys if row.get(k))


@dataclass
class Plan:
    """What the oracle does next for a question: tool calls, or its answer."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    found: list[Found | None] = field(default_factory=list)
    ask: list[str] | None = None


def decide(q: Question, seen: Sequence[Seen], tools: bool) -> Plan:
    plan = Plan(ask=ambiguous(seen))
    if plan.ask:
        return plan
    for step in q.steps:
        got = find(step, seen)
        plan.found.append(got)
        call = next_call(step, seen) if got is None and tools else None
        if call is not None and call not in plan.calls:
            plan.calls.append(call)
    return plan


def ask_text(names: list[str]) -> str:
    names = [n for n in names if n] or ["one of them"]
    listed = ", ".join(names[:-1]) + f" or {names[-1]}" if len(names) > 1 else names[0]
    return f"Which did you mean: {listed}?"


def fast_answer(q: Question, plan: Plan) -> str:
    if plan.ask is not None:
        return ask_text(plan.ask)
    return " ".join(sentence(s, f) for s, f in zip(q.steps, plan.found, strict=True))


def deep_answer(q: Question, plan: Plan) -> str:
    """The deep lane's shape (its prompt and the skill): a spoken first sentence without ids,
    then one ``key: value`` line per cited row (a step with no row says so instead)."""
    if plan.ask is not None:
        return ask_text(plan.ask)
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


def fake_text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in _blocks(content))


def deep_policy(plans: Mapping[str, Question]) -> Policy:
    """The deep lane's model in ``--fake``: load the skill, call its tools, answer."""

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
        if not loaded:
            return ApiTurn(
                text="Loading the recipe-lookup skill.",
                tools=[("Skill", {"skill": SKILL})],
            )
        plan = decide(q, seen, tools=True)
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
            home=cfg.data_dir / "agent-eval",
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
