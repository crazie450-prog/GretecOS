"""The route reviewer (WO-3.10 F7, D389 (7)) on the WO-3.4 fixture DB (tests/unit/planner/
test_plan.py), with :class:`gregtec.llm.fake.ScriptedClient` only: no key, no API call, no spend.
Every item, recipe and machine is made up; no pack data.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import anthropic
import httpx2
import jsonschema
import pytest

from gregtec.agents import route_review as rr
from gregtec.agents.no_ai import AiMode
from gregtec.agents.tools import designer as designer_tool
from gregtec.db import access
from gregtec.llm import keys
from gregtec.llm.fake import ScriptedClient, Turn
from gregtec.llm.prices import OPUS, SONNET
from gregtec.llm.usage import UsageMeter
from gregtec.planner import designer, plan, plancli, progress, rank
from gregtec.planner.request import Constraints, Request
from gregtec.store import Store
from gregtec.vault import deploy as vault_deploy

REPO = Path(__file__).resolve().parents[3]


def _plan_fixture() -> ModuleType:
    path = REPO / "tests" / "unit" / "planner" / "test_plan.py"
    spec = importlib.util.spec_from_file_location("_f7_plan_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tp = _plan_fixture()
WIDGET = tp.WIDGET
REQ = Request(WIDGET, qty=2, tier_cap="MV")
# The fixture's recipes for the widget and Dust C (test:item@3), by their stable ids.
CR_A = "r:gt.recipe.chemicalreactor/bab6b832f779"  # route 1's widget recipe
CR_B = "r:gt.recipe.chemicalreactor/4da7981992a7"
ELECTRO = "r:gt.recipe.electrolyzer/67545e3aed64"
ELECTRO_HV = "r:gt.recipe.electrolyzer/87c65eff537c"  # rejected: above the tier cap
MIXER = "r:gt.recipe.mixer/730f0e518f05"
ARC = "r:gt.recipe.arcfurnace/a6525d1937d8"  # Dust C's side route
DUST_C = "test:item@3"


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tp.make_home(tmp_path_factory.mktemp("review-db"))


@pytest.fixture
def home(db: Path, tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / "data").mkdir(parents=True)
    src = access.connect(db, readonly=True)
    dst = sqlite3.connect(h / "data" / "gos.sqlite")
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "core.sqlite")
    try:
        yield s
    finally:
        s.close()


def reviewer(
    turns: list[Turn],
    store: Store | None = None,
    *,
    ai: AiMode | None = None,
    cap_usd: float = 2.0,
) -> tuple[rr.Reviewer, ScriptedClient]:
    client = ScriptedClient(turns=turns)
    meter = UsageMeter(store, 100.0, (80, 100)) if store is not None else None
    return rr.Reviewer(client, SONNET, OPUS, meter, ai, cap_usd), client


def use(name: str, **inp: object) -> dict[str, Any]:
    return {"name": name, "input": inp}


def finish(verdict: str = "ok", summary: str = "Looks fine.") -> Turn:
    return Turn(tools=[use("finish", verdict=verdict, issues=[], summary=summary)])


def exclude(*keys_: str, reason: str = "it loops") -> Turn:
    return Turn(
        tools=[
            use(
                "propose_constraints",
                exclude=list(keys_),
                issue="a detour",
                reason=reason,
            )
        ]
    )


def pin(node: str, recipe: str, reason: str = "a shorter chain") -> Turn:
    return Turn(
        tools=[
            use(
                "propose_pin", node=node, recipe=recipe, issue="a detour", reason=reason
            )
        ]
    )


def results(client: ScriptedClient, i: int) -> list[dict[str, Any]]:
    """The tool results sent with request ``i``."""
    content = client.requests[i]["messages"][-1]["content"]
    assert isinstance(content, list)
    return content


def saved_reviews(home: Path) -> list[Path]:
    return sorted((home / designer.REVIEWS).glob("*.json"))


# --- the toggle -------------------------------------------------------------------------------


def test_off_by_default_no_model_call_and_the_same_routes(home: Path) -> None:
    d = designer.design(home, REQ)
    assert d.review is None and d.reviewed is None
    assert saved_reviews(home) == []
    spec = {"target": WIDGET, "rate": "2/s", "tier": "MV"}
    assert designer.wants_review(spec) is False
    assert designer.request_from_spec(home, spec) == designer.request_from_spec(
        home, {**spec, "review": True}
    )  # the toggle never reaches the request, so the plan ids don't change
    with pytest.raises(designer.DesignerError, match="review is true or false"):
        designer.request_from_spec(home, {**spec, "review": "yes"})
    again = designer.design(home, REQ)  # the same routes, and no review on any
    assert again.ids == d.ids and all("review" not in p for p in again.plans)


def test_not_offered_to_design_production_or_the_registry(tmp_path: Path) -> None:
    props = designer_tool.DESIGN_PRODUCTION_SCHEMA["properties"]
    assert "review" not in props
    from gregtec.agents.tools import default_registry

    names = {t.name for t in default_registry(tmp_path).tools()}
    assert not names & rr.TOOL_NAMES


def test_verdict_ok_one_call_recorded(home: Path, store: Store) -> None:
    r, client = reviewer([finish()], store)
    d = designer.design(home, REQ, reviewer=r)
    assert len(client.requests) == 1
    req = client.requests[0]
    assert req["model"] == SONNET and "thinking" not in req
    assert {t["name"] for t in req["tools"]} == set(rr.TOOL_NAMES)
    assert "tool_choice" not in req
    assert d.review is not None and d.reviewed is None
    assert d.review["status"] == "done" and d.review["verdict"] == "ok"
    assert d.review["summary_source"] == "model" and not d.review["edited"]
    rows = store.rows("SELECT lane, model, task FROM llm_usage")
    assert [(x["lane"], x["model"]) for x in rows] == [("review", SONNET)]
    assert rows[0]["task"] == f"review:{d.review['id']}"
    on_disk = json.loads(saved_reviews(home)[0].read_text(encoding="utf-8"))
    assert on_disk["id"] == d.review["id"] and on_disk["of"] == d.ids
    assert designer.view(home, d.ids[0])["review"]["id"] == d.review["id"]


# --- a re-run, an edit ------------------------------------------------------------------------


def test_a_rerun_adds_a_reviewed_column(home: Path, store: Store) -> None:
    plain = designer.design(home, REQ)
    r, client = reviewer(
        [exclude(CR_A, ELECTRO, CR_B), finish("fixed", "Re-ran without the loops.")],
        store,
    )
    d = designer.design(home, REQ, reviewer=r)
    assert d.ids[:-1] == plain.ids  # the planner's routes, ids and order unchanged
    assert d.review is not None and d.reviewed == d.ids[-1]
    rv = designer.load(home, d.reviewed)
    want = replace(
        REQ,
        routes=1,
        constraints=Constraints(exclude=(CR_A, ELECTRO, CR_B)),
    )
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        expect = designer._solve(conn, home, want).plans[0]
    finally:
        conn.close()
    assert rv["id"] == expect["id"] and rv["nodes"] == expect["nodes"]
    assert MIXER in {n["recipe"] for n in rv["nodes"]}
    assert rv["rank"]["column"] == "reviewed" and rv["name"].endswith(" · reviewed")
    tp.valid(rv)
    base = [designer.load(home, i) for i in plain.ids]
    assert rv["rank"] == plan._rounded(rank.against(base, rv, rank.BALANCED))
    for b, before in zip(base, plain.plans, strict=True):
        assert b["rank"] == before["rank"]
        assert d.reviewed in b["alternatives"]
    review = rv["review"]
    assert review["edited"] is False and review["verdict"] == "fixed"
    assert review["applied"] == {"exclude": sorted([CR_A, ELECTRO, CR_B]), "pins": []}
    assert review["changes"][0]["result"] == rv["id"]
    shown = json.loads(results(client, 1)[0]["content"])["top"]
    assert shown["column"] == "reviewed" and shown["diff"]["added"]
    v = designer.view(home, d.ids[0])
    assert [x["column"] for x in v["routes"]][-1] == "reviewed"
    assert v["routes"][-1]["review"]["id"] == review["id"]
    # the card keeps the 3 ranked routes; the reviewed route takes a place they leave
    # (D404 (8)), here only in the route list
    assert len(plain.ids) == 3
    assert [r["rank"] for r in v["comparison"]["routes"]] == [1, 2, 3]
    loaded = [designer.load(home, i) for i in d.ids]
    two = [x["id"] for x in designer._shown([*loaded[:2], loaded[-1]])]
    assert two == [*d.ids[:2], d.reviewed]
    # the reviewed route re-solves with no model, to the same id and nodes
    again = designer.resolve(home, d.reviewed)
    assert again.ids[0] == d.reviewed
    assert again.plans[0]["nodes"] == rv["nodes"]


def test_an_edit_waits_for_a_rerun_and_is_reported(home: Path) -> None:
    r, client = reviewer(
        [
            pin(DUST_C, ARC),  # before any re-run: refused
            exclude(ELECTRO),
            pin(DUST_C, ARC),
            finish("fixed", "Swapped Dust C's recipe."),
        ]
    )
    d = designer.design(home, REQ, reviewer=r)
    first = results(client, 1)[0]
    assert first["is_error"] and "propose_constraints first" in first["content"]
    assert d.review is not None and d.review["edited"] is True
    kinds = [(c["kind"], c["refused"]) for c in d.review["changes"]]
    assert kinds == [("exclude", None), ("pin", None)]
    assert d.review["changes"][1]["was"] == "r:gt.recipe.chemicalreactor/ac68fbd241c0"
    rv = designer.load(home, d.reviewed or "")
    assert ARC in {n["recipe"] for n in rv["nodes"]}
    assert rv["review"]["applied"]["pins"] == [{"node": DUST_C, "recipe": ARC}]
    route = designer.view(home, d.ids[0])["routes"][-1]
    assert route["review"]["edited"] is True
    lines = rr.describe(d.review)
    assert lines[0].startswith("EDITED by the reviewer: pinned test:item@3")
    note = (home / "vault" / "plans" / f"{rv['id']}.md").read_text(encoding="utf-8")
    assert "reviewed: edited by the route reviewer" in note


def test_a_pin_the_graph_rejects_is_refused_with_its_reason(home: Path) -> None:
    r, client = reviewer(
        [exclude(CR_B), pin(WIDGET, ELECTRO_HV), finish("unresolved", "Couldn't.")]
    )
    d = designer.design(home, REQ, reviewer=r)
    refused = results(client, 2)[0]
    assert refused["is_error"] and "above the cap" in refused["content"]
    assert d.review is not None and d.review["edited"] is False
    assert d.review["applied"] == {"exclude": [CR_B], "pins": []}
    assert d.review["changes"][-1]["refused"]


def test_the_same_recipes_as_a_ranked_route_add_no_column(home: Path) -> None:
    r, _ = reviewer([exclude(CR_A), finish("fixed", "Route 2 it is.")])
    d = designer.design(home, REQ, reviewer=r)
    assert d.reviewed is None and d.review is not None
    assert d.review["same_as"] == d.ids[1]


# --- numbers ------------------------------------------------------------------------------------


def test_a_made_up_number_is_refused_and_nothing_applied(home: Path) -> None:
    r, client = reviewer(
        [
            exclude(CR_A, ELECTRO, CR_B, reason="it needs 4242.5 machines"),
            finish("ok", "Saves 777.25 EU/t."),
            finish("ok", "Still 777.25 EU/t."),
        ]
    )
    d = designer.design(home, REQ, reviewer=r)
    refused = results(client, 1)[0]
    assert refused["is_error"] and "4242.5" in refused["content"]
    assert results(client, 2)[0]["is_error"]  # the summary's first try
    assert d.review is not None and d.reviewed is None and d.review["changes"] == []
    assert d.review["summary_source"] == "template"
    assert "777" not in d.review["summary"]


def test_a_number_the_planner_gave_is_allowed(home: Path) -> None:
    plain = designer.design(home, REQ)
    machines = plain.plans[0]["metrics"]["machines"]
    r, _ = reviewer([finish("ok", f"Route 1 has {machines} machines.")])
    d = designer.design(home, REQ, reviewer=r)
    assert d.review is not None and d.review["summary_source"] == "model"


def test_issue_items_are_keys_not_text(home: Path) -> None:
    bad = {"kind": "loop", "text": "a loop", "items": ["make 4242 ingots"]}
    good = {"kind": "loop", "text": "a loop", "items": [DUST_C, ELECTRO]}
    r, client = reviewer(
        [
            Turn(tools=[use("finish", verdict="ok", issues=[bad], summary="ok")]),
            Turn(tools=[use("finish", verdict="ok", issues=[good], summary="ok")]),
        ]
    )
    d = designer.design(home, REQ, reviewer=r)
    refused = results(client, 1)[0]
    assert refused["is_error"] and "keys" in refused["content"]
    assert d.review is not None and d.review["issues"][0]["items"] == [DUST_C, ELECTRO]


# --- limits -------------------------------------------------------------------------------------


def test_a_reviewer_that_never_finishes_is_stopped(home: Path) -> None:
    turns = [Turn(tools=[use("alternatives", node=WIDGET)]) for _ in range(20)]
    r, client = reviewer(turns)
    d = designer.design(home, REQ, reviewer=r)
    assert len(client.requests) == rr.MAX_CALLS + 1
    # the configured models refuse a forced tool_choice: the last call is asked in text
    assert all("tool_choice" not in req for req in client.requests)
    told = client.requests[rr.MAX_CALLS - 1]["messages"][-1]["content"][-1]
    assert told == {"type": "text", "text": rr.LAST_CALL}
    # on the last call only finish runs: its alternatives call is refused
    res = results(client, rr.MAX_CALLS)[0]
    assert res["is_error"] and res["content"] == rr.LAST_CALL
    assert d.review is not None and d.review["status"] == "capped"
    assert d.review["stopped"] == "calls" and d.reviewed is None
    rows = json.loads(results(client, 1)[0]["content"])["alternatives"]
    assert {x["recipe"] for x in rows} >= {CR_A, ELECTRO}


def test_a_fifth_resolve_is_refused(home: Path) -> None:
    keys_ = [CR_A, ELECTRO, CR_B, MIXER, "r:gt.recipe.mixer/1088236b6a28"]
    turn = Turn(
        tools=[
            use("propose_constraints", exclude=[k], issue="loop", reason="loop")
            for k in keys_
        ]
    )
    r, client = reviewer([turn, finish("unresolved", "Ran out.")])
    d = designer.design(home, REQ, reviewer=r)
    last = results(client, 1)[-1]
    assert last["is_error"] and f"at most {rr.MAX_RESOLVES}" in last["content"]
    assert d.review is not None and len(d.review["changes"]) <= rr.MAX_RESOLVES


def test_the_spend_cap_stops_the_review(home: Path, store: Store) -> None:
    r, client = reviewer(
        [exclude(CR_A, ELECTRO, CR_B), finish("fixed", "x")], store, cap_usd=1e-6
    )
    d = designer.design(home, REQ, reviewer=r)
    assert len(client.requests) == 1  # nothing after the cap, nothing it asked applied
    assert d.review is not None and d.review["status"] == "capped"
    assert d.review["stopped"] == "usd" and d.review["changes"] == []
    assert d.reviewed is None


def test_unknown_tools_are_refused(home: Path) -> None:
    r, client = reviewer([Turn(tools=[use("db_sql", sql="DROP TABLE plan")]), finish()])
    designer.design(home, REQ, reviewer=r)
    res = results(client, 1)[0]
    assert res["is_error"] and "no such tool" in res["content"]
    for req in client.requests:
        assert {t["name"] for t in req["tools"]} == set(rr.TOOL_NAMES)


# --- the advisor --------------------------------------------------------------------------------


def test_escalation_asks_the_advisor_once(home: Path, store: Store) -> None:
    advice = {
        "assessment": "Exclude the electrolyzer.",
        "recommendation": {
            "kind": "exclude",
            "exclude": [ELECTRO, "about 9999 of them"],
            "note": "build 31337",
        },
        "unresolved": False,
    }
    r, client = reviewer(
        [
            Turn(tools=[use("escalate", issue="a loop", question="what now?")]),
            Turn(tools=[use("advise", **advice)]),
            Turn(tools=[use("escalate", issue="again", question="and now?")]),
            finish("unresolved", "Asked the advisor."),
        ],
        store,
    )
    d = designer.design(home, REQ, reviewer=r)
    adv = client.requests[1]
    assert adv["model"] == OPUS
    assert "tool_choice" not in adv  # a forced tool_choice is a 400 on these models
    assert [t["name"] for t in adv["tools"]] == ["advise"]
    assert "Exclude the electrolyzer" in results(client, 2)[0]["content"]
    second = results(client, 3)[0]
    assert second["is_error"] and "at most 1 escalation" in second["content"]
    assert d.review is not None and d.review["advisor"]["model"] == OPUS
    # keys only: the free text in the recommendation is dropped
    assert d.review["advisor"]["recommendation"] == {
        "kind": "exclude",
        "exclude": [ELECTRO],
    }
    assert d.review["models"] == {"worker": SONNET, "advisor": OPUS}
    models = [
        x["model"] for x in store.rows("SELECT model FROM llm_usage ORDER BY rowid")
    ]
    assert models == [SONNET, OPUS, SONNET, SONNET]


# --- no AI --------------------------------------------------------------------------------------


def test_no_ai_skips_and_saves_the_routes(home: Path, store: Store) -> None:
    mode = AiMode(store)
    mode.set_choice(False)
    r, client = reviewer([finish()], store, ai=mode)
    d = designer.design(home, REQ, reviewer=r)
    assert client.requests == [] and d.ids
    assert d.review is not None and d.review["status"] == "skipped"
    assert rr.describe(d.review)[0].startswith("review: skipped (")
    no_key = rr.Reviewer(None, SONNET, OPUS, off="no Anthropic key stored")
    d2 = designer.design(home, REQ, reviewer=no_key)
    assert d2.review is not None and d2.review["reason"] == "no Anthropic key stored"


class _Down(ScriptedClient):
    def respond(self, request: dict[str, Any], *, streamed: bool) -> Any:  # noqa: ANN401
        self.requests.append(request)
        raise anthropic.APIConnectionError(
            request=httpx2.Request("POST", "https://api.invalid/v1/messages")
        )


def test_an_api_error_ends_the_review_and_keeps_the_routes(
    home: Path, store: Store
) -> None:
    mode = AiMode(store)
    r = rr.Reviewer(_Down(), SONNET, OPUS, None, mode)
    d = designer.design(home, REQ, reviewer=r)
    assert d.review is not None and d.review["status"] == "error"
    assert mode.problem is not None and d.ids


def test_a_cancel_during_the_review_saves_nothing(home: Path) -> None:
    def report(stage: str, k: int | None, n: int | None) -> None:
        if stage == "review":
            raise progress.Cancelled()

    r, _ = reviewer([finish()])
    with progress.reporting(report), pytest.raises(progress.Cancelled):
        designer.design(home, REQ, reviewer=r)
    assert not list((home / "data" / "plans").glob("pl-*.json"))
    assert saved_reviews(home) == []


# --- the schema, the CLI ------------------------------------------------------------------------


def test_the_plan_schema_takes_a_review() -> None:
    schema = json.loads(plan.SCHEMA.read_text(encoding="utf-8"))
    assert "reviewed" in schema["properties"]["rank"]["properties"]["column"]["enum"]
    assert "review" not in schema["required"]
    sub = {"$defs": schema["$defs"], **schema["properties"]["review"]}
    jsonschema.Draft202012Validator(sub).validate(
        {
            "v": 1,
            "id": "rv-0123456789",
            "of": ["pl-0123456789"],
            "verdict": "ok",
            "status": "done",
            "edited": False,
            "changes": [],
            "applied": {"exclude": [], "pins": []},
            "summary": "x",
            "summary_source": "model",
            "models": {"worker": SONNET, "advisor": OPUS},
        }
    )


def test_from_config_uses_the_repo_models_and_cap() -> None:
    from gregtec import config

    cfg = config.Config(model_orchestrator=SONNET, model_planner=OPUS, task_max_usd=0.5)
    r = rr.from_config(cfg, None, None, key=lambda: "sk-test-not-a-key")
    assert (r.worker, r.advisor, r.cap_usd, r.off) == (SONNET, OPUS, 0.5, "")


def test_cli_review_without_a_key_is_skipped(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_key(_: str) -> str:
        raise keys.MissingKeyError("none")

    monkeypatch.setattr(keys, "get_key", no_key)
    monkeypatch.setenv("GOS_HOME", str(home))
    parser = argparse.ArgumentParser()
    plancli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    args = parser.parse_args(
        ["plan", WIDGET, "--tier", "MV", "--qty", "2/s", "--review"]
    )
    assert plancli.run(args, out) == 0
    assert "review: skipped (no Anthropic key stored)" in out.getvalue()


def test_cli_review_prints_the_edit(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = ScriptedClient(
        turns=[exclude(ELECTRO), pin(DUST_C, ARC), finish("fixed", "Swapped it.")]
    )
    real = rr.from_config

    def fake(*a: Any, **kw: Any) -> rr.Reviewer:  # noqa: ANN401
        return replace(real(*a, key=lambda: "sk-test-not-a-key"), client=client)

    monkeypatch.setattr(rr, "from_config", fake)
    monkeypatch.setenv("GOS_HOME", str(home))
    parser = argparse.ArgumentParser()
    plancli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    args = parser.parse_args(
        ["plan", WIDGET, "--tier", "MV", "--qty", "2/s", "--review", "--save"]
    )
    assert plancli.run(args, out) == 0
    text = out.getvalue()
    assert "[reviewed]" in text and "EDITED by the reviewer: pinned test:item@3" in text
    assert "review: fixed (done), 3 calls" in text
    assert len(saved_reviews(home)) == 1


def test_a_design_job_reviews_only_when_its_spec_asks(home: Path) -> None:
    from gregtec.api import job_routes

    made: list[ScriptedClient] = []

    def factory() -> rr.Reviewer:
        r, client = reviewer([finish()])
        made.append(client)
        return r

    spec = {"target": WIDGET, "rate": "2/s", "tier": "MV"}
    title, work = job_routes.designer_work(home, {"spec": spec}, factory)  # type: ignore[misc]
    assert "reviewed" not in title
    assert "review" not in work() and made == []
    title, work = job_routes.designer_work(  # type: ignore[misc]
        home, {"spec": {**spec, "review": True}}, factory
    )
    assert title.endswith(" · reviewed")
    out = work()
    assert out["review"]["status"] == "done" and len(made[0].requests) == 1
