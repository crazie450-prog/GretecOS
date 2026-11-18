"""WO-10.8: the 3D build guide's data from the footprint library and the block catalog
(gregtec.build.guide_data, plan 7.10 "Viewer features", D319, D320).

Footprints come from WO-1.8's self-authored structure fixture (tests/fixtures/gtnh-extractor,
``fixture:*`` blocks); the catalog is a made-up palette run (tests/unit/test_palette.py's
writer). No game data.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.build import footprints as fp
from gregtec.build import guide_data
from gregtec.db import access
from gregtec.db import blocks as block_db
from gregtec.db import structures as structure_db
from gregtec.ingest import palette as palette_ingest
from gregtec.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GT = "gregtech:gt.blockmachines"
ADMIN = "admin-" + "g" * 40


def _palette_tests() -> ModuleType:
    name = "_wo108_palette_tests"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("test_palette.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def add_catalog(conn: sqlite3.Connection, folder: Path) -> None:
    """A made-up catalog for the fixture furnace's blocks (casing named, coil coloured)."""
    pt = _palette_tests()
    states = [
        pt.state(
            "fixture:casing",
            0,
            name="Fixture Casing",
            faces=["fx:side", "fx:top"] + ["fx:side"] * 4,
        ),
        pt.state("fixture:coil", 0, name="Fixture Coil"),
    ]
    textures = [
        {
            "icon": "fx:top",
            "path": "assets/fx/textures/blocks/top.png",
            "rgb": "#a0b0c0",
        },
        {
            "icon": "fx:side",
            "path": "assets/fx/textures/blocks/side.png",
            "rgb": "#405060",
        },
    ]
    conn.execute(
        "INSERT INTO export_run(id, fingerprint, run_key) VALUES (1, '', 'run-a')"
    )
    block_db.write(conn, 1, palette_ingest.read(pt.write_run(folder, states, textures)))
    conn.commit()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(tmp_path / "gos.sqlite")
    structure_db.load(c, FIXTURES / "gtnh-extractor")
    try:
        yield c
    finally:
        c.close()


def test_the_guide_matches_its_footprint(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    add_catalog(conn, tmp_path / "run")
    lib = fp.library(conn)
    for f in lib.every_facing():
        doc = guide_data.guide(f, guide_data.looks(conn, f.materials()))
        assert doc["key"] == f.key and doc["size"] == list(f.size)
        # The BOM is the footprint's materials, controller included.
        assert {p["block"]: p["count"] for p in doc["palette"]} == f.materials()
        # Every cell, at its offset from the low corner, with its block and hatch kinds.
        lo = f.lo
        want = {
            (c.d[0] - lo[0], c.d[1] - lo[1], c.d[2] - lo[2]): (
                c.key,
                tuple(sorted(c.hatches)),
            )
            for c in f.cells
        }
        got = {
            (x, y, z): (
                doc["palette"][b]["block"] if b >= 0 else None,
                tuple(doc["slots"][s]["kinds"]) if s >= 0 else (),
            )
            for x, y, z, b, s in doc["cells"]
        }
        assert got == want
        # Materials per layer add up to the footprint's, and each layer's are its cells'.
        total: dict[str, int] = {}
        for layer in doc["layers"]:
            counted: dict[str, int] = {}
            for c in f.cells:
                if c.d[1] - lo[1] == layer["y"] and c.key is not None:
                    counted[c.key] = counted.get(c.key, 0) + 1
            assert layer["materials"] == counted
            assert layer["blocks"] == sum(counted.values())
            for k, n in counted.items():
                total[k] = total.get(k, 0) + n
        assert total == f.materials()
        assert [layer["y"] for layer in doc["layers"]] == list(range(f.size[1]))
        assert sum(s["cells"] for s in doc["slots"]) == len(f.hatch_cells)
        # The controller sits at its offset 0.
        x, y, z = doc["controller_at"]
        at = next(c for c in doc["cells"] if c[:3] == [x, y, z])
        assert doc["palette"][at[3]]["block"] == f.controller
        assert [i["id"] for i in doc["checklist"]] == [
            f"layer:{y}" for y in range(f.size[1])
        ]


def test_names_and_colours_come_from_the_catalog(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    furnace = fp.library(conn).get(GT, 9001)
    bare = guide_data.guide(furnace, guide_data.looks(conn, furnace.materials()))
    assert bare["catalog"] is False  # no catalog yet: registry names only
    assert all(p["name"] is None for p in bare["palette"])
    add_catalog(conn, tmp_path / "run")
    doc = guide_data.guide(furnace, guide_data.looks(conn, furnace.materials()))
    by = {p["block"]: p for p in doc["palette"]}
    assert by["fixture:casing@0"] | {"count": 0} == {
        "block": "fixture:casing@0",
        "name": "Fixture Casing",
        "top": "#a0b0c0",
        "side": "#405060",
        "count": 0,
    }
    assert by["fixture:coil@0"]["name"] == "Fixture Coil"
    assert by["fixture:coil@0"]["top"] is None  # its icon has no texture line
    assert by[f"{GT}@9001"]["name"] is None  # not in this catalog
    assert doc["ref"] == "mte:9001" and doc["name"] == "Fixture Furnace"


def test_keys_are_registry_names_never_numeric_ids(conn: sqlite3.Connection) -> None:
    for f in fp.library(conn).footprints:
        doc = guide_data.guide(f, {})
        for p in doc["palette"]:
            block, _, meta = p["block"].rpartition("@")
            assert ":" in block and meta.isdigit()


def test_listing_and_variants(conn: sqlite3.Connection) -> None:
    lib = fp.library(conn)
    listed = guide_data.listing(lib)
    assert [g["controller"] for g in listed] == sorted(
        {f.controller for f in lib.footprints}
    )
    furnace = next(g for g in listed if g["mte"] == 9001)
    assert furnace["name"] == "Fixture Furnace" and furnace["variants"]


def test_the_checklist_persists_per_shape(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    store = Store(tmp_path / "state.sqlite")
    try:
        lib = fp.library(conn)
        north = lib.get(GT, 9001)
        east = lib.get(GT, 9001, facing="east")
        assert guide_data.check(store, north, "layer:0", True) == ["layer:0"]
        assert guide_data.check(store, north, "layer:2", True) == ["layer:0", "layer:2"]
        # The facing doesn't change what is built: the same checklist.
        assert guide_data.checked(store, guide_data.checklist_key(east)) == [
            "layer:0",
            "layer:2",
        ]
        hot = lib.get(GT, 9001, tiers={"coil": 3})  # another tier is another build
        assert guide_data.checked(store, guide_data.checklist_key(hot)) == []
        assert guide_data.check(store, north, "layer:0", False) == ["layer:2"]
        for bad in ("layer:99", "layer:-1", "layer:01", "hatch:1", ""):
            with pytest.raises(ValueError, match="not an item"):
                guide_data.check(store, north, bad, True)
        guide_data.reset(store, north)
        assert guide_data.checked(store, guide_data.checklist_key(north)) == []
        doc = guide_data.guide(north, {}, done=["layer:1", "layer:42"])
        assert doc["done"] == ["layer:1"]  # only items the guide has
    finally:
        store.close()


# --- the API ------------------------------------------------------------------------------------


def _core(home: Path) -> Any:  # noqa: ANN401
    from gregtec.api.app import Core
    from gregtec.api.security import Auth
    from gregtec.config import Config
    from gregtec.llm.usage import UsageMeter

    store = Store(access.db_path(home))
    return Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )


def test_guide_routes(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api.app import create_app

    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    admin = {"Authorization": f"Bearer {ADMIN}"}
    core = _core(home)
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            assert c.get("/api/build/guides").status_code == 401
            empty = c.get("/api/build/guides", headers=admin)
            assert empty.status_code == 200 and empty.json() == {"guides": []}
            db = access.connect(access.db_path(home))
            structure_db.load(db, FIXTURES / "gtnh-extractor")
            add_catalog(db, tmp_path / "run")
            lib = fp.library(db)
            db.close()
            listed = c.get("/api/build/guides", headers=admin).json()["guides"]
            assert {g["mte"] for g in listed} >= {9001}

            q = {"controller": f"{GT}@9001"}
            got = c.get("/api/build/guide", params=q, headers=admin)
            assert got.status_code == 200
            doc = got.json()
            f = lib.get(GT, 9001)
            assert doc == guide_data.guide(
                f, {k: v for k, v in _looks(home, f).items()}, [f]
            ) | {"variants": doc["variants"]}
            east = c.get(
                "/api/build/guide",
                params={**q, "facing": "east", "tier": "coil=3"},
                headers=admin,
            ).json()
            assert east["facing"] == "east" and east["tiers"] == {"coil": 3}
            for bad, status in (
                ({"controller": "1000"}, 404),
                ({"controller": f"{GT}@4242"}, 404),
                ({**q, "facing": "up"}, 404),
                ({**q, "tier": "glass=1"}, 404),
                ({**q, "variant": "nope"}, 404),
            ):
                assert (
                    c.get("/api/build/guide", params=bad, headers=admin).status_code
                    == status
                ), bad

            tick = {**q, "item": "layer:1", "done": True}
            assert c.post("/api/build/guide/checklist", json=tick).status_code == 401
            ok = c.post("/api/build/guide/checklist", json=tick, headers=admin)
            assert ok.status_code == 200 and ok.json() == {"done": ["layer:1"]}
            again = c.get("/api/build/guide", params=q, headers=admin).json()
            assert again["done"] == ["layer:1"]
            bad = c.post(
                "/api/build/guide/checklist",
                json={**q, "item": "layer:77", "done": True},
                headers=admin,
            )
            assert bad.status_code == 400
            assert (
                c.post(
                    "/api/build/guide/checklist",
                    json={**q, "item": "layer:1"},
                    headers=admin,
                ).status_code
                == 400
            )
            reset = c.post(
                "/api/build/guide/checklist", json={**q, "reset": True}, headers=admin
            )
            assert reset.json() == {"done": []}
    finally:
        core.store.close()


def _looks(home: Path, f: fp.Footprint) -> dict[str, guide_data.Look]:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        return guide_data.looks(conn, f.materials())
    finally:
        conn.close()


def test_the_dashboards_fixture_is_what_the_core_sends() -> None:
    """dashboard/tests draw tests/fixtures/guide/furnace.json: it must stay the core's shape
    (regenerate with ``uv run python tests/fixtures/guide/make.py``)."""
    import json

    spec = importlib.util.spec_from_file_location(
        "_wo108_guide_make", FIXTURES / "guide" / "make.py"
    )
    assert spec is not None and spec.loader is not None
    make = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make)
    on_disk = json.loads((FIXTURES / "guide" / "furnace.json").read_text("utf-8"))
    assert on_disk == json.loads(json.dumps(make.furnace()))
