"""Shared NTFS access probes for the isolation tests (Windows only; ctypes, no pywin32).

The probes ask Windows for an access right and close the handle straight away, so they never
change a file. A new file is created with delete-on-close, so it is gone when the handle closes.
"""

from __future__ import annotations

import ctypes
import secrets
from pathlib import Path

import pytest

FILE_READ_DATA = 0x0001  # for a folder: list it
FILE_WRITE_DATA = 0x0002  # for a folder: add files to it
DELETE = 0x0001_0000  # rename or delete
WRITE_DAC = 0x0004_0000  # change permissions
SHARE_ALL = 0x7
CREATE_NEW = 1
OPEN_EXISTING = 3
BACKUP_SEMANTICS = 0x0200_0000  # needed to open a folder
DELETE_ON_CLOSE = 0x0400_0000
ACCESS_DENIED = 5


class Probe:
    FILE_READ_DATA = FILE_READ_DATA
    FILE_WRITE_DATA = FILE_WRITE_DATA
    DELETE = DELETE
    WRITE_DAC = WRITE_DAC

    def _open(self, path: Path, access: int, disposition: int, flags: int) -> bool:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create.restype = wintypes.HANDLE
        handle = create(str(path), access, SHARE_ALL, None, disposition, flags, None)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error == ACCESS_DENIED:
                return False
            raise ctypes.WinError(error)
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        return True

    def can(self, path: Path, access: int, *, folder: bool = False) -> bool:
        """Would Windows grant ``access`` on an existing file (or folder)?"""
        return self._open(
            path, access, OPEN_EXISTING, BACKUP_SEMANTICS if folder else 0
        )

    def can_create(self, folder: Path) -> bool:
        """Can a new file be made in ``folder``? (It is deleted again when the handle closes.)"""
        probe = folder / f".gos-probe-{secrets.token_hex(4)}"
        return self._open(probe, FILE_WRITE_DATA | DELETE, CREATE_NEW, DELETE_ON_CLOSE)


@pytest.fixture
def probe() -> Probe:
    return Probe()
