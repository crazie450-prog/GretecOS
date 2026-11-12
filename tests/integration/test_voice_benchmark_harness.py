"""WO-5.7 on a DB built from a real GregtecLink harness run: the spoken-length check over the fast
lane's eval set (the fake model, D153; the keys of tests/evals/fast_lane_questions.tsv were
checked against a harness DB), and the synthetic benchmark run end to end with the fast lane
answering from that DB. As the other harness tests: only with GOS_REQUIRE_HARNESS_RUN=1 after
the client harness; nothing is written outside tmp_path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gregtec import voicebench as vb
from gregtec.config import Config
from gregtec.db import build
from gregtec.ingest import collect
from gregtec_voice import tts
from gregtec_voice.settings import VoiceSettings

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("bench-harness")
    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)
    return home


def test_the_eval_set_is_spoken_within_d23(home: Path) -> None:
    answers, why = vb.eval_answers(Config(gos_home=home))
    assert answers is not None and len(answers) == 60, why
    checks = vb.spoken_checks(answers, tts.prepare(tts.lexicon(home)).said)
    assert [c.result for c in checks[:2]] == [vb.PASS, vb.PASS], checks[:2] + [
        c for c in checks if c.result == vb.FAIL or c.note
    ]
    # the item is named first (plan 4.4): q01 resolves to steel on the harness DB
    assert dict(answers)["q01"].lower().startswith("steel")


def test_the_synthetic_run_asks_the_fast_lane(home: Path) -> None:
    lane = vb._fast_lane(Config(gos_home=home))
    assert lane is not None
    try:
        records, said = vb.synthetic_turns(
            VoiceSettings(gos_home=home),
            vb.SyntheticCore(lane, 0.0),
            turns=1,
            say=tts.prepare(tts.lexicon(home)),
        )
    finally:
        lane.agents.close()
    assert [r["trigger"] for r in records] == ["key", "wake"]
    assert all(r["lane"] == "fast" and r["checked"] for r in records)
    assert all(isinstance(r["total_ms"], int) for r in records)
    assert len(said) == 2 and said[0].lower().startswith("steel")
    assert not any(ch.isdigit() for ch in "".join(said))  # no id or digit is spoken
