"""The core reads a request's body before it refuses it (D355, after the supervisor's D340).

urllib sends a POST's head and body apart. A refusal answered and closed before the body has
been read leaves it unread (or still on its way), the OS resets the connection, and a Windows
client loses the answer: the voice client said "Core offline." for a wrong token (WinError
10054 instead of the 401). So no answer may come while a body of an acceptable size is still
on its way, and a body that never comes holds the answer for at most ``READ_FIRST_S``.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from gregtec.api import app as core_app
from gregtec.api import security
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth, ReadBodyFirst
from gregtec.api.server import bind
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

ADMIN = "admin-" + "a" * 40


@pytest.fixture
def port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, Core]]:
    """A real core (FastAPI + uvicorn, no agents) on 127.0.0.1:<free port>."""
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    sock = bind(0)
    store = Store(tmp_path / "data" / "gos.sqlite")
    core = Core(
        cfg=Config(gos_home=tmp_path, core_port=sock.getsockname()[1]),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(core), log_config=None, access_log=False, lifespan="on"
        )
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive(), "no test core"
        time.sleep(0.02)
    try:
        yield sock.getsockname()[1], core
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        store.close()


def _head(port: int, path: str, length: int, *headers: str) -> bytes:
    lines = [f"POST {path} HTTP/1.1", f"Host: 127.0.0.1:{port}", *headers]
    if not any(h.lower().startswith("content-type:") for h in headers):
        lines.append("Content-Type: application/json")
    lines += [f"Content-Length: {length}", "Connection: close", "", ""]
    return "\r\n".join(lines).encode()


def _answer(sock: socket.socket) -> bytes:
    got = b""
    while chunk := sock.recv(65536):
        got += chunk
    return got


REFUSALS = [
    # (path, extra headers, body, status): each is refused before its route reads the body
    ("/api/ask", ["Host: evil.example"], b'{"text": "hi"}', 403),  # the Host guard
    ("/api/ask", ["Origin: http://evil.example"], b'{"text": "hi"}', 403),  # Origin
    ("/api/ask", [], b'{"text": "hi"}', 401),  # no credential
    ("/api/ask", ["Authorization: Bearer " + "wrong" * 9], b'{"text": "hi"}', 401),
    (
        "/api/ask",
        ["Authorization: Bearer " + ADMIN],
        b'{"text": "hi"}',
        503,
    ),  # no agents
    ("/api/tokens/x", ["Authorization: Bearer {client}"], b"{}", 403),  # admin only
    (
        "/api/screen",
        ["Authorization: Bearer {client}", "Content-Type: image/png"],
        b"\x89PNG",
        403,
    ),
    (
        "/api/ui",
        ["Authorization: Bearer " + ADMIN, "Content-Type: text/plain"],
        b"{}",
        415,
    ),
    ("/api/ui", ["Authorization: Bearer " + ADMIN], b"{" + b" " * 5000 + b"}", 413),
    ("/api/nope", ["Authorization: Bearer " + ADMIN], b"{}", 404),
    ("/api/session", ["Authorization: Bearer " + ADMIN], b"{}", 405),
]


@pytest.mark.parametrize(("path", "headers", "body", "status"), REFUSALS)
def test_a_refused_request_s_body_is_read_before_the_answer(
    port: tuple[int, Core], path: str, headers: list[str], body: bytes, status: int
) -> None:
    number, core = port
    client = core.auth.issue_client_token("discord")
    head = _head(number, path, len(body), *(h.format(client=client) for h in headers))
    if "Host: evil.example" in headers:  # instead of the good one, not beside it
        head = head.replace(f"Host: 127.0.0.1:{number}\r\n".encode(), b"")
    with socket.create_connection(("127.0.0.1", number), timeout=0.3) as sock:
        sock.sendall(head)
        with pytest.raises(TimeoutError):
            sock.recv(65536)  # the core waits for the body it was promised
        sock.settimeout(5)
        sock.sendall(body)
        got = _answer(sock)
    assert got.split(b" ", 2)[1] == str(status).encode(), got[:200]


def test_a_body_that_never_comes_holds_the_refusal_only_briefly(
    port: tuple[int, Core], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(security, "READ_FIRST_S", 0.5)
    number, _ = port
    with socket.create_connection(("127.0.0.1", number), timeout=10) as sock:
        sock.sendall(_head(number, "/api/ask", 1000))  # and never the body
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", number), timeout=5) as other:
            other.sendall(
                f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{number}\r\n"
                "Connection: close\r\n\r\n".encode()
            )
            assert _answer(other).startswith(b"HTTP/1.1 200")  # the core isn't held
        got = _answer(sock)
        waited = time.monotonic() - started
    assert got.startswith(b"HTTP/1.1 401")
    assert 0.3 < waited < 5


def test_an_oversized_body_is_not_read_at_all(port: tuple[int, Core]) -> None:
    """Past the largest body a route takes, the refusal comes at once, as before."""
    number, _ = port
    head = _head(number, "/api/ask", core_app.READ_FIRST_MAX + 1)
    with socket.create_connection(("127.0.0.1", number), timeout=5) as sock:
        sock.sendall(head)
        assert _answer(sock).startswith(b"HTTP/1.1 401")


# --- the middleware alone (no sockets, no clocks but the timeout's) ------------------------------


def _scope(**headers: str) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [
            (k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()
        ],
    }


async def _refuse(scope: Any, receive: Any, send: Any) -> None:  # noqa: ANN401
    """An app that answers 401 without reading the body, as the token check does."""
    await send({"type": "http.response.start", "status": 401, "headers": []})
    await send({"type": "http.response.body", "body": b"no"})


class Peer:
    """The request side: body chunks, then ``after`` (the body's end, a disconnect or nothing
    ever again), and a log of what the app was handed and sent, in order."""

    def __init__(self, chunks: list[bytes], after: str = "end") -> None:
        self.chunks = list(chunks)
        self.after = after
        self.log: list[str] = []

    async def receive(self) -> dict[str, Any]:
        if not self.chunks:
            if self.after == "wait":
                await asyncio.sleep(3600)  # the client never sends the rest
            self.log.append("disconnect")
            return {"type": "http.disconnect"}
        chunk = self.chunks.pop(0)
        self.log.append(f"body {len(chunk)}")
        more = bool(self.chunks) or self.after != "end"
        return {"type": "http.request", "body": chunk, "more_body": more}

    async def send(self, message: dict[str, Any]) -> None:
        self.log.append(message["type"])


def _run(peer: Peer, limit: int = 100, **headers: str) -> None:
    app = ReadBodyFirst(_refuse, limit=limit)
    asyncio.run(app(_scope(**headers), peer.receive, peer.send))


def test_the_whole_body_is_read_before_the_answer_starts() -> None:
    peer = Peer([b"a" * 10, b"b" * 10, b"c" * 10])
    _run(peer, content_length="30")
    assert peer.log == [
        "body 10",
        "body 10",
        "body 10",
        "http.response.start",
        "http.response.body",
    ]


def test_a_chunked_body_is_read_up_to_the_limit_only() -> None:
    peer = Peer([b"a" * 60, b"b" * 60, b"c" * 60])
    _run(peer, limit=100, transfer_encoding="chunked")
    assert peer.log == [
        "body 60",
        "body 60",
        "http.response.start",
        "http.response.body",
    ]


def test_a_declared_length_over_the_limit_is_not_read() -> None:
    peer = Peer([b"a" * 10])
    _run(peer, limit=100, content_length="101")
    assert peer.log == ["http.response.start", "http.response.body"]


@pytest.mark.parametrize(
    "headers",
    [{}, {"content_length": "0"}, {"content_length": "30", "expect": "100-continue"}],
)
def test_no_body_or_one_not_yet_sent_is_not_waited_for(headers: dict[str, str]) -> None:
    peer = Peer([b"a" * 10])
    _run(peer, **headers)
    assert peer.log == ["http.response.start", "http.response.body"]


def test_a_body_the_app_read_is_not_read_again() -> None:
    async def reads(scope: Any, receive: Any, send: Any) -> None:  # noqa: ANN401
        assert (await receive())["more_body"] is False
        await _refuse(scope, receive, send)

    peer = Peer([b"a" * 10])
    asyncio.run(
        ReadBodyFirst(reads, limit=100)(
            _scope(content_length="10"), peer.receive, peer.send
        )
    )
    assert peer.log == ["body 10", "http.response.start", "http.response.body"]


def test_a_disconnect_ends_the_read() -> None:
    peer = Peer([b"a" * 10], after="disconnect")
    _run(peer, content_length="30")
    assert peer.log == [
        "body 10",
        "disconnect",
        "http.response.start",
        "http.response.body",
    ]


def test_a_stalled_body_is_waited_for_at_most_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security, "READ_FIRST_S", 0.2)
    peer = Peer([b"a" * 10], after="wait")
    started = time.monotonic()
    _run(peer, content_length="30")
    assert time.monotonic() - started < 2
    assert peer.log == ["body 10", "http.response.start", "http.response.body"]


def test_websockets_and_lifespan_pass_through() -> None:
    seen: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:  # noqa: ANN401
        seen.append(scope["type"])

    for kind in ("websocket", "lifespan"):
        asyncio.run(ReadBodyFirst(app, limit=10)({"type": kind}, None, None))  # type: ignore[arg-type]
    assert seen == ["websocket", "lifespan"]
