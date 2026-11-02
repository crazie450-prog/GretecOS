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
    shutil.copytree(SHIPPED, inst / ".minecraft")  # the GTNH zips use .minecraft
    (inst / "instance.cfg").write_text("name=GTNH-Lab\n")
    (inst / "mmc-pack.json").write_text(json.dumps({"components": components}))
    if crlf:  # how OpenComputers and ServerUtilities rewrite them on a Windows launch
        for f in (inst / ".minecraft").rglob("*.cfg"):
            f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    return inst


def game(inst: Path) -> Path:
    return inst / ".minecraft"


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
        "oc_rules": "ok",
        "pause_focus": "no file",
        "su_logging": "missing",
        "borderless": "missing",
    }
    assert results["oc_loopback"].detail == 'first rule "deny ip:127.0.0.0/8"'
    assert results["oc_timeout"].detail == "requestTimeout=0"
    assert results["oc_rules"].detail == "7 rules"
    assert tweaks.main([str(inst)]) == 1
    assert snapshot(inst) == before  # checking never writes


def test_apply_changes_only_what_it_must(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inst = make_instance(tmp_path, JAVA17)
    g = game(inst)
    original = {
        rel: (g / rel).read_text() for rel in (tweaks.OC, tweaks.SU, tweaks.LWJGL3IFY)
    }

    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert "All tweaks present." in capsys.readouterr().out
    assert set(status(inst).values()) == {"ok"}

    def changes(rel: str) -> list[str]:
        diff = difflib.unified_diff(
            original[rel].splitlines(), (g / rel).read_text().splitlines(), n=0
        )
        return [
            line
            for line in diff
            if line[:1] in "+-" and not line.startswith(("+++", "---"))
        ]

    assert changes(tweaks.OC) == [
        '+      "allow ip:127.0.0.1",',
        "-    requestTimeout=0",
        "+    requestTimeout=10",
    ]
    # Only world.logging changes; the B:enabled keys of other categories stay false.
    assert changes(tweaks.SU) == [
        "-        B:block_broken=false",
        "-        B:block_placed=false",
        "+        B:block_broken=true",
        "+        B:block_placed=true",
        "-        B:enabled=false",
        "+        B:enabled=true",
    ]
    assert changes(tweaks.LWJGL3IFY) == [
        "-    B:borderless=false",
        "+    B:borderless=true",
    ]
    assert (g / "options.txt").read_text() == "pauseOnLostFocus:false\n"
    for rel, text in original.items():
        assert (g / (rel + ".gos-bak")).read_text() == text

    after = snapshot(inst)
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert snapshot(inst) == after  # nothing left to change


def test_crlf_is_kept(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA17, crlf=True)
    tweaks.main([str(inst), "--apply", "--yes"])
    for rel in (tweaks.OC, tweaks.SU, tweaks.LWJGL3IFY):
        data = (game(inst) / rel).read_bytes()
        assert data.count(b"\n") == data.count(b"\r\n"), rel


def test_one_line_rule_list(tmp_path: Path) -> None:
    """The layout in plan Appendix C, and how a hand edit might leave it."""
    inst = make_instance(tmp_path, JAVA17)
    oc = game(inst) / tweaks.OC
    text = oc.read_text()
    start, end = (
        text.index("filteringRules=["),
        text.index("]", text.index("filteringRules=[")) + 1,
    )
    oc.write_text(
        text[:start]
        + 'filteringRules=["deny private", removeme, "allow default"]'
        + text[end:]
    )
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert (
        'filteringRules=["allow ip:127.0.0.1", "deny private", removeme, "allow default"]'
        in oc.read_text()
    )


@pytest.mark.parametrize(
    ("old", "new", "detail"),
    [
        (
            '"deny private"',
            '"deny  private"',
            "invalid rules",
        ),  # OC splits on single spaces
        ('"deny bogon"', '"deny lan"', "invalid rules"),  # not a filter OC knows
        ("blacklist=[]", 'blacklist=["10.0.0.1"]', "non-empty blacklist"),
    ],
)
def test_rules_oc_would_reject_block_apply(
    tmp_path: Path, old: str, new: str, detail: str
) -> None:
    inst = make_instance(tmp_path, JAVA17)
    oc = game(inst) / tweaks.OC
    oc.write_text(oc.read_text().replace(old, new, 1))
    results = {r.tweak: r for r in tweaks.check(inst)}
    assert results["oc_rules"].status == "invalid"
    assert detail in results["oc_rules"].detail
    before = snapshot(inst)
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 1
    assert snapshot(inst) == before  # nothing is written, not even the other files


def test_last_launch_log(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA17)
    log = game(inst) / "logs" / "fml-client-latest.log"
    log.parent.mkdir()
    log.write_text(
        "[OpenComputers]: Successfully applied 7 Internet Card filtering rules.\n"
    )
    detail = {r.tweak: r for r in tweaks.check(inst)}["oc_rules"].detail
    assert (
        detail
        == "7 rules; last launch: Successfully applied 7 Internet Card filtering rules."
    )


def test_java8_build_skips_borderless(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA8)
    assert status(inst)["borderless"] == "n/a"
    before = (
        game(inst) / tweaks.LWJGL3IFY
    ).read_bytes()  # Java 8 zips ship it too, unused
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert (game(inst) / tweaks.LWJGL3IFY).read_bytes() == before


def test_existing_options_and_misplaced_rule(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA17)
    g = game(inst)
    (g / "options.txt").write_text("fov:0.5\npauseOnLostFocus:true\nlang:en_US\n")
    oc = g / tweaks.OC
    oc.write_text(
        oc.read_text().replace(
            '"deny bogon",', '"deny bogon",\n      "allow ip:127.0.0.1",'
        )
    )
    tweaks.main([str(inst), "--apply", "--yes"])
    assert (
        g / "options.txt"
    ).read_text() == "fov:0.5\npauseOnLostFocus:false\nlang:en_US\n"
    assert oc.read_text().count("allow ip:127.0.0.1") == 1
    assert status(inst)["oc_loopback"] == "ok"


def test_any_value_but_true_means_off(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA17)
    (game(inst) / "options.txt").write_text(
        "pauseOnLostFocus:False\n"
    )  # the game reads only "true"
    assert status(inst)["pause_focus"] == "ok"


def test_apply_asks_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = make_instance(tmp_path, JAVA17)
    before = snapshot(inst)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert tweaks.main([str(inst), "--apply"]) == 1
    assert snapshot(inst) == before
