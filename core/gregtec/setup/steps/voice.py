"""Steps 40 to 60: the headset, the talk key and the wake phrase (``[voice]`` in gos.toml).

- **Headset** (D56, D168): the microphone (``input_device``, the voice client's key, D181; empty
  is Windows' default) and the output the spoken answers go to (``output_endpoint``, Appendix C;
  "Chat", the Arctis Nova Elite's Chat endpoint, by default). Both are a part of a device name,
  picked from the devices the core lists (:mod:`gregtec.setup.devices`).
- **Talk key** (QA:V8, D180): checked by the voice client's own rules (``gregtec_voice.keys``):
  F13-F24, Pause or Scroll Lock, none bound by GTNH or Discord by default, and different from the
  confirm, cancel and next-card hotkeys.
- **Wake phrase** (PM08): never "GregTech" or "Gregtec", which Clayton says all session. WO-5.4
  picks the phrase after its false-trigger tests, so what is saved here is his choice until then.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from gregtec.setup.registry import ERROR, OK, WARN, Check, Context, Field, Outcome, Step

KEY_SUGGESTIONS = (
    *(f"F{n}" for n in range(13, 25)),
    "Pause",
    "Scroll Lock",
)
# [H§1.3]'s examples: uncommon syllables, a made-up proper noun.
WAKE_SUGGESTIONS = ("hey archivist", "okay tessera")
_PHRASE = re.compile(r"^[a-z]+(?:[ '-][a-z]+){1,4}$")
_FORBIDDEN = re.compile(r"greg\s*-?\s*te(ch|c|k)")  # PM08
# [H§1.3]: words said all session in GTNH talk; allowed, with a warning.
_RISKY = {"computer", "lv", "mv", "hv", "ev", "iv", "luv", "zpm", "uv", "uhv", "gt"}


def voice_table(ctx: Context, changes: Mapping[str, str]) -> dict[str, Any]:
    """gos.toml's ``[voice]`` with ``changes`` made (an empty value drops the key)."""
    table = dict(ctx.data.get("voice") or {})
    for key, value in changes.items():
        if value:
            table[key] = value
        else:
            table.pop(key, None)
    return table


def voice_problem(table: Mapping[str, Any]) -> str | None:
    """Why the voice client would refuse ``table``, or None (its own parser decides)."""
    try:
        from gregtec_voice import settings
    except ImportError:
        return "the voice client isn't installed here (uv sync installs it)"
    try:
        settings.parse(table)
    except settings.SettingsError as exc:
        return str(exc)
    return None


def _device_rows(ctx: Context, values: Mapping[str, str]) -> list[Check]:
    listing = ctx.devices()
    if listing.get("error"):
        return [Check("voice.devices", "Audio devices", WARN, str(listing["error"]))]
    rows = []
    for fid, kind, label in (
        ("input_device", "inputs", "Microphone"),
        ("output_endpoint", "outputs", "Spoken answers"),
    ):
        value = values.get(fid, "")
        names = [str(d["name"]) for d in listing.get(kind, [])]
        if not value:
            default = next(
                (str(d["name"]) for d in listing.get(kind, []) if d.get("default")),
                None,
            )
            rows.append(
                Check(
                    f"voice.{fid}",
                    label,
                    OK if default else WARN,
                    f"Windows' default: {default}"
                    if default
                    else "Windows has no default",
                )
            )
            continue
        hits = [n for n in names if value.casefold() in n.casefold()]
        if len(hits) == 1:
            rows.append(Check(f"voice.{fid}", label, OK, hits[0]))
        elif hits:
            rows.append(
                Check(
                    f"voice.{fid}",
                    label,
                    WARN,
                    f"{value!r} matches {len(hits)} devices: {'; '.join(hits)}",
                )
            )
        else:
            rows.append(
                Check(f"voice.{fid}", label, WARN, f"{value!r} isn't connected now")
            )
    if listing.get("provider") == "fake":
        rows.append(
            Check(
                "voice.provider",
                "Device list",
                WARN,
                "test devices: this core isn't on Windows, so no real device is listed",
            )
        )
    return rows


def check_headset(ctx: Context, values: Mapping[str, str]) -> Outcome:
    return Outcome(checks=_device_rows(ctx, values))


def check_keys(ctx: Context, values: Mapping[str, str]) -> Outcome:
    why = voice_problem(voice_table(ctx, values))
    if why is not None:
        return Outcome(problems={"talk_key": why})
    return Outcome(
        checks=[
            Check(
                "voice.talk_key",
                "Talk key",
                OK,
                "a key GTNH and Discord leave free by default; check Discord's keybinds, and "
                "run gos voice keys play to list GTNH's bindings on it",
            )
        ]
    )


def phrase_problem(phrase: str) -> str | None:
    if _FORBIDDEN.search(phrase.replace("_", " ")):
        return "never GregTech or Gregtec: you say those all session (PM08)"
    if not _PHRASE.match(phrase):
        return "two to five words, letters only, e.g. 'hey archivist'"
    return None


def check_wake(ctx: Context, values: Mapping[str, str]) -> Outcome:
    phrase = " ".join(values.get("wake_phrase", "").casefold().split())
    if not phrase:
        return Outcome(
            checks=[
                Check(
                    "voice.wake_phrase",
                    "Wake phrase",
                    WARN,
                    "none yet: the talk key works alone until one is set (WO-5.4)",
                )
            ]
        )
    why = phrase_problem(phrase)
    if why is not None:
        return Outcome(problems={"wake_phrase": why})
    risky = sorted(set(phrase.split()) & _RISKY)
    if risky:
        return Outcome(
            checks=[
                Check(
                    "voice.wake_phrase",
                    "Wake phrase",
                    WARN,
                    f"{', '.join(risky)}: words of GTNH talk can wake GOS by mistake [H§1.3]",
                )
            ]
        )
    return Outcome(
        checks=[
            Check(
                "voice.wake_phrase",
                "Wake phrase",
                OK,
                "WO-5.4's false-trigger tests have the last word",
            )
        ]
    )


def readback(table: Mapping[str, Any]) -> list[Check]:
    """The final checklist's voice row: the saved ``[voice]`` as the voice client reads it."""
    why = voice_problem(table)
    if why is not None:
        return [Check("voice.settings", "Voice settings", ERROR, why)]
    return [
        Check("voice.settings", "Voice settings", OK, "the voice client reads them")
    ]


STEPS = [
    Step(
        id="headset",
        title="Headset",
        order=40,
        intro=(
            "The microphone GOS listens on and the output its spoken answers go to. On the "
            "Arctis Nova Elite that is the Chat endpoint, so the Game/Chat dial sets the mix "
            "(D56)."
        ),
        fields=(
            Field(
                "input_device",
                "Microphone",
                "device",
                toml=("voice", "input_device"),
                devices="inputs",
                help="Empty: Windows' default recording device",
            ),
            Field(
                "output_endpoint",
                "Spoken answers",
                "device",
                toml=("voice", "output_endpoint"),
                devices="outputs",
                help="A part of the device's name is enough, e.g. Chat",
                default="Chat",
            ),
        ),
        check=check_headset,
    ),
    Step(
        id="talk_key",
        title="Talk key",
        order=50,
        intro=(
            "Hold it to talk (or press it twice, in toggle mode). It must be a key neither GTNH "
            "nor Discord uses (QA:V8): F13-F24, Pause or Scroll Lock. A mouse button works when "
            "its software maps it to one of F13-F24."
        ),
        fields=(
            Field(
                "talk_key",
                "Talk key",
                "key",
                toml=("voice", "talk_key"),
                suggestions=KEY_SUGGESTIONS,
                required=True,
                default="F13",
            ),
            Field(
                "talk_mode",
                "Mode",
                "choice",
                toml=("voice", "talk_mode"),
                options=(
                    ("hold", "Hold to talk"),
                    ("toggle", "Press to start and stop"),
                ),
                required=True,
                default="hold",
            ),
        ),
        check=check_keys,
    ),
    Step(
        id="wake",
        title="Wake phrase",
        order=60,
        intro=(
            "What you say to wake GOS (the main trigger, D57). Never GregTech or Gregtec (PM08). "
            "WO-5.4 tests candidates for false wakes and may suggest another."
        ),
        fields=(
            Field(
                "wake_phrase",
                "Wake phrase",
                "phrase",
                toml=("voice", "wake_phrase"),
                suggestions=WAKE_SUGGESTIONS,
                placeholder="hey archivist",
            ),
        ),
        check=check_wake,
        optional=True,
    ),
]
