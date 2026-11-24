"""WO-10.10: the shell workbench's core (plan 7.10, D367-D370) on WO-10.7's fixtures.

- GOS's reviser (:mod:`gregtec.build.shell_revise`): the words it acts on and the ones it refuses;
- the version chain (:mod:`gregtec.build.shell_versions`): parents, notes, times, costs; revise
  through the same gate (accepted, refused with nothing written, repaired); undo and make
  current, nothing ever deleted; a build made before WO-10.10 adopted as a one-version design;
  revise reads no ``structure`` row (the real dump's rows are large);
- the roof's ``pitch`` (1 is the cells of before);
- the router's revise-versus-new decision and the asker's lanes for it;
- ``build_revise`` (deep lane, the architect's), ``gos shell revise|versions|undo|current|compare``
  and the workbench's routes (auth, jobs with a Cancel that saves nothing, compare, the viewer);
- unchanged on purpose: no tool places blocks, refused designs write nothing, the palette is the
  catalog's registry name + meta, nothing here writes a ``.schematic`` or a world.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.agents import ask, deep_config, router
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.no_ai import AiMode
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.catalog import TOOLS
from gregtec.agents.tools.registry import DEEP, FAST
from gregtec.build import shell, shell_revise, shell_store
from gregtec.build import shell_versions as versions
from gregtec.db import access
from gregtec.vault.io import VaultIO

HERE = Path(__file__).parent
ts: Any = sys.modules.get("_wo1010_shell_tests")
if ts is None:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "_wo1010_shell_tests", HERE / "test_shell.py"
    )
    assert _spec is not None and _spec.loader is not None
    ts = importlib.util.module_from_spec(_spec)
    sys.modules["_wo1010_shell_tests"] = ts
    _spec.loader.exec_module(ts)

ADMIN = "a" * 40


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return ts.make_home(tmp_path, ts.lay_tests.production(), ts.lay_tests.power())


@pytest.fixture
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


def first(conn: sqlite3.Connection, home: Path) -> tuple[str, str]:
    """A procedural foundry shell around the steel line, started as a design."""
    made = shell_store.make(
        conn, home, VaultIO(home / "vault"), ["pl-steel"], theme_id="th-foundry"
    )
    assert made.outcome.status == "accepted"
    d = versions.start(home, made.build, asked="a foundry shell", by="procedural")
    return d["id"], made.build


def builds_on_disk(home: Path) -> set[str]:
    root = home / shell_store.ROOT
    return {p.name for p in root.iterdir()} if root.is_dir() else set()


def rev(
    conn: sqlite3.Connection,
    home: Path,
    words: str,
    **kw: Any,  # noqa: ANN401
) -> versions.Revised:
    return versions.revise(conn, home, VaultIO(home / "vault"), None, words, **kw)


# --- GOS's reviser ----------------------------------------------------------------------------


def test_clauses_split_requests_and_keep_a_list_of_sides_together() -> None:
    assert shell_revise.clauses("Taller, and no windows on the north wall") == [
        "taller",
        "no windows on the north wall",
    ]
    assert shell_revise.clauses("no windows on the north and east walls") == [
        "no windows on the north and east walls"
    ]
    assert shell_revise.clauses("make the roof steeper. more lights") == [
        "make the roof steeper",
        "more lights",
    ]


def test_the_reviser_changes_only_what_the_words_name(
    conn: sqlite3.Connection, home: Path
) -> None:
    _, build = first(conn, home)
    rec = shell_store.load(home, build)
    r = shell_revise.revise(rec, "make the roof steeper")
    assert r.ok and r.unknown == () and r.impossible == ()
    roof = [o for o in r.ops if o["op"] == "roof"]
    assert roof[0]["style"] == "gable" and "parapet" not in roof[0]
    others = [o for o in r.ops if o["op"] != "roof"]
    assert others == [o for o in rec["ops"] if o["op"] != "roof"]  # the rest kept
    assert r.headroom >= rec["headroom"]  # raised so the gable fits, never lowered
    r = shell_revise.revise(rec, "taller, and no windows on the north wall")
    assert r.ok, r
    wins = [o for o in r.ops if o["op"] == "windows"]
    assert wins and all("north" not in o["sides"] for o in wins)
    assert r.headroom == rec["headroom"] + shell_revise.STEP
    roof = next(o for o in r.ops if o["op"] == "roof")
    assert roof["y"] == rec["roof_y"] + shell_revise.STEP
    r = shell_revise.revise(rec, "no lights")
    assert r.ok and not [o for o in r.ops if o["op"] == "lights"]
    r = shell_revise.revise(rec, "fewer pillars")
    assert r.ok and next(o for o in r.ops if o["op"] == "pillars")["every"] == 12


def test_the_headroom_note_is_given_only_when_the_headroom_changes(
    conn: sqlite3.Connection, home: Path
) -> None:
    _, build = first(conn, home)
    rec = shell_store.load(home, build)
    _, _, top = shell.LIMITS["headroom"]
    seen = []
    for words in ["make the roof steeper"] * 3 + [
        "no windows on the north wall",
        "no lights",
    ]:
        r = shell_revise.revise(rec, words)
        if not r.ok:
            continue
        noted = any(c.startswith("headroom") for c in r.changes)
        assert noted == (r.headroom != rec["headroom"]), (words, r.changes)
        seen.append((words, r.headroom))
        rec = {**rec, "ops": list(r.ops), "headroom": r.headroom}
    assert ("no windows on the north wall", rec["headroom"]) in seen
    assert rec["headroom"] <= top


@pytest.mark.parametrize(
    ("words", "kind"),
    [
        ("make it gothic", "unknown"),
        ("lower", "impossible"),  # the walls stop at the machines' height already
        ("flatter", "impossible"),  # the fixture's roof is flat
        ("no plinth and make it gothic", "unknown"),  # half a request is never done
    ],
)
def test_the_reviser_refuses_what_it_cannot_do(
    conn: sqlite3.Connection, home: Path, words: str, kind: str
) -> None:
    _, build = first(conn, home)
    r = shell_revise.revise(shell_store.load(home, build), words)
    assert not r.ok
    assert getattr(r, kind), r


def test_a_gable_pitch_of_one_is_the_roof_of_before(conn: sqlite3.Connection) -> None:
    lay = ts.layout.plan_layout(conn, [ts.lay_tests.production()])
    spec = ts.spec_for(conn, lay, headroom=16)
    gable = {"op": "roof", "role": "roof", "style": "gable"}
    walls = {"op": "walls", "role": "wall"}
    one = shell.compile_ops(spec, [walls, gable]).cells
    same = shell.compile_ops(spec, [walls, {**gable, "pitch": 1}]).cells
    two = shell.compile_ops(spec, [walls, {**gable, "pitch": 2}]).cells
    assert one == same and len(two) > len(one)
    top = max(c[1] for c in one)
    assert max(c[1] for c in two) > top
    with pytest.raises(shell.ShellError, match="pitch"):
        shell.compile_ops(spec, [walls, {**gable, "pitch": 4}])


# --- the version chain ------------------------------------------------------------------------


def test_versions_form_a_chain_and_nothing_is_deleted(
    conn: sqlite3.Connection, home: Path
) -> None:
    ds, v1 = first(conn, home)
    v1_bytes = (shell_store.folder(home, v1) / "design.json").read_bytes()
    a = rev(conn, home, "make the roof steeper")
    assert a.saved and a.version is not None and a.parent == v1
    b = rev(conn, home, "no lights")
    assert b.saved and b.version is not None and b.parent == a.build
    d = versions.get(home, ds)
    chain = [(v["n"], v["build"], v["parent"]) for v in d["versions"]]
    assert chain == [(1, v1, None), (2, a.build, v1), (3, b.build, a.build)]
    assert [v["asked"] for v in d["versions"]] == [
        "a foundry shell",
        "make the roof steeper",
        "no lights",
    ]
    assert all(v["by"] == "procedural" and v["cost_usd"] == 0.0 for v in d["versions"])
    ats = [v["at"] for v in d["versions"]]
    assert ats == sorted(ats)
    assert d["current"] == b.build and versions.current_id(home) == ds
    # the saved builds are never touched: a version is a build folder, written once
    assert (shell_store.folder(home, v1) / "design.json").read_bytes() == v1_bytes
    # undo, then make any version current: history grows, no build goes
    d2, cur = versions.undo(home)
    assert cur["build"] == a.build and d2["current"] == a.build
    versions.make_current(home, ds, v1)
    with pytest.raises(versions.VersionError, match="first version"):
        versions.undo(home)
    d = versions.get(home, ds)
    assert [h["current"] for h in d["history"]] == [v1, a.build, b.build, a.build, v1]
    assert builds_on_disk(home) == {v1, a.build, b.build}
    # a revision starts from the current version (v1 again), its parent recorded
    c = rev(conn, home, "fewer pillars")
    assert c.saved and c.parent == v1 and len(versions.get(home, ds)["versions"]) == 4
    with pytest.raises(versions.VersionError, match="not a version"):
        versions.make_current(home, ds, "sh-000000000000")
    # operations that give the current version again change nothing, and say so
    now = shell_store.load(home, c.build)
    same = rev(conn, home, "the same again", ops=now["ops"], by="architect")
    assert not same.saved and same.build == c.build
    assert "nothing changes" in same.reasons[0], same.reasons
    assert "make it current" not in same.reasons[0]


def test_a_refused_revision_writes_nothing_and_keeps_the_current_version(
    conn: sqlite3.Connection, home: Path
) -> None:
    ds, v1 = first(conn, home)
    design_file = (home / versions.ROOT / f"{ds}.json").read_bytes()
    before = builds_on_disk(home)
    for words in ("make it gothic", "flatter"):
        got = rev(conn, home, words)
        assert not got.saved and got.reasons and got.version is None
    # the gate refuses the architect's design with a tile entity or GT's machine block
    rec = shell_store.load(home, v1)
    for block in (ts.CHEST, ts.MACHINE):
        ops = [
            *rec["ops"],
            {"op": "fill", "block": block, "lo": [0, 0, 0], "hi": [0, 0, 0]},
        ]
        got = rev(conn, home, "a chest by the door", ops=ops, by="architect")
        assert not got.saved and got.outcome.status == "refused"
        assert "outside the palette" in " ".join(got.reasons)
    # the same shell again is not a new version
    got = rev(conn, home, "", ops=rec["ops"], by="architect")
    assert not got.saved and "again" in got.reasons[0]
    assert builds_on_disk(home) == before
    assert (home / versions.ROOT / f"{ds}.json").read_bytes() == design_file
    assert versions.get(home, ds)["current"] == v1


def test_a_small_breach_is_repaired_and_saved_as_a_version(
    conn: sqlite3.Connection, home: Path
) -> None:
    ds, v1 = first(conn, home)
    rec = shell_store.load(home, v1)
    lay = json.loads((shell_store.folder(home, v1) / "layout.json").read_text("utf-8"))
    k = lay["keepouts"][0]
    lo = k["lo"]
    breach = {
        "op": "fill",
        "role": "accent",
        "lo": lo,
        "hi": lo,
    }  # one cell in a keep-out
    got = rev(conn, home, "an accent block", ops=[*rec["ops"], breach], by="architect")
    assert got.saved and got.outcome.status == "repaired", got.reasons
    assert any(k["id"] in r for r in got.outcome.repairs)
    v = versions.get(home, ds)["versions"][-1]
    assert (
        v["by"] == "architect" and v["cost_usd"] is None and v["status"] == "repaired"
    )
    # the viewer's DB-free cells are the gate's cells, the repair included
    _, gated = shell_store.rebuild(conn, home, got.build)
    _, free = shell_store.cells_of(home, got.build)
    assert free == dict(gated.cells)


def test_revise_reads_no_structure_row(conn: sqlite3.Connection, home: Path) -> None:
    first(conn, home)

    def deny(action: int, table: str | None, *_: object) -> int:
        if action == sqlite3.SQLITE_READ and table == "structure":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(deny)
    try:
        got = rev(conn, home, "make the roof steeper, no lights")
    finally:
        conn.set_authorizer(None)
    assert got.saved, got.reasons


def test_a_build_from_before_the_workbench_is_a_one_version_design(
    conn: sqlite3.Connection, home: Path
) -> None:
    made = shell_store.make(
        conn, home, VaultIO(home / "vault"), ["pl-steel"], theme_id="th-foundry"
    )
    listed = versions.designs(home)  # no design file yet: nothing is written by a GET
    assert [d["current"] for d in listed] == [made.build]
    assert not (home / versions.ROOT).exists()
    ds = listed[0]["id"]
    assert ds == versions.design_id(made.build) and versions.current_id(home) == ds
    got = rev(conn, home, "no lights")
    assert got.saved
    d = versions.get(home, ds)
    assert [v["build"] for v in d["versions"]] == [made.build, got.build]
    assert len(versions.designs(home)) == 1


def test_compare_gives_material_and_block_differences(
    conn: sqlite3.Connection, home: Path
) -> None:
    _, v1 = first(conn, home)
    got = rev(conn, home, "make the roof steeper")
    c = versions.compare(home, v1, got.build)
    a, b = shell_store.load(home, v1), shell_store.load(home, got.build)
    assert c["blocks_delta"] == b["blocks"] - a["blocks"]
    by = {m["block"]: m for m in c["materials"]}
    for m in a["materials"]:
        assert by[m["block"]]["a"] == m["count"]
    for m in b["materials"]:
        assert by[m["block"]]["b"] == m["count"]
    assert all(m["delta"] == m["b"] - m["a"] for m in c["materials"])
    assert all("@" in m["block"] for m in c["materials"])  # registry name + meta
    assert any(line.startswith("roof") for line in c["ops"])


def test_the_viewer_data_is_the_shell_with_its_layers_and_ghosts(
    conn: sqlite3.Connection, home: Path
) -> None:
    _, v1 = first(conn, home)
    vw = versions.viewer(conn, home, v1)
    rec = shell_store.load(home, v1)
    assert len(vw["cells"]) == rec["blocks"]
    sx, sy, sz = vw["size"]
    assert all(
        0 <= x < sx and 0 <= y < sy and 0 <= z < sz for x, y, z, _, _ in vw["cells"]
    )
    assert (
        sum(sum(layer["materials"].values()) for layer in vw["layers"]) == rec["blocks"]
    )
    assert [p["block"] for p in vw["palette"]] == [m["block"] for m in rec["materials"]]
    machines = vw["ghosts"]["machines"]
    assert machines and all(m["ref"].startswith("mte:") for m in machines)
    cells = {(x, y, z) for x, y, z, _, _ in vw["cells"]}
    for k in vw["ghosts"]["keepouts"]:  # no shell block inside a keep-out
        lo, hi = k["lo"], k["hi"]
        assert not any(
            lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1] and lo[2] <= z <= hi[2]
            for x, y, z in cells
        )


# --- the router and the asker -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "shell_on", "intent"),
    [
        ("make the roof steeper", True, router.SHELL_REVISE),
        ("taller, and no windows on the north wall", True, router.SHELL_REVISE),
        ("Can you make the building taller?", True, router.SHELL_REVISE),
        ("a bit steeper please", True, router.SHELL_REVISE),
        ("swap the windows for reinforced glass", True, router.SHELL_REVISE),
        ("undo", True, router.SHELL_UNDO),
        ("go back to the previous version", True, router.SHELL_UNDO),
        # a new one is asked for: build_request, never a revision
        ("design another shell around my line", True, "handoff"),
        ("start over and design a new building", True, "handoff"),
        ("design a shell around the steel line", True, "handoff"),
        ("design a shell around my EBF plan with no windows", True, "handoff"),
        ("make a shell for plan p-1 with a steeper roof", True, "handoff"),
        ("make a gothic theme with no lights", True, "handoff"),
        # other phrasings of WO-10.7's creation rule (design|make|build|draw|wrap|put ...
        # shell|building|facade) and of "a shell around X": new ones, never a revision
        ("wrap the EBF in a shell with no windows", True, "handoff"),
        ("wrap my EBF in a building with a steeper roof", True, "handoff"),
        ("put the ebf in a building with no windows", True, "handoff"),
        ("build the ebf a shell with a steeper roof", True, "handoff"),
        ("make the EBF a building with no windows", True, "handoff"),
        ("design the shell for the ebf with no windows", True, "handoff"),
        ("design shells with steeper roofs", True, "handoff"),
        # no creation verb: routed as without a shell (WO-10.7's rule), never a revision
        ("I want a shell around the EBF with no windows", True, "general"),
        ("a shell around the cleanroom with a steeper roof please", True, "general"),
        ("refine the foundry theme with fewer lamps", True, "handoff"),
        # the same words without a shell go the same way
        ("wrap the EBF in a shell with no windows", False, "handoff"),
        ("build the ebf a shell with a steeper roof", False, "handoff"),
        # a new part ("a ... wall") or a part of something else is not this shell
        ("build a taller wall", True, "general"),
        ("add more lamps to the nether portal", True, "general"),
        ("add a wall of solar panels", True, "general"),
        ("build a taller wall", False, "general"),
        ("add more lamps to the nether portal", False, "general"),
        # the shell itself made more, or a place on it, is still a revision
        ("build the building higher", True, router.SHELL_REVISE),
        ("make the shell a bit taller", True, router.SHELL_REVISE),
        ("add more lights to the roof", True, router.SHELL_REVISE),
        ("no windows on the left side", True, router.SHELL_REVISE),
        # the change word's object is a part of the building, not something else
        ("no windows on the north wall", True, router.SHELL_REVISE),
        ("add more lights", True, router.SHELL_REVISE),
        ("make the shell taller", True, router.SHELL_REVISE),
        ("I need more power for the building", True, "general"),
        ("add more lights to my base", True, "general"),
        ("remove the walls in plan x", True, "general"),
        # questions and other things are not changes
        ("which roof is steeper", True, "general"),
        ("lower the tier", True, "general"),
        ("recipe for glass", True, router.RECIPES),
        ("how do I make a turtle shell", True, router.RECIPE),
        # without a shell nothing is revised
        ("make the roof steeper", False, "general"),
        ("undo", False, "general"),
    ],
)
def test_the_router_decides_revise_or_new(
    text: str, shell_on: bool, intent: str
) -> None:
    assert router.route(text, shell=shell_on).intent == intent


def asker(home: Path, *, ai: bool) -> tuple[ask.Asker, HandoffRecorder]:
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    deep = HandoffRecorder()
    mode = AiMode()
    mode.key_stored = ai
    return ask.Asker(reg, deep, None, ai=mode, home=home), deep


def test_the_asker_hands_a_revision_to_the_deep_lane_naming_the_design(
    conn: sqlite3.Connection, home: Path
) -> None:
    ds, _ = first(conn, home)
    a, deep = asker(home, ai=True)
    reply = a.ask("make the roof steeper")
    assert reply.lane == router.DEEP and reply.intent == router.SHELL_REVISE
    item = deep.recent()[-1]
    assert item.request == "make the roof steeper"
    assert ds in item.task and "build_revise" in item.task
    assert (
        len(versions.get(home, ds)["versions"]) == 1
    )  # the deep lane writes, not this


def test_with_no_ai_gos_revises_in_code_and_undo_is_instant(
    conn: sqlite3.Connection, home: Path
) -> None:
    a, deep = asker(home, ai=False)
    assert not versions.any_shell(home)  # the router's check reads no file
    assert a.ask("make the roof steeper").intent != router.SHELL_REVISE
    ds, v1 = first(conn, home)
    assert versions.any_shell(home)
    reply = a.ask("make the roof steeper")
    assert reply.lane == "no_ai" and "version 2" in reply.text, reply.text
    assert reply.card is not None and reply.card["type"] == "shell"
    assert not deep.recent()  # no model
    reply = a.ask("change the roof to gothic")
    assert "nothing changed" in reply.text, reply.text
    assert len(versions.get(home, ds)["versions"]) == 2
    reply = a.ask("undo")
    assert reply.intent == router.SHELL_UNDO and "version 1" in reply.text
    assert versions.get(home, ds)["current"] == v1


def test_a_new_shell_request_never_revises_the_existing_design(
    conn: sqlite3.Connection, home: Path
) -> None:
    """With a design in place, "wrap the EBF in a shell ..." asks for a new shell
    (build_request), so neither the code reviser (AI off) nor the architect's revise task
    (AI on) touches the existing design."""
    ds, v1 = first(conn, home)
    for words in (
        "wrap the EBF in a shell with no windows",
        "build the ebf a shell with a steeper roof",
    ):
        a, deep = asker(home, ai=False)
        reply = a.ask(words)
        assert reply.intent != router.SHELL_REVISE, reply.text
        a, deep = asker(home, ai=True)
        reply = a.ask(words)
        assert reply.intent != router.SHELL_REVISE, reply.text
        assert all("build_revise" not in item.task for item in deep.recent())
    d = versions.get(home, ds)
    assert len(d["versions"]) == 1 and d["current"] == v1


def test_the_workbench_names_the_design_it_revises(
    conn: sqlite3.Connection, home: Path
) -> None:
    """ "Ask the architect" revises the design picked in the workbench, not the one Chat made
    current last, and the button (not the router) makes any words a revision."""
    ds_a, _ = first(conn, home)
    made = shell_store.make(
        conn,
        home,
        VaultIO(home / "vault"),
        ["pl-steel"],
        theme_id="th-foundry",
        headroom=6,
    )
    ds_b = versions.start(home, made.build, asked="a taller one", by="procedural")["id"]
    versions.make_current(home, ds_a, versions.get(home, ds_a)["current"])
    assert versions.current_id(home) == ds_a
    a, deep = asker(home, ai=True)
    words = (
        "use brick instead of stone"  # no part word: the router alone wouldn't revise
    )
    assert router.route(words, shell=True).intent == router.GENERAL
    reply = a.ask(words, shell=ds_b)
    assert reply.lane == router.DEEP and reply.intent == router.SHELL_REVISE
    item = deep.recent()[-1]
    assert ds_b in item.task and ds_a not in item.task
    a, deep = asker(home, ai=False)
    reply = a.ask("make the roof steeper", shell=ds_b)
    assert reply.lane == "no_ai" and "version 2" in reply.text, reply.text
    assert len(versions.get(home, ds_b)["versions"]) == 2
    assert len(versions.get(home, ds_a)["versions"]) == 1
    reply = a.ask("make the roof steeper", shell="ds-000000000000")
    assert "no shell design" in reply.text and not deep.recent()


# --- the tool ---------------------------------------------------------------------------------


@pytest.fixture
def reg(home: Path) -> Any:  # noqa: ANN401
    return build_registry(access.db_path(home), vault=VaultIO(home / "vault"))


def test_build_revise_is_the_architects_and_deep_lane_only(reg: Any) -> None:  # noqa: ANN401
    assert "build_revise" in {s["name"] for s in reg.specs(DEEP)}
    assert "build_revise" not in {s["name"] for s in reg.specs(FAST)}
    main, agents = deep_config.tool_sets(reg)
    assert (
        "mcp__gos__build_revise" in agents["architect"]
        and "mcp__gos__build_revise" in main
    )
    assert "mcp__gos__build_revise" not in agents["planner"]
    tool = next(t for t in TOOLS if t.name == "build_revise")
    assert not tool.read_only and tool.lanes == frozenset({DEEP})
    # the locked-down shape: data only, no code, no world action
    props = set(tool.input_schema["properties"])
    assert props == {
        "design",
        "words",
        "design_ops",
        "theme",
        "max_blocks",
        "headroom",
        "entrance",  # a site build's door hint and steering boxes (D422), as data
        "zones",
        "save",
    }
    assert tool.input_schema["additionalProperties"] is False


def test_build_revise_looks_revises_and_refuses(
    reg: Any,  # noqa: ANN401
    conn: sqlite3.Connection,
    home: Path,
) -> None:
    ds, v1 = first(conn, home)
    before = builds_on_disk(home)
    look = reg.call("build_revise", {"words": "no lights", "save": False}, lane=DEEP)
    assert (
        not look.is_error
        and "current ops: " in look.text
        and "GOS's reviser:" in look.text
    )
    assert builds_on_disk(home) == before
    ops = json.loads(look.text.split("current ops: ", 1)[1].splitlines()[0])
    gable = [dict(o, style="gable") if o["op"] == "roof" else o for o in ops]
    for o in gable:
        if o["op"] == "roof":
            o.pop("parapet", None)
            o.pop("parapet_role", None)
    out = reg.call(
        "build_revise",
        {"words": "a gable roof", "design_ops": json.dumps(gable), "headroom": 12},
        lane=DEEP,
    )
    assert not out.is_error, out.text
    assert "version: v2 (current)" in out.text and f"revised: v1 {v1}" in out.text
    d = versions.get(home, ds)
    assert (
        d["versions"][-1]["by"] == "architect"
        and d["versions"][-1]["asked"] == "a gable roof"
    )
    bad = [*ops, {"op": "fill", "block": ts.CHEST, "lo": [0, 0, 0], "hi": [0, 0, 0]}]
    out = reg.call(
        "build_revise", {"words": "a chest", "design_ops": json.dumps(bad)}, lane=DEEP
    )
    assert out.is_error and "nothing was written" in out.text and "v2" in out.text
    assert len(versions.get(home, ds)["versions"]) == 2
    out = reg.call("build_revise", {"words": "no lights"}, lane=DEEP)  # GOS's reviser
    assert not out.is_error and "version: v3" in out.text
    assert reg.call("build_revise", {"words": "x"}, lane=FAST).is_error


# --- site builds (D422) ------------------------------------------------------------------------


def site_first(conn: sqlite3.Connection, home: Path) -> tuple[str, shell_store.Made]:
    """A procedural foundry site build around the steel line, started as a design."""
    made = shell_store.make(
        conn,
        home,
        VaultIO(home / "vault"),
        ["pl-steel"],
        theme_id="th-foundry",
        mode="site",
    )
    assert made.outcome.status == "accepted", made.outcome.reasons
    d = versions.start(home, made.build, asked="a foundry hall", by="procedural")
    return d["id"], made


def _spots(lay: dict[str, Any]) -> dict[str, tuple[Any, ...]]:
    return {
        p["id"]: (p["facing"], tuple(p["lo"]), tuple(p["hi"]))
        for p in lay["placements"]
    }


def _reloads(conn: sqlite3.Connection, home: Path, build: str) -> None:
    """A saved site version gives its own cells and layout again from its own files."""
    spec, out = shell_store.rebuild(conn, home, build)
    _, cells = shell_store.cells_of(home, build)
    assert cells == dict(out.cells)
    assert spec.layout == versions._layout_of(home, build)["id"]
    shell_store.check_layout(conn, home, shell_store.load(home, build))


def test_a_site_revision_fits_again_from_its_program_and_never_reads_structure(
    conn: sqlite3.Connection, home: Path
) -> None:
    ds, made = site_first(conn, home)

    def deny(action: int, table: str | None, *_: object) -> int:
        if action == sqlite3.SQLITE_READ and table == "structure":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(deny)
    try:
        got = rev(conn, home, "make the roof steeper")
    finally:
        conn.set_authorizer(None)
    assert got.saved and got.record is not None, got.reasons
    rec = got.record
    assert shell_store.site_mode(rec) and rec["fit"]["site"]["lo"] == [0, 0, 0]
    # the headroom grew for the gable: the site with it, its floor the parent's
    old = shell_store.load(home, made.build)["fit"]["site"]
    up = rec["headroom"] - made.record["headroom"]
    assert up > 0 and rec["fit"]["site"]["hi"] == [
        old["hi"][0],
        old["hi"][1] + up,
        old["hi"][2],
    ]
    where = shell_store.folder(home, got.build)
    assert sorted(p.name for p in where.iterdir()) == sorted(shell_store.SITE_FILES)
    assert (where / "program.json").read_bytes() == (
        shell_store.folder(home, made.build) / "program.json"
    ).read_bytes()
    lay = versions._layout_of(home, got.build)
    assert _spots(lay) == _spots(versions._layout_of(home, made.build))
    assert "fitted in the building" in " ".join(rec["notes"])
    _reloads(conn, home, got.build)
    # lower than the layout-first top: a site build has none; the fit is the floor
    versions.make_current(home, ds, made.build)  # the flat roof again
    low = rev(conn, home, "shorter by 4")
    assert low.saved, low.reasons
    assert low.record is not None and low.record["wall_top"] - 4 >= 0
    _reloads(conn, home, low.build)
    for k in range(1, 12):  # lower still, until the machines no longer fit
        too = rev(conn, home, f"shorter by {k}", save_it=False)
        if too.outcome.status == "refused":
            break
    assert "high; the tallest such floor is" in too.reasons[0], too.reasons
    assert "puts the roof on the floor" in rev(conn, home, "shorter by 30").reasons[0]
    with pytest.raises(versions.VersionError, match="for a site build"):
        ds2, _ = first(conn, home)
        rev(conn, home, "no lights", zones=[])
    assert versions.current_id(home) == ds2 != ds


def test_a_site_revision_keeps_the_places_that_still_fit(
    conn: sqlite3.Connection, home: Path
) -> None:
    _, made = site_first(conn, home)
    lay = versions._layout_of(home, made.build)
    was = _spots(lay)
    # a pillar through one machine: a fresh fit would move others; they stay put
    hit = next(p for p in lay["placements"] if p["id"] == "m4")
    x, z = hit["lo"][0], hit["lo"][2]
    ops = [
        *made.ops,
        {"op": "fill", "role": "pillar", "lo": [x, 0, z], "hi": [x, 11, z]},
    ]
    got = rev(conn, home, "a pillar", ops=ops, by="architect")
    assert got.saved and got.record is not None, got.reasons
    prior = got.record["fit"]["prior"]
    assert prior == {"build": made.build, "layout": lay["id"]}
    where = shell_store.folder(home, got.build)
    assert sorted(p.name for p in where.iterdir()) == sorted(
        (*shell_store.SITE_FILES, shell_store.PRIOR)
    )
    assert json.loads((where / shell_store.PRIOR).read_text("utf-8")) == lay
    now = _spots(versions._layout_of(home, got.build))
    assert now["m4"] != was["m4"]
    assert {k: v for k, v in now.items() if k != "m4"} == {
        k: v for k, v in was.items() if k != "m4"
    }
    _reloads(conn, home, got.build)  # fitted again with its prior, the same id


def test_refit_fits_todays_machines_into_the_same_building(
    conn: sqlite3.Connection, home: Path, tmp_path: Path
) -> None:
    from gregtec.build.worldreg import WorldRegistry

    _, made = site_first(conn, home)
    same = versions.refit(conn, home, made.build)
    assert not same.saved and "nothing changes" in same.reasons[0]
    plan_file = home / "data" / "plans" / "pl-steel.json"
    saved_plan = plan_file.read_text(encoding="utf-8")
    more = json.loads(saved_plan)
    more["nodes"][0]["count"] += 1
    plan_file.write_text(json.dumps(more), encoding="utf-8")
    rec = shell_store.load(home, made.build)
    names = {m["block"].split("@")[0] for m in rec["materials"]}
    world = WorldRegistry("lab", frozenset(names), frozenset(), "level.dat")
    with pytest.raises(shell.ShellError, match="gos shell refit " + made.build):
        shell_store.export(conn, home, made.build, world)
    got = versions.refit(conn, home, made.build)
    assert got.saved and got.version is not None, got.reasons
    v = got.version
    assert (v["n"], v["by"], v["asked"], v["cost_usd"]) == (2, "procedural", "refit", 0)
    assert got.ops == tuple(rec["ops"]) and got.parent == made.build
    new = shell_store.load(home, got.build)
    assert new["fit"]["site"] == rec["fit"]["site"]  # the same building
    assert new["fit"]["fit"]["of"] == rec["fit"]["fit"]["of"] + 1
    lay = versions._layout_of(home, got.build)
    assert len(lay["placements"]) == new["fit"]["fit"]["of"]
    _reloads(conn, home, got.build)
    assert shell_store.export(conn, home, got.build, world).paths
    # only the current version is refitted; a layout-first build never is
    with pytest.raises(versions.VersionError, match="not the current version"):
        versions.refit(conn, home, made.build)
    # far more machines: they no longer fit, nothing is written
    more["nodes"][0]["count"] += 40
    plan_file.write_text(json.dumps(more), encoding="utf-8")
    before = builds_on_disk(home)
    no = versions.refit(conn, home, got.build)
    assert not no.saved and no.reasons[-1] == "revise the building", no.reasons
    assert builds_on_disk(home) == before
    plan_file.write_text(saved_plan, encoding="utf-8")
    _, v1 = first(conn, home)
    with pytest.raises(versions.VersionError, match="layout-first"):
        versions.refit(conn, home, v1)


def test_gos_shell_design_site_and_refit(
    home: Path, tmp_path: Path, conn: sqlite3.Connection
) -> None:
    code, text = _gos(
        home, tmp_path, "design", "pl-steel", "--theme", "th-foundry", "--mode", "site"
    )
    assert code == 0 and "machines fitted in the building" in text, text
    build = text.split()[1]
    assert shell_store.site_mode(shell_store.load(home, build))
    code, text = _gos(home, tmp_path, "refit", build)
    assert code == 1 and "nothing changes" in text, text
    plan_file = home / "data" / "plans" / "pl-steel.json"
    more = json.loads(plan_file.read_text(encoding="utf-8"))
    more["nodes"][0]["count"] += 1
    plan_file.write_text(json.dumps(more), encoding="utf-8")
    code, text = _gos(home, tmp_path, "refit", build)
    assert code == 0 and "v2" in text and "refit" in text, text
    code, text = _gos(home, tmp_path, "refit", build)
    assert code == 2 and "not the current version" in text, text


def test_build_revise_takes_a_site_builds_entrance_and_zones(
    reg: Any,  # noqa: ANN401
    conn: sqlite3.Connection,
    home: Path,
) -> None:
    ds, made = site_first(conn, home)
    lay = versions._layout_of(home, made.build)
    door = next(e for e in lay["entrances"] if e["id"] == "e1")
    ent = {"side": door["side"], "at": [door["lo"][0], door["lo"][2]]}
    out = reg.call(
        "build_revise",
        {"words": "no lights", "entrance": json.dumps(ent), "zones": "[]"},
        lane=DEEP,
    )
    assert not out.is_error and "machines fitted in the building" in out.text, out.text
    rec = shell_store.load(home, versions.get(home, ds)["current"])
    assert rec["fit"]["entrance"] == ent and rec["fit"]["fit"]["entrance_from"] == (
        "answer"
    )
    bad = {"side": "north", "at": [1, 1]}
    out = reg.call(
        "build_revise",
        {"words": "no pillars", "entrance": json.dumps(bad)},
        lane=DEEP,
    )
    assert out.is_error and "nothing was written" in out.text, out.text
    first(conn, home)  # a layout-first design is current: no entrance there
    out = reg.call("build_revise", {"words": "no lights", "zones": "[]"}, lane=DEEP)
    assert out.is_error and "for a site build" in out.text


# --- gos shell --------------------------------------------------------------------------------


def _gos(home: Path, tmp_path: Path, *argv: str) -> tuple[int, str]:
    return ts._gos(home, tmp_path, *argv)  # type: ignore[no-any-return]


def test_gos_shell_revise_versions_undo_compare(
    home: Path, tmp_path: Path, conn: sqlite3.Connection
) -> None:
    code, text = _gos(home, tmp_path, "design", "pl-steel", "--theme", "th-foundry")
    assert code == 0 and "(design ds-" in text, text
    code, text = _gos(home, tmp_path, "revise", "make", "the", "roof", "steeper")
    assert code == 0 and "v2" in text and "roof: gable" in text, text
    code, text = _gos(home, tmp_path, "revise", "make", "it", "gothic")
    assert code == 1 and "REFUSED: nothing written" in text
    code, text = _gos(home, tmp_path, "versions")
    assert code == 0 and "v2 of 2" in text
    ds = text.split()[1]
    code, text = _gos(home, tmp_path, "versions", ds)
    assert code == 0 and text.count("\n") == 2 and "* v2" in text
    builds = [line.split("\t")[1] for line in text.splitlines()]
    code, text = _gos(home, tmp_path, "compare", *builds)
    assert code == 0 and "blocks" in text
    code, text = _gos(home, tmp_path, "undo")
    assert code == 0 and "v1" in text
    code, text = _gos(home, tmp_path, "current", ds, builds[1])
    assert code == 0 and builds[1] in text
    code, text = _gos(home, tmp_path, "versions", "ds-000000000000")
    assert code == 2


# --- the routes -------------------------------------------------------------------------------


def test_the_workbench_routes(home: Path) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api.app import Core, create_app
    from gregtec.api.security import Auth
    from gregtec.config import Config
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store

    store = Store(home / "data" / "core.sqlite")
    core = Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )
    admin = {"Authorization": f"Bearer {ADMIN}"}
    client_token = core.auth.issue_client_token("voice")

    def job(c: Any, body: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN401
        r = c.post("/api/shells/jobs", headers=admin, json=body)
        assert r.status_code == 200, r.text
        jid = r.json()["job"]["id"]
        for _ in range(400):
            j = c.get(f"/api/design-jobs/{jid}", headers=admin).json()["job"]
            if j["status"] != "running":
                return j  # type: ignore[no-any-return]
            time.sleep(0.05)
        raise AssertionError("the job didn't end")

    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            for path in ("/api/shells/designs", "/api/shells/compare?a=x&b=y"):
                assert c.get(path).status_code == 401
            assert c.post("/api/shells/jobs", json={}).status_code == 401
            if client_token:
                bearer = {"Authorization": f"Bearer {client_token}"}
                assert (
                    c.post("/api/shells/jobs", headers=bearer, json={}).status_code
                    == 403
                )
            assert c.get("/api/shells/designs", headers=admin).json() == {
                "designs": [],
                "current": None,
            }
            bad = c.post(
                "/api/shells/jobs", headers=admin, json={"design": {"plans": []}}
            )
            assert bad.status_code == 400
            bad = c.post(
                "/api/shells/jobs",
                headers=admin,
                json={"design": {"plans": ["pl-steel"], "max_blocks": 0}},
            )
            assert bad.status_code == 400 and "max_blocks" in bad.text
            assert (
                c.post(
                    "/api/shells/jobs",
                    headers=admin,
                    json={"revise": {"words": "taller"}},
                ).status_code
                == 400
            )  # no shell yet
            j = job(
                c,
                {
                    "design": {
                        "plans": ["pl-steel"],
                        "theme": "th-foundry",
                        "headroom": 6,
                    }
                },
            )
            assert j["status"] == "done" and j["kind"] == "shell", j
            ds, v1 = j["result"]["design"], j["result"]["build"]
            assert shell_store.load(home, v1)["headroom"] == 6
            j = job(c, {"revise": {"design": ds, "words": "make the roof steeper"}})
            assert j["status"] == "done" and "changes" in j["result"], j
            v2 = j["result"]["build"]
            j = job(c, {"revise": {"words": "make it gothic"}})
            assert j["result"]["refused"] and j["result"]["build"] == v2
            got = c.get(f"/api/shells/designs/{ds}", headers=admin).json()["design"]
            assert [v["build"] for v in got["versions"]] == [v1, v2]
            listed = c.get("/api/shells/designs", headers=admin).json()
            assert listed["current"] == ds and listed["designs"][0]["versions"] == 2
            cmp = c.get(f"/api/shells/compare?a={v1}&b={v2}", headers=admin).json()
            assert cmp["a"]["build"] == v1 and cmp["materials"]
            view = c.get(f"/api/shells/{v2}/view", headers=admin).json()
            assert view["build"] == v2 and view["cells"] and view["ghosts"]["machines"]
            assert (
                c.get("/api/shells/sh-000000000000/view", headers=admin).status_code
                == 404
            )
            r = c.post(
                f"/api/shells/designs/{ds}/current", headers=admin, json={"build": v1}
            )
            assert r.status_code == 200 and r.json()["design"]["current"] == v1
            r = c.post(
                f"/api/shells/designs/{ds}/current",
                headers=admin,
                json={"build": "sh-000000000000"},
            )
            assert r.status_code == 400
            assert (
                c.post(
                    f"/api/shells/designs/{ds}/current", json={"build": v1}
                ).status_code
                == 401
            )
            assert (
                c.get("/api/shells/designs/ds-000000000000", headers=admin).status_code
                == 404
            )
            # the shell card route is unchanged and still finds builds
            assert (
                c.get(f"/api/shells/{v2}", headers=admin).json()["card"]["build"] == v2
            )
            themes = c.get("/api/shells/themes", headers=admin).json()
            assert {t["id"] for t in themes["themes"]} >= {
                "th-foundry",
                "th-marble-works",
            }
            assert themes["limits"]["headroom"] == [4, 1, 16]
            # unchanged on purpose: nothing here writes a .schematic or touches a world
            assert not (home / shell_store.SCHEMATICS).exists()
    finally:
        store.close()


def test_ask_takes_the_workbench_design_and_checks_its_id(
    conn: sqlite3.Connection, home: Path
) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api.app import Core, create_app
    from gregtec.api.security import Auth
    from gregtec.config import Config
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store

    ds, _ = first(conn, home)
    store = Store(home / "data" / "core.sqlite")
    core = Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        asker=asker(home, ai=False)[0],
    )
    admin = {"Authorization": f"Bearer {ADMIN}"}
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            assert (
                c.post("/api/ask", json={"text": "x", "shell": ds}).status_code == 401
            )
            for bad in ("../ds", "ds-XYZ", 7, "sh-aaaaaaaaaaaa"):
                r = c.post("/api/ask", headers=admin, json={"text": "x", "shell": bad})
                assert r.status_code == 400, (bad, r.text)
            r = c.post(
                "/api/ask",
                headers=admin,
                json={"text": "make the roof steeper", "shell": ds},
            )
            assert r.status_code == 200, r.text
            assert r.json()["intent"] == router.SHELL_REVISE
            assert len(versions.get(home, ds)["versions"]) == 2
    finally:
        store.close()


def test_a_cancelled_shell_job_saves_nothing(home: Path) -> None:
    from gregtec.api import shell_routes
    from gregtec.planner import design_jobs, progress
    from gregtec.planner.designer import _LOCK

    jobs = design_jobs.DesignJobs(home, _LOCK)
    real = progress.stage
    gate = __import__("threading").Event()

    def slow(name: str, k: int | None = None, n: int | None = None) -> None:
        real(name, k, n)
        if name == "compose":
            gate.wait(5)
            real(name, k, n)  # the cancel is heard here, before the gate and saving

    title, work = shell_routes.shell_job(
        home, {"design": {"plans": ["pl-steel"], "theme": "th-foundry"}}
    )
    progress.stage = slow  # type: ignore[assignment]
    try:
        job = jobs.start("shell", title, work)
        for _ in range(100):
            if jobs.get(job.id).stage == "compose":  # type: ignore[union-attr]
                break
            time.sleep(0.02)
        with pytest.raises(design_jobs.Busy):
            jobs.start("shell", title, work)  # one shell job at a time
        jobs.cancel(job.id)
        gate.set()
        jobs.wait(job.id, 10)
    finally:
        progress.stage = real  # type: ignore[assignment]
    assert jobs.get(job.id).status == "cancelled"  # type: ignore[union-attr]
    assert (
        not (home / shell_store.ROOT).exists() and not (home / versions.ROOT).exists()
    )


# --- the revise eval (gos eval shell-revise, D368) --------------------------------------------


def test_the_revise_eval_passes_with_gos_reviser_and_its_scorer_bites(
    conn: sqlite3.Connection, home: Path
) -> None:
    from gregtec.build import shell_eval

    cases = shell_eval.load_cases()
    assert len(cases) >= 5 and all(c.followup and c.expect for c in cases)
    text = shell_eval.CASES.read_text(encoding="utf-8")
    assert "mte:" not in text and "r:" not in text  # no game number or id in the cases
    vault = VaultIO(home / "vault")
    out = io.StringIO()
    rows = shell_eval.run(
        conn, home, vault, ["pl-steel"], cases, shell_eval.gos_reviser(conn, vault), out
    )
    assert [r["result"] for r in rows] == ["PASS"] * len(cases), out.getvalue()
    assert f"{len(cases)} of {len(cases)} cases pass" in out.getvalue()

    # a reviser that also changes what wasn't asked fails "kept"
    def sloppy(h: Path, ds: str, words: str) -> None:
        versions.revise(conn, h, vault, ds, f"{words}, and no plinth")

    row = shell_eval.run_case(conn, home, vault, ["pl-steel"], cases[0], sloppy)
    assert row["gate"] == "pass" and row["kept"] == "fail" and row["result"] == "FAIL"

    # one that changes the wrong thing fails "asked"
    def wrong(h: Path, ds: str, words: str) -> None:
        versions.revise(conn, h, vault, ds, "fewer pillars")

    row = shell_eval.run_case(conn, home, vault, ["pl-steel"], cases[0], wrong)
    assert row["asked"] == "fail" and row["result"] == "FAIL"

    # one that drops windows from another side too fails "asked" (the other sides are kept)
    def greedy(h: Path, ds: str, words: str) -> None:
        versions.revise(conn, h, vault, ds, "no windows on the north and east walls")

    north = next(c for c in cases if c.id == "no-north-windows")
    row = shell_eval.run_case(conn, home, vault, ["pl-steel"], north, greedy)
    assert row["asked"] == "fail" and "not" in row["why"], row
    # one that makes no new version fails the gate column
    row = shell_eval.run_case(
        conn, home, vault, ["pl-steel"], cases[0], lambda h, ds, w: None
    )
    assert row["gate"] == "refused" and row["result"] == "FAIL"


def test_gos_eval_shell_revise_fake_runs_on_a_scratch_copy(
    home: Path, tmp_path: Path
) -> None:
    import argparse

    from gregtec.build import shell_eval_cli

    cfg = tmp_path / "gos.toml"
    cfg.write_text(
        f"[paths]\ngos_home = {json.dumps(home.as_posix())}\n", encoding="utf-8"
    )
    args = argparse.Namespace(
        plans=["pl-steel"], fake=True, cases=None, limit=2, config=cfg
    )
    out = io.StringIO()
    assert shell_eval_cli.run(args, out) == 0, out.getvalue()
    assert "Result: PASS" in out.getvalue()
    assert not (home / shell_store.ROOT).exists()  # his own shells untouched
    assert (home / shell_eval_cli.SCRATCH / shell_store.ROOT).is_dir()
    args.plans = ["pl-nope"]
    assert shell_eval_cli.run(args, io.StringIO()) == 2
