"""One repair-round engine for every tool-less shell designer (WO-10.17, D423).

The shell bake-off (:mod:`gregtec.build.shell_bakeoff`) and the live shell designer
(:mod:`gregtec.build.shell_designer`) ask a model the same way: the brief, then at most
:data:`REPAIR_ROUNDS` more tries, each with the refused answer and the gate's reasons
(:func:`gregtec.build.shell_brief.repair_brief`). Both use these two pieces so they cannot drift:

- :class:`Capped`: a designer whose every try (:func:`gregtec.llm.designers.call`'s retries
  included) passes ``guard`` first, and whose try that broke off after it was sent (or was
  answered without a token count) is reported to ``broke``.
- :func:`rounds`: the first answer and the repairs. A refusal, an answer cut off at the output
  limit, an answer that isn't a design and a gate refusal each end a round with one reason.

The callers keep their own records, caps and stops: the callbacks are theirs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from gregtec.build import shell_brief
from gregtec.llm import designers as llm

REPAIR_ROUNDS = 2  # after the first answer, for every model and every step


@dataclass
class Capped:
    """``inner`` inside the caller's caps: ``guard(user)`` before every try (it raises to stop),
    ``broke(user)`` after a try that may be billed although no usage came back."""

    inner: llm.Designer
    guard: Callable[[str], None]
    broke: Callable[[str], None]

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(
        self, system: str, user: str, schema: Mapping[str, Any], max_output_tokens: int
    ) -> llm.Reply:
        self.guard(user)
        try:
            reply = self.inner.complete(system, user, schema, max_output_tokens)
        except llm.ProviderError as exc:
            if exc.maybe_billed:
                self.broke(user)
            raise
        if not reply.measured:  # answered, but the vendor sent no token count with it
            self.broke(user)
        return reply


def rounds(
    ask: Callable[[str], llm.Reply | str],
    brief: str,
    mode: str,
    gate: Callable[[shell_brief.Answer], str | None],
    on_refuse: Callable[[str], None],
    on_round: Callable[[int], None],
    out_tokens: int = shell_brief.MAX_OUTPUT_TOKENS,
    echo: int = shell_brief.MAX_ECHO,
) -> bool:
    """The first answer and at most :data:`REPAIR_ROUNDS` more, each read for ``mode`` and
    handed to ``gate`` (which saves an accepted design and returns None, or says why it
    refused). ``ask`` gives an answer or why there is none (which ends the rounds, as a
    model's refusal does); ``on_round(n)`` is told each round before its answer is asked for
    (0 the first) and ``on_refuse(why)`` each refused answer. True once the gate accepted."""
    user = brief
    for attempt in range(REPAIR_ROUNDS + 1):
        on_round(attempt)
        got = ask(user)
        if isinstance(got, str):
            on_refuse(got)
            return False
        if got.stop == llm.REFUSAL:
            on_refuse(got.detail or "the model declined")
            return False
        if got.stop == llm.MAX_TOKENS:
            why = (
                f"the answer was cut off at the output limit "
                f"({out_tokens} tokens): use fewer operations"
            )
        else:
            try:
                said = shell_brief.read(got.text, mode)
            except shell_brief.BriefError as exc:
                why = str(exc)
            else:
                refused = gate(said)
                if refused is None:
                    return True
                why = refused
        on_refuse(why)
        user = shell_brief.repair_brief(brief, got.text, why, echo=echo)
    return False
