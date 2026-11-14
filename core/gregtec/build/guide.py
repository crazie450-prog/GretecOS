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

- :func:`scan`: which pages carry scenes, and the folder's file count and size.
- :func:`frame_headers`: the headers a framed guide file is sent with. Its CSP limits which
  files the page loads (scripts, styles, images, fonts, its own ``fetch``) to ``/guide/`` on the
  core's own origin and blocks inline scripts. It is **not** an isolation boundary: the frame is
  same-origin with the dashboard, so a script from the folder can call ``window.parent.fetch``
  (checked against the dashboard's ``connect-src 'self'``) and reach ``/api`` with the paired
  browser's cookie, or read the dashboard's DOM and ``localStorage``. The trust boundary today
  is the folder's contents: only Clayton's own unmodified export goes there. WO-10.8 serves
  ``/guide/`` from its own origin, with a credential that is not the dashboard cookie (D304).
- :func:`media_type`: the fixed types the files are sent as. ``.gz`` files go out as
  ``application/octet-stream`` with no ``Content-Encoding``: the viewer reads the bytes and
  gunzips them itself (``DecompressionStream``), so the browser must not.
"""

from __future__ import annotations

import os
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


def frame_headers(origin: str) -> dict[str, str]:
    """Headers for one guide file framed by the dashboard on ``origin`` (``http://host:port``,
    from a Host the security layer already accepted).

    The page may run scripts, and load styles, images, fonts, media and data, from ``/guide/``
    only; inline styles are allowed (GuideNH's pages set ``style=`` on layout boxes), inline
    scripts are not. ``<base href>`` must stay on this origin (GuideNH's layout sets one). Only
    the dashboard itself may frame it."""
    guide = f"{origin}{PREFIX}"
    csp = (
        f"default-src 'none'; script-src {guide}; style-src {guide} 'unsafe-inline'; "
        f"img-src {guide} data:; font-src {guide}; media-src {guide}; connect-src {guide}; "
        "object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'self'"
    )
    return {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-cache",
    }


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


def scan(root: Path) -> Site:
    """The scene pages under ``root`` and the folder's file count and size. Links are neither
    followed nor counted. A missing folder is ``present`` false.

    It parses every page on each call; a full pack export has hundreds to thousands, so WO-10.8
    caches the result (for example on the folder's mtime)."""
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
            files += 1
            size += path.stat().st_size
            rel = (rel_dir / name).as_posix()
            if path.suffix.lower() != ".html" or rel.split("/")[0] in _SKIP_TOP:
                continue
            parser = _Scenes()
            parser.feed(path.read_text("utf-8", errors="replace"))
            parser.close()
            if parser.scenes:
                pages.append(Page(rel, parser.title.strip(), parser.scenes))
    return Site(True, tuple(sorted(pages, key=lambda p: p.path)), files, size)
