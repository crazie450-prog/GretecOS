"""The alert router (WO-9.2, plan 7.12 "Alerts", 7.5, 7.9; D46, D48, D71, D76, D281, D298-D300).

One router in the core takes every alert GOS raises and sends each to the channels
:func:`gregtec.alerts.policy.decide` allows: the **dashboard** (it is on screen already: the
alert engine's cards, the notices' cards, the credit banner, the pause chip), **voice** (a line
pushed to the voice client), **in game** (a GregtecLink chat line, D76) and a **Discord DM**
(an outbox the bot posts from, :mod:`gregtec.discordbot.alerts`).

**What comes in.** Every alert has a key, an occurrence and a level:

- the alert engine's events (WO-7.5, :attr:`AlertEngine.sinks`): a ``fired`` alert is active
  until its ``cleared``; key ``<binding>/<rule>:<subject>``, occurrence its firing time;
- the proactive notices (WO-6.5): one-shot, active for :data:`NOTICE_TTL_S`; ``power_deficit``
  is ``warn``, the opportunities ``info``;
- the D48 credit alert, read from the credit watch each tick: active while a threshold waits for
  his answer; key ``credits:<pct>``, occurrence the cycle, so once per threshold per cycle;
- the paused game (D258, WO-6.1), read from the watchers' feed each tick: active while paused;
  key ``game_paused:<instance>``, occurrence the pause's start, so once per pause (D76).

"GOS offline" can't come from the core (it is the core that is down): the Discord bot sends it
(:class:`gregtec.discordbot.alerts.OfflineWatch`), with the same policy.

**Level-triggered** (:meth:`AlertRouter.tick`, once a second): each active alert is offered to
each channel it hasn't reached; :func:`~gregtec.alerts.policy.decide` says yes or why not, and a
"not now" (present, quiet hours, ``/alerts quiet``) is asked again next tick. So D76's "sent once,
when both conditions are true, whichever comes second" falls out: the paused DM goes on the first
tick that finds the game paused *and* him away, in either order, and only once per pause.

**Once.** What went where is the ``alert_route`` table (channel, key, occurrence), so a core
restart never sends it again; a base alert's key also waits ``[notify] cooldown_s`` per channel
before a new occurrence goes there. The alert engine fires afresh after a restart (D281: old rows
``lost``), so the router keeps the keys that were firing (``kv`` ``alerts.routing``) and, for
:data:`ADOPT_S` after it starts, a key that fires again is the same occurrence: not repeated.

**Grouped.** Base alerts due in one tick on one binding are one voice line, one chat line and
one DM (the engine's :func:`~gregtec.alerts.engine.summary`: "Lab: power low and 6 machines
stopped."), so a power loss is one message, not seven.

**The outbox** (``alert_dm``): one row per DM, with its text, buttons and the alerts it carries.
The bot lists the undelivered ones (``GET /api/alerts/outbox``), posts each, then marks it
(``POST /api/alerts/outbox/{id}/delivered``): at least once, like WO-9.1's jobs (D295). A row
whose alerts all ended (cleared, answered, unpaused) before it was posted is dropped, as is one
older than :data:`OUTBOX_TTL_S`, so a bot that was down never posts stale news.

Game text (machine and item names) is data in fixed templates (plan 0.1 principle 8); every
telemetry number is OC's, cited ``tel:``; credit numbers are the meter's (D118).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gregtec.alerts import policy as pol
from gregtec.alerts.engine import Alert as EngineAlert
from gregtec.alerts.engine import AlertEvent, summary
from gregtec.alerts.policy import credit_buttons
from gregtec.alerts.rules import place
from gregtec.presence import IdleSource, NoIdle, is_away
from gregtec.store import Store

log = logging.getLogger("gregtec.alerts.router")

NOTICE_TTL_S = 600  # a notice stays deliverable this long (the notices' own cooldown)
ADOPT_S = (
    600.0  # after a start, a key that was firing and fires again is the same occurrence
)
OUTBOX_TTL_S = 6 * 3600  # an undelivered DM older than this is dropped
KEEP_S = 30 * 86400  # route and outbox rows kept
TEST_TTL_S = 300
KV_OPEN = "alerts.routing"
DM_MAX = 1900  # Discord's limit is 2000 characters
MARK = {"err": "[!]", "warn": "[warn]", "info": "[info]"}
PUSH = ("voice", "in_game", "discord")  # the channels the router sends to itself


@dataclass
class Routed:
    """One alert occurrence as the router routes it."""

    key: str
    occ: str
    kind: str
    level: str
    title: str
    text: str  # the DM's body (fixed templates, cited)
    say: str | None  # the voice and in-game line
    since: float
    binding: str | None = None
    until: float | None = None  # one-shot: active until then
    buttons: tuple[dict[str, Any], ...] = ()
    src: EngineAlert | None = (
        None  # a telemetry alert's engine copy (for summary lines)
    )
    why: dict[str, str] = field(default_factory=dict)  # channel -> why not (yet)

    def as_dict(self, sent: Mapping[str, int]) -> dict[str, Any]:
        return {
            "key": self.key,
            "occ": self.occ,
            "kind": self.kind,
            "level": self.level,
            "title": self.title,
            "binding": self.binding,
            "since": int(self.since),
            "until": int(self.until) if self.until is not None else None,
            "sent": dict(sent),
            "held": dict(self.why),
        }


def _clip(text: str, limit: int = DM_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _hhmm(ts: float, local: Callable[[float], datetime]) -> str:
    return local(ts).strftime("%H:%M")


def credit_text(state: Mapping[str, Any]) -> str:
    pct, spent, budget = (
        state.get("pct"),
        state.get("spent_usd"),
        state.get("budget_usd"),
    )
    line = (
        f"API credits: {float(pct or 0):.0f}% of this cycle's included ${float(budget or 0):.2f} "
        f"used (${float(spent or 0):.2f}; GOS's own meter, D118)."
    )
    if state.get("paused"):
        line += " GOS is in no-AI mode until you answer: lookups, recipe cards and saved plans still work."
    else:
        line += " Nothing changes until you answer."
    return line + " GOS never buys credits."


class AlertRouter:
    """See the module docstring. Thread-safe: the engine's sink and the notices call in from
    their threads, :meth:`tick` from the core's watcher."""

    def __init__(
        self,
        store: Store,
        policy: pol.NotifyPolicy | None = None,
        *,
        idle: IdleSource | None = None,
        clock: Callable[[], float] = time.time,
        local: Callable[[float], datetime] = datetime.fromtimestamp,
    ) -> None:
        self.store = store
        self.policy = policy or pol.NotifyPolicy()
        self.idle = idle or NoIdle()
        self.clock = clock
        self.local = local
        # Sources the core sets (None: that source isn't there).
        self.credits: Callable[[], Mapping[str, Any] | None] | None = None
        self.paused: Callable[[], tuple[str, float | None] | None] | None = None
        self.discord_mode: Callable[[], str] = self._mode_from_kv
        self.voice_up: Callable[[], bool] = lambda: False
        self.game_up: Callable[[], bool] = lambda: False
        # Senders the core sets: each returns whether the line reached someone.
        self.say: Callable[[str], bool] = lambda text: False
        self.chat: Callable[[str], bool] = lambda text: False
        self._lock = threading.RLock()
        self._active: dict[str, Routed] = {}  # key -> its open occurrence
        self._sent: dict[
            tuple[str, str], dict[str, int]
        ] = {}  # (key, occ) -> channel: ts
        self._last: dict[tuple[str, str], int] = {}  # (channel, key) -> last sent ts
        self._closed: set[tuple[str, str]] = set()  # (key, occ) that ended in this run
        self.started = clock()
        self._load()

    # --- state kept across restarts

    def _load(self) -> None:
        now = int(self.clock())
        try:
            self.store.try_write(
                "DELETE FROM alert_route WHERE ts < ?", (now - KEEP_S,)
            )
            self.store.try_write(
                "DELETE FROM alert_dm WHERE created < ?", (now - KEEP_S,)
            )
        except sqlite3.Error as exc:
            log.info("old alert routes not pruned", extra={"why": str(exc)[:200]})
        for row in self.store.rows("SELECT channel, key, occ, ts FROM alert_route"):
            self._sent.setdefault((row["key"], row["occ"]), {})[row["channel"]] = row[
                "ts"
            ]
            last = self._last.get((row["channel"], row["key"]), 0)
            self._last[(row["channel"], row["key"])] = max(last, row["ts"])
        rows = self.store.rows("SELECT value FROM kv WHERE key = ?", (KV_OPEN,))
        self._adoptable: dict[str, str] = {}
        if rows:
            try:
                saved = json.loads(rows[0]["value"])
                if isinstance(saved, dict):
                    self._adoptable = {str(k): str(v) for k, v in saved.items()}
            except ValueError:
                pass

    def _save_open(self) -> None:
        opened = {
            k: r.occ for k, r in self._active.items() if r.src is not None
        }  # telemetry alerts only: the others' occurrences are their own
        opened.update(self._adoptable)
        try:
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) "
                    "DO UPDATE SET value = excluded.value, upd = excluded.upd",
                    (KV_OPEN, json.dumps(opened), int(self.clock())),
                )
        except sqlite3.Error as exc:
            log.info("open alerts not saved", extra={"why": str(exc)[:200]})

    def _mode_from_kv(self) -> str:
        from gregtec.remote.routes import alerts_state

        return str(alerts_state(self.store, self.clock())["mode"])

    # --- what comes in

    def alert_event(self, event: AlertEvent) -> None:
        """The alert engine's sink (WO-7.5, D281)."""
        a = event.alert
        if a is None or event.kind not in ("fired", "cleared"):
            return  # stale and fresh: a held alert stays active as it was
        key = f"{a.binding}/{a.key}"
        with self._lock:
            if event.kind == "cleared":
                self._close(key)
                self._adoptable.pop(key, None)
                self._save_open()
                return
            occ = str(int(a.fired_at))
            if key in self._adoptable and self.clock() - self.started < ADOPT_S:
                occ = self._adoptable[key]  # fired again after a restart: the same one
            self._adoptable.pop(key, None)
            self._open(
                Routed(
                    key=key,
                    occ=occ,
                    kind=a.rule,
                    level=a.level,
                    title=a.title,
                    text=f"{MARK[a.level]} {a.title}\n{a.body}",
                    say=a.say,
                    since=a.fired_at,
                    binding=a.binding,
                    src=dataclasses.replace(a),
                )
            )
            self._save_open()

    def notice(self, n: Any) -> None:  # noqa: ANN401 - gregtec.advisor.notices.Notice
        """A proactive notice (WO-6.5): one-shot, deliverable for :data:`NOTICE_TTL_S`."""
        level = "warn" if n.kind == "power_deficit" else "info"
        detail = " ".join(str(d) for d in (n.as_dict().get("detail") or ())[:3])
        refs = " ".join(n.refs[:4])
        text = f"{MARK[level]} {n.title}\n{detail} {refs}".strip()
        with self._lock:
            self._open(
                Routed(
                    key=f"notice/{n.kind}",
                    occ=str(int(n.ts)),
                    kind=n.kind,
                    level=level,
                    title=n.title,
                    text=_clip(text),
                    say=n.say or None,
                    since=float(n.ts),
                    until=float(n.ts) + NOTICE_TTL_S,
                )
            )

    def test(self, text: str = "Test alert from GOS: Discord DMs reach you.") -> None:
        """``gos alerts test``: one DM, whatever his presence (runbook step)."""
        now = self.clock()
        with self._lock:
            self._open(
                Routed(
                    key="test",
                    occ=str(int(now)),
                    kind="test",
                    level="info",
                    title="Test alert",
                    text=_clip(" ".join(text.split())),
                    say=None,
                    since=now,
                    until=now + TEST_TTL_S,
                )
            )

    def _open(self, r: Routed) -> None:
        old = self._active.get(r.key)
        if old is not None and old.occ != r.occ:
            self._closed.add((old.key, old.occ))
        self._active[r.key] = r

    def _close(self, key: str) -> None:
        old = self._active.pop(key, None)
        if old is not None:
            self._closed.add((old.key, old.occ))

    def _poll(self, now: float) -> None:
        """The credit alert and the paused game, read from their owners."""
        credit = self.credits() if self.credits is not None else None
        want: set[str] = set()
        if credit and credit.get("pending") and credit.get("threshold") is not None:
            pct = int(credit["threshold"])
            key = f"credits:{pct}"
            want.add(key)
            occ = str(credit.get("cycle_start") or "")[:10]
            if key not in self._active or self._active[key].occ != occ:
                self._open(
                    Routed(
                        key=key,
                        occ=occ,
                        kind="credits",
                        level="err" if pct >= 100 else "warn",
                        title=f"API credits at {pct}%",
                        text=f"{MARK['err' if pct >= 100 else 'warn']} {credit_text(credit)}",
                        say=None,
                        since=now,
                        buttons=credit_buttons(credit),
                    )
                )
            else:  # the buttons follow the banner (No-AI mode becomes Resume AI)
                self._active[key].buttons = credit_buttons(credit)
        game = self.paused() if self.paused is not None else None
        if game is not None:
            instance, since = game
            key = f"game_paused:{instance}"
            want.add(key)
            occ = str(int(since)) if since is not None else "unknown"
            if key not in self._active or self._active[key].occ != occ:
                where = place(instance)
                when = f" since {_hhmm(since, self.local)}" if since is not None else ""
                self._open(
                    Routed(
                        key=key,
                        occ=occ,
                        kind="game_paused",
                        level="warn",
                        title=f"{where} is paused",
                        text=(
                            f"{MARK['warn']} {where} is paused{when} (the Esc menu, or the "
                            "game lost focus): OC telemetry and the AFK base alerts stop "
                            "until it runs again (D46)."
                        ),
                        say=None,
                        since=since if since is not None else now,
                        binding=instance,
                    )
                )
        for key in [
            k for k, r in self._active.items() if r.kind in ("credits", "game_paused")
        ]:
            if key not in want:
                self._close(key)

    # --- routing

    def context(self, now: float) -> pol.Context:
        try:
            idle = self.idle.idle_s()
        except OSError:
            idle = None
        try:
            mode = self.discord_mode()
        except sqlite3.Error:
            mode = "on"
        return pol.Context(
            away=is_away(idle),
            quiet_hours=self.policy.in_quiet_hours(self.local(now)),
            discord_mode=mode if mode in pol.DISCORD_MODES else "on",
            voice_up=self.voice_up(),
            game_up=self.game_up(),
        )

    def _cooling(self, channel: str, r: Routed, now: float) -> bool:
        if pol.group_of(r.kind) != "base":
            return False
        last = self._last.get((channel, r.key))
        return last is not None and now - last < self.policy.cooldown_s

    def tick(self) -> dict[str, list[Routed]]:
        """Route every active alert once; returns what went to each channel."""
        now = self.clock()
        with self._lock:
            self._poll(now)
            for key in [k for k, r in self._active.items() if r.until is not None]:
                if now >= self._active[key].until:  # type: ignore[operator]
                    self._close(key)
            if self._adoptable and now - self.started >= ADOPT_S:
                self._adoptable.clear()  # those didn't fire again: they ended while down
                self._save_open()
            ctx = self.context(now)
            due: dict[str, list[Routed]] = {c: [] for c in PUSH}
            for r in self._active.values():
                sent = self._sent.get((r.key, r.occ), {})
                for channel in PUSH:
                    if channel in sent:
                        continue
                    verdict = pol.decide(
                        channel, r.kind, r.level, ctx, self.policy, sent.keys()
                    )
                    if verdict == pol.SEND and self._cooling(channel, r, now):
                        verdict = "cooldown"
                    if verdict != pol.SEND:
                        r.why[channel] = verdict
                        continue
                    r.why.pop(channel, None)
                    due[channel].append(r)
            done = {c: self._send(c, due[c], now) for c in PUSH}
            return done

    def _groups(self, items: list[Routed]) -> list[list[Routed]]:
        """Base alerts of one binding together; everything else alone."""
        groups: dict[str, list[Routed]] = {}
        alone: list[list[Routed]] = []
        for r in items:
            if r.src is not None and r.binding is not None:
                groups.setdefault(r.binding, []).append(r)
            else:
                alone.append([r])
        return [*groups.values(), *alone]

    def _line(self, group: list[Routed]) -> str | None:
        if len(group) == 1:
            return group[0].say
        alerts = [r.src for r in group if r.src is not None]
        return summary(place(group[0].binding or ""), alerts)

    def _send(self, channel: str, items: list[Routed], now: float) -> list[Routed]:
        sent: list[Routed] = []
        for group in self._groups(items):
            if channel == "discord":
                ok = self._dm(group, now)
            else:
                line = self._line(group)
                if not line:
                    ok = True  # nothing to say there (a credit alert has no line)
                else:
                    sender = self.say if channel == "voice" else self.chat
                    try:
                        ok = sender(line[:300])
                    except Exception:  # a sender must never stop the routing
                        log.exception(
                            "alert channel failed", extra={"channel": channel}
                        )
                        ok = False
            if ok:
                self._mark(channel, group, now)
                sent += group
        return sent

    def _dm(self, group: list[Routed], now: float) -> bool:
        if len(group) == 1:
            text = group[0].text
        else:
            head = f"{place(group[0].binding or '')}: {len(group)} alerts"
            text = "\n".join([head, *(r.text for r in group)])
        level = max((r.level for r in group), key=pol.rank)
        buttons = [b for r in group for b in r.buttons]
        keys = [[r.key, r.occ] for r in group]
        try:
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO alert_dm(created, level, text, buttons, keys) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        int(now),
                        level,
                        _clip(text),
                        json.dumps(buttons),
                        json.dumps(keys),
                    ),
                )
                for r in group:
                    conn.execute(
                        "INSERT OR IGNORE INTO alert_route(channel, key, occ, ts) "
                        "VALUES ('discord', ?, ?, ?)",
                        (r.key, r.occ, int(now)),
                    )
        except sqlite3.Error as exc:
            log.warning("alert DM not queued", extra={"why": str(exc)[:200]})
            return False
        log.info("alert DM queued", extra={"keys": [r.key for r in group]})
        return True

    def _mark(self, channel: str, group: list[Routed], now: float) -> None:
        for r in group:
            self._sent.setdefault((r.key, r.occ), {})[channel] = int(now)
            self._last[(channel, r.key)] = int(now)
        if channel == "discord":
            return  # written with its outbox row
        try:
            with self.store.transaction() as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO alert_route(channel, key, occ, ts) "
                    "VALUES (?, ?, ?, ?)",
                    [(channel, r.key, r.occ, int(now)) for r in group],
                )
        except sqlite3.Error as exc:
            log.info("alert route not recorded", extra={"why": str(exc)[:200]})

    # --- the outbox, for the Discord bot

    def outbox(self) -> list[dict[str, Any]]:
        """Undelivered DMs, oldest first. A DM whose alerts all ended in this run, or older
        than :data:`OUTBOX_TTL_S`, is dropped instead (marked ``dropped``)."""
        now = int(self.clock())
        rows = self.store.rows(
            "SELECT id, created, level, text, buttons, keys FROM alert_dm "
            "WHERE delivered IS NULL AND dropped IS NULL ORDER BY id"
        )
        out, drop = [], []
        with self._lock:
            for row in rows:
                keys = {tuple(k) for k in json.loads(row["keys"] or "[]")}
                ended = bool(keys) and keys <= self._closed
                if ended or now - row["created"] > OUTBOX_TTL_S:
                    drop.append(row["id"])
                    continue
                out.append(
                    {
                        "id": row["id"],
                        "created": row["created"],
                        "level": row["level"],
                        "text": row["text"],
                        "buttons": json.loads(row["buttons"] or "[]"),
                    }
                )
        if drop:
            marks = ", ".join("?" for _ in drop)
            self.store.try_write(
                f"UPDATE alert_dm SET dropped = ? WHERE id IN ({marks})", (now, *drop)
            )
        return out

    def delivered(self, dm_id: int) -> bool:
        with self.store.transaction() as conn:
            cur = conn.execute(
                "UPDATE alert_dm SET delivered = ? WHERE id = ? AND delivered IS NULL",
                (int(self.clock()), dm_id),
            )
            return cur.rowcount == 1

    def view(self) -> dict[str, Any]:
        """``GET /api/alerts/routed``: the moment, the active alerts, where each went or why not."""
        now = self.clock()
        with self._lock:
            ctx = self.context(now)
            active = [
                r.as_dict(self._sent.get((r.key, r.occ), {}))
                for r in self._active.values()
            ]
        return {"context": dataclasses.asdict(ctx), "active": active}
