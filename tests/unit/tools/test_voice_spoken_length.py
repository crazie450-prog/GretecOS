"""WO-5.7 (D23, D248): the fast lane's eval set, on the fake model over the tools' test DB, through
the speech filter and lexicon the speaker uses: at least 95% of the answers are heard within 2
sentences and 40 words."""

from __future__ import annotations

from pathlib import Path

from gregtec import voicebench as vb
from gregtec.agents import evals
from gregtec.config import Config
from gregtec_voice import tts


def test_the_eval_set_is_spoken_within_d23(tool_db: Path) -> None:
    home = tool_db.parents[1]  # GOS_HOME/data/gos.sqlite
    answers, why = vb.eval_answers(Config(gos_home=home))
    assert answers is not None, why
    assert [item for item, _ in answers] == [q.id for q in evals.load()]
    assert all(text for _, text in answers)
    checks = vb.spoken_checks(answers, tts.prepare(tts.lexicon(home)))
    assert checks[0].result == vb.PASS, [c for c in checks if c.result == vb.FAIL]
