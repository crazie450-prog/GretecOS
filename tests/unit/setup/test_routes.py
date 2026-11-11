"""WO-2.9: the wizard's routes in the real app (Starlette's TestClient): only Clayton's ``gos``
commands and paired devices reach them (D117), secrets are never echoed or logged, and a fresh
GOS_HOME is configured through them alone."""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gregtec import config
from gregtec.api import security
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.setup.service import SetupService
from gregtec.store import Store

from .conftest import path_values
from .fakes import ANTHROPIC_KEY, DISCORD_TOKEN, MemoryKeyring

ADMIN = "admin-" + "s" * 40
BASE = "http://gos.localhost:8765"


@pytest.fixture
def core(tmp_path: Path, home: Path, svc: SetupService) -> Iterator[Core]:
    store = Store(tmp_path / "core.sqlite")
    c = Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        setup=svc,
    )
    yield c
    store.close()


@pytest.fixture
def client(core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(core), base_url=BASE) as c:
        yield c


def admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}"}


def test_only_admin_and_paired_devices_reach_the_wizard(
    client: TestClient, core: Core
) -> None:
    routes = [
        ("GET", "/api/setup"),
        ("GET", "/api/setup/devices"),
        ("POST", "/api/setup/steps/paths/check"),
        ("POST", "/api/setup/steps/paths"),
        ("POST", "/api/setup/secrets/anthropic"),
        ("DELETE", "/api/setup/secrets/anthropic"),
        ("POST", "/api/setup/secrets/anthropic/test"),
        ("POST", "/api/setup/checklist"),
        ("POST", "/api/setup/finish"),
    ]
    voice = core.auth.issue_client_token("voice")
    for method, path in routes:
        body = {"json": {}} if method == "POST" else {}
        assert client.request(method, path, **body).status_code == 401, path  # type: ignore[arg-type]
        r = client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {voice}"},
            **body,  # type: ignore[arg-type]
        )
        assert r.status_code == 403, path
    assert client.get("/api/setup", headers=admin()).status_code == 200
    _, cookie = core.auth.add_device("Monitor 2")
    client.cookies.set(security.COOKIE, cookie)
    assert client.get("/api/setup").status_code == 200
    client.cookies.clear()


def test_a_foreign_origin_is_refused(client: TestClient) -> None:
    r = client.post(
        "/api/setup/secrets/anthropic",
        json={"value": ANTHROPIC_KEY},
        headers={**admin(), "Origin": "http://evil.example"},
    )
    assert r.status_code == 403
    assert not keys.has_key(keys.ANTHROPIC)


def test_a_form_post_is_refused(client: TestClient, ring: MemoryKeyring) -> None:
    r = client.post(
        "/api/setup/secrets/anthropic",
        content=f"value={ANTHROPIC_KEY}",
        headers={**admin(), "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 415 and ring.entries == {}


def test_a_fresh_home_is_configured_from_the_wizard_alone(
    client: TestClient,
    svc: SetupService,
    prism: Path,
    home: Path,
    tmp_path: Path,
    ring: MemoryKeyring,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    state = client.get("/api/setup", headers=admin()).json()
    assert state["done"] is False and state["steps"][0]["id"] == "paths"
    app = tmp_path / "app"
    app.mkdir()
    values = path_values(prism, home, app)
    check = client.post(
        "/api/setup/steps/paths/check", json={"values": values}, headers=admin()
    )
    assert check.status_code == 200 and check.json()["blocked"] is False
    saved = client.post(
        "/api/setup/steps/paths", json={"values": values}, headers=admin()
    )
    assert saved.status_code == 200, saved.json()
    stored = client.post(
        "/api/setup/secrets/anthropic", json={"value": ANTHROPIC_KEY}, headers=admin()
    )
    assert stored.json() == {"name": "anthropic", "stored": True, "replaced": False}
    tested = client.post("/api/setup/secrets/anthropic/test", headers=admin())
    assert tested.json()["ok"] is True
    client.post(
        "/api/setup/secrets/discord", json={"value": DISCORD_TOKEN}, headers=admin()
    )
    devices = client.get("/api/setup/devices", headers=admin()).json()
    assert devices["provider"] == "fake" and devices["outputs"]
    chat = next(d["name"] for d in devices["outputs"] if "Chat" in d["name"])
    for step, vals in (
        ("headset", {"input_device": "", "output_endpoint": chat}),
        ("talk_key", {"talk_key": "F13", "talk_mode": "hold"}),
        ("wake", {"wake_phrase": "hey archivist"}),
    ):
        r = client.post(
            f"/api/setup/steps/{step}", json={"values": vals}, headers=admin()
        )
        assert r.status_code == 200, r.json()
    rows = client.post("/api/setup/checklist", headers=admin()).json()["checks"]
    assert all(r["status"] == "ok" for r in rows if r["id"] != "voice.provider"), rows
    done = client.post("/api/setup/finish", headers=admin())
    assert done.status_code == 200 and done.json()["done"] is True
    assert client.get("/api/setup", headers=admin()).json()["done"] is True

    cfg = config.load(svc.path)  # config.py reads back what the wizard wrote
    assert cfg.prism_instances == prism / "instances" and cfg.gos_home == home
    data = tomllib.loads(svc.path.read_text("utf-8"))
    assert data["voice"]["output_endpoint"] == chat and data["setup"]["completed_utc"]
    text = svc.path.read_text("utf-8")
    assert ANTHROPIC_KEY not in text and DISCORD_TOKEN not in text
    assert keys.get_key(keys.ANTHROPIC) == ANTHROPIC_KEY  # the (fake) keyring holds it
    assert ANTHROPIC_KEY not in caplog.text and DISCORD_TOKEN not in caplog.text


def test_refusals_come_back_with_their_reasons(
    client: TestClient, ring: MemoryKeyring
) -> None:
    r = client.post(
        "/api/setup/steps/talk_key",
        json={"values": {"talk_key": "F3"}},
        headers=admin(),
    )
    assert r.status_code == 400 and "talk_key" in r.json()["problems"]
    r = client.post("/api/setup/steps/nope", json={"values": {}}, headers=admin())
    assert r.status_code == 404
    r = client.post("/api/setup/steps/paths", json={"values": []}, headers=admin())
    assert r.status_code == 400
    bad = "sk-ant-admin01-" + "q" * 40
    r = client.post(
        "/api/setup/secrets/anthropic", json={"value": bad}, headers=admin()
    )
    assert r.status_code == 400 and bad not in r.text and ring.entries == {}
    r = client.post("/api/setup/finish", headers=admin())
    assert r.status_code == 400 and r.json()["checks"]


def test_the_core_makes_its_own_service_for_its_gos_toml(tmp_path: Path) -> None:
    from gregtec.api.setup_routes import service_of

    store = Store(tmp_path / "core.sqlite")
    try:
        c = Core(
            cfg=Config(gos_home=tmp_path),
            store=store,
            auth=Auth(store, ADMIN),
            meter=UsageMeter(store, 100.0, (80, 100)),
            config_path=tmp_path / "custom.toml",
        )
        made = service_of(c)
        assert made.path == tmp_path / "custom.toml" and service_of(c) is made
        assert made.env.meter is c.meter
    finally:
        store.close()
