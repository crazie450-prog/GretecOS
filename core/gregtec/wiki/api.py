"""The MediaWiki ``api.php`` client and its etiquette (WO-8.3, plan 7.8, [H§4]).

Etiquette (https://www.mediawiki.org/wiki/API:Etiquette, [H§4]), all enforced here, not left to
callers:

- **One request at a time.** Every request of every :class:`WikiApi` in the process holds one
  lock (:data:`_SERIAL`) from sending to the end of the pause after it, so the crawl, the idle
  refresh and an agent's fetch on demand never overlap.
- **A pause between requests**: at least ``interval_s`` (1 s, [H§4]) from the end of one to the
  start of the next.
- **A descriptive User-Agent** (:data:`USER_AGENT`): the application and the project's public
  page, no personal details (no name, no e-mail), and ``Accept-Encoding: gzip``.
- **maxlag** (5 s) on every request. A ``maxlag`` error, a 429 or a 503 waits its
  ``Retry-After`` (capped), else an exponential back-off, then tries again, at most
  ``retries`` times; any other 5xx or a network error backs off the same way.
- **At most 50 titles a batch** (:data:`MAX_TITLES`): a longer list is refused, never split
  here (the caller batches).
- **Read-only GETs**: only ``action=query``; nothing here logs in, edits or posts.

**Anti-bot gates stop it** (plan 7.8): a 401 or 403, a page that isn't the API's JSON (an HTML
challenge or login page), a redirect to another host or a body with a challenge's markers raises
:class:`GateBlocked`, which is never retried and never worked around (no other User-Agent, no
browser, no cookies). The cache records it and stops until Clayton asks the wiki admins for API
access or a ``Special:Export`` dump (runbook ``docs/runbooks/wiki.md``).

Everything the wiki returns is data (plan 0.1 principle 8): this module only parses JSON.
"""

from __future__ import annotations

import gzip
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DEFAULT_API = "https://wiki.gtnewhorizons.com/api.php"
USER_AGENT = (
    "GregtecOS-wiki-cache/0.1 (personal offline cache for one player's assistant; "
    "one request at a time; +https://github.com/crazie450-prog/GretecOS)"
)
MAX_TITLES = 50  # [H§4]: at most 50 titles per batch
MAXLAG_S = 5
INTERVAL_S = 1.0  # [H§4]: about 1 s between requests
RETRIES = 4
BACKOFF_S = 2.0
MAX_WAIT_S = 120.0  # the longest Retry-After or back-off honoured
TIMEOUT_S = 30.0
MAX_BODY = 32 << 20
# Text a challenge or bot-check page carries (Cloudflare, Anubis, hCaptcha, reCAPTCHA ...).
CHALLENGE_MARKS = (
    b"cf-chl",
    b"challenge-platform",
    b"just a moment",
    b"captcha",
    b"anubis",
    b"are you a robot",
    b"verify you are human",
)

# One lock for every client in the process: one request at a time, whoever sends it.
_SERIAL = threading.Lock()


class WikiError(Exception):
    """The wiki answered with an error, or kept failing after the back-off."""


class GateBlocked(WikiError):
    """An anti-bot gate or an access refusal: stop, never retry or work around it."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str  # the URL that answered (after redirects)


Transport = Callable[[str, Mapping[str, str], float], Response]


def http_get(url: str, headers: Mapping[str, str], timeout: float) -> Response:
    """One GET with urllib; an HTTP error status is a response, a network failure OSError."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            body = r.read(MAX_BODY + 1)
            return Response(r.status, dict(r.headers.items()), body, r.geturl())
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_BODY + 1) if exc.fp is not None else b""
        hdrs = dict(exc.headers.items()) if exc.headers is not None else {}
        return Response(exc.code, hdrs, body, exc.geturl() or url)
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from None


def check_url(api: str) -> str:
    """``api`` if it is an ``api.php`` URL GOS may use: https, or http to this PC only (the
    tests' fake wiki), with no user, query or fragment."""
    p = urllib.parse.urlsplit(api)
    local = p.hostname in ("127.0.0.1", "localhost", "::1")
    if p.scheme != "https" and not (p.scheme == "http" and local):
        raise ValueError(f"{api}: the wiki API must be https")
    if not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError(f"{api}: give the api.php URL only")
    if not p.path.endswith("api.php"):
        raise ValueError(f"{api}: not a MediaWiki api.php URL")
    return api


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


@dataclass
class WikiApi:
    """A MediaWiki API at ``api`` with the etiquette above. ``transport``, ``sleep`` and
    ``monotonic`` are injectable for tests."""

    api: str = DEFAULT_API
    transport: Transport = http_get
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    interval_s: float = INTERVAL_S
    maxlag: int = MAXLAG_S
    retries: int = RETRIES
    backoff_s: float = BACKOFF_S
    max_wait_s: float = MAX_WAIT_S
    requests: int = 0  # sent, for reports and tests
    _last_end: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        check_url(self.api)
        self.host = urllib.parse.urlsplit(self.api).hostname

    # --- one request -------------------------------------------------------------------

    def query(self, params: Mapping[str, str]) -> dict[str, Any]:
        """One ``action=query`` GET (format=json, formatversion=2, maxlag added), retried per
        the etiquette. Raises :class:`GateBlocked` or :class:`WikiError`."""
        full = {
            "action": "query",
            **params,
            "format": "json",
            "formatversion": "2",
            "maxlag": str(self.maxlag),
        }
        url = f"{self.api}?{urllib.parse.urlencode(full)}"
        last = "no answer"
        for attempt in range(self.retries + 1):
            try:
                resp = self._send(url)
            except OSError as exc:
                last = f"network error: {exc}"
                if attempt < self.retries:
                    self._wait(None, attempt)
                continue
            data, retry_why = self._read(resp)
            if data is not None:
                return data
            last = retry_why
            if attempt < self.retries:
                self._wait(_header(resp.headers, "retry-after"), attempt)
        raise WikiError(f"gave up after {self.retries + 1} tries: {last}")

    def _send(self, url: str) -> Response:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }
        with _SERIAL:
            if self._last_end is not None:
                gap = self.interval_s - (self.monotonic() - self._last_end)
                if gap > 0:
                    self.sleep(gap)
            try:
                self.requests += 1
                return self.transport(url, headers, TIMEOUT_S)
            finally:
                self._last_end = self.monotonic()

    def _read(self, resp: Response) -> tuple[dict[str, Any] | None, str]:
        """(the JSON, "") for an answer; (None, why) for one to retry; raises otherwise."""
        body = resp.body
        if len(body) > MAX_BODY:
            raise WikiError(f"an answer over {MAX_BODY >> 20} MiB")
        if (_header(resp.headers, "content-encoding") or "").lower() == "gzip":
            try:
                body = gzip.decompress(body)
            except (OSError, EOFError) as exc:
                raise WikiError(f"a gzip answer that doesn't unpack: {exc}") from None
        if urllib.parse.urlsplit(resp.url).hostname != self.host:
            raise GateBlocked(
                f"the API redirected to {urllib.parse.urlsplit(resp.url).hostname}"
            )
        if resp.status in (401, 403):
            raise GateBlocked(f"HTTP {resp.status}: the wiki refuses scripted access")
        low = body[:65536].lower()
        if any(mark in low for mark in CHALLENGE_MARKS) and not _is_json(body):
            raise GateBlocked(f"HTTP {resp.status}: an anti-bot challenge page")
        if resp.status == 429 or resp.status >= 500:
            return None, f"HTTP {resp.status}"
        if resp.status != 200:
            raise WikiError(f"HTTP {resp.status}")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise GateBlocked(
                "the API answered with a page that isn't JSON (a login or challenge page?)"
            ) from None
        if not isinstance(data, dict):
            raise WikiError("the API answered with JSON that isn't an object")
        error = data.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", "?"))
            if code == "maxlag":
                return None, "maxlag: the wiki's database is lagging"
            if code in ("readapidenied", "permissiondenied", "blocked"):
                raise GateBlocked(f"{code}: {str(error.get('info', ''))[:200]}")
            raise WikiError(f"{code}: {str(error.get('info', ''))[:200]}")
        return data, ""

    def _wait(self, retry_after: str | None, attempt: int) -> None:
        wait = None
        if retry_after is not None:
            try:
                wait = float(retry_after)
            except ValueError:
                wait = None
        if wait is None or wait < 0:
            wait = self.backoff_s * (2**attempt) * (1 + random.random() / 4)
        self.sleep(min(wait, self.max_wait_s))

    # --- the queries the cache uses ----------------------------------------------------

    def siteinfo(self) -> dict[str, Any]:
        """``meta=siteinfo&siprop=general|rightsinfo`` (the licence check, V14)."""
        q = self.query({"meta": "siteinfo", "siprop": "general|rightsinfo"})
        return q.get("query") or {}

    def pages(self, titles: Sequence[str]) -> list[dict[str, Any]]:
        """The newest revision (ids, timestamp, wikitext) of up to 50 ``titles``, following
        redirects; every page object of the answer, ``missing`` ones included."""
        if not titles:
            return []
        if len(titles) > MAX_TITLES:
            raise ValueError(
                f"at most {MAX_TITLES} titles per batch, not {len(titles)}"
            )
        if any("|" in t or not t.strip() for t in titles):
            raise ValueError("a title can't be empty or hold '|'")
        out: list[dict[str, Any]] = []
        params: dict[str, str] = {
            "prop": "revisions",
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
            "redirects": "1",
            "titles": "|".join(titles),
        }
        for q in self._continued(params):
            out.extend(_list(q.get("pages")))
            for r in _list(q.get("redirects")):
                out.append({"redirect_from": r.get("from"), "title": r.get("to")})
        return out

    def all_pages(
        self, cont: Mapping[str, str] | None = None
    ) -> Iterator[tuple[list[dict[str, Any]], dict[str, str] | None]]:
        """The crawl: every main-namespace page with its newest wikitext, 50 a batch
        (``generator=allpages``). Yields (pages, the continue mark after them; None at the end)."""
        params = {
            "generator": "allpages",
            "gapnamespace": "0",
            "gaplimit": str(MAX_TITLES),
            "prop": "revisions",
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
        }
        yield from self._continued_marks(params, dict(cont or {}))

    def recent_titles(self, since: str) -> tuple[list[str], str]:
        """Main-namespace titles changed (edits, new pages, log actions such as moves and
        deletions) since the ISO time ``since``, oldest first, and the wiki's time now."""
        params = {
            "list": "recentchanges",
            "rcnamespace": "0",
            "rcprop": "title|timestamp|loginfo",
            "rctype": "edit|new|log",
            "rcdir": "newer",
            "rcstart": since,
            "rclimit": "500",
            "curtimestamp": "1",
        }
        titles: list[str] = []
        now = ""
        for q, top in self._continued(params, with_top=True):
            now = now or str(top.get("curtimestamp") or "")
            for rc in _list(q.get("recentchanges")):
                for t in (
                    rc.get("title"),
                    (rc.get("logparams") or {}).get("target_title"),
                ):
                    if isinstance(t, str) and t and t not in titles:
                        titles.append(t)
        return titles, now

    def _continued(
        self, params: Mapping[str, str], with_top: bool = False
    ) -> Iterator[Any]:
        for q, _, top in self._pages_of(params, {}):
            yield (q, top) if with_top else q

    def _continued_marks(
        self, params: Mapping[str, str], cont: dict[str, str]
    ) -> Iterator[tuple[list[dict[str, Any]], dict[str, str] | None]]:
        for q, mark, _ in self._pages_of(params, cont):
            yield _list(q.get("pages")), mark

    def _pages_of(
        self, params: Mapping[str, str], cont: dict[str, str]
    ) -> Iterator[tuple[dict[str, Any], dict[str, str] | None, dict[str, Any]]]:
        while True:
            top = self.query({**params, **cont})
            q = top.get("query") or {}
            nxt = top.get("continue")
            mark = (
                {str(k): str(v) for k, v in nxt.items()}
                if isinstance(nxt, dict)
                else None
            )
            yield (q if isinstance(q, dict) else {}), mark, top
            if mark is None:
                return
            cont = mark


def _list(value: object) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _is_json(body: bytes) -> bool:
    try:
        json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return True
