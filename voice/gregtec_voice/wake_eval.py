"""Scoring wake phrases: false accepts and false rejects (plan 8 WO-5.4, PM08; D187).

Two sources of audio, both run through the real wake engine (:class:`gregtec_voice.wake.
WakeWord`: stage 1, the VAD, stage 2 when given, the cancel window), block by block with
capture times, as the voice client feeds it:

- **Synthetic** (``gos voice wake-eval --kokoro DIR``): each candidate phrase of
  ``tests/evals/wake_phrases.tsv`` is said 40 times (:data:`ATTEMPTS`) by a local text-to-speech
  model (Kokoro, through kokoro-onnx when it is installed), by eight voices at four speeds,
  each followed by a question of the STT set and mixed with generated noise (game-like tones,
  keyboard clicks, PC hum, other voices); the **negatives** are the open-mic lines of
  ``tests/evals/wake_chatter.txt`` (GregTech talk, "hey" and "okay" phrases, near misses of
  every candidate) and each candidate's own near misses, said by the same voices over the same
  noise, then minutes of noise alone. Nothing is written but the report: no audio, no model.
- **Recorded** (``--audio DIR``, Clayton's PC): ``DIR/wake/*.wav`` are wake attempts (each
  file one attempt, the phrase and a question), ``DIR/play/*.wav`` are hours of open-mic
  gameplay (``gos voice capture``), all 16 kHz mono 16-bit; only the configured phrase is
  scored, against plan 8's acceptance: >= 38 of 40 attempts and <= 1 false wake per hour.

A **hit** is an attempt that chimed (stage 2 passed, or stage 1 without it); a **false wake**
is a chime in the negatives. Each is counted at stage 1 too, so the report shows what the
second stage removes. Times are the audio's own, so the scores don't depend on the CPU.
"""

from __future__ import annotations

import csv
import math
import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TextIO

from gregtec_voice.audio import BLOCK_FRAMES, SAMPLE_BYTES, SAMPLE_RATE, Block
from gregtec_voice.wake import Detector, Vad, WakeWord

REPO_ROOT = Path(__file__).resolve().parents[2]
PHRASES = REPO_ROOT / "tests" / "evals" / "wake_phrases.tsv"
CHATTER = REPO_ROOT / "tests" / "evals" / "wake_chatter.txt"
QUESTIONS = REPO_ROOT / "tests" / "evals" / "stt_utterances.tsv"
ATTEMPTS = 40  # plan 8 WO-5.4: >= 38 of 40
HIT_TARGET = 38 / 40
FALSE_PER_HOUR_TARGET = 1.0
VOICES = (
    "af_heart",
    "am_michael",
    "bf_emma",
    "bm_george",
    "af_bella",
    "am_adam",
    "af_sarah",
    "bm_lewis",
)
SPEEDS = (1.0, 0.9, 1.15, 1.3)
# (noise, SNR in dB) for the attempts: a headset microphone hears Clayton far above the rest
CONDITIONS = (
    ("quiet", 60.0),
    ("game", 15.0),
    ("clicks", 15.0),
    ("hum", 20.0),
    ("babble", 12.0),
    ("game", 6.0),
)
NOISES = ("game", "clicks", "hum", "babble", "pink")
MISSES_SHOWN = 8  # per phrase in the report (a phrase that fails would list all 40)
LEAD_S, GAP_S, TAIL_S = (
    0.6,
    0.35,
    2.5,
)  # silence before, between and after (window + EOS)


class EvalError(ValueError):
    """The phrase set, the chatter or a recording can't be used (the message says why)."""


@dataclass(frozen=True)
class Candidate:
    phrase: str
    note: str
    near: tuple[str, ...]


def load_phrases(path: Path = PHRASES) -> list[Candidate]:
    """The candidate phrases (``phrase note near``; ``near`` is ``|``-separated)."""
    from gregtec_voice.settings import wake_phrase

    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    found = []
    for n, row in enumerate(rows, start=2):
        try:
            phrase = wake_phrase(row.get("phrase") or "")
        except (TypeError, ValueError) as exc:
            raise EvalError(f"{path.name} line {n}: the phrase {exc}") from None
        near = tuple(x.strip() for x in (row.get("near") or "").split("|") if x.strip())
        found.append(Candidate(phrase, (row.get("note") or "").strip(), near))
    if not found:
        raise EvalError(f"{path.name} has no phrases")
    return found


def load_lines(path: Path = CHATTER) -> list[str]:
    """One line each; ``#`` starts a comment line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [x.strip() for x in lines if x.strip() and not x.lstrip().startswith("#")]


def load_questions(path: Path = QUESTIONS) -> list[str]:
    with open(path, encoding="utf-8", newline="") as f:
        return [row["text"] for row in csv.DictReader(f, delimiter="\t")]


# --- audio -----------------------------------------------------------------------------------


def _np() -> Any:  # noqa: ANN401 - numpy, which only the engines and this harness need
    import numpy as np

    return np


def resample(x: Any, src: int, dst: int) -> Any:  # noqa: ANN401 - numpy arrays
    """Resample by ``dst/src`` with a windowed-sinc low-pass (numpy only)."""
    np = _np()
    if src == dst:
        return x.astype(np.float32)
    g = math.gcd(src, dst)
    up, down = dst // g, src // g
    taps = 64 * max(up, down) + 1
    cutoff = 0.5 / max(up, down)
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.kaiser(taps, 8.0) * up
    y = np.zeros(len(x) * up, np.float32)
    y[::up] = x
    y = np.convolve(y, h.astype(np.float32), mode="same")
    return y[::down].astype(np.float32)


def rms(x: Any) -> float:  # noqa: ANN401
    np = _np()
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def noise(kind: str, seconds: float, rng: Any) -> Any:  # noqa: ANN401 - numpy Generator
    """``seconds`` of generated noise at unit RMS: ``pink``, ``hum`` (a PC's fans and mains),
    ``clicks`` (keys and mouse), ``game`` (pink rumble with machine-like tones and sweeps),
    ``quiet`` (a near-silent room). ``babble`` is made from speech by the caller."""
    np = _np()
    n = max(1, round(seconds * SAMPLE_RATE))
    t = np.arange(n) / SAMPLE_RATE
    if kind in ("pink", "game", "quiet"):
        spectrum = np.fft.rfft(rng.normal(size=n))
        f = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)
        spectrum[1:] /= np.sqrt(f[1:])
        spectrum[0] = 0
        x = np.fft.irfft(spectrum, n)
    if kind == "hum":
        x = sum(
            np.sin(2 * np.pi * hz * t) / k
            for k, hz in enumerate((60, 120, 180, 240), 1)
        )
        x = x + 0.3 * rng.normal(size=n)
    elif kind == "clicks":
        x = 0.02 * rng.normal(size=n)
        for start in rng.choice(n, size=max(1, int(seconds * 7)), replace=False):
            m = min(n - start, 240)
            x[start : start + m] += rng.normal(size=m) * np.exp(-np.arange(m) / 40)
    elif kind == "game":
        x = x / max(rms(x), 1e-9)
        for _ in range(max(1, int(seconds * 1.5))):
            start, m = int(rng.integers(0, n)), int(rng.integers(1600, 9600))
            m = min(m, n - start)
            hz = float(rng.uniform(150, 2400))
            sweep = hz * (1 + float(rng.uniform(-0.5, 0.5)) * np.arange(m) / m)
            tone = np.sin(2 * np.pi * np.cumsum(sweep) / SAMPLE_RATE)
            x[start : start + m] += 2.0 * tone * np.hanning(m)
    elif kind not in ("pink", "quiet"):
        raise EvalError(f"no noise called {kind!r}")
    x = np.asarray(x, np.float32)
    return x / max(rms(x), 1e-9)


def mix(speech: Any, background: Any, snr_db: float) -> Any:  # noqa: ANN401
    """``speech`` over ``background`` (looped or cut to its length) at ``snr_db``, against the
    speech's loud part (its frames above -40 dB of its peak)."""
    np = _np()
    reps = math.ceil(len(speech) / max(1, len(background)))
    bg = np.tile(background, reps)[: len(speech)]
    frames = speech[: len(speech) // 320 * 320].reshape(-1, 320)
    level = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    loud = level[level > level.max() * 0.01] if len(level) else level
    s = float(np.sqrt(np.mean(loud**2))) if len(loud) else rms(speech)
    return (speech + bg * (s / 10 ** (snr_db / 20))).astype(np.float32)


def pcm16(x: Any) -> bytes:  # noqa: ANN401 - float32 samples
    np = _np()
    return (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def silence(seconds: float) -> Any:  # noqa: ANN401
    np = _np()
    return np.zeros(round(seconds * SAMPLE_RATE), np.float32)


# --- speech ----------------------------------------------------------------------------------


class Tts(Protocol):
    def say(self, text: str, voice: str, speed: float) -> Any:  # noqa: ANN401
        """16 kHz float32 samples."""
        ...


class KokoroTts:
    """Kokoro v1.0 through kokoro-onnx (Apache-2.0 weights, MIT wrapper), when installed; its
    two files (``kokoro-v1.0.onnx``, ``voices-v1.0.bin``) in ``folder``. Not a dependency:
    the harness is a development and PC tool, and WO-5.3 brings the TTS engine."""

    def __init__(self, folder: Path) -> None:
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            raise EvalError(
                "kokoro-onnx isn't installed here (uv pip install kokoro-onnx)"
            ) from None
        model, voices = folder / "kokoro-v1.0.onnx", folder / "voices-v1.0.bin"
        if not (model.is_file() and voices.is_file()):
            raise EvalError(f"{folder} lacks kokoro-v1.0.onnx and voices-v1.0.bin")
        self._kokoro = Kokoro(str(model), str(voices))

    def say(self, text: str, voice: str, speed: float) -> Any:  # noqa: ANN401
        np = _np()
        lang = "en-us" if voice.startswith("a") else "en-gb"
        samples, rate = self._kokoro.create(text, voice=voice, speed=speed, lang=lang)
        return resample(np.asarray(samples, np.float32), rate, SAMPLE_RATE) * 0.8


@dataclass
class Clip:
    pcm: bytes
    label: str
    phrase_end: float | None = None  # seconds into the clip, for attempts

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (SAMPLE_RATE * SAMPLE_BYTES)


def attempts(
    phrase: str,
    tts: Tts,
    questions: Sequence[str],
    *,
    count: int = ATTEMPTS,
    seed: int = 54,
    babble: Any = None,  # noqa: ANN401 - float32 speech to use as babble
) -> list[Clip]:
    """``count`` wake attempts: the phrase, a pause, a question, over :data:`CONDITIONS`."""
    np = _np()
    rng = np.random.default_rng(seed)
    clips = []
    for i in range(count):
        voice = VOICES[i % len(VOICES)]
        speed = SPEEDS[(i // len(VOICES)) % len(SPEEDS)]
        kind, snr = CONDITIONS[i % len(CONDITIONS)]
        said = tts.say(phrase.capitalize() + ",", voice, speed)
        question = tts.say(questions[i % len(questions)], voice, speed)
        speech = np.concatenate(
            [silence(LEAD_S), said, silence(GAP_S), question, silence(TAIL_S)]
        )
        clips.append(
            Clip(
                pcm16(mix(speech, _background(kind, speech, rng, babble), snr)),
                f"{voice} x{speed} {kind} {snr:g} dB",
                LEAD_S + len(said) / SAMPLE_RATE,
            )
        )
    return clips


def _background(kind: str, speech: Any, rng: Any, babble: Any) -> Any:  # noqa: ANN401
    if kind == "babble" and babble is not None and len(babble):
        start = int(rng.integers(0, max(1, len(babble) - len(speech))))
        part = babble[start : start + len(speech)]
        return part / max(rms(part), 1e-9)
    return noise("pink" if kind == "babble" else kind, len(speech) / SAMPLE_RATE, rng)


def chatter(
    lines: Sequence[str],
    tts: Tts,
    *,
    noise_minutes: float = 5.0,
    seed: int = 54,
) -> tuple[list[Clip], Any]:
    """The negatives: every line, said by the voices in turn over rotating noise, then
    ``noise_minutes`` of noise alone (a minute of each kind in turn). Also returns the clean
    speech, for babble."""
    np = _np()
    rng = np.random.default_rng(seed)
    pick = random.Random(seed)
    clips, spoken = [], []
    for i, line in enumerate(lines):
        voice = VOICES[i % len(VOICES)]
        speech = tts.say(line, voice, pick.choice(SPEEDS[:3]))
        spoken.append(speech)
        speech = np.concatenate([speech, silence(pick.uniform(0.3, 1.5))])
        kind = NOISES[i % len(NOISES)]
        bg = noise("pink" if kind == "babble" else kind, len(speech) / SAMPLE_RATE, rng)
        clips.append(Clip(pcm16(mix(speech, bg, 15.0)), f"{voice}: {line}"))
    whole = np.concatenate(spoken) if spoken else np.zeros(0, np.float32)
    minute = 0
    while minute < noise_minutes:
        kind = NOISES[minute % len(NOISES)]
        seconds = min(60.0, (noise_minutes - minute) * 60)
        if kind == "babble" and len(whole):
            reps = math.ceil(seconds * SAMPLE_RATE / len(whole))
            layered = sum(
                np.roll(np.tile(whole, reps), int(rng.integers(0, len(whole))))
                for _ in range(3)
            )[: round(seconds * SAMPLE_RATE)]
            x = layered / max(rms(layered), 1e-9) * 0.05
        else:
            x = noise(kind, seconds, rng) * 0.03
        clips.append(Clip(pcm16(x), f"noise: {kind}"))
        minute += 1
    return clips, whole


# --- scoring ---------------------------------------------------------------------------------


@dataclass
class Bus:
    """A trigger bus that only records (the harness has no voice client)."""

    wakes: list[float] = field(default_factory=list)
    ends: list[float] = field(default_factory=list)
    cancels: int = 0

    def wake(self, t: float) -> None:
        self.wakes.append(t)

    def end_of_speech(self, t: float) -> None:
        self.ends.append(t)

    def cancel(self) -> None:
        self.cancels += 1

    def speech(self, active: bool, t: float) -> None:
        pass


def _inline(job: Callable[[], None]) -> None:
    job()


def feed(engine: WakeWord, pcm: bytes, t: float) -> float:
    """Feed ``pcm`` in 20 ms blocks from time ``t``; the time after it."""
    size = BLOCK_FRAMES * SAMPLE_BYTES
    for i in range(0, len(pcm), size):
        chunk = pcm[i : i + size].ljust(size, b"\0")
        t += BLOCK_FRAMES / SAMPLE_RATE
        engine.feed(Block(chunk, t))
    return t


@dataclass
class Score:
    phrase: str
    engine: str
    attempts: int = 0
    detected: int = 0  # stage 1
    hits: int = 0  # chimed
    latency_ms: list[int] = field(default_factory=list)  # phrase end to detection
    misses: list[str] = field(default_factory=list)
    negative_s: float = 0.0
    false_detected: int = 0
    false_wakes: int = 0
    offenders: list[str] = field(default_factory=list)  # negatives that chimed

    @property
    def hit_rate(self) -> float:
        return self.hits / self.attempts if self.attempts else 0.0

    @property
    def per_hour(self) -> float:
        return self.false_wakes / (self.negative_s / 3600) if self.negative_s else 0.0

    @property
    def met(self) -> bool:
        return (
            self.attempts > 0
            and self.hit_rate >= HIT_TARGET - 1e-9
            and self.negative_s > 0
            and self.per_hour <= FALSE_PER_HOUR_TARGET
        )


def score(
    phrase: str,
    engine_name: str,
    detector: Detector,
    vad: Callable[[], Vad],
    positives: Iterable[Clip],
    negatives: Iterable[Clip],
    verifier: Callable[[bytes], str] | None,
) -> Score:
    """Run every clip through a :class:`WakeWord` (stage 2 inline) and count."""
    result = Score(phrase, engine_name)

    def engine(bus: Bus) -> WakeWord:
        detector.reset()
        return WakeWord(
            detector, vad(), bus, phrase=phrase, verifier=verifier, run=_inline
        )

    for clip in positives:
        bus = Bus()
        wake = engine(bus)
        start = 1000.0
        feed(wake, clip.pcm, start)
        result.attempts += 1
        if wake.wakes:
            result.detected += 1
            if clip.phrase_end is not None:
                result.latency_ms.append(
                    round((bus.wakes[0] - start - clip.phrase_end) * 1000)
                )
        if wake.chimes:
            result.hits += 1
        else:
            result.misses.append(clip.label)
    bus = Bus()
    wake = engine(bus)
    t = 1000.0
    for clip in negatives:
        before_w, before_c = wake.wakes, wake.chimes
        t = feed(wake, clip.pcm, t)
        result.negative_s += clip.seconds
        if wake.chimes > before_c:
            result.offenders.append(clip.label)
        result.false_detected += wake.wakes - before_w
        result.false_wakes += wake.chimes - before_c
    return result


def median(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def report(scores: Sequence[Score], out: TextIO, *, second_stage: bool) -> None:
    stage = "stage 2 on" if second_stage else "stage 1 only (no second stage)"
    print(
        f"Wake phrases ({stage}); targets: >= 38/40 hits, <= 1 false wake/h", file=out
    )
    print(
        f"{'phrase':<22}{'engine':<14}{'stage-1':>9}{'hits':>8}{'latency':>9}"
        f"{'neg. min':>10}{'stage-1 FA':>11}{'false':>7}{'per h':>8}",
        file=out,
    )
    for s in scores:
        lat = median(s.latency_ms)
        print(
            f"{s.phrase:<22}{s.engine:<14}{s.detected:>5}/{s.attempts:<3}"
            f"{s.hits:>4}/{s.attempts:<3}"
            f"{'' if lat is None else f'{lat} ms':>9}"
            f"{s.negative_s / 60:>10.1f}{s.false_detected:>11}{s.false_wakes:>7}"
            f"{s.per_hour:>8.1f}",
            file=out,
        )
    for s in scores:
        for label in s.offenders:
            print(f"  false wake, {s.phrase!r}: {label}", file=out)
        for label in s.misses[:MISSES_SHOWN]:
            print(f"  missed, {s.phrase!r}: {label}", file=out)
        if len(s.misses) > MISSES_SHOWN:
            print(f"  ... and {len(s.misses) - MISSES_SHOWN} more missed", file=out)


def write_tsv(scores: Sequence[Score], path: Path, *, second_stage: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(
            [
                "phrase",
                "engine",
                "second_stage",
                "attempts",
                "stage1_hits",
                "hits",
                "latency_p50_ms",
                "negative_s",
                "stage1_false",
                "false_wakes",
                "false_per_hour",
                "met",
            ]
        )
        for s in scores:
            w.writerow(
                [
                    s.phrase,
                    s.engine,
                    int(second_stage),
                    s.attempts,
                    s.detected,
                    s.hits,
                    median(s.latency_ms) if s.latency_ms else "",
                    round(s.negative_s, 1),
                    s.false_detected,
                    s.false_wakes,
                    round(s.per_hour, 2),
                    int(s.met),
                ]
            )


def recorded(folder: Path) -> list[Clip]:
    """``folder``'s WAVs (16 kHz mono 16-bit), by name."""
    from gregtec_voice.stt_eval import EvalError as WavError
    from gregtec_voice.stt_eval import read_wav

    clips = []
    for path in sorted(folder.glob("*.wav")):
        try:
            clips.append(Clip(read_wav(path), path.name))
        except WavError as exc:
            raise EvalError(str(exc)) from None
    return clips
