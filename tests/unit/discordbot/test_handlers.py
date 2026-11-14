"""WO-9.1 (plan 7.12, §8 row 9.1, [QA:V4], D220, D295): the bot's commands, confirm buttons and
job delivery, driven through its one door with fake interactions on a fake clock.

The 10 s bound (§8): every command's first answer (Discord's deferred "thinking") is at once,
and a message follows within 10 s even when the core is slow ("still working", then the answer);
a long design job is acknowledged ("queued") within 10 s and its result follows later.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.discordbot import handlers, texts
from gregtec.discordbot.core_api import CoreError
from gregtec.discordbot.handlers import Message
from gregtec.remote.jobs import JobQueue
from gregtec.store import Store

from .fakes import (
    BOM,
    CLAYTON,
    PNG,
    FakeClock,
    FakeCore,
    FakeInteraction,
    designed,
    make_bot,
    settle,
)

BOUND_S = 10.0  # §8 row 9.1

COMMANDS: dict[str, dict[str, Any]] = {
    "ask": {"text": "how do I make steel?"},
    "plan": {"target": "steel ingot", "amount": "2/s"},
    "power": {"mode": "best"},
    "status": {},
    "alerts": {"mode": "quiet", "hours": 2},
    "persona": {"name": "plain"},
}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "data" / "gos.sqlite")
    yield s
    s.close()


@pytest.fixture
def core(store: Store) -> FakeCore:
    return FakeCore(queue=JobQueue(store, designed), clock=FakeClock())


def run(test: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(test())


def clayton(core: FakeCore, kind: str = "command") -> FakeInteraction:
    return FakeInteraction(CLAYTON, kind, core.clock)


# --- the 10 s bound -------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(COMMANDS))
def test_every_command_answers_within_10_s_even_when_the_core_is_slow(
    core: FakeCore, name: str
) -> None:
    core.delay = 30.0  # every core call takes 30 s

    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        task = asyncio.ensure_future(bot.interaction(it, name, COMMANDS[name]))
        await core.clock.advance(BOUND_S)
        assert it.acks == [0.0]  # Discord's "thinking", at once
        assert it.sent and it.sent[0][0] <= BOUND_S, it.sent
        assert it.sent[0][1].text == texts.STILL_WORKING
        await core.clock.advance(120)
        await task
        assert len(it.sent) >= 2 and it.sent[1][1].text != texts.STILL_WORKING

    run(go)


@pytest.mark.parametrize("name", list(COMMANDS))
def test_every_command_answers_at_once_when_the_core_is_quick(
    core: FakeCore, name: str
) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, name, COMMANDS[name])
        assert it.acks == [0.0] and it.sent and it.sent[0][0] == 0.0
        assert texts.STILL_WORKING not in it.texts

    run(go)


def test_a_long_design_job_is_acknowledged_at_once_and_its_result_follows(
    core: FakeCore,
) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, "plan", {"target": "steel ingot", "amount": "2/s"})
        assert it.first_answer_at() == 0.0
        assert len(it.sent) == 1 and "queued" in it.texts[0]
        # The design takes a while (the core's worker); nothing comes until it's done.
        assert await bot.deliver() == 0
        await core.clock.advance(300)
        assert core.queue.run_one() is not None
        assert await bot.deliver() == 1
        result = it.sent[-1][1]
        assert "route 1 of 2: EBF steel (plan:pl-00000001)" in result.text
        assert "route 2 of 2: Steel dust (plan:pl-00000002)" in result.text
        assert "3 machines, 120 EU/t average" in result.text
        assert "https://pc.tail1.ts.net/#/designer/pl-00000001" in result.text
        assert result.files == (
            ("pl-00000001-flow.png", PNG),
            ("pl-00000001-bom.tsv", BOM),
        )
        assert ("export", ("pl-00000001", "png")) in core.calls
        assert await bot.deliver() == 0  # delivered once

    run(go)


# --- the commands ---------------------------------------------------------------------------


def test_ask_relays_clayton_s_text_and_posts_the_cited_answer(core: FakeCore) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, "ask", {"text": "  how do I\nmake steel? "})
        assert ("ask", ("how do I make steel?",)) in core.calls
        assert it.texts == [
            "Steel Ingot: Electric Blast Furnace [r:abc123].\nrefs: r:abc123"
        ]
        assert core.journal_lines and core.journal_lines[0][0] == "ask"

    run(go)


@pytest.mark.parametrize("text", ["", " ", "x" * (handlers.TEXT_MAX + 1)])
def test_ask_refuses_empty_or_too_long_text_without_calling_the_core(
    core: FakeCore, text: str
) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, "ask", {"text": text})
        assert "ask" not in core.names() and "1 to" in it.texts[0]

    run(go)


def test_no_ai_mode_says_what_needs_a_model_and_lookups_still_work(
    core: FakeCore,
) -> None:
    """The core's no-AI router (plan 7.16) answers lookups and says what needs the AI; the bot
    passes that on, /status says so, and /plan (no model) still queues."""
    core.ai_online = False
    core.answer = lambda text: {
        "text": "The AI is offline (no API key), so I can't explain routes. Steel Ingot: r:abc123.",
        "lane": "no_ai",
        "refs": ["r:abc123"],
    }

    async def go() -> None:
        bot = make_bot(core, [])
        asked = clayton(core)
        await bot.interaction(asked, "ask", {"text": "why route 2?"})
        assert "AI is offline" in asked.texts[0] and "r:abc123" in asked.texts[0]
        status = clayton(core)
        await bot.interaction(status, "status", {})
        assert (
            "AI: offline (no API key); lookups, plans and status still work"
            in status.texts[0]
        )
        plan = clayton(core)
        await bot.interaction(plan, "plan", {"target": "steel ingot", "amount": "64"})
        assert "queued" in plan.texts[0]

    run(go)


def test_a_deep_lane_handoff_is_followed_until_it_ends(core: FakeCore) -> None:
    core.answer = lambda text: {
        "text": "Handing this to the deep lane.",
        "lane": "deep",
        "handoff": {"id": "t-1"},
    }
    core.deep = [
        {"id": "t-1", "status": "running"},
        {"id": "t-1", "status": "needs_approval"},
        {"id": "t-1", "status": "needs_approval"},
        {
            "id": "t-1",
            "status": "done",
            "text": "Route 1 is cheaper [plan:pl-00000001].",
        },
    ]

    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, "ask", {"text": "compare my steel routes"})
        assert it.texts == ["Handing this to the deep lane."]
        await core.clock.advance(handlers.DEEP_POLL_S * 5)
        assert it.texts[1:] == [
            texts.DEEP_PAUSED,
            "Route 1 is cheaper [plan:pl-00000001].",
        ]
        assert core.names().count("deep_task") == 4
        assert (
            "deep",
            "t-1 done: Route 1 is cheaper [plan:pl-00000001].",
        ) in core.journal_lines

    run(go)


def test_a_late_answer_goes_by_dm_when_the_interaction_has_run_out(
    core: FakeCore,
) -> None:
    core.answer = lambda text: {
        "text": "Deep lane.",
        "lane": "deep",
        "handoff": {"id": "t-2"},
    }
    core.deep = [{"id": "t-2", "status": "running"}]
    dms: list[Message] = []

    async def go() -> None:
        bot = make_bot(core, dms)
        it = clayton(core)
        await bot.interaction(it, "ask", {"text": "a long one"})
        await core.clock.advance(handlers.FOLLOWUP_TTL_S + 60, step=5)
        core.deep = [{"id": "t-2", "status": "done", "text": "Finally."}]
        await core.clock.advance(handlers.DEEP_POLL_S * 2)
        assert [m.text for m in dms] == ["Finally."]
        assert "Finally." not in it.texts

    run(go)


def test_a_follow_up_that_discord_refuses_goes_by_dm(core: FakeCore) -> None:
    dms: list[Message] = []

    async def go() -> None:
        bot = make_bot(core, dms)
        it = clayton(core)
        await bot.interaction(it, "power", {"mode": "best"})
        it.expired = True  # deleted, or Discord forgot it
        core.queue.run_one()
        assert await bot.deliver() == 1
        assert (
            len(dms) == 1
            and "option 1 of 1: Steam turbines (plan:pw-00000001)" in dms[0].text
        )
        assert "https://pc.tail1.ts.net/#/power/pw-00000001" in dms[0].text

    run(go)


def test_plan_validates_its_options(core: FakeCore) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        for opts in (
            {"target": "", "amount": "2/s"},
            {"target": "steel", "amount": " "},
        ):
            it = clayton(core)
            await bot.interaction(it, "plan", opts)
            assert "Give a target" in it.texts[0]
        it = clayton(core)
        await bot.interaction(
            it,
            "plan",
            {"target": "steel", "amount": "64", "within": "2h", "tier": "MV"},
        )
        (call,) = [c for c in core.calls if c[0] == "submit_job"]
        assert call[1][:2] == (
            "plan",
            {"target": "steel", "batch": "64", "within": "2h", "tier": "MV"},
        )
        assert call[1][2] == it.id  # the interaction's id keys the job

    run(go)


def test_a_failed_or_ambiguous_plan_says_so(core: FakeCore) -> None:
    def runner(kind: str, spec: Any) -> dict[str, Any]:  # noqa: ANN401
        if spec["target"] == "steel":
            return {
                "clarify": {
                    "question": "Which steel?",
                    "candidates": [
                        {
                            "n": 1,
                            "name": "Steel Ingot",
                            "key": "gregtech:gt.metaitem.01@11305",
                        }
                    ],
                }
            }
        raise ValueError("no item or fluid called unobtainium")

    core.queue.run = runner

    async def go() -> None:
        bot = make_bot(core, [])
        a, b = clayton(core), clayton(core)
        await bot.interaction(a, "plan", {"target": "steel", "amount": "1/s"})
        await bot.interaction(b, "plan", {"target": "unobtainium", "amount": "1/s"})
        core.queue.run_one()
        core.queue.run_one()
        assert await bot.deliver() == 2
        assert (
            "Which steel?" in a.texts[-1]
            and "gregtech:gt.metaitem.01@11305" in a.texts[-1]
        )
        assert a.sent[-1][1].files == ()
        assert "failed: no item or fluid called unobtainium" in b.texts[-1]
        assert "export" not in core.names()

    run(go)


def test_power_refuses_an_unknown_mode(core: FakeCore) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, "power", {"mode": "cheapest"})
        assert "mode is one of" in it.texts[0] and "submit_job" not in core.names()

    run(go)


def test_status_shows_telemetry_and_the_ledger_with_citations(core: FakeCore) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, "status", {})
        text = it.texts[0]
        assert "AI: online" in text
        assert "Base (lab): 1 OC computer(s)" in text
        assert (
            "warn: EBF out of power [tel:lab/42]" in text
            and "LSC: 81.5% [tel:lab/43]" in text
        )
        assert "MV: 512, 300.5, 211.5 [plan:pl-00000001]" in text

    run(go)


def test_status_shows_what_it_could_and_why_not_the_rest(core: FakeCore) -> None:
    real = core.live

    def broken() -> dict[str, Any]:
        core.calls.append(("live", ()))
        raise CoreError(
            "GET /api/base/live: 503 the OC bridge isn't set up in this core", 503
        )

    core.live = broken  # type: ignore[method-assign]

    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, "status", {})
        assert "EU ledger" in it.texts[0]
        assert (
            "(base: GOS couldn't do that: the OC bridge isn't set up in this core)"
            in it.texts[0]
        )

    run(go)
    assert real is not None


def test_alerts_and_persona(core: FakeCore) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        answers = []
        for opts in (
            {"mode": "off"},
            {"mode": "quiet"},
            {"mode": "loud"},
            {"mode": "quiet", "hours": 3},
        ):
            it = clayton(core)
            await bot.interaction(it, "alerts", opts)
            answers.append(it.texts[0])
        assert answers == [
            "Alerts are off.",
            "quiet needs hours (1-24)",
            "mode is on, off or quiet",
            "Alerts are quiet until <t:1800007200:t>.",
        ]
        assert ("set_alerts", ("quiet", 3.0)) in core.calls
        listing = clayton(core)
        await bot.interaction(listing, "persona", {})
        assert listing.texts[0].splitlines()[:3] == [
            "Personas:",
            "* ship-ai: Ship AI",
            "- plain: Plain",
        ]
        switch = clayton(core)
        await bot.interaction(switch, "persona", {"name": "plain"})
        assert switch.texts == ["Persona: Plain (plain)."]
        unknown = clayton(core)
        await bot.interaction(unknown, "persona", {"name": "nobody"})
        assert unknown.texts == ["GOS couldn't do that: no persona called nobody"]

    run(go)


def test_an_offline_core_is_said_plainly(core: FakeCore) -> None:
    core.offline = True

    async def go() -> None:
        bot = make_bot(core, [])
        for name in ("ask", "plan", "power", "alerts", "persona"):
            it = clayton(core)
            await bot.interaction(it, name, COMMANDS[name])
            assert it.texts == [texts.OFFLINE], name

    run(go)


def test_a_command_or_component_this_bot_does_not_offer(core: FakeCore) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        for kind, name in (
            ("command", "old"),
            ("select", "gos:pick"),
            ("modal", "gos:form"),
            ("component", "other:1"),
        ):
            it = clayton(core, kind)
            await bot.interaction(it, name, {})
            assert it.texts == [texts.GONE]
        assert core.calls == []

    run(go)


# --- confirm buttons (D59, D220) ------------------------------------------------------------

PENDING = {
    "id": 7,
    "readback": "Craft 64 Stainless Steel Ingot. Say confirm.",
    "expires": 1_800_000_060,
}


def test_a_pending_action_is_offered_once_with_buttons_and_confirmed_through_the_desk(
    core: FakeCore,
) -> None:
    core.pending = dict(PENDING)

    async def go() -> None:
        bot = make_bot(core, [])
        it = clayton(core)
        await bot.interaction(it, "ask", {"text": "craft 64 stainless steel"})
        offer = it.sent[-1][1]
        assert offer.text == (
            "Action 7 waits for you: Craft 64 Stainless Steel Ingot. Say confirm. "
            "Expires <t:1800000060:R>."
        )
        assert [b.custom_id for b in offer.buttons] == ["gos:confirm:7", "gos:cancel:7"]
        again = clayton(core)
        await bot.interaction(again, "status", {})
        assert all(not m.buttons for _, m in again.sent)  # offered once
        press = clayton(core, "component")
        await bot.interaction(press, "gos:confirm:7")
        assert press.acks == [0.0]  # the buttons come off first
        assert ("confirm", (7,)) in core.calls
        assert press.texts == ["Done: 64 Stainless Steel Ingot queued."]
        assert (
            "action",
            "confirm 7: Done: 64 Stainless Steel Ingot queued.",
        ) in core.journal_lines

    run(go)


def test_a_stale_button_gets_the_desk_s_own_refusal(core: FakeCore) -> None:
    core.confirm_error = CoreError(
        "POST /api/actions/7/confirm: 409 That action expired. Nothing was sent.", 409
    )

    async def go() -> None:
        bot = make_bot(core, [])
        press = clayton(core, "component")
        await bot.interaction(press, "gos:confirm:7")
        assert press.texts == ["That action expired. Nothing was sent."]
        cancel = clayton(core, "component")
        await bot.interaction(cancel, "gos:cancel:8")
        assert ("cancel", (8,)) in core.calls and cancel.texts == ["Cancelled."]
        bad = clayton(core, "component")
        await bot.interaction(bad, "gos:confirm:seven")
        assert bad.texts == [texts.GONE] and ("confirm", ("seven",)) not in core.calls

    run(go)


# --- restarts -------------------------------------------------------------------------------


def test_a_job_queued_before_a_bot_restart_is_delivered_once_by_dm(
    core: FakeCore,
) -> None:
    dms: list[Message] = []

    async def go() -> None:
        first = make_bot(core, [])
        it = clayton(core)
        await first.interaction(it, "plan", {"target": "steel ingot", "amount": "2/s"})
        # The bot restarts; Discord resends nothing, but if it did, the key finds the same job.
        second = make_bot(core, dms)
        await second.interaction(it, "plan", {"target": "steel ingot", "amount": "2/s"})
        assert "already queued" in it.texts[-1]
        assert len(core.queue.jobs("discord")) == 1
        core.queue.run_one()
        third = make_bot(core, dms)  # restarted again: it never saw the interaction
        assert await third.deliver() == 1
        assert len(dms) == 1 and dms[0].files and "plan:pl-00000001" in dms[0].text
        assert await third.deliver() == 0 and await make_bot(core, dms).deliver() == 0
        assert len(dms) == 1

    run(go)


def test_a_lost_delivered_mark_posts_again_but_never_runs_the_job_again(
    core: FakeCore,
) -> None:
    dms: list[Message] = []
    runs: list[str] = []

    def runner(kind: str, spec: Any) -> dict[str, Any]:  # noqa: ANN401
        runs.append(kind)
        return designed(kind, spec)

    core.queue.run = runner
    real = core.delivered

    def lost(job_id: str) -> dict[str, Any]:
        core.calls.append(("delivered", (job_id,)))
        raise CoreError("POST /api/jobs/x/delivered: no answer on port 8765")

    async def go() -> None:
        bot = make_bot(core, dms)
        await bot.interaction(clayton(core), "power", {"mode": "best"})
        core.queue.run_one()
        core.delivered = lost  # type: ignore[method-assign]
        assert await bot.deliver() == 0  # posted, but the mark was lost
        core.delivered = real  # type: ignore[method-assign]
        assert await bot.deliver() == 1  # posted again (at least once) and marked
        assert await bot.deliver() == 0
        assert runs == ["power"]

    run(go)


def test_deliver_forever_keeps_going_after_an_error(core: FakeCore) -> None:
    async def go() -> None:
        bot = make_bot(core, [])
        calls = 0

        async def flaky() -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            return 0

        bot.deliver = flaky  # type: ignore[method-assign]
        task = asyncio.ensure_future(bot.deliver_forever())
        await core.clock.advance(handlers.JOB_POLL_S * 3)
        task.cancel()
        await settle()
        assert calls >= 3

    run(go)
