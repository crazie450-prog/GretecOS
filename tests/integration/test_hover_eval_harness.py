"""WO-8.4 (D362) on a DB built from a real client harness run: the 10 hover-item cases of
``tests/evals/hover_items.tsv`` all resolve (the registry name, meta and NBT GregtecLink's
client module reports, to the DB's item, the exact NBT variant included), every expectation has
rows to score against on this DB, and ``gos eval hover --fake`` passes all 10 through the
router, the asker's hover context, pre-retrieval, the fast lane with the oracle model and the
number check. The client harness's own items (``whats_this_10_items``) are the same kind of
report. As the other harness tests: only with GOS_REQUIRE_HARNESS_RUN=1 after the client
harness; nothing is written outside tmp_path."""

from __future__ import annotations

import io
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gregtec.agents import hover_eval
from gregtec.agents.recipe_eval import Ground
from gregtec.db import access
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def toml(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    root = tmp_path_factory.mktemp("hover-eval-harness")
    home = root / "home"
    built_home(game, home)
    path = root / "gos.toml"
    path.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def ground(toml: Path) -> Iterator[Ground]:
    home = toml.parent / "home"
    g = Ground(access.db_path(home))
    yield g
    g.close()


def test_all_ten_cases_resolve_on_the_harness_db(ground: Ground) -> None:
    cases = hover_eval.load()
    assert len(cases) == 10
    assert hover_eval.unresolved(ground, cases) == {}
    for case in cases:
        item = hover_eval.resolve(ground, case)
        assert isinstance(item, hover_eval.Resolved)
        assert ("#" in item.key) == ("nbt" in case.item)  # the variant itself
        held = hover_eval.rows(ground, item)
        if case.expect in ("make", "tier"):
            assert held.make.rows, case.id
        elif case.expect == "use":
            assert held.use.rows, case.id
        elif case.expect == "machine":
            assert item.mte is not None and held.machine and held.machine.rows
        elif case.expect == "none":
            assert not held.make.all and not held.use.all, case.id


def test_gos_eval_hover_fake_passes_all_ten(toml: Path) -> None:
    out = io.StringIO()
    code = hover_eval.main(toml, None, True, False, None, out)
    text = out.getvalue()
    assert code == 0, text
    assert "passed: 10 of 10" in text and "Result: PASS" in text
    assert hover_eval.NOT_IN_DB not in text
