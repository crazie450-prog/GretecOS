"""What Clayton's desktop shows (WO-2.6, D161): whether GTNH's window is in front.

The core runs in his interactive session (D116), so on Windows it can ask which window has the
keyboard focus: ``GetForegroundWindow``, its process, and that process's program. GTNH is the
only Java program with a window on the gaming PC (Prism is ``prismlauncher.exe``), so a
foreground ``java.exe`` or ``javaw.exe`` means the game has focus, and the dashboard switches to
glance mode (D49). Three Win32 calls, cheap enough to ask every second. Elsewhere, or when
Windows won't say (an elevated window), it is unknown (None) and the dashboard goes by the mouse
alone.
"""

from __future__ import annotations

import ntpath
import sys

JAVA = ("java.exe", "javaw.exe")
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def foreground_program() -> str | None:
    """The file name of the program whose window is in front (lower case), or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    window = user32.GetForegroundWindow()
    if not window:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    if not pid.value:
        return None
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return None  # an elevated window, or one that just closed
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(
            process, 0, buffer, ctypes.byref(size)
        ):
            return None
        return ntpath.basename(buffer.value).lower()
    finally:
        kernel32.CloseHandle(process)


def game_focused(program: str | None) -> bool | None:
    """True when ``program`` (a :func:`foreground_program` answer) is GTNH's Java, False for
    any other program, None when it isn't known."""
    if program is None:
        return None
    return program in JAVA
