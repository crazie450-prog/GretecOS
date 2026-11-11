"""WO-5.3: the text-to-speech bench, the round-trip check and their commands (D204)."""

from __future__ import annotations

import csv
import io
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec_voice import cli, stt, tts, tts_bench, tts_cli
from gregtec_voice.audio import Segment
from gregtec_voice.playback import FakeOutput
from gregtec_voice.settings import VoiceSettings
from gregtec_voice.tts import CPU, ELEVENLABS, GPU, KOKORO, PIPER, Choice
from gregtec_voice.tts_bench import Phrase, Result


class Slow:
    """An engine whose first audio comes ``delay`` s after it is asked."""

    def __init__(self, name: str, delay: float) -> None:
        self.name = name
        self.label = f"slow {name}"
        self.rate = 1000
        self.delay = delay
        self.said: list[str] = []

    def stream(self, text: str) -> Iterator[bytes]:
        time.sleep(self.delay)
        self.said.append(text)
        yield b"\x01\x00" * 50


def loader(delays: dict[str, float]) -> tts.Loader:
    def load(choice: Choice, voice: VoiceSettings, note: Callable[[str], None]) -> Any:  # noqa: ANN401
        if choice.engine not in delays:
            raise tts.TtsError(f"{choice.engine} can't start here")
        return Slow(choice.engine, delays[choice.engine])

    return load


def test_percentile_nearest_rank() -> None:
    assert tts_bench.percentile([], 0.5) is None
    assert tts_bench.percentile([5], 0.9) == 5
    assert tts_bench.percentile([1, 2, 3, 4], 0.5) == 2
    assert tts_bench.percentile(list(range(1, 11)), 0.9) == 9
    assert tts_bench.percentile([30, 10, 20], 0.5) == 20


def test_bench_one_times_the_first_audio_through_the_speaker(tmp_path: Path) -> None:
    voice = VoiceSettings(gos_home=tmp_path)
    result = tts_bench.bench_one(
        Choice(PIPER),
        voice,
        FakeOutput(),
        tts.lexicon(tmp_path),
        runs=3,
        loader=loader({PIPER: 0.05}),
    )
    assert result.label == "slow piper" and result.problem == ""
    assert len(result.first_ms) == 3 and all(40 <= ms < 1000 for ms in result.first_ms)
    assert result.meets and result.vram_mb is None


def test_bench_one_reports_an_engine_that_cannot_start(tmp_path: Path) -> None:
    result = tts_bench.bench_one(
        Choice(KOKORO),
        VoiceSettings(gos_home=tmp_path),
        FakeOutput(),
        tts.lexicon(tmp_path),
        loader=loader({}),
    )
    assert result.first_ms == [] and "kokoro can't start here" in result.problem


def test_bench_one_logs_the_vram_a_gpu_engine_adds(tmp_path: Path) -> None:
    readings = iter([1000, 1450])
    result = tts_bench.bench_one(
        Choice(KOKORO, GPU),
        VoiceSettings(gos_home=tmp_path),
        FakeOutput(),
        tts.lexicon(tmp_path),
        runs=1,
        loader=loader({KOKORO: 0.0}),
        vram=lambda: next(readings),
    )
    assert result.vram_mb == 450


def test_vram_is_none_without_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts_bench.shutil, "which", lambda name: None)
    assert tts_bench.vram_used_mb() is None


def test_the_pick_is_the_cheapest_that_meets_the_target() -> None:
    fast_kokoro = Result(Choice(KOKORO, GPU), "k", first_ms=[200, 250])
    slow_piper = Result(Choice(PIPER), "p", first_ms=[500, 600])
    cloud = Result(Choice(ELEVENLABS), "e", first_ms=[150])
    assert tts_bench.pick([cloud, fast_kokoro, slow_piper]) is fast_kokoro
    ok_piper = Result(Choice(PIPER), "p", first_ms=[390, 410, 300])
    assert tts_bench.pick([fast_kokoro, ok_piper]) is ok_piper
    assert tts_bench.pick([slow_piper, Result(Choice(KOKORO))]) is None


def test_the_cost_order_puts_the_cpu_first_and_the_cloud_last() -> None:
    assert tts_bench.COST_ORDER[0] == Choice(PIPER, CPU)
    assert tts_bench.COST_ORDER[-1] == Choice(ELEVENLABS)
    gpu = [c for c in tts_bench.COST_ORDER if c.device == GPU]
    assert all(
        tts_bench.COST_ORDER.index(g) > tts_bench.COST_ORDER.index(Choice(KOKORO))
        for g in gpu
    )


def test_report_and_tsv(tmp_path: Path) -> None:
    results = [
        Result(Choice(PIPER), "piper x", 1500, [300, 350, 380]),
        Result(Choice(KOKORO), "kokoro x", 4000, [3000, 3200]),
        Result(Choice(KOKORO, GPU), problem="no GPU provider"),
    ]
    out = io.StringIO()
    assert tts_bench.report(results, out) is results[0]
    text = out.getvalue()
    assert "piper x: P50 350 ms" in text and "meets" in text
    assert "kokoro on gpu: not measured: no GPU provider" in text
    assert 'tts = "piper"' in text and 'tts_device = "cpu"' in text
    path = tmp_path / "r" / "bench.tsv"
    tts_bench.write_tsv(results, path)
    rows = list(
        csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t")
    )
    assert [r["meets"] for r in rows] == ["yes", "no", "no"]
    assert rows[0]["p50_ms"] == "350" and rows[2]["problem"] == "no GPU provider"


def test_report_says_when_nothing_meets_the_target() -> None:
    out = io.StringIO()
    assert tts_bench.report([Result(Choice(KOKORO), "k", 1, [900])], out) is None
    assert "No engine measured here meets the target." in out.getvalue()


# --- the round trip ---------------------------------------------------------------------------


def test_heard_right_compares_letters_with_numbers_in_words() -> None:
    row = Phrase(
        "n01",
        "number",
        "x",
        "It runs at nineteen-twenty E U per tick.",
        ("1920 EU per tick",),
    )
    assert tts_bench.heard_right(row, "It runs at 1920 EU per tick.")
    assert tts_bench.heard_right(row, "it runs at nineteen twenty, EU per tick")
    assert not tts_bench.heard_right(row, "It runs at 1820 EU per tick.")
    term = Phrase("t19", "term", "x", "You need nah-kwa-dah for this.", ("naquadah",))
    assert tts_bench.heard_right(term, "You need Naquadah for this.")
    assert not tts_bench.heard_right(term, "You need not quad out for this.")


def test_load_phrases_refuses_a_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "p.tsv"
    bad.write_text("id\tkind\n", encoding="utf-8")
    with pytest.raises(tts_bench.BenchError, match="header"):
        tts_bench.load_phrases(bad)
    bad.write_text("id\tkind\ttext\tsay\theard\nx\tword\ta\tb\t\n", encoding="utf-8")
    with pytest.raises(tts_bench.BenchError, match="p.tsv:2"):
        tts_bench.load_phrases(bad)


class Heard:
    """A transcriber that hears what the engine was told to say, letter-spelt words joined."""

    name = "heard"

    def __init__(self, engine: Slow) -> None:
        self.engine = engine

    def transcribe(self, segment: Segment) -> str:
        return " ".join(self.engine.said[-2:]).replace("L U V", "LuV")


def test_check_synthesises_and_transcribes_each_phrase(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    engine = Slow(PIPER, 0)
    phrases = [
        Phrase(
            "t07",
            "term",
            "You need LuV for this.",
            "You need L U V for this.",
            ("LuV",),
        ),
        Phrase("t99", "term", "Say EBF.", "Say E B F.", ("nothing like it",)),
    ]
    rows = tts_bench.check(engine, Heard(engine), tts.lexicon(tmp_path), phrases)
    assert [r.ok for r in rows] == [True, True]  # "Say E B F." is heard as said
    out = io.StringIO()
    assert tts_bench.check_report(rows, out)
    assert "terms: 2 of 2 heard right" in out.getvalue()


def test_to_16k_keeps_the_duration() -> None:
    np = pytest.importorskip("numpy")
    for rate in (22050, 24000, 16000):
        got = tts_bench.to_16k(np.zeros(rate, dtype=np.float32), rate)
        assert abs(len(got) - 16000) <= 2


# --- the commands -----------------------------------------------------------------------------


def config(tmp_path: Path) -> Path:
    path = tmp_path / "gos.toml"
    home = str(tmp_path).replace("\\", "/")
    path.write_text(f'[paths]\ngos_home = "{home}"\n', encoding="utf-8")
    return path


def test_bench_targets_skip_what_cannot_run_here() -> None:
    wanted, skipped = tts_cli.bench_targets(
        None, gpu_problem=lambda: "no GPU provider", key_problem=lambda: "no key"
    )
    assert wanted == [Choice(PIPER), Choice(KOKORO)]
    assert {r.choice: r.problem for r in skipped} == {
        Choice(PIPER, GPU): "no GPU provider",
        Choice(KOKORO, GPU): "no GPU provider",
        Choice(ELEVENLABS): "no key",
    }
    wanted, skipped = tts_cli.bench_targets(["kokoro-gpu", "elevenlabs"])
    assert wanted == [Choice(KOKORO, GPU), Choice(ELEVENLABS)] and skipped == []


def test_gos_voice_tts_bench_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tts, "load_engine", loader({PIPER: 0.01, KOKORO: 0.6}))
    out = io.StringIO()
    report = tmp_path / "bench.tsv"
    code = cli.main(
        [
            "tts-bench",
            "--config",
            str(config(tmp_path)),
            "--engine",
            "piper-cpu",
            "--engine",
            "kokoro-cpu",
            "--runs",
            "2",
            "--fake-output",
            "--out",
            str(report),
        ],
        out,
    )
    text = out.getvalue()
    assert code == 0, text
    assert (
        "Latency pick: slow piper" in text
        and "slow kokoro: P50" in text
        and "misses" in text
    )
    assert report.is_file()


def test_gos_voice_tts_bench_fails_when_nothing_meets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tts, "load_engine", loader({}))
    out = io.StringIO()
    code = cli.main(
        [
            "tts-bench",
            "--config",
            str(config(tmp_path)),
            "--engine",
            "piper-cpu",
            "--fake-output",
            "--out",
            str(tmp_path / "b.tsv"),
        ],
        out,
    )
    assert code == tts_cli.EXIT_PROBLEM and "not measured" in out.getvalue()


def test_gos_voice_say_prints_and_speaks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = Slow(PIPER, 0)
    monkeypatch.setattr(tts, "load_engine", lambda choice, voice, note: engine)
    out = io.StringIO()
    code = cli.main(
        [
            "say",
            "--config",
            str(config(tmp_path)),
            "--save",
            str(tmp_path / "wav"),
            "Your EBF runs at 1920 EU/t (r:abc/3f9a0c2b1e7d).",
        ],
        out,
    )
    assert code == 0, out.getvalue()
    assert "Your E B F runs at nineteen-twenty E U per tick." in out.getvalue()
    assert engine.said == ["Your E B F runs at nineteen-twenty E U per tick."]
    assert (tmp_path / "wav" / "001.wav").is_file()


def test_gos_voice_say_needs_a_text(tmp_path: Path) -> None:
    out = io.StringIO()
    assert (
        cli.main(["say", "--config", str(config(tmp_path))], out) == tts_cli.EXIT_SETUP
    )


def test_gos_voice_tts_check_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("numpy")
    engine = Slow(PIPER, 0)
    monkeypatch.setattr(tts, "load_engine", lambda choice, voice, note: engine)

    class Loaded:
        label = "fake stt"

        def transcribe(self, segment: Segment) -> str:
            return engine.said[-1]

    monkeypatch.setattr(stt, "load", lambda *args, **kwargs: Loaded())
    out = io.StringIO()
    code = cli.main(["tts-check", "--config", str(config(tmp_path))], out)
    text = out.getvalue()
    assert "terms: 30 of 30 heard right" in text and "numbers: 10 of 10" in text
    assert code == 0, text


def test_the_output_is_fake_off_windows_or_when_asked(tmp_path: Path) -> None:
    voice = VoiceSettings(gos_home=tmp_path)
    assert isinstance(tts_cli.output_for(voice, fake=True), FakeOutput)
    if sys.platform != "win32":
        assert isinstance(tts_cli.output_for(voice), FakeOutput)
