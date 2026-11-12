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
        assert "design_production" in calls and "planner" in calls
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
    assert policy({"model": s.orchestrator}).tools[0][0] == "Agent"


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
    out = io.StringIO()
    assert design_cost.main(None, fake=True, apply=True, out=out) == 2
    assert "real run only" in out.getvalue()
    assert design_cost.main(None, measure_cap=0.0, out=io.StringIO()) == 2

    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    out = io.StringIO()
    assert design_cost.main(cfg, limit=1, table_path=tmp_path / "t.tsv", out=out) == 1
    assert "Not run" in out.getvalue()


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
    for r in rows[1:]:
        cells = dict(zip(design_cost.TABLE, r.split("\t"), strict=True))
        assert float(cells["cost_usd"]) > 0
        assert cells["subagents"] == "planner"
    assert "# task_max_usd = 0.50 (replayed token counts, not a measurement" in text
    assert "Result: PASS" in text
    # design_production ran for real inside the lane: the requests that this made-up export can
    # make (steel) saved their routes, and the latest design is the Designer's.
    ids = designer.latest(home)
    assert ids, "no design was saved by the deep lane"
    done = designer.accept(home, ids[0])
    assert done.status == "accepted" and done.problems == []
    line_note = (home / "vault" / str(done.line)).read_text("utf-8")
    assert f"plan: plan:{ids[0]}" in line_note and "st: accepted" in line_note
    assert "st: accepted" in (home / "vault" / "plans" / f"{ids[0]}.md").read_text(
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
