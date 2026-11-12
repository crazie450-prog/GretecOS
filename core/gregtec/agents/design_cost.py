"""``gos eval design-cost`` (WO-3.7 acceptance, [QA:A4], D156, D253): the deep lane's cost on 10
design tasks, and ``[budget] task_max_usd`` from the measurements.

Each row of ``tests/evals/design_tasks.tsv`` (``id kind request spec why``: Clayton's words and
the ``design_production`` spec a good run would use) goes to the deep lane as the router hands
it, one fresh session each, and the table gives per task: the outcome, the SDK's cost
(``cost_usd``), the lane's own running estimate (``est_usd``: what the cap is checked against,
D156), turns, tool calls, subagents, seconds and the cost by model; then the worst, mean and
median and the recommended cap. A task that reaches the measuring cap is stopped, never
approved, so a run costs at most 10 caps (``--measure-cap``, default the current
``task_max_usd``); a capped run recommends nothing.

**The cap** (:func:`recommend`, D253): the worst task's cost (the larger of the SDK's and the
lane's), times :data:`MARGIN`, rounded up to a :data:`STEP`, at least :data:`MIN_CAP`. A normal
design task then never pauses, and a runaway one pauses at about one and a half times the
worst seen. ``--apply`` writes it into gos.toml's ``[budget]`` (:func:`apply_cap`, keeping every
other line), only from a real run with every task done.

**Real run** (Clayton's PC step; spending is his call): the stored key, every call in
``llm_usage`` (task ``eval:design-cost:<id>``). **``--fake``** runs the real Claude Code CLI
against :class:`gregtec.llm.fake_api.FakeMessagesAPI` replaying ``tests/evals/design_tokens.tsv``
(``task role turn input output cache_read cache_write call``): each task's orchestrator and
``planner`` turns with their recorded token counts and tool calls (``design_production`` with the
task's spec, the planner subagent, ``plan_list``). The tools run for real against GOS_HOME's DB
and vault, so a fake run saves real plans; the costs are the recorded counts priced, no key, no
cost, nothing in ``llm_usage``. The recording in the repo is a synthetic shape (sizes from the
WO-2.4 fake runs), not a measurement: the real run's numbers set the cap.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TextIO

import keyring.errors

from gregtec import config
from gregtec.agents import deep, deep_eval
from gregtec.agents.ask import setup
from gregtec.agents.deep_config import Settings
from gregtec.agents.tools.mcp import qualified
from gregtec.llm import fake_api, keys
from gregtec.llm.fake_api import ApiTurn, Scripted
from gregtec.setup import tomledit

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS = REPO_ROOT / "tests" / "evals" / "design_tasks.tsv"
RECORDING = REPO_ROOT / "tests" / "evals" / "design_tokens.tsv"
COLUMNS = ("id", "kind", "request", "spec", "why")
TOKEN_COLUMNS = (
    "task",
    "role",
    "turn",
    "input",
    "output",
    "cache_read",
    "cache_write",
    "call",
)
ROLES = ("orchestrator", "planner")
CALLS = ("", "design_production", "plan_list", "planner")
LABEL = "eval:design-cost"
MARGIN = 1.5
STEP = 0.25
MIN_CAP = 0.50
TABLE = (
    "id",
    "kind",
    "status",
    "cost_usd",
    "est_usd",
    "turns",
    "tools",
    "subagents",
    "seconds",
    "by_model",
)
PLANNER_CALL = {
    "subagent_type": "planner",
    "description": "explain the ranked routes",
    "prompt": "Explain the routes design_production returned, in their rank order, from its "
    "rows only: what each needs and its trade-offs. Never reorder them.",
}


class CostError(ValueError):
    """A task or recording file is malformed."""


@dataclass(frozen=True)
class Task:
    id: str
    kind: str
    request: str
    spec: dict[str, Any]
    why: str


@dataclass(frozen=True)
class Turn:
    role: str
    turn: int
    input: int
    output: int
    cache_read: int
    cache_write: int
    call: str


@dataclass(frozen=True)
class Recommendation:
    usd: float | None  # None: the run can't set the cap
    worst: float | None
    reason: str


def _rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if tuple(reader.fieldnames or ()) != columns:
            raise CostError(f"{path}: the columns must be {', '.join(columns)}")
        return [{k: (r[k] or "").strip() for k in columns} for r in reader]


def load_tasks(path: Path = TASKS) -> list[Task]:
    out = []
    for r in _rows(path, COLUMNS):
        try:
            spec = json.loads(r["spec"])
        except json.JSONDecodeError as exc:
            raise CostError(f"{path}: {r['id']}'s spec isn't JSON ({exc})") from None
        if not isinstance(spec, dict) or not spec.get("target"):
            raise CostError(f"{path}: {r['id']}'s spec needs a target")
        out.append(Task(r["id"], r["kind"], r["request"], spec, r["why"]))
    ids = [t.id for t in out]
    if len(set(ids)) != len(ids) or not all(t.id and t.request for t in out):
        raise CostError(f"{path}: every row needs a unique id and a request")
    return out


def load_recording(path: Path = RECORDING) -> dict[str, list[Turn]]:
    out: dict[str, list[Turn]] = {}
    for r in _rows(path, TOKEN_COLUMNS):
        if r["role"] not in ROLES or r["call"] not in CALLS:
            raise CostError(
                f"{path}: {r['task']} has role {r['role']!r} call {r['call']!r}"
            )
        try:
            nums = [
                int(r[k])
                for k in ("turn", "input", "output", "cache_read", "cache_write")
            ]
        except ValueError:
            raise CostError(
                f"{path}: {r['task']} has a count that isn't a whole number"
            ) from None
        if min(nums) < 0:
            raise CostError(f"{path}: {r['task']} has a negative count")
        out.setdefault(r["task"], []).append(Turn(r["role"], *nums, r["call"]))
    return {k: sorted(v, key=lambda t: (t.role, t.turn)) for k, v in out.items()}


def script(task: Task, turns: list[Turn], settings: Settings) -> Scripted:
    """The fake model for one task: its recorded turns, by model."""
    by_role: dict[str, list[ApiTurn]] = {r: [] for r in ROLES}
    for t in turns:
        tools: list[tuple[str, Mapping[str, Any]]] = []
        if t.call == "design_production":
            tools = [(qualified("design_production"), task.spec)]
        elif t.call == "plan_list":
            tools = [(qualified("plan_list"), {})]
        elif t.call == "planner":
            tools = [("Agent", PLANNER_CALL)]
        text = "Working on it." if tools else f"Recorded answer {t.turn} for {task.id}."
        by_role[t.role].append(
            ApiTurn(text, tools, t.input, t.output, t.cache_read, t.cache_write)
        )
    return Scripted(
        {settings.orchestrator: by_role["orchestrator"], settings.planner: by_role["planner"]},
        default=ApiTurn("Done.", input_tokens=50, output_tokens=5),
    )  # fmt: skip


def recommend(lines: list[dict[str, str]]) -> Recommendation:
    """The cap the measured tasks give (see the module docstring)."""
    if not lines:
        return Recommendation(None, None, "no task ran")
    capped = [x["id"] for x in lines if x["status"] == deep_eval.CAPPED]
    bad = [x["id"] for x in lines if x["status"] not in (deep.DONE, deep_eval.CAPPED)]
    costs = [
        max(float(x["cost_usd"] or 0), float(x["est_usd"] or 0))
        for x in lines
        if x["status"] == deep.DONE
    ]
    worst = max(costs) if costs else None
    if bad:
        return Recommendation(None, worst, f"tasks didn't finish: {', '.join(bad)}")
    if capped:
        return Recommendation(
            None,
            worst,
            f"{', '.join(capped)} reached the measuring cap: run again with a higher "
            "--measure-cap",
        )
    assert worst is not None
    steps = math.ceil(round(worst * MARGIN / STEP, 9))
    usd = max(MIN_CAP, steps * STEP)
    return Recommendation(
        usd,
        worst,
        f"worst task ${worst:.4f} x {MARGIN:g}, rounded up to ${STEP:.2f}, at least "
        f"${MIN_CAP:.2f}",
    )


def apply_cap(path: Path, usd: float) -> None:
    """Set ``[budget] task_max_usd`` in gos.toml at ``path``, keeping everything else."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    new = tomledit.set_values(text, {("budget", "task_max_usd"): float(usd)})

    def check(tmp: Path) -> None:
        if config.load(tmp).task_max_usd != float(usd):
            raise tomledit.TomlEditError("the new task_max_usd doesn't read back")

    tomledit.write_atomic(path, new, check)


def _line(
    task: Task, done: deep.DeepTask | None, seconds: float, capped: bool
) -> dict[str, str]:
    row = deep_eval.Row(task.id, task.kind, task.request, task.why)
    line = deep_eval._table_row(row, done, seconds, capped)
    line["est_usd"] = f"{done.spent_usd:.4f}" if done is not None else ""
    return line


def run(
    lane: deep.DeepLaneService,
    tasks: list[Task],
    out: TextIO,
    *,
    table_path: Path | None,
    before: Callable[[Task], None] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> list[dict[str, str]]:
    """Hand each task to ``lane`` in turn; print and return the table's rows."""
    lines: list[dict[str, str]] = []
    print("\t".join(TABLE), file=out)
    for task in tasks:
        if before is not None:
            before(task)
        started = clock()
        handed = lane.handoff(task.request, task.request, "router")
        done = lane.wait(handed.id, deep_eval.WAIT_S)
        capped = done is not None and done.status == deep.NEEDS_APPROVAL
        if capped:
            assert done is not None
            lane.stop_task(done.id)  # its cost so far is the row's; never approve here
        line = _line(task, done, clock() - started, capped)
        lines.append(line)
        print("\t".join(line[c] for c in TABLE), file=out)
        if done is not None and done.error and not capped:
            print(f"# {task.id}: {done.error}", file=out)
    costs = [float(x["cost_usd"]) for x in lines if x["cost_usd"]]
    if costs:
        print(
            f"# worst ${max(costs):.4f}, mean ${statistics.mean(costs):.4f}, median "
            f"${statistics.median(costs):.4f} over {len(costs)} tasks (SDK cost); measuring "
            f"cap ${lane.settings.task_max_usd:.2f}",
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
    return lines


def main(
    config_path: Path | None,
    *,
    tasks: Path | None = None,
    recording: Path | None = None,
    fake: bool = False,
    limit: int | None = None,
    measure_cap: float | None = None,
    table_path: Path | None = None,
    apply: bool = False,
    out: TextIO,
) -> int:
    if apply and fake:
        print(
            "Not run: --apply sets the cap from a real run only (drop --fake)", file=out
        )
        return 2
    if measure_cap is not None and not (math.isfinite(measure_cap) and measure_cap > 0):
        print("Not run: --measure-cap is a dollar amount above 0", file=out)
        return 2
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        rows = load_tasks(tasks or TASKS)
        recorded = load_recording(recording or RECORDING) if fake else {}
    except (OSError, CostError) as exc:
        print(f"Can't read the tasks: {exc}", file=out)
        return 2
    if limit is not None:
        rows = rows[:limit]
    if table_path is None:
        stamp = dt.date.today().isoformat() + ("-fake" if fake else "")
        table_path = cfg.data_dir / "reports" / f"design-costs-{stamp}.tsv"
    settings = Settings.from_config(cfg)
    if measure_cap is not None:
        settings = replace(settings, task_max_usd=measure_cap)
    agents = setup(cfg, deep_lane=False)  # the registry (GOS_HOME's DB and vault)
    try:
        if fake:
            missing = [t.id for t in rows if t.id not in recorded]
            if missing:
                print(
                    f"Can't read the tasks: no recorded turns for {', '.join(missing)}",
                    file=out,
                )
                return 2
            with fake_api.FakeMessagesAPI(Scripted({})) as api:
                lane = deep.DeepLaneService(
                    agents.registry,
                    settings,
                    home=cfg.gos_home / "data" / "agent-eval",
                    key=lambda: deep_eval.FAKE_KEY,
                    meter=None,  # replayed costs never reach llm_usage
                    env=api.env(),
                    task_label=LABEL,
                )

                def before(task: Task) -> None:
                    api.policy = script(task, recorded[task.id], settings)

                try:
                    lines = run(lane, rows, out, table_path=table_path, before=before)
                finally:
                    lane.stop()
        else:
            try:
                keys.get_key(keys.ANTHROPIC)
            except (keys.MissingKeyError, keyring.errors.KeyringError) as exc:
                print(f"Not run: {exc} (or use --fake)", file=out)
                return 1
            lane = deep.DeepLaneService.for_home(cfg.gos_home, settings, agents.meter)
            lane.registry = agents.registry
            lane.task_label = LABEL
            try:
                lines = run(lane, rows, out, table_path=table_path)
            finally:
                lane.stop()
    finally:
        agents.close()
    rec = recommend(lines)
    source = "replayed token counts, not a measurement" if fake else "measured"
    if rec.usd is None:
        print(f"# task_max_usd: no recommendation ({rec.reason})", file=out)
    else:
        print(f"# task_max_usd = {rec.usd:.2f} ({source}: {rec.reason})", file=out)
    if apply:
        if rec.usd is None:
            print("Not applied: the run can't set the cap", file=out)
            return 1
        path = config_path or config.default_path()
        try:
            apply_cap(path, rec.usd)
        except (OSError, ValueError) as exc:
            print(f"Not applied: {exc}", file=out)
            return 1
        print(f"# [budget] task_max_usd = {rec.usd:.2f} written to {path}", file=out)
    bad = [x["id"] for x in lines if x["status"] not in (deep.DONE, deep_eval.CAPPED)]
    if bad:
        print(f"Result: FAIL ({', '.join(bad)})", file=out)
        return 1
    print("Result: PASS", file=out)
    return 0
