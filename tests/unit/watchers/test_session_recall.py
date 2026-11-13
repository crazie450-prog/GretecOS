"""WO-6.3 acceptance, as a fixture: a scripted Lab session gives >= 95% event recall.

A fake GTNH-Lab instance (``fakegame.py``) plays two scripted sessions: the files the game
writes (logs, ``level.dat``, BetterQuesting's progress, stats, VisualProspecting's cache,
JourneyMap's waypoints, the world log, a crash report) and GregtecLink's listener events file in
the contract's v1 lines (``docs/contracts/gregteclink-events-v1.md``, the harness's format). The
real watchers read them on a fake clock and post through their real :class:`Sender` to the real
core route (``POST /api/game/events``), which keeps them in the event store. The script keeps a
ground-truth list of what "happened in game" (plan 7.5's kinds); recall is the share of it found
in the store, in the right session, and is printed with every miss. Sessions must start at the
login lines and end at the quit line and the crash. The journal's computed counts must equal
the truth's. The real Lab session is a PC step (docs/runbooks/event_store.md).
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import Config
from gregtec.events import journal
from gregtec.events import store as event_store
from gregtec.events.service import EventService
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store
from gregtec.vault.io import VaultIO
from gregtec.watchers import machines, runner

ADMIN = "admin-" + "r" * 40
BASE = "http://127.0.0.1:8765"
GT = "gregtech:gt.blockmachines"
RECALL_MIN = 0.95
TEMPLATE = Path(__file__).resolve().parents[3] / "vault-template"

Truth = tuple[str, Callable[[dict[str, Any]], bool], str]  # kind, matcher, label


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


class Script:
    """The two sessions, step by step: each step writes the game's files, records what happened
    in :attr:`truth`, and lets the watchers read and post."""

    def __init__(
        self,
        fg: ModuleType,
        tmp_path: Path,
        clock: Clock,
        post: Any,  # noqa: ANN401
        instance: str = "lab",
        tick: Callable[[], object] | None = None,
    ) -> None:
        self.fg, self.clock = fg, clock
        self.tree = fg.Tree(tmp_path / "prism")
        self.truth: list[tuple[int, Truth]] = []  # (session number, what)
        self.session = 0
        self.seq = 0
        self.quests = [1]
        self.deaths = 0
        self.pos = (100.5, 64.0, -200.5)
        self.dim = 0
        self.inv = [fg.stack(0, 265, 10, 0)]
        tree = self.tree
        tree.save_level(fg.level_dat(self.pos, self.dim, self.inv))
        tree.save_quests(self.quests)
        tree.save_stats({"stat.deaths": 0})
        tree.save_vp(0, [(1, 1, "ore.mix.copper")])
        tree.waypoint("Home", 0, 64, 0, 0)
        tree.log("")
        tree.log("", "fml-client-latest.log")
        (tree.game / "logs" / "world.log").write_text("", encoding="utf-8")
        (tree.game / machines.EVENTS_FILE).parent.mkdir(parents=True)
        (tree.game / machines.EVENTS_FILE).write_text("", encoding="utf-8")
        tree.crash(1)  # an old report: not this session's
        self.watchers = runner.Watchers(
            tree.game, tree.world, tmp_path / "home", clock, settle_s=0
        )
        self.sender = runner.Sender(post, instance)
        self.instance = instance
        self.tick = tick  # the core's events tick, between polls (Play's journal)
        self.poll()  # the baseline: nothing happened yet

    # --- plumbing

    def poll(self) -> None:
        self.clock.now += 1
        self.sender.put(self.watchers.poll_logs())
        self.sender.put(self.watchers.poll_files())
        while self.sender.pending():
            assert self.sender.flush_once()
        if self.tick is not None:
            self.tick()

    def at(self, minutes: float) -> None:
        self.clock.now = self.t0 + minutes * 60

    def happened(
        self, kind: str, label: str, match: Callable[[dict[str, Any]], bool]
    ) -> None:
        self.truth.append((self.session, (kind, match, label)))

    def line(self, msg: str, thread: str = "Client thread") -> None:
        self.tree.log(self.fg.stamp_line(msg, thread))

    # --- what the player does

    def login(self) -> None:
        self.session += 1
        x, y, z = self.pos
        self.line(
            f"Clayton[local:E:1a2b] logged in with entity id 7 at ({x}, {y}, {z})",
            "Server thread",
        )
        self.happened("location", "login", lambda d: d.get("via") == "login")
        self.poll()

    def chat(self, text: str) -> None:
        self.line(f"[CHAT] <Clayton> {text}")
        self.happened("chat", f"chat {text!r}", lambda d: text in str(d.get("text")))
        self.poll()

    def machine(self, kind: str, mte: int, x: int, y: int = 64, z: int = -5) -> None:
        self.seq += 1
        row = {
            "v": 1,
            "kind": kind,
            "boot": 1_790_000_000_000,
            "seq": self.seq,
            "ts_ms": int(self.clock.now * 1000),
            "dim": self.dim,
            "x": x,
            "y": y,
            "z": z,
            "mte_id": mte,
            "registry": GT,
            "meta": mte,
        }
        with open(self.tree.game / machines.EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        self.happened(
            kind,
            f"{kind} mte:{mte} at {x},{y},{z}",
            lambda d: (
                d.get("ref") == f"mte:{mte}" and (d["x"], d["y"], d["z"]) == (x, y, z)
            ),
        )
        self.poll()

    def quest(self, n: int) -> None:
        self.quests.append(n)
        self.tree.save_quests(self.quests)
        ref = "q:" + self.fg.quest_id(n)[2]
        self.happened("quest_done", f"quest {ref}", lambda d: d.get("quest") == ref)
        self.poll()

    def walk(self, dx: float, dz: float, pick_up: int = 0) -> None:
        x, y, z = self.pos
        self.pos = (x + dx, y, z + dz)
        to = self.pos
        if pick_up:
            self.inv = [self.fg.stack(0, 265, 10 + pick_up, 0)]
        self.tree.save_level(self.fg.level_dat(self.pos, self.dim, self.inv))
        self.happened(
            "location",
            f"moved to {to[0]},{to[2]}",
            lambda d: (
                d.get("x") == to[0] and d.get("z") == to[2] and d.get("via") != "login"
            ),
        )
        if pick_up:
            self.happened(
                "item_delta",
                f"picked up {pick_up} iron ingots",
                lambda d: any(
                    i.get("key") == "minecraft:iron_ingot@0"
                    and i.get("delta") == pick_up
                    for i in d.get("items", [])
                ),
            )
        self.poll()

    def portal(self, to: int) -> None:
        was, self.dim = self.dim, to
        self.tree.save_level(self.fg.level_dat(self.pos, self.dim, self.inv))
        self.happened(
            "dim_change",
            f"dim {was} to {to}",
            lambda d: d.get("from") == was and d.get("to") == to,
        )
        self.poll()

    def vein_chat(self, x: int, z: int) -> None:
        self.line(f"[CHAT] You found a Copper vein at ({x}, {z})!")
        self.happened(
            "vein_found",
            f"vein at {x},{z} (chat)",
            lambda d: (d.get("x"), d.get("z")) == (x, z),
        )
        self.poll()

    def vein_cache(self, veins: list[tuple[int, int, str]]) -> None:
        self.tree.save_vp(0, veins)
        mix = veins[-1][2]
        self.happened(
            "vein_found",
            f"{mix} (cache)",
            lambda d: d.get("mix") == mix and d.get("via") == "cache",
        )
        self.poll()

    def die(self) -> None:
        """One death, seen three ways: the chat line, the stats, JourneyMap's waypoint."""
        self.deaths += 1
        self.line("[CHAT] Clayton was slain by Zombie")
        self.poll()
        self.clock.now += 5
        self.tree.save_stats({"stat.deaths": self.deaths})
        x, y, z = (round(v) for v in self.pos)
        self.tree.waypoint(f"Death {self.deaths}", x, y, z, self.dim, "Death")
        self.happened(
            "death",
            f"death {self.deaths}",
            lambda d: (
                "same_as" not in d and "slain by Zombie" in str(d.get("message", ""))
            ),
        )
        self.poll()

    def place(self, block: str, x: int) -> None:
        with open(self.tree.game / "logs" / "world.log", "a", encoding="utf-8") as f:
            f.write(
                f"2026-11-11 12:00:00 : Clayton placed {block}:0 at [{x}, 64, 0] in Overworld\n"
            )
        self.happened(
            "block_placed",
            f"placed {block}",
            lambda d: d.get("block") == block and d.get("x") == x,
        )
        self.poll()

    def pause(self) -> None:
        self.line("Saving and pausing game...", "Server thread")
        self.happened("game_paused", "paused", lambda d: d.get("paused") is True)
        self.poll()

    def unpause(self) -> None:
        self.tree.save_level(
            self.fg.level_dat(self.pos, self.dim, self.inv)
        )  # an autosave
        self.happened("game_paused", "unpaused", lambda d: d.get("paused") is False)
        self.poll()

    def quit(self) -> None:
        self.line("Stopping server", "Server thread")
        self.poll()

    def crash(self) -> None:
        self.tree.crash(2)
        self.happened(
            "crash", "crash report", lambda d: "crash" in str(d.get("report", ""))
        )
        self.poll()

    t0: float = 0.0


@pytest.fixture
def setup(
    tmp_path: Path, fg: ModuleType, request: pytest.FixtureRequest
) -> Iterator[tuple[Script, Core, TestClient]]:
    """The core and the script, for Lab (the acceptance) or, with ``indirect`` "play", for
    Play with the session scribe writing a temp vault's journal as the core's tick does."""
    instance = getattr(request, "param", "lab")
    start = float(int(time.time()) - 6 * 3600)
    clock = Clock(start)
    store = Store(tmp_path / "home" / "data" / "gos.sqlite")
    scribe = None
    if instance == "play":
        shutil.copytree(TEMPLATE, tmp_path / "vault")
        scribe = journal.Scribe(VaultIO(tmp_path / "vault"))
    service = EventService(
        store.path, scribe, worlds={instance: f"{instance}-world"}, clock=clock
    )
    core = Core(
        cfg=Config(gos_home=tmp_path / "home"),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        events=service,
    )
    with TestClient(create_app(core), base_url=BASE) as client:
        token = core.auth.issue_client_token("watchers")

        def post(body: dict[str, Any], timeout: float) -> None:
            got = client.post(
                "/api/game/events",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            got.raise_for_status()

        tick = (lambda: service.tick(None)) if scribe is not None else None
        script = Script(fg, tmp_path, clock, post, instance, tick)
        script.t0 = start
        yield script, core, client
    assert core.events is not None
    core.events.close()
    store.close()


def play(s: Script) -> None:
    """Session 1: login, a base built up over 40 minutes, quit. Session 2: login, a crash."""
    s.at(1)
    s.login()
    s.at(2)
    s.chat("starting the steam age")
    s.at(3)
    s.machine("machine_built", 101, 10)  # a steam macerator
    s.at(4)
    s.machine("machine_built", 103, 12)
    s.at(5)
    s.quest(2)
    s.at(6)
    s.walk(40, 0, pick_up=6)
    s.at(8)
    s.vein_chat(120, -40)
    s.at(9)
    s.vein_cache([(1, 1, "ore.mix.copper"), (3, -2, "ore.mix.tin")])
    s.at(11)
    s.place("Cobblestone", 7)
    s.at(13)
    s.die()
    s.at(15)
    s.walk(-40, 30)
    s.at(17)
    s.portal(-1)
    s.at(19)
    s.portal(0)
    s.at(21)
    s.machine("machine_built", 1000, 20)  # the EBF's controller
    s.at(22)
    s.machine("machine_removed", 103, 12)
    s.at(24)
    s.pause()
    s.at(26)
    s.unpause()
    s.at(28)
    s.quest(3)
    s.at(30)
    s.quest(7)
    s.at(32)
    s.chat("ebf next")
    s.at(34)
    s.machine("machine_built", 1180, 22)  # a hatch
    s.at(36)
    s.walk(25, -25)
    s.at(40)
    s.quit()
    # session 2, an hour later
    s.at(100)
    s.login()
    s.at(102)
    s.quest(9)
    s.at(104)
    s.die()
    s.at(106)
    s.machine("machine_built", 1001, 30)
    s.at(108)
    s.crash()


def measure(s: Script, conn: Any) -> tuple[float, list[str]]:  # noqa: ANN401
    """The share of the script's truth found in the store, in its session, each stored event
    matched once; and the misses."""
    sessions = conn.execute(
        "SELECT * FROM session WHERE instance = ? ORDER BY id", (s.instance,)
    ).fetchall()
    stored: dict[int, list[dict[str, Any]]] = {}
    for n, row in enumerate(sessions, start=1):
        stored[n] = [
            event_store.event_dict(r)
            for r in conn.execute(
                "SELECT * FROM event WHERE session_id = ? ORDER BY ts, id", (row["id"],)
            )
        ]
    used: set[int] = set()
    misses = []
    for n, (kind, match, label) in s.truth:
        found = next(
            (
                e
                for e in stored[n]
                if e["kind"] == kind and e["id"] not in used and match(e["data"])
            ),
            None,
        )
        if found is None:
            misses.append(f"s{n} {kind}: {label}")
        else:
            used.add(found["id"])
    return 1 - len(misses) / len(s.truth), misses


def test_a_scripted_lab_session_gives_95_percent_event_recall(
    setup: tuple[Script, Core, TestClient],
) -> None:
    s, core, client = setup
    play(s)
    assert core.events is not None
    conn = core.events._db()
    sessions = conn.execute(
        "SELECT * FROM session WHERE instance = 'lab' ORDER BY id"
    ).fetchall()
    assert [(r["start_why"], r["end_why"]) for r in sessions] == [
        ("login", "quit"),
        ("login", "crash"),
    ]
    # each step polls a second after it writes
    assert sessions[0]["start_ts"] == int(s.t0 + 61)
    assert sessions[0]["world"] == "lab-world"
    recall, misses = measure(s, conn)
    print(
        f"scripted Lab session: {len(s.truth)} events, recall {recall:.1%}, misses {misses}"
    )
    assert len(s.truth) >= 25  # one miss stays above 95%
    assert recall >= RECALL_MIN, misses
    # one death each session, however many readers saw it
    assert [event_store.counts(conn, r["id"]).get("death") for r in sessions] == [1, 1]
    # the journal's counts are the truth's, computed in code
    want: dict[str, int] = {}
    for n, (kind, _, _) in s.truth:
        if n == 1 and kind in journal.KINDS:
            want[journal.KINDS[kind]] = want.get(journal.KINDS[kind], 0) + 1
    first = dict(sessions[0])
    b = journal.block(conn, first, journal.SESSION_MAX, True)
    assert b is not None and b.counts == want
    # what the Activity view shows for it
    view = client.get(
        f"/api/activity?session={first['id']}",
        headers={"Authorization": f"Bearer {ADMIN}"},
    ).json()
    assert view["session"]["end_why"] == "quit"
    assert (
        view["session"]["played_s"] == 39 * 60
    )  # login (minute 1) to quit (minute 40)


def test_the_measure_sees_a_reader_that_misses(
    setup: tuple[Script, Core, TestClient],
) -> None:
    """The recall is real: without the listener's events file the machines are missed."""
    s, core, _ = setup
    s.watchers.others = [
        r for r in s.watchers.others if not isinstance(r, machines.MachineEventWatch)
    ]
    play(s)
    assert core.events is not None
    recall, misses = measure(s, core.events._db())
    assert len(misses) == 6 and all("machine_" in m for m in misses)
    assert recall < RECALL_MIN


@pytest.mark.parametrize("setup", ["play"], indirect=True)
def test_the_same_session_on_play_is_journaled_with_every_count(
    setup: tuple[Script, Core, TestClient],
) -> None:
    """The journal through the real scribe, on the core's tick between every poll (the
    review's M1, verdict 11): each session's counts in the daily logs equal the truth's, the
    save files read after the quit included."""
    s, core, _ = setup
    play(s)
    assert core.events is not None and core.events.scribe is not None
    for _ in range(
        int(journal.FINAL_WAIT_S // 10) + 1
    ):  # the 10 s tick, after the crash
        s.clock.now += 10
        core.events.tick(None)
    conn = core.events._db()
    recall, misses = measure(s, conn)
    assert recall >= RECALL_MIN, misses
    rows = conn.execute("SELECT * FROM session ORDER BY id").fetchall()
    assert [(r["instance"], r["end_why"], r["journal_done"]) for r in rows] == [
        ("play", "quit", 1),
        ("play", "crash", 1),
    ]
    vault = core.events.scribe.vault
    for n, row in enumerate(rows, 1):
        want: dict[str, int] = {}
        for k, (kind, _, _) in s.truth:
            if k == n and kind in journal.KINDS:
                key = f"n_{journal.KINDS[kind]}"
                want[key] = want.get(key, 0) + 1
        lines: list[str] = []
        for rel in sorted({r["journal_path"] for r in rows}):
            lines += journal.journal_lines(vault.read(rel)[1], int(row["id"]))
        got: dict[str, int] = {}
        for line in lines:
            key, _, value = line.partition(": ")
            if key.startswith("n_"):
                got[key] = got.get(key, 0) + int(value)
        assert got == want, (n, lines)
        assert lines[-1].startswith(f"session_end: s{row['id']} ")
