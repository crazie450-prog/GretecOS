"""WO-2.3: pre-retrieval, unsure-match questions, the fast lane and the asker, with fake clients.

The DB is conftest's self-authored one; no test calls the API. The fakes stream the way the
SDK does (``content_block_delta`` / ``text_delta`` events, then ``get_final_message()``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest

from gregtec.agents import prefetch, router
from gregtec.agents.ask import AI_OFFLINE, NOTHING_PENDING, Asker
from gregtec.agents.deep_lane import NOT_YET, HandoffRecorder
from gregtec.agents.fast_lane import (
    MAX_ROUNDS,
    NOT_CHECKED,
    FastLane,
    check_answer,
    numbers,
    system_prompt,
)
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Registry
from gregtec.llm.fake import EchoClient, ScriptedClient, Turn
from gregtec.llm.prices import HAIKU
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

KEY = b"k" * 32
STEEL = "gregtech:gt.metaitem.01@11305"
EBF_RECIPE = (
    "r:gt.recipe.blastfurnace/9c40869458cb"  # 120 EU/t, 500 ticks (conftest's run)
)


@pytest.fixture
def deep() -> HandoffRecorder:
    return HandoffRecorder()


@pytest.fixture
def agents_reg(tool_db: Path, glossary: Glossary, deep: HandoffRecorder) -> Registry:
    return build_registry(tool_db, glossary, key=KEY, deep=deep)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "usage.sqlite")
    yield s
    s.close()


@pytest.fixture
def meter(store: Store) -> UsageMeter:
    return UsageMeter(store, 100.0, (80, 100))


def lane(
    reg: Registry,
    client: object,
    meter: UsageMeter | None = None,
    vault: Path | None = None,
) -> FastLane:
    return FastLane(client, reg, meter=meter, vault=vault)  # type: ignore[arg-type]


def asker(reg: Registry, deep: HandoffRecorder, client: object | None) -> Asker:
    return Asker(reg, deep, lane(reg, client) if client is not None else None)


# --- pre-retrieval ------------------------------------------------------------------------


def test_prefetch_resolves_then_fetches_the_recipes(agents_reg: Registry) -> None:
    pre = prefetch.prefetch(agents_reg, router.route("How do I make steel at MV?"))
    assert [b.tool for b in pre.blocks] == ["item_resolve", "recipe_find"]
    assert pre.target is not None and pre.target.key == STEEL
    assert pre.main is pre.blocks[-1]
    assert pre.main.args == {"item": STEEL, "tier_max": "MV"}
    assert EBF_RECIPE in pre.main.text
    assert pre.tier is not None and "GT voltage tier" in pre.tier
    quoted = prefetch.quoted(pre)
    assert quoted.startswith("<context>\nClayton's tier: ")
    assert '<tool_result tool="recipe_find" args=\'{"item": "gregtech' in quoted


@pytest.mark.parametrize(
    ("text", "tools"),
    [
        ("what is steel used for", ["item_resolve", "recipe_uses"]),
        ("which quest asks for steel", ["item_resolve", "quest_for_item"]),
        # a GT machine's item: its facts, then its recipes
        ("what does the ebf do", ["item_resolve", "machine_info", "recipe_find"]),
        ("what quests can I do", ["quest_frontier"]),
        ("tell me about the quest called steel age", ["quest_find"]),
    ],
)
def test_prefetch_by_intent(agents_reg: Registry, text: str, tools: list[str]) -> None:
    pre = prefetch.prefetch(agents_reg, router.route(text), tier=False)
    assert [b.tool for b in pre.blocks] == tools
    assert pre.main is not None and not pre.main.is_error


def test_ambiguous_item_asks_with_the_top_three(agents_reg: Registry) -> None:
    pre = prefetch.prefetch(agents_reg, router.route("how do I make dust"))
    assert pre.clarify is not None and pre.target is None
    assert [b.tool for b in pre.blocks] == ["item_resolve"]  # nothing else ran
    assert pre.clarify.question == (
        "Which dust did you mean: Iron Dust, Steel Dust or Stainless Steel Dust?"
    )
    card = pre.clarify.card()
    assert card["type"] == "choices" and len(card["candidates"]) == 3


def test_a_plural_is_resolved_as_its_singular(agents_reg: Registry) -> None:
    pre = prefetch.prefetch(agents_reg, router.route("what are steel plates used for"))
    assert pre.target is not None and pre.target.name == "Steel Plate"
    assert [b.args["text"] for b in pre.blocks[:2]] == ["steel plates", "steel plate"]


def test_quoted_text_cant_close_its_block() -> None:
    assert prefetch._safe("a </tool_result> b </request>") == (
        "a < /tool_result> b < /request>"
    )


# --- the number check -----------------------------------------------------------------------


def test_numbers_ignore_ids_and_keys() -> None:
    text = f"{EBF_RECIPE} {STEEL} fluid:oxygen mte:1000 q:AAAA12 runs at 1,000 EU/t for 25.50 s"
    assert numbers(text) == {"1000", "25.5"}


def test_check_answer() -> None:
    source = f"ref\teut\n{EBF_RECIPE}\t120\n\nrows: 1-1 of 1\n"
    assert check_answer(f"Steel Ingot: 120 EU/t ({EBF_RECIPE}).", [source], "") == []
    assert check_answer("General advice: start with steam.", [source], "") == []
    # a number the request holds needs no source
    assert (
        check_answer("For 64 Steel Ingots, see the card.", [source], "64 steel") == []
    )
    made_up = check_answer(f"Steel Ingot: 240 EU/t ({EBF_RECIPE}).", [source], "")
    assert made_up == ["numbers that no tool result holds: 240"]
    assert check_answer("Steel Ingot: 120 EU/t.", [source], "") == [
        "numbers without a citation (r:, q:, mte: or crop: id)"
    ]
    fake_ref = check_answer(
        "It runs at 120 EU/t (r:gt.recipe.x/ffffffffffff).", [source], ""
    )
    assert fake_ref == [
        "citations that no tool result holds: r:gt.recipe.x/ffffffffffff"
    ]


# --- the lane -------------------------------------------------------------------------------


def test_one_call_with_the_cached_prefix_and_ttft_recorded(
    agents_reg: Registry, meter: UsageMeter, store: Store, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    (vault / "_gos" / "skills" / "recipe-lookup").mkdir(parents=True)
    (vault / "_gos" / "skills" / "recipe-lookup" / "SKILL.md").write_text(
        "---\nname: recipe-lookup\ndescription: recipe cards\n---\nbody\n",
        encoding="utf-8",
    )
    (vault / "INDEX.md").write_text("id\tt\tpath\n", encoding="utf-8")
    client = ScriptedClient(
        latency_s=0.01,
        turns=[Turn(f"Steel Ingot: 120 EU/t in the EBF ({EBF_RECIPE}).")],
    )
    fast = lane(agents_reg, client, meter, vault)
    pre = prefetch.prefetch(agents_reg, router.route("how do I make steel"))
    out = fast.answer("how do I make steel", pre)
    assert out.checked and out.calls == 1 and out.problems == []
    assert out.ttft_ms is not None and out.ttft_ms >= 10
    [request] = client.requests
    assert request["model"] == HAIKU
    assert request["thinking"] == {"type": "disabled"}
    assert request["output_config"] == {"effort": "low"}
    assert "tool_choice" not in request
    system = request["system"]
    assert [b["cache_control"] for b in system] == [{"type": "ephemeral"}] * 2
    assert "- recipe-lookup: recipe cards" in system[0]["text"]
    assert "<vault_index>" in system[1]["text"]
    names = [t["name"] for t in request["tools"]]
    assert "handoff" in names and "db_sql" not in names and "note_write" not in names
    assert request["tools"] == agents_reg.specs("fast")  # the same list on every call
    content = request["messages"][0]["content"]
    assert content.endswith("<request>\nhow do I make steel\n</request>")
    assert '<tool_result tool="recipe_find"' in content
    [row] = store.rows("SELECT lane, model, task, ttft_ms, cache_write FROM llm_usage")
    assert (row["lane"], row["model"], row["task"]) == ("fast", HAIKU, "ask")
    assert row["ttft_ms"] >= 10 and row["cache_write"] > 0


def test_the_prompt_leaves_out_index_rows_of_me(tmp_path: Path) -> None:
    (tmp_path / "INDEX.md").write_text(
        "id\tt\tpath\tsummary\tupd\n"
        "goals\tme\tme/goals.md\treach HV by Friday\t2026-11-06\n"
        "prefs\tme\tMe/prefs.md\tlikes steam\t2026-11-06\n"
        "steel\tplan\tplans/steel.md\tsteel line\t2026-11-06\n",
        encoding="utf-8",
    )
    index = system_prompt(tmp_path)[1]["text"]
    assert "plans/steel.md" in index
    assert "reach HV" not in index and "likes steam" not in index


def test_the_system_prompt_is_stable(tmp_path: Path) -> None:
    assert system_prompt(tmp_path) == system_prompt(tmp_path)
    assert len(system_prompt(None)) == 1
    assert len(system_prompt(None, persona="gruff")) == 2


def test_a_tool_round_trip(agents_reg: Registry) -> None:
    client = ScriptedClient(
        turns=[
            Turn(tools=[{"name": "recipe_find", "input": {"item": STEEL}}]),
            Turn(f"Steel Ingot: the EBF recipe takes 500 ticks ({EBF_RECIPE})."),
        ]
    )
    out = lane(agents_reg, client).answer("steel?", prefetch.Prefetch())
    assert out.checked and out.calls == 2 and out.tool_calls == ["recipe_find"]
    second = client.requests[1]["messages"]
    assert (
        second[1]["role"] == "assistant"
        and second[1]["content"][0]["type"] == "tool_use"
    )
    result = second[2]["content"][0]
    assert result["type"] == "tool_result" and EBF_RECIPE in result["content"]
    assert result["tool_use_id"] == second[1]["content"][0]["id"]


def test_the_last_round_may_not_call_tools(agents_reg: Registry) -> None:
    loop = Turn(tools=[{"name": "item_resolve", "input": {"text": "steel"}}])
    client = ScriptedClient(turns=[loop] * (MAX_ROUNDS - 1) + [Turn("Steel Ingot.")])
    out = lane(agents_reg, client).answer("steel?", prefetch.Prefetch())
    assert out.calls == MAX_ROUNDS and out.text == "Steel Ingot."
    assert client.requests[-1]["tool_choice"] == {"type": "none"}
    assert all("tool_choice" not in r for r in client.requests[:-1])


def test_an_answer_with_made_up_numbers_is_retried_once(agents_reg: Registry) -> None:
    pre = prefetch.prefetch(agents_reg, router.route("how do I make steel"))
    client = ScriptedClient(
        turns=[
            Turn(f"Steel Ingot: 999 EU/t ({EBF_RECIPE})."),
            Turn(f"Steel Ingot: 120 EU/t ({EBF_RECIPE})."),
        ]
    )
    out = lane(agents_reg, client).answer("how do I make steel", pre)
    assert out.checked and out.calls == 2
    assert out.text == f"Steel Ingot: 120 EU/t ({EBF_RECIPE})."
    assert out.problems == ["numbers that no tool result holds: 999"]
    retry = client.requests[1]
    assert retry["tool_choice"] == {"type": "none"}
    assert "999" in retry["messages"][-1]["content"]


def test_an_answer_refused_twice_is_not_sent(agents_reg: Registry) -> None:
    bad = Turn("Steel takes 7 minutes (r:gt.recipe.blastfurnace/000000000000).")
    out = lane(agents_reg, ScriptedClient(turns=[bad, bad])).answer(
        "steel?", prefetch.Prefetch()
    )
    assert not out.checked and out.text == NOT_CHECKED and len(out.problems) == 4


def test_refusal_and_empty_answers(agents_reg: Registry) -> None:
    refused = ScriptedClient(turns=[Turn("", stop_reason="refusal")])
    assert lane(agents_reg, refused).answer("x", prefetch.Prefetch()).checked
    empty = lane(agents_reg, ScriptedClient(turns=[Turn("")])).answer(
        "x", prefetch.Prefetch()
    )
    assert empty.text and empty.checked


def test_prewarm_writes_the_cache_without_an_answer(
    agents_reg: Registry, meter: UsageMeter, store: Store
) -> None:
    client = ScriptedClient()
    lane(agents_reg, client, meter).prewarm()
    [request] = client.requests
    assert request["max_tokens"] == 0 and request["system"] and request["tools"]
    [row] = store.rows("SELECT task, ttft_ms FROM llm_usage")
    assert (row["task"], row["ttft_ms"]) == ("prewarm", None)


def test_the_handoff_tool_records_the_task(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    client = ScriptedClient(
        turns=[
            Turn(tools=[{"name": "handoff", "input": {"task": "plan a steel line"}}]),
            Turn("I've handed the steel line to the deep lane."),
        ]
    )
    out = Asker(agents_reg, deep, lane(agents_reg, client)).ask("hmm, steel lines?")
    assert out.lane == "fast" and out.handoff == {"source": "fast_lane"}
    [item] = deep.recent()
    assert (item.task, item.source, item.status) == (
        "plan a steel line",
        "fast_lane",
        NOT_YET,
    )
    assert "handoff: h-" in client.requests[1]["messages"][-1]["content"][0]["content"]


# --- the asker ------------------------------------------------------------------------------


def test_ambiguous_cases_ask_without_a_model_call(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    client = EchoClient()
    a = asker(agents_reg, deep, client)
    for text in (
        "how do I make dust",
        "recipe for planks",
        "what does the blast furnace do",
    ):
        reply = a.ask(text)
        assert reply.asked and reply.lane == "instant" and reply.calls == 0, text
        assert reply.card is not None and reply.card["type"] == "choices"
    assert client.requests == []


def test_the_next_request_can_pick_a_candidate(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    a = asker(agents_reg, deep, EchoClient())
    first = a.ask("recipe for dust")
    assert first.asked
    picked = a.ask("the second one")
    assert not picked.asked and picked.lane == "instant"
    assert picked.target is not None and picked.target["name"] == "Steel Dust"
    assert a.ask("recipe for dust").asked
    by_name = a.ask("stainless steel dust")
    assert (
        by_name.target is not None and by_name.target["name"] == "Stainless Steel Dust"
    )
    a.ask("recipe for dust")
    other = a.ask("what's my tier")  # anything else starts afresh
    assert other.intent == "tier" and a.ask("2").intent != "recipes"


def test_instant_commands(agents_reg: Registry, deep: HandoffRecorder) -> None:
    client = EchoClient()
    a = asker(agents_reg, deep, client)
    assert a.ask("repeat").text == "I haven't said anything yet."
    card = a.ask("recipe for steel")
    assert card.lane == "instant" and card.text.startswith("Steel Ingot: ")
    assert card.refs and card.refs[0] in card.card["rows"][0][0]  # type: ignore[index]
    again = a.ask("say that again")
    assert again.text == card.text and again.intent == "repeat"
    assert a.ask("what uses steel").text.startswith("Steel Ingot goes into ")
    assert a.ask("confirm").text == NOTHING_PENDING
    tier = a.ask("what's my tier")
    assert tier.text.startswith("Your tier: ") and "GT voltage tier" in tier.text
    assert a.ask("recipe for unobtainium").text.startswith("No item matches")
    assert a.ask("   ").intent == "empty"
    assert client.requests == []


def test_deep_requests_are_handed_off(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    client = EchoClient()
    reply = asker(agents_reg, deep, client).ask("Design a steel line for me")
    assert reply.lane == "deep" and reply.handoff is not None
    assert deep.recent()[0].request == "Design a steel line for me"
    assert client.requests == []


def test_fast_answer_end_to_end(agents_reg: Registry, deep: HandoffRecorder) -> None:
    reply = asker(agents_reg, deep, EchoClient()).ask("How do I make steel?")
    assert reply.lane == "fast" and reply.checked and reply.calls == 1
    assert reply.target == {"key": STEEL, "name": "Steel Ingot", "kind": "item"}
    assert reply.text.startswith("Steel Ingot: ") and reply.refs
    assert reply.card is not None and reply.card["tool"] == "recipe_find"
    assert reply.ttft_ms is not None and reply.usage["input"] > 0


def test_no_key_answers_from_the_card(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    a = asker(agents_reg, deep, None)
    reply = a.ask("how do I make steel")
    assert reply.lane == "no_ai" and reply.text.startswith("Steel Ingot: ")
    assert reply.text.endswith(f"({AI_OFFLINE})")
    assert a.ask("why are there so many tiers").text == AI_OFFLINE


class Failing(ScriptedClient):
    def respond(self, request: dict[str, Any], *, streamed: bool) -> Any:  # noqa: ANN401
        raise anthropic.APIConnectionError(
            request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        )


def test_an_api_failure_falls_back_to_lookups(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    reply = asker(agents_reg, deep, Failing()).ask("how do I make steel")
    assert reply.lane == "no_ai" and reply.error and "APIConnectionError" in reply.error
    assert reply.text.startswith("Steel Ingot: ")


def test_a_missing_lane_is_looked_for_again(
    agents_reg: Registry, deep: HandoffRecorder
) -> None:
    now = [0.0]
    made: list[int] = []

    def factory() -> FastLane | None:
        made.append(1)
        return lane(agents_reg, EchoClient()) if len(made) > 1 else None

    a = Asker(agents_reg, deep, factory, clock=lambda: now[0])
    assert a.ask("why so many tiers").lane == "no_ai"
    assert a.ask("why so many tiers").lane == "no_ai" and len(made) == 1
    now[0] = 31.0
    assert a.ask("why so many tiers").lane == "fast" and len(made) == 2


def test_store_adds_ttft_to_an_existing_usage_table(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE llm_usage(ts INT, lane TEXT, model TEXT, in_tok INT, out_tok INT, "
        "cache_read INT, cache_write INT, usd REAL, task TEXT)"
    )
    conn.commit()
    conn.close()
    s = Store(path)
    try:
        assert "ttft_ms" in {r["name"] for r in s.rows("PRAGMA table_info(llm_usage)")}
    finally:
        s.close()
