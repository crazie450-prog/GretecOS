"""Storage and distribution (WO-4.5, D240-D242) on a real server harness DB.

Runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after the server harness; locally:
mod/README.md) and writes nothing outside tmp_path. §8 row 4.5's three scenarios on the game's
own rows: a deficit window sized to a buffer, an amp overload caught on a cable run, and a tier
mismatch that needs a transformer, each expected value derived here from the DB. Also the V10
evidence: every GT cable's stats come from its exported fields (the ``cable`` table), and its
en_US tooltip, the fallback for older runs, reads the same numbers.
"""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gregtec.db import access
from gregtec.ingest import collect
from gregtec.planner import distribution as dist
from gregtec.planner import ledger
from gregtec.planner.gtmath.tiers import V

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def conn(
    tmp_path_factory: pytest.TempPathFactory, built_home: Callable[[Path, Path], Path]
) -> Iterator[sqlite3.Connection]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    h = tmp_path_factory.mktemp("distribution-harness")
    built_home(game, h)
    c = access.connect(access.db_path(h), readonly=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def cat(conn: sqlite3.Connection) -> dist.Catalog:
    return dist.catalog(conn)


def test_every_gt_cable_has_its_stats_from_its_fields(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    classes = dist.CABLE_CLASSES
    tips = {
        int(r[0]): str(r[1])
        for r in conn.execute(
            f"SELECT mte_id, tooltip FROM machine WHERE class_name IN"
            f" ({','.join('?' * len(classes))})",
            classes,
        )
    }
    assert len(tips) >= 500 and cat.cable_source == "export" and cat.notes == ()
    assert set(tips) == {c.mte_id for c in cat.cables}  # all of them, and nothing else
    tin = dist.find_cable(cat, "1x Tin Wire")
    assert tin.voltage == V[1] and tin.amps >= 1 and tin.loss > 0
    assert all(c.voltage in V for c in cat.cables)
    # the fallback for older runs reads the same numbers from every en_US tooltip
    for c in cat.cables:
        assert dist.parse_cable_tooltip(tips[c.mte_id].split("\n")) == (
            c.voltage,
            c.amps,
            c.loss,
        ), c.ref


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
    energy = math.ceil((peak - gen) * window * 20)
    # the smallest rechargeable HV battery that keeps the count at or under MAX_BUFFERS (D241)
    for bat in conn.execute(
        "SELECT registry_name, meta, max_charge FROM battery WHERE run_id = ? AND tier = 3"
        " AND rechargeable AND nbt_hash = '' AND meta != 32767 AND max_charge > 0"
        " ORDER BY max_charge, registry_name, meta",
        (run,),
    ):
        unit = buf["slots"] * int(bat["max_charge"]) + buf["eu_store"]
        count = max(
            math.ceil(energy / unit),
            math.ceil((peak - gen) / (buf["slots"] * buf["eu_out"])),
        )
        if count <= dist.MAX_BUFFERS:
            break
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
    volts, rated, loss = conn.execute(
        "SELECT voltage, amperage, loss FROM cable WHERE run_id = ? AND mte_id = ?",
        (cat.run_id, tin.mte_id),
    ).fetchone()
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


def test_ic2s_wildcard_batteries_are_storage(cat: dist.Catalog) -> None:
    # review M3: IC2's batteries are in the oredict only at the wildcard meta; the power pass
    # keys them at meta 0, so the Lapotron Crystal (EV) is a battery an EV buffer can take
    lapo = [b for b in cat.batteries if b.registry_name == "IC2:itemBatLamaCrystal"]
    assert [(b.meta, b.tier) for b in lapo] == [(0, 4)]
    buf = max(
        (b for b in cat.buffers if b.tier == 4), key=lambda b: (b.slots, -b.mte_id)
    )
    smaller = [
        b for b in cat.batteries if b.tier == 4 and b.max_charge < lapo[0].max_charge
    ]
    assert smaller  # GT's Small Sunnarium Battery

    def unit(charge: float) -> int:
        return buf.slots * int(charge) + buf.eu_store

    # a window that MAX_BUFFERS buffers of any smaller EV battery can't hold, but of Lapotron
    # Crystals can: without IC2's batteries it would go to the LSC
    eut = buf.eu_out
    window = unit(lapo[0].max_charge) * dist.MAX_BUFFERS // (eut * 20)
    energy = eut * window * 20
    assert all(energy > unit(b.max_charge) * dist.MAX_BUFFERS for b in smaller)
    s = dist.size_buffer(cat, "EV", eut, 0, window)
    assert s.recommend == "battery_buffer" and s.battery_buffer is not None
    assert s.battery_buffer.refs == (buf.ref, lapo[0].ref)
    assert s.battery_buffer.count == math.ceil(energy / unit(lapo[0].max_charge))
