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
        ("F19", "f19", 0x82, 0x71),
        ("F24", "f24", 0x87, None),  # LWJGL 2 has no code past F19
        ("F1", "f1", 0x70, 0x3B),
        ("F11", "f11", 0x7A, 0x57),
        ("Scroll Lock", "scroll_lock", 0x91, 0x46),
        ("scrolllock", "scroll_lock", 0x91, 0x46),
        ("Pause", "pause", 0x13, 0xC5),
        ("page-up", "page_up", 0x21, 0xC9),
        ("PgDn", "page_down", 0x22, 0xD1),
        ("Num 5", "num_5", 0x65, 0x4C),
        ("numpad0", "num_0", 0x60, 0x52),
        ("num +", "num_add", 0x6B, 0x4E),
        ("Menu", "apps", 0x5D, 0xDD),
        ("  insert ", "insert", 0x2D, 0xD2),
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
key_key.nei.recipe:19
key_gui.chatcolor:100:SHIFT
key_key.unbound:0
key_broken:notanumber
lastServer:
"""


def test_bindings_read_options_txt() -> None:
    bound = keys.bindings(OPTIONS)
    assert bound[57] == ["key.jump"]
    assert bound[60] == ["key.screenshot"]  # F2
    # two bindings on F13 (0x64), one with a modifier suffix; mouse buttons and 0 are left out
    assert bound[100] == ["key.xaero_waypoint", "gui.chatcolor"]
    assert -100 not in bound and 0 not in bound


def test_conflicts_name_the_bindings_on_each_configured_key() -> None:
    configured = {
        "talk": keys.parse("F13"),
        "confirm": keys.parse("F14"),
        "cancel": keys.parse("F2"),
        "next_card": keys.parse("F24"),
    }
    found = keys.conflicts(configured, keys.bindings(OPTIONS))
    assert found == {
        "talk": ["gui.chatcolor", "key.xaero_waypoint"],
        "confirm": [],
        "cancel": ["key.screenshot"],
        "next_card": None,  # GTNH's LWJGL 2 table has no F24: options.txt can't tell
    }
