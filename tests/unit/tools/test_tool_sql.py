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
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gregtec.agents.tools import TOOLS, build_registry, sql, tsv
from gregtec.agents.tools.quests import TEXT_NOTE
from gregtec.agents.tools.registry import Env, Registry, guard
from gregtec.db import access, structures

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
    script = access.SCHEMA.read_text(encoding="utf-8") + structures.SCHEMA  # D123
    tables = set(re.findall(r"CREATE (?:VIRTUAL )?TABLE IF NOT EXISTS (\w+)", script))
    assert {"recipe", "quest", "structure_run"} <= tables
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


def short_registry(tool_db: Path, seconds: float) -> Registry:
    tool = next(t for t in TOOLS if t.name == "db_sql")
    reg = Registry(Env(tool_db))
    reg.add(dataclasses.replace(tool, deadline_s=seconds))
    return reg


def test_one_huge_function_call_ends_at_once(run: Runner) -> None:
    """The review's probe: instr over two strings of about 1 MB ran 5 s in one step of the
    query, where the progress handler never looks. Values are now capped at 64 KiB (printf
    stops there; anything else that would pass it is an error)."""
    probe = "instr(printf('%.*c',990000,'a'), printf('%.*c',495000,'a')||'b')"
    for select in (f"SELECT {probe}", "SELECT " + ", ".join([probe] * 4)):
        start = time.monotonic()
        run.out("db_sql", select=select)
        assert time.monotonic() - start < 1, select
    half = "printf('%.*c', 40000, 'a')"
    error = run.fail("db_sql", select=f"SELECT length({half} || {half})")
    assert "too big" in error and "64 KiB" in error


# Long strings, and the longest LIKE and GLOB patterns db_sql takes, that match almost
# everywhere: the slowest a costly function gets.
FLAT = (
    "WITH s(a, b, p, g) AS (SELECT printf('%.*c', 65000, 'a'), printf('%.*c', 32000, 'a') "
    "|| 'b', '%' || printf('%.*c', 96, 'a') || 'b%', '*' || printf('%.*c', 96, 'a') || 'b*') "
)


@pytest.mark.parametrize(
    "expr",
    [
        "instr(a, b)",
        "length(replace(a, b, ''))",
        "a LIKE p",
        "like(p, a, '!')",
        "a GLOB g",
    ],
)
def test_a_row_calling_a_costly_function_hundreds_of_times_stops_at_the_deadline(
    tool_db: Path, expr: str
) -> None:
    """SQLite copies a flattened subquery's expression to every use of its column and runs a
    row's expressions without checking the progress handler: 300 uses of one instr ran 300
    calls (6.5 s at 64 KiB) in one step. Each costly call now checks the deadline first."""
    reg = short_registry(tool_db, 0.2)
    uses = " + ".join(["x"] * 300)
    start = time.monotonic()
    out = reg.call(
        "db_sql", {"select": f"{FLAT}SELECT {uses} FROM (SELECT {expr} AS x FROM s)"}
    )
    elapsed = time.monotonic() - start
    assert out.is_error and "ran past its 0.2 s limit" in out.text, out.text
    assert elapsed < 1.5


@pytest.mark.parametrize("fn", ["trim", "ltrim", "rtrim"])
def test_no_trim(run: Runner, fn: str) -> None:
    """trim(x, chars) takes time that grows with the product of its lengths (half a second for
    one call at 64 KiB), and the authorizer can't tell it from trim(x)."""
    error = run.fail("db_sql", select=f"SELECT {fn}(' a ')")
    assert "refused" in error and fn in error


def test_db_sql_limits(run: Runner) -> None:
    pattern = "a" * 101
    assert "pattern too complex" in run.fail(
        "db_sql", select=f"SELECT 'a' LIKE '{pattern}'"
    )
    assert "100 bytes" in run.fail("db_sql", select=f"SELECT 'a' GLOB '{pattern}'")
    assert run("db_sql", select=f"SELECT 'a' LIKE '{pattern[1:]}'").rows
    columns = ", ".join(str(n) for n in range(101))
    assert "too many columns" in run.fail("db_sql", select=f"SELECT {columns}")
    assert run("db_sql", select=f"SELECT {columns.rsplit(', ', 1)[0]}").rows


# Each wrapped function in the shapes SQLite's own takes: NULLs, numbers, blobs, ESCAPE,
# case-folding (ASCII only), GLOB classes, an empty needle.
COSTLY_CASES = [
    "'Steel Ingot' LIKE 'steel%'",
    "'ÄBC' LIKE 'äbc'",
    "'a_c' LIKE 'a!_c' ESCAPE '!'",
    "'abc' LIKE 'a_c'",
    "'x' NOT LIKE 'y'",
    "NULL LIKE 'a'",
    "1.5 LIKE '1.5'",
    "'Abc' GLOB 'a*'",
    "'abc' GLOB '[a-c]?c'",
    "instr('héllo wörld', 'wö')",
    "instr(x'00010203', x'0203')",
    "instr(12345, 34)",
    "instr('abc', '')",
    "instr(NULL, 'a')",
    "replace('aaa', 'a', 'bb')",
    "replace('abc', '', 'x')",
    "replace(123, 2, 9)",
    "hex(replace(x'616263', x'62', x'7a'))",
]


def test_the_costly_functions_answer_as_sqlites_own(run: Runner) -> None:
    select = "SELECT " + ", ".join(f"{e} AS c{n}" for n, e in enumerate(COSTLY_CASES))
    p = run("db_sql", select=select)
    conn = sqlite3.connect(":memory:")
    try:
        want = conn.execute(select).fetchone()
    finally:
        conn.close()
    got = [p.rows[0][f"c{n}"] for n in range(len(COSTLY_CASES))]
    assert got == [tsv.cell(v) for v in want]


def test_a_page_stops_once_its_values_pass_the_byte_cap(
    run: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    whole = run("db_sql", select="SELECT ref FROM recipe ORDER BY ref").col("ref")
    monkeypatch.setattr(sql, "PAGE_BYTES", 1000)
    # 300 characters a row: a ref after its padding.
    select = "SELECT printf('%.*c', 300 - length(ref), '-') || ref AS big FROM recipe ORDER BY ref"
    p = run("db_sql", select=select)
    assert len(p.rows) == 4 and p.more  # 4 x 300 bytes pass 1,000
    assert any(n.startswith("this page stops at 4 rows") for n in p.notes)
    big = p.col("big")
    while p.more:
        p = run.page(p.more)
        big += p.col("big")
    assert [b.lstrip("-") for b in big] == whole


def test_db_sql_marks_quest_text(run: Runner) -> None:
    for select in (
        "SELECT id, name FROM quest",
        "SELECT quest_id FROM quest_fts WHERE quest_fts MATCH 'steel'",
    ):
        p = run("db_sql", select=select)
        assert TEXT_NOTE in p.notes and sql.SPOILER_NOTE in p.notes, select
    p = run("db_sql", select="SELECT ref FROM recipe")
    assert TEXT_NOTE not in p.notes and sql.SPOILER_NOTE not in p.notes


def test_a_22_character_value_is_a_quest_id_only_in_an_id_column(run: Runner) -> None:
    """cited() is a heuristic (the review's case: a GT material name of 22 letters)."""
    material = "SELECT 'PlatinumMetallicPowder' AS gt_material, -1 AS amount"
    assert sql.UNCITED_NOTE in run("db_sql", select=material).notes
    for select in (
        "SELECT 'AAAAAAAAAAAAAAAAAAAABA' AS id, 1 AS main",
        "SELECT 'AAAAAAAAAAAAAAAAAAAABA' AS QUEST_ID, 1 AS main",
        "SELECT 'q:AAAAAAAAAAAAAAAAAAAABA' AS quest, 1 AS main",
        "SELECT 'r:gt.recipe.x/0123456789ab' AS ref, 120 AS eut",
    ):
        assert sql.UNCITED_NOTE not in run("db_sql", select=select).notes, select


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


def test_db_query_offers_the_game_data_queries(reg: Registry) -> None:
    spec = reg.get("db_query")
    assert spec is not None
    names = spec.input_schema["properties"]["name"]["enum"]
    files = {p.stem for p in access.QUERIES.glob("*.sql")}
    assert names == sorted(sql.DB_QUERY) and sql.DB_QUERY <= files
    assert not [n for n in names if n.startswith("quest")]
    description = spec.input_schema["properties"]["name"]["description"]
    assert "find_names(q)" in description  # :limit belongs to the tool
    assert "quest" not in description


QUEST_QUERIES = sorted(
    p.stem for p in access.QUERIES.glob("*.sql") if p.stem.startswith("quest")
)


@pytest.mark.parametrize("lane", [None, "fast", "deep"])
def test_db_query_never_runs_the_quest_tools_queries(
    reg: Registry, make_runner: Callable[..., Runner], lane: str | None
) -> None:
    """The quest tools show a quest past the frontier nudge-first (D26); their queries would
    show its name, description, tasks and rewards, so db_query has none of them."""
    run = make_runner(reg, lane)
    assert len(QUEST_QUERIES) >= 8
    for name in QUEST_QUERIES:
        assert "must be one of" in run.fail(
            "db_query", name=name, params={"id": "AAAAAAAAAAAAAAAAAAAABA"}
        )


def test_db_query_never_reads_a_private_table(
    run: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever a named query says, db_query's authorizer refuses kv and device."""
    peek = sql.Named("peek", (), "a query that reads kv")
    monkeypatch.setattr(sql, "named_queries", lambda: {"find_names": peek})
    monkeypatch.setattr(access, "query_text", lambda name: "SELECT key FROM kv")
    assert "access to kv.key is prohibited" in run.fail(
        "db_query", name="find_names", params={}
    )


def test_db_querys_authorizer_allows_recursion_and_refuses_private_tables() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE kv(key, value)")
        conn.execute("CREATE TABLE device(id)")
        conn.set_authorizer(sql.authorize_named)
        recursive = (
            "WITH RECURSIVE c(x) AS (SELECT 1 UNION SELECT x + 1 FROM c WHERE x < 3)"
        )
        assert conn.execute(f"{recursive} SELECT count(*) FROM c").fetchone() == (3,)
        for table in ("kv", "device", "main.KV"):
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                conn.execute(f"SELECT count(*) FROM {table}")
    finally:
        conn.close()


def test_db_query_runs_a_named_query(run: Runner) -> None:
    p = run("db_query", name="find_names", params={"q": "steel*"})
    assert p.head["query"] == "find_names"
    assert "Steel Ingot" in p.col("name_en")
    assert p.columns == ["kind", "ref_id", "name_en", "registry_name"]
    assert sql.UNCITED_NOTE in p.notes  # ref_id is a DB id, not a citation
    cited = run("db_query", name="recipe_by_ref", params={"run_id": 1, "ref": "r:x/y"})
    assert sql.UNCITED_NOTE not in cited.notes  # no rows


def test_db_query_parameters(run: Runner) -> None:
    assert "missing q" in run.fail("db_query", name="find_names", params={})
    assert "unknown x" in run.fail(
        "db_query", name="find_names", params={"q": "a", "x": 1}
    )
    assert ":limit is set by db_query" in run.fail(
        "db_query", name="find_names", params={"q": "a", "limit": 10**9}
    )
    assert ":offset is set by db_query" in run.fail(
        "db_query",
        name="recipes_using_item",
        params={"run_id": 1, "items": "[]", "offset": 0},
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
    assert set(found) == sql.DB_QUERY
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
