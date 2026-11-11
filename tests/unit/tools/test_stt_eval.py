"""The STT eval (WO-5.2, D183) over the fake tools DB: the set file, how a transcript resolves
(the router, then item_resolve, as pre-retrieval does), WER, the report, and ``gos voice eval``
with a fake engine. The real set runs on a harness DB in tests/integration/test_stt_harness.py."""

from __future__ import annotations

import io
import wave
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec_voice import cli, stt, stt_eval
from gregtec_voice.audio import SAMPLE_RATE, Segment, tone

STEEL = "gregtech:gt.metaitem.01@11305"
STAINLESS = "gregtech:gt.metaitem.01@11306"
EBF = "gregtech:gt.blockmachines@1000"
SET = (
    "id\tkind\tkey\ttext\n"
    f"e01\titem\t{STEEL}\tHow do I make steel?\n"
    f"e02\tmachine\t{EBF}\tWhat does the EBF do?\n"
    f"e03\titem\t{STAINLESS}\tRecipe for stainless steel.\n"
    "e04\tfluid\tfluid:oxygen\tWhat makes oxygen?\n"
)
HEARD = {  # what the fake engine "hears" for each row
    "e01": "how do I make steel",
    "e02": "What does the E B F do?",  # spaced letters, as Parakeet writes them
    "e03": "recipe for stainless",  # a word lost: GOS would ask which one
    "e04": "what makes oxygen",
}


def write_set(tmp_path: Path, text: str = SET) -> Path:
    path = tmp_path / "set.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_wav(
    path: Path, pcm: bytes, rate: int = SAMPLE_RATE, channels: int = 1
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def recordings(folder: Path, ids: list[str]) -> Path:
    for n, row_id in enumerate(ids):
        write_wav(
            folder / f"{row_id}.wav", tone(0.5 + n / 10)
        )  # lengths tell rows apart
    return folder


@dataclass
class ByLength:
    """A fake engine: hears the row whose recording has this length."""

    texts: dict[int, str]
    prompt: str = ""
    heard: list[int] = field(default_factory=list)

    def raw(self, pcm: bytes) -> str:
        self.heard.append(len(pcm))
        return self.texts[len(pcm)]


def engine(ids: list[str]) -> stt.Stt:
    """A loaded engine as the CLI gets it, spelling with the repo's glossary."""
    texts = {len(tone(0.5 + n / 10)): HEARD[i] for n, i in enumerate(ids)}
    choice = stt.Choice(stt.PARAKEET, stt.CPU, stt.PARAKEET_MODEL)
    repo = Glossary.find(None)
    return stt.Stt(choice, ByLength(texts), stt.Snapper(repo), 7, glossary=repo)


# --- the set ----------------------------------------------------------------------------------


def test_the_repo_set_has_fifty_rows_of_items_machines_and_fluids() -> None:
    rows = stt_eval.load_set()
    assert len(rows) == 50
    assert len({r.id for r in rows}) == 50 and len({r.key for r in rows}) == 50
    kinds = {r.kind for r in rows}
    assert kinds == {"item", "machine", "fluid"}
    assert all(
        r.key.startswith("gregtech:gt.blockmachines@")
        for r in rows
        if r.kind == "machine"
    )
    assert all(r.key.startswith("fluid:") for r in rows if r.kind == "fluid")
    # the words speech engines get wrong are what the set is for
    texts = " ".join(r.text for r in rows)
    for word in ("LuV", "ZPM", "UHV", "EBF", "LCR", "BBF", "HSS-G", "PTFE", "naquadah"):
        assert word in texts


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("id\tkey\ttext\n", "must start with the header id kind key text"),
        ("", "must start with the header"),
        ("id\tkind\tkey\ttext\n", "has no rows"),
        ("id\tkind\tkey\ttext\na\titem\tk\n", "3 columns, want 4"),
        (
            "id\tkind\tkey\ttext\na\tblock\tk\tt\n",
            "kind must be one of item, machine, fluid",
        ),
        ("id\tkind\tkey\ttext\na\titem\t\tt\n", "with a key and text"),
        ("id\tkind\tkey\ttext\na b\titem\tk\tt\n", "is not a new"),
        ("id\tkind\tkey\ttext\n../x\titem\tk\tt\n", "is not a new"),
        ("id\tkind\tkey\ttext\na\titem\tk\tt\na\titem\tk2\tt2\n", "'a' is not a new"),
    ],
)
def test_a_bad_set_is_refused(tmp_path: Path, text: str, problem: str) -> None:
    with pytest.raises(stt_eval.EvalError, match=problem.replace(".", r"\.")):
        stt_eval.load_set(write_set(tmp_path, text))


def test_blank_lines_in_the_set_are_skipped(tmp_path: Path) -> None:
    rows = stt_eval.load_set(write_set(tmp_path, SET + "\n\t\t\t\n"))
    assert [r.id for r in rows] == ["e01", "e02", "e03", "e04"]


# --- resolving --------------------------------------------------------------------------------


def test_resolve_follows_the_router_and_item_resolve(reg: Registry) -> None:
    assert stt_eval.resolve(reg, "How do I make steel?") == stt_eval.Resolved(
        STEEL, "resolved", "Steel Ingot"
    )
    assert stt_eval.resolve(reg, "What does the EBF do?").key == EBF
    asked = stt_eval.resolve(reg, "recipe for stainless")
    assert asked.key is None and asked.how == "asked"
    assert asked.detail.startswith("Which stainless did you mean")
    none = stt_eval.resolve(reg, "how do I make unobtainium")
    assert (none.key, none.how, none.detail) == (None, "no match", "unobtainium")
    general = stt_eval.resolve(reg, "Why does GregTech have so many tiers?")
    assert (general.key, general.how) == (None, "no item named")
    quest = stt_eval.resolve(reg, "tell me about the quest called stone age")
    assert quest.how == "no item named"  # quest words are not an item


def test_spaced_letters_resolve_once_spelt(reg: Registry) -> None:
    snap = stt.Snapper(Glossary.find(None))
    assert stt_eval.resolve(reg, "What does the E B F do?").key is None
    assert stt_eval.resolve(reg, snap("What does the E B F do?")).key == EBF


# --- word error rate --------------------------------------------------------------------------


def test_words_drop_case_and_punctuation() -> None:
    assert stt_eval.words("How do I make HSS-G? It’s cetane-boosted.") == [
        "how", "do", "i", "make", "hss", "g", "it's", "cetane", "boosted",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("ref", "hyp", "n"),
    [
        ("a b c", "a b c", 0),
        ("a b c", "a x c", 1),
        ("a b c", "a c", 1),
        ("a b c", "a b c d", 1),
        ("a b c", "", 3),
        ("", "a b", 2),
        ("how do i make luv", "how do i make love", 1),
    ],
)
def test_edits_count_word_changes(ref: str, hyp: str, n: int) -> None:
    assert stt_eval.edits(ref.split(), hyp.split()) == n


# --- a run ------------------------------------------------------------------------------------


def test_run_scores_entities_and_words(tmp_path: Path, reg: Registry) -> None:
    rows = stt_eval.load_set(write_set(tmp_path))
    ids = [r.id for r in rows]
    audio = recordings(tmp_path / "audio", ids)
    clock = iter(float(n) / 10 for n in range(100))
    done = stt_eval.run(rows, reg, engine(ids), audio, clock=lambda: next(clock))
    assert [r.right for r in done] == [True, True, False, True]
    assert all(r.scorable for r in done)
    assert done[1].transcript == "What does the EBF do?"  # spelt before it was resolved
    assert done[2].got is not None and done[2].got.how == "asked"
    assert done[2].word_edits == 1 and done[2].ref_words == 4
    assert done[0].stt_ms == 100 and done[0].audio_s == pytest.approx(0.5)
    summary = stt_eval.summarise(done)
    assert (summary.rows, summary.right, summary.transcribed) == (4, 3, 4)
    assert summary.entity_accuracy == 0.75 and not summary.met
    assert summary.word_edits == 1 and summary.ref_words == 17
    assert summary.wer == pytest.approx(1 / 17)


def test_a_full_score_meets_both_targets(tmp_path: Path, reg: Registry) -> None:
    rows = stt_eval.load_set(write_set(tmp_path))
    audio = recordings(tmp_path / "audio", [r.id for r in rows])

    class Echo:
        """Hears exactly what the row says (the order the rows are run in)."""

        name = "echo"

        def __init__(self) -> None:
            self.left: Iterator[str] = iter(r.text for r in rows)

        def transcribe(self, segment: Segment) -> str:
            return next(self.left)

    summary = stt_eval.summarise(stt_eval.run(rows, reg, Echo(), audio))
    assert summary.entity_accuracy == 1.0 and summary.wer == 0.0
    assert summary.complete and summary.met


def test_rows_without_good_audio_are_skipped_not_scored(
    tmp_path: Path, reg: Registry
) -> None:
    rows = stt_eval.load_set(write_set(tmp_path))
    audio = tmp_path / "audio"
    write_wav(audio / "e01.wav", tone(1.0), rate=44100)
    write_wav(audio / "e02.wav", tone(1.0), channels=2)
    (audio / "e03.wav").write_bytes(b"not a wav")

    class Broken:
        name = "broken"

        def transcribe(self, segment: Segment) -> str:
            raise RuntimeError("CUDA failed")

    write_wav(audio / "e04.wav", tone(1.0))
    done = stt_eval.run(rows, reg, Broken(), audio)
    assert [r.problem for r in done] == [
        "e01.wav isn't 16 kHz mono 16-bit PCM",
        "e02.wav isn't 16 kHz mono 16-bit PCM",
        done[2].problem,
        "speech-to-text failed (RuntimeError: CUDA failed)",
    ]
    assert done[2].problem is not None and done[2].problem.startswith(
        "e03.wav isn't a WAV"
    )
    summary = stt_eval.summarise(done)
    assert summary.transcribed == 0 and not summary.complete and not summary.met
    missing = stt_eval.run(rows[:1], reg, Broken(), tmp_path / "nowhere")
    assert missing[0].problem == "no audio"
    assert stt_eval.outcome(missing[0]) == "e01  SKIP  no audio"


def test_a_row_the_db_names_otherwise_is_the_sets_fault(
    tmp_path: Path, reg: Registry
) -> None:
    text = SET + "e05\titem\tgregtech:gt.metaitem.01@2032\tHow do I make dust?\n"
    rows = stt_eval.load_set(write_set(tmp_path, text))
    done = stt_eval.run(rows, reg, None, tmp_path)
    assert [r.scorable for r in done] == [True, True, True, True, False]
    assert all(r.transcript is None for r in done)  # --check: no engine, no audio
    line = stt_eval.outcome(done[4])
    assert line.startswith("e05  SET   the text resolves to asked")
    assert line.endswith("not gregtech:gt.metaitem.01@2032: fix the row for this pack")
    assert not stt_eval.summarise(done).complete


def test_report_and_tsv(tmp_path: Path, reg: Registry) -> None:
    rows = stt_eval.load_set(write_set(tmp_path))
    ids = [r.id for r in rows]
    done = stt_eval.run(rows, reg, engine(ids), recordings(tmp_path / "a", ids))
    out = io.StringIO()
    stt_eval.report(stt_eval.summarise(done), "parakeet x on cpu (int8)", out)
    text = out.getvalue()
    assert "entity accuracy: 3 of 4 (75.0%), target >= 95%" in text
    assert "word error rate: 5.9% (1 edits in 17 words), target <= 8%" in text
    assert text.rstrip().endswith("verdict: NOT MET")
    miss = stt_eval.outcome(done[2])
    assert miss.startswith("e03  MISS") and "'recipe for stainless'" in miss
    assert "-> asked: Which stainless did you mean:" in miss
    assert miss.endswith(f"want Stainless Steel Ingot ({STAINLESS})")
    path = tmp_path / "reports" / "r.tsv"
    stt_eval.write_tsv(done, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == list(stt_eval.REPORT_COLUMNS)
    assert len(lines) == 5
    e02 = dict(zip(stt_eval.REPORT_COLUMNS, lines[2].split("\t"), strict=True))
    assert (e02["right"], e02["got"], e02["transcript"]) == (
        "1",
        EBF,
        "What does the EBF do?",
    )


def test_percentile_is_nearest_rank() -> None:
    assert stt_eval.percentile([10, 20, 30, 40], 0.5) == 20
    assert stt_eval.percentile([10, 20, 30, 40], 0.9) == 40
    assert stt_eval.percentile([7], 0.9) == 7


# --- gos voice eval ---------------------------------------------------------------------------


def config(tmp_path: Path, tool_db: Path) -> Path:
    toml = tmp_path / "gos.toml"
    home = tool_db.parent.parent  # GOS_HOME/data/gos.sqlite
    toml.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    return toml


def voice(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(list(argv), out), out.getvalue()


def test_gos_voice_eval_check_tells_which_rows_resolve(
    tmp_path: Path, tool_db: Path
) -> None:
    toml = config(tmp_path, tool_db)
    code, out = voice(
        "eval", "--check", "--set", str(write_set(tmp_path)), "--config", str(toml)
    )
    assert code == 0 and "4 of 4 lines resolve to their key on" in out
    bad = write_set(
        tmp_path / "b", SET + "e05\titem\tminecraft:stick@0\tHow do I make dust?\n"
    )
    code, out = voice("eval", "--check", "--set", str(bad), "--config", str(toml))
    assert code == 1 and "e05  SET" in out and "4 of 5 lines" in out


def test_gos_voice_eval_runs_the_engine_and_writes_the_report(
    tmp_path: Path, tool_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = stt_eval.load_set(write_set(tmp_path))
    ids = [r.id for r in rows]
    audio = recordings(tmp_path / "audio", ids)
    loaded: list[tuple[str, str]] = []

    def load(engine_name: str, model: str, gos_home: Path, **kwargs: object) -> stt.Stt:
        loaded.append((engine_name, model))
        return engine(ids)

    monkeypatch.setattr(stt, "load", load)
    monkeypatch.setattr(
        stt, "cuda_problem", lambda: "no CUDA GPU (no NVIDIA GPU, or no NVIDIA driver)"
    )
    report = tmp_path / "out" / "report.tsv"
    code, out = voice(
        "eval",
        "--set", str(write_set(tmp_path)),
        "--audio", str(audio),
        "--engine", "parakeet",
        "--out", str(report),
        "--config", str(config(tmp_path, tool_db)),
    )  # fmt: skip
    assert code == 1  # 75%: not met
    assert loaded == [("parakeet", "large-v3-turbo")]
    assert "GPU for Whisper: no, no CUDA GPU" in out
    assert (
        "Speech-to-text: parakeet nemo-parakeet-tdt-0.6b-v3 on cpu (int8), loaded in 7 ms"
        in out
    )
    assert "e02  ok" in out and "e03  MISS" in out
    assert "verdict: NOT MET" in out and f"report: {report}" in out
    assert report.is_file()


def test_gos_voice_eval_needs_a_db_and_recordings(
    tmp_path: Path, tool_db: Path
) -> None:
    empty = tmp_path / "empty.toml"
    empty.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "h").as_posix()}"\n', encoding="utf-8"
    )
    code, out = voice("eval", "--config", str(empty))
    assert code == 2 and "No DB at" in out and "gos db build" in out
    code, out = voice(
        "eval",
        "--audio",
        str(tmp_path / "none"),
        "--config",
        str(config(tmp_path, tool_db)),
    )
    assert code == 2 and "No recordings in" in out and "gos voice record" in out
    code, out = voice(
        "eval", "--limit", "0", "--config", str(config(tmp_path, tool_db))
    )
    assert code == 2 and "--limit must be at least 1" in out
    code, out = voice(
        "eval", "--set", str(tmp_path / "missing.tsv"), "--config", str(empty)
    )
    assert code == 2 and "Can't use the set" in out


def test_gos_voice_eval_says_when_no_engine_starts(
    tmp_path: Path, tool_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def load(*args: object, **kwargs: object) -> stt.Stt:
        raise stt.SttError(
            "parakeet ...: onnx-asr isn't installed here (uv sync --group stt)"
        )

    monkeypatch.setattr(stt, "load", load)
    monkeypatch.setattr(stt, "cuda_problem", lambda: "no CUDA GPU")
    audio = recordings(tmp_path / "audio", ["e01"])
    code, out = voice(
        "eval", "--audio", str(audio), "--config", str(config(tmp_path, tool_db))
    )
    assert code == 1
    assert (
        "No speech-to-text engine could start: parakeet ...: onnx-asr isn't installed"
        in out
    )


def test_a_bad_model_name_is_refused_by_the_parser(
    tmp_path: Path, tool_db: Path
) -> None:
    with pytest.raises(SystemExit) as refused:
        voice(
            "eval", "--model", "C:/models", "--config", str(config(tmp_path, tool_db))
        )
    assert refused.value.code == 2
