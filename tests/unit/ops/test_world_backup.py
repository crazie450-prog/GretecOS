"""WO-11.2: scripts/verify_world_backup.py on fake world backups (D333). Nothing here is a real
world, a real instance or a real backups folder; the fake Prism tree is checked untouched."""

from __future__ import annotations

import gzip
import io
import os
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

from gregtec.ops import worldbackup

REPO = Path(__file__).resolve().parents[3]


def nbt(name: bytes = b"") -> bytes:
    """A minimal NBT compound: tag 10, its name, one int, the end tag."""
    head = bytes([10]) + struct.pack(">H", len(name)) + name
    return (
        head + bytes([3]) + struct.pack(">H", 1) + b"x" + struct.pack(">i", 7) + b"\0"
    )


def region(chunks: dict[int, tuple[int, bytes]]) -> bytes:
    """A region file: index -> (compression, payload); payload compressed here."""
    header = bytearray(2 * worldbackup.SECTOR)
    body = bytearray()
    sector = 2
    for index, (kind, payload) in sorted(chunks.items()):
        data = gzip.compress(payload) if kind == 1 else zlib.compress(payload)
        record = struct.pack(">I", len(data) + 1) + bytes([kind]) + data
        count = -(-len(record) // worldbackup.SECTOR)
        record += b"\0" * (count * worldbackup.SECTOR - len(record))
        header[index * 4 : index * 4 + 4] = struct.pack(">I", (sector << 8) | count)
        body += record
        sector += count
    return bytes(header + body)


def world_zip(
    path: Path, *, top: str = "World", files: dict[str, bytes] | None = None
) -> Path:
    entries = {
        "level.dat": gzip.compress(nbt(b"Data")),
        "region/r.0.0.mca": region({0: (2, nbt()), 5: (1, nbt())}),
        "DIM-1/region/r.-1.0.mca": region({1023: (2, nbt())}),
        "region/r.9.9.mca": b"",  # the game leaves empty region files
        "data/villages.dat": gzip.compress(nbt()),
    }
    entries.update(files or {})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(f"{top}/{name}" if top else name, data)
    return path


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    """A fake GOS_HOME (with backups/world) and a fake Prism tree, plus gos.toml."""
    home = tmp_path / "home"
    backups = home / "backups" / "world"
    backups.mkdir(parents=True)
    prism = tmp_path / "Prism"
    play = prism / "instances" / "GTNH-Play" / ".minecraft" / "saves" / "World"
    play.mkdir(parents=True)
    (play / "level.dat").write_bytes(b"play")
    cfg = home / "config" / "gos.toml"
    cfg.parent.mkdir(parents=True)
    app = tmp_path / "GregtecOS-app"
    cfg.write_text(
        "[paths]\n"
        f'gos_home = "{home.as_posix()}"\n'
        f'gos_app = "{app.as_posix()}"\n'
        f'prism_root = "{prism.as_posix()}"\n'
        f'prism_instances = "{(prism / "instances").as_posix()}"\n',
        encoding="utf-8",
    )
    return {"home": home, "backups": backups, "prism": prism, "cfg": cfg, "app": app}


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
    }


def check(tree: dict[str, Path], *args: str) -> tuple[int, str]:
    out = io.StringIO()
    before = snapshot(tree["prism"])
    code = worldbackup.main([*args, "--config", str(tree["cfg"])], out)
    assert snapshot(tree["prism"]) == before  # never writes into an instance
    return code, out.getvalue()


def test_the_newest_backup_restores_and_reads(tree: dict[str, Path]) -> None:
    old = world_zip(tree["backups"] / "old.zip", files={"level.dat": b"bad"})
    os.utime(old, (1, 1))
    world_zip(tree["backups"] / "new.zip")
    code, out = check(tree)
    assert code == 0, out
    assert "new.zip: 5 entries, 3 region files, 3 chunks read" in out
    assert "ok   the world restores and reads" in out


def test_a_world_at_the_zip_root_works_and_keep_keeps_it(
    tree: dict[str, Path], tmp_path: Path
) -> None:
    z = world_zip(tmp_path / "flat.zip", top="")
    work = tmp_path / "work"
    code, out = check(tree, "--zip", str(z), "--work", str(work), "--keep")
    assert code == 0, out
    assert (work / "world" / "region" / "r.0.0.mca").is_file()
    assert (work / "world" / "level.dat").is_file()


@pytest.mark.parametrize(
    ("files", "problem"),
    [
        ({"level.dat": b"not gzip"}, "level.dat isn't gzip"),
        (
            {"level.dat": gzip.compress(b"\x01\x00\x00")},
            "level.dat isn't an NBT compound",
        ),
        ({"region/r.0.0.mca": b"x" * 5000}, "is not a whole region file"),
        ({"region/r.0.0.mca": region({0: (3, nbt())})}, "chunk 0 has compression 3"),
        ({"region/r.0.0.mca": region({0: (2, b"\x05junk")})}, "chunk 0 isn't NBT"),
    ],
)
def test_a_damaged_world_fails(
    tree: dict[str, Path], files: dict[str, bytes], problem: str
) -> None:
    world_zip(tree["backups"] / "b.zip", files=files)
    code, out = check(tree)
    assert code == 1, out
    assert problem in out and "WORLD BACKUP CHECK FAILED" in out


def test_a_chunk_pointing_past_the_end_fails(tree: dict[str, Path]) -> None:
    data = bytearray(region({0: (2, nbt())}))
    data[4:8] = struct.pack(">I", (99 << 8) | 1)  # chunk 1 at sector 99 of 3
    world_zip(tree["backups"] / "b.zip", files={"region/r.0.0.mca": bytes(data)})
    code, out = check(tree)
    assert code == 1 and "chunk 1 points outside the file" in out


def test_a_bad_crc_fails(tree: dict[str, Path]) -> None:
    z = world_zip(tree["backups"] / "b.zip")
    raw = bytearray(z.read_bytes())
    with zipfile.ZipFile(z) as zf:
        info = zf.getinfo("World/level.dat")
    start = info.header_offset + 30 + len(info.filename) + len(info.extra)
    raw[start + 2] ^= 0xFF
    z.write_bytes(bytes(raw))
    code, out = check(tree)
    assert code == 1 and "FAIL" in out


def test_not_a_zip_and_no_world_fail(tree: dict[str, Path], tmp_path: Path) -> None:
    (tree["backups"] / "x.zip").write_bytes(b"nope")
    code, out = check(tree)
    assert code == 1 and "not a readable zip" in out
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "hi")
    code, out = check(tree, "--zip", str(empty))
    assert code == 1 and "no level.dat" in out


def test_an_entry_escaping_the_folder_fails(tree: dict[str, Path]) -> None:
    world_zip(tree["backups"] / "b.zip", files={"../../evil.txt": b"x"})
    code, out = check(tree)
    assert code == 1 and "unsafe entry" in out


def test_no_backups_fails(tree: dict[str, Path]) -> None:
    code, out = check(tree)
    assert code == 1 and "no world backup zip" in out


@pytest.mark.parametrize(
    "where",
    [
        "prism/instances/GTNH-Play/.minecraft/saves",
        "prism",
        "backups",
        "app",
        "home/vault",
        "home/data",
    ],
)
def test_work_never_goes_in_an_instance_or_the_backups(
    tree: dict[str, Path], where: str
) -> None:
    world_zip(tree["backups"] / "b.zip")
    first, rest = where.split("/", 1) if "/" in where else (where, "")
    work = tree[first] / rest / "check" if rest else tree[first] / "check"
    code, out = check(tree, "--work", str(work))
    assert code == 2 and "Refused" in out
    assert not work.exists()


def test_the_script_runs_from_the_repo(tree: dict[str, Path]) -> None:
    world_zip(tree["backups"] / "b.zip")
    done = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "verify_world_backup.py"),
            "--config",
            str(tree["cfg"]),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok   the world restores and reads" in done.stdout


def test_too_little_free_space_fails_before_extracting(
    tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    world_zip(tree["backups"] / "b.zip")
    real = worldbackup.shutil.disk_usage
    monkeypatch.setattr(
        worldbackup.shutil,
        "disk_usage",
        lambda p: real(p)._replace(free=1000),
    )
    code, out = check(tree)
    assert code == 1
    assert "FAIL only 0.0 GiB free" in out and "WORLD BACKUP CHECK FAILED" in out


def test_a_backup_that_cant_be_read_fails_without_a_traceback(
    tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    world_zip(tree["backups"] / "b.zip")

    def locked(src: Path, dest: Path, tries: int = 3) -> None:
        raise PermissionError(13, "locked", str(src))

    monkeypatch.setattr(worldbackup, "copy_in", locked)
    code, out = check(tree)
    assert code == 1
    assert "FAIL can't read or copy b.zip" in out
