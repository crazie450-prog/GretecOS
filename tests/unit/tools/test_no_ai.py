"""WO-2.8: no-AI mode (plan 7.16), the credits flow (D48) and saved plans with no model call.

Detection (no key, 401/403, a network failure, an outage, the API's credit errors), degradation
(the router's lookups keep working and no model is called; deep-lane work says it needs the AI),
recovery, the credit thresholds and the decision API, all over the tool tests' made-up DB with the
fake clients (no key, no cost).
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from gregtec import cards
from gregtec.agents import no_ai, router, saved_plans
from gregtec.agents.ask import AI_OFFLINE, NOTHING_PENDING, Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane, LaneError
from gregtec.agents.no_ai import AiMode, CreditWatch, Problem
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.api import ai_routes, dashboard
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.fake import EchoClient, FakeClient
from gregtec.llm.prices import Usage
from gregtec.llm.summarise import SummariserOff, Summary
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.supervisor import EXIT_STOPPED

from .conftest import KEY

ADMIN = "admin-" + "b" * 40
BASE = "http://gos.localhost:8765"
URL = "https://api.anthropic.com/v1/messages"
RUN = "20261106T120000Z"  # conftest's export run
EBF = "r:gt.recipe.blastfurnace/9c40869458cb"


def status_error(
    status: int, message: str = "error", code: str | None = None
) -> anthropic.APIStatusError:
    error: dict[str, Any] = {"type": "error", "message": message}
    if code is not None:
        error["details"] = {"error_code": code}
    body = {"type": "error", "error": error}
    response = httpx2.Response(status, request=httpx2.Request("POST", URL), json=body)
    cls = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
    }.get(status, anthropic.APIStatusError)
    return cls(message, response=response, body=body)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(request=httpx2.Request("POST", URL))


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Flaky(EchoClient):
    """Echoes, except while ``error`` is set: then every call raises it."""

    def __init__(self) -> None:
        super().__init__()
        self.error: BaseException | None = None
        self.calls = 0

    def respond(self, request: dict[str, Any], *, streamed: bool) -> Any:  # noqa: ANN401
        self.calls += 1
        if self.error is not None:
            raise self.error
        return super().respond(request, streamed=streamed)


@pytest.fixture
def deep() -> HandoffRecorder:
    return HandoffRecorder()


@pytest.fixture
def agents_reg(tool_db: Path, glossary: Glossary, deep: HandoffRecorder) -> Registry:
    return build_registry(tool_db, glossary, key=KEY, deep=deep)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "core.sqlite")
    yield s
    s.close()


def asker(
    reg: Registry, deep: HandoffRecorder, client: FakeClient, ai: AiMode
) -> Asker:
    return Asker(reg, deep, FastLane(client, reg, meter=None), ai=ai)


# --- detection ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (status_error(401, "invalid x-api-key"), no_ai.AUTH),
        (status_error(403, "not allowed"), no_ai.AUTH),
        (connection_error(), no_ai.NETWORK),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", URL)), no_ai.NETWORK),
        (status_error(500, "internal"), no_ai.OUTAGE),
        (status_error(529, "Overloaded"), no_ai.OUTAGE),
        (
            status_error(400, "Your credit balance is too low to access the API."),
            no_ai.CREDITS_API,
        ),
        (status_error(400, "x", code="billing_error"), no_ai.CREDITS_API),
        (
            status_error(400, "You have reached your specified API usage limits."),
            no_ai.CREDITS_API,
        ),
        (
            status_error(429, "x", code="enforced_spend_limit_reached"),
            no_ai.CREDITS_API,
        ),
        (status_error(429, "rate limited"), None),  # the next request may well work
        (status_error(400, "messages: bad"), None),  # our bug, not an outage
        (ValueError("not the API"), None),
    ],
)
def test_classify(exc: BaseException, reason: str | None) -> None:
    found = no_ai.classify(exc)
    assert (found.reason if found else None) == reason
    if found is not None:
        assert found.detail and "sk-ant" not in found.detail


def test_classify_reads_the_error_a_lane_error_wraps() -> None:
    try:
        try:
            raise status_error(401, "invalid x-api-key")
        except anthropic.APIError as exc:
            raise LaneError("AuthenticationError: ...") from exc
    except LaneError as wrapped:
        found = no_ai.classify(wrapped)
    assert found is not None and found.reason == no_ai.AUTH


# --- the mode -------------------------------------------------------------------------------


def test_the_mode_is_online_until_something_turns_it_off() -> None:
    mode = AiMode()
    assert mode.allowed() and mode.reasons() == [] and mode.status()["online"]
    mode.key_seen(False)
    assert not mode.allowed() and mode.reasons() == [no_ai.NO_KEY]
    s = mode.status()
    assert s["online"] is False and s["reason"] == "no_key"
    assert s["detail"] == "no Anthropic key is stored" and "Search" in s["works"]
    mode.key_seen(True)  # recovery: a key is stored again
    assert mode.allowed() and mode.status()["online"]


def test_an_api_problem_lets_one_request_through_a_minute() -> None:
    clock = Clock()
    mode = AiMode(clock=clock, wall=lambda: 1_700_000_000.0)
    pushes: list[int] = []
    mode.on_change = lambda: pushes.append(1)
    found = mode.failed(connection_error())
    assert found == Problem(no_ai.NETWORK, found.detail if found else "")
    assert (
        mode.status()["reason"] == "network" and mode.status()["since"] == 1_700_000_000
    )
    assert not mode.allowed() and not mode.probe_due()  # just tried
    clock.t += no_ai.PROBE_S
    assert mode.allowed()  # one real request may try
    assert not mode.allowed()  # and only one
    clock.t += no_ai.PROBE_S
    assert mode.probe_due() and not mode.probe_due()
    mode.succeeded()
    assert mode.allowed() and mode.reasons() == []
    assert pushes == [1, 1]  # off, then on


def test_a_probe_clears_an_api_problem_but_never_credits() -> None:
    clock = Clock()
    mode = AiMode(clock=clock)
    mode.failed(status_error(401, "invalid x-api-key"))
    clock.t += no_ai.PROBE_S
    assert mode.probe_due()
    mode.probe(lambda: Problem(no_ai.AUTH, "still refused"))
    assert mode.reasons() == [no_ai.AUTH]
    mode.probe(lambda: None)
    assert mode.reasons() == []
    mode.failed(status_error(400, "Your credit balance is too low"))
    clock.t += no_ai.PROBE_S
    assert not mode.probe_due()  # models.list can't see credits: a real request must
    assert mode.allowed()


def test_a_probe_that_raises_leaves_the_mode_alone() -> None:
    mode = AiMode()
    mode.failed(connection_error())

    def no_key() -> Problem | None:
        raise keys.MissingKeyError("anthropic")

    mode.probe(no_key)
    assert mode.reasons() == [no_ai.NETWORK]


def test_a_new_key_is_checked_at_once() -> None:
    clock = Clock()
    mode = AiMode(clock=clock)
    mode.key_seen(True)
    mode.failed(status_error(401, "invalid x-api-key"))
    assert not mode.probe_due()
    mode.key_seen(False)
    mode.key_seen(True)  # `gos key set anthropic` with a new key
    assert mode.probe_due()


def test_his_switch_holds_over_a_restart(store: Store) -> None:
    mode = AiMode(store)
    mode.set_choice(False)
    assert mode.reasons() == [no_ai.CHOICE] and not mode.allowed()
    again = AiMode(store)
    assert again.reasons() == [no_ai.CHOICE]
    again.set_choice(True)
    assert AiMode(store).allowed()


# --- degradation: the router with the key revoked -------------------------------------------


def test_with_the_key_revoked_lookups_work_and_no_model_is_called(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    mode = AiMode()
    mode.key_seen(False)
    client = EchoClient()
    a = asker(agents_reg, deep, client, mode)
    recipe = a.ask("recipe for steel")  # an instant command
    assert recipe.lane == "instant" and recipe.text.startswith("Steel Ingot: ")
    assert recipe.card is not None and recipe.card["type"] == "recipe"
    assert a.ask("what uses steel").text.startswith("Steel Ingot goes into ")
    assert a.ask("what's my tier").text.startswith("Your tier: ")
    assert a.ask("confirm").text == NOTHING_PENDING
    fast = a.ask("How do I make steel?")  # a fast-lane question answered from its card
    assert fast.lane == "no_ai" and fast.text.startswith("Steel Ingot: ")
    assert fast.text.endswith(f"({AI_OFFLINE})") and fast.error == no_ai.NO_KEY
    assert fast.card is not None and cards.validate(fast.card) == []
    assert a.ask("why are there so many tiers").text == AI_OFFLINE
    design = a.ask("Design a steel line for me")
    assert design.lane == "no_ai" and design.handoff is None and deep.recent() == []
    assert "needs the AI" in design.text and "no Anthropic key is stored" in design.text
    assert a.prewarm() == "offline"
    assert client.requests == []  # no model call at all
    mode.key_seen(True)  # recovery
    assert a.ask("How do I make steel?").lane == "fast"
    assert len(client.requests) == 1


def test_an_api_outage_turns_it_off_and_it_comes_back_by_itself(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    clock = Clock()
    mode = AiMode(clock=clock)
    client = Flaky()
    a = asker(agents_reg, deep, client, mode)
    client.error = connection_error()
    first = a.ask("How do I make steel?")
    assert first.lane == "no_ai" and first.text.startswith("Steel Ingot: ")
    assert mode.reasons() == [no_ai.NETWORK]
    tried = client.calls
    second = a.ask("How do I make steel?")  # within the minute: no call at all
    assert second.lane == "no_ai" and second.error == no_ai.NETWORK
    assert client.calls == tried
    assert a.ask("Design a steel line").lane == "no_ai"
    client.error = None  # the API is back
    clock.t += no_ai.PROBE_S
    back = a.ask("How do I make steel?")
    assert back.lane == "fast" and mode.reasons() == []


def test_a_401_is_auth_and_a_rate_limit_is_not_an_outage(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    mode = AiMode(clock=Clock())
    client = Flaky()
    a = asker(agents_reg, deep, client, mode)
    client.error = status_error(429, "rate limited")
    assert a.ask("How do I make steel?").lane == "no_ai"
    assert mode.reasons() == []  # the next request still tries
    client.error = status_error(401, "invalid x-api-key")
    assert a.ask("How do I make steel?").lane == "no_ai"
    assert mode.reasons() == [no_ai.AUTH]
    assert "refused the key" in mode.status()["detail"]


# --- the credits flow (D48) -----------------------------------------------------------------


NOW = datetime(2026, 11, 11, 12, 0).astimezone()
NEXT = datetime(2026, 12, 2, 12, 0).astimezone()


def spend(meter: UsageMeter, usd: float, when: datetime = NOW) -> None:
    meter.record("fast", "claude-haiku-5-5", Usage(), usd=usd, ts=when.timestamp())


@pytest.fixture
def meter(store: Store) -> UsageMeter:
    return UsageMeter(store, 100.0, (80, 100))


def test_80_raises_a_banner_and_100_switches_to_no_ai(meter: UsageMeter) -> None:
    mode = AiMode(meter.store)
    watch = CreditWatch(meter, mode)
    raised: list[int] = []
    watch.on_raise = lambda pct, cycle: raised.append(pct)
    spend(meter, 79.0)
    s = watch.check(NOW)
    assert s["level"] is None and s["threshold"] is None and not s["pending"]
    assert raised == [] and mode.allowed()
    spend(meter, 1.5)  # 80.5%
    s = watch.check(NOW)
    assert (s["level"], s["threshold"], s["pending"], s["paused"]) == (
        "warn",
        80,
        True,
        False,
    )
    assert s["actions"] == ["billing", "no_ai", "stop"] and s["billing_url"].startswith(
        "https://"
    )
    assert raised == [80] and mode.allowed()  # 80% only asks
    watch.check(NOW)
    assert raised == [80]  # once per cycle
    spend(meter, 20.0)  # 100.5%
    s = watch.check(NOW)
    assert (s["level"], s["threshold"], s["pending"], s["paused"]) == (
        "err",
        100,
        True,
        True,
    )
    assert s["actions"] == ["billing", "resume", "stop"]
    assert raised == [80, 100]
    assert not mode.allowed() and mode.reasons() == [no_ai.CREDITS]
    assert mode.status()["detail"] == "paused for credits"


def test_billing_keeps_the_pause_and_resume_ends_it_for_the_cycle(
    meter: UsageMeter,
) -> None:
    mode = AiMode(meter.store)
    watch = CreditWatch(meter, mode)
    spend(meter, 101.0)
    watch.check(NOW)
    s = watch.decide("billing")
    assert s["answer"] == "billing" and s["paused"] and not mode.allowed()
    s = watch.decide("resume")
    assert not s["paused"] and not s["pending"] and mode.allowed()
    spend(meter, 5.0)
    watch.check(NOW)
    assert mode.allowed()  # 100% was answered: it doesn't pause again this cycle
    s = watch.check(NEXT)  # the next cycle's credits arrive
    assert s["threshold"] is None and s["level"] is None


def test_no_ai_at_80_holds_until_the_next_cycle(meter: UsageMeter) -> None:
    mode = AiMode(meter.store)
    watch = CreditWatch(meter, mode)
    spend(meter, 85.0)
    watch.check(NOW)
    s = watch.decide("no_ai")
    assert s["paused"] and s["answer"] == "no_ai" and not mode.allowed()
    watch.check(NOW)
    assert not mode.allowed()
    watch.check(NEXT)
    assert mode.allowed()


def test_resume_at_80_doesnt_skip_the_pause_at_100(meter: UsageMeter) -> None:
    mode = AiMode(meter.store)
    watch = CreditWatch(meter, mode)
    spend(meter, 85.0)
    watch.check(NOW)
    watch.decide("no_ai")
    watch.decide("resume")
    assert mode.allowed()
    spend(meter, 20.0)
    watch.check(NOW)
    assert not mode.allowed()


def test_the_pause_and_the_raised_thresholds_survive_a_restart(
    meter: UsageMeter,
) -> None:
    watch = CreditWatch(meter, AiMode(meter.store))
    spend(meter, 100.0)
    watch.check(NOW)
    mode = AiMode(meter.store)
    again = CreditWatch(meter, mode)
    raised: list[int] = []
    again.on_raise = lambda pct, cycle: raised.append(pct)
    assert not mode.allowed()  # paused from the start
    s = again.check(NOW)
    assert raised == [] and s["paused"] and s["pending"]


def test_stop_answers_and_stops(meter: UsageMeter) -> None:
    stopped: list[int] = []
    watch = CreditWatch(meter, AiMode(meter.store), stop=lambda: stopped.append(1))
    spend(meter, 100.0)
    watch.check(NOW)
    s = watch.decide("stop")
    assert stopped == [1] and s["answer"] == "stop" and s["paused"]
    with pytest.raises(ValueError, match="choice must be one of"):
        watch.decide("buy credits")  # GOS never buys credits


def test_resume_also_ends_his_own_switch(meter: UsageMeter) -> None:
    mode = AiMode(meter.store)
    watch = CreditWatch(meter, mode)
    mode.set_choice(False)
    watch.decide("resume")
    assert mode.allowed()


# --- saved plans ----------------------------------------------------------------------------


def plan_home(tmp_path: Path, tool_db: Path, plans: list[dict[str, Any]]) -> Path:
    """A copy of the tool DB with ``plans`` saved (rows + gos-plan/0 JSON): the DB's path."""
    home = tmp_path / "home"
    (home / "data" / "plans").mkdir(parents=True)
    db = home / "data" / "gos.sqlite"
    shutil.copyfile(tool_db, db)
    conn = sqlite3.connect(db)
    try:
        run_id = conn.execute(
            "SELECT id FROM export_run WHERE run_key = ?", (RUN,)
        ).fetchone()[0]
        for i, p in enumerate(plans):
            doc = {"format": "gos-plan/0", "run": RUN, "cites": [EBF], **p}
            path = f"data/plans/{p['id']}.json"
            (home / path).write_text(json.dumps(doc), "utf-8")
            conn.execute(
                "INSERT INTO plan(id, name, created, kind, status, json_path, run_id,"
                " affected_run) VALUES (?, ?, ?, 'production', ?, ?, ?, ?)",
                (p["id"], p["name"], i, p.get("status", "accepted"), path, run_id,
                 p.get("affected")),
            )  # fmt: skip
        conn.commit()
    finally:
        conn.close()
    return db


STEEL_PLAN = {
    "id": "pl-steel",
    "name": "Steel line",
    "bom": [
        {"key": "gregtech:gt.metaitem.01@11305", "name": "Steel Ingot", "amount": 64,
         "unit": "items", "ref": EBF},
        {"key": "minecraft:coal@0", "name": "Coal", "amount": 12.5, "unit": "items",
         "ref": None},
        {"key": "bad", "name": "No amount", "unit": "items"},
        {"key": "bad", "name": "Bad ref", "amount": 1, "unit": "x", "ref": "<b>"},
    ],
}  # fmt: skip


def test_saved_plans_work_with_the_ai_off(
    tmp_path: Path, tool_db: Path, glossary: Glossary, deep: HandoffRecorder
) -> None:
    db = plan_home(
        tmp_path,
        tool_db,
        [STEEL_PLAN, {"id": "pl-power", "name": "Steam power", "affected": 1}],
    )
    reg = build_registry(db, glossary, key=KEY, deep=deep)
    mode = AiMode()
    mode.set_choice(False)
    client = EchoClient()
    a = asker(reg, deep, client, mode)
    listed = a.ask("show my plans")
    assert listed.lane == "instant" and listed.text.startswith("2 saved plans: ")
    assert listed.card is not None and cards.validate(listed.card) == []
    assert listed.card["tool"] == "plan_list" and listed.card["total"] == 2
    one = a.ask("show plan steel line")
    assert one.lane == "instant" and one.refs == ["plan:pl-steel"]
    card = one.card
    assert card is not None and card["type"] == "bom" and cards.validate(card) == []
    assert card["ref"] == "plan:pl-steel"
    assert [r["name"] for r in card["rows"]] == ["Steel Ingot", "Coal"]
    assert card["rows"][0]["amount"] == 64 and card["rows"][1]["ref"] is None
    assert any("row 3 left out" in n for n in card["notes"])
    assert any("row 4 left out" in n for n in card["notes"])
    assert a.ask("BOM for the steel plan").card == card
    assert a.ask("plan:pl-steel").card == card
    power = a.ask("open plan steam power")
    assert (
        power.card is not None
        and "no BOM is saved with this plan" in power.card["notes"]
    )
    assert any("affected by a pack update" in n for n in power.card["notes"])
    assert a.ask("show plan unobtainium").text == "No saved plan matches 'unobtainium'."
    assert client.requests == []


def test_plans_that_dont_read_are_named_not_shown(
    tmp_path: Path, tool_db: Path
) -> None:
    db = plan_home(tmp_path, tool_db, [STEEL_PLAN, {"id": "pl-b", "name": "B"}])
    (db.parent / "plans" / "pl-b.json").write_text("{not json", "utf-8")
    found, problems = saved_plans.load(db)
    assert [p.id for p in found] == ["pl-steel"]
    assert problems and problems[0].startswith("plan pl-b didn't read")
    card = saved_plans.list_card(found, problems)
    assert cards.validate(card) == [] and card["notes"] == problems


def test_no_db_or_no_plans_is_no_plans(tmp_path: Path, tool_db: Path) -> None:
    assert saved_plans.load(tmp_path / "missing.sqlite") == ([], [])
    assert saved_plans.load(tool_db) == ([], [])
    assert router.route("my plans").intent == router.PLANS


def test_find_by_id_name_or_words() -> None:
    def plan(pid: str, name: str) -> saved_plans.SavedPlan:
        return saved_plans.SavedPlan(pid, name, "", None, RUN, None, None)

    found = [plan("pl-1", "Steel line"), plan("pl-2", "Stainless steel line")]
    assert [p.id for p in saved_plans.find(found, "plan:pl-2")] == ["pl-2"]
    assert [p.id for p in saved_plans.find(found, "steel line")] == ["pl-1"]
    assert [p.id for p in saved_plans.find(found, "steel")] == ["pl-1", "pl-2"]
    assert saved_plans.find(found, "copper") == []


# --- the core: state, the decision API, Stop ------------------------------------------------


@pytest.fixture
def core(tmp_path: Path, tool_db: Path, glossary: Glossary) -> Iterator[Core]:
    home = tmp_path / "corehome"
    (home / "data").mkdir(parents=True)
    db = home / "data" / "gos.sqlite"
    shutil.copyfile(tool_db, db)
    store = Store(db)
    meter = UsageMeter(store, 100.0, (80, 100))
    deep = HandoffRecorder()
    registry = build_registry(db, glossary, key=KEY, deep=deep)
    c = Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=meter,
        board=dashboard.Board(
            Config(gos_home=home),
            dist=tmp_path / "dist",
            foreground=lambda: None,
            clients=lambda cfg: [],
            next_focus=math.inf,
            next_game=math.inf,
            next_data=math.inf,
        ),
    )
    mode = AiMode(store)
    ai_routes.wire(c, mode, CreditWatch(meter, mode))
    c.asker = Asker(
        registry, deep, FastLane(EchoClient(), registry, meter=None), ai=mode
    )
    yield c
    store.close()


@pytest.fixture
def client(core: Core, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    stored = {"yes": True}
    monkeypatch.setattr(keys, "has_key", lambda name: stored["yes"])
    core.key_status = ("ok", "stored")
    with TestClient(create_app(core), base_url=BASE) as c:
        c.stored = stored  # type: ignore[attr-defined]
        yield c


def admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN}"}


def token(client: TestClient, name: str) -> dict[str, str]:
    issued = client.post(f"/api/tokens/{name}", headers=admin()).json()["token"]
    return {"Authorization": f"Bearer {issued}"}


def test_the_key_revoked_end_to_end_through_the_api(
    client: TestClient, core: Core
) -> None:
    client.stored["yes"] = False  # type: ignore[attr-defined]  # the key is revoked
    core.refresh_key()
    s = client.get("/api/state", headers=admin()).json()
    assert s["ai"]["online"] is False and s["ai"]["reason"] == "no_key"
    assert s["credits"]["level"] is None
    recipe = client.post("/api/ask", headers=admin(), json={"text": "recipe for steel"})
    assert (
        recipe.json()["lane"] == "instant" and recipe.json()["card"]["type"] == "recipe"
    )
    tool = client.post(
        "/api/tools/recipe_find", headers=admin(), json={"args": {"item": "steel"}}
    )
    assert tool.status_code == 200  # Search runs the lookup tools directly
    asked = client.post(
        "/api/ask", headers=admin(), json={"text": "how do I make steel"}
    ).json()
    assert asked["lane"] == "no_ai" and asked["text"].startswith("Steel Ingot: ")
    client.stored["yes"] = True  # type: ignore[attr-defined]
    core.refresh_key()
    assert client.get("/api/ai", headers=admin()).json()["ai"]["online"] is True


def test_80_and_100_percent_through_the_state_and_the_decision_api(
    client: TestClient, core: Core
) -> None:
    core.meter.record("fast", "claude-haiku-5-5", Usage(), usd=81.0)
    ai_routes.refresh(core)
    s = client.get("/api/state", headers=admin()).json()
    assert s["credits"]["level"] == "warn" and s["credits"]["threshold"] == 80
    assert s["credits"]["pending"] and s["ai"]["online"]
    core.meter.record("fast", "claude-haiku-5-5", Usage(), usd=20.0)
    ai_routes.refresh(core)
    s = client.get("/api/state", headers=admin()).json()
    assert s["credits"]["level"] == "err" and s["credits"]["paused"]
    assert s["ai"]["online"] is False and s["ai"]["reason"] == "credits"
    reply = client.post("/api/ask", headers=admin(), json={"text": "why so many tiers"})
    assert reply.json()["lane"] == "no_ai"
    billing = client.post(
        "/api/credits/decision", headers=admin(), json={"choice": "billing"}
    ).json()
    assert billing["open"] == no_ai.BILLING_URL and billing["ai"]["online"] is False
    resumed = client.post(
        "/api/credits/decision", headers=admin(), json={"choice": "resume"}
    ).json()
    assert resumed["ai"]["online"] is True and not resumed["credits"]["paused"]


def test_who_may_answer_the_credit_alert(client: TestClient) -> None:
    voice = token(client, "voice")
    refused = client.post(
        "/api/credits/decision", headers=voice, json={"choice": "no_ai"}
    )
    assert refused.status_code == 403
    bot = token(client, "discord")  # WO-9.2's buttons
    ok = client.post("/api/credits/decision", headers=bot, json={"choice": "no_ai"})
    assert ok.status_code == 200 and ok.json()["ai"]["online"] is False
    bad = client.post("/api/credits/decision", headers=admin(), json={"choice": "buy"})
    assert bad.status_code == 400
    assert (
        client.post("/api/ai", headers=voice, json={"online": True}).status_code == 403
    )


def test_his_switch_through_the_api(client: TestClient) -> None:
    off = client.post("/api/ai", headers=admin(), json={"online": False}).json()
    assert off["ai"]["online"] is False and off["ai"]["reason"] == "choice"
    assert (
        client.post("/api/ai", headers=admin(), json={"online": "no"}).status_code
        == 400
    )
    on = client.post("/api/ai", headers=admin(), json={"online": True}).json()
    assert on["ai"]["online"] is True


def test_stop_stops_the_core_with_the_stopped_exit_code(
    client: TestClient, core: Core
) -> None:
    asked: list[int] = []
    core.shutdown = lambda: asked.append(1)
    core.meter.record("fast", "claude-haiku-5-5", Usage(), usd=81.0)
    ai_routes.refresh(core)  # the 80% alert is open
    out = client.post(
        "/api/credits/decision", headers=admin(), json={"choice": "stop"}
    ).json()
    assert out["stopping"] is True and asked == [1]
    assert core.exit_code == EXIT_STOPPED


def test_the_watcher_probes_an_api_problem_once_a_minute(core: Core) -> None:
    assert core.ai is not None
    clock = Clock()
    core.ai.clock = clock
    core.ai.failed(connection_error())
    calls: list[int] = []

    def probe() -> Problem | None:
        calls.append(1)
        return None

    core.probe = probe
    ai_routes.refresh(core)
    assert calls == []  # just failed
    clock.t += no_ai.PROBE_S
    ai_routes.refresh(core)
    assert calls == [1] and core.ai.allowed()


def test_the_credit_pause_stops_deep_lane_work(core: Core) -> None:
    class Task:
        def __init__(self, tid: str, status: str) -> None:
            self.id, self.status = tid, status

    class Deep:
        def __init__(self) -> None:
            self.stopped: list[str] = []

        def tasks(self) -> list[Task]:
            return [Task("a", "running"), Task("b", "queued"), Task("c", "done")]

        def stop_task(self, tid: str) -> bool:
            self.stopped.append(tid)
            return True

    lane = Deep()
    core.deep = lane  # type: ignore[assignment]
    core.meter.record("fast", "claude-haiku-5-5", Usage(), usd=100.0)
    ai_routes.refresh(core)
    assert lane.stopped == ["a", "b"]


@pytest.mark.parametrize(
    ("text", "kind", "intent", "subject"),
    [
        ("show my plans", "instant", router.PLANS, None),
        ("what plans do I have", "instant", router.PLANS, None),
        ("show plan steel line", "instant", router.PLAN, "steel line"),
        ("bill of materials for my steel plan", "instant", router.PLAN, "steel plan"),
        ("plan:pl-0001", "instant", router.PLAN, "plan:pl-0001"),
        (
            "show plan for a steel line",
            "deep",
            "handoff",
            None,
        ),  # deep words outrank (D150)
        ("plan a steel line", "deep", "handoff", None),
    ],
)
def test_saved_plan_routes(
    text: str, kind: str, intent: str, subject: str | None
) -> None:
    r = router.route(text)
    assert (r.kind, r.intent, r.subject) == (kind, intent, subject)


@pytest.mark.parametrize(
    "text",
    ["bom for an ebf", "bill of materials for steel", "show plan of attack for nether"],
)
def test_words_that_name_no_saved_plan_route_as_before_without_plans(
    text: str,
) -> None:
    r = router.route(text)
    assert (r.kind, r.intent) == ("instant", router.PLAN)  # a saved plan first
    assert router.route(text, plans=False).kind == "fast"  # then the AI, as before


def test_a_bom_question_that_names_no_saved_plan_goes_to_the_ai(
    tmp_path: Path, tool_db: Path, glossary: Glossary, deep: HandoffRecorder
) -> None:
    db = plan_home(tmp_path, tool_db, [STEEL_PLAN])
    reg = build_registry(db, glossary, key=KEY, deep=deep)
    mode = AiMode()
    client = EchoClient()
    a = asker(reg, deep, client, mode)
    saved = a.ask("BOM for the steel plan")  # a saved plan: no model call
    assert saved.lane == "instant" and saved.refs == ["plan:pl-steel"]
    assert client.requests == []
    for text in ("bom for an ebf", "show plan of attack for nether"):
        assert a.ask(text).lane == "fast", text
    assert len(client.requests) == 2
    mode.set_choice(False)  # with the AI off the saved-plan answer stays
    assert a.ask("bom for an ebf").text == "No saved plan matches 'an ebf'."
    assert len(client.requests) == 2


def test_plan_ids_match_in_any_case_and_long_refs_are_left_out() -> None:
    plan = saved_plans.SavedPlan("PL-Steel", "Steel", "", None, RUN, None, None)
    assert saved_plans.find([plan], "plan:PL-Steel") == [plan]
    notes: list[str] = []
    long_ref = "r:" + "x" * 199  # 201 characters: over the card schema's 200
    rows = saved_plans._bom(
        [{"key": "k", "name": "n", "amount": 1, "unit": "items", "ref": long_ref}],
        notes,
    )
    assert rows == [] and notes == ["bom row 1 left out: its ref isn't a citation id"]


def test_the_pause_is_at_100_percent_whatever_the_list_says(store: Store) -> None:
    only80 = UsageMeter(store, 100.0, (80,))
    mode = AiMode(store)
    watch = CreditWatch(only80, mode)
    spend(only80, 85.0)
    s = watch.check(NOW)
    assert (s["threshold"], s["level"], s["paused"]) == (80, "warn", False)
    assert mode.allowed()  # 80% only asks, even as the last threshold
    spend(only80, 15.0)  # 100%
    s = watch.check(NOW)
    assert (s["threshold"], s["level"], s["paused"]) == (100, "err", True)
    assert mode.reasons() == [no_ai.CREDITS]


def test_a_90_threshold_doesnt_pause(store: Store) -> None:
    meter = UsageMeter(store, 100.0, (80, 90))
    mode = AiMode(store)
    watch = CreditWatch(meter, mode)
    spend(meter, 95.0)
    assert watch.check(NOW)["threshold"] == 90 and mode.allowed()


def test_billing_or_stop_with_no_alert_open_is_refused(
    meter: UsageMeter, client: TestClient
) -> None:
    watch = CreditWatch(meter, AiMode(meter.store))
    for choice in ("billing", "stop"):
        with pytest.raises(no_ai.NoAlert):
            watch.decide(choice)
    got = client.post("/api/credits/decision", headers=admin(), json={"choice": "stop"})
    assert got.status_code == 409


class FakeSummariser:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls = 0
        self.error = error

    def summarise(
        self,
        period: str,
        sources: Sequence[tuple[str, str]],
        counts: Mapping[str, int],
    ) -> Summary:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return Summary(["note: ok"], "claude-haiku-5-5", Usage())


def test_maintenance_asks_the_mode_before_any_summariser_call(
    meter: UsageMeter,
) -> None:
    mode = AiMode(meter.store)
    inner = FakeSummariser()
    made: list[int] = []

    def make() -> FakeSummariser:
        made.append(1)
        return inner

    factory = no_ai.guard_summariser(mode, make)
    watch = CreditWatch(meter, mode)
    spend(meter, 101.0)
    watch.check(NOW)  # the credit pause
    with pytest.raises(SummariserOff, match="paused for credits"):
        factory()
    assert made == [] and inner.calls == 0  # not even the key is read
    watch.decide("resume")
    guarded = factory()
    assert guarded is not None
    assert guarded.summarise("2026-W44", [], {}).lines == ["note: ok"]
    mode.set_choice(False)  # turned off between two periods of one run
    with pytest.raises(SummariserOff, match="you turned the AI off"):
        guarded.summarise("2026-W45", [], {})
    assert inner.calls == 1


def test_maintenance_summariser_errors_take_the_ai_offline() -> None:
    mode = AiMode()
    inner = FakeSummariser(status_error(401, "invalid x-api-key"))
    guarded = no_ai.guard_summariser(mode, lambda: inner)()
    assert guarded is not None
    with pytest.raises(anthropic.AuthenticationError):
        guarded.summarise("2026-W44", [], {})
    assert mode.reasons() == [no_ai.AUTH]
    assert no_ai.guard_summariser(AiMode(), lambda: None)() is None  # no key stored
