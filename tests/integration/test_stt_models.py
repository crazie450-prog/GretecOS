"""WO-5.2's slow tests: the real speech-to-text engines with downloaded models (D182, D183).

Skipped unless a model is present; nothing is downloaded here:

- ``GOS_STT_TEST_HOME``: a GOS_HOME whose ``models/`` holds the engine's model
  (``gos voice stt --config <its gos.toml>`` downloads it there);
- ``GOS_STT_TEST_ENGINE`` (default ``whisper-cpu``) and ``GOS_STT_TEST_MODEL`` (default
  ``base.en``) pick the engine;
- with ``GOS_STT_TEST_AUDIO`` (a folder of the eval set's WAVs, ``<id>.wav``) and a DB in that
  GOS_HOME (``data/gos.sqlite``), ``gos voice eval`` runs on them and must reach
  ``GOS_STT_TEST_MIN_ACCURACY`` (default 0.8; the acceptance's 95% is Clayton's voice with
  large-v3-turbo on his GPU, docs/runbooks/stt.md).

Run with ``-s`` to see the eval's report (the WO-5.2 smoke test on synthetic speech).
"""

from __future__ import annotations

import csv
import io
import os
import time
from pathlib import Path

import pytest

from gregtec_voice import cli, stt, stt_eval
from gregtec_voice.audio import SAMPLE_RATE, Segment

pytestmark = pytest.mark.slow

ENGINE = os.environ.get("GOS_STT_TEST_ENGINE", "whisper-cpu")
MODEL = os.environ.get("GOS_STT_TEST_MODEL", "base.en")


@pytest.fixture(scope="module")
def home() -> Path:
    found = os.environ.get("GOS_STT_TEST_HOME")
    if not found:
        pytest.skip(
            "set GOS_STT_TEST_HOME to a GOS_HOME with a downloaded speech model"
        )
    module = "onnx_asr" if ENGINE == stt.PARAKEET else "faster_whisper"
    pytest.importorskip(module, reason="the stt dependency group isn't installed")
    choice = stt.choices(ENGINE, MODEL)[-1]
    if not stt.downloaded(choice, Path(found) / "models"):
        pytest.skip(f"{choice.label}: its model isn't in {found}/models")
    return Path(found)


@pytest.fixture(scope="module")
def engine(home: Path) -> stt.Stt:
    return stt.load(ENGINE, MODEL, home, note=print)


def test_a_real_engine_loads_and_hears_silence_as_nothing(engine: stt.Stt) -> None:
    print(
        f"\n{engine.label}: loaded in {engine.load_ms} ms; prompt: {engine.backend.prompt!r}"
    )
    silence = bytes(2 * SAMPLE_RATE)  # one second
    started = time.perf_counter()
    said = engine.transcribe(Segment(silence, "key", 0.0, 1.0, 1.0, 0.3))
    print(
        f"one second of silence: {said!r} in {(time.perf_counter() - started) * 1000:.0f} ms"
    )
    assert said == ""  # the VAD finds no speech: no decode, no made-up "Thank you."
    if engine.name == stt.WHISPER:
        prompt = engine.backend.prompt
        assert prompt.startswith("ULV, LV, MV, HV")
        model = engine.backend.model  # type: ignore[attr-defined]
        tokens = model.hf_tokenizer.encode(" " + prompt, add_special_tokens=False).ids
        assert 0 < len(tokens) <= stt.PROMPT_TOKENS


def test_the_eval_on_recordings_with_a_real_engine(home: Path, tmp_path: Path) -> None:
    audio = os.environ.get("GOS_STT_TEST_AUDIO")
    if not audio or not (home / "data" / "gos.sqlite").is_file():
        pytest.skip("set GOS_STT_TEST_AUDIO and build a DB in GOS_STT_TEST_HOME")
    toml = tmp_path / "gos.toml"
    toml.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    report = tmp_path / "report.tsv"
    out = io.StringIO()
    argv = ["eval", "--audio", audio, "--engine", ENGINE, "--model", MODEL]
    cli.main([*argv, "--out", str(report), "--config", str(toml)], out)
    print("\n" + out.getvalue())
    with open(report, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    assert len(rows) == len(stt_eval.load_set())
    assert all(row["transcript"] or not row["problem"] for row in rows)
    assert not [row["id"] for row in rows if row["problem"]], (
        "every line was transcribed"
    )
    right = sum(row["right"] == "1" for row in rows) / len(rows)
    assert right >= float(os.environ.get("GOS_STT_TEST_MIN_ACCURACY", "0.8"))
