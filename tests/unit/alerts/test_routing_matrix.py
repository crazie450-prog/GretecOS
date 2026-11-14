"""WO-9.2's acceptance matrix (plan 8 row 9.2, 7.12 "Alerts"; D46, D48, D76, D298, D299): every
alert kind x away/present x quiet hours x repeat, on a fake clock with fake channels and a real
``gos.sqlite``, through the real :class:`gregtec.alerts.router.AlertRouter`.

The expected channels are written out by hand below (the spec), not derived from the policy.
Quiet hours are ``23:00-07:00`` (``quiet_min_level`` err: only err gets through); "away" is no
input for 11 minutes (D76's 10). Voice and in-game counts are lines; Discord counts are DMs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from .world import NIGHT, NOON, QUIET, RAISE, Clock, World

# kind -> (present, quiet off), (present, quiet on), (away, quiet off), (away, quiet on)
# V voice, G in game (GregtecLink chat), D Discord DM; the dashboard always shows them.
EXPECT = {
    "power_low": ("VG", "VG", "GD", "GD"),  # err
    "machine_stalled": ("VG", "G", "GD", "G"),  # warn
    "crafting_done": ("VG", "G", "G", "G"),  # info: never DMed
    "power_deficit": ("VG", "G", "GD", "G"),  # a notice, warn
    "plan_buildable": ("VG", "G", "G", "G"),  # a notice, info
    "credits80": (
        "D",
        "",
        "D",
        "",
    ),  # D48: whatever his presence; warn waits out the quiet
    "credits100": ("D", "D", "D", "D"),  # err
    "game_paused": ("", "", "D", ""),  # D76: only while away
}
BASE = (
    "power_low",
    "machine_stalled",
    "crafting_done",
    "power_deficit",
    "plan_buildable",
)
REPEATS = ("first", "steady", "restart", "refire")


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    w = World(tmp_path / "gos.sqlite", Clock(NOON), policy=QUIET)
    yield w
    w.close()


def counts(letters: str) -> dict[str, int]:
    return {
        "voice": int("V" in letters),
        "in_game": int("G" in letters),
        "discord": int("D" in letters),
    }


def end_and_raise_again(w: World, kind: str) -> None:
    """The alert ends, then a new occurrence of it, a minute later."""
    if kind in ("power_low", "machine_stalled", "crafting_done"):
        w.clear(kind)
        w.run(60)
        RAISE[kind](w)
    elif kind in ("power_deficit", "plan_buildable"):
        w.run(60)
        RAISE[kind](w)  # a new notice (its own ts)
    elif kind.startswith("credits"):
        w.credit = None  # answered
        w.run(60)
        RAISE[kind](w)
        assert w.credit is not None
        w.credit["cycle_start"] = "2033-06-01T00:00:00"  # the next cycle's crossing
    else:
        w.unpause()
        w.run(60)
        w.pause()


@pytest.mark.parametrize("repeat", REPEATS)
@pytest.mark.parametrize("quiet", [False, True], ids=["day", "quiet"])
@pytest.mark.parametrize("away", [False, True], ids=["present", "away"])
@pytest.mark.parametrize("kind", list(EXPECT))
def test_matrix(world: World, kind: str, away: bool, quiet: bool, repeat: str) -> None:
    w = world
    w.at(NIGHT if quiet else NOON)
    if away:
        w.away()
    else:
        w.present()
    RAISE[kind](w)
    w.router.tick()
    want = counts(EXPECT[kind][2 * away + quiet])
    assert w.got() == want, (kind, away, quiet)
    if repeat == "steady":  # still active a few minutes on: nothing more
        w.run(300)
        if not away:
            w.present()
        assert w.got() == want
    elif repeat == "restart":  # the core restarts; the source raises it again
        w.restart()
        if kind in ("power_low", "machine_stalled", "crafting_done"):
            w.run(30)
            RAISE[kind](w)  # the engine fires afresh after a restart (D281)
        elif kind in ("power_deficit", "plan_buildable"):
            RAISE[kind](w)  # the same notice again (same ts)
        w.run(120)
        assert w.got() == want, "a duplicate after a restart"
    elif repeat == "refire":
        if away:
            w.away()
        end_and_raise_again(w, kind)
        if not away:
            w.present()
        w.run(5)
        if kind in BASE:  # [notify] cooldown_s: the key waits 30 minutes per channel
            assert w.got() == want
        else:  # once per threshold per cycle, once per pause: a new one goes again
            again = {c: n * 2 for c, n in want.items()}
            assert w.got() == again


@pytest.mark.parametrize("kind", ["power_low", "machine_stalled", "power_deficit"])
def test_a_base_alert_goes_again_after_its_cooldown(world: World, kind: str) -> None:
    w = world
    w.present()
    RAISE[kind](w)
    w.router.tick()
    assert w.got() == counts("VG")
    w.run(1801)
    w.present()
    end_and_raise_again(w, kind)
    w.present()
    w.run(2)
    assert w.got() == {"voice": 2, "in_game": 2, "discord": 0}


def test_every_alert_has_a_level_and_a_key(world: World) -> None:
    w = world
    w.away()
    for kind in EXPECT:
        if kind == "credits100":
            continue  # one credit threshold at a time
        RAISE[kind](w)
    w.router.tick()
    active = w.router.view()["active"]
    assert {a["level"] for a in active} <= {"info", "warn", "err"}
    keys = [a["key"] for a in active]
    assert len(keys) == len(set(keys)) == 7
    assert all(a["key"] and a["occ"] for a in active)
