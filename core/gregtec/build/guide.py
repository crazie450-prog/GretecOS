"""The 3D build guide's export, as the core serves it (WO-10.1 spike, plan 7.10, D35, D304).

GuideNH's ``/guidenhc exportsite`` (GuideNH 1.3.55, in GTNH 2.9.0) writes a static site: one
HTML page per guide page (``<guide>/<lang>/<page>.html``), its shared files in ``_site/`` (the
three.js model viewer, styles, fonts) and content-addressed files in ``_res/<bucket>/<sha256>.*``
(``scenes/*.scene.gz``: each scene state as GuideME's FlatBuffers ``ExpScene``, gzip-compressed;
``placeholders/*.png``; hover and tooltip files). A page shows a scene as an
``<img class="game-scene" data-scene-src=...>`` that the viewer hydrates (WO-10.1 report).

GOS never writes that site and never vendors its viewer (LGPL-3.0, D304): Clayton exports it in a
Lab client and copies the folder to ``GOS_HOME/data/guide-site/`` (:func:`site_root`); the core
serves the folder as it is under ``/guide/`` and the dashboard's Build view frames one page.

What this module does:

- :func:`scan`: which pages carry scenes, and the folder's file count and size;
  :class:`ScanCache` keeps the result and re-parses only pages whose size or time changed
  (a full pack export has thousands of pages, WO-10.8, D304).
- **Its own origin** (WO-10.8, D304, D319): the files are served only on the guide host
  (``guide.<dashboard_host>:<port>``, :attr:`gregtec.config.Config.guide_host`), never on the
  dashboard's, so the framed page is cross-origin with the dashboard: its scripts can't reach the
  parent window, the dashboard's ``localStorage`` or its DOM, and the core refuses any ``/api``
  request carrying the guide's Origin. The dashboard's cookie is host-only and never goes there;
  the credential is a short-lived signed path token (:class:`Tokens`) the paired dashboard gets
  from ``GET /api/guide``: ``/guide/<token>/<path>``, so the export's relative links stay under it.
- :func:`frame_headers`: the headers a framed guide file is sent with. Its CSP limits which
  files the page loads (scripts, styles, images, fonts, its own ``fetch``) to ``/guide/`` on the
  guide origin and blocks inline scripts, and only the dashboard may frame it.
- :func:`media_type`: the fixed types the files are sent as. ``.gz`` files go out as
  ``application/octet-stream`` with no ``Content-Encoding``: the viewer reads the bytes and
  gunzips them itself (``DecompressionStream``), so the browser must not.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from gregtec.config import Config

SITE_DIR = "guide-site"  # under GOS_HOME/data
PREFIX = "/guide/"
# Fixed here, never from mimetypes (the Windows registry), as the dashboard's own files are.
# GuideNH names a shared ``_res`` file with its source resource's extension, or ``.bin`` when it
# has none (``GuideSitePageAssetExporter.extensionOf``), so the set covers what a pack's
# resources carry, not only what the stand-in has.
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".mcmeta": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".gz": "application/octet-stream",
    ".bin": "application/octet-stream",
    ".ogg": "audio/ogg",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}
# Pages GuideNH's site has that are not guide pages.
_SKIP_TOP = ("_site", "_res")


def site_root(cfg: Config) -> Path:
    """Where Clayton's copied ``exportsite`` folder lives: ``GOS_HOME/data/guide-site``."""
    return cfg.data_dir / SITE_DIR


def media_type(path: Path) -> str | None:
    """The type ``path`` is served as, or None for a type the guide route doesn't serve."""
    return TYPES.get(path.suffix.lower())


def frame_headers(guide_origin: str, dashboard_origin: str) -> dict[str, str]:
    """Headers for one guide file served on ``guide_origin`` (``http://guide.<host>:<port>``)
    and framed by the dashboard on ``dashboard_origin``.

    The page may run scripts, and load styles, images, fonts, media and data, from ``/guide/``
    on its own origin only; inline styles are allowed (GuideNH's pages set ``style=`` on layout
    boxes), inline scripts are not. ``<base href>`` must stay on this origin (GuideNH's layout
    sets one). Only the dashboard may frame it (``frame-ancestors``; no ``X-Frame-Options``,
    whose ``SAMEORIGIN`` would refuse the dashboard, now another origin)."""
    guide = f"{guide_origin}{PREFIX}"
    csp = (
        f"default-src 'none'; script-src {guide}; style-src {guide} 'unsafe-inline'; "
        f"img-src {guide} data:; font-src {guide}; media-src {guide}; connect-src {guide}; "
        "object-src 'none'; base-uri 'self'; form-action 'none'; "
        f"frame-ancestors {dashboard_origin}"
    )
    return {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cache-Control": "no-cache",
    }


TOKEN_TTL_S = (
    6 * 3600
)  # a play session (D319): the Build view asks again before it runs out
_TOKEN = re.compile(r"(?P<exp>[0-9]{10})-(?P<sig>[0-9a-f]{32})")


class Tokens:
    """Short-lived signed path tokens for the guide host (D304, D319): ``<expiry>-<hmac>``,
    HMAC-SHA256 over the expiry with a key made when the core starts and held only in memory
    (a restart ends every token; the dashboard asks for a new one). Not the dashboard cookie, not
    a device or client token: it opens ``/guide/`` files and nothing else."""

    def __init__(
        self, key: bytes | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self._key = key if key is not None else secrets.token_bytes(32)
        self._clock = clock

    def _sig(self, exp: int) -> str:
        mac = hmac.new(self._key, f"guide|{exp}".encode(), hashlib.sha256)
        return mac.hexdigest()[:32]

    def issue(self) -> tuple[str, int]:
        """(a new token, its expiry as Unix seconds)."""
        exp = int(self._clock()) + TOKEN_TTL_S
        return f"{exp}-{self._sig(exp)}", exp

    def valid(self, token: str) -> bool:
        m = _TOKEN.fullmatch(token)
        if m is None:
            return False
        exp = int(m["exp"])
        return exp > self._clock() and hmac.compare_digest(m["sig"], self._sig(exp))


class _Scenes(HTMLParser):
    """Counts the GuideNH scenes on a page: ``<img class="game-scene" data-scene-src=...>``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scenes = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return
        found = dict(attrs)
        classes = (found.get("class") or "").split()
        if tag == "img" and "game-scene" in classes and found.get("data-scene-src"):
            self.scenes += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


@dataclass(frozen=True)
class Page:
    path: str  # relative to the site root, with forward slashes
    title: str
    scenes: int


@dataclass(frozen=True)
class Site:
    present: bool
    pages: tuple[Page, ...]  # only pages with at least one scene, sorted by path
    files: int
    bytes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "present": self.present,
            "pages": [
                {"path": p.path, "title": p.title, "scenes": p.scenes}
                for p in self.pages
            ],
            "files": self.files,
            "bytes": self.bytes,
        }


def _parse(path: Path) -> tuple[str, int]:
    parser = _Scenes()
    parser.feed(path.read_text("utf-8", errors="replace"))
    parser.close()
    return parser.title.strip(), parser.scenes


type _Seen = tuple[int, int]  # (size, mtime_ns)


def _scan(root: Path, parse: Callable[[Path, str, _Seen], tuple[str, int]]) -> Site:
    if not root.is_dir():
        return Site(False, (), 0, 0)
    pages: list[Page] = []
    files = size = 0
    for here, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        rel_dir = Path(here).relative_to(root)
        for name in sorted(names):
            path = Path(here) / name
            if path.is_symlink() or not path.is_file():
                continue
            st = path.stat()
            files += 1
            size += st.st_size
            rel = (rel_dir / name).as_posix()
            if path.suffix.lower() != ".html" or rel.split("/")[0] in _SKIP_TOP:
                continue
            title, scenes = parse(path, rel, (st.st_size, st.st_mtime_ns))
            if scenes:
                pages.append(Page(rel, title, scenes))
    return Site(True, tuple(sorted(pages, key=lambda p: p.path)), files, size)


def scan(root: Path) -> Site:
    """The scene pages under ``root`` and the folder's file count and size. Links are neither
    followed nor counted. A missing folder is ``present`` false. Parses every page: the core
    uses :class:`ScanCache`."""
    return _scan(root, lambda path, _rel, _seen: _parse(path))


class ScanCache:
    """:func:`scan` that parses a page again only when its size or modification time changed
    (or it is new); pages that went away are dropped. Each call still lists the folder and stats
    its files (cheap next to parsing thousands of pages: D304's "caches the page scan"), so a
    replaced export shows at once. Thread-safe: the route runs it in a worker thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._root: Path | None = None
        self._pages: dict[str, tuple[_Seen, tuple[str, int]]] = {}
        self.parsed = 0  # pages parsed so far (the tests read it)

    def scan(self, root: Path) -> Site:
        with self._lock:
            if root != self._root:
                self._root, self._pages = root, {}
            fresh: dict[str, tuple[_Seen, tuple[str, int]]] = {}

            def parse(path: Path, rel: str, seen: _Seen) -> tuple[str, int]:
                old = self._pages.get(rel)
                if old is not None and old[0] == seen:
                    fresh[rel] = old
                    return old[1]
                got = _parse(path)
                self.parsed += 1
                fresh[rel] = (seen, got)
                return got

            site = _scan(root, parse)
            self._pages = fresh
            return site
