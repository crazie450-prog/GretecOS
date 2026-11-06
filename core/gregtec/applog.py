"""Structured logging for GOS processes (WO-2.1): one JSON object per line in ``logs/<process>.log``.

Each line has ``ts`` (UTC, ISO 8601), ``level``, ``process``, ``logger`` and ``msg``, plus every
field passed with ``extra=``::

    log.info("process exited", extra={"proc": "core", "code": 1})

gos-dev can read Clayton's ``logs\\`` (D82), so nothing secret is ever logged: no tokens, cookies,
pairing codes or keys. Callers log ids and names instead. Files rotate at 5 MB, keeping 5. On
Windows a rename fails while another program has the file open without share-delete (an editor,
a tail): then the handler keeps appending and tries again a minute later, and no record is lost.

Lines also go to stderr when it is a terminal (``gos serve`` in a window). A process the
supervisor started has its stderr in ``logs\\<name>.stderr.log`` for crashes, so its records
aren't copied there.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 5
ROTATE_RETRY_S = 60.0
_clock = time.monotonic  # tests replace it
# LogRecord's own attributes; anything else on a record came from ``extra=``.
_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, process: str) -> None:
        super().__init__()
        self.process = process

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "process": self.process,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class RotatingFile(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that keeps appending when the file can't be renamed (see above).

    It renames the full file aside first, so a rename the log's reader blocks leaves the backups
    as they were (the stock handler moves them up first, and would drop one on every failed try,
    and then drop each record).
    """

    retry_at = 0.0  # monotonic; no rotation before

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # stdlib naming
        if _clock() < self.retry_at:
            return False
        return bool(super().shouldRollover(record))

    def doRollover(self) -> None:  # stdlib naming
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        aside = f"{self.baseFilename}.rotating"
        try:
            if not os.path.exists(
                aside
            ):  # else a try that failed halfway left it: finish it
                os.replace(self.baseFilename, aside)
            for i in range(self.backupCount - 1, 0, -1):
                older = f"{self.baseFilename}.{i}"
                if os.path.exists(older):
                    os.replace(older, f"{self.baseFilename}.{i + 1}")
            os.replace(aside, f"{self.baseFilename}.1")
        except FileNotFoundError:  # no log file yet
            pass
        except OSError:  # in use: keep appending, try again later
            self.retry_at = _clock() + ROTATE_RETRY_S
        self.stream = self._open()


def setup(process: str, logs_dir: Path, *, level: int = logging.INFO) -> Path:
    """Send the root logger to ``logs_dir/<process>.log`` as JSON lines, and to stderr if any.

    Returns the log file's path. Calling it again replaces the handlers it added before.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"{process}.log"
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_gos", False):
            root.removeHandler(handler)
            handler.close()
    file_handler = RotatingFile(
        path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(JsonFormatter(process))
    handlers: list[logging.Handler] = [file_handler]
    # pythonw (the login start's gos-supervisor.exe) has no stderr; a supervised process's is a
    # file (see the module docstring).
    if sys.stderr is not None and _isatty(sys.stderr):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(f"{process}: %(levelname)s %(message)s"))
        handlers.append(console)
    for handler in handlers:
        handler._gos = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(level)
    return path


def _isatty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return False
