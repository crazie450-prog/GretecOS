"""WO-6.5: ``gos eval advisor``: its checks catch a misordered, uncited or spoiling answer, and the
adversarial fake (behind the real Claude Code CLI, as WO-2.7's eval) passes all three scenarios."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gregtec import cli
from gregtec.advisor import evaluate, steps
from gregtec.llm import keys
from gregtec.vault.io import VaultIO

S = evaluate.scenarios()


def truth(home: Path, sc: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    conn = sqlite3.connect(sc.db)
    try:
        advice = steps.advise(
            conn, vault=VaultIO(home / "vault"), db=sc.db, home=home, spoil=0
        )
    finally:
        conn.close()
    return [s.as_dict(i) for i, s in enumerate(advice.steps, 1)]


def good(want: list[dict[str, Any]]) -> str:
    lines = ["First, look at your next steps on the dashboard."]
    for w in want:
        lines.append(
            f"step{w['rank']}: {w['title']} | why: {'; '.join(w['why'])} | "
            f"refs: {', '.join(w['refs'])} | link: {w['link']}"
        )
    return "\n".join(lines)


@pytest.fixture
def trouble(tmp_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    sc = S.build(tmp_path, "trouble")
    return sc, truth(tmp_path, sc)


def test_a_good_answer_passes(trouble: tuple[Any, list[dict[str, Any]]]) -> None:
    sc, want = trouble
    got = evaluate.check(sc, "deep", good(want), want)
    assert got.passed, got.problems


def test_a_reordered_answer_fails_structure(
    trouble: tuple[Any, list[dict[str, Any]]],
) -> None:
    sc, want = trouble
    swapped = [dict(w) for w in want]
    swapped[0]["rank"], swapped[1]["rank"] = 2, 1
    text = good(sorted(swapped, key=lambda w: w["rank"]))
    got = evaluate.check(sc, "deep", text, want)
    assert got.problems["structure"] and not got.passed
    assert evaluate.check(sc, "fast", good(want), want).problems["structure"]


def test_uncited_numbers_and_made_up_ids_fail_citations(
    trouble: tuple[Any, list[dict[str, Any]]],
) -> None:
    sc, want = trouble
    text = good(want) + "\nnote: you are short about 40 EU/t\nsee plan:pl-nothere"
    problems = evaluate.check(sc, "deep", text, want).problems["citations"]
    assert any("no citation" in p for p in problems)
    assert any("plan:pl-nothere is not in the DB" in p for p in problems)
    spoken = "Your lines are short 32 EU/t.\n" + good(want).split("\n", 1)[1]
    problems = evaluate.check(sc, "deep", spoken, want).problems["citations"]
    assert any("spoken part" in p for p in problems)
    dropped = good(want).replace("plan:pl-macer", "the plan")
    problems = evaluate.check(sc, "deep", dropped, want).problems["citations"]
    assert "plan:pl-macer is not cited" in problems


def test_a_locked_quest_in_the_answer_fails_spoilers(
    trouble: tuple[Any, list[dict[str, Any]]],
) -> None:
    sc, want = trouble
    leak = good(want) + "\nAfter that: Pressure makes diamonds, they say."
    assert evaluate.check(sc, "deep", leak, want).problems["spoilers"]


def test_gos_eval_advisor_fake(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "gos.toml"
    cfg.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n', encoding="utf-8"
    )
    assert cli.main(["eval", "advisor", "--fake", "--config", str(cfg)]) == 0
    printed = capsys.readouterr().out
    assert "advisor: 3 of 3 scenarios pass" in printed
    for name in S.NAMES:
        assert f"== {name}: PASS" in printed
    assert "step1: Power deficit" in printed  # trouble's first step


def test_the_ratings_sheet_has_a_column_for_clayton(tmp_path: Path) -> None:
    from gregtec.llm.fake_api import FakeMessagesAPI

    sheet = tmp_path / "reports" / "advisor.tsv"
    out = io.StringIO()
    with FakeMessagesAPI(evaluate.policy(S.QUESTION)) as api:
        code, results = evaluate.run(
            evaluate.fake_lane(api), out, names=("early",), sheet=sheet
        )
    assert code == 0 and [r.scenario for r in results] == ["early"]
    rows = sheet.read_text(encoding="utf-8").splitlines()
    assert rows[0].split("\t") == list(evaluate.TABLE)
    cells = rows[1].split("\t")
    assert cells[:6] == ["early", "deep", "ok", "ok", "ok", ""]  # `useful`: his to fill
    assert "step1: Quest: Bricks (Steam)" in cells[6]


def test_the_real_run_needs_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    cfg = tmp_path / "gos.toml"
    cfg.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n', encoding="utf-8"
    )
    out = io.StringIO()
    assert evaluate.main(cfg, False, out) == 2
    assert "Not run" in out.getvalue()
