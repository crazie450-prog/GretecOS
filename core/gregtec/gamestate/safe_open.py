"""Read game files without getting in the game's way (plan section 5.5, PM13, CLAUDE.md).

On Windows a plain ``open()`` doesn't allow delete sharing, so while GOS holds a file the game's
save-by-rename (``level.dat_new`` -> ``level.dat``) fails. :func:`safe_open` opens read-only with
read, write *and* delete sharing. Open, read and close straight away; never hold a handle.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import time
from collections.abc import Callable, Iterator
from typing import BinaryIO

RETRIES = 3
BACKOFF_S = 0.2

StrPath = str | os.PathLike[str]


@contextlib.contextmanager
def safe_open(path: StrPath) -> Iterator[BinaryIO]:
    """Open ``path`` for reading in binary mode, sharing read, write and delete (Windows)."""
    if sys.platform != "win32":
        with open(path, "rb") as f:
            yield f
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
    share_all = 0x1 | 0x2 | 0x4
    open_existing = 3
    file_attribute_normal = 0x80
    invalid_handle = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path),
        generic_read,
        share_all,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle is None or handle == invalid_handle:
        # Maps to FileNotFoundError, PermissionError, ... like open() does.
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise
    with os.fdopen(fd, "rb") as f:
        yield f


def read_bytes(
    path: StrPath, validate: Callable[[bytes], object] | None = None
) -> bytes:
    """Read a whole file with :func:`safe_open`, retrying up to 3 times with backoff.

    ``validate`` may raise ``ValueError`` (a partial or corrupt read); that is retried too.
    A missing file is not retried.
    """
    error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with safe_open(path) as f:
                data = f.read()
            if validate is not None:
                validate(data)
            return data
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as exc:
            error = exc
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_S * 2**attempt)
    assert error is not None
    raise error


def read_text(path: StrPath, encoding: str = "utf-8") -> str:
    """:func:`read_bytes`, decoded; undecodable bytes become U+FFFD."""
    return read_bytes(path).decode(encoding, errors="replace")


def sha256_file(path: StrPath, chunk: int = 1 << 20) -> str:
    """Hex SHA-256 of a file, streamed through :func:`safe_open` (for mod jars)."""
    digest = hashlib.sha256()
    with safe_open(path) as f:
        while block := f.read(chunk):
            digest.update(block)
    return digest.hexdigest()
