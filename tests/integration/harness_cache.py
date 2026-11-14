"""One ``gos export collect`` + ``gos db build`` per harness game folder per test session (D340).

Most integration test files need a GOS_HOME holding a DB built from a harness run, exactly as
``collect.collect(game, home, 1, allow_harness=True)`` then ``build.build(home, allow_harness=True)``
leave it. Building it takes 15-65 s each time and about 30 module fixtures did it again for the same
two runs (the server's and the client's). :class:`HomeCache` builds each game folder's home once into
a shared folder and gives every caller its own copy, so a test can still write to its home (plans,
the vault, config) without touching another's.

Under pytest-xdist the workers share the cache: the first to ask for a game folder builds it while
holding a lock (``mkdir`` of ``<key>.lock``, atomic on every OS) and the others wait for it, so each
home is built once per run, not once per worker. A failed build is recorded (``<key>.failed``) so the
waiting workers fail with its message instead of waiting forever; the builder re-raises its own error.

Not a test file (no ``test_`` prefix): ``conftest.py`` and ``test_harness_cache.py`` load it.
"""

from __future__ import annotations

import hashlib
import shutil
import time
import traceback
from collections.abc import Callable
from pathlib import Path

Builder = Callable[[Path, Path], None]  # (game folder, empty GOS_HOME) -> None


class CacheError(RuntimeError):
    pass


def collect_and_build(game: Path, home: Path) -> None:
    """What the fixtures did themselves before D340."""
    from gregtec.db import build
    from gregtec.ingest import collect

    collect.collect(game, home, 1, allow_harness=True)
    build.build(home, allow_harness=True)


class HomeCache:
    def __init__(
        self,
        root: Path,
        builder: Builder = collect_and_build,
        *,
        timeout_s: float = 1800.0,
        poll_s: float = 0.5,
    ) -> None:
        self.root = root
        self.builder = builder
        self.timeout_s = timeout_s
        self.poll_s = poll_s

    def key(self, game: Path) -> str:
        return hashlib.sha256(str(game.resolve()).encode("utf-8")).hexdigest()[:16]

    def home(self, game: Path, dest: Path) -> Path:
        """``dest`` (missing or empty) becomes a copy of ``game``'s built home; returns ``dest``."""
        built = self._built(game)
        shutil.copytree(built, dest, dirs_exist_ok=True)
        # gos db build's report names the home it was built in: name the copy instead, as a
        # build of its own would have.
        origin = str(self._staging(game)).encode("utf-8")
        for report in (dest / "data" / "reports").glob("*.txt"):
            text = report.read_bytes()
            if origin in text:
                report.write_bytes(text.replace(origin, str(dest).encode("utf-8")))
        return dest

    def _staging(self, game: Path) -> Path:
        """Where ``game``'s home is built (under the lock), before it is renamed into place."""
        return self.root / f"{self.key(game)}.building"

    def _built(self, game: Path) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        key = self.key(game)
        done = self.root / key
        failed = self.root / f"{key}.failed"
        lock = self.root / f"{key}.lock"
        deadline = time.monotonic() + self.timeout_s
        while True:
            if done.is_dir():
                return done
            if failed.is_file():
                raise CacheError(
                    f"building the harness home for {game} failed in another worker:\n"
                    + failed.read_text(encoding="utf-8")
                )
            try:
                lock.mkdir()
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise CacheError(
                        f"timed out after {self.timeout_s:.0f} s waiting for {lock} "
                        f"(the harness home for {game})"
                    ) from None
                time.sleep(self.poll_s)
                continue
            try:
                if done.is_dir():  # built between the check above and the lock
                    return done
                staging = self._staging(game)
                shutil.rmtree(staging, ignore_errors=True)
                try:
                    self.builder(game, staging)
                except BaseException:
                    failed.write_text(traceback.format_exc(), encoding="utf-8")
                    shutil.rmtree(staging, ignore_errors=True)
                    raise
                staging.rename(done)
                return done
            finally:
                lock.rmdir()
