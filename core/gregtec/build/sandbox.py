"""The build-script sandbox: a model's Python build script, run apart and turned into cells
(D385; Clayton, 2026-11-22: option B, "run real Python, sandboxed").

A freeform shell design is a short Python program that places blocks with the building API
(``set_block``, ``fill``, ``clear``, ``get_block``; :mod:`gregtec.build.sandbox_child`). GOS never
runs it in its own process. Every layer below must hold before a block reaches the shell gate:

1. **The syntax tree** (:func:`check`, here, before anything runs): at most
   :data:`MAX_SCRIPT_CHARS`; imports of :data:`MODULES` only; no class, ``global``, ``with``,
   async; no name that starts with ``__`` and no attribute that starts with ``_`` (the way out of
   a Python sandbox is through dunders and private module attributes, e.g. ``random._os``), and
   none of :data:`BANNED` (``format`` can read attributes by name).
2. **The child process** (:func:`run`): the same Python, started with ``-S -s -B -P`` (no
   site-packages, no user site, no bytecode written, no script folder on the path) from an empty
   temporary folder with an empty environment (no key, no path, nothing of GOS's), fed the script
   on stdin. It has a short list of builtins (no ``open``, ``eval``, ``exec``, ``getattr``,
   ``__import__`` but its own) and an audit hook (PEP 578) that refuses every audited event after
   the script starts: files, sockets, processes, imports, ``ctypes``, frames, code objects.
3. **The OS limits**: on Windows a Job Object (one process, so it can start no other; memory;
   CPU time; killed when GOS closes it; no access to the desktop or clipboard); on POSIX rlimits
   (address space, CPU seconds, no file may be written, few file handles, no core). A wall-clock
   timeout kills it either way.
4. **The answer is data**: one JSON object of cells (``[x, y, z, what]``, at most the cap) that
   the ``script`` operation of :func:`gregtec.build.shell.compile_ops` reads (``_Builder.resolve``), after which the whole shell gate runs as for
   any design (palette only, nothing in a machine's keep-out, a corridor, a chase or a doorway,
   inside the bounding volume, at most ``max_blocks``).

Nothing the script does reaches a file, the network, a key or a world. The cells it gives are
deterministic: ``random`` is seeded and string hashing is fixed.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHILD = Path(__file__).with_name("sandbox_child.py")
MODULES = ("math", "random", "itertools", "functools")
MAX_SCRIPT_CHARS = 40_000
TIMEOUT_S = 10.0  # wall clock, start-up included (the tools' deadline is 30 s)
# A saved build's script run again (a rebuild, the viewer, export): it passed once, so a busy
# PC gets more time before the same build is refused.
REPLAY_TIMEOUT_S = 25.0
CPU_S = 8  # CPU seconds
MEMORY_BYTES = 1 << 30  # 1 GiB
MAX_ANSWER_BYTES = 64 << 20
BANNED = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "help",
        "memoryview",
        "type",
        "object",
        "super",
        "format",
        "format_map",
        "gi_frame",
        "gi_code",
        "cr_frame",
        "ag_frame",
        "f_globals",
        "f_locals",
        "f_back",
        "f_builtins",
        "tb_frame",
        "co_code",
    }
)
_FORBIDDEN_NODES: dict[type[ast.AST], str] = {
    ast.ClassDef: "a class",
    ast.AsyncFunctionDef: "async",
    ast.AsyncFor: "async",
    ast.AsyncWith: "async",
    ast.Await: "await",
    ast.Global: "global",
    ast.Nonlocal: "nonlocal",
    ast.With: "with",
}


class ScriptError(ValueError):
    """A script that can't be used: why, in words the model can act on."""


def check(script: str) -> ast.Module:
    """The first layer: refuse a script whose syntax tree reaches for anything but building."""
    if not isinstance(script, str) or not script.strip():
        raise ScriptError("the script is empty")
    if len(script) > MAX_SCRIPT_CHARS:
        raise ScriptError(
            f"the script is {len(script)} characters, more than {MAX_SCRIPT_CHARS}"
        )
    try:
        tree = ast.parse(script, "<build script>")
    except SyntaxError as exc:
        raise ScriptError(f"syntax error on line {exc.lineno}: {exc.msg}") from None
    except (ValueError, RecursionError, MemoryError):
        raise ScriptError("the script can't be read as Python") from None
    for node in ast.walk(tree):
        where = f" (line {getattr(node, 'lineno', '?')})"
        for kind, what in _FORBIDDEN_NODES.items():
            if isinstance(node, kind):
                raise ScriptError(f"{what} is not allowed in a build script{where}")
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in MODULES:
                    raise ScriptError(
                        f"import {a.name}: only {', '.join(MODULES)} may be imported{where}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in MODULES:
                raise ScriptError(
                    f"from {node.module} import: only {', '.join(MODULES)} may be imported"
                    f"{where}"
                )
            for a in node.names:
                if a.name == "*" or a.name.startswith("_") or a.name in BANNED:
                    raise ScriptError(f"from {node.module} import {a.name}{where}")
        for name in _names(node):
            if name.startswith("__") or name in BANNED:
                raise ScriptError(f"{name!r} is not allowed in a build script{where}")
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("_") or node.attr in BANNED
        ):
            raise ScriptError(f"'.{node.attr}' is not allowed in a build script{where}")
    return tree


def _names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.FunctionDef | ast.Lambda):
        a = node.args
        args = [*a.posonlyargs, *a.args, *a.kwonlyargs]
        args += [x for x in (a.vararg, a.kwarg) if x is not None]
        out = [x.arg for x in args]
        return [node.name, *out] if isinstance(node, ast.FunctionDef) else out
    if isinstance(node, ast.alias):
        return [node.asname or node.name]
    if isinstance(node, ast.keyword) and node.arg is not None:
        return [node.arg]
    if isinstance(node, ast.ExceptHandler) and node.name:
        return [node.name]
    return []


@dataclass(frozen=True)
class Ran:
    cells: dict[tuple[int, int, int], str]  # cell -> what the script placed there
    log: tuple[str, ...]  # the script's print() lines


def interpreter() -> str:
    """The Python that runs the child. On Windows a venv's ``python.exe`` is a launcher that
    starts the base interpreter as a second process (D380): the Job Object allows one process,
    and a process started before the job is assigned would be outside it, so the base
    interpreter is started directly. The child needs nothing from the venv (``-S``: stdlib
    only)."""
    if os.name == "nt":
        base = getattr(sys, "_base_executable", None)
        if base and Path(base).is_file():
            return str(base)
    return sys.executable


def _env() -> dict[str, str]:
    env = {"PYTHONHASHSEED": "0", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if os.name == "nt":  # Windows' own start-up needs it; it names no user folder
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return env


def _posix_limits() -> None:  # pragma: no cover - runs in the child before exec
    import resource

    for what, value in (
        (resource.RLIMIT_AS, MEMORY_BYTES),
        (resource.RLIMIT_CPU, CPU_S),
        (resource.RLIMIT_FSIZE, 0),
        (resource.RLIMIT_NOFILE, 16),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(what, (value, value))


def run(
    script: str,
    facts: Mapping[str, Any],
    max_cells: int,
    timeout: float = TIMEOUT_S,
) -> Ran:
    """Run ``script`` in the sandbox with ``facts`` (the build's sizes, palette roles and boxes,
    readable in the script as upper-case names) and give its cells, or raise
    :class:`ScriptError`."""
    check(script)
    request = json.dumps(
        {"script": script, "facts": dict(facts), "max_cells": int(max_cells)}
    ).encode("utf-8")
    argv = [interpreter(), "-S", "-s", "-B", "-P", str(CHILD)]
    with tempfile.TemporaryDirectory(prefix="gos-build-") as cwd:
        kw: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "env": _env(),
        }
        if os.name == "nt":
            proc = _start_windows(argv, kw)
        else:
            kw["preexec_fn"] = _posix_limits
            kw["start_new_session"] = True
            proc = subprocess.Popen(argv, **kw)  # our own child, fixed argv
        job = None
        try:
            if os.name == "nt":
                # before the script is sent: it waits on stdin until then
                job = _windows_job(proc)
            out, err = _talk(proc, request, timeout)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if job is not None:
                _close(job)
    return _read(out, proc.returncode, max_cells, err)


MAX_ERR_BYTES = 64 << 10


def _talk(
    proc: subprocess.Popen[bytes], request: bytes, timeout: float
) -> tuple[bytes, bytes]:
    """Send the request and read both pipes, each up to its cap: a child that writes more is
    killed, so GOS never holds more than MAX_ANSWER_BYTES of its output."""
    import threading
    import time

    over = threading.Event()

    def drain(pipe: Any, cap: int, into: bytearray) -> None:  # noqa: ANN401 - a pipe
        while True:
            chunk = pipe.read1(65536) if hasattr(pipe, "read1") else pipe.read(65536)
            if not chunk:
                return
            if len(into) + len(chunk) > cap:
                over.set()
                proc.kill()
                return
            into += chunk

    out, err = bytearray(), bytearray()
    readers = [
        threading.Thread(target=drain, args=(proc.stdout, MAX_ANSWER_BYTES, out)),
        threading.Thread(target=drain, args=(proc.stderr, MAX_ERR_BYTES, err)),
    ]
    for t in readers:
        t.daemon = True
        t.start()
    try:
        assert proc.stdin is not None
        proc.stdin.write(request)
        proc.stdin.close()
    except OSError:  # the child already stopped: its pipes say why
        pass
    deadline = time.monotonic() + timeout
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise ScriptError(
            f"the script ran longer than {timeout:g} seconds and was stopped"
        ) from None
    finally:
        for t in readers:
            t.join(timeout=max(1.0, deadline - time.monotonic()))
    if over.is_set():
        raise ScriptError("the script's answer is too large")
    return bytes(out), bytes(err)


def _last_line(err: bytes) -> str:
    """The child's last stderr line (the exception, never the traceback's paths), clipped."""
    lines = [
        ln.strip() for ln in err.decode("utf-8", "replace").splitlines() if ln.strip()
    ]
    return lines[-1][:200] if lines else ""


def _read(out: bytes, code: int | None, max_cells: int, err: bytes = b"") -> Ran:
    if len(out) > MAX_ANSWER_BYTES:
        raise ScriptError("the script's answer is too large")
    try:
        got = json.loads(out.decode("utf-8")) if out else None
    except (ValueError, UnicodeDecodeError, RecursionError):
        got = None
    if not isinstance(got, dict):
        why = _last_line(err)
        raise ScriptError(
            "the script was stopped (it used too much memory or CPU time, or broke the "
            f"sandbox's rules; exit code {code}" + (f": {why}" if why else "") + ")"
        )
    raw = got.get("log", [])
    log = tuple(str(x)[:200] for x in raw[:50]) if isinstance(raw, list) else ()
    if "error" in got:
        raise ScriptError(str(got["error"])[:500])
    rows = got.get("cells")
    if not isinstance(rows, list) or len(rows) > max_cells:
        raise ScriptError(f"the script's answer has no cells or more than {max_cells}")
    cells: dict[tuple[int, int, int], str] = {}
    for r in rows:
        if (
            not isinstance(r, list)
            or len(r) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) for v in r[:3])
            or not isinstance(r[3], str)
        ):
            raise ScriptError("the script's answer is malformed")
        cells[(r[0], r[1], r[2])] = r[3]
    return Ran(cells, log)


# --- Windows: a Job Object ------------------------------------------------------------------


def _start_windows(
    argv: list[str], kw: dict[str, Any]
) -> subprocess.Popen[bytes]:  # pragma: no cover
    """Start the child out of any job GOS itself runs in (a launcher's or a terminal's), so
    its own Job Object can be the only one; a parent job that refuses that keeps it (the
    pattern of gregtec.playhooks)."""
    flags = subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.Popen(argv, creationflags=flags | 0x01000000, **kw)
    except OSError:  # CREATE_BREAKAWAY_FROM_JOB refused by the job GOS is in
        return subprocess.Popen(argv, creationflags=flags, **kw)


def _windows_job(
    proc: subprocess.Popen[bytes],
) -> int:  # pragma: no cover - Windows only
    """Put the child in a Job Object: one process, MEMORY_BYTES, CPU_S, killed when the job
    is closed, no desktop or clipboard. Raises ScriptError (and kills it) when that fails."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", Basic),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class UiRestrictions(ctypes.Structure):
        _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]

    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    def make(ui_limits: bool) -> int:
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        try:
            info = Extended()
            info.BasicLimitInformation.LimitFlags = (
                0x0002  # PROCESS_TIME
                | 0x0008  # ACTIVE_PROCESS
                | 0x0100  # PROCESS_MEMORY
                | 0x0400  # DIE_ON_UNHANDLED_EXCEPTION
                | 0x2000  # KILL_ON_JOB_CLOSE
            )
            info.BasicLimitInformation.PerProcessUserTimeLimit = CPU_S * 10_000_000
            info.BasicLimitInformation.ActiveProcessLimit = 1
            info.ProcessMemoryLimit = MEMORY_BYTES
            if not k32.SetInformationJobObject(
                job, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
            if ui_limits:  # every JOB_OBJECT_UILIMIT_* (desktop, clipboard, ...)
                ui = UiRestrictions(0xFF)
                if not k32.SetInformationJobObject(
                    job, 4, ctypes.byref(ui), ctypes.sizeof(ui)
                ):
                    raise OSError(
                        ctypes.get_last_error(), "SetInformationJobObject(UI)"
                    )
            handle = int(proc._handle)  # type: ignore[attr-defined]
            if not k32.AssignProcessToJobObject(job, handle):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        except OSError:
            _close(int(job))
            raise
        return int(job)

    try:
        try:
            return make(ui_limits=True)
        except OSError:
            # A child that stayed in GOS's own job (its breakaway refused) can be nested in
            # a second job only without UI limits: keep every other limit.
            return make(ui_limits=False)
    except OSError as exc:
        proc.kill()
        raise ScriptError(
            f"the sandbox could not be set up ({exc}); nothing ran"
        ) from None


def _close(job: int) -> None:  # pragma: no cover - Windows only
    import ctypes

    ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))
