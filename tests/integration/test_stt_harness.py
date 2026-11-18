"""WO-5.2 on a DB built from a real GregtecLink harness run (D183).

- Every line of tests/evals/stt_utterances.tsv resolves to its key: the set is valid on the dev
  runtime it was written over (``gos voice eval --check``, the CLI end to end).
- A transcript that is exactly the line scores 100% entity accuracy and 0% WER: the scoring
  is right on real data.
- Transcripts written the way speech engines write GTNH words (spaced or dotted letters, words
  run together or split) resolve once the repo glossary spells them (``stt.Snapper``), and most
  of them don't without it.

The engines themselves (models, audio) are the slow test's (test_stt_models.py) and Clayton's
PC run (docs/runbooks/stt.md). As the other harness tests: only with GOS_REQUIRE_HARNESS_RUN=1
after the client harness; nothing is written outside tmp_path.
"""

from __future__ import annotations

import io
import os
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.db import access
from gregtec.ingest import collect
from gregtec_voice import cli, stt, stt_eval
from gregtec_voice.audio import SAMPLE_RATE, Segment

REPO = Path(__file__).resolve().parents[2]
# How speech engines wrote these lines in the WO-5.2 smoke runs (Whisper and Parakeet on
# synthetic speech), or would: letters spaced or dotted, words run together or split.
HEARD = {
    "s01": "How do I make an L V motor?",
    "s06": "recipe for the L.U.V. motor",
    "s07": "How do I make a Z P M field generator?",
    "s09": "How do I make a U H V sensor?",
    "s10": "What does the E-B-F do?",
    "s11": "What does the L C R do?",
    "s15": "What does the B.B.F. do?",
    "s17": "What does the ass line do?",
    "s18": "What does the clean room do?",
    "s25": "How is cupro nickel made?",
    "s28": "How do I make tungsten steel?",
    "s30": "How do I make HSSG?",
    "s31": "What use is P T F E?",
    "s36": "How do I make T P V alloy?",
    "s50": "How do I make an M E controller?",
}


@pytest.fixture(scope="module")
def db(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("stt-harness")
    built_home(game, home)
    return access.db_path(home)


@pytest.fixture(scope="module")
def reg(db: Path) -> Registry:
    return build_registry(db, Glossary.find(None))  # vault-template's glossary


def test_every_line_of_the_set_resolves_to_its_key(reg: Registry) -> None:
    rows = stt_eval.run(stt_eval.load_set(), reg, None, Path())
    assert len(rows) == 50
    assert [stt_eval.outcome(r) for r in rows if not r.scorable] == []


def test_gos_voice_eval_check_on_the_harness_db(db: Path, tmp_path: Path) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{db.parent.parent.as_posix()}"\n', encoding="utf-8"
    )
    out = io.StringIO()
    assert cli.main(["eval", "--check", "--config", str(toml)], out) == 0
    assert "50 of 50 lines resolve to their key on" in out.getvalue()


def test_the_lines_themselves_score_full_marks(reg: Registry, tmp_path: Path) -> None:
    rows = stt_eval.load_set()
    for row in rows:
        with wave.open(str(tmp_path / f"{row.id}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(bytes(SAMPLE_RATE))  # half a second of silence

    class Echo:
        name = "echo"

        def __init__(self) -> None:
            self.lines: Iterator[str] = iter(r.text for r in rows)

        def transcribe(self, segment: Segment) -> str:
            return next(self.lines)

    summary = stt_eval.summarise(stt_eval.run(rows, reg, Echo(), tmp_path))
    assert (summary.rows, summary.right, summary.word_edits) == (50, 50, 0)
    assert summary.met


def test_engine_spellings_resolve_once_the_glossary_spells_them(reg: Registry) -> None:
    rows = {r.id: r for r in stt_eval.load_set()}
    glossary = Glossary.find(None)
    spell = stt.Stt(
        stt.Choice(stt.PARAKEET, stt.CPU, stt.PARAKEET_MODEL),
        backend=None,  # type: ignore[arg-type]  # only text() is used
        snap=stt.Snapper(glossary),
        load_ms=0,
        glossary=glossary,
    )
    wrong = {}
    unspelt = 0
    for row_id, heard in HEARD.items():
        found = stt_eval.resolve(reg, spell.text(heard))
        if found.key != rows[row_id].key:
            wrong[row_id] = (spell.text(heard), found)
        unspelt += stt_eval.resolve(reg, heard).key == rows[row_id].key
    assert wrong == {}
    assert unspelt <= 3, "most of these miss without the glossary's spellings"
