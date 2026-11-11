"""WO-3.8: My base in the core's API (gregtec.base.routes), the real app in-process.

Who may read and who may edit (D212: Clayton's paired devices and his gos commands edit; the
client tokens read; nothing without a credential), every edit and its refusals, the search that
adds rows, and the acceptance: a tier override sticks across a restart and a ``gos db build``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.api import security
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.db import access, build
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

from .conftest import JET_FUEL, STEEL

ADMIN = "admin-" + "a" * 40
BASE = "http://gos.localhost:8765"
EBF = 1000


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


def make_core(home: Path) -> Core:
    store = Store(access.db_path(home))
    return Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )


@pytest.fixture
def home(fresh_home: Callable[..., Path]) -> Path:
    return fresh_home()


@pytest.fixture
def client(home: Path) -> Iterator[TestClient]:
    core = make_core(home)
    try:
        with TestClient(create_app(core), base_url=BASE) as c:
            yield c
    finally:
        core.store.close()


def admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}"}


def token(client: TestClient, name: str = "voice") -> dict[str, str]:
    issued = client.post(f"/api/tokens/{name}", headers=admin()).json()["token"]
    return {"Authorization": f"Bearer {issued}"}


def pair(client: TestClient) -> str:
    """Pair a device as the dashboard does; the client keeps no cookie. Returns it."""
    rid = client.post("/api/pair/start", json={"name": "monitor 2"}).json()[
        "request_id"
    ]
    pending = client.get("/api/pair/pending", headers=admin()).json()
    code = next(p["code"] for p in pending if p["request_id"] == rid)
    done = client.post("/api/pair/complete", json={"request_id": rid, "code": code})
    assert done.status_code == 200, done.text
    cookie = done.cookies[security.COOKIE]
    client.cookies.clear()
    return cookie


def edit(
    client: TestClient,
    method: str,
    path: str,
    body: object = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    r = client.request(method, path, json=body, params=params, headers=admin())
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


# --- reading -----------------------------------------------------------------------------------


def test_the_defaults(client: TestClient) -> None:
    """D63 and D64: the quests' tier, nothing owned, nothing stocked."""
    base = client.get("/api/base", headers=admin()).json()
    assert base["tier"] == {
        "stage": "stone",
        "tier": 0,
        "how": "quests",
        "detected": "stone",
        "detected_tier": 0,
        "quest": None,
        "quest_name": None,
    }
    assert base["machines"] == [] and base["stock"] == [] and base["lines"] == []
    assert base["has_db"] is True
    assert [s["stage"] for s in base["stages"]][:3] == ["stone", "steam", "LV"]


def test_who_may_read_and_edit(client: TestClient) -> None:
    assert client.get("/api/base").status_code == 401
    voice = token(client)
    assert client.get("/api/base", headers=voice).status_code == 200
    for method, path, body in (
        ("POST", "/api/base/tier", {"stage": "HV"}),
        ("POST", "/api/base/machines", {"mte_id": EBF, "count": 1}),
        ("DELETE", f"/api/base/machines/{EBF}", None),
        ("POST", "/api/base/stock", {"key": STEEL, "qty": 1}),
    ):
        r = client.request(method, path, json=body, headers=voice)
        assert r.status_code == 403, (method, path)
    r = client.delete("/api/base/stock", params={"key": STEEL}, headers=voice)
    assert r.status_code == 403
    assert client.get("/api/base", headers=admin()).json()["machines"] == []
    # A paired device (the dashboard) edits.
    client.cookies.set(security.COOKIE, pair(client))
    r = client.post("/api/base/machines", json={"mte_id": EBF, "count": 1})
    assert r.status_code == 200 and r.json()["machines"][0]["count"] == 1


def test_a_cross_site_edit_is_refused(client: TestClient) -> None:
    client.cookies.set(security.COOKIE, pair(client))
    r = client.post(
        "/api/base/tier",
        json={"stage": "IV"},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403
    assert client.get("/api/base").json()["tier"]["how"] == "quests"


# --- editing -----------------------------------------------------------------------------------


def test_machines_are_ticked_and_cleared(client: TestClient) -> None:
    base = edit(client, "POST", "/api/base/machines", {"mte_id": EBF, "count": 2})
    (row,) = base["machines"]
    assert (
        row["ref"],
        row["name"],
        row["count"],
        row["source"],
        row["multiblock"],
    ) == (
        "mte:1000",
        "Electric Blast Furnace",
        2,
        "manual",
        True,
    )
    base = edit(client, "POST", "/api/base/machines", {"mte_id": EBF, "count": 0})
    assert base["machines"][0]["count"] == 0  # "none", kept as a row
    base = edit(client, "DELETE", f"/api/base/machines/{EBF}")
    assert base["machines"] == []


def test_stock_is_entered_and_removed(client: TestClient) -> None:
    edit(client, "POST", "/api/base/stock", {"key": STEEL, "qty": 64})
    base = edit(
        client, "POST", "/api/base/stock", {"key": f"fluid:{JET_FUEL}", "qty": 2000}
    )
    assert [(s["key"], s["name"], s["qty"], s["unit"]) for s in base["stock"]] == [
        (f"fluid:{JET_FUEL}", "Jet Fuel A", 2000.0, "L"),
        (STEEL, "Steel Ingot", 64.0, "items"),
    ]
    base = edit(client, "DELETE", "/api/base/stock", params={"key": STEEL})
    assert [s["key"] for s in base["stock"]] == [f"fluid:{JET_FUEL}"]


@pytest.mark.parametrize(
    ("path", "body", "why"),
    [
        ("/api/base/machines", {"mte_id": 99999, "count": 1}, "isn't a machine"),
        ("/api/base/machines", {"mte_id": EBF, "count": -1}, "count must be"),
        ("/api/base/machines", {"mte_id": EBF}, "count must be"),
        ("/api/base/machines", {"count": 1}, "mte_id must be"),
        ("/api/base/stock", {"key": "Steel Ingot", "qty": 1}, "isn't an item or fluid"),
        ("/api/base/stock", {"key": STEEL, "qty": 0}, "above 0"),
        ("/api/base/stock", {"key": STEEL}, "must be a number"),
        ("/api/base/tier", {"stage": "hv"}, "unknown stage"),
        ("/api/base/tier", {"stage": 3}, "stage must be"),
        ("/api/base/tier", {}, "stage is required"),
    ],
)
def test_bad_edits_are_refused_with_the_reason(
    client: TestClient, path: str, body: dict[str, Any], why: str
) -> None:
    r = client.post(path, json=body, headers=admin())
    assert r.status_code == 400 and why in r.json()["error"]


def test_edits_must_be_json(client: TestClient) -> None:
    r = client.post(
        "/api/base/tier",
        content="stage=HV",
        headers={**admin(), "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 415


# --- finding rows to add -----------------------------------------------------------------------


def test_find_items_and_machines(client: TestClient) -> None:
    items = client.get(
        "/api/base/find", params={"q": "stainless steel ingot"}, headers=admin()
    ).json()
    assert items["candidates"][0]["key"].endswith("@11306")
    assert all(c["kind"] in ("item", "fluid") for c in items["candidates"])
    machines = client.get(
        "/api/base/find",
        params={"q": "electric blast furnace", "kind": "machine"},
        headers=admin(),
    ).json()
    assert machines["candidates"][0]["mte_id"] == EBF
    r = client.get("/api/base/find", params={"q": "x", "kind": "ore"}, headers=admin())
    assert r.status_code == 400


def test_find_before_a_db_build(tmp_path: Path) -> None:
    core = make_core(tmp_path)
    try:
        with TestClient(create_app(core), base_url=BASE) as c:
            r = c.get("/api/base/find", params={"q": "steel"}, headers=admin())
            assert r.status_code == 404 and "gos db build" in r.json()["error"]
            base = c.get("/api/base", headers=admin()).json()
            assert base["has_db"] is False and base["machines"] == []
            r = c.post(
                "/api/base/stock", json={"key": STEEL, "qty": 1}, headers=admin()
            )
            assert r.status_code == 400 and "gos db build" in r.json()["error"]
    finally:
        core.store.close()


# --- acceptance: the override sticks -----------------------------------------------------------


def test_a_tier_override_sticks_across_restarts_and_db_rebuilds(home: Path) -> None:
    core = make_core(home)
    try:
        with TestClient(create_app(core), base_url=BASE) as c:
            base = edit(c, "POST", "/api/base/tier", {"stage": "IV"})
            assert (base["tier"]["stage"], base["tier"]["how"]) == ("IV", "override")
            edit(c, "POST", "/api/base/machines", {"mte_id": EBF, "count": 4})
            edit(c, "POST", "/api/base/stock", {"key": STEEL, "qty": 9})
            # The header's tier (and theme) follows at once.
            assert c.get("/api/state", headers=admin()).json()["tier"]["stage"] == "IV"
    finally:
        core.store.close()
    build.build(home)  # `gos db build` again
    core = make_core(home)  # a new core process
    try:
        with TestClient(create_app(core), base_url=BASE) as c:
            base = c.get("/api/base", headers=admin()).json()
            assert (
                base["tier"]["stage"],
                base["tier"]["tier"],
                base["tier"]["how"],
            ) == (
                "IV",
                5,
                "override",
            )
            assert [(m["mte_id"], m["count"]) for m in base["machines"]] == [(EBF, 4)]
            assert [(s["key"], s["qty"]) for s in base["stock"]] == [(STEEL, 9.0)]
            cleared = edit(c, "POST", "/api/base/tier", {"stage": None})
            assert (cleared["tier"]["stage"], cleared["tier"]["how"]) == (
                "stone",
                "quests",
            )
    finally:
        core.store.close()
