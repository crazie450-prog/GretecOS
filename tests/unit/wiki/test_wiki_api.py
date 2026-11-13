"""The API client's etiquette on the fake wiki (WO-8.3 acceptance: concurrency 1, batch size,
maxlag, User-Agent, back-off; [H§4])."""

from __future__ import annotations

import gzip
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from gregtec.wiki import api as wiki_api
from gregtec.wiki.api import GateBlocked, Response, WikiApi, WikiError

from .conftest import Sleeps
from .fakewiki import FakeWiki, Server

WORKER = Path(__file__).with_name("wiki_worker.py")


def client(server: Server, sleeps: Sleeps, **kw: float) -> WikiApi:
    return WikiApi(server.api, sleep=sleeps, **{"interval_s": 0.0, **kw})  # type: ignore[arg-type]


def test_every_request_is_a_polite_read(
    server: Server, fake: FakeWiki, sleeps: Sleeps
) -> None:
    api = client(server, sleeps)
    api.siteinfo()
    api.pages(["Electric Blast Furnace", "Pyrolyse Oven"])
    assert len(fake.seen) == 2
    for seen in fake.seen:
        ua = seen.headers["User-Agent"]
        assert ua == wiki_api.USER_AGENT and ua.startswith("GregtecOS-wiki-cache/")
        assert "@" not in ua and "Mozilla" not in ua  # no e-mail, not a browser
        assert "gzip" in seen.headers["Accept-Encoding"]
        assert seen.params["maxlag"] == "5"
        assert seen.params["action"] == "query" and seen.params["format"] == "json"
        assert seen.path.endswith("api.php")


def test_at_most_50_titles_a_batch(
    server: Server, fake: FakeWiki, sleeps: Sleeps
) -> None:
    api = client(server, sleeps)
    with pytest.raises(ValueError, match="at most 50"):
        api.pages([f"Page {i}" for i in range(51)])
    assert fake.seen == []
    api.pages([f"Page {i}" for i in range(50)])
    assert len(fake.seen[0].params["titles"].split("|")) == 50
    with pytest.raises(ValueError):
        api.pages(["A|B"])


def test_one_request_at_a_time_across_clients_and_threads(
    server: Server, fake: FakeWiki, sleeps: Sleeps
) -> None:
    fake.delay_s = 0.05
    clients = [client(server, sleeps) for _ in range(3)]

    def work(api: WikiApi) -> None:
        for _ in range(3):
            api.pages(["Pyrolyse Oven"])

    threads = [threading.Thread(target=work, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(fake.seen) == 9
    assert fake.max_in_flight == 1


def test_a_pause_between_requests(server: Server, fake: FakeWiki) -> None:
    api = WikiApi(server.api, interval_s=0.2)
    t0 = time.monotonic()
    for _ in range(3):
        api.pages(["Pyrolyse Oven"])
    assert time.monotonic() - t0 >= 0.4
    starts = [s.start for s in fake.seen]
    ends = [s.end for s in fake.seen]
    assert all(starts[i + 1] - ends[i] >= 0.18 for i in range(2))


def test_maxlag_waits_its_retry_after_then_tries_again(
    server: Server, fake: FakeWiki, sleeps: Sleeps
) -> None:
    fake.faults.extend(["maxlag", "maxlag"])
    pages = client(server, sleeps).pages(["Pyrolyse Oven"])
    assert pages[0]["title"] == "Pyrolyse Oven"
    assert len(fake.seen) == 3 and sleeps == [7.0, 7.0]


def test_429_and_5xx_back_off_and_give_up(
    server: Server, fake: FakeWiki, sleeps: Sleeps
) -> None:
    fake.faults.extend(["429", "500"])
    client(server, sleeps).pages(["Pyrolyse Oven"])
    assert sleeps[0] == 3.0  # Retry-After
    assert sleeps[1] >= wiki_api.BACKOFF_S * 2  # exponential, no Retry-After
    sleeps.clear()
    fake.seen.clear()
    fake.faults.extend(["503"] * 10)
    with pytest.raises(WikiError, match="gave up after 5 tries"):
        client(server, sleeps).pages(["Pyrolyse Oven"])
    assert len(fake.seen) == 5 and len(sleeps) == 4


def test_a_long_retry_after_is_capped(sleeps: Sleeps) -> None:
    answers = [
        Response(
            503, {"Retry-After": "86400"}, b"", "https://wiki.example.test/api.php"
        ),
        Response(200, {}, b'{"query": {}}', "https://wiki.example.test/api.php"),
    ]

    def transport(url: str, headers: Mapping[str, str], timeout: float) -> Response:
        return answers.pop(0)

    WikiApi("https://wiki.example.test/api.php", transport, sleeps, interval_s=0).query(
        {}
    )
    assert sleeps == [wiki_api.MAX_WAIT_S]


@pytest.mark.parametrize("fault", ["403", "challenge", "html"])
def test_an_anti_bot_gate_stops_at_once(
    server: Server, fake: FakeWiki, sleeps: Sleeps, fault: str
) -> None:
    fake.faults.append(fault)
    with pytest.raises(GateBlocked):
        client(server, sleeps).pages(["Pyrolyse Oven"])
    assert len(fake.seen) == 1 and sleeps == []  # never retried


def test_a_redirect_to_another_host_is_a_gate(sleeps: Sleeps) -> None:
    def transport(url: str, headers: Mapping[str, str], timeout: float) -> Response:
        return Response(200, {}, b"{}", "https://login.example.org/challenge")

    with pytest.raises(GateBlocked, match="redirected"):
        WikiApi("https://wiki.example.test/api.php", transport, sleeps).query({})


def test_an_api_error_is_not_retried(
    server: Server, fake: FakeWiki, sleeps: Sleeps
) -> None:
    with pytest.raises(WikiError, match="unknown"):
        client(server, sleeps).query({"action": "edit"})  # still a query: nothing else
    assert len(fake.seen) == 1 and sleeps == []
    assert fake.seen[0].params["action"] == "query"


def test_only_https_or_this_pc() -> None:
    for bad in (
        "http://wiki.example.test/api.php",
        "https://wiki.example.test/index.php",
        "https://user:pw@wiki.example.test/api.php",
        "https://wiki.example.test/api.php?x=1",
        "ftp://wiki.example.test/api.php",
    ):
        with pytest.raises(ValueError):
            wiki_api.check_url(bad)
    assert wiki_api.check_url("http://127.0.0.1:9/api.php")
    assert wiki_api.check_url(wiki_api.DEFAULT_API)


def test_crawl_and_recentchanges_follow_continue(
    server: Server, fake: FakeWiki, sleeps: Sleeps
) -> None:
    for i in range(120):
        fake.pages[f"Page {i:03}"] = (2000 + i, f"text {i}")
    api = client(server, sleeps)
    batches = list(api.all_pages())
    assert [len(p) for p, _ in batches] == [50, 50, 24]
    assert batches[-1][1] is None and batches[0][1] is not None
    assert all(s.params["gaplimit"] == "50" for s in fake.seen)
    for i in range(3):
        fake.edit(f"Page {i:03}", "new", f"2026-11-10T0{i}:00:00Z")
    titles, now = api.recent_titles("2026-11-09T00:00:00Z")
    assert titles == ["Page 000", "Page 001", "Page 002"] and now == fake.now


def test_the_pause_is_kept_across_clients(server: Server, fake: FakeWiki) -> None:
    """The core holds two caches (the tools' and the idle job's), each with its own client:
    the pause is between any two requests, not per client."""
    a, b = (WikiApi(server.api, interval_s=0.2) for _ in range(2))
    for api in (a, b, a, b):
        api.pages(["Pyrolyse Oven"])
    gaps = [fake.seen[i + 1].start - fake.seen[i].end for i in range(3)]
    assert all(g >= 0.18 for g in gaps), gaps


def test_one_request_at_a_time_across_processes(
    server: Server, fake: FakeWiki, tmp_path: Path
) -> None:
    """`gos wiki crawl` in the CLI while the core fetches or refreshes: two processes (and a
    thread here) share data/wiki.lock, so one request reaches the wiki at a time, with the
    pause between any two."""
    fake.delay_s = 0.03
    lock = tmp_path / "data" / "wiki.lock"
    procs = [
        subprocess.Popen(
            [sys.executable, str(WORKER), server.api, str(lock), "4", "0.1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for _ in range(2)
    ]
    here = WikiApi(server.api, interval_s=0.1, lock_path=lock)
    for _ in range(4):
        here.pages(["Pyrolyse Oven"])
    for p in procs:
        out, _ = p.communicate(timeout=120)
        assert p.returncode == 0, out.decode("utf-8", "replace")
    assert len(fake.seen) == 12 and fake.max_in_flight == 1
    seen = sorted(fake.seen, key=lambda s: s.start)
    gaps = [seen[i + 1].start - seen[i].end for i in range(11)]
    assert all(g >= 0.08 for g in gaps), gaps


def test_a_back_off_holds_the_turn(
    sleeps: Sleeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While one client waits out a 503, nobody else sends; a fetch on demand gives up."""
    monkeypatch.setattr(wiki_api, "ON_DEMAND_LOCK_S", 0.1)
    url = "https://wiki.example.test/api.php"
    answers = [Response(503, {"Retry-After": "3"}, b"", url)]
    sent: list[str] = []

    def transport(u: str, headers: Mapping[str, str], timeout: float) -> Response:
        sent.append(u)
        return answers.pop(0) if answers else Response(200, {}, b'{"query": {}}', url)

    other = WikiApi(url, transport, interval_s=0)
    tried: list[BaseException] = []

    def backing_off(s: float) -> None:
        sleeps.append(s)

        def go() -> None:
            try:
                other.query({"titles": "X"}, on_demand=True)
            except WikiError as exc:
                tried.append(exc)

        t = threading.Thread(target=go)
        t.start()
        t.join()

    WikiApi(url, transport, backing_off, interval_s=0).query({"titles": "Y"})
    assert sleeps == [3.0] and len(sent) == 2  # only the backing-off client's two
    assert len(tried) == 1 and isinstance(tried[0], wiki_api.Busy)


def test_on_demand_gives_up_sooner(
    server: Server, fake: FakeWiki, sleeps: Sleeps
) -> None:
    fake.faults.extend(["503"] * 10)
    with pytest.raises(WikiError, match="gave up after 2 tries"):
        client(server, sleeps).query({"titles": "Pyrolyse Oven"}, on_demand=True)
    assert len(fake.seen) == 2 and sleeps == [3.0]


def test_a_bad_retry_after_backs_off_instead(sleeps: Sleeps) -> None:
    url = "https://wiki.example.test/api.php"
    answers = [
        Response(503, {"Retry-After": v}, b"", url)
        for v in ("nan", "inf", "-5", "soon")
    ]

    def transport(u: str, headers: Mapping[str, str], timeout: float) -> Response:
        return answers.pop(0) if answers else Response(200, {}, b'{"query": {}}', url)

    WikiApi(url, transport, sleeps, interval_s=0, backoff_s=1.0).query({})
    assert len(sleeps) == 4
    assert all(1.0 <= s <= wiki_api.MAX_WAIT_S for s in sleeps), sleeps


def test_a_gzip_bomb_is_refused(
    sleeps: Sleeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wiki_api, "MAX_BODY", 4096)
    url = "https://wiki.example.test/api.php"
    bomb = gzip.compress(b'{"query": "' + b"0" * 100_000 + b'"}')
    assert len(bomb) < 4096

    def transport(u: str, headers: Mapping[str, str], timeout: float) -> Response:
        return Response(200, {"Content-Encoding": "gzip"}, bomb, url)

    with pytest.raises(WikiError, match="unpacked"):
        WikiApi(url, transport, sleeps, interval_s=0).query({})
