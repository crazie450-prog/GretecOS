"""Write the boiler and tree-farm oracle's vectors (WO-4.2, D231, D234): GT's own classes, run.

GT 5.09.54.265 has no unit tests for its 2.9 large boilers or the Tree Growth Simulator, and D231
left checking the ports in ``core/gregtec/planner/gtmath/boiler.py`` and ``treefarm.py`` to WO-4.2.
This script runs ``GtBoilerOracle.java`` with GT's jar and writes what GT computed to
``../vectors/boiler.tsv``: per 2.9 boiler, its ``EUT_GENERATED``, whether it is superheated and
``runtimeBoost`` over a range of burn times; per boiler and programmed circuit (0-25), the throttled
``lEUt`` (``adjustEUtForConfig``), ``adjustBurnTimeForConfig`` summed over 1000 fuel loads, and
``getWaterConsumptionForSteam`` / ``getSuperheatedSteam`` summed over 4800 ticks; and the tree
farm's ``getTierMultiplier``, mode multipliers and tool constants. ``../test_boiler_vectors.py``
replays every row through the ports. Run it again after a GT pin bump
(docs/runbooks/pack_update.md):

    uv run python tests/unit/gtmath/oracle/make_boiler_vectors.py

Like make_vectors.py it needs a JDK and takes the classpath from the mod's Gradle build unless
``--classpath`` names it. Nothing of GT is copied: the file holds GT's outputs.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

from gregtec.planner.gtmath import GT_COMMIT, GT_VERSION

HERE = Path(__file__).resolve().parent
VECTORS = HERE.parent / "vectors"
ORACLE_TIMEOUT_S = 600


def _gradle_classpath() -> str:
    spec = importlib.util.spec_from_file_location(
        "make_vectors", HERE / "make_vectors.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.gradle_classpath())


def run_oracle(classpath: str, jdk: Path | None) -> str:
    """GT's outputs, one line per boiler, throttle setting and the tree farm."""
    javac = str(jdk / "bin" / "javac") if jdk else "javac"
    java = str(jdk / "bin" / "java") if jdk else "java"
    with tempfile.TemporaryDirectory(prefix="gtboiler-oracle-") as tmp:
        work = Path(tmp)
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
                str(HERE / "GtBoilerOracle.java"),
            ],
            check=True,
        )
        results = work / "results.tsv"
        subprocess.run(
            [
                java,
                "-cp",
                os.pathsep.join([str(classes), classpath]),
                "GtBoilerOracle",
                str(results),
            ],
            check=True,
            timeout=ORACLE_TIMEOUT_S,
            cwd=work,
        )
        return results.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--classpath",
        help="GT's jar and its libraries (default: ask the mod's Gradle build)",
    )
    parser.add_argument(
        "--jdk", type=Path, help="a JDK's home (default: javac and java on PATH)"
    )
    parser.add_argument("--out", type=Path, default=VECTORS)
    args = parser.parse_args(argv)
    classpath = args.classpath or _gradle_classpath()
    text = run_oracle(classpath, args.jdk)
    args.out.mkdir(parents=True, exist_ok=True)
    header = (
        f"# GT {GT_VERSION} (GT5-Unofficial commit {GT_COMMIT}): what its own 2.9 large boilers\n"
        "# and MTETreeFarm computed. Written by tests/unit/gtmath/oracle/make_boiler_vectors.py;\n"
        "# replayed by test_boiler_vectors.py. Columns: kind, class, key=value fields. Do not edit.\n"
    )
    with (args.out / "boiler.tsv").open("w", encoding="utf-8", newline="\n") as out:
        out.write(header)
        out.write(text)
    print(f"wrote {len(text.splitlines())} rows to {args.out / 'boiler.tsv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
