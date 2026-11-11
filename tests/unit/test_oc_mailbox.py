"""WO-7.3: the core's side of the M1 file mailbox (gregtec.bridge.mailbox, D216-D218), in temp
folders laid out as an instance's world: <instance>/.minecraft/saves/<world>/opencomputers/
<drive>/<dir>. The OC side's steps are done here the way oc/mailbox.lua does them."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from gregtec import config
from gregtec.bridge import mailbox as mb
from gregtec.bridge.ingest import MAX_BODY, AgentId, Bridge, Latest, Received
from gregtec.bridge.mailbox import Mailbox, MailboxService, Place

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oc"
DRIVE = "0f000000-0000-4000-8000-000000000003"
AGENT = "c0c0c0c0-0000-4000-8000-000000000001"
WINDOWS = sys.platform == "win32"


def message(seq: int = 1, boot: str = "b0075eed") -> dict[str, Any]:
    doc = json.loads((FIXTURES / "telemetry-first.json").read_text(encoding="utf-8"))
    doc.update(seq=seq, boot=boot, transport="mailbox")
    return doc


class Recording:
    def __init__(self) -> None:
        self.got: list[Received] = []

    def ingest(self, msg: Received) -> str | None:
        self.got.append(msg)
        return None


class Queue:
    def __init__(self, cmds: Sequence[dict[str, Any]] = ()) -> None:
        self.cmds = list(cmds)
        self.back: list[tuple[list[int], str]] = []

    def take(self, agent: AgentId, limit: int) -> list[dict[str, Any]]:
        out, self.cmds = self.cmds[:limit], self.cmds[limit:]
        return out

    def returned(self, ids: Sequence[int], why: str) -> None:
        self.back.append((list(ids), why))


def say(cid: int) -> dict[str, Any]:
    return {"id": cid, "kind": "say", "args": {"text": "hi"}, "confirmed_via": "voice"}


class Clock:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t


def layout(
    tmp_path: Path, game: str = ".minecraft", sub: str = "home/gos/mailbox"
) -> tuple[Place, Path]:
    instance = tmp_path / "GTNH-Lab"
    world = instance / game / "saves" / "lab-ops"
    folder = world.joinpath("opencomputers", DRIVE, *sub.split("/"))
    folder.mkdir(parents=True)
    return Place("lab", instance, "lab-ops", DRIVE, sub), folder


def make_box(
    place: Place,
    *,
    sink: Recording | None = None,
    queue: Queue | None = None,
    clock: Clock | None = None,
    sleeps: list[float] | None = None,
) -> Mailbox:
    def sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)

    return Mailbox(
        place,
        Bridge(sink or Recording(), queue),
        clock=clock or Clock(),
        sleep=sleep,
    )


def oc_send(folder: Path, doc: dict[str, Any] | bytes) -> None:
    """oc/mailbox.lua's send: outbox.tmp, closed, renamed to outbox.json (none there)."""
    assert not (folder / "outbox.json").exists(), "OC never overwrites a message"
    body = doc if isinstance(doc, bytes) else json.dumps(doc).encode("utf-8")
    (folder / "outbox.tmp").write_bytes(body)
    os.rename(folder / "outbox.tmp", folder / "outbox.json")


def oc_receive(folder: Path) -> dict[str, Any] | None:
    """oc/mailbox.lua's receive: read inbox.json, then delete it."""
    inbox = folder / "inbox.json"
    if not inbox.exists():
        return None
    doc = json.loads(inbox.read_bytes())
    inbox.unlink()
    return doc


def reply_at(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "inbox.json").read_bytes())


@pytest.fixture
def fail_os(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """fail_os(name, match, times, exc): os.<name> raises exc for calls whose first path ends
    with match, `times` times (None: always). Returns the list of matched calls."""

    def install(
        name: str, match: str, times: int | None, exc: type[OSError] = PermissionError
    ) -> list[str]:
        real = getattr(os, name)
        seen: list[str] = []

        def fake(*args: object, **kw: object) -> object:
            if str(args[0]).endswith(match):
                seen.append(str(args[0]))
                if times is None or len(seen) <= times:
                    raise exc(
                        13 if exc is PermissionError else 2, "held by another program"
                    )
            return real(*args, **kw)

        monkeypatch.setattr(os, name, fake)
        return seen

    return install


# --- the round trip


def test_a_message_taken_and_answered(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    sink = Recording()
    box = make_box(place, sink=sink)
    oc_send(folder, message(seq=1))
    sent = (folder / "outbox.json").read_bytes()
    box.poll()
    assert not (folder / "outbox.json").exists()
    assert (folder / "outbox.tmp").read_bytes() == sent, "renamed back, not deleted"
    assert reply_at(folder) == {
        "ack": 1,
        "boot": "b0075eed",
        "cmds": [],
        "ok": True,
        "v": 1,
    }
    assert (
        folder / "inbox.json"
    ).read_bytes() == b'{"ack":1,"boot":"b0075eed","cmds":[],"ok":true,"v":1}'
    assert not (folder / "inbox.tmp").exists()
    (msg,) = sink.got
    assert (msg.agent, msg.seq, msg.transport, msg.source) == (
        AgentId("lab", AGENT),
        1,
        "mailbox",
        place.source,
    )
    assert msg.written_at is not None
    assert dict(box.stats) == {
        "taken": 1,
        "accepted": 1,
        "seq_gaps": 0,
        "replies": 1,
        "commands_sent": 0,
    }
    box.poll()  # nothing new: nothing changes
    assert box.stats["replies"] == 1


def test_the_core_writes_inbox_json_only_while_there_is_none(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    box = make_box(place)
    oc_send(folder, message(seq=1))
    box.poll()
    oc_send(folder, message(seq=2))  # OC sent again without reading the reply yet
    box.poll()
    assert not (folder / "outbox.json").exists(), "message 2 taken"
    assert reply_at(folder)["ack"] == 1, "the unread reply is never replaced"
    assert oc_receive(folder) == {
        "ack": 1,
        "boot": "b0075eed",
        "cmds": [],
        "ok": True,
        "v": 1,
    }
    box.poll()
    assert reply_at(folder)["ack"] == 2, "the next reply carries the newest ack"
    assert box.stats["consumed"] == 1 and box.stats["replies"] == 2


def test_commands_ride_on_the_reply(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    queue = Queue([say(5), say(6)])
    box = make_box(place, queue=queue)
    oc_send(folder, message())
    box.poll()
    assert [c["id"] for c in reply_at(folder)["cmds"]] == [5, 6]
    assert box.reply_ids == [5, 6]
    assert box.stats["commands_sent"] == 2


def test_a_message_read_twice_is_recorded_once(
    tmp_path: Path, fail_os: Callable[..., list[str]]
) -> None:
    place, folder = layout(tmp_path)
    sink = Recording()
    box = make_box(place, sink=sink)
    oc_send(folder, message())
    seen = fail_os("replace", "outbox.json", mb.RETRIES)
    box.poll()
    assert len(seen) == mb.RETRIES
    assert (folder / "outbox.json").exists(), "the rename back failed: it stays"
    assert not (folder / "inbox.json").exists(), "no reply before the message is taken"
    box.poll()
    assert not (folder / "outbox.json").exists()
    assert len(sink.got) == 1, "the second read was a repeat"
    assert box.stats["repeats"] == 1 and box.stats["taken"] == 1
    assert reply_at(folder)["ack"] == 1


def test_a_half_written_outbox_is_read_three_times_then_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    place, folder = layout(tmp_path)
    naps: list[float] = []
    monkeypatch.setattr(mb.safe_open.time, "sleep", naps.append)
    box = make_box(place)
    oc_send(folder, b'{"v":1,"kind":"telem')
    box.poll()
    assert naps == [0.2, 0.4], "copy then parse, retry x3 (CLAUDE.md)"
    assert reply_at(folder) == {"v": 1, "ok": False, "cmds": [], "err": "bad_payload"}
    assert (folder / "outbox.tmp").exists(), "taken all the same, so OC can send again"
    assert box.stats["refused_bad_payload"] == 1


def test_an_outbox_over_256_kb_is_refused_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    place, folder = layout(tmp_path)

    def never(*_: object, **__: object) -> bytes:
        raise AssertionError("read")

    monkeypatch.setattr(mb.safe_open, "read_bytes", never)
    box = make_box(place)
    oc_send(folder, b" " * (MAX_BODY + 1))
    box.poll()
    assert reply_at(folder)["err"] == "bad_payload"


def test_an_outbox_nested_too_deep_is_refused_not_stuck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """json.loads raises RecursionError, not ValueError, past Python's recursion limit: it is a
    bad payload like any other, taken so OC can send again (never a poll that fails forever)."""
    place, folder = layout(tmp_path)
    monkeypatch.setattr(mb.safe_open.time, "sleep", lambda _s: None)
    box = make_box(place)
    oc_send(folder, b'{"v":1,"x":' + b"[" * 100_000 + b"]" * 100_000 + b"}")
    box.poll()
    assert reply_at(folder)["err"] == "bad_payload"
    assert not (folder / "outbox.json").exists()
    assert box.stats["refused_bad_payload"] == 1


def test_a_message_of_another_contract_version(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    box = make_box(place)
    doc = message()
    doc["v"] = 2
    oc_send(folder, doc)
    box.poll()
    assert reply_at(folder) == {
        "v": 1,
        "ok": False,
        "cmds": [],
        "err": "unsupported_version",
    }


# --- withdrawing a reply OC didn't read


def test_an_unread_reply_is_withdrawn_and_its_commands_returned(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    clock, queue = Clock(), Queue([say(5)])
    box = make_box(place, queue=queue, clock=clock)
    oc_send(folder, message(seq=1))
    box.poll()
    clock.t += mb.REPLY_TTL_S - 1
    box.poll()
    assert (folder / "inbox.json").exists(), "not yet"
    clock.t += 1
    box.poll()
    assert not (folder / "inbox.json").exists()
    why = (
        "withdrawn: the OC agent didn't read inbox.json within 30 s (the game paused or "
        "the agent stopped)"
    )
    assert queue.back == [([5], why)]
    assert box.stats["withdrawn"] == 1 and box.stats["commands_returned"] == 1
    oc_send(folder, message(seq=2))
    box.poll()
    assert not (folder / "inbox.json").exists(), (
        "quiet after a withdrawal: OC may have been reading the withdrawn reply"
    )
    assert box.due
    clock.t += mb.QUIET_S
    box.poll()
    assert reply_at(folder) == {
        "ack": 2,
        "boot": "b0075eed",
        "cmds": [],
        "ok": True,
        "v": 1,
    }


def test_a_reply_from_before_a_restart_is_withdrawn_by_its_age(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    inbox = folder / "inbox.json"
    inbox.write_bytes(
        json.dumps(
            {"v": 1, "ok": True, "cmds": [say(9)], "boot": "b0075eed", "ack": 4}
        ).encode()
    )
    queue = Queue()
    box = make_box(place, queue=queue)
    box.poll()
    assert inbox.exists(), "written just now: OC may still read it"
    old = time.time() - 100
    os.utime(inbox, (old, old))
    box.poll()
    assert not inbox.exists()
    assert queue.back and queue.back[0][0] == [9]


def test_a_reply_oc_takes_as_it_is_withdrawn_counts_as_read(
    tmp_path: Path, fail_os: Callable[..., list[str]]
) -> None:
    place, folder = layout(tmp_path)
    clock, queue = Clock(), Queue([say(5)])
    box = make_box(place, queue=queue, clock=clock)
    oc_send(folder, message())
    box.poll()
    clock.t += mb.REPLY_TTL_S
    fail_os("remove", "inbox.json", None, FileNotFoundError)
    box.poll()
    assert queue.back == []
    assert box.stats["consumed"] == 1 and box.stats["withdrawn"] == 0


# --- Windows: a file another program holds open (retried, then left for the next poll)


def test_permission_errors_are_retried(
    tmp_path: Path, fail_os: Callable[..., list[str]]
) -> None:
    place, folder = layout(tmp_path)
    sleeps: list[float] = []
    box = make_box(place, sleeps=sleeps)
    oc_send(folder, message())
    fail_os("replace", "inbox.tmp", 2)
    box.poll()
    assert reply_at(folder)["ack"] == 1
    assert sleeps == [0.02, 0.04]
    assert box.stats["permission_retries"] == 2


def test_a_file_held_too_long_waits_for_the_next_poll(
    tmp_path: Path, fail_os: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    place, folder = layout(tmp_path)
    queue = Queue([say(5)])
    box = make_box(place, queue=queue)
    oc_send(folder, message())
    seen = fail_os("replace", "inbox.tmp", None)
    with caplog.at_level(logging.WARNING, logger="gregtec.bridge"):
        box.poll()
        box.poll()
    assert len(seen) == 2 * mb.RETRIES
    assert not (folder / "inbox.json").exists()
    assert not (folder / "inbox.tmp").exists(), "no tmp left behind"
    assert queue.back[0] == ([5], "the core couldn't write inbox.json")
    assert caplog.text.count("a file is held open") == 1, "warned once a minute"
    assert box.due, "the reply is still owed"


def test_a_stale_inbox_tmp_is_replaced(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    (folder / "inbox.tmp").write_bytes(b"left by a crash")
    box = make_box(place)
    oc_send(folder, message())
    box.poll()
    assert reply_at(folder)["ack"] == 1
    assert not (folder / "inbox.tmp").exists()


@pytest.mark.skipif(WINDOWS, reason="symlinks need a privilege on Windows")
def test_never_writes_through_a_link(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    (folder / "inbox.tmp").symlink_to(victim)
    box = make_box(place)
    oc_send(folder, message())
    box.poll()
    assert victim.read_text(encoding="utf-8") == "keep"
    assert reply_at(folder)["ack"] == 1


@pytest.mark.skipif(WINDOWS, reason="symlinks need a privilege on Windows")
def test_an_outbox_that_is_a_link_is_left_alone(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    secret = tmp_path / "core.json"
    secret.write_text('{"token": "x"}', encoding="utf-8")
    (folder / "outbox.json").symlink_to(secret)
    sink = Recording()
    box = make_box(place, sink=sink)
    box.poll()
    assert sink.got == []
    assert (folder / "outbox.json").is_symlink(), "not renamed either"
    assert not (folder / "inbox.json").exists()


def _link_dir(link: Path, target: Path) -> None:
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("level", [3, 5, 6])  # the drive, the world, saves/
def test_a_folder_reached_through_a_link_or_junction_is_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, level: int
) -> None:
    """No link or junction anywhere below the instance folder: gos-dev can change Lab's
    folders (D75), and a link would lead the core's writes elsewhere (D217)."""
    place, folder = layout(tmp_path)
    linked = folder.parents[level - 1]
    elsewhere = tmp_path / "elsewhere"
    moved = elsewhere / folder.relative_to(linked)
    moved.mkdir(parents=True)
    oc_send(moved, message())
    for child in sorted(linked.rglob("*"), reverse=True):
        child.rmdir()
    linked.rmdir()
    _link_dir(linked, elsewhere)
    assert folder.resolve() == moved.resolve()
    sink = Recording()
    box = make_box(place, sink=sink)
    with caplog.at_level(logging.INFO, logger="gregtec.bridge"):
        box.poll()
        box.poll()
    assert sink.got == []
    assert (moved / "outbox.json").exists()
    assert not (moved / "inbox.json").exists()
    assert caplog.text.count("leads its folder elsewhere") == 1


def test_a_missing_folder_is_waited_for_quietly(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    place, folder = layout(tmp_path)
    folder.rmdir()
    box = make_box(place)
    with caplog.at_level(logging.INFO, logger="gregtec.bridge"):
        for _ in range(5):
            box.poll()
        folder.mkdir()
        box.poll()
    assert caplog.text.count("waiting for its folder") == 1
    assert caplog.text.count("OC mailbox ready") == 1


def test_the_world_is_found_in_minecraft_or_dot_minecraft(tmp_path: Path) -> None:
    place, folder = layout(tmp_path, game="minecraft")
    assert (
        place.world_dir() == tmp_path / "GTNH-Lab" / "minecraft" / "saves" / "lab-ops"
    )
    assert place.under(place.world_dir() or tmp_path) == folder
    assert Place("lab", tmp_path / "nowhere", "w", DRIVE, "x").world_dir() is None


# --- the service


def test_the_service_polls_until_stopped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    place, folder = layout(tmp_path)
    box = Mailbox(place, Bridge(Recording()))
    service = MailboxService([box], poll_s=0.01)
    service.start()
    try:
        oc_send(folder, message())
        deadline = time.monotonic() + 5
        while not (folder / "inbox.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert reply_at(folder)["ack"] == 1
    finally:
        with caplog.at_level(logging.INFO, logger="gregtec.bridge"):
            service.stop()
    assert service._thread is None
    (last,) = [r for r in caplog.records if r.getMessage() == "OC mailbox stats"]
    assert (last.taken, last.accepted, last.replies) == (1, 1, 1), "the final count"


def test_one_failing_mailbox_doesnt_stop_the_others(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    good = make_box(place)

    class Broken(Mailbox):
        def poll(self) -> None:
            raise RuntimeError("boom")

    bad = Broken(place, Bridge())
    service = MailboxService([bad, good])
    oc_send(folder, message())
    service.poll_all()
    assert bad.stats["poll_errors"] == 1
    assert reply_at(folder)["ack"] == 1


def test_no_mailboxes_no_thread() -> None:
    service = MailboxService([])
    service.start()
    assert service._thread is None
    service.stop()


# --- gos.toml


def _cfg(tmp_path: Path, text: str) -> config.Config:
    path = tmp_path / "gos.toml"
    path.write_text(text, encoding="utf-8")
    return config.load(path)


def test_mailbox_config(tmp_path: Path) -> None:
    cfg = _cfg(
        tmp_path,
        f"""
[paths]
prism_instances = "{tmp_path.as_posix()}"
lab_world = "lab-ops"
world = "survival"

[mailbox.lab]
drive = "{DRIVE}"

[mailbox.play]
drive = "{DRIVE}"
dir = "gos"
world = "Other World"
""",
    )
    assert cfg.service_errors == ()
    assert cfg.mailboxes == (
        config.OcMailbox("lab", "lab-ops", DRIVE, "home/gos/mailbox"),
        config.OcMailbox("play", "Other World", DRIVE, "gos"),
    )
    service = MailboxService.from_config(cfg, Bridge())
    assert [b.place.instance for b in service.boxes] == [
        tmp_path / "GTNH-Lab",
        tmp_path / "GTNH-Play",
    ]
    assert (
        service.boxes[0].place.source == f"mailbox:lab/lab-ops/{DRIVE}/home/gos/mailbox"
    )


@pytest.mark.parametrize(
    ("table", "why"),
    [
        ('[mailbox.export]\ndrive = "{d}"', "play or lab"),
        ('[mailbox.lab]\nworld = "lab-ops"', "drive must be the OC drive's address"),
        ('[mailbox.lab]\nworld = "lab-ops"\ndrive = "C0FFEE"', "drive must be"),
        (
            '[mailbox.lab]\nworld = "lab-ops"\ndrive = "{d}"\ndir = "../escape"',
            "dir must be",
        ),
        (
            '[mailbox.lab]\nworld = "lab-ops"\ndrive = "{d}"\ndir = "/home/gos"',
            "dir must be",
        ),
        (
            '[mailbox.lab]\nworld = "lab-ops"\ndrive = "{d}"\ndir = "home\\\\gos"',
            "dir must be",
        ),
        ('[mailbox.lab]\ndrive = "{d}"', "world must be"),
        ('[mailbox.lab]\nworld = "../Play"\ndrive = "{d}"', "world must be"),
        (
            '[mailbox.lab]\nworld = "lab-ops"\ndrive = "{d}"\npath = "x"',
            "unknown keys path",
        ),
        ('mailbox = "lab"', "mailbox must be a table"),
    ],
)
def test_bad_mailbox_config_is_a_service_problem(
    tmp_path: Path, table: str, why: str
) -> None:
    """A bad table is a service problem: like any service key, ``gos serve`` refuses to start
    with the reason in the log, and ``gos doctor`` names it (review M2, D217)."""
    cfg = _cfg(tmp_path, table.replace("{d}", DRIVE) + "\n")
    assert cfg.mailboxes == ()
    assert any(why in e for e in cfg.service_errors), cfg.service_errors
    assert any(why in e for e in config.service_problems(cfg))


def test_gos_dev_may_not_watch_plays_mailbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "running_as_dev", lambda: True)
    cfg = _cfg(
        tmp_path,
        f"""
[core]
port = 8775
supervisor_port = 8776
dashboard_host = "gos-dev.localhost"

[mailbox.play]
world = "survival"
drive = "{DRIVE}"
""",
    )
    problems = config.service_problems(cfg)
    assert len(problems) == 1 and "[mailbox.play] must not be set" in problems[0]
    lab = _cfg(
        tmp_path,
        f"""
[core]
port = 8775
supervisor_port = 8776
dashboard_host = "gos-dev.localhost"

[mailbox.lab]
world = "lab-ops"
drive = "{DRIVE}"
""",
    )
    assert config.service_problems(lab) == []


# --- Windows CI: real held handles (OC's RandomAccessFile shares read and write, not delete,
# and so does a plain open() here)


def _hold(path: Path, seconds: float) -> threading.Thread:
    """Hold ``path`` open like OC does, from another thread, for ``seconds``."""
    ready = threading.Event()

    def run() -> None:
        with open(path, "rb"):
            ready.set()
            time.sleep(seconds)

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(5)
    return thread


@pytest.mark.skipif(not WINDOWS, reason="Windows sharing semantics")
def test_windows_the_reply_waits_for_ocs_handle(tmp_path: Path) -> None:
    """The plan's case: os.replace onto inbox.json while OC reads it raises PermissionError
    (MoveFileExW refuses while any handle is open, D84); the core retries until OC lets go."""
    place, folder = layout(tmp_path)
    box = Mailbox(place, Bridge(Recording()))
    target = folder / "inbox.json"
    target.write_bytes(b"old")
    holder = _hold(target, 0.15)
    try:
        assert box._install(folder, b"new")
    finally:
        holder.join()
    assert target.read_bytes() == b"new"
    assert box.stats["permission_retries"] >= 1


@pytest.mark.skipif(not WINDOWS, reason="Windows sharing semantics")
def test_windows_a_reply_oc_is_reading_isnt_withdrawn(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    clock, queue = Clock(), Queue([say(5)])
    box = Mailbox(place, Bridge(Recording(), queue), clock=clock)
    oc_send(folder, message())
    box.poll()
    clock.t += mb.REPLY_TTL_S
    holder = _hold(folder / "inbox.json", 1.5)
    try:
        box.poll()
        assert (folder / "inbox.json").exists(), "OC is reading it: it stays"
        assert queue.back == []
    finally:
        holder.join()
    box.poll()
    assert not (folder / "inbox.json").exists()
    assert queue.back and queue.back[0][0] == [5]


@pytest.mark.skipif(not WINDOWS, reason="Windows sharing semantics")
def test_windows_the_rename_back_waits_for_a_held_outbox_tmp(tmp_path: Path) -> None:
    place, folder = layout(tmp_path)
    box = Mailbox(place, Bridge(Recording()))
    oc_send(folder, message())
    (folder / "outbox.tmp").write_bytes(b"an antivirus scans the old one")
    holder = _hold(folder / "outbox.tmp", 1.5)
    try:
        box.poll()
        assert (folder / "outbox.json").exists(), "not taken while outbox.tmp is held"
        assert box.stats["taken"] == 0
    finally:
        holder.join()
    box.poll()
    assert not (folder / "outbox.json").exists()
    assert box.stats["taken"] == 1 and box.stats["repeats"] == 1


@pytest.mark.skipif(not WINDOWS, reason="Windows sharing semantics")
def test_windows_the_core_reads_outbox_while_another_program_holds_it(
    tmp_path: Path,
) -> None:
    """safe_open shares read, write and delete, so a reader OC or an antivirus left open doesn't
    stop the core reading; the rename back waits until it lets go."""
    place, folder = layout(tmp_path)
    sink = Recording()
    box = Mailbox(place, Bridge(sink))
    oc_send(folder, message())
    holder = _hold(folder / "outbox.json", 0.3)
    try:
        box.poll()
    finally:
        holder.join()
    assert len(sink.got) == 1
    box.poll()
    assert not (folder / "outbox.json").exists()


# --- one address, two mailboxes (review M1, D217)


class PerAgent:
    """A command queue keyed the way WO-7.1's is: by the agent (binding and address)."""

    def __init__(self, cmds: dict[AgentId, list[dict[str, Any]]]) -> None:
        self.cmds = cmds
        self.back: list[list[int]] = []

    def take(self, agent: AgentId, limit: int) -> list[dict[str, Any]]:
        out = self.cmds.get(agent, [])[:limit]
        self.cmds[agent] = self.cmds.get(agent, [])[limit:]
        return out

    def returned(self, ids: Sequence[int], why: str) -> None:
        self.back.append(list(ids))


def test_lab_naming_plays_computer_is_a_different_agent(tmp_path: Path) -> None:
    """Lab runs any Lua (D75), and a Lab world copied from Play has Play's addresses: a Lab
    message claiming Play's computer never dedupes against Play's messages, replaces Play's
    latest message or takes Play's commands."""
    lab_place, lab = layout(tmp_path)
    play_instance = tmp_path / "GTNH-Play"
    play = play_instance.joinpath(
        "minecraft", "saves", "main", "opencomputers", DRIVE, "home", "gos", "mailbox"
    )
    play.mkdir(parents=True)
    play_place = Place("play", play_instance, "main", DRIVE, "home/gos/mailbox")
    sink = Latest()
    queue = PerAgent({AgentId("play", AGENT): [say(41)]})
    bridge = Bridge(sink, queue)
    clock = Clock()
    boxes = [Mailbox(p, bridge, clock=clock) for p in (play_place, lab_place)]
    service = MailboxService(boxes)

    oc_send(play, message(seq=5))
    oc_send(lab, message(seq=1_000_000))  # Play's address and boot, a seq far ahead
    service.poll_all()
    lab_reply, play_reply = oc_receive(lab), oc_receive(play)
    assert lab_reply is not None and play_reply is not None
    assert lab_reply["cmds"] == [], "Lab never takes Play's commands"
    assert [c["id"] for c in play_reply["cmds"]] == [41]
    assert play_reply["ack"] == 5 and lab_reply["ack"] == 1_000_000

    oc_send(play, message(seq=6))
    service.poll_all()
    play_reply = oc_receive(play)
    assert play_reply is not None and play_reply["ack"] == 6
    assert boxes[0].latest is not None and not boxes[0].latest.repeat
    assert boxes[0].stats["accepted"] == 2 and boxes[0].stats["repeats"] == 0
    newest = sink.latest(AgentId("play", AGENT))
    assert newest is not None and newest.seq == 6
    assert newest.source == play_place.source
    lab_newest = sink.latest(AgentId("lab", AGENT))
    assert lab_newest is not None and lab_newest.source == lab_place.source


def test_no_reply_follows_a_withdrawal_within_the_quiet_period(tmp_path: Path) -> None:
    """Review L1: OC opens inbox.json, the core withdraws it (OC's handle doesn't stop that on
    Linux; on Windows it is gone after OC's close), and OC's remove comes a tick later. A new
    reply written in between would be removed unread, so none is written for QUIET_S."""
    place, folder = layout(tmp_path)
    clock, queue = Clock(), Queue([say(5), say(6)])
    box = make_box(place, queue=queue, clock=clock)
    oc_send(folder, message(seq=1))
    box.poll()
    clock.t += mb.REPLY_TTL_S
    oc_send(folder, message(seq=2))  # OC sent again, then opened the old reply
    box.poll()  # withdrawn, and seq 2 taken
    assert box.stats["withdrawn"] == 1 and box.due
    for _ in range(3):
        clock.t += mb.QUIET_S / 4
        box.poll()
        assert not (folder / "inbox.json").exists()
    (folder / "inbox.json").unlink(missing_ok=True)  # OC's late remove finds nothing
    clock.t += mb.QUIET_S / 4
    box.poll()
    reply = oc_receive(folder)
    assert reply is not None and reply["ack"] == 2
    assert [ids for ids, _ in queue.back] == [[5, 6]], (
        "the withdrawn commands went back"
    )


def test_a_folder_swapped_for_a_link_during_a_step_stops_the_mailbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review L2: the check comes before the step, so gos-dev could swap the drive's folder for
    a junction in between. The step after it sees the folder changed and stops the mailbox
    (until a restart), loudly, so a swap redirects one step at most (D217)."""
    place, folder = layout(tmp_path)
    drive = folder.parents[2]
    elsewhere = tmp_path / "elsewhere"
    oc_send(folder, message())
    real_replace = os.replace
    swapped: list[str] = []

    def swap_then_replace(
        src: str | os.PathLike[str], dst: str | os.PathLike[str]
    ) -> None:
        if not swapped:
            swapped.append(str(src))
            os.rename(drive, elsewhere)
            _link_dir(drive, elsewhere)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", swap_then_replace)
    sink = Recording()
    box = make_box(place, sink=sink)
    with caplog.at_level(logging.INFO, logger="gregtec.bridge"):
        box.poll()
    monkeypatch.setattr(os, "replace", real_replace)
    assert swapped and box.stopped
    assert "its folder changed while the core wrote to it" in caplog.text
    moved = elsewhere / folder.relative_to(drive)
    assert not (moved / "inbox.json").exists(), "no reply written after the swap"
    oc_send(moved, message(seq=2))
    box.poll()
    assert len(sink.got) == 1, "nothing more read until the core restarts"


# --- the Lab soak's pass rule (docs/runbooks/oc_agent.md steps 25-27)

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "runbooks" / "oc_agent.md"


def test_the_cores_counts_span_agent_boots_until_the_core_restarts(
    tmp_path: Path,
) -> None:
    """Review M3 follow-up: step 25's `gos-agent --once` (boot A) and the hour's run (boot B)
    reach the same core, whose counts are since the core started. Without a restart between
    them `accepted` is the hour's written + 1, so a lost message would read as a pass."""
    place, folder = layout(tmp_path)
    box = make_box(place)
    oc_send(folder, message(seq=1, boot="0000000a"))  # step 25's --once
    box.poll()
    oc_receive(folder)
    hour = 5
    for seq in range(1, hour + 1):  # the hour's run, written = 5
        oc_send(folder, message(seq=seq, boot="0000000b"))
        box.poll()
        oc_receive(folder)
    assert box.stats["accepted"] == hour + 1, "the same core counts both boots"
    restarted = make_box(place)  # step 26 restarts the core before the hour's run
    for seq in range(1, hour + 1):
        oc_send(folder, message(seq=seq, boot="0000000c"))
        restarted.poll()
        oc_receive(folder)
    assert restarted.stats["accepted"] == hour
    assert restarted.stats["seq_gaps"] == 0


def test_the_runbook_restarts_the_core_between_once_and_the_hour() -> None:
    """The fix for the test above, in the runbook Clayton follows: after step 25's --once and
    before the hour's `gos-agent`, step 26 stops and starts the core."""
    text = RUNBOOK.read_text(encoding="utf-8")
    once = text.index("then `gos-agent --once`")
    step26 = text.index("\n26. ", once)
    step27 = text.index("\n27. ", step26)
    hour = text[step26:step27]
    stop = hour.index("gos.exe stop")
    serve = hour.index("gos.exe serve --detach", stop)
    assert serve < hour.index("run `gos-agent`"), (
        "the hour's run starts on a fresh core"
    )
