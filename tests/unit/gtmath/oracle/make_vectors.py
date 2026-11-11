"""Write the GT oracle's test vectors (WO-3.1, D174): GT's own classes, run on cases for the port.

GT 5.09.54.265 has unit tests for ``OverclockCalculator`` only, none for ``ParallelHelper``. So
besides those tests (ported in ``../test_gt_unit_tests.py``), the port in
``core/gregtec/planner/gtmath`` is checked against GT's real classes: this script makes cases
(GT's unit-test set-ups, edge cases and a seeded random sample), runs ``GtMathOracle.java`` on
them with GT's jar, and writes what GT computed to ``../vectors/*.tsv``. ``test_oracle_vectors.py``
replays every row through the port. Run it again after a GT pin bump
(docs/runbooks/pack_update.md):

    cd mod && ./gradlew build     # once: resolves GT at the pin, Minecraft and Forge
    uv run python tests/unit/gtmath/oracle/make_vectors.py

It needs a JDK (``javac`` and ``java`` on PATH, or ``--jdk``); the classpath comes from the mod's
Gradle build (``classpath.init.gradle``) unless ``--classpath`` names it. Nothing of GT is copied:
the files hold inputs and GT's outputs.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import math
import os
import random
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from gregtec.planner.gtmath import GT_COMMIT, GT_VERSION
from gregtec.planner.gtmath.jmath import INT_MAX, LONG_MAX, to_float
from gregtec.planner.gtmath.overclock import Overclock
from gregtec.planner.gtmath.tiers import VP, V, coil_heat

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
VECTORS = HERE.parent / "vectors"
SEED = 310  # WO-3.1
RANDOM_OC = 1200
RANDOM_PAR = 800

OC_FIELDS = {f.name: f for f in dataclasses.fields(Overclock)}
FLOAT_KEYS = {
    "eut_modifier",
    "duration_modifier",
    "eut_increase_per_oc",
    "duration_decrease_per_oc",
    "heat_discount_exponent",
    "duration_under_one_tick",
    "par_eut_modifier",
    "mult_before",
    "mult_after",
    "heat_mult",
    "duration_multiplier",
}
BOOL_KEYS = {
    "amperage_oc",
    "laser_oc",
    "no_overclock",
    "heat_oc",
    "heat_discount",
    "default_calc",
}
STR_KEYS = {"check", "error"}

Value = int | float | bool | str
Fields = dict[str, Value]


def encode(value: Value) -> str:
    """One value as the oracle reads it: a double in Java's hex form, so every bit survives."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return value.hex()
    return str(value)


def decode(key: str, text: str) -> Value:
    """One value of a vector file, typed by its key."""
    if key in FLOAT_KEYS:
        return float.fromhex(text)
    if key in BOOL_KEYS:
        return text == "1"
    if key in STR_KEYS:
        return text
    return int(text)


def parse_fields(text: str) -> Fields:
    """``key=value key=value ...`` (a vector file's inputs or outputs column)."""
    out: Fields = {}
    for part in text.split():
        key, _, value = part.partition("=")
        out[key] = decode(key, value)
    return out


def format_fields(fields: Fields) -> str:
    return " ".join(f"{k}={encode(v)}" for k, v in fields.items())


def overclock_fields(oc: Overclock) -> Fields:
    """An ``Overclock``'s fields that differ from GT's defaults."""
    out: Fields = {}
    for name, field in OC_FIELDS.items():
        value = getattr(oc, name)
        if value is not None and value != field.default:
            out[name] = value
    return out


def overclock_from(fields: Fields) -> Overclock:
    """The ``Overclock`` a case's inputs describe (keys that aren't its fields are ignored)."""
    return Overclock(**{k: v for k, v in fields.items() if k in OC_FIELDS})  # type: ignore[arg-type]


def _log_uniform(rng: random.Random, lo: float, hi: float) -> int:
    return max(int(lo), round(math.exp(rng.uniform(math.log(lo), math.log(hi)))))


def random_overclock(rng: random.Random, *, for_parallel: bool = False) -> Overclock:
    """A random calculator: GT's tiers, odd values in between, and every option GT has."""
    tier = rng.randrange(0, 15)
    voltage = rng.choice([V[tier], V[tier], VP[tier], _log_uniform(rng, 8, 2**40)])
    amperage = rng.choice([1, 1, 1, 2, 4, 16, 64, 256, 1024, rng.randint(1, 4096)])
    recipe_eut = rng.choice(
        [
            VP[rng.randrange(0, 14)],
            V[rng.randrange(0, 14)],
            rng.randint(1, 32),
            _log_uniform(rng, 1, INT_MAX),
        ]
    )
    if not for_parallel and rng.random() < 0.05:
        recipe_eut = _log_uniform(rng, 2**31, 2**44)  # a long the calculator accepts
    parallel = rng.choice([1, 1, 2 ** rng.randint(0, 12), rng.randint(1, 100_000)])
    current = None
    if not for_parallel and rng.random() < 0.35:
        current = rng.randint(0, parallel * 4)
    laser = rng.random() < 0.15
    return Overclock(
        recipe_eut=recipe_eut,
        duration=rng.choice([_log_uniform(rng, 1, 72_000), rng.randint(1, 20), 1]),
        voltage=voltage,
        amperage=amperage,
        parallel=parallel,
        current_parallel=current,
        eut_modifier=rng.choice(
            [1.0, 1.0, 0.9, 0.8, to_float(0.9), 0.5, rng.uniform(0.05, 2.0)]
        ),
        duration_modifier=rng.choice(
            [
                1.0,
                1.0,
                0.5,
                1 / 2.25,
                to_float(1 / 2.25),
                1 / 6.0,
                rng.uniform(0.01, 3.0),
            ]
        ),
        eut_increase_per_oc=rng.choice([4.0] * 6 + [rng.uniform(1.5, 6.0)]),
        duration_decrease_per_oc=rng.choice(
            [2.0] * 4 + [4.0] * 2 + [rng.uniform(1.1, 6.0)]
        ),
        max_overclocks=rng.choice([INT_MAX] * 6 + [rng.randint(-2, 20)]),
        max_regular_overclocks=rng.choice([INT_MAX] * 3 + [rng.randint(-1, 12)]),
        max_tier_skips=rng.choice([1] * 5 + [0, 2, 3, INT_MAX]),
        amperage_oc=rng.random() < (0.8 if for_parallel else 0.5),
        laser_oc=laser,
        no_overclock=rng.random() < 0.08,
        heat_oc=rng.random() < 0.3,
        heat_discount=rng.random() < 0.3,
        recipe_heat=rng.choice([0, rng.randint(0, 12_000)]),
        machine_heat=rng.choice(
            [
                0,
                coil_heat(rng.randrange(14)) + 100 * rng.randint(-2, 12),
                rng.randint(0, 16_000),
            ]
        ),
        heat_discount_exponent=rng.choice([0.95] * 5 + [rng.uniform(0.5, 0.999)]),
        duration_under_one_tick=rng.choice([None] * 9 + [rng.uniform(0.05, 300.0)]),
    )


def edge_overclocks() -> Iterator[tuple[str, Overclock]]:
    """Corners a random sample rarely reaches: tiny and huge numbers, overflow, rounding."""
    base = Overclock(recipe_eut=30, duration=100, voltage=V[5], amperage=1)
    for d in (0, 1, 2, 3, 4, 5, 7, 128, 2**31 - 1):
        yield f"duration-{d}", dataclasses.replace(base, duration=d)
        yield f"duration-{d}-perfect", dataclasses.replace(base, duration=d).perfect()
        yield (
            f"duration-{d}-heat",
            dataclasses.replace(
                base, duration=d, heat_oc=True, heat_discount=True, machine_heat=9001
            ),
        )
        yield (
            f"duration-{d}-laser",
            dataclasses.replace(base, duration=d, laser_oc=True),
        )
    yield "recipe-0", dataclasses.replace(base, recipe_eut=0)
    yield "recipe-0-amps", dataclasses.replace(base, recipe_eut=0, amperage_oc=True)
    yield "recipe-negative", dataclasses.replace(base, recipe_eut=-30)
    yield "voltage-0", dataclasses.replace(base, voltage=0)
    yield "voltage-negative", dataclasses.replace(base, voltage=-512)
    for tier in range(16):
        yield f"tier-{tier}", dataclasses.replace(base, voltage=V[tier])
        yield (
            f"tier-{tier}-amps",
            dataclasses.replace(base, voltage=V[tier], amperage=16, amperage_oc=True),
        )
        yield (
            f"tier-{tier}-ulv",
            dataclasses.replace(base, recipe_eut=7, voltage=V[tier]),
        )
    # The machine power overflows a long (Java wraps it).
    yield (
        "machine-overflow",
        dataclasses.replace(base, voltage=2**62, amperage=4, amperage_oc=True),
    )
    yield (
        "machine-near-max",
        dataclasses.replace(base, voltage=LONG_MAX // 3, amperage=2, amperage_oc=True),
    )
    # The recipe power overflows a long.
    yield (
        "recipe-overflow",
        dataclasses.replace(base, recipe_eut=LONG_MAX // 2, parallel=4, voltage=2**40),
    )
    # EU/t past Long.MAX_VALUE: GT's "power overflow".
    yield (
        "consumption-overflow",
        dataclasses.replace(
            base,
            recipe_eut=2**40,
            parallel=2**20,
            voltage=LONG_MAX,
            amperage=1,
            amperage_oc=True,
            eut_increase_per_oc=8.0,
        ),
    )
    # getTiersAbove compares in hundredths: a recipe power a hair under a 4x step still counts.
    for power, eut_modifier in (
        (1000, 250.004 / 30),
        (1000, 250.006 / 30),
        (4096, 0.99999),
    ):
        yield (
            f"hundredths-{power}-{eut_modifier!r}",
            dataclasses.replace(
                base, voltage=power, amperage_oc=True, eut_modifier=eut_modifier
            ),
        )
    yield (
        "current-parallel-0",
        dataclasses.replace(base, parallel=8, current_parallel=0),
    )
    yield (
        "current-parallel-above",
        dataclasses.replace(base, parallel=8, current_parallel=64),
    )
    yield "max-overclocks-negative", dataclasses.replace(base, max_overclocks=-5)
    yield (
        "max-regular-negative",
        dataclasses.replace(base, laser_oc=True, max_regular_overclocks=-5),
    )
    yield "tier-skips-0", dataclasses.replace(base, max_tier_skips=0)
    yield "tier-skips-unlimited", dataclasses.replace(base, max_tier_skips=INT_MAX)
    yield "tier-skips-40", dataclasses.replace(base, max_tier_skips=40)
    yield (
        "heat-colder",
        dataclasses.replace(
            base, heat_oc=True, heat_discount=True, recipe_heat=4000, machine_heat=1801
        ),
    )
    yield (
        "heat-overflow",
        dataclasses.replace(
            base,
            heat_oc=True,
            heat_discount=True,
            recipe_heat=-(2**31),
            machine_heat=2**31 - 1,
        ),
    )
    for under in (0.0, 0.05, 0.5, 1.0, 1.5, 12.5):
        yield (
            f"under-one-tick-{under}",
            dataclasses.replace(
                base, voltage=V[9], amperage_oc=True, duration_under_one_tick=under
            ),
        )
        yield (
            f"under-one-tick-{under}-laser",
            dataclasses.replace(
                base, voltage=V[9] * 64, laser_oc=True, duration_under_one_tick=under
            ),
        )
    yield "no-overclock-modifiers", Overclock.no_oc(2_693_264_510, 100)


def random_parallel(rng: random.Random) -> Fields:
    """A random ParallelHelper case: the calculator as a multiblock's processing logic sets it
    up, its EU/t, max parallel, batch size and one input pool."""
    oc = random_overclock(rng, for_parallel=True)
    if oc.laser_oc and oc.recipe_eut < 1:
        oc = dataclasses.replace(oc, recipe_eut=1)
    fields: Fields = overclock_fields(oc)
    fields.pop("parallel", None)  # ParallelHelper sets the calculator's parallel itself
    amps = oc.amperage if oc.amperage_oc else 1
    fields["available_eut"] = rng.choice(
        [oc.voltage * amps] * 6 + [oc.voltage, _log_uniform(rng, 1, 2**45)]
    )
    fields["max_parallel"] = rng.choice(
        [1, 1, 4, 16, 64, 256, 2 ** rng.randint(0, 20), rng.randint(-1, 100_000)]
    )
    if rng.random() < 0.1:
        fields["par_eut_modifier"] = rng.uniform(0.3, 1.5)
    elif oc.eut_modifier != 1.0:
        fields["par_eut_modifier"] = oc.eut_modifier
    batch = rng.choice([1, 1, 128, rng.randint(2, 256)])
    if batch != 1:
        fields["batch_size"] = batch
    if rng.random() < 0.5:
        fields["per_craft"] = rng.choice([0, 1, 1, 2, 16, 64, rng.randint(1, 1000)])
        fields["available"] = rng.choice(
            [0, rng.randint(1, 100), _log_uniform(rng, 1, 10**7)]
        )
    if rng.random() < 0.05:
        fields["default_calc"] = True
    return fields


def edge_parallels() -> Iterator[tuple[str, Fields]]:
    """Parallel-helper corners: the power and voltage checks, the sub-tick and batch paths."""
    hv = {"recipe_eut": 120, "duration": 200, "voltage": V[3], "amperage": 2}
    multi = {**hv, "amperage_oc": True, "available_eut": V[3] * 2}
    yield "power-short", {**multi, "recipe_eut": 1025, "max_parallel": 4}
    yield "power-exact", {**multi, "recipe_eut": 1024, "max_parallel": 4}
    yield (
        "voltage-skip-1",
        {**multi, "recipe_eut": 2048, "available_eut": 8192, "max_parallel": 1},
    )
    yield "voltage-skip-over", {**multi, "recipe_eut": 2049, "available_eut": 8192}
    yield "voltage-skip-0", {**multi, "recipe_eut": 513, "max_tier_skips": 0}
    yield "max-parallel-0", {**multi, "max_parallel": 0}
    yield "max-parallel-negative", {**multi, "max_parallel": -3}
    yield "recipe-eut-0", {**multi, "recipe_eut": 0, "max_parallel": 64}
    yield "no-input", {**multi, "max_parallel": 16, "per_craft": 1, "available": 0}
    yield "input-short", {**multi, "max_parallel": 16, "per_craft": 3, "available": 7}
    yield (
        "input-not-consumed",
        {**multi, "max_parallel": 16, "per_craft": 0, "available": 1},
    )
    for duration in (1, 2, 3, 63, 64, 100, 127, 128, 129, 1000):
        yield (
            f"batch-{duration}",
            {
                **multi,
                "duration": duration,
                "max_parallel": 8,
                "batch_size": 128,
            },
        )
        yield (
            f"batch-{duration}-inputs",
            {
                **multi,
                "duration": duration,
                "max_parallel": 8,
                "batch_size": 128,
                "per_craft": 1,
                "available": 100,
            },
        )
    yield "batch-1", {**multi, "max_parallel": 8, "batch_size": 1}
    yield "batch-2", {**multi, "duration": 10, "max_parallel": 8, "batch_size": 2}
    yield (
        "sub-tick",
        {
            **multi,
            "recipe_eut": 30,
            "duration": 20,
            "voltage": V[9],
            "available_eut": V[9] * 2,
        },
    )
    yield (
        "sub-tick-batch",
        {
            **multi,
            "recipe_eut": 30,
            "duration": 20,
            "voltage": V[9],
            "available_eut": V[9] * 2,
            "batch_size": 128,
            "max_parallel": 4,
        },
    )
    for under in (0.0, 0.25, 0.999, 1.0, 5.0):
        yield (
            f"supplier-{under}",
            {
                **multi,
                "max_parallel": 4,
                "duration_under_one_tick": under,
            },
        )
    yield (
        "huge-parallel",
        {
            **multi,
            "recipe_eut": 1,
            "duration": 1,
            "voltage": V[13],
            "available_eut": V[13] * 64,
            "amperage": 64,
            "max_parallel": INT_MAX,
            "batch_size": 128,
        },
    )
    yield "default-calculator", {**multi, "max_parallel": 4, "default_calc": True}
    yield (
        "heat-discount",
        {
            **multi,
            "recipe_eut": 1100,
            "heat_oc": True,
            "heat_discount": True,
            "recipe_heat": 1000,
            "machine_heat": 4000,
        },
    )


def gt_unit_cases() -> list[tuple[str, Overclock, int, int | None]]:
    """The set-ups of GT's own unit tests (``../test_gt_unit_tests.py``'s ``OC_CASES``)."""
    path = HERE.parent / "test_gt_unit_tests.py"
    spec = importlib.util.spec_from_file_location("gtmath_gt_unit_tests", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"can't load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.OC_CASES)


def cases(seed: int) -> tuple[list[tuple[str, Fields]], list[tuple[str, Fields]]]:
    """(overclock cases, parallel cases), each ``(id, inputs)``."""
    rng = random.Random(seed)
    oc: list[tuple[str, Fields]] = []
    for name, calc, _eut, _ticks in gt_unit_cases():
        oc.append((f"gt:{name}", overclock_fields(calc)))
    for name, calc in edge_overclocks():
        oc.append((f"edge:{name}", overclock_fields(calc)))
    # GT's setters refuse these (and so does Overclock, so they are written as fields).
    refused: Fields = {"recipe_eut": 30, "voltage": 512}
    oc.append(("edge:eut-increase-0", {**refused, "eut_increase_per_oc": 0.0}))
    oc.append(
        (
            "edge:duration-decrease-negative",
            {**refused, "duration_decrease_per_oc": -1.0},
        )
    )
    for n in range(RANDOM_OC):
        oc.append((f"fuzz:{n:04d}", overclock_fields(random_overclock(rng))))
    par = [(f"edge:{name}", fields) for name, fields in edge_parallels()]
    for n in range(RANDOM_PAR):
        par.append((f"fuzz:{n:04d}", random_parallel(rng)))
    return oc, par


def gradle_classpath() -> str:
    """GregtecLink's test runtime classpath, from its Gradle build (GT at the pin included)."""
    mod = REPO / "mod"
    wrapper = mod / ("gradlew.bat" if os.name == "nt" else "gradlew")
    done = subprocess.run(
        [
            str(wrapper),
            "--no-daemon",
            "--quiet",
            "--no-configuration-cache",
            "--init-script",
            str(HERE / "classpath.init.gradle"),
            "gtmathClasspath",
        ],
        cwd=mod,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for line in done.stdout.splitlines():
        if line.startswith("GTMATH_CLASSPATH="):
            return line.removeprefix("GTMATH_CLASSPATH=")
    raise RuntimeError(f"the mod's Gradle build printed no classpath:\n{done.stdout}")


def run_oracle(
    rows: Iterable[tuple[str, str, Fields]], classpath: str, jdk: Path | None
) -> dict[str, Fields]:
    """GT's outputs for ``(kind, id, inputs)`` rows, by id."""
    javac = str(jdk / "bin" / "javac") if jdk else "javac"
    java = str(jdk / "bin" / "java") if jdk else "java"
    with tempfile.TemporaryDirectory(prefix="gtmath-oracle-") as tmp:
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
                "-cp",
                classpath,
                "-d",
                str(classes),
                str(HERE / "GtMathOracle.java"),
            ],
            check=True,
        )
        results = work / "results.tsv"
        subprocess.run(
            [
                java,
                "-cp",
                os.pathsep.join([str(classes), classpath]),
                "GtMathOracle",
                str(cases_file),
                str(results),
            ],
            check=True,
        )
        out_fields: dict[str, Fields] = {}
        for line in results.read_text(encoding="utf-8").splitlines():
            case_id, *cols = line.split("\t")
            out_fields[case_id] = {
                k: decode(k, v) for k, _, v in (c.partition("=") for c in cols)
            }
        return out_fields


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
    oc_cases, par_cases = cases(args.seed)
    classpath = args.classpath or gradle_classpath()
    rows = [("oc", i, f) for i, f in oc_cases] + [("par", i, f) for i, f in par_cases]
    results = run_oracle(rows, classpath, args.jdk)

    # GT's unit tests must hold on GT's own classes: the expected values ported from them (their
    # expressions, evaluated) must be what the oracle saw.
    for name, _calc, eut, ticks in gt_unit_cases():
        got = results[f"gt:{name}"]
        if got.get("eut") != eut or (
            ticks is not None and got.get("duration") != ticks
        ):
            print(
                f"GT's class disagrees with the ported expectation of {name}: {got}",
                file=sys.stderr,
            )
            return 1

    args.out.mkdir(parents=True, exist_ok=True)
    header = (
        f"# GT {GT_VERSION} (GT5-Unofficial commit {GT_COMMIT}): what its own OverclockCalculator and\n"
        f"# ParallelHelper computed for these inputs. Written by tests/unit/gtmath/oracle/make_vectors.py\n"
        f"# (seed {args.seed}); replayed by test_oracle_vectors.py. Doubles are in hex. Do not edit.\n"
    )
    write_vectors(
        args.out / "overclock.tsv", header, [(i, f, results[i]) for i, f in oc_cases]
    )
    write_vectors(
        args.out / "parallel.tsv", header, [(i, f, results[i]) for i, f in par_cases]
    )
    print(
        f"{len(oc_cases)} overclock and {len(par_cases)} parallel vectors in {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
