"""The in-game displays' content: the OC status board, the OCGlasses HUD and chat-box notices
(WO-7.4; plan 7.9 "In-game displays", D24, D76; contract "The display").

Displays are not actions and need no confirmation (plan 7.9): the core composes every line here,
from code, and the OC agent only lays it out (``oc/display.lua``). Over both transports the same
``display`` object rides in the reply (:meth:`gregtec.bridge.ingest.Bridge.reply`), after the
commands and only while it fits in the reply's 2048 bytes:

- ``rev``: a checksum of the board and HUD lines. The agent says in each message which ``rev`` it
  shows (telemetry ``display.rev``), and the core sends the display only when that differs (a
  change, a restarted agent, a reply that was lost) or a chat notice is waiting; so a change
  reaches the screen with the reply to the first message after it (one ``cycle_s``, 5 s).
- ``lines`` (board, at most :data:`BOARD_LINES` of :data:`BOARD_W` printable ASCII characters:
  everything fits OC's smallest screen and GPU, 50 x 16, with the agent's own header and footer):
  power, problems (stopped and stalled first), the action waiting for "confirm", the current goal, the next
  step, AE2 crafting. Each a level: ``ok warn bad info dim``.
- ``hud`` (OCGlasses, at most :data:`HUD_LINES` of :data:`HUD_W`): power, the worst problem, the
  waiting action or the next step. Only to an agent that says it draws one (an M2 agent with a
  glasses terminal, plan 7.9's mode table); it keeps a fixed set of text widgets and sets only
  the lines that changed, all at once (OCGlasses sends a packet per change and draws power per
  widget [F 4.1]).
- ``chat``: at most :data:`CHAT_PER_REPLY` notices for the chat box (one server tick each), each
  said once: ids rise, the agent reports the highest it said (telemetry ``display.said``), a
  notice older than :data:`NOTICE_TTL_S` or from before the agent's run started is not sent.
  Notices are fixed templates about confirmed OC actions (confirmed, started, done, failed,
  check AE2, no result; D59's confirmations, plan 7.9 "Chat box ... for confirmations"), worded
  from the desk's own outcome (:func:`notice_text`, the same cases as the desk's read-out), with
  no OC error text, said once per wording and by one computer: the one that runs the action
  when it has a chat box, else the binding's one chat-box computer (D278). A
  waypoint (core-side, nothing happens in OC) and a ``say`` get none; free text in chat stays
  the ``say`` action, which needs Clayton's confirmation.

Every number on a display is a telemetry reading (``tel:`` cited in the Base view) or comes from
the quest guide's ranked frontier (``q:``), formatted here; nothing is estimated. Game text in
lines (machine, item and quest names) is data, made printable ASCII and cut to fit.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import unicodedata
import zlib
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.bridge.actions import (
    CRAFT_FINAL,
    MAY_STILL_RUN,
    RESULT_TIMED_OUT,
    Action,
    ActionError,
)
from gregtec.bridge.ingest import AgentId
from gregtec.bridge.live import Live
from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.quests import guide
from gregtec.vault.io import VaultIO

log = logging.getLogger("gregtec.bridge")

BOARD_W = 48
BOARD_LINES = 12
HUD_W = 40
HUD_LINES = 4
CHAT_W = 160
CHAT_PER_REPLY = 2
NOTICES_KEPT = 8
NOTICE_TTL_S = 60
CONTEXT_S = 30.0  # the goal and next step are looked up again this often
PENDING_POLL_S = (
    2  # the agent's wait while an action waits for "confirm" in its binding
)
LEVELS = ("ok", "warn", "bad", "info", "dim")
POWER_ROWS = 3
PROBLEM_ROWS = 4
CRAFT_ROWS = 2


def ascii_text(text: object, collapse: bool = True) -> str:
    """Printable ASCII on one line: accents dropped, other characters and codes removed (runs of
    spaces made one unless ``collapse`` is False, which keeps a line's own layout)."""
    s = str(text).replace("§", "\x00")  # a section sign starts a colour code
    out = []
    skip = False
    for ch in unicodedata.normalize("NFKD", s):
        if skip:
            skip = False
            continue
        if ch == "\x00":
            skip = True
            continue
        if " " <= ch <= "~":
            out.append(ch)
        elif ch in "\t\r\n":
            out.append(" ")
    joined = "".join(out)
    return " ".join(joined.split()) if collapse else joined.rstrip()


def fit(text: object, width: int, collapse: bool = True) -> str:
    s = ascii_text(text, collapse)
    return s if len(s) <= width else s[: width - 3].rstrip() + "..."


def eu(n: float) -> str:
    """An EU amount for a small screen: ``1,234``, ``12.3k``, ``4.56M`` ... (sign kept)."""
    sign = "-" if n < 0 else ""
    v = abs(n)
    if v < 10_000:
        return f"{sign}{round(v):,}"
    for unit, size in (
        ("E", 1e18),
        ("P", 1e15),
        ("T", 1e12),
        ("G", 1e9),
        ("M", 1e6),
        ("k", 1e3),
    ):
        if v >= size:
            q = v / size
            return f"{sign}{q:.3g}{unit}" if q < 1000 else f"{sign}{q:,.0f}{unit}"
    return f"{sign}{v:,.0f}"  # unreachable: v >= 10,000 always matched k


def signed(n: float) -> str:
    return ("+" if n > 0 else "") + eu(n)


@dataclass(frozen=True)
class Context:
    """What the board shows beyond telemetry: Clayton's first goal and the guide's next step."""

    goal: str | None = None
    next_step: str | None = None
    next_ref: str | None = None  # q:<id>


ContextSource = Callable[[], Context]
PendingSource = Callable[[], Action | None]


def quest_context(gos_home: Path) -> ContextSource:
    """The first goal (``me/goals.md``) and the top of the ranked frontier (WO-6.4's guide)."""

    def read() -> Context:
        root = gos_home / "vault"
        vault = VaultIO(root) if root.is_dir() else None
        goals = guide.read_goals(vault)
        goal = goals[0] if goals else None
        db = access.db_path(gos_home)
        if not db.is_file():
            return Context(goal)
        conn = access.connect(db, readonly=True)
        try:
            ranked = guide.ranked(conn, vault, db)
        except (quest_db.QuestError, sqlite3.Error) as exc:  # no quest book loaded yet
            log.info("next step not read", extra={"why": str(exc)[:200]})
            ranked = []
        finally:
            conn.close()
        top = ranked[0] if ranked else None
        return Context(goal, top.name if top else None, f"q:{top.id}" if top else None)

    return read


@dataclass
class _Notice:
    id: int
    text: str
    ts: float
    computer: str | None = None  # only this computer says it (None: any in the binding)


class Displays:
    """The ``display`` of each reply (see the module docstring). Thread-safe."""

    def __init__(
        self,
        live: Live,
        *,
        context: ContextSource | None = None,
        pending: PendingSource | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.live = live
        self.context = context
        self.pending = pending
        self.clock = clock
        self._lock = threading.Lock()
        self._ctx: tuple[float, Context] | None = None
        self._notices: dict[str, deque[_Notice]] = {}
        self._last_id = 0
        self._boots: dict[tuple[AgentId, str], float] = {}
        self._worded: dict[int, str] = {}  # action id -> the last notice queued for it

    # --- notices

    def notice(self, binding: str, text: str, computer: str | None = None) -> int:
        """Queue a chat notice for ``binding``'s chat box (only ``computer``'s, if given);
        returns its id."""
        with self._lock:
            return self._queue(binding, text, computer)

    def _queue(self, binding: str, text: str, computer: str | None) -> int:
        nid = max(self._last_id + 1, int(self.clock() * 1000))
        self._last_id = nid
        self._notices.setdefault(binding, deque(maxlen=NOTICES_KEPT)).append(
            _Notice(nid, fit(text, CHAT_W) or "GOS", self.clock(), computer)
        )
        return nid

    def on_action(self, action: Action, say: str) -> None:
        """The action desk's change hook: a notice when a confirmed OC action moves on, once
        per distinct wording (``say`` is the desk's read-out; the notice is worded from the
        action itself, see :func:`notice_text`)."""
        text = notice_text(action)
        if text is None:
            return
        with self._lock:
            if self._worded.get(action.id) == text:
                return
            self._worded[action.id] = text
            while len(self._worded) > 4 * NOTICES_KEPT:
                self._worded.pop(next(iter(self._worded)))
        speaker = self._speaker(action.target, action.computer)
        with self._lock:
            self._queue(action.target, text, speaker)

    def _speaker(self, binding: str, computer: str | None) -> str | None:
        """Which computer of ``binding`` says a notice about an action ``computer`` runs: that
        computer when its latest telemetry says it has a chat box (``display.chat``), else the
        binding's one chat-box computer (the rule ``locate`` uses for ``say``, D220), else
        None (the first agent with a chat box that hears it; D278)."""
        tel = self.live.telemetry
        if computer:
            msg = tel.latest.latest(AgentId(binding, computer))
            shows = msg.doc.get("display") if msg is not None else None
            if isinstance(shows, Mapping) and shows.get("chat") is True:
                return computer
        try:
            return tel.locate(binding, "say", {})
        except ActionError:
            return None

    # --- the reply's display

    def _context(self) -> Context:
        if self.context is None:
            return Context()
        now = time.monotonic()
        with self._lock:
            cached = self._ctx
        if cached is not None and now - cached[0] < CONTEXT_S:
            return cached[1]
        try:
            ctx = self.context()
        except Exception:
            log.exception("display context not read")
            ctx = cached[1] if cached is not None else Context()
        with self._lock:
            self._ctx = (now, ctx)
        return ctx

    def _pending(self, binding: str) -> Action | None:
        if self.pending is None:
            return None
        try:
            found = self.pending()
        except Exception:
            log.exception("pending action not read for the display")
            return None
        return found if found is not None and found.target == binding else None

    def poll_s(self, agent: AgentId) -> int | None:
        """:data:`PENDING_POLL_S` while an action waits for "confirm" in ``agent``'s binding:
        Clayton's answer (or its expiry) then reaches the board, and a confirmed command its
        computer, within a couple of seconds instead of a cycle (D277)."""
        return PENDING_POLL_S if self._pending(agent.binding) is not None else None

    def content(
        self, binding: str
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """(board lines, HUD lines) for ``binding`` now."""
        base = self.live.binding(binding)
        return board_lines(base, self._context(), self._pending(binding)), hud_lines(
            base, self._context(), self._pending(binding)
        )

    def for_agent(self, agent: AgentId) -> dict[str, Any] | None:
        """The ``display`` for the reply to ``agent``'s newest message, or None (nothing new,
        or an agent that draws nothing)."""
        msg = self.live.telemetry.latest.latest(agent)
        if msg is None:
            return None
        shows = msg.doc.get("display")
        if not isinstance(shows, Mapping):
            return None  # an agent without displays (or before WO-7.4)
        board, hud = self.content(agent.binding)
        has_board = isinstance(shows.get("board"), Mapping)
        has_hud = shows.get("hud") is True
        rev = revision(board if has_board else [], hud if has_hud else [])
        now = self.clock()
        with self._lock:
            key = (agent, msg.boot)
            # Its run started at most one cycle before its first message reached the core.
            started = self._boots.setdefault(
                key, msg.received_at - float(msg.doc.get("cycle_s") or 5)
            )
            while len(self._boots) > 128:
                self._boots.pop(next(iter(self._boots)))
            said = shows.get("said")
            said = said if isinstance(said, int) and not isinstance(said, bool) else 0
            chat = [
                {"id": n.id, "t": n.text}
                for n in self._notices.get(agent.binding, ())
                if n.id > said
                and n.ts >= started
                and now - n.ts <= NOTICE_TTL_S
                and n.computer in (None, agent.address)
            ][:CHAT_PER_REPLY]
        if shows.get("chat") is not True:
            chat = []
        if shows.get("rev") == rev and not chat:
            return None
        out: dict[str, Any] = {"rev": rev}
        if has_board:
            out["lines"] = board
        if has_hud:
            out["hud"] = hud
        if chat:
            out["chat"] = chat
        return out


def notice_text(action: Action) -> str | None:
    """The chat notice for ``action`` now, or None: a fixed template per outcome, the same
    cases the desk reads out (``_result_words``, ``_craft_words``, its timeouts), with the
    read-back and never the agent's error text. Waypoints (core-side) and ``say`` (its own
    chat line) get none, and so do actions never confirmed."""
    if action.kind in ("say", "waypoint"):
        return None
    rb = action.readback
    result = action.result if isinstance(action.result, Mapping) else {}
    if action.state == "confirmed":
        return f"GOS: Confirmed: {rb}"
    if action.state == "done":
        craft = result.get("craft")
        if craft in CRAFT_FINAL:
            if craft == "done":
                return f"GOS: Crafting done: {rb}"
            if craft == "canceled":
                return f"GOS: Crafting cancelled in AE2: {rb}"
            if MAY_STILL_RUN in str(result.get("craft_err") or ""):
                return f"GOS: Check AE2: {rb} (it may still run)"
            return f"GOS: Crafting failed: {rb} (see the dashboard)"
        if result.get("state") == "submitted":
            return f"GOS: Started: {rb} (AE2 is crafting it)"
        return f"GOS: Done: {rb}"
    if action.state == "failed":
        if result.get("why") == RESULT_TIMED_OUT:
            return f"GOS: No result for: {rb} (it may have run; check in game)"
        if MAY_STILL_RUN in str(result.get("err") or ""):
            return f"GOS: Check AE2: {rb} (it may still run)"
        return f"GOS: Failed: {rb} (see the dashboard)"
    return None  # proposed, cancelled, expired: nothing was confirmed or nothing ran


def revision(
    board: Sequence[Mapping[str, str]], hud: Sequence[Mapping[str, str]]
) -> int:
    """A 1..2^31-1 checksum of what the board and HUD show."""
    data = json.dumps([board, hud], sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )
    return zlib.crc32(data) % 2_147_483_646 + 1


def _line(text: str, level: str, width: int = BOARD_W) -> dict[str, str]:
    """One line; the game text in it was cleaned with :func:`fit` already, so its own padding
    stays."""
    return {"t": fit(text, width, collapse=False), "c": level}


def _power_text(p: Mapping[str, Any], width: int) -> tuple[str, str]:
    name = fit(p["name"], 16)
    pct = f"{p['pct']:.1f}%" if p.get("pct") is not None else "?%"
    net = None
    if p.get("eu_in") is not None and p.get("eu_out") is not None:
        net = p["eu_in"] - p["eu_out"]
    rate = f" {signed(net)} EU/t" if net is not None else ""
    level = (
        "dim" if p.get("stale") else ("warn" if net is not None and net < 0 else "ok")
    )
    return f"PWR  {name:<16} {pct:>6}{rate}", level


def board_lines(
    base: Mapping[str, Any], ctx: Context, pending: Action | None
) -> list[dict[str, str]]:
    """The status board: power, problems, the waiting action, goal, next step, AE2."""
    stale = base.get("status") == "stale"
    power: list[dict[str, str]] = []
    rows = sorted(base.get("power") or [], key=lambda p: p.get("kind") != "storage")
    for p in rows[:POWER_ROWS]:
        text, level = _power_text(p, BOARD_W)
        power.append(_line(text, level))
    if not rows:
        power.append(_line("PWR  no power readings", "dim"))
    problems: list[dict[str, str]] = []
    found = base.get("problems") or []
    for pr in found[:PROBLEM_ROWS]:
        problems.append(_line(f"!    {pr['text']}", pr["level"]))
    if len(found) > PROBLEM_ROWS:
        problems.append(
            _line(f"     and {len(found) - PROBLEM_ROWS} more problems", "dim")
        )
    if not found and base.get("status") == "connected":
        problems.append(_line("OK   no problems reported", "ok"))
    act: list[dict[str, str]] = []
    if pending is not None:
        what = (
            "a chat message (see the dashboard)"
            if pending.kind == "say"
            else pending.readback
        )
        act.append(_line(f"ACT  Say confirm: {what}", "warn"))
    plan: list[dict[str, str]] = []
    if ctx.goal:
        plan.append(_line(f"GOAL {ctx.goal}", "info"))
    if ctx.next_step:
        plan.append(_line(f"NEXT {ctx.next_step}", "info"))
    ae2: list[dict[str, str]] = []
    a = base.get("ae2")
    if isinstance(a, Mapping):
        ae2.append(
            _line(
                f"AE2  {a['busy']}/{a['cpus']} crafting CPUs busy",
                "dim" if stale else "info",
            )
        )
        for c in [
            c
            for c in a.get("crafts") or []
            if c["state"] in ("computing", "crafting", "check_ae2", "failed")
        ][:CRAFT_ROWS]:
            word = {"check_ae2": "check AE2"}.get(c["state"], c["state"])
            level = "warn" if c["state"] in ("check_ae2", "failed") else "info"
            ae2.append(_line(f"CRFT {c['amount']} {c['name']}: {word}", level))
    # Priority when the board is full: power, the waiting action, problems, goal and next, AE2.
    room = BOARD_LINES
    keep: dict[str, list[dict[str, str]]] = {}
    for name, part in (
        ("power", power),
        ("act", act),
        ("problems", problems),
        ("plan", plan),
        ("ae2", ae2),
    ):
        keep[name] = part[: max(0, room)]
        room -= len(keep[name])
    lines = keep["power"] + keep["problems"] + keep["act"] + keep["plan"] + keep["ae2"]
    if stale:
        lines = [{"t": ln["t"], "c": "dim"} for ln in lines]
    return lines


def hud_lines(
    base: Mapping[str, Any], ctx: Context, pending: Action | None
) -> list[dict[str, str]]:
    """The OCGlasses HUD: a few short lines, so few widgets (power scales with them)."""
    out: list[dict[str, str]] = []
    rows = sorted(base.get("power") or [], key=lambda p: p.get("kind") != "storage")
    if rows:
        p = rows[0]
        pct = f"{p['pct']:.1f}%" if p.get("pct") is not None else "?%"
        net = ""
        if p.get("eu_in") is not None and p.get("eu_out") is not None:
            net = f" {signed(p['eu_in'] - p['eu_out'])} EU/t"
        out.append(
            _line(f"GOS PWR {pct}{net}", "dim" if p.get("stale") else "ok", HUD_W)
        )
    found = base.get("problems") or []
    if found:
        more = f" (+{len(found) - 1})" if len(found) > 1 else ""
        text = found[0]["text"]
        out.append(
            _line(
                f"! {fit(text, HUD_W - 3 - len(more))}{more}", found[0]["level"], HUD_W
            )
        )
    elif base.get("status") == "connected":
        out.append(_line("OK no problems", "ok", HUD_W))
    if pending is not None:
        out.append(_line("Say confirm: see the board", "warn", HUD_W))
    elif ctx.next_step:
        out.append(_line(f"NEXT {ctx.next_step}", "info", HUD_W))
    if base.get("status") == "stale":
        out = [{"t": ln["t"], "c": "dim"} for ln in out]
    return out[:HUD_LINES]
