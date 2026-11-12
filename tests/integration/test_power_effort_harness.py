"""The effort model applied to power (WO-4.3, D236, D237) on a real server harness DB.

As test_power_objectives_harness.py, these run only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod
job, after the harnesses; locally: mod/README.md). On the export's own rows: a large gas turbine
line at EV in ``output_per_effort`` mode counts its Steel large rotor (GT's turbine item, found
by its NBT material, made by the export's assembler recipe) and its dynamo hatch among the build
components; the breakdown's hours add up, cite their machines and recipes, rank the options by
net EU/t per effort hour, and make a valid table card; owning only the turbine unlocks the
machine types its components need. Nothing is written outside tmp_path.
"""

from __future__ import annotations

import dataclasses
import math
import os
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from gregtec.cards import validate
from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import effort, power
from gregtec.planner.machines import current_run
from gregtec.planner.outputs import cards
from gregtec.planner.request import DefaultBaseInputs

REPO = Path(__file__).resolve().parents[2]
EV = 4


def _require_harness() -> None:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    _require_harness()
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("power-effort")
    collect.collect(game, home, 1, allow_harness=True)
    result = build.build(home, allow_harness=True)
    assert result.loaded.power_status == "ok", result.loaded.power_error
    c = access.connect(access.db_path(home), readonly=True)
    yield c
    c.close()


@dataclass
class Owns:
    """A My base that owns some machines (the BaseInputs interface)."""

    owned: Mapping[int, int] = field(default_factory=dict)

    def tier_cap(self) -> int:
        return EV

    def owned_machines(self) -> Mapping[int, int]:
        return self.owned

    def stock(self) -> Mapping[str, float]:
        return {}

    def line_outputs(self) -> Mapping[str, float]:
        return {}


REQ = power.PowerRequest(
    mode="output_per_effort",
    tier_cap=EV,
    kinds=("gas_turbine",),
    candidates=4,
    options=3,
)


@pytest.fixture(scope="module")
def turbine(conn: sqlite3.Connection) -> power.PowerDesign:
    return power.design(conn, REQ, DefaultBaseInputs(EV))


def _generator(p: Mapping[str, object]) -> dict[str, object]:
    nodes = p["nodes"]
    assert isinstance(nodes, list)
    return next(n for n in nodes if n["kind"] == "generator")


def _makers(conn: sqlite3.Connection, key: str) -> set[str]:
    """The r: ids of the enabled recipes whose output is ``key`` (a stable key)."""
    head, _, nbt = key.partition("#")
    reg, meta = head.rsplit("@", 1)
    return {
        str(r[0])
        for r in conn.execute(
            "SELECT r.ref FROM recipe AS r JOIN recipe_output AS o ON o.recipe_id = r.id"
            " JOIN item AS i ON i.id = o.item_id WHERE i.registry_name = ? AND i.meta = ?"
            " AND substr(i.nbt_hash, 1, 8) = ? AND r.enabled = 1 AND r.fake = 0",
            (reg, int(meta), nbt),
        )
    }


def test_the_rotor_and_dynamo_hatch_are_build_components(
    conn: sqlite3.Connection, turbine: power.PowerDesign
) -> None:
    assert turbine.options, turbine.rejected
    for p in turbine.options:
        gen = _generator(p)
        spec = gen["spec"]
        assert isinstance(spec, dict) and spec["rotor"] == ["Steel", "large"]
        count = int(gen["count"])  # type: ignore[call-overload]
        comps = p["metrics"]["effort"]["components"]
        rotor = power.rotor_key(conn, current_run(conn), "Steel", "large")
        assert rotor is not None and rotor.startswith(f"{power.ROTOR_ITEM}@174#")
        assert {"id": rotor, "qty": count, "for": f"mte:{spec['mte']}",
                "part": "rotor"} in comps  # fmt: skip
        dynamo = power.hatch_mte(conn, power.DYNAMO_HATCH, int(spec["dynamo_tier"]))
        assert dynamo is not None
        assert {"id": f"gregtech:gt.blockmachines@{dynamo}", "qty": count,
                "for": f"mte:{spec['mte']}", "part": "hatch"} in comps  # fmt: skip
        # The rotor is made by the export's own assembler recipe, cited in the breakdown.
        cited = {
            x["id"] for row in p["metrics"]["effort"]["rows"] for x in row["recipes"]
        }
        assert _makers(conn, rotor) & cited
        notes = p["metrics"]["effort"]["notes"]
        assert any("maintenance, input, output, muffler" in n for n in notes)


def test_the_breakdown_adds_up_and_ranks_the_mode(
    turbine: power.PowerDesign,
) -> None:
    assert len(turbine.options) >= 2, turbine.rejected  # so the order is tested
    keys = []
    for p in turbine.options:
        m = p["metrics"]
        rows = m["effort"]["rows"]
        assert rows and m["eff_h"] > 0
        build_h = math.fsum(r["h"] for r in rows if r["part"] == "build")
        assert m["build_h"] == pytest.approx(build_h, rel=1e-3, abs=1e-6)
        assert m["eff_h"] == pytest.approx(m["build_h"] + m["unlock_h"], rel=1e-3)
        for r in rows:
            assert r["map"] == effort.HAND or r["mte"] is not None
            assert all(x["id"].startswith("r:") for x in r["recipes"])
            assert r["crafts"] == sum(x["crafts"] for x in r["recipes"])
            assert r["h"] == pytest.approx(math.fsum(x["h"] for x in r["recipes"]))
        ratio = p["power"]["net_eut"] / m["eff_h"]
        assert p["rank"]["eut_per_effort_h"] == pytest.approx(ratio, rel=1e-3)
        # What the effort leaves out is a count beside the number, not only in the notes
        # (on this DB the turbine controllers have no route at EV, WO-3.5 follow-up).
        left = m["effort"]["uncounted"]
        assert isinstance(left, int) and left >= 0
        assert (f"{left} items not counted" in effort.summary(m)) == (left > 0)
        keys.append((left > 0, -ratio))
        card = cards.effort_card(p)
        assert validate(card) == [] and card["tool"] == "effort_estimate"
        head = {h["key"]: h["value"] for h in card["head"]}
        assert head["not counted"].startswith(f"{left} items")
        for row in card["rows"]:  # one citation per cell: drawn as chips
            assert row[3].startswith("r:") and " " not in row[3]
    assert keys == sorted(keys)


def test_owning_only_the_turbine_unlocks_what_its_parts_need(
    conn: sqlite3.Connection,
) -> None:
    # The densest fuel's line (candidates=1): its parts' steps need machine types that have
    # an item and a route at EV, so their unlock hours are counted.
    req = dataclasses.replace(REQ, candidates=1, options=2)
    first = power.design(conn, req, DefaultBaseInputs(EV)).options[0]
    spec = _generator(first)["spec"]
    assert isinstance(spec, dict)
    d = power.design(conn, req, Owns({int(spec["mte"]): 1}))
    p = next(x for x in d.options if x["id"] == first["id"])
    m = p["metrics"]
    assert m["effort"]["unlocks"] and m["unlock_h"] > 0
    assert m["eff_h"] == pytest.approx(m["build_h"] + m["unlock_h"], rel=1e-3)
    unlocked = {r["map"] for r in m["effort"]["rows"] if r["part"] == "unlock"}
    assert unlocked
