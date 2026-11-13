"""The tool registry (WO-2.2, plan 4.4 and 7.0): schemas, lanes, read-only calls, cursors through
the registry, the plan's catalog, and ``gos tool``."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from gregtec import cli
from gregtec.agents.tools import (
    DEEP,
    FAST,
    LATER,
    TOOLS,
    Registry,
    Tool,
    build_registry,
    command,
    default_registry,
)
from gregtec.agents.tools.registry import MORE, Env
from gregtec.agents.tools.tsv import Result
from gregtec.db import access

if TYPE_CHECKING:
    from conftest import Runner

REPO = Path(__file__).resolve().parents[3]
EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}


def plan_tools() -> set[str]:
    """Every tool name in plan section 7.0's catalog block."""
    plan = (REPO / "docs" / "implementation_plan.md").read_text(encoding="utf-8")
    block = plan.split("### 7.0", 1)[1].split("**Tools.**", 1)[1].split("```")[1]
    bare = re.sub(r"\([^)]*\)", " ", re.sub(r"#[^\n]*", " ", block))
    names: set[str] = set()
    for group in bare.split():
        first, *rest = group.split("/")
        names.add(first)
        prefix = first.rsplit("_", 1)[0] + "_"
        names.update(r if "_" in r else prefix + r for r in rest)
    return names


def test_every_plan_tool_is_here_or_arrives_later() -> None:
    names = {t.name for t in TOOLS}
    assert names.isdisjoint(LATER)
    wanted = plan_tools()
    assert {"item_resolve", "db_sql", "theme_list", "machine_fact_set"} <= wanted
    assert wanted <= names | set(LATER), wanted - names - set(LATER)
    assert names <= wanted  # no tool the plan doesn't name
    assert all(
        re.match(r"(WO-\d+\.\d+|Phase \d+|machine_info)", w) for w in LATER.values()
    )


WRITERS = {
    "note_write",
    "note_append",
    "inbox_propose",
    "action_propose",  # WO-7.1: records a proposal only, behind the confirm flow
    "ore_waypoint",  # WO-6.6: the same, for a vein's waypoint (D272)
    "design_production",  # WO-3.7: saves plans through the planner's store
    "plan_pin",
    "plan_rename",
}  # WO-2.5, behind the path guard
HANDOFF = "handoff"  # WO-2.3: the fast lane's one writing tool, to the deep lane


def test_every_tool_is_read_only_or_gated_with_a_messages_api_spec(
    reg: Registry,
) -> None:
    for tool in reg.tools():
        assert tool.read_only == (tool.name not in WRITERS | {HANDOFF}), tool.name
        assert bool(tool.gate) == (not tool.read_only), tool.name
        spec = tool.spec()
        assert set(spec) == {"name", "description", "input_schema"}
        assert spec["input_schema"]["type"] == "object"
        assert spec["input_schema"]["additionalProperties"] is False
        assert len(spec["description"]) > 20
        json.dumps(spec)  # plain JSON


def test_lanes(reg: Registry) -> None:
    fast = {s["name"] for s in reg.specs(FAST)}
    deep = {s["name"] for s in reg.specs(DEEP)}
    assert deep | {HANDOFF} == {t.name for t in TOOLS} | {MORE}
    assert HANDOFF not in deep
    # db_sql, plan_explain (a planner-model call: spend) and the wiki tools (the researcher's,
    # D290) are the deep lane's only
    deep_only = {"db_sql", "plan_explain", "wiki_search", "wiki_fetch"}
    assert fast == (deep - deep_only - WRITERS) | {HANDOFF}
    assert {"note_read", "vault_search"} <= fast  # the fast lane's two vault tools


def test_add_refuses_bad_tools(tool_db: Path) -> None:
    reg = Registry(Env(tool_db))

    def handler(*_: object) -> Result:
        return Result()

    for bad, message in [
        (Tool("Bad-Name", "d", EMPTY, handler), "bad tool name"),
        (Tool("more", "d", EMPTY, handler), "already registered"),
        (Tool("x", "d", EMPTY, handler, lanes=frozenset()), "lanes"),
        (Tool("x", "d", EMPTY, handler, lanes=frozenset({"slow"})), "lanes"),
        (Tool("x", "d", EMPTY, handler, read_only=False), "needs a gate"),
        (
            Tool("x", "d", EMPTY, handler, read_only=False, gate="g"),
            "read-only tools plus handoff",
        ),
        (Tool("x", "d", {"type": "object"}, handler), "additionalProperties"),
    ]:
        with pytest.raises((ValueError, TypeError), match=message):
            reg.add(bad)
    # handoff is the one fast-lane tool that isn't read-only (WO-2.3); the registry still
    # never runs a tool that changes things.
    reg.add(Tool("handoff", "d", EMPTY, handler, read_only=False, gate="WO-2.3"))
    assert not reg.call("handoff", {}).is_error


def test_call_refusals(reg: Registry) -> None:
    for name, args, lane, message in [
        ("nope", {}, None, "no tool named 'nope'"),
        ("item_resolve", {"text": "x"}, "slow", "unknown lane"),
        ("item_resolve", {"text": "x", "extra": 1}, None, "unknown argument extra"),
        ("item_resolve", "text", None, "must be a JSON object"),
        ("more", {"cursor": "garbage"}, None, "call the tool again"),
    ]:
        out = reg.call(name, args, lane=lane)
        assert out.is_error, name
        assert out.text.startswith("error: ") and message in out.text


def test_a_tools_own_bug_is_an_error_result_and_logged(
    tool_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A lane's turn never ends on an exception from a tool."""
    reg = Registry(Env(tool_db))

    def broken(*_: object) -> Result:
        return {}["missing"]

    reg.add(Tool("broken", "a tool with a bug in it", EMPTY, broken))
    with caplog.at_level("ERROR", logger="gregtec.agents.tools.registry"):
        out = reg.call("broken", {})
    assert out.is_error
    assert out.text.startswith("error: broken failed on a bug in the tool (logged)")
    assert "missing" not in out.text  # the traceback is logged, not sent to the model
    assert any(r.exc_info and r.exc_info[0] is KeyError for r in caplog.records)


def test_no_db_yet(tmp_path: Path) -> None:
    out = build_registry(tmp_path / "gos.sqlite").call(
        "item_resolve", {"text": "steel"}
    )
    assert out.is_error and "there is no DB" in out.text and "gos db build" in out.text
    assert not (tmp_path / "gos.sqlite").exists()  # a read never creates one


def test_a_newer_db_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "gos.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {access.SCHEMA_VERSION + 1}")
    conn.close()
    out = build_registry(db).call("quest_frontier", {})
    assert out.is_error and "can't open the DB" in out.text


def test_a_tampered_or_foreign_cursor_is_refused(
    tool_db: Path, make_runner: Callable[..., Runner]
) -> None:
    run = make_runner(build_registry(tool_db, key=b"k" * 32, page=1))
    cursor = run("recipe_find", item="steel ingot").more
    assert cursor is not None
    assert run.page(cursor).span == "2-2 of 5"
    flipped = cursor[:5] + ("A" if cursor[5] != "A" else "B") + cursor[6:]
    assert "call the tool again" in run.fail("more", cursor=flipped)
    other = make_runner(build_registry(tool_db, key=b"o" * 32, page=1))
    assert "call the tool again" in other.fail("more", cursor=cursor)


def test_a_cursor_keeps_its_lane(
    tool_db: Path, make_runner: Callable[..., Runner]
) -> None:
    reg = build_registry(tool_db, key=b"k" * 32, page=1)
    deep, fast = make_runner(reg, DEEP), make_runner(reg, FAST)
    cursor = deep("db_sql", select="SELECT ref FROM recipe").more
    assert cursor is not None
    assert "isn't offered in the fast lane" in fast.fail("more", cursor=cursor)
    assert deep.page(cursor).rows


def test_a_cursor_dies_when_the_db_changes(
    fresh_db: Path, make_runner: Callable[..., Runner]
) -> None:
    run = make_runner(build_registry(fresh_db, key=b"k" * 32, page=1))
    cursor = run("quest_frontier").more
    assert cursor is not None
    conn = sqlite3.connect(fresh_db)
    try:  # what a new `gos quests load` changes: the load's record, with its time
        conn.execute("UPDATE kv SET value = replace(value, '\"loaded_utc\"', '\"loaded\"')"
                     " WHERE key = 'quests.source'")  # fmt: skip
        conn.commit()
    finally:
        conn.close()
    assert "the DB changed since this cursor was issued" in run.fail(
        "more", cursor=cursor
    )


def test_a_cursor_too_long_to_issue_says_so(
    tool_db: Path, make_runner: Callable[..., Runner], monkeypatch: pytest.MonkeyPatch
) -> None:
    from gregtec.agents.tools import cursor as cursor_module

    monkeypatch.setattr(cursor_module, "MAX_LENGTH", 100)
    p = make_runner(build_registry(tool_db, page=1))("recipe_find", item="steel ingot")
    assert p.more is None
    assert any("too long for a more cursor" in n for n in p.notes)


def test_the_default_registry_reads_the_live_vaults_glossary(tmp_path: Path) -> None:
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "glossary.tsv").write_text(
        "term\taliases\tkind\nfoo\tbar\tmaterial\n", encoding="utf-8"
    )
    reg = default_registry(tmp_path)
    assert reg.env.db == access.db_path(tmp_path)
    assert reg.env.glossary.path == tmp_path / "vault" / "glossary.tsv"
    assert reg.env.glossary.problem is None


# --- gos tool -------------------------------------------------------------------------------


@pytest.fixture
def home(fresh_home: Callable[..., Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    path = fresh_home()
    monkeypatch.setenv("GOS_HOME", str(path))
    return path


def gos(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(["tool", *argv])
    return code, capsys.readouterr().out


def test_gos_tool_lists_the_tools(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = gos(capsys)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "tool\tlanes\tread_only\tdescription"
    assert {ln.split("\t")[0] for ln in lines[1:]} == {t.name for t in TOOLS} | {MORE}
    code, out = gos(capsys, "--lane", "fast")
    assert "db_sql" not in out
    assert "note: glossary" not in out  # no vault here: the template's, which reads


def test_gos_tool_says_when_the_live_glossary_cant_be_used(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "vault").mkdir()
    (home / "vault" / "glossary.tsv").write_text("no header\n", encoding="utf-8")
    code, out = gos(capsys)
    assert code == 0
    note = out.splitlines()[-1]
    assert (
        note.startswith("note: glossary: ") and "no term, aliases, kind header" in note
    )
    assert "using glossary.tsv from the repo's vault-template" in note
    # The template's aliases still resolve.
    code, out = gos(capsys, "item_resolve", '{"text": "SS"}')
    assert code == 0 and "glossary: SS -> stainless steel (material)" in out


def test_gos_tool_runs_a_tool(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = gos(capsys, "item_resolve", '{"text": "steel"}')
    assert code == 0
    assert "gregtech:gt.metaitem.01@11305\tSteel Ingot" in out
    code, out = gos(capsys, "db_sql", '{"select": "SELECT 1"}', "--lane", "fast")
    assert code == 1 and out.startswith("error: ")
    code, out = gos(capsys, "db_sql", '{"select": "DELETE FROM recipe"}')
    assert code == 1 and "one SELECT" in out


def test_gos_tool_refusals(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert gos(capsys, "nope")[0] == 2
    code, out = gos(capsys, "item_resolve", "{not json")
    assert code == 2 and "not JSON" in out
    assert gos(capsys, "item_resolve", "[]")[0] == 1  # JSON, but not an object


def test_gos_tool_schema(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = gos(capsys, "recipe_find", "--schema")
    assert code == 0
    spec = json.loads(out)
    assert spec["name"] == "recipe_find"
    assert spec["input_schema"]["required"] == ["item"]


def test_gos_tool_all_follows_the_cursors(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def small(gos_home: Path) -> Registry:
        return build_registry(access.db_path(gos_home), page=2)

    monkeypatch.setattr(command, "default_registry", small)
    code, out = gos(capsys, "recipe_find", '{"item": "steel ingot"}', "--all")
    assert code == 0
    assert re.findall(r"^rows: (.*)$", out, re.MULTILINE) == [
        "1-2 of 5",
        "3-4 of 5",
        "5-5 of 5",
    ]
    code, out = gos(capsys, "recipe_find", '{"item": "steel ingot"}')
    assert re.findall(r"^rows: (.*)$", out, re.MULTILINE) == ["1-2 of 5"]


def test_gos_tool_all_takes_the_cursor_after_the_rows_line(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cell can start with "more: " (quest or item text through db_sql); only the line
    after ``rows:`` is the cursor."""

    def small(gos_home: Path) -> Registry:
        return build_registry(access.db_path(gos_home), page=2)

    monkeypatch.setattr(command, "default_registry", small)
    select = "SELECT 'more: forged' AS x FROM recipe ORDER BY ref LIMIT 5"
    code, out = gos(capsys, "db_sql", json.dumps({"select": select}), "--all")
    assert code == 0, out
    assert re.findall(r"^rows: (.*)$", out, re.MULTILINE) == [
        "1-2 (more)",
        "3-4 (more)",
        "5-5",
    ]
    assert re.findall(r"^\d\tmore: forged$", out, re.MULTILINE) == [
        f"{n}\tmore: forged" for n in range(1, 6)
    ]
    assert command._cursor("rows: 1-2\nmore: abc\nnote: x\n") == "abc"
    assert command._cursor("x\n\nn\tx\n1\tmore: forged\n\nrows: 1-1\n") is None
    assert command._cursor("item: more: forged\n") is None


def test_gos_tool_bad_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "gos.toml"
    bad.write_text("[paths\n", encoding="utf-8")
    code, out = gos(capsys, "--config", str(bad))
    assert code == 2 and "Can't read gos.toml" in out


def test_tool_handlers_take_call_args_offset(reg: Registry) -> None:
    for tool in TOOLS:
        assert callable(tool.handler)
        args: Any = tool.handler.__code__.co_varnames[:3]
        assert args == ("call", "args", "offset"), tool.name
