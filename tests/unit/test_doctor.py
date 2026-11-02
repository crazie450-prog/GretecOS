from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from gregtec import cli, doctor

JAVA17_PACK = {
    "components": [
        {"uid": "org.lwjgl3", "version": "3.3.6"},
        {"uid": "net.minecraft", "version": "1.7.10"},
        {"uid": "net.minecraftforge", "version": "10.13.4.1614"},
        {"uid": "me.eigenraven.lwjgl3ify.forgepatches", "version": "3.0.42"},
    ],
    "formatVersion": 1,
}
JAVA8_PACK = {
    "components": [
        {"uid": "org.lwjgl", "version": "2.9.4-nightly-20150209"},
        {"uid": "net.minecraft", "version": "1.7.10"},
        {"uid": "net.minecraftforge", "version": "10.13.4.1614"},
    ],
    "formatVersion": 1,
}


def make_instance(instances: Path, name: str, pack: dict, game_dir: str) -> Path:
    folder = instances / name
    game = folder / game_dir
    (game / "config" / "GTNewHorizons").mkdir(parents=True)
    (folder / "instance.cfg").write_text(
        f'[General]\nInstanceType=OneSix\nname={name}\nJavaPath="C:/Java/bin/javaw.exe"\n'
    )
    (folder / "mmc-pack.json").write_text(json.dumps(pack))
    (game / "config" / "GTNewHorizons" / "dreamcraft.cfg").write_text(
        "general {\n    S:ModPackVersion=2.9.0\n}\n"
    )
    mods = game / "mods"
    (mods / "1.7.10").mkdir(parents=True)
    (mods / "gregtech-5.09.54.265.jar").write_bytes(b"gt")
    (mods / "OldMod.jar.disabled").write_bytes(b"old")
    (mods / "1.7.10" / "lib.jar").write_bytes(b"lib")
    return folder


def make_prism(root: Path) -> Path:
    root.mkdir()
    (root / "portable.txt").write_text("")
    instances = root / "instances"
    make_instance(instances, "GTNH-Play", JAVA17_PACK, "minecraft")
    make_instance(instances, "GTNH-Old", JAVA8_PACK, ".minecraft")
    (instances / "_LAUNCHER_TEMP").mkdir()  # not an instance: no instance.cfg
    return root


def snapshot(root: Path) -> dict[str, tuple[bytes, float]]:
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_survey_reads_both_builds(tmp_path: Path) -> None:
    [prism] = doctor.survey([make_prism(tmp_path / "Prism")])
    assert prism.portable
    by_name = {i.name: i for i in prism.instances}
    assert set(by_name) == {"GTNH-Play", "GTNH-Old"}
    play, old = by_name["GTNH-Play"], by_name["GTNH-Old"]
    assert (play.java_build, play.game_dir, play.pack_version) == (
        "java17+",
        "minecraft",
        "2.9.0",
    )
    assert (old.java_build, old.game_dir) == ("java8", ".minecraft")
    assert play.java_path == "C:/Java/bin/javaw.exe"
    mods = {m.file: m for m in play.mods}
    assert set(mods) == {
        "1.7.10/lib.jar",
        "OldMod.jar.disabled",
        "gregtech-5.09.54.265.jar",
    }
    assert not mods["OldMod.jar.disabled"].enabled
    assert mods["gregtech-5.09.54.265.jar"].sha256 == hashlib.sha256(b"gt").hexdigest()


def test_custom_instance_dir(tmp_path: Path) -> None:
    root = tmp_path / "Prism"
    root.mkdir()
    make_instance(tmp_path / "elsewhere", "GTNH-Play", JAVA17_PACK, "minecraft")
    (root / "prismlauncher.cfg").write_text(f"InstanceDir={tmp_path / 'elsewhere'}\n")
    [prism] = doctor.survey([root])
    assert [i.name for i in prism.instances] == ["GTNH-Play"]
    assert not prism.portable


def test_unknown_build() -> None:
    assert doctor.detect_java_build(["net.minecraft"]) == "unknown"


def test_main_writes_env_and_verify_list(tmp_path: Path) -> None:
    root = make_prism(tmp_path / "Prism")
    before = snapshot(root)
    out = io.StringIO()
    code = cli.main(
        ["doctor", "--prism-root", str(root), "--gos-home", str(tmp_path / "home")]
    )
    assert code == 0, out.getvalue()
    assert snapshot(root) == before  # read-only

    env = json.loads((tmp_path / "home" / "data" / "env.json").read_text())
    assert env["verify"]["V01"] == "GTNH-Play: java17+"
    assert "GTNH-Old" in env["verify"]["V02"] and ".minecraft" in env["verify"]["V02"]
    assert env["verify"]["V11"] == "closed"
    assert env["verify"]["V12"] == "dropped"
    assert env["verify"]["V17"] == "resolved"
    assert env["verify"]["V03"].startswith("open:")
    assert len(env["verify"]) == len(doctor.load_verify_list())
    [prism] = env["prism_roots"]
    assert {i["name"] for i in prism["instances"]} == {"GTNH-Play", "GTNH-Old"}


def test_printed_report(tmp_path: Path) -> None:
    out = io.StringIO()
    code = doctor.main([make_prism(tmp_path / "Prism")], tmp_path / "home", False, out)
    text = out.getvalue()
    assert code == 0
    assert (
        "GTNH-Play\tpack=2.9.0\tjava=java17+\tgame=minecraft\tmods=2 enabled, 1 disabled"
        in text
    )
    assert "VERIFY (plan section 13.2)" in text
    assert all(f"  {v.id}\t" in text for v in doctor.load_verify_list())


def test_nothing_found_is_not_green(tmp_path: Path) -> None:
    out = io.StringIO()
    assert doctor.main([tmp_path / "missing"], tmp_path / "home", False, out) == 1
    assert "No Prism folder found" in out.getvalue()
    assert "open: no GTNH-Play instance found yet" in out.getvalue()


def test_instance_without_game_dir_is_a_problem(tmp_path: Path) -> None:
    root = make_prism(tmp_path / "Prism")
    broken = root / "instances" / "Broken"
    broken.mkdir()
    (broken / "instance.cfg").write_text("name=Broken\n")
    out = io.StringIO()
    assert doctor.main([root], tmp_path / "home", False, out) == 1
    assert "problem: no minecraft or .minecraft folder" in out.getvalue()
