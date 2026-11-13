"""Write the CropsNH oracle's vectors (WO-6.7, D273): CropsNH's own growth arithmetic, run.

``core/gregtec/crops/rules.py`` re-implements CropsNH 2.0.140's rules from its behaviour. Its
nutrient, growth-speed and stat-variation functions are public static methods of
``TileEntityCropSticks``, and CropsNH's dev jar is on the mod's dev runtime (WO-1.10). So, as
WO-3.1's oracle does for GT (D174), this script runs ``CropsNHOracle.java`` against that jar and
writes what CropsNH computed to ``../vectors/cropsnh.tsv``; ``../test_rules_oracle.py`` replays
every row through the port. Run it again after a CropsNH pin bump (docs/runbooks/pack_update.md):

    uv run python tests/unit/crops/oracle/make_vectors.py

It needs a JDK and takes the classpath from the mod's Gradle build (the GT oracle's
``gradle_classpath``) unless ``--classpath`` names it. Nothing of CropsNH is copied: the file
holds its outputs.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VECTORS = HERE.parent / "vectors"
GT_ORACLE = HERE.parents[1] / "gtmath" / "oracle" / "make_vectors.py"
ORACLE_TIMEOUT_S = 300
CROPSNH = "CropsNH 2.0.140 (tag 2.0.140, commit db0a9936bdcc)"


def _gradle_classpath() -> str:
    spec = importlib.util.spec_from_file_location("make_vectors", GT_ORACLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.gradle_classpath())


def run_oracle(classpath: str, jdk: Path | None) -> str:
    javac = str(jdk / "bin" / "javac") if jdk else "javac"
    java = str(jdk / "bin" / "java") if jdk else "java"
    with tempfile.TemporaryDirectory(prefix="cropsnh-oracle-") as tmp:
        work = Path(tmp)
        classes = work / "classes"
        subprocess.run(
            [
                javac,
                "-nowarn",
                "-proc:none",
                "-cp",
                classpath,
                "-d",
                str(classes),
                str(HERE / "CropsNHOracle.java"),
            ],
            check=True,
        )
        results = work / "results.tsv"
        subprocess.run(
            [
                java,
                "-cp",
                os.pathsep.join([str(classes), classpath]),
                "CropsNHOracle",
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
        help="CropsNH's jar and its libraries (default: ask the mod's Gradle build)",
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
        f"# {CROPSNH}: what TileEntityCropSticks' getNutrientsPerCycle, getGrowthRate and\n"
        "# variateStat computed. Written by tests/unit/crops/oracle/make_vectors.py; replayed by\n"
        "# test_rules_oracle.py. Columns: see CropsNHOracle.java. Do not edit.\n"
    )
    path = args.out / "cropsnh.tsv"
    with path.open("w", encoding="utf-8", newline="\n") as out:
        out.write(header)
        out.write(text)
    print(f"wrote {len(text.splitlines())} rows to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
