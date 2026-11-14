"""Write the Large Naquadah Reactor oracle's vectors (WO-4.8, D326): GT's own class, run on cases.

GT 5.09.54.265 has no unit tests for GoodGenerator's Large Naquadah Reactor
(``gregtech.common.tileentities.machines.multi.MTELargeNaquadahReactor``). So the port in
``core/gregtec/planner/gtmath/naquadah.py`` is checked against the real class, as the turbine
oracle does (D231): this script makes cases, runs ``GtNaquadahOracle.java`` on them with GT's jar
and writes what GT computed to ``../vectors/naquadah.tsv``: the reactor's own coolant and
excited-fluid lists and GoodGenerator's liquid-air rate (the ``lists`` row), the fuel map's
recipes the oracle added (GoodGenerator's nine and edge cases), and per case the runs the reactor
made (``checkProcessing_EM`` then ``onRunningTick`` for each tick): every fuel with no boost, each
coolant, each excited fluid and both at once; one dynamo hatch per tier and kind around the
output; tanks that run out; and a seeded random sample. ``../test_naquadah_vectors.py`` replays
every row through the port. Run it again after a GT pin bump (docs/runbooks/pack_update.md):

    uv run python tests/unit/gtmath/oracle/make_naquadah_vectors.py

Like make_vectors.py it needs a JDK and takes the classpath from the mod's Gradle build unless
``--classpath`` names it. Nothing of GT is copied: the file holds inputs and GT's outputs.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import random
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from gregtec.planner.gtmath import GT_COMMIT, GT_VERSION

HERE = Path(__file__).resolve().parent
VECTORS = HERE.parent / "vectors"
SEED = 480  # WO-4.8
RANDOM_CASES = 400
ORACLE_TIMEOUT_S = 600
BIG = 10**9  # litres: a tank that never runs out in a case
COOLANTS = 4  # the reactor's lists (the oracle's lists row says what they hold)
EXCITED = 5

Fields = dict[str, str]


def _load_make_vectors() -> ModuleType:
    path = HERE / "make_vectors.py"
    spec = importlib.util.spec_from_file_location("gtmath_make_vectors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_fields(text: str) -> Fields:
    out: Fields = {}
    for part in text.split():
        key, _, value = part.partition("=")
        out[key] = value
    return out


def format_fields(fields: Fields) -> str:
    return " ".join(f"{k}={v}" for k, v in fields.items())


def run_oracle(
    rows: Iterable[tuple[str, str, Fields]], classpath: str, jdk: Path | None
) -> dict[str, Fields]:
    """GT's outputs for ``(kind, id, inputs)`` rows, by id."""
    javac = str(jdk / "bin" / "javac") if jdk else "javac"
    java = str(jdk / "bin" / "java") if jdk else "java"
    with tempfile.TemporaryDirectory(prefix="gtnaquadah-oracle-") as tmp:
        work = Path(tmp)
        cases_file = work / "cases.tsv"
        with cases_file.open("w", encoding="utf-8", newline="\n") as out:
            for kind, case_id, fields in rows:
                cols = [kind, case_id, *(f"{k}={v}" for k, v in fields.items())]
                out.write("\t".join(cols) + "\n")
        classes = work / "classes"
        subprocess.run(
            [
                javac,
                "-nowarn",
                "-proc:none",
                "-XDignore.symbol.file",  # sun.misc.Unsafe, to set up GT outside the game
                "-cp",
                classpath,
                "-d",
                str(classes),
                str(HERE / "GtNaquadahOracle.java"),
            ],
            check=True,
        )
        results = work / "results.tsv"
        subprocess.run(
            [
                java,
                "-cp",
                os.pathsep.join([str(classes), classpath]),
                "GtNaquadahOracle",
                str(cases_file),
                str(results),
            ],
            check=True,
            timeout=ORACLE_TIMEOUT_S,
            cwd=work,
        )
        out_rows: dict[str, Fields] = {}
        for line in results.read_text(encoding="utf-8").splitlines():
            cols = line.split("\t")
            out_rows[cols[0]] = dict(c.split("=", 1) for c in cols[1:])
        return out_rows


# One dynamo per kind: GT's 1 A hatch, TecTech's multi-amp hatch, TecTech's laser source hatch.
DYNAMOS = (
    [f"gt:{t}" for t in range(13)]
    + [f"tt:{t}:{a}" for t in range(3, 11) for a in (4, 16, 64)]
    + [f"laser:{t}:{a}" for t in range(5, 11) for a in (256, 1024, 4096)]
)
HUGE = "laser:13:4096"


def cases(fuels: int, seed: int) -> list[tuple[str, Fields]]:
    """The run cases: ``fuels`` is how many fuel recipes the oracle's map holds."""
    out: list[tuple[str, Fields]] = []
    full = {"air": str(BIG)}
    for i in range(fuels):
        base = {"fuel": f"{i}:{BIG}", **full}
        out.append((f"steady:f{i}:none", {**base, "dynamo": HUGE, "runs": "3"}))
        for c in range(COOLANTS):
            out.append(
                (
                    f"steady:f{i}:c{c}",
                    {**base, "coolant": f"{c}:{BIG}", "dynamo": HUGE, "runs": "3"},
                )
            )
        for e in range(EXCITED):
            out.append(
                (
                    f"steady:f{i}:e{e}",
                    {**base, "excited": f"{e}:{BIG}", "dynamo": HUGE, "runs": "3"},
                )
            )
        out.append(
            (
                f"steady:f{i}:all",
                {
                    **base,
                    "coolant": ",".join(f"{c}:{BIG}" for c in range(COOLANTS)),
                    "excited": ",".join(f"{e}:{BIG}" for e in range(EXCITED)),
                    "dynamo": HUGE,
                    "runs": "3",
                },
            )
        )
    # Every dynamo on MkI (GoodGenerator's fuel 3) and on the cap's edges (the last fuels).
    for d in DYNAMOS:
        for i in (3, fuels - 3, fuels - 2):
            out.append(
                (
                    f"dynamo:f{i}:{d}",
                    {"fuel": f"{i}:{BIG}", **full, "dynamo": d, "runs": "2"},
                )
            )
    # Tanks that run out, and no liquid air at all.
    for i in (3, 4, 8):
        out.append((f"noair:f{i}", {"fuel": f"{i}:{BIG}", "dynamo": HUGE, "runs": "2"}))
        for air in (0, 2399, 2400, 4800, 7199, 9600):
            out.append(
                (
                    f"air{air}:f{i}",
                    {
                        "fuel": f"{i}:{BIG}",
                        "air": str(air),
                        "dynamo": HUGE,
                        "runs": "3",
                    },
                )
            )
        for fuel in (0, 1, 2, 3, 4, 17):
            out.append(
                (
                    f"fuel{fuel}:f{i}",
                    {
                        "fuel": f"{i}:{fuel}",
                        **full,
                        "excited": f"2:{BIG}",
                        "dynamo": HUGE,
                        "runs": "6",
                    },
                )
            )
    rng = random.Random(seed)
    for n in range(RANDOM_CASES):
        f: Fields = {
            "fuel": f"{rng.randrange(fuels)}:{rng.choice([0, 1, 2, 5, 20, 100, BIG])}"
        }
        if rng.random() < 0.9:
            f["air"] = str(rng.choice([0, 2400, 5000, 12000, 30000, BIG]))
        if rng.random() < 0.6:
            picks = rng.sample(range(COOLANTS), rng.randint(1, COOLANTS))
            f["coolant"] = ",".join(
                f"{c}:{rng.choice([0, 19, 20, 999, 1000, 2500, 40000, BIG])}"
                for c in picks
            )
        if rng.random() < 0.6:
            picks = rng.sample(range(EXCITED), rng.randint(1, EXCITED))
            f["excited"] = ",".join(
                f"{e}:{rng.choice([0, 19, 20, 179, 180, 500, 5000, BIG])}"
                for e in picks
            )
        f["dynamo"] = rng.choice([*DYNAMOS, HUGE])
        f["runs"] = str(rng.randint(1, 5))
        out.append((f"random:{n}", f))
    return out


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
    lists = run_oracle([("lists", "lists", {})], classpath, args.jdk)["lists"]
    fuels = sum(1 for k in lists if k.startswith("fuel_"))
    rows = cases(fuels, args.seed)
    out = run_oracle([("run", i, f) for i, f in rows], classpath, args.jdk)
    args.out.mkdir(parents=True, exist_ok=True)
    header = (
        f"# GT {GT_VERSION} (GT5-Unofficial commit {GT_COMMIT}): what its own Large Naquadah Reactor\n"
        f"# (MTELargeNaquadahReactor) computed for these inputs. Written by\n"
        f"# tests/unit/gtmath/oracle/make_naquadah_vectors.py (seed {args.seed}); replayed by\n"
        f"# test_naquadah_vectors.py. Do not edit.\n"
    )
    write_vectors(
        args.out / "naquadah.tsv",
        header,
        [("lists", {}, lists), *((i, f, out[i]) for i, f in rows)],
    )
    print(f"{len(rows) + 1} naquadah reactor vectors in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
