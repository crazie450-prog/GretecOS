"""The wiki cache in ``GOS_HOME/data/gos.sqlite`` (WO-8.3, plan 7.8, 6.6, [H§4], [QA:D5]).

- **Tables.** ``wiki_page(title, revid, fetched, url, text)`` holds each page's newest wikitext
  and revid; ``wiki_fts`` is its FTS5 index (external content, kept in step here: the old row
  is deleted from the index before a page changes). Wikitext is stored as the wiki gives it, cut
  at :data:`MAX_TEXT` characters (said in the text).
- **The licence comes first** (V14). :func:`read_licence` asks the wiki's
  ``meta=siteinfo&siprop=general|rightsinfo``; ``gos wiki licence`` shows it to Clayton and,
  when he accepts, :meth:`WikiCache.record_licence` keeps it in ``kv`` (``wiki.licence``) with
  the API it came from. **Nothing fills the cache until a licence is recorded** (every write
  path checks), and a wiki that states no licence can't be recorded. The cache is personal: no
  wiki text is committed, bundled or redistributed.
- **The one-time crawl** (:meth:`WikiCache.crawl`, ``gos wiki crawl``, Clayton's step):
  ``generator=allpages`` 50 pages a request, resumable (its continue mark is kept in ``kv``
  after each batch). When it ends, the refresh starts from the crawl's start (less a margin).
- **The refresh** (:meth:`WikiCache.refresh`, the idle-maintenance job ``wiki``, plan 6.6):
  ``list=recentchanges`` since the last refresh, then the changed titles 50 at a time; a page
  the wiki no longer has is removed. It needs the crawl to have finished.
- **Fetch on demand** (:meth:`WikiCache.fetch`): a page not in the cache is asked for once
  (``wiki_fetch``), and a redirect's target once more; a missing page is an answer, not an
  error, and is remembered for :data:`MISSING_TTL_S` so a loop of made-up titles can't make a
  request each. On demand gives up quickly when a crawl or refresh holds the turn.
- **Redirects are pages**: a redirect is stored with its own ``#REDIRECT [[...]]`` wikitext and
  revid (no request follows redirects), and :func:`page_of` follows one hop. So a page that
  becomes a redirect, a move that leaves one and a retargeted redirect all replace the old
  title's text in the refresh.
- **An anti-bot gate stops everything** (plan 7.8): :class:`gregtec.wiki.api.GateBlocked` is
  recorded in ``kv`` (``wiki.blocked``) and every automatic request (refresh, fetch on demand)
  refuses until Clayton runs ``gos wiki licence`` again, which clears it only if the wiki
  answers. Never worked around.
- **Citations and attribution** [QA:D5]: a page is cited ``w:<Title_with_underscores>@<revid>``
  (:attr:`Page.cite`); :meth:`Licence.attribution` names the page, the wiki, its licence and
  that the text comes unchanged from a personal cache.

All requests go through :class:`gregtec.wiki.api.WikiApi` (one at a time across processes, via
``data/wiki.lock`` next to the DB; maxlag, 50 titles a batch, back-off). Wiki text is data,
never instructions (plan 0.1 principle 8); what siteinfo says about the wiki (its name, server,
licence) is printed and quoted, so it is kept to one line of printable text.
"""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
import time
import urllib.parse
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypeVar

from gregtec.db import access
from gregtec.wiki.api import MAX_TITLES, GateBlocked, WikiApi, WikiError, check_url

T = TypeVar("T")

KV_LICENCE = "wiki.licence"
KV_BLOCKED = "wiki.blocked"
KV_CRAWL = "wiki.crawl"
KV_RC_SINCE = "wiki.rc_since"
MAX_TEXT = (
    400_000  # characters of one page kept (the tools' DB values are capped at 1 MB)
)
RC_MARGIN_S = 3600  # the refresh starts this long before the crawl began (clock skew)
RC_KEEP_DAYS = 30  # MediaWiki keeps recent changes 90 days by default; assume less
MISSING_TTL_S = 900  # a title the wiki hasn't got isn't asked for again this soon
LOCK_NAME = "wiki.lock"
REDIRECT = re.compile(r"^\s*#redirect\s*:?\s*\[\[([^\]|#]+)", re.IGNORECASE)
_SPACE = re.compile(r"[\s_]+")
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


class NoLicence(WikiError):
    """No licence recorded: the cache refuses to fill."""


class Blocked(WikiError):
    """An anti-bot gate was recorded: no request until Clayton deals with it."""


def utc(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def normalize(title: str) -> str:
    """A title as MediaWiki keys it: no fragment, underscores as spaces, one space between
    words, the first letter upper case."""
    t = _SPACE.sub(" ", title.split("#", 1)[0]).strip()
    return t[:1].upper() + t[1:]


def cite(title: str, revid: int) -> str:
    """``w:<page>@<revid>`` (plan 0.1 principle 2): spaces as underscores, as in the wiki's URLs,
    so a citation is one word."""
    return f"w:{normalize(title).replace(' ', '_')}@{revid}"


@dataclass(frozen=True)
class Licence:
    """What the wiki's siteinfo says about itself, as recorded."""

    api: str
    sitename: str
    server: str
    articlepath: str
    text: str  # rightsinfo text ("Creative Commons Attribution-ShareAlike")
    url: str  # rightsinfo url
    generator: str = ""
    recorded: str = ""

    def article_url(self, title: str) -> str:
        path = self.articlepath or "/wiki/$1"
        name = urllib.parse.quote(normalize(title).replace(" ", "_"), safe="/:()',!")
        return self.server + path.replace("$1", name)

    @property
    def terms(self) -> str:
        return f"{self.text} ({self.url})" if self.url else self.text

    def attribution(self, title: str | None = None) -> str:
        if title is None:
            return (
                f"Wiki text from {self.sitename} ({self.server}), {self.terms}; "
                "unchanged, from GOS's personal cache (not redistributed)."
            )
        return (
            f'"{normalize(title)}" from {self.sitename} ({self.article_url(title)}), '
            f"{self.terms}; unchanged wikitext from GOS's personal cache (not "
            "redistributed)."
        )


@dataclass(frozen=True)
class Page:
    title: str
    revid: int
    fetched: int
    url: str
    text: str
    redirected_from: str | None = None

    @property
    def cite(self) -> str:
        return cite(self.title, self.revid)


def one_line(value: object, limit: int) -> str:
    """``value`` as one line of printable text (no control characters, newlines or terminal
    escapes), at most ``limit`` characters: the wiki chose it."""
    return _UNPRINTABLE.sub(" ", str(value or "")).strip()[:limit]


def parse_licence(api: str, info: dict[str, Any], now: float) -> Licence:
    """The licence in a siteinfo answer; ValueError when the wiki states none."""
    general = info.get("general") if isinstance(info.get("general"), dict) else {}
    rights = info.get("rightsinfo") if isinstance(info.get("rightsinfo"), dict) else {}
    text = one_line(rights.get("text"), 300)
    url = one_line(rights.get("url"), 500)
    if not text and not url:
        raise ValueError(
            "the wiki's siteinfo states no licence (rightsinfo is empty): ask the wiki admins "
            "before caching anything"
        )
    if url and urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        url = ""
    server = one_line(general.get("server"), 300)
    if server.startswith("//"):
        server = "https:" + server
    if not server:
        p = urllib.parse.urlsplit(api)
        server = f"{p.scheme}://{p.netloc}"
    return Licence(
        api=api,
        sitename=one_line(general.get("sitename"), 200) or "the wiki",
        server=server,
        articlepath=one_line(general.get("articlepath"), 200) or "/wiki/$1",
        text=text or url,
        url=url,
        generator=one_line(general.get("generator"), 100),
        recorded=utc(now),
    )


def read_licence(api: WikiApi, now: float | None = None) -> Licence:
    """Ask the wiki (V14's query) and parse its licence; nothing is recorded."""
    return parse_licence(api.api, api.siteinfo(), time.time() if now is None else now)


def _kv_get(conn: sqlite3.Connection, key: str) -> object:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _kv_set(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
        "value = excluded.value, upd = excluded.upd",
        (key, json.dumps(value), int(time.time())),
    )


def licence_of(conn: sqlite3.Connection) -> Licence | None:
    raw = _kv_get(conn, KV_LICENCE)
    if not isinstance(raw, dict):
        return None
    try:
        return Licence(
            **{k: str(v) for k, v in raw.items() if k in Licence.__dataclass_fields__}
        )
    except TypeError:
        return None


def page_of(conn: sqlite3.Connection, title: str, hops: int = 1) -> Page | None:
    """The cached page ``title`` (a cached redirect followed ``hops`` times), or None."""
    want = normalize(title)
    row = conn.execute(
        "SELECT title, revid, fetched, url, text FROM wiki_page WHERE title = ?",
        (want,),
    ).fetchone()
    if row is None:
        return None
    text = row["text"] or ""
    m = REDIRECT.match(text)
    if m and hops > 0:
        target = page_of(conn, m[1], hops - 1)
        if target is not None:
            return Page(**{**asdict(target), "redirected_from": want})
    return Page(
        row["title"],
        int(row["revid"]),
        int(row["fetched"] or 0),
        row["url"] or "",
        text,
    )


@dataclass
class Stats:
    stored: int = 0
    unchanged: int = 0
    removed: int = 0
    requests: int = 0

    def line(self) -> str:
        return (
            f"{self.stored} stored, {self.unchanged} unchanged, {self.removed} removed, "
            f"{self.requests} requests"
        )


class WikiCache:
    """The cache over the DB at ``db``. ``api_for`` makes the client for the recorded API
    (tests pass one for the fake wiki)."""

    def __init__(
        self,
        db: Path,
        api_for: Callable[[str], WikiApi] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self.api_for = api_for or (
            lambda api: WikiApi(api, lock_path=db.parent / LOCK_NAME)
        )
        self.now = now
        self._api: WikiApi | None = None
        self._missing: dict[str, float] = {}  # title -> when the wiki said it has none

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = access.connect(self.db)
        try:
            yield conn
        finally:
            conn.close()

    # --- state -------------------------------------------------------------------------

    def licence(self) -> Licence | None:
        if not self.db.is_file():
            return None
        with self._conn() as conn:
            return licence_of(conn)

    def blocked(self) -> str | None:
        if not self.db.is_file():
            return None
        with self._conn() as conn:
            raw = _kv_get(conn, KV_BLOCKED)
        return str(raw.get("why")) if isinstance(raw, dict) else None

    def status(self) -> dict[str, Any]:
        with self._conn() as conn:
            n = conn.execute("SELECT count(*) FROM wiki_page").fetchone()[0]
            return {
                "licence": licence_of(conn),
                "blocked": _dict(_kv_get(conn, KV_BLOCKED)).get("why"),
                "crawl": _kv_get(conn, KV_CRAWL),
                "rc_since": _kv_get(conn, KV_RC_SINCE),
                "pages": int(n),
            }

    def record_licence(self, lic: Licence) -> None:
        """Clayton accepted ``lic`` (shown by ``gos wiki licence``): the cache may fill from its
        API. A gate recorded before is cleared: the wiki just answered."""
        check_url(lic.api)
        with self._conn() as conn, _tx(conn):
            old = licence_of(conn)
            _kv_set(conn, KV_LICENCE, asdict(lic))
            conn.execute("DELETE FROM kv WHERE key = ?", (KV_BLOCKED,))
            if old is not None and old.api != lic.api:  # another wiki: start again
                conn.execute(
                    "DELETE FROM kv WHERE key IN (?, ?)", (KV_CRAWL, KV_RC_SINCE)
                )
        self._api = None

    def block(self, why: str) -> None:
        with self._conn() as conn, _tx(conn):
            _kv_set(conn, KV_BLOCKED, {"why": why, "at": utc(self.now())})

    def _client(self) -> tuple[WikiApi, Licence]:
        """The client for the recorded licence's API; refuses with no licence or a gate."""
        lic = self.licence()
        if lic is None:
            raise NoLicence(
                "no wiki licence is recorded, so the cache stays empty: Clayton runs "
                "`gos wiki licence` first (V14)"
            )
        why = self.blocked()
        if why is not None:
            raise Blocked(
                f"the wiki blocked scripted access ({why}); GOS won't try again until "
                "Clayton asks the wiki admins (runbook wiki.md) and runs `gos wiki licence`"
            )
        if self._api is None or self._api.api != lic.api:
            self._api = self.api_for(lic.api)
        return self._api, lic

    def _guarded(self, run: Callable[[], T]) -> T:
        try:
            return run()
        except GateBlocked as exc:
            self.block(str(exc))
            raise

    # --- writes ------------------------------------------------------------------------

    def _store(
        self,
        pages: Sequence[dict[str, Any]],
        lic: Licence,
        stats: Stats,
        delete_missing: bool,
    ) -> list[Page]:
        """Put each page object of an answer that has its wikitext; ``missing`` pages are
        removed when ``delete_missing``. Returns the pages stored or unchanged."""
        now = int(self.now())
        kept: list[Page] = []
        with self._conn() as conn, _tx(conn):
            if licence_of(conn) is None:  # every write path: no licence, no fill
                raise NoLicence("no wiki licence is recorded")
            for p in pages:
                title = p.get("title")
                if not isinstance(title, str) or not title.strip():
                    continue
                title = normalize(title)
                if p.get("missing") or p.get("invalid"):
                    if delete_missing and _delete(conn, title):
                        stats.removed += 1
                    continue
                revs = p.get("revisions")
                if (
                    not isinstance(revs, list)
                    or not revs
                    or not isinstance(revs[0], dict)
                ):
                    continue  # its content comes in a later batch of the answer
                rev = revs[0]
                main = (rev.get("slots") or {}).get("main") or {}
                text = main.get("content") if isinstance(main, dict) else None
                if text is None:
                    text = rev.get("content")
                revid = rev.get("revid")
                if not isinstance(text, str) or not isinstance(revid, int):
                    continue
                if len(text) > MAX_TEXT:
                    cut = len(text) - MAX_TEXT
                    text = (
                        text[:MAX_TEXT] + f"\n[GOS: {cut} more characters not cached]"
                    )
                url = lic.article_url(title)
                if _put(conn, title, revid, now, url, text):
                    stats.stored += 1
                else:
                    stats.unchanged += 1
                kept.append(Page(title, revid, now, url, text))
        return kept

    def fetch(self, title: str) -> Page | None:
        """The page ``title``: from the cache, else asked of the wiki once (on demand) and
        cached; a redirect's target likewise. None when the wiki has no such page."""
        want = normalize(title)
        if not want:
            raise ValueError("no title")
        found = self._cached(want) or self._ask(want)
        m = REDIRECT.match(found.text) if found is not None else None
        if found is None or m is None:
            return found
        # a redirect whose target isn't cached: ask for the target (one hop, as page_of)
        target = self._ask(normalize(m[1]))
        if target is None:
            return found
        return Page(**{**asdict(target), "redirected_from": found.title})

    def _cached(self, title: str) -> Page | None:
        if not self.db.is_file():
            return None
        with self._conn() as conn:
            return page_of(conn, title)

    def _ask(self, title: str) -> Page | None:
        """``title`` asked of the wiki (on demand), stored, and read back from the cache."""
        seen = self._missing.get(title)
        if seen is not None and self.now() - seen < MISSING_TTL_S:
            return None
        api, lic = self._client()
        answer = self._guarded(lambda: api.pages([title], on_demand=True))
        if not self._store(answer, lic, Stats(), delete_missing=False):
            self._missing[title] = self.now()
            return None
        self._missing.pop(title, None)
        return self._cached(title)

    def crawl(
        self,
        stop: Callable[[], bool] = lambda: False,
        progress: Callable[[Stats], None] | None = None,
        again: bool = False,
    ) -> tuple[Stats, bool]:
        """The one-time crawl (resumed where it stopped). Returns (stats, finished)."""
        api, lic = self._client()
        with self._conn() as conn:
            state = _dict(_kv_get(conn, KV_CRAWL))
        if again or state.get("api") != lic.api:
            state = {}
        if state.get("done") and not again:
            return Stats(), True
        if not state:
            state = {
                "api": lic.api,
                "started": utc(self.now() - RC_MARGIN_S),
                "pages": 0,
            }
        stats = Stats()
        before = api.requests
        batches = self._guarded(lambda: api.all_pages(state.get("continue")))
        while True:
            if stop():
                return stats, False
            try:
                pages, mark = self._guarded(lambda: next(batches))
            except StopIteration:
                break
            self._store(pages, lic, stats, delete_missing=False)
            stats.requests = api.requests - before
            state["pages"] = int(state.get("pages", 0)) + len(pages)
            state["continue"] = mark
            with self._conn() as conn, _tx(conn):
                _kv_set(conn, KV_CRAWL, state)
            if progress is not None:
                progress(stats)
            if mark is None:
                break
        state.update(done=True, finished=utc(self.now()), **{"continue": None})
        with self._conn() as conn, _tx(conn):
            _kv_set(conn, KV_CRAWL, state)
            if again or _kv_get(conn, KV_RC_SINCE) is None:
                _kv_set(conn, KV_RC_SINCE, state["started"])
        return stats, True

    def refresh(
        self, stop: Callable[[], bool] = lambda: False
    ) -> tuple[Stats, list[str]]:
        """Pages changed on the wiki since the last refresh, fetched again (plan 6.6). Returns
        (stats, notes). Refuses before the crawl has finished."""
        api, lic = self._client()
        with self._conn() as conn:
            crawl = _dict(_kv_get(conn, KV_CRAWL))
            since = _kv_get(conn, KV_RC_SINCE)
        if not crawl.get("done") or not isinstance(since, str):
            raise WikiError(
                "the one-time crawl hasn't finished: `gos wiki crawl` first"
            )
        notes: list[str] = []
        try:
            age = self.now() - calendar.timegm(
                time.strptime(since, "%Y-%m-%dT%H:%M:%SZ")
            )
        except ValueError:
            age = 0
        if age > RC_KEEP_DAYS * 86400 + 86400:
            notes.append(
                f"the last refresh was over {RC_KEEP_DAYS} days ago; the wiki may have dropped "
                "older changes: `gos wiki crawl --again` catches everything"
            )
        stats = Stats()
        before = api.requests
        titles, wiki_now = self._guarded(lambda: api.recent_titles(since))
        for i in range(0, len(titles), MAX_TITLES):
            if stop():
                stats.requests = api.requests - before
                return stats, [*notes, "stopped; the next run does the rest"]
            batch = [normalize(t) for t in titles[i : i + MAX_TITLES]]
            answer = self._guarded(lambda b=batch: api.pages(b))
            # redirects aren't followed: a title that now redirects (an edit, or a move that
            # left one) gets its own #REDIRECT text and revid in place of the old page's
            self._store(answer, lic, stats, delete_missing=True)
        stats.requests = api.requests - before
        with self._conn() as conn, _tx(conn):
            _kv_set(conn, KV_RC_SINCE, wiki_now or utc(self.now()))
        return stats, notes

    def maintenance(self, stop: Callable[[], bool]) -> tuple[str, str]:
        """The idle-maintenance job (plan 6.6): (ok | skipped | failed, detail)."""
        if self.licence() is None:
            return "skipped", "no wiki licence recorded (gos wiki licence)"
        if self.blocked() is not None:
            return "skipped", f"the wiki blocked scripted access: {self.blocked()}"
        with self._conn() as conn:
            crawl = _dict(_kv_get(conn, KV_CRAWL))
        if not crawl.get("done"):
            return "skipped", "the one-time crawl hasn't finished (gos wiki crawl)"
        try:
            stats, notes = self.refresh(stop)
        except GateBlocked as exc:
            return "failed", f"stopped: an anti-bot gate ({exc}); see runbook wiki.md"
        except (WikiError, OSError) as exc:
            return "failed", str(exc)[:300]
        return "ok", "; ".join([stats.line(), *notes])


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _put(
    conn: sqlite3.Connection, title: str, revid: int, now: int, url: str, text: str
) -> bool:
    """Store a page and its index row; False when the cache already has this revision."""
    old = conn.execute(
        "SELECT rowid, revid, title, text FROM wiki_page WHERE title = ?", (title,)
    ).fetchone()
    if old is not None and old["revid"] == revid:
        return False
    if old is not None:
        conn.execute(
            "INSERT INTO wiki_fts(wiki_fts, rowid, title, text) VALUES('delete', ?, ?, ?)",
            (old["rowid"], old["title"], old["text"]),
        )
        conn.execute(
            "UPDATE wiki_page SET revid = ?, fetched = ?, url = ?, text = ? WHERE rowid = ?",
            (revid, now, url, text, old["rowid"]),
        )
        rowid = old["rowid"]
    else:
        cur = conn.execute(
            "INSERT INTO wiki_page(title, revid, fetched, url, text) VALUES (?, ?, ?, ?, ?)",
            (title, revid, now, url, text),
        )
        rowid = cur.lastrowid
    conn.execute(
        "INSERT INTO wiki_fts(rowid, title, text) VALUES (?, ?, ?)",
        (rowid, title, text),
    )
    return True


def _delete(conn: sqlite3.Connection, title: str) -> bool:
    old = conn.execute(
        "SELECT rowid, title, text FROM wiki_page WHERE title = ?", (title,)
    ).fetchone()
    if old is None:
        return False
    conn.execute(
        "INSERT INTO wiki_fts(wiki_fts, rowid, title, text) VALUES('delete', ?, ?, ?)",
        (old["rowid"], old["title"], old["text"]),
    )
    conn.execute("DELETE FROM wiki_page WHERE rowid = ?", (old["rowid"],))
    return True
