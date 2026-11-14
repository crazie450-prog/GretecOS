"""The agents' tool layer (WO-2.2) over a DB built from a real GregtecLink harness run.

The run is the dev runtime's (GT 5.09.54.265 and its dependencies, not the pack), so the checks
are in shape: the resolver finds GT's own steel, stainless steel and EBF; every recipe row cites
a recipe of the run under its ``r:`` key with the DB's own numbers, across every page; paging
reaches the count the query reports; machine facts come back with their confidence; every named
query runs on the real schema; and db_sql's guards hold on the real file. As in
test_db_harness.py this runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after both
harnesses): run the client harness (mod/README.md), then
``GOS_REQUIRE_HARNESS_RUN=1 uv run pytest tests/integration/test_tools_harness.py``. With
``GOS_QUESTS_PACK`` set too (an extracted GTNH 2.9.0 pack's ``.minecraft``, as in
test_quests_pack.py), the quest tools also run on the real quest book for a new world. Nothing
is written outside tmp_path.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gregtec.agents.tools import build_registry
from gregtec.agents.tools.glossary import Glossary
from gregtec.agents.tools.registry import Output, Registry
from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import collect
from gregtec.ingest import quests as bq

REPO = Path(__file__).resolve().parents[2]
STEEL = "gregtech:gt.metaitem.01@11305"
STAINLESS = "gregtech:gt.metaitem.01@11306"
PLAYER = "11111111-2222-4333-8444-555555555555"


@pytest.fixture(scope="module")
def db(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the client harness")
    game = REPO / "mod" / "run" / "client"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("tools-harness")
    built_home(game, home)
    return access.db_path(home)


@pytest.fixture(scope="module")
def reg(db: Path) -> Registry:
    return build_registry(db, Glossary.find(None))  # the repo's vault-template glossary


def call(reg: Registry, name: str, **args: object) -> str:
    start = time.monotonic()
    out: Output = reg.call(name, args)
    assert time.monotonic() - start < 5, f"{name} took too long"
    assert not out.is_error, out.text
    return out.text


def total(text: str) -> int:
    """N of the result's ``rows: a-b of N`` line."""
    line = next(ln for ln in text.split("\n") if ln.startswith("rows: "))
    return int(line.rsplit(" of ", 1)[1])


def table(text: str) -> tuple[dict[str, str], list[dict[str, str]], str | None]:
    """(head, rows, cursor) of a result."""
    lines = text.rstrip("\n").split("\n")
    head: dict[str, str] = {}
    i = 0
    while i < len(lines) and lines[i] and "\t" not in lines[i]:
        key, _, value = lines[i].partition(": ")
        head[key] = value
        i += 1
    if i < len(lines) and lines[i] == "":
        i += 1
    columns = lines[i].split("\t")
    end = next(j for j in range(i, len(lines)) if lines[j].startswith("rows: ")) - 1
    rows = [
        dict(zip(columns, ln.split("\t"), strict=True)) for ln in lines[i + 1 : end]
    ]
    cursors = [ln[6:] for ln in lines if ln.startswith("more: ")]
    return head, rows, cursors[0] if cursors else None


def recipes(db: Path, refs: list[str]) -> dict[str, tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    try:
        run = conn.execute(
            "SELECT id FROM export_run ORDER BY created_at DESC, run_key DESC LIMIT 1"
        ).fetchone()[0]
        out = {}
        for ref in refs:
            row = conn.execute(
                "SELECT eut, duration_ticks, amperage FROM recipe WHERE run_id = ? AND ref = ?",
                (run, ref),
            ).fetchone()
            assert row is not None, ref
            out[ref] = tuple("" if v is None else str(v) for v in row)
        return out
    finally:
        conn.close()


def test_the_resolver_finds_gts_own_items(reg: Registry) -> None:
    for text, key in [
        ("steel", STEEL),
        ("steel ingot", STEEL),
        ("stainless steel", STAINLESS),
        ("SS", STAINLESS),  # the glossary alias
    ]:
        head, rows, _ = table(call(reg, "item_resolve", text=text))
        assert head["ambiguous"] == "0", text
        assert rows[0]["key"] == key, text
    head, rows, _ = table(call(reg, "item_resolve", text="EBF", kind="machine"))
    assert rows[0]["key"] == "mte:1000" and rows[0]["name"] == "Electric Blast Furnace"


def test_every_recipe_row_is_cited_with_the_dbs_numbers(
    reg: Registry, db: Path
) -> None:
    first = call(reg, "recipe_find", item="steel ingot")
    head, rows, cursor = table(first)
    assert head["item"] == f"Steel Ingot [{STEEL}]"
    seen = list(rows)
    while cursor:
        _, rows, cursor = table(call(reg, "more", cursor=cursor))
        seen += rows
    refs = [r["ref"] for r in seen]
    assert len(refs) == len(set(refs)) == total(first) > 50  # every page, no row twice
    found = recipes(db, refs)
    for r in seen:
        assert r["ref"].startswith("r:")
        assert (r["eut"], r["dur_t"], r["amps"]) == found[r["ref"]], r["ref"]
    # Recycling recipes last.
    flags = ["recycling" in r["flags"] for r in seen]
    assert flags == sorted(flags)


def test_the_ebf_and_its_steel_recipes(reg: Registry, db: Path) -> None:
    _, rows, _ = table(call(reg, "recipe_find", item="steel ingot", machine="EBF"))
    assert rows and {r["machine"] for r in rows} == {"Blast Furnace"}
    assert all(r["cond"].startswith("coil_heat=") for r in rows)
    recipes(db, [r["ref"] for r in rows])
    head, facts, _ = table(call(reg, "machine_info", machine="EBF"))
    assert head["machine"] == "mte:1000"
    by_key = {f["key"]: f for f in facts}
    assert by_key["oc"]["value"] == "heat" and by_key["oc"]["source"] == "curated"
    assert float(by_key["parallel"]["confidence"]) >= 0.8
    _, uses, _ = table(call(reg, "recipe_uses", item="steel ingot"))
    assert len(uses) == 50


def test_keys_with_spaces_resolve(reg: Registry, db: Path) -> None:
    """Fluid names such as "jet fuel a" and TConstruct's "Clay Cast" registry name have
    spaces: the keys item_resolve and the recipe cards give must work as arguments."""
    conn = sqlite3.connect(db)
    try:
        spaced = conn.execute(
            "SELECT f.name, count(DISTINCT o.recipe_id) FROM fluid AS f "
            "JOIN recipe_output AS o ON o.fluid_id = f.id "
            "WHERE f.name LIKE '% %' GROUP BY f.name ORDER BY f.name LIMIT 5"
        ).fetchall()
    finally:
        conn.close()
    assert spaced
    for name, _ in spaced:
        found = call(reg, "recipe_find", item=f"fluid:{name}", show_fake=True)
        head, rows, _ = table(found)
        assert head["item"].endswith(f"[fluid:{name}]") and rows, name
        _, found, _ = table(call(reg, "item_resolve", text=f"fluid:{name}"))
        assert [r["key"] for r in found] == [f"fluid:{name}"]


def test_every_named_query_runs_on_the_real_schema(reg: Registry, db: Path) -> None:
    spec = reg.get("db_query")
    assert spec is not None
    for name in spec.input_schema["properties"]["name"]["enum"]:
        out = reg.call("db_query", {"name": name, "params": {}})
        if out.is_error:  # it takes parameters: say which, then run it with NULLs
            assert "takes" in out.text, out.text
            wanted = out.text.split(" takes ", 1)[1].split(";")[0].split(", ")
            params = {p: None for p in wanted}
            params.update(
                {k: "[]" for k in ("ids", "items", "ores") if k in params}
                | ({"q": '"steel"'} if "q" in params else {})
                | ({"all": 0} if "all" in params else {})
                | ({"nbt": ""} if "nbt" in params else {})
            )
            out = reg.call("db_query", {"name": name, "params": params})
        assert not out.is_error, (name, out.text)


def test_db_sql_guards_hold_on_the_real_file(reg: Registry, db: Path) -> None:
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    for statement in (
        "DELETE FROM recipe",
        "WITH x AS (SELECT 1) DELETE FROM recipe",
        "SELECT * FROM kv",
        "ATTACH DATABASE ':memory:' AS x",
        "SELECT load_extension('x')",
    ):
        assert reg.call("db_sql", {"select": statement}).is_error, statement
    out = reg.call("db_sql", {"select": "SELECT count(*) FROM recipe a, recipe b"})
    assert out.is_error and "limit and was stopped" in out.text
    # One row calling instr 300 times over 64 KiB strings (a flattened subquery's column):
    # stopped by the deadline check in each call, not after the row (about 7 s).
    flat = (
        "WITH s(a, b) AS (SELECT printf('%.*c', 65000, 'a'), printf('%.*c', 32000, 'a') "
        "|| 'b') SELECT "
        + " + ".join(["x"] * 300)
        + " FROM (SELECT instr(a, b) AS x FROM s)"
    )
    start = time.monotonic()
    out = reg.call("db_sql", {"select": flat})
    assert out.is_error and "limit and was stopped" in out.text, out.text
    assert time.monotonic() - start < 3.5
    text = call(reg, "db_sql", select="SELECT ref, eut FROM recipe ORDER BY eut DESC")
    assert "rows: 1-50" in text and "\nmore: " in text
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_the_quest_tools_on_the_real_book(
    reg: Registry, db: Path, tmp_path: Path
) -> None:
    game = os.environ.get("GOS_QUESTS_PACK")
    if not game:
        pytest.skip(
            "set GOS_QUESTS_PACK to an extracted GTNH 2.9.0 pack's .minecraft folder"
        )
    world = tmp_path / "world"
    (world / bq.WORLD_DIR).mkdir(parents=True)
    names = {
        "nameCache:9": {"0:10": {"isOP:1": 0, "name:8": "Clayton", "uuid:8": PLAYER}}
    }
    (world / bq.WORLD_DIR / bq.NAME_CACHE).write_text(
        json.dumps(names), encoding="utf-8"
    )
    copy = tmp_path / "gos.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute("VACUUM INTO ?", (str(copy),))
    finally:
        conn.close()
    loaded = quest_db.read(world, Path(game))
    conn = access.connect(copy)
    try:
        quest_db.write(conn, loaded)
    finally:
        conn.close()
    quests = build_registry(copy)
    head, rows, _ = table(call(quests, "quest_frontier"))
    assert [r["name"] for r in rows] == ["Your First Night"]  # a new world (D113)
    assert head["player"] == "Clayton"
    _, found, _ = table(call(quests, "quest_for_item", item="steel ingot"))
    assert found and all(r["quest"].startswith("q:") for r in found)
    locked = next(r for r in found if r["state"] == "locked")
    head, parts, _ = table(call(quests, "quest_get", id=locked["quest"]))
    assert head["spoil"] == "0" and "description" not in head
    assert any(p["part"] == "start" for p in parts)  # where to start: the frontier
