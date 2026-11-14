"""Storage and distribution (WO-4.5, D240-D242): gt_power.json's storage read and written to the
DB, cable tooltips parsed, buffer sizing, cable runs, transformer placement, the ledger view and
``gos dist``.

The three §8 scenarios (a deficit window sized to a buffer, an amp overload caught on a cable run,
a tier mismatch needing a transformer) each derive their expected values from the DB rows in the
test, never from numbers typed next to them.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec import cli
from gregtec.db import access
from gregtec.db import power as power_db
from gregtec.ingest import power as power_ingest
from gregtec.planner import distribution as dist
from gregtec.planner import distributioncli, ledger
from gregtec.planner.gtmath.tiers import VN, V

GT = "gregtech:gt.metaitem.01"
LSC = "kekztech:kekztech_lapotronicenergyunit_block"


def _mode(eu_in: int, a_in: int, eu_out: int, a_out: int) -> dict[str, int]:
    return {"eu_in": eu_in, "amps_in": a_in, "eu_out": eu_out, "amps_out": a_out}


def _transformer(
    mte: int, cls: str, tier: int, a: tuple[int, int, int, int]
) -> dict[str, Any]:
    # a: down amps in/out, up amps in/out
    return {
        "mte_id": mte,
        "class": cls,
        "tier": tier,
        "down": _mode(V[tier + 1], a[0], V[tier], a[1]),
        "up": _mode(V[tier], a[2], V[tier + 1], a[3]),
    }


STORAGE = {
    "batteries": [
        {"battery": {"item": GT, "meta": 32518}, "ore": ["batteryLV"], "tier": 1,
         "max_charge": 100000.0, "transfer_limit": 32.0, "rechargeable": True},
        {"battery": {"item": GT, "meta": 32519}, "ore": ["batteryLV"], "tier": 1,
         "max_charge": 50000.0, "transfer_limit": 32.0, "rechargeable": True},
        {"battery": {"item": GT, "meta": 32530}, "ore": ["batteryHV"], "tier": 3,
         "max_charge": 9000000.0, "transfer_limit": 512.0, "rechargeable": False},
        {"battery": {"item": GT, "meta": 32538}, "ore": ["batteryHV"], "tier": 3,
         "max_charge": 1600000.0, "transfer_limit": 512.0, "rechargeable": True},
        {"battery": {"item": GT, "meta": 1}, "ore": ["batteryX"], "error": "boom"},
    ],
    "buffers": [
        {"mte_id": 161, "class": "x.MTEBasicBatteryBuffer", "tier": 1, "slots": 1,
         "eu_in": 32, "eu_out": 32, "eu_store": 2048},
        {"mte_id": 191, "class": "x.MTEBasicBatteryBuffer", "tier": 1, "slots": 16,
         "eu_in": 32, "eu_out": 32, "eu_store": 32768},
        {"mte_id": 193, "class": "x.MTEBasicBatteryBuffer", "tier": 3, "slots": 16,
         "eu_in": 512, "eu_out": 512, "eu_store": 524288},
    ],
    "transformers": [
        _transformer(21, "x.MTETransformer", 1, (2, 4, 5, 1)),
        _transformer(22, "x.MTETransformer", 2, (2, 4, 5, 1)),
        _transformer(23, "x.MTETransformer", 3, (2, 4, 5, 1)),
        _transformer(878, "x.MTETransformerHiAmp", 1, (6, 16, 20, 4)),
        # bartworks' distributor extends MTETransformer but keeps the voltage: never a step
        {"mte_id": 12601, "class": "x.MTEEnergyDistributor", "tier": 1,
         "down": _mode(32, 64, 32, 64), "up": _mode(32, 64, 32, 64)},
    ],
    "lsc": [
        {"name": "IV", "block": {"item": LSC, "meta": 1}, "glass_tier": 2,
         "capacity": "600000000"},
        {"name": "None", "block": {"item": LSC, "meta": 6}, "glass_tier": 0, "capacity": "0"},
        {"name": "EV", "block": {"item": LSC, "meta": 7}, "glass_tier": 1,
         "capacity": "60000000"},
        {"name": "UMV", "block": {"item": LSC, "meta": 10}, "glass_tier": 9,
         "capacity": "85070591730234615847396907784232501249"},
    ],
}  # fmt: skip

CABLES = {
    1240: ("1x Tin Wire", 32, 1, 2),
    1241: ("2x Tin Wire", 32, 2, 2),
    1246: ("1x Tin Cable", 32, 1, 1),
    1420: ("1x Gold Wire", 512, 3, 6),
    1428: ("4x Gold Cable", 512, 12, 2),
    2341: ("2x Superconductor HV Wire", 512, 12, 0),
}
# GT's own fields, as the power pass writes them (review M1)
STORAGE["cables"] = [
    {"mte_id": mte, "voltage": v, "amperage": a, "loss": loss}
    for mte, (_n, v, a, loss) in CABLES.items()
]


def _tooltip(v: int, a: int, loss: int, sep: str = ",") -> str:
    # as GT writes it (with colour codes, numbers grouped in the JVM's locale) and `gos db build`
    # stores it (lines joined)
    def n(x: int) -> str:
        return f"{x:,}".replace(",", sep)

    return "\n".join(
        [
            f"Max Voltage: §e{n(v)}§7 (§2{VN[V.index(v)]}§7)",
            f"Max Amperage: §b{n(a)}§7",
            f"Loss/Meter/Ampere: §c{n(loss)}§7 EU-Volt",
        ]
    )


def _db(path: Path, storage: dict[str, Any] = STORAGE) -> sqlite3.Connection:
    conn = access.connect(path)
    conn.execute(
        "INSERT INTO export_run(id, run_key, fingerprint) VALUES (1, 'r1', 'f')"
    )
    for mte, (name, v, a, loss) in CABLES.items():
        conn.execute(
            "INSERT INTO machine(mte_id, class_name, name_en, tooltip) VALUES (?,?,?,?)",
            (
                mte,
                "gregtech.api.metatileentity.implementations.MTECable",
                name,
                _tooltip(v, a, loss),
            ),
        )
    names = {161: "Small LV Battery Buffer", 191: "Huge LV Battery Buffer",
             193: "Huge HV Battery Buffer", 21: "Low Voltage Transformer",
             22: "Medium Voltage Transformer", 23: "High Voltage Transformer",
             878: "Low Voltage Hi-Amp Transformer", 12601: "Energy Distributor",
             1000: "EBF"}  # fmt: skip
    for mte, name in names.items():
        tip = "Machine Type: Blast Furnace" if mte == 1000 else ""
        conn.execute(
            "INSERT INTO machine(mte_id, class_name, name_en, tooltip) VALUES (?,?,?,?)",
            (mte, "x", name, tip),
        )
    conn.executemany(
        "INSERT INTO item(registry_name, meta, nbt_hash, name_en) VALUES (?,?,'',?)",
        [(GT, 32518, "Small Lithium Battery"), (GT, 32538, "Large Lithium Battery")],
    )
    power_db.write(conn, 1, _power(storage))
    return conn


def _power(storage: dict[str, Any] = STORAGE) -> power_ingest.Power:
    return power_ingest.parse(
        {"sizes": [], "materials": [], "generators": [], "storage": storage}
    )


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = _db(tmp_path / "gos.sqlite")
    yield c
    c.close()


@pytest.fixture
def cat(conn: sqlite3.Connection) -> dist.Catalog:
    return dist.catalog(conn)


def row(conn: sqlite3.Connection, sql: str, *args: object) -> sqlite3.Row:
    r = conn.execute(sql, args).fetchone()
    assert r is not None
    return r


# --- the export and the DB ---------------------------------------------------------------------


def test_storage_reads_from_gt_power_json() -> None:
    s = _power().storage
    assert s is not None
    assert [b.meta for b in s.batteries] == [
        32518,
        32519,
        32530,
        32538,
    ]  # the error row is out
    assert not s.batteries[2].rechargeable and s.batteries[0].ores == ("batteryLV",)
    assert s.buffers[1].slots == 16 and s.transformers[0].down.amps_out == 4
    assert s.lsc[-1].capacity == int(STORAGE["lsc"][-1]["capacity"])  # past 64 bits
    assert (
        power_ingest.parse({"sizes": [], "materials": [], "generators": []}).storage
        is None
    )
    bad = {**STORAGE, "lsc": [{"name": "IV", "block": {"item": LSC, "meta": 1},
                               "glass_tier": 2, "capacity": 6e8}]}  # fmt: skip
    with pytest.raises(power_ingest.FormatError):
        power_ingest.parse(
            {"sizes": [], "materials": [], "generators": [], "storage": bad}
        )


def test_the_storage_tables_hold_the_run(conn: sqlite3.Connection) -> None:
    assert row(conn, "SELECT count(*) FROM battery WHERE run_id = 1")[0] == 4
    assert row(conn, "SELECT count(*) FROM cable WHERE run_id = 1")[0] == len(CABLES)
    assert (
        row(conn, "SELECT count(*) FROM transformer WHERE run_id = 1")[0] == 10
    )  # 2 modes
    big = row(conn, "SELECT capacity FROM lsc_cell WHERE name = 'UMV'")[0]
    assert int(big) == int(STORAGE["lsc"][-1]["capacity"])
    power_db.delete(conn, [1])
    for table in ("cable", "battery", "battery_buffer", "transformer", "lsc_cell"):
        assert row(conn, f"SELECT count(*) FROM {table}")[0] == 0


SCHEMA_V4 = Path(__file__).resolve().parents[2] / "fixtures" / "gos-schema-v4.sql"


def test_a_version_4_db_gets_the_storage_tables(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    c = sqlite3.connect(path)
    try:
        c.executescript(SCHEMA_V4.read_text(encoding="utf-8"))
        c.execute("INSERT INTO item(registry_name, meta) VALUES ('minecraft:stone', 0)")
        c.commit()
        assert c.execute("PRAGMA user_version").fetchone() == (4,)
    finally:
        c.close()
    conn = access.connect(path)
    try:
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0]
            == access.SCHEMA_VERSION
        )
        assert access.SCHEMA_VERSION >= 5
        assert conn.execute("SELECT count(*) FROM lsc_cell").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM item").fetchone()[0] == 1
    finally:
        conn.close()


def test_cable_tooltips_parse_in_any_number_locale() -> None:
    # en, de/it (.), fr (narrow no-break space), sv (no-break space), de-CH (')
    for sep in (",", ".", "\u202f", "\u00a0", "'"):
        tip = _tooltip(2097152, 1024, 0, sep).split("\n")
        assert dist.parse_cable_tooltip(tip) == (2097152, 1024, 0), sep
    assert dist.parse_cable_tooltip(["Max Voltage: 32", "Max Amperage: 2"]) is None
    assert dist.parse_cable_tooltip(["Machine Type: Blast Furnace"]) is None
    # a number read wrong is refused, never used: not one of GT's voltages, or not the tier
    # named beside it
    for bad in ("Max Voltage: 2 (EV)", "Max Voltage: 2048 (HV)", "Max Voltage: 2049"):
        assert (
            dist.parse_cable_tooltip([bad, "Max Amperage: 1", "Loss/Meter/Ampere: 1"])
            is None
        )


def test_the_catalog_reads_cables_and_the_newest_run(cat: dist.Catalog) -> None:
    assert [c.mte_id for c in cat.cables] == sorted(
        CABLES
    )  # the EBF's tooltip is no cable
    tin = dist.find_cable(cat, "1X TIN WIRE")
    assert (tin.voltage, tin.amps, tin.loss, tin.ref) == (32, 1, 2, "mte:1240")
    assert dist.find_cable(cat, "mte:1428").name == "4x Gold Cable"
    for bad in ("mte:x", "mte:9", "Copper"):
        with pytest.raises(dist.DistributionError):
            dist.find_cable(cat, bad)
    # one-use batteries (no charge goes back in) are not storage
    assert 32530 not in {b.meta for b in cat.batteries}
    assert cat.run_id == 1 and cat.cable_source == "export" and cat.notes == ()


def _no_cable_rows(tmp_path: Path) -> sqlite3.Connection:
    old = {k: v for k, v in STORAGE.items() if k != "cables"}  # a first WO-4.5 run
    return _db(tmp_path / "old.sqlite", old)


def test_cables_fall_back_to_their_tooltips(tmp_path: Path, cat: dist.Catalog) -> None:
    conn = _no_cable_rows(tmp_path)
    try:
        conn.execute(  # a cable whose tooltip isn't en_US: counted, never guessed
            "INSERT INTO machine(mte_id, class_name, name_en, tooltip) VALUES (?,?,?,?)",
            (1300, dist.CABLE_CLASSES[1], "1x Kupferkabel", "Max. Spannung: 32"),
        )
        old = dist.catalog(conn)
    finally:
        conn.close()
    assert old.cable_source == "tooltip" and old.run_id == 1
    assert old.cables == cat.cables  # the same numbers as GT's own fields
    assert len(old.notes) == 1 and old.notes[0].startswith(
        "1 cable tooltips unreadable"
    )


def test_a_version_4_db_reads_without_storage(tmp_path: Path) -> None:
    # review M2: `gos dist` opens the DB read-only, so a v4 DB is never upgraded
    path = tmp_path / "gos.sqlite"
    c = sqlite3.connect(path)
    try:
        c.executescript(SCHEMA_V4.read_text(encoding="utf-8"))
        c.execute(
            "INSERT INTO export_run(id, run_key, fingerprint) VALUES (1, 'r1', 'f')"
        )
        c.execute(
            "INSERT INTO machine(mte_id, class_name, name_en, tooltip) VALUES (?,?,?,?)",
            (1240, dist.CABLE_CLASSES[0], "1x Tin Wire", _tooltip(32, 1, 2)),
        )
        c.commit()
    finally:
        c.close()
    conn = access.connect(path, readonly=True)
    try:
        cat = dist.catalog(conn)
    finally:
        conn.close()
    assert cat.run_id is None and cat.buffers == () and cat.cable_source == "tooltip"
    assert [c.ref for c in cat.cables] == ["mte:1240"]


def test_storage_comes_from_the_current_run_only(conn: sqlite3.Connection) -> None:
    # review L4: a newer run without storage never mixes with an older run's storage
    conn.execute(
        "INSERT INTO export_run(id, run_key, created_at, fingerprint)"
        " VALUES (2, 'r2', '2099-01-01', 'f')"
    )
    cat = dist.catalog(conn)
    assert cat.run_id is None and cat.batteries == () and cat.cable_source == "tooltip"
    assert dist.catalog(conn, run_id=1).run_id == 1


# --- §8 scenario 1: a deficit window sized to a buffer -----------------------------------------


def test_scenario_a_deficit_window_is_sized_to_a_battery_buffer(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    peak, gen, window = 2400.0, 1800.0, 120.0
    s = dist.size_buffer(cat, "HV", peak, gen, window)
    # expected from the DB rows: the HV buffer with the most slots, the smallest rechargeable HV
    # battery that keeps the count at or under MAX_BUFFERS
    buf = row(conn, "SELECT mte_id, slots, eu_out, eu_store FROM battery_buffer WHERE tier = 3"
                    " ORDER BY slots DESC LIMIT 1")  # fmt: skip
    energy = math.ceil((peak - gen) * window * 20)
    for bat in conn.execute("SELECT meta, max_charge FROM battery WHERE tier = 3"
                            " AND rechargeable ORDER BY max_charge"):  # fmt: skip
        unit = buf["slots"] * int(bat["max_charge"]) + buf["eu_store"]
        count = max(
            math.ceil(energy / unit),
            math.ceil((peak - gen) / (buf["slots"] * buf["eu_out"])),
        )
        if count <= dist.MAX_BUFFERS:
            break
    assert s.energy_eu == energy and s.deficit_eut == peak - gen
    bb = s.battery_buffer
    assert bb is not None and s.recommend == "battery_buffer"
    assert (bb.count, bb.unit_eu, bb.capacity_eu) == (count, unit, count * unit)
    assert bb.refs == (f"mte:{buf['mte_id']}", f"item:{GT}@{bat['meta']}")
    assert bb.eut_max == count * buf["slots"] * buf["eu_out"]
    assert s.lsc is None  # no LSC cell at or below HV


def test_a_long_window_goes_to_the_lsc(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    peak, gen, window = 20000.0, 1800.0, 600.0
    s = dist.size_buffer(cat, "HV", peak, gen, window, tier_cap="IV")
    energy = math.ceil((peak - gen) * window * 20)
    cell = row(conn, "SELECT name, meta, capacity FROM lsc_cell WHERE name IN ('EV', 'IV')"
                     " ORDER BY CAST(capacity AS INT) DESC LIMIT 1")  # fmt: skip
    assert s.battery_buffer is not None and s.battery_buffer.count > dist.MAX_BUFFERS
    assert s.recommend == "lsc" and s.lsc is not None
    assert s.lsc.count == math.ceil(energy / int(cell["capacity"]))
    assert s.lsc.unit_ref == f"item:{LSC}@{cell['meta']}"
    # the UMV cell is far past the cap; more buffers allowed keeps the battery buffers
    assert dist.size_buffer(cat, "HV", peak, gen, window, tier_cap="IV",
                            max_buffers=100).recommend == "battery_buffer"  # fmt: skip
    # no LSC cell under the cap: battery buffers however many
    s2 = dist.size_buffer(cat, "HV", peak, gen, window)
    assert s2.recommend == "battery_buffer" and "no LSC cell" in s2.reason


def test_the_smallest_battery_that_fits_is_picked(tmp_path: Path) -> None:
    # review L1: not the battery with the most charge (an endgame one holds ~2^63 EU)
    conn = _db(tmp_path / "gos.sqlite")
    try:
        cat = dist.catalog(conn)
    finally:
        conn.close()
    lv = sorted((b for b in cat.batteries if b.tier == 1), key=lambda b: b.max_charge)
    small, big = lv[0], lv[-1]
    unit = 16 * int(small.max_charge) + 32768  # the Huge LV buffer, mte:191
    fits = dist.size_buffer(cat, "LV", 1, 0, unit / 40)  # half a buffer of small ones
    assert fits.battery_buffer is not None and fits.battery_buffer.refs[1] == small.ref
    assert fits.battery_buffer.count == 1
    over = dist.size_buffer(cat, "LV", 1, 0, 3 * unit / 20, max_buffers=2)
    assert over.battery_buffer is not None and over.battery_buffer.refs[1] == big.ref
    none_fit = dist.size_buffer(cat, "LV", 1, 0, 1e6, max_buffers=1)
    assert none_fit.battery_buffer is not None
    assert none_fit.battery_buffer.refs[1] == big.ref  # the most charge when none fits
    with pytest.raises(dist.DistributionError):
        dist.size_buffer(cat, "LV", 32, 0, 1, max_buffers=0)


def test_no_deficit_needs_no_buffer(cat: dist.Catalog) -> None:
    s = dist.size_buffer(cat, "LV", 30, 32, 600)
    assert (s.recommend, s.energy_eu, s.battery_buffer) == ("none", 0, None)
    s = dist.size_buffer(cat, "MV", 300, 0, 10)
    assert s.recommend == "none" and s.notes  # nothing stored at MV in the DB
    for bad in ((-1, 0, 1), (1, 0, -5), (1, 0, math.nan), (math.inf, 0, 1)):
        with pytest.raises(dist.DistributionError):
            dist.size_buffer(cat, "LV", *bad)
    with pytest.raises(dist.DistributionError):
        dist.size_buffer(cat, "XV", 1, 0, 1)


# --- §8 scenario 2: an amp overload caught on a cable run --------------------------------------


def test_scenario_an_amp_overload_is_caught_on_a_cable_run(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    tin, gold = dist.find_cable(cat, "1x Tin Wire"), dist.find_cable(cat, "2x Tin Wire")
    amps, volts = 2, V[1]
    chk = dist.check_run([dist.Segment(gold, 5), dist.Segment(tin, 10)], volts, amps)
    rated = {
        r["mte_id"]: (r["voltage"], r["amperage"], r["loss"])
        for r in conn.execute(
            "SELECT mte_id, voltage, amperage, loss FROM cable WHERE mte_id IN (1240, 1241)"
        )
    }
    assert not chk.ok and chk.burns == ("mte:1240",)  # 1x tin's amps < 2 < 2x tin's
    assert rated[1240][1] < amps <= rated[1241][1]
    assert chk.max_amps == rated[1240][1]
    loss = 5 * rated[1241][2] + 10 * rated[1240][2]
    assert chk.loss_per_amp == loss and chk.voltage_out == volts - loss
    assert (chk.loss_eut, chk.delivered_eut) == (loss * amps, (volts - loss) * amps)
    assert any(p.startswith("amp overload: 1x Tin Wire") for p in chk.problems)


def test_overvoltage_and_a_run_that_loses_everything(cat: dist.Catalog) -> None:
    tin = dist.find_cable(cat, "1x Tin Cable")
    chk = dist.check_run([dist.Segment(tin, 1)], V[3], 1)
    assert chk.burns == ("mte:1246",) and "overvoltage" in chk.problems[0]
    long = dist.check_run([dist.Segment(tin, 40)], V[1], 1)
    assert long.voltage_out == 0 and long.delivered_eut == 0 and long.loss_eut == V[1]
    assert not long.ok and "nothing arrives" in long.problems[-1]
    ok = dist.check_run(
        [dist.Segment(dist.find_cable(cat, "4x Gold Cable"), 40)], V[3], 4
    )
    assert ok.ok and ok.voltage_out == V[3] - 80
    for segs, v, a in (
        ([], 32, 1),
        ([dist.Segment(tin, 0)], 32, 1),
        ([dist.Segment(tin, 1)], 0, 1),
        ([dist.Segment(tin, 1)], 32, math.nan),
    ):
        with pytest.raises(dist.DistributionError):
            dist.check_run(segs, v, a)


def test_cables_for_a_tier_lowest_loss_first(cat: dist.Catalog) -> None:
    fits = dist.cables_for(cat, V[3], 4)
    assert [c.mte_id for c in fits] == [2341, 1428]  # 1x Gold Wire carries 3 A
    assert [c.mte_id for c in dist.cables_for(cat, V[1], 1)] == [
        1246,
        1240,
    ]  # 1x per kind
    assert dist.cables_for(cat, V[2], 1) == []  # nothing rated for MV


# --- §8 scenario 3: a tier mismatch needing a transformer --------------------------------------


def test_scenario_a_tier_mismatch_gets_a_transformer_chain(
    conn: sqlite3.Connection, cat: dist.Catalog
) -> None:
    # lines draw 8 A at LV, the only source is at HV (the ledger's tier_mismatch)
    led = ledger.build(
        [
            _line("pl-a", draws=(("LV", 200.0, 256.0, 8),)),
            _line("pw-b", source=("HV", 2000.0, 4)),
        ]
    )
    assert ("tier_mismatch", "all", "LV") in {
        (w.kind, w.scope, w.tier) for w in led.warnings
    }
    rep = dist.from_ledger(cat, led.tiers["all"], 60)
    [plan] = rep.transformers
    assert (plan.source, plan.line, plan.amps) == ("HV", "LV", 8)
    # expected from the DB's step-down rows, per step from the lines back to the source
    need, expect = 8.0, []
    for tier in (1, 2):
        units = conn.execute(
            "SELECT mte_id, eu_in, eu_out, amps_out FROM transformer"
            " WHERE tier = ? AND mode = 'down' AND eu_in > eu_out",
            (tier,),
        ).fetchall()
        best = min(
            units,
            key=lambda u: (math.ceil(need / u["amps_out"]), u["amps_out"], u["mte_id"]),
        )
        expect.append((f"mte:{best['mte_id']}", math.ceil(need / best["amps_out"])))
        need = math.ceil(need * best["eu_out"] / best["eu_in"])
    expect.reverse()
    assert [(s.unit.ref, s.count) for s in plan.steps] == expect
    assert [(s.high, s.low, s.mode) for s in plan.steps] == [
        ("HV", "MV", "down"),
        ("MV", "LV", "down"),
    ]
    assert plan.source_amps == need
    assert "mte:12601" not in {c[0] for s in plan.steps for c in s.candidates}
    assert rep.buffers == []  # HV's 2000 EU/t covers LV's 256 EU/t peak


def test_step_up_and_same_tier(cat: dist.Catalog) -> None:
    up = dist.place_transformers(cat, "LV", "MV", 1)
    [step] = up.steps
    assert (step.mode, step.low, step.high, step.count) == ("up", "LV", "MV", 1)
    assert up.source_amps == 4  # 1 A at 128 V is 4 A at 32 V
    same = dist.place_transformers(cat, "HV", "HV", 3)
    assert same.steps == () and same.source_amps == 3
    with pytest.raises(dist.DistributionError):
        dist.place_transformers(cat, "IV", "LV", 1)  # no EV transformer in the DB
    for bad in (-1, math.nan, math.inf):
        with pytest.raises(dist.DistributionError):
            dist.place_transformers(cat, "HV", "LV", bad)


def _line(
    pid: str,
    draws: tuple[tuple[str, float, float, int], ...] = (),
    source: tuple[str, float, int] | None = None,
) -> ledger.Line:
    return ledger.Line(
        ledger.Entry(
            pid,
            "power" if source else "production",
            pid,
            tuple(ledger.TierDraw(*d) for d in draws),
            None
            if source is None
            else ledger.Source(source[0], source[1], source[1], source[2], "g"),
            (),
        ),
        "running",
    )


def test_the_ledger_buffers_only_the_base_shortfall(cat: dist.Catalog) -> None:
    led = ledger.build(
        [
            _line("pl-a", draws=(("HV", 1500.0, 2600.0, 6),)),
            _line("pl-b", draws=(("LV", 20.0, 30.0, 1),)),
            _line("pw-c", source=("HV", 2000.0, 4)),
        ]
    )
    rep = dist.from_ledger(cat, led.tiers["all"], 30)
    # the base is short 2630 - 2000 = 630 at peak: HV 600 over what is made there, LV its
    # whole 30 (nothing made at LV), together exactly the base's 630
    assert [(b.tier, b.deficit_eut) for b in rep.buffers] == [
        ("LV", 30.0),
        ("HV", 600.0),
    ]
    capped = dist.from_ledger(
        cat,
        ledger.build(
            [
                _line("pl-a", draws=(("HV", 1500.0, 2600.0, 6),)),
                _line("pl-b", draws=(("LV", 20.0, 30.0, 1),)),
                _line("pw-c", source=("HV", 2000.0, 4)),
                _line("pw-d", source=("MV", 610.0, 4)),
            ]
        ).tiers["all"],
        30,
    )
    # review L2: the base is short 2630 - 2610 = 20, shared out by the tiers' own deficits
    # (LV 30, HV 600), so together they store no more than the base's 20
    got = {b.tier: b.deficit_eut for b in capped.buffers}
    assert got == pytest.approx({"LV": 20 * 30 / 630, "HV": 20 * 600 / 630})
    assert math.fsum(got.values()) == pytest.approx(20)
    assert [p.line for p in rep.transformers] == ["LV"]
    empty = dist.from_ledger(cat, [], 30)
    assert empty.buffers == [] and empty.notes


# --- gos dist ----------------------------------------------------------------------------------


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    _db(home / "data" / "gos.sqlite").close()
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f"[paths]\ngos_home = {json.dumps(str(home))}\n", encoding="utf-8")
    return cfg


def _run(cfg: Path, *argv: str) -> tuple[int, str]:
    parser = argparse.ArgumentParser(prog="gos")
    distributioncli.add_parsers(parser.add_subparsers(dest="command", required=True))
    out = io.StringIO()
    sub, *rest = argv
    code = distributioncli.run(
        parser.parse_args(["dist", sub, "--config", str(cfg), *rest]), out
    )
    return code, out.getvalue()


def test_gos_dist_prints_and_exits_by_the_checks(tmp_path: Path) -> None:
    cfg = _home(tmp_path)
    code, text = _run(cfg, "buffer", "--tier", "HV", "--peak", "2400", "--gen", "1800",
                      "--window", "120")  # fmt: skip
    assert code == 0 and "recommend: battery_buffer" in text and "mte:193" in text
    code, text = _run(cfg, "cable", "--tier", "LV", "--amps", "2", "--segment",
                      "2x Tin Wire=5", "--segment", "mte:1240=10")  # fmt: skip
    assert (
        code == 1 and "amp overload: 1x Tin Wire" in text and "burns: mte:1240" in text
    )
    code, text = _run(cfg, "cable", "--voltage", "512", "--amps", "4", "--segment",
                      "4x Gold Cable=40", "--json")  # fmt: skip
    data = json.loads(text)
    assert code == 0 and data["ok"] and data["fits"] == ["mte:2341", "mte:1428"]
    code, text = _run(cfg, "transformer", "--from", "HV", "--to", "LV", "--amps", "8")
    assert code == 0 and "HV -> MV (down)" in text and "mte:878" in text
    code, text = _run(cfg, "ledger", "--window", "60", "--json")
    assert code == 0 and json.loads(text)["notes"]  # an empty ledger
    assert (
        _run(cfg, "cable", "--tier", "LV", "--amps", "1", "--segment", "nope")[0] == 2
    )
    assert _run(cfg, "transformer", "--from", "IV", "--to", "LV", "--amps", "1")[0] == 2


@pytest.mark.parametrize(
    "argv",
    [
        ("buffer", "--tier", "HV", "--peak", "1", "--gen", "0", "--window", "nan"),
        ("buffer", "--tier", "HV", "--peak", "1", "--gen", "0", "--window", "inf"),
        ("buffer", "--tier", "HV", "--peak", "-1", "--gen", "0", "--window", "1"),
        ("buffer", "--tier", "HV", "--peak", "1", "--gen", "0", "--window", "1",
         "--max-buffers", "0"),
        ("ledger", "--window", "1", "--max-buffers", "-2"),
        ("cable", "--voltage", "0", "--amps", "1", "--segment", "mte:1240=1"),
        ("cable", "--tier", "LV", "--amps", "inf", "--segment", "mte:1240=1"),
        ("transformer", "--from", "HV", "--to", "LV", "--amps", "nan"),
    ],
)  # fmt: skip
def test_gos_dist_refuses_bad_numbers(tmp_path: Path, argv: tuple[str, ...]) -> None:
    # review L3: argparse refuses them (exit 2) before anything is read
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path / "gos.toml", *argv)
    assert exc.value.code == 2


def test_gos_dist_on_a_version_4_db(tmp_path: Path) -> None:
    # review M2: no traceback; `cable` works from the tooltips, the others say what to do
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    c = sqlite3.connect(home / "data" / "gos.sqlite")
    try:
        c.executescript(SCHEMA_V4.read_text(encoding="utf-8"))
        c.execute(
            "INSERT INTO machine(mte_id, class_name, name_en, tooltip) VALUES (?,?,?,?)",
            (1240, dist.CABLE_CLASSES[0], "1x Tin Wire", _tooltip(32, 1, 2)),
        )
        c.commit()
    finally:
        c.close()
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f"[paths]\ngos_home = {json.dumps(str(home))}\n", encoding="utf-8")
    code, text = _run(
        cfg, "buffer", "--tier", "HV", "--peak", "1", "--gen", "0", "--window", "1"
    )
    assert code == 2 and "no storage data" in text
    code, text = _run(cfg, "cable", "--tier", "LV", "--amps", "1", "--segment",
                      "1x Tin Wire=4", "--json")  # fmt: skip
    data = json.loads(text)
    assert code == 0 and data["ok"] and data["cable_source"] == "tooltip"
    assert data["voltage_out"] == 32 - 4 * 2


def test_gos_dist_without_storage_data(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    access.connect(home / "data" / "gos.sqlite").close()
    cfg = tmp_path / "gos.toml"
    cfg.write_text(f"[paths]\ngos_home = {json.dumps(str(home))}\n", encoding="utf-8")
    code, text = _run(
        cfg, "buffer", "--tier", "HV", "--peak", "1", "--gen", "0", "--window", "1"
    )
    assert code == 2 and "no storage data" in text


def test_gos_dist_is_wired_into_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "gos.toml"
    cfg.write_text(
        f"[paths]\ngos_home = {json.dumps(str(tmp_path))}\n", encoding="utf-8"
    )
    assert cli.main(["dist", "transformer", "--config", str(cfg), "--from", "HV", "--to",
                     "LV", "--amps", "1"]) == 2  # fmt: skip
    assert "gos db build" in capsys.readouterr().out
