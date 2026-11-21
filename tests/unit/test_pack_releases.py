"""WO-1.9's release watcher and stock check, with a fake network and clock (no real HTTP)."""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from gregtec.packupdate import releases

H = 3600.0


def atom(*tags: str, title: str = "Release") -> bytes:
    entries = "".join(
        f"<entry><title>{title} {t}</title><updated>2026-11-0{i + 1}T00:00:00Z</updated>"
        f'<link rel="alternate" href="https://github.com/o/r/releases/tag/{t}"/></entry>'
        for i, t in enumerate(tags)
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


def manifest(version: str, gt: str, **mods: str) -> dict[str, object]:
    github = {
        "GT5-Unofficial": {"version": gt, "side": "BOTH"},
        "NotEnoughItems": {"version": "2.8.171-GTNH", "side": "BOTH"},
        **{k: {"version": v, "side": "BOTH"} for k, v in mods.items()},
    }
    return {"version": version, "github_mods": github, "external_mods": {}}


class Net:
    """Answers from a table; counts calls; can fail."""

    def __init__(self, table: Mapping[str, bytes]) -> None:
        self.table = dict(table)
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail: str | None = None

    def __call__(self, url: str, headers: Mapping[str, str]) -> releases.Response:
        self.calls.append((url, dict(headers)))
        if self.fail:
            raise OSError(self.fail)
        if url not in self.table:
            return releases.Response(404)
        if headers.get("If-None-Match") == "e1":
            return releases.Response(304)
        return releases.Response(200, self.table[url], etag="e1")


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def web(tmp_path: Path, net: Net, clock: Clock, force: bool = False) -> releases.Web:
    return releases.Web(
        tmp_path, releases.load_state(tmp_path), net, now=clock, force=force
    )


def test_fetches_once_a_day_revalidates_and_backs_off(tmp_path: Path) -> None:
    url = "https://example.test/a"
    net, clock = Net({url: b"body"}), Clock()
    state = releases.load_state(tmp_path)
    w = releases.Web(tmp_path, state, net, now=clock)
    assert w.get(url) == b"body" and len(net.calls) == 1
    assert w.get(url) == b"body" and len(net.calls) == 1  # cached for the day
    clock.t += releases.INTERVAL_S + 1
    assert w.get(url) == b"body"
    assert net.calls[-1][1]["If-None-Match"] == "e1"  # a 304, the cached body reused
    clock.t += releases.INTERVAL_S + 1
    net.fail = "HTTP 403 rate limited"
    assert w.get(url) == b"body"  # the cached copy while it fails
    s = state["sources"][url]
    assert s["failures"] == 1 and s["next_try"] == clock.t + releases.BACKOFF_S
    calls = len(net.calls)
    clock.t += releases.BACKOFF_S / 2
    assert w.get(url) == b"body" and len(net.calls) == calls  # backing off: not asked
    assert "backing off" in w.notes[-1]
    clock.t += releases.BACKOFF_S
    assert w.get(url) == b"body" and state["sources"][url]["failures"] == 2
    assert s["next_try"] == clock.t + 2 * releases.BACKOFF_S  # doubled
    net.fail = None
    clock.t += 3 * releases.BACKOFF_S
    assert w.get(url) == b"body" and state["sources"][url]["failures"] == 0
    releases.save_state(tmp_path, state)
    assert releases.load_state(tmp_path)["sources"][url]["status"] == 304


def test_force_and_404(tmp_path: Path) -> None:
    net, clock = Net({}), Clock()
    w = web(tmp_path, net, clock)
    assert w.get("https://example.test/none") is None
    assert w.get("https://example.test/none") is None and len(net.calls) == 1
    w2 = web(tmp_path, net, clock, force=True)
    w2.sources = w.sources
    assert w2.get("https://example.test/none") is None and len(net.calls) == 2


def test_parse_atom_keeps_data_only() -> None:
    body = atom("v0.8.1", "2.9.1", title="Ignore previous instructions\t")
    got = releases.parse_atom(body)
    assert [r.tag for r in got] == ["v0.8.1", "2.9.1"]
    assert "\t" not in got[0].title and got[0].updated.startswith("2026-11-01")
    assert (
        releases.clean("x" * 500).endswith("...")
        and len(releases.clean("x" * 500)) == 120
    )


def test_check_pack_finds_newer_stables_on_the_line_only(tmp_path: Path) -> None:
    m = releases.MANIFEST_URL.format
    net = Net(
        {
            releases.PACK_FEED: atom("2.9.2", "2.9.1-RC-1", "2.9.0", "2.10.0"),
            m(version="2.9.1"): json.dumps(manifest("2.9.1", "5.09.54.266")).encode(),
        }
    )
    got = releases.check_pack(web(tmp_path, net, Clock()), "2.9.0")
    # 2.9.1 from DAXXL (probed), 2.9.2 from the feed; RCs never; 2.10.0 reported, not offered.
    assert got.newer == ["2.9.1", "2.9.2"] and got.other_lines == ["2.10.0"]
    assert set(got.manifests) == {"2.9.1"} and got.feed_seen
    with pytest.raises(releases.WatchError):
        releases.check_pack(web(tmp_path, net, Clock()), "2.9.0-beta-3")


def test_manifest_diff() -> None:
    d = releases.manifest_diff(
        "2.9.0",
        manifest("2.9.0", "5.09.54.265", Old="1", Same="2", Bump="1.0"),
        "2.9.1",
        manifest("2.9.1", "5.09.54.266", New="3", Same="2", Bump="1.1"),
    )
    assert d.gt == ("5.09.54.265", "5.09.54.266")
    assert d.added == ["New 3"] and d.removed == ["Old 1"]
    assert d.changed == ["Bump 1.0 -> 1.1", "GT5-Unofficial 5.09.54.265 -> 5.09.54.266"]
    assert "GT 5.09.54.265 -> 5.09.54.266" in d.lines()[0]


def test_stock_check_by_file_name() -> None:
    m = manifest("2.9.0", "5.09.54.265", Server="1")
    m["github_mods"]["Server"]["side"] = "SERVER"  # type: ignore[index]
    assets = {
        "mods": [
            {
                "name": "GT5-Unofficial",
                "versions": [
                    {
                        "version_tag": "5.09.54.264",
                        "filename": "gregtech-5.09.54.264.jar",
                    },
                    {
                        "version_tag": "5.09.54.265",
                        "filename": "gregtech-5.09.54.265.jar",
                    },
                ],
            },
            {
                "name": "NotEnoughItems",
                "versions": [
                    {
                        "version_tag": "2.8.170-GTNH",
                        "filename": "NotEnoughItems-2.8.170-GTNH.jar",
                    },
                    {
                        "version_tag": "2.8.171-GTNH",
                        "filename": "NotEnoughItems-2.8.171-GTNH.jar",
                    },
                ],
            },
        ]
    }
    files = [
        "mods/Gregtech-5.09.54.265.JAR",  # Windows ignores case
        "mods/NotEnoughItems-2.8.170-GTNH.jar",
        "mods/1.7.10/TwistSpaceTechnology-0.8.0.jar",
    ]
    s = releases.stock_check("2.9.0", m, assets, files)
    assert s.present == 1 and s.missing == []
    assert s.other_version == [
        "NotEnoughItems: NotEnoughItems-2.8.170-GTNH.jar instead of NotEnoughItems-2.8.171-GTNH.jar"
    ]
    assert s.not_stock == ["TwistSpaceTechnology-0.8.0.jar"]
    files.remove("mods/Gregtech-5.09.54.265.JAR")
    s = releases.stock_check("2.9.0", m, assets, files)
    assert s.missing == ["GT5-Unofficial 5.09.54.265 (gregtech-5.09.54.265.jar)"]


def test_the_repos_addon_list_reads() -> None:
    addons = releases.read_addons()
    names = {a.name for a in addons}
    assert {"Twist Space Technology", "GT-Not-Leisure", "123Technology"} <= names
    assert {a.watch for a in addons} <= {"installed", "always"}


def test_addons_relevant_to_play(tmp_path: Path) -> None:
    path = tmp_path / "addons.tsv"
    path.write_text(
        "name\trepo\tjar\twatch\tnote\n"
        "TST\to/tst\t*twist*.jar\tinstalled\t\n"
        "GTNL\to/gtnl\t*gtnl*.jar\talways\tgate\n"
        "Other\to/other\t*other*.jar\tinstalled\t\n",
        encoding="utf-8",
    )
    net = Net(
        {
            releases.FEED_URL.format(repo="o/tst"): atom("0.8.1", "0.8.0"),
            releases.FEED_URL.format(repo="o/gtnl"): atom("0.2.8"),
        }
    )
    files = ["mods/TwistSpaceTechnology-0.8.0.jar"]
    got = releases.check_addons(
        web(tmp_path, net, Clock()), releases.read_addons(path), files
    )
    assert [a.addon.name for a in got] == ["TST", "GTNL"]  # Other isn't in Play
    assert "a newer release?" in got[0].verdict
    assert got[1].verdict.startswith("not in Play; newest release 0.2.8")


def test_the_pinned_gtnl_jars_count_as_installed(tmp_path: Path) -> None:
    """D365: once Play has the pinned jars, the real list matches each to its own row, and a
    feed whose newest release is the pinned one says so."""
    addons = [
        a for a in releases.read_addons() if a.name in ("GT-Not-Leisure", "GTNLLib")
    ]
    feed = releases.FEED_URL.format
    net = Net(
        {
            feed(repo="GTNotLeisure/GT-Not-Leisure"): atom("0.2.7", "0.2.7-rc2.1"),
            feed(repo="GTNotLeisure/GTNLLib"): atom("1.1.8", "1.1.6"),
        }
    )
    files = ["mods/sciencenotleisure-0.2.7.jar", "mods/gtnllib-1.1.6.jar"]
    got = {
        a.addon.name: a
        for a in releases.check_addons(web(tmp_path, net, Clock()), addons, files)
    }
    assert got["GT-Not-Leisure"].jars == ("sciencenotleisure-0.2.7.jar",)
    assert got["GT-Not-Leisure"].verdict.endswith("matches the installed jar")
    assert got["GTNLLib"].jars == ("gtnllib-1.1.6.jar",)
    # 1.1.8 is out, but GTNL 0.2.7 builds against 1.1.6
    assert "a newer release?" in got["GTNLLib"].verdict
    got = releases.check_addons(web(tmp_path, net, Clock()), addons, [])
    # GTNL is watched always; GTNLLib only once installed
    assert [a.addon.name for a in got] == ["GT-Not-Leisure"]
    assert got[0].verdict.startswith("not in Play; newest release 0.2.7")


def test_watch_records_what_it_saw(tmp_path: Path) -> None:
    play = tmp_path / "GTNH-Play"
    (play / ".minecraft" / "mods").mkdir(parents=True)
    (play / ".minecraft" / "mods" / "TwistSpaceTechnology-0.8.0.jar").write_bytes(b"x")
    m = releases.MANIFEST_URL.format
    net = Net(
        {
            m(version="2.9.0"): json.dumps(manifest("2.9.0", "5.09.54.265")).encode(),
            m(version="2.9.1"): json.dumps(manifest("2.9.1", "5.09.54.266")).encode(),
        }
    )
    w = releases.watch(tmp_path / "home", "2.9.0", play, net, now=Clock())
    text = "\n".join(w.lines())
    assert "NEW STABLE RELEASE: GTNH 2.9.1" in text
    assert "gos pack update --start 2.9.1" in text
    assert "GT 5.09.54.265 -> 5.09.54.266" in text
    assert "Twist Space Technology" in text  # its feed 404s: noted, not fatal
    state = releases.load_state(tmp_path / "home")
    assert state["last"]["newer"] == ["2.9.1"] and len(state["history"]) == 1


# The addon jars scripts/fetch_play_files.ps1 pins for Play, and the addons.tsv row each is.
PINNED_JARS = {
    "TwistSpaceTechnology-0.8.0.jar": "Twist Space Technology",
    "OTHTechnology-2.2.1.1.jar": "123Technology",
    "programmablehatches-0.2.1p0.jar": "Programmable Hatches",
    "plannh-0.2.0.jar": "PlanNH",
    "worldedit-v0.0.10.jar": "worldedit-gtnh",
}
# The jars it pins for the Lab test first: not in Play until that test passes. GTNL and GTNLLib
# are new (D365); the other two replace a jar Play has, which stays pinned as the rollback (D373).
LAB_FIRST_JARS = {
    "sciencenotleisure-0.2.7.jar": "GT-Not-Leisure",
    "gtnllib-1.1.6.jar": "GTNLLib",
    "TwistSpaceTechnology-0.8.2.jar": "Twist Space Technology",
    "plannh-0.2.4.jar": "PlanNH",
}
# What upstream's newest release was on 2026-11-20, per addon row (D373).
NEWEST_2026_11_20 = {
    "Twist Space Technology": ("0.8.2", "0.8.1", "0.8.0"),
    "123Technology": ("2.2.1.3", "2.2.1.2", "2.2.1.1"),
    "Programmable Hatches": ("v0.2.1p9-beta", "v0.2.1p0-beta"),
    "PlanNH": ("0.2.4", "0.2.0"),
    "worldedit-gtnh": ("1.0.1", "1.0.0", "v0.0.10"),
}
FETCH_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_play_files.ps1"


def test_every_pinned_play_jar_matches_exactly_its_addon_row() -> None:
    """Each jar the fetch script pins matches its own addons.tsv row and no other, so
    `gos pack watch` checks it (123Technology's release jar is OTHTechnology-<v>.jar)."""
    pinned = set(
        re.findall(r"name = '([^']+\.jar)'", FETCH_SCRIPT.read_text(encoding="utf-8"))
    )
    # a new pin needs its row here and in addons.tsv
    assert pinned == set(PINNED_JARS) | set(LAB_FIRST_JARS)
    addons = releases.read_addons()
    for jar, row in (PINNED_JARS | LAB_FIRST_JARS).items():
        rows = [
            a.name
            for a in addons
            if any(fnmatch.fnmatchcase(jar.lower(), p) for p in a.patterns)
        ]
        assert rows == [row], jar


def test_watch_checks_every_pinned_addon_in_play(tmp_path: Path) -> None:
    mods = tmp_path / "GTNH-Play" / ".minecraft" / "mods"
    mods.mkdir(parents=True)
    for jar in PINNED_JARS:
        (mods / jar).write_bytes(b"x")
    addons = releases.read_addons()
    newest = {
        "Twist Space Technology": "0.8.0",
        "123Technology": "2.2.1.1",
        "Programmable Hatches": "v0.2.1p0-beta",
        "PlanNH": "0.2.0",
        "worldedit-gtnh": "v0.0.10",
        "GT-Not-Leisure": "0.2.8",
    }
    net = Net(
        {
            releases.FEED_URL.format(repo=a.repo): atom(newest[a.name])
            for a in addons
            if a.name in newest
        }
    )
    w = releases.watch(
        tmp_path / "home", "2.9.0", tmp_path / "GTNH-Play", net, now=Clock()
    )
    got = {a.addon.name: a for a in w.addons}
    # every installed addon, and GTNL (watched always); GTNLLib only once its jar is there
    assert set(got) == set(PINNED_JARS.values()) | {
        a.name for a in addons if a.watch == "always"
    }
    for jar, row in PINNED_JARS.items():
        assert got[row].jars == (jar,)
        assert got[row].latest is not None, row
    assert "matches the installed jar" in got["123Technology"].verdict
    assert got["GT-Not-Leisure"].verdict.startswith("not in Play; newest release 0.2.8")


def test_the_watch_reports_each_newer_addon_release_until_play_has_it(
    tmp_path: Path,
) -> None:
    """D373: with Play on its pinned jars, `gos pack watch` reports every addon whose newest
    release isn't the installed one (the ready ones and the ones still waiting alike); once
    Play has a Lab-first update, that addon reads as up to date and its jar is still known."""
    addons = [a for a in releases.read_addons() if a.name in NEWEST_2026_11_20]
    assert len(addons) == len(NEWEST_2026_11_20)
    net = Net(
        {
            releases.FEED_URL.format(repo=a.repo): atom(*NEWEST_2026_11_20[a.name])
            for a in addons
        }
    )

    def verdicts(files: list[str]) -> dict[str, str]:
        got = releases.check_addons(web(tmp_path, net, Clock()), addons, files)
        assert all(len(a.jars) == 1 for a in got), [a.jars for a in got]
        return {a.addon.name: a.verdict for a in got}

    before = verdicts([f"mods/{jar}" for jar in PINNED_JARS])
    assert set(before) == set(NEWEST_2026_11_20)
    assert all("a newer release?" in v for v in before.values()), before

    swapped = {
        "TwistSpaceTechnology-0.8.0.jar": "TwistSpaceTechnology-0.8.2.jar",
        "plannh-0.2.0.jar": "plannh-0.2.4.jar",
    }
    assert set(swapped.values()) < set(LAB_FIRST_JARS)
    after = verdicts([f"mods/{swapped.get(jar, jar)}" for jar in PINNED_JARS])
    for name, verdict in after.items():
        if name in ("Twist Space Technology", "PlanNH"):
            assert verdict.endswith("matches the installed jar"), name
        else:  # 123Technology, Programmable Hatches and worldedit-gtnh still wait (D373)
            assert "a newer release?" in verdict, name


@pytest.mark.parametrize("play", ["missing", None])
def test_addons_watched_always_are_checked_without_plays_mods(
    tmp_path: Path, play: str | None
) -> None:
    """pack_update.md: the watch reports GT-Not-Leisure (watch always) whether or not Play
    has it, so a Play whose mods can't be listed (or none configured) still gets it; its
    verdict says Play wasn't looked in, never 'not in Play', which isn't known then."""
    gtnl = next(a for a in releases.read_addons() if a.watch == "always")
    net = Net({releases.FEED_URL.format(repo=gtnl.repo): atom("0.2.8")})
    folder = None if play is None else tmp_path / "no-play"
    w = releases.watch(tmp_path / "home", "2.9.0", folder, net, now=Clock())
    assert [a.addon.name for a in w.addons] == [gtnl.name]
    assert w.addons[0].verdict.startswith(
        "couldn't look in Play's mods; newest release 0.2.8"
    )
    assert "not in Play" not in "\n".join(w.lines())
    text = "\n".join(w.lines())
    if play is not None:
        assert "only the addons watched always were checked" in text
    state = releases.load_state(tmp_path / "home")
    assert state["last"]["addons"] == {gtnl.name: "0.2.8"}


@pytest.mark.parametrize(
    ("tag", "jar", "same"),
    [
        ("1.2", "x-1.2.jar", True),
        ("v1.2", "x-1.2-GTNH.jar", True),
        ("1.2", "x-1.2.3.jar", False),
        ("1.2", "x-11.2.jar", False),
        ("1.2.3", "x-1.2.jar", False),
        ("2.2.1.1", "123Technology-2.2.1.1.jar", True),
        ("", "x-1.2.jar", False),
    ],
)
def test_addon_tags_match_on_version_boundaries(tag: str, jar: str, same: bool) -> None:
    assert releases.version_in(tag, jar) is same


def test_stock_reports_a_missing_play_as_a_note(tmp_path: Path) -> None:
    net = Net(
        {
            releases.MANIFEST_URL.format(version="2.9.0"): json.dumps(
                manifest("2.9.0", "5.09.54.265")
            ).encode(),
            releases.ASSETS_URL: json.dumps({"mods": []}).encode(),
        }
    )
    result, notes = releases.stock(tmp_path, "2.9.0", tmp_path / "no-play", net)
    assert result is None and "Play's mods couldn't be listed" in notes[0]


def test_a_watch_that_read_no_feed_is_not_an_all_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """QA 10-13: offline or rate-limited, the watch says it couldn't check and exits 1."""
    from gregtec import cli

    down = Net({})
    down.fail = "HTTP 403 Forbidden"
    w = releases.watch(tmp_path / "h0", "2.9.0", None, down, force=True, now=Clock())
    text = "\n".join(w.lines())
    assert not w.pack.feed_seen and not w.pack.newer
    assert "Couldn't check for a stable release newer than 2.9.0" in text
    assert "No stable release newer" not in text and "HTTP 403 Forbidden" in text

    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n'
        f'prism_instances = "{(tmp_path / "instances").as_posix()}"\n',
        encoding="utf-8",
    )
    real, clock, net = releases.watch, Clock(), [down]

    def fake(
        home: Path, adopted: str, play: Path | None, *, force: bool
    ) -> releases.Watch:
        return real(home, adopted, play, net[0], force=force, now=clock)

    monkeypatch.setattr(releases, "watch", fake)
    args = ["pack", "watch", "--config", str(toml)]
    assert cli.main([*args, "--force"]) == 1
    assert "Couldn't check" in capsys.readouterr().out
    net[0] = Net({releases.PACK_FEED: atom("2.9.0")})
    assert cli.main([*args, "--force"]) == 0  # --force: past the failure's back-off
    assert "No stable release newer than 2.9.0 on its line." in capsys.readouterr().out
    # Within the once-a-day interval the cached feed counts as read: still the all-clear.
    clock.t += 60
    net[0] = down
    assert cli.main(args) == 0
    assert "No stable release newer than 2.9.0 on its line." in capsys.readouterr().out
