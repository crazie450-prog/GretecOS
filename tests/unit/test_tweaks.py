from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import pytest

from gregtec import tweaks

SHIPPED = Path(__file__).resolve().parents[1] / "fixtures" / "gtnh-2.9.0"
JAVA17 = [
    {"uid": "org.lwjgl3"},
    {"uid": "net.minecraftforge"},
    {"uid": "me.eigenraven.lwjgl3ify.forgepatches"},
]
JAVA8 = [{"uid": "org.lwjgl"}, {"uid": "net.minecraftforge"}]


def make_instance(
    tmp: Path, components: list[dict[str, str]], *, crlf: bool = False
) -> Path:
    inst = tmp / "GTNH-Lab"
    shutil.copytree(SHIPPED, inst / "minecraft")
    (inst / "instance.cfg").write_text("name=GTNH-Lab\n")
    (inst / "mmc-pack.json").write_text(json.dumps({"components": components}))
    if crlf:
        for f in (inst / "minecraft").rglob("*.cfg"):
            f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    return inst


def status(inst: Path) -> dict[str, str]:
    return {r.tweak: r.status for r in tweaks.check(inst)}


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def test_shipped_defaults_are_missing(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA17)
    before = snapshot(inst)
    results = {r.tweak: r for r in tweaks.check(inst)}
    assert {k: r.status for k, r in results.items()} == {
        "oc_loopback": "missing",
        "oc_timeout": "missing",
        "pause_focus": "no file",
        "su_logging": "missing",
        "borderless": "missing",
    }
    assert results["oc_loopback"].detail == 'first rule "deny ip:127.0.0.0/8"'
    assert results["oc_timeout"].detail == "requestTimeout=0"
    assert tweaks.main([str(inst)]) == 1
    assert snapshot(inst) == before  # checking never writes


def test_apply_changes_only_what_it_must(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inst = make_instance(tmp_path, JAVA17)
    game = inst / "minecraft"
    original = {
        rel: (game / rel).read_text()
        for rel in (tweaks.OC, tweaks.SU, tweaks.LWJGL3IFY)
    }

    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert "All tweaks present." in capsys.readouterr().out
    assert set(status(inst).values()) == {"ok"}

    def changes(rel: str) -> list[str]:
        diff = difflib.unified_diff(
            original[rel].splitlines(), (game / rel).read_text().splitlines(), n=0
        )
        return [
            line
            for line in diff
            if line[:1] in "+-" and not line.startswith(("+++", "---"))
        ]

    assert changes(tweaks.OC) == [
        '+      "allow ip:127.0.0.1"',
        "-    requestTimeout=0",
        "+    requestTimeout=10",
    ]
    assert changes(tweaks.SU) == [
        "-        B:block_broken=false",
        "+        B:block_broken=true",
        "-        B:block_placed=false",
        "+        B:block_placed=true",
        "-        B:enabled=false",
        "+        B:enabled=true",
    ]
    assert changes(tweaks.LWJGL3IFY) == [
        "-    B:borderless=false",
        "+    B:borderless=true",
    ]
    assert (game / "options.txt").read_text() == "pauseOnLostFocus:false\n"
    for rel, text in original.items():
        assert (game / (rel + ".gos-bak")).read_text() == text

    after = snapshot(inst)
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert snapshot(inst) == after  # nothing left to change


def test_crlf_is_kept(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA17, crlf=True)
    tweaks.main([str(inst), "--apply", "--yes"])
    for rel in (tweaks.OC, tweaks.SU, tweaks.LWJGL3IFY):
        data = (inst / "minecraft" / rel).read_bytes()
        assert data.count(b"\n") == data.count(b"\r\n"), rel


def test_java8_build_skips_borderless(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA8)
    (
        inst / "minecraft" / tweaks.LWJGL3IFY
    ).unlink()  # the Java 8 build has no lwjgl3ify
    assert status(inst)["borderless"] == "n/a"
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert not (inst / "minecraft" / tweaks.LWJGL3IFY).exists()


def test_existing_options_and_misplaced_rule(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA17)
    game = inst / "minecraft"
    (game / "options.txt").write_text("fov:0.5\npauseOnLostFocus:true\nlang:en_US\n")
    oc = game / tweaks.OC
    oc.write_text(
        oc.read_text().replace(
            '"deny bogon",', '"deny bogon",\n      "allow ip:127.0.0.1",'
        )
    )
    tweaks.main([str(inst), "--apply", "--yes"])
    assert (
        game / "options.txt"
    ).read_text() == "fov:0.5\npauseOnLostFocus:false\nlang:en_US\n"
    assert oc.read_text().count("allow ip:127.0.0.1") == 1
    assert status(inst)["oc_loopback"] == "ok"


def test_apply_asks_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = make_instance(tmp_path, JAVA17)
    before = snapshot(inst)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert tweaks.main([str(inst), "--apply"]) == 1
    assert snapshot(inst) == before
