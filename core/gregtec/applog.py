"""Structured logging for GOS processes (WO-2.1): one JSON object per line in ``logs/<process>.log``.

Each line has ``ts`` (UTC, ISO 8601), ``level``, ``process``, ``logger`` and ``msg``, plus every
field passed with ``extra=``::

    log.info("process exited", extra={"proc": "core", "code": 1})

gos-dev can read Clayton's ``logs\\`` (D82), so nothing secret is ever logged: no tokens, cookies,
pairing codes or keys. Callers log ids and names instead. Files rotate at 5 MB, keeping 5.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 5
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
    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True
    )
    file_handler.setFormatter(JsonFormatter(process))
    handlers: list[logging.Handler] = [file_handler]
    # pythonw (the login start's gos-supervisor.exe) has no stderr.
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(f"{process}: %(levelname)s %(message)s"))
        handlers.append(console)
    for handler in handlers:
        handler._gos = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(level)
    return path
