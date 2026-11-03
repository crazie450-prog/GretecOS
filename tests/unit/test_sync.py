"""sync_instances.py (WO-0.3) on small fake Prism instances.

Play uses .minecraft (as the GTNH zips do), Export too (Prism's Copy keeps it), Lab uses minecraft.
"""

from __future__ import annotations

import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from gregtec import config, sync

REPO = Path(__file__).resolve().parents[2]
OLD = 1_600_000_000_000_000_000  # a fixed mtime in ns, so copied mtimes are checkable
PLAY_FILES = {
    "mods/example-1.0.jar": b"jar one",
    "mods/1.7.10/inner-2.0.jar": b"inner jar",
    "mods/NotEnoughItems-2.8.171-GTNH.jar": b"nei 171",
    "mods/gregteclink-0.1.0.jar": b"link client",
    "config/example.cfg": b"B:enabled=true\n",
    "config/sub/deep.cfg": b"deep",
    "config/gregteclink.cfg": b"client config",
    "scripts/example.zs": b"// zs",
    "options.txt": b"pauseOnLostFocus:false\n",  # not mirrored
    "saves/world/data.txt": b"save",  # not mirrored
}


def write(game: Path, rel: str, data: bytes, mtime_ns: int = OLD) -> Path:
    path = game / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def make_instance(instances: Path, name: str, game_dir: str) -> Path:
    inst = instances / name
    (inst / game_dir).mkdir(parents=True)
    (inst / "instance.cfg").write_text(f"name={name}\n")
    return inst / game_dir


def snapshot(root: Path) -> dict[str, tuple[bytes, int] | None]:
    """Every file's bytes and mtime, and every folder (None), under ``root``."""
    out: dict[str, tuple[bytes, int] | None] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = None if p.is_dir() else (p.read_bytes(), p.stat().st_mtime_ns)
    return out


class Env:
    def __init__(self, tmp: Path, lab_exclude: list[str] | None = None) -> None:
        self.instances = tmp / "instances"
        self.play = make_instance(self.instances, "GTNH-Play", ".minecraft")
        self.export = make_instance(self.instances, "GTNH-Export", ".minecraft")
        self.lab = make_instance(self.instances, "GTNH-Lab", "minecraft")
        for rel, data in PLAY_FILES.items():
            write(self.play, rel, data)
        # Export's own NEI and GregtecLink export module (plan 5.1)
        write(self.export, "mods/NotEnoughItems-2.8.172-GTNH.jar", b"nei 172")
        write(self.export, "mods/gregteclink-0.1.0.jar", b"link export")
        write(self.export, "config/gregteclink.cfg", b"export config")
        self.config = tmp / "gos.toml"
        lab = "" if lab_exclude is None else f"[sync.lab]\nexclude = {lab_exclude!r}\n"
        self.write_config(
            f'[paths]\nprism_instances = "{self.instances.as_posix()}"\n{lab}'
        )
        self.play_before = snapshot(self.play.parent)

    def write_config(self, text: str) -> None:
        self.config.write_text(text)

    def sync(self, *extra: str) -> int:
        return sync.main(["--config", str(self.config), *extra])

    def targets(self) -> dict[str, dict[str, tuple[bytes, int] | None]]:
        return {"export": snapshot(self.export), "lab": snapshot(self.lab)}

    def assert_play_untouched(self) -> None:
        assert snapshot(self.play.parent) == self.play_before


def mirrored(game: Path) -> dict[str, tuple[bytes, int] | None]:
    return {k: v for k, v in snapshot(game).items() if k.split("/")[0] in sync.MIRRORED}


def test_first_sync_copies_and_a_second_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    assert env.sync() == 0
    out = capsys.readouterr().out
    assert "copy    mods/1.7.10/inner-2.0.jar" in out

    # Lab has no excludes: an exact mirror, mtimes included, and nothing else copied.
    assert mirrored(env.lab) == mirrored(env.play)
    assert not (env.lab / "options.txt").exists()
    assert not (env.lab / "saves").exists()
    # Export: the same, except its own NEI and GregtecLink files.
    want = {
        k: v
        for k, v in mirrored(env.play).items()
        if "NotEnoughItems" not in k and "gregteclink" not in k
    }
    want["mods/NotEnoughItems-2.8.172-GTNH.jar"] = (b"nei 172", OLD)
    want["mods/gregteclink-0.1.0.jar"] = (b"link export", OLD)
    want["config/gregteclink.cfg"] = (b"export config", OLD)
    assert mirrored(env.export) == want

    before = env.targets()
    assert env.sync() == 0
    out = capsys.readouterr().out
    assert out.count("nothing to do") == 2
    assert "nothing to do (4 excluded)" in out  # Play's and Export's NEI, GregtecLink
    assert env.targets() == before  # idempotent: same bytes and mtimes
    env.assert_play_untouched()


def test_changes_and_deletions_are_mirrored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    assert env.sync() == 0
    retimed = env.lab / "config/example.cfg"
    inode = retimed.stat().st_ino
    later = OLD + 10**9
    write(env.play, "mods/example-1.0.jar", b"jar one, bigger", later)  # new size
    write(env.play, "scripts/example.zs", b"// ZS", later)  # same size, new bytes
    write(env.play, "config/example.cfg", PLAY_FILES["config/example.cfg"], later)
    (env.play / "mods/1.7.10/inner-2.0.jar").unlink()
    (env.play / "mods/1.7.10").rmdir()
    (env.play / "config/sub/deep.cfg").unlink()
    (env.play / "config/sub").rmdir()
    write(env.play, "config/new/added.cfg", b"added")
    env.play_before = snapshot(env.play.parent)
    capsys.readouterr()

    assert env.sync("--only", "lab") == 0
    out = capsys.readouterr().out
    assert "copied 1, updated 2, retimed 1, deleted 2, folders +1 -2, excluded 0" in out
    assert "export" not in out
    assert mirrored(env.lab) == mirrored(env.play)
    assert retimed.stat().st_ino == inode  # same bytes: only the mtime was set
    assert env.sync("--only", "lab") == 0
    assert "nothing to do" in capsys.readouterr().out
    env.assert_play_untouched()


def test_excludes_are_never_copied_over_or_deleted(tmp_path: Path) -> None:
    env = Env(tmp_path, lab_exclude=["config/lab-only*", "config/labdir/keep.cfg"])
    write(env.lab, "config/lab-only.cfg", b"lab only")
    write(env.lab, "config/lab-only/inner.cfg", b"lab only folder")
    write(env.lab, "config/labdir/keep.cfg", b"kept")
    write(env.lab, "config/labdir/other.cfg", b"not excluded: goes")
    export_own = {
        rel: snapshot(env.export)[rel]
        for rel in (
            "mods/NotEnoughItems-2.8.172-GTNH.jar",
            "mods/gregteclink-0.1.0.jar",
            "config/gregteclink.cfg",
        )
    }
    # Play changes a file Export excludes: still not copied over Export's own.
    write(env.play, "config/gregteclink.cfg", b"client config v2", OLD + 10**9)
    env.play_before = snapshot(env.play.parent)

    assert env.sync() == 0
    export = snapshot(env.export)
    assert {rel: export[rel] for rel in export_own} == export_own
    assert "mods/NotEnoughItems-2.8.171-GTNH.jar" not in export
    lab = snapshot(env.lab)
    assert lab["config/lab-only.cfg"] == (b"lab only", OLD)
    assert lab["config/lab-only/inner.cfg"] == (b"lab only folder", OLD)
    assert lab["config/labdir/keep.cfg"] == (b"kept", OLD)
    assert "config/labdir/other.cfg" not in lab
    assert lab["config/gregteclink.cfg"] == (b"client config v2", OLD + 10**9)
    env.assert_play_untouched()


def test_dry_run_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    write(env.lab, "config/stale.cfg", b"Play has no such file")
    before = env.targets()
    assert env.sync("--dry-run") == 0
    out = capsys.readouterr().out
    assert "Dry run: nothing is written." in out
    assert "delete  config/stale.cfg" in out
    assert "copy    config/example.cfg" in out
    assert env.targets() == before
    env.assert_play_untouched()


def _refuses(
    env: Env, capsys: pytest.CaptureFixture[str], why: str, *args: str
) -> None:
    before = env.targets()
    assert env.sync(*args) == 1
    out = capsys.readouterr().out
    assert "Refused, nothing written:" in out
    assert why in out
    assert env.targets() == before  # neither target was written
    env.assert_play_untouched()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege")
@pytest.mark.parametrize("where", ["lab file", "lab folder", "play", "lab game"])
def test_a_symlink_refuses_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    env = Env(tmp_path)
    if where == "lab file":
        link = env.lab / "config/example.cfg"
        link.parent.mkdir(parents=True)
        link.symlink_to(env.play / "config/example.cfg")
    elif where == "lab folder":
        link = env.lab / "config"
        link.symlink_to(env.play / "config", target_is_directory=True)
    elif where == "play":
        link = env.play / "config/elsewhere.cfg"
        link.symlink_to(tmp_path / "gos.toml")
        env.play_before = snapshot(env.play.parent)
    else:
        os.rmdir(env.lab)
        link = env.lab
        link.symlink_to(env.play, target_is_directory=True)
    _refuses(env, capsys, f"{link} is a symlink or junction")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege")
def test_a_target_instance_linked_to_play_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    lab = env.lab.parent
    for p in sorted(lab.rglob("*"), reverse=True):
        p.rmdir() if p.is_dir() else p.unlink()
    lab.rmdir()
    lab.symlink_to(env.play.parent, target_is_directory=True)
    _refuses(env, capsys, f"lab: {lab} is a symlink or junction")


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions")
def test_a_junction_in_a_target_refuses_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    junction = env.lab / "config"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(env.play / "config")],
        check=True,
        capture_output=True,
    )
    assert junction.is_junction()
    _refuses(env, capsys, f"{junction} is a symlink or junction")


def test_a_hard_link_in_a_target_refuses_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = Env(tmp_path)
    (env.lab / "config").mkdir()
    os.link(env.play / "config/example.cfg", env.lab / "config/example.cfg")
    _refuses(env, capsys, f"{Path('config/example.cfg')} is a hard link")


@pytest.mark.parametrize(
    ("lab", "why"),
    [
        ("GTNH-Play", "is Play, is inside Play or holds it"),
        ("GTNH-Play/.minecraft", "is not a Prism instance"),
        ("GTNH-Missing", "doesn't exist (create it with Prism's Copy instance"),
        ("GTNH-Export", "is the export instance, is inside it or holds it"),
    ],
)
@pytest.mark.parametrize("only", [(), ("--only", "lab")])
def test_a_bad_target_refuses_everything(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    lab: str,
    why: str,
    only: tuple[str, ...],
) -> None:
    env = Env(tmp_path)
    env.write_config(
        f'[paths]\nprism_instances = "{env.instances.as_posix()}"\nlab = "{lab}"\n'
    )
    _refuses(env, capsys, why, *only)


@pytest.mark.parametrize("where", ["inside Play", "holds Play"])
def test_a_target_inside_play_or_holding_it_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """Real Prism instances, so only the overlap check can refuse them."""
    env = Env(tmp_path)
    if where == "inside Play":
        game = make_instance(env.play.parent, "nested", "minecraft")
        env.play_before = snapshot(env.play.parent)
        paths = (
            f'prism_instances = "{env.instances.as_posix()}"\nlab = "GTNH-Play/nested"'
        )
    else:  # lab is the instances folder itself, which holds GTNH-Play
        game = make_instance(tmp_path, "instances", "minecraft")
        paths = (
            f'prism_instances = "{tmp_path.as_posix()}"\nplay = "instances/GTNH-Play"\n'
            'export = "instances/GTNH-Export"\nlab = "instances"'
        )
    env.write_config(f"[paths]\n{paths}\n")
    _refuses(env, capsys, f"lab: {game.parent} is Play, is inside Play or holds it")
    assert snapshot(game) == {}


def test_exclude_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    export = config.DEFAULT_SYNC_EXCLUDE["export"]
    assert sync.excluded("mods/NotEnoughItems-2.8.172-GTNH.jar", export)
    assert sync.excluded("config/gregteclink/client.cfg", export)  # inside a folder
    assert not sync.excluded("config/example.cfg", export)
    assert not sync.excluded("mods/1.7.10/x.jar", ["mods/*.jar"])  # * stops at /
    assert sync.excluded("mods/1.7.10/x.jar", ["mods/*/x.jar"])
    monkeypatch.setattr(sync, "IGNORE_CASE", False)
    assert not sync.excluded("mods/GregtecLink-0.1.0.jar", export)
    monkeypatch.setattr(sync, "IGNORE_CASE", True)  # Windows
    assert sync.excluded("mods/GregtecLink-0.1.0.jar", export)


def test_config_defaults_and_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOS_HOME", str(tmp_path))
    assert config.default_path() == tmp_path / "config" / "gos.toml"
    cfg = config.load()  # no file: Appendix C defaults
    assert cfg.instance("play") == Path("C:/Games/PrismLauncher/instances/GTNH-Play")
    assert cfg.instance("lab") == Path("C:/Games/PrismLauncher/instances/GTNH-Lab")
    assert cfg.sync_exclude == config.DEFAULT_SYNC_EXCLUDE

    config.default_path().parent.mkdir()
    config.default_path().write_text(
        '[paths]\nprism_instances = "D:/Prism/instances"\nexport = "Exp"\n'
        '[sync.lab]\nexclude = ["config/test-only*"]\n'
        '[sync.export]\nexclude = ["config/export-only.cfg"]\n'
        "[core]\nport = 8765\n"  # other sections are read by later WOs
    )
    cfg = config.load()
    assert cfg.instance("export") == Path("D:/Prism/instances/Exp")
    assert cfg.play == "GTNH-Play"
    # A custom list adds to the defaults, so Export's NEI exclude can't be dropped by accident.
    assert cfg.sync_exclude == {
        "export": (*config.DEFAULT_SYNC_EXCLUDE["export"], "config/export-only.cfg"),
        "lab": ("config/test-only*",),
    }


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("[paths]\nplay = 3\n", "[paths] play must be a non-empty string"),
        ('[sync.export]\nexclude = "mods/x.jar"\n', "[sync.export] exclude must be"),
        ("[sync]\nlab = 1\n", "sync.lab must be a table"),
        ("[paths\n", "Expected"),  # TOML syntax
        # Exclude patterns that can never match, so sync would delete what they protect.
        *(
            (f"[sync.lab]\nexclude = ['{p}']\n", f"[sync.lab] exclude {p!r} can never")
            for p in [
                "config\\lab-only.cfg",  # Windows-style, in a TOML literal string
                "./config/lab-only.cfg",
                "/config/lab-only.cfg",
                "C:/Games/config/lab-only.cfg",
                "minecraft/config/lab-only.cfg",  # relative to the instance
                "lab-only.cfg",  # not inside mods, config or scripts
                "config/../config/lab-only.cfg",
                "config//lab-only.cfg",
            ]
        ),
    ],
)
def test_config_errors(tmp_path: Path, text: str, error: str) -> None:
    path = tmp_path / "gos.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match=re.escape(error)):
        config.load(path)
    assert sync.main(["--config", str(path)]) == 2


def test_only_and_the_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    assert sync.main(["--config", str(env.config), "--only", "export"]) == 0
    assert "lab:" not in capsys.readouterr().out
    assert snapshot(env.lab) == {}
    monkeypatch.setattr(sys, "argv", ["sync_instances.py", "--config", str(env.config)])
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(REPO / "scripts" / "sync_instances.py"), run_name="__main__")
    assert done.value.code == 0
    assert mirrored(env.lab) == mirrored(env.play)
    env.assert_play_untouched()
