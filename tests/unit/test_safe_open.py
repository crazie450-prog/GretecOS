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
