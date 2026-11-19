"""QA 5-9: the core right after the wizard, before the first ``gos db build``.

The core's own Store makes ``data/gos.sqlite`` with only its tables, so "is there a DB file?"
is no test for "are there recipes?". Every lookup, the Designer, the Power view and the
wizard's DB row must say there are no recipes yet, not show a raw SQLite error or "no match".
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from gregtec import doctor
from gregtec.agents.ask import Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane
from gregtec.agents.tools import build_registry
from gregtec.api.app import create_app
from gregtec.db import access
from gregtec.llm.fake import EchoClient
from gregtec.packupdate import gtpin

from .conftest import KEY
from .test_my_base_api import admin, make_core


def test_a_home_whose_db_the_core_made_says_no_recipes_yet(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    core = make_core(home)  # its Store makes data/gos.sqlite, core tables only
    try:
        db = access.db_path(home)
        assert db.is_file() and not access.is_built(db)
        deep = HandoffRecorder()
        registry = build_registry(db, key=KEY, deep=deep)
        fast = FastLane(EchoClient(), registry, meter=None)  # type: ignore[arg-type]
        core.asker = Asker(registry, deep, fast)
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            for name, args in (
                ("item_resolve", {"text": "steel"}),
                ("recipe_find", {"item": "steel"}),
            ):
                r = c.post(f"/api/tools/{name}", json={"args": args}, headers=admin())
                assert r.status_code == 200, r.text
                error = r.json()["card"]["error"]
                assert "gos db build" in error and "no such table" not in error
            spec = {"target": "steel ingot", "rate": 1, "tier": "HV"}
            r = c.post("/api/designer/design", json=spec, headers=admin())
            assert r.status_code == 400 and "no recipe DB" in r.text, r.text
            r = c.post(
                "/api/power/design",
                json={"mode": "best", "tier": "LV"},
                headers=admin(),
            )
            assert r.status_code == 400 and "no recipe DB" in r.text, r.text
            r = c.get("/api/power", headers=admin())
            assert r.status_code == 200 and r.json()["ledger"] is None, r.text
        ok, line = gtpin.check_db(home)
        assert ok and "no recipes" in line and "can't read" not in line
        row = doctor.pin_check(home, [])  # the wizard's row: a note, not "can't read"
        assert "gos db build" in row.detail and "no such table" not in row.detail
    finally:
        core.store.close()
