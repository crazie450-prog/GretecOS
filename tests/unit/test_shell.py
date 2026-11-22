"""WO-10.7: the shell designer (plan 7.10, D316-D318) on fixtures.

Footprints come from WO-1.8's self-authored fixture (tests/fixtures/gtnh-extractor, made-up multis
9001-9003); the block catalog is a handful of made-up states (the starter themes' blocks, a chest
with a tile entity, GT's machine block, a meta above 15); the plans are WO-10.5's test plans. No
game data.

Every property is checked against the layout JSON with this file's own geometry, not the module's:
no cell inside a keep-out, corridor, chase or doorway, every cell inside the bounding volume and
outside the layout's own bounds' keep-outs, every state in the palette and none with a tile entity,
at most max_blocks.

- the procedural generator (the fallback, the No-AI path and the test baseline) on the fixture
  layouts and on random plans and options (hypothesis): always accepted as designed, never repaired;
- the validator on random designs (hypothesis): whatever it accepts or repairs holds every rule;
- the architect's path with a scripted fake model's designs: a good design accepted; a tile-entity
  block, GT's machine block, an unknown role or a malformed op refused; a small keep-out breach
  repaired, a large one refused; too many blocks refused; nothing written when refused;
- the mesh, the schematic (positions and WEOffset, the palette rule against a world), the store,
  ``gos shell``, the ``build_request`` tool and the card.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import shutil
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gregtec import cards, cli
from gregtec.agents import deep_config
from gregtec.agents.tools import build_registry, registry, shells
from gregtec.agents.tools.registry import DEEP, FAST
from gregtec.build import footprints as fp
from gregtec.build import layout, palette, schematic, shell, shell_cli, shell_store
from gregtec.build import themes as theme_lib
from gregtec.build.palette import Entry
from gregtec.build.worldreg import WorldRegistry
from gregtec.db import access
from gregtec.db import blocks as block_db
from gregtec.db import structures as structure_db
from gregtec.ingest import palette as ingest
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "vault-template"
DATASET = REPO / "tests" / "fixtures" / "gtnh-extractor"
HERE = Path(__file__).parent


def _module(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lay_tests = _module("_wo107_layout_tests", HERE / "test_build_layout.py")

Cell = tuple[int, int, int]


def state(block: str, meta: int = 0, *, has_te: bool = False) -> ingest.BlockState:
    return ingest.BlockState(
        block=block,
        meta=meta,
        item=block,
        item_meta=meta,
        name=f"{block.split(':')[1]} {meta}",
        unlocalized="tile.x",
        mod=block.split(":")[0],
        has_te=has_te,
        opaque=True,
        full_cube=True,
        normal_cube=True,
        standard_render=True,
        liquid=False,
        falls=False,
        light=0,
        light_opacity=255,
        tab="buildingBlocks",
        hidden=False,
        faces=(),
        errors=(),
    )


STATES = [
    *(state("gregtech:gt.blockconcretes", m) for m in (0, 3, 7)),
    state("gregtech:gt.blockcasings2", 0),
    state("gregtech:gt.blockcasings", 11),
    state("gregtech:gt.blockglass1", 0),
    state("gregtech:gt.blockglass1", 10),
    *(state("gregtech:gt.blockstones", m) for m in (3, 6, 7, 11, 15)),
    state("minecraft:stone_slab", 0),
    state("minecraft:glowstone", 0),
    state("minecraft:glass", 0),
    state("minecraft:quartz_block", 2),
    # the bake-off's own palettes (tests/evals/shell_bakeoff.tsv, WO-10.14)
    *(state("minecraft:stonebrick", m) for m in (0, 1, 3)),
    *(
        state(b, m)
        for b, m in (
            ("minecraft:stone", 0),
            ("minecraft:nether_brick", 0),
            ("minecraft:cobblestone", 0),
            ("minecraft:gold_block", 0),
            ("minecraft:log", 1),
            ("minecraft:wool", 14),
            ("minecraft:wooden_slab", 5),
            ("minecraft:stained_hardened_clay", 15),
        )
    ),
    *(state("minecraft:stone_slab", m) for m in (1, 5, 7)),
    *(state("minecraft:stained_glass", m) for m in (3, 11, 14)),
    *(state("minecraft:sandstone", m) for m in (0, 1, 2)),
    *(state("minecraft:planks", m) for m in (1, 5)),
    *(state("minecraft:quartz_block", m) for m in (0, 1)),
    state("minecraft:wool", 20),
    state("minecraft:chest", 0, has_te=True),
    state("gregtech:gt.blockmachines", 0, has_te=True),
]
CHEST = "minecraft:chest@0"
MACHINE = "gregtech:gt.blockmachines@0"


def make_home(tmp_path: Path, *plans: dict[str, Any]) -> Path:
    home = tmp_path / "home"
    shutil.copytree(TEMPLATE, home / "vault")
    db = access.db_path(home)
    db.parent.mkdir(parents=True)
    c = access.connect(db)
    try:
        c.execute(
            "INSERT INTO export_run(id, fingerprint, run_key) VALUES (1, '', 'run-a')"
        )
        block_db.write(c, 1, ingest.Palette(tuple(STATES), (), ()))
        c.commit()
        structure_db.load(c, DATASET)
    finally:
        c.close()
    (home / "data" / "plans").mkdir(parents=True)
    for p in plans:
        (home / "data" / "plans" / f"{p['id']}.json").write_text(
            json.dumps(p), encoding="utf-8"
        )
    return home


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return make_home(tmp_path, lay_tests.production(), lay_tests.power())


@pytest.fixture
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


_SHARED: list[tuple[Path, sqlite3.Connection]] = []


def shared(
    tmp_path_factory: pytest.TempPathFactory | None = None,
) -> sqlite3.Connection:
    """One catalog + structure DB for the property tests (hypothesis reruns them often)."""
    if not _SHARED:
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="gos-shell-"))
        h = make_home(root)
        _SHARED.append((root, access.connect(access.db_path(h), readonly=True)))
    return _SHARED[0][1]


def foundry() -> theme_lib.Theme:
    text = (TEMPLATE / "_gos" / "themes" / "th-foundry.md").read_text(encoding="utf-8")
    return theme_lib.parse(text, "th-foundry", "shipped")


def marble() -> theme_lib.Theme:
    text = (TEMPLATE / "_gos" / "themes" / "th-marble-works.md").read_text(
        encoding="utf-8"
    )
    return theme_lib.parse(text, "th-marble-works", "shipped")


def spec_for(
    conn: sqlite3.Connection,
    lay: Mapping[str, Any],
    theme: theme_lib.Theme | None = None,
    **kw: Any,  # noqa: ANN401
) -> shell.Spec:
    t = theme or foundry()
    sw = shell.swatches_of(t)
    shell.check_palette(conn, sw)
    return shell.spec_from_layout(lay, sw, theme=t, **kw)


# --- the rules, checked from the layout JSON alone ------------------------------------------


def _b(d: Mapping[str, Any]) -> tuple[Cell, Cell]:
    return tuple(d["lo"]), tuple(d["hi"])  # type: ignore[return-value]


def _in(c: Cell, box: tuple[Cell, Cell]) -> bool:
    return all(box[0][i] <= c[i] <= box[1][i] for i in range(3))


def door_box(e: Mapping[str, Any], outer: tuple[Cell, Cell]) -> tuple[Cell, Cell]:
    lo, hi = list(e["lo"]), list(e["hi"])
    side = e["side"]
    axis = 0 if side in ("west", "east") else 2
    if side in ("west", "north"):
        lo[axis], hi[axis] = outer[0][axis], e["lo"][axis] - 1
    else:
        lo[axis], hi[axis] = e["hi"][axis] + 1, outer[1][axis]
    return tuple(lo), tuple(hi)  # type: ignore[return-value]


def rules_hold(
    lay: Mapping[str, Any],
    cells: Mapping[Cell, Entry],
    allowed: set[Entry],
    max_blocks: int,
    headroom: int,
    relief: int = 0,
    interior_open: bool = False,
) -> None:
    lo, hi = lay["bounds"]["lo"], lay["bounds"]["hi"]
    m = 1 + relief  # the wall ring and the relief zone outside it (D386)
    outer = ((lo[0] - m, lo[1], lo[2] - m), (hi[0] + m, hi[1] + headroom, hi[2] + m))
    clear = [_b(v) for k in ("keepouts", "corridors", "chases") for v in lay[k]]
    clear += [door_box(e, outer) for e in lay["entrances"]]
    if (
        not interior_open
    ):  # before D389 the interior below the roof: walls in the ring only
        clear.append(_b(lay["bounds"]))
    tes = {Entry(s.block, s.meta) for s in STATES if s.has_te}
    assert 0 < len(cells) <= max_blocks
    for c, e in cells.items():
        assert _in(c, outer), c
        assert not any(_in(c, b) for b in clear), c
        assert e in allowed and e not in tes and e.meta <= 15, e
        assert e.block != palette.GT_MACHINES
    # the doorway is open and walls stand beside it
    for e in lay["entrances"]:
        d = door_box(e, outer)
        assert not any(_in(c, d) for c in cells)


def accepted(conn: sqlite3.Connection, spec: shell.Spec) -> shell.Outcome:
    out = shell.design(conn, spec, shell.procedural(spec), "procedural")
    assert out.status == "accepted", (out.reasons, out.repairs)
    return out


# --- the procedural generator ---------------------------------------------------------------


@pytest.mark.parametrize("theme", [foundry, marble])
def test_the_procedural_shell_on_the_fixture_layouts(
    conn: sqlite3.Connection,
    theme: Any,  # noqa: ANN401
) -> None:
    for plans in (
        [lay_tests.production()],
        [lay_tests.power()],
        [lay_tests.production(), lay_tests.power()],
    ):
        lay = layout.plan_layout(conn, plans)
        t = theme()
        spec = spec_for(conn, lay, t)
        out = accepted(conn, spec)
        rules_hold(
            lay,
            out.cells,
            spec.allowed(),
            spec.max_blocks,
            spec.headroom,
            spec.relief,
            spec.interior_open,
        )
        assert out.repairs == ()
        # walls all round, a roof over everything, lights in it
        roles = {out.roles[e] for e in out.cells.values()}
        assert {"wall", "roof", "light", "window"} <= roles
        b = (
            spec.walls
        )  # the roof covers the wall line (D386: the relief zone is outside)
        roof = {(c[0], c[2]) for c in out.cells if c[1] == spec.roof_y}
        assert (
            roof
            >= {
                (x, z)
                for x in range(b.lo[0], b.hi[0] + 1)
                for z in range(b.lo[2], b.hi[2] + 1)
            }
            or t.id == "th-marble-works"
        )  # marble's gable covers it in steps
        assert (
            shell.design(conn, spec, shell.procedural(spec), "procedural").cells
            == out.cells
        )


def test_the_procedural_shell_follows_the_theme_rules(conn: sqlite3.Connection) -> None:
    lay = layout.plan_layout(conn, [lay_tests.production()])
    spec = spec_for(conn, lay)
    ops = shell.procedural(spec)
    kinds = [o["op"] for o in ops]
    assert kinds[:3] == [
        "walls",
        "walls",
        "walls",
    ]  # walls, the plinth (trim), the eaves band
    win = next(o for o in ops if o["op"] == "windows")
    assert win["y"][1] == spec.wall_top - 1  # "high strip windows ... under the eaves"
    roof = next(o for o in ops if o["op"] == "roof")
    assert roof["style"] == "flat" and roof["edge_n"] == 1  # the stone slab edge
    assert kinds[-1] == "clear"  # the doorway
    steep = theme_lib.Theme(**{**foundry().__dict__, "roof": ("steep pitched roofs",)})
    gable = shell.procedural(spec_for(conn, lay, steep))
    short = min(spec.bounds.size[0], spec.bounds.size[2])
    style = next(o for o in gable if o["op"] == "roof")["style"]
    assert style == ("gable" if short <= 33 else "flat")


machines = lay_tests.machines


@st.composite
def plan_sets(draw: st.DrawFn) -> list[dict[str, Any]]:
    ps = draw(lay_tests.plans())
    if not any(
        n["count"] and n["machine"]["mte"] != lay_tests.UNKNOWN
        for p in ps
        for n in p["nodes"]
    ):
        ps[0]["nodes"].append(lay_tests.node("n99", 9001, 1))
    return ps


WINDOW_WORDS = st.sampled_from(
    ["high strip windows", "tall windows", "plain windows", "a clerestory", ""]
)
ROOF_WORDS = st.sampled_from(
    ["steep roofs", "flat roof with a parapet", "", "gable roof"]
)


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(plan_sets(), lay_tests.options, WINDOW_WORDS, ROOF_WORDS, st.integers(1, 6))
def test_the_procedural_shell_always_holds_every_rule(
    ps: list[dict[str, Any]],
    opts: layout.Options,
    window: str,
    roof: str,
    headroom: int,
) -> None:
    conn = shared()
    lay = layout.plan_layout(conn, ps, opts)
    t = theme_lib.Theme(
        **{**foundry().__dict__, "window": (window,) if window else (), "roof": (roof,)}
    )
    spec = spec_for(conn, lay, t, headroom=headroom, max_blocks=shell.MAX_BLOCKS)
    out = accepted(conn, spec)
    rules_hold(lay, out.cells, spec.allowed(), spec.max_blocks, headroom)


# --- the validator on random designs --------------------------------------------------------

ROLES = ["wall", "trim", "pillar", "accent", "roof", "window", "light", "floor"]


@st.composite
def designs(draw: st.DrawFn, bounds: tuple[Cell, Cell]) -> list[dict[str, Any]]:
    lo, hi = bounds
    coord = [st.integers(lo[i] - 3, hi[i] + 3) for i in range(3)]
    ops: list[dict[str, Any]] = []
    for _ in range(draw(st.integers(1, 6))):
        kind = draw(st.sampled_from([k for k in shell.OPS if k != "script"]))
        op: dict[str, Any] = {"op": kind}
        if kind != "clear":
            op["role"] = draw(st.sampled_from(ROLES))
        if kind in ("fill", "frame", "clear"):
            a = [draw(c) for c in coord]
            b = [draw(c) for c in coord]
            op["lo"] = [min(a[i], b[i]) for i in range(3)]
            op["hi"] = [max(a[i], b[i]) for i in range(3)]
        elif kind in ("walls", "pillars", "windows"):
            y0 = draw(st.integers(lo[1] - 1, hi[1]))
            op["y"] = [y0, draw(st.integers(y0, hi[1] + 1))]
            op["inset"] = draw(st.integers(0, 3))
        elif kind == "roof":
            op["style"] = draw(st.sampled_from(shell.ROOFS))
            op["y"] = draw(st.integers(hi[1] - 6, hi[1] + 1))
            if op["style"] in ("shed", "sawtooth"):
                op["sides"] = [draw(st.sampled_from(shell.SIDES))]
        # sometimes a volume of its own (WO-10.12)
        if kind in shell.VOLUME_OPS and draw(st.booleans()):
            a = [draw(c) for c in coord]
            b = [draw(c) for c in coord]
            op["lo"] = [min(a[i], b[i]) for i in range(3)]
            op["hi"] = [max(a[i], b[i]) for i in range(3)]
            op.pop("y", None)
        ops.append(op)
    return ops


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.data())
def test_whatever_the_validator_lets_through_holds_every_rule(
    data: st.DataObject,
) -> None:
    conn = shared()
    lay = layout.plan_layout(conn, [lay_tests.production()])
    t = marble()  # every role but floor
    spec = spec_for(conn, lay, t, max_blocks=data.draw(st.integers(50, 4000)))
    b = spec.bounds
    ops = data.draw(designs((b.lo, b.hi)))
    out = shell.design(conn, spec, ops, "architect")
    if out.ok:
        rules_hold(
            lay,
            out.cells,
            spec.allowed(),
            spec.max_blocks,
            spec.headroom,
            spec.relief,
            spec.interior_open,
        )
        f = shell.inspect(spec, out.cells)
        assert f.clean and (out.status == "repaired") == bool(out.repairs)
    else:
        assert out.reasons and not out.cells


@settings(max_examples=80, deadline=None)
@given(st.data())
def test_inspect_counts_like_a_brute_force_check(data: st.DataObject) -> None:
    conn = shared()
    lay = layout.plan_layout(conn, [lay_tests.production()])
    spec = spec_for(conn, lay, max_blocks=data.draw(st.integers(1, 300)))
    b = spec.bounds
    states = [*sorted(spec.allowed(), key=lambda e: e.key), Entry("minecraft:chest", 0)]
    cells = data.draw(
        st.dictionaries(
            st.tuples(*(st.integers(b.lo[i] - 2, b.hi[i] + 2) for i in range(3))),
            st.sampled_from(states),
            max_size=200,
        )
    )
    f = shell.inspect(spec, cells)
    boxes = [(v.id, (v.box.lo, v.box.hi)) for v in spec.clear]
    outside = [c for c in cells if not _in(c, (b.lo, b.hi))]
    inside = [c for c in cells if _in(c, (b.lo, b.hi))]
    hits: dict[str, int] = {}
    for c in inside:
        for vid, box in boxes:
            if _in(c, box):
                hits[vid] = hits.get(vid, 0) + 1
                break
    assert f.outside == len(outside) and f.blocks == len(inside)
    assert dict(f.breaches) == hits
    assert sum(f.foreign.values()) == sum(
        1 for c in inside if cells[c] not in spec.allowed()
    )
    assert f.over == max(0, len(inside) - spec.max_blocks)


# --- the architect's designs (a scripted fake model) ----------------------------------------


def good_design(spec: shell.Spec) -> list[dict[str, Any]]:
    """What a well-behaved architect sends: its own take on the vocabulary."""
    top = spec.wall_top
    ops: list[dict[str, Any]] = [
        {"op": "walls", "role": "wall", "y": [0, top]},
        {"op": "walls", "role": "trim", "y": [0, 0]},
        {"op": "pillars", "role": "pillar", "every": 6},
        {"op": "windows", "role": "window", "y": [1, 2], "every": 6, "width": 3},
        {
            "op": "roof",
            "role": "roof",
            "style": "flat",
            "edge": "roof",
            "edge_n": 1,
            "parapet": 1,
        },
        {"op": "lights", "role": "light", "every": 5},
    ]
    ops += [
        {"op": "clear", "lo": list(v.box.lo), "hi": list(v.box.hi)}
        for v in spec.doorways()
    ]
    return ops


@pytest.fixture
def lay(conn: sqlite3.Connection) -> dict[str, Any]:
    return layout.plan_layout(conn, [lay_tests.production()])


def test_a_good_design_is_accepted(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    spec = spec_for(conn, lay)
    out = shell.design(conn, spec, good_design(spec), "architect")
    assert out.status == "accepted" and out.repairs == ()
    rules_hold(
        lay,
        out.cells,
        spec.allowed(),
        spec.max_blocks,
        spec.headroom,
        spec.relief,
        spec.interior_open,
    )
    top = spec.roof_y + 1
    assert any(c[1] == top for c in out.cells)  # the parapet


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (
            {"op": "fill", "block": CHEST, "lo": [-1, 0, -1], "hi": [-1, 0, -1]},
            "has a tile entity",
        ),
        (
            {"op": "fill", "block": MACHINE, "lo": [-1, 1, -1], "hi": [-1, 1, -1]},
            "GOS never pastes machines",
        ),
        (
            {
                "op": "fill",
                "block": "minecraft:wool@20",
                "lo": [-1, 0, -1],
                "hi": [-1, 0, -1],
            },
            "worldedit-gtnh #30",
        ),
        (
            {
                "op": "fill",
                "block": "minecraft:glass@0",
                "lo": [-1, 0, -1],
                "hi": [-1, 0, -1],
            },
            "not in this build's palette",
        ),
        (
            {
                "op": "fill",
                "block": "nomod:nothing@0",
                "lo": [-1, 0, -1],
                "hi": [-1, 0, -1],
            },
            "not in the block catalog",
        ),
        ({"op": "walls", "role": "tower"}, "the palette has no 'tower' entry"),
        ({"op": "tower", "role": "wall"}, "'tower' is not one of"),
        ({"op": "walls", "role": "wall", "colour": "red"}, "unknown key(s) colour"),
        (
            {"op": "fill", "role": "wall", "lo": [0, 0], "hi": [1, 1, 1]},
            "lo must be [x, y, z]",
        ),
        (
            {"op": "fill", "role": "wall", "lo": [5, 0, 0], "hi": [1, 1, 1]},
            "lo must not be above hi",
        ),
        ({"op": "walls", "role": "wall", "role2": 1}, "unknown key"),
        (
            {"op": "walls", "role": "wall", "block": CHEST},
            "name a role or a block, not both",
        ),
    ],
)
def test_a_bad_design_is_refused_and_nothing_written(
    conn: sqlite3.Connection, lay: dict[str, Any], bad: dict[str, Any], why: str
) -> None:
    spec = spec_for(conn, lay)
    out = shell.design(conn, spec, [*good_design(spec), bad], "architect")
    assert out.status == "refused" and not out.cells
    assert why in " ".join(out.reasons), out.reasons


def test_a_small_breach_is_repaired_a_large_one_refused(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    spec = spec_for(conn, lay)
    k = next(v for v in spec.clear if v.kind == "keepout")
    # a pillar through the middle of a keep-out (its machine's footprint)
    mid = [(k.box.lo[i] + k.box.hi[i]) // 2 for i in range(3)]
    pillar = {
        "op": "fill",
        "role": "pillar",
        "lo": [mid[0], 0, mid[2]],
        "hi": [mid[0], 3, mid[2]],
    }
    out = shell.design(conn, spec, [*good_design(spec), pillar], "architect")
    assert out.status == "repaired"
    assert any(f"inside keepout {k.id}" in r for r in out.repairs), out.repairs
    rules_hold(
        lay,
        out.cells,
        spec.allowed(),
        spec.max_blocks,
        spec.headroom,
        spec.relief,
        spec.interior_open,
    )
    clean = shell.design(conn, spec, good_design(spec), "architect")
    assert out.cells == clean.cells  # only the breach went
    # blocks over the doorway and in a corridor are removed too
    door = spec.doorways()[0].box
    plug = {"op": "fill", "role": "wall", "lo": list(door.lo), "hi": list(door.hi)}
    out = shell.design(conn, spec, [*good_design(spec), plug], "architect")
    assert out.status == "repaired" and out.cells == clean.cells
    # filling the whole hall is far too much to repair
    i = spec.interior
    flood = {"op": "fill", "role": "wall", "lo": list(i.lo), "hi": list(i.hi)}
    out = shell.design(conn, spec, [*good_design(spec), flood], "architect")
    assert out.status == "refused" and "too far off to repair" in out.reasons[0]
    assert f"more than {round(shell.REPAIR_SHARE * 100)}%" in out.reasons[0]
    assert "{" not in out.reasons[0]  # QA 10-13: the share is formatted, not shown raw
    # a design that is mostly outside the bounding volume is refused, a little is clipped
    far = {"op": "fill", "role": "wall", "lo": [-40, 0, -40], "hi": [-20, 9, -20]}
    out = shell.design(conn, spec, [*good_design(spec), far], "architect")
    assert out.status == "refused"
    b = spec.bounds  # two cells past its west edge, one inside
    near = {
        "op": "fill",
        "role": "wall",
        "lo": [b.lo[0] - 2, 0, b.lo[2]],
        "hi": [b.lo[0], 0, b.lo[2]],
    }
    out = shell.design(conn, spec, [*good_design(spec), near], "architect")
    assert out.status == "repaired" and "outside the bounding volume" in out.repairs[0]


def test_a_clear_outside_the_bounds_places_nothing_and_is_not_counted(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    spec = spec_for(conn, lay)
    base = shell.procedural(spec)
    clean = accepted(conn, spec)
    b = spec.bounds
    # a big no-op clear west of the shell: still accepted as designed (review M1)
    far = {
        "op": "clear",
        "lo": [b.lo[0] - 11, 0, b.lo[2]],
        "hi": [b.lo[0] - 1, b.hi[1], b.hi[2] + 10],
    }
    # a few air cells just past the edge
    edge = {"op": "clear", "lo": [b.lo[0] - 1, 0, b.lo[2]], "hi": [b.lo[0] - 1, 0, 3]}
    for extra in (far, edge):
        out = shell.design(conn, spec, [*base, extra], "architect")
        assert out.status == "accepted" and out.repairs == (), (
            out.reasons,
            out.repairs,
        )
        assert out.cells == clean.cells
    assert shell.compile_ops(spec, [*base, far]).outside == 0


def test_the_interior_below_the_roof_is_never_the_shells(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    """With the interior closed (a build from before D389), cells in no keep-out, corridor or
    chase but inside the layout (above a short machine, between keep-outs) are the machines' and
    their runs' space: repaired away (D316, review M2)."""
    spec = spec_for(conn, lay, interior_open=False)
    i = spec.interior
    vols = [v.box for v in spec.clear if v.kind != "interior"]
    free = next(
        c for c in i.cells() if c[1] == i.hi[1] and not any(v.contains(c) for v in vols)
    )
    stub = {"op": "fill", "role": "pillar", "lo": list(free), "hi": list(free)}
    out = shell.design(conn, spec, [*good_design(spec), stub], "architect")
    assert out.status == "repaired" and free not in out.cells
    assert any("the layout's interior" in r for r in out.repairs), out.repairs
    rules_hold(
        lay,
        out.cells,
        spec.allowed(),
        spec.max_blocks,
        spec.headroom,
        spec.relief,
        spec.interior_open,
    )
    # walls set in from the ring are inside the interior too
    inset = {"op": "walls", "role": "wall", "y": [0, 1], "inset": 1}
    out = shell.design(conn, spec, [*good_design(spec), inset], "architect")
    assert out.ok and not any(i.contains(c) for c in out.cells)


def test_too_many_blocks_is_refused(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    spec = spec_for(conn, lay)
    n = len(shell.design(conn, spec, good_design(spec), "architect").cells)
    tight = spec_for(conn, lay, max_blocks=n - 1)
    out = shell.design(conn, tight, good_design(tight), "architect")
    assert out.status == "refused" and "more than max_blocks" in out.reasons[0]
    exact = spec_for(conn, lay, max_blocks=n)
    assert (
        shell.design(conn, exact, good_design(exact), "architect").status == "accepted"
    )
    # a single huge op is refused before it fills memory
    tiny = spec_for(conn, lay, max_blocks=10)
    huge = [{"op": "fill", "role": "wall", "lo": [-1, 0, -1], "hi": [-1, 9, 40]}]
    assert (
        "more than max_blocks" in shell.design(conn, tiny, huge, "architect").reasons[0]
    )


def test_an_empty_or_malformed_design_is_refused(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    spec = spec_for(conn, lay)
    for ops, why in [
        ([], "non-empty list"),
        ([{"op": "clear", "lo": [0, 0, 0], "hi": [1, 1, 1]}], "no blocks"),
        ([{"op": "walls", "role": "wall"}] * (shell.MAX_OPS + 1), "at most"),
        (
            [{"op": "windows", "role": "window", "every": 3, "width": 3}],
            "width must be",
        ),
    ]:
        out = shell.design(conn, spec, ops, "architect")
        assert out.status == "refused" and why in out.reasons[0], out.reasons


def test_the_palette_rule_refuses_a_theme_or_extra_entry(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    for extra, why in [
        ([("trim", CHEST)], "has a tile entity"),
        ([("accent", MACHINE)], "GOS never pastes machines"),
        ([("accent", "minecraft:wool@20")], "worldedit-gtnh #30"),
        ([("tower", "minecraft:glass@0")], "role 'tower'"),
    ]:
        with pytest.raises(shell.ShellError, match=why):
            sw = shell.swatches_of(foundry(), extra)
            shell.check_palette(conn, sw)
    # excluding the stone slab leaves the roof one entry
    sw = shell.swatches_of(foundry(), exclude=["minecraft:stone_slab@0"])
    assert Entry("minecraft:stone_slab", 0) not in {s.entry for s in sw}
    only = shell.swatches_of(None, [("window", "minecraft:glass@0")])
    with pytest.raises(shell.ShellError, match="no wall or roof"):
        shell.check_palette(conn, only)


# --- outputs --------------------------------------------------------------------------------


def test_mesh_merges_faces_and_covers_every_exposed_face() -> None:
    a, b = Entry("x:a", 0), Entry("x:b", 0)
    one = shell.mesh({(0, 0, 0): a}, [a])
    assert sorted(f[0] for f in one) == [0, 1, 2, 3, 4, 5]
    assert all(f[4:6] == [1, 1] for f in one)
    slab = {(x, 0, z): a for x in range(4) for z in range(3)}
    faces = shell.mesh(slab, [a])
    up = [f for f in faces if f[0] == 2]
    assert up == [[2, 0, 0, 0, 4, 3, 0]]
    # area adds up to the exposed faces of each colour
    mixed = {**slab, (1, 0, 1): b}
    got = shell.mesh(mixed, [a, b])
    exposed = sum(
        1
        for c in mixed
        for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
        if (c[0] + d[0], c[1] + d[1], c[2] + d[2]) not in mixed
    )
    assert sum(f[4] * f[5] for f in got) == exposed
    assert [f for f in got if f[0] == 2 and f[6] == 1] == [[2, 1, 0, 1, 1, 1, 1]]


def test_the_schematic_puts_each_cell_where_the_layout_has_it(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    spec = spec_for(conn, lay)
    out = accepted(conn, spec)
    s = shell.to_schematic(spec, out.cells)
    assert (s.width, s.height, s.length) == spec.bounds.size
    # WEOffset: the shell's min corner relative to the paste origin, entrance e1's lo cell
    # (the cell the paste steps tell Clayton to stand in, D318), from the layout JSON alone
    e1 = lay["entrances"][0]
    assert e1["id"] == "e1" and e1["lo"] == [
        0,
        0,
        5,
    ]  # in the hall corridor, not (0, 0, 0)
    lo_b = lay["bounds"]["lo"]
    r = spec.relief  # the bounding volume reaches past the wall ring (D386)
    corner = (lo_b[0] - 1 - r, lo_b[1], lo_b[2] - 1 - r)
    assert s.offset == tuple(corner[i] - e1["lo"][i] for i in range(3))
    assert s.offset == (-1 - r, 0, -6 - r)
    stand = shell.paste_steps(shell.spec_json(spec)["origin"])[2]
    x, y, z = e1["lo"]
    assert "entrance e1" in stand and f"(layout x {x}, y {y}, z {z})" in stand
    assert "west edge" in stand and "north-most" in stand
    # pasting at that cell puts every block back at its layout cell
    for c in list(out.cells)[:200]:
        idx = (
            c[0] - spec.bounds.lo[0],
            c[1] - spec.bounds.lo[1],
            c[2] - spec.bounds.lo[2],
        )
        world = tuple(e1["lo"][i] + s.offset[i] + idx[i] for i in range(3))
        assert world == c
    lo = spec.bounds.lo
    for c, e in list(out.cells.items())[:500]:
        assert s.at(c[0] - lo[0], c[1] - lo[1], c[2] - lo[2]) == e
    assert sum(s.used().values()) == len(out.cells)
    names = {e.block for e in out.cells.values()}
    world = WorldRegistry("lab", frozenset(names), frozenset(), "level.dat")
    written = schematic.write_shell(conn, world, s)
    assert written.how == "single" and written.report.ok
    back = schematic.read(written.parts[0].data)
    assert schematic.diff(s, back.schematic) == [] and back.schematic.offset == s.offset
    # a world without one of the blocks refuses the shell at export
    poor = WorldRegistry(
        "lab", frozenset(names - {"minecraft:glowstone"}), frozenset(), "x"
    )
    with pytest.raises(schematic.ShellRefused, match="not in the world"):
        schematic.write_shell(conn, poor, s)


def test_the_build_id_is_deterministic(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    spec = spec_for(conn, lay)
    ops = shell.procedural(spec)
    assert shell.build_id(spec, ops) == shell.build_id(
        spec_for(conn, lay), json.loads(json.dumps(ops))
    )
    assert shell.build_id(spec, ops) != shell.build_id(spec, ops[:-1])


def test_the_layout_must_be_a_layout(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    sw = shell.swatches_of(foundry())
    with pytest.raises(shell.ShellError, match="gos-layout"):
        shell.spec_from_layout({**lay, "v": 9}, sw)
    with pytest.raises(shell.ShellError, match="no entrance"):
        shell.spec_from_layout({**lay, "entrances": []}, sw)
    with pytest.raises(shell.ShellError, match="max_blocks"):
        shell.spec_from_layout(lay, sw, max_blocks=0)
    with pytest.raises(shell.ShellError, match="line type"):
        shell.spec_from_layout(lay, sw, line_type="castle")


# --- the store, the card, gos shell ---------------------------------------------------------


def test_make_saves_a_build_and_export_rechecks_it(
    home: Path, conn: sqlite3.Connection
) -> None:
    vault = VaultIO(home / "vault")
    made = shell_store.make(conn, home, vault, ["pl-steel"], theme_id="th-foundry")
    assert made.outcome.status == "accepted" and made.saved is not None
    files = sorted(p.name for p in made.saved.iterdir())
    assert files == sorted(shell_store.FILES)
    rec = shell_store.load(home, made.build)
    assert rec["source"] == "procedural" and rec["theme"] == "th-foundry@v1"
    assert rec["blocks"] == len(made.outcome.cells)
    tsv = (made.saved / "palette.tsv").read_text(encoding="utf-8").splitlines()
    assert tsv[0] == "role\tblock\tname\tcount" and len(tsv) == rec["types"] + 1
    assert all("@" in line.split("\t")[1] for line in tsv[1:])
    notes = (made.saved / "notes.txt").read_text(encoding="utf-8")
    assert "Lab only" in notes and "never pastes machines" in notes
    assert shell_store.builds(home)[0]["id"] == made.build
    c = shell_store.card(home, made.build)
    assert (
        cards.validate(c) == [] and c["type"] == "shell" and c["ref"] == "plan:pl-steel"
    )
    assert c["faces"] and len(c["palette"]) == rec["types"]
    # export: the same blocks, again through every check, and the world's registry
    names = {m["block"].split("@")[0] for m in rec["materials"]}
    world = WorldRegistry("lab", frozenset(names), frozenset(), "level.dat")
    ex = shell_store.export(conn, home, made.build, world)
    shell_store.write(ex)
    assert [p.name for p in ex.paths] == [f"{made.build}.schematic"]
    got = schematic.read(ex.paths[0].read_bytes())
    assert sum(got.schematic.used().values()) == rec["blocks"]
    # a re-solve moved the machines: the saved layout is stale, export refuses (review M3)
    plan_file = home / "data" / "plans" / "pl-steel.json"
    saved_plan = plan_file.read_text(encoding="utf-8")
    resolved = json.loads(saved_plan)
    resolved["nodes"][0]["count"] += 1
    plan_file.write_text(json.dumps(resolved), encoding="utf-8")
    with pytest.raises(shell.ShellError, match="the layout changed"):
        shell_store.export(conn, home, made.build, world)
    plan_file.unlink()
    with pytest.raises(shell.ShellError, match="isn't saved any more"):
        shell_store.export(conn, home, made.build, world)
    plan_file.write_text(saved_plan, encoding="utf-8")
    assert shell_store.export(conn, home, made.build, world).paths == ex.paths
    # a tampered design is refused at export, never written
    rec["ops"].append(
        {"op": "fill", "block": CHEST, "lo": [-1, 0, -1], "hi": [-1, 0, -1]}
    )
    (made.saved / "design.json").write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(shell.ShellError, match="no longer passes"):
        shell_store.export(conn, home, made.build, world)


def test_make_refuses_and_writes_nothing(home: Path, conn: sqlite3.Connection) -> None:
    vault = VaultIO(home / "vault")
    bad = [{"op": "fill", "block": CHEST, "lo": [-1, 0, -1], "hi": [-1, 0, -1]}]
    made = shell_store.make(
        conn, home, vault, ["pl-steel"], theme_id="th-foundry", ops=bad
    )
    assert made.outcome.status == "refused" and made.saved is None
    assert not (home / shell_store.ROOT).exists()
    with pytest.raises(shell.ShellError, match="isn't saved"):
        shell_store.make(conn, home, vault, ["pl-nope"])
    with pytest.raises(shell.ShellError, match="prompt-only"):
        shell_store.make(conn, home, vault, ["pl-steel"], prompt="a glass box")
    made = shell_store.make(
        conn,
        home,
        vault,
        ["pl-steel"],
        prompt="a glass box",
        extra=[("wall", "minecraft:glass@0"), ("roof", "minecraft:quartz_block@2")],
    )
    assert made.outcome.status == "accepted" and made.record["mode"] == "prompt"


def test_blank_mode_uses_the_theme_used_nearby(
    home: Path, conn: sqlite3.Connection
) -> None:
    vault = VaultIO(home / "vault")
    site = shell_store.Site(0, 100, 100)
    first = shell_store.make(
        conn, home, vault, ["pl-steel"], theme_id="th-marble-works", site=site
    )
    assert first.saved is not None
    near = shell_store.make(
        conn,
        home,
        vault,
        ["pl-power"],
        line_type="power",
        site=shell_store.Site(0, 120, 90),
    )
    assert near.brief.mode == "blank" and near.brief.theme is not None
    assert (
        near.brief.theme.id == "th-marble-works"
        and "within 256 blocks" in near.brief.why
    )
    far = shell_store.make(conn, home, vault, ["pl-power"], line_type="power")
    assert (
        far.brief.theme is not None and far.brief.theme.id == "th-foundry"
    )  # fits power


def _gos(home: Path, tmp_path: Path, *argv: str) -> tuple[int, str]:
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f"[paths]\ngos_home = {json.dumps(home.as_posix())}\n", encoding="utf-8"
    )
    parser = argparse.ArgumentParser()
    shell_cli.add_parsers(parser.add_subparsers(dest="command"))
    out = io.StringIO()
    code = shell_cli.run(
        parser.parse_args(["shell", *argv, "--config", str(toml)]), out
    )
    return code, out.getvalue()


def test_cli(home: Path, tmp_path: Path) -> None:
    code, text = _gos(home, tmp_path, "design", "pl-steel", "--theme", "th-foundry")
    assert code == 0, text
    build = text.split()[1]
    assert shell_store.BUILD_ID.fullmatch(build) and "accepted" in text
    design = tmp_path / "design.json"
    design.write_text(
        json.dumps(
            [{"op": "fill", "block": CHEST, "lo": [-1, 0, -1], "hi": [-1, 0, -1]}]
        ),
        encoding="utf-8",
    )
    code, text = _gos(
        home,
        tmp_path,
        "design",
        "pl-steel",
        "--theme",
        "th-foundry",
        "--design",
        str(design),
    )
    assert code == 1 and "REFUSED" in text and "has a tile entity" in text
    code, text = _gos(home, tmp_path, "list")
    assert code == 0 and build in text
    code, text = _gos(home, tmp_path, "show", build)
    assert code == 0 and "palette" in text and "Lab only" in text
    code, text = _gos(home, tmp_path, "show", build, "--card")
    assert code == 0 and cards.validate(json.loads(text)) == []
    world = tmp_path / "labworld"
    world.mkdir()
    code, text = _gos(home, tmp_path, "export", build, "--world-dir", str(world))
    assert code == 2 and "registry" in text  # no level.dat: nothing written
    assert not (home / shell_store.SCHEMATICS).exists()
    code, text = _gos(home, tmp_path, "show", "sh-000000000000")
    assert code == 2 and "no shell build" in text


def test_the_gos_cli_has_the_command() -> None:
    assert "shell" in shell_cli.COMMANDS
    with pytest.raises(SystemExit) as got:
        cli.main(["shell", "design", "--help"])
    assert got.value.code == 0


# --- the build_request tool -----------------------------------------------------------------


@pytest.fixture
def reg(home: Path) -> Any:  # noqa: ANN401
    return build_registry(access.db_path(home), vault=VaultIO(home / "vault"))


def test_build_request_is_the_architects_and_deep_lane_only(reg: Any) -> None:  # noqa: ANN401
    fast = {s["name"] for s in reg.specs(FAST)}
    deep = {s["name"] for s in reg.specs(DEEP)}
    assert "build_request" in deep and "build_request" not in fast
    main, agents = deep_config.tool_sets(reg)
    assert "mcp__gos__build_request" in agents["architect"]
    assert "mcp__gos__build_request" not in agents["planner"]
    assert "mcp__gos__build_request" in main


def ask(reg: Any, **args: Any) -> Any:  # noqa: ANN401
    base: dict[str, Any] = {"plans": "pl-steel", "theme": "th-foundry"}
    if "design" in args:
        args["design"] = json.dumps(args["design"])
    return reg.call("build_request", {**base, **args}, lane=DEEP)


def ops_of(text: str) -> list[dict[str, Any]]:
    line = next(x for x in text.splitlines() if x.startswith("note: ops: "))
    got: list[dict[str, Any]] = json.loads(line.removeprefix("note: ops: "))
    return got


def test_build_request_designs_validates_and_refuses(reg: Any, home: Path) -> None:  # noqa: ANN401
    # save false: a look at the procedural ops that writes nothing (review L3)
    look = ask(reg, save=False)
    assert not look.is_error and "status: accepted" in look.text, look.text
    assert "not saved" in look.text and ops_of(look.text)[0]["op"] == "walls"
    assert not (home / shell_store.ROOT).exists()
    base = ask(reg)
    assert not base.is_error, base.text
    assert ops_of(base.text) == ops_of(look.text)
    assert "status: accepted" in base.text and "source: procedural" in base.text
    assert "wall\tgregtech:gt.blockconcretes@3" in base.text
    ops = ops_of(base.text)
    assert ops[0]["op"] == "walls"
    # the architect's own design, built from the baseline: accepted
    mine = [*ops[:-1], {"op": "pillars", "role": "pillar", "every": 4}, ops[-1]]
    got = ask(reg, design=mine)
    assert (
        not got.is_error
        and "source: architect" in got.text
        and "status: accepted" in got.text
    )
    # a tile-entity block: refused, nothing written
    before = sorted(p.name for p in (home / shell_store.ROOT).iterdir())
    te = [*ops, {"op": "fill", "block": CHEST, "lo": [-1, 0, -1], "hi": [-1, 0, -1]}]
    bad = ask(reg, design=te)
    assert bad.is_error and "refused" in bad.text and "has a tile entity" in bad.text
    assert sorted(p.name for p in (home / shell_store.ROOT).iterdir()) == before
    # a breach: repaired and said
    lay_ = json.loads(
        next((home / shell_store.ROOT).glob("*/layout.json")).read_text("utf-8")
    )
    k = lay_["keepouts"][0]
    breach = [
        *ops,
        {
            "op": "fill",
            "role": "pillar",
            "lo": k["lo"],
            "hi": [k["lo"][0], 1, k["lo"][2]],
        },
    ]
    fixed = ask(reg, design=breach)
    assert not fixed.is_error and "status: repaired" in fixed.text
    assert "inside keepout" in fixed.text
    # too many blocks: refused
    small = ask(reg, design=ops, max_blocks=10)
    assert small.is_error and "more than max_blocks" in small.text
    for args, why in [
        ({"design": "not json"}, "design is not JSON"),
        ({"plans": "PL STEEL"}, "is not a plan id"),
        ({"site": {"dim": 0}}, "site is {dim, x, z}"),
    ]:
        out = reg.call("build_request", {"plans": "pl-steel", **args}, lane=DEEP)
        assert out.is_error and why in out.text, out.text
    assert reg.call("build_request", {"plans": "pl-steel"}, lane=FAST).is_error


# --- the API --------------------------------------------------------------------------------

ADMIN = "admin-" + "s" * 40


def test_the_shell_routes(home: Path) -> None:
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
            assert c.get("/api/shells").status_code == 401
            assert c.get("/api/shells", headers=admin).json() == {"shells": []}
            made = c.post(
                "/api/shells",
                headers=admin,
                json={"plans": ["pl-steel"], "theme": "th-foundry"},
            )
            assert made.status_code == 200, made.text
            card = made.json()["card"]
            assert cards.validate(card) == [] and card["source"] == "procedural"
            listed = c.get("/api/shells", headers=admin).json()["shells"]
            assert [s["id"] for s in listed] == [card["build"]]
            got = c.get(f"/api/shells/{card['build']}", headers=admin).json()["card"]
            assert got == card
            assert (
                c.get("/api/shells/sh-000000000000", headers=admin).status_code == 404
            )
            assert c.get("/api/shells/..%2fx", headers=admin).status_code == 404
            bad = c.post("/api/shells", headers=admin, json={"plans": "pl-steel"})
            assert bad.status_code == 400
            nope = c.post("/api/shells", headers=admin, json={"plans": ["pl-nope"]})
            assert nope.status_code == 400 and "isn't saved" in nope.text
    finally:
        store.close()


# --- a real dump's largest structure rows -------------------------------------------------------

BIG_MTE = (
    15411  # Forge of the Gods: its t3 row is 1,239,746 bytes of blocks_json on 2.9.0
)


def add_big_structure(home: Path, mte: int = BIG_MTE, cells: int = 24_000) -> int:
    """A structure row for ``mte`` (no plan's machine) whose blocks_json is over the tools'
    1 MB value limit; returns its size in bytes."""
    blocks = json.dumps(
        [
            {
                "d": [i % 40, i // 1600, (i // 40) % 40],
                "block": "gregtech:gt.blockcasings",
                "meta": 8,
            }
            for i in range(cells)
        ]
    )
    assert len(blocks) > registry.MAX_VALUE_BYTES
    c = access.connect(access.db_path(home))
    try:
        c.execute(
            "INSERT INTO structure(variant, size_x, size_y, size_z, blocks_json, source,"
            " registry_name, meta, name_en, channels_json, hatches_json, subs_json,"
            " failures_json) VALUES ('t3', 40, 15, 40, ?, 'gtnh-extractor', ?, ?,"
            " 'Forge of the Gods', '{}', '[]', '{}', '[]')",
            (blocks, palette.GT_MACHINES, mte),
        )
        c.commit()
    finally:
        c.close()
    return len(blocks)


def test_a_layout_reads_only_its_plans_structure_rows(home: Path) -> None:
    """Defect (real dump): every build_request failed with "string or blob too big" because the
    layout read every structure row, and Forge of the Gods' is over the tools' 1 MB limit."""
    add_big_structure(home)
    c = access.connect(access.db_path(home), readonly=True)
    try:
        registry.guard(c, 30.0)  # a tool's connection, at the default value limit
        lay = layout.plan_layout(c, [lay_tests.production(), lay_tests.power()])
        assert lay["placements"]
        with pytest.raises(sqlite3.DataError, match="too big"):
            fp.library(c)  # every row: what the layout did before
        with pytest.raises(sqlite3.DataError, match="too big"):
            fp.library(c, [BIG_MTE])  # that row itself: over the default limit
    finally:
        c.close()


def test_build_request_beside_a_structure_row_over_a_megabyte(
    reg: Any,  # noqa: ANN401
    home: Path,
) -> None:
    add_big_structure(home)
    got = ask(reg, save=False)
    assert not got.is_error, got.text
    assert "status: accepted" in got.text


def test_build_request_reads_a_plans_own_structure_row_over_a_megabyte(
    tmp_path: Path,
) -> None:
    """A plan with Forge of the Gods: build_request's connection allows its structure row
    (shells.MAX_STRUCTURE_BYTES), every other tool keeps the 1 MB limit."""
    forge = {
        **lay_tests.production("pl-forge"),
        "nodes": [lay_tests.node("n1", BIG_MTE, 1)],
    }
    forge["edges"] = []
    home = make_home(tmp_path, forge)
    size = add_big_structure(home)
    assert registry.MAX_VALUE_BYTES < size < shells.MAX_STRUCTURE_BYTES
    reg = build_registry(access.db_path(home), vault=VaultIO(home / "vault"))
    limits = {t.name: t.max_value_bytes for t in reg.tools()}
    assert limits.pop("build_request") == shells.MAX_STRUCTURE_BYTES
    assert set(limits.values()) == {registry.MAX_VALUE_BYTES}
    got = reg.call(
        "build_request",
        {"plans": "pl-forge", "theme": "th-foundry", "save": False},
        lane=DEEP,
    )
    assert not got.is_error, got.text
    assert "status: accepted" in got.text


def test_an_open_interior_is_the_shells_but_the_machines_space_is_not(
    conn: sqlite3.Connection, lay: dict[str, Any]
) -> None:
    """D389: the floor between machines is the shell's (columns, inner walls, courtyards); their
    keep-outs, the corridors and the chases stay empty."""
    spec = spec_for(conn, lay)
    assert spec.interior_open and not any(v.kind == "interior" for v in spec.clear)
    i = spec.interior
    vols = [v.box for v in spec.clear]
    free = next(c for c in i.cells() if not any(v.contains(c) for v in vols))
    stub = {"op": "fill", "role": "pillar", "lo": list(free), "hi": list(free)}
    out = shell.design(conn, spec, [*good_design(spec), stub], "architect")
    assert out.status == "accepted" and free in out.cells
    keep = next(v.box for v in spec.clear if v.kind == "keepout")
    into = {"op": "fill", "role": "pillar", "lo": list(keep.lo), "hi": list(keep.lo)}
    out = shell.design(conn, spec, [*good_design(spec), into], "architect")
    assert out.status == "repaired" and keep.lo not in out.cells
    assert shell.spec_json(spec)["interior_open"] is True
