"""WO-7.3: the M1 mailbox under concurrency, and the accelerated soak (plan 8's acceptance: an hour
in lab-ops with no lost or half-read files; D218).

:class:`LuaBox` is oc/mailbox.lua's ``send``/``receive``/``exchange`` step for step, on real files:
``exists`` to poll, outbox.tmp opened "w", written, closed, then renamed to outbox.json only while
there is none (a rename that fails is OC's ``false``, tried again 8 times); inbox.json's size
checked, read in 2048-byte chunks, closed, then removed (a failed remove is OC's ``false``). Its
writes to outbox.tmp go in several flushed pieces with a yield between them, a wider window than
OC's single write, so a core that read a file still being written would see it here.

A real :class:`MailboxService` thread polls the folder while the writer runs in another thread,
and the fake queue hands out commands as the action queue would (a returned command goes out
again under its id). At the end every message must have been recorded exactly once, in order,
with no gap and no refusal (a half-read file is a ``bad_payload``), every reply OC read must be
whole JSON whose ack never runs ahead of what OC sent, and every command must have reached OC.

``GOS_MAILBOX_SOAK_S`` (default 6) sets how long :func:`test_accelerated_soak` runs; it prints
its numbers (``pytest -s``). Run with 120 or more to stand for the hour: the agent's cycle is 5 s
(720 exchanges an hour) and here they run back to back.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gregtec.bridge import mailbox as mb
from gregtec.bridge.ingest import Bridge, Received
from gregtec.bridge.mailbox import Mailbox, MailboxService, Place

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oc"
DRIVE = "0f000000-0000-4000-8000-000000000003"
SOAK_S = float(os.environ.get("GOS_MAILBOX_SOAK_S", "6"))

# The real os functions, taken before any test patches os (the writer is "OC", not the core).
_exists, _getsize, _rename, _remove = (
    os.path.exists,
    os.path.getsize,
    os.rename,
    os.remove,
)


# --- OC's side, as oc/mailbox.lua does it


@dataclass
class LuaBox:
    """oc/mailbox.lua's Box on a real folder. ``scale`` shrinks its waits (POLL_S 0.25 s, the
    rename retry's 0.05 s, wait_s) so the run is accelerated, never its order of steps."""

    folder: Path
    wait_s: float = 2.0
    scale: float = 0.01
    reply_max: int = 8192
    rng: random.Random = field(default_factory=random.Random)
    n: Counter[str] = field(default_factory=Counter)
    pause_s: float = 0.0  # the next exchange stops this long between send and receive

    POLL_S = 0.25
    RENAME_TRIES = 8

    @property
    def outbox(self) -> str:
        return str(self.folder / "outbox.json")

    @property
    def tmp(self) -> str:
        return str(self.folder / "outbox.tmp")

    @property
    def inbox(self) -> str:
        return str(self.folder / "inbox.json")

    def sleep(self, s: float) -> None:
        time.sleep(s * self.scale)

    def send(self, body: bytes) -> tuple[bool, str | None]:
        deadline = time.monotonic() + self.wait_s * self.scale
        while _exists(self.outbox):
            if time.monotonic() >= deadline:
                self.n["full"] += 1
                return False, "the core hasn't taken the last message"
            self.sleep(self.POLL_S)
        with open(self.tmp, "wb") as h:  # filesystem.open(tmp, "w"): truncates
            cut = sorted(self.rng.sample(range(1, len(body)), 2))
            for a, b in zip([0, *cut], [*cut, len(body)], strict=True):
                h.write(body[a:b])
                h.flush()
                time.sleep(0)  # let the core run mid-write
        for i in range(1, self.RENAME_TRIES + 1):
            if self._rename(self.tmp, self.outbox):
                self.n["written"] += 1
                return True, None
            if i < self.RENAME_TRIES:
                self.n["rename_retries"] += 1
                self.sleep(0.05)
        return False, "can't rename outbox.tmp to outbox.json"

    @staticmethod
    def _rename(a: str, b: str) -> bool:
        """OC's rename: false when the target is there or the OS refuses (File.renameTo)."""
        if _exists(b):
            return False
        try:
            _rename(a, b)
        except OSError:
            return False
        return True

    def receive(self, wait: float) -> tuple[dict[str, Any] | None, str | None]:
        deadline = time.monotonic() + wait * self.scale
        while not _exists(self.inbox):
            if time.monotonic() >= deadline:
                return None, None
            self.sleep(self.POLL_S)
        try:
            size = _getsize(self.inbox)
        except OSError:
            size = 0  # filesystem.size of a file gone since exists: 0
        if size > self.reply_max:
            self._remove()
            return None, "inbox.json over reply_max"
        try:
            h = open(self.inbox, "rb")  # noqa: SIM115 (OC's handle, closed below)
        except OSError:
            return None, None  # gone since exists: the core withdrew it
        parts: list[bytes] = []
        total, over = 0, False
        with h:
            while chunk := h.read(2048):
                total += len(chunk)
                if total > self.reply_max:
                    over = True
                    break
                parts.append(chunk)
        if not self._remove():
            self.n["remove_failed"] += 1
        if over:
            return None, "inbox.json over reply_max"
        self.n["replies"] += 1
        try:
            reply = json.loads(b"".join(parts))
        except ValueError:
            self.n["bad_replies"] += 1
            return None, "bad reply in inbox.json"
        if not isinstance(reply, dict):
            self.n["bad_replies"] += 1
            return None, "bad reply in inbox.json"
        return reply, None

    def _remove(self) -> bool:
        try:
            _remove(self.inbox)
        except OSError:
            return False
        return True

    def exchange(self, body: bytes) -> tuple[dict[str, Any] | None, str | None]:
        reply, rerr = self.receive(0)
        sent, serr = self.send(body)
        if self.pause_s:  # the game paused mid-exchange: OC stops, the core doesn't
            time.sleep(self.pause_s)
            self.pause_s = 0.0
        if sent and reply is None and rerr is None:
            reply, rerr = self.receive(self.wait_s)
        if reply is not None:
            return reply, None
        if not sent:
            return None, serr
        if rerr:
            return None, rerr
        return {"v": 1, "ok": True, "cmds": []}, None


@dataclass
class Agent:
    """gos-agent's side of the run: a boot, seq 1, 2, ..., commands run once by id."""

    box: LuaBox
    boot: str
    rng: random.Random
    seq: int = 0
    sent: list[int] = field(default_factory=list)  # seqs the core should have
    ran: list[int] = field(default_factory=list)  # command ids, first time each
    repeats: int = 0  # a command id seen again (only after a withdrawal)
    acks: list[int] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)

    def body(self) -> bytes:
        doc = json.loads(
            (FIXTURES / "telemetry-first.json").read_text(encoding="utf-8")
        )
        machine = doc["machines"][0]
        doc.update(
            seq=self.seq,
            boot=self.boot,
            transport="mailbox",
            machines=[machine] * self.rng.randint(1, 64),  # ~0.6 to 20 KB
        )
        return json.dumps(doc).encode("utf-8")

    def cycle(self) -> None:
        self.seq += 1
        reply, err = self.box.exchange(self.body())
        if self.box.n["written"] > len(self.sent):
            self.sent.append(self.seq)
        if reply is None:
            self.errors[err or "?"] += 1
            return
        assert reply.get("ok") is True, reply
        if "ack" in reply:
            assert reply["boot"] == self.boot
            ack = int(reply["ack"])
            assert ack <= self.seq, "the core acked a message OC never sent"
            assert not self.acks or ack >= self.acks[-1], "acks go back"
            self.acks.append(ack)
        for cmd in reply["cmds"]:
            if cmd["id"] in self.ran:
                self.repeats += 1
            else:
                self.ran.append(cmd["id"])


# --- the core's side


class Recording:
    def __init__(self) -> None:
        self.got: list[Received] = []

    def ingest(self, msg: Received) -> str | None:
        self.got.append(msg)
        return None


class Queue:
    """The action queue's part (WO-7.1): new commands now and then; returned ones go out again
    under the same id."""

    def __init__(self, rng: random.Random, rate: float = 0.3) -> None:
        self.rng, self.rate = rng, rate
        self.lock = threading.Lock()
        self.waiting: list[int] = []
        self.next_id = 1
        self.given: set[int] = set()
        self.back: list[int] = []
        self.open = True

    def take(self, agent: str, limit: int) -> list[dict[str, Any]]:
        with self.lock:
            if self.open and self.rng.random() < self.rate:
                for _ in range(self.rng.randint(1, 3)):
                    self.waiting.append(self.next_id)
                    self.next_id += 1
            out, self.waiting = self.waiting[:limit], self.waiting[limit:]
            self.given.update(out)
            return [
                {
                    "id": i,
                    "kind": "say",
                    "args": {"text": f"#{i}"},
                    "confirmed_via": "voice",
                }
                for i in out
            ]

    def returned(self, ids: Sequence[int], why: str) -> None:
        with self.lock:
            self.back.extend(ids)
            self.waiting = list(ids) + self.waiting


def layout(tmp_path: Path) -> tuple[Place, Path]:
    instance = tmp_path / "GTNH-Lab"
    folder = (
        instance / ".minecraft" / "saves" / "lab-ops" / "opencomputers" / DRIVE
    ).joinpath("home", "gos", "mailbox")
    folder.mkdir(parents=True)
    return Place("lab", instance, "lab-ops", DRIVE, "home/gos/mailbox"), folder


@dataclass
class Run:
    agent: Agent
    box: Mailbox
    sink: Recording
    queue: Queue
    seconds: float
    folder: Path


def run(
    tmp_path: Path,
    *,
    seconds: float,
    seed: int,
    ttl_s: float = mb.REPLY_TTL_S,
    stall_every: int = 0,
    rate: float = 0.3,
    scale: float = 0.01,
) -> Run:
    """The writer and a real MailboxService for ``seconds``, then a drain: the queue closes and
    the agent keeps cycling until every command given out has reached it."""
    place, folder = layout(tmp_path)
    rng = random.Random(seed)
    sink, queue = Recording(), Queue(random.Random(seed + 1), rate)
    box = Mailbox(place, Bridge(sink, queue), ttl_s=ttl_s)
    service = MailboxService([box], poll_s=0.001)
    agent = Agent(
        LuaBox(folder, scale=scale, rng=random.Random(seed + 2)),
        f"{rng.getrandbits(32):08x}",
        random.Random(seed + 3),
    )
    service.start()
    start = time.monotonic()
    try:
        while time.monotonic() - start < seconds:
            if stall_every and agent.seq % stall_every == stall_every - 1:
                agent.box.pause_s = ttl_s * rng.uniform(2.0, 4.0)
            agent.cycle()
        queue.open = False
        for _ in range(2000):
            with queue.lock:
                pending = queue.waiting or box.reply_ids
            if not pending and set(agent.ran) >= queue.given:
                break
            agent.cycle()
        # Let the core take the last message, so recorded == sent.
        deadline = time.monotonic() + 5
        while _exists(folder / "outbox.json") and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        service.stop()
    elapsed = time.monotonic() - start
    return Run(agent, box, sink, queue, elapsed, folder)


def check(r: Run) -> None:
    """No lost message, no half-read file, nothing recorded twice, every command delivered."""
    stats, agent = r.box.stats, r.agent
    got = [m.seq for m in r.sink.got]
    assert got == agent.sent, "every message written was recorded once, in order"
    assert len(agent.sent) == agent.box.n["written"]
    assert all(m.boot == agent.boot for m in r.sink.got)
    refused = {k: v for k, v in stats.items() if k.startswith("refused_")}
    assert refused == {}, "a half-read outbox.json is a bad_payload"
    # A gap is only a seq the agent counted but couldn't write (the core still held the last).
    unwritten = sum(b - a - 1 for a, b in zip(agent.sent, agent.sent[1:]))
    assert stats["seq_gaps"] == unwritten
    assert stats["accepted"] == len(got)
    assert stats["taken"] == stats["accepted"] + stats["repeats"]
    assert stats["poll_errors"] == 0
    assert agent.box.n["bad_replies"] == 0, "a half-read inbox.json"
    assert set(agent.ran) == r.queue.given, "every command reached OC"
    assert len(agent.ran) == len(set(agent.ran))
    if stats["withdrawn"] == 0:
        assert agent.repeats == 0
    assert agent.acks and agent.acks[-1] <= agent.sent[-1]


def numbers(r: Run) -> dict[str, Any]:
    s, n = r.box.stats, r.agent.box.n
    return {
        "seconds": round(r.seconds, 1),
        "exchanges": r.agent.seq,
        "written": n["written"],
        "recorded": len(r.sink.got),
        "replies_written": s["replies"],
        "replies_read": n["replies"],
        "withdrawn": s["withdrawn"],
        "commands": len(r.queue.given),
        "commands_returned": len(r.queue.back),
        "command_repeats_at_oc": r.agent.repeats,
        "waits_for_core": n["full"],
        "oc_rename_retries": n["rename_retries"],
        "core_permission_retries": s["permission_retries"],
        "repeats_read": s["repeats"],
        "half_read": s.get("refused_bad_payload", 0) + n["bad_replies"],
        "lost": len(r.agent.sent) - len(r.sink.got),
        "agent_errors": dict(r.agent.errors),
    }


@pytest.mark.parametrize("seed", range(3))
def test_concurrent_writer_and_core(tmp_path: Path, seed: int) -> None:
    r = run(tmp_path, seconds=1.5, seed=seed)
    check(r)
    assert r.agent.seq > 20  # Windows runners sleep coarsely
    assert r.box.stats["withdrawn"] == 0


def test_withdrawn_replies_under_concurrency(tmp_path: Path) -> None:
    """A short TTL and an agent that stalls: replies are withdrawn and their commands sent again
    under their ids; OC runs each id once, and still nothing is lost or half-read."""
    r = run(tmp_path, seconds=2.0, seed=7, ttl_s=0.05, stall_every=10, rate=1.0)
    check(r)
    assert r.box.stats["withdrawn"] >= 1
    # Every reply carried commands (rate 1), so every withdrawal handed some back.
    assert r.queue.back, "withdrawn commands went back to the queue"
    assert len(r.queue.back) == r.box.stats["commands_returned"]


def test_held_files_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core's renames and replaces fail now and then as on Windows with a file held open:
    it retries, or leaves the step to the next poll, and nothing is lost or read twice."""
    rng = random.Random(11)
    real_replace = os.replace

    def flaky(a: str | Path, b: str | Path) -> None:
        if rng.random() < 0.2:
            raise PermissionError(13, "held by another program")
        real_replace(a, b)

    monkeypatch.setattr(mb.os, "replace", flaky)
    monkeypatch.setattr(mb, "RETRY_FIRST_S", 0.0005)
    r = run(tmp_path, seconds=1.5, seed=3)
    check(r)
    assert r.box.stats["permission_retries"] > 0


def test_accelerated_soak(tmp_path: Path) -> None:
    """The acceptance soak, accelerated (D218): back-to-back exchanges with the real service.
    ``GOS_MAILBOX_SOAK_S`` sets its length; 120 s or more stands for the hour's 720 exchanges
    many times over."""
    r = run(tmp_path, seconds=SOAK_S, seed=2026, scale=0.02)
    check(r)
    out = numbers(r)
    print("\nM1 soak:", json.dumps(out, sort_keys=True))
    assert out["lost"] == 0 and out["half_read"] == 0
    assert not (r.folder / "inbox.tmp").exists()
