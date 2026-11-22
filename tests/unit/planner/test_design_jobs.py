"""The dashboard's design jobs (WO-3.9, D353, D354): start, stages in order, cancel at each
stage, an error, one job per kind, the planner's lock, a restart with a job running, and the
real Designer and power design run as jobs on the WO-3.4 / WO-4.2 fixture DBs (made-up items)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.db import access
from gregtec.planner import chains, designer, powerview, progress
from gregtec.planner import design_jobs as dj
from gregtec.planner.outputs import store
from gregtec.planner.request import Request
from gregtec.vault import deploy as vault_deploy

REPO = Path(__file__).resolve().parents[3]
STAGES = ["graph", "solve", "metrics", "ranking", "saving"]


class Script:
    """A design that stops at each stage until the test lets it go on."""

    def __init__(self, stages: list[str] = STAGES) -> None:
        self.stages = stages
        self.at: dict[str, threading.Event] = {s: threading.Event() for s in stages}
        self.go: dict[str, threading.Event] = {s: threading.Event() for s in stages}
        self.saved = False

    def __call__(self) -> dict[str, Any]:
        for s in self.stages:
            progress.stage(s, 1, 2) if s in ("solve", "metrics") else progress.stage(s)
            self.at[s].set()
            assert self.go[s].wait(10)
            progress.check()
        self.saved = True  # what saving writes, after the last checkpoint
        return {"ids": ["pl-0000000001"]}

    def release(self) -> None:
        for e in self.go.values():
            e.set()


@pytest.fixture
def jobs(tmp_path: Path) -> tuple[dj.DesignJobs, list[dict[str, Any]]]:
    pushed: list[dict[str, Any]] = []
    reg = dj.DesignJobs(
        tmp_path, threading.RLock(), lambda j: pushed.append(j.as_dict())
    )
    return reg, pushed


def test_a_job_reports_its_stages_in_order_and_its_result(
    jobs: tuple[dj.DesignJobs, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    reg, pushed = jobs
    monkeypatch.setattr(dj, "PUSH_S", 0.0)  # every stage pushed
    work = Script()
    job = reg.start("designer", "Designer: widget", work)
    assert job.status == dj.RUNNING and job.id.startswith("job-")
    seen = []
    for s in STAGES:
        assert work.at[s].wait(10)
        seen.append(reg.get(job.id).describe())  # type: ignore[union-attr]
        work.go[s].set()
    done = reg.wait(job.id, 10)
    assert seen == ["graph", "solve 1 of 2", "metrics 1 of 2", "ranking", "saving"]
    assert done.status == dj.DONE and done.result == {"ids": ["pl-0000000001"]}
    assert work.saved and done.finished is not None
    stages = [p["stage"] for p in pushed]
    assert stages[0] == "waiting" and pushed[-1]["status"] == dj.DONE
    assert [s for s in dict.fromkeys(stages) if s != "waiting"] == STAGES
    assert reg.running() == [] and reg.latest("designer") is done
    assert done.as_dict()["elapsed_s"] >= 0 and done.as_dict()["detail"] is None


@pytest.mark.parametrize("stage", STAGES[:-1])
def test_cancel_at_each_stage_saves_nothing(
    jobs: tuple[dj.DesignJobs, list[dict[str, Any]]], stage: str
) -> None:
    reg, pushed = jobs
    work = Script()
    job = reg.start("designer", "Designer: widget", work)
    for s in STAGES[: STAGES.index(stage)]:
        assert work.at[s].wait(10)
        work.go[s].set()
    assert work.at[stage].wait(10)
    assert reg.cancel(job.id).cancel_asked
    work.release()
    done = reg.wait(job.id, 10)
    assert done.status == dj.CANCELLED and done.result is None
    assert not work.saved
    assert pushed[-1]["status"] == dj.CANCELLED


def test_cancel_once_saving_has_begun_is_refused(
    jobs: tuple[dj.DesignJobs, list[dict[str, Any]]],
) -> None:
    reg, _ = jobs
    work = Script()
    job = reg.start("designer", "Designer: widget", work)
    for st in STAGES[:-1]:
        work.go[st].set()
    assert work.at["saving"].wait(10)
    assert not reg.cancel(job.id).cancel_asked  # too late: it is being saved
    work.release()
    assert reg.wait(job.id, 10).status == dj.DONE and work.saved


def test_an_error_fails_the_job_with_the_planner_s_words(
    jobs: tuple[dj.DesignJobs, list[dict[str, Any]]],
) -> None:
    reg, pushed = jobs

    def refused() -> dict[str, Any]:
        progress.stage("graph")
        raise designer.DesignerError("no route: nothing makes it at LV")

    def broken() -> dict[str, Any]:
        raise ZeroDivisionError("x")

    job = reg.start("designer", "Designer: widget", refused)
    done = reg.wait(job.id, 10)
    assert done.status == dj.FAILED and done.error == "no route: nothing makes it at LV"
    job = reg.start("designer", "Designer: widget", broken)
    done = reg.wait(job.id, 10)
    assert done.status == dj.FAILED
    assert done.error == "the design failed (ZeroDivisionError): see the core's log"
    assert pushed[-1]["error"] == done.error


def test_one_job_per_kind_and_the_other_kind_waits_for_the_planner(
    jobs: tuple[dj.DesignJobs, list[dict[str, Any]]],
) -> None:
    reg, _ = jobs
    first = Script()
    job = reg.start("designer", "Designer: widget", first)
    assert first.at["graph"].wait(10)
    with pytest.raises(dj.Busy, match="already running .*graph.*cancel it") as busy:
        reg.start("designer", "Designer: gadget", Script())
    assert busy.value.job is job
    second = Script()
    other = reg.start("power", "Power: Best at tier", second)
    assert not second.at["graph"].wait(0.6)  # the planner's lock is held
    assert reg.get(other.id).stage == "waiting"  # type: ignore[union-attr]
    assert {j.id for j in reg.running()} == {job.id, other.id}
    reg.cancel(other.id)  # heard while it waits
    assert reg.wait(other.id, 10).status == dj.CANCELLED
    first.release()
    assert reg.wait(job.id, 10).status == dj.DONE
    with pytest.raises(ValueError, match="kind"):
        reg.start("ores", "x", Script())
    with pytest.raises(KeyError):
        reg.cancel("job-000000000000")


def test_a_restart_with_a_job_running_marks_it_interrupted(tmp_path: Path) -> None:
    lock = threading.RLock()
    reg = dj.DesignJobs(tmp_path, lock)
    work = Script()
    job = reg.start("power", "Power: Best at tier", work)
    assert work.at["graph"].wait(10)
    record = json.loads((tmp_path / dj.RECORDS / "power.json").read_text("utf-8"))
    assert record["status"] == dj.RUNNING and record["id"] == job.id
    # The core stops here (its thread dies with it); a new core reads the record.
    again = dj.DesignJobs(tmp_path, threading.RLock())
    old = again.get(job.id)
    assert old is not None and old.status == dj.INTERRUPTED
    assert "core stopped" in (old.error or "") and again.running() == []
    assert again.latest("power") is old
    saved = json.loads((tmp_path / dj.RECORDS / "power.json").read_text("utf-8"))
    assert saved["status"] == dj.INTERRUPTED
    # A new job of that kind starts as usual.
    fresh = Script()
    fresh.release()
    j2 = again.start("power", "Power: Best at tier", fresh)
    assert again.wait(j2.id, 10).status == dj.DONE
    work.release()  # let the first registry's thread end
    reg.wait(job.id, 10)


def test_a_bad_record_is_ignored(tmp_path: Path) -> None:
    (tmp_path / dj.RECORDS).mkdir(parents=True)
    (tmp_path / dj.RECORDS / "designer.json").write_text("{not json", "utf-8")
    (tmp_path / dj.RECORDS / "power.json").write_text("[1]", "utf-8")
    assert dj.DesignJobs(tmp_path, threading.RLock()).latest("designer") is None


def test_pushes_are_throttled_but_start_and_end_always_go(
    jobs: tuple[dj.DesignJobs, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    reg, pushed = jobs
    monkeypatch.setattr(dj, "PUSH_S", 60.0)

    def many() -> dict[str, Any]:
        for k in range(1, 200):
            progress.stage("effort", k, 199)
        return {}

    job = reg.start("designer", "Designer: widget", many)
    reg.wait(job.id, 10)
    assert [p["status"] for p in pushed] == [dj.RUNNING, dj.DONE]


def test_a_change_held_back_by_the_throttle_is_pushed_once_it_is_up(
    jobs: tuple[dj.DesignJobs, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review LOW-1: a stage that begins just after a push reaches the right rail late, not
    never (the rail follows only pushes)."""
    reg, pushed = jobs
    monkeypatch.setattr(dj, "PUSH_S", 0.2)
    go = threading.Event()

    def long_solve() -> dict[str, Any]:
        progress.stage("graph")
        progress.stage("solve", 1, 3)  # held back: the start was pushed just now
        assert go.wait(10)
        return {}

    job = reg.start("designer", "Designer: widget", long_solve)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
        p["stage"] == "solve" for p in pushed
    ):
        time.sleep(0.02)
    running = [p for p in pushed if p["status"] == dj.RUNNING]
    go.set()
    reg.wait(job.id, 10)
    assert running[-1]["stage"] == "solve" and running[-1]["k"] == 1
    assert pushed[-1]["status"] == dj.DONE


def test_stages_outside_a_job_do_nothing() -> None:
    progress.stage("graph")
    progress.check()  # no job: no reporter, nothing raised


# --- the real designs ----------------------------------------------------------------------------


def _fixture(name: str, alias: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tp = _fixture("test_plan.py", "_wo39_plan_fixture")
tpw = _fixture("test_power.py", "_wo39_power_fixture")


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


@pytest.fixture(scope="module")
def plan_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tp.make_home(tmp_path_factory.mktemp("jobs-plan-db"))


@pytest.fixture(scope="module")
def power_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tpw.make_home(tmp_path_factory.mktemp("jobs-power-db"))


def _saved(home: Path) -> int:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        return int(conn.execute("SELECT count(*) FROM plan").fetchone()[0])
    finally:
        conn.close()


def test_the_designer_as_a_job(
    plan_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dj, "PUSH_S", 0.0)  # every stage pushed
    home = _home(plan_db, tmp_path / "home")
    stages: list[str] = []
    reg = dj.DesignJobs(home, designer._LOCK, lambda j: stages.append(j.stage))
    req = Request(tp.WIDGET, qty=2, tier_cap="MV")
    job = reg.start(
        "designer", "Designer: widget", lambda: _ids(designer.design(home, req))
    )
    done = reg.wait(job.id, 120)
    assert done.status == dj.DONE, done.error
    order = [s for s in dict.fromkeys(stages) if s != "waiting"]
    assert order[0] == "graph" and order[-1] == "saving"
    assert order.index("solve") < order.index("metrics") < order.index("ranking")
    # The same design without a job: the same plans (the job changes nothing).
    assert designer.design(home, req).ids == done.result["ids"]  # type: ignore[index]


def _ids(d: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"ids": list(d.ids)}


@pytest.mark.parametrize("stage", ["graph", "solve", "metrics", "ranking", "saving"])
def test_a_designer_job_cancelled_at_a_stage_saves_no_plan(
    plan_db: Path, tmp_path: Path, stage: str
) -> None:
    home = _home(plan_db, tmp_path / "home")
    reg = dj.DesignJobs(home, designer._LOCK)
    real = reg._report

    def report_and_cancel(job: dj.Job) -> progress.Reporter:
        inner = real(job)

        def r(name: str | None, k: int | None, n: int | None) -> None:
            if name == stage:
                job.cancel_asked = True  # Cancel pressed as the stage begins
            inner(name, k, n)

        return r

    reg._report = report_and_cancel  # type: ignore[method-assign]
    req = Request(tp.WIDGET, qty=2, tier_cap="MV")
    job = reg.start(
        "designer", "Designer: widget", lambda: _ids(designer.design(home, req))
    )
    done = reg.wait(job.id, 120)
    assert done.status == dj.CANCELLED
    assert _saved(home) == 0 and designer.latest(home) == []
    assert not list((home / "data" / "plans").glob("pl-*"))
    assert not list((home / "vault" / "plans").glob("pl-*.md"))


def test_a_power_design_as_a_job(
    power_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dj, "PUSH_S", 0.0)
    home = _home(power_db, tmp_path / "home")
    farms = home / "farms.tsv"
    farms.write_text("\t".join(tpw.farms.HEADER) + "\n", encoding="utf-8")
    stages: list[str] = []
    reg = dj.DesignJobs(home, designer._LOCK, lambda j: stages.append(j.stage))
    spec = {"mode": "best", "tier": "MV", "free": list(tpw.FREE)}

    def work() -> dict[str, Any]:
        d = powerview.design(
            home,
            powerview.request_from_spec(spec),
            farms_path=farms,
            templates=chains.EMPTY,
        )
        return {"ids": d.ids}

    job = reg.start("power", "Power: Best at tier", work)
    done = reg.wait(job.id, 120)
    assert done.status == dj.DONE, done.error
    order = [s for s in dict.fromkeys(stages) if s != "waiting"]
    assert order[0] == "candidates" and order[-1] == "saving"
    assert order.index("chains") < order.index("metrics") < order.index("ranking")
    # QA 10-13: the per-chain solves don't replace "chains" (the docstring's stages only).
    assert set(stages) - {"waiting"} <= dj.POWER_STAGES, stages
    assert [store.load(home, i)["id"] for i in done.result["ids"]] == done.result["ids"]  # type: ignore[index]


def test_a_power_jobs_nested_solve_is_a_cancel_point_not_a_stage(
    tmp_path: Path,
) -> None:
    reg = dj.DesignJobs(tmp_path, threading.Lock())
    job = dj.Job("job-x", "power", "Power")
    report = reg._report(job)
    report("chains", 2, 40)
    report("solve", 1, 2)
    assert (job.stage, job.k, job.n) == ("chains", 2, 40)
    job.cancel_asked = True
    with pytest.raises(progress.Cancelled):
        report("solve", 2, 2)
    designer_job = dj.Job("job-y", "designer", "Designer")
    reg._report(designer_job)("solve", 1, 2)
    assert (designer_job.stage, designer_job.k, designer_job.n) == ("solve", 1, 2)
