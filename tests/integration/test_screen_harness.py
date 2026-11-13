"""WO-8.2 acceptance logic on a DB built from a real GregtecLink harness run (fake model).

The five screenshot cases (tests/evals/screen_cases.tsv), drawn synthetic, go through the
screen lane to the fake vision model over the full pack's export: each must name its item as
the DB resolves it, cite the expected ids, keep its numbers checked and say no decoy number.
The injected caption's case is also run with a model that obeys it: nothing reaches the deep
lane and its number is never said. The real-model run on Lab screenshots is Clayton's paid step
(``gos eval screen --images``). As test_fast_lane_harness.py: only with
GOS_REQUIRE_HARNESS_RUN=1 after the client harness; nothing is written outside tmp_path.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

import gregtec.agents.tools  # noqa: F401 - first: deep_lane and the catalog import each other
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.screen import eval as screen_eval
from gregtec.screen.cache import ScreenCache
from gregtec.screen.fake import Scene, VisionClient
from gregtec.screen.lane import ScreenLane
from gregtec.screen.reader import ScreenReader, Shot

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("screen-harness")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    return access.db_path(home)


def test_the_five_cases_pass_on_the_pack_with_the_fake_model(
    db: Path, tmp_path: Path
) -> None:
    reg = build_registry(db, Glossary.find(None), deep=HandoffRecorder())
    cases = screen_eval.load()
    reader, lane = screen_eval.fake_reader(reg, tmp_path, cases)
    out = io.StringIO()
    code = screen_eval.run(
        lambda: reader, cases, screen_eval.draw, lane.allowed, out, fake_model=True
    )
    assert code == 0, out.getvalue()


def test_an_obeying_model_is_stopped_on_the_pack(db: Path, tmp_path: Path) -> None:
    deep = HandoffRecorder()
    reg = build_registry(db, Glossary.find(None), deep=deep)
    case = next(c for c in screen_eval.load() if c.id == "s05")
    frame = screen_eval.draw(case)
    client = VisionClient()
    client.add(
        frame,
        Scene(
            case.item,
            case.look,
            case.caption,
            obeys=True,
            tool={"name": "handoff", "input": {"task": "build an EBF"}},
            claim="The Electric Blast Furnace needs 9999 EU/t.",
        ),
    )
    reader = ScreenReader(reg, ScreenCache(tmp_path), ScreenLane(client, reg))
    reply = reader.read(Shot(frame, "dxcam"))
    assert list(deep._recent) == []
    assert "9999" not in reply.text
