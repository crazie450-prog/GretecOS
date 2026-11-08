"""Speech-to-text (plan 8 WO-5.2, 6.4, 7.15, [D§2.2], QA:V2; D182, D183).

:func:`create` is the voice client's hook (D179): it returns a transcriber whose
``transcribe(segment)`` turns one closed segment (16 kHz mono int16, its 300 ms pre-roll
included) into one line of text, "" when nothing was said.

**Segment mode, no streaming.** The talk key (and the wake engine's end of speech, WO-5.4) gives
each segment's ends, so a segment is transcribed whole once it closes; voice activity detection
only trims it ([D§2.2]). Silero VAD runs first for both engines: a segment with no speech in it
(a tap that only stopped speech, a false wake) is "" without a decode, so Whisper never makes
up "Thank you." for a silent one.

**Engines** (``[voice] stt_engine``; models download once into ``GOS_HOME/models``):

=============  ================================================================================
auto           Whisper on the GPU when it works here, else Parakeet on the CPU (the default)
whisper-gpu    faster-whisper ``[voice] stt_model`` on CUDA, ``int8_float16``; nothing else
whisper-cpu    faster-whisper ``stt_model`` on the CPU, ``int8``
parakeet       Parakeet TDT 0.6B v3 through onnx-asr on the CPU, ``int8`` (no prompt biasing)
=============  ================================================================================

The GPU "works here" when CTranslate2 finds a CUDA device, cuBLAS 12 loads (the NVIDIA driver
alone isn't enough; the CUDA 12 runtime libraries are a PC step, docs/runbooks/stt.md) and the
model loads and decodes a warm-up; any failure is the next engine's turn, and the log says why.
Whisper decodes with ``language="en"`` and ``beam_size=1``. CPU engines use 4 threads, so a
transcription burst never takes every core from GTNH.

**The glossary** (``glossary.tsv``, plan 6.4: the entries ``item_resolve`` expands, from the
live vault or else vault-template) does two things:

1. **Hotwords** (Whisper only): its forms, joined by commas, go in faster-whisper's
   ``hotwords`` (the prompt slot before the audio), cut at :data:`PROMPT_TOKENS` tokens of the
   model's own tokenizer. Order: terms with a pronunciation in ``say`` (words speech engines
   don't know: tiers, EBF, naquadah), abbreviations, those terms' other aliases, the other
   terms, then the rest.
2. **Spelling** (both engines, :class:`Snapper`): a run of up to four words whose letters and
   digits are a glossary form's is written as the glossary writes it: "E-B-F", "E B F" and
   "e.b.f." become "EBF", "ass line" "assline", "HS-SG" and "HSSG" "HSS-G". Only spacing, case
   and punctuation change, so a word never becomes another word; and one plain word changes
   only where the glossary adds punctuation or spaces to it ("me" stays "me", not "ME").

The text is data (plan 0.1 principle 8): it is sent to the core as Clayton's question and
nothing else; this module only writes it down.
"""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from gregtec.agents.tools.glossary import Glossary
from gregtec_voice.audio import SAMPLE_RATE, Segment
from gregtec_voice.settings import VoiceSettings

log = logging.getLogger(__name__)

AUTO, WHISPER_GPU, WHISPER_CPU, PARAKEET = (
    "auto",
    "whisper-gpu",
    "whisper-cpu",
    "parakeet",
)
WHISPER, CUDA, CPU = "whisper", "cuda", "cpu"
COMPUTE = {CUDA: "int8_float16", CPU: "int8"}  # [D§2.2]
LANGUAGE, BEAM = "en", 1
CPU_THREADS = 4
# Whisper's prompt slot holds 223 tokens (half its 448-token window, less one); faster-whisper
# would cut a longer one mid-word, so it is cut here, between forms.
PROMPT_TOKENS = 220
PROMPT_KINDS = frozenset({"tier", "machine", "material", "item", "line", "mod"})
SNAP_WORDS = 4
WARM_UP_S = 0.5
MIN_SPEECH_S = 0.1  # shorter than this, a segment can't hold a word
# Models pinned to the revision WO-5.2 measured (Hugging Face repo, commit). Other faster-whisper
# names (small.en, ...) and "org/name" ids download at their latest revision.
WHISPER_REPOS = {
    "large-v3-turbo": (
        "dropbox-dash/faster-whisper-large-v3-turbo",  # was mobiuslabsgmbh/...; MIT
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    ),
    "distil-large-v3": (
        "Systran/faster-distil-whisper-large-v3",  # MIT
        "c3058b475261292e64a0412df1d2681c06260fab",
    ),
}
WHISPER_REPOS["turbo"] = WHISPER_REPOS["large-v3-turbo"]
PARAKEET_MODEL = (
    "nemo-parakeet-tdt-0.6b-v3"  # CC-BY-4.0 (NVIDIA), ONNX export by istupakov
)
PARAKEET_REPO = (
    "istupakov/parakeet-tdt-0.6b-v3-onnx",
    "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce",
)
PARAKEET_FILES = (
    "config.json",
    "vocab.txt",
    "encoder-model.int8.onnx",
    "decoder_joint-model.int8.onnx",
)
# What CTranslate2 loads for CUDA (it links the CUDA runtime itself and needs no cuDNN).
CUBLAS = (
    ("cublas64_12.dll", "cublasLt64_12.dll")
    if sys.platform == "win32"
    else ("libcublas.so.12", "libcublasLt.so.12")
)


class SttError(RuntimeError):
    """No engine could start (the message says why for each), or one failed on a segment."""


# --- the glossary: hotwords and spelling ------------------------------------------------------


def _abbreviation(form: str) -> bool:
    return sum(c.isupper() for c in form) >= 2


def prompt_forms(glossary: Glossary) -> list[str]:
    """The glossary's forms in the order the prompt takes them (see the module docstring).
    A form spelt like an earlier one but for spacing, case or hyphens ("tungsten steel",
    "clean room") is left out: the prompt is for spelling, and :class:`Snapper` fixes the
    spacing. A tier's aliases ("ludicrous voltage", "tier 6") are plain words and stay out."""
    ranks: list[list[str]] = [[], [], [], [], []]
    for entry in glossary.entries:
        if entry.kind not in PROMPT_KINDS:
            continue
        aliases = () if entry.kind == "tier" else entry.aliases
        if entry.say:
            ranks[0].append(entry.term)
        else:
            ranks[1 if _abbreviation(entry.term) else 3].append(entry.term)
        for alias in aliases:
            if _abbreviation(alias):
                ranks[1].append(alias)
            else:
                ranks[2 if entry.say else 4].append(alias)
    seen: set[str] = set()
    out = []
    for form in (f for rank in ranks for f in rank):
        if (key := compact(form)) and key not in seen:
            seen.add(key)
            out.append(form)
    return out


def hotwords(
    forms: Sequence[str], count: Callable[[str], int], budget: int = PROMPT_TOKENS
) -> str:
    """``forms`` joined by ", ", as many as fit in ``budget`` tokens (``count`` measures)."""
    text = ""
    for form in forms:
        longer = f"{text}, {form}" if text else form
        if count(longer) > budget:
            break
        text = longer
    return text


def compact(text: str) -> str:
    """Letters and digits only, lower case: how :class:`Snapper` compares spellings."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _edges(token: str) -> tuple[str, str, str]:
    """``token`` as leading punctuation, the word, trailing punctuation."""
    start, end = 0, len(token)
    while start < end and not token[start].isalnum():
        start += 1
    while end > start and not token[end - 1].isalnum():
        end -= 1
    return token[:start], token[start:end], token[end:]


class Snapper:
    """Writes glossary forms as the glossary does (the module docstring's point 2)."""

    def __init__(self, glossary: Glossary) -> None:
        self.forms: dict[str, str] = {}
        # File order: a term comes before the aliases spelt like it.
        for entry in glossary.entries:
            for form in entry.forms:
                if len(key := compact(form)) >= 2:
                    self.forms.setdefault(key, form)

    def __call__(self, text: str) -> str:
        tokens = text.split()
        out = []
        i = 0
        while i < len(tokens):
            for n in range(min(SNAP_WORDS, len(tokens) - i), 0, -1):
                lead, word, trail = _edges(tokens[i])
                if n > 1:
                    _, last, trail = _edges(tokens[i + n - 1])
                    word = " ".join([word, *tokens[i + 1 : i + n - 1], last])
                form = self.forms.get(compact(word))
                # One plain word changes only when the glossary writes it with more than
                # letters and digits ("HSSG" is "HSS-G"): "me" stays "me", not "ME".
                if form is None or (n == 1 and word.isalnum() and form.isalnum()):
                    continue
                # "e.b.f." ends in its own dot, not a full stop
                if "." in word and trail.startswith("."):
                    trail = trail[1:]
                out.append(f"{lead}{form}{trail}")
                i += n
                break
            else:
                out.append(tokens[i])
                i += 1
        return " ".join(out)


# --- engines ----------------------------------------------------------------------------------


class Backend(Protocol):
    """A loaded engine: 16 kHz mono int16 PCM in, its raw text out."""

    prompt: str  # the hotwords it decodes with ("" when it takes none)

    def raw(self, pcm: bytes) -> str: ...


def floats(pcm: bytes) -> Any:  # noqa: ANN401 - numpy, which only the engines need
    """int16 PCM as the float32 samples in [-1, 1) both engines take."""
    import numpy as np

    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def _speech(audio: Any) -> Any:  # noqa: ANN401 - numpy float32 arrays
    """``audio`` with the silence around and between its speech cut out (Silero VAD, the copy
    faster-whisper ships); an empty array when there is no speech."""
    import numpy as np
    from faster_whisper.vad import collect_chunks, get_speech_timestamps

    chunks = get_speech_timestamps(audio)
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(collect_chunks(audio, chunks)[0])


def whisper_files(model: str, folder: Path, note: Callable[[str], None]) -> str:
    """The model's folder in ``folder`` (Hugging Face's cache layout), downloaded once."""
    from faster_whisper.utils import download_model

    if Path(model).is_absolute() and Path(model).is_dir():  # a converted model's folder
        return model
    repo, revision = WHISPER_REPOS.get(model, (model, None))
    cache = str(folder)
    try:
        return str(
            download_model(
                repo, cache_dir=cache, local_files_only=True, revision=revision
            )
        )
    except (
        OSError,
        ValueError,
    ):  # not downloaded yet: huggingface_hub's not-found errors
        note(f"Downloading the Whisper model {model} to {folder} (once)...")
        return str(download_model(repo, cache_dir=cache, revision=revision))


def parakeet_files(folder: Path, note: Callable[[str], None]) -> Path:
    """Parakeet's int8 files in ``folder``, downloaded once (a cut-off download resumes)."""
    if all((folder / name).is_file() for name in PARAKEET_FILES):
        return folder
    from huggingface_hub import snapshot_download

    note(f"Downloading {PARAKEET_MODEL} (int8, about 670 MB) to {folder} (once)...")
    repo, revision = PARAKEET_REPO
    snapshot_download(
        repo, revision=revision, local_dir=folder, allow_patterns=list(PARAKEET_FILES)
    )
    return folder


class WhisperBackend:
    """faster-whisper (MIT), CTranslate2 underneath."""

    def __init__(self, model: Any, prompt: str) -> None:  # noqa: ANN401 - a WhisperModel
        self.model = model
        self.prompt = prompt

    @classmethod
    def load(
        cls,
        model: str,
        device: str,
        models: Path,
        glossary: Glossary,
        note: Callable[[str], None],
    ) -> WhisperBackend:
        from faster_whisper import WhisperModel

        path = whisper_files(model, models / "whisper", note)
        loaded = WhisperModel(
            path, device=device, compute_type=COMPUTE[device], cpu_threads=CPU_THREADS
        )
        tokenizer = loaded.hf_tokenizer

        def count(text: str) -> int:  # as faster-whisper encodes its hotwords
            return len(tokenizer.encode(" " + text, add_special_tokens=False).ids)

        backend = cls(loaded, hotwords(prompt_forms(glossary), count))
        backend.warm_up()
        return backend

    def warm_up(self) -> None:
        """Run the encoder and a decoder step once: CUDA kernels load now (or fail now)."""
        import numpy as np

        silence = np.zeros(round(WARM_UP_S * SAMPLE_RATE), dtype=np.float32)
        segments, _ = self.model.transcribe(
            silence, language=LANGUAGE, beam_size=BEAM, max_new_tokens=2
        )
        list(segments)

    def raw(self, pcm: bytes) -> str:
        segments, _ = self.model.transcribe(
            floats(pcm),
            language=LANGUAGE,
            beam_size=BEAM,
            vad_filter=True,
            without_timestamps=True,
            condition_on_previous_text=False,
            hotwords=self.prompt or None,
        )
        return " ".join(s.text.strip() for s in segments)


class ParakeetBackend:
    """Parakeet TDT 0.6B v3 through onnx-asr (MIT) on ONNX Runtime's CPU provider."""

    prompt = ""

    def __init__(self, model: Any) -> None:  # noqa: ANN401 - an onnx-asr adapter
        self.model = model

    @classmethod
    def load(cls, models: Path, note: Callable[[str], None]) -> ParakeetBackend:
        import onnx_asr
        import onnxruntime

        path = parakeet_files(models / "parakeet" / PARAKEET_MODEL, note)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = CPU_THREADS
        options.inter_op_num_threads = 1
        model = onnx_asr.load_model(
            PARAKEET_MODEL,
            path,
            quantization="int8",
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        backend = cls(model)
        backend.model.recognize(floats(bytes(2 * SAMPLE_RATE)), sample_rate=SAMPLE_RATE)
        return backend

    def raw(self, pcm: bytes) -> str:
        speech = _speech(floats(pcm))
        if not len(speech):
            return ""
        return str(self.model.recognize(speech, sample_rate=SAMPLE_RATE))


# --- choosing and loading ---------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    engine: str  # whisper or parakeet
    device: str  # cuda or cpu
    model: str

    @property
    def label(self) -> str:
        return f"{self.engine} {self.model} on {self.device} ({COMPUTE[self.device]})"


def choices(engine: str, model: str) -> list[Choice]:
    """What ``[voice] stt_engine`` tries, in order."""
    gpu, cpu = Choice(WHISPER, CUDA, model), Choice(WHISPER, CPU, model)
    parakeet = Choice(PARAKEET, CPU, PARAKEET_MODEL)
    found = {
        AUTO: [gpu, parakeet],
        WHISPER_GPU: [gpu],
        WHISPER_CPU: [cpu],
        PARAKEET: [parakeet],
    }
    if engine not in found:
        raise SttError(f"unknown stt_engine {engine!r}")
    return found[engine]


def _loadable(name: str) -> bool:
    try:
        if (
            sys.platform == "win32"
        ):  # the search order CTranslate2's own LoadLibrary uses (PATH)
            ctypes.CDLL(name, winmode=0)
        else:
            ctypes.CDLL(name)
    except OSError:
        return False
    return True


def cuda_problem() -> str | None:
    """Why Whisper can't run on the GPU here, or None when it may (the warm-up decides)."""
    try:
        import ctranslate2

        devices = ctranslate2.get_cuda_device_count()
    except Exception as exc:  # noqa: BLE001 - not installed, or a broken CUDA driver
        return f"CTranslate2 can't look for a GPU ({type(exc).__name__}: {exc})"
    if devices < 1:
        return "no CUDA GPU (no NVIDIA GPU, or no NVIDIA driver)"
    missing = [name for name in CUBLAS if not _loadable(name)]
    if missing:
        return (
            f"{' and '.join(missing)} not found: install NVIDIA's CUDA 12 cuBLAS "
            "(docs/runbooks/stt.md)"
        )
    return None


Loader = Callable[[Choice, Path, Glossary, Callable[[str], None]], Backend]


def _installed(module: str, package: str) -> None:
    if importlib.util.find_spec(module) is None:
        raise SttError(
            f"{package} isn't installed here (uv sync --group stt; gos deploy installs it)"
        )


def _load_whisper(
    choice: Choice, models: Path, glossary: Glossary, note: Callable[[str], None]
) -> Backend:
    _installed("faster_whisper", "faster-whisper")
    return WhisperBackend.load(choice.model, choice.device, models, glossary, note)


def _load_parakeet(
    choice: Choice, models: Path, glossary: Glossary, note: Callable[[str], None]
) -> Backend:
    _installed("onnx_asr", "onnx-asr")
    return ParakeetBackend.load(models, note)


LOADERS: Mapping[str, Loader] = {WHISPER: _load_whisper, PARAKEET: _load_parakeet}


@dataclass
class Stt:
    """A loaded engine as the voice client's transcriber (``gregtec_voice.hooks.Transcriber``)."""

    choice: Choice
    backend: Backend
    snap: Snapper
    load_ms: int
    skipped: list[str] = field(
        default_factory=list
    )  # engines tried first, and why each failed
    glossary: Glossary = field(default_factory=Glossary)

    @property
    def name(self) -> str:
        return self.choice.engine

    @property
    def label(self) -> str:
        return self.choice.label

    def text(self, raw: str) -> str:
        """The engine's text as the core gets it: one line, glossary spellings."""
        return self.snap(" ".join(raw.split()))

    def transcribe(self, segment: Segment) -> str:
        if segment.seconds < MIN_SPEECH_S:  # nothing to decode
            return ""
        return self.text(self.backend.raw(segment.pcm))


def load(
    engine: str,
    model: str,
    gos_home: Path,
    *,
    glossary: Glossary | None = None,
    note: Callable[[str], None] | None = None,
    loaders: Mapping[str, Loader] = LOADERS,
    gpu_problem: Callable[[], str | None] = cuda_problem,
) -> Stt:
    """The first engine of ``engine``'s :func:`choices` that loads (models in
    ``gos_home/models``, the glossary from ``gos_home/vault`` unless given). Raises
    :class:`SttError` naming every failure when none does."""
    if glossary is None:
        glossary = Glossary.find(gos_home / "vault")
        if glossary.problem:
            log.warning(
                "the vault's glossary.tsv isn't used",
                extra={"problem": glossary.problem},
            )
    tell = note or (lambda message: log.info(message))
    snap = Snapper(glossary)
    skipped: list[str] = []
    for choice in choices(engine, model):
        if choice.device == CUDA and (problem := gpu_problem()):
            skipped.append(f"{choice.label}: {problem}")
            continue
        started = time.monotonic()
        try:
            backend = loaders[choice.engine](
                choice, gos_home / "models", glossary, tell
            )
        except Exception as exc:  # noqa: BLE001 - any engine failure: the next one's turn
            skipped.append(f"{choice.label}: {type(exc).__name__}: {exc}")
            continue
        stt = Stt(
            choice,
            backend,
            snap,
            round((time.monotonic() - started) * 1000),
            skipped,
            glossary,
        )
        if skipped:
            log.warning(
                "speech-to-text fell back", extra={"skipped": "; ".join(skipped)}
            )
        log.info(
            "speech-to-text ready",
            extra={
                "engine": stt.label,
                "load_ms": stt.load_ms,
                "prompt_chars": len(backend.prompt),
            },
        )
        return stt
    raise SttError("; ".join(skipped))


class Unavailable:
    """No engine could start: every segment fails with the reason, so each turn says
    "Speech-to-text failed." and the hotkeys keep working (plan 0.1 principle 9)."""

    name = "unavailable"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def transcribe(self, segment: Segment) -> str:
        raise SttError(f"no speech-to-text engine could start: {self.reason}")


def create(settings: VoiceSettings) -> Stt | Unavailable:
    """The voice client's transcriber (``gregtec_voice.hooks``, D179)."""
    try:
        return load(settings.stt_engine, settings.stt_model, settings.gos_home)
    except SttError as exc:
        log.error("no speech-to-text engine could start", extra={"error": str(exc)})
        return Unavailable(str(exc))
