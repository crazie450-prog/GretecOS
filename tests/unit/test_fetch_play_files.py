"""WO-0.7: scripts/fetch_play_files.ps1 against a local HTTP server (Windows only; touches
nothing else), plus a check of its real pins that runs everywhere."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_play_files.ps1"
windows = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows PowerShell script"
)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """A folder served over HTTP on 127.0.0.1, and its base URL."""
    root = tmp_path / "srv"
    root.mkdir()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(root))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def serve(
    server: tuple[Path, str], name: str, body: bytes, sha256: str | None = None
) -> dict[str, str]:
    """Serve ``body`` as ``name``; the manifest entry pins ``sha256`` (default: the real hash)."""
    root, base = server
    (root / name).write_bytes(body)
    return {
        "name": name,
        "url": f"{base}/{name}",
        "sha256": sha256 or hashlib.sha256(body).hexdigest(),
    }


def manifest(path: Path, entries: list[dict[str, str]]) -> Path:
    path.write_text(json.dumps(entries), encoding="ascii")
    return path


def run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        # CI runs pytest from PowerShell 7; its PSModulePath breaks Windows PowerShell 5.1's modules.
        env={k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"} | env,
        timeout=120,
    )


def files_outside(root: Path, staging: Path) -> dict[str, tuple[int, int]]:
    """Every file under ``root`` but not under ``staging``: size and mtime."""
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and staging not in p.parents
    }


@windows
def test_keeps_good_files_and_deletes_a_mismatch(
    tmp_path: Path, server: tuple[Path, str]
) -> None:
    entries = [
        serve(server, "pack.zip", b"zip" * 1000),
        serve(server, "good.jar", b"good jar"),
        serve(server, "bad.jar", b"tampered", sha256=hashlib.sha256(b"x").hexdigest()),
    ]
    m = manifest(tmp_path / "manifest.json", entries)
    staging = tmp_path / "staging"
    before = files_outside(tmp_path, staging)

    result = run("-Manifest", str(m), "-Dest", str(staging))
    assert result.returncode == 1, result.stdout + result.stderr
    assert sorted(p.name for p in staging.iterdir()) == ["good.jar", "pack.zip"]
    assert (staging / "pack.zip").read_bytes() == b"zip" * 1000
    assert "FAILED    bad.jar: SHA256 " in result.stdout
    assert files_outside(tmp_path, staging) == before  # nothing written outside staging


@windows
def test_second_run_skips_and_a_stale_file_is_replaced(
    tmp_path: Path, server: tuple[Path, str]
) -> None:
    entries = [serve(server, "pack.zip", b"zip"), serve(server, "a.jar", b"jar")]
    m = manifest(tmp_path / "manifest.json", entries)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "a.jar").write_bytes(b"an old download")  # wrong hash: replaced

    first = run("-Manifest", str(m), "-Dest", str(staging))
    assert first.returncode == 0, first.stdout + first.stderr
    assert "deleted   a.jar: wrong SHA256" in first.stdout
    assert (staging / "a.jar").read_bytes() == b"jar"
    assert "Add Instance > Import" in first.stdout
    kept = {p.name: p.stat().st_mtime_ns for p in staging.iterdir()}

    for name in ("pack.zip", "a.jar"):  # a download would now fail
        (server[0] / name).unlink()
    second = run("-Manifest", str(m), "-Dest", str(staging))
    assert second.returncode == 0, second.stdout + second.stderr
    assert second.stdout.count("(already here)") == 2
    assert {p.name: p.stat().st_mtime_ns for p in staging.iterdir()} == kept


@windows
def test_the_next_steps_keep_the_lab_first_jars_out_of_play(
    tmp_path: Path, server: tuple[Path, str]
) -> None:
    """D365: the fresh-build steps list GTNL and GTNLLib for the Lab first, never under the
    'copy into Play's mods' list."""
    names = ["pack.zip", "a.jar", "sciencenotleisure-0.2.7.jar", "gtnllib-1.1.6.jar"]
    m = manifest(
        tmp_path / "manifest.json", [serve(server, n, n.encode()) for n in names]
    )
    staging = tmp_path / "staging"
    result = run("-Manifest", str(m), "-Dest", str(staging))
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    into_play = lines.index(
        "  then copy into C:\\Games\\PrismLauncher\\instances\\GTNH-Play\\.minecraft\\mods\\ :"
    )
    no_world = lines.index(
        "  then launch to the main menu and quit. Do not create a world."
    )
    lab = next(
        i for i, ln in enumerate(lines) if ln.startswith("  NOT into Play yet (D365)")
    )
    play_list = [ln.strip() for ln in lines[into_play + 1 : no_world]]
    assert play_list == [str(staging / "a.jar")]
    assert no_world < lab
    assert [ln.strip() for ln in lines[lab + 3 :]] == [
        str(staging / "sciencenotleisure-0.2.7.jar"),
        str(staging / "gtnllib-1.1.6.jar"),
    ]


@windows
def test_the_next_steps_keep_a_replacing_jar_out_of_play_and_name_the_old_one(
    tmp_path: Path, server: tuple[Path, str]
) -> None:
    """D373: an update that replaces a jar Play has is listed for the Lab first, with the jar
    it replaces; Play's list keeps the old jar."""
    names = [
        "pack.zip",
        "TwistSpaceTechnology-0.8.0.jar",
        "plannh-0.2.0.jar",
        "gtnllib-1.1.6.jar",
        "TwistSpaceTechnology-0.8.2.jar",
        "plannh-0.2.4.jar",
    ]
    m = manifest(
        tmp_path / "manifest.json", [serve(server, n, n.encode()) for n in names]
    )
    staging = tmp_path / "staging"
    result = run("-Manifest", str(m), "-Dest", str(staging))
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    into_play = lines.index(
        "  then copy into C:\\Games\\PrismLauncher\\instances\\GTNH-Play\\.minecraft\\mods\\ :"
    )
    no_world = lines.index(
        "  then launch to the main menu and quit. Do not create a world."
    )
    lab = next(
        i for i, ln in enumerate(lines) if ln.startswith("  NOT into Play yet (D365)")
    )
    swap = next(
        i for i, ln in enumerate(lines) if ln.startswith("  NOT into Play yet (D373)")
    )
    assert [ln.strip() for ln in lines[into_play + 1 : no_world]] == [
        str(staging / "TwistSpaceTechnology-0.8.0.jar"),
        str(staging / "plannh-0.2.0.jar"),
    ]
    assert no_world < lab < swap
    assert [ln.strip() for ln in lines[lab + 3 : swap]] == [
        str(staging / "gtnllib-1.1.6.jar")
    ]
    assert "[sync.lab] exclude" in " ".join(lines[swap : swap + 4])
    assert [ln.strip() for ln in lines[swap + 4 :]] == [
        f"{staging / 'TwistSpaceTechnology-0.8.2.jar'}  (replaces TwistSpaceTechnology-0.8.0.jar)",
        f"{staging / 'plannh-0.2.4.jar'}  (replaces plannh-0.2.0.jar)",
    ]


def test_the_lab_first_patterns_are_the_gtnl_pins() -> None:
    """The printed steps hold back exactly the jars D365 pins for the Lab first."""
    from fnmatch import fnmatchcase

    text = SCRIPT.read_text(encoding="ascii")
    patterns = re.findall(
        r"'([^']+)'", re.search(r"\$labFirst = @\(([^)]*)\)", text)[1]
    )
    names = re.findall(r"name = '([^']+\.jar)'", text)
    held = [n for n in names if any(fnmatchcase(n, p) for p in patterns)]
    assert held == ["sciencenotleisure-0.2.7.jar", "gtnllib-1.1.6.jar"]
    assert "foreach ($f in $jars)" in text and "foreach ($f in $lab)" in text


def test_the_replacing_jars_are_pinned_beside_the_jars_they_replace() -> None:
    """D373: every Lab-first update names the jar it replaces, both are pinned (the old one is
    Play's and the rollback), and the printed steps list them apart from Play's."""
    text = SCRIPT.read_text(encoding="ascii")
    table = re.search(r"\$replaces = @\{([^}]*)\}", text)[1]
    replaces = dict(re.findall(r"'([^']+\.jar)' = '([^']+\.jar)'", table))
    assert replaces == {
        "TwistSpaceTechnology-0.8.2.jar": "TwistSpaceTechnology-0.8.0.jar",
        "plannh-0.2.4.jar": "plannh-0.2.0.jar",
    }
    names = re.findall(r"name = '([^']+\.jar)'", text)
    assert set(replaces) | set(replaces.values()) <= set(names)
    for new, old in replaces.items():  # the same mod, another version
        assert new.split("-")[0] == old.split("-")[0] and new != old
    assert "foreach ($f in $swap)" in text
    assert "-and -not $replaces.ContainsKey($_.name)" in text  # kept out of Play's list


@windows
def test_default_staging_folder_is_in_the_profile(
    tmp_path: Path, server: tuple[Path, str]
) -> None:
    m = manifest(tmp_path / "manifest.json", [serve(server, "a.jar", b"jar")])
    home = tmp_path / "home"
    result = run("-Manifest", str(m), USERPROFILE=str(home))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / "Downloads" / "GTNH-2.9.0" / "a.jar").read_bytes() == b"jar"


@windows
@pytest.mark.parametrize(
    "dest",
    ["PrismLauncher/dl", "inst/new", ".minecraft/mods", "save/new"],
    ids=["prism", "instance", "game", "world"],
)
def test_refuses_prism_instance_and_game_folders(
    tmp_path: Path, server: tuple[Path, str], dest: str
) -> None:
    (tmp_path / "inst").mkdir()
    (tmp_path / "inst" / "instance.cfg").write_text("name=GTNH-Play\n")
    (tmp_path / "save").mkdir()
    (tmp_path / "save" / "level.dat").write_bytes(b"\x0a")
    m = manifest(tmp_path / "manifest.json", [serve(server, "a.jar", b"jar")])
    before = files_outside(tmp_path, tmp_path / "unused")

    result = run("-Manifest", str(m), "-Dest", str(tmp_path / dest))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Refusing to write under" in result.stdout
    assert not (tmp_path / dest).exists()
    assert files_outside(tmp_path, tmp_path / "unused") == before


@windows
def test_refuses_a_name_that_is_a_path(
    tmp_path: Path, server: tuple[Path, str]
) -> None:
    entry = serve(server, "a.jar", b"jar") | {"name": "..\\a.jar"}
    m = manifest(tmp_path / "manifest.json", [entry])
    staging = tmp_path / "staging"
    before = files_outside(tmp_path, staging)

    result = run("-Manifest", str(m), "-Dest", str(staging))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Not a plain file name" in result.stdout
    assert not staging.exists()
    assert files_outside(tmp_path, staging) == before


def test_real_pins() -> None:
    """The pinned files: both zips, the addons cleared for Play (D88, D90), GTNL 0.2.7 with the
    GTNLLib it is built against, for the Lab test first (D365), and the two updates for the Lab
    test first (D373); never GTNL's rolling dev-build."""
    text = SCRIPT.read_text(encoding="ascii")
    pins = re.findall(
        r"name = '([^']+)'.*?url = '([^']+)'\s+sha256 = '([0-9a-f]{64})'",
        text,
        flags=re.DOTALL,
    )
    assert [p[0] for p in pins] == [
        "GT_New_Horizons_2.9.0_Java_17-26.zip",
        "GT_New_Horizons_2.9.0_Java_8.zip",
        "TwistSpaceTechnology-0.8.0.jar",
        "programmablehatches-0.2.1p0.jar",
        "worldedit-v0.0.10.jar",
        "plannh-0.2.0.jar",  # Play's PlanNH: two in mods/ is a duplicate-mod crash
        "OTHTechnology-2.2.1.1.jar",
        "sciencenotleisure-0.2.7.jar",  # the main jar, not -dev or -sources
        "gtnllib-1.1.6.jar",  # the GTNLLib that GTNL 0.2.7 builds against
        "TwistSpaceTechnology-0.8.2.jar",  # Lab first; replaces 0.8.0 (D373)
        "plannh-0.2.4.jar",  # Lab first; replaces 0.2.0 (D373)
    ]
    # Play's own list (what isn't held back for the Lab) has one jar per mod
    held = (
        "sciencenotleisure-",
        "gtnllib-",
        "TwistSpaceTechnology-0.8.2",
        "plannh-0.2.4",
    )
    for_play = [
        p[0] for p in pins if p[0].endswith(".jar") and not p[0].startswith(held)
    ]
    assert len({n.split("-")[0].lower() for n in for_play}) == len(for_play) == 5
    for name, url, _ in pins:
        assert url.startswith("https://") and url.endswith("/" + name)
    assert len({p[2] for p in pins}) == len(pins)
    assert "dev-build" not in "".join(p[1] for p in pins)
    gtnl = [p[1] for p in pins if "GTNotLeisure/" in p[1]]
    assert all("/releases/download/" in u for u in gtnl) and len(gtnl) == 2
