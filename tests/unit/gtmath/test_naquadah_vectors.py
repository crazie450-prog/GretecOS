"""The Large Naquadah Reactor oracle's vectors, replayed through the port (WO-4.8, D326).

``vectors/naquadah.tsv`` holds what GT 5.09.54.265's own ``MTELargeNaquadahReactor`` computed
(``oracle/make_naquadah_vectors.py`` wrote it by running GT's jar): its own coolant and excited
lists and the liquid-air rate (the ``lists`` row), the fuel map's recipes, and per case every run
the reactor made. Every run must come out of ``gregtec.planner.gtmath.naquadah`` the same, to the
litre and EU. CLAUDE.md: any change under ``planner/gtmath`` must keep these green.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

from gregtec.planner.gtmath import GT_COMMIT, GT_VERSION
from gregtec.planner.gtmath import naquadah as nq
from gregtec.planner.gtmath.tiers import V

HERE = Path(__file__).resolve().parent
VECTORS = HERE / "vectors"


def _load() -> ModuleType:
    path = HERE / "oracle" / "make_naquadah_vectors.py"
    spec = importlib.util.spec_from_file_location("gtmath_make_naquadah_vectors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MNV = _load()
Fields = dict[str, str]


def _rows() -> list[tuple[str, Fields, Fields]]:
    lines = (VECTORS / "naquadah.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith(f"# GT {GT_VERSION} (GT5-Unofficial commit {GT_COMMIT})")
    body = [line for line in lines if not line.startswith("#")]
    assert body[0] == "id\tinputs\toutputs"
    rows = []
    for line in body[1:]:
        case_id, inputs, outputs = line.split("\t")
        rows.append((case_id, MNV.parse_fields(inputs), MNV.parse_fields(outputs)))
    return rows


ROWS = _rows()
LISTS = ROWS[0][2]
RUNS = ROWS[1:]


def _boosts(prefix: str) -> tuple[nq.Boost, ...]:
    out = []
    i = 0
    while f"{prefix}_{i}" in LISTS:
        fluid, amount, value = LISTS[f"{prefix}_{i}"].rsplit(":", 2)
        out.append(nq.Boost(fluid, int(amount), int(value)))
        i += 1
    return tuple(out)


REACTOR = nq.Reactor(
    air=int(LISTS["liquid_air_per_second"]),
    coolants=_boosts("coolant"),
    excited=_boosts("excited"),
    air_fluid="liquidair",
)
FUELS = {
    int(k.removeprefix("fuel_")): tuple(int(x) for x in v.split(":")[:2])
    for k, v in LISTS.items()
    if k.startswith("fuel_")
}


def _cap(spec: str) -> int:
    _, tier, *amps = spec.split(":")
    return V[int(tier)] * (int(amps[0]) if amps else 1)


def test_the_lists_are_goodgenerators_defaults_at_the_pin() -> None:
    # What the oracle's GT built: the reactor's own lists in their order (the first present wins).
    assert REACTOR.air == 2400
    assert [(b.amount, b.value) for b in REACTOR.coolants] == [
        (20, 500),
        (1000, 275),
        (1000, 150),
        (1000, 105),
    ]
    assert [(b.amount, b.value) for b in REACTOR.excited] == [
        (20, 64),
        (20, 16),
        (20, 4),
        (180, 3),
        (180, 2),
    ]
    # GoodGenerator's nine fuels: (basic output EU/t, burn time) for U, Th, Pu, MkI-VI.
    assert [FUELS[i] for i in range(9)] == [
        (12960, 100),
        (2200, 500),
        (32400, 150),
        (975000, 60),
        (2300000, 70),
        (9511000, 80),
        (88540000, 100),
        (399576000, 160),
        (2077795200, 240),
    ]
    # Every recipe takes 1 L and gives 1 L of its depleted fluid.
    for k, v in LISTS.items():
        if k.startswith("fuel_"):
            assert v.split(":")[2:] == ["in", "1", "out", "1"]


def test_the_vectors_cover_every_path() -> None:
    kinds = Counter(case_id.split(":")[0] for case_id, _, _ in RUNS)
    assert kinds["steady"] == len(FUELS) * 11
    assert kinds["random"] == MNV.RANDOM_CASES
    results = Counter()
    for _, _, out in RUNS:
        for k, v in out.items():
            if k.endswith("_stopped") and v:
                results[v] += 1
            if k.endswith("_check"):
                results[f"check_{v}"] += 1
    assert results["insufficient_dynamo"] > 20
    assert results["check_fail"] > 20
    assert results["check_ok"] > 1000


@pytest.mark.parametrize(
    ("case_id", "inputs", "outputs"), RUNS, ids=[r[0] for r in RUNS]
)
def test_runs_match_gt(case_id: str, inputs: Fields, outputs: Fields) -> None:
    assert "error" not in outputs, outputs
    cap = _cap(inputs["dynamo"])
    assert int(outputs["cap"]) == cap
    index, litres = (int(x) for x in inputs["fuel"].split(":"))
    basic, duration = FUELS[index]
    tanks = nq.Tanks({"fuel": litres})
    if "air" in inputs:
        tanks.litres["liquidair"] = int(inputs["air"])

    def put(spec: str | None, boosts: tuple[nq.Boost, ...]) -> list[str]:
        names = []
        for part in (spec or "").split(",") if spec else []:
            i, n = (int(x) for x in part.split(":"))
            tanks.litres[boosts[i].fluid] = n
            names.append(boosts[i].fluid)
        return names

    coolants = put(inputs.get("coolant"), REACTOR.coolants)
    excited = put(inputs.get("excited"), REACTOR.excited)
    runs = nq.simulate(
        REACTOR,
        tanks,
        fuel="fuel",
        basic=basic,
        duration=duration,
        cap=cap,
        runs=int(inputs["runs"]),
        coolants=coolants,
        excited=excited,
    )
    got: Fields = {"cap": str(cap)}
    for n, r in enumerate(runs):
        p = f"r{n}_"
        got[p + "check"] = "ok" if r.ok else "fail"
        if not r.ok:
            break
        got.update(
            {
                p + "duration": str(r.duration),
                p + "fuel": str(r.fuel),
                p + "air": str(r.air),
                p + "coolant": str(r.coolant),
                p + "excited": str(r.excited),
                p + "depleted": str(r.depleted),
                p + "eu": str(r.eu),
                p + "eut": str(r.eut),
                p + "eff": str(r.efficiency),
                p + "stopped": r.stopped,
            }
        )
    assert got == outputs


def test_steady_is_the_tick_loop_with_full_tanks() -> None:
    """``steady`` (what the planner reads) equals the simulated loop for every full-tank case."""
    for case_id, inputs, outputs in RUNS:
        if not case_id.startswith("steady:") or case_id.endswith(":all"):
            continue
        index = int(inputs["fuel"].split(":")[0])
        basic, duration = FUELS[index]
        coolant = (
            REACTOR.coolants[int(inputs["coolant"].split(":")[0])]
            if "coolant" in inputs
            else None
        )
        excited = (
            REACTOR.excited[int(inputs["excited"].split(":")[0])]
            if "excited" in inputs
            else None
        )
        s = nq.steady(
            REACTOR, basic=basic, duration=duration, coolant=coolant, excited=excited
        )
        last = "r2_"  # the third run: steady (the first run starts from trueOutput 0)
        assert outputs[last + "check"] == "ok", case_id
        assert int(outputs[last + "fuel"]) == s.fuel, case_id
        assert int(outputs[last + "depleted"]) == s.depleted, case_id
        assert int(outputs[last + "air"]) == s.air, case_id
        assert int(outputs[last + "coolant"]) == s.coolant, case_id
        assert int(outputs[last + "excited"]) == s.excited, case_id
        assert int(outputs[last + "eut"]) == s.eut, case_id
        assert int(outputs[last + "eu"]) == s.eu, case_id


def test_steady_mki() -> None:
    mk1 = nq.steady(REACTOR, basic=975000, duration=60)
    assert (mk1.eut, mk1.checks, mk1.fuel, mk1.air, mk1.eu) == (
        975000,
        3,
        1,
        7200,
        58_500_000,
    )
    assert mk1.per_second(mk1.fuel) == pytest.approx(1 / 3)
    assert mk1.per_second(mk1.air) == 2400
    # MkII burns 70 ticks: 4 checks a run, so 9600 L of liquid air per 70 ticks, not 2400 L/s.
    mk2 = nq.steady(REACTOR, basic=2300000, duration=70)
    assert mk2.checks == 4 and mk2.per_second(mk2.air) == pytest.approx(9600 * 20 / 70)
    boosted = nq.steady(
        REACTOR,
        basic=975000,
        duration=60,
        coolant=REACTOR.coolants[3],
        excited=REACTOR.excited[2],
    )
    assert (boosted.eut, boosted.fuel, boosted.coolant, boosted.excited) == (
        975000 * 105 * 4 // 100,
        4,
        3000,
        60,
    )


def test_steady_refuses_what_the_reactor_does_not_take() -> None:
    with pytest.raises(ValueError, match="burn time"):
        nq.steady(REACTOR, basic=1, duration=0)
    with pytest.raises(ValueError, match="coolants"):
        nq.steady(REACTOR, basic=1, duration=20, coolant=nq.Boost("water", 1, 999))
    with pytest.raises(ValueError, match="excited"):
        nq.steady(REACTOR, basic=1, duration=20, excited=nq.Boost("water", 1, 9))
