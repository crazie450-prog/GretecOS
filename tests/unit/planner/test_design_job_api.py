"""The design jobs over HTTP (WO-3.9, D353): who may start and cancel, the checks before a job
starts, one job per kind (409), the job's result equal to the waiting route's answer, the state's
``jobs`` (the right rail), on the WO-3.4 and WO-4.2 fixture DBs (made-up items)."""

from __future__ import annotations

import importlib.util
import sqlite3
import statistics
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.agents import route_review as rr
from gregtec.api import plan_routes
from gregtec.db import access
from gregtec.planner import chains, designer, powerview, progress
from gregtec.planner import design_jobs as dj
from gregtec.planner.request import Request
from gregtec.vault import deploy as vault_deploy

REPO = Path(__file__).resolve().parents[3]
ADMIN = "admin-" + "b" * 40


def _fixture(name: str, alias: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tp = _fixture("test_plan.py", "_wo39a_plan_fixture")
tpw = _fixture("test_power.py", "_wo39a_power_fixture")


@pytest.fixture(scope="module")
def plan_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return Path(tp.make_home(tmp_path_factory.mktemp("job-api-plan")))


@pytest.fixture(scope="module")
def power_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return Path(tpw.make_home(tmp_path_factory.mktemp("job-api-power")))


def _home(db: Path, h: Path) -> Path:
    (h / "data").mkdir(parents=True)
    src = access.connect(db, readonly=True)
    dst = sqlite3.connect(h / "data" / "gos.sqlite")
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


def _core(home: Path) -> Any:  # noqa: ANN401
    from gregtec.api.app import Core
    from gregtec.api.security import Auth
    from gregtec.config import Config
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store

    s = Store(access.db_path(home))
    return Core(
        cfg=Config(gos_home=home),
        store=s,
        auth=Auth(s, ADMIN),
        meter=UsageMeter(s, 100.0, (80, 100)),
    )


ADMIN_H = {"Authorization": f"Bearer {ADMIN}"}


def _ended(c: Any, job_id: str) -> dict[str, Any]:  # noqa: ANN401
    for _ in range(1200):
        job = c.get(f"/api/design-jobs/{job_id}", headers=ADMIN_H).json()["job"]
        if job["status"] != dj.RUNNING:
            return dict(job)
        time.sleep(0.05)
    raise AssertionError("the job never ended")


def test_the_designer_job_api(
    plan_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api import dashboard
    from gregtec.api.app import create_app

    home = _home(plan_db, tmp_path / "home")
    core = _core(home)
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            tok = c.post("/api/tokens/voice", headers=ADMIN_H).json()["token"]
            voice = {"Authorization": f"Bearer {tok}"}
            spec = {"target": tp.WIDGET, "rate": "2/s", "tier": "MV"}
            body = {"spec": spec}
            assert c.post("/api/designer/jobs", json=body).status_code == 401
            assert (
                c.post("/api/designer/jobs", json=body, headers=voice).status_code
                == 403
            )
            for bad, code in (
                ({}, 400),
                ({"spec": spec, "resolve": "pl-0000000001"}, 400),
                ({"spec": "steel"}, 400),
                ({"spec": {"target": tp.WIDGET, "colour": "red"}}, 400),
                ({"resolve": "x"}, 400),
                ({"resolve": "pl-0000000001"}, 404),
                ({"pin": {"plan": "pl-0000000001", "node": tp.GAS}}, 404),
            ):
                got = c.post("/api/designer/jobs", json=bad, headers=ADMIN_H)
                assert got.status_code == code, (bad, got.text)
            started = c.post("/api/designer/jobs", json=body, headers=ADMIN_H)
            assert started.status_code == 200, started.text
            job = started.json()["job"]
            assert job["kind"] == "designer" and job["status"] == dj.RUNNING
            assert job["title"] == f"Designer: {tp.WIDGET} · MV"
            done = _ended(c, job["id"])
            assert done["status"] == dj.DONE, done["error"]
            # The result is what POST /api/designer/design answers for the same spec.
            waited = c.post("/api/designer/design", json=spec, headers=ADMIN_H).json()
            assert done["result"]["ids"] == waited["ids"]
            assert done["result"]["view"] == waited["view"]
            ids = done["result"]["ids"]
            again = c.post(
                "/api/designer/jobs", json={"resolve": ids[0]}, headers=ADMIN_H
            )
            assert _ended(c, again.json()["job"]["id"])["result"]["ids"] == ids
            listed = c.get("/api/design-jobs", headers=voice).json()
            assert listed["running"] == []
            latest = listed["latest"]["designer"]
            assert latest["status"] == dj.DONE and "result" not in latest
            # One at a time: a design held at its graph stage; a second request is a 409.
            hold, at = threading.Event(), threading.Event()
            real = designer.design

            def slow(h: Path, req: Request) -> Any:  # noqa: ANN401
                progress.stage("graph")
                at.set()
                assert hold.wait(10)
                progress.check()
                return real(h, req)

            monkeypatch.setattr(designer, "design", slow)
            first = c.post("/api/designer/jobs", json=body, headers=ADMIN_H)
            jid = first.json()["job"]["id"]
            assert at.wait(10)
            busy = c.post("/api/designer/jobs", json=body, headers=ADMIN_H)
            assert busy.status_code == 409 and "already running" in busy.text
            rail = [j for j in dashboard.state(core, True)["jobs"] if j["id"] == jid]
            assert (
                rail and rail[0]["kind"] == "designer" and rail[0]["detail"] == "graph"
            )
            assert (
                rail[0]["elapsed_s"] >= 0
            )  # the core's count: the rail's time (LOW-3)
            assert (
                c.post(f"/api/design-jobs/{jid}/cancel", headers=voice).status_code
                == 403
            )
            nope = c.post("/api/design-jobs/job-000000000000/cancel", headers=ADMIN_H)
            assert nope.status_code == 404
            assert c.get("/api/design-jobs/nope", headers=ADMIN_H).status_code == 404
            asked = c.post(f"/api/design-jobs/{jid}/cancel", headers=ADMIN_H)
            assert asked.json()["job"]["cancel_asked"] is True
            hold.set()
            assert _ended(c, jid)["status"] == dj.CANCELLED
            assert not [
                j for j in dashboard.state(core, True)["jobs"] if j["id"] == jid
            ]
            # The core answers while a design computes (a busy Python loop, as the planner is).
            # The loop runs until the test lets it go, so every answer below came while it ran:
            # a core the job starved could answer only after it (then "done", after the 60 s
            # cap). The bound is on the median of the five pairs, not the slowest (WO-11.4). It
            # mostly measures this test's own client thread waiting for the GIL beside the busy
            # job (an idle job: 0-16 ms; a browser in another process, measured on the harness
            # DB during a real design: 8 ms). Measured medians with this busy job: 0.21-0.40 s on
            # Ubuntu runners, 0.34-0.91 s on Windows runners (timer granularity per GIL hand-off).
            # A job that holds the GIL in 50 ms slices (sys.setswitchinterval(0.05)) gives
            # 3.1-4.5 s on both and fails it.
            free = threading.Event()

            def busy(h: Path, req: Request) -> Any:  # noqa: ANN401
                end = time.monotonic() + 60.0
                while not free.is_set() and time.monotonic() < end:
                    sum(i * i for i in range(2000))
                    progress.check()
                return real(h, req)

            monkeypatch.setattr(designer, "design", busy)
            jid = c.post("/api/designer/jobs", json=body, headers=ADMIN_H).json()[
                "job"
            ]["id"]
            pairs: list[float] = []
            try:
                for _ in range(5):
                    t0 = time.monotonic()
                    assert c.get("/api/health", headers=ADMIN_H).status_code == 200
                    got = c.get(f"/api/design-jobs/{jid}", headers=ADMIN_H)
                    pairs.append(time.monotonic() - t0)
                    assert got.status_code == 200
                    assert got.json()["job"]["status"] == dj.RUNNING
                    time.sleep(0.2)
            finally:
                free.set()
            assert statistics.median(pairs) < 2.0, pairs
            assert _ended(c, jid)["status"] == dj.DONE
    finally:
        core.store.close()


class HeldReviewer:
    """A fake route reviewer (no model) whose "model call" waits until the test lets it go,
    hearing a cancel as the real one does; it changes nothing."""

    def __init__(self) -> None:
        self.waiting = threading.Event()
        self.go = threading.Event()

    def run(self, *_: Any) -> rr.Outcome:  # noqa: ANN401
        progress.stage("review", 1, 8)
        self.waiting.set()
        while not self.go.wait(0.05):
            progress.check()
        return rr.Outcome({"id": "rv-0123456789", "status": "done"}, None)


def test_accept_and_rename_go_ahead_while_a_reviewed_design_job_runs(
    plan_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D411 (6): the Designer view keeps accept and rename usable during a design job, so
    the API must answer them while one (a reviewed one) runs; a second design, re-solve or pin
    job is still a 409 (D353). The job re-saves the same plans (the same spec): its result
    keeps the accepted status and the new name, not the ones it started with."""
    from fastapi.testclient import TestClient

    from gregtec.api.app import create_app

    home = _home(plan_db, tmp_path / "home")
    rv = HeldReviewer()
    monkeypatch.setattr(plan_routes, "reviewer_for", lambda core: lambda: rv)
    core = _core(home)
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            spec = {"target": tp.WIDGET, "rate": "2/s", "tier": "MV"}
            first = c.post("/api/designer/design", json=spec, headers=ADMIN_H).json()
            ids = first["ids"]
            body = {"spec": {**spec, "review": True}}
            started = c.post("/api/designer/jobs", json=body, headers=ADMIN_H)
            assert started.status_code == 200, started.text
            jid = started.json()["job"]["id"]
            assert rv.waiting.wait(120)
            for other in (
                body,
                {"resolve": ids[0]},
                {"pin": {"plan": ids[0], "node": tp.WIDGET, "recipe": None}},
            ):
                busy = c.post("/api/designer/jobs", json=other, headers=ADMIN_H)
                assert busy.status_code == 409, (other, busy.text)
            took = c.post(f"/api/plans/{ids[0]}/accept", headers=ADMIN_H)
            assert took.status_code == 200, took.text
            assert took.json()["status"] == "accepted"
            named = c.post(
                f"/api/plans/{ids[0]}/name",
                json={"name": "Widget line"},
                headers=ADMIN_H,
            )
            assert named.status_code == 200, named.text
            row = named.json()["view"]["routes"][0]
            assert (row["id"], row["name"], row["status"]) == (
                ids[0],
                "Widget line",
                "accepted",
            )
            job = c.get(f"/api/design-jobs/{jid}", headers=ADMIN_H).json()["job"]
            assert job["status"] == dj.RUNNING  # both answered while it ran
            rv.go.set()
            done = _ended(c, jid)
            assert done["status"] == dj.DONE, done["error"]
            assert done["result"]["ids"] == ids
            top = done["result"]["view"]["routes"][0]
            assert (top["id"], top["name"], top["status"]) == (
                ids[0],
                "Widget line",
                "accepted",
            )
    finally:
        core.store.close()


def test_the_power_job_api(
    power_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api.app import create_app

    home = _home(power_db, tmp_path / "home")
    farms = home / "farms.tsv"
    farms.write_text("\t".join(tpw.farms.HEADER) + "\n", encoding="utf-8")
    real = powerview.design
    monkeypatch.setattr(
        powerview,
        "design",
        lambda h, req, **kw: real(h, req, farms_path=farms, templates=chains.EMPTY),
    )
    core = _core(home)
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            bad = c.post("/api/power/jobs", json={"mode": "fastest"}, headers=ADMIN_H)
            assert bad.status_code == 400 and "mode" in bad.text
            spec = {"mode": "best", "tier": "MV", "free": list(tpw.FREE)}
            started = c.post("/api/power/jobs", json=spec, headers=ADMIN_H)
            assert started.status_code == 200, started.text
            job = started.json()["job"]
            assert job["kind"] == "power" and job["title"] == "Power: Best at tier · MV"
            done = _ended(c, job["id"])
            assert done["status"] == dj.DONE, done["error"]
            ids = done["result"]["ids"]
            assert [o["id"] for o in done["result"]["view"]["options"]] == ids
            assert c.get("/api/power", headers=ADMIN_H).json()["ids"] == ids
    finally:
        core.store.close()
