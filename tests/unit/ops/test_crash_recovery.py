"""WO-11.2: crash recovery (D333). Each writer runs in a child process that is killed hard
(SIGKILL; TerminateProcess on Windows) at a varied moment, as a crash or a power cut would stop
it; then the real recovery code runs and the test shows: no lost or duplicated confirmed action,
no half-written file (vault notes, the mailbox reply, the watchers' tails, the bot's state), no
stuck lock, every committed DB transaction kept and the DB intact.

A process kill keeps the OS's page cache, which a power cut doesn't. What a power cut adds is
covered by ordering: every file is fsynced before the rename that publishes it (checked here
for each writer), and SQLite runs in WAL with ``synchronous=FULL`` (Python's default), which
fsyncs the WAL at each commit.

The real processes: the core started by a real supervisor (``python -m gregtec.supervisor``),
the supervisor killed hard, the core leaving on its own (its stdin closes), then both started
again on the same GOS_HOME. Voice, the watchers and the Discord bot keep no state of their own
that can tear beyond the files tested here; what they send the core is replayed (events, by
their id), refused when already done (a confirm), or at least once by design (a DM, D295).
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

StrPath = str | os.PathLike[str]

import pytest

from gregtec import cli, config, control, store, supervisor
from gregtec.alerts.policy import NotifyPolicy
from gregtec.bridge import mailbox as mb
from gregtec.bridge.actions import ActionDesk
from gregtec.bridge.ingest import AgentId
from gregtec.discordbot.alerts import AlertDesk
from gregtec.events import store as events
from gregtec.store import DB_NAME, Store
from gregtec.vault import io as vault_io
from gregtec.watchers import files as watcher_files

PY = sys.executable
SEEDS = (1, 2, 3)  # each a different kill moment; every run is reproducible
AGENT = AgentId("lab", "addr-1")


def kill_pid(pid: int) -> None:
    """Windows: TerminateProcess. POSIX: SIGKILL, so nothing runs on the way out."""
    with contextlib.suppress(ProcessLookupError, OSError):
        os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)


def run_and_kill(code: str, args: list[str], lines: int, seed: int) -> list[str]:
    """Run ``code`` (its first line out is ``pid <n>``), let it print ``lines`` progress lines,
    wait a seeded few milliseconds more, so the kill lands inside a write, and kill it hard.
    Returns the progress lines it printed."""
    proc = subprocess.Popen(
        [PY, "-c", textwrap.dedent(code), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    seen: list[str] = []
    try:
        assert proc.stdout is not None
        first = proc.stdout.readline().split()
        assert first[:1] == ["pid"], first + [proc.stderr.read() if proc.stderr else ""]
        pid = int(first[1])
        while len(seen) < lines:
            line = proc.stdout.readline()
            if not line:
                raise AssertionError(proc.stderr.read() if proc.stderr else "no output")
            seen.append(line.strip())
        time.sleep(random.Random(seed).uniform(0.0, 0.02))
        kill_pid(pid)
        rest, _ = proc.communicate(timeout=30)
        seen += [line.strip() for line in rest.splitlines() if line.strip()]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
    return seen


def integrity(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("PRAGMA integrity_check")]
    finally:
        conn.close()


def lines_for(seed: int, low: int = 20, high: int = 120) -> int:
    return random.Random(seed * 7919).randint(low, high)


# --- the DB and kv: every committed transaction kept, nothing torn, no stuck lock -------------

DB_CHILD = """
    import os, sys
    from pathlib import Path
    from gregtec.store import Store
    s = Store(Path(sys.argv[1]))
    print("pid", os.getpid(), flush=True)
    i = 0
    while True:
        i += 1
        with s.transaction() as c:
            c.execute("INSERT INTO action(created, kind, args, state) VALUES (?, 'say', '{}', 'done')", (i,))
            c.execute(
                "INSERT INTO kv(key, value, upd) VALUES ('crash.n', ?, ?) ON CONFLICT(key) "
                "DO UPDATE SET value = excluded.value, upd = excluded.upd", (str(i), i))
        print(i, flush=True)
"""


@pytest.mark.parametrize("seed", SEEDS)
def test_db_and_kv_keep_every_committed_transaction(tmp_path: Path, seed: int) -> None:
    db = tmp_path / DB_NAME
    seen = run_and_kill(DB_CHILD, [str(db)], lines_for(seed), seed)
    committed = int(seen[-1])
    assert integrity(db) == ["ok"]
    s = Store(db)
    try:
        n = s.rows("SELECT COUNT(*) AS n FROM action")[0]["n"]
        kv = int(s.rows("SELECT value FROM kv WHERE key = 'crash.n'")[0]["value"])
        assert n == kv  # the row and its counter: both or neither
        assert n >= committed  # every commit the child reported survived
        s.try_write(
            "INSERT INTO kv(key, value, upd) VALUES ('after', '1', 1)"
        )  # no lock
        # the power-cut half: the WAL is fsynced at each commit (2 = FULL)
        assert s.rows("PRAGMA synchronous")[0][0] == 2
    finally:
        s.close()


# --- confirmed actions: never lost silently, never sent twice ----------------------------------

ACTION_CHILD = """
    import os, sys
    from pathlib import Path
    from gregtec.store import Store
    from gregtec.bridge.actions import ActionDesk
    from gregtec.bridge.ingest import AgentId
    s = Store(Path(sys.argv[1]))
    desk = ActionDesk(s, locate=lambda binding, kind, args: "addr-1")
    agent = AgentId("lab", "addr-1")
    print("pid", os.getpid(), flush=True)
    i = 0
    while True:
        i += 1
        aid = desk.propose("say", {"text": f"hello {i}"}, target="lab").action.id
        print("proposed", aid, flush=True)
        desk.confirm("voice", aid)
        print("confirmed", aid, flush=True)
        for cmd in desk.take(agent, 5):
            print("sent", cmd["id"], flush=True)
        if i % 2:
            desk.results(agent, {"results": [{"id": aid, "ok": True, "state": "done"}]})
            print("done", aid, flush=True)
"""


@pytest.mark.parametrize("seed", SEEDS)
def test_a_killed_core_loses_no_confirmed_action_and_sends_none_twice(
    tmp_path: Path, seed: int
) -> None:
    db = tmp_path / DB_NAME
    seen = run_and_kill(ACTION_CHILD, [str(db)], lines_for(seed), seed)
    sent = [int(x.split()[1]) for x in seen if x.startswith("sent ")]
    assert len(sent) == len(set(sent))
    s = Store(db)
    try:
        desk = ActionDesk(s, locate=lambda b, k, a: "addr-1")  # the restarted core
        rows = {
            r["id"]: (r["state"], json.loads(r["result"] or "{}"))
            for r in s.rows("SELECT id, state, result FROM action")
        }
        assert sorted(rows) == list(range(1, len(rows) + 1))  # none vanished
        for aid, (state, result) in rows.items():
            assert state in ("sent", "done", "failed", "expired"), (aid, state)
            if state in ("failed", "expired"):  # said out loud, with why: nothing ran
                assert "restarted" in result["why"], result
        for aid in sent:  # what went to OC stays sent or done: never re-queued
            assert rows[aid][0] in ("sent", "done")
        assert desk.take(AGENT, 100) == []  # nothing is sent again
        assert desk.pending() is None
    finally:
        s.close()
    assert integrity(db) == ["ok"]


def test_a_confirm_repeated_after_a_restart_is_refused(tmp_path: Path) -> None:
    """Voice repeats "confirm", or the restarted Discord bot posts a button press again: the
    second finds the action already confirmed, so it can't run twice."""
    s = Store(tmp_path / DB_NAME)
    try:
        clock = [1000.0]
        desk = ActionDesk(s, locate=lambda b, k, a: "addr-1", clock=lambda: clock[0])
        aid = desk.propose("say", {"text": "hi"}, target="lab").action.id  # type: ignore[union-attr]
        assert desk.confirm("discord", aid).ok
        again = ActionDesk(s, locate=lambda b, k, a: "addr-1", clock=lambda: clock[0])
        out = again.confirm("discord", aid)
        assert not out.ok
        assert (
            again.take(AGENT, 5) == []
        )  # the restart failed it, unsent: it never runs
        assert again.get(aid).state == "failed"  # type: ignore[union-attr]
        aid2 = again.propose("say", {"text": "hi"}, target="lab").action.id  # type: ignore[union-attr]
        assert again.confirm("voice", aid2).ok
        assert not again.confirm(
            "voice", aid2
        ).ok  # "That action is already confirmed."
        assert [c["id"] for c in again.take(AGENT, 5)] == [aid2]
        assert again.take(AGENT, 5) == []
    finally:
        s.close()


# --- the event store: the watchers' retry stores each event once ------------------------------

EVENT_CHILD = """
    import os, sys
    from pathlib import Path
    from gregtec import store
    from gregtec.events.store import record
    conn = store.connect(Path(sys.argv[1]))
    print("pid", os.getpid(), flush=True)
    b = 0
    while True:
        batch = [{"id": f"u{b}-{j}", "kind": "death", "ts": 1_800_000_000 + b * 30 + j,
                  "source": "log", "data": {"msg": "fell"}} for j in range(20)]
        record(conn, "lab", batch, world="lab-ops")
        print(b, flush=True)
        b += 1
"""


def batch(b: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"u{b}-{j}",
            "kind": "death",
            "ts": 1_800_000_000 + b * 30 + j,
            "source": "log",
            "data": {"msg": "fell"},
        }
        for j in range(20)
    ]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_event_store_keeps_each_event_once_after_a_kill_and_a_resend(
    tmp_path: Path, seed: int
) -> None:
    db = tmp_path / DB_NAME
    seen = run_and_kill(EVENT_CHILD, [str(db)], lines_for(seed, 5, 40), seed)
    last = int(seen[-1])
    conn = store.connect(db)
    try:
        for b in range(last + 3):  # the watchers send again what wasn't acknowledged
            events.record(conn, "lab", batch(b), world="lab-ops")
        n, uids = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT uid) FROM event"
        ).fetchone()
        assert n == uids == 20 * (last + 3)
        orphans = conn.execute(
            "SELECT COUNT(*) FROM event e LEFT JOIN session s ON s.id = e.session_id "
            "WHERE e.session_id IS NOT NULL AND s.id IS NULL"
        ).fetchone()[0]
        assert orphans == 0
    finally:
        conn.close()
    assert integrity(db) == ["ok"]


# --- the vault: a note is whole or untouched; a cut-short write's temp file is swept ----------

VAULT_CHILD = """
    import hashlib, os, random, sys
    from pathlib import Path
    from gregtec.vault.io import atomic_write
    vault = Path(sys.argv[1])
    rng = random.Random(int(sys.argv[2]))
    print("pid", os.getpid(), flush=True)
    i = 0
    while True:
        i += 1
        k = i % 6
        body = f"---\\nid: pl-{k}\\n---\\nv: {i}\\n" + "x" * rng.randint(1, 400_000) + "\\n"
        data = body + f"sha: {hashlib.sha256(body.encode()).hexdigest()}\\n"
        atomic_write(vault / "plans" / f"pl-{k}.md", data.encode())
        print(i, flush=True)
"""


@pytest.mark.parametrize("seed", SEEDS)
def test_no_vault_note_is_ever_half_written(tmp_path: Path, seed: int) -> None:
    import hashlib

    vault = tmp_path / "vault"
    (vault / "plans").mkdir(parents=True)
    (vault / ".git").mkdir()
    (vault / ".git" / ".keep.deadbeef.gos-tmp").write_text("git's own", "utf-8")
    run_and_kill(VAULT_CHILD, [str(vault), str(seed)], lines_for(seed, 10, 60), seed)
    notes = sorted((vault / "plans").glob("pl-*.md"))
    assert notes
    for note in notes:
        text = note.read_text("utf-8")
        body, _, tail = text.rpartition("sha: ")
        assert tail.strip() == hashlib.sha256(body.encode()).hexdigest(), note.name
    # A kill inside a write may leave its temp file; the core's start sweeps it.
    vault_io.sweep_tmp(vault)
    assert not list((vault / "plans").glob("*.gos-tmp"))
    assert (
        vault / ".git" / ".keep.deadbeef.gos-tmp"
    ).exists()  # git's folder is left alone
    assert sorted((vault / "plans").glob("pl-*.md")) == notes


def test_sweep_tmp_takes_only_atomic_writes_temp_files(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "plans").mkdir(parents=True)
    (vault / "me" / "sub").mkdir(parents=True)
    keep = [
        vault / "plans" / "pl-1.md",
        vault / "plans" / "x.gos-tmp",  # not an atomic_write name
        vault / "plans" / ".pl-1.md.nothex!.gos-tmp",
        # Clayton's own me/ is never written, not even by the sweep (CLAUDE.md)
        vault / "me" / ".goals.md.0a1b2c3d.gos-tmp",
        vault / "me" / "sub" / ".n.md.0a1b2c3d.gos-tmp",
    ]
    for p in keep:
        p.write_text("x", "utf-8")
    (vault / "plans" / ".pl-1.md.0a1b2c3d.gos-tmp").write_text("half", "utf-8")
    (vault / "plans" / "me").mkdir()  # only the vault's top-level me/ is Clayton's
    (vault / "plans" / "me" / ".a.md.0a1b2c3d.gos-tmp").write_text("half", "utf-8")
    assert vault_io.sweep_tmp(vault) == 2
    dirs = [vault / "plans", vault / "plans" / "me", vault / "me", vault / "me" / "sub"]
    assert sorted(vault.rglob("*")) == sorted([*dirs, *keep])
    assert vault_io.sweep_tmp(tmp_path / "none") == 0


# --- the OC mailbox's reply, the watchers' tails, the bot's state: old or new, never torn -----

MAILBOX_CHILD = """
    import json, os, sys
    from pathlib import Path
    from gregtec.bridge.mailbox import Mailbox, INBOX
    folder = Path(sys.argv[1])
    box = Mailbox(place=None, bridge=None)
    print("pid", os.getpid(), flush=True)
    i = 0
    while True:
        i += 1
        (folder / INBOX).unlink(missing_ok=True)  # OC read the last one
        assert box._install(folder, json.dumps({"n": i, "pad": "y" * 1800}).encode())
        print(i, flush=True)
"""


@pytest.mark.parametrize("seed", SEEDS)
def test_the_mailbox_reply_is_whole_or_absent(tmp_path: Path, seed: int) -> None:
    seen = run_and_kill(MAILBOX_CHILD, [str(tmp_path)], lines_for(seed), seed)
    inbox = tmp_path / mb.INBOX
    if inbox.exists():
        doc = json.loads(inbox.read_text("utf-8"))
        assert doc["pad"] == "y" * 1800 and doc["n"] >= int(seen[-1])
    box = mb.Mailbox(place=None, bridge=None)  # type: ignore[arg-type]
    inbox.unlink(missing_ok=True)
    assert box._install(tmp_path, b'{"n": 0}')  # a stale inbox.tmp is no obstacle
    assert not (tmp_path / mb.INBOX_TMP).exists()


TAILS_CHILD = """
    import os, sys
    from pathlib import Path
    from gregtec.watchers.files import save_tails
    path = Path(sys.argv[1])
    print("pid", os.getpid(), flush=True)
    i = 0
    while True:
        i += 1
        save_tails(path, {"n": i, "pad": "z" * 200_000})
        print(i, flush=True)
"""


@pytest.mark.parametrize("seed", SEEDS)
def test_the_watchers_tails_are_old_or_new(tmp_path: Path, seed: int) -> None:
    path = tmp_path / "data" / "watchers" / "tails.json"
    seen = run_and_kill(TAILS_CHILD, [str(path)], lines_for(seed, 5, 40), seed)
    tails = watcher_files.load_tails(path)
    assert tails["pad"] == "z" * 200_000 and tails["n"] >= int(seen[-1])


BOT_CHILD = """
    import os, sys
    from pathlib import Path
    from gregtec.alerts.policy import NotifyPolicy
    from gregtec.discordbot.alerts import AlertDesk
    desk = AlertDesk(None, NotifyPolicy(), state_file=Path(sys.argv[1]))
    print("pid", os.getpid(), flush=True)
    i = 0
    while True:
        i += 1
        desk.state["alerts"] = {"n": i, "pad": "q" * 200_000}
        desk._save()
        print(i, flush=True)
"""


@pytest.mark.parametrize("seed", SEEDS)
def test_the_bots_alert_state_is_old_or_new(tmp_path: Path, seed: int) -> None:
    path = tmp_path / "bot" / "alerts.json"
    seen = run_and_kill(BOT_CHILD, [str(path)], lines_for(seed, 5, 40), seed)
    desk = AlertDesk(None, NotifyPolicy(), state_file=path)  # type: ignore[arg-type]
    saved = desk.state["alerts"]
    assert saved["pad"] == "q" * 200_000 and saved["n"] >= int(seen[-1])


def fsync_order(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, write: Callable[[], None]
) -> list[str]:
    calls: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        calls.append("fsync")
        real_fsync(fd)

    def replace(src: StrPath, dst: StrPath) -> None:
        calls.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "fsync", fsync)
    monkeypatch.setattr(module.os, "replace", replace)
    write()
    return calls


def test_every_published_file_is_fsynced_before_its_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The power-cut half: the data reaches the disk before the rename makes it the file."""
    from gregtec.discordbot import alerts as bot_alerts
    from gregtec.ops import backups

    desk = AlertDesk(None, NotifyPolicy(), state_file=tmp_path / "bot" / "alerts.json")  # type: ignore[arg-type]
    cases: list[tuple[ModuleType, Callable[[], None]]] = [
        (vault_io, lambda: vault_io.atomic_write(tmp_path / "n.md", b"x")),
        (
            watcher_files,
            lambda: watcher_files.save_tails(tmp_path / "t.json", {"n": 1}),
        ),
        (bot_alerts, desk._save),
        (backups, lambda: backups.write_manifest(tmp_path / "gos-1.sqlite", {})),
        (
            mb,
            lambda: mb.Mailbox(place=None, bridge=None)._install(tmp_path, b"{}"),  # type: ignore[arg-type]
        ),
    ]
    for module, write in cases:
        calls = fsync_order(monkeypatch, module, write)
        assert calls[:1] == ["fsync"] and "replace" in calls, (module.__name__, calls)
        monkeypatch.undo()
    assert json.loads((tmp_path / "bot" / "alerts.json").read_text("utf-8"))


# --- the real processes: the supervisor killed hard, the core leaves, both come back ----------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for[T](check: Callable[[], T | None], timeout: float, step: float = 0.1) -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout} s")
        time.sleep(step)


@pytest.fixture
def allow_elevated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(supervisor.ALLOW_ELEVATED_ENV, "1")


@contextlib.contextmanager
def supervisor_process(path: Path, cwd: Path) -> Iterator[subprocess.Popen[bytes]]:
    with open(cwd / "supervisor.out", "ab") as out:
        proc = subprocess.Popen(
            [PY, "-m", "gregtec.supervisor", "--config", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            cwd=cwd,
        )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=20)


def core_health(port: int) -> dict[str, Any] | None:
    try:
        return control.call(port, "GET", "/health")
    except control.ControlError:
        return None


@pytest.mark.usefixtures("allow_elevated")
def test_supervisor_killed_hard_core_leaves_and_both_restart_clean(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = tmp_path / "gos.toml"
    path.write_text(
        "[paths]\n"
        f'gos_home = "{home.as_posix()}"\n'
        f'prism_root = "{(tmp_path / "prism").as_posix()}"\n'
        f'prism_instances = "{(tmp_path / "instances").as_posix()}"\n'
        "[core]\n"
        f"port = {free_port()}\n"
        f"supervisor_port = {free_port()}\n",
        encoding="utf-8",
    )
    cfg = config.load(path)
    db = cfg.data_dir / DB_NAME
    s = Store(db)  # the state a crash left: one action of each kind in flight
    try:
        with s.transaction() as conn:
            for state in ("proposed", "confirmed", "sent", "done"):
                conn.execute(
                    "INSERT INTO action(created, kind, args, state, target, computer, "
                    "readback, source, confirmed_via, sent_ts) VALUES "
                    "(?, 'say', '{\"text\": \"hi\"}', ?, 'lab', 'addr-1', 'Say hi', 'agent', "
                    "?, ?)",
                    (
                        int(time.time()),
                        state,
                        None if state == "proposed" else "voice",
                        int(time.time()) if state in ("sent", "done") else None,
                    ),
                )
    finally:
        s.close()
    note = home / "vault" / "plans" / "pl-1.md"
    note.parent.mkdir(parents=True)
    note.write_text("st: x\n", "utf-8")
    torn = note.with_name(".pl-1.md.0a1b2c3d.gos-tmp")
    torn.write_text("half", "utf-8")

    def states() -> list[str]:
        conn = sqlite3.connect(db)
        try:
            return [r[0] for r in conn.execute("SELECT state FROM action ORDER BY id")]
        finally:
            conn.close()

    run_file = cfg.run_dir / control.SUPERVISOR_FILE
    with supervisor_process(path, tmp_path):
        sup = wait_for(
            lambda: control.running(run_file, control.SUPERVISOR_SERVICE), 60
        )
        first = wait_for(lambda: core_health(cfg.core_port), 60)
        assert states() == ["expired", "failed", "sent", "done"]
        assert not torn.exists() and note.read_text("utf-8") == "st: x\n"
        kill_pid(sup["pid"])  # no stop, no clean-up: a crash
        # The core's stdin was the supervisor's pipe: it ends by itself.
        wait_for(lambda: core_health(cfg.core_port) is None, 30)
    with supervisor_process(path, tmp_path) as again:
        wait_for(
            lambda: (h := core_health(cfg.core_port)) and h["pid"] != first["pid"], 60
        )
        assert states() == ["expired", "failed", "sent", "done"]  # nothing re-queued
        assert integrity(db) == ["ok"]
        assert cli.main(["stop", "--all", "--config", str(path)]) == 0
        assert again.wait(timeout=30) == 0
    assert not run_file.exists()
