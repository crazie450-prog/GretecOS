"""The planner model as :data:`gregtec.planner.explain.Writer` (WO-3.7, plan §7.1 step 11,
[QA:D4], D199, D251).

``plan_explain`` (the line-designer skill's explanation) runs :func:`gregtec.planner.explain.
explain`, which hands the model only the metrics JSON, checks its text with the numeric
validator, retries once and falls back to the template. The model behind it is one Messages
API call per attempt on the planner model (``[models] planner``), with no tools: a writer, never
a judge of the order (the ranking is rank.py's). Each call is recorded in ``llm_usage`` (lane
``deep``, task :data:`TASK`) when there is a meter. A failed call raises :class:`WriterError`,
and the tool then gives the template.

:func:`factory` is what the core hands the tool registry: it reads the stored key when a tool
first asks, so no key means no writer (the template, D158's no-AI path).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import anthropic
import keyring.errors

from gregtec.agents import fast_lane
from gregtec.llm import keys
from gregtec.llm.prices import PRICES, estimate_cost
from gregtec.llm.usage import UsageMeter
from gregtec.planner.explain import Writer

log = logging.getLogger(__name__)

LANE = "deep"
TASK = "plan_explain"
# The prompt asks for at most 12 short lines; the rest is room for the model's thinking.
MAX_TOKENS = 4000
# One call inside a deep-lane task, so not the fast lane's 15 s.
TIMEOUT_S, RETRIES = 120.0, 1

Factory = Callable[[], Writer | None]


class WriterError(RuntimeError):
    """The planner model couldn't be reached (an API error, a timeout)."""


def messages_writer(
    client: fast_lane.Client,
    model: str,
    *,
    meter: UsageMeter | None = None,
    on_usd: Callable[[float], None] | None = None,
) -> Writer:
    """A writer that sends each prompt as one user message to ``model`` (streamed, read as
    the final message). ``on_usd`` gets each call's cost (the cost eval's per-task total); a
    refusal or an empty answer comes back as ``""``, which the validator refuses."""

    def write(prompt: str) -> str:
        try:
            with client.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.APIError as exc:
            raise WriterError(f"{type(exc).__name__}: {exc}") from exc
        usage = fast_lane._usage(message)
        if meter is not None:
            usd = meter.record(LANE, model, usage, task=TASK)
        else:
            usd = estimate_cost(model, usage) if model in PRICES else 0.0
        if on_usd is not None:
            on_usd(usd)
        if message.stop_reason == "refusal":
            return ""
        return "".join(
            b.text for b in message.content if getattr(b, "type", None) == "text"
        )

    return write


def client_for(key: str, base_url: str | None = None) -> anthropic.Anthropic:
    """The writer's Messages client (``base_url``: the cost eval's fake API)."""
    return anthropic.Anthropic(
        api_key=key,
        base_url=base_url,
        timeout=anthropic.Timeout(TIMEOUT_S, connect=fast_lane.CONNECT_S),
        max_retries=RETRIES,
    )


def factory(
    model: str,
    meter: UsageMeter | None,
    *,
    key: Callable[[], str] | None = None,
    base_url: str | None = None,
    on_usd: Callable[[float], None] | None = None,
) -> Factory:
    """The core's writer on ``model``: ``key`` (default the stored Anthropic key) read when a
    tool first asks; no key, no writer (None: the template)."""

    def make() -> Writer | None:
        try:
            secret = key() if key is not None else keys.get_key(keys.ANTHROPIC)
        except keys.MissingKeyError:
            return None
        except keyring.errors.KeyringError as exc:
            log.warning("keyring unavailable", extra={"error": type(exc).__name__})
            return None
        return messages_writer(
            client_for(secret, base_url), model, meter=meter, on_usd=on_usd
        )

    return make
