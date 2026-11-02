"""gos doctor against fixture trees shaped like the GTNH 2.9.0 Prism zips and Prism 11.1.1."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from gregtec import cli, doctor

# mmc-pack.json component uids and versions as in GT_New_Horizons_2.9.0_Java_17-26.zip and _Java_8.zip.
JAVA17_PACK = {
    "components": [
        {"uid": "org.lwjgl3", "version": "3.4.3", "cachedName": "LWJGL 3"},
        {"uid": "net.minecraft", "version": "1.7.10", "important": True},
        {"uid": "me.eigenraven.lwjgl3ify.forgepatches", "cachedVersion": "3.0.42"},
        {"uid": "net.minecraftforge", "version": "10.13.4.1614"},
        {"uid": "me.eigenraven.lwjgl3ify.launchargs", "version": "3.0.42"},
    ],
    "formatVersion": 1,
}
JAVA8_PACK = {
    "components": [
        {
            "uid": "org.lwjgl",
            "version": "2.9.4-nightly-20150209",
            "dependencyOnly": True,
        },
        {"uid": "net.minecraft", "version": "1.7.10", "important": True},
        {"uid": "net.minecraftforge", "version": "10.13.4.1614"},
    ],
    "formatVersion": 1,
}
# The zip's instance.cfg: no [General] header, no Java keys.
ZIP_INSTANCE_CFG = "InstanceType=OneSix\nOverrideJavaLocation=false\niconKey=gtnh_icon\nname=GTNH 2.9.0\nnotes=\n"


def make_instance(
    instances: Path,
    folder: str,
    pack: dict,
    game_dir: str = ".minecraft",
    cfg: str = ZIP_INSTANCE_CFG,
    version: str = "2.9.0",
    lwjgl3ify: bool = True,
) -> Path:
    inst = instances / folder
    game = inst / game_dir
    (game / "config" / "GTNewHorizons").mkdir(parents=True)
    (inst / "instance.cfg").write_text(cfg)
    (inst / "mmc-pack.json").write_text(json.dumps(pack, indent=4))
    (game / "config" / "GTNewHorizons" / "dreamcraft.cfg").write_text(
        "modules {\n    # Version of the Modpack [default: 2.9.x]\n"
        f"    S:ModPackVersion={version}\n}}\n"
    )
    (game / "config" / "DreamCoreMod.properties").write_text(
        f"#Config file\ndownloadOnlyOnce=true\ndisplayedModpackVersion={version}\n"
    )
    menu = game / "config" / "txloader" / "load" / "mainmenu"
    menu.mkdir(parents=True)
    (menu / "version.txt").write_text(f"GTNH {version} (2026-10-31)")
    mods = game / "mods"
    for sub in ("1.7.10", "OpenSecurity/sounds/alarms", ".index"):
        (mods / sub).mkdir(parents=True)
    (mods / "gregtech-5.09.54.265.jar").write_bytes(b"gt")
    if lwjgl3ify:
        (mods / "lwjgl3ify-3.0.42.jar").write_bytes(b"l3")
    (mods / "OldMod.jar.disabled").write_bytes(b"old")
    (mods / "1.7.10" / "lib.jar").write_bytes(b"lib")
    (mods / "OpenSecurity" / "sounds" / "alarms" / "klaxon1.ogg").write_bytes(b"ogg")
    (mods / ".index" / "gregtech.pw.toml").write_text("name = 'GT'")
    return inst


def make_prism(root: Path) -> Path:
    root.mkdir()
    (root / "portable.txt").write_text("")
    (root / "prismlauncher.exe").write_bytes(b"MZ")
    (root / "prismlauncher.cfg").write_text(
        "[General]\nConfigVersion=1.3\n"
        'JavaPath="C:\\\\Games\\\\PrismLauncher\\\\java\\\\java-runtime-epsilon\\\\bin\\\\javaw.exe"\n'
        "JavaVersion=25.0.5\n"
    )
    instances = root / "instances"
    make_instance(
        instances, "GTNH-Play", JAVA17_PACK
    )  # imported from the zip, folder renamed
    make_instance(
        instances,
        "GTNH-Old",
        JAVA8_PACK,
        game_dir="minecraft",
        lwjgl3ify=False,
        cfg="[General]\nConfigVersion=1.3\nname=Old world\nOverrideJavaLocation=true\n"
        "JavaPath=C:/Java/jre8/bin/javaw.exe\nJavaVersion=1.8.0_442\n",
    )
    (instances / ".tmp").mkdir()
    (instances / ".tmp" / "instance.cfg").write_text("name=staging\n")
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
    by_folder = {i.folder_name: i for i in prism.instances}
    assert set(by_folder) == {"GTNH-Play", "GTNH-Old"}
    play, old = by_folder["GTNH-Play"], by_folder["GTNH-Old"]
    assert play.name == "GTNH 2.9.0"  # import keeps the zip's name=
    assert (play.gtnh, play.java_build, play.game_dir, play.pack_version) == (
        True,
        "java17+",
        ".minecraft",
        "2.9.0",
    )
    assert play.java_source == "global"
    assert (
        play.java_path
        == r"C:\Games\PrismLauncher\java\java-runtime-epsilon\bin\javaw.exe"
    )
    assert play.java_version == "25.0.5"
    assert (old.java_build, old.game_dir, old.java_source) == (
        "java8",
        "minecraft",
        "instance",
    )
    assert (old.java_path, old.java_version) == (
        "C:/Java/jre8/bin/javaw.exe",
        "1.8.0_442",
    )
    assert play.problems == [] and old.problems == []

    mods = {m.file: m for m in play.mods}
    assert set(mods) == {
        "1.7.10/lib.jar",
        "OldMod.jar.disabled",
        "gregtech-5.09.54.265.jar",
        "lwjgl3ify-3.0.42.jar",
    }
    assert not mods["OldMod.jar.disabled"].enabled
    assert mods["gregtech-5.09.54.265.jar"].sha256 == hashlib.sha256(b"gt").hexdigest()


def test_placeholder_version_is_unknown(tmp_path: Path) -> None:
    inst = make_instance(tmp_path / "instances", "X", JAVA17_PACK, version="2.9.x")
    found = doctor.read_instance(inst, False, {})
    assert found.pack_version is None
    assert found.pack_version_sources["dreamcraft.cfg"] == "2.9.x"


def test_disagreeing_versions_and_mixed_build_are_problems(tmp_path: Path) -> None:
    inst = make_instance(
        tmp_path / "instances", "X", JAVA8_PACK
    )  # Java 8 pack + lwjgl3ify jar
    props = inst / ".minecraft" / "config" / "DreamCoreMod.properties"
    props.write_text("displayedModpackVersion=2.9.1\n")
    problems = doctor.read_instance(inst, False, {}).problems
    assert any(p.startswith("pack version files disagree") for p in problems)
    assert "Java 8 components but an enabled lwjgl3ify jar: mixed build" in problems


def test_userdata_portable_and_installed_copies(tmp_path: Path) -> None:
    portable = tmp_path / "Portable"
    (portable / "UserData" / "instances").mkdir(parents=True)
    make_instance(portable / "UserData" / "instances", "GTNH-Play", JAVA17_PACK)
    installed = tmp_path / "Programs" / "PrismLauncher"
    installed.mkdir(parents=True)
    (installed / "prismlauncher.exe").write_bytes(
        b"MZ"
    )  # data lives in %APPDATA%, not here
    roots = doctor.survey([portable, installed, portable])
    assert [(r.path, r.portable) for r in roots] == [(str(portable / "UserData"), True)]


def test_custom_instance_dir(tmp_path: Path) -> None:
    root = tmp_path / "Prism"
    root.mkdir()
    make_instance(tmp_path / "elsewhere", "GTNH-Play", JAVA17_PACK)
    (root / "prismlauncher.cfg").write_text(f"InstanceDir={tmp_path / 'elsewhere'}\n")
    [prism] = doctor.survey([root])
    assert [i.folder_name for i in prism.instances] == ["GTNH-Play"]
    assert not prism.portable


def test_unknown_build() -> None:
    assert doctor.detect_java_build(["net.minecraft"]) == "unknown"


def test_main_writes_env_and_verify_list(tmp_path: Path) -> None:
    root = make_prism(tmp_path / "Prism")
    before = snapshot(root)
    code = cli.main(
        ["doctor", "--prism-root", str(root), "--gos-home", str(tmp_path / "home")]
    )
    assert code == 0
    assert snapshot(root) == before  # read-only

    env = json.loads((tmp_path / "home" / "data" / "env.json").read_text())
    assert env["verify"]["V01"] == "GTNH-Play: java17+ build (25.0.5)"
    assert "GTNH-Old" in env["verify"]["V02"] and "minecraft" in env["verify"]["V02"]
    assert env["verify"]["V11"] == "closed"
    assert env["verify"]["V12"] == "dropped"
    assert env["verify"]["V17"] == "resolved"
    assert env["verify"]["V03"].startswith("open:")
    assert len(env["verify"]) == len(doctor.load_verify_list())
    [prism] = env["prism_roots"]
    assert {i["folder_name"] for i in prism["instances"]} == {"GTNH-Play", "GTNH-Old"}


def test_printed_report(tmp_path: Path) -> None:
    out = io.StringIO()
    code = doctor.main([make_prism(tmp_path / "Prism")], tmp_path / "home", False, out)
    text = out.getvalue()
    assert code == 0, text
    assert (
        "GTNH-Play\tGTNH\tpack=2.9.0\tjava=java17+\tgame=.minecraft\tmods=3 enabled, 1 disabled"
        in text
    )
    assert "runs on: C:/Java/jre8/bin/javaw.exe (1.8.0_442; instance setting)" in text
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
