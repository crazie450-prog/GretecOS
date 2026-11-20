"""A fake OpenAI Responses API on 127.0.0.1 for the shell bake-off's tests (WO-10.11, D374).

Beside :mod:`gregtec.llm.fake_api` (the fake Messages API): no key, no cost, no network. It
answers ``POST /v1/responses`` the way OpenAI's API reference describes a streamed response:
server-sent events ending in ``response.completed``, ``response.incomplete`` or
``response.failed``, each carrying the whole response object (``status``, ``output`` with
``output_text`` or ``refusal`` parts, ``incomplete_details``, ``error``, ``usage`` with
``input_tokens_details.cached_tokens`` and ``output_tokens_details.reasoning_tokens``); or an
HTTP error with ``{"error": {"message", "type", "code"}}`` and, when asked, a ``Retry-After``
header; or a stream that stops half way; or one that never starts (a timeout).

The policy decides each answer from the request body (:class:`Answer`). Every request body and
its headers are kept (``requests``, ``headers``), so a test can check what was sent: the model,
the JSON schema, no ``tools``, and the key only in the ``Authorization`` header.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self


@dataclass(frozen=True)
class Answer:
    text: str = ""
    status: str = "completed"  # completed, incomplete or failed
    reason: str | None = None  # incomplete_details.reason
    refusal: str | None = None  # a refusal part instead of text
    failed: tuple[str, str] | None = None  # (code, message) of a failed response
    input_tokens: int = 1000
    cached_tokens: int = 0
    output_tokens: int = 200
    reasoning_tokens: int = 50
    http: int = 200  # an HTTP error instead of a stream
    error_code: str = ""
    error_type: str = ""
    error_message: str = "fake error"
    retry_after: str | None = None
    cut_after: int | None = None  # close the stream after this many events
    stream_error: tuple[str, str] | None = None  # an `error` event (code, message)
    delay_s: float = 0.0  # wait this long before answering


Policy = Callable[[dict[str, Any]], Answer]


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        return None

    def _json(self, status: int, body: dict[str, Any], extra: dict[str, str]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        size = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(size) if size else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        api = self.server.api
        headers = {k.lower(): v for k, v in self.headers.items()}
        with api.lock:
            api.requests.append(body)
            api.headers.append(headers)
        if self.path.split("?", 1)[0] != "/v1/responses":
            self._json(
                404,
                {"error": {"message": "no such route", "type": "x", "code": None}},
                {},
            )
            return
        if api.key is not None and headers.get("authorization") != f"Bearer {api.key}":
            self._json(
                401,
                {
                    "error": {
                        "message": "Incorrect API key provided.",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
                {},
            )
            return
        answer = api.policy(body)
        if answer.delay_s:
            time.sleep(answer.delay_s)
        try:
            if answer.http != 200:
                extra = (
                    {"Retry-After": answer.retry_after} if answer.retry_after else {}
                )
                self._json(
                    answer.http,
                    {
                        "error": {
                            "message": answer.error_message,
                            "type": answer.error_type or None,
                            "code": answer.error_code or None,
                        }
                    },
                    extra,
                )
                return
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()  # no length: the stream ends when the connection closes
            for n, event in enumerate(api.events(answer, str(body.get("model", "")))):
                if answer.cut_after is not None and n >= answer.cut_after:
                    break
                self.wfile.write(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
                )
                self.wfile.flush()
        except OSError:  # the client gave up (a timeout test)
            return


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    api: FakeResponsesAPI


class FakeResponsesAPI:
    """``with FakeResponsesAPI(policy, key="sk-test") as api:`` serves on ``api.url``."""

    def __init__(self, policy: Policy, key: str | None = None) -> None:
        self.policy = policy
        self.key = key  # the only key accepted (None: any)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.lock = threading.Lock()
        self._ids = itertools.count(1)
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1/responses"

    def __enter__(self) -> Self:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.api = self
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fake-responses-api", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(5)

    def response(self, answer: Answer, model: str, n: int) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if answer.refusal is not None:
            content.append({"type": "refusal", "refusal": answer.refusal})
        elif answer.text:
            content.append(
                {"type": "output_text", "text": answer.text, "annotations": []}
            )
        output = (
            [
                {
                    "id": f"msg_fake_{n}",
                    "type": "message",
                    "status": answer.status,
                    "role": "assistant",
                    "content": content,
                }
            ]
            if content
            else []
        )
        return {
            "id": f"resp_fake_{n}",
            "object": "response",
            "status": answer.status,
            "model": model,
            "error": (
                {"code": answer.failed[0], "message": answer.failed[1]}
                if answer.failed
                else None
            ),
            "incomplete_details": {"reason": answer.reason} if answer.reason else None,
            "output": output,
            "usage": {
                "input_tokens": answer.input_tokens,
                "input_tokens_details": {
                    "cached_tokens": answer.cached_tokens,
                    "cache_write_tokens": 0,
                },
                "output_tokens": answer.output_tokens,
                "output_tokens_details": {"reasoning_tokens": answer.reasoning_tokens},
                "total_tokens": answer.input_tokens + answer.output_tokens,
            },
        }

    def events(self, answer: Answer, model: str) -> list[dict[str, Any]]:
        n = next(self._ids)
        final = self.response(answer, model, n)
        started = {**final, "status": "in_progress", "output": [], "usage": None}
        events: list[dict[str, Any]] = [
            {"type": "response.created", "response": started},
            {"type": "response.in_progress", "response": started},
        ]
        if answer.stream_error is not None:
            code, message = answer.stream_error
            return [*events, {"type": "error", "code": code, "message": message}]
        step = max(1, len(answer.text) // 4)
        for i in range(0, len(answer.text), step):
            events.append(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": answer.text[i : i + step],
                }
            )
        last = {
            "completed": "response.completed",
            "incomplete": "response.incomplete",
            "failed": "response.failed",
        }[answer.status]
        events.append({"type": last, "response": final})
        for seq, e in enumerate(events):
            e["sequence_number"] = seq
        return events
