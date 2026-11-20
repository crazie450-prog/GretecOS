"""``gos eval shell-revise`` (WO-10.10, D368): briefs with a follow-up each, scored by the gate and
by whether the follow-up changed what was asked and kept the rest (:mod:`.shell_eval`).

- ``--fake`` (free; CI runs the same cases in ``tests/unit/test_shell_versions.py``): GOS's own
  reviser stands in for the model. No key, no cost.
- without ``--fake`` (Clayton's PC step, paid): each follow-up goes to the deep lane's architect
  as the router hands it (``build_revise`` on the design named), one fresh task each, with the
  stored key; ``llm_usage`` records it as ``eval:shell-revise``. About $0.50-$1.00 a case (five
  cases: about $2.50-$5.00), never more than ``[budget] task_max_usd`` a case: a task that reaches
  it is stopped, never approved.

Both run on a scratch copy of GOS_HOME's DB, vault and the plans named
(``data/eval/shell-revise-home``, replaced each run), so his own shells and designs never
change. Exit 0 every case passed, 1 a case failed (or no key), 2 bad input.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import TextIO

COMMAND = "shell-revise"
SCRATCH = Path("data", "eval", "shell-revise-home")  # in GOS_HOME
LABEL = "eval:shell-revise"


def add_parsers(evals: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = evals.add_parser(
        COMMAND,
        help="shell revisions: did the follow-up change what was asked and keep the rest "
        "(WO-10.10)",
        description=(
            "Designs each case's brief (GOS's procedural shell around the plans named), hands "
            "its follow-up to the reviser under test and scores the new version: the gate, the "
            "asked change, the rest kept. --fake: GOS's own reviser (free). Without it: the "
            "deep lane's architect with the stored key (paid; a case at most [budget] "
            "task_max_usd). Runs on a scratch copy of GOS_HOME."
        ),
    )
    p.add_argument(
        "plans", nargs="+", metavar="PLAN_ID", help="the saved plans to house"
    )
    p.add_argument(
        "--fake", action="store_true", help="GOS's own reviser: no key, no cost"
    )
    p.add_argument("--cases", type=Path, default=None, help="another case file")
    p.add_argument("--limit", type=int, default=None, help="only the first N cases")
    p.add_argument("--config", type=Path, default=None, help="gos.toml to read")


def scratch(home: Path, plans: list[str]) -> Path:
    """A copy of ``home``'s DB and vault (:func:`gregtec.agents.design_cost.scratch_home`) and of
    the plans named, at ``home/SCRATCH``."""
    from gregtec.agents import design_cost
    from gregtec.planner.outputs import store as plan_store

    dest = design_cost.scratch_home(home, home / SCRATCH)
    for pid in plans:
        src = plan_store.load(home, pid)  # refused (a ValueError) when it isn't saved
        target = dest / "data" / "plans" / f"{src['id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(home / "data" / "plans" / f"{src['id']}.json", target)
    return dest


def run(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec import config
    from gregtec.build import shell_eval
    from gregtec.db import access
    from gregtec.vault.io import VaultIO

    try:
        cfg = config.load(args.config)
        cases = shell_eval.load_cases(args.cases or shell_eval.CASES)
        if args.limit is not None:
            cases = cases[: args.limit]
        home = scratch(cfg.gos_home, list(args.plans))
    except (OSError, ValueError) as exc:
        print(f"Not run: {exc}", file=out)
        return 2
    db = access.db_path(home)
    conn = access.connect(db, readonly=True)
    vault = VaultIO(home / "vault")
    try:
        if args.fake:
            rows = shell_eval.run(
                conn,
                home,
                vault,
                args.plans,
                cases,
                shell_eval.gos_reviser(conn, vault),
                out,
            )
        else:
            rows = _paid(cfg, home, conn, vault, args.plans, cases, out)
            if rows is None:
                return 1
    finally:
        conn.close()
    print(f"# ran on a copy of the DB, vault and plans: {home}", file=out)
    bad = [r["id"] for r in rows if r["result"] != "PASS"]
    print(f"Result: {'FAIL (' + ', '.join(bad) + ')' if bad else 'PASS'}", file=out)
    return 1 if bad else 0


def _paid(
    cfg: object,
    home: Path,
    conn: object,
    vault: object,
    plans: list[str],
    cases: list[object],
    out: TextIO,
) -> list[dict[str, str]] | None:
    """The architect as the reviser under test: one deep-lane task per case (paid)."""
    from gregtec.agents import deep, deep_eval, design_cost
    from gregtec.agents.ask import setup
    from gregtec.agents.deep_config import Settings
    from gregtec.build import shell_eval
    from gregtec.build import shell_versions as versions
    from gregtec.config import Config

    assert isinstance(cfg, Config)
    if not design_cost._has_key(out):
        return None
    settings = Settings.from_config(cfg)
    agents = setup(cfg, deep_lane=False)  # the live meter (llm_usage) only
    lane = deep.DeepLaneService.for_home(home, settings, agents.meter)
    lane.task_label = LABEL

    def architect(h: Path, ds: str, words: str) -> None:
        handed = lane.handoff(versions.revise_task(ds, words), words, "router")
        done = lane.wait(handed.id, deep_eval.WAIT_S)
        if done is not None and done.status == deep.NEEDS_APPROVAL:
            lane.stop_task(done.id)  # at the cap: stopped, never approved
        spent = f"${done.spent_usd:.4f}" if done is not None else "unknown"
        print(f"# {ds}: the deep lane's estimate {spent}", file=out)

    try:
        return shell_eval.run(conn, home, vault, plans, cases, architect, out)  # type: ignore[arg-type]
    finally:
        lane.stop()
        agents.close()
