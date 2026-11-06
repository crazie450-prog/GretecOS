"""``db_query`` and ``db_sql`` (WO-2.2, plan 4.6 and 5.3): named queries, and the guards on the
one ad-hoc SELECT: a read-only connection (``mode=ro`` + ``PRAGMA query_only``), an authorizer
that allows only SELECT, READ of game tables and a list of safe functions, a time limit and a
row cap. Every forbidden shape must come back as an error and leave the DB byte-for-byte as it
was.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gregtec.agents.tools import TOOLS, build_registry, sql
from gregtec.agents.tools.registry import Env, Registry, guard
from gregtec.db import access

if TYPE_CHECKING:
    from conftest import Runner


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- db_sql: refused shapes ----------------------------------------------------------------

REFUSED = {
    "insert": "INSERT INTO kv VALUES ('a', 'b', 0)",
    "replace": "REPLACE INTO recipe_map(id) VALUES (1)",
    "update": "UPDATE recipe SET eut = 0",
    "delete": "DELETE FROM recipe",
    "drop": "DROP TABLE recipe",
    "create table": "CREATE TABLE x(a)",
    "create temp table": "CREATE TEMP TABLE x(a)",
    "create temp view": "CREATE TEMP VIEW v AS SELECT 1",
    "create index": "CREATE INDEX ix ON recipe(eut)",
    "attach": "ATTACH DATABASE ':memory:' AS x",
    "detach": "DETACH DATABASE main",
    "pragma write": "PRAGMA query_only = 0",
    "pragma writable_schema": "PRAGMA writable_schema = 1",
    "pragma read": "PRAGMA table_info(recipe)",
    "pragma function": "SELECT * FROM pragma_table_info('recipe')",
    "pragma database_list": "SELECT * FROM pragma_database_list",
    "vacuum into": "VACUUM INTO 'copy.db'",
    "begin": "BEGIN",
    "savepoint": "SAVEPOINT s",
    "analyze": "ANALYZE",
    "reindex": "REINDEX",
    "cte delete": "WITH x AS (SELECT 1) DELETE FROM recipe",
    "cte insert": "WITH x AS (SELECT 1) INSERT INTO kv SELECT 'a', 'b', 0 FROM x",
    "fts command": "INSERT INTO name_fts(name_fts) VALUES ('rebuild')",
    "two statements": "SELECT 1; DELETE FROM recipe",
    "load_extension": "SELECT load_extension('evil')",
    "randomblob": "SELECT randomblob(1000000000)",
    "zeroblob": "SELECT zeroblob(1000000000)",
    "unlisted function": "SELECT sqlite_version()",
    "fts5 pointer": "SELECT fts5(NULL)",
    "private table": "SELECT * FROM kv",
    "private table count": "SELECT count(*) FROM kv",
    "private table rowid": "SELECT rowid FROM device",
    "private table schema-qualified": "SELECT * FROM main.kv",
    "private table in a subquery": "SELECT (SELECT value FROM kv LIMIT 1)",
    "temp schema": "SELECT * FROM temp.sqlite_master",
    "recursive cte": (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c"
    ),
    "nul": "SELECT 1\x00; DELETE FROM recipe",
}


@pytest.mark.parametrize("shape", REFUSED)
def test_db_sql_refuses(run: Runner, tool_db: Path, shape: str) -> None:
    before = digest(tool_db)
    error = run.fail("db_sql", select=REFUSED[shape])
    assert any(w in error for w in ("refused", "one SELECT", "SQL error", "NUL"))
    assert digest(tool_db) == before


@pytest.mark.parametrize(
    "shape",
    ["cte delete", "cte insert", "private table", "load_extension", "recursive cte"],
)
def test_the_authorizer_alone_refuses_what_passes_the_first_word(
    run: Runner, shape: str
) -> None:
    assert sql.check_select(REFUSED[shape]) == REFUSED[shape]
    assert "refused: " in run.fail("db_sql", select=REFUSED[shape])


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO kv VALUES ('a', 'b', 0)",
        "DELETE FROM recipe",
        "CREATE TABLE x(a)",
        "ATTACH DATABASE ':memory:' AS x",
    ],
)
def test_the_connection_alone_refuses_writes(tool_db: Path, statement: str) -> None:
    """Without the authorizer, mode=ro, query_only and the ATTACH limit still hold."""
    before = digest(tool_db)
    conn = access.connect(tool_db, readonly=True)
    try:
        guard(conn, 5)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(statement)
    finally:
        conn.close()
    assert digest(tool_db) == before


def test_every_schema_table_is_readable_or_private() -> None:
    script = access.SCHEMA.read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE (?:VIRTUAL )?TABLE IF NOT EXISTS (\w+)", script))
    assert tables, "schema.sql changed shape"
    assert tables <= sql.GAME_TABLES | sql.PRIVATE
    assert not sql.GAME_TABLES & sql.PRIVATE
    assert {"kv", "device"} <= sql.PRIVATE


# --- db_sql: allowed shapes ----------------------------------------------------------------

ALLOWED = {
    "select": "SELECT ref, eut FROM recipe ORDER BY ref",
    "join": "SELECT r.ref, m.unloc_name FROM recipe r JOIN recipe_map m ON m.id = r.recipe_map_id",
    "cte": "WITH x AS (SELECT ref FROM recipe) SELECT count(*) AS n FROM x",
    "values": "VALUES (1, 'r:a/b'), (2, 'r:a/c')",
    "fts and bm25": (
        "SELECT kind, name_en, bm25(name_fts) AS rank FROM name_fts WHERE name_fts MATCH 'steel'"
        " ORDER BY bm25(name_fts)"
    ),
    "fts highlight": (
        "SELECT highlight(name_fts, 2, '[', ']') FROM name_fts WHERE name_fts MATCH 'steel'"
    ),
    "quest fts": (
        "SELECT q.id, q.name FROM quest_fts f JOIN quest q ON q.id = f.quest_id"
        " WHERE quest_fts MATCH 'steel'"
    ),
    "json": "SELECT value FROM json_each('[1, 2]')",
    "json operators": """SELECT '{"a": 1}' ->> '$.a' AS a, json_extract('{"b": 2}', '$.b')""",
    "schema": "SELECT sql FROM sqlite_schema WHERE name = 'recipe'",
    "comments": "-- the recipes\n/* all */ SELECT ref FROM recipe",
    "window": "SELECT ref, row_number() OVER (ORDER BY ref) FROM recipe",
}  # fmt: skip


@pytest.mark.parametrize("shape", ALLOWED)
def test_db_sql_runs_read_only_selects(run: Runner, tool_db: Path, shape: str) -> None:
    before = digest(tool_db)
    p = run("db_sql", select=ALLOWED[shape])
    assert p.columns and p.rows
    assert digest(tool_db) == before


def test_db_sql_rows_are_the_dbs(
    run: Runner, db_rows: Callable[..., list[tuple]]
) -> None:
    p = run("db_sql", select="SELECT ref, eut, duration_ticks FROM recipe ORDER BY ref")
    want = db_rows("SELECT ref, eut, duration_ticks FROM recipe ORDER BY ref")
    got = [(r["ref"], r["eut"], r["duration_ticks"]) for r in p.rows]
    assert got == [tuple("" if v is None else str(v) for v in row) for row in want]
    assert sql.UNCITED_NOTE not in p.notes


def test_db_sql_says_when_numbers_have_no_citation(run: Runner) -> None:
    p = run("db_sql", select="SELECT name_en, max_item_in FROM recipe_map")
    assert sql.UNCITED_NOTE in p.notes
    quests = run("db_sql", select="SELECT id, main FROM quest")  # bare quest ids count
    assert sql.UNCITED_NOTE not in quests.notes


def test_db_sql_has_a_time_limit(tool_db: Path) -> None:
    tool = next(t for t in TOOLS if t.name == "db_sql")
    reg = Registry(Env(tool_db))
    reg.add(dataclasses.replace(tool, deadline_s=0.05))
    big = ", ".join(f"recipe r{i}" for i in range(8))
    out = reg.call("db_sql", {"select": f"SELECT count(*) FROM {big}"})
    assert out.is_error
    assert "ran past its 0.05 s limit" in out.text
    assert sql.DEADLINE_S == 2.0 and tool.deadline_s == sql.DEADLINE_S


def test_db_sql_pages_by_its_row_cap(
    tool_db: Path, make_runner: Callable[..., Runner]
) -> None:
    run = make_runner(build_registry(tool_db, key=b"k" * 32, page=3))
    p = run("db_sql", select="SELECT ref FROM recipe ORDER BY ref")
    refs = list(p.col("ref"))
    assert len(refs) == 3 and p.more
    while p.more:
        p = run.page(p.more)
        refs += p.col("ref")
    whole = make_runner(build_registry(tool_db))(
        "db_sql", select="SELECT ref FROM recipe ORDER BY ref"
    )
    assert refs == whole.col("ref")


def test_db_sql_is_deep_lane_only(reg: Registry) -> None:
    assert "db_sql" not in [s["name"] for s in reg.specs("fast")]
    assert "db_sql" in [s["name"] for s in reg.specs("deep")]
    out = reg.call("db_sql", {"select": "SELECT 1"}, lane="fast")
    assert out.is_error and "isn't offered in the fast lane" in out.text


def test_db_sql_arguments(run: Runner) -> None:
    assert "longer than 4000" in run.fail("db_sql", select="SELECT 1 " + " " * 4000)
    assert "SQL error" in run.fail("db_sql", select="SELECT nope FROM recipe")
    assert "one SELECT" in run.fail("db_sql", select="   ")


# --- db_query --------------------------------------------------------------------------------


def test_db_query_offers_every_named_query(reg: Registry) -> None:
    spec = reg.get("db_query")
    assert spec is not None
    names = spec.input_schema["properties"]["name"]["enum"]
    assert names == sorted(p.stem for p in access.QUERIES.glob("*.sql"))
    description = spec.input_schema["properties"]["name"]["description"]
    assert "find_names(q)" in description  # :limit belongs to the tool
    assert "quest_frontier(player, line)" in description


def test_db_query_runs_a_named_query(run: Runner) -> None:
    p = run("db_query", name="find_names", params={"q": "steel*"})
    assert p.head["query"] == "find_names"
    assert "Steel Ingot" in p.col("name_en")
    assert p.columns == ["kind", "ref_id", "name_en", "registry_name"]


def test_db_query_parameters(run: Runner) -> None:
    assert "missing q" in run.fail("db_query", name="find_names", params={})
    assert "unknown x" in run.fail(
        "db_query", name="find_names", params={"q": "a", "x": 1}
    )
    assert ":limit is set by db_query" in run.fail(
        "db_query", name="find_names", params={"q": "a", "limit": 10**9}
    )
    assert ":offset is set by db_query" in run.fail(
        "db_query", name="quest_search", params={"q": "a", "player": "p", "offset": 0}
    )
    assert "must be one of" in run.fail("db_query", name="../schema", params={})
    assert "params.q must be one of" in run.fail(
        "db_query", name="find_names", params={"q": ["a"]}
    )
    assert "database error" in run.fail(
        "db_query", name="find_names", params={"q": '"unbalanced'}
    )


def test_db_query_pages_with_and_without_its_own_limit(
    tool_db: Path, make_runner: Callable[..., Runner]
) -> None:
    run = make_runner(build_registry(tool_db, key=b"k" * 32, page=2))
    for name, params in [
        ("find_names", {"q": "steel*"}),  # has :limit
        ("item_ores", {"items": "[1, 2, 3, 4, 5, 6, 7, 8]"}),  # has none
    ]:
        whole = make_runner(build_registry(tool_db))(
            "db_query", name=name, params=params
        )
        assert len(whole.rows) > 2, name
        p = run("db_query", name=name, params=params)
        rows = list(p.rows)
        while p.more:
            p = run.page(p.more)
            rows += p.rows
        assert rows == whole.rows, name


def test_named_queries_parse() -> None:
    found = sql.named_queries()
    assert set(found) == {p.stem for p in access.QUERIES.glob("*.sql")}
    for q in found.values():
        assert q.summary, f"{q.name}.sql has no leading comment"
    assert sql.params_of("SELECT ':no' -- :nope\n, :yes, x::y FROM t /* :hid */") == (
        "yes",
    )


# Plausible values for every parameter a named query takes, so each one runs.
SAMPLE: dict[str, object] = {
    "q": '"steel"',
    "player": "11111111-2222-4333-8444-555555555555",
    "ids": "[1, 2, 3]",
    "items": "[1, 2, 3]",
    "ores": "[]",
    "maps": None,
    "handlers": None,
    "all": 0,
    "tier_max": None,
    "line": None,
    "kind": None,
    "nbt": "",
    "meta": 11305,
    "registry": "gregtech:gt.metaitem.01",
    "name": "Blast Furnace",
    "ref": "r:gt.recipe.blastfurnace/000000000000",
    "run_id": 1,
    "item_id": 1,
    "fluid_id": None,
    "fluid": None,
    "mte_id": 1000,
    "id": "AAAAAAAAAAAAAAAAAAAAAg",
}


def sample(name: str) -> dict[str, object]:
    wanted = [p for p in sql.named_queries()[name].params if p not in sql.MANAGED]
    missing = [p for p in wanted if p not in SAMPLE]
    assert not missing, f"add {missing} to SAMPLE"
    return {p: SAMPLE[p] for p in wanted}


@pytest.mark.parametrize("name", sorted(sql.named_queries()))
def test_every_named_query_runs(run: Runner, name: str) -> None:
    p = run("db_query", name=name, params=sample(name))
    assert p.columns, name
