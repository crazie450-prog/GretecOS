"""``gos smoke`` (WO-0.4): check that both lanes of plan section 4.4 answer with the stored key.

- **Fast lane:** N streamed Messages API calls to Haiku 5.5 with thinking disabled and effort
  low. Each prints its time to the first text token and its total time, then the medians (V21:
  no time-to-first-token figure is published for Haiku 5.5 with thinking off).
- **Deep lane:** one Claude Agent SDK query on Sonnet 5.5 with no built-in tools, no MCP servers,
  no settings files and a small ``max_budget_usd`` cap. The CLI's own init message must say it
  used the stored key and loaded no tools or MCP servers, or the lane fails.

The key comes from the OS keyring (``keys``) and goes only to this process: the API client's
``api_key`` and the SDK subprocess's environment. It is never put in the user or machine
environment, where interactive Claude Code would offer to use it. Token use and cost estimates
are printed; the costs come from ``PRICES``, which are API prices, not game numbers. Nothing is
retried: a smoke test should show the first error as it is.
"""

from __future__ import annotations

import asyncio
import os
import re
import statistics
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TextIO

import anthropic
import claude_agent_sdk
import keyring.errors
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
)

from gregtec.llm import keys

if TYPE_CHECKING:
    import httpx2

FAST_MODEL = "claude-haiku-5-5"
DEEP_MODEL = "claude-sonnet-5-5"
FAST_PROMPT = "Reply with a short greeting of at most five words."
FAST_MAX_TOKENS = 256  # a five-word reply; a reply that hits this has reasoning in it
DEEP_PROMPT = "Reply with the single word: ready"
DEEP_BUDGET_USD = 0.10  # a cap, not an estimate: one short turn should cost far less

# Credentials the Agent SDK's CLI ranks above ANTHROPIC_API_KEY (Claude Code authentication docs),
# checked before it starts. A gateway sign-in or managed login settings outrank the key too;
# the CLI's init message (apiKeySource) catches those.
OUTRANKING_ENV = (
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

QueryFn = Callable[..., AsyncIterator[Any]]


@dataclass(frozen=True)
class Price:
    """USD per million tokens. ``cache_write`` is the 5-minute rate."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# platform.claude.com/docs/en/about-claude/pricing, checked 2026-11-03 (plan section 4.4, QA:A12).
PRICES_CHECKED = "2026-11-03"
PRICES = {
    FAST_MODEL: Price(input=0.10, output=0.50, cache_write=0.125, cache_read=0.01),
    DEEP_MODEL: Price(input=2.00, output=10.00, cache_write=2.50, cache_read=0.10),
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
    """Estimated USD for one request's usage. Haiku 5.5 is priced per request by prompt size."""
    price = PRICES[model]
    prompt = usage.input + usage.cache_write + usage.cache_read
    if model == FAST_MODEL and prompt > HAIKU_LONG_PROMPT:
        price = HAIKU_LONG
    return (
        usage.input * price.input
        + usage.output * price.output
        + usage.cache_write * price.cache_write
        + usage.cache_read * price.cache_read
    ) / 1_000_000


# The usage tier's monthly spend cap (a 429 with no retry-after; retries keep failing).
TIER_CAP = "enforced_spend_limit_reached"
# The API says "Your credit balance is too low", Claude Code "Credit balance too low".
CREDIT_LOW = re.compile(r"credit balance (is )?too low")


def explain_api_error(status: int | None, message: str, code: str | None = None) -> str:
    """A line Clayton can act on. The out-of-money errors look different (plan D48).

    ``code`` is the API's ``error.details.error_code`` or the Agent SDK's
    ``AssistantMessage.error``. The wording matched is from the rate-limits and API credits docs.
    """
    lowered = message.lower()
    if "specified workspace api usage limits" in lowered:
        reason = (
            "the workspace spend limit is reached; raise it in Console > Settings > "
            "Workspaces, or wait for the date in the message"
        )
    elif "specified api usage limits" in lowered:
        reason = (
            "the organization spend limit set in Console > Billing is reached; raise it "
            "there, or wait for the date in the message"
        )
    elif code == TIER_CAP or TIER_CAP in lowered:
        reason = (
            "the organization's monthly spend cap for its usage tier is reached. "
            "Retrying fails until the date in the message; a higher tier restores access"
        )
    elif CREDIT_LOW.search(lowered) or code == "billing_error":
        reason = (
            "credit balance too low: the Max API credits are not claimed, are used up, "
            "or expired with the billing cycle; see Console > Billing"
        )
    elif status == 401:
        reason = (
            "authentication failed: the stored key is wrong, revoked or expired. "
            "Store a new one with: uv run gos key set anthropic"
        )
    elif status == 402:
        reason = "billing problem; see Console > Billing"
    elif status == 403:
        reason = (
            "this key is not allowed to do that; check its workspace in the Console"
        )
    elif status == 404:
        reason = "model not found, or not available to this organization"
    elif status == 429:
        reason = "rate limited: wait a minute and run it again"
    elif status is not None and status >= 500:
        reason = "the API had a server error or is overloaded: run it again later"
    else:
        reason = "API error"
    where = f"HTTP {status}" if status is not None else "no HTTP status"
    return f"{reason} ({where}: {message})"


def _error_parts(exc: anthropic.APIStatusError) -> tuple[str, str | None]:
    """The API's own error message and ``error.details.error_code``, if the body has them."""
    error = exc.body.get("error") if isinstance(exc.body, dict) else None
    if not isinstance(error, dict):
        return exc.message, None
    details = error.get("details")
    code = details.get("error_code") if isinstance(details, dict) else None
    return str(error.get("message") or exc.message), code


@dataclass
class FastRun:
    start_ms: float | None  # message_start arrived
    first_token_ms: float | None  # first text_delta arrived
    total_ms: float
    usage: Usage
    text: str
    stop_reason: str | None
    workspace_id: str | None


def _fast_call(client: anthropic.Anthropic, clock: Callable[[], float]) -> FastRun:
    sent = clock()
    started = first = None
    with client.messages.stream(
        model=FAST_MODEL,
        max_tokens=FAST_MAX_TOKENS,
        thinking={"type": "disabled"},
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": FAST_PROMPT}],
    ) as stream:
        for event in stream:
            if event.type == "message_start" and started is None:
                started = clock()
            elif (
                event.type == "content_block_delta"
                and event.delta.type == "text_delta"
                and first is None
            ):
                first = clock()
        done = clock()
        message = stream.get_final_message()
        workspace_id = stream.workspace_id

    def ms(t: float | None) -> float | None:
        return None if t is None else (t - sent) * 1000

    u = message.usage
    return FastRun(
        start_ms=ms(started),
        first_token_ms=ms(first),
        total_ms=(done - sent) * 1000,
        usage=Usage(
            input=u.input_tokens,
            output=u.output_tokens,
            cache_write=u.cache_creation_input_tokens or 0,
            cache_read=u.cache_read_input_tokens or 0,
        ),
        text="".join(b.text for b in message.content if b.type == "text"),
        stop_reason=message.stop_reason,
        workspace_id=workspace_id,
    )


def _ms(value: float | None) -> str:
    return "none" if value is None else f"{value:.0f} ms"


def fast_lane(
    client: anthropic.Anthropic, runs: int, out: TextIO, clock: Callable[[], float]
) -> bool:
    print(
        f"Fast lane: {FAST_MODEL}, thinking disabled, effort low, streamed, runs: {runs}",
        file=out,
    )
    done: list[FastRun] = []
    for n in range(1, runs + 1):
        try:
            run = _fast_call(client, clock)
        except anthropic.APIStatusError as exc:
            message, code = _error_parts(exc)
            print(
                f"  run {n} failed: {explain_api_error(exc.status_code, message, code)}",
                file=out,
            )
            return False
        except anthropic.APIConnectionError as exc:
            print(f"  run {n} failed: could not reach the API: {exc}", file=out)
            return False
        cost = estimate_cost(FAST_MODEL, run.usage)
        stop = (
            "" if run.stop_reason == "end_turn" else f", stop_reason {run.stop_reason}"
        )
        print(
            f"  run {n}: first token {_ms(run.first_token_ms)} "
            f"(message_start {_ms(run.start_ms)}), total {_ms(run.total_ms)}, "
            f"{run.usage}, ~${cost:.6f}{stop} -> {run.text[:60]!a}",
            file=out,
        )
        done.append(run)
        if not run.text.strip() or run.stop_reason == "refusal":
            print(f"  run {n} gave no answer.", file=out)
            return False
    ttfts = [r.first_token_ms for r in done if r.first_token_ms is not None]
    print(
        f"  median: first token {_ms(statistics.median(ttfts) if ttfts else None)}, "
        f"total {_ms(statistics.median(r.total_ms for r in done))} (V21)",
        file=out,
    )
    total = Usage()
    for r in done:
        total.add(r.usage)
    cost = sum(estimate_cost(FAST_MODEL, r.usage) for r in done)
    print(f"  usage: {total}, ~${cost:.6f} (prices checked {PRICES_CHECKED})", file=out)
    print(f"  workspace: {done[-1].workspace_id or 'not reported'}", file=out)
    return True


@dataclass
class DeepRun:
    init: dict[str, Any] | None = None  # the CLI's system/init message
    assistant_error: str | None = None
    result: ResultMessage | None = None
    error: Exception | None = None


async def _deep_query(key: str, query: QueryFn) -> DeepRun:
    options = ClaudeAgentOptions(
        model=DEEP_MODEL,
        tools=[],  # no built-in tools (plan section 4.4)
        strict_mcp_config=True,  # no MCP servers from .mcp.json, settings or connectors
        setting_sources=[],  # no settings files: no hooks, plugins or CLAUDE.md
        max_turns=1,
        max_budget_usd=DEEP_BUDGET_USD,
        env={
            "ANTHROPIC_API_KEY": key,  # the CLI subprocess only
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",  # SDK hygiene, plan section 4.4
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        },
    )
    run = DeepRun()
    try:
        async for message in query(prompt=DEEP_PROMPT, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                run.init = message.data
            elif isinstance(message, AssistantMessage) and message.error:
                run.assistant_error = message.error
            elif isinstance(message, ResultMessage):
                run.result = message
    except claude_agent_sdk.ClaudeSDKError as exc:
        # A failed run yields its error result first, then raises.
        run.error = exc
    return run


def _init_problem(init: dict[str, Any] | None) -> str | None:
    """Why the CLI's report of its own session breaks the deep lane's rules, or None."""
    if init is None:
        return "the CLI sent no init message, so the credential it used is unknown"
    source = init.get("apiKeySource")
    if source != "ANTHROPIC_API_KEY":
        return (
            f"the CLI did not use the stored key (apiKeySource {source!r}), so this run "
            "did not test the API credits. A Claude apps gateway sign-in or managed login "
            "settings on this PC outrank the key (Claude Code docs: Authentication)"
        )
    if init.get("tools") != [] or init.get("mcp_servers") != []:
        return "the CLI did not report zero tools and zero MCP servers; the deep lane has none"
    return None


# AssistantMessage.error values, for when the result carries no HTTP status.
_SDK_ERROR_STATUS = {
    "authentication_failed": 401,
    "billing_error": 402,
    "rate_limit": 429,
}


def deep_lane(key: str, out: TextIO, query: QueryFn) -> bool:
    print(
        f"Deep lane: {DEEP_MODEL} via the Claude Agent SDK, no built-in tools, "
        f"budget cap ${DEEP_BUDGET_USD:.2f}",
        file=out,
    )
    if found := [name for name in OUTRANKING_ENV if os.environ.get(name)]:
        unset = "; ".join(f"Remove-Item Env:{name}" for name in found)
        print(
            f"  not run: the SDK would use these instead of the stored key: "
            f"{', '.join(found)}. Remove them from this PowerShell window ({unset}) "
            "and run again.",
            file=out,
        )
        return False
    run = asyncio.run(_deep_query(key, query))
    if (init := run.init) is not None:
        print(
            f"  CLI {init.get('claude_code_version')}: model {init.get('model')}, "
            f"apiKeySource {init.get('apiKeySource')}, tools {init.get('tools')}, "
            f"MCP servers {init.get('mcp_servers')}",
            file=out,
        )
    result = run.result
    if result is None:
        why = (
            f"Agent SDK error: {run.error}"
            if run.error
            else "the SDK ended without a result"
        )
        print(f"  failed: {why}", file=out)
        return False
    u = result.usage or {}
    usage = Usage(
        input=u.get("input_tokens") or 0,
        output=u.get("output_tokens") or 0,
        cache_write=u.get("cache_creation_input_tokens") or 0,
        cache_read=u.get("cache_read_input_tokens") or 0,
    )
    sdk_cost = (
        "not reported"
        if result.total_cost_usd is None
        else f"${result.total_cost_usd:.6f}"
    )
    print(
        f"  usage: {usage}, ~${estimate_cost(DEEP_MODEL, usage):.6f} "
        f"(SDK estimate {sdk_cost})",
        file=out,
    )
    problem = _init_problem(init)
    if problem:
        print(f"  failed: {problem}", file=out)
    answer = (result.result or "").strip()
    if not result.is_error and answer:
        print(f"  answered: {answer[:60]!a}", file=out)
        return problem is None
    if result.subtype == "error_max_budget_usd":
        why = f"hit the ${DEEP_BUDGET_USD:.2f} budget cap"
    elif not result.is_error:
        why = "the SDK's result had no text"
    else:
        status = result.api_error_status or _SDK_ERROR_STATUS.get(
            run.assistant_error or ""
        )
        text = result.result or "; ".join(result.errors or []) or result.subtype
        why = explain_api_error(status, text, run.assistant_error)
    print(f"  failed: {why}", file=out)
    return False


def run(
    *,
    runs: int,
    skip_sdk: bool,
    out: TextIO,
    http_client: httpx2.Client | None = None,
    query: QueryFn = claude_agent_sdk.query,
    clock: Callable[[], float] = time.perf_counter,
) -> int:
    """Exit code 0 if every lane that ran answered, else 1."""
    try:
        key = keys.get_key(keys.ANTHROPIC)
    except (keys.MissingKeyError, keyring.errors.KeyringError) as exc:
        print(f"Smoke test not run: {exc}", file=out)
        return 1
    client = anthropic.Anthropic(api_key=key, max_retries=0, http_client=http_client)
    fast_ok = fast_lane(client, runs, out, clock)
    if skip_sdk:
        print("Deep lane: skipped (--skip-sdk)", file=out)
        deep_ok = True
    else:
        deep_ok = deep_lane(key, out, query)
    ok = fast_ok and deep_ok
    print("Result: " + ("every lane answered." if ok else "FAILED."), file=out)
    return 0 if ok else 1
