"""Shared by the rehearsal's driver and its child processes (WO-11.3): a file keyring, the
command mailboxes the fakes are driven through, and the fake desktop's control files.

Test-only. The child processes get this folder on ``PYTHONPATH`` and
``PYTHON_KEYRING_BACKEND=rfakes.FileKeyring``, so the real watchers, the voice client and the
Discord bot read their core tokens from one JSON file the driver can see and change (L9 deletes
and restores the Anthropic key in it). Nothing here imports GOS, so loading the keyring backend
never drags the core into a process that doesn't need it.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import keyring.backend
import keyring.errors

KEYRING_ENV = "GOS_REHEARSAL_KEYRING"
CONTROL_ENV = "GOS_REHEARSAL_CONTROL"


def write_atomic(path: Path, text: str) -> None:
    """``text`` to ``path`` through a temp file and a replace: a reader in another process sees
    the old file or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(20):  # Windows: a reader may hold the target for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def read_json(path: Path, default: Any = None) -> Any:  # noqa: ANN401 - JSON
    for _ in range(20):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (PermissionError, ValueError):
            time.sleep(0.05)  # mid-replace on Windows
    return default


class FileKeyring(keyring.backend.KeyringBackend):
    """A keyring in the JSON file :data:`KEYRING_ENV` names (test only)."""

    priority = 1  # type: ignore[assignment]

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self.path = path or Path(os.environ[KEYRING_ENV])

    def _load(self) -> dict[str, str]:
        data = read_json(self.path, {})
        return data if isinstance(data, dict) else {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._load().get(f"{service}|{username}")

    def set_password(self, service: str, username: str, password: str) -> None:
        data = self._load()
        data[f"{service}|{username}"] = password
        write_atomic(self.path, json.dumps(data))

    def delete_password(self, service: str, username: str) -> None:
        data = self._load()
        if data.pop(f"{service}|{username}", None) is None:
            raise keyring.errors.PasswordDeleteError(username)
        write_atomic(self.path, json.dumps(data))


class Mailbox:
    """One fake's commands in, its results out: ``<root>/<name>/in/<n>.json`` (taken in order,
    each once) and ``<root>/<name>/out.jsonl`` (appended, one JSON object a line)."""

    def __init__(self, root: Path, name: str) -> None:
        self.folder = root / name
        self.inbox = self.folder / "in"
        self.out = self.folder / "out.jsonl"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self._n = 0

    # the driver's side
    def send(self, command: dict[str, Any]) -> None:
        self._n += 1
        write_atomic(self.inbox / f"{time.time_ns():020d}-{self._n:04d}.json", json.dumps(command))

    def results(self) -> list[dict[str, Any]]:
        try:
            text = self.out.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        out = []
        for line in text.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass  # a line still being written
        return out

    # the fake's side
    def commands(self) -> Iterator[dict[str, Any]]:
        for path in sorted(self.inbox.glob("*.json")):
            data = read_json(path)
            path.unlink(missing_ok=True)
            if isinstance(data, dict):
                yield data

    def record(self, result: dict[str, Any]) -> None:
        with open(self.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(result) + "\n")


class FileIdle:
    """Keyboard and mouse idle (plan 7.12's "away"), from ``<control>/idle_s``: a number of
    seconds, or absent for present (0 s)."""

    def __init__(self, control: Path) -> None:
        self.path = control / "idle_s"

    def idle_s(self) -> float | None:
        value = read_json(self.path, 0)
        return float(value) if isinstance(value, int | float) else 0.0


def control_dir() -> Path:
    return Path(os.environ[CONTROL_ENV])
