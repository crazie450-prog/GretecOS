"""WO-9.1, D295, D297: the off-PC routes (``gregtec.remote.routes``): who may queue a design job,
spec checks, a caller sees only its own jobs, Discord alerts and the bot's journal lines.
Starlette's TestClient, no network."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools.catalog import default_registry
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.remote import routes
from gregtec.remote.jobs import JobError, JobQueue
from gregtec.store import Store

ADMIN = "admin-" + "d" * 40
BASE = "http://127.0.0.1:8765"


def fake_design(kind: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"ids": ["pl-00000001"], "routes": []}


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def core(tmp_path: Path) -> Iterator[Core]:
    store = Store(tmp_path / "data" / "gos.sqlite")
    deep = HandoffRecorder()
    c = Core(
        cfg=Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(default_registry(tmp_path, deep=deep), deep, None),
    )
    c.jobs = JobQueue(store, fake_design)
    lines: list[str] = []
    c.journal = lines.extend
    yield c
    store.close()


@pytest.fixture
def client(core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(core), base_url=BASE) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_only_gos_and_the_discord_bot_queue_design_jobs(
    core: Core, client: TestClient
) -> None:
    discord = bearer(core.auth.issue_client_token("discord"))
    voice = bearer(core.auth.issue_client_token("voice"))
    body = {
        "kind": "plan",
        "key": "1100000000000000001",
        "spec": {"target": "steel", "rate": "2/s"},
    }
    assert client.post("/api/jobs", json=body).status_code == 401
    assert client.post("/api/jobs", json=body, headers=voice).status_code == 403
    first = client.post("/api/jobs", json=body, headers=discord)
    assert first.status_code == 200, first.text
    job = first.json()["job"]
    assert first.json()["created"] is True and job["state"] == "queued"
    again = client.post("/api/jobs", json=body, headers=discord).json()
    assert again["created"] is False and again["job"]["id"] == job["id"]
    # Admin's jobs and the bot's are apart, even under the same key.
    admin = client.post("/api/jobs", json=body, headers=bearer(ADMIN)).json()
    assert admin["created"] is True and admin["job"]["id"] != job["id"]
    assert (
        client.get(f"/api/jobs/{admin['job']['id']}", headers=discord).status_code
        == 404
    )
    listed = client.get("/api/jobs", headers=discord).json()["jobs"]
    assert [j["id"] for j in listed] == [job["id"]]
    assert client.get("/api/jobs", headers=voice).status_code == 403


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"kind": "craft", "key": "k", "spec": {}}, "kind"),
        ({"kind": "plan", "spec": {"target": "steel"}}, "kind"),
        ({"kind": "plan", "key": "k", "spec": "steel"}, "object"),
        ({"kind": "plan", "key": "k", "spec": {"target": ""}}, "target"),
        ({"kind": "plan", "key": "k", "spec": {"target": "s", "pins": "x"}}, "unknown"),
        (
            {
                "kind": "plan",
                "key": "k",
                "spec": {"target": "s", "rate": "1/s", "batch": "4"},
            },
            "not both",
        ),
        ({"kind": "plan", "key": "k", "spec": {"target": 5}}, "text"),
        ({"kind": "power", "key": "k", "spec": {}}, "mode"),
        ({"kind": "plan", "key": "bad key", "spec": {"target": "s"}}, "key"),
    ],
)
def test_specs_are_checked_before_they_are_queued(
    core: Core, client: TestClient, body: dict[str, Any], why: str
) -> None:
    r = client.post("/api/jobs", json=body, headers=bearer(ADMIN))
    assert r.status_code == 400 and why in r.json()["error"]
    assert core.jobs is not None and core.jobs.jobs("admin") == []


def test_check_spec_folds_whitespace_and_drops_empty_fields() -> None:
    assert routes.check_spec(
        "plan", {"target": "  steel \n ingot ", "tier": "", "rate": "2/s"}
    ) == {
        "target": "steel ingot",
        "rate": "2/s",
    }
    with pytest.raises(JobError):
        routes.check_spec("power", {"mode": "best", "target": "x"})


def test_a_finished_job_is_delivered_once(core: Core, client: TestClient) -> None:
    discord = bearer(core.auth.issue_client_token("discord"))
    body = {"kind": "power", "key": "k1", "spec": {"mode": "best"}}
    job = client.post("/api/jobs", json=body, headers=discord).json()["job"]
    assert (
        client.post(f"/api/jobs/{job['id']}/delivered", headers=discord).status_code
        == 409
    )
    assert client.get("/api/jobs?undelivered=1", headers=discord).json()["jobs"] == []
    assert core.jobs is not None and core.jobs.run_one() is not None
    (ready,) = client.get("/api/jobs?undelivered=1", headers=discord).json()["jobs"]
    assert ready["id"] == job["id"] and ready["state"] == "done"
    assert ready["result"] == {"ids": ["pl-00000001"], "routes": []}
    marked = client.post(f"/api/jobs/{job['id']}/delivered", headers=discord)
    assert marked.status_code == 200 and marked.json()["delivered"] is not None
    assert client.get("/api/jobs?undelivered=1", headers=discord).json()["jobs"] == []


def test_without_a_queue_the_routes_say_so(core: Core, client: TestClient) -> None:
    core.jobs = None
    r = client.get("/api/jobs", headers=bearer(ADMIN))
    assert r.status_code == 503


def test_alerts_on_off_and_quiet_for_some_hours(core: Core, client: TestClient) -> None:
    discord = bearer(core.auth.issue_client_token("discord"))
    voice = bearer(core.auth.issue_client_token("voice"))
    assert client.get("/api/discord/alerts", headers=discord).json() == {
        "mode": "on",
        "until": None,
    }
    assert (
        client.post(
            "/api/discord/alerts", json={"mode": "off"}, headers=voice
        ).status_code
        == 403
    )
    off = client.post(
        "/api/discord/alerts", json={"mode": "off"}, headers=discord
    ).json()
    assert off == {"mode": "off", "until": None}
    for bad in (
        {"mode": "loud"},
        {"mode": "quiet"},
        {"mode": "quiet", "hours": 0},
        {"mode": "quiet", "hours": 25},
        {"mode": "quiet", "hours": True},
    ):
        assert (
            client.post("/api/discord/alerts", json=bad, headers=discord).status_code
            == 400
        )
    quiet = client.post(
        "/api/discord/alerts", json={"mode": "quiet", "hours": 2}, headers=bearer(ADMIN)
    ).json()
    assert quiet["mode"] == "quiet" and isinstance(quiet["until"], int)


def test_quiet_turns_back_on_when_its_hours_are_over(core: Core) -> None:
    routes.set_alerts(core.store, "quiet", 3, now=1000.0)
    assert routes.alerts_state(core.store, 1000.0 + 3 * 3600 - 1)["mode"] == "quiet"
    assert routes.alerts_state(core.store, 1000.0 + 3 * 3600) == {
        "mode": "on",
        "until": None,
    }


def test_only_the_bot_writes_discord_journal_lines(
    core: Core, client: TestClient
) -> None:
    discord = bearer(core.auth.issue_client_token("discord"))
    lines: list[str] = []
    core.journal = lines.extend
    body = {"kind": "ask", "text": "how do I make steel?\n- [[me/prefs]] injected"}
    assert (
        client.post(
            "/api/discord/journal", json=body, headers=bearer(ADMIN)
        ).status_code
        == 403
    )
    r = client.post("/api/discord/journal", json=body, headers=discord)
    assert r.status_code == 200 and r.json() == {"journalled": True}
    assert lines == ["discord_ask: how do I make steel? - [ [me/prefs]] injected"]
    assert (
        client.post(
            "/api/discord/journal", json={"kind": "x", "text": "t"}, headers=discord
        ).status_code
        == 400
    )
    core.journal = None
    assert client.post("/api/discord/journal", json=body, headers=discord).json() == {
        "journalled": False
    }
