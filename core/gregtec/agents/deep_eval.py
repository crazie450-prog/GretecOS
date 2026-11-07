"""``gos eval deep-lane`` (WO-2.4 acceptance, QA:A4, D157): the cost table of 10 deep-lane tasks.

Each row of ``tests/evals/deep_lane_tasks.tsv`` (``id kind request why``) is handed to the deep
lane as the router hands a request (``source`` router), one at a time, each in a fresh session,
and the table gives per task: the outcome, the SDK's cost (``total_cost_usd``), the cost by
model, model turns, tool calls, subagents and seconds; then the total, mean and median cost and
how many tasks reached the cap. A task that pauses at ``[budget] task_max_usd`` is stopped, not
approved, so a run never spends more than 10 caps; its row says ``capped`` and its cost
so far. The table is also written as TSV (``--out``, default
``GOS_HOME/data/reports/deep-lane-costs-<date>.tsv``) for WO-3.7's re-measurement and plan 4.4's
cost sketch.

The real run uses the stored key and records every call in ``llm_usage`` (task
``eval:deep-lane:<id>``); it is Clayton's PC step. ``--fake`` runs the real Claude Code CLI
against :class:`gregtec.llm.fake_api.FakeMessagesAPI` (a scripted model that reads INDEX.md and
answers): no key, no cost, nothing recorded; it checks the plumbing and the table, not the
model. Exit 0 when no task failed or found the lane offline.
"""

from __future__ import annotations

import csv
import datetime as dt
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import keyring.errors

from gregtec import config
from gregtec.agents import deep
from gregtec.agents.ask import setup
from gregtec.agents.deep_config import Settings
from gregtec.llm import fake_api, keys

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS = REPO_ROOT / "tests" / "evals" / "deep_lane_tasks.tsv"
COLUMNS = ("id", "kind", "request", "why")
LABEL = "eval:deep-lane"
CAPPED = "capped"  # paused at the cap, then stopped by the eval
WAIT_S = 1800.0
FAKE_KEY = "sk-ant-fake-eval-000000000000000000000000000000"
TABLE = (
    "id",
    "kind",
    "status",
    "cost_usd",
    "turns",
    "tools",
    "subagents",
    "seconds",
    "by_model",
)


class EvalError(ValueError):
    """The task file is malformed."""


@dataclass(frozen=True)
class Row:
    id: str
    kind: str
    request: str
    why: str


def load(path: Path) -> list[Row]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise EvalError(f"{path}: the columns must be {', '.join(COLUMNS)}")
        rows = [Row(**{k: (r[k] or "").strip() for k in COLUMNS}) for r in reader]
    ids = [r.id for r in rows]
    if len(set(ids)) != len(ids) or not all(r.id and r.request for r in rows):
        raise EvalError(f"{path}: every row needs a unique id and a request")
    return rows


def _table_row(
    row: Row, task: deep.DeepTask | None, seconds: float, capped: bool
) -> dict[str, str]:
    if task is None:
        return {"id": row.id, "kind": row.kind, "status": "lost"} | dict.fromkeys(
            TABLE[3:], ""
        )
    by_model = ", ".join(f"{m} ${usd:.4f}" for m, usd in sorted(task.models.items()))
    return {
        "id": row.id,
        "kind": row.kind,
        "status": CAPPED if capped else task.status,
        "cost_usd": f"{task.cost_usd:.4f}",
        "turns": str(task.turns),
        "tools": str(len(task.tools)),
        "subagents": ",".join(task.subagents) or "-",
        "seconds": f"{seconds:.0f}",
        "by_model": by_model or "-",
    }


def run(
    lane: deep.DeepLaneService,
    rows: list[Row],
    out: TextIO,
    *,
    table_path: Path | None,
    clock: Callable[[], float] = time.perf_counter,
) -> int:
    lines: list[dict[str, str]] = []
    tasks: list[deep.DeepTask] = []
    print("\t".join(TABLE), file=out)
    for row in rows:
        started = clock()
        handed = lane.handoff(row.request, row.request, "router")
        task = lane.wait(handed.id, WAIT_S)
        capped = task is not None and task.status == deep.NEEDS_APPROVAL
        if capped:
            assert task is not None
            lane.stop_task(task.id)  # the cost so far is the row's; never approve here
        line = _table_row(row, task, clock() - started, capped)
        lines.append(line)
        if task is not None:
            tasks.append(task)
        print("\t".join(line[c] for c in TABLE), file=out)
        if task is not None and task.error and not capped:
            print(f"# {row.id}: {task.error}", file=out)
    costs = [t.cost_usd for t in tasks]
    capped_n = sum(1 for line in lines if line["status"] == CAPPED)
    bad = [line["id"] for line in lines if line["status"] not in (deep.DONE, CAPPED)]
    if costs:
        print(
            f"# total ${sum(costs):.4f}, mean ${statistics.mean(costs):.4f}, "
            f"median ${statistics.median(costs):.4f} over {len(costs)} tasks; "
            f"{capped_n} reached the ${lane.settings.task_max_usd:.2f} cap",
            file=out,
        )
    if table_path is not None:
        table_path.parent.mkdir(parents=True, exist_ok=True)
        with open(table_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=TABLE, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(lines)
        print(f"# table written to {table_path}", file=out)
    if bad:
        print(f"Result: FAIL ({', '.join(bad)})", file=out)
        return 1
    print("Result: PASS", file=out)
    return 0


def main(
    config_path: Path | None,
    tasks_path: Path | None,
    fake: bool,
    limit: int | None,
    table_path: Path | None,
    out: TextIO,
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        rows = load(tasks_path or TASKS)
    except (OSError, EvalError) as exc:
        print(f"Can't read the tasks: {exc}", file=out)
        return 2
    if limit is not None:
        rows = rows[:limit]
    if table_path is None:
        stamp = dt.date.today().isoformat() + ("-fake" if fake else "")
        table_path = cfg.data_dir / "reports" / f"deep-lane-costs-{stamp}.tsv"
    agents = setup(cfg, deep_lane=False)  # the registry and the meter; the lane is ours
    settings = Settings.from_config(cfg)
    try:
        if fake:
            with fake_api.FakeMessagesAPI(fake_api.echo) as api:
                lane = deep.DeepLaneService(
                    agents.registry,
                    settings,
                    home=cfg.gos_home / "data" / "agent-eval",
                    key=lambda: FAKE_KEY,
                    meter=None,  # simulated costs never reach llm_usage
                    env=api.env(),
                    task_label=LABEL,
                )
                try:
                    return run(lane, rows, out, table_path=table_path)
                finally:
                    lane.stop()
        try:
            keys.get_key(keys.ANTHROPIC)
        except (keys.MissingKeyError, keyring.errors.KeyringError) as exc:
            print(f"Not run: {exc} (or use --fake)", file=out)
            return 1
        lane = deep.DeepLaneService.for_home(cfg.gos_home, settings, agents.meter)
        lane.registry = agents.registry
        lane.task_label = LABEL
        if agents.meter is None:
            print(
                "# no DB yet: the costs are printed, not recorded in llm_usage",
                file=out,
            )
        try:
            return run(lane, rows, out, table_path=table_path)
        finally:
            lane.stop()
    finally:
        agents.close()
