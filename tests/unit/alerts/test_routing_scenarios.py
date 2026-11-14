"""WO-9.2 scenarios on the fake clock (plan 7.12, D46, D48, D76, D298, D299): Esc then walk away
(and the other order), quiet hours that hold an alert until they end, Discord's /alerts, voice and
DM never both, grouping, and the outbox the bot posts from."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.alerts.engine import AlertEvent
from gregtec.alerts.router import KEEP_S, OUTBOX_TTL_S
from gregtec.alerts.routes import paused_of
from gregtec.watchers.feed import HEARD_S, GameFeed
from gregtec.watchers.runner import HEARTBEAT_S

from .world import DAY, NIGHT, NOON, QUIET, RAISE, Clock, World


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


# --- review fixes (M1, M2, L1, L2, L5, L6)


def _feed_post(feed: GameFeed, paused: bool, since: float | None) -> None:
    """One watchers' post (WO-6.1's shape): its paused state rides on every post."""
    state = {"paused": paused, "since": since, "why": "esc" if paused else None}
    feed.receive({"instance": "lab", "paused": state, "events": []})


def test_esc_then_walk_away_through_the_watchers_real_feed(world: World) -> None:
    """M2: the real path. The watchers post on the change and a heartbeat every HEARTBEAT_S;
    the core's GameFeed takes them; paused_of reads it as the router's source; the bot polls the
    outbox every 5 s but hasn't posted yet. 11 minutes: exactly one DM, still in the outbox."""
    w = world
    feed = GameFeed(clock=w.clock)
    w.router.paused = lambda: paused_of(feed, None)
    w.present()  # Esc
    esc = w.clock.t
    _feed_post(feed, True, esc)
    last = w.clock.t
    while w.clock.t < esc + 11 * 60:
        w.clock.t += 1
        if w.clock.t - last >= HEARTBEAT_S:
            _feed_post(feed, True, esc)
            last = w.clock.t
        w.router.tick()
        if int(w.clock.t) % 5 == 0:
            w.router.outbox()  # the bot's poll (Discord is slow today: not posted yet)
    (dm,) = w.router.outbox()
    assert "Lab is paused since 12:00" in dm["text"]
    assert dm["created"] == int(esc + 600)  # the first tick he counted as away
    assert len(w.dms()) == 1


def test_watchers_unheard_for_a_while_dont_lose_the_paused_dm(world: World) -> None:
    """M2's second half: the watchers miss heartbeats past HEARD_S after the DM was queued and
    the bot polls in the gap. The DM is dropped as stale, but it was never posted, so when the
    same pause is heard again it is queued again: one DM in the outbox at the end."""
    w = world
    feed = GameFeed(clock=w.clock)
    w.router.paused = lambda: paused_of(feed, None)
    w.away()
    esc = w.clock.t
    _feed_post(feed, True, esc)
    w.router.tick()
    assert len(w.router.outbox()) == 1
    w.run(HEARD_S + 10)  # no heartbeat: paused is unknown, the occurrence closes
    assert w.router.outbox() == []  # the bot polled in the gap
    _feed_post(feed, True, esc)  # heard again: the same pause
    w.router.tick()
    (dm,) = w.router.outbox()
    assert "Lab is paused" in dm["text"]
    assert len(w.dms()) == 1


def test_a_pause_that_flaps_without_a_poll_keeps_its_dm(world: World) -> None:
    w = world
    w.away()
    w.pause()
    w.router.tick()
    since = w.paused
    w.unpause()
    w.router.tick()  # closed for a tick
    w.paused = since  # the same pause again
    w.router.tick()
    assert len(w.router.outbox()) == 1  # reopened: not stale news, not queued twice
    assert len(w.dms()) == 1


@pytest.mark.parametrize("kind", ["machine_stalled", "credits80", "game_paused"])
def test_no_bot_nothing_is_counted_as_dmed(world: World, kind: str) -> None:
    """M1: Discord isn't set up (the bot stays stopped, D296): nothing is queued and a base
    alert is said when he is back; once a bot reads the outbox, what is still due goes."""
    w = world
    w.bot_up = False
    w.away()
    RAISE[kind](w)
    w.run(60)
    assert w.dms() == []
    assert w.router.view()["active"][0]["held"]["discord"] == "no Discord bot"
    w.present()
    w.router.tick()
    assert len(w.said) == (1 if kind == "machine_stalled" else 0)
    w.bot_up = True
    w.router.tick()
    assert len(w.dms()) == (1 if kind == "credits80" else 0)


def test_after_a_restart_a_key_is_adopted_until_its_binding_settles(
    world: World,
) -> None:
    """L1: adoption ends on the engine's word (AlertEngine.settled), not on wall time."""
    w = world
    w.away()
    w.fire("machine_stalled")
    w.router.tick()
    assert len(w.dms()) == 1
    settled = {"lab": False}
    w.restart()
    w.router.settled = lambda binding, rule: settled[binding]
    w.run(3600, step=10)  # paused for an hour across the restart: never settled
    w.away()
    w.fire("machine_stalled")  # the new engine fires it afresh
    w.run(5)
    assert len(w.dms()) == 1  # the same occurrence
    w.restart()  # and once more, now it settles without firing: it ended while down
    w.router.settled = lambda binding, rule: True
    w.router.tick()
    saved = w.store.rows("SELECT value FROM kv WHERE key = 'alerts.routing'")
    assert json.loads(saved[0]["value"]) == {}


def test_after_a_restart_a_dm_whose_alert_ended_while_down_is_dropped(
    world: World,
) -> None:
    """L2: after the first tick read the sources, a DM is stale news if none of its alerts is
    active or may still fire again; a key the engine may still fire keeps its DM."""
    w = world
    w.away()
    w.fire("machine_stalled")
    w.fire("power_low", binding="base", subject="base/7")
    w.credits(80)
    w.router.tick()
    assert len(w.router.outbox()) == 3
    w.credit = None  # answered on the dashboard while the core was down
    w.restart()
    settled = {"lab": True, "base": False}
    w.router.settled = lambda binding, rule: settled[binding]
    assert len(w.router.outbox()) == 3  # before the first tick: not known yet
    w.router.tick()
    (left,) = w.router.outbox()
    assert "Power low" in left["text"]  # base hasn't settled: it may still be firing


def test_two_pauses_with_no_start_are_two_dms(world: World) -> None:
    """L5: a pause whose start is unknown is new from the first poll that sees it."""
    w = world
    w.away()
    w.paused = ("lab", None)
    w.router.tick()
    w.run(30)
    assert len(w.dms()) == 1
    w.unpause()
    w.run(60)
    w.paused = ("lab", None)
    w.router.tick()
    assert len(w.dms()) == 2
    assert all("since" not in d["text"] for d in w.dms())


def test_a_held_telemetry_alert_says_so_in_its_dm(world: World) -> None:
    """L6: DMed while its binding's telemetry is held (paused, OC silent)."""
    w = world
    w.away()
    w.fire("machine_stalled")
    w.router.alert_event(AlertEvent("stale", "lab", w.clock.t, why="paused"))
    w.router.tick()
    (dm,) = w.dms()
    assert dm["text"].startswith(
        "[warn] Machine stalled: EBF (held: no fresh telemetry)"
    )
    w.router.alert_event(AlertEvent("fresh", "lab", w.clock.t))
    w.fire("power_low")
    w.router.tick()
    assert "held" not in w.dms()[-1]["text"]


def test_the_in_memory_history_is_pruned(world: World) -> None:
    """L2: ended occurrences, sends and cooldowns don't grow without limit."""
    w = world
    w.away()
    for n in range(20):
        w.fire("machine_stalled", subject=f"lab/{n}")
        w.router.tick()
        w.clear("machine_stalled", subject=f"lab/{n}")
    w.router.tick()
    assert len(w.router._closed) == 20
    w.at(w.clock.t + OUTBOX_TTL_S + 120)
    w.router.tick()
    assert w.router._closed == {} and w.router._last == {}
    w.at(w.clock.t + KEEP_S)
    w.router.tick()
    assert w.router._sent == {}


def test_the_routed_view_says_when_presence_is_unknown(world: World) -> None:
    """Advisor verdict 2: unknown counts as present, and the view says it is unknown."""
    w = world
    w.away()
    assert w.router.view()["context"]["presence"] == "away"
    w.present()
    assert w.router.view()["context"]["presence"] == "present"
    w.idle.known = False
    view = w.router.view()["context"]
    assert view["presence"] == "unknown" and view["away"] is False
