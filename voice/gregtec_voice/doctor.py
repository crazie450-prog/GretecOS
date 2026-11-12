"""``gos voice doctor`` (WO-5.5, D58, D247): what GOS's voice client does to the PC's audio and
keys, for the Discord coexistence runbook (``docs/runbooks/discord_coexistence.md``).

It prints, and changes nothing:

- **capture**: WASAPI shared mode (never exclusive), 16 kHz mono, and the microphone
  ``[voice] input_device`` opens;
- **output**: the device speech and the wake chime play on (``[voice] output_endpoint``, the
  Arctis Nova Elite's Chat endpoint, D56) and Windows' default devices, which GOS never changes;
- **ducking**: Windows' communications ducking (``UserDuckingPreference``, read only; WO-5.3's
  ``scripts/set_comm_ducking.ps1`` is what sets it, and Clayton runs that himself);
- **the hook**: what the low-level keyboard hook does (observe only: nothing suppressed, nothing
  injected) and, on Windows, that it installs and comes out again;
- **the four keys**, with :func:`gregtec_voice.keys.discord_warning` for the ones people often
  bind in Discord (D246).

"Windows' default devices" here are WASAPI's defaults, which follow the console role. Windows
keeps a separate Default Communication Device (what communications ducking and many voice
apps' "Default" follow); reading it needs Core Audio COM (``IMMDeviceEnumerator``), which the
voice client doesn't use, so the runbook has Clayton note it by hand (mmsys.cpl).

GOS can't see Discord's own settings (input and output device, keybinds, attenuation) and
never reads them: the runbook has Clayton check those by hand. Exit code 0 when nothing needs
attention, 1 when something does (each such line starts with ``!``).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TextIO

from gregtec_voice import audio, keys, playback, ptt
from gregtec_voice.audio import AudioError, Device
from gregtec_voice.settings import VoiceSettings

DUCKING_KEY = r"Software\Microsoft\Multimedia\Audio"
DUCKING_VALUE = "UserDuckingPreference"
DO_NOTHING = 3
DUCKING_LABELS = {
    0: "mute all other sounds",
    1: "reduce other sounds by 80%",
    2: "reduce other sounds by 50%",
    3: "do nothing",
}
NOT_HERE = "not checked: Windows only"


class Unavailable(Exception):
    """A check that can't run here (not Windows): the message says why."""


@dataclass(frozen=True)
class Probe:
    """Where the doctor looks; tests pass fakes, :func:`windows_probe` is the real PC."""

    capture_devices: Callable[[], list[Device]]
    playback_devices: Callable[[], list[Device]]
    ducking: Callable[[], int | None]  # None: not set (Windows' default, 80%)
    hook_check: Callable[[Sequence[int]], None]  # raises when the hook doesn't install


def read_ducking() -> int | None:
    """Windows' communications ducking for this account, read only (``KEY_READ``)."""
    if sys.platform != "win32":
        raise Unavailable(NOT_HERE)
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, DUCKING_KEY, 0, winreg.KEY_READ
        ) as key:
            value, _ = winreg.QueryValueEx(key, DUCKING_VALUE)
    except FileNotFoundError:
        return None
    return int(value)


def check_hook(watched: Sequence[int]) -> None:
    """Install the voice client's own hook (observe only) for a moment and take it out again."""
    if sys.platform != "win32":
        raise Unavailable(NOT_HERE)
    hook = ptt.WindowsHook(ptt.KeyFilter(watched, lambda event: None))
    hook.install()
    try:
        time.sleep(0.1)
        if not hook.alive():
            raise RuntimeError("the hook's thread stopped at once")
    finally:
        hook.uninstall()


def _windows_only() -> list[Device]:
    raise Unavailable(NOT_HERE)


def windows_probe() -> Probe:
    if sys.platform != "win32":
        return Probe(_windows_only, _windows_only, read_ducking, check_hook)
    return Probe(
        audio.capture_devices, playback.playback_devices, read_ducking, check_hook
    )


def _default(devices: Sequence[Device]) -> str:
    found = [d.name for d in devices if d.default]
    return found[0] if found else "none"


def report(voice: VoiceSettings, probe: Probe, out: TextIO) -> int:
    """Print the report; the number of lines that need attention."""
    problems = 0

    def say(line: str, problem: bool = False) -> None:
        nonlocal problems
        problems += problem
        print(("! " if problem else "  ") + line, file=out)

    print("Capture", file=out)
    mode = "exclusive" if audio.WASAPI_EXCLUSIVE else "shared"
    say(
        f"mode: WASAPI {mode} mode, {audio.SAMPLE_RATE // 1000} kHz mono, "
        f"{round(audio.BLOCK_S * 1000)} ms blocks; Windows converts the headset's format",
        problem=audio.WASAPI_EXCLUSIVE,
    )
    try:
        inputs = probe.capture_devices()
        say(
            f"microphone: {audio.find_device(voice.input_device, inputs).name} "
            f"(input_device = {voice.input_device!r})"
        )
        say(f"Windows' default microphone: {_default(inputs)} (GOS never changes it)")
    except Unavailable as exc:
        say(f"microphone: {exc}")
    except AudioError as exc:
        say(f"microphone: {exc}", problem=True)

    print("Output", file=out)
    say(f"mode: WASAPI {mode} mode", problem=audio.WASAPI_EXCLUSIVE)
    try:
        outputs = probe.playback_devices()
        pick = playback.pick_device(voice.output_endpoint, outputs)
        say(
            f"speech and the wake chime: {pick.device.name} "
            f"(output_endpoint = {voice.output_endpoint!r})"
        )
        if pick.why:
            say(pick.why, problem=True)
        say(f"Windows' default output: {_default(outputs)} (GOS never changes it)")
    except Unavailable as exc:
        say(f"speech and the wake chime: {exc}")
    except AudioError as exc:
        say(f"speech and the wake chime: {exc}", problem=True)

    print("Ducking", file=out)
    try:
        value = probe.ducking()
    except Unavailable as exc:
        say(f"Windows communications ducking: {exc}")
    except (OSError, TypeError, ValueError) as exc:  # ValueError: not a number (REG_SZ)
        say(f"Windows communications ducking: can't read it ({exc})", problem=True)
    else:
        label = (
            "not set (Windows' default: reduce other sounds by 80%)"
            if value is None
            else f"{value} ({DUCKING_LABELS.get(value, 'unknown')})"
        )
        if value == DO_NOTHING:
            say(f"Windows communications ducking: {label}")
        else:
            say(
                f"Windows communications ducking: {label}: run scripts\\set_comm_ducking.ps1 "
                "(docs/runbooks/tts.md, part E)",
                problem=True,
            )

    print("Key hook", file=out)
    say(
        "low-level keyboard hook (pynput, WH_KEYBOARD_LL): observes only; every key goes on "
        "to the game and Discord, nothing is suppressed or injected; watchdog every "
        f"{round(ptt.WATCH_EVERY_S * 1000)} ms"
    )
    try:
        probe.hook_check([k.vk for k in voice.all_keys().values()])
        say("hook: installs and comes out again")
    except Unavailable as exc:
        say(f"hook: {exc}")
    except Exception as exc:  # noqa: BLE001 - whatever pynput or Windows raised: report it
        say(f"hook: didn't install ({exc})", problem=True)

    print("Keys", file=out)
    triggers = f"{voice.primary_trigger} first, {voice.backup_trigger} as backup"
    say(f"triggers: {triggers}; talk mode {voice.talk_mode}")
    for role, key in voice.all_keys().items():
        say(f"{role.replace('_', ' ')}: {key.label}")
        if warning := keys.discord_warning(key):
            say(f"  Discord WARNING: {warning}")
    print("Discord", file=out)
    say(
        "GOS never opens, reads or changes Discord's process, settings or keybinds, and "
        "never mutes it (D58): check Discord's input and output device, keybinds and "
        "attenuation by hand (docs/runbooks/discord_coexistence.md)"
    )
    return problems
