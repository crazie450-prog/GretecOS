"""WO-5.2: ``gos voice stt`` and ``gos voice record`` with fake engines and the Linux stand-ins
for the microphone and key hook (``gos voice eval`` needs a DB: tests/unit/tools)."""

from __future__ import annotations

import io
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gregtec import applog
from gregtec.agents.tools.glossary import Glossary
from gregtec_voice import audio, cli, ptt, stt, stt_eval
from gregtec_voice.audio import SAMPLE_RATE, Segment, tone

F13 = 0x7C
SET = (
    "id\tkind\tkey\ttext\n"
    "s01\titem\tgregtech:gt.metaitem.01@32600\tHow do I make an LV motor?\n"
    "s02\tmachine\tgregtech:gt.blockmachines@1000\tWhat does the EBF do?\n"
)


def setup(tmp_path: Path, voice: str = "") -> Path:
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n[voice]\n{voice}\n',
        encoding="utf-8",
    )
    return toml


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(list(argv), out), out.getvalue()


def write_wav(path: Path, pcm: bytes) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return path


@dataclass
class Heard:
    text: str = "what does the E B F do"
    prompt: str = "LV, EBF"
    calls: int = 0

    def raw(self, pcm: bytes) -> str:
        self.calls += 1
        return self.text


def fake_load(
    monkeypatch: pytest.MonkeyPatch, backend: Heard | None = None
) -> list[tuple[str, str, Path]]:
    loads: list[tuple[str, str, Path]] = []

    def load(
        engine: str,
        model: str,
        gos_home: Path,
        *,
        note: Callable[[str], None],
        **kw: object,
    ) -> stt.Stt:
        loads.append((engine, model, gos_home))
        note(
            f"Downloading the Whisper model {model} to {gos_home / 'models'} (once)..."
        )
        glossary = Glossary.find(None)
        return stt.Stt(
            stt.Choice(stt.WHISPER, stt.CPU, model),
            backend or Heard(),
            stt.Snapper(glossary),
            1234,
            ["whisper x on cuda (int8_float16): no CUDA GPU"],
            glossary,
        )

    monkeypatch.setattr(stt, "load", load)
    monkeypatch.setattr(stt, "cuda_problem", lambda: "no CUDA GPU")
    return loads


# --- gos voice stt ----------------------------------------------------------------------------


def test_stt_loads_the_engine_and_transcribes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = fake_load(monkeypatch)
    wav = write_wav(tmp_path / "q.wav", tone(1.0))
    toml = setup(tmp_path, voice='stt_engine = "whisper-cpu"\nstt_model = "small.en"')
    code, out = run("stt", str(wav), "--config", str(toml))
    assert code == 0
    assert loads == [("whisper-cpu", "small.en", tmp_path / "home")]
    assert "GPU for Whisper: no, no CUDA GPU" in out
    assert (
        "Downloading the Whisper model small.en" in out
    )  # the engine's notes are shown
    assert "  not used: whisper x on cuda (int8_float16): no CUDA GPU" in out
    assert "Speech-to-text: whisper small.en on cpu (int8), loaded in 1234 ms" in out
    assert "  prompt: 2 glossary forms from " in out
    assert "q.wav: 'what does the EBF do' (" in out


def test_stt_engine_and_model_flags_win_over_gos_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = fake_load(monkeypatch)
    toml = setup(tmp_path, voice='stt_engine = "whisper-cpu"')
    code, _ = run(
        "stt", "--engine", "parakeet", "--model", "base.en", "--config", str(toml)
    )
    assert code == 0 and loads[0][:2] == ("parakeet", "base.en")
    with pytest.raises(SystemExit):
        run("stt", "--engine", "whisper", "--config", str(toml))


def test_stt_reports_a_bad_wav_and_a_failed_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_load(monkeypatch)
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFF....")
    code, out = run("stt", str(bad), "--config", str(setup(tmp_path)))
    assert code == 1 and "bad.wav isn't a WAV file" in out

    def broken(*args: object, **kwargs: object) -> stt.Stt:
        raise stt.SttError("parakeet ...: no model")

    monkeypatch.setattr(stt, "load", broken)
    code, out = run("stt", "--config", str(setup(tmp_path)))
    assert (
        code == 1
        and "No speech-to-text engine could start: parakeet ...: no model" in out
    )


# --- gos voice record -------------------------------------------------------------------------


def segment(seconds: float, amplitude: int = 8000) -> Segment:
    pcm = tone(seconds, amplitude=amplitude)
    return Segment(pcm, "key", 0.0, seconds, seconds, 0.3)


def test_recording_saves_each_line_and_asks_again_for_a_slip(tmp_path: Path) -> None:
    rows = stt_eval.load_set(write_set(tmp_path))
    out = io.StringIO()
    rec = stt_eval.Recording(rows, tmp_path / "rec", out)
    rec.prompt()
    assert rec.transcribe(segment(0.7)) == ""  # too short: a slip of the key
    assert rec.transcribe(segment(1.5, amplitude=10)) == ""  # too quiet
    assert rec.index == 0 and not (tmp_path / "rec").exists()
    assert rec.transcribe(segment(1.5)) == ""
    assert rec.transcribe(segment(1.2)) == ""
    assert rec.done.is_set() and rec.index == 2
    assert rec.transcribe(segment(1.2)) == ""  # after the last line: nothing more
    assert sorted(p.name for p in (tmp_path / "rec").iterdir()) == [
        "s01.wav",
        "s02.wav",
    ]
    with wave.open(str(tmp_path / "rec" / "s01.wav"), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 24000)
    text = out.getvalue()
    assert "[1/2] s01: How do I make an LV motor?" in text
    assert text.count("too short or too quiet") == 2
    assert "[2/2] s02: What does the EBF do?" in text
    assert "All 2 recorded in" in text


def write_set(tmp_path: Path) -> Path:
    path = tmp_path / "set.tsv"
    path.write_text(SET, encoding="utf-8")
    return path


def test_to_record_continues_or_takes_the_ids_asked(tmp_path: Path) -> None:
    rows = stt_eval.load_set(write_set(tmp_path))
    folder = tmp_path / "rec"
    assert [r.id for r in stt_eval.to_record(rows, folder)] == ["s01", "s02"]
    folder.mkdir()
    write_wav(folder / "s01.wav", tone(1.0))
    assert [r.id for r in stt_eval.to_record(rows, folder)] == ["s02"]
    assert [r.id for r in stt_eval.to_record(rows, folder, ["s01"])] == ["s01"]
    with pytest.raises(stt_eval.EvalError, match="not in the set: s99"):
        stt_eval.to_record(rows, folder, ["s99"])


@dataclass
class Parts:
    """The Windows parts ``gos voice record`` gets, faked, and kept for the test to drive."""

    source: audio.FakeSource = field(default_factory=audio.FakeSource)
    state: ptt.FakeKeyState = field(default_factory=ptt.FakeKeyState)
    hook: ptt.FakeHook | None = None

    def __call__(
        self,
        voice: Any,  # noqa: ANN401 - a VoiceClient
        device: str,
    ) -> tuple[audio.AudioPipe, ptt.FakeHook, ptt.Watchdog]:
        key_filter = ptt.KeyFilter(voice.watched, voice.post)
        self.hook = ptt.FakeHook(key_filter)
        dog = ptt.Watchdog(self.hook, self.state, key_filter)
        return audio.AudioPipe(self.source, voice.recorder), self.hook, dog


def wait(cond: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_record_saves_one_talk_key_segment_per_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parts = Parts()
    monkeypatch.setattr(cli, "_windows_only", lambda out: False)
    monkeypatch.setattr(cli, "_windows_parts", parts)
    monkeypatch.setattr(applog, "setup", lambda process, logs_dir: tmp_path / "x.log")
    # the wake word as the only trigger in gos.toml: record still uses the talk key
    toml = setup(tmp_path, voice='primary_trigger = "wake"\nbackup_trigger = "none"')
    folder = tmp_path / "rec"
    result: dict[str, Any] = {}

    def main() -> None:
        result["code"], result["out"] = run(
            "record",
            "--dir",
            str(folder),
            "--set",
            str(write_set(tmp_path)),
            "--config",
            str(toml),
        )

    runner = threading.Thread(target=main)
    runner.start()
    try:
        wait(lambda: parts.hook is not None and parts.hook.alive())
        wait(lambda: parts.source._on_block is not None)
        pcm = tone(0.02)
        for name in ("s01.wav", "s02.wav"):
            for _ in range(20):
                parts.source.push(pcm)
            assert parts.hook is not None
            parts.state.down.add(F13)
            parts.hook.press(F13)
            for _ in range(60):  # held for 1.2 s
                parts.source.push(pcm)
                time.sleep(0.005)
            parts.state.down.discard(F13)
            parts.hook.release(F13)
            deadline = time.monotonic() + 10
            while not (
                folder / name
            ).is_file():  # the post-roll needs blocks after the key
                assert time.monotonic() < deadline
                parts.source.push(pcm)
                time.sleep(0.01)
    finally:
        runner.join(20)
    assert not runner.is_alive()
    assert result["code"] == 0
    assert "2 lines to record into" in result["out"] and "hold F13" in result["out"]
    assert "All 2 recorded" in result["out"] and "2 of 2 recorded." in result["out"]
    code, out = run(
        "record",
        "--dir",
        str(folder),
        "--set",
        str(write_set(tmp_path)),
        "--config",
        str(toml),
    )
    assert code == 0 and "Every line has a recording in" in out


def test_eval_help_shows_a_single_percent(capsys: pytest.CaptureFixture[str]) -> None:
    """QA W4: argparse doesn't %-format a description, so '%%' there is shown as is."""
    with pytest.raises(SystemExit):
        cli.main(["eval", "--help"])
    text = capsys.readouterr().out
    assert "95%" in text and "8%" in text and "%%" not in text
