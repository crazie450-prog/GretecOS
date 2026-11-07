"""A fake Messages API on 127.0.0.1 for the deep lane's tests and ``gos eval deep-lane --fake``.

The deep lane runs the real Claude Code CLI (the Agent SDK's bundled binary). Pointed at this
server (``ANTHROPIC_BASE_URL``) with a dummy key, the CLI starts, reports its session, calls the
in-process MCP tools and runs the hooks exactly as it would against the API; only the model's
turns are scripted. That is the deep lane's test seam: no key, no cost, no network.

The server answers ``POST /v1/messages`` with a streamed (SSE) message built from an
:class:`ApiTurn` that the policy returns for the request body: text, tool calls and the usage
it reports (so the lane's cost meter and budget cap can be driven). Every request body is kept
in :attr:`FakeMessagesAPI.requests`. ``count_tokens`` gets a small count; anything else a 404.
"""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self


@dataclass(frozen=True)
class ApiTurn:
    """One model turn: text, then tool calls ``(name, input)``; the usage it reports."""

    text: str = ""
    tools: Sequence[tuple[str, Mapping[str, Any]]] = ()
    input_tokens: int = 100
    output_tokens: int = 20
    cache_read: int = 0
    cache_write: int = 0


Policy = Callable[[dict[str, Any]], ApiTurn]


def last_tool_results(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The ``tool_result`` blocks of the request's last user message."""
    messages = body.get("messages") or []
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            return [
                c
                for c in content
                if isinstance(c, dict) and c.get("type") == "tool_result"
            ]
        return []
    return []


def result_text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def request_text(body: Mapping[str, Any]) -> str:
    """The text of the request's first user message (the task prompt)."""
    for message in body.get("messages") or []:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        texts = [
            c.get("text", "")
            for c in content or []
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        found = [t for t in texts if "<request>" in t]
        return found[0] if found else (texts[-1] if texts else "")
    return ""


def tool_names(body: Mapping[str, Any]) -> list[str]:
    return [str(t.get("name")) for t in body.get("tools") or []]


@dataclass
class Scripted:
    """Turns by model (the first model id that the request's model starts with); a model
    whose script is used up, or that has none, gets ``default``."""

    scripts: dict[str, list[ApiTurn]]
    default: ApiTurn = field(default_factory=lambda: ApiTurn(text="Done."))

    def __call__(self, body: dict[str, Any]) -> ApiTurn:
        model = str(body.get("model", ""))
        for prefix, turns in self.scripts.items():
            if model.startswith(prefix) and turns:
                return turns.pop(0)
        return self.default


def echo(body: dict[str, Any]) -> ApiTurn:
    """A well-behaved fake orchestrator: reads INDEX.md first, then answers from it."""
    results = last_tool_results(body)
    if not results and "mcp__gos__note_read" in tool_names(body):
        return ApiTurn(
            text="Let me check the vault index.",
            tools=[("mcp__gos__note_read", {"path": "INDEX.md"})],
            input_tokens=6000,
            output_tokens=60,
        )
    seen = result_text(results[0]) if results else ""
    lines = len([line for line in seen.split("\n") if line.strip()])
    return ApiTurn(
        text=(
            "The fake deep lane read the vault index. "
            f"(A scripted answer: the index result had {lines} lines.)"
        ),
        input_tokens=7000,
        output_tokens=120,
    )


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        return None

    def _send(self, status: int, data: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        size = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(size) if size else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        path = self.path.split("?", 1)[0]
        if path.endswith("/count_tokens"):
            self._send(200, b'{"input_tokens": 100}', "application/json")
            return
        if not path.endswith("/v1/messages"):
            self._send(404, b'{"type":"error"}', "application/json")
            return
        api = self.server.api
        with api.lock:
            api.requests.append(body)
        turn = api.policy(body)
        self._send(
            200, api.stream(turn, str(body.get("model", ""))), "text/event-stream"
        )

    def do_GET(self) -> None:
        self._send(404, b'{"type":"error"}', "application/json")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    api: FakeMessagesAPI


class FakeMessagesAPI:
    """``with FakeMessagesAPI(policy) as api:`` serves on ``api.url`` until the block ends."""

    def __init__(self, policy: Policy = echo) -> None:
        self.policy = policy
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self._ids = itertools.count(1)
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def env(self) -> dict[str, str]:
        """The CLI environment that sends it here (with a dummy key, which the lane sets)."""
        return {
            "ANTHROPIC_BASE_URL": self.url,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }

    def __enter__(self) -> Self:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.api = self
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fake-messages-api", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(5)

    def stream(self, turn: ApiTurn, model: str) -> bytes:
        n = next(self._ids)
        usage = {
            "input_tokens": turn.input_tokens,
            "output_tokens": 1,
            "cache_creation_input_tokens": turn.cache_write,
            "cache_read_input_tokens": turn.cache_read,
        }
        events: list[dict[str, Any]] = [
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_fake_{n}",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": usage,
                },
            }
        ]
        index = 0
        if turn.text:
            events += [
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": turn.text},
                },
                {"type": "content_block_stop", "index": index},
            ]
            index += 1
        for name, args in turn.tools:
            events += [
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": f"toolu_fake_{n}_{index}",
                        "name": name,
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(args),
                    },
                },
                {"type": "content_block_stop", "index": index},
            ]
            index += 1
        events += [
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "tool_use" if turn.tools else "end_turn",
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": turn.output_tokens},
            },
            {"type": "message_stop"},
        ]
        return "".join(
            f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
        ).encode()
