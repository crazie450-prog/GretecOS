"""Win32 file handles for :mod:`gregtec.gamestate.safe_open` (Windows only; D357).

``CreateFileW`` (read, sharing read, write and delete, overlapped), then ``FSCTL_REQUEST_OPLOCK``
for a read-handle oplock (``OPLOCK_LEVEL_CACHE_READ | OPLOCK_LEVEL_CACHE_HANDLE``). The request
stays pending while the oplock holds; its completion (an event) is the break. Reads are
overlapped ``ReadFile`` calls at an offset. Closing the handle is what acknowledges a break.
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
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF
DISPOSE_WAIT_MS = 5000

INVALID_HANDLE = ctypes.c_void_p(-1).value


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


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

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


class Win32Handle:
    """A :class:`gregtec.gamestate.safe_open.Handle` over a real file (module docstring)."""

    def __init__(self, path: str | os.PathLike[str], oplock: bool = True) -> None:
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
        self.handle = int(handle)
        self.leased = False
        self.oplock_error = (
            0  # the request's error when it wasn't granted (diagnostics)
        )
        self._events: list[int] = []
        try:
            self._read_event = _event()
            self._events.append(self._read_event)
            self._stop = _event()
            self._events.append(self._stop)
            self._break = _event()
            self._events.append(self._break)
            if oplock:
                self._request()
        except BaseException:
            CloseHandle(self.handle)
            for ev in self._events:
                CloseHandle(ev)
            raise

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
            # completed at once: a plain shared read, as before D357.
            self.oplock_error = 0 if ok else err

    @property
    def new_level(self) -> int:
        """The oplock level a break left (after a break; diagnostics)."""
        return int(self._out.NewOplockLevel) if self.leased else 0

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
        for ev in self._events:
            CloseHandle(ev)
        self._events = []


def open_handle(path: str | os.PathLike[str], oplock: bool = True) -> Win32Handle:
    return Win32Handle(path, oplock)
