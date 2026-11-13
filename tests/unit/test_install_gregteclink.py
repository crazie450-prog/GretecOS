"""WO-8.1 (D287): ``scripts/install_gregteclink.py``, the script Clayton runs to put GregtecLink with
the listener and client modules into Play (or Lab). Tested only on fake Prism trees in tmp_path,
Lab-shaped and Play-shaped; never on a real instance."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from gregtec import config
from gregtec.gamelink import install

REPO = Path(__file__).resolve().parents[2]
JAR = "gregteclink-0123456789ab.jar"
JAR_DATA = b"deployed link jar"
TOKEN = "tok-" + "z" * 40


def tree(tmp: Path) -> dict[str, Path]:
    """Prism instances (Play, Lab), GOS_APP with a deployed jar, GOS_HOME and gos.toml."""
    out: dict[str, Path] = {}
    for role, name, game in (
        ("play", "GTNH-Play", ".minecraft"),
        ("lab", "GTNH-Lab", "minecraft"),
    ):
        inst = tmp / "instances" / name
        (inst / game / "mods").mkdir(parents=True)
        (inst / game / "config").mkdir()
        (inst / "instance.cfg").write_text("InstanceType=OneSix\n", "utf-8")
        (inst / game / "mods" / "gregtech.jar").write_bytes(b"gt")
        out[role] = inst / game
    current = tmp / "GregtecOS-app" / "current"
    (current / "mod-dist").mkdir(parents=True)
    (current / "mod-dist" / JAR).write_bytes(JAR_DATA)
    record = {
        "commit": "0123456789ab" + "0" * 28,
        "gregteclink": {"jar": JAR, "sha256": hashlib.sha256(JAR_DATA).hexdigest()},
    }
    (current / "release.json").write_text(json.dumps(record), "utf-8")
    out["home"] = tmp / "GregtecOS"
    out["toml"] = tmp / "gos.toml"
    out["toml"].write_text(
        "[paths]\n"
        f'prism_instances = "{(tmp / "instances").as_posix()}"\n'
        f'gos_app = "{(tmp / "GregtecOS-app").as_posix()}"\n'
        f'gos_home = "{out["home"].as_posix()}"\n'
        "[core]\nport = 18765\n",
        "utf-8",
    )
    return out


def snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


class Core:
    """The core's ``POST /api/tokens/<name>``, as ``servicecli._core_call`` reaches it."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, cfg: config.Config, method: str, path: str) -> Any:  # noqa: ANN401
        self.calls.append((method, path))
        if self.fail:
            raise SystemExit("GOS isn't running: start it with gos serve")
        return {"name": path.rsplit("/", 1)[1], "token": TOKEN}


def run(
    t: dict[str, Path],
    *args: str,
    core: Core | None = None,
    running: list[str] | None = None,
) -> tuple[int, str]:
    out = io.StringIO()
    code = install.main(
        ["--config", str(t["toml"]), *args],
        out=out,
        clients=lambda cfg: list(running or []),
        core_call=core or Core(),
    )
    return code, out.getvalue()


@pytest.fixture(autouse=True)
def not_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "running_as_dev", lambda: False)


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    t = tree(tmp_path)
    before = snapshot(tmp_path)
    core = Core()
    code, out = run(t, "--instance", "lab", core=core)
    assert code == 0 and "Nothing written; run again with --apply." in out
    assert f"write {t['lab'] / 'mods' / JAR}" in out
    assert snapshot(tmp_path) == before and core.calls == []


@pytest.mark.parametrize("role", ["lab", "play"])
def test_apply_installs_the_jar_the_config_and_the_token(
    tmp_path: Path, role: str
) -> None:
    t = tree(tmp_path)
    game = t[role]
    (game / "mods" / "gregteclink-old.jar").write_bytes(b"old")
    other = "lab" if role == "play" else "play"
    other_before = snapshot(t[other])
    core = Core()
    code, out = run(t, "--instance", role, "--apply", core=core)
    assert code == 0, out
    assert core.calls == [("POST", f"/api/tokens/gregteclink-{role}")]
    token_file = t["home"] / "config" / f"gregteclink-{role}.token"
    assert token_file.read_text("utf-8") == TOKEN + "\n"
    if sys.platform != "win32":
        assert token_file.stat().st_mode & 0o077 == 0
    assert TOKEN not in out  # the token is never printed
    assert sorted(p.name for p in (game / "mods").iterdir()) == ["gregtech.jar", JAR]
    assert (game / "mods" / JAR).read_bytes() == JAR_DATA
    cfg = (game / "config" / "gregteclink.cfg").read_text("utf-8")
    assert "    B:client=true\n    B:export=false\n    B:listener=true\n" in cfg
    assert "    I:port=18765\n" in cfg
    assert f"    S:token_file={token_file.as_posix()}\n" in cfg
    assert all(f"    B:{f}=true\n" in cfg for f in install.FEEDS)
    assert not list(game.rglob("*.gos-tmp"))
    assert snapshot(t[other]) == other_before  # only the named instance


def test_a_feed_turned_off_stays_off(tmp_path: Path) -> None:
    t = tree(tmp_path)
    assert run(t, "--instance", "lab", "--apply")[0] == 0
    path = t["lab"] / "config" / "gregteclink.cfg"
    path.write_text(
        path.read_text("utf-8").replace("B:chat=true", "B:chat=FALSE"), "utf-8"
    )
    assert run(t, "--instance", "lab", "--apply")[0] == 0
    cfg = path.read_text("utf-8")
    assert "    B:chat=false\n" in cfg and "    B:target=true\n" in cfg


def test_the_config_is_what_the_client_module_reads() -> None:
    text = install.config_text(
        "play", 8765, Path("C:/Users/c/GregtecOS/config/x.token"), ["notices"]
    )
    harness = (
        REPO / "mod" / "harness" / "config-client-link" / "gregteclink.cfg"
    ).read_text("utf-8")
    # The same category names and keys as the client harness's config, which the mod parses.
    for line in ("modules {", "client {", "    B:client=true", "    I:port="):
        assert line in text and line in harness
    assert "    B:notices=false\n" in text
    assert "    S:token_file=C:/Users/c/GregtecOS/config/x.token\n" in text


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ("running", "GTNH-Play is running; close it"),
        ("dev", "never as gos-dev"),
        ("no jar", "run gos deploy first"),
        ("bad sha", "doesn't match the SHA256"),
        ("no instance", "doesn't exist"),
    ],
)
def test_refusals_write_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, refusal: str
) -> None:
    t = tree(tmp_path)
    running: list[str] = []
    if change == "running":
        running = ["play"]
    elif change == "dev":
        monkeypatch.setattr(config, "running_as_dev", lambda: True)
    elif change == "no jar":
        (tmp_path / "GregtecOS-app" / "current" / "release.json").unlink()
    elif change == "bad sha":
        (tmp_path / "GregtecOS-app" / "current" / "mod-dist" / JAR).write_bytes(
            b"tampered"
        )
    elif change == "no instance":
        (t["lab"].parent / "instance.cfg").unlink()
        refusal = "not a Prism instance"
    before = snapshot(tmp_path)
    core = Core()
    code, out = run(t, "--instance", "lab", "--apply", core=core, running=running)
    assert code == 1 and "Refused; nothing was written:" in out and refusal in out
    assert snapshot(tmp_path) == before and core.calls == []


def test_lab_at_plays_folder_or_without_the_exclude_is_refused(tmp_path: Path) -> None:
    t = tree(tmp_path)
    cfg = config.load(t["toml"])
    found, refusals = install.plan(
        dataclasses.replace(cfg, lab=cfg.play), "lab", lambda c: []
    )
    assert found is None and any("is Play" in r for r in refusals)
    no_exclude = dataclasses.replace(cfg, sync_exclude={**cfg.sync_exclude, "lab": ()})
    found, refusals = install.plan(no_exclude, "lab", lambda c: [])
    assert found is None and any("[sync.lab] exclude" in r for r in refusals)
    assert install.plan(cfg, "export", lambda c: [])[1] == [
        "--instance is play or lab, not export"
    ]


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)
def test_a_link_at_mods_is_refused(tmp_path: Path) -> None:
    t = tree(tmp_path)
    mods = t["lab"] / "mods"
    for p in mods.iterdir():
        p.unlink()
    mods.rmdir()
    os.symlink(t["play"] / "mods", mods)
    code, out = run(t, "--instance", "lab", "--apply")
    assert code == 1 and "symlink or junction" in out
    assert not (t["play"] / "mods" / JAR).exists()


def test_no_core_writes_nothing(tmp_path: Path) -> None:
    t = tree(tmp_path)
    before = snapshot(tmp_path)
    code, out = run(t, "--instance", "lab", "--apply", core=Core(fail=True))
    assert code == 1 and "GOS isn't running" in out
    assert snapshot(tmp_path) == before


def test_the_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    t = tree(tmp_path)
    monkeypatch.setattr(install.prism, "running_clients", lambda cfg: [])
    monkeypatch.setattr(
        sys,
        "argv",
        ["install_gregteclink.py", "--config", str(t["toml"]), "--instance", "lab"],
    )
    monkeypatch.setattr(install, "plan", _plan_without_processes(install.plan))
    with pytest.raises(SystemExit) as done:
        runpy.run_path(
            str(REPO / "scripts" / "install_gregteclink.py"), run_name="__main__"
        )
    assert done.value.code == 0


def _plan_without_processes(real: Any) -> Any:  # noqa: ANN401
    def plan(cfg: config.Config, role: str, clients: Any = None) -> Any:  # noqa: ANN401
        return real(cfg, role, lambda c: [])

    return plan
