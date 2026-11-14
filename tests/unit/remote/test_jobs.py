"""WO-9.1, D295: the durable design-job queue (``gregtec.remote.jobs``): idempotent submits,
one run per job, jobs that survive a core restart (and a crash mid-job), and the journal."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from gregtec.remote import jobs
from gregtec.remote.jobs import DONE, FAILED, QUEUED, RUNNING, JobError, JobQueue
from gregtec.store import Store

SPEC = {"target": "steel ingot", "rate": "2/s"}


class Runs:
    """A fake designer: counts each run, per spec target."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, dict[str, Any]]] = []
        self.lock = threading.Lock()
        self.fail: str | None = None

    def __call__(self, kind: str, spec: Mapping[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.runs.append((kind, dict(spec)))
        if self.fail:
            raise RuntimeError(self.fail)
        return {"ids": ["pl-aaaa1111", "pl-aaaa2222"], "routes": []}


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "data" / "gos.sqlite"


@pytest.fixture
def store(db: Path) -> Iterator[Store]:
    s = Store(db)
    yield s
    s.close()


def test_submit_is_idempotent_by_client_and_key(store: Store) -> None:
    q = JobQueue(store, Runs())
    job, created = q.submit("discord", "1234567890", "plan", SPEC)
    assert created and job.state == QUEUED and job.id.startswith("j-")
    again, created = q.submit("discord", "1234567890", "plan", dict(SPEC))
    assert not created and again.id == job.id
    other, created = q.submit(
        "admin", "1234567890", "plan", SPEC
    )  # another client's key
    assert created and other.id != job.id
    with pytest.raises(JobError, match="another job"):
        q.submit("discord", "1234567890", "plan", {"target": "copper"})
    for bad in (
        ("Discord", "k", "plan", SPEC),
        ("discord", "", "plan", SPEC),
        ("discord", "a b", "plan", SPEC),
        ("discord", "k", "craft", SPEC),
        ("discord", "k", "plan", {"target": "x" * 5000}),
    ):
        with pytest.raises(JobError):
            q.submit(*bad)  # type: ignore[arg-type]


def test_a_job_runs_once_and_a_finished_job_is_never_claimed_again(
    store: Store,
) -> None:
    runs = Runs()
    journal: list[list[str]] = []
    q = JobQueue(store, runs, journal=journal.append)
    job, _ = q.submit("discord", "k1", "plan", SPEC)
    done = q.run_one()
    assert done is not None and done.id == job.id and done.state == DONE
    assert done.result == {"ids": ["pl-aaaa1111", "pl-aaaa2222"], "routes": []}
    assert done.attempts == 1 and done.started is not None and done.finished is not None
    assert q.run_one() is None and len(runs.runs) == 1
    q.recover()  # a restart doesn't touch a finished job
    assert q.run_one() is None and len(runs.runs) == 1
    # Submitting it again (the bot retrying) gets the finished job, not a new run.
    again, created = q.submit("discord", "k1", "plan", SPEC)
    assert not created and again.state == DONE
    assert q.run_one() is None and len(runs.runs) == 1
    assert journal == [
        [
            f"remote_{job.id.replace('-', '_')}: plan; done; via discord; steel ingot; "
            "plan:pl-aaaa1111, plan:pl-aaaa2222"
        ]
    ]


def test_a_failed_job_keeps_its_reason_and_is_not_retried(store: Store) -> None:
    runs = Runs()
    runs.fail = "no item called unobtainium"
    q = JobQueue(store, runs)
    q.submit("discord", "k1", "plan", {"target": "unobtainium", "batch": "1"})
    failed = q.run_one()
    assert failed is not None and failed.state == FAILED
    assert failed.error == "no item called unobtainium" and failed.result is None
    q.recover()
    assert q.run_one() is None and len(runs.runs) == 1


def test_jobs_survive_a_core_restart(db: Path) -> None:
    """Queued in one core, run by the next: the queue lives in gos.sqlite."""
    first = Store(db)
    JobQueue(first, Runs()).submit("discord", "k1", "plan", SPEC)
    first.close()  # the core stops before the worker got to it

    second = Store(db)
    try:
        runs = Runs()
        q = JobQueue(second, runs)
        q.recover()
        done = q.run_one()
        assert done is not None and done.state == DONE and len(runs.runs) == 1
        assert [j.id for j in q.jobs("discord", undelivered=True)] == [done.id]
    finally:
        second.close()


def test_a_job_a_dead_core_left_running_runs_again_once(db: Path) -> None:
    first = Store(db)
    q1 = JobQueue(first, Runs())
    job, _ = q1.submit("discord", "k1", "plan", SPEC)
    claimed = q1.claim()  # the worker took it ... and the core died mid-design
    assert claimed is not None and claimed.state == RUNNING
    first.close()

    second = Store(db)
    try:
        runs = Runs()
        q2 = JobQueue(second, runs)
        q2.recover()
        back = q2.get(job.id)
        assert back is not None and back.state == QUEUED and back.started is None
        done = q2.run_one()
        assert done is not None and done.state == DONE and done.attempts == 2
        assert len(runs.runs) == 1  # its one finished run
        q2.recover()
        assert q2.run_one() is None and len(runs.runs) == 1
    finally:
        second.close()


def test_a_job_that_keeps_killing_the_core_fails_instead_of_looping(db: Path) -> None:
    for _ in range(jobs.MAX_ATTEMPTS):
        s = Store(db)
        q = JobQueue(s, Runs())
        q.submit("discord", "k1", "plan", SPEC)  # the same job each time (idempotent)
        q.recover()
        assert q.claim() is not None  # ... and the core dies again
        s.close()
    s = Store(db)
    try:
        runs = Runs()
        q = JobQueue(s, runs)
        q.recover()
        (job,) = q.jobs("discord")
        assert job.state == FAILED and "interrupted" in (job.error or "")
        assert q.run_one() is None and runs.runs == []
    finally:
        s.close()


def test_two_workers_on_one_db_run_each_job_exactly_once(db: Path) -> None:
    stores = [Store(db), Store(db)]
    try:
        runs = Runs()
        queues = [JobQueue(s, runs) for s in stores]
        ids = {
            queues[0].submit("discord", f"k{i}", "plan", SPEC)[0].id for i in range(12)
        }

        def work(q: JobQueue) -> None:
            while q.run_one() is not None:
                pass

        threads = [threading.Thread(target=work, args=(q,)) for q in queues]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(runs.runs) == 12
        assert {j.id for j in queues[0].jobs("discord") if j.state == DONE} == ids
    finally:
        for s in stores:
            s.close()


def test_delivery_is_marked_once_and_only_for_the_owner(store: Store) -> None:
    clock = Clock()
    q = JobQueue(store, Runs(), clock=clock)
    job, _ = q.submit("discord", "k1", "plan", SPEC)
    assert q.delivered(job.id, "discord") is None  # not finished yet
    q.run_one()
    assert q.delivered(job.id, "admin") is None  # not admin's job
    assert q.get(job.id, "admin") is None
    first = q.delivered(job.id, "discord")
    assert first is not None and first.delivered == int(clock.now)
    clock.now += 100
    again = q.delivered(job.id, "discord")  # a repeat keeps the first time
    assert again is not None and again.delivered == first.delivered
    assert q.jobs("discord", undelivered=True) == []


def test_old_finished_jobs_are_dropped_at_start(store: Store) -> None:
    clock = Clock()
    q = JobQueue(store, Runs(), clock=clock)
    old, _ = q.submit("discord", "old", "plan", SPEC)
    q.run_one()
    clock.now += jobs.KEEP_DAYS * 86400 + 10
    queued, _ = q.submit("discord", "new", "power", {"mode": "best"})
    q.recover()
    assert q.get(old.id) is None and q.get(queued.id) is not None


def test_a_journal_failure_never_fails_the_job(store: Store) -> None:
    def broken(lines: list[str]) -> None:
        raise OSError("vault is read-only")

    q = JobQueue(store, Runs(), journal=broken)
    q.submit("discord", "k1", "plan", SPEC)
    done = q.run_one()
    assert done is not None and done.state == DONE


def test_a_result_too_large_to_keep_fails_the_job(store: Store) -> None:
    q = JobQueue(store, lambda kind, spec: {"blob": "x" * (jobs.RESULT_MAX + 1)})
    q.submit("discord", "k1", "plan", SPEC)
    done = q.run_one()
    assert (
        done is not None and done.state == FAILED and "too large" in (done.error or "")
    )


def test_journal_lines_are_one_defused_line() -> None:
    job = jobs.Job(
        id="j-0123456789",
        client="discord",
        key="k",
        kind="plan",
        spec={"target": "[[evil]]\nnew line"},
        state=FAILED,
        created=1,
        started=1,
        finished=2,
        attempts=1,
        result=None,
        error="bad\n[[link]]",
        delivered=None,
    )
    (line,) = jobs.journal_lines(job)
    assert "\n" not in line and "[[" not in line
    assert line.startswith("remote_j_0123456789: plan; failed; via discord;")


def test_the_worker_thread_runs_queued_jobs_and_stops(store: Store) -> None:
    runs = Runs()
    q = JobQueue(store, runs)
    q.start()
    try:
        job, _ = q.submit("discord", "k1", "plan", SPEC)
        deadline = time.monotonic() + 10
        while (got := q.get(job.id)) is None or got.state != DONE:
            assert time.monotonic() < deadline, "the worker didn't run the job"
            time.sleep(0.02)
    finally:
        q.stop()
    assert len(runs.runs) == 1
