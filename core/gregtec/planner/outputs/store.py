"""Saving a plan (WO-3.6, plan §7.1 step 10, Appendix D ``plan``, [QA:D13], D200-D202).

:func:`save` writes, for one plan:

1. ``GOS_HOME/data/plans/<id>.json``: the canonical plan JSON (:func:`gregtec.planner.plan.dumps`),
   the source of truth every other output is made from;
2. ``GOS_HOME/data/plans/<id>/``: the exports (:data:`gregtec.planner.outputs.FILES`): the BOM TSV,
   the Mermaid and PNG diagrams, the gtnhplanner.com project and the gtnh-flow YAML;
3. its ``plan`` row (Appendix D): id, name, created, kind, target, objective (the request's mode),
   status, json_path (relative to GOS_HOME), metrics (JSON), gt_version, run_id. Saving again
   (the same request on the same run gives the same id) refreshes the JSON and metrics and keeps
   the row's name and status, which WO-3.7 lets Clayton edit;
4. the vault note ``plans/<id>.md``, only through :class:`gregtec.vault.io.VaultIO` (atomic,
   path-guarded, checked against ``_gos/schema.md``): a summary of at most 40 lines (plan 6.1,
   D201): ``key: value`` facts, the machines by type as a TSV table (cut short, with a note,
   when it wouldn't fit) and the flow diagram in a ``mermaid`` fence only when the whole note
   still fits, else a ``note:`` pointing at ``flow.mmd``.

The default name is the designer's ("Steel Ingot line · 1/s · MV", [QA:D13]); a plan saved again
keeps its row's name in the JSON, the exports and the note. The lifecycle is :data:`LIFECYCLE`, a
straight line (plan §7.1 step 10); a new row is ``proposed`` and :func:`set_status` moves it one
step along :data:`NEXT` (WO-3.7 adds the accept button and the base/lines note).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.packupdate.plans import PLAN_ID
from gregtec.planner import plan as plan_mod
from gregtec.planner.outputs import FILES, bom, gtnhflow, gtnhplanner, mermaid, png
from gregtec.planner.outputs.book import Book, run_id
from gregtec.vault.io import VaultError, VaultIO, atomic_write
from gregtec.vault.schema import TYPE_MAX_LINES

PLANS = Path("data", "plans")  # in GOS_HOME
LIFECYCLE = ("proposed", "accepted", "building", "running", "retired")
NEXT: dict[str, tuple[str, ...]] = {
    "proposed": ("accepted",),
    "accepted": ("building", "retired"),
    "building": ("running", "retired"),
    "running": ("retired",),
    "retired": (),
}  # plan §7.1 step 10's line, one step at a time; a line dropped before it runs retires (D238)
NOTE_MAX = TYPE_MAX_LINES[
    "plan"
]  # plan 6.1: a plan note is a summary of at most 40 lines
FRONTMATTER_LINES = 5  # ---, id, t, upd, --- (VaultIO writes them)


@dataclass
class Saved:
    id: str
    json_path: Path
    files: dict[str, Path]
    note: str | None  # the vault path written, or None
    problems: list[str] = field(default_factory=list)


def json_rel(pid: str) -> str:
    return (PLANS / f"{pid}.json").as_posix()


def exports(home: Path, plan: Mapping[str, Any], book: Book) -> dict[str, Path]:
    """Write the export files beside the plan's JSON; format -> path."""
    folder = home / PLANS / plan["id"]
    data: dict[str, bytes] = {
        "bom": bom.tsv(plan).encode("utf-8"),
        "mermaid": mermaid.text(plan, book).encode("utf-8"),
        "png": png.render(plan, book),
        "gtnhplanner": gtnhplanner.text(plan, book).encode("utf-8"),
        "gtnhflow": gtnhflow.text(plan, book).encode("utf-8"),
    }
    out: dict[str, Path] = {}
    for fmt, blob in data.items():
        path = folder / FILES[fmt]
        atomic_write(path, blob)
        out[fmt] = path
    return out


def _fact(v: object) -> str:
    if isinstance(v, float):
        return f"{v:.9g}"
    return " ".join(str(v).split())


def note_body(plan: Mapping[str, Any], book: Book, status: str) -> str:
    """The vault note's body (no frontmatter: VaultIO writes it)."""
    m = plan.get("metrics") or {}
    r = plan.get("rank") or {}
    lay = plan.get("layout") or {}
    pid = plan["id"]
    facts: list[tuple[str, object]] = [
        ("title", plan["name"]),
        ("st", status),
        ("plan", f"plan:{pid}"),
        ("json", json_rel(pid)),
        ("run", plan["run"]),
        ("tgt", plan["target"]["id"]),
        ("tgt_rate_s", plan["target"]["rate_s"]),
        ("tier", plan["tier_cap"]),
    ]
    for key in ("machines", "eut_avg", "eut_peak", "eff_h", "scarcity"):
        if key in m:
            facts.append((key, m[key]))
    if lay:
        # D200: a machine type with no structure counts in neither sum, so they are lower bounds
        low = "≥" if lay.get("unknown") else ""
        facts += [("area", f"{low}{lay['area']}"), ("volume", f"{low}{lay['volume']}")]
        if lay.get("unknown"):
            facts.append(("footprint_unknown", ", ".join(lay["unknown"])))
    if r:
        facts.append(
            (
                "rank",
                f"{r.get('position')} of {r.get('of')} · {r.get('column')} · score {r.get('score')}",
            )
        )
    if plan.get("alternatives"):
        facts.append(("alts", ", ".join(f"[[{a}]]" for a in plan["alternatives"])))
    exports_dir = (PLANS / pid).as_posix()
    facts.append(("exports", f"{exports_dir}/ ({', '.join(FILES.values())})"))
    lines = [f"{k}: {_fact(v)}" for k, v in facts if v not in (None, "")]
    rows = [
        f"mte:{t['mte']}\t{_fact(t['name'])}\t{t['tier']}\t{t['count']}"
        for t in m.get("by_type") or ()
    ]
    # Room for the table: the cap less the frontmatter, the facts and one line for the
    # diagram's pointer; a fence, header and fence take 3, a cut table's note 1 more.
    room = NOTE_MAX - FRONTMATTER_LINES - len(lines) - 1
    if rows and len(rows) + 3 > room:
        keep = max(0, room - 4)
        lines.append(
            f"note: the table shows {keep} of {len(rows)} machine types; "
            f"{exports_dir}/{FILES['bom']} has every machine"
        )
        rows = rows[:keep]
    if rows:
        lines += ["```tsv", "mte\tmachine\ttier\tcount", *rows, "```"]
    diagram = ["```mermaid", *mermaid.lines(plan, book), "```"]
    if FRONTMATTER_LINES + len(lines) + len(diagram) <= NOTE_MAX:
        lines += diagram
    else:
        lines.append(
            f"note: the flow diagram is {exports_dir}/{FILES['mermaid']} (too long for this note)"
        )
    return "\n".join(lines) + "\n"


def _row(
    conn: sqlite3.Connection, plan: Mapping[str, Any], run: int, now: float | None
) -> None:
    """Insert or refresh ``plan``'s row, keeping its name and status."""
    conn.execute(
        "INSERT INTO plan(id, name, created, kind, target, objective, status, json_path, metrics,"
        " gt_version, run_id) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET json_path = excluded.json_path,"
        " metrics = excluded.metrics, gt_version = excluded.gt_version,"
        " run_id = excluded.run_id, target = excluded.target, objective = excluded.objective",
        (
            str(plan["id"]),
            plan["name"],
            int(now if now is not None else time.time()),
            plan.get("kind", "production"),
            plan["target"]["id"],
            (plan.get("request") or {}).get("mode"),
            json_rel(str(plan["id"])),
            json.dumps(
                plan.get("metrics") or {}, sort_keys=True, separators=(",", ":")
            ),
            plan.get("gt"),
            run,
        ),
    )


def save_power(
    home: Path,
    plan: Mapping[str, Any],
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
) -> Path:
    """Persist a power plan (WO-4.2's JSON, ``kind: power``) so it can be accepted and moved
    along its lifecycle (WO-4.4, D238): its canonical JSON and its ``plan`` row only. Its
    exports and its ``plans/`` note are production-shaped and wait for the Power view (WO-4.6)."""
    pid = str(plan["id"])
    if not PLAN_ID.fullmatch(pid) or plan.get("kind") != "power":
        raise ValueError(f"{pid!r} isn't a power plan")
    run = run_id(conn, str(plan["run"]))  # LookupError before any file is written
    row = conn.execute("SELECT name FROM plan WHERE id = ?", (pid,)).fetchone()
    if row is not None and row[0] and row[0] != plan["name"]:
        plan = {**plan, "name": str(row[0])}
    path = home / json_rel(pid)
    atomic_write(path, plan_mod.dumps(plan).encode("utf-8"))
    _row(conn, plan, run, now)
    return path


def save(
    home: Path,
    plan: Mapping[str, Any],
    conn: sqlite3.Connection,
    book: Book,
    vault: VaultIO | None = None,
    *,
    now: float | None = None,
) -> Saved:
    """Persist ``plan`` (see the module docstring). ``conn`` is a writable connection to
    ``GOS_HOME/data/gos.sqlite``. A vault that can't take the note is a problem, not a failure:
    the JSON and the row are the plan."""
    pid = str(plan["id"])
    if not PLAN_ID.fullmatch(pid):
        raise ValueError(f"{pid!r} isn't a plan id")
    run = run_id(conn, str(plan["run"]))  # LookupError before any file is written
    row = conn.execute("SELECT name FROM plan WHERE id = ?", (pid,)).fetchone()
    if row is not None and row[0] and row[0] != plan["name"]:
        plan = {
            **plan,
            "name": str(row[0]),
        }  # the row's name (WO-3.7 lets Clayton edit it)
    path = home / json_rel(pid)
    atomic_write(path, plan_mod.dumps(plan).encode("utf-8"))
    files = exports(home, plan, book)
    _row(conn, plan, run, now)
    status = str(
        conn.execute("SELECT status FROM plan WHERE id = ?", (pid,)).fetchone()[0]
        or "proposed"
    )
    saved = Saved(pid, path, files, None)
    if vault is None:
        saved.problems.append(f"no vault: the note plans/{pid}.md wasn't written")
        return saved
    try:
        saved.note = vault.write(f"plans/{pid}.md", note_body(plan, book, status))
    except (VaultError, OSError) as exc:
        saved.problems.append(f"the vault note wasn't written: {exc}")
    return saved


def set_status(conn: sqlite3.Connection, pid: str, status: str) -> str:
    """Move a plan along its lifecycle; returns the old status. ValueError when ``status`` isn't
    one ``NEXT`` allows from where it is (or the plan isn't there)."""
    if status not in LIFECYCLE:
        raise ValueError(f"status must be one of {', '.join(LIFECYCLE)}")
    row = conn.execute("SELECT status FROM plan WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise ValueError(f"no plan {pid}")
    old = str(row[0] or "proposed")
    if status not in NEXT.get(old, ()):
        raise ValueError(
            f"plan {pid} is {old}: it can go to {', '.join(NEXT.get(old, ())) or 'nothing'}"
        )
    conn.execute("UPDATE plan SET status = ? WHERE id = ?", (status, pid))
    return old


def load(home: Path, pid: str) -> dict[str, Any]:
    """A saved plan's canonical JSON."""
    if not PLAN_ID.fullmatch(pid):
        raise ValueError(f"{pid!r} isn't a plan id")
    data = (home / json_rel(pid)).read_bytes()
    out = json.loads(data)
    if not isinstance(out, dict) or out.get("id") != pid:
        raise ValueError(f"{json_rel(pid)} isn't plan {pid}")
    return out
