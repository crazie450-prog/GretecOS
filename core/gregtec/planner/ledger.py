"""The EU ledger, ``base/power.md`` (WO-4.4, plan §7.1 step 10, §7.2 "EU ledger", D238, D239).

The base-wide EU budget: every plan Clayton accepted and hasn't retired, as **lines** (what a
production plan draws, per tier: average and peak EU/t and amps, its metrics' ``eu`` rows) and
**sources** (what a power plan exports: its net EU/t at its distribution tier and amps), summed
**per tier**, with **warnings** (:func:`build`). Every number is a plan's own, copied from its
JSON and cited ``plan:<id>``; this module only adds and compares them, it computes no game number.

- **The record** (``ledger_line``, one of the core's tables in ``data/gos.sqlite``): per plan, the
  entry :func:`entry` takes from its JSON when it is accepted or moved (:func:`record`). Its status
  is the ``plan`` row's (WO-3.6's lifecycle), joined in :func:`lines`, so the ledger and the plan
  can't disagree about it: ``accepted``, ``building`` and ``running`` count, ``proposed`` and
  ``retired`` don't. So accepting a plan adds its line, a lifecycle move updates it, retiring one
  (or accepting one that supersedes it, :func:`gregtec.planner.designer.accept`) removes it.
  :func:`sync` records a counted plan that has no entry yet (one accepted through WO-3.7's stub,
  before the ledger recorded anything) from its saved JSON; every move and ``gos ledger --write``
  run it.
- **Two scopes:** ``all`` (every counted plan: the budget once everything accepted is built) and
  ``running`` (only running lines and sources: what should be live now). Net per tier is shown
  for both; every warning names its scope.
- **Warnings** (D239): ``deficit`` (the scope's average use over its generation, base-wide: GT
  transformers move EU between tiers), ``tier_mismatch`` (a tier that lines draw at with no source
  at that tier: its EU has to come through transformers, or a source is missing) and
  ``amp_overload`` (a tier whose lines can draw more amps at once than its sources give).
- **The note** (:func:`render`, :func:`write`): ``base/power.md`` through
  :class:`gregtec.vault.io.VaultIO` only (atomic, path-guarded, checked against the vault's
  ``_gos/schema.md``); facts, then the lines, sources, per-tier and warnings TSV tables. Never
  ``me/``.
- **Telemetry (HOOK, WO-7.1's tables):** :func:`reconcile` reads the fresh OC power entries'
  measured average EU/t in and out (``tel_machine`` rows with status ``power``, their newest
  ``eu_in``/``eu_out`` samples, each only while it is itself fresh: WO-7.1 rewrites an unchanged
  reading every ``HEARTBEAT_S``, so an older one was left out since and is never carried
  forward), summed (chained entries, an LSC and a buffer, may count the same EU twice), and the
  note and card show them beside the running scope's
  generation and use. It compares, never corrects: the ledger's numbers stay the plans'. Which
  readings mean what for a given base (an LSC that every line feeds through, or not), and alerts
  on drift, are WO-7.4/7.5's.
- :func:`line_outputs`: what running production lines make (their target, at its rate), which
  My base gives the designer as ``line`` boundaries (D211, D252).
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.bridge.telemetry import HEARTBEAT_S, STALE_S
from gregtec.planner.gtmath.tiers import VN

TABLE = "ledger_line"
DDL = (
    f"CREATE TABLE IF NOT EXISTS {TABLE}(plan TEXT PRIMARY KEY, entry TEXT NOT NULL,"
    " upd INT NOT NULL)"
)
NOTE = "base/power.md"
COUNTED = ("accepted", "building", "running")  # the statuses the ledger counts
SCOPES = ("all", "running")
TIER_ORDER = {name: i for i, name in enumerate(VN)}
NOTE_MAX = 150  # plan 6.2's cap
FRONTMATTER_LINES = 5


def _num(v: object) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0


def _fmt(v: float) -> str:
    """A sum as the note writes it: whole numbers without a point, else 9 significant digits."""
    v = round(v, 9)
    return str(int(v)) if v == int(v) else f"{v:.9g}"


def _tier_key(tier: str) -> int:
    return TIER_ORDER.get(tier, len(TIER_ORDER))


# --- one plan's entry --------------------------------------------------------------------------


@dataclass(frozen=True)
class TierDraw:
    tier: str
    eut_avg: float
    eut_peak: float
    amps: int


@dataclass(frozen=True)
class Source:
    """What a power plan exports: net EU/t at its distribution tier (plan JSON ``power``)."""

    tier: str
    eut: float  # net: the generator's output less its own chain's use
    gross_eut: float
    amps: int
    label: str


@dataclass(frozen=True)
class Entry:
    plan: str  # the plan's id (pl-... or pw-...)
    kind: str  # production or power
    name: str
    draws: tuple[
        TierDraw, ...
    ]  # a production plan's EU/t per tier; none for a power plan
    source: Source | None  # a power plan's export
    outputs: tuple[tuple[str, float], ...]  # stable key -> rate per second (production)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "kind": self.kind,
            "name": self.name,
            "draws": [d.__dict__ for d in self.draws],
            "source": None if self.source is None else self.source.__dict__,
            "outputs": [list(o) for o in self.outputs],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Entry:
        src = d.get("source")
        return cls(
            str(d["plan"]),
            str(d["kind"]),
            str(d["name"]),
            tuple(
                TierDraw(
                    str(x["tier"]),
                    _num(x["eut_avg"]),
                    _num(x["eut_peak"]),
                    int(x["amps"]),
                )
                for x in d.get("draws") or ()
            ),
            None
            if not src
            else Source(
                str(src["tier"]),
                _num(src["eut"]),
                _num(src["gross_eut"]),
                int(src["amps"]),
                str(src["label"]),
            ),
            tuple((str(k), _num(v)) for k, v in d.get("outputs") or ()),
        )


def entry(plan: Mapping[str, Any]) -> Entry:
    """The ledger's entry for ``plan``: its numbers copied from its JSON, never computed."""
    pid = str(plan["id"])
    kind = str(plan.get("kind") or "production")
    name = " ".join(str(plan.get("name") or pid).split())
    if kind == "power":
        pw = plan.get("power") or {}
        dist = pw.get("distribution") or {}
        gen = pw.get("generator") or {}
        tier = str(dist.get("tier") or gen.get("tier") or "")
        source = (
            Source(
                tier,
                _num(pw.get("net_eut")),
                _num(pw.get("gross_eut")),
                int(dist.get("amps") or 0),
                " ".join(str(gen.get("label") or "").split()),
            )
            if tier
            else None
        )
        # The chain's own draws come out of the generator's output (net_eut): not lines.
        return Entry(pid, kind, name, (), source, ())
    draws = tuple(
        TierDraw(
            str(e["tier"]),
            _num(e.get("eut_avg")),
            _num(e.get("eut_peak")),
            int(e.get("amps") or 0),
        )
        for e in (plan.get("metrics") or {}).get("eu") or ()
        if isinstance(e, Mapping) and e.get("tier")
    )
    target = plan.get("target") or {}
    outputs = (
        ((str(target["id"]), _num(target["rate_s"])),)
        if target.get("id") and _num(target.get("rate_s")) > 0
        else ()
    )
    return Entry(pid, kind, name, draws, None, outputs)


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)


def record(
    conn: sqlite3.Connection, plan: Mapping[str, Any], now: int | None = None
) -> Entry:
    """Upsert ``plan``'s entry (inside the caller's transaction when there is one)."""
    e = entry(plan)
    ensure_table(conn)
    conn.execute(
        f"INSERT INTO {TABLE}(plan, entry, upd) VALUES (?, ?, ?) ON CONFLICT(plan) DO UPDATE"
        " SET entry = excluded.entry, upd = excluded.upd",
        (
            e.plan,
            json.dumps(e.as_dict(), sort_keys=True, separators=(",", ":")),
            int(time.time()) if now is None else now,
        ),
    )
    return e


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def sync(home: Path, conn: sqlite3.Connection, now: int | None = None) -> list[str]:
    """Record every counted plan (``plan`` row ``accepted``, ``building`` or ``running``) that
    has no entry yet, from its saved JSON under ``home`` (a plan accepted before WO-4.4: WO-3.7's
    stub recorded nothing). Inside the caller's transaction when there is one; returns the
    problems (a plan whose JSON can't be read stays out, and says so)."""
    from gregtec.planner.outputs import store

    if not _has(conn, "plan"):
        return []
    ensure_table(conn)
    marks = ",".join("?" * len(COUNTED))
    missing = [
        str(r[0])
        for r in conn.execute(
            f"SELECT p.id FROM plan p LEFT JOIN {TABLE} l ON l.plan = p.id"
            f" WHERE p.status IN ({marks}) AND l.plan IS NULL ORDER BY p.id",
            COUNTED,
        )
    ]
    problems = []
    for pid in missing:
        try:
            plan = store.load(home, pid)
        except (OSError, ValueError) as exc:
            problems.append(f"plan:{pid} isn't in the EU ledger: its JSON: {exc}")
            continue
        record(conn, plan, now)
    return problems


@dataclass(frozen=True)
class Line:
    entry: Entry
    status: str


def lines(conn: sqlite3.Connection) -> list[Line]:
    """Every recorded plan the ledger counts, with its ``plan`` row's status, by plan id."""
    if not _has(conn, TABLE) or not _has(conn, "plan"):
        return []
    marks = ",".join("?" * len(COUNTED))
    out = []
    for raw, status in conn.execute(
        f"SELECT l.entry, p.status FROM {TABLE} l JOIN plan p ON p.id = l.plan"
        f" WHERE p.status IN ({marks}) ORDER BY l.plan",
        COUNTED,
    ):
        out.append(Line(Entry.from_dict(json.loads(raw)), str(status)))
    return out


def running_outputs(
    conn: sqlite3.Connection,
) -> dict[str, tuple[float, tuple[str, ...]]]:
    """Stable key -> (rate per second, the plan ids making it) of **running** production lines,
    rates summed: My base's panel cites the plans (``plan:``)."""
    rates: dict[str, list[float]] = {}
    plans: dict[str, list[str]] = {}
    for line in lines(conn):
        if line.status != "running":
            continue
        for key, rate in line.entry.outputs:
            rates.setdefault(key, []).append(rate)
            plans.setdefault(key, []).append(line.entry.plan)
    return {k: (math.fsum(rates[k]), tuple(plans[k])) for k in rates}


def line_outputs(conn: sqlite3.Connection) -> dict[str, float]:
    """Stable key -> rate per second made by **running** production lines (summed), for My
    base's ``line_outputs`` (D211): the designer takes them as ``line`` boundaries (never for a
    request's own target, :mod:`gregtec.planner.graph`)."""
    return {k: rate for k, (rate, _) in running_outputs(conn).items()}


# --- telemetry (the hook) ----------------------------------------------------------------------


@dataclass(frozen=True)
class Telemetry:
    """OC's measured power, from WO-7.1's tables (see the module docstring: a hook)."""

    entries: int  # fresh power entries read
    eu_in: float  # their measured average EU/t in, summed
    eu_out: float  # and out
    refs: tuple[str, ...]  # tel:<binding>/<address>
    age_s: int  # the oldest sample read's age


def reconcile(conn: sqlite3.Connection, now: float | None = None) -> Telemetry | None:
    """**HOOK** (plan 7.2 "reconciles with telemetry once OC is live"): the fresh OC power
    entries' newest ``eu_in``/``eu_out`` samples, summed; None without WO-7.1's tables or
    without a fresh power entry. A stale entry is never read, nor a sample older than its
    entry's ``last_seen`` less WO-7.1's ``HEARTBEAT_S`` (an unchanged reading is rewritten that
    often, so an older one has been left out since: WO-7.1 never carries one forward).
    ``age_s`` is the oldest sample read's age."""
    if not _has(conn, "tel_machine") or not _has(conn, "tel_sample"):
        return None
    t = time.time() if now is None else now
    rows = conn.execute(
        "SELECT id, last_seen FROM tel_machine WHERE status = 'power' AND last_seen >= ?"
        " ORDER BY id",
        (int(t) - STALE_S,),
    ).fetchall()
    if not rows:
        return None
    eu_in: list[float] = []
    eu_out: list[float] = []
    refs = []
    oldest = 0
    for src, last_seen in rows:
        got = False
        for key, sink in (("eu_in", eu_in), ("eu_out", eu_out)):
            row = conn.execute(
                "SELECT value, ts FROM tel_sample WHERE src = ? AND key = ? AND ts >= ?"
                " AND value IS NOT NULL ORDER BY ts DESC LIMIT 1",
                (src, key, int(last_seen) - HEARTBEAT_S),
            ).fetchone()
            if row is not None:
                sink.append(float(row[0]))
                oldest = max(oldest, max(0, int(t) - int(row[1])))
                got = True
        if got:
            refs.append(f"tel:{src}")
    if not refs:
        return None
    return Telemetry(
        len(refs), math.fsum(eu_in), math.fsum(eu_out), tuple(refs), oldest
    )


# --- the ledger --------------------------------------------------------------------------------


@dataclass(frozen=True)
class TierNet:
    tier: str
    gen_eut: float
    use_eut_avg: float
    use_eut_peak: float
    amps_in: int  # what the sources at this tier give
    amps_out: int  # what the lines at this tier can draw at once
    plans: tuple[str, ...]

    @property
    def net_eut(self) -> float:
        return self.gen_eut - self.use_eut_avg


@dataclass(frozen=True)
class LedgerWarning:
    kind: str  # deficit, tier_mismatch, amp_overload
    scope: str  # all or running
    tier: str  # "" for a base-wide warning
    detail: str
    plans: tuple[str, ...]


@dataclass
class Ledger:
    lines: list[Line]
    tiers: dict[str, list[TierNet]]  # per scope
    warnings: list[LedgerWarning]
    telemetry: Telemetry | None = None
    notes: list[str] = field(default_factory=list)

    def total(self, scope: str, what: str) -> float:
        return math.fsum(getattr(t, what) for t in self.tiers[scope])

    def as_dict(self) -> dict[str, Any]:
        return {
            "lines": [
                {
                    **ln.entry.as_dict(),
                    "status": ln.status,
                    "ref": f"plan:{ln.entry.plan}",
                }
                for ln in self.lines
            ],
            "tiers": {
                s: [
                    {
                        "tier": t.tier,
                        "gen_eut": t.gen_eut,
                        "use_eut_avg": t.use_eut_avg,
                        "use_eut_peak": t.use_eut_peak,
                        "net_eut": t.net_eut,
                        "amps_in": t.amps_in,
                        "amps_out": t.amps_out,
                        "plans": [f"plan:{p}" for p in t.plans],
                    }
                    for t in ts
                ]
                for s, ts in self.tiers.items()
            },
            "warnings": [
                {
                    "kind": w.kind,
                    "scope": w.scope,
                    "tier": w.tier or None,
                    "detail": w.detail,
                    "plans": [f"plan:{p}" for p in w.plans],
                }
                for w in self.warnings
            ],
            "telemetry": None if self.telemetry is None else self.telemetry.__dict__,
            "notes": list(self.notes),
        }


def _per_tier(lines: Sequence[Line]) -> list[TierNet]:
    acc: dict[str, dict[str, Any]] = {}

    def at(tier: str) -> dict[str, Any]:
        return acc.setdefault(
            tier, {"gen": [], "avg": [], "peak": [], "in": 0, "out": 0, "plans": []}
        )

    for ln in lines:
        e = ln.entry
        if e.source is not None:
            a = at(e.source.tier)
            a["gen"].append(e.source.eut)
            a["in"] += e.source.amps
            a["plans"].append(e.plan)
        for d in e.draws:
            a = at(d.tier)
            a["avg"].append(d.eut_avg)
            a["peak"].append(d.eut_peak)
            a["out"] += d.amps
            a["plans"].append(e.plan)
    return [
        TierNet(
            tier,
            math.fsum(a["gen"]),
            math.fsum(a["avg"]),
            math.fsum(a["peak"]),
            a["in"],
            a["out"],
            tuple(dict.fromkeys(a["plans"])),
        )
        for tier, a in sorted(acc.items(), key=lambda kv: _tier_key(kv[0]))
    ]


def _warnings(
    scope: str, lines: Sequence[Line], tiers: Sequence[TierNet]
) -> list[LedgerWarning]:
    out: list[LedgerWarning] = []
    gen = math.fsum(t.gen_eut for t in tiers)
    use = math.fsum(t.use_eut_avg for t in tiers)
    users = tuple(ln.entry.plan for ln in lines if ln.entry.draws)
    if use > gen:
        out.append(
            LedgerWarning(
                "deficit",
                scope,
                "",
                f"lines use {_fmt(use)} EU/t on average, sources give {_fmt(gen)} EU/t: "
                f"short {_fmt(use - gen)} EU/t",
                users,
            )
        )
    sourced = [t.tier for t in tiers if t.gen_eut > 0 or t.amps_in > 0]
    for t in tiers:
        drawn = t.use_eut_avg > 0 or t.use_eut_peak > 0 or t.amps_out > 0
        here = tuple(
            ln.entry.plan
            for ln in lines
            if any(d.tier == t.tier for d in ln.entry.draws)
        )
        if drawn and t.tier not in sourced:
            out.append(
                LedgerWarning(
                    "tier_mismatch",
                    scope,
                    t.tier,
                    f"{t.tier} lines draw {_fmt(t.use_eut_avg)} EU/t but no source is at"
                    f" {t.tier} (sources at: {', '.join(sourced) or 'none'}): transformers"
                    " needed, or a source is missing",
                    here,
                )
            )
        elif drawn and t.amps_out > t.amps_in:
            out.append(
                LedgerWarning(
                    "amp_overload",
                    scope,
                    t.tier,
                    f"{t.tier} lines can draw {t.amps_out} A at once, {t.tier} sources give"
                    f" {t.amps_in} A",
                    t.plans,
                )
            )
    return out


def build(found: Iterable[Line], telemetry: Telemetry | None = None) -> Ledger:
    """The ledger of ``found`` (see the module docstring); pure, so the same lines always give
    the same ledger."""
    all_lines = list(found)
    scoped = {
        "all": all_lines,
        "running": [ln for ln in all_lines if ln.status == "running"],
    }
    tiers = {s: _per_tier(ls) for s, ls in scoped.items()}
    warnings = [w for s in SCOPES for w in _warnings(s, scoped[s], tiers[s])]
    notes = []
    if any(ln.entry.kind == "power" for ln in all_lines):
        notes.append(
            "a source's EU/t is its plan's net: its fuel chain's use is already taken out"
        )
    return Ledger(all_lines, tiers, warnings, telemetry, notes)


def read(conn: sqlite3.Connection, now: float | None = None) -> Ledger:
    return build(lines(conn), reconcile(conn, now))


# --- the note and the card ---------------------------------------------------------------------


def _clean(text: str) -> str:
    return " ".join(text.replace("\t", " ").split())


def render(ledger: Ledger, cap: int | None = NOTE_MAX) -> str:
    """``base/power.md``'s body (VaultIO writes the frontmatter): at most ``cap`` lines with
    the frontmatter (plan 6.2's 150); None prints every row (``gos ledger``)."""
    facts: list[tuple[str, str]] = [
        ("title", "EU ledger"),
        ("lines", str(sum(1 for ln in ledger.lines if ln.entry.draws))),
        ("sources", str(sum(1 for ln in ledger.lines if ln.entry.source))),
    ]
    for scope, prefix in (("all", ""), ("running", "run_")):
        gen = ledger.total(scope, "gen_eut")
        use = ledger.total(scope, "use_eut_avg")
        facts += [
            (f"{prefix}gen_eut", _fmt(gen)),
            (f"{prefix}use_eut_avg", _fmt(use)),
            (f"{prefix}use_eut_peak", _fmt(ledger.total(scope, "use_eut_peak"))),
            (f"{prefix}net_eut", _fmt(gen - use)),
        ]
    facts.append(("warnings", str(len(ledger.warnings))))
    tel = ledger.telemetry
    if tel is None:
        facts.append(("tel", "not reconciled: no fresh OC power telemetry"))
    else:
        facts += [
            ("tel_eu_in", _fmt(tel.eu_in)),
            ("tel_eu_out", _fmt(tel.eu_out)),
            (
                "tel",
                f"{tel.entries} OC power entries (summed, may double-count chained"
                f" entries), oldest {tel.age_s} s: "
                + ", ".join(tel.refs[:5])
                + (f" and {len(tel.refs) - 5} more" if len(tel.refs) > 5 else ""),
            ),
        ]
    facts.append(
        (
            "note",
            (
                "statuses accepted, building and running count; all = every one, run ="
                " running only; every number is its plan's (plan:), summed"
            ),
        )
    )
    facts += [("note", n) for n in ledger.notes]
    out = [f"{k}: {v}" for k, v in facts]

    line_rows = [
        f"plan:{ln.entry.plan}\t{_clean(ln.entry.name)}\t{ln.status}\t{d.tier}\t"
        f"{_fmt(d.eut_avg)}\t{_fmt(d.eut_peak)}\t{d.amps}"
        for ln in ledger.lines
        for d in ln.entry.draws
    ]
    source_rows = [
        f"plan:{ln.entry.plan}\t{_clean(ln.entry.source.label or ln.entry.name)}\t{ln.status}\t"
        f"{ln.entry.source.tier}\t{_fmt(ln.entry.source.eut)}\t"
        f"{_fmt(ln.entry.source.gross_eut)}\t{ln.entry.source.amps}"
        for ln in ledger.lines
        if ln.entry.source is not None
    ]
    tier_rows = [
        f"{scope}\t{t.tier}\t{_fmt(t.gen_eut)}\t{_fmt(t.use_eut_avg)}\t{_fmt(t.use_eut_peak)}\t"
        f"{_fmt(t.net_eut)}\t{t.amps_in}\t{t.amps_out}"
        for scope in SCOPES
        for t in ledger.tiers[scope]
    ]
    warn_rows = [
        f"{w.kind}\t{w.scope}\t{w.tier or '-'}\t{_clean(w.detail)}\t"
        + (", ".join(f"plan:{p}" for p in w.plans) or "-")
        for w in ledger.warnings
    ]
    tables = [
        ("plan\tname\tst\ttier\teut_avg\teut_peak\tamps", line_rows, "lines"),
        ("plan\tsource\tst\ttier\teut\tgross_eut\tamps", source_rows, "sources"),
        (
            "scope\ttier\tgen_eut\tuse_eut_avg\tuse_eut_peak\tnet_eut\tamps_in\tamps_out",
            tier_rows,
            "tiers",
        ),
        ("warning\tscope\ttier\tdetail\tplans", warn_rows, "warnings"),
    ]
    # Fit the cap: lines are cut first, then sources, then the per-tier table; warnings last.
    room = 10**9 if cap is None else cap - FRONTMATTER_LINES - len(out)
    present = [(h, rows, what) for h, rows, what in tables if rows]
    need = sum(len(rows) + 3 for _, rows, _ in present)  # fence, header, fence
    cut: dict[str, int] = {}
    for _, rows, what in present:  # in cutting order: the tables' own order
        if need <= room:
            break
        keep = max(0, len(rows) - (need - room + 1))  # one line for the cut's note
        cut[what] = keep
        need += 1 - (len(rows) - keep) - (3 if keep == 0 else 0)
    for header, rows, what in present:
        if what in cut:
            out.append(
                f"note: the {what} table shows {cut[what]} of {len(rows)} rows;"
                " gos ledger prints them all"
            )
            rows = rows[: cut[what]]
        if rows:
            out += ["```tsv", header, *rows, "```"]
    return "\n".join(out) + "\n"


def card(ledger: Ledger, scope: str = "all") -> dict[str, Any]:
    """The catalog's ``power_ledger`` card (D160): generation and use per tier of ``scope``,
    a tier's citation its one plan (null when several share it), warnings as notes."""
    from gregtec.cards import checked

    tel = ledger.telemetry
    notes = [
        f"{w.kind} ({w.scope}{', ' + w.tier if w.tier else ''}): {w.detail}"
        for w in ledger.warnings
    ]
    if tel is not None:
        notes.append(
            f"OC measured {_fmt(tel.eu_in)} EU/t in and {_fmt(tel.eu_out)} EU/t out on"
            f" {tel.entries} power entries; running sources give"
            f" {_fmt(ledger.total('running', 'gen_eut'))} EU/t, running lines use"
            f" {_fmt(ledger.total('running', 'use_eut_avg'))} EU/t"
        )
    if not ledger.lines:
        notes.append("no accepted plans yet: accept a plan to add it")
    notes = [n[:500] for n in notes]
    if len(notes) > 20:
        notes = [*notes[:19], f"and {len(notes) - 19} more: gos ledger prints them all"]
    tiers = [
        t for t in ledger.tiers[scope] if t.tier in TIER_ORDER and t.tier != "MAX+"
    ][:16]
    return checked(
        {
            "type": "power_ledger",
            "title": "EU ledger" + (" · running" if scope == "running" else ""),
            "source": {
                "kind": "plan" if tel is None else "oc",
                "label": "Accepted plans" if tel is None else "Accepted plans · OC",
                "age_s": None if tel is None else tel.age_s,
            },
            "tiers": [
                {
                    "tier": t.tier,
                    "gen": t.gen_eut,
                    "use": t.use_eut_avg,
                    "net": t.net_eut,
                    "ref": f"plan:{t.plans[0]}" if len(t.plans) == 1 else None,
                }
                for t in tiers
            ],
            "notes": notes,
        }
    )


# --- writing -----------------------------------------------------------------------------------


def write(home: Path, ledger: Ledger) -> tuple[str | None, list[str]]:
    """Write ``base/power.md`` through the vault's writer; (path, problems). No vault, or a note
    the vault refuses, is a problem, never a failure: the record in the DB is the ledger."""
    from gregtec.vault.io import VaultError, VaultIO

    root = home / "vault"
    if not root.is_dir():
        return None, [f"no vault: {NOTE} wasn't written"]
    try:
        return VaultIO(root).write(NOTE, render(ledger)), []
    except (VaultError, OSError) as exc:
        return None, [f"{NOTE} wasn't written: {exc}"]


@dataclass(frozen=True)
class LedgerUpdate:
    plan: str  # plan:<id>
    status: str  # the plan's lifecycle status the entry is for
    draws: tuple[TierDraw, ...]  # the plan's EU/t per tier, as its metrics give them
    recorded: bool  # the entry is in the ledger's record
    note: str
    warnings: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "status": self.status,
            "draws": [d.__dict__ for d in self.draws],
            "recorded": self.recorded,
            "note": self.note,
            "warnings": list(self.warnings),
            "problems": list(self.problems),
        }


def ledger_update(
    home: Path,
    conn: sqlite3.Connection,
    plan: Mapping[str, Any],
    status: str,
    *,
    now: float | None = None,
) -> LedgerUpdate:
    """Record ``plan`` at ``status`` (its ``plan`` row already says so) on ``conn`` (writable)
    and rewrite ``base/power.md``. A plan the ledger doesn't count at ``status`` (proposed,
    retired) leaves the ledger; its record stays for the history."""
    e = record(conn, plan, None if now is None else int(now))
    return publish(home, conn, e, status, now=now)


def publish(
    home: Path,
    conn: sqlite3.Connection,
    e: Entry,
    status: str,
    *,
    now: float | None = None,
) -> LedgerUpdate:
    """:func:`ledger_update` without the record step, for a caller that recorded ``e`` in its
    own transaction (``gregtec.planner.designer._move``): read the ledger, rewrite
    ``base/power.md`` and report ``e``'s update."""
    ledger = read(conn, now)
    path, problems = write(home, ledger)
    counted = status in COUNTED
    what = (
        ("added to" if status == "accepted" else "updated in")
        if counted
        else "taken out of"
    )
    return LedgerUpdate(
        f"plan:{e.plan}",
        status,
        e.draws,
        True,
        f"{what} the EU ledger" + (f" ({path})" if path else ""),
        tuple(f"{w.kind} ({w.scope}): {w.detail}" for w in ledger.warnings),
        tuple(problems),
    )
