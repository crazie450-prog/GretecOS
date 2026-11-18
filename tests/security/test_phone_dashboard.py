"""WO-9.3 acceptance, security half (plan 4.6, D53, D117, D301): the phone dashboard over
Tailscale Serve.

The phone's requests arrive as Tailscale Serve sends them (``gregtec.tailnet``'s docstring, V28):
``Host: <pc>.<tailnet>.ts.net`` with no port, ``Origin: https://<that name>``, and for anything
that came through Funnel a ``Tailscale-Funnel-Request`` header. The real app runs in-process on a
temporary GOS_HOME; ``gos tailnet`` runs against a fake ``tailscale`` CLI
(``tests/fixtures/fake_tailscale.py``).
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gregtec import config, tailnet
from gregtec.api import security
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

ADMIN = "admin-" + "b" * 40
TAILNET = "gaming-pc.tail1234.ts.net"
PHONE = f"https://{TAILNET}"
WS = f"wss://{TAILNET}/ws"
FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_tailscale.py"


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


def make_core(tmp_path: Path, hosts: tuple[str, ...]) -> Core:
    store = Store(tmp_path / "data" / "gos.sqlite")
    return Core(
        cfg=Config(gos_home=tmp_path, extra_allowed_hosts=hosts),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )


@pytest.fixture
def core(tmp_path: Path) -> Iterator[Core]:
    c = make_core(tmp_path, (TAILNET,))
    yield c
    c.store.close()


@pytest.fixture
def phone(core: Core) -> Iterator[TestClient]:
    """A browser on the phone: every request as Tailscale Serve passes it on."""
    with TestClient(create_app(core), base_url=PHONE, headers={"Origin": PHONE}) as c:
        yield c


def pair(phone: TestClient, core: Core, name: str = "Pixel 9") -> tuple[str, str]:
    """Pair from the phone, the code read as monitor 2 shows it. (device id, cookie)."""
    started = phone.post("/api/pair/start", json={"name": name})
    assert started.status_code == 200, started.text
    rid = started.json()["request_id"]
    assert "code" not in started.json()  # the code never goes back to the asker
    code = next(p["code"] for p in core.pairing.pending() if p["request_id"] == rid)
    done = phone.post("/api/pair/complete", json={"request_id": rid, "code": code})
    assert done.status_code == 200, done.text
    assert "secure" in done.headers["set-cookie"].lower()
    cookie = done.cookies[security.COOKIE]
    phone.cookies.clear()
    return done.json()["device_id"], cookie


def ws_refusal(phone: TestClient, **headers: str) -> tuple[int, str]:
    with (
        pytest.raises(WebSocketDisconnect) as caught,
        phone.websocket_connect(WS, headers=headers),
    ):
        pass
    return caught.value.code, caught.value.reason


# --- the phone, paired ------------------------------------------------------------------------


def test_the_phone_pairs_once_and_then_reads_live_status(
    phone: TestClient, core: Core
) -> None:
    device_id, cookie = pair(phone, core)
    phone.cookies.set(security.COOKIE, cookie)
    assert phone.get("/api/session").json() == {
        "role": "device",
        "name": "Pixel 9",
        "device_id": device_id,
    }
    assert phone.get("/api/state").status_code == 200
    with phone.websocket_connect(WS) as ws:
        assert ws.receive_json()["role"] == "device"
    # One device, one row: the phone isn't asked to pair again.
    assert [d["id"] for d in core.auth.devices()] == [device_id]


# --- refused --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "other-pc.tail1234.ts.net",  # another node of the tailnet
        f"{TAILNET}.evil.example",
        f"evil.{TAILNET}",
        f"{TAILNET}:8443",
    ],
)
def test_a_foreign_host_is_refused(phone: TestClient, host: str) -> None:
    r = phone.get("/", headers={"Host": host})
    assert r.status_code == 403
    assert r.json() == {"error": "host not allowed"}
    assert ws_refusal(phone, Host=host) == (1008, "host not allowed")


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "https://other-pc.tail1234.ts.net",
        f"https://{TAILNET}.evil.example",
        f"https://{TAILNET}/plan",
        "null",
    ],
)
def test_a_foreign_origin_is_refused(phone: TestClient, origin: str) -> None:
    r = phone.post("/api/pair/start", json={"name": "x"}, headers={"Origin": origin})
    assert r.status_code == 403
    assert r.json() == {"error": "origin not allowed"}
    assert ws_refusal(phone, Origin=origin) == (1008, "origin not allowed")


@pytest.mark.parametrize(
    "path", ["/api/state", "/api/answers", "/api/session", "/api/devices"]
)
def test_a_missing_cookie_is_refused(phone: TestClient, path: str) -> None:
    assert phone.get(path).status_code == 401
    assert phone.post("/api/actions/1/confirm").status_code == 401
    assert ws_refusal(phone) == (1008, "not paired")


def test_an_unpaired_device_is_refused(phone: TestClient, core: Core) -> None:
    for cookie in ("0123456789abcdef.forged", "garbage", "0123456789abcdef."):
        phone.cookies.set(security.COOKIE, cookie)
        assert phone.get("/api/state").status_code == 401, cookie
        assert ws_refusal(phone) == (1008, "not paired")
    # A pairing request that never completed is no device either.
    phone.cookies.clear()
    phone.post("/api/pair/start", json={"name": "half"})
    assert core.auth.devices() == []


def test_a_revoked_device_is_refused(phone: TestClient, core: Core) -> None:
    device_id, cookie = pair(phone, core)
    phone.cookies.set(security.COOKIE, cookie)
    with phone.websocket_connect(WS) as ws:
        assert ws.receive_json()["type"] == "hello"
        # Revoked from monitor 2's Settings (or gos devices revoke).
        assert core.auth.revoke_device(device_id)
        core.disconnect(lambda w: w.device_id == device_id)
        with pytest.raises(WebSocketDisconnect) as caught:
            while True:
                ws.receive_json()
        assert (caught.value.code, caught.value.reason) == (1008, "revoked")
    assert phone.get("/api/state").status_code == 401
    assert ws_refusal(phone) == (1008, "not paired")


def test_a_device_can_revoke_itself_from_the_phone(
    phone: TestClient, core: Core
) -> None:
    device_id, cookie = pair(phone, core)
    phone.cookies.set(security.COOKIE, cookie)
    assert phone.delete(f"/api/devices/{device_id}").status_code == 200
    assert phone.get("/api/state").status_code == 401


def test_the_tailnet_name_is_refused_when_not_configured(tmp_path: Path) -> None:
    core = make_core(tmp_path, ())
    with TestClient(
        create_app(core), base_url=PHONE, headers={"Origin": PHONE}
    ) as phone:
        for method, path in (
            ("GET", "/"),
            ("GET", "/health"),
            ("POST", "/api/pair/start"),
        ):
            r = phone.request(method, path, json={"name": "x"})
            assert r.status_code == 403, path
            assert r.json() == {"error": "host not allowed"}
        assert ws_refusal(phone) == (1008, "host not allowed")
        assert core.pairing.pending() == []
    core.store.close()


def test_an_entry_with_the_https_port_takes_the_bare_name(tmp_path: Path) -> None:
    """A browser drops :443, so ``<name>:443`` in allowed_hosts must allow ``<name>`` (D301)."""
    cfg = Config(gos_home=tmp_path, extra_allowed_hosts=(f"{TAILNET}:443",))
    assert TAILNET in cfg.allowed_hosts and f"{TAILNET}:443" in cfg.allowed_hosts
    assert cfg.tailnet_host == TAILNET
    assert Config(gos_home=tmp_path).tailnet_host is None
    core = make_core(tmp_path, (f"{TAILNET}:443",))
    with TestClient(
        create_app(core), base_url=PHONE, headers={"Origin": PHONE}
    ) as phone:
        assert phone.get("/health").status_code == 200
    core.store.close()


def test_a_funnel_request_is_refused_whatever_else_it_carries(
    phone: TestClient, core: Core
) -> None:
    _, cookie = pair(phone, core)
    phone.cookies.set(security.COOKIE, cookie)
    assert phone.get("/api/state").status_code == 200
    for header in ("Tailscale-Funnel-Request", "tailscale-funnel-request"):
        for path in ("/", "/health", "/api/state"):
            r = phone.get(path, headers={header: "?1"})
            assert r.status_code == 403, path
            assert r.json() == {"error": "funnel request"}
        assert ws_refusal(phone, **{header: "?1"}) == (1008, "funnel request")
    r = phone.get(
        "/api/health",
        headers={
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
            "Tailscale-Funnel-Request": "?1",
            "Authorization": f"Bearer {ADMIN}",
        },
    )
    assert r.status_code == 403


def test_the_core_listens_on_loopback_only(tmp_path: Path) -> None:
    """Outside the tailnet nothing reaches it: no LAN or public address is accepted (4.6)."""
    toml = tmp_path / "gos.toml"
    for host in ("0.0.0.0", "192.168.1.20", "::"):
        toml.write_text(f'[core]\nhost = "{host}"\n', encoding="utf-8")
        cfg = config.load(toml)
        assert cfg.core_host == config.LOOPBACK
        assert any("[core] host" in e for e in cfg.service_errors)


# --- gos tailnet against a fake tailscale CLI --------------------------------------------------


def fake_state(tmp_path: Path, **extra: object) -> Path:
    path = tmp_path / "fake_tailscale.json"
    state = {
        "status": {
            "BackendState": "Running",
            "Self": {"DNSName": f"{TAILNET}.", "Online": True},
        },
        "serve": {},
        **extra,
    }
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def fake_cli(monkeypatch: pytest.MonkeyPatch, state: Path) -> tailnet.Tailscale:
    monkeypatch.setenv("FAKE_TAILSCALE_STATE", str(state))
    return tailnet.Tailscale([sys.executable, str(FAKE)])


def calls(state: Path) -> list[list[str]]:
    return json.loads(state.read_text(encoding="utf-8"))["calls"]


def cli(
    tmp_path: Path, ts: tailnet.Tailscale, command: str, *, funnel: bool = False
) -> tuple[int, str]:
    toml = tmp_path / "gos.toml"
    if not toml.exists():
        toml.write_text(f'[core]\nallowed_hosts = ["{TAILNET}"]\n', encoding="utf-8")
    out = io.StringIO()
    args = argparse.Namespace(tailnet_command=command, config=toml, funnel=funnel)
    return tailnet.run(args, out, ts=ts), out.getvalue()


@pytest.fixture(autouse=True)
def not_gos_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "running_as_dev", lambda: False)


def test_setup_serves_the_core_to_the_tailnet_and_checks_funnel_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = fake_state(tmp_path)
    ts = fake_cli(monkeypatch, state)
    code, out = cli(tmp_path, ts, "setup")
    assert code == 0, out
    assert "Funnel is off" in out
    serve = json.loads(state.read_text(encoding="utf-8"))["serve"]
    assert serve["Web"][f"{TAILNET}:443"]["Handlers"]["/"]["Proxy"] == (
        "http://127.0.0.1:8765"
    )
    assert "AllowFunnel" not in serve
    assert ["serve", "--bg", "--https=443", "http://127.0.0.1:8765"] in calls(state)
    assert not any("funnel" in " ".join(c) for c in calls(state))
    # Run again: nothing changes, nothing is added twice.
    assert cli(tmp_path, ts, "setup")[0] == 0
    assert sum(1 for c in calls(state) if "--bg" in c) == 1
    code, out = cli(tmp_path, ts, "check")
    assert code == 0, out
    assert out.count("ok ") == 4


def test_funnel_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = fake_state(tmp_path)
    ts = fake_cli(monkeypatch, state)
    for command in ("setup", "check", "off"):
        code, out = cli(tmp_path, ts, command, funnel=True)
        assert code == tailnet.EXIT_REFUSED
        assert "never turns on Tailscale Funnel" in out
    assert "calls" not in json.loads(state.read_text(encoding="utf-8"))
    with pytest.raises(tailnet.FunnelRefused):
        ts.run("funnel", "--bg", "8765")
    with pytest.raises(tailnet.FunnelRefused):
        ts.run("serve", "--funnel")
    assert "calls" not in json.loads(state.read_text(encoding="utf-8"))


def test_setup_refuses_while_funnel_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve = {"AllowFunnel": {f"{TAILNET}:8443": True}}
    state = fake_state(tmp_path, serve=serve)
    ts = fake_cli(monkeypatch, state)
    code, out = cli(tmp_path, ts, "setup")
    assert code == tailnet.EXIT_REFUSED
    assert "tailscale funnel --https=8443 off" in out
    assert not any("--bg" in c for c in calls(state))
    code, out = cli(tmp_path, ts, "check")
    assert code == tailnet.EXIT_PROBLEM
    assert "BAD Funnel is ON" in out


def test_funnel_in_a_foreground_session_counts_too() -> None:
    serve = {"Foreground": {"abc": {"AllowFunnel": {f"{TAILNET}:443": True}}}}
    assert tailnet.funnels(serve) == [f"{TAILNET}:443"]
    with pytest.raises(tailnet.FunnelRefused):
        tailnet.no_funnel(serve)


def test_setup_undoes_a_handler_that_came_up_with_funnel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = fake_state(tmp_path, funnel_on_serve=True)
    ts = fake_cli(monkeypatch, state)
    code, out = cli(tmp_path, ts, "setup")
    assert code == tailnet.EXIT_REFUSED, out
    assert calls(state)[-2:] == [
        ["serve", "status", "--json"],
        ["serve", "--https=443", "off"],
    ]
    serve = json.loads(state.read_text(encoding="utf-8"))["serve"]
    assert not tailnet.funnels(serve)
    assert tailnet.handler(serve, TAILNET) is None


def test_setup_wont_replace_another_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = {
        "Web": {
            f"{TAILNET}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}
        }
    }
    state = fake_state(tmp_path, serve=other)
    ts = fake_cli(monkeypatch, state)
    code, out = cli(tmp_path, ts, "setup")
    assert code == tailnet.EXIT_PROBLEM
    assert "already serves something else" in out
    assert not any("--bg" in c for c in calls(state))


def test_setup_needs_tailscale_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = fake_state(
        tmp_path, status={"BackendState": "NeedsLogin", "Self": {"DNSName": ""}}
    )
    ts = fake_cli(monkeypatch, state)
    code, out = cli(tmp_path, ts, "setup")
    assert code == tailnet.EXIT_PROBLEM
    assert "isn't running and logged in" in out


def test_check_names_the_missing_allowed_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "gos.toml").write_text("", encoding="utf-8")
    state = fake_state(tmp_path)
    ts = fake_cli(monkeypatch, state)
    code, out = cli(tmp_path, ts, "setup")
    assert code == 0
    assert f'add "{TAILNET}" to [core] allowed_hosts' in out
    code, out = cli(tmp_path, ts, "check")
    assert code == tailnet.EXIT_PROBLEM
    assert f"BAD {TAILNET} isn't in [core] allowed_hosts" in out


def test_off_removes_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = fake_state(tmp_path)
    ts = fake_cli(monkeypatch, state)
    assert cli(tmp_path, ts, "setup")[0] == 0
    assert cli(tmp_path, ts, "off")[0] == 0
    serve = json.loads(state.read_text(encoding="utf-8"))["serve"]
    assert tailnet.handler(serve, TAILNET) is None


def test_gos_dev_cant_change_clayton_s_tailscale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "running_as_dev", lambda: True)
    state = fake_state(tmp_path)
    ts = fake_cli(monkeypatch, state)
    for command in ("setup", "off"):
        code, out = cli(tmp_path, ts, command)
        assert code == tailnet.EXIT_REFUSED
        assert "Clayton runs gos tailnet" in out
    assert "calls" not in json.loads(state.read_text(encoding="utf-8"))
    code, out = cli(tmp_path, ts, "check")  # reading is allowed
    assert code == tailnet.EXIT_PROBLEM  # nothing is served yet
    assert "isn't served to the core" in out
    assert calls(state)[0] == ["status", "--json"]


def test_a_failing_cli_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = fake_state(tmp_path, fail="serve")
    ts = fake_cli(monkeypatch, state)
    code, out = cli(tmp_path, ts, "setup")
    assert code == tailnet.EXIT_PROBLEM
    assert "tailscale serve status --json failed" in out
