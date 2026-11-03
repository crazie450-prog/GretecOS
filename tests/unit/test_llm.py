"""WO-0.4: keys in the OS keyring and ``gos smoke``, with the keyring faked and the network mocked.

The fast lane runs through the real ``anthropic`` client with an ``httpx2.MockTransport``, so
the request body and the SDK's own SSE parsing and error classes are exercised. The deep lane
gets a fake ``query`` that yields the Agent SDK's real message types. No test opens a socket.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx2
import keyring
import keyring.backend
import keyring.errors
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultError,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

from gregtec import cli
from gregtec.llm import keys, smoke

KEY = "sk-ant-api03-" + "T3stK3y" * 12  # fake, but the shape of a real key
NEW_KEY = "sk-ant-api03-" + "N3wK3y" * 14  # a rotated one
TARGET = "gregtec-os/anthropic"


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        # Like WinVaultKeyring (keyring 25.7.0): a credential about to be overwritten is
        # first copied to "<user>@<service>".
        for (each, user), old in list(self.entries.items()):
            if each == service:
                self.entries[(f"{user}@{service}", user)] = old
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.entries.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture
def ring() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    fake = MemoryKeyring()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(previous)


@pytest.fixture
def stored_key(ring: MemoryKeyring) -> MemoryKeyring:
    ring.set_password(TARGET, keys.ANTHROPIC, KEY)
    return ring


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*smoke.OUTRANKING_ENV, "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


# --- keys and `gos key` ---------------------------------------------------------------------


def test_key_set_status_delete(
    ring: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompts: list[str] = []
    typed = iter([f"  {KEY}\n", NEW_KEY])

    def getpass(prompt: str) -> str:
        prompts.append(prompt)
        return next(typed)

    printed: list[str] = []  # everything each command wrote, stdout and stderr

    def gos(*argv: str) -> str:
        assert cli.main(list(argv)) == 0
        captured = capsys.readouterr()
        printed.append(captured.out + captured.err)
        return captured.out

    monkeypatch.setattr(cli.getpass, "getpass", getpass)
    assert "anthropic: not stored" in gos("key", "status")

    assert "anthropic: stored in the OS keyring" in gos("key", "set", "anthropic")
    assert ring.entries == {(TARGET, "anthropic"): KEY}  # stripped
    assert "anthropic: replaced" in gos("key", "set", "anthropic")
    assert ring.entries == {(TARGET, "anthropic"): NEW_KEY}

    status = gos("key", "status")
    assert "anthropic: stored" in status
    assert "MemoryKeyring" in status  # names the backend: the PC shows WinVaultKeyring
    assert keys.get_key(keys.ANTHROPIC) == NEW_KEY

    assert "anthropic: deleted" in gos("key", "delete", "anthropic")
    assert ring.entries == {}
    assert "anthropic: nothing stored" in gos("key", "delete", "anthropic")
    assert prompts == ["anthropic key (input hidden): "] * 2
    assert len(printed) == 6
    for text in printed:
        assert KEY not in text and NEW_KEY not in text


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("", "Nothing entered"),
        ("not-a-key-at-all-but-quite-long-enough-0123456789", "starts with sk-ant-"),
        ("sk-ant-admin01-" + "a" * 60, "Admin API key"),
        ("sk-ant-api03-short", "cut off"),
        ("sk-ant-api03-" + "a" * 30 + " " + "b" * 30, "spaces"),
    ],
)
def test_key_set_rejects_bad_input_without_echoing_it(
    ring: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    typed: str,
    expected: str,
) -> None:
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: typed)
    assert cli.main(["key", "set", "anthropic"]) == 1
    assert ring.entries == {}
    out = capsys.readouterr()
    assert expected in out.err
    if typed:
        assert typed not in out.out + out.err


def test_missing_key_error_says_what_to_run(ring: MemoryKeyring) -> None:
    with pytest.raises(keys.MissingKeyError, match="uv run gos key set anthropic"):
        keys.get_key(keys.ANTHROPIC)


def test_key_command_reports_keyring_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_service: str, _name: str) -> str:
        raise keyring.errors.NoKeyringError("No recommended backend was available.")

    monkeypatch.setattr(keys.keyring, "get_password", broken)
    assert cli.main(["key", "status"]) == 1
    assert "keyring error: No recommended backend" in capsys.readouterr().err


def test_backend_chain_names_where_keys_go(monkeypatch: pytest.MonkeyPatch) -> None:
    class WinVaultKeyring:
        pass

    class OtherKeyring:
        pass

    class ChainerBackend:
        backends = (WinVaultKeyring(), OtherKeyring())

    monkeypatch.setattr(keys.keyring, "get_keyring", ChainerBackend)
    assert keys.backend_chain() == ["WinVaultKeyring", "OtherKeyring"]
    monkeypatch.setattr(keys.keyring, "get_keyring", WinVaultKeyring)
    assert keys.backend_chain() == ["WinVaultKeyring"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_windows_credential_manager_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real default backend, under a service name of its own so it can't touch the real entry.
    # keyring may wrap it in ChainerBackend; either way the key must land in Credential Manager.
    chain = keys.backend_chain()
    assert chain[0] == "WinVaultKeyring", (
        f"{type(keyring.get_keyring()).__name__}: {chain}"
    )
    monkeypatch.setattr(keys, "SERVICE", "gregtec-os-pytest")
    target = "gregtec-os-pytest/anthropic"
    # Where WinVaultKeyring.set_password copies a credential it is about to overwrite.
    copy = f"anthropic@{target}"
    keys.delete_key(keys.ANTHROPIC)  # in case an earlier run was cut short
    try:
        assert keys.set_key(keys.ANTHROPIC, KEY) is False
        assert keyring.get_password(target, "anthropic") == KEY
        assert keys.set_key(keys.ANTHROPIC, NEW_KEY) is True
        assert keys.get_key(keys.ANTHROPIC) == NEW_KEY
        assert keyring.get_password(copy, "anthropic") is None  # the old key is gone
    finally:
        keys.delete_key(keys.ANTHROPIC)
    assert keys.has_key(keys.ANTHROPIC) is False
    assert keyring.get_password(copy, "anthropic") is None


# --- fast lane --------------------------------------------------------------------------------


def sse(
    text: str = "Hello there, friend!", stop_reason: str = "end_turn"
) -> list[bytes]:
    """A Messages API stream in the documented event order, one SSE event per item: message_start,
    content_block_start, ping, two text_deltas, content_block_stop, message_delta, message_stop.
    """
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": smoke.FAST_MODEL,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        ("ping", {"type": "ping"}),
    ]
    for piece in (text[: len(text) // 2], text[len(text) // 2 :]):
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": piece},
                },
            )
        )
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 8},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return [
        f"event: {name}\ndata: {json.dumps(data)}\n\n".encode() for name, data in events
    ]


class FakeApi:
    """Answers every request with ``respond`` and records what was sent."""

    def __init__(self, respond: Callable[[], httpx2.Response] | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.respond = respond or (
            lambda: httpx2.Response(
                200,
                headers={
                    "content-type": "text/event-stream",
                    "anthropic-workspace-id": "wrkspc_test",
                },
                content=b"".join(sse()),
            )
        )

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.respond()

    def client(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))


def error_response(
    status: int, error_type: str, message: str, details: dict[str, str] | None = None
) -> Callable[[], httpx2.Response]:
    error: dict[str, Any] = {"type": error_type, "message": message}
    if details:
        error["details"] = details
    body = {"type": "error", "error": error, "request_id": "req_test"}
    return lambda: httpx2.Response(status, json=body)


def clock(times: list[float]) -> Callable[[], float]:
    it = iter(times)
    return lambda: next(it)


async def no_query(**_kwargs: object) -> AsyncIterator[object]:
    raise AssertionError("the deep lane should not run")
    yield  # pragma: no cover


class Timeline:
    """A fake clock that the fake API moves on as the request and each stream event arrive."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_fast_lane_request_timing_and_median(stored_key: MemoryKeyring) -> None:
    # Per run: ms after the request reaches the API until its response headers go back, then
    # until each event of sse() arrives. The first token is the first text_delta (4th event),
    # not message_start, content_block_start or the ping.
    schedule = iter(
        [
            (
                30,
                [50, 60, 70, 100, 130, 140, 150, 500],
            ),  # first token 100 ms, total 500
            (40, [45, 120, 150, 200, 210, 220, 230, 600]),  # 200 ms, 600 ms
            (35, [100, 300, 800, 900, 905, 910, 915, 950]),  # 900 ms, 950 ms
        ]
    )
    timeline = Timeline()
    arrivals: list[float] = []

    def respond() -> httpx2.Response:
        arrived = timeline.now
        arrivals.append(arrived)
        headers_ms, events_ms = next(schedule)
        timeline.now = arrived + headers_ms / 1000

        def body() -> Iterator[bytes]:
            for at_ms, event in zip(events_ms, sse(), strict=True):
                timeline.now = arrived + at_ms / 1000
                yield event

        return httpx2.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "anthropic-workspace-id": "wrkspc_test",
            },
            content=body(),
        )

    api = FakeApi(respond)
    out = io.StringIO()
    code = smoke.run(
        runs=3,
        skip_sdk=True,
        out=out,
        http_client=api.client(),
        query=no_query,
        clock=timeline,
    )
    text = out.getvalue()
    assert code == 0, text
    assert len(api.requests) == 3
    assert arrivals == pytest.approx(
        [0.0, 0.5, 1.1]
    )  # each run starts when the last ends
    for request in api.requests:
        assert request.url.path == "/v1/messages"
        # The stored key; current API versions take API keys as a bearer token.
        assert request.headers["authorization"] == f"Bearer {KEY}"
        body = json.loads(request.content)
        assert body["model"] == "claude-haiku-5-5"
        assert body["thinking"] == {"type": "disabled"}
        assert body["output_config"] == {"effort": "low"}
        assert body["stream"] is True
        assert not {"temperature", "top_p", "top_k"} & body.keys()  # 400 on 2026-10-21
    # Timed from before the request is sent: the 30-40 ms until the headers count.
    assert "run 1: first token 100 ms (message_start 50 ms), total 500 ms" in text
    assert "run 2: first token 200 ms (message_start 45 ms), total 600 ms" in text
    assert "run 3: first token 900 ms (message_start 100 ms), total 950 ms" in text
    assert "20 in / 8 out tokens, ~$0.000006 -> 'Hello there, friend!'" in text
    # Medians: the means would be 400 ms and 683 ms.
    assert "median: first token 200 ms, total 600 ms (V21)" in text
    assert "usage: 60 in / 24 out tokens, ~$0.000018" in text
    assert "workspace: wrkspc_test" in text
    assert "Deep lane: skipped" in text
    assert KEY not in text


@pytest.mark.parametrize(
    ("respond", "expected"),
    [
        (
            error_response(401, "authentication_error", "invalid x-api-key"),
            "authentication failed: the stored key is wrong, revoked or expired",
        ),
        (
            error_response(
                400,
                "invalid_request_error",
                "Your credit balance is too low to access the Anthropic API. "
                "Please go to Plans & Billing to upgrade or purchase credits.",
            ),
            "credit balance too low",
        ),
        (
            error_response(
                400,
                "invalid_request_error",
                "You have reached your specified workspace API usage limits. "
                "You will regain access on 2026-12-01 at 00:00 UTC.",
            ),
            "the workspace spend limit is reached",
        ),
        (
            error_response(
                400,
                "invalid_request_error",
                "You have reached your specified API usage limits. "
                "You will regain access on 2026-12-01 at 00:00 UTC.",
            ),
            "the organization spend limit set in Console > Billing is reached",
        ),
        (
            error_response(
                429,
                "rate_limit_error",
                "You have reached your API usage limits: your organization has crossed "
                "its monthly API usage threshold, set based on your organization's API "
                "tier. You will regain access on 2026-12-01 at 00:00 UTC.",
                {"error_code": "enforced_spend_limit_reached"},
            ),
            "monthly spend cap for its usage tier is reached. Retrying fails",
        ),
        (
            error_response(
                429,
                "rate_limit_error",
                "Number of request tokens has exceeded your per-minute rate limit.",
            ),
            "rate limited: wait a minute",
        ),
        (
            error_response(529, "overloaded_error", "Overloaded"),
            "server error or is overloaded",
        ),
    ],
)
def test_fast_lane_errors_are_readable_and_not_retried(
    stored_key: MemoryKeyring, respond: Callable[[], httpx2.Response], expected: str
) -> None:
    api = FakeApi(respond)
    out = io.StringIO()
    code = smoke.run(
        runs=5,
        skip_sdk=True,
        out=out,
        http_client=api.client(),
        query=no_query,
        clock=clock([float(n) for n in range(10)]),
    )
    text = out.getvalue()
    assert code == 1
    assert (
        len(api.requests) == 1
    )  # no SDK retries, and the lane stops at the first error
    assert "run 1 failed: " in text and expected in text
    assert "Result: FAILED." in text
    assert KEY not in text


def test_fast_lane_refusal_is_not_an_answer(stored_key: MemoryKeyring) -> None:
    api = FakeApi(
        lambda: httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"".join(sse("I can't help with that.", stop_reason="refusal")),
        )
    )
    out = io.StringIO()
    code = smoke.run(
        runs=2,
        skip_sdk=True,
        out=out,
        http_client=api.client(),
        query=no_query,
        clock=clock([float(n) for n in range(10)]),
    )
    assert code == 1
    assert "stop_reason refusal" in out.getvalue()
    assert "run 1 gave no answer." in out.getvalue()


def test_missing_key_fails_before_any_call(ring: MemoryKeyring) -> None:
    api = FakeApi()
    out = io.StringIO()
    code = smoke.run(
        runs=5, skip_sdk=False, out=out, http_client=api.client(), query=no_query
    )
    assert code == 1
    assert api.requests == []
    assert "Smoke test not run: no anthropic key is stored" in out.getvalue()
    assert "uv run gos key set anthropic" in out.getvalue()


# --- deep lane --------------------------------------------------------------------------------


def result_message(**fields: object) -> ResultMessage:
    base: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 2100,
        "duration_api_ms": 1900,
        "is_error": False,
        "num_turns": 1,
        "session_id": "session_test",
        "total_cost_usd": 0.004321,
        "usage": {
            "input_tokens": 6,
            "output_tokens": 4,
            "cache_creation_input_tokens": 1500,
            "cache_read_input_tokens": 0,
        },
        "result": "ready",
    }
    return ResultMessage(**{**base, **fields})


def init_message(**fields: object) -> SystemMessage:
    """The CLI's system/init message, with the fields the bundled CLI 2.1.316 sent for the
    lane's options in a local run (fake key, API unreachable)."""
    data: dict[str, Any] = {
        "type": "system",
        "subtype": "init",
        "apiKeySource": "ANTHROPIC_API_KEY",
        "claude_code_version": "2.1.316",
        "model": smoke.DEEP_MODEL,
        "tools": [],
        "mcp_servers": [],
    }
    return SystemMessage(subtype="init", data={**data, **fields})


class FakeQuery:
    def __init__(self, messages: list[Any], error: Exception | None = None) -> None:
        self.messages = messages
        self.error = error
        self.calls: list[tuple[str, ClaudeAgentOptions]] = []

    async def __call__(
        self, *, prompt: str, options: ClaudeAgentOptions
    ) -> AsyncIterator[Any]:
        self.calls.append((prompt, options))
        for message in self.messages:
            yield message
        if self.error:
            raise self.error


def test_deep_lane_is_locked_down_and_reports_cost(stored_key: MemoryKeyring) -> None:
    query = FakeQuery(
        [
            init_message(),
            AssistantMessage(content=[TextBlock("ready")], model=smoke.DEEP_MODEL),
            result_message(),
        ]
    )
    out = io.StringIO()
    code = smoke.run(
        runs=1,
        skip_sdk=False,
        out=out,
        http_client=FakeApi().client(),
        query=query,
        clock=clock([0.0, 0.1, 0.2, 0.3]),
    )
    text = out.getvalue()
    assert code == 0, text
    [(prompt, options)] = query.calls
    assert prompt == smoke.DEEP_PROMPT
    assert options.model == "claude-sonnet-5-5"
    assert options.tools == []  # passes --tools "": no built-in tools
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    assert options.max_turns == 1
    assert options.max_budget_usd == smoke.DEEP_BUDGET_USD
    assert options.env["ANTHROPIC_API_KEY"] == KEY
    assert "ANTHROPIC_AUTH_TOKEN" not in options.env
    assert (
        "CLI 2.1.316: model claude-sonnet-5-5, apiKeySource ANTHROPIC_API_KEY, "
        "tools [], MCP servers []"
    ) in text
    assert "failed" not in text
    assert "6 in / 4 out tokens, cache write 1500 / read 0, ~$0.003802" in text
    assert "(SDK estimate $0.004321)" in text
    assert "answered: 'ready'" in text
    assert "Result: every lane answered." in text
    assert KEY not in text


@pytest.mark.parametrize(
    ("status", "sdk_error", "api_text", "expected"),
    [
        (
            400,
            "billing_error",
            (
                'API Error: 400 {"type":"error","error":{"type":"invalid_request_error",'
                '"message":"Your credit balance is too low to access the Anthropic API."}}'
            ),
            "credit balance too low",
        ),
        # Claude Code's own wording (API credits docs).
        (
            None,
            "billing_error",
            "Credit balance too low · Add funds: https://platform.claude.com/settings/billing",
            "credit balance too low",
        ),
        # Wording not matched: the SDK's billing_error wins over the HTTP 400.
        (400, "billing_error", "API Error: 400 billing", "credit balance too low"),
        # The tier cap is a 429 too, but waiting a minute won't fix it.
        (
            429,
            "rate_limit",
            (
                'API Error: 429 {"type":"error","error":{"type":"rate_limit_error",'
                '"message":"You have reached your API usage limits: your organization has '
                'crossed its monthly API usage threshold.","details":'
                '{"error_code":"enforced_spend_limit_reached"}}}'
            ),
            "the organization's monthly spend cap for its usage tier is reached",
        ),
    ],
)
def test_deep_lane_money_errors(
    stored_key: MemoryKeyring,
    status: int | None,
    sdk_error: str,
    api_text: str,
    expected: str,
) -> None:
    failed = result_message(
        is_error=True,
        result=api_text,
        api_error_status=status,
        total_cost_usd=0.0,
        usage={},
    )
    query = FakeQuery(
        [
            init_message(),
            AssistantMessage(
                content=[TextBlock(api_text)],
                model=smoke.DEEP_MODEL,
                error=sdk_error,  # type: ignore[arg-type]
            ),
            failed,
        ],
        error=ResultError("Claude Code returned an error result", data={}, exit_code=1),
    )
    out = io.StringIO()
    code = smoke.run(
        runs=1,
        skip_sdk=False,
        out=out,
        http_client=FakeApi().client(),
        query=query,
        clock=clock([0.0, 0.1, 0.2, 0.3]),
    )
    text = out.getvalue()
    assert code == 1
    assert f"failed: {expected}" in text, text
    assert text.count("failed") == 1, text
    assert "Result: FAILED." in text


def test_deep_lane_maps_sdk_auth_error_without_status(
    stored_key: MemoryKeyring,
) -> None:
    query = FakeQuery(
        [
            init_message(),
            AssistantMessage(
                content=[TextBlock("Invalid API key")],
                model=smoke.DEEP_MODEL,
                error="authentication_failed",
            ),
            result_message(is_error=True, result="Invalid API key", usage=None),
        ]
    )
    out = io.StringIO()
    assert smoke.deep_lane(KEY, out, query) is False
    assert "failed: authentication failed" in out.getvalue()
    assert "HTTP 401: Invalid API key" in out.getvalue()


def test_deep_lane_budget_cap_and_sdk_failure(stored_key: MemoryKeyring) -> None:
    capped = FakeQuery(
        [init_message(), result_message(subtype="error_max_budget_usd", is_error=True)]
    )
    out = io.StringIO()
    assert smoke.deep_lane(KEY, out, capped) is False
    assert "failed: hit the $0.10 budget cap" in out.getvalue()

    from claude_agent_sdk import CLINotFoundError

    missing = FakeQuery([], error=CLINotFoundError("Claude Code not found"))
    out = io.StringIO()
    assert smoke.deep_lane(KEY, out, missing) is False
    assert "failed: Agent SDK error: Claude Code not found" in out.getvalue()


def test_deep_lane_refuses_credentials_that_outrank_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "a-gateway-token")
    query = FakeQuery([result_message()])
    out = io.StringIO()
    assert smoke.deep_lane(KEY, out, query) is False
    assert query.calls == []
    assert "Remove-Item Env:ANTHROPIC_AUTH_TOKEN" in out.getvalue()


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        # A gateway sign-in or managed login settings made the CLI skip the key.
        (
            [init_message(apiKeySource="none"), result_message()],
            "the CLI did not use the stored key (apiKeySource 'none')",
        ),
        (
            [init_message(tools=["Bash", "Read"]), result_message()],
            "the CLI did not report zero tools and zero MCP servers",
        ),
        (
            [
                init_message(mcp_servers=[{"name": "github", "status": "connected"}]),
                result_message(),
            ],
            "the CLI did not report zero tools and zero MCP servers",
        ),
        (  # no tools list at all: the lock-down can't be checked
            [init_message(tools=None), result_message()],
            "the CLI did not report zero tools and zero MCP servers",
        ),
        ([result_message()], "the CLI sent no init message"),
    ],
)
def test_deep_lane_needs_the_cli_to_report_the_key_and_no_tools(
    messages: list[Any], expected: str
) -> None:
    out = io.StringIO()
    assert smoke.deep_lane(KEY, out, FakeQuery(messages)) is False
    text = out.getvalue()
    assert f"failed: {expected}" in text, text
    # The answer and its cost still show, so a run billed elsewhere is visible.
    assert "~$0.003802" in text and "answered: 'ready'" in text


@pytest.mark.parametrize(
    ("status", "message", "code", "expected"),
    [
        (
            400,
            "Your credit balance is too low to access the Anthropic API.",
            None,
            "credit balance too low",
        ),
        (
            None,
            "Credit balance too low · Add funds: https://platform.claude.com/settings/billing",
            None,
            "credit balance too low",
        ),
        (
            400,
            "You have reached your specified workspace API usage limits.",
            None,
            "the workspace spend limit",
        ),
        (
            400,
            "You have reached your specified API usage limits.",
            None,
            "the organization spend limit",
        ),
        (
            429,
            (
                "You have reached your API usage limits: your organization has crossed "
                "its monthly API usage threshold."
            ),
            "enforced_spend_limit_reached",
            "the organization's monthly spend cap",
        ),
        (400, "unrecognised wording", "billing_error", "credit balance too low"),
        (429, "Too many requests", "rate_limit", "rate limited"),
        (None, "Connection refused", "server_error", "API error (no HTTP status"),
    ],
)
def test_explain_api_error(
    status: int | None, message: str, code: str | None, expected: str
) -> None:
    assert smoke.explain_api_error(status, message, code).startswith(expected)


# --- prices and the CLI -----------------------------------------------------------------------


def test_estimate_cost() -> None:
    # 1M of each token kind, priced per the table (plan section 4.4, re-checked in WO-0.4).
    million = smoke.Usage(input=1_000_000, output=1_000_000)
    assert smoke.estimate_cost("claude-haiku-5-5", smoke.Usage(input=50_000)) == (
        pytest.approx(0.005)
    )
    assert smoke.estimate_cost("claude-sonnet-5-5", million) == pytest.approx(12.0)
    # Haiku over 100k prompt tokens, cache reads included, is billed at 5x.
    long = smoke.Usage(input=60_000, cache_read=50_000, output=1_000)
    assert smoke.estimate_cost("claude-haiku-5-5", long) == pytest.approx(
        (60_000 * 0.50 + 50_000 * 0.05 + 1_000 * 2.50) / 1_000_000
    )


def test_gos_smoke_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(smoke, "run", fake_run)
    assert cli.main(["smoke"]) == 0
    assert cli.main(["smoke", "--runs", "2", "--skip-sdk"]) == 0
    assert [(c["runs"], c["skip_sdk"]) for c in calls] == [(5, False), (2, True)]
    with pytest.raises(SystemExit):
        cli.main(["smoke", "--runs", "0"])
