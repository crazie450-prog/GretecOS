"""The durable job queue for long design jobs from off the PC (WO-9.1, plan 7.12, D295).

A **job** is one ``/plan`` (a production design, :func:`gregtec.api.plan_routes.designed`) or
``/power`` (a power design, :func:`gregtec.api.power_routes.designed`) asked from Discord. It
lives in the core's DB (``remote_job`` in ``gos.sqlite``), so it survives a restart of the bot
or of the core, and the core's one worker thread runs the queue in order.

**Never run twice.**

- *Submitting* is idempotent: a job is keyed by its client and the client's own key (the Discord
  interaction's id), so a bot that sends the same command again after a restart, or retries a
  request whose answer it lost, gets the job it already made (``created`` false).
- *Running*: the worker claims the oldest queued job by moving it to ``running`` in one write
  (``UPDATE ... WHERE state = 'queued'``) and records ``done`` or ``failed`` only on a running
  job. A job that is ``done`` or ``failed`` is never claimed again.
- *A core that dies mid-job* leaves it ``running`` with no result. When the core starts again
  (:meth:`JobQueue.recover`, before the worker starts) such a job goes back to ``queued`` once:
  its run never finished, so nothing of it was reported, and a design re-solved from the same
  request on the same DB saves the same plan ids (D250), so the re-run is the job's one
  finished run. A job interrupted :data:`MAX_ATTEMPTS` times fails ("interrupted") instead of
  looping on a crash.
- *Delivery* is the client's: it lists its finished, undelivered jobs, posts each, then marks
  it delivered. A bot that dies between posting and marking posts that one result again after
  its restart (at least once; the job itself is not run again).

Results hold the planner's own numbers and ids (plan 0.1 principles 1 and 2), never a model's.
Every finished job is also written to the day's journal (``log/YYYY-MM-DD.md``) through
:class:`gregtec.vault.io.VaultIO` (the ``journal`` callback).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from gregtec.store import Store

log = logging.getLogger("gregtec.remote")

KINDS = ("plan", "power")
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
STATES = (QUEUED, RUNNING, DONE, FAILED)
MAX_ATTEMPTS = 2  # a job interrupted this often by a core restart fails
KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")
CLIENT = re.compile(r"[a-z0-9_-]{1,32}")
KEEP_DAYS = 30  # finished jobs older than this are dropped when the core starts
ERROR_MAX = 500
RESULT_MAX = 64 * 1024

Runner = Callable[[str, Mapping[str, Any]], dict[str, Any]]


class JobError(ValueError):
    """A job can't be submitted as asked; the message says why."""


@dataclass(frozen=True)
class Job:
    id: str
    client: str
    key: str
    kind: str
    spec: dict[str, Any]
    state: str
    created: int
    started: int | None
    finished: int | None
    attempts: int
    result: dict[str, Any] | None
    error: str | None
    delivered: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "spec": self.spec,
            "state": self.state,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "attempts": self.attempts,
            "result": self.result,
            "error": self.error,
            "delivered": self.delivered,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        return cls(
            id=row["id"],
            client=row["client"],
            key=row["key"],
            kind=row["kind"],
            spec=json.loads(row["spec"]),
            state=row["state"],
            created=row["created"],
            started=row["started"],
            finished=row["finished"],
            attempts=row["attempts"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            delivered=row["delivered"],
        )


class JobQueue:
    """The queue over the core's ``store``. ``run(kind, spec)`` does one job (in the worker
    thread) and returns its result, or raises with the reason it failed. ``journal`` gets the
    journal lines of each finished job; ``on_change`` each job whose state changed."""

    def __init__(
        self,
        store: Store,
        run: Runner,
        *,
        journal: Callable[[list[str]], None] | None = None,
        on_change: Callable[[Job], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.run = run
        self.journal = journal
        self.on_change = on_change
        self.clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- submitting and reading

    def submit(
        self, client: str, key: str, kind: str, spec: Mapping[str, Any]
    ) -> tuple[Job, bool]:
        """The job for ``(client, key)``: made now (True) or the one already made (False)."""
        if not CLIENT.fullmatch(client):
            raise JobError("not a client name")
        if not KEY.fullmatch(key):
            raise JobError("key is 1 to 64 letters, digits, _ or -")
        if kind not in KINDS:
            raise JobError(f"kind is one of {', '.join(KINDS)}")
        if not isinstance(spec, Mapping):
            raise JobError("spec is an object")
        text = json.dumps(dict(spec), sort_keys=True)
        if len(text) > 4096:
            raise JobError("spec is too long")
        now = int(self.clock())
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM remote_job WHERE client = ? AND key = ?", (client, key)
            ).fetchone()
            if row is not None:
                found = Job.from_row(row)
                if found.kind != kind or found.spec != json.loads(text):
                    raise JobError("that key already names another job")
                return found, False
            job_id = f"j-{secrets.token_hex(5)}"
            conn.execute(
                "INSERT INTO remote_job(id, client, key, kind, spec, state, created, attempts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (job_id, client, key, kind, text, QUEUED, now),
            )
            made = Job.from_row(
                conn.execute("SELECT * FROM remote_job WHERE id = ?", (job_id,)).fetchone()
            )
        log.info("job queued", extra={"job": job_id, "kind": kind, "client": client})
        self._changed(made)
        self._wake.set()
        return made, True

    def get(self, job_id: str, client: str | None = None) -> Job | None:
        """One job; with ``client``, only that client's."""
        rows = self.store.rows("SELECT * FROM remote_job WHERE id = ?", (job_id,))
        if not rows:
            return None
        job = Job.from_row(rows[0])
        return job if client is None or job.client == client else None

    def jobs(self, client: str, *, undelivered: bool = False) -> list[Job]:
        """``client``'s jobs, oldest first; ``undelivered``: the finished ones not delivered."""
        sql = "SELECT * FROM remote_job WHERE client = ?"
        if undelivered:
            sql += " AND state IN ('done', 'failed') AND delivered IS NULL"
        sql += " ORDER BY created, rowid LIMIT 100"
        return [Job.from_row(r) for r in self.store.rows(sql, (client,))]

    def delivered(self, job_id: str, client: str) -> Job | None:
        """Mark a finished job of ``client``'s delivered (again is fine); None if it isn't one."""
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE remote_job SET delivered = COALESCE(delivered, ?) WHERE id = ? AND"
                " client = ? AND state IN ('done', 'failed')",
                (int(self.clock()), job_id, client),
            )
            row = conn.execute(
                "SELECT * FROM remote_job WHERE id = ? AND client = ?", (job_id, client)
            ).fetchone()
        if row is None:
            return None
        job = Job.from_row(row)
        return job if job.delivered is not None else None

    # --- running

    def recover(self) -> None:
        """At start, before the worker: jobs a dead core left running go back to the queue
        (or fail after :data:`MAX_ATTEMPTS`), and old finished jobs are dropped."""
        now = int(self.clock())
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE remote_job SET state = 'failed', finished = ?, error = ? "
                "WHERE state = 'running' AND attempts >= ?",
                (now, "interrupted by core restarts; ask again", MAX_ATTEMPTS),
            )
            back = conn.execute(
                "UPDATE remote_job SET state = 'queued', started = NULL "
                "WHERE state = 'running'"
            ).rowcount
            conn.execute(
                "DELETE FROM remote_job WHERE state IN ('done', 'failed') AND finished < ?",
                (now - KEEP_DAYS * 86400,),
            )
        if back:
            log.info("interrupted jobs queued again", extra={"jobs": back})

    def claim(self) -> Job | None:
        """Move the oldest queued job to running and return it (None: nothing queued)."""
        now = int(self.clock())
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM remote_job WHERE state = 'queued' ORDER BY created, rowid"
                " LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            n = conn.execute(
                "UPDATE remote_job SET state = 'running', started = ?, attempts = attempts + 1"
                " WHERE id = ? AND state = 'queued'",
                (now, row["id"]),
            ).rowcount
            if n != 1:
                return None
            job = Job.from_row(
                conn.execute(
                    "SELECT * FROM remote_job WHERE id = ?", (row["id"],)
                ).fetchone()
            )
        self._changed(job)
        return job

    def run_one(self) -> Job | None:
        """Claim and run the next job; the finished job, or None if nothing was queued."""
        job = self.claim()
        if job is None:
            return None
        result: dict[str, Any] | None = None
        error: str | None = None
        try:
            result = self.run(job.kind, job.spec)
            if len(json.dumps(result)) > RESULT_MAX:
                result, error = None, "the result is too large to keep"
        except Exception as exc:  # noqa: BLE001 - every failure is the job's, not the worker's
            error = (str(exc) or type(exc).__name__)[:ERROR_MAX]
            log.info("job failed", extra={"job": job.id, "why": error[:200]})
        return self._finish(job, result, error)

    def _finish(
        self, job: Job, result: dict[str, Any] | None, error: str | None
    ) -> Job | None:
        state = FAILED if error is not None else DONE
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE remote_job SET state = ?, finished = ?, result = ?, error = ? "
                "WHERE id = ? AND state = 'running'",
                (
                    state,
                    int(self.clock()),
                    json.dumps(result) if result is not None else None,
                    error,
                    job.id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM remote_job WHERE id = ?", (job.id,)
            ).fetchone()
        done = Job.from_row(row) if row is not None else None
        if done is not None:
            log.info("job finished", extra={"job": job.id, "state": done.state})
            self._changed(done)
            if self.journal is not None:
                try:
                    self.journal(journal_lines(done))
                except Exception:  # the journal is a record, never the job's failure
                    log.exception("job not journalled", extra={"job": job.id})
        return done

    def start(self) -> None:
        self.recover()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="gos-remote-jobs", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.run_one()
            except sqlite3.Error:
                log.exception("job queue: DB error")
                job = None
            if job is None:
                self._wake.wait(timeout=5.0)
                self._wake.clear()

    def _changed(self, job: Job) -> None:
        if self.on_change is not None:
            try:
                self.on_change(job)
            except Exception:
                log.exception("job on_change failed")


def journal_lines(job: Job) -> list[str]:
    """The journal line of a finished job (plan 6.2 grammar): its kind, state and plan ids."""
    parts = [job.kind, job.state, f"via {job.client}"]
    target = job.spec.get("target") or job.spec.get("mode")
    if isinstance(target, str):
        parts.append(" ".join(target.split())[:80])
    res = job.result or {}
    ids = [i for i in res.get("ids") or () if isinstance(i, str)]
    if ids:
        parts.append(", ".join(f"plan:{i}" for i in ids[:3]))
    if res.get("clarify"):
        parts.append("asked which item")
    if job.error:
        parts.append(f"why {' '.join(job.error.split())[:120]}")
    line = f"remote_{job.id.replace('-', '_')}: " + "; ".join(parts)
    return [re.sub(r"\[(?=\[)", "[ ", line)[:300]]
