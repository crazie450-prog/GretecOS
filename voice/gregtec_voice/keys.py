"""Key names for the talk key and the hotkeys, and the check against GTNH's bindings (D180).

A key is one keyboard key that types nothing: F1-F24, Pause, Scroll Lock, the navigation block
(Insert, Delete, Home, End, Page Up, Page Down), the arrows, the numpad and the Menu key. Names are
case-insensitive, and spaces or hyphens count as ``_`` ("F13", "Scroll Lock", "page-up", "num 5").

Refused, with the reason: keys that type or edit text in GTNH's chat and Discord (letters,
digits, Space, Enter, Tab, Escape, Backspace, punctuation), modifiers and lock keys, Print Screen
(Windows takes it for screenshots), combinations, and mouse buttons. A mouse button would need a
low-level *mouse* hook, which runs Python on every mouse movement in game; map the button to one
of F13-F24 in the mouse's own software instead, which also makes it a key GTNH doesn't bind.

**GTNH's bindings** are ``key_<name>:<code>`` lines in the game folder's ``options.txt``, coded
as LWJGL 2's ``Keyboard.KEY_*`` (DirectInput scan codes; lwjgl3ify, on GTNH's Java 17+ build,
keeps that format), mouse buttons as ``code - 100`` and ``0`` for unbound. :func:`bindings` reads
them, :func:`conflicts` lists the ones on a configured key. F20-F24 have no LWJGL 2 code, so
GTNH's Java 8 build can't bind them; on the Java 17+ build check its Controls menu by hand.
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
    # LWJGL 2: F1-F10 0x3B-0x44, F11 0x57, F12 0x58, F13-F18 0x64-0x69, F19 0x71, none above.
    lwjgl = [*range(0x3B, 0x45), 0x57, 0x58, *range(0x64, 0x6A), 0x71]
    return [
        Key(f"f{n}", f"F{n}", 0x6F + n, lwjgl[n - 1] if n <= len(lwjgl) else None)
        for n in range(1, 25)
    ]


_NUMPAD_LWJGL = (0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49)

KEYS: dict[str, Key] = {
    k.name: k
    for k in [
        *_f_keys(),
        Key("pause", "Pause", 0x13, 0xC5),
        Key("scroll_lock", "Scroll Lock", 0x91, 0x46),
        Key("insert", "Insert", 0x2D, 0xD2),
        Key("delete", "Delete", 0x2E, 0xD3),
        Key("home", "Home", 0x24, 0xC7),
        Key("end", "End", 0x23, 0xCF),
        Key("page_up", "Page Up", 0x21, 0xC9),
        Key("page_down", "Page Down", 0x22, 0xD1),
        Key("left", "Left", 0x25, 0xCB),
        Key("up", "Up", 0x26, 0xC8),
        Key("right", "Right", 0x27, 0xCD),
        Key("down", "Down", 0x28, 0xD0),
        *(Key(f"num_{n}", f"Num {n}", 0x60 + n, _NUMPAD_LWJGL[n]) for n in range(10)),
        Key("num_multiply", "Num *", 0x6A, 0x37),
        Key("num_add", "Num +", 0x6B, 0x4E),
        Key("num_subtract", "Num -", 0x6D, 0x4A),
        Key("num_decimal", "Num .", 0x6E, 0x53),
        Key("num_divide", "Num /", 0x6F, 0xB5),
        Key("apps", "Menu", 0x5D, 0xDD),
    ]
}
BY_VK = {k.vk: k for k in KEYS.values()}
_LABELS = {k.label.casefold(): k for k in KEYS.values()}

ALIASES = {
    "scrolllock": "scroll_lock",
    "scroll": "scroll_lock",
    "break": "pause",
    "pause_break": "pause",
    "ins": "insert",
    "del": "delete",
    "pageup": "page_up",
    "pgup": "page_up",
    "prior": "page_up",
    "pagedown": "page_down",
    "pgdn": "page_down",
    "next": "page_down",
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
REFUSED = {
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


def normalise(text: str) -> str:
    return re.sub(r"[\s\-]+", "_", text.strip().casefold())


def parse(text: str) -> Key:
    """The key ``text`` names. Raises :class:`KeyNameError` saying why a name isn't allowed."""
    if not isinstance(text, str) or not text.strip():
        raise KeyNameError("a key name can't be empty")
    if found := _LABELS.get(" ".join(text.split()).casefold()):
        return found  # as gos voice prints it: "Num -", "Page Up"
    name = normalise(text)
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
        f"{text!r} isn't a key name GOS knows; use one of F1-F24 (F13-F24 are free in "
        "GTNH and Discord by default), Pause, Scroll Lock, Insert, Delete, Home, End, "
        "Page Up, Page Down, the arrows, Num 0-9 or Menu"
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
