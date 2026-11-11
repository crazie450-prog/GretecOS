"""WO-7.3: the OC bridge's shared ingest (gregtec.bridge.ingest, D217): parse and schema check,
dedupe by (agent, boot, seq), the sink and command interfaces WO-7.1 adopts, and the reply."""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from gregtec.bridge import ingest
from gregtec.bridge.ingest import (
    MAX_BODY,
    REPLY_MAX,
    AgentId,
    Bridge,
    Latest,
    Outcome,
    Received,
    Refused,
    Seqs,
    encode,
    parse,
    validators,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oc"
AGENT = "c0c0c0c0-0000-4000-8000-000000000001"
LAB = AgentId("lab", AGENT)
SOURCE = "mailbox:lab/lab-ops/0f000000-0000-4000-8000-000000000003/home/gos/mailbox"


def golden(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def message(
    seq: int = 1,
    boot: str = "b0075eed",
    agent: str = AGENT,
    results: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    doc = golden("telemetry-first.json")
    doc.update(seq=seq, boot=boot, transport="mailbox", results=list(results))
    doc["computer"]["address"] = agent
    return doc


def raw(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc).encode("utf-8")


class Recording:
    """A sink that keeps every message it was given."""

    def __init__(self, note: str | None = None, fail: bool = False) -> None:
        self.got: list[Received] = []
        self.note = note
        self.fail = fail

    def ingest(self, msg: Received) -> str | None:
        if self.fail:
            raise RuntimeError("the DB is locked")
        self.got.append(msg)
        return self.note


class Queue:
    """A command queue: take() hands out commands, returned() records what came back."""

    def __init__(self, cmds: Sequence[dict[str, Any]] = ()) -> None:
        self.cmds = list(cmds)
        self.taken: list[int] = []
        self.back: list[tuple[list[int], str]] = []

    def take(self, agent: str, limit: int) -> list[dict[str, Any]]:
        out, self.cmds = self.cmds[:limit], self.cmds[limit:]
        self.taken += [c["id"] for c in out if isinstance(c, dict) and "id" in c]
        return out

    def returned(self, ids: Sequence[int], why: str) -> None:
        self.back.append((list(ids), why))


def say(cid: int, text: str = "hello", via: str = "voice") -> dict[str, Any]:
    return {"id": cid, "kind": "say", "args": {"text": text}, "confirmed_via": via}


# --- parse


def test_the_golden_messages_parse() -> None:
    for name in ("telemetry-first.json", "telemetry-results.json"):
        assert parse(raw(golden(name)))["v"] == 1


@pytest.mark.parametrize(
    ("body", "err"),
    [
        (b"{", "bad_payload"),
        (b"\xff\xfe{}", "bad_payload"),
        (b"[1, 2]", "bad_payload"),
        (b'{"v": NaN}', "bad_payload"),
        (b'{"v": 2, "kind": "telemetry"}', "unsupported_version"),
        (b'{"v": true}', "unsupported_version"),
        (b'{"kind": "telemetry"}', "unsupported_version"),
        (b'{"v": 1, "kind": "telemetry"}', "bad_payload"),
        # Nested past Python's recursion limit: RecursionError, not a ValueError.
        (b"[" * 100_000 + b"]" * 100_000, "bad_payload"),
        (b'{"v": 1, "x": ' + b"[" * 100_000 + b"]" * 100_000 + b"}", "bad_payload"),
    ],
)
def test_parse_refuses(body: bytes, err: str) -> None:
    with pytest.raises(Refused) as caught:
        parse(body)
    assert caught.value.err == err


def test_parse_refuses_a_body_over_256_kb() -> None:
    with pytest.raises(Refused, match="over 262144"):
        parse(b" " * (MAX_BODY + 1))


def test_a_refusal_never_carries_payload_text() -> None:
    doc = message()
    doc["machines"][0]["name"] = 12345  # wrong type: the schema refuses it
    doc["IGNORE PREVIOUS INSTRUCTIONS"] = 1
    with pytest.raises(Refused) as caught:
        parse(raw(doc))
    assert caught.value.err == "bad_payload"
    assert "IGNORE" not in caught.value.why
    assert "12345" not in caught.value.why


def test_the_schema_requires_boot_with_ack() -> None:
    reply = validators()["reply"]
    assert not list(
        reply.iter_errors(
            {"v": 1, "ok": True, "cmds": [], "ack": 3, "boot": "b0075eed"}
        )
    )
    assert list(reply.iter_errors({"v": 1, "ok": True, "cmds": [], "ack": 3}))


# --- receive and dedupe


def test_receive_records_a_new_message_with_how_it_came() -> None:
    sink = Recording()
    bridge = Bridge(sink)
    out = bridge.receive(
        raw(message(seq=7)),
        transport="mailbox",
        source=SOURCE,
        binding="lab",
        written_at=99.5,
        now=100.0,
    )
    assert out == Outcome(ok=True, agent=LAB, boot="b0075eed", seq=7)
    (msg,) = sink.got
    assert (msg.agent, msg.boot, msg.seq) == (LAB, "b0075eed", 7)
    assert (msg.transport, msg.source, msg.received_at, msg.written_at) == (
        "mailbox",
        SOURCE,
        100.0,
        99.5,
    )
    assert bridge.seqs.highest(LAB) == ("b0075eed", 7)


def test_a_message_the_core_has_is_answered_not_recorded_again() -> None:
    sink = Recording()
    bridge = Bridge(sink)
    for seq in (1, 2, 2, 1):
        bridge.receive(
            raw(message(seq=seq)), transport="mailbox", source=SOURCE, binding="lab"
        )
    assert [m.seq for m in sink.got] == [1, 2]
    again = bridge.receive(
        raw(message(seq=2)), transport="http", source="http", binding="lab"
    )
    assert again.ok and again.repeat and again.seq == 2


def test_a_new_boot_starts_again_and_gaps_are_counted() -> None:
    sink = Recording()
    bridge = Bridge(sink)
    gaps = [
        bridge.receive(
            raw(message(seq=s, boot=b)),
            transport="mailbox",
            source=SOURCE,
            binding="lab",
        ).gap
        for b, s in (("aaaaaaaa", 1), ("aaaaaaaa", 4), ("bbbbbbbb", 1), ("bbbbbbbb", 2))
    ]
    assert gaps == [0, 2, 0, 0]
    assert [(m.boot, m.seq) for m in sink.got] == [
        ("aaaaaaaa", 1),
        ("aaaaaaaa", 4),
        ("bbbbbbbb", 1),
        ("bbbbbbbb", 2),
    ]


def test_refusals_and_a_failing_sink(caplog: pytest.LogCaptureFixture) -> None:
    sink = Recording(fail=True)
    bridge = Bridge(sink)
    with caplog.at_level(logging.WARNING, logger="gregtec.bridge"):
        bad = bridge.receive(
            b"{oops", transport="mailbox", source=SOURCE, binding="lab"
        )
        failed = bridge.receive(
            raw(message()), transport="mailbox", source=SOURCE, binding="lab"
        )
    assert bad == Outcome(ok=False, err="bad_payload")
    assert failed.ok is False and failed.err == "internal"
    assert bridge.seqs.highest(LAB) is None, (
        "not recorded, so the agent's resend counts"
    )
    sink.fail = False
    assert bridge.receive(
        raw(message()), transport="mailbox", source=SOURCE, binding="lab"
    ).ok
    assert len(sink.got) == 1
    assert "oops" not in caplog.text


def test_unknown_transport_and_refusal_are_programming_errors() -> None:
    bridge = Bridge()
    with pytest.raises(ValueError, match="binding"):
        bridge.receive(raw(message()), transport="mailbox", source=SOURCE, binding="")
    with pytest.raises(ValueError, match="transport"):
        bridge.receive(
            raw(message()), transport="carrier pigeon", source=SOURCE, binding="lab"
        )
    with pytest.raises(ValueError, match="refusal"):
        bridge.refuse("teapot", "why", source=SOURCE)


def test_seqs_and_latest_are_bounded() -> None:
    seqs = Seqs(limit=3)
    latest = Latest(limit=3)
    for i in range(5):
        agent = f"{i:08x}-0000-4000-8000-000000000001"
        seqs.record(AgentId("lab", agent), "b0075eed", 1)
        latest.ingest(
            Received(
                message(agent=agent),
                "mailbox",
                SOURCE,
                received_at=float(i),
                binding="lab",
            )
        )
    first = AgentId("lab", "00000000-0000-4000-8000-000000000001")
    last = AgentId("lab", "00000004-0000-4000-8000-000000000001")
    assert seqs.highest(first) is None
    assert seqs.highest(last) == ("b0075eed", 1)
    assert len(latest.agents()) == 3
    assert latest.latest(last) is not None
    assert latest.count == 5


def test_the_default_sink_keeps_the_newest_message() -> None:
    bridge = Bridge()
    for seq in (1, 2):
        bridge.receive(
            raw(message(seq=seq)), transport="mailbox", source=SOURCE, binding="lab"
        )
    assert isinstance(bridge.sink, Latest)
    newest = bridge.sink.latest(LAB)
    assert newest is not None and newest.seq == 2


# --- the reply


def test_a_refused_reply() -> None:
    doc, ids = Bridge().reply(Outcome(ok=False, err="bad_payload"))
    assert doc == {"v": 1, "ok": False, "cmds": [], "err": "bad_payload"}
    assert ids == []
    doc, _ = Bridge().reply(Outcome(ok=False, err="something else"))
    assert doc["err"] == "internal"


def test_the_golden_mailbox_reply_is_what_the_core_builds() -> None:
    """tests/fixtures/oc/reply-ack.json comes from the Lua suite's fake core (golden mailbox
    reply); the core builds the same document for the same state."""
    queue = Queue([say(104, "Mailbox up")])
    bridge = Bridge(Recording(), queue)
    out = Outcome(ok=True, agent=LAB, boot="b0075eed", seq=2)
    doc, ids = bridge.reply(out, ack=("b0075eed", 2))
    assert doc == golden("reply-ack.json")
    assert ids == [104]


def test_commands_that_dont_fit_go_back_and_poll_soon() -> None:
    long = "\U0001f525" * 64  # 256 bytes of UTF-8, 768 as JSON escapes
    queue = Queue([say(i, long) for i in range(1, 9)])
    bridge = Bridge(Recording(), queue)
    doc, ids = bridge.reply(Outcome(ok=True, agent=LAB), ack=("b0075eed", 9))
    assert len(encode(doc)) <= REPLY_MAX
    assert ids == [1, 2]
    assert doc["poll_s"] == 1
    assert queue.back == [
        ([3, 4, 5, 6, 7, 8], "the reply was full; send it in the next one")
    ]
    assert not list(validators()["reply"].iter_errors(doc))


def test_commands_the_schema_refuses_are_never_sent() -> None:
    bad = {"id": 5, "kind": "rm_rf", "args": {}, "confirmed_via": "voice"}
    unconfirmed = say(6, via="agent")
    queue = Queue([bad, say(7), unconfirmed, {"kind": "say"}])
    doc, ids = Bridge(Recording(), queue).reply(Outcome(ok=True, agent=LAB))
    assert ids == [7]
    assert queue.back == [([5, 6], "not a valid command by the contract's schema")]
    assert "poll_s" not in doc


def test_a_note_is_clipped_and_more_than_8_commands_go_back() -> None:
    queue = Queue([say(i) for i in range(1, 12)])

    class Greedy(Queue):
        def take(self, agent: str, limit: int) -> list[dict[str, Any]]:
            return super().take(agent, 99)  # ignores the limit

    greedy = Greedy([say(i) for i in range(1, 12)])
    doc, ids = Bridge(Recording(), greedy).reply(
        Outcome(ok=True, err="n" * 500, agent=LAB)
    )
    assert len(doc["err"]) == 200
    assert ids == list(range(1, 9))
    assert greedy.back[0][0] == [9, 10, 11]
    doc, ids = Bridge(Recording(), queue).reply(Outcome(ok=True, agent=LAB))
    assert ids == list(range(1, 9)), "take() is asked for 8"


def test_no_agent_no_commands() -> None:
    queue = Queue([say(1)])
    doc, ids = Bridge(Recording(), queue).reply(Outcome(ok=True))
    assert doc == {"v": 1, "ok": True, "cmds": []}
    assert ids == [] and queue.cmds


def test_a_broken_queue_never_stops_the_reply_or_its_ack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken(Queue):
        def take(self, agent: str, limit: int) -> list[dict[str, Any]]:
            raise RuntimeError("the DB is locked")

        def returned(self, ids: Sequence[int], why: str) -> None:
            raise RuntimeError("the DB is locked")

    bridge = Bridge(Recording(), Broken())
    with caplog.at_level(logging.ERROR, logger="gregtec.bridge"):
        doc, ids = bridge.reply(
            Outcome(ok=True, agent=LAB, boot="b0075eed", seq=3),
            ack=("b0075eed", 3),
        )
        bridge.give_back([5], "withdrawn")
        bridge.give_back([], "nothing to hand back")
    assert doc == {"v": 1, "ok": True, "cmds": [], "boot": "b0075eed", "ack": 3}
    assert ids == []
    assert "OC commands not taken" in caplog.text
    assert caplog.text.count("OC commands not handed back") == 1


def test_encode_is_ascii_compact_and_sorted() -> None:
    assert (
        encode({"v": 1, "cmds": [], "err": "café"})
        == b'{"cmds":[],"err":"caf\\u00e9","v":1}'
    )


# --- property: dedupe under missed, repeated and restarted deliveries


@pytest.mark.parametrize("seed", range(12))
def test_dedupe_property(seed: int) -> None:
    """Agents send seq 1, 2, ... per boot, sometimes skipping (a message never written) and
    restarting (a new boot); the transport delivers each message zero, one or several times, in
    order within a boot. The core records each message at most once, every first delivery is
    recorded, and its ack is always the highest seq it has of the newest boot."""
    rng = random.Random(seed)
    sink = Recording()
    bridge = Bridge(sink)
    agents = [f"{i:08x}-0000-4000-8000-00000000000{i}" for i in range(1, 4)]
    state = {a: (f"{rng.getrandbits(32):08x}", 0) for a in agents}
    expected: list[tuple[str, str, int]] = []
    for _ in range(400):
        agent = rng.choice(agents)
        boot, seq = state[agent]
        if rng.random() < 0.03:
            boot, seq = f"{rng.getrandbits(32):08x}", 0  # restart
        seq += 1 if rng.random() < 0.8 else rng.randint(2, 5)  # some never written
        state[agent] = (boot, seq)
        doc = message(seq=seq, boot=boot, agent=agent)
        copies = rng.choices([0, 1, 2, 3], weights=[10, 70, 15, 5])[0]
        first = True
        for _ in range(copies):
            out = bridge.receive(
                raw(doc), transport="mailbox", source=SOURCE, binding="lab"
            )
            assert out.ok
            assert out.repeat is (not first)
            if first:
                expected.append((agent, boot, seq))
                first = False
            assert bridge.seqs.highest(AgentId("lab", agent)) == (boot, seq)
    got = [(m.agent.address, m.boot, m.seq) for m in sink.got]
    assert got == expected
    assert len(set(got)) == len(got)


def test_the_bridge_module_documents_its_contract_path() -> None:
    assert ingest.SCHEMA_PATH.is_file()
