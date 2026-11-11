"""``gos voice ...`` (WO-5.1, D179-D181): the voice client and the checks for Clayton's PC.

| command                      | does                                                          |
|------------------------------|---------------------------------------------------------------|
| ``run``                      | the voice client (talk key, hotkeys, capture, the core)       |
| ``test``                     | the key hook, watchdog and capture live, no core: prints each |
|                              | key, segment and action (plan 8 WO-5.1's acceptance check)    |
| ``keys {play,lab}``          | the configured keys and the GTNH bindings on them             |
| ``devices``                  | the WASAPI capture devices                                    |
| ``bench``                    | turn timings from ``logs/voice.log`` against PM14             |
| ``stt [WAV ...]``            | load speech-to-text (downloading its model once), say which   |
|                              | engine and why, transcribe the WAVs (WO-5.2)                  |
| ``record``                   | record the STT eval set: one talk-key segment per line        |
| ``eval``                     | the STT eval: entity accuracy and WER (WO-5.2, D183)          |

``run``, ``test``, ``devices`` and ``record`` need Windows (WASAPI and the keyboard hook);
``keys``, ``bench``, ``stt`` and ``eval`` run anywhere. ``run`` needs the core's voice token in
this account's keyring (``gos token new voice``, D117).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from gregtec import applog
from gregtec.agents.tools.glossary import Glossary
from gregtec.config import Config
from gregtec.gamestate.safe_open import read_bytes
from gregtec.llm import keys as stored
from gregtec_voice import audio, hooks, keys, ptt, settings, stt, stt_eval
from gregtec_voice.audio import AudioError, AudioPipe, Segment, WasapiSource
from gregtec_voice.client import (
    VOICE,
    CoreClient,
    Online,
    Process,
    Pushed,
    Refused,
    VoiceClient,
)
from gregtec_voice.settings import VoiceSettings

log = logging.getLogger(__name__)

TARGETS_MS = {"key": 1700, "wake": 2200}  # PM14: to first audio, P50
GAME_DIRS = ("minecraft", ".minecraft")  # Prism's order (gregtec.tweaks)
EXIT_PROBLEM, EXIT_SETUP = 1, 2
EVAL_AUDIO = (
    "GOS_HOME/data/evals/stt"  # gos voice record writes, gos voice eval reads (D183)
)


def eval_audio(cfg: Config) -> Path:
    return cfg.data_dir / "evals" / "stt"


def _model_name(value: str) -> str:
    """argparse's check of --model: as gos.toml's [voice] stt_model."""
    try:
        return settings.parse({"stt_model": value}).stt_model
    except settings.SettingsError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(
        prog="gos voice", description="The Gregtec OS voice client (WO-5.1)."
    )
    commands = parser.add_subparsers(dest="voice_command", required=True)

    run = commands.add_parser(
        "run",
        help="run the voice client",
        description=(
            "Talk key, hotkeys, 16 kHz WASAPI capture with a 300 ms pre-roll, and the core "
            "connection, until Ctrl+C (or, --supervised, a 'stop' line on stdin)."
        ),
    )
    run.add_argument(
        "--transcript",
        metavar="TEXT",
        help="every segment 'says' TEXT instead of going through speech-to-text: measures "
        "the rest of the path (key, capture, core; speech once WO-5.3's engine is there)",
    )
    run.add_argument(
        "--supervised",
        action="store_true",
        help="stop on a 'stop' line or end of stdin (as gos-supervisor stops its processes)",
    )
    test = commands.add_parser(
        "test",
        help="check the keys, the hook and the microphone live (no core)",
        description=(
            "Installs the key hook and its watchdog and opens the microphone, then prints "
            "every configured key the hook sees, what it does, and each recorded segment's "
            "length and level. Nothing is sent anywhere. Press each key with GTNH focused, "
            "fullscreen and then borderless."
        ),
    )
    test.add_argument(
        "--seconds",
        type=float,
        default=0,
        help="stop after this long (default: Ctrl+C)",
    )
    test.add_argument(
        "--save",
        type=Path,
        metavar="DIR",
        help="also write each segment to DIR as a WAV (it is your voice: delete them after)",
    )
    keys_cmd = commands.add_parser(
        "keys",
        help="the configured keys and the GTNH bindings on them",
        description=(
            "Reads the instance's options.txt (read only, the way GOS reads game files) and "
            "lists any GTNH binding on the talk key or a hotkey: Minecraft's, every mod's "
            "and NEI's (GTNH's NEI keeps its keys there too). Discord keeps its keybinds "
            "where GOS can't read them: check Discord's Settings > Keybinds by hand."
        ),
    )
    keys_cmd.add_argument("instance", choices=("play", "lab"))
    commands.add_parser("devices", help="list the WASAPI capture devices")
    bench = commands.add_parser(
        "bench",
        help="turn timings from logs/voice.log against PM14",
        description=(
            "P50 and P90 of each stage of the last turns the voice client logged, and PM14's "
            "targets: talk key <= 1.7 s and wake word <= 2.2 s from the end of speech to the "
            "first audio (P50)."
        ),
    )
    bench.add_argument(
        "--last", type=int, default=100, help="turns to use (default 100)"
    )
    stt_cmd = commands.add_parser(
        "stt",
        help="load speech-to-text and transcribe WAV files",
        description=(
            "Says whether Whisper can use the GPU here and why not, loads the configured "
            "engine (downloading its model to GOS_HOME/models the first time), shows the "
            "glossary prompt, then transcribes each 16 kHz mono WAV."
        ),
    )
    stt_cmd.add_argument("wav", nargs="*", type=Path, help="16 kHz mono 16-bit WAVs")
    record = commands.add_parser(
        "record",
        help="record your voice for the STT eval set",
        description=(
            "Shows each line of the STT eval set: hold the talk key, say it as you would ask "
            "GOS, release. Each recording is saved as <id>.wav (your voice: it stays in "
            "GOS_HOME). Without --only, only the lines with no recording yet are asked, so a "
            "stopped session continues where it stopped."
        ),
    )
    record.add_argument(
        "--only", help="comma-separated ids to (re)record, e.g. s07,s23"
    )
    record.add_argument(
        "--dir", type=Path, help=f"where to save (default: {EVAL_AUDIO})"
    )
    evaluate = commands.add_parser(
        "eval",
        help="the STT eval: entity accuracy and word error rate",
        description=(
            "Transcribes each recording of the STT eval set, resolves each transcript to an "
            "item as the core does, and reports entity accuracy (target >= 95%%) and word "
            "error rate (target <= 8%%), with a TSV report in GOS_HOME/data/reports. "
            "--check only checks that every line of the set resolves to its key on this DB."
        ),
    )
    evaluate.add_argument(
        "--audio", type=Path, help=f"the recordings (default: {EVAL_AUDIO})"
    )
    evaluate.add_argument(
        "--check", action="store_true", help="only check the set against this DB"
    )
    evaluate.add_argument("--limit", type=int, help="only the first N lines")
    evaluate.add_argument("--out", type=Path, help="the TSV report's path")
    for each in (record, evaluate):
        each.add_argument(
            "--set", type=Path, help="another set file (id kind key text)"
        )
    for each in (stt_cmd, evaluate):
        each.add_argument(
            "--engine",
            choices=settings.STT_ENGINES,
            help="instead of [voice] stt_engine",
        )
        each.add_argument(
            "--model", type=_model_name, help="instead of [voice] stt_model"
        )
    for each in (
        run,
        test,
        keys_cmd,
        commands.choices["devices"],
        bench,
        stt_cmd,
        record,
        evaluate,
    ):
        each.add_argument("--config", type=Path, help="gos.toml (default: GOS_HOME's)")
    args = parser.parse_args(argv)
    try:
        cfg, voice_settings = settings.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return EXIT_SETUP
    handler = {
        "run": _run,
        "test": _test,
        "keys": _keys,
        "devices": _devices,
        "bench": _bench,
        "stt": _stt,
        "record": _record,
        "eval": _eval,
    }[args.voice_command]
    return handler(cfg, voice_settings, args, out)


def _windows_only(out: TextIO) -> bool:
    if sys.platform == "win32":
        return False
    print(
        "The voice client's key hook and WASAPI capture run on Windows only.", file=out
    )
    return True


def _describe(voice: VoiceSettings) -> str:
    talk = f"talk {voice.talk_key.label} ({voice.talk_mode})"
    if not voice.key_trigger:
        talk = f"talk key off ({voice.talk_key.label})"
    hot = ", ".join(
        f"{n.replace('_', ' ')} {k.label}" for n, k in voice.hotkeys().items()
    )
    return f"{talk}; {hot}"


def _windows_parts(
    voice: VoiceClient, device: str
) -> tuple[AudioPipe, ptt.WindowsHook, ptt.Watchdog]:
    key_filter = ptt.KeyFilter(voice.watched, voice.post, tick=ptt.tick_count())
    hook = ptt.WindowsHook(key_filter)
    watchdog = ptt.Watchdog(hook, ptt.AsyncKeyState(), key_filter)
    return AudioPipe(WasapiSource(device), voice.recorder), hook, watchdog


def _stop_on_stdin(stop: threading.Event) -> None:
    try:
        for line in sys.stdin.buffer:
            if line.strip() == b"stop":
                break
    except (OSError, ValueError, AttributeError):
        pass
    stop.set()


def _run(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    if _windows_only(out):
        return EXIT_SETUP
    try:
        token = stored.get_key(f"token-{VOICE}")
    except stored.MissingKeyError:
        print(
            "No voice token is stored. As yourself, with GOS running: gos token new voice",
            file=out,
        )
        return EXIT_SETUP
    applog.setup(VOICE, cfg.logs_dir)
    stop = threading.Event()
    if not args.transcript:  # the first run downloads the model: gos voice stt shows it
        print("Loading speech-to-text...", file=out)
    transcriber = (
        hooks.FixedTranscriber(args.transcript)
        if args.transcript
        else hooks.transcriber(voice_settings)
    )
    voice: VoiceClient
    core = CoreClient(
        cfg.core_port,
        token,
        on_push=lambda message: voice.post(Pushed(message)),
        on_online=lambda online: voice.post(Online(online, time.monotonic())),
        on_refused=lambda: voice.post(Refused(time.monotonic())),
    )
    voice = VoiceClient(
        voice_settings,
        core,
        transcriber=transcriber,
        speaker=hooks.speaker(voice_settings),
    )
    wake = (
        hooks.wake_engine(voice_settings, voice)
        if voice_settings.wake_trigger
        else None
    )
    if voice_settings.wake_trigger and wake is None:
        log.warning(
            "no wake-word engine yet (WO-5.4): the talk key is the trigger"
            if voice_settings.key_trigger
            else "no wake-word engine yet (WO-5.4) and the talk key is off: only the "
            'hotkeys work (set [voice] backup_trigger = "key")'
        )
    pipe, hook, watchdog = _windows_parts(voice, voice_settings.input_device)
    if wake is not None:
        pipe.sinks.append(wake)
    if args.supervised:
        threading.Thread(target=_stop_on_stdin, args=(stop,), daemon=True).start()
    log.info(
        "voice client starting",
        extra={"keys": _describe(voice_settings), "stt": transcriber.name},
    )
    stt_label = getattr(transcriber, "label", transcriber.name)
    print(
        f"Voice client: {_describe(voice_settings)}; speech-to-text: {stt_label}. "
        "Ctrl+C stops it.",
        file=out,
    )
    process = Process(voice, pipe, hook, watchdog=watchdog, core=core, wake=wake)
    try:
        process.run(stop)
    except AudioError as exc:
        log.error("can't open the microphone", extra={"error": str(exc)})
        print(f"Can't open the microphone: {exc}", file=out)
        return EXIT_PROBLEM
    except KeyboardInterrupt:
        pass
    log.info("voice client stopped")
    return 0


# --- gos voice test ---------------------------------------------------------------------------


@dataclass
class _TestCore:
    """``gos voice test``'s core: prints what would be sent, sends nothing."""

    out: TextIO

    def ask(self, text: str) -> dict[str, Any]:
        print(f"  -> would ask the core: {text!r}", file=self.out)
        return {"text": "", "lane": "test"}

    def prewarm(self) -> None:
        pass

    def ui(self, action: str) -> None:
        print(f"  -> would tell the dashboards: {action}", file=self.out)

    def state(self, state: str, trigger: str | None) -> None:
        print(f"  state: {state}" + (f" ({trigger})" if trigger else ""), file=self.out)


@dataclass
class _SegmentReport:
    """``gos voice test``'s transcriber: reports each segment, says nothing."""

    out: TextIO
    save: Path | None
    name: str = "test"
    count: int = 0

    def transcribe(self, segment: Segment) -> str:
        self.count += 1
        peak = segment.peak_dbfs()
        level = "silent" if math.isinf(peak) else f"peak {peak:.0f} dBFS"
        line = (
            f"  segment {self.count}: {segment.seconds:.2f} s ({segment.pre_roll_s * 1000:.0f} "
            f"ms pre-roll), {level}"
        )
        if segment.capped:
            line += ", stopped at the 30 s cap"
        if self.save is not None:
            self.save.mkdir(parents=True, exist_ok=True)
            path = self.save / f"segment-{self.count}.wav"
            path.write_bytes(segment.wav())
            line += f", saved {path}"
        print(line, file=self.out)
        return ""


@dataclass
class _KeyLog:
    """Prints each key event on the controller thread (never on the hook thread)."""

    out: TextIO
    started: float = field(default_factory=time.monotonic)
    seen: dict[str, int] = field(default_factory=dict)
    lags: list[int] = field(default_factory=list)
    missed: int = 0

    def __call__(self, event: object) -> None:
        if not isinstance(event, ptt.KeyEvent):
            return
        label = ptt.key_label(event.vk)
        if event.down:
            self.seen[label] = self.seen.get(label, 0) + 1
        if event.lag_ms is not None:
            self.lags.append(event.lag_ms)
        note = ""
        if event.source == "watchdog":
            self.missed += 1
            note = " (the hook missed it: the watchdog re-installed the hook)"
        elif event.lag_ms is not None:
            note = f" (hook {event.lag_ms} ms)"
        stamp = event.t - self.started
        print(
            f"{stamp:8.2f} s  {label} {'down' if event.down else 'up'}{note}",
            file=self.out,
        )


def _test(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    if _windows_only(out):
        return EXIT_SETUP
    applog.setup(f"{VOICE}-test", cfg.logs_dir)
    key_log = _KeyLog(out)
    voice = VoiceClient(
        voice_settings,
        _TestCore(out),
        transcriber=_SegmentReport(out, args.save),
        speaker=hooks.LogSpeaker(lambda text: None),
        observer=key_log,
    )
    pipe, hook, watchdog = _windows_parts(voice, voice_settings.input_device)
    print(
        f"Keys: {_describe(voice_settings)}.\nFocus GTNH (fullscreen, then borderless), "
        "press each key and talk on the talk key; nothing should happen in GTNH or "
        "Discord. Ctrl+C ends the test.",
        file=out,
    )
    stop = threading.Event()
    timer = threading.Timer(args.seconds, stop.set)
    timer.daemon = True  # Ctrl+C before --seconds ran out mustn't leave it waiting
    if args.seconds > 0:
        timer.start()
    try:
        Process(voice, pipe, hook, watchdog=watchdog).run(stop)
    except AudioError as exc:
        print(f"Can't open the microphone: {exc}", file=out)
        return EXIT_PROBLEM
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        stop.set()
    print("\nSummary", file=out)
    print(f"  microphone: {pipe.source.name}, {pipe.blocks} blocks", file=out)
    print(f"  blocks dropped: {pipe.dropped}", file=out)
    for role, key in voice_settings.all_keys().items():
        print(
            f"  {role.replace('_', ' ')} {key.label}: {key_log.seen.get(key.label, 0)} presses",
            file=out,
        )
    if key_log.lags:
        lags = sorted(key_log.lags)
        print(
            f"  hook delay: median {lags[len(lags) // 2]} ms, max {lags[-1]} ms",
            file=out,
        )
    print(
        f"  watchdog: {watchdog.reinstalls} re-installs, {key_log.missed} missed keys passed on",
        file=out,
    )
    return 0


# --- gos voice keys ---------------------------------------------------------------------------


def _game_folder(instance: Path) -> Path | None:
    for name in GAME_DIRS:
        if (instance / name).is_dir():
            return instance / name
    return None


def _keys(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    instance = cfg.instance(args.instance)
    game = _game_folder(instance)
    if game is None:
        print(f"{instance} has no minecraft or .minecraft folder.", file=out)
        return EXIT_SETUP
    options = game / "options.txt"
    if not options.is_file():
        print(f"{options} doesn't exist yet: start {instance.name} once.", file=out)
        return EXIT_SETUP
    # Read once and let go (gamestate.safe_open); options.txt is cp1252 on the Java 8 build.
    text = read_bytes(options).decode("utf-8", "replace")
    found = keys.conflicts(voice_settings.all_keys(), keys.bindings(text))
    print(f"GTNH bindings in {options}:", file=out)
    clashes = 0
    for role, key in voice_settings.all_keys().items():
        hits = found[role]
        if hits is None:
            verdict = "GTNH's Java 8 build can't bind it; on the Java 17+ build check Controls"
        elif hits:
            clashes += 1
            verdict = "BOUND in GTNH to " + ", ".join(hits)
        else:
            verdict = "free in GTNH"
        print(f"  {role.replace('_', ' ')}: {key.label}: {verdict}", file=out)
    print(
        "Discord: open Settings > Keybinds and check that none of these keys is bound.",
        file=out,
    )
    return EXIT_PROBLEM if clashes else 0


# --- gos voice devices ------------------------------------------------------------------------


def _devices(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    if _windows_only(out):
        return EXIT_SETUP
    try:
        devices = audio.capture_devices()
        chosen = audio.find_device(voice_settings.input_device, devices)
    except AudioError as exc:
        print(f"Capture devices: {exc}", file=out)
        return EXIT_PROBLEM
    for device in devices:
        marks = [
            m
            for m, on in (("default", device.default), ("used", device == chosen))
            if on
        ]
        print(
            f"  {device.name}" + (f"  [{', '.join(marks)}]" if marks else ""), file=out
        )
    return 0


# --- gos voice bench --------------------------------------------------------------------------

STAGES = ("held_ms", "close_ms", "stt_ms", "core_ms", "speak_ms", "total_ms")


def turn_records(logs: Path) -> list[dict[str, Any]]:
    """Every ``turn`` record in ``logs/voice.log`` and its rotated copies, oldest first
    (applog keeps ``voice.log.1`` to ``.5``, ``.1`` the newest)."""
    rotated = []
    for path in logs.glob(f"{VOICE}.log.*"):
        number = path.name.removeprefix(f"{VOICE}.log.")
        if number.isdigit():
            rotated.append((int(number), path))
    paths = [path for _, path in sorted(rotated, reverse=True)]
    found = []
    for path in [*paths, logs / f"{VOICE}.log"]:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("msg") == "turn":
                found.append(entry)
    return found


def _p(values: list[int], q: float) -> int:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _bench(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    turns = turn_records(cfg.logs_dir)[-max(1, args.last) :]
    answered = [t for t in turns if t.get("outcome") in ("answered", "stopped")]
    if not answered:
        print(f"No answered turns in {cfg.logs_dir / (VOICE + '.log')} yet.", file=out)
        return EXIT_PROBLEM
    met = True
    for trigger in ("key", "wake"):
        rows = [t for t in answered if t.get("trigger") == trigger]
        if not rows:
            continue
        print(f"{trigger}: {len(rows)} turns", file=out)
        for stage in STAGES:
            values = [int(t[stage]) for t in rows if isinstance(t.get(stage), int)]
            if values:
                print(
                    f"  {stage[:-3]:>6}: P50 {_p(values, 0.5)} ms, P90 {_p(values, 0.9)} ms"
                    f" ({len(values)})",
                    file=out,
                )
        totals = [
            int(t["total_ms"]) for t in rows if isinstance(t.get("total_ms"), int)
        ]
        if not totals:
            print(
                "  no turn reached first audio (text-to-speech arrives with WO-5.3)",
                file=out,
            )
            met = False
            continue
        p50 = _p(totals, 0.5)
        ok = p50 <= TARGETS_MS[trigger]
        met = met and ok
        print(
            f"  PM14: P50 {p50} ms to first audio, target {TARGETS_MS[trigger]} ms: "
            + ("met" if ok else "MISSED"),
            file=out,
        )
    return 0 if met else EXIT_PROBLEM


# --- gos voice stt, record, eval (WO-5.2) -----------------------------------------------------


def _stt_settings(
    voice_settings: VoiceSettings, args: argparse.Namespace
) -> VoiceSettings:
    """--engine and --model over gos.toml's [voice] stt_engine and stt_model."""
    return replace(
        voice_settings,
        stt_engine=args.engine or voice_settings.stt_engine,
        stt_model=args.model or voice_settings.stt_model,
    )


def _load_stt(
    cfg: Config,
    voice_settings: VoiceSettings,
    out: TextIO,
    glossary: Glossary | None = None,
) -> stt.Stt | None:
    """The engine, with what was tried first and why; None (said) when none starts."""
    stt.find_cuda(cfg.gos_home)
    gpu = stt.cuda_problem()
    print(f"GPU for Whisper: {'yes' if gpu is None else 'no, ' + gpu}", file=out)
    try:
        engine = stt.load(
            voice_settings.stt_engine,
            voice_settings.stt_model,
            cfg.gos_home,
            glossary=glossary,
            note=lambda message: print(message, file=out),
        )
    except stt.SttError as exc:
        print(f"No speech-to-text engine could start: {exc}", file=out)
        return None
    for skipped in engine.skipped:
        print(f"  not used: {skipped}", file=out)
    print(f"Speech-to-text: {engine.label}, loaded in {engine.load_ms} ms", file=out)
    source = engine.glossary.path or "none"
    if engine.backend.prompt:
        forms = engine.backend.prompt.count(", ") + 1
        print(f"  prompt: {forms} glossary forms from {source}", file=out)
    else:
        print(
            f"  prompt: none ({engine.name} takes none); spellings from {source}",
            file=out,
        )
    return engine


def _stt(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    engine = _load_stt(cfg, _stt_settings(voice_settings, args), out)
    if engine is None:
        return EXIT_PROBLEM
    code = 0
    for path in args.wav:
        try:
            pcm = stt_eval.read_wav(path)
        except (stt_eval.EvalError, OSError) as exc:
            print(f"{path}: {exc}", file=out)
            code = EXIT_PROBLEM
            continue
        started = time.perf_counter()
        said = engine.transcribe(stt_eval.segment_of(pcm))
        ms = round((time.perf_counter() - started) * 1000)
        print(f"{path.name}: {said!r} ({ms} ms)", file=out)
    return code


@dataclass
class _QuietCore:
    """``gos voice record``'s core: nothing is sent, nothing is printed."""

    def ask(self, text: str) -> dict[str, Any]:
        return {"text": "", "lane": "record"}

    def prewarm(self) -> None:
        pass

    def ui(self, action: str) -> None:
        pass

    def state(self, state: str, trigger: str | None) -> None:
        pass


def _record(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    if _windows_only(out):
        return EXIT_SETUP
    folder = args.dir or eval_audio(cfg)
    try:
        utterances = stt_eval.load_set(args.set or stt_eval.SET)
        only = [i.strip() for i in (args.only or "").split(",") if i.strip()]
        rows = stt_eval.to_record(utterances, folder, only)
    except (OSError, stt_eval.EvalError) as exc:
        print(f"Can't use the set: {exc}", file=out)
        return EXIT_SETUP
    if not rows:
        print(
            f"Every line has a recording in {folder}; --only s01,s02 records lines again.",
            file=out,
        )
        return 0
    applog.setup(f"{VOICE}-record", cfg.logs_dir)
    talk = replace(
        voice_settings, primary_trigger=settings.KEY, backup_trigger=settings.NONE
    )
    recording = stt_eval.Recording(rows, folder, out)
    voice = VoiceClient(
        talk,
        _QuietCore(),
        transcriber=recording,
        speaker=hooks.LogSpeaker(lambda text: None),
    )
    pipe, hook, watchdog = _windows_parts(voice, talk.input_device)
    print(
        f"{len(rows)} lines to record into {folder}. For each: hold {talk.talk_key.label}, "
        "say it as you would ask GOS, release. Ctrl+C stops; run it again to go on.",
        file=out,
    )
    recording.prompt()
    try:
        Process(voice, pipe, hook, watchdog=watchdog).run(recording.done)
    except AudioError as exc:
        print(f"Can't open the microphone: {exc}", file=out)
        return EXIT_PROBLEM
    except KeyboardInterrupt:
        pass
    print(f"{recording.index} of {len(rows)} recorded.", file=out)
    return 0


def _eval(
    cfg: Config, voice_settings: VoiceSettings, args: argparse.Namespace, out: TextIO
) -> int:
    from gregtec.agents.tools import build_registry  # only this command needs the tools
    from gregtec.db import access

    try:
        utterances = stt_eval.load_set(args.set or stt_eval.SET)
    except (OSError, stt_eval.EvalError) as exc:
        print(f"Can't use the set: {exc}", file=out)
        return EXIT_SETUP
    if args.limit is not None:
        if args.limit < 1:
            print("--limit must be at least 1", file=out)
            return EXIT_SETUP
        utterances = utterances[: args.limit]
    db = access.db_path(cfg.gos_home)
    if not db.is_file():
        print(
            f"No DB at {db}: run gos export collect and gos db build first.", file=out
        )
        return EXIT_SETUP
    glossary = Glossary.find(cfg.gos_home / "vault")
    registry = build_registry(db, glossary)
    if args.check:
        rows = stt_eval.run(utterances, registry, None, Path())
        bad = [row for row in rows if not row.scorable]
        for row in bad:
            print(stt_eval.outcome(row), file=out)
        print(
            f"{len(rows) - len(bad)} of {len(rows)} lines resolve to their key on {db}",
            file=out,
        )
        return EXIT_PROBLEM if bad else 0
    audio_dir = args.audio or eval_audio(cfg)
    if not audio_dir.is_dir():
        print(
            f"No recordings in {audio_dir}: run gos voice record first (or give --audio).",
            file=out,
        )
        return EXIT_SETUP
    engine = _load_stt(cfg, _stt_settings(voice_settings, args), out, glossary)
    if engine is None:
        return EXIT_PROBLEM
    rows = stt_eval.run(
        utterances,
        registry,
        engine,
        audio_dir,
        each=lambda row: print(stt_eval.outcome(row), file=out),
    )
    summary = stt_eval.summarise(rows)
    stt_eval.report(summary, engine.label, out)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = args.out or cfg.data_dir / "reports" / f"stt-eval-{stamp}.tsv"
    stt_eval.write_tsv(rows, path)
    print(f"report: {path}", file=out)
    return 0 if summary.met else EXIT_PROBLEM
