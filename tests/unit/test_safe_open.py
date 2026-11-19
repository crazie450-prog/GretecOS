from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from gregtec.gamestate import safe_open as so


def test_reads_bytes_text_and_hash(tmp_path: Path) -> None:
    f = tmp_path / "level.dat"
    f.write_bytes(b"caf\xc3\xa9 \xff")
    assert so.read_bytes(f) == b"caf\xc3\xa9 \xff"
    assert so.read_text(f) == "caf\u00e9 \ufffd"
    assert so.sha256_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_limit_caps_the_read(tmp_path: Path) -> None:
    f = tmp_path / "gt_recipes.jsonl"
    f.write_bytes(b"0123456789")
    assert so.read_bytes(f, limit=4) == b"0123"
    assert so.read_bytes(f, limit=11) == b"0123456789"


def test_missing_file_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(so.time, "sleep", sleeps.append)
    with pytest.raises(FileNotFoundError):
        so.read_bytes(tmp_path / "missing.json")
    assert sleeps == []


def test_invalid_read_is_retried_then_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(so.time, "sleep", sleeps.append)
    f = tmp_path / "QuestProgress.json"
    f.write_text("{partial")
    calls: list[bytes] = []

    def validate(data: bytes) -> None:
        calls.append(data)
        raise ValueError("partial write")

    with pytest.raises(ValueError, match="partial write"):
        so.read_bytes(f, validate=validate)
    assert len(calls) == so.RETRIES
    assert sleeps == [so.BACKOFF_S, so.BACKOFF_S * 2]


def test_retry_succeeds_once_the_file_is_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(so.time, "sleep", lambda _s: f.write_text('{"ok": 1}'))
    f = tmp_path / "x.json"
    f.write_text("{")

    def validate(data: bytes) -> None:
        if not data.endswith(b"}"):
            raise ValueError("truncated")

    assert so.read_bytes(f, validate=validate) == b'{"ok": 1}'


def test_hash_is_retried_while_a_file_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(so.time, "sleep", sleeps.append)
    f = tmp_path / "mod.jar"
    f.write_bytes(b"jar")
    real, calls = so.safe_open, [0]

    def held_once(path: so.StrPath) -> object:
        calls[0] += 1
        if calls[0] == 1:
            raise PermissionError(13, "held by antivirus")
        return real(path)

    monkeypatch.setattr(so, "safe_open", held_once)
    assert so.sha256_file(f) == hashlib.sha256(b"jar").hexdigest()
    assert sleeps == [so.BACKOFF_S]
    with pytest.raises(FileNotFoundError):
        so.sha256_file(tmp_path / "missing.jar")
    assert sleeps == [so.BACKOFF_S]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing semantics")
def test_game_can_save_by_rename_while_we_hold_the_file(tmp_path: Path) -> None:
    level = tmp_path / "level.dat"
    level.write_bytes(b"old")
    new = tmp_path / "level.dat_new"
    new.write_bytes(b"new")
    with so.safe_open(level) as f:
        assert f.read() == b"old"
        os.replace(level, tmp_path / "level.dat_old")  # what 1.7.10 does on save
        os.replace(new, level)
    assert level.read_bytes() == b"new"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing semantics")
def test_plain_open_would_block_the_save(tmp_path: Path) -> None:
    level = tmp_path / "level.dat"
    level.write_bytes(b"old")
    with open(level, "rb"), pytest.raises(PermissionError):
        os.replace(level, tmp_path / "level.dat_old")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing semantics")
def test_replace_onto_the_held_file_goes_on_and_the_read_is_dropped(
    tmp_path: Path,
) -> None:
    # GTNH 2.9.0 (Hodgepodge, BetterQuesting): temp file, then Files.move(ATOMIC_MOVE,
    # REPLACE_EXISTING) onto the file. os.replace is the same MoveFileExW(MOVEFILE_REPLACE_EXISTING)
    # call. Before WO-6.8 Windows refused it while safe_open held the file (WinError 5, the G11
    # finding); now the replace breaks safe_open's oplock, the handle is closed and the replace
    # goes on (D357), and what safe_open read is marked interrupted, so readers retry.
    level = tmp_path / "level.dat"
    level.write_bytes(b"old")
    tmp = tmp_path / ".level.dat-1.tmp"
    tmp.write_bytes(b"new")
    with so.safe_open(level) as f:
        assert f.read() == b"old"
        os.replace(tmp, level)
        assert so.interrupted(f)
        with pytest.raises(so.ReadInterrupted):
            so.check_whole(f)
    assert level.read_bytes() == b"new"
