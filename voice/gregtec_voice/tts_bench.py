"""Measuring text-to-speech (plan 8 WO-5.3, PM14; D203, D204).

**The bench** (``gos voice tts-bench``, :func:`bench`): each engine, in :data:`COST_ORDER`, says
the :data:`ANSWERS` (spoken answers as the lanes write them: citations, numbers and units in)
through the real speaker (filter, lexicon, chunks, engine, output device), and the time from
``speak()`` to the first audio written to the device is taken for each. An engine **meets the
target** when its P50 is <= :data:`gregtec_voice.tts.TARGET_MS` (plan 8 WO-5.3: first audio
<= 400 ms after the first sentence). The **pick** is the first engine in cost order that meets
it: the CPU before the GPU (no VRAM next to GTNH, [D§2.8]), Piper before Kokoro (the smaller
model), the local engines before ElevenLabs (paid, and the text leaves the PC). VRAM in use is
read from ``nvidia-smi`` before and after each GPU engine loads and speaks, where there is one.

**The check** (``gos voice tts-check``, :func:`check`): ``tests/evals/tts_phrases.tsv`` holds 30
GTNH terms and 10 number and unit phrases, each with the text an answer would hold, what the
filter and lexicon must make of it (``say``; the unit tests hold the code to it) and the
spellings a listener's transcript may have (``heard``). Each row is synthesised by the engine
and transcribed by speech-to-text (WO-5.2's engine, glossary hotwords included): a row passes
when the transcript, with its numbers in words, holds one of the ``heard`` forms or the ``say``
text (letters only, any case). It is a round trip through two models, so a miss may be either
one's; Clayton's listening check on the PC is the acceptance.
"""

from __future__ import annotations

import csv
import math
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from gregtec_voice import speech_filter, tts
from gregtec_voice.audio import SAMPLE_RATE, Segment
from gregtec_voice.hooks import Transcriber
from gregtec_voice.lexicon import Lexicon
from gregtec_voice.playback import Output
from gregtec_voice.settings import VoiceSettings
from gregtec_voice.tts import CPU, ELEVENLABS, GPU, KOKORO, PIPER, Choice

REPO_ROOT = Path(__file__).resolve().parents[2]
PHRASES = REPO_ROOT / "tests" / "evals" / "tts_phrases.tsv"
HEADER = ("id", "kind", "text", "say", "heard")
KINDS = ("term", "number")
COST_ORDER = (
    Choice(PIPER, CPU),
    Choice(KOKORO, CPU),
    Choice(PIPER, GPU),
    Choice(KOKORO, GPU),
    Choice(ELEVENLABS, CPU),
)
SPEAK_TIMEOUT_S = 120.0
# Answers shaped as the fast and deep lanes write them, for timing only: the items, numbers and
# ids are placeholders, never game data, and nothing here is shown as an answer.
ANSWERS = (
    (
        "Test Ingot: the EBF makes it at 120 EU/t for 25 s (r:test.recipe/3f9a0c2b1e7d). "
        "There are 3 recipes in all."
    ),
    "The LuV line needs 4 Test Plates per part (r:test.recipe/aa01bb02cc03), so 16 in all.",
    "Your test line runs at 1920 EU/t with 2A at HV, inside a 2048 EU/t budget (plan:p17).",
    "Test Fluid comes from the LCR, 144 L for 144 L (r:test.recipe/0f1e2d3c4b5a).",
    "I handed that to the deep lane; the design will show on your dashboard in a minute.",
    "The test coils reach 2700K in this example (q:1234, r:test.recipe/abc123def456).",
    "The test buffer holds 1.5M EU in this example, about 95% full.",
    "Test Block: 8 Test Wires and a Test Casing in the Assembler at 30 EU/t (r:test/1a2b3c).",
    "The next quest in this example is Test Quest (q:Test_Quest@512), and it unlocks the LCR.",
    "The test recipe needs an IV machine, 7680 EU/t for 20 s (r:test.recipe/9e8d7c6b5a4f).",
)


class BenchError(RuntimeError):
    """The phrases file is malformed, or nothing could be measured."""


# --- the bench --------------------------------------------------------------------------------


@dataclass
class Result:
    choice: Choice
    label: str = ""
    load_ms: int | None = None
    first_ms: list[int] = field(default_factory=list)
    vram_mb: int | None = (
        None  # VRAM this engine added (GPU engines with nvidia-smi only)
    )
    problem: str = ""  # why it wasn't measured

    @property
    def p50(self) -> int | None:
        return percentile(self.first_ms, 0.5)

    @property
    def p90(self) -> int | None:
        return percentile(self.first_ms, 0.9)

    @property
    def meets(self) -> bool:
        return self.p50 is not None and self.p50 <= tts.TARGET_MS


def percentile(values: Sequence[int], q: float) -> int | None:
    """The ``q`` quantile of ``values`` (nearest rank), None when there are none."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered), max(1, math.ceil(q * len(ordered)))) - 1]


def vram_used_mb() -> int | None:
    """VRAM in use on the first NVIDIA GPU (``nvidia-smi``), None without one."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        done = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return int(done.stdout.split()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def say_once(speaker: tts.Speaker, text: str, clock: Callable[[], float]) -> int | None:
    """Speak ``text`` and wait; the ms from ``speak()`` to the first audio (None: no audio)."""
    done = threading.Event()
    first: list[float] = []
    started = clock()
    speaker.speak(text, on_first_audio=lambda: first.append(clock()), on_done=done.set)
    if not done.wait(SPEAK_TIMEOUT_S):
        speaker.stop()
        return None
    return round((first[0] - started) * 1000) if first else None


def bench_one(
    choice: Choice,
    settings: VoiceSettings,
    output: Output,
    lexicon: Lexicon,
    *,
    runs: int = len(ANSWERS),
    loader: tts.Loader | None = None,
    vram: Callable[[], int | None] = vram_used_mb,
    clock: Callable[[], float] = time.monotonic,
) -> Result:
    """One engine through the whole speaker (its model loaded and warmed up first)."""
    result = Result(choice)
    before = vram() if choice.device == GPU else None
    engines = tts.Engines([choice], settings, loader or tts.load_engine)
    started = clock()
    engine = engines.current()
    if engine is None:
        result.problem = "; ".join(engines.skipped) or "didn't load"
        return result
    result.label, result.load_ms = engine.label, round((clock() - started) * 1000)
    speaker = tts.Speaker(engines, output, tts.prepare(lexicon), clock=clock)
    for text in (ANSWERS * (runs // len(ANSWERS) + 1))[:runs]:
        ms = say_once(speaker, text, clock)
        if ms is None:
            result.problem = speaker.last.get("error", "no audio")
            break
        result.first_ms.append(ms)
    if before is not None and (after := vram()) is not None:
        result.vram_mb = after - before
    return result


def pick(results: Sequence[Result]) -> Result | None:
    """The cheapest result that meets the target (:data:`COST_ORDER`)."""
    met = [r for r in results if r.meets]
    return min(met, key=lambda r: COST_ORDER.index(r.choice), default=None)


def report(results: Sequence[Result], out: TextIO) -> Result | None:
    """The table, the pick and its gos.toml lines; returns the pick."""
    print(
        f"First audio after speak() (target: P50 <= {tts.TARGET_MS} ms, plan 8 WO-5.3):",
        file=out,
    )
    for r in results:
        name = r.label or r.choice.label
        if r.problem and not r.first_ms:
            print(f"  {name}: not measured: {r.problem}", file=out)
            continue
        vram = f", VRAM +{r.vram_mb} MB" if r.vram_mb is not None else ""
        verdict = "meets" if r.meets else "misses"
        print(
            f"  {name}: P50 {r.p50} ms, P90 {r.p90} ms, max {max(r.first_ms)} ms over "
            f"{len(r.first_ms)} answers; load {r.load_ms} ms{vram}: {verdict} the target",
            file=out,
        )
    chosen = pick(results)
    if chosen is None:
        print("No engine measured here meets the target.", file=out)
        return None
    print(
        f"Pick: {chosen.label} (the cheapest that meets it). In gos.toml's [voice]:\n"
        f'  tts = "{chosen.choice.engine}"\n  tts_device = "{chosen.choice.device}"',
        file=out,
    )
    return chosen


def write_tsv(results: Sequence[Result], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(
            (
                "engine",
                "device",
                "label",
                "load_ms",
                "answers",
                "p50_ms",
                "p90_ms",
                "max_ms",
                "vram_mb",
                "meets",
                "problem",
            )
        )
        for r in results:
            w.writerow(
                (
                    r.choice.engine,
                    r.choice.device,
                    r.label,
                    r.load_ms,
                    len(r.first_ms),
                    r.p50,
                    r.p90,
                    max(r.first_ms, default=None),
                    r.vram_mb,
                    "yes" if r.meets else "no",
                    r.problem,
                )
            )


# --- the round-trip check ---------------------------------------------------------------------


@dataclass(frozen=True)
class Phrase:
    id: str
    kind: str
    text: str
    say: str
    heard: tuple[str, ...]


def load_phrases(path: Path = PHRASES) -> list[Phrase]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    if not rows or tuple(rows[0]) != HEADER:
        raise BenchError(f"{path} must start with the header {' '.join(HEADER)}")
    found: list[Phrase] = []
    for n, row in enumerate(rows[1:], 2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(HEADER) or row[1] not in KINDS or not all(row[:4]):
            raise BenchError(
                f"{path}:{n}: want {' '.join(HEADER)} with a kind of {KINDS}"
            )
        heard = tuple(h.strip() for h in row[4].split("|") if h.strip())
        found.append(Phrase(row[0], row[1], row[2], row[3], heard))
    return found


def letters(text: str) -> str:
    """``text`` with its numbers in words, then letters only, lower case."""
    return re.sub(r"[^a-z]", "", speech_filter.numbers(text).casefold())


def heard_right(phrase: Phrase, transcript: str) -> bool:
    got = letters(transcript)
    if letters(phrase.say) in got:
        return True
    return any(letters(form) and letters(form) in got for form in phrase.heard)


@dataclass
class Checked:
    phrase: Phrase
    said: str
    transcript: str = ""
    ok: bool = False
    problem: str = ""


def synthesise(engine: tts.Engine, text: str) -> bytes:
    """``text`` (already filtered) as 16 kHz int16 PCM, as the microphone would deliver it."""
    import numpy as np

    pcm = b"".join(piece for part in tts.chunks(text) for piece in engine.stream(part))
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    silence = np.zeros(SAMPLE_RATE // 4, dtype=np.float32)
    audio = np.concatenate([silence, to_16k(samples, engine.rate), silence])
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


def to_16k(samples: Any, rate: int) -> Any:  # noqa: ANN401 - numpy float32 arrays
    """``samples`` at 16 kHz: wake_eval's windowed-sinc resampler for a small ratio (Kokoro's
    and ElevenLabs' 24 kHz), else low-passed (a moving average) and linearly interpolated
    (Piper's 22.05 kHz, whose 320/441 ratio would make that resampler's filter enormous)."""

    import numpy as np

    from gregtec_voice.wake_eval import resample

    g = math.gcd(rate, SAMPLE_RATE)
    if max(rate, SAMPLE_RATE) // g <= 8:
        return resample(samples, rate, SAMPLE_RATE)
    smoothed = np.convolve(samples, np.ones(2, np.float32) / 2, mode="same")
    times = np.arange(0, len(samples) / rate, 1 / SAMPLE_RATE)
    return np.interp(times, np.arange(len(samples)) / rate, smoothed).astype(np.float32)


def check(
    engine: tts.Engine,
    transcriber: Transcriber,
    lexicon: Lexicon,
    phrases: Sequence[Phrase],
) -> list[Checked]:
    out = []
    for phrase in phrases:
        said = speech_filter.spoken(phrase.text, lexicon.apply)
        row = Checked(phrase, said)
        try:
            pcm = synthesise(engine, said)
            row.transcript = transcriber.transcribe(
                Segment(pcm, "key", 0.0, 0.0, 0.0, 0.0)
            )
            row.ok = heard_right(phrase, row.transcript)
        except Exception as exc:  # noqa: BLE001 - report the row, go on
            row.problem = f"{type(exc).__name__}: {exc}"
        out.append(row)
    return out


def check_report(rows: Sequence[Checked], out: TextIO) -> bool:
    for kind in KINDS:
        mine = [r for r in rows if r.phrase.kind == kind]
        print(
            f"{kind}s: {sum(r.ok for r in mine)} of {len(mine)} heard right", file=out
        )
    for r in rows:
        if not r.ok:
            why = r.problem or f"heard {r.transcript!r}"
            print(f"  MISS {r.phrase.id} said {r.said!r}: {why}", file=out)
    return all(r.ok for r in rows)
