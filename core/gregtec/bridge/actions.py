"""World actions and the confirm flow (WO-7.1; plan 7.9 "Safe actions", D25, D59, D214, D220).

The ``action`` table (Appendix D, plus ``target``, ``computer``, ``readback``, ``source``,
``sent_ts`` and ``done_ts``) holds every action; :class:`ActionDesk` is the only code that
changes it. The life of one action::

    proposed --confirm--> confirmed --take--> sent --result--> done | failed
        |                    |    ^             |
        |                    |    +--returned---+  (a reply that never reached the agent)
        +--cancel / a newer proposal--> cancelled
        +--60 s--> expired       confirmed 5 min unsent, or sent 5 min with no result --> failed

- **Propose** (:meth:`ActionDesk.propose`): an agent's ``action_propose`` tool, never more. The
  kinds are plan 7.9's allowlist only (:data:`KINDS`: ``craft``, ``machine_pause``,
  ``machine_resume``, ``say``, ``waypoint``); nothing destructive exists. The arguments are
  checked against the OC contract's command schema (``waypoint``: :mod:`.waypoint`), the OC
  computer that will run it is chosen from the latest telemetry (the one that reports that
  machine, the AE2 network or the chat box), and GOS reads it back: "Craft 64 Stainless Steel
  Ingot. Say confirm." Only one action is ever pending: a new proposal cancels the old one and
  says so ("Cancelled: ...").
- **Confirm** (:meth:`ActionDesk.confirm`): only with ``via`` one of :data:`VIAS` (``voice``,
  ``dashboard``, ``phone``, ``discord``), which only the core's own routes pass: the router's
  instant command "confirm", the approval card's button and the confirm-by-id API the phone
  and Discord buttons call. A button names the action it confirms (by id), so an old card can't
  confirm a newer action; plain "confirm" takes whatever is pending, once it has been pending
  :data:`SETTLE_S` (a younger one, perhaps replacing what Clayton heard, is read back again
  instead: "A new action just replaced it: ... Say confirm."). No tool, agent or OC
  message can reach it: the agents' tools only propose and read, and an OC message only reports
  results. A pending action expires 60 s after it was proposed and can't be confirmed after.
- **Queue and deliver** (:class:`gregtec.bridge.ingest.Commands`): a confirmed ``waypoint`` runs
  at once (the core writes the file); the others wait for their OC computer's next message and
  go out in its reply (HTTP or the M1 inbox) once (D214's at-most-once delivery). A command no
  reply carried goes back under the same id.
- **Results** (:meth:`ActionDesk.results`): the agent's ``results`` and ``crafts`` from the
  telemetry of the same binding and computer finish an action; a craft's progress is kept in its
  result. Every change is logged, appended to the day's journal (``log/YYYY-MM-DD.md``) and
  announced (:attr:`ActionDesk.on_change`: the dashboards and, with WO-5.3, the voice).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gregtec.bridge import waypoint
from gregtec.bridge.ingest import AgentId, validators
from gregtec.store import Store

log = logging.getLogger("gregtec.bridge.actions")

OC_KINDS = (
    "craft",
    "machine_pause",
    "machine_resume",
    "say",
)  # the contract's commands
KINDS = (*OC_KINDS, "waypoint")  # plan 7.9's safe actions; nothing destructive exists
VIAS = ("voice", "dashboard", "phone", "discord")  # how Clayton confirms (D59, D214)
TARGETS = ("play", "lab")
PENDING_S = 60  # a proposal Clayton hasn't confirmed by then expires (D59)
SETTLE_S = (
    3  # a plain "confirm" won't take a proposal younger than this (read back first)
)
QUEUE_S = (
    300  # a confirmed action its OC computer hasn't collected by then fails, unrun
)
RESULT_S = 300  # a sent action with no result by then: "timed out; may have run" (D214)
SAY_CONFIRM = "Say confirm."
NOTHING_PENDING = "Nothing is waiting for confirmation."
STATES = ("proposed", "confirmed", "sent", "done", "failed", "cancelled", "expired")
FINAL = ("done", "failed", "cancelled", "expired")
CRAFT_FINAL = ("done", "canceled", "failed")
MAY_STILL_RUN = "may still run"


class ActionError(ValueError):
    """A proposal the desk won't take; the message says why (the agent reads it)."""


@dataclass(frozen=True)
class Action:
    id: int
    created: int
    kind: str
    args: dict[str, Any]
    state: str
    target: str
    computer: str | None
    readback: str
    source: str
    confirmed_via: str | None
    confirmed_ts: int | None
    sent_ts: int | None
    done_ts: int | None
    result: dict[str, Any] | None

    @property
    def expires(self) -> int:
        return self.created + PENDING_S

    def command(self) -> dict[str, Any]:
        """The contract's command (D214); only for a confirmed OC action."""
        if self.kind not in OC_KINDS or self.confirmed_via not in VIAS:
            raise AssertionError(f"action {self.id} is not a confirmed OC command")
        return {
            "id": self.id,
            "kind": self.kind,
            "args": self.args,
            "confirmed_via": self.confirmed_via,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created": self.created,
            "kind": self.kind,
            "args": self.args,
            "state": self.state,
            "target": self.target,
            "computer": self.computer,
            "readback": self.readback,
            "source": self.source,
            "confirmed_via": self.confirmed_via,
            "confirmed_ts": self.confirmed_ts,
            "sent_ts": self.sent_ts,
            "done_ts": self.done_ts,
            "expires": self.expires if self.state == "proposed" else None,
            "result": self.result,
        }


@dataclass(frozen=True)
class Outcome:
    """What a propose, confirm or cancel did: the words to say, and the actions it changed."""

    ok: bool
    text: str
    action: Action | None = None
    cancelled: Action | None = None  # the proposal a new one replaced


# (binding, kind, args) -> the OC computer's address, or ActionError (the telemetry's).
Locator = Callable[[str, str, Mapping[str, Any]], str]
# (registry name, damage) -> the item's English name, or None (the game DB's).
Namer = Callable[[str, int], str | None]


def _no_locator(binding: str, kind: str, args: Mapping[str, Any]) -> str:
    raise ActionError("no OC telemetry is coming in, so no OC computer can run that")


def _no_names(name: str, damage: int) -> str | None:
    return None


def check_args(kind: str, args: object) -> dict[str, Any]:
    """``args`` for ``kind`` as the contract (or :mod:`.waypoint`) allows them, or ActionError."""
    if kind not in KINDS:
        raise ActionError(
            f"{kind!r} is not a safe action; the actions are {', '.join(KINDS)}"
        )
    if not isinstance(args, dict):
        raise ActionError("args must be an object")
    if kind == "waypoint":
        from jsonschema import Draft202012Validator

        error = next(
            iter(Draft202012Validator(waypoint.ARGS_SCHEMA).iter_errors(args)), None
        )
        if error is not None:
            raise ActionError(f"waypoint args: {error.message[:200]}")
        try:
            waypoint.check(args)
        except waypoint.WaypointError as exc:
            raise ActionError(str(exc)) from None
        return dict(args)
    # The contract's own check of a command, with a stand-in id and confirmation.
    probe = {"id": 1, "kind": kind, "args": args, "confirmed_via": VIAS[0]}
    error = next(iter(validators()["command"].iter_errors(probe)), None)
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path) or "args"
        raise ActionError(
            f"{kind} args don't fit the OC contract at {where}: {error.message[:200]}"
        )
    return dict(args)


class ActionDesk:
    """The action table and the confirm flow. Thread-safe: one change at a time.

    ``locate`` picks the OC computer for an action from the telemetry; ``name`` gives an
    item's name for a read-back; ``mark`` writes a waypoint (``(target, args) -> what it
    did``); ``journal`` appends lines to the day's journal; ``on_change`` gets each changed
    action and the words to announce. All of them are optional, and a failing one is logged,
    never the change's failure (but a failing ``mark`` fails its waypoint action).
    """

    def __init__(
        self,
        store: Store,
        *,
        locate: Locator = _no_locator,
        name: Namer = _no_names,
        mark: Callable[[str, Mapping[str, Any]], str] | None = None,
        journal: Callable[[list[str]], None] | None = None,
        on_change: Callable[[Action, str], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.locate = locate
        self.name = name
        self.mark = mark
        self.journal = journal
        self.on_change = on_change
        self.clock = clock
        self._lock = threading.RLock()
        self._recover()

    # --- reading

    def get(self, action_id: int) -> Action | None:
        rows = self.store.rows(
            f"SELECT {_COLUMNS} FROM action WHERE id = ?", (action_id,)
        )
        return _action(rows[0]) if rows else None

    def recent(self, limit: int = 20) -> list[Action]:
        rows = self.store.rows(
            f"SELECT {_COLUMNS} FROM action ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        )
        return [_action(r) for r in rows]

    def pending(self) -> Action | None:
        """The one proposed action, if it hasn't expired."""
        with self._lock:
            self.tick()
            rows = self.store.rows(
                f"SELECT {_COLUMNS} FROM action WHERE state = 'proposed' ORDER BY id DESC LIMIT 1"
            )
            return _action(rows[0]) if rows else None

    # --- the flow

    def propose(
        self, kind: str, args: object, *, target: str = "play", source: str = "agent"
    ) -> Outcome:
        """Record a proposal and read it back; a pending one is cancelled, out loud."""
        checked = check_args(kind, args)
        if target not in TARGETS:
            raise ActionError(f"target must be {' or '.join(TARGETS)}")
        computer = None
        if kind in OC_KINDS:
            computer = self.locate(target, kind, checked)
        elif self.mark is None:
            raise ActionError("this core can't write waypoints")
        phrase = self.describe(kind, checked, target)
        with self._lock:
            self.tick()
            now = int(self.clock())
            old = self._proposed()
            with self.store.transaction() as conn:
                if old is not None:
                    conn.execute(
                        "UPDATE action SET state = 'cancelled', done_ts = ?, result = ? "
                        "WHERE id = ? AND state = 'proposed'",
                        (now, _json({"why": "replaced by a newer proposal"}), old.id),
                    )
                cur = conn.execute(
                    "INSERT INTO action(created, kind, args, state, target, computer, readback, "
                    "source) VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?)",
                    (now, kind, _json(checked), target, computer, phrase, source[:40]),
                )
                new_id = int(cur.lastrowid or 0)
            new = self.get(new_id)
            assert new is not None
            cancelled = self.get(old.id) if old is not None else None
        text = f"{phrase}. {SAY_CONFIRM}"
        if cancelled is not None:
            text = f"Cancelled: {cancelled.readback}. {text}"
            self._changed(cancelled, f"Cancelled: {cancelled.readback}.")
        self._changed(new, text)
        return Outcome(True, text, new, cancelled)

    def confirm(self, via: str, action_id: int | None = None) -> Outcome:
        """Clayton confirms the pending action (``action_id``: only if it is that one)."""
        if via not in VIAS:
            raise ValueError(f"confirmations come by {', '.join(VIAS)}, not {via!r}")
        with self._lock:
            self.tick()
            pending = self._proposed()
            if pending is None or (action_id is not None and action_id != pending.id):
                return Outcome(False, self._not_pending(action_id))
            now = int(self.clock())
            if action_id is None and now - pending.created < SETTLE_S:
                # A plain "confirm" names no action: one proposed this recently may not have
                # been read back yet (an async deep lane, a second proposal in one turn).
                replaced = self.store.rows(
                    "SELECT 1 FROM action WHERE state = 'cancelled' AND id < ? AND "
                    "done_ts >= ? AND result LIKE '%replaced by a newer proposal%' LIMIT 1",
                    (pending.id, pending.created),
                )
                lead = "A new action just replaced it" if replaced else "Just proposed"
                return Outcome(
                    False, f"{lead}: {pending.readback}. {SAY_CONFIRM}", pending
                )
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE action SET state = 'confirmed', confirmed_via = ?, confirmed_ts = ? "
                    "WHERE id = ? AND state = 'proposed'",
                    (via, now, pending.id),
                )
            action = self.get(pending.id)
            assert action is not None
            if action.kind == "waypoint":
                return self._run_waypoint(action)
        text = f"Confirmed: {action.readback}. It goes to the {action.target} OC computer next."
        self._changed(action, text)
        return Outcome(True, text, action)

    def cancel(self, via: str | None = None, action_id: int | None = None) -> Outcome:
        """Clayton cancels the pending action (``action_id``: only if it is that one)."""
        with self._lock:
            self.tick()
            pending = self._proposed()
            if pending is None or (action_id is not None and action_id != pending.id):
                return Outcome(False, self._not_pending(action_id))
            now = int(self.clock())
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE action SET state = 'cancelled', done_ts = ?, result = ? "
                    "WHERE id = ? AND state = 'proposed'",
                    (
                        now,
                        _json({"why": f"cancelled by {via or 'Clayton'}"}),
                        pending.id,
                    ),
                )
            action = self.get(pending.id)
            assert action is not None
        text = f"Cancelled: {action.readback}."
        self._changed(action, text)
        return Outcome(True, text, action)

    def tick(self) -> list[Action]:
        """Expire and time out what is due (the core calls it every second, every step does)."""
        now = int(self.clock())
        changed: list[tuple[Action, str]] = []
        with self._lock:
            due = self.store.rows(
                f"SELECT {_COLUMNS} FROM action WHERE "
                "(state = 'proposed' AND created + ? <= ?) OR "
                "(state = 'confirmed' AND confirmed_ts + ? <= ?) OR "
                "(state = 'sent' AND sent_ts + ? <= ?)",
                (PENDING_S, now, QUEUE_S, now, RESULT_S, now),
            )
            for row in due:
                action = _action(row)
                if action.state == "proposed":
                    state, why = "expired", "not confirmed within 60 s"
                    say = f"Expired: {action.readback}. Nothing was done."
                elif action.state == "confirmed":
                    state, why = (
                        "failed",
                        "its OC computer didn't collect it; nothing ran",
                    )
                    say = f"Failed: {action.readback}. The OC computer never took it."
                else:
                    state, why = "failed", "timed out; may have run"
                    say = f"No result for: {action.readback}. It may have run; check in game."
                with self.store.transaction() as conn:
                    conn.execute(
                        "UPDATE action SET state = ?, done_ts = ?, result = ? WHERE id = ? "
                        "AND state = ?",
                        (state, now, _json({"why": why}), action.id, action.state),
                    )
                after = self.get(action.id)
                assert after is not None
                changed.append((after, say))
        for action, say in changed:
            self._changed(action, say)
        return [a for a, _ in changed]

    # --- the OC side (gregtec.bridge.ingest.Commands)

    def take(self, agent: AgentId, limit: int) -> list[dict[str, Any]]:
        """The confirmed commands for this binding's computer, now ``sent`` (at most once)."""
        with self._lock:
            self.tick()
            rows = self.store.rows(
                f"SELECT {_COLUMNS} FROM action WHERE state = 'confirmed' AND target = ? "
                "AND computer = ? AND kind IN ('craft','machine_pause','machine_resume','say') "
                "ORDER BY id LIMIT ?",
                (agent.binding, agent.address, max(0, limit)),
            )
            actions = [_action(r) for r in rows]
            if not actions:
                return []
            now = int(self.clock())
            with self.store.transaction() as conn:
                for a in actions:
                    conn.execute(
                        "UPDATE action SET state = 'sent', sent_ts = ? WHERE id = ? "
                        "AND state = 'confirmed'",
                        (now, a.id),
                    )
        for a in actions:
            log.info(
                "action sent",
                extra={"action": a.id, "kind": a.kind, "agent": str(agent)},
            )
        return [a.command() for a in actions]

    def returned(self, ids: Sequence[int], why: str) -> None:
        """Commands :meth:`take` gave out that no reply carried: back in the queue, same id."""
        with self._lock, self.store.transaction() as conn:
            for i in ids:
                conn.execute(
                    "UPDATE action SET state = 'confirmed', sent_ts = NULL WHERE id = ? "
                    "AND state = 'sent'",
                    (int(i),),
                )
        if ids:
            log.info("actions back in the queue", extra={"ids": list(ids), "why": why})

    def results(self, agent: AgentId, doc: Mapping[str, Any]) -> None:
        """Finish actions from a telemetry message's ``results`` and track its ``crafts``.

        Only an action sent to (or queued for) this very binding and computer can be finished
        by it; anything else in the message is ignored. A repeated result changes nothing."""
        changed: list[tuple[Action, str]] = []
        now = int(self.clock())
        with self._lock:
            for res in doc.get("results") or []:
                action = self._mine(agent, res.get("id"), ("sent", "confirmed"))
                if action is None:
                    continue
                ok = bool(res.get("ok"))
                state = "done" if ok else "failed"
                result = {"state": res.get("state"), "ok": ok}
                if res.get("err"):
                    result["err"] = str(res["err"])[:200]
                with self.store.transaction() as conn:
                    conn.execute(
                        "UPDATE action SET state = ?, done_ts = ?, result = ? WHERE id = ? "
                        "AND state IN ('sent','confirmed')",
                        (state, now, _json(result), action.id),
                    )
                after = self.get(action.id)
                assert after is not None
                changed.append((after, _result_words(after)))
            for craft in doc.get("crafts") or []:
                action = self._mine(agent, craft.get("id"), ("done",))
                if action is None or action.kind != "craft":
                    continue
                result = dict(action.result or {})
                before = result.get("craft")
                result["craft"] = str(craft.get("state"))
                if craft.get("err"):
                    result["craft_err"] = str(craft["err"])[:200]
                if before == result["craft"]:
                    continue
                with self.store.transaction() as conn:
                    conn.execute(
                        "UPDATE action SET result = ? WHERE id = ?",
                        (_json(result), action.id),
                    )
                after = self.get(action.id)
                assert after is not None
                if result["craft"] in CRAFT_FINAL:
                    changed.append((after, _craft_words(after)))
        for action, say in changed:
            self._changed(action, say)

    # --- helpers

    def describe(self, kind: str, args: Mapping[str, Any], target: str) -> str:
        """The read-back, without "Say confirm."."""
        where = "" if target == "play" else f" in {target.capitalize()}"
        if kind == "craft":
            named = None
            try:
                named = self.name(str(args["name"]), int(args["damage"]))
            except Exception:  # a read-back still works without the DB
                log.exception("item name lookup failed")
            item = named or f"{args['name']} (damage {args['damage']})"
            cpu = f" on CPU {args['cpu']}" if args.get("cpu") else ""
            return f"Craft {args['amount']} {item}{cpu}{where}"
        if kind in ("machine_pause", "machine_resume"):
            verb = "Pause" if kind == "machine_pause" else "Resume"
            return f"{verb} machine {args['addr'][:8]}{where}"
        if kind == "say":
            return f'Say "{args["text"]}" in chat{where}'
        return (
            f'Add waypoint "{args["name"]}" at {args["x"]}, {args["y"]}, {args["z"]} '
            f"in dimension {args['dim']}{where}"
        )

    def _proposed(self) -> Action | None:
        rows = self.store.rows(
            f"SELECT {_COLUMNS} FROM action WHERE state = 'proposed' ORDER BY id DESC LIMIT 1"
        )
        return _action(rows[0]) if rows else None

    def _not_pending(self, action_id: int | None) -> str:
        if action_id is None:
            return NOTHING_PENDING
        found = self.get(action_id)
        if found is None:
            return "There is no such action."
        if found.state == "expired":
            return f"That expired: {found.readback}. Nothing was done; ask again."
        if found.state == "proposed":  # only another, newer proposal can be pending
            return "That isn't the action waiting for confirmation."
        return f"That action is already {found.state}."

    def _mine(
        self, agent: AgentId, raw_id: object, states: tuple[str, ...]
    ) -> Action | None:
        if not isinstance(raw_id, int) or isinstance(raw_id, bool):
            return None
        action = self.get(raw_id)
        if (
            action is None
            or action.state not in states
            or action.target != agent.binding
            or action.computer != agent.address
        ):
            return None
        return action

    def _run_waypoint(self, action: Action) -> Outcome:
        """A confirmed waypoint: the core writes it now (plan 7.9)."""
        now = int(self.clock())
        try:
            assert self.mark is not None
            what = self.mark(action.target, action.args)
            state, result, ok = (
                "done",
                {"state": "done", "ok": True, "note": what},
                True,
            )
        except (
            Exception
        ) as exc:  # any failure fails the action; it never stays confirmed
            if not isinstance(exc, waypoint.WaypointError | OSError):
                log.exception("waypoint not written", extra={"action": action.id})
            msg = (
                str(exc)
                if isinstance(exc, waypoint.WaypointError)
                else type(exc).__name__
            )
            state, result, ok = (
                "failed",
                {"state": "failed", "ok": False, "err": msg[:200]},
                False,
            )
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE action SET state = ?, sent_ts = ?, done_ts = ?, result = ? WHERE id = ?",
                (state, now, now, _json(result), action.id),
            )
        after = self.get(action.id)
        assert after is not None
        text = _result_words(after)
        self._changed(after, text)
        return Outcome(ok, text, after)

    def _recover(self) -> None:
        """At start: no proposal or unsent command outlives the core that read it back."""
        now = int(self.clock())
        due = self.store.rows(
            "SELECT id FROM action WHERE state IN ('proposed', 'confirmed') ORDER BY id"
        )
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE action SET state = 'expired', done_ts = ?, result = ? "
                "WHERE state = 'proposed'",
                (now, _json({"why": "the core restarted before it was confirmed"})),
            )
            conn.execute(
                "UPDATE action SET state = 'failed', done_ts = ?, result = ? "
                "WHERE state = 'confirmed'",
                (
                    now,
                    _json({"why": "the core restarted before sending it; nothing ran"}),
                ),
            )
        # Journalled like every change (nothing is listening for on_change yet).
        for row in due:
            after = self.get(int(row["id"]))
            if after is not None and self.journal is not None:
                try:
                    self.journal(_journal_lines(after))
                except Exception:
                    log.exception("action not journalled", extra={"action": after.id})

    def _changed(self, action: Action, say: str) -> None:
        log.info(
            "action changed",
            extra={"action": action.id, "kind": action.kind, "state": action.state},
        )
        if self.journal is not None:
            try:
                self.journal(_journal_lines(action))
            except Exception:  # the journal is a record, never the action's failure
                log.exception("action not journalled", extra={"action": action.id})
        if self.on_change is not None:
            try:
                self.on_change(action, say)
            except Exception:
                log.exception("action change not pushed", extra={"action": action.id})


_COLUMNS = (
    "id, created, kind, args, state, target, computer, readback, source, confirmed_via, "
    "confirmed_ts, sent_ts, done_ts, result"
)


def _action(row: sqlite3.Row) -> Action:
    return Action(
        id=int(row["id"]),
        created=int(row["created"]),
        kind=str(row["kind"]),
        args=json.loads(row["args"] or "{}"),
        state=str(row["state"]),
        target=str(row["target"] or ""),
        computer=row["computer"],
        readback=str(row["readback"] or ""),
        source=str(row["source"] or ""),
        confirmed_via=row["confirmed_via"],
        confirmed_ts=row["confirmed_ts"],
        sent_ts=row["sent_ts"],
        done_ts=row["done_ts"],
        result=json.loads(row["result"]) if row["result"] else None,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _result_words(action: Action) -> str:
    result = action.result or {}
    if action.state == "done":
        if result.get("state") == "submitted":
            return f"Started: {action.readback}. AE2 is crafting it."
        return f"Done: {action.readback}."
    err = str(result.get("err") or result.get("why") or "no reason given")
    if MAY_STILL_RUN in err:
        return f"Check AE2: {action.readback}. The agent lost track of it; it may still run."
    return f"Failed: {action.readback}. {err}"


def _craft_words(action: Action) -> str:
    result = action.result or {}
    state = result.get("craft")
    if state == "done":
        return f"Crafting done: {action.readback}."
    if state == "canceled":
        return f"Crafting cancelled in AE2: {action.readback}."
    err = str(result.get("craft_err") or "")
    if MAY_STILL_RUN in err:
        return f"Check AE2: {action.readback}. It may still run."
    return f"Crafting failed: {action.readback}. {err}".strip()


def _journal_lines(action: Action) -> list[str]:
    """One ``key: value`` line in the note grammar (plan 6.2): the action and its new state."""
    parts = [action.state, action.kind, action.target]
    if action.confirmed_via:
        parts.append(f"via {action.confirmed_via}")
    result = action.result or {}
    for key in ("craft", "err", "why"):
        if result.get(key):
            parts.append(f"{key} {result[key]}")
    text = "; ".join(str(p) for p in parts)
    readback = " ".join(action.readback.split()).replace("[[", "[ [")
    return [f"action_{action.id}: {text}; {readback}"[:300]]
