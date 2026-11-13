"""WO-3.7: the Designer in the core's API (gregtec.api.plan_routes), the real app in-process, on the
tool tests' made-up export (its steel ingot has three routes at HV).

Who may read and who may design, pin, accept and rename (D250: Clayton's paired devices and his
gos commands write; the client tokens read; nothing without a credential), and the flow the
dashboard drives: design -> view -> alternatives -> pin -> reload -> re-solve -> accept -> name.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.api.app import create_app
from gregtec.cards import validate as card_problems
from gregtec.vault import deploy as vault_deploy

from .conftest import STEEL
from .test_my_base_api import admin, make_core, pair, token

REPO = Path(__file__).resolve().parents[3]
SPEC = {"target": STEEL, "rate": "1/s", "tier": "HV"}


@pytest.fixture
def home(fresh_home: Callable[..., Path]) -> Path:
    h = fresh_home()
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


@pytest.fixture
def client(home: Path) -> Iterator[TestClient]:
    core = make_core(home)
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            yield c
    finally:
        core.store.close()


def ok(r: Any) -> dict[str, Any]:  # noqa: ANN401 - a TestClient response
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def test_who_may_read_and_write(client: TestClient) -> None:
    assert client.get("/api/designer").status_code == 401
    assert client.post("/api/designer/design", json=SPEC).status_code == 401
    voice = token(client)
    assert (
        client.post("/api/designer/design", json=SPEC, headers=voice).status_code == 403
    )
    assert ok(client.get("/api/designer", headers=voice)) == {"ids": []}
    made = ok(client.post("/api/designer/design", json=SPEC, headers=admin()))
    pid = made["ids"][0]
    assert ok(client.get(f"/api/plans/{pid}/designer", headers=voice))["id"] == pid
    for path, body in (
        (f"/api/plans/{pid}/accept", {}),
        (f"/api/plans/{pid}/resolve", {}),
        (f"/api/plans/{pid}/name", {"name": "x"}),
        (f"/api/plans/{pid}/pin", {"node": STEEL, "recipe": None}),
        (f"/api/plans/{pid}/status", {"status": "retired"}),  # WO-4.4: Trusted only
    ):
        assert client.post(path, json=body, headers=voice).status_code == 403
    # A paired dashboard may.
    cookie = pair(client)
    client.cookies.set("gos_device", cookie)
    try:
        r = client.post(f"/api/plans/{pid}/name", json={"name": "Steel works"})
    finally:
        client.cookies.clear()
    assert r.status_code == 200, r.text


def test_design_view_pin_reload_resolve_accept(client: TestClient, home: Path) -> None:
    made = ok(client.post("/api/designer/design", json=SPEC, headers=admin()))
    ids = made["ids"]
    assert len(ids) == 3 and made["problems"] == []
    view = made["view"]
    assert [r["id"] for r in view["routes"]] == ids
    assert card_problems(view["comparison"]) == []
    for r in view["routes"]:
        assert card_problems(r["flow"]) == [] and card_problems(r["bom"]) == []
    assert ok(client.get("/api/designer", headers=admin()))["ids"] == ids
    listed = ok(client.get("/api/plans", headers=admin()))["plans"]
    assert {p["id"] for p in listed} == set(ids)

    # The second route's dust node: its alternatives, then pin the one it doesn't use.
    second = view["routes"][1]
    dust = next(n for n in second["nodes"] if n["item"] != STEEL)
    alts = ok(
        client.get(
            f"/api/plans/{second['id']}/alternatives",
            params={"node": dust["item"]},
            headers=admin(),
        )
    )
    other = next(a["recipe"] for a in alts["alternatives"] if not a["used"])
    pinned = ok(
        client.post(
            f"/api/plans/{second['id']}/pin",
            json={"node": dust["item"], "recipe": other},
            headers=admin(),
        )
    )
    pid = pinned["ids"][0]
    pin = {"node": dust["item"], "recipe": other}
    assert pin in pinned["view"]["routes"][0]["pins"]
    # Reload: the latest design and its view carry the pin.
    assert ok(client.get("/api/designer", headers=admin()))["ids"] == pinned["ids"]
    again = ok(client.get(f"/api/plans/{pid}/designer", headers=admin()))
    assert all(pin in r["pins"] for r in again["routes"])
    # Re-solve: the same ids, the pin held.
    solved = ok(client.post(f"/api/plans/{pid}/resolve", json={}, headers=admin()))
    assert solved["ids"] == pinned["ids"]
    assert pin in solved["view"]["routes"][0]["pins"]

    done = ok(client.post(f"/api/plans/{pid}/accept", json={}, headers=admin()))
    assert done["status"] == "accepted" and done["problems"] == []
    assert done["line"].startswith("base/lines/ln-")
    assert (home / "vault" / done["line"]).is_file()
    assert done["ledger"]["recorded"] is True
    assert done["view"]["routes"][0]["status"] == "accepted"
    again = client.post(f"/api/plans/{pid}/accept", json={}, headers=admin())
    assert again.status_code == 400 and "accepted" in again.json()["error"]

    # WO-4.4: the ledger over HTTP, and a lifecycle move that updates it.
    led = ok(client.get("/api/ledger", headers=admin()))
    assert [ln["plan"] for ln in led["ledger"]["lines"]] == [pid]
    assert led["card"]["type"] == "power_ledger" and led["card"]["tiers"]
    assert led["running"]["tiers"] == []
    bad = client.post(
        f"/api/plans/{pid}/status", json={"status": "proposed"}, headers=admin()
    )
    assert bad.status_code == 400
    moved = ok(
        client.post(
            f"/api/plans/{pid}/status", json={"status": "retired"}, headers=admin()
        )
    )
    assert moved["status"] == "retired"
    assert moved["ledger"]["note"].startswith("taken out of the EU ledger")
    assert ok(client.get("/api/ledger", headers=admin()))["ledger"]["lines"] == []

    named = ok(
        client.post(
            f"/api/plans/{pid}/name", json={"name": "My steel"}, headers=admin()
        )
    )
    assert named["view"]["routes"][0]["name"] == "My steel"


def test_refusals(client: TestClient) -> None:
    def post(path: str, body: object) -> int:
        return client.post(path, json=body, headers=admin()).status_code

    assert post("/api/designer/design", {"target": STEEL, "bogus": 1}) == 400
    assert post("/api/designer/design", {"target": STEEL, "rate": "fast"}) == 400
    assert post("/api/designer/design", {"target": STEEL, "routes": 9}) == 400
    assert post("/api/designer/design", {}) == 400
    assert post("/api/plans/pl-0000000000/accept", {}) == 404
    assert post("/api/plans/BAD..ID/accept", {}) == 400
    assert (
        client.get("/api/plans/pl-0000000000/designer", headers=admin()).status_code
        == 404
    )
    made = ok(client.post("/api/designer/design", json=SPEC, headers=admin()))
    pid = made["ids"][0]
    assert post(f"/api/plans/{pid}/pin", {"node": 5}) == 400
    assert post(f"/api/plans/{pid}/name", {"name": ""}) == 400
    assert (
        client.get(f"/api/plans/{pid}/alternatives", headers=admin()).status_code == 400
    )


def test_an_unclear_target_asks(client: TestClient) -> None:
    out = ok(
        client.post(
            "/api/designer/design",
            json={"target": "ingot", "tier": "HV"},
            headers=admin(),
        )
    )
    assert out["clarify"]["type"] == "choices"
    assert card_problems(out["clarify"]) == []
