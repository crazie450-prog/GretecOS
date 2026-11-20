"""The dashboard's design jobs: a design run in the background with its stage and a Cancel
(WO-3.9, plan 7.1, 7.14, D353, D354).

A job is one Designer design (a spec, a re-solve or a pin: :mod:`.designer`) or one power
design (:mod:`.powerview`), the same functions ``POST /api/designer/design``, ``POST
/api/power/design``, the agent tools, ``gos plan`` / ``gos power design`` and the Discord queue
(:mod:`gregtec.remote.jobs`) call. Starting one answers at once with the job; the design runs on
its own thread, and the job's ``stage`` follows the planner's checkpoints (:mod:`.progress`):
``waiting`` (another design holds the planner), ``graph``, ``solve`` k of n, ``metrics`` k of n
(``effort`` k of n inside it), ``ranking``, ``saving`` for a production design; ``candidates``,
``chains`` k of n, ``lines``, ``metrics`` k of n, ``ranking``, ``saving`` for a power design.
Every change goes to ``on_change`` (the core pushes it over the WebSocket), at most every
:data:`PUSH_S` seconds while it runs (a change held back is pushed once that time is up) and
always when it starts and ends.

- **One job per kind** (Designer, power) at a time: a second request gets :class:`Busy`
  naming the running job, never a queue. The two kinds share the planner's lock, so a power
  job started during a Designer job waits (``waiting``) and can be cancelled while it does.
- **Cancel** (D353) is checked at every checkpoint: between stages, between solver calls, between
  the items the graph builder expands and between the components of the effort walk. Every
  checkpoint comes before ``saving``, so a cancelled job saved nothing (no plan, no vault note,
  no ledger line: designs never touch the ledger, accepting does). Once ``saving`` has begun a
  cancel is refused (the design is about to be there) and the job finishes as ``done``.
- **The page closes** (D354): the job keeps running to its result. It is saved as every design
  is (the Designer's and the Power view's latest design), so the view shows it when it opens
  again, and ``GET /api/design-jobs/{id}`` gives its result while the core runs.
- **The core stops** with a job running: the job's record (``data/jobs/<kind>.json``, written
  when it starts and ends) still says ``running`` when the core starts again, and is then marked
  ``interrupted``: nothing was saved, start it again. Designs are not resumed; a design is
  deterministic, so starting it again gives the same plans.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from gregtec.planner import progress
from gregtec.vault.io import atomic_write

log = logging.getLogger("gregtec.planner.design_jobs")

KINDS = ("designer", "power")
RUNNING, DONE, FAILED, CANCELLED, INTERRUPTED = (
    "running",
    "done",
    "failed",
    "cancelled",
    "interrupted",
)
ENDED = (DONE, FAILED, CANCELLED, INTERRUPTED)
PUSH_S = 0.25  # at most one push per job this often while it runs
WAIT_S = 0.25  # how often a job waiting for the planner's lock checks for a cancel
RECORDS = Path("data", "jobs")  # in GOS_HOME
KEEP = 20  # finished jobs kept in memory for GET /api/design-jobs/{id}
ERROR_MAX = 500
SAVING = "saving"
# A power design's stages (the docstring); the solver's own "solve" inside "chains" isn't one.
POWER_STAGES = frozenset(
    {"candidates", "chains", "lines", "metrics", "ranking", SAVING}
)

Work = Callable[[], dict[str, Any]]


class Lock(Protocol):
    def acquire(self, blocking: bool = ..., timeout: float = ...) -> bool: ...
    def release(self) -> None: ...


class Busy(RuntimeError):
    """A job of this kind is running already."""

    def __init__(self, job: Job) -> None:
        super().__init__(
            f"a {job.kind} design is already running ({job.title}, {job.describe()}):"
            " wait for it or cancel it"
        )
        self.job = job


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = RUNNING
    stage: str = "waiting"
    k: int | None = None
    n: int | None = None
    sub: tuple[int, int] | None = None  # the effort walk's component k of n
    started: float = field(default_factory=time.time)
    finished: float | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    cancel_asked: bool = False

    def describe(self) -> str:
        """The stage as words: ``solve 2 of 3``, ``metrics 1 of 3 · effort 12 of 40``."""
        text = self.stage + (f" {self.k} of {self.n}" if self.k and self.n else "")
        if self.sub is not None:
            text += f" · effort {self.sub[0]} of {self.sub[1]}"
        return text

    def as_dict(self, *, result: bool = True) -> dict[str, Any]:
        end = self.finished if self.finished is not None else time.time()
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "stage": self.stage,
            "k": self.k,
            "n": self.n,
            "sub": list(self.sub) if self.sub else None,
            "detail": self.describe() if self.status == RUNNING else None,
            "started": self.started,
            "finished": self.finished,
            "elapsed_s": round(end - self.started, 1),
            "error": self.error,
            "cancel_asked": self.cancel_asked,
        }
        if result:
            out["result"] = self.result
        return out


class DesignJobs:
    """The core's design jobs (see the module docstring). ``lock`` is the planner's lock
    (:data:`gregtec.planner.designer._LOCK`, re-entrant) that every design takes."""

    def __init__(
        self,
        home: Path,
        lock: Lock,
        on_change: Callable[[Job], None] | None = None,
    ) -> None:
        self.home = home
        self.planner_lock = lock
        self.on_change = on_change
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._pushed: dict[str, float] = {}
        self._trailing: dict[str, threading.Timer] = {}  # a held-back change's push
        self._recover()

    # --- records ----------------------------------------------------------------------------

    def _record_path(self, kind: str) -> Path:
        return self.home / RECORDS / f"{kind}.json"

    def _write_record(self, job: Job) -> None:
        try:
            path = self._record_path(job.kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(job.as_dict(result=False), sort_keys=True)
            atomic_write(path, body.encode("utf-8"))
        except OSError as exc:  # the record only serves a restart's answer
            log.warning("job record not written", extra={"why": str(exc)[:200]})

    def _recover(self) -> None:
        """A record still ``running`` at start: the core stopped while it ran."""
        for kind in KINDS:
            try:
                data = json.loads(self._record_path(kind).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                continue
            job = Job(
                id=data["id"],
                kind=kind,
                title=str(data.get("title") or ""),
                status=str(data.get("status") or FAILED),
                stage=str(data.get("stage") or ""),
                started=float(data.get("started") or 0.0),
                finished=data.get("finished"),
                error=data.get("error"),
            )
            if job.status == RUNNING:
                job.status = INTERRUPTED
                job.finished = time.time()
                job.error = (
                    "the core stopped while this design ran: nothing was saved; start it"
                    " again"
                )
                self._write_record(job)
            self._jobs[job.id] = job

    # --- the jobs ---------------------------------------------------------------------------

    def start(self, kind: str, title: str, work: Work) -> Job:
        """Start ``work`` (it returns the answer the view shows) as a job of ``kind``."""
        if kind not in KINDS:
            raise ValueError(f"kind is one of {', '.join(KINDS)}")
        with self._lock:
            for j in self._jobs.values():
                if j.kind == kind and j.status == RUNNING:
                    raise Busy(j)
            job = Job(f"job-{secrets.token_hex(6)}", kind, title[:200])
            self._jobs[job.id] = job
            ended = [j for j in self._jobs.values() if j.status in ENDED]
            for old in sorted(ended, key=lambda j: j.started)[:-KEEP]:
                del self._jobs[old.id]
            thread = threading.Thread(
                target=self._run, args=(job, work), name=f"gos-{job.id}", daemon=True
            )
            self._threads[job.id] = thread
        self._write_record(job)
        self._push(job, force=True)
        thread.start()
        return job

    def _report(self, job: Job) -> progress.Reporter:
        def report(name: str | None, k: int | None, n: int | None) -> None:
            with self._lock:
                if job.cancel_asked:
                    raise progress.Cancelled()
                if name is None:
                    return
                if (
                    job.kind == "power"
                    and name not in POWER_STAGES
                    and name != "effort"
                ):
                    return  # the per-chain solves inside "chains": a cancel point, not a stage
                if name == "effort":
                    job.sub = (k, n) if k and n else None
                else:
                    job.stage, job.k, job.n, job.sub = name, k, n, None
            self._push(job)

        return report

    def _run(self, job: Job, work: Work) -> None:
        report = self._report(job)
        try:
            with progress.reporting(report):
                # Wait for the planner's lock here, where a cancel is still heard.
                while not self.planner_lock.acquire(timeout=WAIT_S):
                    report(None, None, None)
                try:
                    result = work()
                finally:
                    self.planner_lock.release()
            self._end(job, DONE, result=result)
        except progress.Cancelled:
            self._end(job, CANCELLED)
        except Exception as exc:  # noqa: BLE001 - the job's answer, never the core's crash
            self._end(job, FAILED, error=_message(exc))

    def _end(
        self,
        job: Job,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            job.status = status
            job.finished = time.time()
            job.result = result
            job.error = error[:ERROR_MAX] if error else None
            job.sub = None
        self._write_record(job)
        self._push(job, force=True)
        with self._lock:
            self._threads.pop(job.id, None)  # wait() returns once the end was pushed

    def _push(self, job: Job, *, force: bool = False) -> None:
        if self.on_change is None:
            return
        now = time.monotonic()
        with self._lock:
            wait = PUSH_S - (now - self._pushed.get(job.id, 0.0))
            if not force and wait > 0:
                # Held back: push it once the throttle is up, so the last stage is never lost
                # (the right rail follows only pushes).
                if job.id not in self._trailing:
                    timer = threading.Timer(wait, self._trail, (job,))
                    timer.daemon = True
                    self._trailing[job.id] = timer
                    timer.start()
                return
            self._pushed[job.id] = now
            held = self._trailing.pop(job.id, None)
        if held is not None:
            held.cancel()
        try:
            self.on_change(job)
        except Exception:  # a push that fails must not stop the design
            log.exception("job push failed")

    def _trail(self, job: Job) -> None:
        with self._lock:
            if self._trailing.pop(job.id, None) is None:
                return  # a push went out in the meantime
            ended = job.status in ENDED
        if not ended:  # the end has its own push
            self._push(job, force=True)

    def cancel(self, job_id: str) -> Job:
        """Ask a running job to stop at its next checkpoint. A job already saving, or ended,
        is returned as it is (``cancel_asked`` false)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status == RUNNING and job.stage != SAVING:
                job.cancel_asked = True
        self._push(job, force=True)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def running(self) -> list[Job]:
        with self._lock:
            return sorted(
                (j for j in self._jobs.values() if j.status == RUNNING),
                key=lambda j: j.started,
            )

    def latest(self, kind: str) -> Job | None:
        """The newest job of ``kind``, running or ended."""
        with self._lock:
            found = [j for j in self._jobs.values() if j.kind == kind]
        return max(found, key=lambda j: j.started) if found else None

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        """Block until the job ends (tests, the CLI)."""
        with self._lock:
            thread = self._threads.get(job_id)
            job = self._jobs[job_id]
        if thread is not None:
            thread.join(timeout)
        return job


def _message(exc: Exception) -> str:
    """A failed job's error as the view shows it: the planner's own refusal, else its kind."""
    from gregtec.planner.designer import DesignerError  # designer imports the planner

    if isinstance(exc, DesignerError):
        return str(exc)
    detail = getattr(exc, "detail", None)  # an HTTPException from the routes' helpers
    if isinstance(detail, str):
        return detail
    log.error("design job failed", exc_info=exc)
    return f"the design failed ({type(exc).__name__}): see the core's log"
