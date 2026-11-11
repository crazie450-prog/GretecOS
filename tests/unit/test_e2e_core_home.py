"""WO-2.6: the dashboard e2e core (dashboard/e2e/serve_core.py) wipes only a home it made itself,
so a wrong ``--home`` or ``GOS_E2E_HOME`` can never delete a real folder."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]


def serve_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "serve_core", REPO / "dashboard" / "e2e" / "serve_core.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_new_home_is_made_and_marked(tmp_path: Path) -> None:
    sc = serve_core()
    home = tmp_path / "e2e-home"
    sc.fresh_home(home)
    assert (home / sc.MARKER).is_file() and (home / "e2e").is_dir()


def test_its_own_home_is_wiped_on_the_next_run(tmp_path: Path) -> None:
    sc = serve_core()
    home = tmp_path / "e2e-home"
    sc.fresh_home(home)
    (home / "data").mkdir()
    (home / "data" / "gos.sqlite").write_bytes(b"old run")
    sc.fresh_home(home)
    assert not (home / "data").exists() and (home / sc.MARKER).is_file()


def test_an_empty_folder_is_used(tmp_path: Path) -> None:
    sc = serve_core()
    sc.fresh_home(tmp_path)
    assert (tmp_path / sc.MARKER).is_file()


def test_any_other_folder_or_file_is_refused_and_kept(tmp_path: Path) -> None:
    sc = serve_core()
    real = tmp_path / "GregtecOS"
    real.mkdir()
    (real / "gos.toml").write_text("[core]\n", "utf-8")
    with pytest.raises(SystemExit, match="refusing to delete"):
        sc.fresh_home(real)
    assert (real / "gos.toml").read_text("utf-8") == "[core]\n"
    a_file = tmp_path / "notes.txt"
    a_file.write_text("keep", "utf-8")
    with pytest.raises(SystemExit):
        sc.fresh_home(a_file)
    assert a_file.read_text("utf-8") == "keep"


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)
def test_a_link_to_a_marked_home_is_refused(tmp_path: Path) -> None:
    sc = serve_core()
    target = tmp_path / "target"
    sc.fresh_home(target)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SystemExit):
        sc.fresh_home(link)
    assert (target / sc.MARKER).is_file()
