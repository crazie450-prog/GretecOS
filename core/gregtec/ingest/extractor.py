"""``gos structures extract``: run MrBruh's gtnh-extractor re-pinned to GTNH 2.9.0 (WO-1.8, D124).

gtnh-extractor (tools/gtnh-extractor in github.com/MrBruh/gtnh-process-line-solver, Apache-2.0)
is a Forge 1.7.10 dev mod: ``./gradlew runServer`` boots a dedicated dev server with GT5-Unofficial
and StructureLib, builds every StructureLib controller facing north, records each cell's block
and meta, hatch slots and tier tables, writes JSON and exits [G§3.1][A§4.2]. Upstream pins it to
2.9.0-beta-3. GOS runs it as an external tool and vendors none of it:

1. :func:`fetch`: a git checkout of the pinned upstream :data:`COMMIT` in ``<work>/upstream``,
   forced back to that commit and cleaned on every run.
2. :func:`prepare`: copies ``tools/gtnh-extractor`` to ``<work>/build`` without upstream's Gradle
   wrapper, puts this repo's own wrapper from ``mod/`` in its place (the same Gradle and daemon
   JDK GregtecLink builds with), sets the GTNH settings plugin to ``mod/``'s version, rewrites the
   dependency coordinates to :data:`PINS` (the 2.9.0 DreamAssemblerXXL manifest), and writes the
   dev server's ``eula.txt`` and ``server.properties``. A coordinate that is missing or appears
   twice stops the run: upstream changed, so re-check the pin.
3. :func:`run_gradle`: ``gradlew --no-daemon runServer -PdatasetOut=<out> ...``. The first run
   downloads the toolchains, Forge and the mod jars (several GB) from the GTNH Nexus.
4. :func:`check`: a ``BUILD SUCCESSFUL`` doesn't prove a dump ran [upstream README], so the
   output must hold a fresh ``_meta.json`` and pass :func:`gregtec.ingest.structures.read_dataset`
   against the GT pin.

The dev runtime is GT5-Unofficial and its hard dependencies only, so the dump covers the base
pack's controllers (GT, GT++, TecTech, bartworks, ... are all in GT5-Unofficial), not addon
multis (plan 7.10: those are captured in the Lab). Nothing here reads or writes a Prism instance.
The output is pack data: it lives in GOS_HOME, never in the repo.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from gregtec.ingest.structures import META, Dataset, FormatError, read_dataset

REPO_ROOT = Path(__file__).resolve().parents[3]
UPSTREAM = "https://github.com/MrBruh/gtnh-process-line-solver.git"
COMMIT = "03b6eb45977fa57822a6e2e660b146b3d95fd9d9"  # upstream HEAD on 2026-10-05
TOOL = Path("tools", "gtnh-extractor")
PACK_VERSION = "2.9.0"
GT = "GT5-Unofficial"
# From DreamAssemblerXXL releases/manifests/2.9.0.json (plan section 3).
PINS: dict[str, str] = {
    GT: "5.09.54.265",
    "StructureLib": "1.4.47",
    "CropsNH": "2.0.140",
    "BetterBuildersWands": "0.13.12-GTNH",
}
RECORDED = (GT, "StructureLib", "CropsNH")  # passed as -PmodVersions, into _meta.json
# This repo's Gradle wrapper (mod/), used instead of upstream's.
WRAPPER = (
    Path("gradlew"),
    Path("gradlew.bat"),
    Path("gradle", "wrapper", "gradle-wrapper.jar"),
    Path("gradle", "wrapper", "gradle-wrapper.properties"),
    Path("gradle", "gradle-daemon-jvm.properties"),
)
_NOT_COPIED = {"gradlew", "gradlew.bat", "gradle", "run", "build", ".gradle", "out"}
_SETTINGS_PLUGIN = re.compile(
    r'(id\("com\.gtnewhorizons\.gtnhsettingsconvention"\)\s*version\(")([^"]+)("\))'
)
MARKER = ".gos-extractor-work"  # in the work folder: it is ours to clean and rebuild
SERVER_FILES = {
    "eula.txt": "eula=true\n",
    "server.properties": "online-mode=false\nserver-port=25599\n",
}

Runner = Callable[..., Any]  # subprocess.run's shape


class ExtractError(RuntimeError):
    """The extractor could not be fetched, prepared, run or checked."""


def claim(work: Path) -> None:
    """Make ``work`` ours, or refuse a folder that has other things in it.

    The checkout is force-cleaned and the build folder deleted on every run, so a mistyped
    ``--work`` must not point them at someone else's files.
    """
    if work.exists() and any(work.iterdir()) and not (work / MARKER).is_file():
        raise ExtractError(f"{work} is not empty and is not an extractor work folder")
    work.mkdir(parents=True, exist_ok=True)
    (work / MARKER).write_text("gos structures extract (WO-1.8)\n", encoding="utf-8")


def fetch(
    work: Path,
    *,
    url: str = UPSTREAM,
    commit: str = COMMIT,
    git: str = "git",
    runner: Runner = subprocess.run,
) -> Path:
    """Check ``commit`` of ``url`` out in ``<work>/upstream``, forced and clean; return it."""
    up = work / "upstream"

    def call(*args: str) -> str:
        done = runner(
            [git, "-C", str(up), *args], capture_output=True, text=True, check=False
        )
        if done.returncode != 0:
            raise ExtractError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
        return str(done.stdout)

    up.mkdir(parents=True, exist_ok=True)
    if not (up / ".git").exists():
        call("init", "-q")
    head = runner(
        [git, "-C", str(up), "rev-parse", "--verify", "-q", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0 or str(head.stdout).strip() != commit:
        call("fetch", "-q", "--depth", "1", url, commit)
    call("checkout", "-q", "--detach", "--force", commit)
    call("clean", "-q", "-ffdx")
    if call("rev-parse", "HEAD").strip() != commit:
        raise ExtractError(f"{up} is not at {commit} after checkout")
    return up


def prepare(upstream: Path, build: Path, *, repo: Path = REPO_ROOT) -> None:
    """Assemble ``build`` from upstream's tool folder, this repo's wrapper and the 2.9.0 pins."""
    src = upstream / TOOL
    if not (src / "dependencies.gradle").is_file():
        raise ExtractError(f"{src} has no dependencies.gradle; not gtnh-extractor")
    mod = repo / "mod"
    if build.is_symlink():
        raise ExtractError(f"{build} is a link; refusing to replace it")
    if build.exists():
        shutil.rmtree(build)
    shutil.copytree(
        src,
        build,
        ignore=lambda d, names: [
            n for n in names if Path(d) == src and n in _NOT_COPIED
        ],
    )
    for rel in WRAPPER:
        if not (mod / rel).is_file():
            raise ExtractError(f"{mod / rel} is missing; run from a full checkout")
        (build / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mod / rel, build / rel)
    deps = build / "dependencies.gradle"
    deps.write_text(repin(deps.read_text(encoding="utf-8")), encoding="utf-8")
    ours = _one(
        _SETTINGS_PLUGIN, (mod / "settings.gradle.kts").read_text(encoding="utf-8")
    )
    settings = build / "settings.gradle.kts"
    text = settings.read_text(encoding="utf-8")
    _one(_SETTINGS_PLUGIN, text)
    settings.write_text(
        _SETTINGS_PLUGIN.sub(lambda m: m[1] + ours[2] + m[3], text), encoding="utf-8"
    )
    server = build / "run" / "server"
    server.mkdir(parents=True)
    for name, body in SERVER_FILES.items():
        (server / name).write_text(body, encoding="utf-8")


def repin(text: str) -> str:
    """``dependencies.gradle`` with each :data:`PINS` coordinate set to its 2.9.0 version."""
    for artifact, version in PINS.items():
        pattern = re.compile(
            rf'(com\.github\.GTNewHorizons:{re.escape(artifact)}:)([^:"]+)(:dev")'
        )
        _one(pattern, text)
        text = pattern.sub(lambda m, v=version: m[1] + v + m[3], text)
    return text


def _one(pattern: re.Pattern[str], text: str) -> re.Match[str]:
    found = list(pattern.finditer(text))
    if len(found) != 1:
        raise ExtractError(
            f"expected one match of {pattern.pattern!r}, found {len(found)}; upstream "
            "changed, so re-check the pin (D124)"
        )
    return found[0]


def gradle_command(build: Path, out: Path) -> list[str]:
    wrapper = build / ("gradlew.bat" if os.name == "nt" else "gradlew")
    return [
        str(wrapper),
        "--no-daemon",
        "runServer",
        f"-PdatasetOut={out}",
        f"-PpackVersion={PACK_VERSION}",
        "-PmodVersions=" + ",".join(f"{m}={PINS[m]}" for m in RECORDED),
        f"-PextractorSha={COMMIT}+gos-{PACK_VERSION}",
    ]


def run_gradle(build: Path, out: Path, *, runner: Runner = subprocess.run) -> int:
    """Run the dump; the build folder has no ``.git``, so the mod's version comes from VERSION."""
    env = dict(os.environ, VERSION=COMMIT[:12])
    done = runner(
        gradle_command(build, out),
        cwd=build,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return int(done.returncode)


def check(out: Path, started: float) -> Dataset:
    """The dump in ``out``, if this run wrote it and it reads cleanly against the pins."""
    meta = out / "multiblocks" / META
    if not meta.is_file():
        raise ExtractError(
            f"no {meta}: the server crashed or never reached the dump; see "
            "<work>/build/run/server/logs and crash-reports"
        )
    if meta.stat().st_mtime < started - 2:
        raise ExtractError(f"{meta} is older than this run")
    try:
        data = read_dataset(out, gt_version=PINS[GT])
    except FormatError as exc:
        raise ExtractError(f"the dump doesn't read: {exc}") from None
    if data.meta.pack_version != PACK_VERSION:
        raise ExtractError(
            f"{meta} says pack {data.meta.pack_version}, not {PACK_VERSION}"
        )
    return data


def default_paths(gos_home: Path, now: datetime | None = None) -> tuple[Path, Path]:
    """(work, out): ``data/cache/gtnh-extractor`` and ``data/structures/2.9.0-<UTC>``."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    data = gos_home / "data"
    return (
        data / "cache" / "gtnh-extractor",
        data / "structures" / f"{PACK_VERSION}-{stamp}",
    )


def main(
    gos_home: Path | None,
    work: Path | None,
    out: Path | None,
    stream: TextIO | None = None,
    *,
    runner: Runner = subprocess.run,
) -> int:
    stream = stream or sys.stdout
    home = gos_home or Path(os.environ.get("GOS_HOME") or Path.home() / "GregtecOS")
    default_work, default_out = default_paths(home)
    work = (work or default_work).resolve()
    out = (out or default_out).resolve()
    if out.exists() and any(out.iterdir()):
        print(f"{out} is not empty; name a new --out folder.", file=sys.stderr)
        return 2
    git = shutil.which("git")
    if git is None:
        print("git is not on PATH.", file=sys.stderr)
        return 2
    try:
        print(f"Fetching gtnh-extractor {COMMIT[:12]} into {work}", file=stream)
        claim(work)
        upstream = fetch(work, git=git, runner=runner)
        build = work / "build"
        prepare(upstream, build)
        pins = ", ".join(f"{k} {v}" for k, v in PINS.items())
        print(f"Re-pinned to GTNH {PACK_VERSION} ({pins}) in {build}", file=stream)
        print(
            "Running the dev server (the first run downloads several GB) ...",
            file=stream,
        )
        out.mkdir(parents=True, exist_ok=True)
        started = time.time()
        code = run_gradle(build, out, runner=runner)
        if code != 0:
            raise ExtractError(
                f"gradlew runServer exited {code}; see {build / 'run' / 'server'}"
            )
        data = check(out, started)
    except (ExtractError, OSError) as exc:
        print(f"Extract failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Dumped {len(data.controllers)} controllers "
        f"({sum(len(c.variants) for c in data.controllers)} variants) to {out}; "
        f"{len(data.meta.failures)} not dumped.",
        file=stream,
    )
    for failure in data.meta.failures:
        print(f"  {failure.registry_name}: {failure.reason}", file=stream)
    print(f"Next: gos structures load {out}", file=stream)
    return 0
