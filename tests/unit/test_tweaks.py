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
    for f in (inst / ".minecraft").rglob("*.cfg"):  # LF, whatever git checked out
        lf = f.read_bytes().replace(b"\r\n", b"\n")
        # crlf: how OpenComputers and ServerUtilities rewrite them on a Windows launch
        f.write_bytes(lf.replace(b"\n", b"\r\n") if crlf else lf)
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
    lf = make_instance(tmp_path / "lf", JAVA17)
    crlf = make_instance(tmp_path / "crlf", JAVA17, crlf=True)
    for inst in (lf, crlf):
        assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
        assert set(status(inst).values()) == {"ok"}
    for rel in (tweaks.OC, tweaks.SU, tweaks.LWJGL3IFY):
        data = (game(crlf) / rel).read_bytes()
        assert b"\r\r\n" not in data, rel
        assert data == (game(lf) / rel).read_bytes().replace(b"\n", b"\r\n"), rel


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
        # OC wants a CIDR prefix (Integer.parseInt), not a netmask
        ('"deny ip:10.0.0.0/8"', '"deny ip:10.0.0.0/255.0.0.0"', "invalid rules"),
        ("blacklist=[]", 'blacklist=["10.0.0.1"]', "non-empty blacklist"),
        ("whitelist=[]", 'whitelist=["10.0.0.1"]', "non-empty whitelist"),
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
    assert snapshot(inst) == before  # nothing is written


def test_unfixable_later_file_writes_nothing(tmp_path: Path) -> None:
    """lwjgl3ify.cfg is planned last; OC, options.txt and SU, fixed before it, stay untouched."""
    inst = make_instance(tmp_path, JAVA17)
    (game(inst) / tweaks.LWJGL3IFY).write_text("# no window category\n")
    assert tweaks.plan_fixes(tweaks.check(inst))[-1] == tweaks.LWJGL3IFY
    before = snapshot(inst)
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 1
    assert snapshot(inst) == before


@pytest.mark.parametrize(
    ("rule", "valid"),
    [
        ("removeme", True),
        ("removeme now", True),  # OC ignores any rule starting with removeme
        ("allow default", True),
        ("deny", True),  # zero filters: deny everything
        ("deny private ", True),  # Java's split drops trailing empty parts
        ("deny ipv6 bogon", True),  # filters are ANDed
        ("deny private:x", True),  # only the part before ':' names the filter
        ("allow domain:example.com", True),
        ("deny ip:127.0.0.1", True),
        ("deny ip:10.0.0.0/8", True),
        ("deny ip:10.0.0.0/+8", True),  # Integer.parseInt takes a sign
        ("deny ip:10.0.0.0/33", True),  # and OC checks no range
        ("deny ip:fe80::/10", True),
        ("", False),
        (" deny private", False),
        ("deny  private", False),  # an empty part between the spaces
        ("deny lan", False),
        ("permit private", False),
        ("deny domain", False),
        ("deny ip", False),
        ("deny ip:example.com", False),
        ("deny ip:10.0.0.0/255.0.0.0", False),  # netmask
        ("deny ip:10.0.0.0/0.255.255.255", False),  # hostmask
        ("deny ip:10.0.0.0/", False),
        ("deny ip:10.0.0.0/99999999999", False),  # not a 32-bit int
        ("deny ip:fe80::1%1", False),  # Guava 17 takes no zone id
        ("deny ip:fe80::%eth0/64", False),
    ],
)
def test_valid_oc_rule(rule: str, valid: bool) -> None:
    assert tweaks.valid_oc_rule(rule) is valid


def test_bracket_in_comment_on_list_line(tmp_path: Path) -> None:
    """A "]" in a comment after "filteringRules=[" doesn't make it a one-line list."""
    inst = make_instance(tmp_path, JAVA17)
    oc = game(inst) / tweaks.OC
    oc.write_text(
        oc.read_text().replace("filteringRules=[", "filteringRules=[ # see [C1.3]", 1)
    )
    results = {r.tweak: r for r in tweaks.check(inst)}
    assert results["oc_rules"].detail == "7 rules"
    assert results["oc_loopback"].detail == 'first rule "deny ip:127.0.0.0/8"'
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert (
        'filteringRules=[ # see [C1.3]\n      "allow ip:127.0.0.1",\n'
        '      "deny ip:127.0.0.0/8",\n'
    ) in oc.read_text()
    assert set(status(inst).values()) == {"ok"}


def test_items_on_the_list_line(tmp_path: Path) -> None:
    """A hand edit can leave items on the "filteringRules=[" line of a multi-line list."""
    inst = make_instance(tmp_path, JAVA17)
    oc = game(inst) / tweaks.OC
    text = oc.read_text()
    start = text.index("filteringRules=[")
    end = text.index("]", start) + 1
    rules = 'filteringRules=[ "deny private",\n      "allow default"\n    ]'
    oc.write_text(
        text[:start] + rules.replace("deny private", "deny  private") + text[end:]
    )
    assert status(inst)["oc_rules"] == "invalid"  # items on that line are checked too
    oc.write_text(text[:start] + rules + text[end:])
    results = {r.tweak: r for r in tweaks.check(inst)}
    assert results["oc_loopback"].detail == 'first rule "deny private"'
    assert results["oc_rules"].detail == "2 rules"
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert (
        'filteringRules=["allow ip:127.0.0.1", "deny private",\n'
        '      "allow default"\n    ]'
    ) in oc.read_text()
    assert set(status(inst).values()) == {"ok"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10", "ok"),
        ("10  # seconds", "ok"),
        ("2147483647 // the most getInt takes", "ok"),
        ("-5", "missing"),
        ("+10", "invalid"),  # HOCON's tokenizer reads '+' only as '+='
        (
            "10s",
            "invalid",
        ),  # HOCON reads these as strings or floats; getInt wants an int
        ("10 seconds", "invalid"),
        ("1.5", "invalid"),
        ("2147483648", "invalid"),
    ],
)
def test_request_timeout_is_a_plain_int(
    tmp_path: Path, value: str, expected: str
) -> None:
    inst = make_instance(tmp_path, JAVA17)
    oc = game(inst) / tweaks.OC
    oc.write_text(oc.read_text().replace("requestTimeout=0", f"requestTimeout={value}"))
    result = {r.tweak: r for r in tweaks.check(inst)}["oc_timeout"]
    assert result.status == expected
    if expected == "invalid":
        assert result.detail.startswith(f"requestTimeout={value} ")
        assert "getInt" in result.detail
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    kept = value if expected == "ok" else "10"
    assert f"    requestTimeout={kept}\n" in oc.read_text()
    assert status(inst)["oc_timeout"] == "ok"


def test_loopback_rule_must_come_first(tmp_path: Path) -> None:
    """OC applies the first rule that matches, so the rule further down does not count."""
    inst = make_instance(tmp_path, JAVA17)
    oc = game(inst) / tweaks.OC
    oc.write_text(
        oc.read_text()
        .replace('"deny bogon",', '"deny bogon",\n      "allow ip:127.0.0.1",')
        .replace("requestTimeout=0", "requestTimeout=10")
    )
    results = {r.tweak: r for r in tweaks.check(inst)}
    assert results["oc_timeout"].status == "ok"
    assert results["oc_loopback"].status == "missing"
    assert results["oc_loopback"].detail == 'first rule "deny ip:127.0.0.0/8"'


def test_su_logging_needs_all_three_keys(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, JAVA17)
    su = game(inst) / tweaks.SU
    # world.logging's line only: auto_shutdown and ranks have a B:enabled=false too
    su.write_text(
        su.read_text().replace("        B:enabled=false", "        B:enabled=true")
    )
    assert status(inst)["su_logging"] == "missing"


@pytest.mark.parametrize(
    ("rel", "key", "tweak"),
    [
        (tweaks.LWJGL3IFY, "borderless", "borderless"),
        (tweaks.SU, "block_placed", "su_logging"),
    ],
)
def test_forge_bool_is_read_raw(tmp_path: Path, rel: str, key: str, tweak: str) -> None:
    """Forge keeps the rest of the line after '=' as it is, so "true " is no boolean."""
    inst = make_instance(tmp_path, JAVA17)
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    f = game(inst) / rel
    f.write_text(f.read_text().replace(f"B:{key}=true\n", f"B:{key}=TRUE\n"))
    assert status(inst)[tweak] == "ok"  # Forge lowercases it first
    f.write_text(f.read_text().replace(f"B:{key}=TRUE\n", f"B:{key}=true \n"))
    assert status(inst)[tweak] == "missing"
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert f"B:{key}=true\n" in f.read_text()


def test_apply_keeps_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    """The Java 8 build writes options.txt in cp1252 on Windows; untouched lines stay as they are."""
    inst = make_instance(tmp_path, JAVA8)
    options = game(inst) / "options.txt"
    data = (
        b'resourcePacks:["Caf\xe9 \x85.zip"]\r\npauseOnLostFocus:true\r\nlang:en_US\r\n'
    )
    options.write_bytes(data)
    assert tweaks.main([str(inst), "--apply", "--yes"]) == 0
    assert options.read_bytes() == data.replace(
        b"pauseOnLostFocus:true", b"pauseOnLostFocus:false"
    )


@pytest.mark.parametrize("name", [".minecraft", "minecraft"])
def test_game_folder_as_argument(tmp_path: Path, name: str) -> None:
    """mmc-pack.json is read from the instance folder, so the Java 17+ build is still seen."""
    inst = make_instance(tmp_path, JAVA17)
    g = inst / name
    if name != ".minecraft":
        (inst / ".minecraft").rename(g)
    assert {r.tweak: r.status for r in tweaks.check(g)}["borderless"] == "missing"
    assert tweaks.main([str(g), "--apply", "--yes"]) == 0
    assert "    B:borderless=true\n" in (g / tweaks.LWJGL3IFY).read_text()
    assert set(status(inst).values()) == {"ok"}


def test_not_an_instance_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inst = make_instance(tmp_path, JAVA17)
    bare = tmp_path / "bare"  # instance.cfg but no minecraft folder
    bare.mkdir()
    (bare / "instance.cfg").write_text("name=bare\n")
    before = snapshot(tmp_path)
    for path in (
        tmp_path / "GTNH-Lba",  # a typo
        tmp_path / "typo" / "GTNH-Lab",
        tmp_path,  # the instances folder
        game(inst) / "saves",
        bare,
    ):
        for args in ([], ["--apply", "--yes"]):
            assert tweaks.main([str(path), *args]) == 2
            assert "not a Prism instance" in capsys.readouterr().out
    assert snapshot(tmp_path) == before
    assert not (tmp_path / "GTNH-Lba").exists()
    assert not (tmp_path / "typo").exists()
    assert not (bare / "minecraft").exists()


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
