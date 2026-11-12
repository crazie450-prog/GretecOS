"""Write the turbine oracle's vectors (WO-4.1, D231): GT's own turbine classes, run on cases.

GT 5.09.54.265 has no unit tests for ``TurbineStatCalculator`` or the turbines' ``fluidIntoPower``.
So the port in ``core/gregtec/planner/gtmath/turbine.py`` is checked against GT's real classes, as
WO-3.1's oracle does for the overclock math (D174): this script makes cases, runs
``GtTurbineOracle.java`` on them with GT's jar and writes what GT computed to
``../vectors/rotor.tsv`` (every rotor material GT defines x the four rotor sizes: the material's
stats and every ``TurbineStatCalculator`` value) and ``../vectors/turbine.tsv`` (``fluidIntoPower``
of the five 2.9 large turbines and their five legacy controllers: named Lab set-ups, three rotor
set-ups per controller at four flows, tight and loose; the flow edges around each rotor's
optimal and maximum flow; and a seeded random sample). ``../test_turbine_vectors.py`` replays
every row through the port. Run it again after a GT pin bump (docs/runbooks/pack_update.md):

    uv run python tests/unit/gtmath/oracle/make_turbine_vectors.py

Like make_vectors.py it needs a JDK and takes the classpath from the mod's Gradle build unless
``--classpath`` names it. Nothing of GT is copied: the files hold inputs and GT's outputs. The
oracle runs twice: first the rotors, whose stats then place the flows of the power cases.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
import random
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from gregtec.planner.gtmath import GT_COMMIT, GT_VERSION
from gregtec.planner.gtmath.jmath import INT_MAX
from gregtec.planner.gtmath.turbine import (
    Kind,
    RotorMaterial,
    RotorSize,
    RotorStats,
    dynamo_cap,
    fluid_into_power,
)

HERE = Path(__file__).resolve().parent
VECTORS = HERE.parent / "vectors"
SEED = 410  # WO-4.1
RANDOM_POWER = 1500
ORACLE_TIMEOUT_S = 600

# GT's rotor item ids (IDMetaTool01: TURBINE_SMALL, TURBINE, TURBINE_LARGE, TURBINE_HUGE).
SIZES = {170: "small", 172: "normal", 174: "large", 176: "huge"}
# The oracle's controller names: the five large turbines, new and legacy.
TURBINES = {
    "steam": (Kind.STEAM, False),
    "hp_steam": (Kind.HP_STEAM, False),
    "sc_steam": (Kind.SC_STEAM, False),
    "gas": (Kind.GAS, False),
    "plasma": (Kind.PLASMA, False),
    "steam_legacy": (Kind.STEAM, True),
    "hp_steam_legacy": (Kind.HP_STEAM, True),
    "sc_steam_legacy": (Kind.SC_STEAM, True),
    "gas_legacy": (Kind.GAS, True),
    "plasma_legacy": (Kind.PLASMA, True),
}
# Three rotor set-ups per controller (WO-4.1's acceptance): materials GT's turbine rotors are
# commonly made of, one per stage of the game; each is a case input, GT computes the rest.
SETUPS = (("Titanium", 174), ("TungstenSteel", 176), ("Neutronium", 172))
# Sample fuel values for the fuelled turbines' set-ups (inputs only: the DB holds the real ones).
SETUP_FUEL = {Kind.GAS: (100, 1000), Kind.PLASMA: (100_000, 2_000_000)}

ROTOR_FLOATS = {
    "tool_speed",
    "steam_mult",
    "gas_mult",
    "plasma_mult",
    "speed_mult",
    "base_damage",
    "dur_mult",
    "base_eff",
    "loose_eff",
    "loose_steam_eff",
    "loose_gas_eff",
    "loose_plasma_eff",
    "opt_flow",
    "opt_steam",
    "opt_gas",
    "opt_plasma",
    "opt_loose_steam",
    "opt_loose_gas",
    "opt_loose_plasma",
    "steam_eut",
    "gas_eut",
    "plasma_eut",
    "loose_steam_eut",
    "loose_gas_eut",
    "loose_plasma_eut",
    "opt",
}
STR_KEYS = {"material", "turbine", "error", "names"}

Value = int | float | str
Fields = dict[str, Value]


def _load_make_vectors() -> ModuleType:
    path = HERE / "make_vectors.py"
    spec = importlib.util.spec_from_file_location("gtmath_make_vectors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encode(value: Value) -> str:
    if isinstance(value, float):
        return value.hex()
    return str(value)


def decode(key: str, text: str) -> Value:
    if key in ROTOR_FLOATS:
        return float.fromhex(text)
    if key in STR_KEYS:
        return text
    return int(text)


def parse_fields(text: str) -> Fields:
    out: Fields = {}
    for part in text.split():
        key, _, value = part.partition("=")
        out[key] = decode(key, value)
    return out


def format_fields(fields: Fields) -> str:
    return " ".join(f"{k}={encode(v)}" for k, v in fields.items())


def rotor_from(outputs: Fields, material: str, size_id: int) -> RotorStats:
    """The port's rotor for a rotor vector's outputs (the stats GT reported)."""
    return RotorStats(
        RotorSize(
            name=SIZES[size_id],
            speed_multiplier=float(outputs["speed_mult"]),
            base_damage=float(outputs["base_damage"]),
            durability_multiplier=float(outputs["dur_mult"]),
        ),
        RotorMaterial(
            name=material,
            tool_speed=float(outputs["tool_speed"]),
            tool_quality=int(outputs["tool_quality"]),
            durability=int(outputs["durability"]),
            steam_multiplier=float(outputs["steam_mult"]),
            gas_multiplier=float(outputs["gas_mult"]),
            plasma_multiplier=float(outputs["plasma_mult"]),
        ),
    )


def run_oracle(
    rows: Iterable[tuple[str, str, Fields]], classpath: str, jdk: Path | None
) -> dict[str, Fields]:
    """GT's outputs for ``(kind, id, inputs)`` rows, by id."""
    javac = str(jdk / "bin" / "javac") if jdk else "javac"
    java = str(jdk / "bin" / "java") if jdk else "java"
    with tempfile.TemporaryDirectory(prefix="gtturbine-oracle-") as tmp:
        work = Path(tmp)
        cases_file = work / "cases.tsv"
        with cases_file.open("w", encoding="utf-8", newline="\n") as out:
            for kind, case_id, fields in rows:
                cols = [kind, case_id, *(f"{k}={encode(v)}" for k, v in fields.items())]
                out.write("\t".join(cols) + "\n")
        classes = work / "classes"
        subprocess.run(
            [
                javac,
                "-nowarn",
                "-proc:none",
                "-XDignore.symbol.file",  # sun.misc.Unsafe, to set up registries outside the game
                "-cp",
                classpath,
                "-d",
                str(classes),
                str(HERE / "GtTurbineOracle.java"),
            ],
            check=True,
        )
        results = work / "results.tsv"
        subprocess.run(
            [
                java,
                "-cp",
                os.pathsep.join([str(classes), classpath]),
                "GtTurbineOracle",
                str(cases_file),
                str(results),
            ],
            check=True,
            timeout=ORACLE_TIMEOUT_S,
            cwd=work,
        )
        out_fields: dict[str, Fields] = {}
        for line in results.read_text(encoding="utf-8").splitlines():
            case_id, *cols = line.split("\t")
            out_fields[case_id] = {
                k: decode(k, v) for k, _, v in (c.partition("=") for c in cols)
            }
        return out_fields


def _caps(rng: random.Random) -> int:
    tier = rng.randrange(0, 15)
    return rng.choice(
        [
            dynamo_cap(tier, rng.choice([1, 1, 4, 16, 64])),
            dynamo_cap(tier),
            0,
            rng.randrange(1, 2**40),
            2**33,
        ]
    )


def _fuel(rng: random.Random, kind: Kind) -> int:
    if kind is Kind.GAS:
        return max(1, round(math.exp(rng.uniform(0, math.log(5_000_000)))))
    if kind is Kind.PLASMA:
        return max(1, round(math.exp(rng.uniform(0, math.log(2**30)))))
    return 0


def _flows(power_max: int, optimal: int, rng: random.Random | None = None) -> list[int]:
    """Litres to offer: around the optimal and the maximum flow (and 1 L)."""
    pts = {
        1,
        optimal - 1,
        optimal,
        optimal + 1,
        power_max - 1,
        power_max,
        power_max + 1,
    }
    pts |= {optimal // 2, 2 * power_max, INT_MAX}
    valid = sorted(p for p in pts if 1 <= p <= INT_MAX)
    return [rng.choice(valid)] if rng is not None else valid


def power_cases(
    rotors: dict[tuple[str, int], RotorStats], seed: int
) -> list[tuple[str, Fields]]:
    rng = random.Random(seed)
    cases: list[tuple[str, Fields]] = []
    for name, (kind, legacy) in TURBINES.items():
        fuels = SETUP_FUEL.get(kind, (0,))
        for material, size in SETUPS:
            rotor = rotors[(material, size)]
            for loose in (0, 1):
                for fuel in fuels:
                    p = fluid_into_power(
                        kind,
                        rotor,
                        available=INT_MAX,
                        cap=2**62,
                        fuel_value=fuel,
                        loose=bool(loose),
                        legacy=legacy,
                    )
                    optimal = int(p.optimal_flow)
                    # The Lab set-up: an IV dynamo (one plain hatch, 1 A) and flows at half the
                    # optimal, the optimal, the maximum and twice the maximum.
                    for label, flow in (
                        ("half", max(1, optimal // 2)),
                        ("optimal", max(1, optimal)),
                        ("max", max(1, p.max_flow)),
                        ("double", max(1, 2 * p.max_flow)),
                    ):
                        fields: Fields = {
                            "turbine": name,
                            "material": material,
                            "size": size,
                            "loose": loose,
                            "fuel": fuel,
                            "cap": dynamo_cap(5),
                            "available": min(flow, INT_MAX),
                        }
                        tag = f"{name}:{material}-{SIZES[size]}-{'loose' if loose else 'tight'}"
                        cases.append((f"setup:{tag}:f{fuel}:{label}", fields))
    # Flow edges for every controller with a sample of rotors.
    keys = sorted(rotors)
    for name, (kind, legacy) in TURBINES.items():
        for n, key in enumerate(rng.sample(keys, 12)):
            rotor = rotors[key]
            loose = n % 2
            fuel = _fuel(rng, kind)
            p = fluid_into_power(
                kind,
                rotor,
                available=INT_MAX,
                cap=2**62,
                fuel_value=fuel,
                loose=bool(loose),
                legacy=legacy,
            )
            for flow in _flows(p.max_flow, int(p.optimal_flow)):
                cases.append(
                    (
                        f"edge:{name}:{n:02d}:{flow}",
                        {
                            "turbine": name,
                            "material": key[0],
                            "size": key[1],
                            "loose": loose,
                            "fuel": fuel,
                            "cap": _caps(rng),
                            "available": flow,
                        },
                    )
                )
    for n in range(RANDOM_POWER):
        name = rng.choice(sorted(TURBINES))
        kind, legacy = TURBINES[name]
        key = rng.choice(keys)
        loose = rng.randrange(2)
        fuel = _fuel(rng, kind)
        p = fluid_into_power(
            kind,
            rotors[key],
            available=INT_MAX,
            cap=2**62,
            fuel_value=fuel,
            loose=bool(loose),
            legacy=legacy,
        )
        flow = _flows(p.max_flow, int(p.optimal_flow), rng)[0]
        cases.append(
            (
                f"fuzz:{n:04d}",
                {
                    "turbine": name,
                    "material": key[0],
                    "size": key[1],
                    "loose": loose,
                    "fuel": fuel,
                    "cap": _caps(rng),
                    "available": flow,
                },
            )
        )
    return cases


def write_vectors(
    path: Path, header: str, rows: list[tuple[str, Fields, Fields]]
) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as out:
        out.write(header)
        out.write("id\tinputs\toutputs\n")
        for case_id, inputs, outputs in rows:
            out.write(f"{case_id}\t{format_fields(inputs)}\t{format_fields(outputs)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--classpath",
        help="GT's jar and its libraries (default: ask the mod's Gradle build)",
    )
    parser.add_argument(
        "--jdk", type=Path, help="a JDK's home (default: javac and java on PATH)"
    )
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=VECTORS)
    args = parser.parse_args(argv)
    classpath = args.classpath or _load_make_vectors().gradle_classpath()

    names = run_oracle([("list", "names", {})], classpath, args.jdk)["names"]["names"]
    materials = sorted(str(names).split(","))
    rotor_rows = [
        (f"{m}:{SIZES[s]}", {"material": m, "size": s})
        for m in materials
        for s in SIZES
    ]
    rotor_out = run_oracle(
        [("rotor", i, f) for i, f in rotor_rows], classpath, args.jdk
    )
    rotors: dict[tuple[str, int], RotorStats] = {}
    for case_id, fields in rotor_rows:
        out = rotor_out[case_id]
        if "error" in out:
            print(f"GT refused rotor {case_id}: {out['error']}", file=sys.stderr)
            return 1
        rotors[(str(fields["material"]), int(fields["size"]))] = rotor_from(
            out, str(fields["material"]), int(fields["size"])
        )
    for material, size in SETUPS:
        if (material, size) not in rotors:
            print(f"GT has no rotor material {material}", file=sys.stderr)
            return 1
    power_rows = power_cases(rotors, args.seed)
    power_out = run_oracle(
        [("power", i, f) for i, f in power_rows], classpath, args.jdk
    )

    args.out.mkdir(parents=True, exist_ok=True)
    header = (
        f"# GT {GT_VERSION} (GT5-Unofficial commit {GT_COMMIT}): what its own TurbineStatCalculator\n"
        f"# and large turbines' fluidIntoPower computed for these inputs. Written by\n"
        f"# tests/unit/gtmath/oracle/make_turbine_vectors.py (seed {args.seed}); replayed by\n"
        f"# test_turbine_vectors.py. Floats are in hex. Do not edit.\n"
    )
    write_vectors(
        args.out / "rotor.tsv",
        header,
        [(i, f, rotor_out[i]) for i, f in rotor_rows],
    )
    write_vectors(
        args.out / "turbine.tsv",
        header,
        [(i, f, power_out[i]) for i, f in power_rows],
    )
    print(
        f"{len(rotor_rows)} rotor and {len(power_rows)} turbine vectors in {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
