"""WO-3.7 review H1: the planner model as explain's Writer (gregtec.agents.plan_writer), against a
fake Messages client: one user message per attempt, its cost reported, a refusal read as empty
text (the validator refuses it), an API error raised as WriterError (the tool then uses the
template)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from gregtec.agents import plan_writer
from gregtec.llm.prices import PRICES, Usage, estimate_cost

MODEL = next(iter(PRICES))


class FakeMessages:
    def __init__(self, reply: object) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:  # noqa: ANN401
        self.requests.append(kwargs)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply

    def stream(self, **kwargs: Any) -> Any:  # noqa: ANN401
        raise AssertionError("the writer doesn't stream")


def message(text: str, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )


def client(reply: object) -> SimpleNamespace:
    return SimpleNamespace(messages=FakeMessages(reply))


def test_one_message_per_attempt_and_its_cost() -> None:
    c = client(message("The first route ranks first."))
    spent: list[float] = []
    write = plan_writer.messages_writer(c, MODEL, on_usd=spent.append)
    assert write("the prompt") == "The first route ranks first."
    (req,) = c.messages.requests
    assert req["model"] == MODEL and req["max_tokens"] == plan_writer.MAX_TOKENS
    assert req["messages"] == [{"role": "user", "content": "the prompt"}]
    assert "tools" not in req  # a writer, nothing else
    assert spent == [estimate_cost(MODEL, Usage(input=1000, output=200))]


def test_a_refusal_is_empty_text() -> None:
    write = plan_writer.messages_writer(client(message("partial", "refusal")), MODEL)
    assert write("p") == ""


def test_an_api_error_is_a_writer_error() -> None:
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))
    write = plan_writer.messages_writer(client(err), MODEL)
    with pytest.raises(plan_writer.WriterError, match="APIConnectionError"):
        write("p")


def test_no_key_no_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise plan_writer.keys.MissingKeyError(name)

    monkeypatch.setattr(plan_writer.keys, "get_key", missing)
    assert plan_writer.factory(MODEL, None)() is None
