"""The OC agent's GT names against a real export (WO-7.2, D213, D214).

The agent reads each GT block's meta tile entity name with Computronics' ``getName()`` and reads
the names in its ``power_names`` (default ``multimachine.supercapacitor``, the LSC) as power
storage; the core maps a name to the machine's ``mte_id`` through the export's ``meta_name``
(docs/contracts/oc_bridge.md, Identity). This checks both on a harness run of GregtecLink's
export, whose machines are GT 5.09.54.265's own: the default storage name is the LSC, every
machine and battery buffer name in the golden telemetry examples is exactly one machine, and a
meta name never stands for two. As the other harness tests, only with GOS_REQUIRE_HARNESS_RUN=1
after the server harness (mod/README.md); nothing is written outside tmp_path.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

import pytest

from gregtec.ingest import collect, machines

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "fixtures" / "oc"
AGENT = REPO / "oc" / "gos-agent.lua"


@pytest.fixture(scope="module")
def by_name(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[machines.Machine]]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("oc-names")
    run = collect.collect(game, home, 1, allow_harness=True)
    out: dict[str, list[machines.Machine]] = {}
    for m in machines.read_machines(run.dest):
        if m.meta_name:
            out.setdefault(m.meta_name, []).append(m)
    return out


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
            assert len(found) == 1, f"{path.name}: {entry['name']} is {len(found)} machines"
    (ebf,) = by_name["multimachine.blastfurnace"]
    assert ebf.multiblock
    (buffer,) = by_name["batterybuffer.16.tier.04"]
    assert buffer.class_name.endswith(".MTEBasicBatteryBuffer")


def test_a_meta_name_is_one_mte_id(by_name: dict[str, list[machines.Machine]]) -> None:
    """The core may key a gt_machine's name to one mte_id (D214)."""
    shared = {name: [m.mte_id for m in ms] for name, ms in by_name.items() if len(ms) > 1}
    assert shared == {}
    assert len(by_name) > 1000, "the run's machine pass looks short"
    tiers = Counter(n.split(".")[0] for n in by_name)
    assert tiers["batterybuffer"] >= 10
