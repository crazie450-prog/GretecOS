"""WO-9.2 scenarios on the fake clock (plan 7.12, D46, D48, D76, D298, D299): Esc then walk away
(and the other order), quiet hours that hold an alert until they end, Discord's /alerts, voice and
DM never both, grouping, and the outbox the bot posts from."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.alerts.router import OUTBOX_TTL_S

from .world import DAY, NIGHT, NOON, QUIET, Clock, World


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    w = World(tmp_path / "gos.sqlite", Clock(NOON), policy=QUIET)
    yield w
    w.close()


# --- D76: "game paused" only while he's away, sent once, whichever comes second


def test_esc_then_walk_away_dms_once_at_ten_minutes(world: World) -> None:
    w = world
    w.present()  # his last input: the Esc key
    w.pause()  # the watchers see "Saving and pausing game..." (D258)
    w.run(599)
    assert w.dms() == []
    w.run(2)  # 10 minutes without input: away
    dms = w.dms()
    assert len(dms) == 1 and "Lab is paused" in dms[0]["text"]
    w.run(1200)
    assert len(w.dms()) == 1


def test_away_then_paused_dms_at_the_pause(world: World) -> None:
    w = world
    w.away(minutes=30)
    w.run(5)
    assert w.dms() == []
    w.pause()  # the game lost focus while he was away
    w.router.tick()
    assert len(w.dms()) == 1


def test_back_and_away_again_in_one_pause_is_still_one_dm(world: World) -> None:
    w = world
    w.present()
    w.pause()
    w.run(601)
    assert len(w.dms()) == 1
    w.present()  # back at the PC, game still paused
    w.run(700)  # and away again
    assert len(w.dms()) == 1


def test_unpaused_before_he_is_away_sends_nothing(world: World) -> None:
    w = world
    w.present()
    w.pause()
    w.run(300)
    w.unpause()
    w.run(600)
    assert w.dms() == []


def test_a_new_pause_is_a_new_dm(world: World) -> None:
    w = world
    w.away()
    w.pause()
    w.router.tick()
    w.unpause()
    w.run(60)
    w.pause()
    w.router.tick()
    assert len(w.dms()) == 2


def test_no_paused_dm_again_after_a_core_restart(world: World) -> None:
    w = world
    w.away()
    w.pause()
    w.router.tick()
    w.restart()
    w.run(120)
    w.restart()
    w.run(120)
    assert len(w.dms()) == 1


def test_present_the_paused_game_is_never_dmed(world: World) -> None:
    w = world
    w.pause()
    for _ in range(12):
        w.present()
        w.run(300)
    assert w.dms() == []


# --- quiet hours: held, not dropped


def test_quiet_hours_hold_a_warn_until_they_end(world: World) -> None:
    w = world
    w.at(NIGHT)  # 02:00
    w.away(minutes=60)
    w.fire("machine_stalled")
    w.run(5 * 3600 - 1, step=60)  # to 06:59:59
    assert w.dms() == []
    w.run(61, step=1)
    assert len(w.dms()) == 1  # 07:00, still stalled, still away


def test_an_alert_that_cleared_in_quiet_hours_is_never_sent(world: World) -> None:
    w = world
    w.at(NIGHT)
    w.away(minutes=60)
    w.fire("machine_stalled")
    w.run(600)
    w.clear("machine_stalled")
    w.at(DAY + 8 * 3600)
    w.away(minutes=60)
    w.run(5)
    assert w.dms() == []


def test_quiet_hours_hold_the_voice_line_too(world: World) -> None:
    w = world
    w.at(DAY + 6 * 3600 + 59 * 60)  # 06:59
    w.present()
    w.fire("machine_stalled")
    w.run(30)
    assert w.said == []
    w.present()
    w.run(60)  # 07:00
    assert len(w.said) == 1


# --- Discord's /alerts (D297)


def test_alerts_off_stops_every_dm(world: World) -> None:
    w = world
    w.mode = "off"
    w.away()
    w.fire("power_low")
    w.credits(100)
    w.pause()
    w.run(10)
    assert w.dms() == []
    assert len(w.chat) == 1  # the passive channels still carry it


def test_alerts_quiet_holds_all_but_err(world: World) -> None:
    w = world
    w.mode = "quiet"
    w.away()
    w.fire("power_low")  # err
    w.fire("machine_stalled", subject="lab/7")  # warn
    w.run(5)
    assert len(w.dms()) == 1 and "Power low" in w.dms()[0]["text"]
    w.mode = "on"  # its hours are over
    w.away()
    w.run(2)
    assert len(w.dms()) == 2


# --- voice and a DM never both (dedupe across channels)


def test_said_while_present_is_not_dmed_when_he_walks_away(world: World) -> None:
    w = world
    w.present()
    w.fire("machine_stalled")
    w.router.tick()
    assert len(w.said) == 1
    w.run(1200)  # he walked away; still stalled
    assert w.dms() == []


def test_dmed_while_away_is_not_said_when_he_comes_back(world: World) -> None:
    w = world
    w.away()
    w.fire("machine_stalled")
    w.router.tick()
    assert len(w.dms()) == 1
    w.present()
    w.run(60)
    assert w.said == []


def test_no_voice_client_present_then_away_is_dmed(world: World) -> None:
    w = world
    w.voice_up = False  # the game (and so voice) isn't running
    w.present()
    w.fire("machine_stalled")
    w.run(30)
    assert w.said == [] and w.dms() == []
    w.run(600)  # away now
    assert len(w.dms()) == 1


# --- grouping: one power loss is one message


def test_alerts_of_one_tick_are_one_line_and_one_dm(world: World) -> None:
    w = world
    w.present()
    w.fire("power_low", subject="lab/1")
    w.fire("machine_stalled", subject="lab/2")
    w.fire("machine_stalled", subject="lab/3")
    w.router.tick()
    assert w.said == ["Lab: power low and 2 machines stalled."]
    assert w.chat == w.said
    w.away()
    w.fire("maintenance", subject="lab/4")
    w.fire("maintenance", subject="lab/5")
    w.router.tick()
    dms = w.dms()
    assert len(dms) == 1 and dms[0]["text"].startswith("Lab: 2 alerts")
    assert dms[0]["level"] == "err"


# --- the outbox the bot posts from


def test_the_credit_dm_carries_the_banners_buttons(world: World) -> None:
    w = world
    w.credits(80)
    w.router.tick()
    (dm,) = w.router.outbox()
    ids = [b["custom_id"] for b in dm["buttons"]]
    assert ids == [
        "gos:credit:billing:2033-05-01",
        "gos:credit:no_ai:2033-05-01",
        "gos:credit:stop:2033-05-01",
    ]
    assert "80%" in dm["text"] and "never buys credits" in dm["text"]


def test_a_dm_whose_alert_ended_before_the_bot_posted_it_is_dropped(
    world: World,
) -> None:
    w = world
    w.away()
    w.fire("machine_stalled")
    w.credits(80)
    w.router.tick()
    assert len(w.router.outbox()) == 2
    w.clear("machine_stalled")
    w.credit = None  # answered on the dashboard
    w.router.tick()
    assert w.router.outbox() == []


def test_a_stale_undelivered_dm_is_dropped(world: World) -> None:
    w = world
    w.credits(80)
    w.router.tick()
    w.at(w.clock.t + OUTBOX_TTL_S + 1)
    assert w.router.outbox() == []


def test_delivered_once(world: World) -> None:
    w = world
    w.credits(80)
    w.router.tick()
    (dm,) = w.router.outbox()
    assert w.router.delivered(dm["id"]) is True
    assert w.router.delivered(dm["id"]) is False
    assert w.router.outbox() == []
    w.restart()
    w.run(60)
    assert w.router.outbox() == []


def test_the_test_dm_goes_whatever_his_presence(world: World) -> None:
    w = world
    w.present()
    w.router.test()
    w.router.tick()
    (dm,) = w.router.outbox()
    assert "Test alert" in dm["text"] and dm["buttons"] == []


def test_a_failing_channel_never_stops_the_others(world: World) -> None:
    w = world

    def broken(text: str) -> bool:
        raise RuntimeError("socket gone")

    w.router.say = broken
    w.present()
    w.fire("machine_stalled")
    w.router.tick()
    assert len(w.chat) == 1
