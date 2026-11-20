"""Every tool that pages, its ``more`` cursor followed to the end, gives the rows one big page
gives (the registry's paging contract, plan 5.3).

Defect (two agents on real 'steel ingot' plans built from a harness export): effort_estimate
failed with "has no DB to page from" once a plan's effort table ran past one page (50 rows).
The registry refused any second page of a tool registered with ``needs_db=False``, so
plan_export, plan_get (nodes, alternatives, routes), plan_list, theme_list and telemetry_get
failed the same way. Such a tool's cursor now carries the tool's own epoch (``Result.epoch``,
a digest of every row) and is refused once those rows change; a live reading's (telemetry) is
never refused. A writing tool's result comes in one page: a cursor would run its write again.

The cases run on the tool tests' made-up export with a page of 1 row, so a few rows already
page (effort_estimate: the registry's own 50, on a breakdown of 120 rows).
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec import store as core_store
from gregtec.agents.tools.catalog import build_registry
from gregtec.agents.tools.registry import DEEP, Registry
from gregtec.agents.tools.tsv import Result, digest
from gregtec.bridge import ops as bridge_ops
from gregtec.config import Config
from gregtec.db import access
from gregtec.events import store as event_store
from gregtec.store import Store
from gregtec.vault import deploy as vault_deploy
from gregtec.vault.io import VaultIO

from .conftest import STEEL, parse

REPO = Path(__file__).resolve().parents[3]
WHOLE = 10**6


def walk(
    reg: Registry, tool: str, args: dict[str, Any]
) -> tuple[list[dict[str, str]], int]:
    """Every row of the tool's result, following its cursors; and how many pages it took."""
    out = reg.call(tool, args, lane=DEEP)
    rows: list[dict[str, str]] = []
    pages = 0
    while True:
        assert not out.is_error, (tool, pages, out.text)
        pages += 1
        p = parse(out.text)
        rows += p.rows
        if p.more is None:
            return rows, pages
        out = reg.call("more", {"cursor": p.more}, lane=DEEP)


def same_when_paged(
    small: Registry, whole: Registry, tool: str, args: dict[str, Any]
) -> int:
    """The rows page by page equal one big page's; the number of pages."""
    got, pages = walk(small, tool, args)
    want, one = walk(whole, tool, args)
    assert one == 1 and got == want, tool
    return pages


# --- the plan tools (needs_db=False: they read data/plans) ----------------------------------


@pytest.fixture
def home(fresh_home: Callable[..., Path]) -> Path:
    h = fresh_home()
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    return h


def _regs(home: Path, page: int) -> tuple[Registry, Registry]:
    def make(size: int) -> Registry:
        return build_registry(
            access.db_path(home), vault=VaultIO(home / "vault"), page=size
        )

    return make(page), make(WHOLE)


@pytest.fixture
def designed(home: Path) -> list[str]:
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    out = reg.call(
        "design_production", {"target": STEEL, "rate": "1/s", "tier": "HV"}, lane=DEEP
    )
    assert not out.is_error, out.text
    return [r["plan"].removeprefix("plan:") for r in parse(out.text).rows]


def test_effort_estimate_pages_to_the_end(home: Path, designed: list[str]) -> None:
    """The made-up export's plans have no effort rows (its machines have no items): give one
    a breakdown of 120 recipes, past two pages of 50, as a real steel plan's runs past one."""
    pid = designed[0]
    path = home / "data" / "plans" / f"{pid}.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["metrics"]["effort"] = {
        "rows": [
            {
                "part": "build",
                "map": "gt.recipe.blastfurnace",
                "mte": 1000,
                "crafts": n,
                "machines": 1,
                "h": n / 3600,
                "recipes": [{"id": f"r:gt.recipe.blastfurnace/{n:012x}", "crafts": n,
                             "h": n / 3600}],
            }
            for n in range(1, 121)
        ],
        "uncounted": 0,
        "craft_s": 1.0,
        "notes": [],
        "unlocks": [],
        "components": [],
    }  # fmt: skip
    path.write_text(json.dumps(plan), encoding="utf-8")
    reg, whole = _regs(home, 50)  # the registry's own page
    rows, pages = walk(reg, "effort_estimate", {"id": pid})
    assert pages == 3 and len(rows) == 120
    assert [r["crafts"] for r in rows] == [str(n) for n in range(1, 121)]
    assert walk(whole, "effort_estimate", {"id": pid}) == (rows, 1)


@pytest.mark.parametrize(
    "tool, args",
    [
        ("plan_export", {"fmt": "bom"}),
        ("plan_export", {"fmt": "mermaid"}),
        ("plan_get", {"view": "nodes"}),
        ("plan_get", {"view": "routes"}),
        ("plan_get", {"view": "alternatives", "node": STEEL, "all": True}),
    ],
)
def test_the_plan_tools_page_to_the_end(
    home: Path, designed: list[str], tool: str, args: dict[str, Any]
) -> None:
    small, whole = _regs(home, 1)
    pages = [same_when_paged(small, whole, tool, {"id": p, **args}) for p in designed]
    assert max(pages) > 1


def test_plan_list_and_theme_list_page_to_the_end(
    home: Path, designed: list[str]
) -> None:
    small, whole = _regs(home, 1)
    assert same_when_paged(small, whole, "plan_list", {}) == len(designed)
    assert same_when_paged(small, whole, "theme_list", {}) > 1


def test_a_files_cursor_dies_when_its_rows_change(
    home: Path, designed: list[str]
) -> None:
    small, _ = _regs(home, 1)
    first = parse(small.call("plan_list", {}, lane=DEEP).text)
    assert first.more is not None
    renamed = small.call(
        "plan_rename", {"id": designed[-1], "name": "Renamed line"}, lane=DEEP
    )
    assert not renamed.is_error, renamed.text
    stale = small.call("more", {"cursor": first.more}, lane=DEEP)
    assert stale.is_error and "changed since this cursor was issued" in stale.text
    # a fresh call pages again, with the new name
    rows, _ = walk(small, "plan_list", {})
    assert "Renamed line" in {r["name"] for r in rows}


def test_a_writing_tool_answers_in_one_page(home: Path) -> None:
    small, _ = _regs(home, 1)
    out = small.call(
        "design_production", {"target": STEEL, "rate": "1/s", "tier": "HV"}, lane=DEEP
    )
    p = parse(out.text)
    assert not out.is_error and p.more is None
    assert len(p.rows) == 3 and p.span == "1-3 of 3"


def test_a_no_db_tools_cursor_follows_its_own_epoch(tmp_path: Path) -> None:
    from gregtec.agents.tools.registry import Env, Tool

    table = [("a",), ("b",), ("c",)]

    def handler(call: Any, args: dict[str, Any], offset: int) -> Result:  # noqa: ANN401
        return Result(
            ("x",),
            list(table[offset : offset + call.page]),
            offset=offset,
            more=offset + call.page < len(table),
            epoch=digest(table) if args["checked"] else None,
        )

    schema = {
        "type": "object",
        "properties": {"checked": {"type": "boolean", "description": "d"}},
        "required": ["checked"],
        "additionalProperties": False,
    }
    reg = Registry(Env(tmp_path / "none.sqlite"), page=1)
    reg.add(Tool("rows", "d", schema, handler, needs_db=False))
    for checked in (True, False):
        rows, pages = walk(reg, "rows", {"checked": checked})
        assert [r["x"] for r in rows] == ["a", "b", "c"] and pages == 3
    checked = parse(reg.call("rows", {"checked": True}).text).more
    live = parse(reg.call("rows", {"checked": False}).text).more
    assert checked is not None and live is not None
    table[2] = ("C",)
    stale = reg.call("more", {"cursor": checked})
    assert stale.is_error and "changed since this cursor was issued" in stale.text
    assert parse(reg.call("more", {"cursor": live}).text).rows == [{"n": "2", "x": "b"}]


def test_digest_is_of_every_row() -> None:
    assert digest([(1, "a")]) == digest([(1, "a")])
    assert digest([(1, "a")]) != digest([(1, "b")])
    assert digest([(1, "a")]) != digest([("1", "a")])
    assert digest([]) != digest([("",)])


# --- telemetry (needs_db=False, live: its cursor is never refused) -------------------------


@pytest.fixture
def ops(tmp_path: Path) -> Iterator[bridge_ops.Ops]:
    store = Store(tmp_path / "core.sqlite")
    o = bridge_ops.build(Config(gos_home=tmp_path), store)
    yield o
    store.close()


def test_telemetry_get_pages_to_the_end(tmp_path: Path, ops: bridge_ops.Ops) -> None:
    doc = json.loads(
        (REPO / "tests/fixtures/oc/telemetry-first.json").read_text(encoding="utf-8")
    )
    now = int(time.time()) + 0.9
    ops.telemetry.clock = lambda: now  # every age is read at one moment
    assert ops.bridge.receive(
        json.dumps(copy.deepcopy(doc)).encode(),
        transport="mailbox",
        source="t",
        binding="lab",
        now=now,
    ).ok
    small = build_registry(tmp_path / "no.sqlite", ops=ops, page=1)
    whole = build_registry(tmp_path / "no.sqlite", ops=ops, page=WHOLE)
    assert same_when_paged(small, whole, "telemetry_get", {"scope": "all"}) > 1


def test_crop_status_pages_to_the_end(
    tmp_path: Path, ops: bridge_ops.Ops, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The breeding stations' status reads telemetry too: three stations, a page of one."""
    from gregtec.crops import station

    found = [
        {"ref": f"tel:lab/station-{n}", "state": "idle", "age_s": n, "stale": False}
        for n in range(3)
    ]
    monkeypatch.setattr(station, "status", lambda telemetry, instance: found)
    small = build_registry(tmp_path / "no.sqlite", ops=ops, page=1)
    whole = build_registry(tmp_path / "no.sqlite", ops=ops, page=WHOLE)
    assert same_when_paged(small, whole, "crop_status", {}) == 3


# --- the DB tools, on the made-up export with its quest book --------------------------------


@pytest.mark.parametrize(
    "tool, args",
    [
        ("recipe_find", {"item": "steel ingot"}),
        ("recipe_uses", {"item": "steel ingot"}),
        ("quest_find", {"text": "steel"}),
        ("quest_frontier", {}),
        ("db_sql", {"select": "SELECT ref FROM recipe ORDER BY ref"}),
    ],
)
def test_the_db_tools_page_to_the_end(
    tool_db: Path, tool: str, args: dict[str, Any]
) -> None:
    small = build_registry(tool_db, page=1)
    whole = build_registry(tool_db, page=WHOLE)
    assert same_when_paged(small, whole, tool, args) > 1


def test_the_event_tools_page_before_the_first_db_build(tmp_path: Path) -> None:
    """Found by the same check: on a home whose gos.sqlite only the core's Store has made (no
    ``gos db build`` yet, QA 5-9's state) a result past one page failed with "there is no DB
    ... yet", because the cursor's epoch asked for the export run. Such a DB's epoch is empty."""
    path = tmp_path / "gos.sqlite"
    conn = core_store.connect(path)
    now = time.time()
    event_store.record(
        conn,
        "play",
        [
            {"id": "p1", "kind": "location", "ts": now - 600, "source": "log",
             "data": {"via": "login", "x": 5, "y": 64, "z": 5, "dim": 0}},
            {"id": "p2", "kind": "quest_done", "ts": now - 500, "source": "log",
             "data": {"quest": "q:AAAAAAAAAAAAAAAAAAAABQ"}},
            {"id": "p3", "kind": "death", "ts": now - 200, "source": "log",
             "data": {"message": "Clayton was slain by Zombie"}},
        ],
        "survival",
    )  # fmt: skip
    conn.close()
    assert not access.is_built(path)
    small = build_registry(path, page=1)
    whole = build_registry(path, page=WHOLE)
    assert same_when_paged(small, whole, "events_recent", {"window": "1h"}) == 3
    assert same_when_paged(small, whole, "session_summary", {}) == 3
