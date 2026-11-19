"""WO-2.7 acceptance logic on a DB built from a real GregtecLink client harness run.

The 50 questions (tests/evals/recipe_lookup.tsv) were written over the dev runtime's DB: every
step's references must mean rows there (a none row: none), the truth's own SQL must agree with
the tools on them, and the whole set must pass with the oracle fakes (the fast lane's model and,
for the rows the router hands to the deep lane, the real Claude Code CLI loading the
recipe-lookup skill against the fake Messages API): plan 8's >= 95% correct and 0 uncited
numbers, as a logic check. The real-model run is Clayton's PC step (docs/runbooks/
recipe_lookup.md). As test_fast_lane_harness.py: only with GOS_REQUIRE_HARNESS_RUN=1 after the
client harness. Nothing is written outside tmp_path.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gregtec.agents import recipe_eval, recipe_oracle
from gregtec.agents.recipe_eval import ANSWER, ASK, NONE
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.agents.tools.tsv import parse
from gregtec.config import Config
from gregtec.db import access
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def home(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("recipe-lookup-harness")
    built_home(game, home)
    return home


@pytest.fixture(scope="module")
def ground(home: Path) -> Iterator[recipe_eval.Ground]:
    g = recipe_eval.Ground(access.db_path(home))
    yield g
    g.close()


@pytest.fixture(scope="module")
def reg(home: Path) -> Registry:
    return build_registry(access.db_path(home), Glossary.find(None))


def test_every_reference_means_rows_of_the_db(ground: recipe_eval.Ground) -> None:
    for q in recipe_eval.load():
        for step in q.steps:
            t = ground.truth(step)
            if q.expect == NONE:
                assert not t.rows, (q.id, step.text())
            else:
                assert t.rows, (q.id, step.text())
                for want in step.want:
                    if want in ("count", "simplest"):
                        continue
                    have = [r for r in t.rows.values() if r.fields.get(want)]
                    assert have, (q.id, step.text(), want)
                if "simplest" in step.want:
                    assert t.simplest(), (q.id, step.text())


def _refs(reg: Registry, tool: str, args: dict[str, object]) -> set[str]:
    out = reg.call(tool, args)
    found = {r["ref"] for r in parse(out.text).rows}
    for _ in range(100):
        lines = out.text.split("\n")
        cursor = next((ln[6:] for ln in lines if ln.startswith("more: ")), None)
        if cursor is None:
            break
        out = reg.call("more", {"cursor": cursor})
        found |= {r["ref"] for r in parse(out.text).rows}
    return found


def test_the_truth_agrees_with_the_tools(
    ground: recipe_eval.Ground, reg: Registry
) -> None:
    """For each step whose lookup the tools answer whole (a make step through recipe_find,
    a use step with no filter through recipe_uses), the same rows."""
    seen = 0
    for q in recipe_eval.load():
        for step in q.steps:
            names = {k for k, _ in step.filters}
            whole = (step.op == recipe_eval.MAKE and names <= {"map", "tier"}) or (
                step.op == recipe_eval.USE and not names
            )
            if not whole:
                continue
            tool, args = recipe_oracle.lookup(step)
            assert set(ground.truth(step).all) == _refs(reg, tool, args), q.id
            seen += 1
    assert seen >= 20


def test_the_words_name_each_steps_item(reg: Registry) -> None:
    """The words the fake models look each step up by resolve to the step's own item here
    (a chained step's may name an input slot instead); unclear rows' words are unclear."""
    for q in recipe_eval.load():
        if not q.steps:
            out = parse(reg.call("item_resolve", {"text": q.words[0]}).text)
            assert out.head.get("ambiguous") == "1", q.id
            continue
        for i, (step, words) in enumerate(zip(q.steps, q.words, strict=True)):
            kind = {"kind": "machine"} if step.op == recipe_eval.MACHINE else {}
            out = parse(reg.call("item_resolve", {"text": words} | kind).text)
            if i and q.kind == "multistep" and out.rows[0]["key"] != step.key:
                continue  # taken from the previous row's in cell (r36's copper wire)
            assert out.head.get("ambiguous") == "0" and out.rows, (q.id, words)
            assert out.rows[0]["key"] == step.key, (q.id, words, out.rows[0]["key"])


def test_the_question_set_passes_with_the_oracles(home: Path, tmp_path: Path) -> None:
    cfg = Config(gos_home=home)
    questions = recipe_eval.load()
    ground = recipe_eval.Ground(access.db_path(home))
    out = io.StringIO()
    try:
        code, results = recipe_oracle.run_fake(cfg, questions, ground, out)
    finally:
        ground.close()
    report = out.getvalue()
    assert code == 0, report
    assert "correct: 50 of 50 (100.0%; target >= 95%)" in report
    assert "uncited numbers: 0 (target 0)" in report
    lanes = {r.lane for r in results}
    assert lanes == {"instant", "fast", "deep"}  # every lane the router sends rows to
    deep_rows = [r for r in results if r.lane == "deep"]
    assert len(deep_rows) >= 3 and all(r.score.passed for r in deep_rows)
    answered = [r for r in results if r.q.expect == ANSWER]
    assert all(r.score.cited for r in answered)
    # no answer cites a flagged row (fake?, recycling) where unflagged rows exist
    ground = recipe_eval.Ground(access.db_path(home))
    try:
        for r in answered:
            flagged = {c for s in r.q.steps for c in ground.truth(s).flagged}
            assert not flagged & set(r.score.cited), (r.q.id, r.score.cited)
    finally:
        ground.close()
    # an unclear item reached by a model, not by pre-retrieval: each lane asked itself
    asked = {r.lane for r in results if r.q.expect == ASK and not r.reply.asked}
    assert asked == {"fast", "deep"}
    table = tmp_path / "report.tsv"
    recipe_eval.write_table(results, table)
    assert len(table.read_text(encoding="utf-8").splitlines()) == 51
