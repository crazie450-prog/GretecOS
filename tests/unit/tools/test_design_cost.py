"""WO-3.7: the deep lane's cost on 10 design tasks and ``task_max_usd`` from it (``gos eval
design-cost``, [QA:A4], D253), with the real Claude Code CLI against a fake model that replays
recorded token counts; and the §8 end to end: a text request goes through the deep lane, whose
``design_production`` saves real plans on the tool tests' DB, and Clayton accepts one into the
vault.
"""

from __future__ import annotations

import io
import math
import sqlite3
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from gregtec import config
from gregtec.agents import design_cost, router
from gregtec.agents.deep_config import Settings
from gregtec.llm import keys
from gregtec.planner import designer
from gregtec.setup import tomledit
from gregtec.vault import deploy as vault_deploy

REPO = Path(__file__).resolve().parents[3]


def line(tid: str, status: str, cost: float, est: float) -> dict[str, str]:
    return {
        "id": tid,
        "status": status,
        "cost_usd": f"{cost:.4f}",
        "est_usd": f"{est:.4f}",
    }


def test_the_task_and_recording_files() -> None:
    tasks = design_cost.load_tasks()
    assert len(tasks) == 10 and len({t.id for t in tasks}) == 10
    assert all(t.spec["target"] for t in tasks)
    # Design requests go to the deep lane (or reach it through the fast lane's handoff).
    deep_ids = [t.id for t in tasks if router.route(t.request).kind == router.DEEP]
    assert len(deep_ids) >= 7, deep_ids
    recorded = design_cost.load_recording()
    assert set(recorded) == {t.id for t in tasks}
    for turns in recorded.values():
        calls = [t.call for t in turns]
        assert "design_production" in calls and "plan_explain" in calls
        # the planner row is plan_explain's writer call (D199): no tool of its own
        assert [t.call for t in turns if t.role == "planner"] == [""]


def test_bad_files_are_refused(tmp_path: Path) -> None:
    bad = tmp_path / "t.tsv"
    bad.write_text(
        "id\tkind\trequest\tspec\twhy\nx\tdesign\tplan it\tnot json\t-\n", "utf-8"
    )
    with pytest.raises(design_cost.CostError, match="isn't JSON"):
        design_cost.load_tasks(bad)
    bad.write_text("id\tkind\trequest\tspec\twhy\nx\tdesign\tplan it\t{}\t-\n", "utf-8")
    with pytest.raises(design_cost.CostError, match="needs a target"):
        design_cost.load_tasks(bad)
    rec = tmp_path / "r.tsv"
    head = "\t".join(design_cost.TOKEN_COLUMNS) + "\n"
    rec.write_text(head + "x\twriter\t1\t1\t1\t0\t0\t\n", "utf-8")
    with pytest.raises(design_cost.CostError, match="role"):
        design_cost.load_recording(rec)
    rec.write_text(head + "x\tplanner\t1\t-1\t1\t0\t0\t\n", "utf-8")
    with pytest.raises(design_cost.CostError, match="negative"):
        design_cost.load_recording(rec)


def test_the_script_replays_turns_by_model() -> None:
    task = design_cost.load_tasks()[0]
    turns = design_cost.load_recording()[task.id]
    s = Settings()
    policy = design_cost.script(task, turns, s)
    first = policy({"model": s.orchestrator, "messages": []})
    assert first.tools == [("mcp__gos__design_production", task.spec)]
    assert (first.input_tokens, first.cache_write) == (
        turns[0].input,
        turns[0].cache_write,
    )
    assert policy({"model": s.planner, "messages": []}).output_tokens == next(
        t.output for t in turns if t.role == "planner"
    )
    # plan_explain gets the plan the task just saved, from design_production's result
    result = "target: Steel Ingot [x]\ndesign: plan:pl-abc123, plan:pl-d\n"
    saved = {
        "model": s.orchestrator,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "tool_result", "content": result}],
            }
        ],
    }
    assert policy(saved).tools == [("mcp__gos__plan_explain", {"id": "pl-abc123"})]
    again = design_cost.script(task, turns, s)
    again({"model": s.orchestrator, "messages": []})
    assert again({"model": s.orchestrator}).tools == [
        ("mcp__gos__plan_explain", {"id": design_cost.NO_PLAN})
    ]


def test_the_cap_rule() -> None:
    done = [line("a", "done", 0.10, 0.12), line("b", "done", 0.2165, 0.20)]
    rec = design_cost.recommend(done)
    assert rec.worst == 0.2165 and rec.usd == 0.50  # 0.325 -> 0.50 (the floor)
    big = design_cost.recommend([line("a", "done", 0.9, 1.1)])
    assert big.usd == 1.75  # 1.1 x 1.5 = 1.65 -> the next quarter dollar
    exact = design_cost.recommend([line("a", "done", 1.0, 0.0)])
    assert exact.usd == 1.5  # already on a step: no rounding up past it
    assert design_cost.recommend([]).usd is None
    capped = design_cost.recommend([*done, line("c", "capped", 2.0, 2.0)])
    assert capped.usd is None and "measure-cap" in capped.reason
    failed = design_cost.recommend([*done, line("d", "failed", 0, 0)])
    assert failed.usd is None and "d" in failed.reason


def test_apply_cap_keeps_the_rest_of_gos_toml(tmp_path: Path) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text(
        "# Clayton's file\n[budget]\nmonthly_included_usd = 100.0  # credits\n"
        'task_max_usd = 2.0\n\n[models]\nplanner = "claude-opus-5-5"\n',
        "utf-8",
    )
    design_cost.apply_cap(toml, 0.75)
    text = toml.read_text("utf-8")
    assert "# Clayton's file" in text and "# credits" in text
    assert tomllib.loads(text)["budget"]["task_max_usd"] == 0.75
    assert config.load(toml).task_max_usd == 0.75
    assert (tmp_path / "gos.toml.bak").is_file()
    fresh = tmp_path / "new.toml"
    design_cost.apply_cap(fresh, 1.25)
    assert config.load(fresh).task_max_usd == 1.25
    with pytest.raises(tomledit.TomlEditError):
        tomledit.format_value(math.inf)


def test_apply_needs_a_real_run_and_real_needs_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for bad in ({"fake": True}, {"limit": 1}, {"tasks": tmp_path / "t.tsv"}):
        out = io.StringIO()
        assert design_cost.main(None, apply=True, out=out, **bad) == 2  # type: ignore[arg-type]
        assert "every default task only" in out.getvalue()
    assert design_cost.main(None, measure_cap=0.0, out=io.StringIO()) == 2

    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    out = io.StringIO()
    assert design_cost.main(cfg, limit=1, table_path=tmp_path / "t.tsv", out=out) == 1
    assert "Not run" in out.getvalue()


def _table(path: Path, ids: list[str], mode: str, cost: float = 0.3) -> Path:
    head = "\t".join(design_cost.TABLE)
    rows = [
        "\t".join(
            {
                "id": i,
                "status": "done",
                "cost_usd": f"{cost:.4f}",
                "est_usd": f"{cost:.4f}",
                "mode": mode,
            }.get(c, "")
            for c in design_cost.TABLE
        )
        for i in ids
    ]
    path.write_text("\n".join([head, *rows]) + "\n", encoding="utf-8")
    return path


def test_apply_from_a_saved_real_table(tmp_path: Path) -> None:
    """Review L1: the cap from a real run's table without paying again, only from all 10
    default tasks of a real run."""
    ids = [t.id for t in design_cost.load_tasks()]
    toml = tmp_path / "gos.toml"
    toml.write_text("[budget]\ntask_max_usd = 2.0\n", "utf-8")
    out = io.StringIO()
    real = _table(tmp_path / "real.tsv", ids, "real", 0.9)
    assert design_cost.main(toml, apply_from=real, out=out) == 0, out.getvalue()
    assert config.load(toml).task_max_usd == 1.5  # 0.9 x 1.5 = 1.35 -> 1.50
    for name, table in (
        ("fake", _table(tmp_path / "fake.tsv", ids, "fake")),
        ("short", _table(tmp_path / "short.tsv", ids[:1], "real")),
    ):
        out = io.StringIO()
        assert design_cost.main(toml, apply_from=table, out=out) == 1, name
        assert "Not applied" in out.getvalue()
    assert config.load(toml).task_max_usd == 1.5
    out = io.StringIO()
    assert design_cost.main(toml, apply_from=real, fake=True, out=out) == 2
    bad = tmp_path / "bad.tsv"
    bad.write_text("id\tstatus\n", "utf-8")
    assert design_cost.main(toml, apply_from=bad, out=io.StringIO()) == 2


def test_text_requests_through_the_deep_lane_to_an_accepted_plan(
    fresh_home: Callable[..., Path], tmp_path: Path
) -> None:
    """The fake run over all 10 tasks: the table, the cap from the replayed counts, real plans
    saved by design_production, and Clayton accepting one into the vault."""
    home = fresh_home()
    vault_deploy.deploy(home / "vault", REPO / "vault-template")
    cfg = tmp_path / "gos.toml"
    cfg.write_text(
        f'[paths]\ngos_home = "{home.as_posix()}"\n\n[budget]\ntask_max_usd = 2.0\n',
        "utf-8",
    )
    table = tmp_path / "costs.tsv"
    out = io.StringIO()
    code = design_cost.main(cfg, fake=True, table_path=table, out=out)
    text = out.getvalue()
    assert code == 0, text
    assert text.splitlines()[0].split("\t") == list(design_cost.TABLE)
    rows = table.read_text("utf-8").splitlines()
    assert len(rows) == 11 and all("\tdone\t" in r for r in rows[1:])
    # Every task's cost is its recorded tokens priced: the lane's estimate equals the SDK's.
    cells = {}
    for r in rows[1:]:
        row = dict(zip(design_cost.TABLE, r.split("\t"), strict=True))
        cells[row["id"]] = row
        assert float(row["cost_usd"]) > 0 and row["mode"] == "fake"
        # the explanation is plan_explain's, not a subagent's
        assert row["subagents"] == "-"
    # Review L3: "done" isn't "designed". On this made-up export only the steel targets can be
    # designed ("stainless steel ingot" resolves to its steel too); only those tasks reach
    # plan_explain's writer (the planner row, priced). The others' rows are failed designs.
    explained = {i for i, row in cells.items() if float(row["explain_usd"]) > 0}
    assert explained == {"g01", "g06", "g10"}, explained
    assert "# task_max_usd = 0.50 (replayed token counts, not a measurement" in text
    assert "Result: PASS" in text
    # Review L2: the tasks ran on a copy of the DB and vault; the live ones are untouched.
    assert designer.latest(home) == []
    assert not list((home / "vault" / "plans").glob("pl-*.md"))
    scratch = home / design_cost.SCRATCH
    # design_production ran for real inside the lane: the steel requests saved their routes,
    # and the latest design is the Designer's (in the copy).
    ids = designer.latest(scratch)
    assert ids, "no design was saved by the deep lane"
    done = designer.accept(scratch, ids[0])
    assert done.status == "accepted" and done.problems == []
    line_note = (scratch / "vault" / str(done.line)).read_text("utf-8")
    assert f"plan: plan:{ids[0]}" in line_note and "st: accepted" in line_note
    assert "st: accepted" in (scratch / "vault" / "plans" / f"{ids[0]}.md").read_text(
        "utf-8"
    )
    # A fake run never reaches llm_usage.
    c = sqlite3.connect(home / "data" / "gos.sqlite")
    try:
        has = c.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'llm_usage'"
        ).fetchone()[0]
        n = c.execute("SELECT count(*) FROM llm_usage").fetchone()[0] if has else 0
    finally:
        c.close()
    assert n == 0
