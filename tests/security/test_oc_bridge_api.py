"""WO-7.1 acceptance: ``POST /oc/telemetry`` and the confirm-by-id routes (plan 7.9, 8; D214,
D220, D221).

Runs the real FastAPI app in-process on a temporary GOS_HOME. ``/oc/telemetry`` always answers
HTTP 200 with a reply of at most 2048 bytes: no token, a wrong one, an oversize or non-JSON body
are each ``ok: false`` with an ``err``, and nothing in them is processed. An OC token opens that
route only. Confirming takes one of Clayton's ways in (D59), never admin or an OC computer.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gregtec.agents.ask import CONFIRM_ELSEWHERE, Asker
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.tools.catalog import build_registry
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.bridge import ops as bridge_ops
from gregtec.bridge.actions import PENDING_S, SETTLE_S
from gregtec.bridge.http import RATE
from gregtec.bridge.ingest import MAX_BODY, REPLY_MAX, validators
from gregtec.cards import build as cards_build
from gregtec.cards import validate as validate_card
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.llm.usage import UsageMeter
from gregtec.store import Store

REPO = Path(__file__).resolve().parents[2]
FIRST = json.loads(
    (REPO / "tests" / "fixtures" / "oc" / "telemetry-first.json").read_text(
        encoding="utf-8"
    )
)
ADMIN = "admin-" + "b" * 40
OC_BASE = "http://127.0.0.1:8765"
DASH = "http://gos.localhost:8765"
TAILNET = "gaming-pc.tail1234.ts.net"


@pytest.fixture(autouse=True)
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: False)


@pytest.fixture
def core(tmp_path: Path) -> Iterator[Core]:
    cfg = Config(gos_home=tmp_path, extra_allowed_hosts=(TAILNET,))
    store = Store(tmp_path / "data" / "gos.sqlite")
    ops = bridge_ops.build(cfg, store)
    c = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        oc=ops.bridge,
        ops=ops,
    )
    ops.desk.on_change = c.action_changed
    registry = build_registry(tmp_path / "data" / "none.sqlite", ops=ops)
    c.asker = Asker(registry, HandoffRecorder(), None, actions=ops.desk)
    yield c
    store.close()


@pytest.fixture
def client(core: Core) -> Iterator[TestClient]:
    with TestClient(create_app(core), base_url=OC_BASE) as c:
        yield c


@pytest.fixture
def token(core: Core) -> str:
    return core.auth.issue_client_token("oc-lab")


def post(
    client: TestClient, body: bytes | dict[str, Any], **headers: str
) -> dict[str, Any]:
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    h = {"Content-Type": "application/json", **headers}
    r = client.post("/oc/telemetry", content=data, headers=h)
    assert r.status_code == 200, r.text  # always, whatever happened
    assert r.headers["content-type"].startswith("application/json")
    assert len(r.content) <= REPLY_MAX
    doc = r.json()
    assert not list(validators()["reply"].iter_errors(doc)), doc
    return doc


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def nothing_processed(core: Core) -> None:
    assert core.store.rows("SELECT COUNT(*) FROM tel_machine")[0][0] == 0
    assert core.store.rows("SELECT COUNT(*) FROM tel_sample")[0][0] == 0
    assert core.ops is not None and core.ops.telemetry.latest.agents() == []


def msg(seq: int = 1) -> dict[str, Any]:
    doc = copy.deepcopy(FIRST)
    doc["seq"] = seq
    return doc


# --- the happy path -------------------------------------------------------------------------


def test_a_message_is_recorded_and_acknowledged(
    client: TestClient, core: Core, token: str
) -> None:
    reply = post(client, msg(), **bearer(token))
    assert reply == {"v": 1, "ok": True, "cmds": [], "boot": FIRST["boot"], "ack": 1}
    assert core.store.rows("SELECT COUNT(*) FROM tel_machine")[0][0] == 3
    agent = core.ops.telemetry.latest.agents()[0]  # type: ignore[union-attr]
    assert (agent.binding, agent.address) == ("lab", FIRST["computer"]["address"])


def test_a_play_token_binds_play(client: TestClient, core: Core) -> None:
    play = core.auth.issue_client_token("oc-play-base")
    assert post(client, msg(), **bearer(play))["ok"]
    assert core.ops.telemetry.latest.agents()[0].binding == "play"  # type: ignore[union-attr]


# --- refusals: still 200, nothing processed -----------------------------------------------------


@pytest.mark.parametrize(
    "auth",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer wrong-token",
        "Basic b2M6b2M=",
        f"Bearer {ADMIN}",  # the admin token is not an OC computer's
    ],
)
def test_no_or_a_wrong_token(
    client: TestClient, core: Core, token: str, auth: str | None
) -> None:
    headers = {} if auth is None else {"Authorization": auth}
    reply = post(client, msg(), **headers)
    assert reply == {"v": 1, "ok": False, "cmds": [], "err": "unauthorized"}
    nothing_processed(core)


def test_other_client_and_revoked_tokens_are_refused(
    client: TestClient, core: Core
) -> None:
    voice = core.auth.issue_client_token("voice")
    assert post(client, msg(), **bearer(voice))["err"] == "unauthorized"
    old = core.auth.issue_client_token("oc-lab")
    new = core.auth.issue_client_token("oc-lab")  # replaces the old one
    assert post(client, msg(), **bearer(old))["err"] == "unauthorized"
    core.auth.revoke_client_token("oc-lab")
    assert post(client, msg(), **bearer(new))["err"] == "unauthorized"
    nothing_processed(core)


@pytest.mark.parametrize(
    ("body", "content_type", "err"),
    [
        (b"not json at all", "application/json", "bad_payload"),
        (b"[1, 2, 3]", "application/json", "bad_payload"),
        (b'{"v": 1, "kind": "telemetry"}', "application/json", "bad_payload"),
        (b"\xff\xfe{}", "application/json", "bad_payload"),
        (b'{"v": 2}', "application/json", "unsupported_version"),
        (json.dumps(FIRST).encode(), "text/plain", "bad_payload"),
        (
            json.dumps(FIRST).encode(),
            "application/x-www-form-urlencoded",
            "bad_payload",
        ),
        (b"", "application/json", "bad_payload"),
    ],
)
def test_a_bad_body(
    client: TestClient, core: Core, token: str, body: bytes, content_type: str, err: str
) -> None:
    reply = post(client, body, **bearer(token), **{"Content-Type": content_type})
    assert reply == {"v": 1, "ok": False, "cmds": [], "err": err}
    nothing_processed(core)


def test_an_oversize_body(client: TestClient, core: Core, token: str) -> None:
    doc = msg()
    doc["errors"] = []
    big = json.dumps(doc).encode()
    big = big[:-1] + b"," + b'"pad":"' + b"x" * (MAX_BODY - len(big)) + b'"}'
    assert len(big) > MAX_BODY
    assert post(client, big, **bearer(token))["err"] == "bad_payload"

    def chunks() -> Iterator[bytes]:  # no Content-Length: refused as it streams in
        for _ in range(MAX_BODY // 4096 + 2):
            yield b" " * 4096

    r = client.post(
        "/oc/telemetry",
        content=chunks(),
        headers={"Content-Type": "application/json", **bearer(token)},
    )
    assert r.status_code == 200 and r.json()["err"] == "bad_payload"
    nothing_processed(core)


def test_a_lying_content_length_is_refused(
    client: TestClient, core: Core, token: str
) -> None:
    for length in ("abc", str(MAX_BODY + 1)):
        r = client.post(
            "/oc/telemetry",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": length,
                **bearer(token),
            },
        )
        assert r.status_code == 200 and r.json()["err"] == "bad_payload"
    nothing_processed(core)


def test_the_rate_limit(client: TestClient, core: Core, token: str) -> None:
    burst, _ = RATE
    replies = [post(client, msg(seq), **bearer(token)) for seq in range(1, burst + 3)]
    assert all(r["ok"] for r in replies[:burst])
    assert replies[-1] == {"v": 1, "ok": False, "cmds": [], "err": "internal"}
    # The refused messages were not recorded.
    assert core.ops.bridge.seqs.highest(  # type: ignore[union-attr]
        core.ops.telemetry.latest.agents()[0]  # type: ignore[union-attr]
    ) == (FIRST["boot"], burst)


def test_the_token_is_never_logged(
    client: TestClient, core: Core, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    post(client, msg(), **bearer(token))
    post(client, msg(), **bearer(token + "x"))
    post(client, b"nope", **bearer(token))
    for record in caplog.records:
        assert token not in str(record.__dict__)


# --- an OC token opens nothing else ------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/health"),
        ("GET", "/api/session"),
        ("GET", "/api/usage"),
        ("GET", "/api/actions"),
        ("POST", "/api/ask"),
        ("POST", "/api/actions/1/confirm"),
        ("GET", "/api/state"),
    ],
)
def test_an_oc_token_opens_only_its_route(
    client: TestClient, token: str, method: str, path: str
) -> None:
    r = client.request(method, path, headers=bearer(token), json={"text": "confirm"})
    assert r.status_code == 401


def test_oc_token_names(core: Core) -> None:
    for bad in ("oc", "oc-export", "oc-", "oc-lab-", "oc-play_x"):
        with pytest.raises(ValueError):
            core.auth.issue_client_token(bad)
    for good in ("oc-play", "oc-lab", "oc-lab-crops", "oc-play-base-2"):
        core.auth.issue_client_token(good)


# --- confirm by id ---------------------------------------------------------------------------


def pair(core: Core, host: str) -> dict[str, str]:
    """A paired device's cookie, made straight in the DB (pairing is WO-2.1's, tested there)."""
    _, cookie = core.auth.add_device("Pixel")
    return {"Cookie": f"gos_device={cookie}", "Host": host}


def proposed(client: TestClient, core: Core, token: str, kind: str = "say") -> int:
    post(client, msg(), **bearer(token))  # fresh telemetry, so a computer can be chosen
    args = {"text": "hi"} if kind == "say" else {}
    out = core.ops.desk.propose(kind, args, target="lab")  # type: ignore[union-attr]
    assert out.action is not None
    return out.action.id


@pytest.mark.parametrize(
    ("host", "via"), [("gos.localhost:8765", "dashboard"), (TAILNET, "phone")]
)
def test_a_dashboard_or_phone_button_confirms_by_id(
    client: TestClient, core: Core, token: str, host: str, via: str
) -> None:
    aid = proposed(client, core, token)
    headers = pair(core, host)
    r = client.post(f"/api/actions/{aid}/confirm", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["action"]["confirmed_via"] == via
    assert (
        client.post(f"/api/actions/{aid}/confirm", headers=headers).status_code == 409
    )


@pytest.mark.parametrize(("name", "via"), [("voice", "voice"), ("discord", "discord")])
def test_voice_and_discord_confirm_as_themselves(
    client: TestClient, core: Core, token: str, name: str, via: str
) -> None:
    aid = proposed(client, core, token)
    tok = core.auth.issue_client_token(name)
    r = client.post(f"/api/actions/{aid}/confirm", headers=bearer(tok))
    assert r.status_code == 200 and r.json()["action"]["confirmed_via"] == via


def test_admin_and_other_clients_cant_confirm(
    client: TestClient, core: Core, token: str
) -> None:
    aid = proposed(client, core, token)
    mod = core.auth.issue_client_token("mod")
    for headers in (bearer(ADMIN), bearer(mod), {}):
        r = client.post(f"/api/actions/{aid}/confirm", headers=headers)
        assert r.status_code in (401, 403)
    assert core.ops.desk.get(aid).state == "proposed"  # type: ignore[union-attr]
    r = client.post(f"/api/actions/{aid}/cancel", headers=bearer(ADMIN))
    assert r.status_code == 200 and r.json()["action"]["state"] == "cancelled"


def test_an_old_card_or_an_expired_action_cant_be_confirmed(
    client: TestClient, core: Core, token: str
) -> None:
    first = proposed(client, core, token)
    second = proposed(client, core, token)
    voice = bearer(core.auth.issue_client_token("voice"))
    r = client.post(f"/api/actions/{first}/confirm", headers=voice)
    assert (
        r.status_code == 409
        and r.json()["error"] == "That action is already cancelled."
    )
    core.ops.desk.clock = lambda: 10.0**12  # type: ignore[union-attr]
    r = client.post(f"/api/actions/{second}/confirm", headers=voice)
    assert r.status_code == 409 and r.json()["error"].startswith("That expired: ")
    assert PENDING_S == 60


def test_the_confirmed_command_goes_out_in_the_next_reply(
    client: TestClient, core: Core, token: str
) -> None:
    aid = proposed(client, core, token)
    assert post(client, msg(2), **bearer(token))["cmds"] == []  # unconfirmed: nothing
    voice = bearer(core.auth.issue_client_token("voice"))
    client.post(f"/api/actions/{aid}/confirm", headers=voice)
    reply = post(client, msg(3), **bearer(token))
    assert reply["cmds"] == [
        {"id": aid, "kind": "say", "args": {"text": "hi"}, "confirmed_via": "voice"}
    ]
    assert post(client, msg(4), **bearer(token))["cmds"] == []  # at most once


# --- the instant "confirm" -------------------------------------------------------------------


def test_saying_confirm(client: TestClient, core: Core, token: str) -> None:
    aid = proposed(client, core, token)
    admin = client.post("/api/ask", json={"text": "confirm"}, headers=bearer(ADMIN))
    assert admin.status_code == 200 and admin.json()["text"] == CONFIRM_ELSEWHERE
    voice = bearer(core.auth.issue_client_token("voice"))
    # Review M3: a plain "confirm" right after a proposal reads it back instead.
    soon = client.post("/api/ask", json={"text": "confirm"}, headers=voice)
    assert soon.json()["pending"] is True
    assert soon.json()["text"] == 'Just proposed: Say "hi" in chat in Lab. Say confirm.'
    assert core.ops.desk.get(aid).state == "proposed"  # type: ignore[union-attr]
    core.ops.desk.clock = lambda: time.time() + SETTLE_S  # type: ignore[union-attr]
    r = client.post("/api/ask", json={"text": "confirm"}, headers=voice)
    assert r.status_code == 200, r.text
    assert r.json()["pending"] is True and r.json()["text"].startswith(
        'Confirmed: Say "hi"'
    )
    assert core.ops.desk.get(aid).confirmed_via == "voice"  # type: ignore[union-attr]
    again = client.post("/api/ask", json={"text": "confirm"}, headers=voice)
    assert again.json()["pending"] is False


def test_the_approval_card(client: TestClient, core: Core, token: str) -> None:
    aid = proposed(client, core, token)
    pending = core.ops.desk.pending()  # type: ignore[union-attr]
    assert pending is not None
    card = cards_build.action_approval(pending.as_dict())
    assert validate_card(card) == []
    assert card["id"] == f"action-{aid}" and card["kind"] == "action"
    assert card["readback"] == '"Say "hi" in chat in Lab"'
    assert [a["id"] for a in card["actions"]] == ["confirm", "cancel"]
    assert card["expires_at"] == pending.created + PENDING_S
