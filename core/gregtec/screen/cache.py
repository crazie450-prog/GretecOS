"""The screen cache: each posted frame for 24 h, then deleted (plan 7.7, 4.3; D288).

Frames live in ``GOS_HOME/data/cache/screens/`` as ``<UTC stamp>-<id>.png``, re-encoded by
:func:`normalise` (so no metadata from the sender is kept). :meth:`ScreenCache.sweep` deletes
every frame older than :data:`KEEP_S` by its file time; the core sweeps when it starts, before
each new frame is kept and at most hourly from its watcher (:meth:`ScreenCache.tick`). A file
that can't be deleted now (Windows: open elsewhere) is tried again on the next sweep.
"""

from __future__ import annotations

import io
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

log = logging.getLogger(__name__)

DIR = Path("data") / "cache" / "screens"  # under GOS_HOME (plan 4.3)
KEEP_S = 24 * 3600  # plan 7.7: cached for 24 h, then deleted
SWEEP_EVERY_S = 3600.0
MAX_BYTES = 12 * 1024 * 1024  # a posted frame (a 4K PNG is well under this)
MAX_PIXELS = 40_000_000  # refused before decoding past this (a decompression bomb)
LONG_EDGE = 1568  # Claude's vision works on at most this many pixels on the long edge
MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg"}
NAME = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{12}\.png$")


class FrameError(ValueError):
    """The posted bytes aren't one PNG or JPEG frame GOS will read."""


@dataclass(frozen=True)
class Frame:
    """One kept frame: its id, file, size and when it came."""

    id: str
    path: Path
    width: int
    height: int
    at: float  # epoch seconds

    def read(self) -> bytes:
        return self.path.read_bytes()


def normalise(data: bytes) -> tuple[bytes, int, int]:
    """``data`` (PNG or JPEG) as an RGB PNG at most :data:`LONG_EDGE` pixels on its long edge:
    (png bytes, width, height). Raises :class:`FrameError`."""
    if not data:
        raise FrameError("no image")
    if len(data) > MAX_BYTES:
        raise FrameError(f"the image is over {MAX_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in MEDIA_TYPES:
                raise FrameError("send a PNG or JPEG image")
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise FrameError("the image is too large")
            image.load()
            rgb = image.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise FrameError(f"the image doesn't read ({type(exc).__name__})") from None
    scale = LONG_EDGE / max(rgb.size)
    if scale < 1:
        size = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
        rgb = rgb.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    rgb.save(out, format="PNG", optimize=False)
    return out.getvalue(), rgb.width, rgb.height


class ScreenCache:
    def __init__(
        self,
        home: Path,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.dir = home / DIR
        self.clock = clock
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._swept: float | None = None

    def keep(self, data: bytes) -> Frame:
        """Normalise ``data`` and keep it (after a sweep). Raises :class:`FrameError`."""
        png, width, height = normalise(data)
        self.sweep()
        now = self.clock()
        stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
        frame_id = f"{stamp}-{secrets.token_hex(6)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{frame_id}.png"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(png)
        os.replace(tmp, path)
        os.utime(path, (now, now))  # the sweep goes by this time
        return Frame(frame_id, path, width, height, now)

    def frames(self) -> list[Path]:
        try:
            return sorted(p for p in self.dir.iterdir() if NAME.match(p.name))
        except OSError:
            return []

    def sweep(self) -> int:
        """Delete every frame older than :data:`KEEP_S` (and stray ``.tmp`` files as old);
        returns how many were deleted."""
        cutoff = self.clock() - KEEP_S
        deleted = 0
        with self._lock:
            self._swept = self.monotonic()
            try:
                entries = list(self.dir.iterdir())
            except OSError:
                return 0
            for path in entries:
                if not (NAME.match(path.name) or path.suffix == ".tmp"):
                    continue  # only GOS's own frames
                try:
                    if path.stat().st_mtime >= cutoff:
                        continue
                    path.unlink()
                    deleted += 1
                except OSError as exc:  # open elsewhere: the next sweep tries again
                    log.debug("screen frame not deleted", extra={"why": str(exc)})
        if deleted:
            log.info("screen frames deleted", extra={"count": deleted})
        return deleted

    def tick(self) -> None:
        """The core's watcher: a sweep at most every :data:`SWEEP_EVERY_S`."""
        if self._swept is None or self.monotonic() - self._swept >= SWEEP_EVERY_S:
            self.sweep()
