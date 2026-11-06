"""The export diff and pipeline (WO-1.4) on real export runs from GregtecLink's harnesses.

``./gradlew runServer -Pharness`` and ``runClient25 -Pharness`` (mod/README.md) each leave one
complete run (mode "harness", the dev runtime: GT 5.09.54.265 and its dependencies, not the pack)
in ``mod/run/{server,client}/gregteclink/exports/``; the client's has NEI's files. This runs only
with GOS_REQUIRE_HARNESS_RUN=1 and then fails without both runs; a plain ``uv run pytest`` skips
it, since an old run left in the ignored ``mod/run/`` may predate today's mod. Run the harnesses,
then ``GOS_REQUIRE_HARNESS_RUN=1 uv run pytest tests/integration``. Nothing is written outside
tmp_path.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from gregtec import config, playhooks
from gregtec.ingest import fingerprint, rundiff

REPO = Path(__file__).resolve().parents[2]


def harness_run(side: str) -> Path:
    """The newest real run (seeded pruning runs have no files) the ``side`` harness left."""
    exports = REPO / "mod" / "run" / side / rundiff.EXPORTS
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    runs = []
    for folder in sorted(exports.glob("*")) if exports.is_dir() else []:
        try:
            m = json.loads((folder / rundiff.MANIFEST).read_bytes())
        except (OSError, ValueError):
            continue
        if m.get("complete") is True and m.get("files"):
            runs.append((m["created_utc"], folder))
    if not runs:
        pytest.fail(f"no harness run in {exports} (mod/README.md)")
    return max(runs)[1]


def test_server_run_against_client_run() -> None:
    server, client = harness_run("server"), harness_run("client")
    d = rundiff.diff(rundiff.load_run(server), rundiff.load_run(client))
    assert ("nei", "skipped_server", "ok") in d.parts
    assert any(f.startswith("nei/") for f in d.files_added)
    assert not d.files_removed
    # The same GT recipes on both sides: the GT counts match.
    counts = {k for k, _, _ in d.counts}
    assert not counts & {"recipes", "recipe_maps", "machines", "recipe_errors"}
    text = rundiff.report(d)
    assert "  nei: skipped_server -> ok" in text
    assert "a harness run (dev runtime): not game data" in text


def test_pipeline_on_a_real_run(tmp_path: Path) -> None:
    """gos export run with Prism and the game faked: "Export" writes the client harness's run,
    and the previous export is the server harness's run."""
    server, client = harness_run("server"), harness_run("client")
    instances = tmp_path / "instances"
    for name in ("GTNH-Play", "GTNH-Export"):
        (instances / name / ".minecraft" / "mods").mkdir(parents=True)
        (instances / name / "instance.cfg").write_text(f"[General]\nname={name}\n")
    (instances / "GTNH-Play/.minecraft/mods/gregtech.jar").write_bytes(b"gt")
    exports = instances / "GTNH-Export/.minecraft" / rundiff.EXPORTS
    shutil.copytree(server, exports / server.name)
    home = tmp_path / "home"
    path = tmp_path / "gos.toml"
    path.write_text(
        f'[paths]\nprism_instances = "{instances.as_posix()}"\n'
        f'gos_home = "{home.as_posix()}"\ngos_app = "{(tmp_path / "app").as_posix()}"\n'
    )
    cfg = config.load(path)
    old_play = fingerprint.Fingerprint.of((("mods/gregtech.jar", "0" * 64),))
    fingerprint.record_export(home, server.name, old_play, old_play)

    running = [False]

    def launch(argv: Sequence[str]) -> None:
        def export() -> None:
            running[0] = True
            shutil.copytree(client, exports / f"{client.name}.partial")
            (exports / f"{client.name}.partial").rename(exports / client.name)
            time.sleep(0.1)
            running[0] = False

        threading.Thread(target=export).start()

    out = io.StringIO()
    code = playhooks.run_export(
        cfg,
        out,
        launch=launch,
        clients=lambda cfg: ["export"] if running[0] else [],
        allow_harness=True,
        poll_s=0.05,
    )
    text = out.getvalue()
    assert code == 0, text
    last = fingerprint.last_export(home)
    assert last is not None and last["run_id"] == client.name
    report = home / f"data/reports/export-diff-{server.name}-{client.name}.txt"
    body = report.read_text()
    assert "Play: 0 added, 0 removed, 1 changed" in body
    assert "  ~ mods/gregtech.jar" in body
    assert "  nei: skipped_server -> ok" in body
