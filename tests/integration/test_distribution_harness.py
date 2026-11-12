"""Storage and distribution (WO-4.5, D240-D242) on a real server harness DB.

Runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the server harness; locally:
mod/README.md) and writes nothing outside tmp_path. §8 row 4.5's three scenarios on the game's
own rows: a deficit window sized to a buffer, an amp overload caught on a cable run, and a tier
mismatch that needs a transformer, each expected value derived here from the DB. Also the V10
evidence: every GT cable's stats come from its exported tooltip.
"""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import distribution as dist
from gregtec.planner import ledger
from gregtec.planner.gtmath.tiers import V

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("distribution-harness")
    collect.collect(game, h, 1, allow_harness=True)
    build.build(h, allow_harness=True)
    c = access.connect(access.db_path(h), readonly=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def cat(conn: sqlite3.Connection) -> dist.Catalog:
    return dist.catalog(conn)


def test_every_gt_cable_has_its_stats_from_the_tooltip(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    cable_classes = (
        "gregtech.api.metatileentity.implementations.MTECable",
        "gtPlusPlus.xmod.gregtech.api.metatileentity.implementations.GTPPMTECable",
    )
    ids = {
        r[0]
        for r in conn.execute(
            f"SELECT mte_id FROM machine WHERE class_name IN ({','.join('?' * 2)})",
            cable_classes,
        )
    }
    assert len(ids) >= 500
    assert ids == {c.mte_id for c in cat.cables}  # all of them, and nothing else
    tin = dist.find_cable(cat, "1x Tin Wire")
    assert tin.voltage == V[1] and tin.amps >= 1 and tin.loss > 0
    assert all(c.voltage in V for c in cat.cables)


def test_the_storage_rows_match_gts_voltages(cat: dist.Catalog) -> None:
    assert cat.run_id is not None
    for b in cat.buffers:
        assert b.eu_out == V[b.tier] and b.slots in (1, 4, 9, 16)
    gt = [t for t in cat.transformers if t.eu_in != t.eu_out]
    assert gt
    for t in gt:
        lo, hi = V[t.tier], V[t.tier + 1]
        if t.mode == "down":
            assert (t.eu_in, t.eu_out) == (hi, lo) and t.amps_out > t.amps_in
        else:
            assert (t.eu_in, t.eu_out) == (lo, hi) and t.amps_in > t.amps_out
    assert {b.tier for b in cat.batteries} >= set(range(1, 9))
    assert {c.name for c in cat.cells if c.capacity > 0} >= {
        "EV",
        "IV",
        "LuV",
        "ZPM",
        "UV",
    }


def test_scenario_a_deficit_window_is_sized_to_a_buffer(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    peak, gen, window = 2400.0, 1800.0, 120.0
    s = dist.size_buffer(cat, "HV", peak, gen, window)
    run = cat.run_id
    buf = conn.execute(
        "SELECT mte_id, slots, eu_out, eu_store FROM battery_buffer WHERE run_id = ? AND tier = 3"
        " ORDER BY slots DESC, mte_id LIMIT 1",
        (run,),
    ).fetchone()
    bat = conn.execute(
        "SELECT registry_name, meta, max_charge FROM battery WHERE run_id = ? AND tier = 3"
        " AND rechargeable AND nbt_hash = '' AND meta != 32767"
        " ORDER BY max_charge DESC, registry_name, meta LIMIT 1",
        (run,),
    ).fetchone()
    energy = math.ceil((peak - gen) * window * 20)
    unit = buf["slots"] * int(bat["max_charge"]) + buf["eu_store"]
    count = max(
        math.ceil(energy / unit),
        math.ceil((peak - gen) / (buf["slots"] * buf["eu_out"])),
    )
    assert s.energy_eu == energy and s.recommend == "battery_buffer"
    bb = s.battery_buffer
    assert bb is not None and (bb.count, bb.capacity_eu) == (count, count * unit)
    assert bb.refs == (
        f"mte:{buf['mte_id']}",
        f"item:{bat['registry_name']}@{bat['meta']}",
    )
    # a long, big window at an IV cap goes to the LSC: its biggest cell up to IV
    big = dist.size_buffer(cat, "HV", 20000, 1800, 600, tier_cap="IV")
    cell = max(
        (c for c in cat.cells if c.name in ("EV", "IV")), key=lambda c: c.capacity
    )
    e2 = math.ceil((20000 - 1800) * 600 * 20)
    assert big.recommend == "lsc" and big.lsc is not None
    assert big.lsc.count == math.ceil(e2 / cell.capacity)


def test_scenario_an_amp_overload_is_caught_on_a_cable_run(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    tin = dist.find_cable(cat, "1x Tin Wire")
    tip = conn.execute(
        "SELECT tooltip FROM machine WHERE mte_id = ?", (tin.mte_id,)
    ).fetchone()[0]
    stats = dist.parse_cable_tooltip(tip.split("\n"))
    assert stats is not None
    volts, rated, loss = stats
    amps, length = rated + 1, 10
    chk = dist.check_run([dist.Segment(tin, length)], volts, amps)
    assert not chk.ok and chk.burns == (tin.ref,)
    assert any(p.startswith("amp overload") for p in chk.problems)
    assert chk.loss_per_amp == loss * length
    assert chk.voltage_out == max(0, volts - loss * length)
    # the same run within the cable's amps passes
    assert dist.check_run([dist.Segment(tin, length)], volts, rated).ok
    fits = dist.cables_for(cat, volts, amps)
    assert fits and all(c.amps >= amps and c.voltage >= volts for c in fits)


def test_scenario_a_tier_mismatch_needs_a_transformer(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    led = ledger.build(
        [
            ledger.Line(
                ledger.Entry(
                    "pl-lv",
                    "production",
                    "LV line",
                    (ledger.TierDraw("LV", 100.0, 120.0, 6),),
                    None,
                    (),
                ),
                "accepted",
            ),
            ledger.Line(
                ledger.Entry(
                    "pw-ev",
                    "power",
                    "EV source",
                    (),
                    ledger.Source("EV", 4000.0, 4000.0, 2, "gen"),
                    (),
                ),
                "accepted",
            ),
        ]
    )
    assert any(
        w.kind == "tier_mismatch" and w.tier == "LV" and w.scope == "all"
        for w in led.warnings
    )
    rep = dist.from_ledger(cat, led.tiers["all"], 60)
    [plan] = rep.transformers
    need, expect = 6.0, []
    for tier in (1, 2, 3):  # LV, MV, HV transformers, from the lines back to EV
        units = conn.execute(
            "SELECT mte_id, eu_in, eu_out, amps_out FROM transformer WHERE run_id = ?"
            " AND tier = ? AND mode = 'down' AND eu_in > eu_out",
            (cat.run_id, tier),
        ).fetchall()
        best = min(
            units,
            key=lambda u: (math.ceil(need / u["amps_out"]), u["amps_out"], u["mte_id"]),
        )
        expect.append((f"mte:{best['mte_id']}", math.ceil(need / best["amps_out"])))
        need = math.ceil(need * best["eu_out"] / best["eu_in"])
    expect.reverse()
    assert [(s.unit.ref, s.count) for s in plan.steps] == expect
    assert [s.high for s in plan.steps] == ["EV", "HV", "MV"]
    assert plan.source_amps == need
    assert rep.buffers == []  # EV's 4000 EU/t covers the LV peak
