"""The wake word (plan 8 WO-5.4, D57, PM08, PM14, [H§1]; D185, D186).

:func:`create` is the voice client's hook (D179): it returns a :class:`WakeWord` that gets every
16 kHz block on the audio worker thread (``feed``) and drives the client through its trigger
bus: ``wake(t)``, ``end_of_speech(t)``, ``cancel()`` and ``speech(active, t)``.

**A wake, step by step** (all on the audio worker; times are the blocks' capture times):

1. **Detection** (stage 1). The detector hears the phrase in the stream: sherpa-onnx keyword
   spotting (the primary, no training: the phrase is spelt in the model's own word pieces) or a
   custom openWakeWord model (the alternative; always ``inference_framework="onnx"``: the
   default, tflite, isn't on Windows, and without model paths openWakeWord then raises instead
   of falling back, the Windows trap [H§1.1]). At once ``wake(t)`` opens the client's segment,
   so nothing Clayton says next is lost while stage 2 runs.
2. **The second stage**. The last :data:`VERIFY_S` seconds of audio (the phrase, and whatever
   came just before it) go to the client's own speech-to-text (faster-whisper on the GPU, the
   engine already loaded, WO-5.2) on a worker thread; the wake stands only when the transcript
   holds the phrase (:func:`match_score` >= :data:`MATCH`), so an acoustic look-alike is
   dropped ([H§1.3] point 4). A rejected wake is ``cancel()``: silent, nothing is sent.
3. **The chime and the cancel window** (plan 8: chime + 1.5 s). A wake that passed stage 2
   plays a short chime in the headset (never into the microphone path). Clayton then has
   :data:`CANCEL_WINDOW_S` to start his question; if he says nothing, the wake is dropped
   (``cancel()``), so a false wake costs a chime and nothing more. The cancel hotkey drops it
   at any time, as for the talk key (D179).
4. **End of speech**. Once the question has started (:data:`QUESTION_S` of speech after the
   detection), :data:`END_SILENCE_S` of silence ends it: ``end_of_speech(t)`` with ``t`` the
   end of the last speech, PM14's start point (the silence wait is the "up to ~0.6 s" PM14
   allows for).

**Voice activity** is Silero VAD, the copy faster-whisper ships (no download), run on each
32 ms of audio. It also gives **barge-in by voice** (plan 4.5, D180): :data:`BARGE_IN_S` of
speech is ``speech(True, t)``, which stops GOS while it speaks; its end is ``speech(False, t)``.

Logs: each wake is one ``wake`` record (its outcome, stage-2 score and timings; never the
transcript, D82). Models download once into ``GOS_HOME/models`` (the KWS model and
openWakeWord's two feature models, at pinned URLs and hashes); a custom openWakeWord model is
Clayton's own file (``[voice] wake_model``). Nothing here is committed or vendored: sherpa-onnx,
sentencepiece and openWakeWord are Apache-2.0 libraries installed from PyPI.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import shutil
import tarfile
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from gregtec_voice.audio import (
    BLOCK_S,
    BYTES_PER_S,
    SAMPLE_RATE,
    Block,
    Ring,
    Segment,
    _sounddevice,
    _wasapi,
)
from gregtec_voice.hooks import Transcriber, TriggerBus
from gregtec_voice.settings import OPENWAKEWORD, SHERPA, WAKE, VoiceSettings
from gregtec_voice.stt import floats

log = logging.getLogger(__name__)

CANCEL_WINDOW_S = 1.5  # plan 8 WO-5.4: after the chime, this long to start the question
END_SILENCE_S = 0.6  # PM14: end-of-speech detection adds up to ~0.6 s
QUESTION_S = (
    0.2  # speech after the detection that makes it a question (not the phrase's tail)
)
BARGE_IN_S = 0.3  # speech this long while GOS speaks stops it (D180)
QUESTION_MAX_S = 30.0  # the recorder's cap (D181)
VERIFY_S = 2.0  # audio before the detection that stage 2 hears ([H§1.3]: ~2 s)
VERIFY_TIMEOUT_S = 5.0  # stage 2 slower than this: the wake is dropped
REFRACTORY_S = (
    0.5  # after a wake ends, the phrase's echo in the detector can't fire again
)
MATCH = 0.8  # stage 2: how close the transcript must come to the phrase (D186)
VAD_ON, VAD_OFF = (
    0.5,
    0.35,
)  # Silero speech probability: speech from ON, silence below OFF
VAD_CHUNK, VAD_CONTEXT = (
    512,
    64,
)  # Silero v6 at 16 kHz: 32 ms chunks with 4 ms of context
OWW_FRAME = 1280  # openWakeWord's 80 ms frame
DEFAULT_THRESHOLD = {SHERPA: 0.25, OPENWAKEWORD: 0.5}  # D186
SHERPA_BOOST = 1.5  # keywords_score for the phrase (D186)
TIMEOUT_S = 60.0
USER_AGENT = "gregtec-os-voice (model download)"

# The sherpa-onnx English KWS model (Apache-2.0; 10,000 h of GigaSpeech, ~19 MB) [H§1.2].
KWS_MODEL = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
KWS_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
    f"{KWS_MODEL}.tar.bz2"
)
KWS_SHA256 = "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a"
KWS_FILES = {
    "tokens": "tokens.txt",
    "bpe": "bpe.model",
    "encoder": "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    "decoder": "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
    "joiner": "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
}
# openWakeWord's shared feature models (Apache-2.0 code; release v0.5.1).
OWW_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
OWW_FEATURES = {
    "melspectrogram.onnx": "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
    "embedding_model.onnx": "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
}

IDLE, VERIFYING, VERIFIED = "idle", "verifying", "verified"


class WakeError(RuntimeError):
    """The wake engine can't start (the message says why and what to do)."""


# --- the phrase ------------------------------------------------------------------------------


def _letters(word: str) -> str:
    return "".join(c for c in word.casefold() if c.isalnum())


def match_score(text: str, phrase: str) -> float:
    """How closely some run of ``text``'s words spells ``phrase`` (0 to 1): the best
    similarity of the phrase's letters to those of any run of one word fewer to one word more
    than it has. Spacing, case and punctuation don't count ("Hey, Archivist!" is 1.0)."""
    target = _letters(phrase)
    words = [w for w in (_letters(x) for x in text.split()) if w]
    n = len(phrase.split())
    best = 0.0
    for size in range(max(1, n - 1), n + 2):
        for i in range(len(words) - size + 1):
            run = "".join(words[i : i + size])
            best = max(best, difflib.SequenceMatcher(None, run, target).ratio())
    return best


def strip_phrase(text: str, phrase: str) -> str:
    """A wake turn's transcript without the phrase's tail at its start: the segment's pre-roll
    (D181) holds the last 300 ms of the phrase, which speech-to-text may write out ("...vist,
    what does an EBF need?"). Leading words are dropped only while their letters are close to
    the end of the phrase's; the question's own words never are."""
    words = text.split()
    target = _letters(phrase)
    cut = 0
    for k in range(1, min(len(words), len(phrase.split()) + 1) + 1):
        lead = _letters(" ".join(words[:k]))
        if len(lead) < 2:
            continue
        tail = target[-len(lead) :]
        if difflib.SequenceMatcher(None, lead, tail).ratio() >= MATCH:
            cut = k
    return " ".join(words[cut:])


# --- the parts -------------------------------------------------------------------------------


class Detector(Protocol):
    """Stage 1: hears the phrase in a stream of 20 ms blocks."""

    name: str

    def accept(self, pcm: bytes) -> bool:
        """True when the phrase ended in what was heard so far."""
        ...

    def reset(self) -> None: ...


class Vad(Protocol):
    def is_speech(self, pcm: bytes) -> bool:
        """Whether the latest audio, up to the end of ``pcm``, is speech."""
        ...


class Chime(Protocol):
    def play(self) -> None:
        """Start the chime and return at once."""
        ...


Run = Callable[[Callable[[], None]], None]


def _thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="gos-voice-wake-check", daemon=True).start()


class NoChime:
    def play(self) -> None:
        pass


@dataclass
class _Wake:
    """The wake in progress."""

    detected: float  # the block that fired
    seq: int
    checked: float | None = None  # when stage 2 passed
    window_end: float | None = None
    heard: float = 0.0  # seconds of speech since the detection
    last_speech: float | None = None
    score: float | None = None
    check_ms: int | None = None


class WakeWord:
    """The wake engine (``gregtec_voice.hooks.WakeEngine``); see the module docstring.

    ``verifier`` is stage 2 (PCM in, text out; ``None`` skips it, e.g. ``gos voice run
    --transcript``); ``run`` starts it off the audio thread (tests run it inline). Its verdict
    is taken on the audio thread with the next block, so every step of a wake happens there."""

    def __init__(
        self,
        detector: Detector,
        vad: Vad,
        bus: TriggerBus,
        *,
        phrase: str,
        verifier: Callable[[bytes], str] | None = None,
        chime: Chime | None = None,
        run: Run = _thread,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.detector = detector
        self.vad = vad
        self.bus = bus
        self.phrase = phrase
        self.verifier = verifier
        self.chime = chime or NoChime()
        self.run = run
        self.clock = clock
        self.name = getattr(detector, "name", "wake")
        self.state = IDLE
        self.wakes = 0  # stage-1 detections
        self.chimes = 0  # wakes that passed stage 2
        self._ring = Ring(VERIFY_S)
        self._wake: _Wake | None = None
        self._verdict: tuple[int, float | None, int] | None = None  # seq, score, ms
        self._lock = threading.Lock()
        self._quiet_until = 0.0
        self._speech_s = 0.0  # the current run of speech
        self._silence_s = 0.0
        self._talking = False  # speech(True) was sent and speech(False) not yet

    # --- the audio worker

    def feed(self, block: Block) -> None:
        t = block.t_end
        self._ring.push(block.pcm)
        speech = self.vad.is_speech(block.pcm)
        self._barge_in(speech, t)
        fired = self.detector.accept(block.pcm)
        if self.state == IDLE:
            if fired and t >= self._quiet_until:
                self._detected(t)
            return
        wake = self._wake
        assert wake is not None
        if t > wake.detected and speech:
            wake.heard += BLOCK_S
            wake.last_speech = t
        if self.state == VERIFYING:
            self._take_verdict(wake, t)
        if self.state == VERIFIED:
            self._listen(wake, t)

    def close(self) -> None:
        self.state = IDLE

    # --- steps

    def _barge_in(self, speech: bool, t: float) -> None:
        if speech:
            self._speech_s += BLOCK_S
            self._silence_s = 0.0
            if not self._talking and self._speech_s >= BARGE_IN_S - 1e-9:
                self._talking = True
                self.bus.speech(True, t)
        else:
            self._speech_s = 0.0
            self._silence_s += BLOCK_S
            if self._talking and self._silence_s >= END_SILENCE_S - 1e-9:
                self._talking = False
                self.bus.speech(False, t)

    def _detected(self, t: float) -> None:
        self.wakes += 1
        wake = _Wake(t, self.wakes)
        self._wake = wake
        self.bus.wake(t)
        if self.verifier is None:
            self._passed(wake, t)
            return
        self.state = VERIFYING
        pcm = self._ring.snapshot()
        verifier, seq = self.verifier, wake.seq

        def check() -> None:
            began = time.monotonic()
            try:
                score: float | None = match_score(verifier(pcm), self.phrase)
            except Exception:
                log.exception("the wake word's second stage failed")
                score = None
            with self._lock:
                self._verdict = (seq, score, round((time.monotonic() - began) * 1000))

        self.run(check)

    def _take_verdict(self, wake: _Wake, t: float) -> None:
        with self._lock:
            verdict, self._verdict = self._verdict, None
        if verdict is None or verdict[0] != wake.seq:
            if t - wake.detected >= VERIFY_TIMEOUT_S:
                self._end(wake, t, "check timed out")
            return
        _, score, ms = verdict
        wake.score, wake.check_ms = score, ms
        if score is None:
            self._end(wake, t, "check failed")
        elif score < MATCH:
            self._end(wake, t, "rejected")
        else:
            self._passed(wake, t)

    def _passed(self, wake: _Wake, t: float) -> None:
        self.state = VERIFIED
        self.chimes += 1
        wake.checked = t
        wake.window_end = t + CANCEL_WINDOW_S
        try:
            self.chime.play()
        except Exception:
            log.exception("the wake chime failed")

    def _listen(self, wake: _Wake, t: float) -> None:
        started = wake.heard >= QUESTION_S - 1e-9
        if not started:
            if wake.window_end is not None and t >= wake.window_end:
                self._end(wake, t, "no question")
            return
        assert wake.last_speech is not None
        if (
            t - wake.last_speech >= END_SILENCE_S - 1e-9
            or t - wake.detected >= QUESTION_MAX_S
        ):
            self._end(wake, t, "asked", wake.last_speech)

    def _end(
        self, wake: _Wake, t: float, outcome: str, spoke_until: float | None = None
    ) -> None:
        if outcome == "asked":
            assert spoke_until is not None
            self.bus.end_of_speech(spoke_until)
        else:
            self.bus.cancel()
        self.state = IDLE
        self._wake = None
        self._quiet_until = t + REFRACTORY_S
        self.detector.reset()
        log.info(
            "wake",
            extra={
                "outcome": outcome,
                "engine": self.name,
                "score": None if wake.score is None else round(wake.score, 2),
                "check_ms": wake.check_ms,
                "question_s": round(wake.heard, 2),
                "end_ms": None
                if spoke_until is None
                else round((t - spoke_until) * 1000),
            },
        )


# --- engines ---------------------------------------------------------------------------------


class SileroVad:
    """Silero VAD v6 (MIT), the copy faster-whisper ships, run stream-wise on 32 ms chunks
    through ONNX Runtime, one thread."""

    def __init__(self) -> None:
        import numpy as np
        import onnxruntime
        from faster_whisper.utils import get_assets_path

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(
            str(Path(get_assets_path()) / "silero_vad_v6.onnx"),
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )
        self._np = np
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(VAD_CONTEXT, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._speech = False

    def prob(self, chunk: Any) -> float:  # noqa: ANN401 - VAD_CHUNK float32 samples
        np = self._np
        x = np.concatenate([self._context, chunk])[None, :]
        out, self._h, self._c = self._session.run(
            None, {"input": x, "h": self._h, "c": self._c}
        )
        self._context = chunk[-VAD_CONTEXT:]
        return float(out.reshape(-1)[0])

    def is_speech(self, pcm: bytes) -> bool:
        np = self._np
        self._buf = np.concatenate([self._buf, floats(pcm)])
        while len(self._buf) >= VAD_CHUNK:
            chunk, self._buf = self._buf[:VAD_CHUNK], self._buf[VAD_CHUNK:]
            p = self.prob(chunk)
            self._speech = p >= VAD_ON or (self._speech and p >= VAD_OFF)
        return self._speech


def keyword_line(pieces: list[str], threshold: float, boost: float) -> str:
    """One line of a sherpa-onnx keywords file: the phrase's word pieces, its boost
    (``:``), its threshold (``#``) and a label (``@``)."""
    return f"{' '.join(pieces)} :{boost} #{threshold} @WAKE"


class SherpaKws:
    """sherpa-onnx keyword spotting (Apache-2.0): a small streaming zipformer whose decoder
    only follows the phrase's own word pieces, so any phrase works without training."""

    name = SHERPA

    def __init__(
        self,
        folder: Path,
        phrase: str,
        *,
        threshold: float = DEFAULT_THRESHOLD[SHERPA],
        boost: float = SHERPA_BOOST,
    ) -> None:
        import sentencepiece
        import sherpa_onnx

        sp = sentencepiece.SentencePieceProcessor()
        sp.load(str(folder / KWS_FILES["bpe"]))
        pieces = [str(p) for p in sp.encode(phrase.upper(), out_type=str)]
        known = {
            line.split()[0]
            for line in (folder / KWS_FILES["tokens"])
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        }
        unknown = [p for p in pieces if p not in known]
        if unknown:
            raise WakeError(
                f"the KWS model can't spell {phrase!r} ({' '.join(unknown)})"
            )
        self.pieces = pieces
        keywords = folder / "gos-keywords.txt"  # the model's folder is GOS_HOME's own
        keywords.write_text(
            keyword_line(pieces, threshold, boost) + "\n", encoding="utf-8"
        )
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=str(folder / KWS_FILES["tokens"]),
            encoder=str(folder / KWS_FILES["encoder"]),
            decoder=str(folder / KWS_FILES["decoder"]),
            joiner=str(folder / KWS_FILES["joiner"]),
            keywords_file=str(keywords),
            num_threads=1,
            keywords_score=boost,
            keywords_threshold=threshold,
        )
        self._stream = self._kws.create_stream()

    def accept(self, pcm: bytes) -> bool:
        self._stream.accept_waveform(SAMPLE_RATE, floats(pcm))
        heard = False
        while self._kws.is_ready(self._stream):
            self._kws.decode_stream(self._stream)
            if self._kws.get_result(self._stream):
                heard = True
                self._kws.reset_stream(self._stream)
        return heard

    def reset(self) -> None:
        self._kws.reset_stream(self._stream)


class OpenWakeWord:
    """A custom openWakeWord model (Apache-2.0 library; the model is Clayton's, trained with
    openWakeWord's or livekit-wakeword's pipeline [H§1.2]). Always ONNX: openWakeWord's default
    framework is tflite, which Windows lacks (the Windows trap, [H§1.1])."""

    name = OPENWAKEWORD

    def __init__(
        self,
        model: Path,
        features: Path,
        *,
        threshold: float = DEFAULT_THRESHOLD[OPENWAKEWORD],
    ) -> None:
        import numpy as np
        from openwakeword.model import Model

        if not model.is_file():
            raise WakeError(f"[voice] wake_model {model} doesn't exist")
        self._model = Model(
            wakeword_models=[str(model)],
            inference_framework="onnx",  # never the tflite default (Windows trap)
            melspec_model_path=str(features / "melspectrogram.onnx"),
            embedding_model_path=str(features / "embedding_model.onnx"),
        )
        self._np = np
        self.threshold = threshold
        self._buf = np.zeros(0, dtype=np.int16)

    def accept(self, pcm: bytes) -> bool:
        np = self._np
        self._buf = np.concatenate([self._buf, np.frombuffer(pcm, dtype="<i2")])
        heard = False
        while len(self._buf) >= OWW_FRAME:
            frame, self._buf = self._buf[:OWW_FRAME], self._buf[OWW_FRAME:]
            scores = self._model.predict(frame)
            heard = heard or max(scores.values(), default=0.0) >= self.threshold
        return heard

    def reset(self) -> None:
        self._model.reset()
        self._buf = self._np.zeros(0, dtype=self._np.int16)


def chime_pcm() -> bytes:
    """Two short rising tones (about 0.2 s, -18 dBFS), faded in and out."""
    import array
    import math

    samples = array.array("h")
    amplitude = 32767 * 10 ** (-18 / 20)
    for hz, seconds in ((880.0, 0.09), (1320.0, 0.11)):
        n = round(seconds * SAMPLE_RATE)
        fade = round(0.01 * SAMPLE_RATE)
        for i in range(n):
            ramp = min(1.0, i / fade, (n - 1 - i) / fade)
            samples.append(
                round(amplitude * ramp * math.sin(2 * math.pi * hz * i / SAMPLE_RATE))
            )
    return samples.tobytes()


class WasapiChime:
    """Windows: the chime on WASAPI's default output device (the headset), on its own short
    thread, so the audio worker never waits for it."""

    def __init__(self) -> None:
        self.pcm = chime_pcm()

    def play(self) -> None:
        threading.Thread(target=self._play, name="gos-voice-chime", daemon=True).start()

    def _play(self) -> None:
        try:
            sd = _sounddevice()
            _, api = _wasapi(sd)
            with sd.RawOutputStream(
                samplerate=SAMPLE_RATE,
                device=api["default_output_device"],
                channels=1,
                dtype="int16",
                extra_settings=sd.WasapiSettings(auto_convert=True),
            ) as stream:
                stream.write(self.pcm)
        except Exception as exc:  # noqa: BLE001 - PortAudioError, AudioError: log, go on
            log.warning("the wake chime didn't play", extra={"error": str(exc)})


# --- models ----------------------------------------------------------------------------------


def fetch(url: str, sha256: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` (through a ``.part`` file), refusing a wrong hash."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 16):
            digest.update(chunk)
            f.write(chunk)
    if digest.hexdigest() != sha256:
        part.unlink()
        raise WakeError(f"{url} didn't match its pinned SHA-256 (not used)")
    part.replace(dest)


Fetch = Callable[[str, str, Path], None]


def kws_files(models: Path, note: Callable[[str], None], fetch: Fetch = fetch) -> Path:
    """The KWS model's folder in ``models/kws``, downloaded once (about 18 MB)."""
    folder = models / "kws" / KWS_MODEL
    if all((folder / name).is_file() for name in KWS_FILES.values()):
        return folder
    note(f"Downloading the wake-word model {KWS_MODEL} to {folder.parent} (once)...")
    archive = folder.parent / f"{KWS_MODEL}.tar.bz2"
    fetch(KWS_URL, KWS_SHA256, archive)
    unpack = folder.parent / f"{KWS_MODEL}.unpack"
    shutil.rmtree(unpack, ignore_errors=True)
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(unpack, filter="data")
    shutil.rmtree(folder, ignore_errors=True)
    (unpack / KWS_MODEL).replace(folder)
    shutil.rmtree(unpack, ignore_errors=True)
    archive.unlink()
    return folder


def oww_features(
    models: Path, note: Callable[[str], None], fetch: Fetch = fetch
) -> Path:
    """openWakeWord's two feature models in ``models/openwakeword``, downloaded once."""
    folder = models / "openwakeword"
    for name, sha in OWW_FEATURES.items():
        if not (folder / name).is_file():
            note(f"Downloading openWakeWord's {name} to {folder} (once)...")
            fetch(OWW_URL + name, sha, folder / name)
    return folder


def threshold(settings: VoiceSettings) -> float:
    return settings.wake_threshold or DEFAULT_THRESHOLD[settings.wake_engine]


def detector(
    settings: VoiceSettings,
    *,
    phrase: str | None = None,
    note: Callable[[str], None] | None = None,
) -> Detector:
    """Stage 1 for ``settings`` (``phrase``: another phrase, the harness). Raises
    :class:`WakeError` naming what is missing."""
    tell = note or (lambda message: log.info(message))
    models = settings.gos_home / "models"
    try:
        if settings.wake_engine == SHERPA:
            return SherpaKws(
                kws_files(models, tell),
                phrase or settings.wake_phrase,
                threshold=threshold(settings),
            )
        return OpenWakeWord(
            Path(settings.wake_model).expanduser(),
            oww_features(models, tell),
            threshold=threshold(settings),
        )
    except ImportError as exc:
        raise WakeError(
            f"{settings.wake_engine} isn't installed here ({exc.name}): uv sync --group "
            "wake (gos deploy installs it)"
        ) from None
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, WakeError):
            raise
        raise WakeError(
            f"{settings.wake_engine}: {type(exc).__name__}: {exc}"
        ) from None


def vad() -> Vad:
    try:
        return SileroVad()
    except ImportError as exc:
        raise WakeError(
            f"the wake word's VAD needs the stt group ({exc.name}): uv sync --group stt"
        ) from None


def verifier(transcriber: Transcriber) -> Callable[[bytes], str]:
    """Stage 2 through the client's own speech-to-text (the model already loaded)."""

    def heard(pcm: bytes) -> str:
        now = time.monotonic()
        seconds = len(pcm) / BYTES_PER_S
        return transcriber.transcribe(Segment(pcm, WAKE, now - seconds, now, now, 0.0))

    return heard


def create(
    settings: VoiceSettings,
    bus: TriggerBus,
    transcriber: Transcriber | None = None,
) -> WakeWord | None:
    """The voice client's wake engine (``gregtec_voice.hooks``, D179, D185), or None when it
    can't start (the log says why; the talk key still works, plan 0.1 principle 9).
    ``transcriber``: stage 2 (None skips it)."""
    try:
        found = detector(settings)
        listening = vad()
    except WakeError as exc:
        log.error("the wake word can't start", extra={"error": str(exc)})
        return None
    log.info(
        "wake word ready",
        extra={
            "engine": settings.wake_engine,
            "threshold": threshold(settings),
            "second_stage": transcriber is not None,
        },
    )
    return WakeWord(
        found,
        listening,
        bus,
        phrase=settings.wake_phrase,
        verifier=None if transcriber is None else verifier(transcriber),
        chime=WasapiChime(),
    )
