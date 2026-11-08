"""WO-5.1: ``gos voice keys`` (GTNH bindings on the configured keys), ``gos voice bench``
(PM14 from the turn records), the Windows-only commands and the core's ``gos voice``
passthrough. The instance folders here are made up in tmp_path, never a real instance."""

from __future__ import annotations

import builtins
import io
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gregtec import applog, voicecli
from gregtec import cli as gos_cli
from gregtec.api.server import bind
from gregtec_voice import audio, cli, ptt

OPTIONS = "key_key.jump:57\nkey_key.journeymap.map:100\nkey_key.drop:16\n"


def setup(
    tmp_path: Path,
    options: str | None = OPTIONS,
    voice: str = "",
    game: str = ".minecraft",
    port: int | None = None,
) -> Path:
    instances = tmp_path / "instances"
    lab = instances / "GTNH-Lab"
    (lab / game).mkdir(parents=True)
    (lab / "instance.cfg").write_text("", encoding="utf-8")
    if options is not None:
        (lab / game / "options.txt").write_text(options, encoding="utf-8")
    toml = tmp_path / "gos.toml"
    core = "" if port is None else f"[core]\nport = {port}\n"
    toml.write_text(
        f'{core}[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n'
        f'prism_instances = "{instances.as_posix()}"\n[voice]\n{voice}\n',
        encoding="utf-8",
    )
    return toml


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(list(argv), out), out.getvalue()


def test_keys_lists_the_gtnh_bindings_on_each_key(tmp_path: Path) -> None:
    code, out = run("keys", "lab", "--config", str(setup(tmp_path)))
    assert code == 1  # F13 is bound
    assert "talk: F13: BOUND in GTNH to key.journeymap.map" in out
    assert "confirm: F14: free in GTNH" in out
    assert "Discord: open Settings > Keybinds" in out


def test_keys_passes_when_every_key_is_free(tmp_path: Path) -> None:
    toml = setup(
        tmp_path, voice='talk_key = "F17"\nnext_card_key = "F24"', game="minecraft"
    )
    code, out = run("keys", "lab", "--config", str(toml))
    assert code == 0
    assert "talk: F17: free in GTNH" in out
    assert "next card: F24: GTNH's Java 8 build can't bind it" in out


def test_keys_needs_the_game_folder_and_options(tmp_path: Path) -> None:
    code, out = run("keys", "lab", "--config", str(setup(tmp_path, options=None)))
    assert code == 2 and "doesn't exist yet: start GTNH-Lab once" in out
    code, out = run("keys", "play", "--config", str(setup(tmp_path / "b")))
    assert code == 2 and "GTNH-Play has no minecraft or .minecraft folder" in out


def test_a_bad_voice_key_is_reported(tmp_path: Path) -> None:
    code, out = run(
        "keys", "lab", "--config", str(setup(tmp_path, voice='talk_key = "mouse4"'))
    )
    assert code == 2 and "Can't read gos.toml" in out and "mouse button" in out


def turn(trigger: str, total: int | None, **kw: Any) -> str:  # noqa: ANN401
    record = {
        "ts": "2026-11-07T12:00:00.000+00:00",
        "level": "info",
        "process": "voice",
        "logger": "gregtec_voice.client",
        "msg": "turn",
        "trigger": trigger,
        "outcome": "answered",
        "held_ms": 1200,
        "close_ms": 130,
        "stt_ms": 250,
        "core_ms": 1000,
        "speak_ms": None if total is None else 300,
        "total_ms": total,
        **kw,
    }
    return json.dumps(record)


def test_bench_reports_pm14_from_the_turn_records(tmp_path: Path) -> None:
    toml = setup(tmp_path)
    logs = tmp_path / "home" / "logs"
    logs.mkdir(parents=True)
    # voice.log.1 holds older turns than voice.log (applog's rotation)
    (logs / "voice.log.1").write_text(
        "\n".join(turn("key", t) for t in (1500, 1600, 1700)) + "\n", encoding="utf-8"
    )
    (logs / "voice.log").write_text(
        "\n".join(
            [
                turn("key", 1650),
                turn("key", 1800),
                turn("wake", 2500),
                turn("wake", 2100),
                turn("wake", 2600),
                turn("key", None, outcome="nothing said"),
                '{"msg": "voice client starting"}',
                "not json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "voice-test.log").write_text(turn("key", 99999) + "\n", encoding="utf-8")
    code, out = run("bench", "--config", str(toml))
    assert "key: 5 turns" in out and "wake: 3 turns" in out
    assert "PM14: P50 1650 ms to first audio, target 1700 ms: met" in out
    assert "PM14: P50 2500 ms to first audio, target 2200 ms: MISSED" in out
    assert "   stt: P50 250 ms, P90 250 ms (5)" in out
    assert code == 1  # the wake word missed its target
    # the last three turns: wake 2100 and 2600 answered (P50 2100), one with nothing said
    code, out = run("bench", "--config", str(toml), "--last", "3")
    assert code == 0 and "wake: 2 turns" in out and "key:" not in out


def test_bench_without_audio_or_turns(tmp_path: Path) -> None:
    toml = setup(tmp_path)
    code, out = run("bench", "--config", str(toml))
    assert code == 1 and "No answered turns" in out
    logs = tmp_path / "home" / "logs"
    logs.mkdir(parents=True)
    (logs / "voice.log").write_text(turn("key", None) + "\n", encoding="utf-8")
    code, out = run("bench", "--config", str(toml))
    assert code == 1 and "text-to-speech arrives with WO-5.3" in out


@pytest.mark.skipif(sys.platform == "win32", reason="Linux: no WASAPI or key hook")
@pytest.mark.parametrize("command", ["run", "test", "devices"])
def test_the_windows_commands_say_so_elsewhere(tmp_path: Path, command: str) -> None:
    code, out = run(command, "--config", str(setup(tmp_path)))
    assert code == 2 and "run on Windows only" in out


# --- gos voice run and test with the Windows parts faked (the microphone, hook, key state)


def fake_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, interrupt: bool = False
) -> None:
    """``run`` and ``test`` past the Windows check, with a fake microphone, hook and key state.
    ``interrupt``: Ctrl+C as the hook is installed."""

    def parts(
        voice: Any,  # noqa: ANN401 - a VoiceClient
        device: str,
    ) -> tuple[audio.AudioPipe, ptt.FakeHook, ptt.Watchdog]:
        key_filter = ptt.KeyFilter(voice.watched, voice.post)
        hook = ptt.FakeHook(key_filter)
        if interrupt:

            def ctrl_c() -> None:
                raise KeyboardInterrupt

            hook.install = ctrl_c  # type: ignore[method-assign]
        watchdog = ptt.Watchdog(hook, ptt.FakeKeyState(), key_filter)
        return audio.AudioPipe(audio.FakeSource(), voice.recorder), hook, watchdog

    monkeypatch.setattr(cli, "_windows_only", lambda out: False)
    monkeypatch.setattr(cli, "_windows_parts", parts)
    # not the root logger's handlers: a file there would outlive tmp_path
    monkeypatch.setattr(applog, "setup", lambda process, logs_dir: tmp_path / "x.log")


def live_timers() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if isinstance(t, threading.Timer)]


def test_voice_test_stops_after_its_seconds_and_sums_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_windows(monkeypatch, tmp_path)
    code, out = run("test", "--seconds", "0.3", "--config", str(setup(tmp_path)))
    assert code == 0
    assert "Keys: talk F13 (hold); confirm F14, cancel F15, next card F16." in out
    assert "blocks dropped: 0" in out and "watchdog: 0 re-installs" in out
    assert "talk F13: 0 presses" in out


def test_voice_test_ends_at_once_on_ctrl_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's L4: Ctrl+C before --seconds ran out used to leave the timer's thread
    holding the process until the seconds were over."""
    fake_windows(monkeypatch, tmp_path, interrupt=True)
    before = set(live_timers())
    started = time.monotonic()
    code, out = run("test", "--seconds", "600", "--config", str(setup(tmp_path)))
    assert code == 0 and "Summary" in out
    assert time.monotonic() - started < 5
    deadline = time.monotonic() + 2
    while set(live_timers()) - before:  # cancelled: its thread ends at once
        assert time.monotonic() < deadline, "the --seconds timer is still waiting"
        time.sleep(0.02)


def test_voice_run_supervised_stops_on_a_stop_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_windows(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.stored, "get_key", lambda name: "t" * 43)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"stop\n")))
    sock = bind(0)  # a port nobody serves: the client is offline, and says so in time
    port = sock.getsockname()[1]
    sock.close()
    toml = setup(tmp_path, port=port)
    code, out = run("run", "--supervised", "--config", str(toml))
    assert code == 0 and "Voice client: talk F13 (hold)" in out


def test_voice_run_needs_the_voice_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_windows(monkeypatch, tmp_path)

    def missing(name: str) -> str:
        raise cli.stored.MissingKeyError(name)

    monkeypatch.setattr(cli.stored, "get_key", missing)
    code, out = run("run", "--config", str(setup(tmp_path)))
    assert code == 2 and "gos token new voice" in out


def test_gos_voice_hands_its_arguments_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    toml = setup(tmp_path, voice='talk_key = "F17"')
    assert gos_cli.main(["voice", "keys", "lab", "--config", str(toml)]) == 0
    assert "talk: F17: free in GTNH" in capsys.readouterr().out
    with pytest.raises(SystemExit) as helped:
        gos_cli.main(["voice", "--help"])  # --help reaches gos voice's own parser
    assert helped.value.code == 0
    assert "usage: gos voice" in capsys.readouterr().out


def test_gos_voice_without_the_voice_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def without_voice(name: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if name.startswith("gregtec_voice"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_voice)
    assert voicecli.run(voicecli.argparse.Namespace(voice_args=["bench"])) == 2
    assert "The voice client isn't installed here" in capsys.readouterr().err
