"""WO-2.3 acceptance logic on a DB built from a real GregtecLink harness run (fake model).

The question set (tests/evals/fast_lane_questions.tsv) was written over the dev runtime's DB, so
here every row must pass with the fake client: the 40 fast-lane rows answer with checked
numbers after pre-retrieval resolved the expected item, the instant and hand-off rows go
where they should, and all 10 ambiguous items ask instead of answering, without a model call.
The real-model timing (P50 time to first token <= 1.2 s) is Clayton's PC run of
``gos eval fast-lane``. As test_tools_harness.py: only with GOS_REQUIRE_HARNESS_RUN=1 after the
client harness; with GOS_QUESTS_PACK set, the quest rows run on the real quest book (a new
world). Nothing is written outside tmp_path.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from gregtec.agents import evals
from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import collect
from gregtec.ingest import quests as bq
from gregtec.llm.fake import EchoClient
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

REPO = Path(__file__).resolve().parents[2]
PLAYER = "11111111-2222-4333-8444-555555555555"


@pytest.fixture(scope="module")
def db(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("fast-lane-harness")
    built_home(game, home)
    path = access.db_path(home)
    if pack := os.environ.get("GOS_QUESTS_PACK"):
        world = home / "world"
        (world / bq.WORLD_DIR).mkdir(parents=True)
        names = {
            "nameCache:9": {
                "0:10": {"isOP:1": 0, "name:8": "Clayton", "uuid:8": PLAYER}
            }
        }
        (world / bq.WORLD_DIR / bq.NAME_CACHE).write_text(
            json.dumps(names), encoding="utf-8"
        )
        conn = access.connect(path)
        try:
            quest_db.write(conn, quest_db.read(world, Path(pack)))
        finally:
            conn.close()
    return path


def test_the_question_set_passes_with_the_fake_model(db: Path, tmp_path: Path) -> None:
    deep = HandoffRecorder()
    reg = build_registry(
        db, Glossary.find(None), deep=deep
    )  # vault-template's glossary
    store = Store(tmp_path / "usage.sqlite")
    try:
        meter = UsageMeter(store, 100.0, (80, 100))
        lane = FastLane(EchoClient(), reg, meter=meter)
        out = io.StringIO()
        code = evals.run(lambda: Asker(reg, deep, lane), evals.load(), out, fake=True)
        report = out.getvalue()
        assert code == 0, report
        assert "passed: 60 of 60" in report
        assert "ambiguous rows that asked: 10 of 10" in report
        rows = store.rows("SELECT task, ttft_ms FROM llm_usage")
        assert len(rows) == 40  # one call per fast-lane row, none for the others
        assert all(r["task"] == evals.TASK and r["ttft_ms"] is not None for r in rows)
    finally:
        store.close()
