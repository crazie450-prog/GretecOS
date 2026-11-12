"""``gos eval design-cost`` (WO-3.7 acceptance, [QA:A4], D156, D253): the deep lane's cost on 10
design tasks, and ``[budget] task_max_usd`` from the measurements.

Each row of ``tests/evals/design_tasks.tsv`` (``id kind request spec why``: Clayton's words and
the ``design_production`` spec a good run would use) goes to the deep lane as the router hands
it, one fresh session each, and the table gives per task: the outcome, the SDK's cost
(``cost_usd``), the lane's own running estimate (``est_usd``: what the cap is checked against,
D156), the explanation's planner-model calls (``explain_usd``: ``plan_explain``'s writer, D199,
outside the lane's cap and metered on their own), turns, tool calls, subagents, seconds, the
cost by model and the ``mode`` (real or fake); then the worst, mean and median and the
recommended cap. A task that reaches the measuring cap is stopped, never approved, so a run
costs at most 10 caps (``--measure-cap``, default the current ``task_max_usd``) plus the
explanations; a capped run recommends nothing.

**Where it runs** (review L2): on a copy of GOS_HOME's DB and vault in :data:`SCRATCH`
(``data/eval/design-cost-home``, replaced by each run and kept until the next for a look), so
the tasks' plans, notes and the Designer's latest design never touch the live ones. Only the
real run's ``llm_usage`` rows go to the live DB (task ``eval:design-cost:<id>``, and
``plan_explain`` for the explanations).

**The cap** (:func:`recommend`, D253): the worst task's cost (the larger of the SDK's and the
lane's), times :data:`MARGIN`, rounded up to a :data:`STEP`, at least :data:`MIN_CAP`. A normal
design task then never pauses, and a runaway one pauses at about one and a half times the
worst seen. ``--apply`` writes it into gos.toml's ``[budget]`` (:func:`apply_cap`, keeping every
other line), only from a real run of every row of the default task file with every task done;
``--apply-from <table.tsv>`` does the same from such a run's saved table, without running (and
paying) again (review L1).

**Real run** (Clayton's PC step; spending is his call): the stored key. **``--fake``** runs the
real Claude Code CLI against :class:`gregtec.llm.fake_api.FakeMessagesAPI` replaying
``tests/evals/design_tokens.tsv`` (``task role turn input output cache_read cache_write call``):
each task's orchestrator turns with their recorded token counts and tool calls
(``design_production`` with the task's spec, ``plan_explain`` on the plan it saved,
``plan_list``), and its ``planner`` row as the explanation's writer call (the same fake API).
The tools run for real against the scratch copy; the costs are the recorded counts priced: no
key, no cost, nothing in ``llm_usage``. On the tool tests' made-up DB only the steel tasks can
be designed: the others' ``design_production`` (and so ``plan_explain``) return errors to the
scripted model, which still finishes, so their row is ``done`` with the cost of a failed design,
not of a real one (review L3). The recording in the repo is a synthetic shape (sizes from the
WO-2.4 fake runs), not a measurement: the real run's numbers set the cap.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
import shutil
import sqlite3
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TextIO

import keyring.errors

from gregtec import config
from gregtec.agents import deep, deep_eval, plan_writer
from gregtec.agents.ask import setup
from gregtec.agents.deep_config import Settings
from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.mcp import qualified
from gregtec.db import access
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
CALLS = ("", "design_production", "plan_list", "plan_explain")
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
    "explain_usd",
    "turns",
    "tools",
    "subagents",
    "seconds",
    "by_model",
    "mode",
)
REAL, FAKE = "real", "fake"
SCRATCH = Path("data", "eval", "design-cost-home")  # in GOS_HOME
LATEST_PLAN = (
    "@designed"  # in a scripted plan_explain call: the plan the task just saved
)
NO_PLAN = "pl-0000000000"  # when it saved none (the tool then says so)
_DESIGNED = re.compile(r"^design: plan:(pl-[0-9A-Za-z_-]+)", re.MULTILINE)


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
        elif t.call == "plan_explain":
            tools = [(qualified("plan_explain"), {"id": LATEST_PLAN})]
        if tools:
            text = "Working on it."
        elif (
            t.role == "planner"
        ):  # the explanation: no number, so the validator passes it
            text = (
                "The first route ranks first on the weighted score; the others follow."
            )
        else:
            text = f"Recorded answer for task {task.id}."
        by_role[t.role].append(
            ApiTurn(text, tools, t.input, t.output, t.cache_read, t.cache_write)
        )
    return _Script(
        Scripted(
            {settings.orchestrator: by_role["orchestrator"], settings.planner: by_role["planner"]},
            default=ApiTurn("Done.", input_tokens=50, output_tokens=5),
        )
    )  # fmt: skip


@dataclass
class _Script:
    """The scripted turns, with ``plan_explain``'s id filled in from the last tool result
    (the plan ``design_production`` just saved, else :data:`NO_PLAN`)."""

    turns: Scripted

    def __call__(self, body: dict[str, Any]) -> ApiTurn:
        turn = self.turns(body)
        if not any(args.get("id") == LATEST_PLAN for _, args in turn.tools):
            return turn
        seen = "\n".join(
            fake_api.result_text(b) for b in fake_api.last_tool_results(body)
        )
        found = _DESIGNED.search(seen)
        pid = found.group(1) if found else NO_PLAN
        tools = [
            (name, {**args, "id": pid} if args.get("id") == LATEST_PLAN else args)
            for name, args in turn.tools
        ]
        return replace(turn, tools=tools)


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
    task: Task,
    done: deep.DeepTask | None,
    seconds: float,
    capped: bool,
    explain_usd: float,
    mode: str,
) -> dict[str, str]:
    row = deep_eval.Row(task.id, task.kind, task.request, task.why)
    line = deep_eval._table_row(row, done, seconds, capped)
    line["est_usd"] = f"{done.spent_usd:.4f}" if done is not None else ""
    line["explain_usd"] = f"{explain_usd:.4f}"
    line["mode"] = mode
    return line


def scratch_home(home: Path, dest: Path) -> Path:
    """A copy of ``home``'s DB (SQLite's backup: safe while the core runs) and vault (without
    its git history) at ``dest``, replacing what was there."""
    if dest.exists():
        shutil.rmtree(dest)
    db = access.db_path(dest)
    db.parent.mkdir(parents=True)
    live = access.db_path(home)
    if live.is_file():
        src = access.connect(live, readonly=True)
        dst = sqlite3.connect(db)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
    if (home / "vault").is_dir():
        shutil.copytree(
            home / "vault", dest / "vault", ignore=shutil.ignore_patterns(".git")
        )
    return dest


def run(
    lane: deep.DeepLaneService,
    tasks: list[Task],
    out: TextIO,
    *,
    table_path: Path | None,
    before: Callable[[Task], None] | None = None,
    explained: list[float] | None = None,
    mode: str = REAL,
    clock: Callable[[], float] = time.perf_counter,
) -> list[dict[str, str]]:
    """Hand each task to ``lane`` in turn; print and return the table's rows. ``explained``
    collects the explanation calls' costs (emptied before each task)."""
    lines: list[dict[str, str]] = []
    spent = explained if explained is not None else []
    print("\t".join(TABLE), file=out)
    for task in tasks:
        if before is not None:
            before(task)
        spent.clear()
        started = clock()
        handed = lane.handoff(task.request, task.request, "router")
        done = lane.wait(handed.id, deep_eval.WAIT_S)
        capped = done is not None and done.status == deep.NEEDS_APPROVAL
        if capped:
            assert done is not None
            lane.stop_task(done.id)  # its cost so far is the row's; never approve here
        line = _line(task, done, clock() - started, capped, sum(spent), mode)
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


def read_table(path: Path) -> list[dict[str, str]]:
    """A saved table (``--out``) as :func:`run` returned it."""
    return _rows(path, TABLE)


def full_real_run(lines: list[dict[str, str]]) -> str | None:
    """Why ``lines`` can't set the cap (None: they can): every row of the default task file,
    from a real run (review L1)."""
    want = [t.id for t in load_tasks(TASKS)]
    if [x["id"] for x in lines] != want:
        return f"the cap is set from all {len(want)} default tasks ({TASKS.name}), in order"
    if any(x["mode"] != REAL for x in lines):
        return "the cap is set from a real run only (this table is a fake run's)"
    return None


def _apply(config_path: Path | None, lines: list[dict[str, str]], out: TextIO) -> int:
    why = full_real_run(lines)
    rec = recommend(lines)
    if why is not None or rec.usd is None:
        print(f"Not applied: {why or rec.reason}", file=out)
        return 1
    path = config_path or config.default_path()
    try:
        apply_cap(path, rec.usd)
    except (OSError, ValueError) as exc:
        print(f"Not applied: {exc}", file=out)
        return 1
    print(f"# [budget] task_max_usd = {rec.usd:.2f} written to {path}", file=out)
    return 0


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
    apply_from: Path | None = None,
    out: TextIO,
) -> int:
    if apply_from is not None:
        if apply or fake or tasks or recording or limit is not None:
            print("Not run: --apply-from runs nothing; give it alone", file=out)
            return 2
        try:
            saved = read_table(apply_from)
        except (OSError, CostError) as exc:
            print(f"Can't read the table: {exc}", file=out)
            return 2
        return _apply(config_path, saved, out)
    if apply and (fake or tasks is not None or limit is not None):
        print(
            "Not run: --apply sets the cap from a real run of every default task only "
            "(drop --fake, --tasks and --limit)",
            file=out,
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
    if fake:
        missing = [t.id for t in rows if t.id not in recorded]
        if missing:
            print(
                f"Can't read the tasks: no recorded turns for {', '.join(missing)}",
                file=out,
            )
            return 2
    elif not _has_key(out):
        return 1
    if table_path is None:
        stamp = dt.date.today().isoformat() + ("-fake" if fake else "")
        table_path = cfg.data_dir / "reports" / f"design-costs-{stamp}.tsv"
    settings = Settings.from_config(cfg)
    if measure_cap is not None:
        settings = replace(settings, task_max_usd=measure_cap)
    scratch = scratch_home(cfg.gos_home, cfg.gos_home / SCRATCH)
    agents = setup(cfg, deep_lane=False)  # the live meter (llm_usage) only
    explained: list[float] = []
    try:
        if fake:
            with fake_api.FakeMessagesAPI(Scripted({})) as api:
                writer = plan_writer.factory(
                    settings.planner,
                    None,  # replayed costs never reach llm_usage
                    key=lambda: deep_eval.FAKE_KEY,
                    base_url=api.url,
                    on_usd=explained.append,
                )
                lane = deep.DeepLaneService(
                    default_registry(scratch, writer=writer),
                    settings,
                    home=scratch / "data" / "agent-eval",
                    key=lambda: deep_eval.FAKE_KEY,
                    meter=None,
                    env=api.env(),
                    task_label=LABEL,
                )

                def before(task: Task) -> None:
                    api.policy = script(task, recorded[task.id], settings)

                try:
                    lines = run(
                        lane,
                        rows,
                        out,
                        table_path=table_path,
                        before=before,
                        explained=explained,
                        mode=FAKE,
                    )
                finally:
                    lane.stop()
        else:
            writer = plan_writer.factory(
                settings.planner, agents.meter, on_usd=explained.append
            )
            lane = deep.DeepLaneService.for_home(scratch, settings, agents.meter)
            lane.registry = default_registry(scratch, writer=writer)
            lane.task_label = LABEL
            try:
                lines = run(lane, rows, out, table_path=table_path, explained=explained)
            finally:
                lane.stop()
    finally:
        agents.close()
    print(f"# the tasks ran on a copy of the DB and vault: {scratch}", file=out)
    rec = recommend(lines)
    source = "replayed token counts, not a measurement" if fake else "measured"
    if rec.usd is None:
        print(f"# task_max_usd: no recommendation ({rec.reason})", file=out)
    else:
        print(f"# task_max_usd = {rec.usd:.2f} ({source}: {rec.reason})", file=out)
    if apply and _apply(config_path, lines, out) != 0:
        return 1
    bad = [x["id"] for x in lines if x["status"] not in (deep.DONE, deep_eval.CAPPED)]
    if bad:
        print(f"Result: FAIL ({', '.join(bad)})", file=out)
        return 1
    print("Result: PASS", file=out)
    return 0


def _has_key(out: TextIO) -> bool:
    try:
        keys.get_key(keys.ANTHROPIC)
    except (keys.MissingKeyError, keyring.errors.KeyringError) as exc:
        print(f"Not run: {exc} (or use --fake)", file=out)
        return False
    return True
