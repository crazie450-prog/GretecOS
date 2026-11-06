"""The PowerShell hook wrappers (WO-1.4) on Windows, run as Prism runs them.

A fake GOS_APP holds copies of scripts/prism_prelaunch.ps1 and prism_postexit.ps1 and a junction
``current`` to this repo, so ``..\\current\\.venv\\Scripts\\gos.exe`` is the repo's own ``gos``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec import config, locks, prism
from gregtec.ingest import fingerprint

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Path]:
    import _winapi

    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    for name in (prism.PRE_SCRIPT, prism.POST_SCRIPT):
        shutil.copy(REPO / "scripts" / name, app / "scripts" / name)
    _winapi.CreateJunction(str(REPO), str(app / "current"))
    try:
        yield app
    finally:
        os.rmdir(app / "current")  # the junction only, never the repo behind it


def setup(tmp: Path) -> tuple[Path, dict[str, str]]:
    play = tmp / "instances" / "GTNH-Play"
    (play / ".minecraft" / "mods").mkdir(parents=True)
    (play / "instance.cfg").write_text("[General]\nname=GTNH-Play\n")
    (play / ".minecraft" / "mods" / "a.jar").write_bytes(b"a")
    home = tmp / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "gos.toml").write_text(
        f'[paths]\nprism_instances = "{(tmp / "instances").as_posix()}"\n'
        f'gos_home = "{home.as_posix()}"\n'
    )
    env = {**os.environ, "GOS_HOME": str(home), "INST_DIR": str(play)}
    return home, env


def hook(app: Path, script: str, env: dict[str, str]) -> tuple[int, str]:
    """The command install_prism_hooks.py sets, run as Prism runs it: split by
    QProcess::splitCommand (no escaped quotes in it), in the game folder."""
    pre, post = prism.hook_commands(app)
    command = pre if script == prism.PRE_SCRIPT else post
    argv = [a.replace('"', "") for a in re.findall(r'(?:"[^"]*"|\S)+', command)]
    done = subprocess.run(
        argv,
        cwd=Path(env["INST_DIR"]) / ".minecraft",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return done.returncode, done.stdout + done.stderr


def test_pre_launch_passes_only_the_refusal_to_prism(app: Path, tmp_path: Path) -> None:
    home, env = setup(tmp_path)
    code, text = hook(app, prism.PRE_SCRIPT, env)
    assert code == 0 and "recipe data is stale" in text, text
    with locks.hold(home, "export", "gos export run"):
        code, text = hook(app, prism.PRE_SCRIPT, env)
    assert code == 3 and "not launching" in text, text
    code, text = hook(app, prism.PRE_SCRIPT, {**env, "GOS_HOME": str(tmp_path / "bad")})
    assert code == 0, text  # no gos.toml there: defaults, no such Play, still launches


def test_post_exit_unchanged_does_nothing(app: Path, tmp_path: Path) -> None:
    home, env = setup(tmp_path)
    fp = fingerprint.compute_play(config.load(home / "config" / "gos.toml"))
    fingerprint.record_export(home, "20261106T000000Z", fp, fp)
    code, text = hook(app, prism.POST_SCRIPT, env)
    assert code == 0 and "nothing to do" in text, text


def test_a_missing_release_never_blocks_play(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    for name in (prism.PRE_SCRIPT, prism.POST_SCRIPT):
        shutil.copy(REPO / "scripts" / name, app / "scripts" / name)
    _, env = setup(tmp_path)
    code, text = hook(app, prism.PRE_SCRIPT, env)
    assert code == 0 and "is missing" in text, text
    code, text = hook(app, prism.POST_SCRIPT, env)
    assert code == 1 and "is missing" in text, text
    # GOS_APP's scripts themselves missing (an older release, a crash mid-deploy): Play starts.
    for name in (prism.PRE_SCRIPT, prism.POST_SCRIPT):
        (app / "scripts" / name).unlink()
    code, text = hook(app, prism.PRE_SCRIPT, env)
    assert code == 0 and "is missing (run gos deploy)" in text, text
    assert "CLIXML" not in text, text
    code, text = hook(app, prism.POST_SCRIPT, env)
    assert code == 1 and "is missing (run gos deploy)" in text, text
