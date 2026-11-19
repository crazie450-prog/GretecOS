"""WO-10.8 acceptance on a real harness DB: the 3D build guide's data, through the API, matches
each structure's footprint (plan 8 row 10.8, D319, D320).

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the server harness (CI's mod job; locally
mod/README.md). The server run is built into a home (``gos export collect`` + ``gos db build``,
through the shared harness cache), then a structure dump is loaded with ``gos structures load``'s
own loader: the one in GOS_STRUCTURES_DATASET when it is set, else the repo's self-authored fixture.
For every controller ``GET /api/build/guides`` lists, and every facing, ``GET /api/build/guide``
must give the footprint's own cells, BOM, per-layer materials and hatch slots (registry names,
never numeric ids), and the checklist must persist through the API. Nothing is written outside
tmp_path.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.build import footprints as fp
from gregtec.db import access
from gregtec.db import structures as structure_db
from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]
GAME = REPO / "mod" / "run" / "server"
FIXTURE = REPO / "tests" / "fixtures" / "gtnh-extractor"
DATASET = os.environ.get("GOS_STRUCTURES_DATASET")
ADMIN = "admin-" + "h" * 40


@pytest.fixture(scope="module")
def home(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the server harness")
    if not (GAME / collect.EXPORTS).is_dir():
        pytest.fail(
            f"no server harness run in {GAME / collect.EXPORTS} (mod/README.md)"
        )
    home = tmp_path_factory.mktemp("build-guide")
    built_home(GAME, home)
    conn = access.connect(access.db_path(home))
    try:
        structure_db.load(conn, Path(DATASET) if DATASET else FIXTURE)
    finally:
        conn.close()
    return home


@pytest.fixture(scope="module")
def client(home: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from gregtec.api.app import Core, create_app
    from gregtec.api.security import Auth
    from gregtec.config import Config
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store

    store = Store(access.db_path(home))
    core = Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            c.headers["Authorization"] = f"Bearer {ADMIN}"
            yield c
    finally:
        store.close()


def _footprints(home: Path) -> fp.Library:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        return fp.library(conn)
    finally:
        conn.close()


def test_the_guide_for_every_harness_structure_matches_its_footprint(
    home: Path,
    client: Any,  # noqa: ANN401
) -> None:
    lib = _footprints(home)
    listed = client.get("/api/build/guides").json()["guides"]
    assert {g["controller"] for g in listed} == {f.controller for f in lib.footprints}
    assert listed, "no structure in the harness DB"
    checked = 0
    for entry in listed:
        for variant in entry["variants"]:
            for facing in fp.FACINGS:
                params = {
                    "controller": entry["controller"],
                    "variant": variant,
                    "facing": facing,
                }
                got = client.get("/api/build/guide", params=params)
                assert got.status_code == 200, (params, got.text)
                g = got.json()
                reg, meta = fp.parse_controller(entry["controller"])
                f = lib.get(reg, meta, variant, facing)
                assert g["key"] == f.key and g["size"] == list(f.size)
                assert {p["block"]: p["count"] for p in g["palette"]} == f.materials()
                lo = f.lo
                want = {
                    (c.d[0] - lo[0], c.d[1] - lo[1], c.d[2] - lo[2]): (
                        c.key,
                        tuple(sorted(c.hatches)),
                    )
                    for c in f.cells
                }
                cells = {
                    (x, y, z): (
                        g["palette"][b]["block"] if b >= 0 else None,
                        tuple(g["slots"][s]["kinds"]) if s >= 0 else (),
                    )
                    for x, y, z, b, s in g["cells"]
                }
                assert cells == want, f.key
                for layer in g["layers"]:
                    counted: dict[str, int] = {}
                    for c in f.cells:
                        if c.d[1] - lo[1] == layer["y"] and c.key is not None:
                            counted[c.key] = counted.get(c.key, 0) + 1
                    assert layer["materials"] == counted, (f.key, layer["y"])
                for p in g["palette"]:
                    block, _, m = p["block"].rpartition("@")
                    assert (
                        ":" in block
                        and not block.split(":")[0].isdigit()
                        and m.isdigit()
                    )
                checked += 1
    assert checked >= 4 * len(listed)
    # The checklist persists through the API.
    first = listed[0]["controller"]
    tick = client.post(
        "/api/build/guide/checklist",
        json={"controller": first, "item": "layer:0", "done": True},
    )
    assert tick.status_code == 200 and tick.json() == {"done": ["layer:0"]}
    assert client.get("/api/build/guide", params={"controller": first}).json()[
        "done"
    ] == ["layer:0"]
