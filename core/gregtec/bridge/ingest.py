"""What the core does with one OpenComputers telemetry message, whichever transport carried it.

Contract ``gos-oc-bridge/1``: docs/contracts/oc_bridge.md and oc_bridge.schema.json (D214). WO-7.3
defines this interface for its file mailbox (M1, :mod:`gregtec.bridge.mailbox`); WO-7.1's HTTP
endpoint (M2, ``POST /oc/telemetry``) adopts it, so both transports check, dedupe, record and
answer a message the same way (D217)::

    raw bytes -> Bridge.receive() -> Outcome -> Bridge.reply() -> reply document -> encode()

- :func:`parse`: at most 256 KB (D214), one JSON object, ``v`` 1 (else ``unsupported_version``),
  valid against ``#/$defs/telemetry`` (else ``bad_payload``). NaN and infinities are refused, as
  JSON itself has none. A refusal's log line names where the message broke the schema, never
  its text: everything in a message is game or mod data (plan 0.1 principle 8).
- :class:`AgentId`: an agent is its OC computer's address *as seen through one binding*, the
  sender the transport vouches for (a mailbox: its gos.toml role, ``lab`` or ``play``; WO-7.1's
  HTTP endpoint: the instance its token is issued for). The address is only what a message says
  about itself, and Lab runs any Lua gos-dev puts there (D75), so a Lab message naming Play's
  computer is a different agent: it never dedupes against, replaces or takes the commands of
  Play's (a Lab world copied from Play even has the same addresses) (D217).
- :class:`Seqs` (dedupe): per agent the core keeps the agent's ``boot`` and the highest ``seq``
  it has. A message it already has (a repeated read, a retried
  POST) is answered and acknowledged but not recorded again. A new ``boot`` is a restarted agent,
  whose ``seq`` starts at 1 again. ``seq`` may jump: the agent counts a message it couldn't send.
- :class:`Sink` records a new message (WO-7.1, 7.4 and 7.5 add the tables and alerts; until
  then :class:`Latest` keeps each agent's newest message in memory). :class:`Commands` holds the
  confirmed commands waiting for an agent (WO-7.1's action queue; :class:`NoCommands` until then).
- :meth:`Bridge.reply`: ``{v, ok, cmds, err, boot, ack, poll_s}``, at most 2048 bytes as
  :func:`encode` writes it (OC reads 2048 bytes a call). ``ack`` always comes with the ``boot`` it
  counts in, so an agent never takes an acknowledgement meant for an earlier run of itself (a reply
  left in ``inbox.json``, D216). Commands that don't fit, or that the schema refuses, go back to
  the queue (:meth:`Commands.returned`), and ``poll_s`` 1 asks for the next message in a second.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple, NoReturn, Protocol

from jsonschema import Draft202012Validator

log = logging.getLogger("gregtec.bridge")

CONTRACT_V = 1
MAX_BODY = 262_144  # bytes: the core accepts telemetry up to 256 KB (D214)
REPLY_MAX = 2048  # bytes: the most OC reads in one call (plan 8 WO-7.1, D214)
MAX_CMDS = 8  # commands in one reply (the schema's maxItems)
NOTE_MAX = 200  # an ok reply's note (the schema's maxLength for err)
REFUSALS = ("unauthorized", "bad_payload", "unsupported_version", "internal")
TRANSPORTS = ("http", "mailbox")
MAX_AGENTS = 64  # agents the core remembers (dedupe and the latest message)
SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "contracts" / "oc_bridge.schema.json"
)
_PATH_PART = re.compile(r"[^A-Za-z0-9_]")


class Refused(ValueError):
    """A message the core can't take. ``err`` goes in the reply, ``why`` only in the log."""

    def __init__(self, err: str, why: str) -> None:
        super().__init__(why)
        self.err = err
        self.why = why


@cache
def validators() -> dict[str, Draft202012Validator]:
    """Validators for the schema's ``telemetry``, ``reply`` and ``command`` definitions."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return {
        name: Draft202012Validator(
            {"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}
        )
        for name in ("telemetry", "reply", "command")
    }


def _no_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not JSON")


def _where(path: Sequence[object]) -> str:
    """A schema error's location, cut down so no payload text reaches the log."""
    parts = [_PATH_PART.sub("?", str(p))[:32] for p in list(path)[:8]]
    return "/".join(parts) or "(the top)"


def parse(raw: bytes) -> dict[str, Any]:
    """One telemetry document from its bytes, or :class:`Refused`."""
    if len(raw) > MAX_BODY:
        raise Refused("bad_payload", f"{len(raw)} bytes, over {MAX_BODY}")
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    # UnicodeDecodeError and JSONDecodeError are ValueErrors; nesting deeper than Python's
    # recursion limit (a 256 KB body can nest 100,000 deep) raises RecursionError.
    except (ValueError, RecursionError) as exc:
        raise Refused("bad_payload", f"not JSON ({type(exc).__name__})") from None
    if not isinstance(doc, dict):
        raise Refused("bad_payload", "not a JSON object")
    version = doc.get("v")
    if isinstance(version, bool) or version != CONTRACT_V:
        raise Refused("unsupported_version", "the message's contract version is not 1")
    error = next(iter(validators()["telemetry"].iter_errors(doc)), None)
    if error is not None:
        raise Refused(
            "bad_payload",
            f"not valid by the schema at {_where(error.absolute_path)} ({error.validator})",
        )
    return doc


def encode(doc: Mapping[str, Any]) -> bytes:
    """A reply as the core sends it: compact, ASCII only (OC's JSON decodes the escapes), keys
    sorted."""
    return json.dumps(
        doc, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode("ascii")


class AgentId(NamedTuple):
    """Who an agent is: the binding a transport vouches for, and the OC computer's address."""

    binding: (
        str  # e.g. "play" or "lab" (a mailbox's role); never taken from the message
    )
    address: str  # the message's computer.address

    def __str__(self) -> str:
        return f"{self.binding}/{self.address}"


@dataclass(frozen=True)
class Received:
    """One accepted telemetry message and how it arrived."""

    doc: Mapping[str, Any]
    transport: str  # "http" or "mailbox"
    source: str  # where it came from, e.g. "mailbox:lab/lab-ops/<drive>/<dir>"
    received_at: float  # time.time() when the core read it
    written_at: float | None = None  # M1: when OC wrote outbox.json (its mtime)
    binding: str = field(default="", kw_only=True)  # who the transport vouches for

    @property
    def agent(self) -> AgentId:
        """The agent's identity: the binding and its OC computer's address (D217)."""
        return AgentId(self.binding, str(self.doc["computer"]["address"]))

    @property
    def boot(self) -> str:
        return str(self.doc["boot"])

    @property
    def seq(self) -> int:
        return int(self.doc["seq"])


class Sink(Protocol):
    """Records each new message (one the core didn't have yet)."""

    def ingest(self, msg: Received) -> str | None:
        """Record ``msg``. Return a note for the agent (it shows and logs it once) or None.
        Raising refuses the message as ``internal``: nothing in it counts as recorded."""
        ...


class Commands(Protocol):
    """The confirmed commands waiting for agents (WO-7.1's action queue, D59, D214)."""

    def take(self, agent: AgentId, limit: int) -> list[dict[str, Any]]:
        """Up to ``limit`` confirmed commands for ``agent`` (a command is confirmed for one
        binding's computer, never for an address alone), now counted as sent: each goes out
        once (at most once, D214)."""
        ...

    def returned(self, ids: Sequence[int], why: str) -> None:
        """Commands :meth:`take` gave out that the agent didn't get: a reply too full for them,
        one the core couldn't write, or (M1) a reply withdrawn unread. A withdrawn one may
        still have been read if OC opened it at that very moment (the core can't see that on
        every OS), so send these again under the same id, never as new commands: the agent
        never runs an id twice and answers a repeat with the result it kept (D214, D217)."""
        ...


class NoCommands:
    """No action queue yet (WO-7.1 brings one): nothing to send."""

    def take(self, agent: AgentId, limit: int) -> list[dict[str, Any]]:
        return []

    def returned(self, ids: Sequence[int], why: str) -> None:
        if ids:
            log.warning("OC commands returned", extra={"ids": list(ids), "why": why})


class Latest:
    """The default sink until WO-7.1/7.4: each agent's newest message, in memory."""

    def __init__(self, limit: int = MAX_AGENTS) -> None:
        self._lock = threading.Lock()
        self._latest: OrderedDict[AgentId, Received] = OrderedDict()
        self._limit = limit
        self.count = 0

    def ingest(self, msg: Received) -> str | None:
        with self._lock:
            before = self._latest.pop(msg.agent, None)
            self._latest[msg.agent] = msg
            while len(self._latest) > self._limit:
                self._latest.popitem(last=False)
            self.count += 1
        if before is None or before.boot != msg.boot:
            log.info(
                "OC agent seen",
                extra={
                    "agent": msg.agent.address,
                    "binding": msg.agent.binding,
                    "boot": msg.boot,
                    "seq": msg.seq,
                    "transport": msg.transport,
                    "source": msg.source,
                },
            )
        return None

    def latest(self, agent: AgentId) -> Received | None:
        with self._lock:
            return self._latest.get(agent)

    def agents(self) -> list[AgentId]:
        with self._lock:
            return list(self._latest)


class Seqs:
    """Which messages the core has: per agent, its boot and the highest seq (dedupe, D217)."""

    def __init__(self, limit: int = MAX_AGENTS) -> None:
        self._lock = threading.Lock()
        self._by_agent: OrderedDict[AgentId, tuple[str, int]] = OrderedDict()
        self._limit = limit

    def has(self, agent: AgentId, boot: str, seq: int) -> bool:
        """Whether the core already has this message (same boot, seq at most the highest)."""
        with self._lock:
            last = self._by_agent.get(agent)
            return last is not None and last[0] == boot and seq <= last[1]

    def record(self, agent: AgentId, boot: str, seq: int) -> int:
        """Remember a new message. Returns how many seqs it skipped within its boot (0 for the
        first message of a boot): messages the agent counted but never sent, or lost."""
        with self._lock:
            last = self._by_agent.pop(agent, None)
            gap = 0
            if last is not None and last[0] == boot:
                gap = max(0, seq - last[1] - 1)
                seq = max(seq, last[1])
            self._by_agent[agent] = (boot, seq)
            while len(self._by_agent) > self._limit:
                self._by_agent.popitem(last=False)
            return gap

    def highest(self, agent: AgentId) -> tuple[str, int] | None:
        """(boot, seq): the newest boot of ``agent`` and the highest seq the core has of it."""
        with self._lock:
            return self._by_agent.get(agent)


@dataclass(frozen=True)
class Outcome:
    """What became of one message."""

    ok: bool
    err: str | None = (
        None  # ok False: one of REFUSALS; ok True: a note for the agent or None
    )
    agent: AgentId | None = None
    boot: str | None = None
    seq: int | None = None
    repeat: bool = False  # the core had it already, so it wasn't recorded again
    gap: int = 0  # seqs skipped since the agent's previous message of this boot


class Bridge:
    """The shared ingest: every transport hands each message to :meth:`receive` and answers with
    :meth:`reply`. Thread-safe: one message is checked and recorded at a time."""

    def __init__(
        self, sink: Sink | None = None, commands: Commands | None = None
    ) -> None:
        self.sink: Sink = sink if sink is not None else Latest()
        self.commands: Commands = commands if commands is not None else NoCommands()
        self.seqs = Seqs()
        self._lock = threading.Lock()

    def refuse(self, err: str, why: str, *, source: str) -> Outcome:
        """A transport's own refusal (a missing token, a file too big to read)."""
        if err not in REFUSALS:
            raise ValueError(f"not a refusal the contract has: {err!r}")
        log.warning(
            "OC message refused", extra={"source": source, "err": err, "why": why}
        )
        return Outcome(ok=False, err=err)

    def receive(
        self,
        raw: bytes,
        *,
        transport: str,
        source: str,
        binding: str,
        written_at: float | None = None,
        now: float | None = None,
    ) -> Outcome:
        """Check one message, record it unless the core has it already, and say what became of it.

        ``binding``: who the transport vouches the sender is (a mailbox's role, an HTTP token's
        instance); with the message's computer address it is the agent (:class:`AgentId`)."""
        if transport not in TRANSPORTS:
            raise ValueError(f"unknown transport {transport!r}")
        if not binding:
            raise ValueError("a message needs the binding its transport vouches for")
        try:
            doc = parse(raw)
        except Refused as exc:
            return self.refuse(exc.err, exc.why, source=source)
        msg = Received(
            doc,
            transport,
            source,
            time.time() if now is None else now,
            written_at,
            binding=binding,
        )
        agent, boot, seq = msg.agent, msg.boot, msg.seq
        with self._lock:
            if self.seqs.has(agent, boot, seq):
                return Outcome(ok=True, agent=agent, boot=boot, seq=seq, repeat=True)
            try:
                note = self.sink.ingest(msg)
            except (
                Exception
            ):  # the sink's failure is the message's refusal, never the core's
                log.exception("OC message not recorded", extra={"source": source})
                return Outcome(
                    ok=False, err="internal", agent=agent, boot=boot, seq=seq
                )
            gap = self.seqs.record(agent, boot, seq)
        if isinstance(note, str) and note:
            note = note[:NOTE_MAX]
        else:
            note = None
        return Outcome(ok=True, err=note, agent=agent, boot=boot, seq=seq, gap=gap)

    def reply(
        self,
        outcome: Outcome,
        *,
        ack: tuple[str, int] | None = None,
        poll_s: int | None = None,
    ) -> tuple[dict[str, Any], list[int]]:
        """The reply to ``outcome`` and the ids of the commands in it.

        ``ack`` is (boot, seq): over M1 the agent's newest boot and the highest seq the core has
        of it; over HTTP it may be the message the reply answers. An accepted message's reply
        carries the commands waiting for its agent, as many as fit in :data:`REPLY_MAX` bytes;
        the rest, and any the schema refuses, go back to the queue.
        """
        if not outcome.ok:
            err = outcome.err if outcome.err in REFUSALS else "internal"
            return {"v": CONTRACT_V, "ok": False, "cmds": [], "err": err}, []
        doc: dict[str, Any] = {"v": CONTRACT_V, "ok": True, "cmds": []}
        if outcome.err:
            doc["err"] = outcome.err[:NOTE_MAX]
        if ack is not None:
            doc["boot"], doc["ack"] = ack
        if poll_s is not None:
            doc["poll_s"] = poll_s
        if outcome.agent is None:
            return doc, []
        try:
            taken = self.commands.take(outcome.agent, MAX_CMDS)
        except Exception:  # a broken queue must not stop the reply and its ack
            log.exception("OC commands not taken; the reply goes without them")
            taken = []
        # Room for poll_s 1 is kept while filling, so adding it after never breaks the limit.
        sized = dict(doc, poll_s=doc.get("poll_s", 1))
        sent: list[dict[str, Any]] = []
        full: list[int] = []
        bad: list[int] = []
        for cmd in taken:
            if not _command_ok(cmd):
                bad.append(_command_id(cmd))
            elif (
                len(sent) < MAX_CMDS
                and len(encode(dict(sized, cmds=[*sent, cmd]))) <= REPLY_MAX
            ):
                sent.append(cmd)
            else:
                full.append(_command_id(cmd))
        if bad:
            log.error("OC commands refused by the schema; not sent", extra={"ids": bad})
            self.give_back(
                [i for i in bad if i >= 1],
                "not a valid command by the contract's schema",
            )
        if full:
            self.give_back(full, "the reply was full; send it in the next one")
            doc["poll_s"] = 1
        doc["cmds"] = sent
        return doc, [int(cmd["id"]) for cmd in sent]

    def give_back(self, ids: Sequence[int], why: str) -> None:
        """Hand commands back to the queue (:meth:`Commands.returned`). A failing queue is
        logged, never raised: the transport's own step (a reply, a withdrawal) has happened."""
        if not ids:
            return
        try:
            self.commands.returned(list(ids), why)
        except Exception:
            log.exception(
                "OC commands not handed back", extra={"ids": list(ids), "why": why}
            )


def _command_ok(cmd: object) -> bool:
    return isinstance(cmd, dict) and not any(validators()["command"].iter_errors(cmd))


def _command_id(cmd: object) -> int:
    """A command's id, or -1 for one without a usable id (logged, never returned)."""
    if isinstance(cmd, dict):
        cid = cmd.get("id")
        if isinstance(cid, int) and not isinstance(cid, bool):
            return cid
    return -1
