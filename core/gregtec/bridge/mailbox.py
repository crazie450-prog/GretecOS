"""M1: the OpenComputers file mailbox, the core's side (WO-7.3; plan 7.9 "M1 mailbox protocol",
research F§2; contract docs/contracts/oc_bridge.md; D216-D218).

From MV, before an internet card, the OC agent (oc/gos-agent.lua with oc/mailbox.lua) and the core
exchange the contract's two documents through two fixed files in one folder of one of the OC
computer's managed drives: ``<world>/opencomputers/<drive address>/<dir>/`` (gos.toml
``[mailbox.lab]`` or ``[mailbox.play]``). GTNH sets ``bufferChanges=false``, so each side sees
the other's change on its next call [F§2.2, F§2.3]. Unmanaged drives and ``/tmp`` aren't folders,
so only a managed drive can hold a mailbox (the agent refuses the others).

- ``outbox.tmp`` (OC): the next message, written and closed, then renamed to ``outbox.json``.
- ``outbox.json`` (OC, then the core): OC renames a message there only while there is none, so it
  waits for the core to take the last one and never overwrites a message unread. The core reads
  it with ``safe_open`` (open, read, close), then renames it back to ``outbox.tmp``: the
  acknowledgement OC sees (``outbox.json`` gone).
- ``inbox.tmp`` (the core): the reply, flushed, fsynced and closed, then ``os.replace``-d to
  ``inbox.json``.
- ``inbox.json`` (the core, then OC): the core writes it only while there is none. OC reads it in
  2 KB chunks and deletes it, the acknowledgement the core sees. A reply OC hasn't read in
  :data:`REPLY_TTL_S` is withdrawn and its commands go back to the queue.

Why outbox.json goes back to outbox.tmp: OC's drive adds every byte OC writes to a new file to
its count and never learns of a file deleted or replaced from outside until the world loads
again [F§2.3]. OC writes the next message over the existing outbox.tmp, which it subtracts
first, so its count stays flat; with a new file every 5 s the drive would count itself full
within about an hour (``tests/oc/test_mailbox.lua`` shows both).

Each message carries the agent's ``boot`` and ``seq`` and each reply the ``boot`` and ``ack`` the
core has, so a missed or repeated read on either side is harmless (:mod:`gregtec.bridge.ingest`
dedupes; the agent never runs a command id twice). On Windows a file another program holds open
can't be replaced, renamed or deleted (OC's handles share read and write, not delete [F§2.5]):
each such step is retried on ``PermissionError`` and, if the file is still held, tried again on
the next poll with nothing changed.

The core writes nothing else in the world: inbox.tmp and inbox.json, and the rename of
outbox.json to outbox.tmp, in that one folder, each only after checking the folder is still the
one gos.toml names under the world's ``opencomputers`` folder, reached through no link or
junction (gos-dev can change Lab's worlds, D75), and never through a file that is a link.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gregtec.bridge.ingest import MAX_BODY, Bridge, Outcome, encode
from gregtec.config import Config
from gregtec.gamestate import safe_open
from gregtec.sync import _is_link
from gregtec.tweaks import GAME_DIRS

log = logging.getLogger("gregtec.bridge")

OUTBOX = "outbox.json"
OUTBOX_TMP = "outbox.tmp"
INBOX = "inbox.json"
INBOX_TMP = "inbox.tmp"
POLL_S = 0.25  # how often the core looks for outbox.json (a stat; OC waits up to 2 s for a reply)
REPLY_TTL_S = (
    30.0  # a reply OC hasn't read in this long is withdrawn (3 cycles at OC's slowest)
)
RETRIES = (
    6  # tries of a step Windows refuses while a file is held: 20 ms, doubling (0.6 s)
)
RETRY_FIRST_S = 0.02
HEARTBEAT_S = 300.0  # a stats line in the core's log every 5 minutes
WARN_EVERY_S = 60.0  # the same warning at most once a minute


@dataclass(frozen=True)
class Place:
    """Where one mailbox is: ``<instance>/<minecraft|.minecraft>/saves/<world>/opencomputers/
    <drive>/<dir>``."""

    role: str
    instance: Path
    world: str
    drive: str
    dir: str

    @property
    def source(self) -> str:
        """How messages from here are labelled in the ingest and the logs."""
        return f"mailbox:{self.role}/{self.world}/{self.drive}/{self.dir}"

    def world_dir(self) -> Path | None:
        """The world's folder (Prism's game folder is ``minecraft``, else ``.minecraft``)."""
        for name in GAME_DIRS:
            game = self.instance / name
            if game.is_dir():
                return game / "saves" / self.world
        return None

    def under(self, world: Path) -> Path:
        """The mailbox folder in ``world``."""
        return world.joinpath("opencomputers", self.drive, *self.dir.split("/"))


def _json_object(data: bytes) -> None:
    """``read_bytes``'s check: a whole JSON object, else retried (a partial write)."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as exc:  # RecursionError: nested too deep
        raise ValueError(
            f"outbox.json is not one whole JSON document ({type(exc).__name__})"
        ) from None
    if not isinstance(doc, dict):
        # ValueError, not TypeError: read_bytes retries a ValueError as a partial read.
        raise ValueError("outbox.json is not a JSON object")  # noqa: TRY004


@dataclass
class Mailbox:
    """One mailbox. :meth:`poll` does one pass; :class:`MailboxService` calls it every
    :data:`POLL_S` from one thread, so nothing here runs twice at once."""

    place: Place
    bridge: Bridge
    ttl_s: float = REPLY_TTL_S
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    stats: Counter[str] = field(default_factory=Counter)
    latest: Outcome | None = None  # the newest message's outcome
    due: bool = False  # a message taken since the last reply was written
    reply_at: float | None = None  # when this core wrote the reply now in inbox.json
    reply_ids: list[int] = field(default_factory=list)  # the commands in it
    max_age_s: float = (
        0.0  # the oldest message taken since the last heartbeat (now - mtime)
    )
    _state: str = "new"
    _warned: dict[str, float] = field(default_factory=dict)

    # --- one pass

    def poll(self) -> None:
        """Take a waiting message, then answer it when inbox.json is free (or withdraw a reply OC
        hasn't read in :attr:`ttl_s`)."""
        folder = self.folder()
        if folder is None:
            return
        self._take(folder)
        self._answer(folder)

    def folder(self) -> Path | None:
        """The mailbox folder, resolved, or None while it isn't there or a link leads elsewhere.

        Nothing between the instance folder and the mailbox folder may be a link or junction
        (the game folder, ``saves``, the world, ``opencomputers``, the drive, ``dir``): gos-dev
        can change Lab's folders (D75), so a link there could lead the core's writes anywhere.
        """
        world = self.place.world_dir()
        expected = self.place.under(world) if world is not None else self.place.instance
        try:
            if world is None:
                raise FileNotFoundError(expected)
            real_instance = self.place.instance.resolve(strict=True)
            real = expected.resolve(strict=True)
        except OSError:
            self._to(
                "missing",
                logging.INFO,
                "OC mailbox: waiting for its folder (the agent makes it when it starts)",
                path=str(expected),
            )
            return None
        want = real_instance / expected.relative_to(self.place.instance)
        if os.path.normcase(real) != os.path.normcase(want) or not real.is_dir():
            self._to(
                "refused",
                logging.ERROR,
                "OC mailbox: a link or junction leads its folder elsewhere; nothing read or written",
                path=str(expected),
                leads_to=str(real),
            )
            return None
        self._to("ready", logging.INFO, "OC mailbox ready", path=str(real))
        return real

    # --- OC -> core

    def _take(self, folder: Path) -> None:
        path = folder / OUTBOX
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            self._warn("stat", "OC mailbox: can't look at outbox.json", error=str(exc))
            return
        if _is_link(st) or not stat.S_ISREG(st.st_mode):
            self._warn(
                "link",
                "OC mailbox: outbox.json isn't a plain file; left alone",
                path=str(path),
            )
            return
        now = self.clock()
        source = self.place.source
        if st.st_size > MAX_BODY:
            outcome = self.bridge.refuse(
                "bad_payload", f"outbox.json is {st.st_size} bytes", source=source
            )
        else:
            try:
                raw = safe_open.read_bytes(
                    path, validate=_json_object, limit=MAX_BODY + 1
                )
            except FileNotFoundError:
                return
            except ValueError as exc:  # still not one whole JSON object after 3 reads
                outcome = self.bridge.refuse("bad_payload", str(exc), source=source)
            except OSError as exc:  # held by another program 3 times over: next poll
                self.stats["read_failures"] += 1
                self._warn(
                    "read", "OC mailbox: can't read outbox.json now", error=str(exc)
                )
                return
            else:
                outcome = self.bridge.receive(
                    raw,
                    transport="mailbox",
                    source=source,
                    written_at=st.st_mtime,
                    now=now,
                )
        if not self._acknowledge(folder):
            return  # outbox.json stays; the next poll reads it again (a repeat, D217)
        self.stats["taken"] += 1
        if not outcome.ok:
            self.stats[f"refused_{outcome.err}"] += 1
        elif outcome.repeat:
            self.stats["repeats"] += 1
        else:
            self.stats["accepted"] += 1
            self.stats["seq_gaps"] += outcome.gap
        self.max_age_s = max(self.max_age_s, now - st.st_mtime)
        self.latest = outcome
        self.due = True

    def _acknowledge(self, folder: Path) -> bool:
        """outbox.json back to outbox.tmp: OC sees it was taken, and its drive count stays flat."""
        if self.folder() != folder:
            return False

        def rename() -> None:
            with contextlib.suppress(
                FileNotFoundError
            ):  # gone already: the slot is free
                os.replace(folder / OUTBOX, folder / OUTBOX_TMP)

        return self._retry(rename, "rename outbox.json to outbox.tmp")

    # --- core -> OC

    def _answer(self, folder: Path) -> None:
        inbox = folder / INBOX
        try:
            st: os.stat_result | None = os.lstat(inbox)
        except FileNotFoundError:
            st = None
        except OSError as exc:
            self._warn("stat", "OC mailbox: can't look at inbox.json", error=str(exc))
            return
        if st is not None:  # OC hasn't taken the last reply yet
            written = self.reply_at if self.reply_at is not None else st.st_mtime
            if self.clock() - written >= self.ttl_s:
                self._withdraw(folder)
            return
        if self.reply_at is not None:  # OC read and deleted it
            self.stats["consumed"] += 1
            self.reply_at, self.reply_ids = None, []
        if not self.due or self.latest is None:
            return
        outcome = self.latest
        ack = (
            self.bridge.seqs.highest(outcome.agent)
            if outcome.ok and outcome.agent is not None
            else None
        )
        doc, ids = self.bridge.reply(outcome, ack=ack)
        if self.folder() != folder or not self._install(folder, encode(doc)):
            self.bridge.give_back(ids, "the core couldn't write inbox.json")
            return  # still due: the next poll tries again
        self.reply_at, self.reply_ids, self.due = self.clock(), ids, False
        self.stats["replies"] += 1
        self.stats["commands_sent"] += len(ids)

    def _install(self, folder: Path, data: bytes) -> bool:
        """inbox.tmp: write, flush, fsync, close; then ``os.replace`` to inbox.json."""
        tmp, target = folder / INBOX_TMP, folder / INBOX
        try:
            # A stale inbox.tmp (a crash before the replace) goes first; "x" never opens a file
            # through a link someone left in its place.
            if not self._retry(lambda: tmp.unlink(missing_ok=True), "remove inbox.tmp"):
                return False
            with open(tmp, "xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            self._warn("write", "OC mailbox: can't write inbox.tmp", error=str(exc))
            return False
        if self._retry(lambda: os.replace(tmp, target), "replace inbox.json"):
            return True
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False

    def _withdraw(self, folder: Path) -> None:
        """Take back a reply OC hasn't read in time; hand its commands back to the queue. If OC
        holds the file, it is reading it right now: it stays (and counts as delivered)."""
        inbox = folder / INBOX
        ids = self.reply_ids if self.reply_at is not None else _command_ids(inbox)
        if self.folder() != folder:
            return
        gone = False

        def remove() -> None:
            nonlocal gone
            try:
                os.remove(inbox)
            except FileNotFoundError:  # OC took it this very moment
                gone = True

        if not self._retry(remove, "withdraw inbox.json"):
            return
        if gone:
            self.stats["consumed"] += 1
        else:
            self.stats["withdrawn"] += 1
            self.stats["commands_returned"] += len(ids)
            log.warning(
                "OC mailbox: the agent didn't read its reply in time; withdrawn",
                extra={
                    "source": self.place.source,
                    "ttl_s": self.ttl_s,
                    "commands": ids,
                },
            )
            self.bridge.give_back(
                ids,
                f"withdrawn: the OC agent didn't read inbox.json within {self.ttl_s:g} s "
                "(the game paused or the agent stopped)",
            )
        self.reply_at, self.reply_ids = None, []

    # --- helpers

    def _retry(self, op: Callable[[], object], what: str) -> bool:
        """Run ``op``, retrying a ``PermissionError`` (Windows: the file is held open)."""
        for attempt in range(RETRIES):
            try:
                op()
                return True
            except PermissionError as exc:
                self.stats["permission_retries"] += 1
                if attempt == RETRIES - 1:
                    self._warn(
                        what,
                        "OC mailbox: a file is held open; trying again next poll",
                        step=what,
                        error=str(exc),
                    )
                    return False
                self.sleep(RETRY_FIRST_S * 2**attempt)
            except OSError as exc:
                self._warn(
                    what, "OC mailbox: a file step failed", step=what, error=str(exc)
                )
                return False
        return False

    def _to(self, state: str, level: int, msg: str, **extra: object) -> None:
        """Log a change of the folder's state once, not every poll."""
        if state != self._state:
            self._state = state
            log.log(level, msg, extra={"source": self.place.source, **extra})

    def _warn(self, key: str, msg: str, **extra: object) -> None:
        now = time.monotonic()
        if now - self._warned.get(key, -WARN_EVERY_S) >= WARN_EVERY_S:
            self._warned[key] = now
            log.warning(msg, extra={"source": self.place.source, **extra})

    def heartbeat(self) -> None:
        """One stats line in the core's log."""
        log.info(
            "OC mailbox stats",
            extra={
                "source": self.place.source,
                "state": self._state,
                "max_age_s": round(self.max_age_s, 3),
                **dict(sorted(self.stats.items())),
            },
        )
        self.max_age_s = 0.0


def _command_ids(inbox: Path) -> list[int]:
    """The command ids in a reply this core didn't write in this run (one from before a
    restart). It is the core's own file, read like any other world file."""
    try:
        doc = json.loads(safe_open.read_bytes(inbox, limit=MAX_BODY))
    except (OSError, ValueError):
        return []
    cmds = doc.get("cmds") if isinstance(doc, dict) else None
    if not isinstance(cmds, list):
        return []
    return [
        c["id"]
        for c in cmds
        if isinstance(c, dict)
        and isinstance(c.get("id"), int)
        and not isinstance(c["id"], bool)
    ]


class MailboxService:
    """Polls every configured mailbox from one daemon thread, from the core's start to its stop."""

    def __init__(self, boxes: Sequence[Mailbox], *, poll_s: float = POLL_S) -> None:
        self.boxes = list(boxes)
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_config(cls, cfg: Config, bridge: Bridge) -> MailboxService:
        """One :class:`Mailbox` per ``[mailbox.<role>]`` in gos.toml."""
        boxes = [
            Mailbox(
                Place(m.role, cfg.instance(m.role), m.world, m.drive, m.dir),
                bridge,
            )
            for m in cfg.mailboxes
        ]
        return cls(boxes)

    def start(self) -> None:
        if not self.boxes or self._thread is not None:
            return
        for box in self.boxes:
            log.info("OC mailbox watched", extra={"source": box.place.source})
        self._thread = threading.Thread(
            target=self._run, name="oc-mailbox", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop polling; each mailbox logs its stats one last time (the Lab soak reads them)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
            for box in self.boxes:
                box.heartbeat()

    def poll_all(self) -> None:
        """One pass over every mailbox; one mailbox's failure never stops the others."""
        for box in self.boxes:
            try:
                box.poll()
            except Exception:  # the loop must keep running
                box.stats["poll_errors"] += 1
                if (
                    box.stats["poll_errors"] <= 3
                    or box.stats["poll_errors"] % 1000 == 0
                ):
                    log.exception(
                        "OC mailbox poll failed", extra={"source": box.place.source}
                    )

    def _run(self) -> None:
        next_beat = time.monotonic() + HEARTBEAT_S
        while not self._stop.wait(self.poll_s):
            self.poll_all()
            if time.monotonic() >= next_beat:
                next_beat += HEARTBEAT_S
                for box in self.boxes:
                    box.heartbeat()
