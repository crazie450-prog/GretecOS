"""Fake Messages API clients: the fast lane's tests and ``gos eval fast-lane --fake`` (D153).

No test or CI run calls the API (there is no key there); these stand in for
``anthropic.Anthropic`` with the same streaming shape: ``client.messages.stream(**request)`` is a
context manager that yields ``content_block_delta`` / ``text_delta`` events and then gives
``get_final_message()`` (content blocks, ``stop_reason``, ``usage``), and
``client.messages.create(**request)`` returns a final message. Every request is kept in
``requests`` so a test can check what was sent (model, thinking, cache breakpoints, tools).

- :class:`ScriptedClient` answers with the turns it is given, in order.
- :class:`EchoClient` answers like a well-behaved model would, from the tool results in the
  request: the resolved item first, the first row's id, and a number copied from that row. An
  ambiguous result gets the clarifying question. It exercises the lane, the number check and
  the meter; it says nothing about how the real model answers.

Usage is simulated: the request's characters / 4 as tokens, the tools and system prompt
written to the cache on the first call and read from it on later ones.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Self

from gregtec.agents.tools.tsv import parse

TOOL_BLOCK = re.compile(
    r'<tool_result tool="(?P<tool>[a-z_]+)"[^>]*>\n(?P<text>.*?)\n</tool_result>',
    re.DOTALL,
)


@dataclass
class Turn:
    """One scripted model turn: text, tool calls ({"name", "input"}) and the stop reason."""

    text: str = ""
    tools: Sequence[dict[str, Any]] = ()
    stop_reason: str | None = None  # default: tool_use when tools, else end_turn


def _message(turn: Turn, usage: SimpleNamespace, n: int) -> SimpleNamespace:
    content: list[SimpleNamespace] = []
    if turn.text:
        content.append(SimpleNamespace(type="text", text=turn.text))
    for i, use in enumerate(turn.tools):
        content.append(
            SimpleNamespace(
                type="tool_use",
                id=f"toolu_fake_{n}_{i}",
                name=use["name"],
                input=use["input"],
            )
        )
    stop = turn.stop_reason or ("tool_use" if turn.tools else "end_turn")
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage)


class _Stream:
    def __init__(self, message: SimpleNamespace, latency_s: float, chunk: int) -> None:
        self.message = message
        self.latency_s = latency_s
        self.chunk = chunk

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def __iter__(self) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(type="message_start")
        if self.latency_s:
            time.sleep(self.latency_s)
        for block in self.message.content:
            if block.type != "text":
                continue
            for i in range(0, len(block.text), self.chunk):
                delta = SimpleNamespace(
                    type="text_delta", text=block.text[i : i + self.chunk]
                )
                yield SimpleNamespace(type="content_block_delta", delta=delta)
        yield SimpleNamespace(type="message_stop")

    def get_final_message(self) -> SimpleNamespace:
        return self.message


@dataclass
class _Messages:
    owner: FakeClient

    def stream(self, **request: object) -> _Stream:
        return self.owner.respond(request, streamed=True)

    def create(self, **request: object) -> SimpleNamespace:
        return self.owner.respond(request, streamed=False).message


@dataclass
class FakeClient:
    """The shared machinery; subclasses decide each turn (:meth:`turn`)."""

    latency_s: float = 0.0  # a pause before the first text token
    chunk: int = 16  # characters per text delta
    requests: list[dict[str, Any]] = field(default_factory=list)
    _cached: set[str] = field(default_factory=set)

    @property
    def messages(self) -> _Messages:
        return _Messages(self)

    def turn(self, request: dict[str, Any]) -> Turn:
        raise NotImplementedError

    def _usage(self, request: dict[str, Any], output: str) -> SimpleNamespace:
        prefix = json.dumps(
            [request.get("tools"), request.get("system")], sort_keys=True
        )
        rest = json.dumps(request.get("messages"), default=str)
        cached = prefix if request.get("system") else ""
        hit = cached in self._cached
        if cached:
            self._cached.add(cached)
        size = len(cached) // 4
        return SimpleNamespace(
            input_tokens=len(rest) // 4 + (0 if cached else len(prefix) // 4),
            output_tokens=len(output) // 4 + 1,
            cache_creation_input_tokens=0 if hit else size,
            cache_read_input_tokens=size if hit else 0,
        )

    def respond(self, request: dict[str, Any], *, streamed: bool) -> _Stream:
        self.requests.append(request)
        if request.get("max_tokens") == 0:  # a cache pre-warm: no answer
            turn = Turn(stop_reason="max_tokens")
        else:
            turn = self.turn(request)
        usage = self._usage(request, turn.text)
        message = _message(turn, usage, len(self.requests))
        return _Stream(message, self.latency_s if streamed else 0.0, self.chunk)


@dataclass
class ScriptedClient(FakeClient):
    turns: list[Turn] = field(default_factory=list)

    def turn(self, request: dict[str, Any]) -> Turn:
        if not self.turns:
            raise AssertionError("the scripted client ran out of turns")
        return self.turns.pop(0)


def _texts(content: object) -> list[str]:
    """The text of a request message's content: a string, or blocks (tool results too)."""
    if isinstance(content, str):
        return [content]
    out = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                out.append(str(block.get("text", "")))
            elif block.get("type") == "tool_result":
                out.append(str(block.get("content", "")))
    return out


@dataclass
class EchoClient(FakeClient):
    def turn(self, request: dict[str, Any]) -> Turn:
        messages = request.get("messages") or []
        last = _texts(messages[-1].get("content", "")) if messages else []
        if any(t.startswith("Gregtec OS didn't send") for t in last):
            return Turn("I don't know that number; I can look it up.")
        results: list[tuple[str, str]] = []
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                results += [
                    (m["tool"], m["text"]) for m in TOOL_BLOCK.finditer(content)
                ]
            else:
                results += [("tool", text) for text in _texts(content)]
        return Turn(answer(results))


def answer(results: Sequence[tuple[str, str]]) -> str:
    """What a careful model would say from ``results`` ((tool, text) pairs)."""
    item = ""
    for _, text in results:
        p = parse(text)
        if p.head.get("ambiguous") == "1":
            names = [r.get("name", "") for r in p.rows[:3]]
            return f"Which did you mean: {', '.join(names[:-1])} or {names[-1]}?"
        item = item or p.head.get("item", "") or p.head.get("name", "")
        if p.rows and p.rows[0].get("name") and p.head.get("ambiguous") == "0":
            item = item or p.rows[0]["name"]
    for tool, text in reversed(results):
        p = parse(text)
        if not p.rows:
            continue
        row = p.rows[0]
        ref = row.get("ref") or row.get("quest") or row.get("cite")
        if not ref:
            continue
        name = item.split(" [")[0] or row.get("name", "it")
        if row.get("eut"):
            return f"{name}: the first recipe runs in {row['machine']} at {row['eut']} EU/t ({ref})."
        if row.get("machine"):
            return f"{name}: the first recipe is {row['machine']} ({ref})."
        if row.get("quest"):
            return (
                f"{row.get('name', 'A quest')} ({ref}) is the first quest that matches."
            )
        return f"{name}: {row.get('key', '')} is {row.get('value', '')} ({ref})."
    if item:
        return f"{item.split(' [')[0]}: I found no recipe rows for it."
    return "General advice: I can look that up if you name the item."
