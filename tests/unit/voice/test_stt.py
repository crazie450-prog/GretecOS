"""Speech-to-text (WO-5.2, D182): the glossary prompt, spellings, engine choice and fallback,
with fake engines (no model, no GPU)."""

from __future__ import annotations

import logging
import random
import string
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gregtec.agents.tools.glossary import Entry, Glossary
from gregtec_voice import hooks, stt
from gregtec_voice.audio import Segment, tone
from gregtec_voice.settings import VoiceSettings

GLOSSARY_TSV = (
    "term\taliases\tkind\tref\tsay\n"
    "LV\tlow voltage, tier 1\ttier\ttier:1\tL V\n"
    "LuV\tludicrous voltage, tier 6\ttier\ttier:6\tL U V\n"
    "EBF\telectric blast furnace, blast furnace\tmachine\t\tE B F\n"
    "PA\tprocessing array\tremoved\t\tP A\n"
    "assembly line\tassline, AL\tmachine\t\t\n"
    "cleanroom\tclean room\tmachine\t\t\n"
    "naquadah\tnaq\tmaterial\t\tnah-kwa-dah\n"
    "HSS-G\thigh speed steel G\tmaterial\t\tH S S G\n"
    "stainless steel\tSS\tmaterial\t\t\n"
    "AE2\tapplied energistics\tmod\t\tA E two\n"
    "ME\tME system\tmachine\t\tM E\n"
)


@pytest.fixture
def glossary(tmp_path: Path) -> Glossary:
    path = tmp_path / "glossary.tsv"
    path.write_text(GLOSSARY_TSV, encoding="utf-8")
    found = Glossary.load(path)
    assert found.problem is None
    return found


def segment(seconds: float = 1.0) -> Segment:
    pcm = tone(seconds)
    return Segment(pcm, "key", 0.0, seconds, seconds, 0.0)


# --- the prompt -------------------------------------------------------------------------------


def test_the_glossary_reads_the_say_column(glossary: Glossary) -> None:
    by_term = {e.term: e for e in glossary.entries}
    assert by_term["LuV"].say == "L U V"
    assert by_term["assembly line"].say == ""
    assert Entry("x", (), "item").say == ""  # older callers build entries without it


def test_prompt_forms_rank_pronounced_terms_then_abbreviations(
    glossary: Glossary,
) -> None:
    forms = stt.prompt_forms(glossary)
    # 1. terms with a pronunciation, in file order (tiers first in the real glossary)
    assert forms[:7] == ["LV", "LuV", "EBF", "naquadah", "HSS-G", "AE2", "ME"]
    # 2. other abbreviations: terms and aliases with two or more capitals
    assert forms[7:10] == ["AL", "SS", "ME system"]
    # 3. the pronounced terms' other aliases, 4. the other terms, 5. their other aliases
    assert forms[10:] == [
        "electric blast furnace",
        "blast furnace",
        "naq",
        "high speed steel G",
        "applied energistics",
        "assembly line",
        "cleanroom",
        "stainless steel",
        "assline",
    ]
    # tier aliases are plain words; a removed thing is never biased toward
    assert "ludicrous voltage" not in forms and "tier 1" not in forms
    assert "PA" not in forms and "processing array" not in forms


def test_prompt_forms_drop_spacing_variants(glossary: Glossary) -> None:
    forms = stt.prompt_forms(glossary)
    assert "cleanroom" in forms and "clean room" not in forms
    assert len({stt.compact(f) for f in forms}) == len(forms)


def test_hotwords_stop_at_the_budget_between_forms() -> None:
    def count(text: str) -> int:
        return len(text.split())

    forms = ["LV", "LuV", "electric blast furnace", "naquadah"]
    assert stt.hotwords(forms, count, budget=4) == "LV, LuV"  # a form is whole or out
    assert stt.hotwords(forms, count, budget=5) == "LV, LuV, electric blast furnace"
    everything = "LV, LuV, electric blast furnace, naquadah"
    assert stt.hotwords(forms, count, budget=6) == everything
    assert stt.hotwords(forms, count, budget=100) == everything
    assert stt.hotwords(forms, count, budget=0) == ""


def test_the_repo_glossary_leads_with_the_tiers_and_fits() -> None:
    forms = stt.prompt_forms(Glossary.find(None))  # vault-template/glossary.tsv
    assert forms[:4] == ["ULV", "LV", "MV", "HV"]
    assert "EBF" in forms and "naquadah" in forms and "PA" not in forms
    text = stt.hotwords(forms, lambda t: len(t) // 3, stt.PROMPT_TOKENS)
    assert 0 < len(text) // 3 <= stt.PROMPT_TOKENS
    assert text.startswith("ULV, LV, MV, HV, ")


# --- spellings --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heard", "written"),
    [
        ("What does the E-B-F do?", "What does the EBF do?"),
        ("What does the E B F do?", "What does the EBF do?"),
        ("what does the e.b.f. do", "what does the EBF do"),
        ("what does the E.B.F.?", "what does the EBF?"),
        ("tell me about E.B.F.", "tell me about EBF"),
        ("How do I make an L V motor?", "How do I make an LV motor?"),
        ("recipe for the L.U.V. motor", "recipe for the LuV motor"),
        ("How do I make HS-SG?", "How do I make HSS-G?"),
        (
            "How do I make HSSG?",
            "How do I make HSS-G?",
        ),  # one word, but HSS-G has a hyphen
        ("How do I make H.S.S.-G?", "How do I make HSS-G?"),
        ("What does the ass line do?", "What does the assline do?"),
        ("Tell me about the clean room.", "Tell me about the cleanroom."),
        ("How do I make an M E controller?", "How do I make an ME controller?"),
        ("Is A E 2 installed?", "Is AE2 installed?"),
        # one plain word is never changed, even when a glossary form is that word
        ("Tell me about it", "Tell me about it"),
        ("show me the recipes", "show me the recipes"),
        ("what is ss", "what is ss"),
        ("", ""),
    ],
)
def test_snapper_writes_glossary_forms(
    glossary: Glossary, heard: str, written: str
) -> None:
    assert stt.Snapper(glossary)(heard) == written


def test_snapper_never_changes_letters(glossary: Glossary) -> None:
    snap = stt.Snapper(glossary)
    rng = random.Random(52)
    pool = [*"ELBVFSM", "the", "ass", "line", "clean", "room", "-", ".", "E-B-F", "naq"]
    for _ in range(500):
        text = " ".join(rng.choice(pool) for _ in range(rng.randint(1, 8)))
        snapped = snap(text)
        assert stt.compact(snapped) == stt.compact(text), text


def test_snapper_prefers_the_term_over_an_alias_spelt_alike(tmp_path: Path) -> None:
    path = tmp_path / "glossary.tsv"
    path.write_text(
        "term\taliases\tkind\nTPV-Alloy\tTPV alloy\tmaterial\n", encoding="utf-8"
    )
    assert stt.Snapper(Glossary.load(path))("make T P V alloy") == "make TPV-Alloy"


def test_compact_keeps_letters_and_digits_only() -> None:
    assert stt.compact("H.S.S.-G!") == "hssg"
    assert stt.compact("AE 2") == "ae2"
    assert stt.compact("Ünï-1 x") == "ünï1x"
    assert stt.compact(string.punctuation + " \t") == ""


# --- engines ----------------------------------------------------------------------------------


@dataclass
class FakeBackend:
    text: str = "what does the E B F do"
    prompt: str = "LV, EBF"
    heard: list[bytes] = field(default_factory=list)

    def raw(self, pcm: bytes) -> str:
        self.heard.append(pcm)
        return self.text


@dataclass
class Loaders:
    """Fake engine loaders: each choice's engine fails or answers as told."""

    fail: dict[str, Exception] = field(default_factory=dict)
    tried: list[stt.Choice] = field(default_factory=list)

    def __call__(self) -> dict[str, stt.Loader]:
        def loader(engine: str) -> stt.Loader:
            def load(
                choice: stt.Choice,
                models: Path,
                glossary: Glossary,
                note: Callable[[str], None],
            ) -> stt.Backend:
                self.tried.append(choice)
                if engine in self.fail:
                    raise self.fail[engine]
                note(f"loading {choice.label} from {models}")
                return FakeBackend()

            return load

        return {stt.WHISPER: loader(stt.WHISPER), stt.PARAKEET: loader(stt.PARAKEET)}


def test_choices_per_engine_setting() -> None:
    def labels(engine: str) -> list[str]:
        return [c.label for c in stt.choices(engine, "large-v3-turbo")]

    assert labels("auto") == [
        "whisper large-v3-turbo on cuda (int8_float16)",
        "parakeet nemo-parakeet-tdt-0.6b-v3 on cpu (int8)",
    ]
    assert labels("whisper-gpu") == ["whisper large-v3-turbo on cuda (int8_float16)"]
    assert labels("whisper-cpu") == ["whisper large-v3-turbo on cpu (int8)"]
    assert labels("parakeet") == ["parakeet nemo-parakeet-tdt-0.6b-v3 on cpu (int8)"]
    with pytest.raises(stt.SttError):
        stt.choices("whisper", "large-v3-turbo")


def test_auto_uses_whisper_on_a_working_gpu(tmp_path: Path, glossary: Glossary) -> None:
    loaders = Loaders()
    notes: list[str] = []
    found = stt.load(
        "auto",
        "large-v3-turbo",
        tmp_path,
        glossary=glossary,
        note=notes.append,
        loaders=loaders(),
        gpu_problem=lambda: None,
    )
    assert found.label == "whisper large-v3-turbo on cuda (int8_float16)"
    assert found.name == "whisper" and found.skipped == []
    assert notes == [f"loading {found.label} from {tmp_path / 'models'}"]


def test_auto_falls_back_to_parakeet_without_a_gpu(
    tmp_path: Path, glossary: Glossary
) -> None:
    loaders = Loaders()
    found = stt.load(
        "auto",
        "large-v3-turbo",
        tmp_path,
        glossary=glossary,
        loaders=loaders(),
        gpu_problem=lambda: "no CUDA GPU",
    )
    assert found.name == "parakeet"
    assert found.skipped == [
        "whisper large-v3-turbo on cuda (int8_float16): no CUDA GPU"
    ]
    assert [c.engine for c in loaders.tried] == [
        "parakeet"
    ]  # the GPU wasn't even tried


def test_auto_falls_back_when_the_gpu_model_fails(
    tmp_path: Path, glossary: Glossary
) -> None:
    loaders = Loaders(fail={stt.WHISPER: RuntimeError("CUBLAS_STATUS_ARCH_MISMATCH")})
    found = stt.load(
        "auto",
        "large-v3-turbo",
        tmp_path,
        glossary=glossary,
        loaders=loaders(),
        gpu_problem=lambda: None,
    )
    assert found.name == "parakeet"
    assert "RuntimeError: CUBLAS_STATUS_ARCH_MISMATCH" in found.skipped[0]


def test_an_explicit_engine_never_falls_back(
    tmp_path: Path, glossary: Glossary
) -> None:
    loaders = Loaders()
    with pytest.raises(stt.SttError, match="no CUDA GPU"):
        stt.load(
            "whisper-gpu",
            "large-v3-turbo",
            tmp_path,
            glossary=glossary,
            loaders=loaders(),
            gpu_problem=lambda: "no CUDA GPU",
        )
    assert loaders.tried == []
    loaders = Loaders(fail={stt.PARAKEET: OSError("no model")})
    with pytest.raises(stt.SttError, match="parakeet .*: OSError: no model"):
        stt.load("parakeet", "x", tmp_path, glossary=glossary, loaders=loaders())


def test_whisper_cpu_needs_no_gpu(tmp_path: Path, glossary: Glossary) -> None:
    def gpu() -> str:
        raise AssertionError("whisper-cpu must not probe the GPU")

    found = stt.load(
        "whisper-cpu",
        "small.en",
        tmp_path,
        glossary=glossary,
        loaders=Loaders()(),
        gpu_problem=gpu,
    )
    assert found.label == "whisper small.en on cpu (int8)"


def test_every_failure_is_named(tmp_path: Path, glossary: Glossary) -> None:
    loaders = Loaders(fail={stt.PARAKEET: ValueError("bad file")})
    with pytest.raises(stt.SttError) as caught:
        stt.load(
            "auto",
            "large-v3-turbo",
            tmp_path,
            glossary=glossary,
            loaders=loaders(),
            gpu_problem=lambda: "no CUDA GPU",
        )
    assert str(caught.value) == (
        "whisper large-v3-turbo on cuda (int8_float16): no CUDA GPU; "
        "parakeet nemo-parakeet-tdt-0.6b-v3 on cpu (int8): ValueError: bad file"
    )


def test_the_live_vault_glossary_is_used(tmp_path: Path) -> None:
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "glossary.tsv").write_text(GLOSSARY_TSV, encoding="utf-8")
    found = stt.load(
        "parakeet", "x", tmp_path, loaders=Loaders()(), gpu_problem=lambda: None
    )
    assert found.glossary.path == tmp_path / "vault" / "glossary.tsv"
    assert found.text("how is H S S G made") == "how is HSS-G made"


def test_transcribe_tidies_and_spells(glossary: Glossary) -> None:
    backend = FakeBackend(text="  what does\nthe  E B F do ")
    found = stt.Stt(
        stt.Choice(stt.WHISPER, stt.CPU, "base.en"), backend, stt.Snapper(glossary), 5
    )
    one = segment(1.0)
    assert found.transcribe(one) == "what does the EBF do"
    assert backend.heard == [one.pcm]


def test_floats_scale_int16_to_unit_range() -> None:
    np = pytest.importorskip("numpy")  # the engines' own dependency (the stt group)
    pcm = np.array([0, 16384, -32768, 32767], dtype="<i2").tobytes()
    out = stt.floats(pcm)
    assert out.dtype == np.float32
    assert out.tolist() == [0.0, 0.5, -1.0, 32767 / 32768]


def test_a_segment_too_short_for_a_word_is_nothing(glossary: Glossary) -> None:
    backend = FakeBackend()
    found = stt.Stt(
        stt.Choice(stt.WHISPER, stt.CPU, "base.en"), backend, stt.Snapper(glossary), 5
    )
    assert found.transcribe(segment(0.05)) == ""
    assert backend.heard == []


def test_create_degrades_to_an_engine_that_fails_each_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    def load(*args: object, **kwargs: object) -> stt.Stt:
        raise stt.SttError("parakeet ...: onnx-asr isn't installed here")

    monkeypatch.setattr(stt, "load", load)
    with caplog.at_level(logging.ERROR):
        found = stt.create(VoiceSettings(gos_home=tmp_path))
    assert isinstance(found, stt.Unavailable) and found.name == "unavailable"
    assert "no speech-to-text engine could start" in caplog.text
    with pytest.raises(stt.SttError, match="onnx-asr isn't installed"):
        found.transcribe(segment())


def test_the_hook_finds_this_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, str, Path]] = []

    def load(engine: str, model: str, gos_home: Path, **kwargs: object) -> str:
        seen.append((engine, model, gos_home))
        return "the engine"

    monkeypatch.setattr(stt, "load", load)
    settings = VoiceSettings(
        stt_engine="parakeet", stt_model="small.en", gos_home=tmp_path
    )
    assert hooks.transcriber(settings) == "the engine"
    assert seen == [("parakeet", "small.en", tmp_path)]


def test_missing_engines_say_how_to_install_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stt.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(
        stt.SttError, match=r"onnx-asr isn't installed here \(uv sync --group stt"
    ):
        stt._load_parakeet(stt.choices("parakeet", "x")[0], Path(), Glossary(), print)
    with pytest.raises(stt.SttError, match="faster-whisper isn't installed"):
        stt._load_whisper(stt.choices("whisper-cpu", "x")[0], Path(), Glossary(), print)


def test_cuda_problem_names_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    devices = [0]
    fake = types.SimpleNamespace(get_cuda_device_count=lambda: devices[0])
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    assert stt.cuda_problem() == "no CUDA GPU (no NVIDIA GPU, or no NVIDIA driver)"
    devices[0] = 1
    monkeypatch.setattr(stt, "_loadable", lambda name: name == stt.CUBLAS[0])
    problem = stt.cuda_problem()
    assert (
        problem is not None
        and stt.CUBLAS[1] in problem
        and stt.CUBLAS[0] not in problem
    )
    assert "docs/runbooks/stt.md" in problem
    monkeypatch.setattr(stt, "_loadable", lambda name: True)
    assert stt.cuda_problem() is None
    monkeypatch.setitem(sys.modules, "ctranslate2", None)  # not installed
    problem = stt.cuda_problem()
    assert problem is not None and problem.startswith(
        "CTranslate2 can't look for a GPU"
    )


def test_a_library_that_is_not_there_is_not_loadable() -> None:
    assert not stt._loadable("libgos-no-such-library.so.99")
