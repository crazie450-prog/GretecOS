"""WO-2.1 acceptance (plan 4.6, QA:A8): Host/Origin checks, tokens, cookie and device pairing.

Runs the real FastAPI app in-process (Starlette's TestClient) on a temporary GOS_HOME.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gregtec import applog
from gregtec.api import app as core_app
from gregtec.api import security
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth, Pairing, PairingError
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

ADMIN = "admin-" + "a" * 40
BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/ws"  # TestClient ignores base_url for WebSockets
TAILNET = "gaming-pc.tail1234.ts.net"


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Detach the handlers applog.setup adds, so no test leaves a log file open."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def make_core(tmp_path: Path, **cfg: object) -> Core:
    config = Config(gos_home=tmp_path, **cfg)  # type: ignore[arg-type]
    store = Store(tmp_path / "data" / "gos.sqlite")
    return Core(
        cfg=config,
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def core(tmp_path: Path) -> Iterator[Core]:
    c = make_core(tmp_path)
    yield c
    c.store.close()


@pytest.fixture
def client(core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(core), base_url=BASE) as c:
        yield c


def admin_headers(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}", **extra}


def pair(client: TestClient, name: str = "Pixel 9") -> tuple[str, str]:
    """Pair a device through the API. Returns (device id, cookie value)."""
    started = client.post("/api/pair/start", json={"name": name})
    assert started.status_code == 200
    rid = started.json()["request_id"]
    pending = client.get("/api/pair/pending", headers=admin_headers()).json()
    code = next(p["code"] for p in pending if p["request_id"] == rid)
    done = client.post("/api/pair/complete", json={"request_id": rid, "code": code})
    assert done.status_code == 200, done.text
    cookie = done.cookies[security.COOKIE]
    client.cookies.clear()
    return done.json()["device_id"], cookie


# --- Host and Origin ---------------------------------------------------------------------------


def test_health_probe_needs_no_credential(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "gregtec-core"


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8765",
        "127.0.0.1",  # no port
        "127.0.0.1:8766",  # another port
        "localhost",
        "127.0.0.1:8765.evil.example",
        "127.0.0.2:8765",
        "[::1]:8765",
        TAILNET,  # not configured here
    ],
)
def test_foreign_host_is_rejected(client: TestClient, host: str) -> None:
    for path in ("/health", "/api/health", "/api/pair/pending"):
        r = client.get(path, headers=admin_headers(Host=host))
        assert r.status_code == 403, (host, path)
        assert r.json() == {"error": "host not allowed"}
    r = client.post("/api/pair/start", json={"name": "x"}, headers={"Host": host})
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST:8765"])
def test_loopback_hosts_are_accepted(client: TestClient, host: str) -> None:
    assert (
        client.get("/api/health", headers=admin_headers(Host=host)).status_code == 200
    )


def test_tailnet_host_is_accepted_when_configured(tmp_path: Path) -> None:
    core = make_core(tmp_path, extra_allowed_hosts=(TAILNET,))
    with TestClient(create_app(core), base_url=f"https://{TAILNET}") as c:
        r = c.get("/api/health", headers=admin_headers(Origin=f"https://{TAILNET}"))
        assert r.status_code == 200
    core.store.close()


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "https://evil.example",
        "null",  # sandboxed frames and file:// pages
        "http://localhost:3000",  # another local web server
        "http://127.0.0.1:8766",
        "http://127.0.0.1:8765.evil.example",
        "http://127.0.0.1:8765/path",
        "ftp://127.0.0.1:8765",
        "http://user@127.0.0.1:8765",
        "http://127.0.0.1:99999",
        "",
    ],
)
def test_foreign_origin_is_rejected(client: TestClient, origin: str) -> None:
    # Even with the admin token: a web page must never act through the core.
    r = client.get("/api/health", headers=admin_headers(Origin=origin))
    assert r.status_code == 403
    assert r.json() == {"error": "origin not allowed"}
    r = client.post("/api/pair/start", json={"name": "x"}, headers={"Origin": origin})
    assert r.status_code == 403


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8765", "http://localhost:8765"])
def test_own_origin_is_accepted(client: TestClient, origin: str) -> None:
    assert (
        client.get("/api/health", headers=admin_headers(Origin=origin)).status_code
        == 200
    )


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_cross_site_fetch_is_rejected(client: TestClient, site: str) -> None:
    r = client.get("/api/health", headers=admin_headers(**{"Sec-Fetch-Site": site}))
    assert r.status_code == 403
    for ok in ("same-origin", "none"):
        r = client.get("/api/health", headers=admin_headers(**{"Sec-Fetch-Site": ok}))
        assert r.status_code == 200


def test_conflicting_host_headers_are_rejected() -> None:
    allowed = ("127.0.0.1:8765",)
    scope = {
        "type": "http",
        "headers": [(b"host", b"127.0.0.1:8765"), (b"host", b"evil.example")],
    }
    assert security.refusal(security._headers(scope), allowed) == "host not allowed"
    scope["headers"] = [
        (b"host", b"127.0.0.1:8765"),
        (b"origin", b"http://127.0.0.1:8765"),
    ]
    assert security.refusal(security._headers(scope), allowed) is None


def test_websocket_with_foreign_origin_or_host_is_rejected(client: TestClient) -> None:
    for headers, why in (
        (admin_headers(Origin="http://evil.example"), "origin not allowed"),
        (admin_headers(Host="evil.example:8765"), "host not allowed"),
        (admin_headers(**{"Sec-Fetch-Site": "cross-site"}), "cross-site request"),
    ):
        with (
            pytest.raises(WebSocketDisconnect) as caught,
            client.websocket_connect(WS, headers=headers),
        ):
            pass
        assert (caught.value.code, caught.value.reason) == (1008, why)
    with client.websocket_connect(WS, headers=admin_headers(Origin=BASE)) as ws:
        assert ws.receive_json()["type"] == "hello"


# --- unpaired devices and tokens ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/api/health", "/api/usage", "/api/session", "/api/devices", "/api/pair/pending"],
)
def test_unpaired_device_is_refused(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401
    for cookie in (
        "",
        "garbage",
        "0123456789abcdef.secret",
        "0123456789abcdef.",
        "x.y.z",
    ):
        client.cookies.set(security.COOKIE, cookie)
        assert client.get(path).status_code == 401, cookie
    client.cookies.clear()
    for auth in (
        "Bearer wrong",
        "Bearer ",
        "Basic " + ADMIN,
        ADMIN,
        f"Bearer {ADMIN}x",
    ):
        assert client.get(path, headers={"Authorization": auth}).status_code == 401, (
            auth
        )


def test_unpaired_websocket_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(WS):
        pass
    assert (caught.value.code, caught.value.reason) == (1008, "not paired")
    client.cookies.set(security.COOKIE, "0123456789abcdef.forged")
    with pytest.raises(WebSocketDisconnect) as caught, client.websocket_connect(WS):
        pass
    assert caught.value.reason == "not paired"
    client.cookies.clear()
    _, cookie = pair(client)
    client.cookies.set(security.COOKIE, cookie)
    with client.websocket_connect(WS) as ws:
        assert ws.receive_json()["role"] == "device"


def test_pairing_sets_a_strict_httponly_cookie(client: TestClient) -> None:
    started = client.post("/api/pair/start", json={"name": "Pixel 9"})
    assert started.status_code == 200
    body = started.json()
    assert set(body) == {
        "request_id",
        "expires_in",
    }  # the code is never sent to the asker
    rid = body["request_id"]
    # Only a paired device or the admin sees the code.
    assert client.get("/api/pair/pending").status_code == 401
    pending = client.get("/api/pair/pending", headers=admin_headers()).json()
    assert [p["name"] for p in pending] == ["Pixel 9"]
    code = pending[0]["code"]
    assert re.fullmatch(r"\d{8}", code)

    wrong = "0" * 8 if code != "0" * 8 else "1" * 8
    r = client.post("/api/pair/complete", json={"request_id": rid, "code": wrong})
    assert r.status_code == 403
    assert security.COOKIE not in r.cookies

    spaced = f"{code[:4]} {code[4:]}"  # typed as shown by `gos pair`
    r = client.post("/api/pair/complete", json={"request_id": rid, "code": spaced})
    assert r.status_code == 200
    header = r.headers["set-cookie"]
    assert header.startswith(f"{security.COOKIE}=")
    assert "HttpOnly" in header
    assert "SameSite=strict" in header or "SameSite=Strict" in header
    assert "Path=/" in header
    assert "Secure" not in header  # plain http on loopback
    assert f"Max-Age={security.COOKIE_MAX_AGE}" in header

    # The cookie now works, for HTTP and the WebSocket.
    session = client.get("/api/session").json()
    assert session["role"] == "device" and session["name"] == "Pixel 9"
    assert client.get("/api/health").status_code == 200
    # The request is used up.
    again = client.post("/api/pair/complete", json={"request_id": rid, "code": code})
    assert again.status_code == 404


def test_cookie_is_secure_on_the_tailnet_name(tmp_path: Path) -> None:
    core = make_core(tmp_path, extra_allowed_hosts=(TAILNET,))
    with TestClient(create_app(core), base_url=f"https://{TAILNET}") as c:
        rid = c.post("/api/pair/start", json={"name": "phone"}).json()["request_id"]
        code = core.pairing.pending()[0]["code"]
        r = c.post("/api/pair/complete", json={"request_id": rid, "code": code})
        assert r.status_code == 200
        assert "Secure" in r.headers["set-cookie"]
    core.store.close()


def test_pairing_closes_after_five_wrong_codes(client: TestClient, core: Core) -> None:
    rid = client.post("/api/pair/start", json={"name": "x"}).json()["request_id"]
    code = core.pairing.pending()[0]["code"]
    wrong = "0" * 8 if code != "0" * 8 else "1" * 8
    for _ in range(security.PAIR_MAX_TRIES - 1):
        r = client.post("/api/pair/complete", json={"request_id": rid, "code": wrong})
        assert r.json() == {"error": "wrong code"}
    r = client.post("/api/pair/complete", json={"request_id": rid, "code": wrong})
    assert "closed" in r.json()["error"]
    r = client.post("/api/pair/complete", json={"request_id": rid, "code": code})
    assert r.status_code == 404
    assert core.pairing.pending() == []


def test_pairing_limits() -> None:
    now = [1000.0]
    pairing = Pairing(clock=lambda: now[0])
    first = pairing.start("a")
    pairing.start("b")
    pairing.start("c")
    with pytest.raises(PairingError) as caught:
        pairing.start("d")
    assert caught.value.status == 429
    # Expiry frees the slots.
    now[0] += security.PAIR_TTL_S
    with pytest.raises(PairingError) as caught:
        pairing.complete(first.request_id, first.code)
    assert caught.value.status == 404  # expired
    assert pairing.pending() == []
    # At most 10 requests start per 10 minutes, even when each is denied straight away.
    started = 3
    while started < security.PAIR_RATE[0]:
        assert pairing.deny(pairing.start(f"e{started}").request_id)
        started += 1
    with pytest.raises(PairingError) as caught:
        pairing.start("too many")
    assert caught.value.status == 429 and "later" in caught.value.reason
    now[0] += security.PAIR_RATE[1]
    pairing.start("allowed again")


@pytest.mark.parametrize("name", ["", "   ", "\x00\x01", 5, None])
def test_pairing_needs_a_name(client: TestClient, name: object) -> None:
    assert client.post("/api/pair/start", json={"name": name}).status_code == 400


def test_device_name_is_cleaned() -> None:
    assert security.clean_device_name("  My\nPhone\t ") == "My Phone"
    assert len(security.clean_device_name("x" * 100)) == 40


def test_pairing_takes_only_json(client: TestClient) -> None:
    r = client.post(
        "/api/pair/start", data={"name": "x"}
    )  # a form post, as a web page sends
    assert r.status_code == 415
    r = client.post(
        "/api/pair/start",
        content=b'{"name": "' + b"x" * 5000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_pairing_request_can_be_denied(client: TestClient, core: Core) -> None:
    rid = client.post("/api/pair/start", json={"name": "x"}).json()["request_id"]
    code = core.pairing.pending()[0]["code"]
    assert client.post(f"/api/pair/{rid}/deny").status_code == 401
    assert (
        client.post(f"/api/pair/{rid}/deny", headers=admin_headers()).status_code == 200
    )
    r = client.post("/api/pair/complete", json={"request_id": rid, "code": code})
    assert r.status_code == 404


def test_revoked_device_is_refused(client: TestClient) -> None:
    device_id, cookie = pair(client)
    _, other = pair(client, "monitor 2")
    client.cookies.set(security.COOKIE, cookie)
    assert client.get("/api/health").status_code == 200
    client.cookies.clear()
    # A paired device can revoke another (dashboard Settings).
    client.cookies.set(security.COOKIE, other)
    assert client.delete(f"/api/devices/{device_id}").status_code == 200
    listed = {d["id"]: d for d in client.get("/api/devices").json()}
    assert listed[device_id]["revoked"] is True
    client.cookies.clear()
    client.cookies.set(security.COOKIE, cookie)
    assert client.get("/api/health").status_code == 401
    client.cookies.clear()
    assert (
        client.delete(f"/api/devices/{device_id}", headers=admin_headers()).status_code
        == 404
    )


def test_cookie_secret_must_match(client: TestClient) -> None:
    device_id, cookie = pair(client)
    client.cookies.set(security.COOKIE, f"{device_id}.{'A' * 43}")
    assert client.get("/api/health").status_code == 401
    client.cookies.set(security.COOKIE, cookie)
    assert client.get("/api/health").status_code == 200


def test_client_tokens(client: TestClient) -> None:
    assert client.post("/api/tokens/voice").status_code == 401
    r = client.post("/api/tokens/voice", headers=admin_headers())
    assert r.status_code == 200
    token = r.json()["token"]
    voice = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/session", headers=voice).json() == {
        "role": "client",
        "name": "voice",
        "device_id": None,
    }
    assert client.get("/api/health", headers=voice).status_code == 200
    # A client token can't manage devices, pairing or tokens.
    assert client.get("/api/devices", headers=voice).status_code == 403
    assert client.get("/api/pair/pending", headers=voice).status_code == 403
    assert client.post("/api/tokens/mod", headers=voice).status_code == 403
    assert client.post("/api/admin/shutdown", headers=voice).status_code == 403
    # A paired device can't issue tokens either.
    _, cookie = pair(client)
    client.cookies.set(security.COOKIE, cookie)
    assert client.post("/api/tokens/mod").status_code == 403
    client.cookies.clear()
    # Re-issuing replaces the old token; revoking ends the new one.
    newer = client.post("/api/tokens/voice", headers=admin_headers()).json()["token"]
    assert client.get("/api/health", headers=voice).status_code == 401
    newer_h = {"Authorization": f"Bearer {newer}"}
    assert client.get("/api/health", headers=newer_h).status_code == 200
    assert (
        client.delete("/api/tokens/voice", headers=admin_headers()).status_code == 200
    )
    assert client.get("/api/health", headers=newer_h).status_code == 401
    listed = client.get("/api/tokens", headers=admin_headers()).json()
    assert [(t["name"], t["revoked"]) for t in listed] == [("voice", True)]
    for bad in ("Voice", "1x", "a" * 40, "x_y"):
        assert (
            client.post(f"/api/tokens/{bad}", headers=admin_headers()).status_code
            == 400
        )


def test_websocket_pushes_health_and_pairing(client: TestClient) -> None:
    with client.websocket_connect(WS, headers=admin_headers()) as ws:
        assert ws.receive_json() == {"type": "hello", "role": "admin", "name": "admin"}
        health = ws.receive_json()
        assert health["type"] == "health"
        assert {i["id"] for i in health["data"]["items"]} >= {"core", "db", "credits"}
        assert ws.receive_json() == {"type": "pair.pending", "data": []}
        ws.send_json({"type": "ping"})
        client.post("/api/pair/start", json={"name": "tablet"})
        seen = []
        while not any(m["type"] == "pair.pending" and m["data"] for m in seen):
            seen.append(ws.receive_json())
        assert {"type": "pong"} in seen
        pending = next(m for m in seen if m["type"] == "pair.pending")["data"]
        assert pending[0]["name"] == "tablet" and re.fullmatch(
            r"\d{8}", pending[0]["code"]
        )


def test_client_websocket_gets_no_pairing_codes(client: TestClient) -> None:
    token = client.post("/api/tokens/discord", headers=admin_headers()).json()["token"]
    with client.websocket_connect(
        WS, headers={"Authorization": f"Bearer {token}"}
    ) as ws:
        assert ws.receive_json()["role"] == "client"
        assert ws.receive_json()["type"] == "health"
        client.post("/api/pair/start", json={"name": "tablet"})
        ws.send_json({"type": "ping"})
        while (msg := ws.receive_json())["type"] != "pong":
            assert msg["type"] != "pair.pending"


def test_secrets_never_reach_logs_or_the_db(
    client: TestClient, core: Core, tmp_path: Path
) -> None:
    log_file = applog.setup("core", tmp_path / "logs", level=logging.DEBUG)
    try:
        rid = client.post("/api/pair/start", json={"name": "Pixel"}).json()[
            "request_id"
        ]
        code = core.pairing.pending()[0]["code"]
        client.post("/api/pair/complete", json={"request_id": rid, "code": "99999999"})
        done = client.post("/api/pair/complete", json={"request_id": rid, "code": code})
        cookie = done.cookies[security.COOKIE]
        token = client.post("/api/tokens/voice", headers=admin_headers()).json()[
            "token"
        ]
        client.get("/api/health", headers={"Authorization": f"Bearer {token}"})
    finally:
        for handler in logging.getLogger().handlers:
            handler.flush()
    logged = log_file.read_text(encoding="utf-8")
    assert "pairing requested" in logged and "device paired" in logged
    for line in logged.splitlines():
        json.loads(line)
    secret = cookie.split(".", 1)[1]
    for value in (code, secret, token, ADMIN):
        assert value not in logged
    dump = "\n".join(sqlite3.connect(core.store.path).iterdump())
    for value in (code, secret, token, ADMIN):
        assert value not in dump
    assert security.sha256(secret) in dump  # only hashes are kept


def test_admin_shutdown_needs_admin(client: TestClient, core: Core) -> None:
    calls: list[int] = []
    core.shutdown = lambda: calls.append(1)
    assert client.post("/api/admin/shutdown").status_code == 401
    assert (
        client.post("/api/admin/shutdown", headers=admin_headers()).status_code == 200
    )
    assert calls == [1]


def test_unknown_routes_answer_json(client: TestClient) -> None:
    r = client.get("/nope", headers=admin_headers())
    assert r.status_code == 404 and "error" in r.json()


def test_short_admin_token_is_refused(tmp_path: Path) -> None:
    store = Store(tmp_path / "gos.sqlite")
    with pytest.raises(ValueError):
        Auth(store, "short")
    store.close()


def test_states_map_to_strip_statuses() -> None:
    assert core_app.STATE_STATUS["running"] == "ok"
    assert core_app.STATE_STATUS["backoff"] == "error"
    assert core_app.STATE_STATUS["stopped"] == "off"


# --- review fixes ------------------------------------------------------------------------------


def test_revoking_a_device_closes_its_websocket(client: TestClient) -> None:
    device_id, cookie = pair(client)
    client.cookies.set(security.COOKIE, cookie)
    with client.websocket_connect(WS) as ws:
        assert ws.receive_json()["type"] == "hello"
        assert ws.receive_json()["type"] == "health"
        assert ws.receive_json()["type"] == "pair.pending"
        client.cookies.clear()
        assert (
            client.delete(
                f"/api/devices/{device_id}", headers=admin_headers()
            ).status_code
            == 200
        )
        with pytest.raises(WebSocketDisconnect) as caught:
            while True:  # a health push may come first
                ws.receive_json()
        assert (caught.value.code, caught.value.reason) == (1008, "revoked")


def test_replacing_a_client_token_closes_the_old_ones_socket(
    client: TestClient,
) -> None:
    token = client.post("/api/tokens/voice", headers=admin_headers()).json()["token"]
    with client.websocket_connect(
        WS, headers={"Authorization": f"Bearer {token}"}
    ) as ws:
        assert ws.receive_json()["type"] == "hello"
        client.post("/api/tokens/voice", headers=admin_headers())
        with pytest.raises(WebSocketDisconnect) as caught:
            while True:
                ws.receive_json()
        assert caught.value.reason == "revoked"


def test_binary_frames_are_ignored(client: TestClient) -> None:
    with client.websocket_connect(WS, headers=admin_headers()) as ws:
        for _ in range(3):
            ws.receive_json()
        ws.send_bytes(b"\x00\x01")
        ws.send_text("not json")
        ws.send_json({"type": "ping"})
        while ws.receive_json()["type"] != "pong":
            pass


def test_a_chunked_body_is_limited_too(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"name": "'
        for _ in range(10):
            yield b"x" * 1000
        yield b'"}'

    r = client.post(
        "/api/pair/start",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_expired_pairing_requests_are_pushed_away(core: Core) -> None:
    now = [100.0]
    core.pairing = Pairing(clock=lambda: now[0])
    sock = core_app.Socket(security.Principal("admin", "admin"), asyncio.Queue())
    core.sockets.add(sock)
    core.pairing.start("tablet")
    core.push_pending()
    first = json.loads(sock.queue.get_nowait())  # type: ignore[arg-type]
    assert [p["name"] for p in first["data"]] == ["tablet"]
    core.push_pending()
    assert sock.queue.empty()  # unchanged: nothing pushed
    now[0] += security.PAIR_TTL_S
    core.push_pending()
    assert json.loads(sock.queue.get_nowait()) == {"type": "pair.pending", "data": []}  # type: ignore[arg-type]
    client_sock = core_app.Socket(
        security.Principal("client", "voice"), asyncio.Queue()
    )
    core.sockets.add(client_sock)
    core.pairing.start("phone")
    core.push_pending()
    assert client_sock.queue.empty()  # clients never get codes


def test_a_full_queue_drops_messages(core: Core) -> None:
    sock = core_app.Socket(security.Principal("admin", "admin"), asyncio.Queue())
    core.sockets.add(sock)
    for _ in range(core_app.QUEUE_MAX + 50):
        core.broadcast({"type": "health"})
    assert sock.queue.qsize() == core_app.QUEUE_MAX
