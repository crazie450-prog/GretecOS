"""WO-10.17 (D424): plan-free buildings, and the site factor on plans (D425's big cases).

A plan-free building names no plans: its SITE is sized from the roofed floor Clayton asks for
(``area``), and it is checked by the floor check (:func:`gregtec.build.fit.floor_check`: that
many roofed columns joined to a ground door) instead of a machine fit. On WO-10.7's fixtures
(made-up blocks, themes and plans; no game data):

- the plot from the area (the design's table), its bounds and refusals;
- GOS's procedural plan-free building passes the floor check across areas and site factors;
- the store: saved with no plans, no program and an empty layout; rebuilt, viewed and exported
  from its own files, its paste origin at the door; refusals for every misuse;
- versions: revised (GOS's reviser and an answer's operations) keeping the area and plot;
  never refitted;
- the brief (prompt v7) and the fake designers on a plan-free brief; the layout and site
  prompts are unchanged, byte for byte;
- build_request, ``gos shell design --mode free``, the workbench's job and POST /api/shells;
- the site factor on plans: a bigger site, replayed as it was saved.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec import cards
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.registry import DEEP
from gregtec.build import fit, layout, schematic, shell, shell_brief, shell_store
from gregtec.build import shell_fakes as fakes
from gregtec.build import shell_versions as versions
from gregtec.build.worldreg import WorldRegistry
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
OPTS = layout.Options()


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


def free(
    conn: sqlite3.Connection,
    home: Path,
    area: int = 400,
    **kw: Any,  # noqa: ANN401
) -> shell_store.Made:
    kw.setdefault("theme_id", "th-foundry")
    return shell_store.make(
        conn, home, VaultIO(home / "vault"), [], mode="free", area=area, **kw
    )


def world_of(rec: dict[str, Any]) -> WorldRegistry:
    names = {m["block"].split("@")[0] for m in rec["materials"]}
    return WorldRegistry("lab", frozenset(names), frozenset(), "level.dat")


# --- the plot ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("area", "factor", "headroom", "size"),
    [
        (25, 1.25, 4, (9, 16, 9)),
        (1_500, 2.0, 4, (55, 16, 55)),
        (2_000, 2.0, 12, (64, 24, 64)),
        (3_000, 2.0, 16, (78, 28, 78)),
        (6_000, 2.0, 4, (110, 16, 110)),
    ],
)
def test_the_plot_from_the_area(
    area: int, factor: float, headroom: int, size: tuple[int, int, int]
) -> None:
    box = shell.site_for_area(area, OPTS, headroom, factor)
    assert box.lo == (0, 0, 0) and box.size == size


def test_the_plot_and_the_area_have_limits() -> None:
    with pytest.raises(shell.ShellError) as got:
        shell.site_for_area(6_000, OPTS, 4, 3.0)
    assert str(got.value) == (
        "6,000 blocks of floor at site factor 3.0 needs a 135-wide plot; the most is 128: "
        "lower the factor (at most 2.7 for this area) or the area"
    )
    assert shell.site_for_area(6_000, OPTS, 4, 2.7).size[0] == 128
    for bad in (24, 6_001, 1.5, True, "100"):
        with pytest.raises(shell.ShellError, match="the area must be a whole number"):
            shell.site_for_area(bad, OPTS)  # type: ignore[arg-type]
    for factor in (1.2, 6.1, float("nan")):
        with pytest.raises(shell.ShellError, match="the site factor must be from"):
            shell.site_for_area(100, OPTS, 4, factor)
    need = shell.need_for_area(1_500, OPTS)
    assert need == {
        "door_height": 3,
        "aisle_height": 5,
        "cap_height": 5,
        "required": 4_500,
        "keepout_volume": 0,
        "floor_area": 1_500,
        "floor_required": 1_500,
        "tallest": [0, 0, 0],
        "widest": [0, 0, 0],
    }


@pytest.mark.parametrize("area", [25, 100, 1_500, 6_000])
def test_gos_procedural_plan_free_building_always_holds_its_floor(
    conn: sqlite3.Connection, home: Path, area: int
) -> None:
    most = min(int(shell.SITE_MAX_SIDE**2 * 10 / area) / 10, shell.SITE_FACTORS[1])
    for factor in (1.25, 2.0, most):
        made = free(
            conn, home, area, site_factor=factor, max_blocks=40_000, save_it=False
        )
        assert made.outcome.status == "accepted", (factor, made.outcome.reasons)
        assert made.fit is not None and made.fit["fit"]["floor"] >= area
        st = made.fit["fit"]
        assert st["door"]["side"] == "west" and st["unjoined"] == 0
        assert made.spec.origin == (0, 0, st["door"]["at"][1])


def test_the_build_id_names_the_area_and_the_site(conn: sqlite3.Connection) -> None:
    sw = shell.swatches_of(ts.foundry())
    a = shell.spec_for_area(400, shell.site_for_area(400), sw, theme=ts.foundry())
    ops = shell.procedural(a, fit=True)
    assert shell.build_id(a, ops) == shell.build_id(a, ops)
    b = shell.spec_for_area(401, a.site, sw, theme=ts.foundry())  # type: ignore[arg-type]
    c = shell.spec_for_area(
        400, shell.site_for_area(400, None, None, 3.0), sw, theme=ts.foundry()
    )
    ids = {shell.build_id(s, ops) for s in (a, b, c)}
    assert len(ids) == 3
    door = {"side": "west", "at": [0, 14]}
    assert shell.build_id(a, ops, entrance=door) != shell.build_id(a, ops)
    with pytest.raises(shell.ShellError, match="composes on a site or plan-free"):
        shell.procedural(a)


def test_the_script_facts_of_a_plan_free_building() -> None:
    sw = shell.swatches_of(ts.foundry())
    spec = shell.spec_for_area(400, shell.site_for_area(400), sw)
    facts = shell.script_facts(spec)
    assert "program" not in facts
    assert facts["site"] == spec.site.to_json()  # type: ignore[union-attr]
    assert facts["area"] == 400 and facts["floor_y"] == 0
    assert facts["need"]["floor_required"] == 400
    assert facts["doorways"] == [] and facts["keep_clear"] == []
    assert spec.plans == () and spec.program == () and spec.clear == ()


def test_the_prompts_hall_fragment_holds_the_floor(conn: sqlite3.Connection) -> None:
    """free_prompt's growing hall, run as a script on a plan-free spec, passes the check."""
    text = shell_brief.free_prompt()
    start = text.index('top, x1, z1 = NEED["cap_height"]')
    end = text.index("# the door\n", start)
    fragment = text[start:end]
    sw = shell.swatches_of(ts.foundry())
    spec = shell.spec_for_area(300, shell.site_for_area(300), sw)
    out = shell.design(conn, spec, [{"op": "script", "script": fragment}], "architect")
    assert out.ok, out.reasons
    assert spec.site is not None
    got = fit.floor_check(out.cells, spec.site, 300)
    assert got.ok, got.reasons


# --- the store ---------------------------------------------------------------------------------


def test_a_plan_free_building_is_saved_rebuilt_viewed_and_exported(
    conn: sqlite3.Connection, home: Path
) -> None:
    made = free(conn, home, 400, prompt="an empty marble hall")
    assert made.outcome.status == "accepted" and made.saved is not None
    assert sorted(p.name for p in made.saved.iterdir()) == sorted(shell_store.FILES)
    assert json.loads((made.saved / "layout.json").read_text("utf-8")) == {}
    rec = shell_store.load(home, made.build)
    assert rec["plans"] == [] and rec["layout"] == ""
    assert shell_store.free_mode(rec) and not shell_store.site_mode(rec)
    info = rec["fit"]
    assert info["mode"] == "free" and info["area"] == 400 and info["zones"] == []
    assert info["site_factor"] == 2.0 and info["site"]["hi"] == [28, 15, 28]
    st = info["fit"]
    assert st["floor"] == 27 * 27 + 3 and st["need_floor"] == 400
    assert rec["origin"] == {
        "entrance": "e1",
        "side": "west",
        "cell": [0, 0, st["door"]["at"][1]],
    }
    line = f"roofed floor {27 * 27 + 3:,} of ≥400, joined to the west door (spare 83%)"
    assert line in rec["notes"]
    assert any("a plan-free building: no machines" in n for n in rec["notes"])
    assert shell_store.builds(home)[0]["area"] == 400
    # replayed from its own files: the same cells and origin, no plans read
    spec, out = shell_store.rebuild(conn, home, made.build)
    assert (
        dict(out.cells) == dict(made.outcome.cells) and spec.origin == made.spec.origin
    )
    _, cells = shell_store.cells_of(home, made.build)
    assert cells == dict(out.cells)
    shell_store.check_layout(conn, home, rec)  # no plans: nothing changed under it
    view = versions.viewer(conn, home, made.build)
    assert view["ghosts"] == {"machines": [], "keepouts": []} and view["cells"]
    card = shell_store.card(home, made.build)
    assert cards.validate(card) == [] and card["ref"] is None and card["layout"] is None
    # the schematic's WEOffset is the shell's corner from the door's entrance cell
    ex = shell_store.export(conn, home, made.build, world_of(rec))
    got = ex.written.parts[0].schematic
    lo, o = spec.bounds.lo, spec.origin
    assert got.offset == (lo[0] - o[0], lo[1] - o[1], lo[2] - o[2])
    assert sum(got.used().values()) == rec["blocks"]
    shell_store.write(ex)
    assert schematic.read(ex.paths[0].read_bytes()).schematic.offset == got.offset
    # a tampered design whose door moved is refused at export, never written
    rec["ops"].append({"op": "fill", "role": "wall", "lo": [0, 0, 0], "hi": [0, 2, 28]})
    (made.saved / "design.json").write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(shell.ShellError, match="no longer passes|finds its door"):
        shell_store.export(conn, home, made.build, world_of(rec))


def test_plan_free_misuse_is_refused(conn: sqlite3.Connection, home: Path) -> None:
    vault = VaultIO(home / "vault")
    cases: list[tuple[dict[str, Any], str]] = [
        (
            {"plan_ids": ["pl-steel"], "mode": "free", "area": 400},
            "a plan-free building takes an area and no plans",
        ),
        ({"plan_ids": [], "mode": "free"}, "takes an area and no plans"),
        (
            {"plan_ids": [], "mode": "free", "area": 400, "zones": [{"kind": "open"}]},
            "a plan-free building has no zones (zones steer where machines go)",
        ),
        (
            {
                "plan_ids": [],
                "mode": "free",
                "area": 400,
                "layout_options": layout.Options(),
            },
            "no layout options",
        ),
        (
            {"plan_ids": ["pl-steel"], "mode": "site", "area": 400},
            "an area is only for a plan-free building",
        ),
        (
            {"plan_ids": ["pl-steel"], "site_factor": 3.0},
            "a site factor is for a site or plan-free build",
        ),
        ({"plan_ids": [], "mode": "site"}, "name 1 to 3 saved plans"),
        ({"plan_ids": [], "mode": "free", "area": 10}, "the area must be"),
        (
            {"plan_ids": [], "mode": "free", "area": 6_000, "site_factor": 6.0},
            "needs a 190-wide plot",
        ),
    ]
    before = set((home / shell_store.ROOT).glob("*"))
    for kw, why in cases:
        with pytest.raises(shell.ShellError, match=re.escape(why)):
            shell_store.make(conn, home, vault, theme_id="th-foundry", **kw)
    # too small for its area: refused with the floor check's numbers, nothing written
    small = [
        {"op": "walls", "role": "wall", "y": [0, 3], "lo": [0, 0, 0], "hi": [9, 3, 9]},
        {"op": "roof", "role": "roof", "lo": [0, 4, 0], "hi": [9, 4, 9]},
        {"op": "clear", "lo": [0, 0, 4], "hi": [0, 2, 6]},
    ]
    got = free(conn, home, 400, ops=small)
    assert got.outcome.status == "refused" and got.saved is None
    assert got.outcome.reasons == (
        (
            "the roofed floor joined to the west door has 67 columns at least 3 high; "
            "it needs 400: add at least 333"
        ),
    )
    assert set((home / shell_store.ROOT).glob("*")) == before


def test_an_answers_door_is_kept_and_named(
    conn: sqlite3.Connection, home: Path
) -> None:
    door = {"side": "west", "at": [0, 13]}
    made = free(conn, home, 400, entrance=door)
    assert made.outcome.ok and made.fit is not None
    assert made.fit["entrance"] == door and made.fit["fit"]["entrance_from"] == "answer"
    bad = free(conn, home, 400, entrance={"side": "north", "at": [5, 5]})
    assert bad.outcome.status == "refused"
    assert "entrance at [5,5] on the north" in bad.outcome.reasons[0]


# --- versions ----------------------------------------------------------------------------------


def test_a_plan_free_revision_keeps_its_area_and_plot(
    conn: sqlite3.Connection, home: Path
) -> None:
    made = free(conn, home, 400)
    ds = versions.start(home, made.build, asked="an empty hall", by="procedural")["id"]
    vault = VaultIO(home / "vault")
    got = versions.revise(conn, home, vault, ds, "make the roof steeper")
    assert got.saved and got.record is not None, got.reasons
    rec = got.record
    assert rec["fit"]["area"] == 400 and rec["fit"]["mode"] == "free"
    old = made.fit["site"]  # type: ignore[index]
    up = rec["headroom"] - made.record["headroom"]
    assert rec["fit"]["site"]["hi"] == [old["hi"][0], old["hi"][1] + up, old["hi"][2]]
    where = shell_store.folder(home, got.build)
    assert sorted(p.name for p in where.iterdir()) == sorted(shell_store.FILES)
    shell_store.rebuild(conn, home, got.build)
    # an answer's operations: a hall too small is refused with the floor check's reason
    small = [
        {"op": "walls", "role": "wall", "y": [0, 3], "lo": [0, 0, 0], "hi": [9, 3, 9]},
        {"op": "roof", "role": "roof", "lo": [0, 4, 0], "hi": [9, 4, 9]},
        {"op": "clear", "lo": [0, 0, 4], "hi": [0, 2, 6]},
    ]
    no = versions.revise(conn, home, vault, ds, "smaller", ops=small, by="architect")
    assert not no.saved and "it needs 400: add at least 333" in no.reasons[0]
    ok = versions.revise(
        conn, home, vault, ds, "no pillars", ops=[*made.ops[:-1]], by="architect"
    )
    assert not ok.saved and "no doorway" in ok.reasons[0]  # the door's clear is gone
    door = versions.revise(
        conn,
        home,
        vault,
        ds,
        "no lights",
        entrance={"side": "west", "at": [0, 13]},
    )
    assert door.saved and door.record is not None, door.reasons
    assert door.record["fit"]["entrance"] == {"side": "west", "at": [0, 13]}
    with pytest.raises(versions.VersionError, match="has no zones"):
        versions.revise(
            conn, home, vault, ds, "x", zones=[{"kind": "open", "lo": [0] * 3}]
        )
    with pytest.raises(versions.VersionError) as refit:
        versions.refit(conn, home, door.build)
    assert str(refit.value) == (
        f"{door.build} is plan-free: it has no machines to refit. Place them in the game "
        "yourself, or design on plans."
    )


# --- the brief and the fakes -------------------------------------------------------------------

GOLDEN = {  # the layout (v5) and site (v6) system texts at e9d48ae, before plan-free (D424)
    "layout": "f6b8d368dce293bee0859dfb6c03d580c006b2bb6c5efa02fda94e449446a36e",
    "site": "00569a1f4f2e4ae499c6114ecaa67d34d1a363725f2f5525c764d3a73ab3b8d1",
}


def test_the_plan_free_prompt_and_the_old_ones() -> None:
    for mode, digest in GOLDEN.items():
        text = shell_brief.system_prompt(mode)
        assert hashlib.sha256(text.encode()).hexdigest() == digest, mode
    assert shell_brief.PROMPT_VERSIONS == {"layout": 5, "site": 6, "free": 7}
    text = shell_brief.system_prompt("free")
    for word in ("PROGRAM", "aisle", "keep-out", "chase", "1.75"):
        assert word not in text, word
    assert "furnishes himself" in text and "AREA" in text and "covered()" in text
    assert shell_brief.schema("free") is shell_brief.SITE_SCHEMA
    answer = '{"script": "", "ops": [], "entrance": null, "zones": []}'
    assert shell_brief.read(answer, "free").zones == ()
    with pytest.raises(
        shell_brief.BriefError, match="a plan-free building has no zones"
    ):
        shell_brief.read(
            '{"ops": [], "zones": [{"kind": "open", "lo": [0,0,0], "hi": [1,1,1]}]}',
            "free",
        )


def test_the_plan_free_brief_and_its_repair(
    conn: sqlite3.Connection, home: Path
) -> None:
    made = free(conn, home, 400, save_it=False)
    rec = made.record
    names = shell_brief.names_of(conn, rec)
    brief = shell_brief.design_brief(rec, {}, names, "an empty marble hall")
    need = (
        "NEED: roofed floor >= 400 columns at least 3 high, joined to a ground door 3 "
        "wide x 3 high with a straight way out of SITE."
    )
    assert need in brief.splitlines()
    assert "PROGRAM" not in brief and "Services" not in brief
    facts = json.loads(brief.splitlines()[-1].removeprefix("facts: "))
    assert facts["mode"] == "free" and facts["area"] == 400 and facts["doorways"] == []
    again = shell_brief.repair_brief(brief, "{}", "too small")
    assert again.splitlines()[-1] == need
    saved = free(conn, home, 400)
    rev = shell_brief.revise_brief(
        shell_store.load(home, saved.build), {}, names, "taller"
    )
    assert "current entrance: null" in rev and "current zones" not in rev
    assert "checks the revised building's floor again" in rev


@pytest.mark.parametrize(
    ("model", "rounds"),
    [
        (fakes.PLAIN, 1),
        (fakes.ORNATE, 1),
        (fakes.TOWER, 1),
        (fakes.SLOPPY, 2),
        (fakes.CRAMPED, 0),
    ],
)
def test_the_fakes_on_a_plan_free_brief(
    conn: sqlite3.Connection, home: Path, model: str, rounds: int
) -> None:
    made = free(conn, home, 400, save_it=False)
    rec = made.record
    brief = shell_brief.design_brief(
        rec, {}, shell_brief.names_of(conn, rec), "an empty hall"
    )
    designer = fakes.FakeDesigner(model)
    system, user = shell_brief.system_prompt("free"), brief
    passed = 0
    for n in range(1, 4):
        text = designer.complete(system, user, shell_brief.schema("free"), 16_000).text
        try:
            said = shell_brief.read(text, "free")
            got = free(conn, home, 400, ops=said.ops, save_it=False)
            why = None if got.outcome.ok else " | ".join(got.outcome.reasons)
        except shell_brief.BriefError as exc:
            why = str(exc)
        if why is None:
            passed = n
            break
        user = shell_brief.repair_brief(brief, text, why)
    assert passed == rounds, (model, why)
    if model == fakes.CRAMPED:
        assert why is not None and "it needs 400: add at least" in why


def test_a_fakes_revision_of_a_plan_free_building(
    conn: sqlite3.Connection, home: Path
) -> None:
    made = free(conn, home, 400)
    rec = shell_store.load(home, made.build)
    brief = shell_brief.revise_brief(
        rec, {}, shell_brief.names_of(conn, rec), "no lights"
    )
    text = fakes.FakeDesigner(fakes.PLAIN).complete(
        shell_brief.system_prompt("free"), brief, shell_brief.schema("free"), 16_000
    )
    said = shell_brief.read(text.text, "free")
    assert said.entrance is None and said.zones == ()
    assert not any(o.get("op") == "lights" for o in said.ops)


# --- build_request, the CLI and the routes -----------------------------------------------------


def test_build_request_takes_a_plan_free_building(home: Path) -> None:
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    out = reg.call(
        "build_request",
        {"mode": "free", "area": 400, "theme": "th-foundry", "save": False},
        lane=DEEP,
    )
    assert not out.is_error, out.text
    assert "free: a plan-free building, no machines" in out.text
    assert "roofed floor >= 400 columns at least 3 high" in out.text
    assert "roofed floor 732 of ≥400, joined to the west door" in out.text
    saved = reg.call(
        "build_request",
        {"mode": "free", "area": 400, "theme": "th-foundry", "site_factor": 3.0},
        lane=DEEP,
    )
    assert not saved.is_error and "data/build/shells/sh-" in saved.text, saved.text
    both = reg.call(
        "build_request",
        {"mode": "free", "area": 400, "theme": "th-foundry", "plans": "pl-steel"},
        lane=DEEP,
    )
    assert both.is_error and "takes an area and no plans" in both.text
    none = reg.call("build_request", {"theme": "th-foundry"}, lane=DEEP)
    assert none.is_error and "name 1 to 3 saved plans" in none.text
    small = reg.call(
        "build_request", {"mode": "free", "area": 10, "theme": "th-foundry"}, lane=DEEP
    )
    assert small.is_error and "area must be at least 25" in small.text
    site = reg.call(
        "build_request",
        {
            "plans": "pl-steel",
            "theme": "th-foundry",
            "mode": "site",
            "site_factor": 4.0,
        },
        lane=DEEP,
    )
    assert not site.is_error, site.text


def test_gos_shell_design_free(home: Path, tmp_path: Path) -> None:
    gos = ts._gos
    code, text = gos(
        home,
        tmp_path,
        "design",
        "--mode",
        "free",
        "--area",
        "400",
        "--theme",
        "th-foundry",
    )
    assert code == 0 and "plan-free, ≥400 floor" in text, text
    build = text.split()[1]
    code, text = gos(home, tmp_path, "list")
    assert code == 0 and f"{build}\t" in text and "plan-free, ≥400 floor" in text
    code, text = gos(home, tmp_path, "show", build)
    assert code == 0 and "roofed floor 732 of ≥400" in text
    code, text = gos(home, tmp_path, "refit", build)
    assert code == 2 and "is plan-free: it has no machines to refit" in text
    code, text = gos(
        home,
        tmp_path,
        "design",
        "--mode",
        "free",
        "--area",
        "400",
        "--theme",
        "th-foundry",
        "--entrance",
        "west:0,17",
        "--site-factor",
        "3.0",
    )
    assert code == 0, text
    for argv, why in (
        (("design", "--mode", "free", "--theme", "th-foundry"), "needs --area"),
        (("design", "pl-steel", "--mode", "free", "--area", "400"), "no plans"),
        (("design", "pl-steel", "--area", "400"), "--area is only for --mode free"),
        (("design", "--mode", "free", "--area", "400", "--entrance", "w"), "side:x,z"),
        (("design", "--theme", "th-foundry"), "name 1 to 3 saved plans"),
    ):
        code, text = gos(home, tmp_path, *argv)
        assert code == 2 and why in text, (argv, text)


def test_the_routes_take_a_plan_free_building(home: Path) -> None:
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
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            for body, why in (
                ({"mode": "free", "plans": ["pl-steel"], "area": 400}, "no plans"),
                ({"mode": "free"}, "takes an area and no plans"),
                ({"mode": "free", "area": 10}, "the area must be"),
                ({"mode": "free", "area": 6_000, "site_factor": 6.0}, "190-wide"),
                ({"plans": ["pl-steel"], "area": 400}, "only for a plan-free"),
                ({"plans": ["pl-steel"], "site_factor": 3.0}, "a site factor is for"),
                ({"mode": "nope", "plans": ["pl-steel"]}, "mode is one of"),
            ):
                r = c.post("/api/shells/jobs", headers=admin, json={"design": body})
                assert r.status_code == 400 and why in r.text, (body, r.text)
            r = c.post(
                "/api/shells/jobs",
                headers=admin,
                json={"design": {"mode": "free", "area": 400, "theme": "th-foundry"}},
            )
            assert r.status_code == 200, r.text
            started = r.json()["job"]
            assert started["title"] == "Shell: a plan-free building (≥400 floor)"
            for _ in range(400):
                j = c.get(f"/api/design-jobs/{started['id']}", headers=admin).json()
                if j["job"]["status"] != "running":
                    break
                time.sleep(0.05)
            assert j["job"]["status"] == "done", j
            build = j["job"]["result"]["build"]
            assert shell_store.free_mode(shell_store.load(home, build))
            # the workbench reads its floor from the design and the viewer (stage 3)
            ds = j["job"]["result"]["design"]
            listed = c.get("/api/shells/designs", headers=admin).json()["designs"]
            assert [(d["id"], d["plans"], d["area"]) for d in listed] == [(ds, [], 400)]
            assert (
                c.get(f"/api/shells/designs/{ds}", headers=admin).json()["design"][
                    "area"
                ]
                == 400
            )
            view = c.get(f"/api/shells/{build}/view", headers=admin).json()
            assert view["area"] == 400 and view["ghosts"]["machines"] == []
            # the plot note: the server's own plot, never the dashboard's
            got = c.get("/api/shells/plot?area=400", headers=admin).json()
            assert got == {
                "area": 400,
                "factor": shell.SITE_FACTOR,
                "size": list(shell.site_for_area(400).size),
            }
            got = c.get(
                "/api/shells/plot?area=1500&factor=3&headroom=8", headers=admin
            ).json()
            assert got["size"] == list(shell.site_for_area(1500, None, 8, 3.0).size)
            for q, why in (
                ("area=10", "the area must be"),
                ("area=6000&factor=6", "190-wide"),
                ("area=400&factor=9", "the site factor must be"),
                ("area=400&headroom=99", "headroom must be"),
            ):
                r = c.get(f"/api/shells/plot?{q}", headers=admin)
                assert r.status_code == 400 and why in r.text, (q, r.text)
            themes = c.get("/api/shells/themes", headers=admin).json()
            assert themes["plot"] == {
                "area": list(shell.AREA),
                "site_factor": [shell.SITE_FACTOR, *shell.SITE_FACTORS],
            }
            made = c.post(
                "/api/shells",
                headers=admin,
                json={"mode": "free", "area": 500, "theme": "th-foundry"},
            )
            assert made.status_code == 200, made.text
            card = made.json()["card"]
            assert cards.validate(card) == [] and card["layout"] is None
            bad = c.post("/api/shells", headers=admin, json={"mode": "free"})
            assert bad.status_code == 400 and "takes an area" in bad.text
    finally:
        store.close()


# --- the site factor on plans (D425) -----------------------------------------------------------


def test_a_bigger_site_on_plans_is_replayed_as_saved(
    conn: sqlite3.Connection, home: Path
) -> None:
    vault = VaultIO(home / "vault")
    plain = shell_store.make(
        conn, home, vault, ["pl-steel"], theme_id="th-foundry", mode="site"
    )
    big = shell_store.make(
        conn,
        home,
        vault,
        ["pl-steel"],
        theme_id="th-foundry",
        mode="site",
        site_factor=6.0,
    )
    assert plain.outcome.ok and big.outcome.ok, big.outcome.reasons
    assert plain.fit is not None and big.fit is not None
    small_side = plain.fit["site"]["hi"][0] + 1
    big_side = big.fit["site"]["hi"][0] + 1
    assert big_side > small_side and big.build != plain.build
    rec = shell_store.load(home, big.build)
    spec, out = shell_store.rebuild(conn, home, big.build)
    assert len(out.cells) == rec["blocks"] and spec.site == big.spec.site
    shell_store.check_layout(conn, home, rec)
