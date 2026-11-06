"""Claude API prices for cost estimates (plan section 4.4). API prices, not game numbers.

``gos smoke`` (WO-0.4) and the ``llm_usage`` meter (WO-2.1) both price usage from this table, so
there is one copy to re-check (QA:A12).
"""

from __future__ import annotations

from dataclasses import dataclass

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
FABLE = "claude-fable-5-1"


@dataclass(frozen=True)
class Price:
    """USD per million tokens. ``cache_write`` is the 5-minute rate."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# Haiku and Sonnet: platform.claude.com/docs/en/about-claude/pricing, checked 2026-11-03 (WO-0.4).
# Opus and Fable: plan section 4.4 and [D§1.10] (Fable's cache read is from [D§1.10]; section 4.4
# shows none), with cache writes at 1.25x input [D§1.10]. Not re-checked since; re-check before
# WO-2.4 measures deep-lane costs.
PRICES_CHECKED = "2026-11-03"
PRICES = {
    HAIKU: Price(input=0.10, output=0.50, cache_write=0.125, cache_read=0.01),
    SONNET: Price(input=2.00, output=10.00, cache_write=2.50, cache_read=0.10),
    OPUS: Price(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20),
    FABLE: Price(input=10.00, output=50.00, cache_write=12.50, cache_read=0.25),
}
# Haiku 5.5 bills a request whose prompt (cache reads and writes included) is over 100k
# tokens at 5x, for the whole request.
HAIKU_LONG_PROMPT = 100_000
HAIKU_LONG = Price(input=0.50, output=2.50, cache_write=0.625, cache_read=0.05)


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    def add(self, other: Usage) -> None:
        self.input += other.input
        self.output += other.output
        self.cache_write += other.cache_write
        self.cache_read += other.cache_read

    def __str__(self) -> str:
        cache = (
            f", cache write {self.cache_write} / read {self.cache_read}"
            if self.cache_write or self.cache_read
            else ""
        )
        return f"{self.input} in / {self.output} out tokens{cache}"


def estimate_cost(model: str, usage: Usage) -> float:
    """Estimated USD for one request's usage. Haiku 5.5 is priced per request by prompt size.

    Raises ``KeyError`` for a model that isn't in ``PRICES``: a cost is never guessed.
    """
    price = PRICES[model]
    prompt = usage.input + usage.cache_write + usage.cache_read
    if model == HAIKU and prompt > HAIKU_LONG_PROMPT:
        price = HAIKU_LONG
    return (
        usage.input * price.input
        + usage.output * price.output
        + usage.cache_write * price.cache_write
        + usage.cache_read * price.cache_read
    ) / 1_000_000
