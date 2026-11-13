"""``gos wiki`` (WO-8.3, plan 7.8): Clayton's commands for the wiki cache.

- ``gos wiki licence [--api URL] [--yes]``: V14's query (``meta=siteinfo&siprop=general|
  rightsinfo``), the licence shown, then recorded when he says yes. Until then the cache stays
  empty. Run again, it records the licence afresh and clears a recorded anti-bot gate if the
  wiki answers.
- ``gos wiki crawl [--again]``: the one-time crawl (resumable: Ctrl+C stops it after the
  current batch and the next run goes on). ``--again`` starts over.
- ``gos wiki refresh``: the idle-maintenance refresh now.
- ``gos wiki status``: licence, pages, crawl and refresh state, a recorded gate.
- ``gos wiki show TITLE``: one page's citation, attribution and first lines (cache first).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.store import DB_NAME
from gregtec.wiki.api import DEFAULT_API, GateBlocked, WikiError
from gregtec.wiki.cache import NoLicence, Stats, WikiCache, read_licence

COMMANDS = ("wiki",)
GATE_HELP = (
    "The wiki refused scripted access. GOS won't try to get around it. Ask the GTNH wiki "
    "admins (GTNH Discord) for API access or a Special:Export dump (docs/runbooks/wiki.md), "
    "then run `gos wiki licence` again."
)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser("wiki", help="the GTNH wiki cache (WO-8.3)")
    p.add_argument(
        "--config", type=Path, default=None, help="gos.toml (default: GOS_HOME's)"
    )
    sub = p.add_subparsers(dest="wiki_command", required=True)
    lic = sub.add_parser(
        "licence",
        help="show the wiki's licence and record it (needed before any caching)",
    )
    lic.add_argument(
        "--api", default=DEFAULT_API, help=f"api.php URL (default {DEFAULT_API})"
    )
    lic.add_argument("--yes", action="store_true", help="record it without asking")
    crawl = sub.add_parser("crawl", help="the one-time crawl (resumable)")
    crawl.add_argument("--again", action="store_true", help="start the crawl over")
    sub.add_parser(
        "refresh", help="fetch the pages changed on the wiki since the last refresh"
    )
    sub.add_parser("status", help="licence, pages, crawl and refresh state")
    show = sub.add_parser(
        "show", help="one page's citation, attribution and first lines"
    )
    show.add_argument("title")


def run(
    args: argparse.Namespace,
    out: TextIO = sys.stdout,
    cache: WikiCache | None = None,
    answer: str | None = None,
) -> int:
    """``cache`` and ``answer`` (the y/N reply) are for tests."""
    if cache is None:
        try:
            cfg = config.load(args.config)
        except (OSError, ValueError) as exc:
            print(f"Can't read gos.toml: {exc}", file=out)
            return 2
        cache = WikiCache(cfg.data_dir / DB_NAME)
    cmd = args.wiki_command
    try:
        if cmd == "licence":
            return _licence(cache, args.api, args.yes, out, answer)
        if cmd == "crawl":
            return _crawl(cache, args.again, out)
        if cmd == "refresh":
            stats, notes = cache.refresh()
            print(f"Refreshed: {stats.line()}.", file=out)
            for note in notes:
                print(f"note: {note}", file=out)
            return 0
        if cmd == "status":
            return _status(cache, out)
        return _show(cache, args.title, out)
    except GateBlocked as exc:
        print(f"Stopped: {exc}\n{GATE_HELP}", file=out)
        return 1
    except (WikiError, ValueError) as exc:
        print(str(exc), file=out)
        return 1


def _licence(
    cache: WikiCache, api: str, yes: bool, out: TextIO, answer: str | None
) -> int:
    client = cache.api_for(api)
    try:
        lic = read_licence(client)
    except GateBlocked as exc:
        cache.db.parent.mkdir(parents=True, exist_ok=True)
        cache.block(str(exc))
        raise
    print(
        f"Wiki:     {lic.sitename} ({lic.server}; {lic.generator or 'MediaWiki'})",
        file=out,
    )
    print(f"Licence:  {lic.text}", file=out)
    print(f"Terms:    {lic.url or '(no URL given)'}", file=out)
    print(
        "GOS keeps a personal cache only: wiki text is never committed, bundled or shared, "
        "and every answer that uses it cites the page and names this licence.",
        file=out,
    )
    if not yes:
        reply = (
            answer
            if answer is not None
            else input("Record it and allow the cache? [y/N] ")
        )
        if reply.strip().lower() not in ("y", "yes"):
            print("Not recorded: the cache stays empty.", file=out)
            return 1
    cache.db.parent.mkdir(parents=True, exist_ok=True)
    cache.record_licence(lic)
    print("Recorded. Next: gos wiki crawl", file=out)
    return 0


def _crawl(cache: WikiCache, again: bool, out: TextIO) -> int:
    def progress(stats: Stats) -> None:
        print(f"  {stats.line()}", file=out, flush=True)

    try:
        stats, done = cache.crawl(progress=progress, again=again)
    except NoLicence as exc:
        print(str(exc), file=out)
        return 1
    except KeyboardInterrupt:
        print("Stopped; `gos wiki crawl` goes on from here.", file=out)
        return 1
    print(
        ("Crawl finished: " if done else "Crawl stopped: ") + stats.line() + ".",
        file=out,
    )
    return 0 if done else 1


def _status(cache: WikiCache, out: TextIO) -> int:
    if not cache.db.is_file():
        print("No DB yet: no licence recorded, nothing cached.", file=out)
        return 0
    s = cache.status()
    lic = s["licence"]
    if lic is None:
        print(
            "Licence:  not recorded (gos wiki licence); the cache stays empty", file=out
        )
    else:
        print(f"Licence:  {lic.terms}, recorded {lic.recorded} for {lic.api}", file=out)
    crawl = s["crawl"] or {}
    state = (
        "finished " + str(crawl.get("finished"))
        if crawl.get("done")
        else ("under way" if crawl else "not started")
    )
    print(f"Pages:    {s['pages']}", file=out)
    print(f"Crawl:    {state}", file=out)
    print(f"Refresh:  changes since {s['rc_since'] or '-'}", file=out)
    if s["blocked"]:
        print(f"Blocked:  {s['blocked']}\n{GATE_HELP}", file=out)
    return 0


def _show(cache: WikiCache, title: str, out: TextIO) -> int:
    page = cache.fetch(title)
    if page is None:
        print(f"The wiki has no page {title!r}.", file=out)
        return 1
    lic = cache.licence()
    print(f"{page.title}  {page.cite}  {page.url}", file=out)
    if lic is not None:
        print(lic.attribution(page.title), file=out)
    for line in page.text.splitlines()[:20]:
        print(f"  {line}", file=out)
    return 0
