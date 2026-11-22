"""``gos eval shell-revise`` (WO-10.10, D368): does a revision change what was asked and keep the
rest?

Each case of ``tests/evals/shell_revise.tsv`` (``id theme line_type brief followup expect
touches``) is a brief and one follow-up. The run designs the brief's shell (GOS's procedural
generator around the plans named, with the case's theme), starts a design, then hands the
follow-up to the **reviser under test** and scores the version it leaves current:

- **gate**: the brief's shell and the revision both passed the gate (:func:`gregtec.build.shell.
  design`) and the revision is a new version (refused: the case fails);
- **asked**: every ``expect`` check holds on the new version against the old one (``;``-separated:
  ``roof.style=gable``, ``lights=none``, ``windows.sides!north``, ``windows.sides-north``
  (the old version's sides less north, every other side kept), ``headroom>+0``,
  ``walls.top>+0``, ``<kind>.every>+0`` / ``<+0``: the new value against the old);
- **kept**: every operation whose kind is not in ``touches`` is unchanged (the rest of the shell).

The reviser under test is a function ``(home, design id, follow-up) -> None`` that leaves a new
current version or none. ``--fake`` (free, CI): GOS's own reviser (:func:`gregtec.build.
shell_versions.revise` without operations), the stand-in for the model. The paid run (Clayton's
PC step): the deep lane's architect, handed the follow-up as the router hands it
(:func:`gregtec.build.shell_versions.revise_task`), one fresh task each, on a scratch copy of
GOS_HOME, so his own designs never change. Its cost is the deep lane's, bounded per case by
``[budget] task_max_usd``.

Deterministic scoring; the file holds no game number (coordinates only, which the layout gives).
"""

from __future__ import annotations

import csv
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from gregtec.build import shell, shell_store
from gregtec.build import shell_versions as versions
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[3]
CASES = REPO / "tests" / "evals" / "shell_revise.tsv"
COLUMNS = ("id", "theme", "line_type", "brief", "followup", "expect", "touches")
TABLE = ("id", "gate", "asked", "kept", "result", "why")
_CHECK = re.compile(r"^([a-z]+)(?:\.([a-z]+))?(=|!|-|>|<)(.+)$")

Reviser = Callable[[Path, str, str], None]


class EvalError(ValueError):
    pass


@dataclass(frozen=True)
class Case:
    id: str
    theme: str
    line_type: str
    brief: str
    followup: str
    expect: tuple[str, ...]
    touches: frozenset[str]


def load_cases(path: Path = CASES) -> list[Case]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    out = []
    for n, r in enumerate(rows, 2):
        if tuple(r) != COLUMNS:
            raise EvalError(f"{path.name}: the columns are {' '.join(COLUMNS)}")
        expect = tuple(x.strip() for x in r["expect"].split(";") if x.strip())
        for x in expect:
            if not _CHECK.match(x):
                raise EvalError(f"{path.name} line {n}: {x!r} is not a check")
        touches = frozenset(x.strip() for x in r["touches"].split(",") if x.strip())
        if not expect or not touches <= set(shell.OPS):
            raise EvalError(
                f"{path.name} line {n}: expect and touches (op kinds) are needed"
            )
        out.append(
            Case(
                r["id"],
                r["theme"],
                r["line_type"],
                r["brief"],
                r["followup"],
                expect,
                touches,
            )
        )
    if len({c.id for c in out}) != len(out):
        raise EvalError(f"{path.name}: case ids repeat")
    return out


def _of(ops: Sequence[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [o for o in ops if o.get("op") == kind]


def _value(rec: Mapping[str, Any], kind: str, key: str | None) -> Any:  # noqa: ANN401
    ops = rec["ops"]
    if kind == "headroom":
        return rec["headroom"]
    found = _of(ops, kind)
    if key is None:
        return found
    if kind == "walls" and key == "top":
        whole = [o for o in found if "lo" not in o] or found  # not a tower's walls
        tops = [
            o.get("y", [0, o["hi"][1] if "hi" in o else rec["wall_top"]]) for o in whole
        ]
        return max((t[1] if isinstance(t, list) else t) for t in tops) if tops else None
    if (
        kind == "roof" and found
    ):  # the whole building's roof (WO-10.12: not a tower's cap)
        whole = [o for o in found if "lo" not in o] or found
        return whole[-1].get(key, "flat" if key == "style" else None)
    return found[0].get(key) if found else None


def check(old: Mapping[str, Any], new: Mapping[str, Any], spec: str) -> str | None:
    """None when ``spec`` holds of ``new`` (against ``old``), else why not."""
    m = _CHECK.match(spec)
    if m is None:
        return f"{spec!r} is not a check"
    kind, key, op, want = m.groups()
    got = _value(new, kind, key)
    if op == "=":
        if want == "none":
            return None if not got else f"{kind}: {len(got)} operations left"
        return None if str(got) == want else f"{kind}.{key} is {got!r}, not {want}"
    if op == "!":
        found = _of(new["ops"], kind)
        if not found:
            return f"no {kind} left at all"
        bad = [o for o in found if want in o.get(key or "", list(shell.SIDES))]
        return None if not bad else f"{kind}.{key} still has {want}"
    if op == "-":  # the old sides less these ones, and no other side lost or gained

        def sides(rec: Mapping[str, Any]) -> set[str]:
            return {
                x
                for o in _of(rec["ops"], kind)
                for x in o.get(key or "", list(shell.SIDES))
            }

        want_sides = sides(old) - set(want.split("+"))
        return (
            None
            if sides(new) == want_sides
            else f"{kind}.{key} are {sorted(sides(new))}, not {sorted(want_sides)}"
        )
    before = _value(old, kind, key)
    if not isinstance(got, int) or not isinstance(before, int):
        return f"{kind}.{key}: no number to compare ({before!r} -> {got!r})"
    delta = int(want)
    ok = got - before > delta if op == ">" else got - before < delta
    return None if ok else f"{kind}.{key} went {before} -> {got}"


def kept(
    old: Mapping[str, Any], new: Mapping[str, Any], touches: frozenset[str]
) -> str | None:
    for kind in sorted(set(shell.OPS) - touches):
        if list(_of(old["ops"], kind)) != list(_of(new["ops"], kind)):
            return f"{kind} changed, though not asked"
    return None


def gos_reviser(conn: sqlite3.Connection, vault: VaultIO | None) -> Reviser:
    """GOS's own reviser as the reviser under test (the free run)."""

    def revise(home: Path, ds: str, words: str) -> None:
        versions.revise(conn, home, vault, ds, words)

    return revise


def run_case(
    conn: sqlite3.Connection,
    home: Path,
    vault: VaultIO | None,
    plans: Sequence[str],
    case: Case,
    reviser: Reviser,
) -> dict[str, str]:
    row = {
        "id": case.id,
        "gate": "",
        "asked": "",
        "kept": "",
        "result": "FAIL",
        "why": "",
    }
    try:
        made = shell_store.make(
            conn,
            home,
            vault,
            list(plans),
            theme_id=case.theme,
            line_type=case.line_type,
        )
    except shell.ShellError as exc:
        row["why"] = f"the brief: {exc}"
        return row
    if not made.outcome.ok:
        row["gate"] = "refused"
        row["why"] = "the brief: " + " | ".join(made.outcome.reasons)
        return row
    d = versions.start(home, made.build, asked=case.brief, by="procedural")
    v1 = d["current"]
    if v1 != made.build:  # pragma: no cover - start makes it current
        raise AssertionError(v1)
    try:
        reviser(home, d["id"], case.followup)
    except shell.ShellError as exc:
        row["gate"] = "refused"
        row["why"] = str(exc)
        return row
    after = versions.get(home, d["id"])["current"]
    if after == v1:
        row["gate"] = "refused"
        row["why"] = "no new version: the revision was refused or not made"
        return row
    row["gate"] = "pass"
    old, new = shell_store.load(home, v1), shell_store.load(home, after)
    misses = [w for w in (check(old, new, x) for x in case.expect) if w]
    row["asked"] = "pass" if not misses else "fail"
    left = kept(old, new, case.touches)
    row["kept"] = "pass" if left is None else "fail"
    row["why"] = "; ".join([*misses, *([left] if left else [])])
    if not misses and left is None:
        row["result"] = "PASS"
    versions.make_current(
        home, d["id"], v1, "the eval's next case starts from the brief"
    )
    return row


def run(
    conn: sqlite3.Connection,
    home: Path,
    vault: VaultIO | None,
    plans: Sequence[str],
    cases: Sequence[Case],
    reviser: Reviser,
    out: TextIO,
) -> list[dict[str, str]]:
    rows = []
    print("\t".join(TABLE), file=out)
    for case in cases:
        row = run_case(conn, home, vault, plans, case, reviser)
        rows.append(row)
        print("\t".join(row[c] for c in TABLE), file=out)
    passed = sum(r["result"] == "PASS" for r in rows)
    print(f"# {passed} of {len(rows)} cases pass", file=out)
    return rows
