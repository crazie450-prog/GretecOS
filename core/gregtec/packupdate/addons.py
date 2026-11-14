"""The addon watcher end to end (WO-11.1, plan 5.2 step 4 and 7.13, D69, QA:D9, D329-D331).

Most of the chain is older work, reused here: Play's fingerprint and the stale flag (WO-1.4,
:mod:`gregtec.ingest.fingerprint`), the hooks and ``gos export run`` (:mod:`gregtec.playhooks`),
collecting and building a run (WO-1.2, WO-1.3), the recipe/crop/oredict diff by mod
(:mod:`.diff`) and the affected-plan check (:mod:`.plans`) of WO-1.9, and the Designer's
re-solve (WO-3.7). This module adds the links between them:

- **The export question** (D69): post-exit's "Mods changed. Export now?" is a small file,
  ``GOS_HOME/run/export-question.json`` (``run\\`` gives gos-dev nothing, so only Clayton's
  processes ask or answer it). :func:`ask` opens it and waits up to 5 minutes for the first
  answer from the dashboard's approval card, the voice's "confirm"/"cancel" (both through the
  core, :func:`answer`) or the Windows dialog, whichever comes first; no answer is a no.
- **The notices**: :func:`due_notices` gives the core one spoken and dashboard notice per stale
  episode (a new Play fingerprint that differs from the last export's; the dashboard's stale
  alert card stays while it holds) and one per export question. The core says each once
  (``kv`` ``addons.announced``).
- **After the export** (:func:`after_export`, called by ``gos export run`` under the instance
  lock once the run is recorded): the run is collected into GOS_HOME and built into the DB,
  then diffed against the previous export's run in the same DB. The **signature diff** per mod
  (recipes added, removed and changed by content hash; machines added and removed) goes to the
  vault inbox through :class:`gregtec.vault.io.VaultIO` (``inbox/ib-YYYYMMDD-export-diff-
  YYYY-MM-DD.md``, D330), with the full report in ``GOS_HOME/data/reports/``. New multiblock
  controllers become **curation tasks** (``data/curation/controllers-<run>.tsv`` and the note):
  curated ``machine_fact`` rows and a structure capture, each only while it is missing. Every
  plan citing a recipe or crop whose content changed or that vanished is **marked** affected
  (``plan.affected_run``, D331): the Designer shows it with a one-click re-solve.

Everything reads game files only from GOS_HOME's collected copy; Export's folder is read by
:func:`gregtec.ingest.collect.collect` (safe_open). Numbers in the note are counts and the DB's.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from gregtec import config
from gregtec.db import access, build
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import collect, fingerprint, machines
from gregtec.packupdate import diff, plans
from gregtec.vault import fts
from gregtec.vault.io import VaultError, VaultIO

QUESTION = Path("run", "export-question.json")  # in GOS_HOME
QUESTION_FORMAT = "gos-export-question/1"
YES, NO = "yes", "no"
VIAS = ("dashboard", "phone", "voice", "discord", "admin", "dialog")
ASK_POLL_S = 1.0
KV_ANNOUNCED = "addons.announced"
ANNOUNCED_KEEP = 20
CURATION = build.CURATION  # GOS_HOME/data/curation
REPORTS = Path("data", "reports")
NOTE_ROWS = 30  # recipe rows in the note; the report in data/reports has them all
NOTE_MACHINES = 15
NOTE_PLANS = 15
SAY_STALE = (
    "Heads up: your mods changed since the last export, so recipe data is stale. "
    "I'll ask about exporting when you quit."
)
SAY_QUESTION = "Mods changed. Export now? Say confirm or cancel."


# --- the export question (D69) -----------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    text: str
    asked: float  # epoch seconds
    expires: float
    answer: str | None = None  # YES, NO or None
    via: str | None = None

    def open(self, now: float) -> bool:
        return self.answer is None and now < self.expires

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": QUESTION_FORMAT,
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "asked": self.asked,
            "expires": self.expires,
            "answer": self.answer,
            "via": self.via,
        }


def _write(home: Path, q: Question) -> None:
    fingerprint.atomic_write(home / QUESTION, json.dumps(q.as_dict()).encode("utf-8"))


def read_question(home: Path) -> Question | None:
    """The question file, answered or not; None when there is none or it doesn't read."""
    try:
        d = json.loads(read_bytes(home / QUESTION)[:65536])
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("format") != QUESTION_FORMAT:
        return None
    try:
        answer = d.get("answer")
        return Question(
            str(d["id"]),
            str(d["title"]),
            str(d["text"]),
            float(d["asked"]),
            float(d["expires"]),
            answer if answer in (YES, NO) else None,
            str(d["via"]) if d.get("via") else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def current(home: Path, now: float | None = None) -> Question | None:
    """The question waiting for an answer, if one is."""
    q = read_question(home)
    return q if q is not None and q.open(time.time() if now is None else now) else None


def open_question(
    home: Path, title: str, text: str, timeout_s: float, now: float | None = None
) -> Question:
    t = time.time() if now is None else now
    q = Question(secrets.token_hex(6), title, text, t, t + timeout_s)
    _write(home, q)
    return q


def answer(
    home: Path, qid: str, yes: bool, via: str, now: float | None = None
) -> Question | None:
    """Answer question ``qid``; None when it isn't the open one (answered, expired or gone)."""
    if via not in VIAS:
        raise ValueError(f"via must be one of {', '.join(VIAS)}")
    q = current(home, now)
    if q is None or q.id != qid:
        return None
    done = Question(q.id, q.title, q.text, q.asked, q.expires, YES if yes else NO, via)
    _write(home, done)
    return done


def close(home: Path, qid: str) -> None:
    """Remove question ``qid``'s file (a newer question's stays)."""
    q = read_question(home)
    if q is not None and q.id == qid:
        try:
            (home / QUESTION).unlink(missing_ok=True)
        except OSError:
            pass  # it expires anyway


# (title, question, timeout in s) -> yes, no, or None for timed out
Dialog = Callable[[str, str, int], bool | None]


def ask(
    home: Path,
    title: str,
    text: str,
    timeout_s: int,
    *,
    dialog: Dialog | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = ASK_POLL_S,
) -> bool | None:
    """Ask on the dashboard and by voice (through the core, which reads the question file) and
    in ``dialog`` at once; the first answer wins. None: no answer within ``timeout_s``."""
    q = open_question(home, title, text, timeout_s)
    shown: list[bool | None] = []
    if dialog is not None:

        def show() -> None:
            try:
                shown.append(dialog(title, text, timeout_s))
            except Exception:  # noqa: BLE001 (a dialog that can't show leaves the others)
                shown.append(None)

        threading.Thread(target=show, daemon=True, name="export-dialog").start()
    deadline = clock() + timeout_s
    try:
        while True:
            got = read_question(home)
            if got is None or got.id != q.id:
                return None  # removed, or replaced by a newer question
            if got.answer is not None:
                return got.answer == YES
            if shown and shown[0] is not None:
                answer(home, q.id, bool(shown[0]), "dialog")
                return bool(shown[0])
            if clock() >= deadline:
                return None
            sleep(poll_s)
    finally:
        close(home, q.id)


# --- the notices -------------------------------------------------------------------------------


def stale_key(home: Path) -> str | None:
    """The stale episode: Play's fingerprint while it differs from the last export's."""
    state = fingerprint.load_state(home)
    if state.get("stale") is not True:
        return None
    play = state.get("play") if isinstance(state.get("play"), dict) else {}
    return f"stale:{play.get('fingerprint') or 'unknown'}"


def _notice(kind: str, title: str, say: str, detail: str, ts: int) -> dict[str, Any]:
    # The shape of gregtec.advisor.notices.Notice.as_dict: the voice client says ``say``.
    return {
        "kind": kind,
        "priority": 2,
        "title": title,
        "say": say,
        "detail": [detail],
        "refs": [],
        "link": None,
        "ts": ts,
    }


def due_notices(
    home: Path, announced: Sequence[str], now: float | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """(key, notice) for each notice not yet said: the stale episode, the open question."""
    t = time.time() if now is None else now
    out: list[tuple[str, dict[str, Any]]] = []
    key = stale_key(home)
    if key is not None and key not in announced:
        last = fingerprint.last_export(home)
        detail = (
            f"Play changed since export {last['run_id']}; recipe answers use that export "
            "until a new one is built."
            if last
            else "No export is recorded yet, so recipe data may not match Play."
        )
        out.append(
            (
                key,
                _notice(
                    "recipes_stale", "Recipe data is stale", SAY_STALE, detail, int(t)
                ),
            )
        )
    q = current(home, t)
    if q is not None and f"question:{q.id}" not in announced:
        out.append(
            (
                f"question:{q.id}",
                _notice("export_question", q.title, SAY_QUESTION, q.text, int(t)),
            )
        )
    return out


def load_announced(conn: sqlite3.Connection) -> list[str]:
    try:
        row = conn.execute(
            "SELECT value FROM kv WHERE key = ?", (KV_ANNOUNCED,)
        ).fetchone()
        got = json.loads(row[0]) if row else []
    except (sqlite3.Error, ValueError, TypeError):
        return []
    return [str(k) for k in got] if isinstance(got, list) else []


def save_announced(conn: sqlite3.Connection, keys: Sequence[str]) -> None:
    conn.execute(
        "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE"
        " SET value = excluded.value, upd = excluded.upd",
        (KV_ANNOUNCED, json.dumps(list(keys)[-ANNOUNCED_KEEP:]), int(time.time())),
    )


def approval_card(q: Question) -> dict[str, Any]:
    """The open question as a catalog approval card (kind ``export``): Export now, Not now."""
    return {
        "type": "approval",
        "id": f"export-{q.id}",
        "kind": "export",
        "title": q.title[:200],
        "readback": q.text[:1000],
        "detail": "No answer before the countdown ends counts as Not now.",
        "expires_at": int(q.expires),
        "actions": [
            {"id": "confirm", "label": "Export now", "primary": True},
            {"id": "cancel", "label": "Not now", "primary": False},
        ],
    }


# --- after the export: collect, build, signature diff, curation, flags ---------------------------


@dataclass(frozen=True)
class MachineRow:
    key: str  # registry@meta: never a numeric block id
    mte_id: int
    name: str
    mod: str
    class_name: str
    multiblock: bool


@dataclass
class MachineDiff:
    added: list[MachineRow] = field(default_factory=list)
    removed: list[MachineRow] = field(default_factory=list)
    note: str | None = None  # why it couldn't be compared


def _machines(folder: Path | None) -> dict[str, MachineRow] | None:
    if folder is None or not (folder / machines.MACHINES).is_file():
        return None
    out: dict[str, MachineRow] = {}
    for m in machines.read_machines(folder):
        key = f"{m.ref.name}@{m.ref.meta}"
        out[key] = MachineRow(
            key,
            m.mte_id,
            m.name or m.meta_name or key,
            m.mod or "unknown",
            m.class_name,
            m.multiblock,
        )
    return out


def machine_diff(old: diff.Side, new: diff.Side) -> MachineDiff:
    """Machines added and removed between two collected runs, keyed by registry@meta."""
    try:
        a, b = _machines(old.folder), _machines(new.folder)
    except (OSError, ValueError) as exc:
        return MachineDiff(note=f"not compared: {exc}")
    if a is None or b is None:
        return MachineDiff(note="not compared: a collected run has no machine pass")
    return MachineDiff(
        [b[k] for k in sorted(b.keys() - a.keys())],
        [a[k] for k in sorted(a.keys() - b.keys())],
    )


@dataclass(frozen=True)
class Curation:
    machine: MachineRow
    needs: tuple[str, ...]  # "machine_fact", "structure"


def curation(conn: sqlite3.Connection, added: Sequence[MachineRow]) -> list[Curation]:
    """New multiblock controllers still missing curated ``machine_fact`` rows or a structure."""
    out: list[Curation] = []
    for m in added:
        if not m.multiblock:
            continue
        row = conn.execute(
            "SELECT id FROM machine WHERE mte_id = ?", (m.mte_id,)
        ).fetchone()
        needs = []
        curated = structure = None
        if row is not None:
            curated = conn.execute(
                "SELECT 1 FROM machine_fact WHERE machine_id = ? AND source = 'curated'",
                (row[0],),
            ).fetchone()
            structure = conn.execute(
                "SELECT 1 FROM structure WHERE machine_id = ?", (row[0],)
            ).fetchone()
        if curated is None:
            needs.append("machine_fact")
        if structure is None:
            needs.append("structure")
        if needs:
            out.append(Curation(m, tuple(needs)))
    return out


def write_curation(home: Path, run_key: str, tasks: Sequence[Curation]) -> Path | None:
    if not tasks:
        return None
    lines = ["mte\titem\tname\tmod\tclass\tneeds"]
    lines += [
        f"{t.machine.mte_id}\t{t.machine.key}\t{t.machine.name}\t{t.machine.mod}\t"
        f"{t.machine.class_name}\t{', '.join(t.needs)}"
        for t in tasks
    ]
    path = home / CURATION / f"controllers-{run_key}.tsv"
    fingerprint.atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))
    return path


def _cell(text: object) -> str:
    return " ".join(str(text).replace("\t", " ").split())[:120]


def note_body(
    s: diff.Summary,
    md: MachineDiff,
    tasks: Sequence[Curation],
    flagged: Sequence[plans.Flagged],
    report: Path | None,
) -> list[str]:
    """The inbox note (``_gos/schema.md``: key: value lines and TSV tables, under 150 lines)."""
    r = s.recipes
    machines_by_mod: dict[str, int] = {}
    for m in md.added:
        machines_by_mod[m.mod] = machines_by_mod.get(m.mod, 0) + 1
    mods = {m for m, *_ in r.by_mod()} | set(machines_by_mod)
    rows = {m: (a, d, c) for m, a, d, c in r.by_mod()}
    order = sorted(
        mods,
        key=lambda m: (-(sum(rows.get(m, (0, 0, 0))) + machines_by_mod.get(m, 0)), m),
    )
    out = [
        f"old_run: {s.old.run_key}",
        f"new_run: {s.new.run_key}",
        f"recipes_added: {len(r.added)}",
        f"recipes_removed: {len(r.removed)}",
        f"recipes_changed: {len(r.changed)}",
        f"machines_added: {len(md.added)}",
        f"machines_removed: {len(md.removed)}",
        f"curation_tasks: {len(tasks)}",
        "plans_affected: "
        + (", ".join(f"plan:{f.plan.id}" for f in flagged) if flagged else "none"),
    ]
    if s.gt_problem is not None:
        out.append(f"note: {s.gt_problem}")
    if md.note:
        out.append(f"note: machines {md.note}")
    if report is not None:
        out.append(f"report: {report.as_posix()}")
    if order:
        out += ["```tsv", "mod\tadded\tremoved\tchanged\tmachines_added"]
        out += [
            f"{_cell(m)}\t{rows.get(m, (0, 0, 0))[0]}\t{rows.get(m, (0, 0, 0))[1]}\t"
            f"{rows.get(m, (0, 0, 0))[2]}\t{machines_by_mod.get(m, 0)}"
            for m in order[: diff.TOP_MODS]
        ]
        out.append("```")
    changes = [
        *(f"removed\t{x.ref}\t{_cell(x.mod)}\t{_cell(x.output)}\t" for x in r.removed),
        *(
            f"changed\t{c.new.ref}\t{_cell(c.new.mod)}\t{_cell(c.new.output)}\t{_cell(c.what())}"
            for c in r.changed
        ),
        *(f"added\t{x.ref}\t{_cell(x.mod)}\t{_cell(x.output)}\t" for x in r.added),
    ]
    if changes:
        out += ["```tsv", "change\trecipe\tmod\toutput\twhat"]
        out += changes[:NOTE_ROWS]
        out.append("```")
        if len(changes) > NOTE_ROWS:
            out.append(
                f"note: {len(changes) - NOTE_ROWS} more recipe changes in the report"
            )
    shown = [*(("added", m) for m in md.added), *(("removed", m) for m in md.removed)]
    if shown:
        out += ["```tsv", "change\tmachine\titem\tname\tmod\tmultiblock"]
        out += [
            f"{what}\tmte:{m.mte_id}\t{m.key}\t{_cell(m.name)}\t{_cell(m.mod)}\t"
            f"{'yes' if m.multiblock else 'no'}"
            for what, m in shown[:NOTE_MACHINES]
        ]
        out.append("```")
    if tasks:
        out += ["```tsv", "curation\tmachine\tname\tneeds"]
        out += [
            f"new controller\tmte:{t.machine.mte_id}\t{_cell(t.machine.name)}\t"
            f"{', '.join(t.needs)}"
            for t in tasks[:NOTE_MACHINES]
        ]
        out.append("```")
        out.append(
            "note: curated machine_fact rows go in data/curated/machine_facts.tsv; "
            "capture the structure in the Lab (plan 7.10)"
        )
    if flagged:
        out += ["```tsv", "plan\tname\tstatus\tcite\twhy"]
        for f in flagged[:NOTE_PLANS]:
            for h in f.hits[:3]:
                out.append(
                    f"plan:{f.plan.id}\t{_cell(f.plan.name)}\t{f.plan.status or '-'}\t"
                    f"{h.cite}\t{_cell(h.why)}"
                )
        out.append("```")
        out.append(
            "note: each affected plan has a one-click re-solve in the Designer (#/designer/<id>)"
        )
    return out


def _statuses(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(pid): str(status or "")
        for pid, status in conn.execute("SELECT id, status FROM plan")
    }


@dataclass
class AfterExport:
    lines: list[str] = field(default_factory=list)
    run_key: str | None = None
    note: str | None = None  # the vault note's path
    flagged: list[str] = field(default_factory=list)
    curation: Path | None = None
    ok: bool = True


def write_note(
    home: Path, body: Sequence[str], today: dt.date | None = None
) -> tuple[str | None, str | None]:
    """(path, None) of the new inbox note, or (None, why not)."""
    root = home / "vault"
    if not root.is_dir():
        return None, f"no vault at {root} (gos vault deploy makes it)"
    db = access.db_path(home)

    def mirror(rel: str) -> None:  # searchable at once, as the agents' notes (WO-2.5)
        fts.mirror_one(db, root, rel)

    day = today or dt.date.today()
    try:
        vault = VaultIO(root, today=lambda: day, on_write=mirror)
        return vault.propose(f"Export diff {day.isoformat()}", "\n".join(body)), None
    except (VaultError, OSError) as exc:
        return None, str(exc)


def _newest_run(db: Path) -> str | None:
    if not db.is_file():
        return None
    try:
        conn = access.connect(db, readonly=True)
    except (sqlite3.Error, access.SchemaError):
        return None
    try:
        row = conn.execute(
            "SELECT run_key FROM export_run ORDER BY created_at DESC, run_key DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return None if row is None else str(row[0])


def after_export(
    cfg: config.Config,
    run_name: str,
    exported: str,
    out: TextIO,
    *,
    allow_harness: bool = False,
    today: dt.date | None = None,
) -> AfterExport:
    """Collect and build export ``run_name`` (Play's fingerprint ``exported``), then diff it
    against the run the DB answered from before (its newest), note it, task and flag (module
    docstring). Never raises for a step that fails: the result says what didn't happen."""
    home = cfg.gos_home
    res = AfterExport()
    db = access.db_path(home)
    previous = _newest_run(db)

    def say(text: str) -> None:
        res.lines.append(text)
        print(f"GOS: {text}", file=out, flush=True)

    try:
        got = collect.collect(
            fingerprint.game_folder(cfg.instance("export")),
            home,
            cfg.keep_collected,
            allow_harness=allow_harness,
        )
        if got.run.run_id != run_name:
            raise collect.CollectError(
                f"collected {got.run.run_id}, but the export was {run_name}"
            )
        built = build.build(
            home,
            run_name,
            keep=cfg.keep_runs,
            fingerprint=exported,
            allow_harness=allow_harness,
        )
    except (collect.CollectError, build.BuildError, OSError) as exc:
        res.ok = False
        say(
            f"export {run_name} not loaded into the DB: {exc}. Recipe data stays as it was."
        )
        return res
    res.run_key = run_name
    say(f"export {run_name} collected and built into {built.db}.")
    if previous is None or previous == run_name:
        say("no earlier export in the DB: nothing to diff against.")
        return res
    try:
        old = diff.side(db, previous, home)
        new = diff.side(db, run_name, home)
        s = diff.summarise(
            old,
            new,
            play_old=fingerprint.run_entries(home, previous),
            play_new=fingerprint.run_entries(home, run_name),
        )
    except diff.DiffError as exc:
        res.ok = False
        say(f"no signature diff: {exc}")
        return res
    md = machine_diff(old, new)
    conn = access.connect(db)
    try:
        found, problems = plans.from_db(conn, home)
        status = _statuses(conn)
        found = [
            plans.Plan(p.id, p.name, status.get(p.id, p.status), p.run, p.cites)
            for p in found
        ]
        flagged = plans.check(
            found,
            lambda key: plans.view(conn, key),
            plans.RunView(conn, new.run_id, new.run_key),
        )
        marked = plans.mark_affected(conn, [f.plan.id for f in flagged], new.run_id)
        tasks = curation(conn, md.added)
    finally:
        conn.close()
    res.flagged = [f.plan.id for f in flagged]
    plan_lines = plans.lines(flagged, len(found), problems)
    text = diff.report(s, plan_lines)
    text += "\n" + "\n".join(_machine_lines(md, tasks)) + "\n"
    report = home / REPORTS / f"export-diff-{old.run_key}-{new.run_key}.recipes.txt"
    fingerprint.atomic_write(report, text.encode("utf-8"))
    res.curation = write_curation(home, new.run_key, tasks)
    say(s.headline())
    say(
        f"machines: {len(md.added)} added, {len(md.removed)} removed"
        + (f" ({md.note})" if md.note else "")
    )
    if tasks:
        say(f"{len(tasks)} curation tasks for new controllers: {res.curation}")
    say(
        f"{marked} plans marked affected by {new.run_key}"
        + (f": {', '.join(res.flagged)}" if res.flagged else "")
    )
    note, why = write_note(home, note_body(s, md, tasks, flagged, report), today)
    res.note = note
    say(f"vault note: {note}" if note else f"warning: no vault note ({why})")
    say(f"signature diff report: {report}")
    return res


def _machine_lines(md: MachineDiff, tasks: Sequence[Curation]) -> list[str]:
    if md.note:
        return [f"Machines: {md.note}"]
    lines = [f"Machines: {len(md.added)} added, {len(md.removed)} removed"]
    lines += [f"  + mte:{m.mte_id} {m.key} [{m.mod}] {m.name}" for m in md.added]
    lines += [f"  - mte:{m.mte_id} {m.key} [{m.mod}] {m.name}" for m in md.removed]
    lines += [
        f"  curation: mte:{t.machine.mte_id} {t.machine.name} needs {', '.join(t.needs)}"
        for t in tasks
    ]
    return lines


# --- what the Designer shows -------------------------------------------------------------------


def affected(conn: sqlite3.Connection, home: Path, pid: str) -> dict[str, Any] | None:
    """Plan ``pid``'s "affected by update" flag: the run that flagged it and the cites it hit
    there, or None when it isn't flagged (or the flagging run has left the DB)."""
    row = conn.execute(
        "SELECT e.id, e.run_key, p.json_path, own.run_key FROM plan AS p"
        " JOIN export_run AS e ON e.id = p.affected_run"
        " LEFT JOIN export_run AS own ON own.id = p.run_id WHERE p.id = ?",
        (pid,),
    ).fetchone()
    if row is None:
        return None
    hits: list[Mapping[str, str]] = []
    try:
        path = Path(str(row[2]))
        plan = plans.read_plan(path if path.is_absolute() else home / path)
    except (OSError, ValueError, TypeError):
        plan = None
    if plan is not None:
        if row[3] is not None:
            plan = plans.Plan(plan.id, plan.name, plan.status, str(row[3]), plan.cites)
        flagged = plans.check(
            [plan],
            lambda key: plans.view(conn, key),
            plans.RunView(conn, int(row[0]), str(row[1])),
        )
        hits = [{"cite": h.cite, "why": h.why} for f in flagged for h in f.hits][:20]
    return {"run": str(row[1]), "hits": hits}
