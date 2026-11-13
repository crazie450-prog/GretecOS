"""The wiki cache lives beside the game data in gos.sqlite: a ``gos db build`` (the real loader,
on the tool tests' self-authored export run) keeps it, and the tools read both (WO-8.3)."""

from __future__ import annotations

from pathlib import Path

from gregtec.agents.tools import build_registry
from gregtec.agents.tools.registry import DEEP
from gregtec.agents.tools.tsv import parse
from gregtec.db import build
from gregtec.wiki.api import WikiApi
from gregtec.wiki.cache import WikiCache, read_licence

from ..tools.conftest import build_home
from .conftest import NOW, Sleeps
from .fakewiki import Server


def test_a_db_rebuild_keeps_the_wiki_cache(
    tmp_path: Path, server: Server, sleeps: Sleeps
) -> None:
    home = tmp_path / "home"
    db = build_home(home, quests=False)
    cache = WikiCache(
        db,
        api_for=lambda api: WikiApi(api, sleep=sleeps, interval_s=0),
        now=lambda: NOW,
    )
    cache.record_licence(read_licence(cache.api_for(server.api)))
    assert cache.crawl()[1]
    build.build(home)  # the same run again: recipes replaced, the wiki untouched
    reg = build_registry(db, wiki=cache)
    p = parse(reg.call("wiki_search", {"q": "pyrolyse"}, lane=DEEP).text)
    assert [r["cite"] for r in p.rows] == ["w:Pyrolyse_Oven@1004"]
    steel = parse(reg.call("item_resolve", {"text": "steel ingot"}, lane=DEEP).text)
    assert steel.rows  # the game data is there too
    assert cache.status()["pages"] == 4 and cache.licence() is not None
