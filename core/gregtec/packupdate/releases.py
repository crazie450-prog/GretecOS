"""The release watcher and the stock-pack check (WO-1.9, plan section 7.13, D44, D87, D146).

**Sources**, all plain HTTPS GETs with no API key (GitHub's REST API wants a token for useful
rate limits; these don't):

- GTNH's release manifests, which DreamAssemblerXXL (DAXXL, the tool that assembles the pack)
  commits for each release: ``releases/manifests/<version>.json`` (every mod and its version)
  and ``gtnh-assets.json`` (every mod's versions and file names) [A§0.1, F M1-M4].
- The modpack's GitHub releases feed (``releases.atom``), and each addon's (``data/curated/
  addons.tsv``).

**New stable releases**: the newest stable (``X.Y.Z``) newer than the adopted pack (default
:data:`gregtec.config.PACK_PIN`), seen in the modpack's feed or found by asking DAXXL for the
next patch versions' manifests (the feed lagged the manifests at 2.9.0 [A§0.1]). Only the adopted
pack's ``X.Y`` line is offered (D02: 2.9 only); a newer line is reported, not offered. A version
with a manifest gets a summary: GT and NEI versions and every mod added, removed or changed.

**Addons**: for each addon in ``addons.tsv`` that Play has (a jar in Play's ``mods/`` matches its
pattern) or that is watched ``always`` (an addon the D02 start gate waits for), the newest entry
of its releases feed, and whether its tag appears in the installed jar's name (a heuristic: the
feed doesn't say which release is a pre-release).

**Stock-pack check (D87)**: Play's ``mods/`` file names against the adopted release's manifest
and ``gtnh-assets.json``: stock jars present, missing, present in another version, and the
jars that aren't stock (Clayton's addons). Play is only listed (file names), never opened.

**Caching and back-off** (``GOS_HOME/data/cache/releases/``, state in
``GOS_HOME/data/packupdate/watch.json``): each URL is fetched at most once per
:data:`INTERVAL_S` (the watcher runs daily), with ``If-None-Match``/``If-Modified-Since`` so an
unchanged file costs a 304. A failure (no network, a 403 rate limit, a 5xx) backs that URL off,
1 h doubling up to 24 h, and the cached copy is used meanwhile. ``--force`` ignores both.

Everything fetched is data, never instructions (plan 0.1 principle 8): only tags, titles, dates
and version strings are kept, stripped of control characters and cut short.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.ingest import fingerprint

DAXXL = "https://raw.githubusercontent.com/GTNewHorizons/DreamAssemblerXXL/master"
MANIFEST_URL = DAXXL + "/releases/manifests/{version}.json"
ASSETS_URL = DAXXL + "/gtnh-assets.json"
PACK_FEED = "https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/releases.atom"
FEED_URL = "https://github.com/{repo}/releases.atom"
ADDONS = Path(__file__).resolve().parents[3] / "data" / "curated" / "addons.tsv"
CACHE = Path("data", "cache", "releases")  # in GOS_HOME
STATE = Path("data", "packupdate", "watch.json")  # in GOS_HOME
REPORTS = Path("data", "reports")
FORMAT = "gos-pack-watch/1"
USER_AGENT = "gregtec-os-pack-watcher (+https://github.com/crazie450-prog/GretecOS)"
TIMEOUT_S = 30.0
INTERVAL_S = 20 * 3600  # daily, with slack for a check that ran a little late
BACKOFF_S = 3600
BACKOFF_MAX_S = 24 * 3600
MAX_BODY = 64 << 20  # gtnh-assets.json is about 16 MB
PROBE = 3  # next patch versions asked of DAXXL
HISTORY = 30
STABLE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
CLIENT_SIDES = ("BOTH", "CLIENT")  # Play is a client; SERVER-only mods aren't in it
MOD_SUFFIXES = (".jar", ".zip")
_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class WatchError(RuntimeError):
    pass


def clean(text: object, limit: int = 120) -> str:
    """Outside text as one short line of data."""
    s = _CONTROL.sub(" ", str(text)).strip()
    return s if len(s) <= limit else s[: limit - 3] + "..."


def version_key(v: str) -> tuple[int, int, int] | None:
    m = STABLE.fullmatch(v)
    return None if m is None else (int(m[1]), int(m[2]), int(m[3]))


# --- fetching ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    status: int  # 200, 304 or 404; anything else is raised as OSError
    body: bytes = b""
    etag: str | None = None
    last_modified: str | None = None


Fetch = Callable[[str, Mapping[str, str]], Response]


def http_fetch(url: str, headers: Mapping[str, str]) -> Response:
    """One GET. 404 and 304 are answers; any other failure raises OSError."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as r:
            body = r.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise OSError(f"{url}: over {MAX_BODY >> 20} MiB")
            return Response(
                r.status, body, r.headers.get("ETag"), r.headers.get("Last-Modified")
            )
    except urllib.error.HTTPError as exc:
        if exc.code in (304, 404):
            return Response(exc.code)
        raise OSError(f"HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from None


def _utc(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def load_state(home: Path) -> dict[str, Any]:
    try:
        data = json.loads((home / STATE).read_bytes())
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        data = {"format": FORMAT}
    for key in ("sources", "last"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def save_state(home: Path, state: Mapping[str, Any]) -> None:
    fingerprint.atomic_write(home / STATE, json.dumps(state, indent=1).encode("utf-8"))


class Web:
    """Cached, backed-off GETs (module docstring). ``state`` is :func:`load_state`'s dict,
    updated in place; save it with :func:`save_state`."""

    def __init__(
        self,
        home: Path,
        state: dict[str, Any],
        fetch: Fetch = http_fetch,
        *,
        now: Callable[[], float] = time.time,
        force: bool = False,
    ) -> None:
        self.cache = home / CACHE
        self.sources: dict[str, Any] = state["sources"]
        self.fetch, self.now, self.force = fetch, now, force
        self.notes: list[str] = []

    def _body_path(self, url: str) -> Path:
        return self.cache / (
            hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".body"
        )

    def _cached(self, url: str) -> bytes | None:
        try:
            return self._body_path(url).read_bytes()
        except OSError:
            return None

    def get(self, url: str) -> bytes | None:
        """The body (fresh or cached), or None: not there (404) or never fetched."""
        s = self.sources.setdefault(url, {})
        now = self.now()
        missing = s.get("status") == 404
        if not self.force:
            if now < float(s.get("next_try", 0)):
                self.notes.append(
                    f"{url}: backing off until {_utc(float(s['next_try']))} after "
                    f"{s.get('failures')} failures ({s.get('error')}); using the copy of "
                    f"{s.get('ok_utc', 'never')}"
                )
                return None if missing else self._cached(url)
            if now - float(s.get("ok", 0)) < INTERVAL_S:
                return None if missing else self._cached(url)
        cached = None if missing else self._cached(url)
        headers: dict[str, str] = {}
        if cached is not None:
            if s.get("etag"):
                headers["If-None-Match"] = str(s["etag"])
            if s.get("last_modified"):
                headers["If-Modified-Since"] = str(s["last_modified"])
        try:
            r = self.fetch(url, headers)
        except OSError as exc:
            failures = int(s.get("failures", 0)) + 1
            wait = min(BACKOFF_S * 2 ** (failures - 1), BACKOFF_MAX_S)
            s.update(
                failures=failures,
                error=clean(exc),
                next_try=now + wait,
                failed_utc=_utc(now),
            )
            self.notes.append(
                f"{url}: {clean(exc)}; trying again after {_utc(now + wait)}"
                + ("" if cached is None else "; using the cached copy")
            )
            return cached
        s.update(
            failures=0,
            error=None,
            next_try=0,
            ok=now,
            ok_utc=_utc(now),
            status=r.status,
        )
        if r.status == 404:
            return None
        if r.status == 304 and cached is not None:
            return cached
        if (
            r.status == 304
        ):  # nothing cached to reuse: ask again unconditionally next time
            s.update(ok=0, etag=None, last_modified=None)
            return None
        s.update(etag=r.etag, last_modified=r.last_modified)
        fingerprint.atomic_write(self._body_path(url), r.body)
        return r.body

    def json(self, url: str) -> object:
        body = self.get(url)
        if body is None:
            return None
        try:
            return json.loads(body)
        except ValueError:
            self.notes.append(f"{url}: not JSON; ignored")
            return None


# --- feeds -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    tag: str
    title: str
    updated: str


def parse_atom(body: bytes) -> list[Release]:
    """A GitHub releases feed's entries, newest first, as data."""
    root = ET.fromstring(body)
    out = []
    for entry in root.iter(f"{_ATOM}entry"):
        link = entry.find(f"{_ATOM}link")
        href = "" if link is None else str(link.get("href", ""))
        tag = href.rsplit("/tag/", 1)[1] if "/tag/" in href else ""
        if not tag:
            continue
        out.append(
            Release(
                clean(urllib.parse.unquote(tag), 60),
                clean(entry.findtext(f"{_ATOM}title", "")),
                clean(entry.findtext(f"{_ATOM}updated", ""), 30),
            )
        )
    return out


def feed(web: Web, url: str) -> list[Release] | None:
    body = web.get(url)
    if body is None:
        return None
    try:
        return parse_atom(body)
    except ET.ParseError as exc:
        web.notes.append(f"{url}: not an Atom feed ({exc}); ignored")
        return None


# --- the pack --------------------------------------------------------------------------------


@dataclass
class PackCheck:
    adopted: str
    newer: list[str] = field(default_factory=list)  # stable, on the adopted X.Y line
    other_lines: list[str] = field(
        default_factory=list
    )  # newer X.Y lines: reported only
    manifests: dict[str, Any] = field(default_factory=dict)  # version -> manifest JSON
    feed_seen: bool = False


def check_pack(web: Web, adopted: str) -> PackCheck:
    base = version_key(adopted)
    if base is None:
        raise WatchError(f"the adopted pack version {adopted!r} isn't X.Y.Z")
    result = PackCheck(adopted)
    found: set[str] = set()
    releases = feed(web, PACK_FEED)
    if releases is not None:
        result.feed_seen = True
        found |= {r.tag.removeprefix("v") for r in releases}
    for z in range(base[2] + 1, base[2] + 1 + PROBE):
        v = f"{base[0]}.{base[1]}.{z}"
        m = web.json(MANIFEST_URL.format(version=v))
        if not isinstance(m, dict):
            break
        result.manifests[v] = m
        found.add(v)
    nxt = (
        f"{base[0]}.{base[1] + 1}.0"  # the next line, reported only (the feed may lag)
    )
    if isinstance(web.json(MANIFEST_URL.format(version=nxt)), dict):
        found.add(nxt)
    for v in sorted(found, key=lambda x: version_key(x) or (0, 0, 0)):
        k = version_key(v)
        if k is None or k <= base:
            continue
        (result.newer if k[:2] == base[:2] else result.other_lines).append(v)
    for v in result.newer:
        if v not in result.manifests:
            m = web.json(MANIFEST_URL.format(version=v))
            if isinstance(m, dict):
                result.manifests[v] = m
    return result


def _mods(manifest: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("github_mods", "external_mods"):
        table = manifest.get(key)
        for name, info in table.items() if isinstance(table, dict) else ():
            if isinstance(info, dict) and info.get("side") in (*CLIENT_SIDES, "SERVER"):
                out[clean(name, 80)] = clean(info.get("version"), 60)
    return out


@dataclass
class ManifestDiff:
    old: str
    new: str
    gt: tuple[str | None, str | None]
    nei: tuple[str | None, str | None]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    def lines(self, limit: int = 60) -> list[str]:
        out = [
            (
                f"GTNH {self.old} -> {self.new}: GT {self.gt[0]} -> {self.gt[1]}, "
                f"NEI {self.nei[0]} -> {self.nei[1]}; mods {len(self.added)} added, "
                f"{len(self.removed)} removed, {len(self.changed)} changed"
            )
        ]
        rows = [f"  + {m}" for m in self.added] + [f"  - {m}" for m in self.removed]
        rows += [f"  ~ {m}" for m in self.changed]
        out += rows[:limit]
        if len(rows) > limit:
            out.append(f"  ... and {len(rows) - limit} more")
        return out


def manifest_diff(
    old: str, a: Mapping[str, Any], new: str, b: Mapping[str, Any]
) -> ManifestDiff:
    ma, mb = _mods(a), _mods(b)
    d = ManifestDiff(
        old,
        new,
        (ma.get("GT5-Unofficial"), mb.get("GT5-Unofficial")),
        (ma.get("NotEnoughItems"), mb.get("NotEnoughItems")),
    )
    d.added = [f"{k} {mb[k]}" for k in sorted(mb.keys() - ma.keys())]
    d.removed = [f"{k} {ma[k]}" for k in sorted(ma.keys() - mb.keys())]
    d.changed = [
        f"{k} {ma[k]} -> {mb[k]}"
        for k in sorted(ma.keys() & mb.keys())
        if ma[k] != mb[k]
    ]
    return d


# --- Play's mods: the stock check and the addons ----------------------------------------------


def play_mod_files(play: Path) -> list[str]:
    """Play's loaded mod files (``.jar``/``.zip`` under ``mods/``, any depth), posix relpaths.
    Only names are read."""
    game = fingerprint.game_folder(play)
    mods = game / "mods"
    out: list[str] = []
    for root, dirs, files in os.walk(mods):  # os.walk doesn't follow links by default
        dirs.sort()
        rel = Path(root).relative_to(game).as_posix()
        out += [f"{rel}/{f}" for f in sorted(files) if f.lower().endswith(MOD_SUFFIXES)]
    return out


@dataclass
class StockCheck:
    version: str
    present: int = 0
    missing: list[str] = field(default_factory=list)  # "mod version (file)"
    other_version: list[str] = field(default_factory=list)
    not_stock: list[str] = field(default_factory=list)  # Play's files no stock mod has
    unknown_files: list[str] = field(
        default_factory=list
    )  # stock mods with no file name

    def lines(self, limit: int = 60) -> list[str]:
        out = [
            (
                f"Stock check against GTNH {self.version} (D87): {self.present} stock files "
                f"present, {len(self.missing)} missing, {len(self.other_version)} in another "
                f"version, {len(self.not_stock)} not stock"
            )
        ]
        for mark, rows in (
            ("missing", self.missing),
            ("other version", self.other_version),
            ("not stock", self.not_stock),
            ("no file name in gtnh-assets.json", self.unknown_files),
        ):
            out += [f"  {mark}: {r}" for r in rows[:limit]]
            if len(rows) > limit:
                out.append(f"  {mark}: ... and {len(rows) - limit} more")
        return out


def stock_check(
    version: str,
    manifest: Mapping[str, Any],
    assets: Mapping[str, Any],
    files: Sequence[str],
) -> StockCheck:
    """Play's mod files against the release's client mods (file names compared without case,
    as Windows does)."""
    known: dict[str, str] = {}  # any version's file name -> mod
    wanted: dict[str, tuple[str, str | None]] = {}  # mod -> (version, file name)
    by_name = {
        str(m.get("name")): m
        for m in (assets.get("mods") or [])
        if isinstance(m, dict) and m.get("name")
    }
    for key in ("github_mods", "external_mods"):
        table = manifest.get(key)
        for name, info in table.items() if isinstance(table, dict) else ():
            if not isinstance(info, dict) or info.get("side") not in CLIENT_SIDES:
                continue
            want = str(info.get("version"))
            filename = None
            for v in by_name.get(name, {}).get("versions") or []:
                if not isinstance(v, dict) or not v.get("filename"):
                    continue
                known[str(v["filename"]).casefold()] = name
                if v.get("version_tag") == want:
                    filename = str(v["filename"])
            wanted[name] = (want, filename)
    have = {Path(f).name.casefold(): Path(f).name for f in files}
    result = StockCheck(version)
    for name, (want, filename) in sorted(wanted.items()):
        if filename is None:
            result.unknown_files.append(f"{name} {want}")
        elif filename.casefold() in have:
            result.present += 1
        else:
            others = [have[f] for f, mod in known.items() if mod == name and f in have]
            if others:
                result.other_version.append(
                    f"{name}: {', '.join(sorted(others))} instead of {filename}"
                )
            else:
                result.missing.append(f"{name} {want} ({filename})")
    result.not_stock = sorted(n for f, n in have.items() if f not in known)
    return result


@dataclass(frozen=True)
class Addon:
    name: str
    repo: str
    patterns: tuple[str, ...]
    watch: str  # "installed" or "always"
    note: str


def read_addons(path: Path = ADDONS) -> list[Addon]:
    """``addons.tsv``: name, repo (owner/repo), jar (globs joined by |), watch, note."""
    rows = path.read_text(encoding="utf-8").splitlines()
    if not rows or rows[0].split("\t")[:4] != ["name", "repo", "jar", "watch"]:
        raise ValueError(f"{path}: the header must start name, repo, jar, watch")
    out = []
    for n, line in enumerate(rows[1:], start=2):
        if not line.strip() or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) < 4:
            raise ValueError(f"{path}:{n}: needs name, repo, jar and watch")
        name, repo, jar, watch = cells[:4]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError(f"{path}:{n}: repo must be owner/repo")
        if watch not in ("installed", "always"):
            raise ValueError(f"{path}:{n}: watch must be installed or always")
        patterns = tuple(p.strip().lower() for p in jar.split("|") if p.strip())
        out.append(
            Addon(name, repo, patterns, watch, cells[4] if len(cells) > 4 else "")
        )
    return out


@dataclass(frozen=True)
class AddonCheck:
    addon: Addon
    jars: tuple[str, ...]  # Play's matching files
    latest: Release | None
    verdict: str


def check_addons(
    web: Web, addons: Sequence[Addon], files: Sequence[str]
) -> list[AddonCheck]:
    names = [Path(f).name for f in files]
    out = []
    for a in addons:
        jars = tuple(
            n
            for n in names
            if any(fnmatch.fnmatchcase(n.lower(), p) for p in a.patterns)
        )
        if not jars and a.watch != "always":
            continue
        releases = feed(web, FEED_URL.format(repo=a.repo))
        latest = releases[0] if releases else None
        if latest is None:
            verdict = "no release feed read (see notes)"
        elif not jars:
            verdict = f"not in Play; newest release {latest.tag} ({latest.updated})"
        else:
            tag = latest.tag.lower().removeprefix("v")
            same = any(tag in j.lower() for j in jars)
            verdict = f"newest release {latest.tag} ({latest.updated}) " + (
                "matches the installed jar"
                if same
                else "isn't in the installed jar's name: a newer release? Check it "
                "in Lab before Play (D90)"
            )
        out.append(AddonCheck(a, jars, latest, verdict))
    return out


# --- the watcher -----------------------------------------------------------------------------


@dataclass
class Watch:
    checked_utc: str
    pack: PackCheck
    summaries: list[ManifestDiff]
    addons: list[AddonCheck]
    notes: list[str]

    def lines(self) -> list[str]:
        p = self.pack
        out = [f"Pack watch {self.checked_utc}: adopted GTNH {p.adopted}"]
        if p.newer:
            out.append(
                f"NEW STABLE RELEASE: GTNH {p.newer[-1]}"
                + (f" (also {', '.join(p.newer[:-1])})" if len(p.newer) > 1 else "")
                + f". Start the playbook: gos pack update --start {p.newer[-1]}"
            )
        else:
            out.append(f"No stable release newer than {p.adopted} on its line.")
        if p.other_lines:
            out.append(
                f"Newer release lines (not offered; GOS is {p.adopted[:3]}-only, D02): "
                + ", ".join(p.other_lines)
            )
        for s in self.summaries:
            out += s.lines()
        for v in p.newer:
            if v not in p.manifests:
                out.append(f"GTNH {v}: released, but DAXXL has no manifest for it yet")
        if self.addons:
            out.append("Addons:")
            for a in self.addons:
                jars = ", ".join(a.jars) or "-"
                out.append(f"  {a.addon.name} [{jars}]: {a.verdict}")
        if self.notes:
            out.append("Notes:")
            out += [f"  {n}" for n in self.notes]
        return out


def watch(
    home: Path,
    adopted: str,
    play: Path | None,
    fetch: Fetch = http_fetch,
    *,
    force: bool = False,
    now: Callable[[], float] = time.time,
    addons_path: Path = ADDONS,
) -> Watch:
    """Check once and record what was seen in ``watch.json`` (``play``: Play's instance folder,
    for its mod list; None leaves the addons out)."""
    state = load_state(home)
    web = Web(home, state, fetch, now=now, force=force)
    pack = check_pack(web, adopted)
    summaries = []
    base = web.json(MANIFEST_URL.format(version=adopted)) if pack.newer else None
    for v in pack.newer:
        if isinstance(base, dict) and v in pack.manifests:
            summaries.append(manifest_diff(adopted, base, v, pack.manifests[v]))
    notes = []
    files: list[str] = []
    if play is not None:
        try:
            files = play_mod_files(play)
        except (OSError, fingerprint.FingerprintError) as exc:
            notes.append(f"Play's mods couldn't be listed ({exc}): addons not checked")
            play = None
    checks = check_addons(web, read_addons(addons_path), files) if play else []
    result = Watch(_utc(now()), pack, summaries, checks, notes + web.notes)
    state["last"] = {
        "checked_utc": result.checked_utc,
        "adopted": adopted,
        "newer": pack.newer,
        "other_lines": pack.other_lines,
        "addons": {a.addon.name: a.latest.tag if a.latest else None for a in checks},
    }
    state["history"] = [*state["history"], state["last"]][-HISTORY:]
    save_state(home, state)
    return result


def stock(
    home: Path,
    version: str,
    play: Path,
    fetch: Fetch = http_fetch,
    *,
    force: bool = False,
    now: Callable[[], float] = time.time,
) -> tuple[StockCheck | None, list[str]]:
    """The D87 check of Play's mods against GTNH ``version``; (None, notes) when the release
    data can't be had."""
    state = load_state(home)
    web = Web(home, state, fetch, now=now, force=force)
    manifest = web.json(MANIFEST_URL.format(version=version))
    assets = web.json(ASSETS_URL) if isinstance(manifest, dict) else None
    save_state(home, state)
    if not isinstance(manifest, dict) or not isinstance(assets, dict):
        return None, [f"no release data for GTNH {version}", *web.notes]
    return stock_check(version, manifest, assets, play_mod_files(play)), web.notes
