"""WO-2.3: the fast-lane question set, its scoring and ``POST /api/ask``, with fake clients."""

from __future__ import annotations

import io
import itertools
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gregtec.agents import evals, router
from gregtec.agents.ask import Asker, Reply
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.fake import EchoClient
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

ADMIN = "admin-" + "a" * 40
LANE_OF = {"answer": "fast", "instant": "instant", "handoff": "deep"}


def test_the_question_set() -> None:
    qs = evals.load()
    core = [q for q in qs if q.set == "core"]
    ambiguous = [q for q in qs if q.set == "ambiguous"]
    assert len(core) == 50 and len(ambiguous) == 10  # plan §8, §9
    assert all(q.expect == "ask" for q in ambiguous)
    assert (
        sum(q.expect == "answer" for q in core) >= 35
    )  # mostly model answers: the P50's
    assert {q.kind for q in core} >= {"recipe", "item", "uses", "quest"}
    for q in qs:  # the router alone, no DB: each row goes where it expects
        got = router.route(q.question)
        if q.expect == "ask":
            assert got.kind != "deep" and got.subject, q.id
        else:
            assert got.kind == LANE_OF[q.expect], (q.id, got)


def test_load_refuses_a_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "q.tsv"
    bad.write_text("id\tquestion\nq1\thi\n", encoding="utf-8")
    with pytest.raises(evals.EvalError, match="header"):
        evals.load(bad)
    bad.write_text(
        "\t".join(evals.COLUMNS) + "\nq1\tcore\trecipe\tmaybe\t\thi\n", encoding="utf-8"
    )
    with pytest.raises(evals.EvalError, match="bad expect"):
        evals.load(bad)


def q(expect: str, key: str = "") -> evals.Question:
    return evals.Question("x", "core", "recipe", expect, key, "how do I make steel")


@pytest.mark.parametrize(
    ("question", "reply", "passed"),
    [
        (q("ask"), Reply("Which?", "instant", "recipe", asked=True), True),
        (q("ask"), Reply("Steel.", "fast", "recipe", calls=1), False),
        (q("answer", "k@1"), Reply("x", "fast", "recipe", target={"key": "k@1"}), True),
        (
            q("answer", "k@1"),
            Reply("x", "fast", "recipe", target={"key": "k@2"}),
            False,
        ),
        (q("answer"), Reply("x", "fast", "recipe", checked=False), False),
        (q("answer"), Reply("x", "no_ai", "recipe", error="down"), False),
        (q("answer"), Reply("x", "fast", "recipe", asked=True), False),
        (q("instant"), Reply("x", "instant", "recipes"), True),
        (q("handoff"), Reply("x", "deep", "handoff", handoff={"id": "h"}), True),
        (q("handoff"), Reply("x", "fast", "general"), False),
    ],
)
def test_score(question: evals.Question, reply: Reply, passed: bool) -> None:
    assert evals.score(question, reply)[0] is passed


@pytest.fixture
def lane_reg(tool_db: Path, glossary: Glossary) -> Registry:
    return build_registry(tool_db, glossary, deep=HandoffRecorder())


QUESTIONS = [
    evals.Question("q1", "core", "recipe", "answer", "gregtech:gt.metaitem.01@11305",
                   "How do I make steel?"),
    evals.Question("q2", "core", "instant", "instant", "", "what's my tier"),
    evals.Question("q3", "core", "deep", "handoff", "", "Design a steel line"),
    evals.Question("a1", "ambiguous", "ambiguous", "ask", "", "How do I make dust?"),
    evals.Question("a2", "ambiguous", "ambiguous", "ask", "", "recipe for planks"),
]  # fmt: skip


def run(reg: Registry, fake: bool, step_s: float = 0.0) -> tuple[int, str]:
    ticks = itertools.count()

    def stepped() -> float:
        return next(ticks) * step_s

    clock = stepped if step_s else time.perf_counter
    deep = HandoffRecorder()
    lane = FastLane(EchoClient(), reg, clock=clock)
    out = io.StringIO()
    code = evals.run(
        lambda: Asker(reg, deep, lane, clock=clock), QUESTIONS, out, fake=fake
    )
    return code, out.getvalue()


def test_run_reports_and_passes(lane_reg: Registry) -> None:
    code, report = run(lane_reg, fake=True)
    assert code == 0, report
    assert "passed: 5 of 5" in report
    assert "ambiguous rows that asked: 2 of 2" in report
    assert "p50 ttft (core rows answered by the model): " in report
    assert report.rstrip().endswith("Result: PASS")


def test_a_slow_model_fails_the_real_run(lane_reg: Registry) -> None:
    # each clock read is 1 s later: send, first token... the turn's TTFT is over 1.2 s
    code, report = run(lane_reg, fake=False, step_s=1.0)
    assert code == 1 and "Result: FAIL" in report


def test_main_without_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise keys.MissingKeyError(name)

    monkeypatch.setattr(keys, "get_key", missing)
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{tmp_path.as_posix()}"\n', encoding="utf-8")
    out = io.StringIO()
    assert evals.main(cfg, None, False, 2, out) == 1
    assert "no Anthropic key is stored" in out.getvalue()


# --- POST /api/ask --------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path, lane_reg: Registry) -> Iterator[tuple[TestClient, Core]]:
    store = Store(tmp_path / "data" / "gos.sqlite")
    deep = HandoffRecorder()
    core = Core(
        cfg=Config(gos_home=tmp_path),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=Asker(lane_reg, deep, FastLane(EchoClient(), lane_reg)),
    )
    with TestClient(create_app(core), base_url="http://127.0.0.1:8765") as client:
        yield client, core
    store.close()


def test_ask_needs_a_credential(api: tuple[TestClient, Core]) -> None:
    client, _ = api
    assert client.post("/api/ask", json={"text": "steel"}).status_code == 401


def test_ask_answers(api: tuple[TestClient, Core]) -> None:
    client, _ = api
    auth = {"Authorization": f"Bearer {ADMIN}"}
    got = client.post("/api/ask", json={"text": "How do I make steel?"}, headers=auth)
    assert got.status_code == 200, got.text
    body = got.json()
    assert (
        body["lane"] == "fast"
        and body["checked"]
        and body["card"]["tool"] == "recipe_find"
    )
    assert body["target"]["name"] == "Steel Ingot" and body["ttft_ms"] is not None
    asked = client.post(
        "/api/ask", json={"text": "how do I make dust"}, headers=auth
    ).json()
    assert asked["asked"] and asked["card"]["type"] == "choices"
    for bad in ({"text": ""}, {"text": "x" * 1001}, {"text": 3}, {}):
        assert client.post("/api/ask", json=bad, headers=auth).status_code == 400, bad


def test_ask_without_agents(api: tuple[TestClient, Core]) -> None:
    client, core = api
    core.asker = None
    auth = {"Authorization": f"Bearer {ADMIN}"}
    assert client.post("/api/ask", json={"text": "hi"}, headers=auth).status_code == 503
