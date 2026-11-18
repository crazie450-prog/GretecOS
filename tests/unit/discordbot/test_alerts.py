"""WO-9.2 in the bot (plan 7.12 "Alerts", 4.2; D48, D76, D300): the alert outbox posted as DMs,
the credit DM's buttons through the core's own budget control, and "GOS offline", which the bot
sends because it outlives the core. Fake core, fake clocks, fake input idle; one test against a
real HTTP core that goes away."""

from __future__ import annotations

import asyncio
import http.server
import json
import threading
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.alerts.policy import NotifyPolicy
from gregtec.discordbot.alerts import BACK_DM, AlertDesk
from gregtec.discordbot.core_api import CoreError, HttpCore
from gregtec.discordbot.handlers import Bot, Message
from gregtec.presence import FakeIdle
from gregtec.remote.jobs import JobQueue
from gregtec.store import Store

from .fakes import (
    CLAYTON,
    SETTINGS,
    STRANGER,
    FakeClock,
    FakeCore,
    FakeInteraction,
    designed,
    make_bot,
)

T0 = 2_000_000_000.0
CYCLE = "2033-05-01"
CREDITS = {
    "threshold": 80,
    "pending": True,
    "cycle_start": f"{CYCLE}T00:00:00",
    "actions": ["billing", "no_ai", "stop"],
}


def run(test: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(test())


class Wall:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "data" / "gos.sqlite")
    yield s
    s.close()


@pytest.fixture
def core(store: Store) -> FakeCore:
    c = FakeCore(queue=JobQueue(store, designed), clock=FakeClock())
    c.credits_state = dict(CREDITS)
    return c


class Rig:
    def __init__(self, core: FakeCore, tmp: Path, **kw: Any) -> None:  # noqa: ANN401
        self.core = core
        self.dms: list[Message] = []
        self.wall = Wall()
        self.idle = FakeIdle(self.wall)
        self.stopped = False
        self.tmp = tmp
        self.bot = make_bot(core, self.dms)
        self.desk = self.new_desk(**kw)

    def new_desk(self, **kw: Any) -> AlertDesk:  # noqa: ANN401
        desk = AlertDesk(
            self.bot,
            kw.get("policy", NotifyPolicy()),
            idle=self.idle,
            state_file=self.tmp / "run" / "discord-alerts.json",
            core_stopped=lambda: self.stopped,
            wall=self.wall,
        )
        self.bot.credit = desk.credit_button
        return desk

    async def passes(self, seconds: float, step: float = 5.0) -> None:
        end = self.wall.t + seconds
        while self.wall.t < end:
            self.wall.t = min(end, self.wall.t + step)
            await self.desk.poll()

    def away(self) -> None:
        self.idle.touch(self.wall.t - 11 * 60)

    @property
    def texts(self) -> list[str]:
        return [m.text for m in self.dms]


@pytest.fixture
def rig(core: FakeCore, tmp_path: Path) -> Rig:
    return Rig(core, tmp_path)


def dm(
    dm_id: int, text: str = "[warn] Machine stalled: EBF", **more: object
) -> dict[str, Any]:
    return {"id": dm_id, "created": int(T0), "level": "warn", "text": text, **more}


# --- the outbox


def test_outbox_dms_are_posted_once_and_marked(rig: Rig, core: FakeCore) -> None:
    core.outbox = [
        dm(1),
        dm(
            2,
            "[warn] API credits at 80%",
            buttons=[
                {"custom_id": f"gos:credit:no_ai:{CYCLE}", "label": "No-AI mode"},
                {
                    "custom_id": "gos:confirm:9",
                    "label": "sneaky",
                },  # not a credit button
            ],
        ),
    ]

    async def go() -> None:
        assert await rig.desk.poll() == 2
        assert await rig.desk.poll() == 0

    run(go)
    assert rig.texts == ["[warn] Machine stalled: EBF", "[warn] API credits at 80%"]
    assert [b.custom_id for b in rig.dms[1].buttons] == [f"gos:credit:no_ai:{CYCLE}"]
    assert core.marked == [1, 2]


def test_a_dm_discord_refuses_is_tried_again(rig: Rig, core: FakeCore) -> None:
    core.outbox = [dm(1)]
    fails = [True]

    async def flaky(msg: Message) -> None:
        if fails.pop(0) if fails else False:
            raise RuntimeError("50007 Cannot send messages to this user")
        rig.dms.append(msg)

    rig.bot.dm = flaky

    async def go() -> None:
        assert await rig.desk.poll() == 0
        assert core.marked == []
        assert await rig.desk.poll() == 1

    run(go)
    assert core.marked == [1]


# --- the credit buttons (D48): Clayton only, through the core's budget control


def press(rig: Rig, custom_id: str, user: int = CLAYTON) -> FakeInteraction:
    it = FakeInteraction(user, "component", rig.core.clock)
    run(lambda: rig.bot.interaction(it, custom_id))
    return it


def test_no_ai_button_goes_through_the_decision_api(rig: Rig, core: FakeCore) -> None:
    it = press(rig, f"gos:credit:no_ai:{CYCLE}")
    assert core.decisions == ["no_ai"]
    assert it.acks and "No-AI mode is on" in it.texts[0]
    assert ("action", "credits no_ai") in core.journal_lines


def test_open_billing_answers_with_the_link_and_keeps_the_rest(
    rig: Rig, core: FakeCore
) -> None:
    it = press(rig, f"gos:credit:billing:{CYCLE}")
    assert core.decisions == ["billing"]
    assert "https://console.anthropic.com/settings/billing" in it.texts[0]
    left = [b.custom_id for _, m in it.sent for b in m.buttons]
    assert left == [f"gos:credit:no_ai:{CYCLE}", f"gos:credit:stop:{CYCLE}"]


def test_stop_asks_once_more(rig: Rig, core: FakeCore) -> None:
    it = press(rig, f"gos:credit:stop:{CYCLE}")
    assert core.decisions == []
    (sure,) = [b.custom_id for _, m in it.sent for b in m.buttons]
    assert sure == f"gos:credit:stop!:{CYCLE}"
    it2 = press(rig, sure)
    assert core.decisions == ["stop"]
    assert "Stopping GOS" in it2.texts[0]


def test_an_old_cycles_button_changes_nothing(rig: Rig, core: FakeCore) -> None:
    it = press(rig, "gos:credit:no_ai:2033-04-01")
    assert core.decisions == []
    assert "earlier cycle" in it.texts[0]


def test_the_desks_refusal_is_shown_as_it_is(rig: Rig, core: FakeCore) -> None:
    core.decision_error = CoreError(
        "POST /api/credits/decision: 409 no credit alert is open to answer with 'billing'",
        409,
    )
    it = press(rig, f"gos:credit:billing:{CYCLE}")
    assert it.texts == ["no credit alert is open to answer with 'billing'"]


def test_a_foreign_users_credit_click_is_ignored(rig: Rig, core: FakeCore) -> None:
    for custom in (
        f"gos:credit:no_ai:{CYCLE}",
        f"gos:credit:stop!:{CYCLE}",
        f"gos:credit:billing:{CYCLE}",
        f"gos:credit:resume:{CYCLE}",
    ):
        it = press(rig, custom, user=STRANGER)
        assert not it.answered
    assert core.calls == [] and core.decisions == [] and rig.dms == []
    assert rig.bot.ignored == 4


# --- "GOS offline" (D76): sent by the bot, which outlives the core


def test_offline_while_away_is_dmed_once_after_a_minute(
    rig: Rig, core: FakeCore
) -> None:
    core.offline = True

    async def go() -> None:
        rig.away()
        await rig.passes(55)
        assert rig.dms == []
        await rig.passes(10)
        assert len(rig.dms) == 1 and "GOS offline" in rig.texts[0]
        await rig.passes(3600, step=30)
        assert len(rig.dms) == 1

    run(go)


def test_offline_then_away_dms_when_he_has_gone(rig: Rig, core: FakeCore) -> None:
    core.offline = True

    async def go() -> None:
        rig.idle.touch()  # at the PC when the core went down
        await rig.passes(595)
        assert rig.dms == []
        await rig.passes(10)  # 10 minutes since his last input
        assert len(rig.dms) == 1

    run(go)


def test_back_online_follows_and_a_new_outage_is_a_new_dm(
    rig: Rig, core: FakeCore
) -> None:
    async def go() -> None:
        rig.away()
        core.offline = True
        await rig.passes(70)
        core.offline = False
        await rig.passes(5)
        assert rig.texts[-1] == BACK_DM
        core.offline = True
        rig.away()
        await rig.passes(70)
        assert sum("GOS offline" in t for t in rig.texts) == 2

    run(go)


def test_a_short_outage_says_nothing(rig: Rig, core: FakeCore) -> None:
    async def go() -> None:
        rig.away()
        core.offline = True
        await rig.passes(30)  # a supervisor restart takes ~2 s (D115)
        core.offline = False
        await rig.passes(10)

    run(go)
    assert rig.dms == []


def test_stopped_on_purpose_is_not_offline(rig: Rig, core: FakeCore) -> None:
    rig.stopped = True  # gos stop, or D48's Stop: the supervisor's state "stopped"
    core.offline = True

    async def go() -> None:
        rig.away()
        await rig.passes(600)

    run(go)
    assert rig.dms == []


def test_present_never_dms_offline(rig: Rig, core: FakeCore) -> None:
    core.offline = True

    async def go() -> None:
        for _ in range(10):
            rig.idle.touch()
            await rig.passes(300)

    run(go)
    assert rig.dms == []


def test_alerts_off_and_quiet_hold_the_offline_dm(rig: Rig, core: FakeCore) -> None:
    async def go() -> None:
        core.alerts_state = {"mode": "off", "until": None}
        await rig.desk.poll()  # the bot keeps the last /alerts state the core gave
        core.offline = True
        rig.away()
        await rig.passes(300)
        assert rig.dms == []
        core.offline = False
        core.alerts_state = {"mode": "quiet", "until": int(rig.wall.t) + 600}
        await rig.desk.poll()
        core.offline = True
        rig.away()
        await rig.passes(300)
        assert rig.dms == []  # warn waits out /alerts quiet
        await rig.passes(400)
        assert len(rig.dms) == 1  # quiet over, still down, still away

    run(go)


def test_no_second_offline_dm_after_a_bot_restart(rig: Rig, core: FakeCore) -> None:
    core.offline = True

    async def go() -> None:
        rig.away()
        await rig.passes(70)
        assert len(rig.dms) == 1
        rig.desk = rig.new_desk()  # the supervisor restarted the bot mid-outage
        rig.away()
        await rig.passes(600)

    run(go)
    assert len(rig.dms) == 1
    saved = json.loads((rig.tmp / "run" / "discord-alerts.json").read_text("utf-8"))
    assert saved["sent"] is True and saved["down_since"] is not None


# --- against a real core that goes away (real HTTP, real socket)


class _Core(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        raw = json.dumps({"dms": [], "alerts": {"mode": "on", "until": None}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: Any) -> None:  # noqa: ANN401
        pass


def test_the_bot_reports_a_real_core_that_died(tmp_path: Path) -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Core)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    dms: list[Message] = []

    async def send(msg: Message) -> None:
        dms.append(msg)

    bot = Bot(SETTINGS, HttpCore(port, "t" * 40), dm=send)
    wall = Wall()
    idle = FakeIdle(wall)
    desk = AlertDesk(
        bot,
        NotifyPolicy(),
        idle=idle,
        state_file=tmp_path / "run" / "discord-alerts.json",
        wall=wall,
    )

    async def go() -> None:
        idle.touch(wall.t - 3600)
        await desk.poll()
        assert desk.state["down_since"] is None
        server.shutdown()  # the core process dies: its port answers no more
        server.server_close()
        thread.join(5)
        for _ in range(14):
            wall.t += 5
            await desk.poll()

    run(go)
    assert len(dms) == 1 and "GOS offline" in dms[0].text


# --- review L3: "GOS is back" by the policy, and an unsent outage restarts with the bot


def test_back_online_follows_the_policy(rig: Rig, core: FakeCore) -> None:
    async def go() -> None:
        rig.away()
        core.offline = True
        await rig.passes(70)
        assert len(rig.dms) == 1
        rig.idle.touch()  # he is back at the PC: he sees it is back
        core.offline = False
        await rig.passes(5)
        assert len(rig.dms) == 1
        rig.away()
        core.offline = True
        await rig.passes(70)
        assert len(rig.dms) == 2
        core.alerts_state = {"mode": "off", "until": None}  # /alerts off meanwhile
        core.offline = False
        await rig.passes(5)
        assert BACK_DM not in rig.texts

    run(go)


def test_an_outage_not_dmed_restarts_with_the_bot(rig: Rig, core: FakeCore) -> None:
    """A down_since left from before a deliberate stop days ago is not counted from then."""
    state = rig.tmp / "run" / "discord-alerts.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    old = {"down_since": T0 - 3 * 86400, "sent": False, "alerts": None}
    state.write_text(json.dumps(old), encoding="utf-8")
    rig.desk = rig.new_desk()
    core.offline = True

    async def go() -> None:
        rig.away()
        await rig.passes(30)
        assert rig.dms == []  # counted from this start
        await rig.passes(40)
        assert len(rig.dms) == 1 and "hasn't answered for 6" in rig.texts[0]

    run(go)
