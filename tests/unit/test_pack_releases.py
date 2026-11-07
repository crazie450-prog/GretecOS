"""WO-1.9's release watcher and stock check, with a fake network and clock (no real HTTP)."""

from __future__ import annotations

import json
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
