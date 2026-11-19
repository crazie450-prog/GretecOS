"""Win32 file handles for :mod:`gregtec.gamestate.safe_open` (Windows only; D357).

``NtCreateFile`` (read, sharing read, write and delete, asynchronous) with
``FILE_OPEN_REQUIRING_OPLOCK``, then at once ``FSCTL_REQUEST_OPLOCK`` for a read-handle oplock
(``OPLOCK_LEVEL_CACHE_READ | OPLOCK_LEVEL_CACHE_HANDLE``). The flag makes the open and the
oplock one step: until the request is made, a call of the game's that would break the oplock
waits instead of being refused, so the handle is never open without the oplock (D357). The
request stays pending while the oplock holds; its completion (an event) is the break. Reads are
overlapped ``ReadFile`` calls at an offset. Closing the handle is what acknowledges a break.
Where the volume takes neither the flag nor the oplock, the handle is a plain ``CreateFileW``
one (sharing all three) and :attr:`Win32Handle.oplock_error` says why (``safe_open`` logs it).
Re-implemented from Microsoft's documentation of the calls; nothing vendored.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

GENERIC_READ = 0x80000000
SHARE_ALL = 0x1 | 0x2 | 0x4  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_FLAG_OVERLAPPED = 0x40000000
FSCTL_REQUEST_OPLOCK = 0x00090240
OPLOCK_LEVEL_CACHE_READ = 0x1
OPLOCK_LEVEL_CACHE_HANDLE = 0x2
REQUEST_OPLOCK_INPUT_FLAG_REQUEST = 0x1
REQUEST_OPLOCK_CURRENT_VERSION = 1
ERROR_HANDLE_EOF = 38
ERROR_IO_PENDING = 997
ERROR_OPLOCK_COMPLETED = -1  # ours: the request completed at once, so no oplock is held
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF
DISPOSE_WAIT_MS = 5000

INVALID_HANDLE = ctypes.c_void_p(-1).value

# NtCreateFile (ntifs.h)
FILE_GENERIC_READ = 0x00120089  # what GENERIC_READ maps to for a file
FILE_OPEN = 1
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_OPEN_REQUIRING_OPLOCK = 0x00010000
OBJ_CASE_INSENSITIVE = 0x00000040
# The volume can't take FILE_OPEN_REQUIRING_OPLOCK: open without it (a plain read, logged).
NO_ATOMIC_OPEN = {
    0xC000000D,  # STATUS_INVALID_PARAMETER
    0xC0000010,  # STATUS_INVALID_DEVICE_REQUEST
    0xC00000BB,  # STATUS_NOT_SUPPORTED
    0xC00000E2,  # STATUS_OPLOCK_NOT_GRANTED (Windows 7, when other handles are open)
}


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class REQUEST_OPLOCK_INPUT_BUFFER(ctypes.Structure):
    _fields_ = [
        ("StructureVersion", wintypes.WORD),
        ("StructureLength", wintypes.WORD),
        ("RequestedOplockLevel", wintypes.DWORD),
        ("Flags", wintypes.DWORD),
    ]


class REQUEST_OPLOCK_OUTPUT_BUFFER(ctypes.Structure):
    _fields_ = [
        ("StructureVersion", wintypes.WORD),
        ("StructureLength", wintypes.WORD),
        ("OriginalOplockLevel", wintypes.DWORD),
        ("NewOplockLevel", wintypes.DWORD),
        ("Flags", wintypes.DWORD),
        ("AccessMode", wintypes.DWORD),
        ("ShareMode", wintypes.WORD),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]

NtCreateFile = ntdll.NtCreateFile
NtCreateFile.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    ctypes.POINTER(OBJECT_ATTRIBUTES),
    ctypes.POINTER(IO_STATUS_BLOCK),
    ctypes.c_void_p,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
]
NtCreateFile.restype = ctypes.c_long
RtlNtStatusToDosError = ntdll.RtlNtStatusToDosError
RtlNtStatusToDosError.argtypes = [ctypes.c_long]
RtlNtStatusToDosError.restype = wintypes.ULONG
CreateFileW = kernel32.CreateFileW
CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
CreateFileW.restype = wintypes.HANDLE
CreateEventW = kernel32.CreateEventW
CreateEventW.argtypes = [
    wintypes.LPVOID,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
CreateEventW.restype = wintypes.HANDLE
SetEvent = kernel32.SetEvent
SetEvent.argtypes = [wintypes.HANDLE]
SetEvent.restype = wintypes.BOOL
DeviceIoControl = kernel32.DeviceIoControl
DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(OVERLAPPED),
]
DeviceIoControl.restype = wintypes.BOOL
ReadFile = kernel32.ReadFile
ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(OVERLAPPED),
]
ReadFile.restype = wintypes.BOOL
GetOverlappedResult = kernel32.GetOverlappedResult
GetOverlappedResult.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(OVERLAPPED),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.BOOL,
]
GetOverlappedResult.restype = wintypes.BOOL
WaitForSingleObject = kernel32.WaitForSingleObject
WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
WaitForSingleObject.restype = wintypes.DWORD
WaitForMultipleObjects = kernel32.WaitForMultipleObjects
WaitForMultipleObjects.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.BOOL,
    wintypes.DWORD,
]
WaitForMultipleObjects.restype = wintypes.DWORD
GetFileSizeEx = kernel32.GetFileSizeEx
GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
GetFileSizeEx.restype = wintypes.BOOL
CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL


def _error() -> OSError:
    # Maps to FileNotFoundError, PermissionError, ... like open() does.
    return ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]


def _event() -> int:
    ev = CreateEventW(None, True, False, None)
    if not ev:
        raise _error()
    return int(ev)


def nt_path(path: str | os.PathLike[str]) -> str:
    """``path`` made absolute, as an NT path: ``\\??\\C:\\...``, ``\\??\\UNC\\server\\...``."""
    p = os.path.abspath(os.fspath(path))
    if p.startswith(("\\\\?\\", "\\\\.\\")):
        return "\\??\\" + p[4:]
    if p.startswith("\\\\"):
        return "\\??\\UNC\\" + p[2:]
    return "\\??\\" + p


def _open_plain(path: str | os.PathLike[str]) -> int:
    handle = CreateFileW(
        os.fspath(path),
        GENERIC_READ,
        SHARE_ALL,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED,
        None,
    )
    if handle is None or handle == INVALID_HANDLE:
        raise _error()
    return int(handle)


def _open_atomic(path: str | os.PathLike[str]) -> tuple[int | None, int]:
    """``NtCreateFile`` with ``FILE_OPEN_REQUIRING_OPLOCK``: ``(handle, 0)``, or ``(None, the
    Windows error)`` when the volume can't take the flag. Any other failure raises as ``open()``
    would: a missing file is ``FileNotFoundError``, and ``STATUS_CANNOT_BREAK_OPLOCK`` (another
    program's oplock our open would break) an ``OSError`` that the readers retry."""
    name = nt_path(path)
    units = len(name.encode("utf-16-le")) // 2
    buf = ctypes.create_unicode_buffer(name, units + 1)
    us = UNICODE_STRING(units * 2, (units + 1) * 2, ctypes.cast(buf, ctypes.c_void_p))
    oa = OBJECT_ATTRIBUTES(
        ctypes.sizeof(OBJECT_ATTRIBUTES),
        None,
        ctypes.pointer(us),
        OBJ_CASE_INSENSITIVE,
        None,
        None,
    )
    iosb = IO_STATUS_BLOCK()
    handle = wintypes.HANDLE()
    status = NtCreateFile(
        ctypes.byref(handle),
        FILE_GENERIC_READ,
        ctypes.byref(oa),
        ctypes.byref(iosb),
        None,
        FILE_ATTRIBUTE_NORMAL,
        SHARE_ALL,
        FILE_OPEN,
        FILE_NON_DIRECTORY_FILE | FILE_OPEN_REQUIRING_OPLOCK,
        None,
        0,
    )
    if status >= 0:  # NT_SUCCESS
        return int(handle.value or 0), 0
    err = int(RtlNtStatusToDosError(status))
    if status & 0xFFFFFFFF in NO_ATOMIC_OPEN:
        return None, err
    raise ctypes.WinError(err)  # type: ignore[attr-defined]


class Win32Handle:
    """A :class:`gregtec.gamestate.safe_open.Handle` over a real file (module docstring).

    ``oplock=False`` is the plain shared handle (safe_open before WO-6.8). ``atomic=False`` opens
    with ``CreateFileW`` and requests the oplock afterwards, which leaves a moment when the handle
    is open without it: only the tests do that, to measure the moment (D357).
    """

    def __init__(
        self, path: str | os.PathLike[str], oplock: bool = True, atomic: bool = True
    ) -> None:
        self.leased = False
        self.atomic = False  # the open and the oplock were one step
        self.oplock_error = (
            0  # why there is no oplock, when oplock=True (safe_open logs it)
        )
        self._events: list[int] = []
        try:
            for _ in range(3):
                self._events.append(_event())
            self._read_event, self._stop, self._break = self._events
            handle: int | None = None
            if oplock and atomic:
                handle, self.oplock_error = _open_atomic(path)
                self.atomic = handle is not None
            if handle is None:
                handle = _open_plain(path)
        except BaseException:
            self._free_events()
            raise
        self.handle = handle
        try:
            if oplock:
                # Nothing touches the file between the open and the request (Microsoft's rule
                # for FILE_OPEN_REQUIRING_OPLOCK).
                self._request()
                if not self.leased and self.atomic:
                    # Microsoft: when the request fails, close the handle the flag opened.
                    CloseHandle(self.handle)
                    self.atomic = False
                    self.handle = 0
                    self.handle = _open_plain(path)
        except BaseException:
            if self.handle:
                CloseHandle(self.handle)
            self._free_events()
            raise

    def _free_events(self) -> None:
        for ev in self._events:
            CloseHandle(ev)
        self._events = []

    def _request(self) -> None:
        self._in = REQUEST_OPLOCK_INPUT_BUFFER(
            REQUEST_OPLOCK_CURRENT_VERSION,
            ctypes.sizeof(REQUEST_OPLOCK_INPUT_BUFFER),
            OPLOCK_LEVEL_CACHE_READ | OPLOCK_LEVEL_CACHE_HANDLE,
            REQUEST_OPLOCK_INPUT_FLAG_REQUEST,
        )
        self._out = REQUEST_OPLOCK_OUTPUT_BUFFER(
            REQUEST_OPLOCK_CURRENT_VERSION, ctypes.sizeof(REQUEST_OPLOCK_OUTPUT_BUFFER)
        )
        self._ov = OVERLAPPED()
        self._ov.hEvent = self._break
        ok = DeviceIoControl(
            self.handle,
            FSCTL_REQUEST_OPLOCK,
            ctypes.byref(self._in),
            ctypes.sizeof(self._in),
            ctypes.byref(self._out),
            ctypes.sizeof(self._out),
            None,
            ctypes.byref(self._ov),
        )
        err = ctypes.get_last_error()
        if not ok and err == ERROR_IO_PENDING:
            self.leased = True  # granted: the request completes when the oplock breaks
        else:
            # Not granted (ERROR_OPLOCK_NOT_GRANTED; a file system without oplocks), or
            # completed at once: a plain shared read, as before D357, which safe_open logs.
            self.oplock_error = ERROR_OPLOCK_COMPLETED if ok else err

    def size(self) -> int:
        n = ctypes.c_longlong()
        if not GetFileSizeEx(self.handle, ctypes.byref(n)):
            raise _error()
        return int(n.value)

    def read_at(self, offset: int, size: int) -> bytes:
        if size <= 0:
            return b""
        buf = ctypes.create_string_buffer(size)
        ov = OVERLAPPED()
        ov.Offset = offset & 0xFFFFFFFF
        ov.OffsetHigh = offset >> 32
        ov.hEvent = self._read_event
        got = wintypes.DWORD(0)
        if not ReadFile(self.handle, buf, size, None, ctypes.byref(ov)):
            err = ctypes.get_last_error()
            if err == ERROR_HANDLE_EOF:
                return b""
            if err != ERROR_IO_PENDING:
                raise _error()
        if not GetOverlappedResult(
            self.handle, ctypes.byref(ov), ctypes.byref(got), True
        ):
            if ctypes.get_last_error() == ERROR_HANDLE_EOF:
                return b""
            raise _error()
        return buf.raw[: got.value]

    def broken(self) -> bool:
        return self.leased and WaitForSingleObject(self._break, 0) == WAIT_OBJECT_0

    def wait_break(self) -> bool:
        both = (wintypes.HANDLE * 2)(self._break, self._stop)
        return WaitForMultipleObjects(2, both, False, INFINITE) == WAIT_OBJECT_0

    def release(self) -> None:
        SetEvent(self._stop)

    def close(self) -> None:
        CloseHandle(self.handle)

    def dispose(self) -> None:
        if self.leased:
            # Closing the handle completes the pending request; wait for it, so the kernel is
            # done with the buffers before they are freed.
            WaitForSingleObject(self._break, DISPOSE_WAIT_MS)
        self._free_events()


def open_handle(
    path: str | os.PathLike[str], oplock: bool = True, atomic: bool = True
) -> Win32Handle:
    return Win32Handle(path, oplock, atomic)
