"""The voice client's ``[voice]`` keys in gos.toml (Appendix C; D180, D181).

| key              | default | meaning                                                         |
|------------------|---------|-----------------------------------------------------------------|
| primary_trigger  | wake    | wake (D57) or key                                               |
| backup_trigger   | key     | key, wake or none                                               |
| talk_key         | F13     | one key unbound in GTNH and Discord (QA:V8, :mod:`keys`)        |
| talk_mode        | hold    | hold (talk while held) or toggle (press to start, again to end) |
| confirm_key      | F14     | global hotkey: says "confirm" to the core (D59)                 |
| cancel_key       | F15     | global hotkey: drops what is being recorded or said, then       |
|                  |         | says "cancel" to the core                                       |
| next_card_key    | F16     | global hotkey: the dashboard shows its next card                |
| input_device     | ""      | "" = Windows' default capture device (WASAPI), else a part of   |
|                  |         | a device name from ``gos voice devices``                        |

The four keys must differ. The other Appendix C ``[voice]`` keys (wake engine and phrase, STT,
TTS, output endpoint) belong to WO-5.2 to WO-5.5 and are ignored here. The core's own keys
(``[core] port``, ``[paths] gos_home``) come from :func:`gregtec.config.load`.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec import config
from gregtec.config import Config
from gregtec_voice import keys
from gregtec_voice.keys import Key

WAKE, KEY, NONE = "wake", "key", "none"
HOLD, TOGGLE = "hold", "toggle"
HOTKEYS = ("confirm", "cancel", "next_card")


@dataclass(frozen=True)
class VoiceSettings:
    primary_trigger: str = WAKE
    backup_trigger: str = KEY
    talk_key: Key = field(default_factory=lambda: keys.KEYS["f13"])
    talk_mode: str = HOLD
    confirm_key: Key = field(default_factory=lambda: keys.KEYS["f14"])
    cancel_key: Key = field(default_factory=lambda: keys.KEYS["f15"])
    next_card_key: Key = field(default_factory=lambda: keys.KEYS["f16"])
    input_device: str = ""

    @property
    def key_trigger(self) -> bool:
        return KEY in (self.primary_trigger, self.backup_trigger)

    @property
    def wake_trigger(self) -> bool:
        return WAKE in (self.primary_trigger, self.backup_trigger)

    def hotkeys(self) -> dict[str, Key]:
        """Hotkey name -> key, in :data:`HOTKEYS` order."""
        return {name: getattr(self, f"{name}_key") for name in HOTKEYS}

    def all_keys(self) -> dict[str, Key]:
        """Every configured key by role: talk, confirm, cancel, next_card."""
        return {"talk": self.talk_key, **self.hotkeys()}


class SettingsError(ValueError):
    """One or more ``[voice]`` keys are wrong; the message lists each."""


def parse(table: Mapping[str, Any]) -> VoiceSettings:
    """The ``[voice]`` table as settings. Raises :class:`SettingsError` listing every problem."""
    defaults = VoiceSettings()
    problems: list[str] = []

    def choice(key: str, allowed: tuple[str, ...], default: str) -> str:
        value = table.get(key, default)
        if value not in allowed:
            problems.append(f"[voice] {key} must be one of {', '.join(allowed)}")
            return default
        return str(value)

    def key_of(name: str, default: Key) -> Key:
        value = table.get(name)
        if value is None:
            return default
        if not isinstance(value, str):
            problems.append(f'[voice] {name} must be a key name, e.g. "F13"')
            return default
        try:
            return keys.parse(value)
        except keys.KeyNameError as exc:
            problems.append(f"[voice] {name}: {exc}")
            return default

    primary = choice("primary_trigger", (WAKE, KEY), defaults.primary_trigger)
    backup = choice("backup_trigger", (KEY, WAKE, NONE), defaults.backup_trigger)
    if primary == backup:
        problems.append("[voice] backup_trigger must differ from primary_trigger")
    device = table.get("input_device", defaults.input_device)
    if not isinstance(device, str):
        problems.append("[voice] input_device must be a string")
        device = defaults.input_device
    found = VoiceSettings(
        primary_trigger=primary,
        backup_trigger=backup,
        talk_key=key_of("talk_key", defaults.talk_key),
        talk_mode=choice("talk_mode", (HOLD, TOGGLE), defaults.talk_mode),
        confirm_key=key_of("confirm_key", defaults.confirm_key),
        cancel_key=key_of("cancel_key", defaults.cancel_key),
        next_card_key=key_of("next_card_key", defaults.next_card_key),
        input_device=device.strip(),
    )
    roles: dict[str, list[str]] = {}
    for role, key in found.all_keys().items():
        roles.setdefault(key.name, []).append(role)
    for name, shared in roles.items():
        if len(shared) > 1:
            problems.append(
                f"[voice] {' and '.join(shared)} are both {keys.KEYS[name].label}: "
                "each needs its own key"
            )
    if problems:
        raise SettingsError("; ".join(problems))
    return found


def load(path: Path | None = None) -> tuple[Config, VoiceSettings]:
    """gos.toml's core config and its ``[voice]`` settings (default file: config.default_path).

    Raises ``ValueError``: a file that doesn't parse, a bad core key (as :func:`config.load`
    does) or a bad ``[voice]`` key (:class:`SettingsError`).
    """
    cfg = config.load(path)
    if path is None:
        path = config.default_path()
        if not path.is_file():
            return cfg, VoiceSettings()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    table = data.get("voice", {})
    if not isinstance(table, dict):
        raise SettingsError("[voice] must be a table")
    return cfg, parse(table)
