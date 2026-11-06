"""The curator's jobs: idle maintenance of the vault and the DB (plan 6.6) [QA:A9].

:func:`run` runs them in this order; each reports ``ok``, ``skipped`` or ``failed`` and a failure
never stops the others:

1. ``compact``: daily logs to weekly ones once the week's Sunday is 7 days old, weekly logs to
   monthly ones once the week's Sunday is 8 weeks old (a week belongs to the month of its
   Thursday, as ISO weeks do). One Haiku call per period (:mod:`gregtec.llm.summarise`); the
   summary is written (``log/wk/YYYY-Www.md``, ``log/mo/YYYY-MM.md``, an existing one included in
   the input) before its sources are deleted, and git keeps them. Without an Anthropic key the
   job is skipped and the logs wait.
2. ``index``: rebuild ``INDEX.md`` (:mod:`gregtec.vault.index`).
3. ``schema``: check every note against ``_gos/schema.md``; the problems go to one standing note,
   ``inbox/ib-schema-check.md``, rewritten when they change and removed when there are none.
4. ``fts``: mirror the notes into ``note_fts`` (:mod:`gregtec.vault.fts`).
5. ``backup``: ``VACUUM INTO data/backups/gos-YYYYMMDD.sqlite`` (through a temporary name, so a
   half-written backup never has a backup's name), keeping the newest ``keep_backups``.
6. ``git``: commit the agents' and the curator's changes (:func:`commit_agent_writes`).

Git commits cover every path except ``me/`` (Clayton's) and ``_gos/`` (``gos vault deploy``
commits it); hooks and signing are off, as in deploy.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gregtec.llm.summarise import Summariser, SummaryError
from gregtec.llm.usage import UsageMeter
from gregtec.vault import fts, index, io, schema
from gregtec.vault.deploy import _cmd

log = logging.getLogger(__name__)

JOBS = ("compact", "index", "schema", "fts", "backup", "git")
OK, SKIPPED, FAILED = "ok", "skipped", "failed"
KEEP_BACKUPS = 7
BACKUP = re.compile(r"gos-(\d{8})\.sqlite")
SCHEMA_REPORT = "inbox/ib-schema-check.md"
REPORT_ROWS = 120
DAILY_AGE = dt.timedelta(days=7)
WEEKLY_AGE = dt.timedelta(weeks=8)
WEEKLY = re.compile(r"log/wk/(\d{4})-W(\d{2})\.md")
GIT_PATHS = (".", ":(exclude)me", ":(exclude)_gos")
LANE = "maintenance"  # llm_usage.lane of the compaction calls


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
    try:
        summary = summariser.summarise(period, texts)
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
    sources_line = ", ".join([*earlier, *(s.removeprefix("log/") for s in sources)])
    body = [f"period: {period}", f"from: {sources_line}", *summary.lines]
    text = io.frontmatter(schema.id_for(rel, kind), kind.t, today)
    text += "".join(line + "\n" for line in body)
    problems = schema.check_text(rel, text, types)
    if problems:
        raise SummaryError("; ".join(problems))
    io.atomic_write(target, text.encode("utf-8"))
    for src in sources:
        (root / src).unlink()


def compact(
    root: Path,
    today: dt.date,
    summariser: Summariser | None,
    meter: UsageMeter | None = None,
) -> JobResult:
    days, weeks = _daily_groups(root, today), _weekly_groups(root, today)
    if not days and not weeks:
        return JobResult("compact", OK, "nothing old enough")
    if summariser is None:
        return JobResult(
            "compact",
            SKIPPED,
            "no Anthropic key stored: the logs wait (gos key set anthropic)",
        )
    done, failed = [], []
    for period, sources in sorted(days.items()):
        try:
            _summary_note(
                root, f"log/wk/{period}.md", period, sources, summariser, meter, today
            )
            done.append(period)
        except (SummaryError, OSError, ValueError) as exc:
            failed.append(f"{period}: {exc}")
    # Weeks made just now are only days old: they never qualify for a month in the same run.
    for period, sources in sorted(_weekly_groups(root, today).items()):
        try:
            _summary_note(
                root, f"log/mo/{period}.md", period, sources, summariser, meter, today
            )
            done.append(period)
        except (SummaryError, OSError, ValueError) as exc:
            failed.append(f"{period}: {exc}")
    detail = f"compacted {', '.join(done) or 'nothing'}"
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
    db: Path, folder: Path, today: dt.date, keep: int = KEEP_BACKUPS
) -> JobResult:
    if not db.is_file():
        return JobResult("backup", SKIPPED, "no DB yet")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"gos-{today:%Y%m%d}.sqlite"
    if target.exists():
        detail = f"{target.name} already exists"
    else:
        tmp = folder / f"{target.name}.tmp"
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(db, timeout=30)
        try:
            conn.execute("VACUUM INTO ?", (str(tmp),))
        finally:
            conn.close()
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

    def run(self, today: dt.date, jobs: Sequence[str] = JOBS) -> list[JobResult]:
        unknown = set(jobs) - set(JOBS)
        if unknown:
            raise ValueError(f"unknown jobs: {sorted(unknown)}")
        results = []
        for job in JOBS:
            if job not in jobs:
                continue
            try:
                result = self._job(job, today)
            except Exception as exc:  # one job's failure never stops the others
                log.exception("maintenance job %s failed", job)
                result = JobResult(job, FAILED, f"{type(exc).__name__}: {exc}")
            log.info(
                "maintenance job",
                extra={"job": job, "status": result.status, "detail": result.detail},
            )
            results.append(result)
        return results

    def _job(self, job: str, today: dt.date) -> JobResult:
        if (
            job in ("compact", "index", "schema", "fts", "git")
            and not (self.vault / io.SCHEMA_NOTE).is_file()
        ):
            return JobResult(
                job, SKIPPED, f"no vault at {self.vault}: gos vault deploy"
            )
        if job == "compact":
            return compact(self.vault, today, self.summariser(), self.meter)
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
