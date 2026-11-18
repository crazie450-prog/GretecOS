"""WO-10.1: the guide export the core serves to the Build view (gregtec.build.guide, D304).

Uses the hand-made stand-in site (tests/fixtures/guide_site/standin.py), never GuideNH's files.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.build import guide
from gregtec.config import Config

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "guide_site"
ADMIN = "admin-" + "g" * 40


def _standin() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_wo101_standin", FIXTURES / "standin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_wo101_standin"] = module
    spec.loader.exec_module(module)
    return module


standin = _standin()


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = guide.site_root(Config(gos_home=tmp_path / "home"))
    standin.make(root)
    return root


def test_site_root_is_under_gos_home_data(tmp_path: Path) -> None:
    cfg = Config(gos_home=tmp_path)
    assert guide.site_root(cfg) == tmp_path / "data" / "guide-site"


def test_scan_finds_the_scene_page_and_counts_files(site: Path) -> None:
    found = guide.scan(site)
    assert found.present
    assert [(p.path, p.title, p.scenes) for p in found.pages] == [
        (standin.PAGE, "Stand-in multiblock", 1)
    ]
    on_disk = [p for p in site.rglob("*") if p.is_file()]
    assert found.files == len(on_disk) == 5
    assert found.bytes == sum(p.stat().st_size for p in on_disk)
    assert found.as_dict()["pages"] == [
        {"path": standin.PAGE, "title": "Stand-in multiblock", "scenes": 1}
    ]


def test_scan_of_a_missing_folder(tmp_path: Path) -> None:
    assert guide.scan(tmp_path / "nope").as_dict() == {
        "present": False,
        "pages": [],
        "files": 0,
        "bytes": 0,
    }


def test_scan_skips_pages_without_scenes_and_the_site_folders(site: Path) -> None:
    (site / "standin" / "en_us" / "index.html").write_text(
        "<!doctype html><title>Index</title><img class='game-scene' src='x.png'>",
        "utf-8",
    )  # a scene image with no data-scene-src is a static picture
    (site / "_site" / "layout.html").write_text(
        "<img class='game-scene' data-scene-src='_res/scenes/x.scene.gz'>", "utf-8"
    )
    assert [p.path for p in guide.scan(site).pages] == [standin.PAGE]


def test_scan_does_not_follow_links(site: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "page.html").write_text(
        "<img class='game-scene' data-scene-src='a'>", "utf-8"
    )
    try:
        (site / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account can't make symbolic links")
    assert [p.path for p in guide.scan(site).pages] == [standin.PAGE]


def test_media_types_are_fixed_and_gz_is_bytes() -> None:
    assert guide.media_type(Path("a/b.scene.gz")) == "application/octet-stream"
    assert guide.media_type(Path("x.JS")) == "text/javascript"
    assert guide.media_type(Path("p.html")) == "text/html; charset=utf-8"
    assert guide.media_type(Path("tool.exe")) is None
    assert guide.media_type(Path("noext")) is None


# What GuideNH 1.3.55's site can hold: its own _site files, and _res files named with their
# source resource's extension, or .bin without one (GuideSitePageAssetExporter.extensionOf).
EXPORT_SUFFIXES = (
    ".html", ".js", ".mjs", ".css", ".json", ".svg", ".png", ".webp", ".woff2", ".woff",
    ".gz", ".md", ".bin", ".jpg", ".jpeg", ".gif", ".ttf", ".otf", ".mcmeta", ".ogg", ".txt",
)  # fmt: skip


def test_every_suffix_an_export_holds_is_served() -> None:
    missing = [s for s in EXPORT_SUFFIXES if guide.media_type(Path(f"a{s}")) is None]
    assert missing == []
    assert guide.media_type(Path("_res/icons/abc.bin")) == "application/octet-stream"


def test_frame_headers_keep_the_frame_inside_guide() -> None:
    headers = guide.frame_headers("http://gos.localhost:8765")
    csp = dict(
        part.strip().split(" ", 1)
        for part in headers["Content-Security-Policy"].split(";")
    )
    here = "http://gos.localhost:8765/guide/"
    assert csp["default-src"] == "'none'"
    for directive in ("script-src", "connect-src", "font-src", "media-src"):
        assert csp[directive] == here, directive
    assert csp["img-src"] == f"{here} data:"
    assert csp["style-src"] == f"{here} 'unsafe-inline'"
    assert csp["frame-ancestors"] == "'self'" and csp["base-uri"] == "'self'"
    assert "unsafe-eval" not in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["X-Content-Type-Options"] == "nosniff"


# --- the API ------------------------------------------------------------------------------------


def _core(home: Path) -> Any:  # noqa: ANN401
    from gregtec.api.app import Core
    from gregtec.api.security import Auth
    from gregtec.db import access
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store

    store = Store(access.db_path(home))
    return Core(
        cfg=Config(gos_home=home),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
    )


def test_guide_api_and_files(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api.app import create_app

    home = tmp_path / "home"
    core = _core(home)
    admin = {"Authorization": f"Bearer {ADMIN}"}
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            assert c.get("/api/guide").status_code == 401
            assert c.get("/api/guide", headers=admin).json()["present"] is False
            assert c.get(f"/guide/{standin.PAGE}", headers=admin).status_code == 404

            standin.make(guide.site_root(core.cfg))
            listing = c.get("/api/guide", headers=admin).json()
            assert listing["present"] and listing["pages"][0]["path"] == standin.PAGE

            assert c.get(f"/guide/{standin.PAGE}").status_code == 401
            page = c.get(f"/guide/{standin.PAGE}", headers=admin)
            assert page.status_code == 200
            assert page.headers["content-type"] == "text/html; charset=utf-8"
            assert (
                "connect-src http://gos.localhost:8765/guide/;"
                in (page.headers["content-security-policy"])
            )
            assert page.headers["x-frame-options"] == "SAMEORIGIN"

            scene = next((guide.site_root(core.cfg) / "_res" / "scenes").iterdir())
            got = c.get(f"/guide/_res/scenes/{scene.name}", headers=admin)
            assert got.status_code == 200
            assert got.headers["content-type"] == "application/octet-stream"
            assert "content-encoding" not in got.headers
            assert got.content == scene.read_bytes()
            assert json.loads(gzip.decompress(got.content))["blocks"]

            res = guide.site_root(core.cfg) / "_res" / "misc"
            res.mkdir()
            for suffix in EXPORT_SUFFIXES:
                (res / f"f{suffix}").write_bytes(b"x")
                got = c.get(f"/guide/_res/misc/f{suffix}", headers=admin)
                assert got.status_code == 200, suffix

            (guide.site_root(core.cfg) / "tool.exe").write_bytes(b"MZ")
            # Real files of a served type outside the folder: only the path guard hides them.
            for above in (home, home / "data"):
                (above / "secret.json").write_text("{}", "utf-8")
            for bad in (
                "tool.exe",
                "_site/../../secret.json",  # httpx folds this to /secret.json before sending
                "_site/%2e%2e/%2e%2e/secret.json",  # these reach the route as `..` paths
                "_site/..%2f..%2fsecret.json",
                "..%2fsecret.json",
                "..%2f..%2fsecret.json",
                "%2e%2e/%2e%2e/secret.json",
                "_site/..%5c..%5csecret.json",
                "_site/nope.js",
                "_site/",
            ):
                assert c.get(f"/guide/{bad}", headers=admin).status_code == 404, bad
    finally:
        core.store.close()
