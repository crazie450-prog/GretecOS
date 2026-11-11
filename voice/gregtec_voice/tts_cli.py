"""``gos voice tts-bench``, ``tts-check`` and ``say`` (WO-5.3, D203, D204).

| command        | does                                                                       |
|----------------|----------------------------------------------------------------------------|
| ``tts-bench``  | each engine's first audio after ``speak()``; picks the cheapest that meets |
|                | the target (``gregtec_voice.tts_bench``)                                   |
| ``tts-check``  | the 40 check phrases through text-to-speech and back through speech-to-    |
|                | text (a smoke test)                                                        |
| ``say``        | a text, or the 40 check phrases, on the output device: the listening check |

All three run anywhere; on Linux the audio goes to a fake device (``--save DIR`` writes WAVs).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from gregtec.config import Config
from gregtec_voice import settings, speech_filter, tts
from gregtec_voice.playback import FakeOutput, Output, WasapiOutput, WavOutput
from gregtec_voice.settings import VoiceSettings
from gregtec_voice.tts import ELEVENLABS, GPU, Choice
from gregtec_voice.tts_bench import (
    ANSWERS,
    COST_ORDER,
    BenchError,
    Result,
    bench_one,
    check,
    check_report,
    load_phrases,
    report,
    say_once,
    write_tsv,
)

EXIT_PROBLEM, EXIT_SETUP = 1, 2
PHRASE_GAP_S = 0.6
CHOICE_NAMES = {
    ELEVENLABS if c.engine == ELEVENLABS else f"{c.engine}-{c.device}": c
    for c in COST_ORDER
}

Handler = Callable[[Config, VoiceSettings, argparse.Namespace, TextIO], int]


def add_parsers(commands: argparse._SubParsersAction) -> dict[str, Handler]:
    """``tts-bench``, ``tts-check`` and ``say`` on ``gos voice``; their handlers by name."""
    bench_cmd = commands.add_parser(
        "tts-bench",
        help="time text-to-speech per engine; pick the cheapest that meets the target",
        description=(
            "Says sample answers with each engine (piper and kokoro on the CPU; on the GPU "
            "when ONNX Runtime has a GPU provider; ElevenLabs when its key is stored) "
            "through the filter, the lexicon and the output device, times the first audio "
            "after speak() and picks the cheapest engine whose P50 is <= "
            f"{tts.TARGET_MS} ms. Logs the VRAM a GPU engine adds where nvidia-smi is. "
            "Writes a TSV report to GOS_HOME/data/reports."
        ),
    )
    bench_cmd.add_argument(
        "--engine",
        action="append",
        choices=sorted(CHOICE_NAMES),
        help="only this engine (repeatable; default: every one that can run here)",
    )
    bench_cmd.add_argument(
        "--runs",
        type=int,
        default=len(ANSWERS),
        help=f"answers per engine (default {len(ANSWERS)})",
    )
    bench_cmd.add_argument(
        "--fake-output",
        action="store_true",
        help="no sound device: time to the audio handed over (always so off Windows)",
    )
    bench_cmd.add_argument(
        "--save",
        type=Path,
        metavar="DIR",
        help="write the audio to DIR as WAVs instead",
    )
    bench_cmd.add_argument("--out", type=Path, help="the TSV report's path")
    check_cmd = commands.add_parser(
        "tts-check",
        help="the 40 check phrases through text-to-speech and back through speech-to-text",
        description=(
            "Synthesises each row of tests/evals/tts_phrases.tsv (30 GTNH terms, 10 number "
            "and unit phrases) with the text-to-speech engine and transcribes it with the "
            "speech-to-text engine: a smoke test of the pronunciations and numbers. The "
            "acceptance is listening: gos voice say --phrases."
        ),
    )
    check_cmd.add_argument(
        "--stt-engine",
        choices=settings.STT_ENGINES,
        help="instead of [voice] stt_engine",
    )
    check_cmd.add_argument("--stt-model", help="instead of [voice] stt_model")
    say_cmd = commands.add_parser(
        "say",
        help="say a text (or the 40 check phrases) on the output device",
        description=(
            "Says TEXT through the configured engine, filter, lexicon and output device "
            "([voice] output_endpoint) and prints what is said. --phrases says each row of "
            "tests/evals/tts_phrases.tsv: the listening check."
        ),
    )
    say_cmd.add_argument("text", nargs="?", help="what to say")
    say_cmd.add_argument(
        "--phrases", action="store_true", help="say the 40 check phrases"
    )
    say_cmd.add_argument(
        "--save",
        type=Path,
        metavar="DIR",
        help="write the audio to DIR as WAVs instead",
    )
    for each in (bench_cmd, check_cmd, say_cmd):
        each.add_argument(
            "--tts", choices=settings.TTS_ENGINES, help="instead of [voice] tts"
        )
        each.add_argument(
            "--tts-device",
            choices=settings.TTS_DEVICES,
            help="instead of [voice] tts_device",
        )
        each.add_argument("--config", type=Path, help="gos.toml (default: GOS_HOME's)")
    return {"tts-bench": _bench, "tts-check": _check, "say": _say}


def _settings(voice_settings: VoiceSettings, args: argparse.Namespace) -> VoiceSettings:
    return replace(
        voice_settings,
        tts=args.tts or voice_settings.tts,
        tts_device=args.tts_device or voice_settings.tts_device,
    )


def output_for(
    voice_settings: VoiceSettings, *, fake: bool = False, save: Path | None = None
) -> Output:
    """The configured device on Windows, else (or ``fake``) a fake one; ``save``: WAVs."""
    if save is not None:
        return WavOutput(save)
    if sys.platform == "win32" and not fake:
        return WasapiOutput(voice_settings.output_endpoint)
    return FakeOutput()


def bench_targets(
    names: list[str] | None,
    gpu_problem: Callable[[], str] | None = None,
    key_problem: Callable[[], str] | None = None,
) -> tuple[list[Choice], list[Result]]:
    """What to measure, and what can't be measured here (with why)."""
    if names:
        return [CHOICE_NAMES[name] for name in names], []
    problem = (gpu_problem or _gpu_problem)()
    key_problem = key_problem or _key_problem
    wanted: list[Choice] = []
    skipped: list[Result] = []
    for choice in COST_ORDER:
        if choice.device == GPU and problem:
            skipped.append(Result(choice, problem=problem))
        elif choice.engine == ELEVENLABS and (missing := key_problem()):
            skipped.append(Result(choice, problem=missing))
        else:
            wanted.append(choice)
    return wanted, skipped


def _key_problem() -> str:
    try:
        tts.elevenlabs_key()
    except tts.TtsError as exc:
        return str(exc)
    return ""


def _gpu_problem() -> str:
    try:
        tts.providers(GPU)
    except Exception as exc:  # noqa: BLE001 - TtsError, or ONNX Runtime not installed
        return str(exc)
    return ""


def _bench(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    if args.runs < 1:
        print("--runs must be at least 1", file=out)
        return EXIT_SETUP
    voice_settings = _settings(voice_settings, args)
    output = output_for(voice_settings, fake=args.fake_output, save=args.save)
    print(f"Output: {output.name}", file=out)
    lexicon = tts.lexicon(cfg.gos_home)
    wanted, results = bench_targets(args.engine)
    for choice in wanted:
        print(f"Measuring {choice.label}...", file=out)
        results.append(
            bench_one(choice, voice_settings, output, lexicon, runs=args.runs)
        )
    results.sort(key=lambda r: COST_ORDER.index(r.choice))
    chosen = report(results, out)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = args.out or cfg.data_dir / "reports" / f"tts-bench-{stamp}.tsv"
    write_tsv(results, path)
    print(f"report: {path}", file=out)
    return 0 if chosen is not None else EXIT_PROBLEM


def _engine(voice_settings: VoiceSettings, out: TextIO) -> tts.Engine | None:
    engines = tts.Engines(
        tts.choices(voice_settings.tts, voice_settings.tts_device),
        voice_settings,
        note=lambda message: print(message, file=out),
    )
    engine = engines.current()
    for skipped in engines.skipped:
        print(f"  not used: {skipped}", file=out)
    if engine is None:
        print("No text-to-speech engine could start.", file=out)
    else:
        print(f"Text-to-speech: {engine.label}", file=out)
    return engine


def _check(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    from gregtec_voice import stt

    voice_settings = _settings(voice_settings, args)
    try:
        phrases = load_phrases()
    except (OSError, BenchError) as exc:
        print(f"Can't use the phrases: {exc}", file=out)
        return EXIT_SETUP
    engine = _engine(voice_settings, out)
    if engine is None:
        return EXIT_PROBLEM
    try:
        transcriber = stt.load(
            args.stt_engine or voice_settings.stt_engine,
            args.stt_model or voice_settings.stt_model,
            cfg.gos_home,
            note=lambda message: print(message, file=out),
        )
    except stt.SttError as exc:
        print(f"No speech-to-text engine could start: {exc}", file=out)
        return EXIT_PROBLEM
    print(f"Speech-to-text: {transcriber.label}", file=out)
    rows = check(engine, transcriber, tts.lexicon(cfg.gos_home), phrases)
    return 0 if check_report(rows, out) else EXIT_PROBLEM


def _say(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    if not args.text and not args.phrases:
        print("Give a TEXT to say, or --phrases.", file=out)
        return EXIT_SETUP
    try:
        texts = (
            [(p.id, p.text) for p in load_phrases()]
            if args.phrases
            else [("", args.text)]
        )
    except (OSError, BenchError) as exc:
        print(f"Can't use the phrases: {exc}", file=out)
        return EXIT_SETUP
    voice_settings = _settings(voice_settings, args)
    output = output_for(voice_settings, save=args.save)
    speaker = tts.create(
        voice_settings, output=output, note=lambda message: print(message, file=out)
    )
    if not isinstance(speaker, tts.Speaker):
        print(f"No text-to-speech engine could start: {speaker.reason}", file=out)
        return EXIT_PROBLEM
    engine = speaker.engines.current()
    print(
        f"Text-to-speech: {engine.label if engine else 'none'}; output: {output.name}",
        file=out,
    )
    lexicon = tts.lexicon(cfg.gos_home)
    for label, text in texts:
        said = speech_filter.spoken(text, lexicon.apply)
        print(f"{label + ': ' if label else ''}{said}", file=out)
        say_once(speaker, text, time.monotonic)
        if args.phrases:
            time.sleep(PHRASE_GAP_S)
    return 0
