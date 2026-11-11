"""WO-5.1, D180: key names for the talk key and the hotkeys, and the check against GTNH's
bindings in options.txt (QA:V8)."""

from __future__ import annotations

import pytest

from gregtec_voice import keys


@pytest.mark.parametrize(
    ("text", "name", "vk", "lwjgl"),
    [
        ("F13", "f13", 0x7C, 0x64),
        ("f16", "f16", 0x7F, 0x67),
        ("F18", "f18", 0x81, 0x69),
        ("F19", "f19", 0x82, 0x71),
        ("F20", "f20", 0x83, None),  # LWJGL 2 has no code past F19
        ("F24", "f24", 0x87, None),
        ("Scroll Lock", "scroll_lock", 0x91, 0x46),
        ("scrolllock", "scroll_lock", 0x91, 0x46),
        ("  scroll-lock ", "scroll_lock", 0x91, 0x46),
        ("Pause", "pause", 0x13, 0xC5),
        ("break", "pause", 0x13, 0xC5),
    ],
)
def test_names_parse(text: str, name: str, vk: int, lwjgl: int | None) -> None:
    key = keys.parse(text)
    assert (key.name, key.vk, key.lwjgl) == (name, vk, lwjgl)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("a", "types or edits text"),
        ("7", "types or edits text"),
        ("space", "types or edits text"),
        ("Enter", "types or edits text"),
        ("esc", "types or edits text"),
        # F1-F12: hard-coded (F1 hides the HUD, F3 is the debug screen) or GTNH's
        ("F1", "hard-codes F1 and F3"),
        ("F3", "hard-codes F1 and F3"),
        ("F11", "use one of F13-F24"),
        # they edit or move in chat and Discord, and NEI binds some (QA:V8)
        ("Delete", "types or edits text"),
        ("  insert ", "types or edits text"),
        ("Home", "types or edits text"),
        ("Up", "NEI binds the arrows"),
        ("arrow-left", "NEI binds the arrows"),
        ("Page Up", "Page Up and Page Down"),
        ("PgDn", "Page Up and Page Down"),
        # the numpad types with Num Lock on; NEI binds Num 0
        ("Num 5", "with Num Lock on"),
        ("numpad0", "NEI binds Num 0"),
        ("Num -", "with Num Lock on"),
        ("num +", "with Num Lock on"),
        ("Menu", "context menus"),
        ("left shift", "modifier"),
        ("RCtrl", "modifier"),
        ("caps lock", "lock key"),
        ("print screen", "screenshots"),
        ("mouse5", "map it to one of F13-F24"),
        ("X2", "map it to one of F13-F24"),
        ("ctrl+f13", "no combinations"),
        ("F25", "isn't a key name GOS knows"),
        ("TBD (unbound in GTNH and Discord)", "isn't a key name GOS knows"),
        ("", "can't be empty"),
    ],
)
def test_refused_names_say_why(text: str, why: str) -> None:
    with pytest.raises(keys.KeyNameError, match=why):
        keys.parse(text)


def test_only_keys_that_type_nothing_and_are_unbound_by_default_are_allowed() -> None:
    """D180 after the review (M3): F13-F24, Pause and Scroll Lock, nothing else."""
    assert sorted(k.label for k in keys.KEYS.values()) == sorted(
        [f"F{n}" for n in range(13, 25)] + ["Pause", "Scroll Lock"]
    )


def test_every_key_has_a_distinct_virtual_key_and_lwjgl_code() -> None:
    vks = [k.vk for k in keys.KEYS.values()]
    codes = [k.lwjgl for k in keys.KEYS.values() if k.lwjgl is not None]
    assert len(set(vks)) == len(vks) and len(set(codes)) == len(codes)
    assert keys.BY_VK[0x7C].label == "F13"


OPTIONS = """\
version:0
pauseOnLostFocus:false
key_key.attack:-100
key_key.use:-99
key_key.forward:17
key_key.jump:57
key_key.screenshot:60
key_key.xaero_waypoint:100
key_nei.options.keys.gui.recipe:19
key_nei.options.keys.world.chunkoverlay:70
key_gui.chatcolor:100:SHIFT
key_key.unbound:0
key_broken:notanumber
lastServer:
"""


def test_bindings_read_options_txt() -> None:
    bound = keys.bindings(OPTIONS)
    assert bound[57] == ["key.jump"]
    assert bound[60] == ["key.screenshot"]  # F2
    # GTNH's NEI registers its keys as Minecraft key bindings, so they are here too
    assert bound[19] == ["nei.options.keys.gui.recipe"]
    # two bindings on F13 (0x64), one with a modifier suffix; mouse buttons and 0 are left out
    assert bound[100] == ["key.xaero_waypoint", "gui.chatcolor"]
    assert -100 not in bound and 0 not in bound


def test_conflicts_name_the_bindings_on_each_configured_key() -> None:
    configured = {
        "talk": keys.parse("F13"),
        "confirm": keys.parse("F14"),
        "cancel": keys.parse("Scroll Lock"),  # rebound in NEI here (0x46)
        "next_card": keys.parse("F24"),
    }
    found = keys.conflicts(configured, keys.bindings(OPTIONS))
    assert found == {
        "talk": ["gui.chatcolor", "key.xaero_waypoint"],
        "confirm": [],
        "cancel": ["nei.options.keys.world.chunkoverlay"],
        "next_card": None,  # GTNH's LWJGL 2 table has no F24: options.txt can't tell
    }
