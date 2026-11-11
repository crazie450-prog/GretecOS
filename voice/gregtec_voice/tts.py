"""Text-to-speech (plan 8 WO-5.3, 7.15, [D§2.3], [D§2.7], PM14; D203-D205).

:func:`create` is the voice client's hook (D179): it returns a speaker whose ``speak(text, ...)``
returns at once and says ``text`` on the output device, and whose ``stop()`` is barge-in.

**What is said** is the reply through the speech filter and the pronunciation lexicon
(``gregtec_voice.speech_filter``, ``gregtec_voice.lexicon``; plan 7.15): no citation or id, D23's
length, numbers and units in words, glossary pronunciations.

**Sentence streaming** ([D§2.7]): the filtered text is cut into chunks, each sentence one chunk,
and the first sentence cut again at its first comma once it has :data:`FIRST_CHUNK_WORDS`
words, so the first audio waits only for a short chunk. One thread synthesises the chunks in
order, :data:`AHEAD` at most ahead of a second thread that plays them, so the next chunk is
ready when the one before it ends.

**Engines** (``[voice] tts``, ``tts_device``; models download once into ``GOS_HOME/models`` at a
pinned SHA-256):

==============  ================================================================================
kokoro          Kokoro-82M v1.0 (Apache-2.0 weights) through kokoro-onnx (MIT) on ONNX Runtime,
                on the CPU or (``tts_device = "gpu"``) ONNX Runtime's CUDA or DirectML provider
piper           a Piper voice (``en_US-joe-medium``, CC0 data) run by GOS's own code on ONNX
                Runtime from its ``.onnx.json`` (the GPL-3.0 piper-tts package isn't used, D203)
elevenlabs      ElevenLabs Flash v2.5 over HTTPS (optional, paid; its key in the keyring, ``gos
                key set elevenlabs``); text goes to ElevenLabs, so it is never the default
==============  ================================================================================

Kokoro and Piper both phonemise with espeak-ng through phonemizer (both GPL-3.0, installed from
PyPI with kokoro-onnx, never vendored: D203). An engine that can't start gives way to the next
in :func:`choices` (the same engine on the CPU, then the other local one), and a chunk an engine
fails on is said by the next one, so GOS keeps talking (plan 0.1 principle 9).
``gos voice tts-bench`` times each engine and picks the cheapest that meets PM14's share
(first audio <= 400 ms after the first sentence; ``gregtec_voice.tts_bench``).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import queue
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from gregtec.agents.tools.glossary import Glossary
from gregtec_voice import speech_filter, wake
from gregtec_voice.lexicon import Lexicon
from gregtec_voice.playback import BLOCK_S, SAMPLE_BYTES, Output, Stream, default_output
from gregtec_voice.settings import VoiceSettings

log = logging.getLogger(__name__)

KOKORO, PIPER, ELEVENLABS = "kokoro", "piper", "elevenlabs"
CPU, GPU = "cpu", "gpu"
ENGINES = (KOKORO, PIPER, ELEVENLABS)
DEVICES = (CPU, GPU)
CPU_THREADS = 4  # as speech-to-text: a burst never takes every core from GTNH
FIRST_CHUNK_WORDS = 6  # [D§2.7]: the first chunk ends at a clause once it has 6 words
AHEAD = 2  # chunks synthesised ahead of the one playing
TARGET_MS = 400  # plan 8 WO-5.3: first audio <= 400 ms after the first sentence
GPU_PROVIDERS = ("CUDAExecutionProvider", "DmlExecutionProvider")

# Kokoro v1.0 (hexgrad/Kokoro-82M, Apache-2.0) as kokoro-onnx's release exports it.
KOKORO_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
)
KOKORO_FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
KOKORO_VOICE = "am_michael"  # American English, male (personas choose theirs: WO-5.6)
# A persona's voice entries (WO-5.6, D207): its prefix -> the engine it is for.
VOICE_PREFIXES = {"kokoro": "kokoro", "eleven": "elevenlabs", "piper": "piper"}
KOKORO_RATE = 24_000
# Piper voices (rhasspy/piper-voices, MIT repo; each voice's data licence in its MODEL_CARD),
# pinned to a commit. joe: CC0 data.
PIPER_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    "cf910fb59f1d79ab3b625a63993a108751c6c25d/en/"
)
PIPER_VOICES = {
    "en_US-joe-medium": (
        "en_US/joe/medium/",
        "abf4b998e34045cbd00eaf050de2de620f81ded6eb8b85a24b022a1c2b3f08d2",
        "7b5f0293c0bf5bda8e377be1696da84af798befeef31038e43beefc01fe858da",
    ),
}
PIPER_VOICE = "en_US-joe-medium"
# ElevenLabs (D203): Flash v2.5, raw 24 kHz PCM streamed back. The voice is a premade one's id
# (Adam) until Clayton picks his (a PC step).
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"
ELEVENLABS_MODEL = "eleven_flash_v2_5"
ELEVENLABS_VOICE = "pNInz6obpgDQGcFmaJgB"
ELEVENLABS_RATE = 24_000
ELEVENLABS_KEY = "elevenlabs"  # gregtec.llm.keys name: gregtec-os/elevenlabs
HTTP_TIMEOUT_S = 10.0
HTTP_CHUNK = 4_800  # 100 ms of 24 kHz int16


class TtsError(RuntimeError):
    """An engine can't start or failed on a chunk (the message says why, never a key)."""


# --- chunks -----------------------------------------------------------------------------------


def chunks(text: str) -> list[str]:
    """Filtered text as the chunks synthesised one by one: each sentence, the first one cut
    at its first comma (or semicolon) that comes after :data:`FIRST_CHUNK_WORDS` words and
    leaves at least three words after it."""
    out = speech_filter.sentences(text)
    if out:
        words = out[0].split()
        for i in range(FIRST_CHUNK_WORDS - 1, len(words) - 3):
            if words[i].endswith((",", ";")):
                out[:1] = [" ".join(words[: i + 1]), " ".join(words[i + 1 :])]
                break
    return out


def pcm16(samples: Any) -> bytes:  # noqa: ANN401 - numpy float32 samples in [-1, 1]
    import numpy as np

    clipped = np.clip(np.asarray(samples, dtype=np.float32).ravel(), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


# --- engines ----------------------------------------------------------------------------------


class Engine(Protocol):
    name: str  # kokoro, piper, elevenlabs
    label: str  # what it is, for logs and the bench
    rate: int

    def stream(self, text: str) -> Iterator[bytes]:
        """``text`` (one chunk) as int16 mono PCM at :attr:`rate`, in one or more pieces."""
        ...


def providers(device: str) -> list[str]:
    """ONNX Runtime's providers for ``device``. Raises :class:`TtsError` when ``gpu`` has no
    GPU provider here (the GPU run is a PC step: docs/runbooks/tts.md)."""
    if device == CPU:
        return ["CPUExecutionProvider"]
    import onnxruntime

    preload = getattr(onnxruntime, "preload_dlls", None)
    if callable(
        preload
    ):  # onnxruntime-gpu: CUDA and cuDNN from NVIDIA's wheels, if there
        try:
            preload()
        except Exception as exc:  # noqa: BLE001 - missing DLLs: the provider check says so
            log.info("onnxruntime couldn't preload CUDA", extra={"error": str(exc)})
    available = onnxruntime.get_available_providers()
    for name in GPU_PROVIDERS:
        if name in available:
            return [name, "CPUExecutionProvider"]
    raise TtsError(
        "ONNX Runtime has no GPU provider here (onnxruntime-gpu or onnxruntime-directml isn't "
        "installed: docs/runbooks/tts.md)"
    )


def session(path: Path, device: str) -> Any:  # noqa: ANN401 - an onnxruntime.InferenceSession
    import onnxruntime

    wanted = providers(device)
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = CPU_THREADS
    options.inter_op_num_threads = 1
    loaded = onnxruntime.InferenceSession(str(path), options, providers=wanted)
    if (
        loaded.get_providers()[0] != wanted[0]
    ):  # ONNX Runtime fell back to the CPU itself
        raise TtsError(f"{wanted[0]} didn't start (CUDA or cuDNN missing?)")
    return loaded


def _installed() -> None:
    if importlib.util.find_spec("kokoro_onnx") is None:
        raise TtsError(
            "kokoro-onnx isn't installed here (uv sync --group tts; gos deploy adds it)"
        )


Fetch = Callable[[str, str, Path], None]


def model_files(
    folder: Path,
    files: Mapping[str, str],
    base: str,
    note: Callable[[str], None],
    fetch: Fetch,
) -> Path:
    """``files`` (name -> SHA-256) in ``folder``, each downloaded once from ``base + name``."""
    missing = [name for name in files if not (folder / name).is_file()]
    if missing:
        note(f"Downloading {', '.join(missing)} to {folder} (once)...")
        for name in missing:
            fetch(base + name, files[name], folder / name)
    return folder


class KokoroEngine:
    name = KOKORO
    rate = KOKORO_RATE

    def __init__(self, kokoro: Any, voice: str, label: str) -> None:  # noqa: ANN401 - Kokoro
        self.kokoro = kokoro
        self.voice = voice
        self.label = label
        self.lang = "en-gb" if voice.startswith("b") else "en-us"

    @classmethod
    def load(
        cls,
        models: Path,
        device: str,
        voice: str,
        note: Callable[[str], None],
        fetch: Fetch | None = None,
    ) -> KokoroEngine:
        _installed()
        from kokoro_onnx import Kokoro

        folder = model_files(
            models / "kokoro", KOKORO_FILES, KOKORO_URL, note, fetch or wake.fetch
        )
        loaded = Kokoro.from_session(
            session(folder / "kokoro-v1.0.onnx", device),
            str(folder / "voices-v1.0.bin"),
        )
        voice = voice or KOKORO_VOICE
        if voice not in loaded.voices:
            raise TtsError(
                f"Kokoro has no voice {voice!r} (am_michael, af_heart, bm_george, ...)"
            )
        engine = cls(loaded, voice, f"kokoro {voice} on {device}")
        list(engine.stream("Ready."))  # the first run is slow: not on a real answer
        return engine

    def with_voice(self, voice: str) -> KokoroEngine:
        """The same loaded model in another of its voices (a persona's, D207): no reload."""
        if voice == self.voice:
            return self
        if voice not in self.kokoro.voices:
            raise TtsError(f"Kokoro has no voice {voice!r}")
        return KokoroEngine(
            self.kokoro, voice, self.label.replace(self.voice, voice, 1)
        )

    def stream(self, text: str) -> Iterator[bytes]:
        samples, _ = self.kokoro.create(
            text, voice=self.voice, speed=1.0, lang=self.lang
        )
        yield pcm16(samples)


_espeak_lock = threading.Lock()


def espeak(language: str) -> Any:  # noqa: ANN401 - a phonemizer EspeakBackend
    """phonemizer's espeak-ng backend on the espeak-ng library kokoro-onnx installs
    (espeakng-loader), as kokoro-onnx itself sets it up."""
    import espeakng_loader
    from phonemizer.backend import EspeakBackend
    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    with _espeak_lock:
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        return EspeakBackend(language, preserve_punctuation=True, with_stress=True)


class PiperEngine:
    """A Piper (VITS) voice run on ONNX Runtime, re-implemented from the voice's ``.onnx.json``:
    espeak-ng IPA phonemes, the voice's own vowel clusters as one symbol, each symbol's ids
    from ``phoneme_id_map`` with the pad id after each, between the start and end ids; the
    model takes them with the voice's noise, length and noise-w scales and returns float
    samples at ``audio.sample_rate``."""

    name = PIPER

    def __init__(
        self,
        model: Any,  # noqa: ANN401 - an InferenceSession (or a fake)
        config: Mapping[str, Any],
        phonemize: Callable[[str], str],
        label: str,
    ) -> None:
        self.model = model
        self.phonemize = phonemize
        self.label = label
        self.rate = int(config["audio"]["sample_rate"])
        self.ids_of: Mapping[str, list[int]] = config["phoneme_id_map"]
        self.clusters = sorted(
            ("".join(c) for c in config.get("vowel_clusters", [])),
            key=len,
            reverse=True,
        )
        inference = config.get("inference", {})
        self.scales = [
            float(inference.get("noise_scale", 0.667)),
            float(inference.get("length_scale", 1.0)),
            float(inference.get("noise_w", 0.8)),
        ]

    @classmethod
    def load(
        cls,
        models: Path,
        device: str,
        voice: str,
        note: Callable[[str], None],
        fetch: Fetch | None = None,
    ) -> PiperEngine:
        _installed()  # phonemizer and espeak-ng come with kokoro-onnx (the tts group)
        voice = voice or PIPER_VOICE
        if voice not in PIPER_VOICES:
            raise TtsError(
                f"no pinned Piper voice {voice!r} (there is {', '.join(PIPER_VOICES)})"
            )
        path, onnx_sha, json_sha = PIPER_VOICES[voice]
        files = {f"{voice}.onnx": onnx_sha, f"{voice}.onnx.json": json_sha}
        folder = model_files(
            models / "piper", files, PIPER_URL + path, note, fetch or wake.fetch
        )
        config = json.loads((folder / f"{voice}.onnx.json").read_text(encoding="utf-8"))
        backend = espeak(str(config.get("espeak", {}).get("voice", "en-us")))

        def phonemize(text: str) -> str:
            with _espeak_lock:
                return str(backend.phonemize([text], strip=True)[0])

        engine = cls(session(folder / f"{voice}.onnx", device), config, phonemize, "")
        engine.label = f"piper {voice} on {device}"
        list(engine.stream("Ready."))
        return engine

    def symbols(self, phonemes: str) -> list[str]:
        """``phonemes`` as the voice's symbols (decomposed; vowel clusters joined)."""
        text = unicodedata.normalize("NFD", phonemes)
        out: list[str] = []
        i = 0
        while i < len(text):
            cluster = next((c for c in self.clusters if text.startswith(c, i)), None)
            if cluster is not None and cluster in self.ids_of:
                out.append(cluster)
                i += len(cluster)
            else:
                out.append(text[i])
                i += 1
        return out

    def ids(self, phonemes: str) -> list[int]:
        pad = self.ids_of["_"]
        ids = [*self.ids_of["^"], *pad]
        for symbol in self.symbols(phonemes):
            if symbol in self.ids_of:  # a symbol the voice doesn't know is skipped
                ids += [*self.ids_of[symbol], *pad]
        return [*ids, *self.ids_of["$"]]

    def stream(self, text: str) -> Iterator[bytes]:
        import numpy as np

        ids = self.ids(self.phonemize(text))
        audio = self.model.run(
            None,
            {
                "input": np.array([ids], dtype=np.int64),
                "input_lengths": np.array([len(ids)], dtype=np.int64),
                "scales": np.array(self.scales, dtype=np.float32),
            },
        )[0]
        yield pcm16(audio)


Opener = Callable[..., Any]


def elevenlabs_key() -> str:
    """The ElevenLabs key from this account's keyring; :class:`TtsError` says why not."""
    import keyring.errors

    from gregtec.llm import keys

    try:
        return keys.get_key(ELEVENLABS_KEY)
    except keys.MissingKeyError as exc:
        raise TtsError(str(exc)) from None
    except keyring.errors.KeyringError as exc:
        raise TtsError(f"the keyring can't be read ({type(exc).__name__})") from None


class ElevenLabsEngine:
    """ElevenLabs Flash v2.5 (D203): each chunk is one streaming request; audio comes back as
    raw 24 kHz PCM and is played as it arrives. The text is already filtered (numbers in
    words: Flash doesn't normalise them itself [D§2.3])."""

    name = ELEVENLABS
    rate = ELEVENLABS_RATE

    def __init__(
        self,
        key: str,
        voice: str = ELEVENLABS_VOICE,
        *,
        model: str = ELEVENLABS_MODEL,
        opener: Opener = urllib.request.urlopen,
    ) -> None:
        self._key = key
        self.voice = voice or ELEVENLABS_VOICE
        self.model = model
        self.opener = opener
        self.label = f"elevenlabs {model} {self.voice}"

    def with_voice(self, voice: str) -> ElevenLabsEngine:
        """Another voice id with the same key and model (a persona's, D207)."""
        if voice == self.voice:
            return self
        return ElevenLabsEngine(self._key, voice, model=self.model, opener=self.opener)

    @classmethod
    def load(
        cls, voice: str, opener: Opener = urllib.request.urlopen
    ) -> ElevenLabsEngine:
        return cls(elevenlabs_key(), voice, opener=opener)

    def request(self, text: str) -> urllib.request.Request:
        url = (
            ELEVENLABS_URL.format(voice=self.voice) + f"?output_format=pcm_{self.rate}"
        )
        body = json.dumps({"text": text, "model_id": self.model}).encode("utf-8")
        headers = {"xi-api-key": self._key, "Content-Type": "application/json"}
        return urllib.request.Request(url, data=body, headers=headers, method="POST")

    def stream(self, text: str) -> Iterator[bytes]:
        try:
            with self.opener(self.request(text), timeout=HTTP_TIMEOUT_S) as response:
                rest = b""
                while piece := response.read(HTTP_CHUNK):
                    piece = rest + piece
                    whole = len(piece) - len(piece) % SAMPLE_BYTES
                    rest = piece[whole:]
                    if whole:
                        yield piece[:whole]
        except urllib.error.HTTPError as exc:
            raise TtsError(f"ElevenLabs answered HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TtsError(f"ElevenLabs unreachable ({type(exc).__name__})") from None


# --- choosing and loading ---------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    engine: str
    device: str = CPU

    @property
    def label(self) -> str:
        return (
            self.engine
            if self.engine == ELEVENLABS
            else f"{self.engine} on {self.device}"
        )


def choices(engine: str, device: str) -> list[Choice]:
    """What ``[voice] tts`` and ``tts_device`` try, in order: the configured engine, it on the
    CPU when the GPU was asked for, then the local engines on the CPU, cheapest first."""
    found = [Choice(engine, CPU if engine == ELEVENLABS else device)]
    for each in (Choice(engine, CPU), Choice(PIPER, CPU), Choice(KOKORO, CPU)):
        if each.engine != ELEVENLABS and each not in found:
            found.append(each)
    return found


Loader = Callable[[Choice, VoiceSettings, Callable[[str], None]], Engine]


def load_engine(
    choice: Choice, settings: VoiceSettings, note: Callable[[str], None]
) -> Engine:
    models = settings.gos_home / "models"
    # A voice is the configured engine's: a fallback engine uses its own default.
    voice = settings.tts_voice if choice.engine == settings.tts else ""
    if choice.engine == KOKORO:
        return KokoroEngine.load(models, choice.device, voice, note)
    if choice.engine == PIPER:
        return PiperEngine.load(models, choice.device, voice, note)
    if choice.engine == ELEVENLABS:
        return ElevenLabsEngine.load(voice)
    raise TtsError(f"unknown tts engine {choice.engine!r}")


STRIKES = 3  # chunk failures in a row before an engine is dropped for the session


@dataclass
class Engines:
    """The engines to speak with, in :func:`choices` order. One that won't load is dropped.
    One that fails on a chunk hands the rest of that utterance to the next, and the next
    utterance tries it again (a CUDA out-of-memory beside GTNH or a network blip passes); only
    :data:`STRIKES` failures in a row drop it for the session."""

    choices: list[Choice]
    settings: VoiceSettings
    loader: Loader | None = None  # None: load_engine
    note: Callable[[str], None] = field(default=lambda message: log.info(message))
    skipped: list[str] = field(default_factory=list)  # choices dropped, and why
    _loaded: dict[Choice, Engine] = field(default_factory=dict)
    _dropped: set[Choice] = field(default_factory=set)
    _strikes: dict[Choice, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def current(self) -> Engine | None:
        """The engine to start an utterance with, loading it (and dropping ones that won't)."""
        with self._lock:
            return self._first(0)

    def _first(self, start: int) -> Engine | None:
        for choice in self.choices[start:]:
            if choice in self._dropped:
                continue
            if choice in self._loaded:
                return self._loaded[choice]
            started = time.monotonic()
            try:
                engine = (self.loader or load_engine)(choice, self.settings, self.note)
            except Exception as exc:  # noqa: BLE001 - any engine failure: the next one's turn
                self._dropped.add(choice)
                self.skipped.append(f"{choice.label}: {type(exc).__name__}: {exc}")
                log.warning(
                    "a text-to-speech engine can't start",
                    extra={
                        "engine": choice.label,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                continue
            self._loaded[choice] = engine
            log.info(
                "text-to-speech ready",
                extra={
                    "engine": engine.label,
                    "load_ms": round((time.monotonic() - started) * 1000),
                },
            )
            return engine
        return None

    def _choice_of(self, engine: Engine) -> Choice | None:
        return next((c for c, e in self._loaded.items() if e is engine), None)

    def failed(self, engine: Engine, error: Exception) -> Engine | None:
        """``engine`` failed on a chunk: the engine after it for the rest of this utterance, or
        None. The :data:`STRIKES`-th failure in a row drops it."""
        error_text = f"{type(error).__name__}: {error}"
        with self._lock:
            choice = self._choice_of(engine)
            if choice is None:  # not one of these engines
                return None
            strikes = self._strikes.get(choice, 0) + 1
            self._strikes[choice] = strikes
            if strikes >= STRIKES and choice not in self._dropped:
                self._dropped.add(choice)
                self.skipped.append(
                    f"{engine.label}: {strikes} failures in a row, last {error_text}"
                )
            following = self._first(self.choices.index(choice) + 1)
        log.warning(
            "text-to-speech failed on a chunk",
            extra={
                "engine": engine.label,
                "error": error_text,
                "strikes": strikes,
                "dropped": choice in self._dropped,
                "next": following.label if following is not None else "none",
            },
        )
        return following

    def worked(self, engine: Engine) -> None:
        """``engine`` said a chunk: its failures in a row start again from zero."""
        with self._lock:
            choice = self._choice_of(engine)
            if choice is not None:
                self._strikes.pop(choice, None)


# --- the speaker ------------------------------------------------------------------------------


_END = None


class _Utterance:
    def __init__(
        self,
        speaker: Speaker,
        parts: list[str],
        on_first_audio: Callable[[], None],
        on_done: Callable[[], None],
    ) -> None:
        self.speaker = speaker
        self.parts = parts
        self.on_first_audio = on_first_audio
        self.on_done = on_done
        self.stopped = threading.Event()
        self.queue: queue.Queue[tuple[int, bytes] | None] = queue.Queue(maxsize=AHEAD)
        self.started = speaker.clock()
        self.record: dict[str, Any] = {
            "chunks": len(parts),
            "chars": sum(map(len, parts)),
        }

    def start(self) -> None:
        for target, name in (
            (self._synth, "gos-voice-tts"),
            (self._play, "gos-voice-play"),
        ):
            threading.Thread(target=target, name=name, daemon=True).start()

    def stop(self) -> None:
        self.stopped.set()

    def _put(self, item: tuple[int, bytes] | None) -> bool:
        while not self.stopped.is_set():
            try:
                self.queue.put(item, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _synth(self) -> None:
        engines = self.speaker.engines
        try:
            engine = engines.current()  # after a failure, the next one says the rest
            for i, part in enumerate(self.parts):
                while engine is not None and not self.stopped.is_set():
                    try:
                        # in the persona's voice when it names one for this engine (D207)
                        voiced = self.speaker.voiced(engine)
                        for pcm in voiced.stream(part):
                            if i == 0 and "synth_ms" not in self.record:
                                self.record["synth_ms"] = self._ms()
                                self.record["engine"] = voiced.label
                            if not self._put((engine.rate, pcm)):
                                return
                        engines.worked(engine)
                        break
                    except Exception as exc:  # noqa: BLE001 - say the rest with the next one
                        engine = engines.failed(engine, exc)
                if engine is None:
                    self.record["error"] = "no text-to-speech engine"
                    return
                if self.stopped.is_set():
                    return
        finally:
            self._put(_END)  # (when stopped, the player ends by itself)

    def _play(self) -> None:
        stream: Stream | None = None
        rate = 0
        try:
            while not self.stopped.is_set():
                try:
                    item = self.queue.get(timeout=0.05)
                except queue.Empty:
                    continue  # the next chunk is still being synthesised
                if item is _END:
                    break
                if item[0] != rate:
                    if stream is not None:
                        stream.close()
                    rate = item[0]
                    stream = self.speaker.output.open(rate)
                self._write(stream, rate, item[1])
        except Exception as exc:  # noqa: BLE001 - a device that went away: log, finish
            self.record["error"] = f"{type(exc).__name__}: {exc}"
            self.stopped.set()
        finally:
            if stream is not None:
                try:
                    stream.close(abort=self.stopped.is_set())
                except Exception as exc:  # noqa: BLE001 - closing a device that went away
                    log.info(
                        "closing the speech output failed", extra={"error": str(exc)}
                    )
            self._finish()

    def _write(self, stream: Stream | None, rate: int, pcm: bytes) -> None:
        assert stream is not None
        step = max(SAMPLE_BYTES, round(BLOCK_S * rate) * SAMPLE_BYTES)
        for at in range(0, len(pcm), step):
            if self.stopped.is_set():
                return
            stream.write(pcm[at : at + step])
            if "first_audio_ms" not in self.record:
                self.record["first_audio_ms"] = self._ms()
                self.on_first_audio()

    def _ms(self) -> int:
        return round((self.speaker.clock() - self.started) * 1000)

    def _finish(self) -> None:
        self.record["stopped"] = self.stopped.is_set() and "error" not in self.record
        self.record["total_ms"] = self._ms()
        log.info("spoke", extra=self.record)
        self.speaker.finished(self)
        self.on_done()


class Speaker:
    """The voice client's speaker (``gregtec_voice.hooks.Speaker``): filter, lexicon, chunks,
    engines and the output device. One utterance at a time; a new one stops the last."""

    def __init__(
        self,
        engines: Engines,
        output: Output,
        prepare: Callable[[str], str],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engines = engines
        self.output = output
        self.prepare = prepare
        self.clock = clock
        self._current: _Utterance | None = None
        self._lock = threading.Lock()
        self._voices: dict[str, str] = {}  # engine -> the persona's voice (D207)
        # engine name -> (the engine, the voice, it in that voice): one per engine, last used
        self._voiced: dict[str, tuple[Engine, str, Engine]] = {}
        self.last: dict[
            str, Any
        ] = {}  # the last utterance's record (the bench reads it)

    @property
    def name(self) -> str:
        engine = self.engines.current()
        return engine.name if engine is not None else "none"

    def speak(
        self,
        text: str,
        on_first_audio: Callable[[], None],
        on_done: Callable[[], None],
    ) -> None:
        self.stop()
        parts = chunks(self.prepare(text))
        if not parts:
            on_done()
            return
        utterance = _Utterance(self, parts, on_first_audio, on_done)
        with self._lock:
            self._current = utterance
        utterance.start()

    def use_voice(self, spec: str) -> None:
        """The persona's voices (WO-5.6, D207), e.g. ``kokoro:am_fenrir, eleven:<id>``: from
        the next chunk on, each engine speaks in its entry's voice, over ``[voice] tts_voice``;
        an engine with no entry (or "") keeps its configured voice. Piper has one pinned voice,
        so a ``piper:`` entry changes nothing until it has more."""
        voices: dict[str, str] = {}
        for entry in spec.split(","):
            prefix, sep, voice = entry.strip().partition(":")
            engine = VOICE_PREFIXES.get(prefix)
            if sep and engine is not None and voice:
                voices[engine] = voice
        if voices != self._voices:
            self._voices = voices
            log.info("speaking in the persona's voice", extra={"voices": spec[:200]})

    def voiced(self, engine: Engine) -> Engine:
        """``engine`` in the persona's voice for it, if any (kept per engine until its voice changes:
        a cheap copy, no model reload). A voice
        the engine doesn't have is logged and the engine's own voice speaks."""
        voice = self._voices.get(engine.name)
        change = getattr(engine, "with_voice", None)
        if not voice or change is None:
            return engine
        held = self._voiced.get(engine.name)
        if held is not None and held[0] is engine and held[1] == voice:
            return held[2]
        try:
            found = change(voice)
        except Exception as exc:  # noqa: BLE001 - its own voice still speaks
            log.warning(
                "the persona's voice can't be used",
                extra={"engine": engine.label, "voice": voice, "error": str(exc)},
            )
            found = engine
        self._voiced[engine.name] = (engine, voice, found)
        return found

    def stop(self) -> None:
        """Barge-in: what is playing stops within one block; nothing waiting is said."""
        with self._lock:
            current, self._current = self._current, None
        if current is not None:
            current.stop()

    def finished(self, utterance: _Utterance) -> None:
        with self._lock:
            self.last = dict(utterance.record)
            if self._current is utterance:
                self._current = None


def prepare(lexicon: Lexicon) -> Callable[[str], str]:
    """The reply as said: the speech filter with ``lexicon`` (plan 7.15)."""
    return lambda text: speech_filter.spoken(text, lexicon.apply)


def lexicon(gos_home: Path) -> Lexicon:
    glossary = Glossary.find(gos_home / "vault")
    if glossary.problem:
        log.warning(
            "the vault's glossary.tsv isn't used", extra={"problem": glossary.problem}
        )
    return Lexicon.from_glossary(glossary)


class Unavailable:
    """No engine could start: nothing is said (the log says why once); turns still finish."""

    name = "unavailable"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def speak(
        self, text: str, on_first_audio: Callable[[], None], on_done: Callable[[], None]
    ) -> None:
        on_done()

    def stop(self) -> None:
        pass


def create(
    settings: VoiceSettings,
    *,
    output: Output | None = None,
    loader: Loader | None = None,
    note: Callable[[str], None] | None = None,
) -> Speaker | Unavailable:
    """The voice client's speaker (``gregtec_voice.hooks``, D179): the first engine of
    :func:`choices` loads now (downloading its model the first time)."""
    engines = Engines(choices(settings.tts, settings.tts_device), settings, loader)
    if note is not None:
        engines.note = note
    if engines.current() is None:
        reason = "; ".join(engines.skipped)
        log.error("no text-to-speech engine could start", extra={"error": reason})
        return Unavailable(reason)
    return Speaker(
        engines,
        output if output is not None else default_output(settings.output_endpoint),
        prepare(lexicon(settings.gos_home)),
    )
