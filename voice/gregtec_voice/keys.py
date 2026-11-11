"""Key names for the talk key and the hotkeys, and the check against GTNH's bindings (D180).

A key is one of F13-F24, Pause or Scroll Lock: keys that type nothing, edit nothing, and that
neither GTNH (Minecraft, its mods, NEI) nor Discord binds by default. Names are case-insensitive,
and spaces or hyphens count as ``_`` ("F13", "Scroll Lock", "scroll-lock").

Refused, with the reason: F1-F12 (Minecraft 1.7.10 hard-codes F1 and F3; GTNH binds most of the
rest: screenshot, view, fullscreen, NEI's overlays), the keys that type or edit text in GTNH's
chat and Discord (letters, digits, Space, Enter, Tab, Escape, Backspace, punctuation, Insert,
Delete, Home, End, Page Up, Page Down, the arrows, the numpad), the Menu key, modifiers and lock
keys, Print Screen (Windows takes it for screenshots), combinations, and mouse buttons. A mouse
button would need a low-level *mouse* hook, which runs Python on every mouse movement in game;
map the button to one of F13-F24 in the mouse's own software instead.

**GTNH's bindings** are ``key_<name>:<code>`` lines in the game folder's ``options.txt``, coded
as LWJGL 2's ``Keyboard.KEY_*`` (DirectInput scan codes; lwjgl3ify, on GTNH's Java 17+ build,
keeps that format), mouse buttons as ``code - 100`` and ``0`` for unbound. GTNH's NEI registers
its keys as Minecraft key bindings (``key_nei.options.keys.*``), so they are there too.
:func:`bindings` reads them, :func:`conflicts` lists the ones on a configured key. F20-F24 have no
LWJGL 2 code, so GTNH's Java 8 build can't bind them; on the Java 17+ build check its Controls
menu by hand.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    name: str  # canonical, e.g. "f13"
    label: str  # for people, e.g. "F13"
    vk: int  # Windows virtual-key code
    lwjgl: int | None  # LWJGL 2 key code (options.txt); None: LWJGL 2 has none


def _f_keys() -> list[Key]:
    # LWJGL 2: F13-F18 0x64-0x69, F19 0x71, none above (GTNH's Java 8 build can't bind those).
    lwjgl = [*range(0x64, 0x6A), 0x71]
    return [
        Key(f"f{n}", f"F{n}", 0x6F + n, lwjgl[n - 13] if n - 13 < len(lwjgl) else None)
        for n in range(13, 25)
    ]


KEYS: dict[str, Key] = {
    k.name: k
    for k in [
        *_f_keys(),
        Key("pause", "Pause", 0x13, 0xC5),
        Key("scroll_lock", "Scroll Lock", 0x91, 0x46),
    ]
}
BY_VK = {k.vk: k for k in KEYS.values()}
_LABELS = {k.label.casefold(): k for k in KEYS.values()}

ALIASES = {
    "scrolllock": "scroll_lock",
    "scroll": "scroll_lock",
    "break": "pause",
    "pause_break": "pause",
    # refused keys' other names, so the reason is given for them too
    "ins": "insert",
    "del": "delete",
    "pageup": "page_up",
    "pgup": "page_up",
    "prior": "page_up",
    "pagedown": "page_down",
    "pgdn": "page_down",
    "next": "page_down",
    **{f"arrow_{d}": d for d in ("left", "up", "right", "down")},
    **{f"{d}_arrow": d for d in ("left", "up", "right", "down")},
    "menu": "apps",
    "context_menu": "apps",
    **{f"numpad{n}": f"num_{n}" for n in range(10)},
    **{f"numpad_{n}": f"num_{n}" for n in range(10)},
    **{f"num{n}": f"num_{n}" for n in range(10)},
    "num_*": "num_multiply",
    "num_+": "num_add",
    "num_-": "num_subtract",
    "num_.": "num_decimal",
    "num_/": "num_divide",
}

_TYPES = "it types or edits text in GTNH's chat and Discord"
_F1_F12 = (
    "Minecraft hard-codes F1 and F3, and GTNH binds most of F1-F12 (screenshot, view, "
    "fullscreen, NEI's overlays): use one of F13-F24"
)
REFUSED = {
    **dict.fromkeys([f"f{n}" for n in range(1, 13)], _F1_F12),
    **dict.fromkeys(
        [
            "space",
            "enter",
            "return",
            "tab",
            "escape",
            "esc",
            "backspace",
            "num_enter",
        ],
        _TYPES,
    ),
    **dict.fromkeys(
        [
            "insert",
            "delete",
            "home",
            "end",
            "page_up",
            "page_down",
            "left",
            "up",
            "right",
            "down",
        ],
        f"{_TYPES} (and NEI binds the arrows, Page Up and Page Down)",
    ),
    **dict.fromkeys(
        [
            *(f"num_{n}" for n in range(10)),
            "num_multiply",
            "num_add",
            "num_subtract",
            "num_decimal",
            "num_divide",
        ],
        f"{_TYPES} with Num Lock on (and NEI binds Num 0)",
    ),
    "apps": "it opens context menus in Discord and other Windows apps",
    **dict.fromkeys(
        [
            m
            for base in ("shift", "ctrl", "control", "alt", "win", "cmd", "super")
            for m in (base, f"left_{base}", f"right_{base}", f"l{base}", f"r{base}")
        ],
        "it is a modifier, which GTNH and Windows use with other keys",
    ),
    **dict.fromkeys(
        ["caps_lock", "capslock", "num_lock", "numlock"],
        "it is a lock key, which changes how other keys type",
    ),
    **dict.fromkeys(
        ["print_screen", "printscreen", "prtsc", "print"],
        "Windows takes it for screenshots",
    ),
    **dict.fromkeys(
        [
            "mouse1",
            "mouse2",
            "mouse3",
            "mouse4",
            "mouse5",
            "x1",
            "x2",
            "xbutton1",
            "xbutton2",
            "left_click",
            "right_click",
            "middle_click",
            "mouse_left",
            "mouse_right",
            "mouse_middle",
        ],
        "it is a mouse button: map it to one of F13-F24 in the mouse's software and use that",
    ),
}


class KeyNameError(ValueError):
    """A key name that isn't one of :data:`KEYS` (the message says why)."""


_NUM_MINUS = re.compile(r"num(?:pad)?[\s_]*-")  # "-" is otherwise a word break


def normalise(text: str) -> str:
    return re.sub(r"[\s\-]+", "_", text.strip().casefold())


def parse(text: str) -> Key:
    """The key ``text`` names. Raises :class:`KeyNameError` saying why a name isn't allowed."""
    if not isinstance(text, str) or not text.strip():
        raise KeyNameError("a key name can't be empty")
    squeezed = " ".join(text.split()).casefold()
    if found := _LABELS.get(squeezed):
        return found  # as gos voice prints it: "Scroll Lock"
    name = "num_subtract" if _NUM_MINUS.fullmatch(squeezed) else normalise(text)
    if "+" in name and name not in ALIASES and len(name) > 1:
        raise KeyNameError(f"{text!r}: one key only, no combinations")
    name = ALIASES.get(name, name)
    if name in KEYS:
        return KEYS[name]
    if name in REFUSED:
        raise KeyNameError(f"{text!r}: {REFUSED[name]}")
    if len(name) == 1:
        raise KeyNameError(f"{text!r}: {_TYPES}")
    raise KeyNameError(
        f"{text!r} isn't a key name GOS knows; use one of F13-F24, Pause or Scroll Lock "
        "(free in GTNH and Discord by default)"
    )


def bindings(options_txt: str) -> dict[int, list[str]]:
    """LWJGL key code -> the GTNH bindings on it, from ``options.txt`` text.

    Each binding is ``key_<name>:<code>``; mouse buttons (negative codes) and unbound ones
    (``0``) are left out, since a :class:`Key` is never either. A value with more after the code
    (``30:SHIFT``) counts by its code.
    """
    found: dict[int, list[str]] = {}
    for line in options_txt.splitlines():
        name, sep, value = line.strip().partition(":")
        if not sep or not name.startswith("key_"):
            continue
        try:
            code = int(value.split(":")[0])
        except ValueError:
            continue
        if code > 0:
            found.setdefault(code, []).append(name.removeprefix("key_"))
    return found


def conflicts(
    keys: Mapping[str, Key], bound: Mapping[int, list[str]]
) -> dict[str, list[str] | None]:
    """For each ``role -> key``: the GTNH bindings on it (empty: free), None when GTNH has no
    code for the key (LWJGL 2), so options.txt can't tell."""
    return {
        role: (None if key.lwjgl is None else sorted(bound.get(key.lwjgl, [])))
        for role, key in keys.items()
    }
