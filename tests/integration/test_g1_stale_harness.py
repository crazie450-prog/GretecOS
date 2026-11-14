"""G1's stale-detection check on a real export run (G1 review; WO-1.4's acceptance, plan 5.2 step 4).

WO-1.4's own acceptance test (``tests/unit/test_playhooks.py::test_acceptance_flow``) runs on
hand-made manifests. This runs the same chain on the client harness's real run: three fake Prism
instances in tmp_path, a fake Prism "launch" that copies the harness run into Export as a new
run, and the real hooks. No export recorded -> stale; the export records Play's fingerprint ->
the next pre-launch is not stale and an unchanged quit asks nothing; a changed GT jar in Play ->
stale, naming the change, and the quit asks; a second export writes the run diff and clears it.
Affected-plan flags on a simulated GT update are WO-1.9's harness test
(``test_pack_update_harness.py``), which the G1 review re-runs beside this one.

As the other harness tests, this runs only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod job, after
the harnesses). Nothing is written outside tmp_path; the harness's run is only read.
"""

from __future__ import annotations

import io
import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from gregtec import config, playhooks
from gregtec.ingest import collect, fingerprint

REPO = Path(__file__).resolve().parents[2]
OLD_JAR, NEW_JAR = "gregtech-5.09.54.265.jar", "gregtech-5.09.54.266.jar"


def harness_run() -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    root = REPO / "mod" / "run" / "client" / collect.EXPORTS
    runs, _ = collect.find_runs(root, allow_harness=True) if root.is_dir() else ([], [])
    if not runs:
        pytest.fail(f"no client harness run in {root} (mod/README.md)")
    return runs[0].path


def make_instance(root: Path, name: str) -> Path:
    inst = root / name
    game = inst / ".minecraft"
    for d in ("mods", "scripts", "config/GTNewHorizons"):
        (game / d).mkdir(parents=True)
    (inst / "instance.cfg").write_text("InstanceType=OneSix\n", encoding="utf-8")
    (inst / "mmc-pack.json").write_text('{"components": []}', encoding="utf-8")
    (game / "config/GTNewHorizons/dreamcraft.cfg").write_text("x\n", encoding="utf-8")
    (game / "mods" / OLD_JAR).write_bytes(b"gt 265")
    return inst


class FakePrism:
    """Launching Export copies the harness's real run in as a new, finished run."""

    def __init__(self, cfg: config.Config, src: Path) -> None:
        self.cfg, self.src, self.n = cfg, src, 0

    def launch(self, argv: Sequence[str]) -> None:
        assert argv[-1] == self.cfg.export
        self.n += 1
        run_id = f"g1-{self.n}"
        exports = self.cfg.instance("export") / ".minecraft" / collect.EXPORTS
        dst = exports / run_id
        shutil.copytree(self.src, dst)
        m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
        m["run_id"] = run_id
        (dst / "manifest.json").write_text(json.dumps(m), encoding="utf-8")

    def clients(self, cfg: config.Config) -> list[str]:
        return []


class Asker:
    def __init__(self, answer: bool | None) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, title: str, question: str, timeout_s: int) -> bool | None:
        self.asked.append(question)
        return self.answer


def test_stale_detection_on_a_real_export_run(tmp_path: Path) -> None:
    src = harness_run()
    root = tmp_path / "instances"
    for name in ("GTNH-Play", "GTNH-Lab", "GTNH-Export"):
        make_instance(root, name)
    cfg = config.Config(
        prism_instances=root,
        gos_home=tmp_path / "home",
        gos_app=tmp_path / "app",
        prism_root=tmp_path / "prism",
    )
    prism = FakePrism(cfg, src)

    def pre_launch() -> str:
        out = io.StringIO()
        assert playhooks.pre_launch(cfg, "play", out, clients=prism.clients) == 0
        return out.getvalue()

    def post_exit(ask: Asker) -> str:
        out = io.StringIO()

        def no_spawn() -> playhooks.Started:
            raise AssertionError("the test answers No: nothing may start")

        assert playhooks.post_exit(cfg, "play", out, ask=ask, spawn=no_spawn) == 0
        return out.getvalue()

    def export() -> str:
        out = io.StringIO()
        code = playhooks.run_export(
            cfg,
            out,
            launch=prism.launch,
            clients=prism.clients,
            allow_harness=True,
            load=False,  # WO-1.4 only: no collect, build or diff (WO-11.1)
            sleep=lambda s: None,
            poll_s=0.0,
        )
        assert code == 0, out.getvalue()
        return out.getvalue()

    # No export yet: stale, and the quit asks (No: nothing starts, still stale).
    assert "recipe data is stale: no export is recorded yet" in pre_launch()
    ask = Asker(False)
    assert "not exporting (No)" in post_exit(ask)
    assert len(ask.asked) == 1 and "No export is recorded yet." in ask.asked[0]

    # The export records Play's fingerprint against the real run.
    export()
    last = fingerprint.last_export(cfg.gos_home)
    assert last is not None and last["run_id"] == "g1-1"
    assert last["fingerprint"] == fingerprint.compute_play(cfg).digest
    assert "recipe data matches Play (export g1-1" in pre_launch()
    ask = Asker(True)
    assert "nothing to do" in post_exit(ask) and ask.asked == []

    # A changed GT jar in Play: stale, naming the change; the quit asks.
    mods = cfg.instance("play") / ".minecraft" / "mods"
    (mods / OLD_JAR).unlink()
    (mods / NEW_JAR).write_bytes(b"gt 266")
    text = pre_launch()
    assert "recipe data is stale: Play changed since export g1-1" in text
    assert "1 added, 1 removed, 0 changed" in text
    ask = Asker(False)
    post_exit(ask)
    assert len(ask.asked) == 1 and "1 added, 1 removed" in ask.asked[0]
    assert fingerprint.load_state(cfg.gos_home)["stale"] is True

    # The next export (Play synced into Export first) clears it and writes the run diff.
    text = export()
    assert (cfg.instance("export") / ".minecraft" / "mods" / NEW_JAR).is_file()
    assert "recipe data matches Play (export g1-2" in pre_launch()
    assert fingerprint.load_state(cfg.gos_home)["stale"] is False
    report = cfg.gos_home / "data" / "reports" / "export-diff-g1-1-g1-2.txt"
    assert "Play: 1 added, 1 removed, 0 changed" in report.read_text(encoding="utf-8")
