"""The OC agent's GT and item names against a real export (WO-7.2, D213, D214).

The agent reads each GT block's meta tile entity name with Computronics' ``getName()`` (no OC or
Computronics method gives the ``mte_id`` itself) and reads the names in its ``power_names``
(default ``multimachine.supercapacitor``, the LSC) as power storage; the core maps a name to
``mte_id``s through the export's ``meta_name`` (docs/contracts/oc_bridge.md, Identity). This checks
that mapping on a harness run of GregtecLink's export, whose machines are GT 5.09.54.265's own:
the default storage name is the LSC, every name in the golden telemetry examples is exactly one
machine, and a name shared by several ``mte_id``s is either a controller and its deprecated twins
(GT's ``DEPRECATED - Controller will be removed`` tooltip, the ``deprecated`` machine fact) or one
of TecTech's Tesla coils, whose four sizes share a name. Items go by registry name and damage: the
golden examples' watchlist and craft, and the config example's watchlist, are each one item of a
DB built from the same run. As the other harness tests, only with GOS_REQUIRE_HARNESS_RUN=1 after
the server harness (mod/README.md); nothing is written outside tmp_path.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from gregtec.db import build
from gregtec.ingest import collect, machine_facts, machines

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "fixtures" / "oc"
AGENT = REPO / "oc" / "gos-agent.lua"
CONFIG_EXAMPLE = REPO / "oc" / "gos-agent.cfg.example"
TESLA = ".MTETeslaCoil"


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A GOS_HOME with the server harness's newest run, collected as ``gos export collect`` does."""
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("oc-names")
    collect.collect(game, home, 1, allow_harness=True)
    return home


@pytest.fixture(scope="module")
def by_name(home: Path) -> dict[str, list[machines.Machine]]:
    (run,) = (home / collect.DATA_EXPORTS).iterdir()
    out: dict[str, list[machines.Machine]] = {}
    for m in machines.read_machines(run):
        if m.meta_name:
            out.setdefault(m.meta_name, []).append(m)
    return out


@pytest.fixture(scope="module")
def db(home: Path) -> Path:
    return build.build(home, allow_harness=True).db


def _deprecated(m: machines.Machine) -> bool:
    """The DB build's own reading of the tooltip (machine_fact ``deprecated``)."""
    facts = machine_facts.parse_tooltip(m.tooltip, bool(m.recipe_maps))
    return any(f.key == "deprecated" for f in facts)


def _default_power_names() -> list[str]:
    text = AGENT.read_text(encoding="utf-8")
    found = re.search(r"power_names = \{([^}]*)\}", text)
    assert found, "gos-agent.lua's DEFAULTS.power_names"
    return re.findall(r'"([^"]+)"', found.group(1))


def test_the_default_storage_name_is_the_lsc(
    by_name: dict[str, list[machines.Machine]],
) -> None:
    names = _default_power_names()
    assert names == ["multimachine.supercapacitor"]
    (lsc,) = by_name["multimachine.supercapacitor"]
    assert lsc.class_name.endswith(".MTELapotronicSuperCapacitor")
    assert lsc.multiblock


def test_the_golden_names_are_real_machines(
    by_name: dict[str, list[machines.Machine]],
) -> None:
    for path in GOLDEN.glob("telemetry-*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for entry in doc["power"] + doc["machines"]:
            found = by_name.get(entry["name"], [])
            assert len(found) == 1, (
                f"{path.name}: {entry['name']} is {len(found)} machines"
            )
    (ebf,) = by_name["multimachine.blastfurnace"]
    assert ebf.multiblock
    (buffer,) = by_name["batterybuffer.16.tier.04"]
    assert buffer.class_name.endswith(".MTEBasicBatteryBuffer")


def test_a_shared_name_is_a_deprecated_twin_or_a_tesla_coil(
    by_name: dict[str, list[machines.Machine]],
) -> None:
    """D214: the core takes the one machine of a name that isn't deprecated; it keeps a Tesla
    coil's four sizes as a set. Any other kind of sharing is new and needs the contract updated."""
    kinds: Counter[str] = Counter()
    other: dict[str, list[int]] = {}
    for name, ms in by_name.items():
        if len(ms) == 1:
            kinds["one"] += 1
            continue
        live = [m for m in ms if not _deprecated(m)]
        if len(live) == 1:
            kinds["deprecated twin"] += 1
        elif all(m.class_name.endswith(TESLA) for m in ms) and len(ms) == 4:
            kinds["tesla sizes"] += 1
        else:
            other[name] = [m.mte_id for m in ms]
    assert other == {}
    assert kinds["one"] > 1000, "the run's machine pass looks short"
    assert kinds["deprecated twin"] > 0, kinds
    assert kinds["tesla sizes"] > 0, kinds
    tiers = Counter(n.split(".")[0] for n, ms in by_name.items() if len(ms) == 1)
    assert tiers["batterybuffer"] >= 10


def _watched(path: Path) -> list[tuple[str, int]]:
    """(name, damage) of every watchlist entry in the config example."""
    text = path.read_text(encoding="utf-8")
    pairs = re.findall(r'\{ name = "([^"]+)", damage = (\d+) \}', text)
    return [(n, int(d)) for n, d in pairs]


def test_the_watched_and_crafted_items_are_items_of_the_db(db: Path) -> None:
    """The core resolves the agent's items by registry name and damage, never a numeric id."""
    wanted: set[tuple[str, int]] = set(_watched(CONFIG_EXAMPLE))
    assert len(wanted) == 2, "the config example's watchlist"
    for path in GOLDEN.glob("telemetry-*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        wanted |= {
            (i["name"], i["damage"]) for i in doc.get("ae2", {}).get("items", [])
        }
        wanted |= {(c["name"], c["damage"]) for c in doc["crafts"]}
    for path in GOLDEN.glob("reply-*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        wanted |= {
            (c["args"]["name"], c["args"]["damage"])
            for c in doc["cmds"]
            if c["kind"] == "craft"
        }
    conn = sqlite3.connect(db)
    try:
        for name, damage in sorted(wanted):
            found = conn.execute(
                "SELECT name_en FROM item WHERE registry_name = ? AND meta = ?"
                " AND nbt_hash = ''",
                (name, damage),
            ).fetchall()
            assert len(found) == 1, f"{name}@{damage}: {found}"
        steel = conn.execute(
            "SELECT name_en FROM item WHERE registry_name = ? AND meta = ?",
            ("gregtech:gt.metaitem.01", 11305),
        ).fetchall()
    finally:
        conn.close()
    assert steel == [("Steel Ingot",)]
    assert len(wanted) >= 4
