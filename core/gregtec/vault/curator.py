"""The curator's jobs: idle maintenance of the vault and the DB (plan 6.6) [QA:A9].

:func:`run` runs them in this order; each reports ``ok``, ``skipped`` or ``failed`` and a failure
never stops the others:

1. ``compact``: daily logs to weekly ones once the week's Sunday is 7 days old, weekly logs to
   monthly ones once the week's Sunday is 8 weeks old (a week belongs to the month of its
   Thursday, as ISO weeks do). One Haiku call per period (:mod:`gregtec.llm.summarise`); the
   summary is written (``log/wk/YYYY-Www.md``, ``log/mo/YYYY-MM.md``, an existing one included in
   the input) before its sources are deleted, and git keeps them. The period's counts (events
   by kind, :func:`gregtec.llm.summarise.event_counts`) are computed here from the logs, written
   into the summary as ``n_<kind>: N`` lines and given to the summariser, which may only use
   those numbers (D142 (f), D143). Without an Anthropic key, or once the cycle's spend reaches
   the first credit alert (``[budget] credit_alerts_pct``, 80%: D142 (e), D143; checked before
   each period), the job is skipped and the logs wait. A period whose summary was refused
   :data:`MAX_REFUSALS` times is held (``kv``) and listed in ``inbox/ib-compact-held.md`` for
   Clayton; ``gos vault maintain`` (``retry_held``) tries again.
2. ``quests``: rewrite the questline summaries (``quests/qs-<line>.md``) whose content the last
   quest load changed (:mod:`gregtec.quests.summaries`, WO-6.4); nothing when no quests are
   loaded.
3. ``schema``: check every note against ``_gos/schema.md``; the problems go to one standing note,
   ``inbox/ib-schema-check.md``, rewritten when they change and removed when there are none.
4. ``index``: rebuild ``INDEX.md`` (:mod:`gregtec.vault.index`), after the inbox notes the first
   two jobs write.
5. ``fts``: mirror the notes into ``note_fts`` (:mod:`gregtec.vault.fts`).
6. ``backup``: ``VACUUM INTO data/backups/gos-YYYYMMDD.sqlite`` (through a temporary name, so a
   half-written backup never has a backup's name; a ``.tmp`` left by a killed run is removed),
   keeping the newest ``keep_backups``. The scheduler also runs it at session end once the
   daily run has been missed for 2 days (D142 (g), D143), with a deadline when the core is
   stopping (an interrupted backup leaves no file).
7. ``git``: commit the agents' and the curator's changes (:func:`commit_agent_writes`).

Git commits cover every path except ``me/`` (Clayton's) and ``_gos/`` (``gos vault deploy``
commits it) and the temporary files of a write that was killed; hooks and signing are off, as
in deploy. :meth:`Curator.run` checks ``stop`` between jobs (and compaction between periods), so
the core's stop doesn't wait for, or kill, a whole run.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import keyring.errors

from gregtec.llm.summarise import (
    Summariser,
    SummariserOff,
    SummaryError,
    count_lines,
    event_counts,
)
from gregtec.llm.usage import UsageMeter
from gregtec.vault import fts, index, io, schema
from gregtec.vault.deploy import _cmd

log = logging.getLogger(__name__)

JOBS = ("compact", "quests", "schema", "index", "fts", "backup", "git")
OK, SKIPPED, FAILED = "ok", "skipped", "failed"
KEEP_BACKUPS = 7
BACKUP = re.compile(r"gos-(\d{8})\.sqlite")
BACKUP_TMP = re.compile(r"gos-(\d{8})\.sqlite\.tmp")
# A backup with a deadline that ends in one of these was cut short, not failed.
_CUT_SHORT = {
    sqlite3.SQLITE_INTERRUPT: "out of time",
    sqlite3.SQLITE_BUSY: "the DB stayed locked",
    sqlite3.SQLITE_LOCKED: "the DB stayed locked",
}
SCHEMA_REPORT = "inbox/ib-schema-check.md"
REPORT_ROWS = 120
DAILY_AGE = dt.timedelta(days=7)
WEEKLY_AGE = dt.timedelta(weeks=8)
WEEKLY = re.compile(r"log/wk/(\d{4})-W(\d{2})\.md")
# io.atomic_write's temporary files: a write killed half-way leaves one, never committed.
GIT_PATHS = (".", ":(exclude)me", ":(exclude)_gos", ":(exclude,glob)**/.*.gos-tmp")
LANE = "maintenance"  # llm_usage.lane of the compaction calls
MAX_REFUSALS = 3  # refused summaries of one period before it waits for Clayton
# kv key + period -> {"n": refused summaries, "why": the last reason}
KV_REFUSED = "maintenance.compact_refused."
HELD_NOTE = "inbox/ib-compact-held.md"
# D142 (e): compaction stops at the cycle's first credit alert; 80% when gos.toml lists none.
DEFAULT_STOP_PCT = 80


@dataclass(frozen=True)
class JobResult:
    job: str
    status: str
    detail: str

    def line(self) -> str:
        return f"{self.job}\t{self.status}\t{self.detail}"


# --- git -----------------------------------------------------------------------------------


def commit_agent_writes(vault: Path, message: str) -> str | None:
    """Commit every change outside ``me/`` and ``_gos/``. Returns the new commit's short hash,
    or None when there was nothing to commit. Raises RuntimeError when git can't."""
    if not (vault / ".git").is_dir():
        raise RuntimeError(f"{vault} isn't a git repo: run gos vault deploy")
    if shutil.which("git") is None:
        raise RuntimeError("git isn't on PATH")

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(
            _cmd(vault, *args),
            cwd=vault,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and done.returncode:
            raise RuntimeError(f"git {args[0]} failed: {done.stderr.strip()[:300]}")
        return done

    git("add", "-A", "--", *GIT_PATHS)
    if (
        git("diff", "--cached", "--quiet", "--", *GIT_PATHS, check=False).returncode
        == 0
    ):
        return None
    git("commit", "-q", "-m", message, "--", *GIT_PATHS)
    return git("rev-parse", "--short", "HEAD").stdout.strip()


# --- compaction ----------------------------------------------------------------------------


def _week_days(year: int, week: int) -> tuple[dt.date, dt.date]:
    """(Thursday, Sunday) of an ISO week."""
    return dt.date.fromisocalendar(year, week, 4), dt.date.fromisocalendar(
        year, week, 7
    )


def _daily_groups(root: Path, today: dt.date) -> dict[str, list[str]]:
    """Weeks (``YYYY-Www``) whose Sunday is at least 7 days old -> their daily logs."""
    groups: dict[str, list[str]] = defaultdict(list)
    for rel in io.notes(root):
        day = io.DAILY.fullmatch(rel)
        if day is None:
            continue
        try:
            date = dt.date.fromisoformat(day[1])
        except ValueError:
            continue
        year, week, _ = date.isocalendar()
        if today - _week_days(year, week)[1] >= DAILY_AGE:
            groups[f"{year}-W{week:02d}"].append(rel)
    return groups


def _weekly_groups(root: Path, today: dt.date) -> dict[str, list[str]]:
    """Months (``YYYY-MM``) -> their weekly logs whose Sunday is at least 8 weeks old."""
    groups: dict[str, list[str]] = defaultdict(list)
    for rel in io.notes(root):
        m = WEEKLY.fullmatch(rel)
        if m is None:
            continue
        try:
            thursday, sunday = _week_days(int(m[1]), int(m[2]))
        except ValueError:
            continue
        if today - sunday >= WEEKLY_AGE:
            groups[f"{thursday:%Y-%m}"].append(rel)
    return groups


def _summary_note(
    root: Path,
    rel: str,
    period: str,
    sources: Sequence[str],
    summariser: Summariser,
    meter: UsageMeter | None,
    today: dt.date,
) -> None:
    target = root / rel
    inputs = list(sources)
    if target.is_file():
        inputs.insert(
            0, rel
        )  # an earlier summary of the same period is merged, not lost
    texts = [(p, (root / p).read_text(encoding="utf-8")) for p in inputs]
    counts = event_counts(texts)  # D142 (f): counted here, never by the model
    try:
        summary = summariser.summarise(period, texts, counts)
    except SummaryError as exc:
        if meter is not None and exc.usage is not None and exc.model is not None:
            meter.record(LANE, exc.model, exc.usage, task=f"log compaction {period}")
        raise
    if meter is not None:
        meter.record(
            LANE, summary.model, summary.usage, task=f"log compaction {period}"
        )
    types = schema.load_types(root / io.SCHEMA_NOTE)
    kind = schema.type_for(rel, types)
    if kind is None:
        raise SummaryError(f"the schema has no type at {rel}")
    earlier: list[str] = []
    if inputs and inputs[0] == rel:  # keep the earlier summary's sources in from:
        for line in texts[0][1].splitlines():
            if line.startswith("from: "):
                earlier = line.removeprefix("from: ").split(", ")
                break
    # dict.fromkeys: a run killed between the write and the deletes lists its sources once
    named = dict.fromkeys([*earlier, *(s.removeprefix("log/") for s in sources)])
    sources_line = ", ".join(named)
    body = [
        f"period: {period}",
        f"from: {sources_line}",
        *count_lines(counts),
        *summary.lines,
    ]
    text = io.frontmatter(schema.id_for(rel, kind), kind.t, today)
    text += "".join(line + "\n" for line in body)
    problems = schema.check_text(rel, text, types)
    if problems:
        raise SummaryError("; ".join(problems))
    io.atomic_write(target, text.encode("utf-8"))
    for src in sources:
        (root / src).unlink()


Refusals = dict[str, tuple[int, str]]  # period -> (refused summaries, the last reason)


def _refusals(meter: UsageMeter | None) -> Refusals:
    """Every period whose summary was refused, as kept in ``kv``."""
    if meter is None:
        return {}
    found = {}
    for row in meter.store.rows(
        "SELECT key, value FROM kv WHERE key LIKE ?", (KV_REFUSED + "%",)
    ):
        try:
            value = json.loads(row["value"])
            found[row["key"].removeprefix(KV_REFUSED)] = (
                int(value["n"]),
                str(value["why"]),
            )
        except (ValueError, TypeError, KeyError):
            continue
    return found


def _set_refusal(meter: UsageMeter | None, period: str, n: int, why: str) -> None:
    if meter is None:
        return
    with meter.store.transaction() as conn:
        if n == 0:
            conn.execute("DELETE FROM kv WHERE key = ?", (KV_REFUSED + period,))
            return
        conn.execute(
            "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO "
            "UPDATE SET value = excluded.value, upd = excluded.upd",
            (
                KV_REFUSED + period,
                json.dumps({"n": n, "why": why[:300]}),
                int(time.time()),
            ),
        )


def _held_note(root: Path, today: dt.date, held: Refusals) -> None:
    """``inbox/ib-compact-held.md``: the held periods, or no note when there are none."""
    target = root / HELD_NOTE
    if not held:
        target.unlink(missing_ok=True)
        return
    body = [
        "title: log summaries the curator gave up on",
        f"count: {len(held)}",
        (
            f"note: each period's summary was refused {MAX_REFUSALS} times, so its logs stay "
            "as they are and no more calls are made for it"
        ),
        "note: gos vault maintain --job compact tries these periods again",
        "",
        "```tsv",
        "period\trefused\tlast_reason",
    ]
    body += [
        f"{period}\t{n}\t{_clean_cell(why)[:200]}"
        for period, (n, why) in sorted(held.items())
    ]
    body.append("```")
    text = io.frontmatter("ib-compact-held", "inbox", today)
    text += "".join(line + "\n" for line in body)
    try:
        old = target.read_text(encoding="utf-8")
        if old.split("\n---\n", 1)[-1] == text.split("\n---\n", 1)[-1]:
            return
    except OSError:
        pass
    io.atomic_write(target, text.encode("utf-8"))


def stop_pct(meter: UsageMeter) -> int:
    """The cycle's share of the included credits at which compaction stops: the first of
    ``[budget] credit_alerts_pct`` (D142 (e))."""
    return min(meter.alerts_pct) if meter.alerts_pct else DEFAULT_STOP_PCT


def _over_alert(meter: UsageMeter | None) -> str | None:
    """Why compaction must stop for the credits, or None."""
    if meter is None:
        return None
    pct, limit = meter.cycle()["pct"], stop_pct(meter)
    if pct < limit:
        return None
    return f"the cycle's spend reached the {limit}% credit alert ({pct}%, D142): the logs wait"


def compact(
    root: Path,
    today: dt.date,
    summariser: Callable[[], Summariser | None],
    meter: UsageMeter | None = None,
    *,
    retry_held: bool = False,
    stop: Callable[[], bool] = lambda: False,
) -> JobResult:
    """Compact the logs old enough (see the module docstring). ``summariser`` is only called
    (the keyring read) once a period is due; ``meter`` meters the calls, keeps the refusal
    counts in ``kv`` and gives the credit cycle; without one, nothing is held or checked."""
    days, weeks = _daily_groups(root, today), _weekly_groups(root, today)
    refused = _refusals(meter)
    due = set(days) | set(weeks)
    # A period no longer due: its logs are gone (compacted, or deleted by hand).
    for period in set(refused) - due:
        _set_refusal(meter, period, 0, "")
        del refused[period]
    held = (
        {} if retry_held else {p: r for p, r in refused.items() if r[0] >= MAX_REFUSALS}
    )
    _held_note(root, today, held)
    if not due:
        return JobResult("compact", OK, "nothing old enough")
    if not due - set(held):
        return JobResult(
            "compact",
            SKIPPED,
            f"{', '.join(sorted(held))} held after {MAX_REFUSALS} refused summaries "
            f"({HELD_NOTE})",
        )
    if (why := _over_alert(meter)) is not None:
        return JobResult("compact", SKIPPED, why)
    try:
        summ = summariser()
    except SummariserOff as exc:
        return JobResult("compact", SKIPPED, f"{exc}: the logs wait")
    except keyring.errors.KeyringError as exc:
        return JobResult(
            "compact",
            SKIPPED,
            f"the Anthropic key can't be read ({exc}): the logs wait",
        )
    if summ is None:
        return JobResult(
            "compact",
            SKIPPED,
            "no Anthropic key stored: the logs wait (gos key set anthropic)",
        )
    done: list[str] = []
    failed: list[str] = []
    stopped = False
    capped: str | None = None

    def one(period: str, rel: str, sources: list[str]) -> None:
        nonlocal stopped, capped
        if period in held or stopped or capped is not None:
            return
        if stop():
            stopped = True
            return
        # Each call spends: the alert is checked again before the next one.
        if done or failed:
            capped = _over_alert(meter)
            if capped is not None:
                return
        try:
            _summary_note(root, rel, period, sources, summ, meter, today)
            done.append(period)
            if period in refused:
                _set_refusal(meter, period, 0, "")
        except SummaryError as exc:
            n = refused.get(period, (0, ""))[0] + 1
            refused[period] = (n, str(exc))
            _set_refusal(meter, period, n, str(exc))
            if n >= MAX_REFUSALS:
                held[period] = refused[period]
            failed.append(f"{period}: {exc}")
        except SummariserOff as exc:
            capped = str(exc)
        except (OSError, ValueError) as exc:
            failed.append(f"{period}: {exc}")

    for period, sources in sorted(days.items()):
        one(period, f"log/wk/{period}.md", sources)
    # Weeks made just now are only days old: they never qualify for a month in the same run.
    for period, sources in sorted(_weekly_groups(root, today).items()):
        one(period, f"log/mo/{period}.md", sources)
    _held_note(root, today, held)
    detail = f"compacted {', '.join(done) or 'nothing'}"
    if held:
        detail += f"; held {', '.join(sorted(held))} ({HELD_NOTE})"
    if stopped:
        detail += "; stopped before the rest"
    if capped is not None:
        detail += f"; stopped before the rest: {capped}"
    if failed:
        return JobResult("compact", FAILED, detail + "; failed " + "; ".join(failed))
    return JobResult("compact", OK, detail)


# --- schema check --------------------------------------------------------------------------


def schema_problems(root: Path) -> list[tuple[str, str]]:
    """(path, problem) for every note that doesn't follow ``_gos/schema.md``, plus any id two
    notes share."""
    types = schema.load_types(root / io.SCHEMA_NOTE)
    found: list[tuple[str, str]] = []
    ids: dict[str, list[str]] = defaultdict(list)
    for rel in io.notes(root):
        try:
            text = (root / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError) as exc:
            found.append((rel, f"can't be read: {exc}"))
            continue
        found += [(rel, p) for p in schema.check_text(rel, text, types)]
        try:
            ids[schema.parse(text).meta["id"]].append(rel)
        except ValueError:
            pass
    for note_id, paths in sorted(ids.items()):
        if len(paths) > 1:
            found += [(p, f"id {note_id} is also used by another note") for p in paths]
    return found


def _clean_cell(text: str) -> str:
    return " ".join(text.replace("\t", " ").split())


def check_schema(root: Path, today: dt.date) -> JobResult:
    problems = [(p, why) for p, why in schema_problems(root) if p != SCHEMA_REPORT]
    target = root / SCHEMA_REPORT
    if not problems:
        if target.exists():
            target.unlink()
            return JobResult(
                "schema", OK, "every note follows the schema (report removed)"
            )
        return JobResult("schema", OK, "every note follows the schema")
    shown = problems[:REPORT_ROWS]
    body = [
        "title: notes that don't follow _gos/schema.md",
        f"count: {len(problems)}",
        "note: written by the curator's daily check; fix the notes, and this note goes away",
    ]
    if len(problems) > len(shown):
        body.append(f"note: the first {len(shown)} problems are shown")
    body += ["", "```tsv", "path\tproblem"]
    body += [f"{_clean_cell(p)}\t{_clean_cell(why)}" for p, why in shown]
    body.append("```")
    text_body = "".join(line + "\n" for line in body)
    try:
        old = target.read_text(encoding="utf-8")
        if old.split("\n---\n", 1)[-1] == text_body:
            return JobResult("schema", OK, f"{len(problems)} problems, as before")
    except OSError:
        pass
    text = io.frontmatter("ib-schema-check", "inbox", today) + text_body
    io.atomic_write(target, text.encode("utf-8"))
    return JobResult(
        "schema", OK, f"{len(problems)} problems, reported in {SCHEMA_REPORT}"
    )


# --- backup --------------------------------------------------------------------------------


def backup(
    db: Path,
    folder: Path,
    today: dt.date,
    keep: int = KEEP_BACKUPS,
    deadline: float | None = None,
) -> JobResult:
    """``deadline`` (a ``time.monotonic()`` value): past it, ``VACUUM INTO`` is interrupted (and
    a wait on a locked DB ends at it), its ``.tmp`` removed and the job ``skipped``, so a
    stopping core ends the backup itself before the supervisor's kill."""
    if not db.is_file():
        return JobResult("backup", SKIPPED, "no DB yet")
    folder.mkdir(parents=True, exist_ok=True)
    for stale in [p for p in folder.iterdir() if BACKUP_TMP.fullmatch(p.name)]:
        stale.unlink(
            missing_ok=True
        )  # a run killed in VACUUM INTO, maybe on another day
    target = folder / f"gos-{today:%Y%m%d}.sqlite"
    if target.exists():
        detail = f"{target.name} already exists"
    else:
        tmp = folder / f"{target.name}.tmp"
        busy_s = 30.0  # the progress handler doesn't run while SQLite waits on a lock
        if deadline is not None:
            busy_s = max(0.0, min(busy_s, deadline - time.monotonic()))
        conn = sqlite3.connect(db, timeout=busy_s)
        cut_short: str | None = None
        try:
            if deadline is not None:
                conn.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
            conn.execute("VACUUM INTO ?", (str(tmp),))
        except sqlite3.OperationalError as exc:
            code = exc.sqlite_errorcode & 0xFF  # the primary code of an extended one
            if deadline is None or code not in _CUT_SHORT:
                raise
            cut_short = _CUT_SHORT[code]
        finally:
            conn.close()
        if cut_short is not None:
            # removed once closed: Windows won't delete an open file
            tmp.unlink(missing_ok=True)
            return JobResult("backup", SKIPPED, f"cut short: {cut_short}")
        os.replace(tmp, target)
        detail = f"wrote {target.name}"
    kept = sorted(
        (p for p in folder.iterdir() if BACKUP.fullmatch(p.name)), reverse=True
    )
    for old in kept[keep:]:
        old.unlink()
    pruned = len(kept[keep:])
    return JobResult(
        "backup", OK, detail + (f"; removed {pruned} older" if pruned else "")
    )


# --- the run -------------------------------------------------------------------------------


@dataclass
class Curator:
    vault: Path
    db: Path
    backups: Path
    summariser: Callable[[], Summariser | None] = lambda: None
    meter: UsageMeter | None = None
    keep_backups: int = KEEP_BACKUPS
    # gos vault maintain sets it: Clayton's own run tries held periods again.
    retry_held: bool = False

    def run(
        self,
        today: dt.date,
        jobs: Sequence[str] = JOBS,
        stop: Callable[[], bool] = lambda: False,
    ) -> list[JobResult]:
        """Run ``jobs`` in :data:`JOBS` order. Once ``stop()`` is true, the jobs not started
        are ``skipped`` (``stopped``)."""
        unknown = set(jobs) - set(JOBS)
        if unknown:
            raise ValueError(f"unknown jobs: {sorted(unknown)}")
        results = []
        for job in JOBS:
            if job not in jobs:
                continue
            if stop():
                results.append(JobResult(job, SKIPPED, "stopped"))
                continue
            try:
                result = self._job(job, today, stop)
            except Exception as exc:  # one job's failure never stops the others
                log.exception("maintenance job %s failed", job)
                result = JobResult(job, FAILED, f"{type(exc).__name__}: {exc}")
            log.info(
                "maintenance job",
                extra={"job": job, "status": result.status, "detail": result.detail},
            )
            results.append(result)
        return results

    def _job(self, job: str, today: dt.date, stop: Callable[[], bool]) -> JobResult:
        if (
            job in ("compact", "quests", "index", "schema", "fts", "git")
            and not (self.vault / io.SCHEMA_NOTE).is_file()
        ):
            return JobResult(
                job, SKIPPED, f"no vault at {self.vault}: gos vault deploy"
            )
        if job == "compact":
            return compact(
                self.vault,
                today,
                self.summariser,
                self.meter,
                retry_held=self.retry_held,
                stop=stop,
            )
        if job == "quests":
            # imported here: the quest guide imports the tool layer
            from gregtec.quests import summaries

            done = summaries.refresh_paths(self.db, self.vault)
            if done is None:
                return JobResult(job, SKIPPED, "no quests loaded")
            return JobResult(
                job, OK, f"{len(done.written)} written, {done.unchanged} unchanged"
            )
        if job == "index":
            changed = index.build(self.vault, today)
            return JobResult(job, OK, "rebuilt" if changed else "unchanged")
        if job == "schema":
            return check_schema(self.vault, today)
        if job == "fts":
            self.db.parent.mkdir(parents=True, exist_ok=True)
            return JobResult(job, OK, f"{fts.mirror_all(self.db, self.vault)} notes")
        if job == "backup":
            return backup(self.db, self.backups, today, self.keep_backups)
        commit = commit_agent_writes(self.vault, f"GOS idle maintenance {today}")
        return JobResult(
            job, OK, f"committed {commit}" if commit else "nothing to commit"
        )
