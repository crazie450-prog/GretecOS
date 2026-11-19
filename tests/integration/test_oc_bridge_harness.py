"""The OC bridge and the confirm flow over a DB built from a real harness export (WO-7.1).

A GOS_HOME is made from the server harness's newest run (``gos export collect`` then ``gos db
build``, as the other harness tests do); then the core's bridge (:func:`gregtec.bridge.ops.build`)
takes the contract's golden telemetry over ``POST /oc/telemetry``, the deep lane's
``action_propose`` tool proposes a craft of the golden craft's item, read back with its name from
the DB, and the command goes out only after Clayton's confirm. Only with
GOS_REQUIRE_HARNESS_RUN=1 after the server harness (mod/README.md); nothing is written outside
tmp_path.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.registry import DEEP, FAST
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.bridge import ops as bridge_ops
from gregtec.config import Config
from gregtec.db import access
from gregtec.ingest import collect
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "fixtures" / "oc"
ADMIN = "admin-" + "c" * 40


@pytest.fixture(scope="module")
def home(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("oc-bridge")
    built_home(game, home)
    return home


@pytest.fixture
def core(home: Path) -> Iterator[Core]:
    cfg = Config(gos_home=home)
    store = Store(access.db_path(home))
    ops = bridge_ops.build(cfg, store)
    c = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        oc=ops.bridge,
        ops=ops,
    )
    yield c
    store.close()


def test_a_craft_from_telemetry_to_the_reply(core: Core, home: Path) -> None:
    assert core.ops is not None
    first = json.loads((GOLDEN / "telemetry-first.json").read_text(encoding="utf-8"))
    craft = json.loads((GOLDEN / "telemetry-results.json").read_text(encoding="utf-8"))[
        "crafts"
    ][0]
    token = core.auth.issue_client_token("oc-lab")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with TestClient(create_app(core), base_url="http://127.0.0.1:8765") as client:
        r = client.post("/oc/telemetry", content=json.dumps(first), headers=headers)
        assert r.status_code == 200 and r.json()["ok"] is True

        reg = default_registry(home, ops=core.ops)
        assert reg.get("action_propose") is not None
        assert "action_propose" not in {t.name for t in reg.tools(FAST)}
        args = {"name": craft["name"], "damage": craft["damage"], "amount": 64}
        out = reg.call(
            "action_propose",
            {"kind": "craft", "args": args, "instance": "lab"},
            lane=DEEP,
        )
        assert not out.is_error, out.text
        conn = access.connect(access.db_path(home), readonly=True)
        try:
            (name,) = conn.execute(
                "SELECT name_en FROM item WHERE registry_name = ? AND meta = ? "
                "AND nbt_hash = ''",
                (craft["name"], craft["damage"]),
            ).fetchone()
        finally:
            conn.close()
        assert f"say: Craft 64 {name} in Lab. Say confirm." in out.text
        pending = core.ops.desk.pending()
        assert pending is not None and pending.computer == first["computer"]["address"]

        # An agent's extra "confirmed" is refused by the schema; nothing is sent unconfirmed.
        refused = reg.call(
            "action_propose",
            {"kind": "say", "args": {"text": "x"}, "confirmed": True},
            lane=DEEP,
        )
        assert refused.is_error
        first["seq"] = 2
        r = client.post("/oc/telemetry", content=json.dumps(first), headers=headers)
        assert r.json()["cmds"] == []

        voice = core.auth.issue_client_token("voice")
        r = client.post(
            f"/api/actions/{pending.id}/confirm",
            headers={"Authorization": f"Bearer {voice}"},
        )
        assert r.status_code == 200, r.text
        first["seq"] = 3
        r = client.post("/oc/telemetry", content=json.dumps(first), headers=headers)
        assert r.json()["cmds"] == [
            {"id": pending.id, "kind": "craft", "args": args, "confirmed_via": "voice"}
        ]
        tel = reg.call("telemetry_get", {"scope": "all"}, lane=FAST)
        assert "tel:lab/" in tel.text and "multimachine.blastfurnace" in tel.text
