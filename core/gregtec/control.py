"""Finding and calling the local supervisor and core (WO-2.1, D115).

Each running process writes a run file with its pid, port and admin token:

- ``GOS_HOME/run/supervisor.json`` and ``GOS_HOME/run/core.json``. ``run\\`` inherits GOS_HOME's
  ACL, which gives gos-dev nothing (setup_gos_dev.ps1 step 4); on POSIX the files are 0600.
- ``GOS_HOME/data/launch.json``: the supervisor's port and a token that can only ask it to launch
  Lab or Export. gos-dev reads ``data\\`` (D82), so Claude Code can run ``gos launch``.

Run files are written atomically (a new temp file, then a rename), and a process removes its own
file on a clean exit. A file left by a crash is harmless: callers check that the port answers.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SUPERVISOR_SERVICE = "gos-supervisor"  # what each answers in GET /health
CORE_SERVICE = "gregtec-core"
SUPERVISOR_FILE = "supervisor.json"
CORE_FILE = "core.json"
LAUNCH_FILE = "launch.json"
LOOPBACK = "127.0.0.1"


def write_run_file(path: Path, data: dict[str, Any], *, private: bool = True) -> None:
    """Write ``data`` as JSON to ``path`` atomically. ``private``: 0600 on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600 if private else 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_run_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def remove_run_file(path: Path, pid: int) -> None:
    """Delete ``path`` if it is still this process's (another instance may have replaced it)."""
    data = read_run_file(path)
    if data is not None and data.get("pid") == pid:
        path.unlink(missing_ok=True)


class ControlError(Exception):
    """The process didn't answer, or answered with an error status."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def call(
    port: int,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 5.0,
    raw: tuple[str, bytes] | None = None,
) -> dict[str, Any]:
    """One request to ``http://127.0.0.1:<port><path>`` with a JSON ``body`` or a ``raw``
    (content type, bytes) one (the voice client's screen frame, WO-8.2); the answer is JSON.
    Raises :class:`ControlError`."""
    data = None if body is None else json.dumps(body).encode()
    kind = "application/json"
    if raw is not None:
        kind, data = raw
    request = urllib.request.Request(
        f"http://{LOOPBACK}:{port}{path}", data=data, method=method
    )
    if data is not None:
        request.add_header("Content-Type", kind)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    # Never through a proxy: HTTP(S)_PROXY would otherwise get the token.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = str(
                json.loads(detail).get("error") or json.loads(detail).get("detail")
            )
        except (ValueError, AttributeError):
            pass
        raise ControlError(f"{method} {path}: {exc.code} {detail}", exc.code) from None
    except (OSError, ValueError) as exc:
        raise ControlError(
            f"{method} {path}: no answer on port {port} ({exc})"
        ) from None
    try:
        result = json.loads(raw or b"{}")
    except ValueError:
        raise ControlError(f"{method} {path}: the answer isn't JSON") from None
    return result if isinstance(result, dict) else {"result": result}


def running(run_file: Path, service: str) -> dict[str, Any] | None:
    """The run file's data if the process it names answers ``GET /health``, else None.

    The answer must name ``service`` and the file's pid, so a token is never sent to some other
    program that took the port after the process died (it would also need the dead pid).
    """
    data = read_run_file(run_file)
    if data is None or not isinstance(data.get("port"), int):
        return None
    try:
        health = call(data["port"], "GET", "/health", timeout=2.0)
    except ControlError:
        return None
    if health.get("service") != service or health.get("pid") != data.get("pid"):
        return None
    return data
