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
| stt_engine       | auto    | speech-to-text (WO-5.2, D182): auto (Whisper on the GPU when    |
|                  |         | it works, else Parakeet on the CPU), whisper-gpu, whisper-cpu   |
|                  |         | or parakeet                                                     |
| stt_model        | large-v3-turbo | the Whisper model (Appendix C): a faster-whisper name   |
|                  |         | (large-v3-turbo, distil-large-v3, small.en, ...) or a Hugging   |
|                  |         | Face id of a CTranslate2 conversion ("org/name")                |
| wake_engine      | sherpa-onnx | the wake word (WO-5.4, D185): sherpa-onnx (keyword spotting, |
|                  |         | no training) or openwakeword (a custom model, ``wake_model``)   |
| wake_phrase      | hey archivist | what wakes GOS: 1-4 plain words, never "GregTech" or    |
|                  |         | "Gregtec" (PM08); sherpa-onnx spots it as written              |
| wake_model       | ""      | openwakeword only: the custom model's ``.onnx`` file            |
| wake_threshold   | 0       | 0 = the engine's tuned default (D186), else 0.05-0.95: higher   |
|                  |         | wakes less often, on noise and on Clayton alike                 |

The four keys must differ. The other Appendix C ``[voice]`` keys (TTS, output endpoint) belong
to WO-5.3 and WO-5.5 and are ignored here. The core's own keys
(``[core] port``, ``[paths] gos_home``) come from :func:`gregtec.config.load`; ``gos_home``
rides along here because the engines keep their models in ``GOS_HOME/models`` and read the
vault's glossary.
"""

from __future__ import annotations

import re
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
# speech-to-text engines (WO-5.2, D182); gregtec_voice.stt reads these
STT_ENGINES = ("auto", "whisper-gpu", "whisper-cpu", "parakeet")
STT_MODEL = "large-v3-turbo"  # Appendix C
# the wake word (WO-5.4, D185, D186); gregtec_voice.wake reads these
SHERPA, OPENWAKEWORD = "sherpa-onnx", "openwakeword"
WAKE_ENGINES = (SHERPA, OPENWAKEWORD)
WAKE_PHRASE = (
    "hey archivist"  # PM08's pick from the WO-5.4 harness, pending Clayton (D186)
)
_PHRASE_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
_MODEL_NAME = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?"
)


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
    stt_engine: str = "auto"
    stt_model: str = STT_MODEL
    wake_engine: str = SHERPA
    wake_phrase: str = WAKE_PHRASE
    wake_model: str = ""
    wake_threshold: float = 0.0  # 0: the engine's default
    gos_home: Path = field(default_factory=config.default_home)  # [paths] gos_home

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


def wake_phrase(value: object) -> str:
    """``value`` as a wake phrase: lower case, single spaces. Raises ``ValueError`` (or
    ``TypeError`` for a non-string) saying why it can't be one (PM08: never "GregTech" or
    "Gregtec", which Clayton says all session)."""
    if not isinstance(value, str):
        raise TypeError("must be a phrase in quotes")
    words = value.casefold().replace(",", " ").split()
    if not 1 <= len(words) <= 4 or not all(_PHRASE_WORD.fullmatch(w) for w in words):
        raise ValueError("must be 1 to 4 plain words (letters only)")
    if any(w.startswith("greg") for w in words) or "gregte" in "".join(words):
        raise ValueError(
            'must not contain "GregTech" or "Gregtec": you say them all session (PM08)'
        )
    if len("".join(words)) < 6:
        raise ValueError("is too short: short phrases wake on everyday words")
    return " ".join(words)


def parse(table: Mapping[str, Any], gos_home: Path | None = None) -> VoiceSettings:
    """The ``[voice]`` table as settings (``gos_home``: the core config's). Raises
    :class:`SettingsError` listing every problem."""
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
    model = table.get("stt_model", defaults.stt_model)
    if not isinstance(model, str) or not _MODEL_NAME.fullmatch(model.strip()):
        problems.append(
            '[voice] stt_model must be a Whisper model name ("large-v3-turbo", '
            '"distil-large-v3", ...) or a Hugging Face id ("org/name")'
        )
        model = defaults.stt_model
    try:
        phrase = wake_phrase(table.get("wake_phrase", defaults.wake_phrase))
    except (TypeError, ValueError) as exc:
        problems.append(f"[voice] wake_phrase {exc}")
        phrase = defaults.wake_phrase
    engine = choice("wake_engine", WAKE_ENGINES, defaults.wake_engine)
    wake_model = table.get("wake_model", defaults.wake_model)
    if not isinstance(wake_model, str):
        problems.append("[voice] wake_model must be a path in quotes")
        wake_model = defaults.wake_model
    wake_model = wake_model.strip()
    if engine == OPENWAKEWORD and not wake_model.casefold().endswith(".onnx"):
        problems.append(
            "[voice] wake_model must name the custom openWakeWord model's .onnx file "
            '(wake_engine = "openwakeword")'
        )
    threshold = table.get("wake_threshold", defaults.wake_threshold)
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, int | float)
        or not (threshold == 0 or 0.05 <= threshold <= 0.95)
    ):
        problems.append(
            "[voice] wake_threshold must be 0 (the default) or 0.05 to 0.95"
        )
        threshold = defaults.wake_threshold
    found = VoiceSettings(
        primary_trigger=primary,
        backup_trigger=backup,
        talk_key=key_of("talk_key", defaults.talk_key),
        talk_mode=choice("talk_mode", (HOLD, TOGGLE), defaults.talk_mode),
        confirm_key=key_of("confirm_key", defaults.confirm_key),
        cancel_key=key_of("cancel_key", defaults.cancel_key),
        next_card_key=key_of("next_card_key", defaults.next_card_key),
        input_device=device.strip(),
        stt_engine=choice("stt_engine", STT_ENGINES, defaults.stt_engine),
        stt_model=model.strip(),
        wake_engine=engine,
        wake_phrase=phrase,
        wake_model=wake_model,
        wake_threshold=float(threshold),
        gos_home=gos_home or defaults.gos_home,
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
            return cfg, VoiceSettings(gos_home=cfg.gos_home)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    table = data.get("voice", {})
    if not isinstance(table, dict):
        raise SettingsError("[voice] must be a table")
    return cfg, parse(table, cfg.gos_home)
