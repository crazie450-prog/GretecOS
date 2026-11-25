"""WO-10.17 (D423): the live shell designer.

``[shell] designer`` names a ``[[bakeoff.models]]`` label (or a built-in fake's); that model then
draws every new AI shell design and revision through the bake-off's tool-less seam, the same gate
and the same repair rounds, every try capped and metered. On WO-10.7's fixtures (made-up blocks,
themes and plans; no game data), with fakes only: the built-in fake designers, the fake OpenAI
Responses API on loopback and an in-memory keyring. No test reaches a vendor: the guard below
refuses any connection or lookup off this PC.
"""

from __future__ import annotations

import io
import json
import socket
import sqlite3
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import keyring
import keyring.backend
import keyring.errors
import pytest

from gregtec import cli, config
from gregtec.agents import ask
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.no_ai import AUTH, NO_KEY, OUTAGE, AiMode, Problem
from gregtec.agents.tools import build_registry
from gregtec.agents.tools.registry import DEEP, ShellJobError, ShellJobs
from gregtec.agents.tools.tsv import parse
from gregtec.build import shell_bakeoff as bo
from gregtec.build import (
    shell_brief,
    shell_designer,
    shell_rounds,
    shell_store,
)
from gregtec.build import shell_fakes as fakes
from gregtec.build import shell_versions as versions
from gregtec.db import access
from gregtec.llm import designers as llm
from gregtec.llm.fake_openai import Answer, FakeResponsesAPI
from gregtec.llm.prices import Usage
from gregtec.llm.usage import UsageMeter
from gregtec.planner import design_jobs, progress
from gregtec.store import Store
from gregtec.vault.io import VaultIO

HERE = Path(__file__).parent
ts: Any = sys.modules.get("_wo1017_shell_tests")
if ts is None:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "_wo1017_shell_tests", HERE / "test_shell.py"
    )
    assert _spec is not None and _spec.loader is not None
    ts = importlib.util.module_from_spec(_spec)
    sys.modules["_wo1017_shell_tests"] = ts
    _spec.loader.exec_module(ts)

ADMIN = "a" * 40
OPENAI_KEY = "sk-proj-" + "T3stOpenAIKey" * 4  # fake, but the shape of a real key
MODEL_ID = "gpt-6-astra"
TOML = f"""
[bakeoff]
[[bakeoff.models]]
label = "astra"
provider = "openai"
model = "{MODEL_ID}"
effort = "high"
input_usd = 10.0
output_usd = 50.0

[[bakeoff.models]]
label = "mystery"
provider = "openai"
model = "gpt-x"

[[bakeoff.models]]
label = "opus"
provider = "anthropic"
model = "claude-opus-5-5"
"""
LOCAL = ("127.0.0.1", "::1", "localhost")


@pytest.fixture(autouse=True)
def outside(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No test here connects to, or looks up, anything but this PC."""
    refused: list[str] = []
    connect, resolve = socket.socket.connect, socket.getaddrinfo
    for name in ("http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)

    def guarded_connect(self: socket.socket, address: Any, *rest: Any) -> None:  # noqa: ANN401
        if isinstance(address, tuple) and address[0] not in LOCAL:
            refused.append(str(address[0]))
            raise AssertionError(f"a designer test tried to connect to {address[0]!r}")
        return connect(self, address, *rest)

    def guarded_resolve(host: Any, *rest: Any, **kw: Any) -> Any:  # noqa: ANN401
        name = host.decode() if isinstance(host, bytes) else host
        if name not in (None, "", *LOCAL):
            refused.append(str(name))
            raise AssertionError(f"a designer test tried to look up {name!r}")
        return resolve(host, *rest, **kw)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_resolve)
    return refused


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.entries.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def ring() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    fake = MemoryKeyring()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(previous)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return ts.make_home(tmp_path, ts.lay_tests.production(), ts.lay_tests.power())  # type: ignore[no-any-return]


@pytest.fixture
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def toml(tmp_path: Path, home: Path) -> Path:
    path = tmp_path / "gos-designer.toml"
    path.write_text(
        f'[paths]\ngos_home = "{home.as_posix()}"\n{TOML}', encoding="utf-8"
    )
    return path


@pytest.fixture
def store(home: Path) -> Iterator[Store]:
    s = Store(access.db_path(home))
    try:
        yield s
    finally:
        s.close()


def cfg_of(home: Path, label: str = fakes.PLAIN, **kw: Any) -> config.Config:  # noqa: ANN401
    return config.Config(gos_home=home, shell_designer=label, **kw)


def ctx_of(
    home: Path,
    toml: Path,
    label: str = fakes.PLAIN,
    store: Store | None = None,
    **kw: Any,  # noqa: ANN401
) -> shell_designer.Ctx:
    meter = None if store is None else UsageMeter(store, 100.0, (80, 100))
    cfg = cfg_of(home, label, **kw.pop("cfg", {}))
    return shell_designer.Ctx(
        cfg, home, store=store, meter=meter, path=toml, sleep=lambda _s: None, **kw
    )


def site_ask(
    words: str = "a tall hall with a clock tower",
) -> shell_brief.DesignRequest:
    return shell_brief.DesignRequest(("pl-steel",), words, theme="th-foundry")


def good(body: dict[str, Any]) -> Answer:
    """The fake OpenAI's answer: fake-plain's design for the brief it was sent."""
    text = fakes.FakeDesigner(fakes.PLAIN).complete("", body["input"], {}, 0).text
    return Answer(text=text, input_tokens=2000, output_tokens=400)


def first(conn: sqlite3.Connection, home: Path, mode: str = "site") -> str:
    made = shell_store.make(
        conn,
        home,
        VaultIO(home / "vault"),
        ["pl-steel"],
        theme_id="th-foundry",
        mode=mode,
    )
    assert made.outcome.ok, made.outcome.reasons
    return str(versions.start(home, made.build, asked="a shell", by="procedural")["id"])


def usage_rows(store: Store) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in store.rows(
            "SELECT lane, model, usd, task, in_tok, out_tok FROM llm_usage"
        )
    ]


# --- config and resolve ------------------------------------------------------------------------


def test_the_shell_keys_are_soft_service_keys(tmp_path: Path) -> None:
    path = tmp_path / "gos.toml"
    path.write_text('[shell]\ndesigner = "astra"\ndesigner_max_usd = 6\n', "utf-8")
    cfg = config.load(path)
    assert cfg.shell_designer == "astra" and cfg.shell_designer_max_usd == 6.0
    assert cfg.service_errors == ()
    assert config.Config().shell_designer == ""  # unset: today's behaviour
    for text, why in (
        ('[shell]\ndesigner = "Astra!"\n', "[shell] designer must be"),
        ("[shell]\ndesigner = 3\n", "[shell] designer must be"),
        ("[shell]\ndesigner_max_usd = 0.1\n", "designer_max_usd must be"),
        ("[shell]\ndesigner_max_usd = nan\n", "designer_max_usd must be"),
        ("[shell]\ndesigner_max_usd = true\n", "designer_max_usd must be"),
        ('shell = "x"\n', "shell must be a table"),
    ):
        path.write_text(text, "utf-8")
        cfg = config.load(path)  # never stops the load: the core says so at start
        assert any(why in e for e in cfg.service_errors), (text, cfg.service_errors)
        assert cfg.shell_designer == "" and cfg.shell_designer_max_usd == 4.0


def test_resolve_reads_the_label_per_request(
    home: Path, toml: Path, ring: MemoryKeyring, tmp_path: Path
) -> None:
    assert shell_designer.resolve(cfg_of(home, ""), toml) is None
    got = shell_designer.resolve(cfg_of(home), toml)
    assert isinstance(got, shell_designer.Ready) and got.spec.fake  # no key needed
    nope = shell_designer.resolve(cfg_of(home, "zebra"), toml)
    assert nope == shell_designer.NotReady(
        "zebra", "no [[bakeoff.models]] entry is labelled zebra"
    )
    unpriced = shell_designer.resolve(cfg_of(home, "mystery"), toml)
    assert isinstance(unpriced, shell_designer.NotReady)
    assert unpriced.why == (
        "mystery has no price in gos.toml: add input_usd and output_usd to its "
        "[[bakeoff.models]] entry; the live designer never runs unpriced"
    )
    keyless = shell_designer.resolve(cfg_of(home, "astra"), toml)
    assert isinstance(keyless, shell_designer.NotReady)
    assert keyless.why == (
        "no openai key is stored: run `gos key set openai` (the setup wizard doesn't ask "
        "for it)"
    )
    ring.set_password("gregtec-os/openai", "openai", OPENAI_KEY)
    ready = shell_designer.resolve(cfg_of(home, "astra"), toml)
    assert isinstance(ready, shell_designer.Ready)
    assert isinstance(ready.designer, llm.OpenAIDesigner)
    assert ready.designer.model == MODEL_ID and ready.spec.provider == "openai"
    # a broken [bakeoff] only makes the designer not ready; gos.toml still loads
    broken = tmp_path / "broken.toml"
    broken.write_text(
        '[shell]\ndesigner = "astra"\n[bakeoff]\ncap_usd = -1\n', encoding="utf-8"
    )
    cfg = config.load(broken)
    assert cfg.service_errors == () and cfg.shell_designer == "astra"
    bad = shell_designer.resolve(cfg, broken)
    assert isinstance(bad, shell_designer.NotReady)
    assert bad.why.startswith("the [bakeoff] table can't be read: ")


def test_the_bakeoff_builds_its_designers_as_the_live_designer_does() -> None:
    fake = shell_designer.build(bo.FAKES[fakes.SLOPPY], "")
    assert isinstance(fake, fakes.FakeDesigner) and fake.model == fakes.SLOPPY
    spec = bo.ModelSpec("astra", "openai", MODEL_ID, "low")
    built = shell_designer.build(spec, OPENAI_KEY)
    assert isinstance(built, llm.OpenAIDesigner) and built.url == llm.OPENAI_URL
    assert built.effort == "low"


# --- held: the AI switch, the credit pause, the day cap ----------------------------------------


def test_held_honours_the_switch_and_the_pause_for_every_designer(
    home: Path, toml: Path, ring: MemoryKeyring, store: Store
) -> None:
    ring.set_password("gregtec-os/openai", "openai", OPENAI_KEY)
    ring.set_password("gregtec-os/anthropic", "anthropic", "sk-ant-api03-" + "x" * 40)
    astra = shell_designer.resolve(cfg_of(home, "astra"), toml)
    opus = shell_designer.resolve(cfg_of(home, "opus"), toml)
    assert isinstance(astra, shell_designer.Ready)
    assert isinstance(opus, shell_designer.Ready)
    cfg = cfg_of(home, "astra")
    mode = AiMode()
    mode.set_choice(False)
    assert shell_designer.held(cfg, store, mode, astra) == (
        "the AI is off (you turned the AI off): nothing is spent",
        0.0,
    )
    mode = AiMode()
    mode.set_credit_pause(True)
    assert shell_designer.held(cfg, store, mode, astra)[0] == (
        "the AI is off (paused for credits): nothing is spent"
    )
    # the Anthropic API's own problems stop only an Anthropic designer
    for reason in (NO_KEY, AUTH, OUTAGE):
        mode = AiMode()
        if reason == NO_KEY:
            mode.key_seen(False)
        else:
            mode.problem = Problem(reason, "x")
        assert reason in mode.reasons()
        assert shell_designer.held(cfg, store, mode, astra) == (None, 4.0)
        why, _ = shell_designer.held(cfg, store, mode, opus)
        assert why is not None and why.startswith("the AI is off ("), reason
    assert (
        shell_designer.held(cfg, store, AiMode(), None)[0] == shell_designer.UNSET_WHY
    )


def test_held_refuses_at_the_day_cap_and_lowers_the_cap_to_what_it_leaves(
    home: Path, toml: Path, ring: MemoryKeyring, store: Store
) -> None:
    ring.set_password("gregtec-os/openai", "openai", OPENAI_KEY)
    astra = shell_designer.resolve(cfg_of(home, "astra"), toml)
    meter = UsageMeter(store, 100.0, (80, 100))
    cfg = cfg_of(home, "astra")
    meter.record("fast", "claude-opus-5-5", Usage(), usd=7.5, task="x")
    assert shell_designer.held(cfg, store, AiMode(), astra) == (None, 2.5)
    meter.record("fast", "claude-opus-5-5", Usage(), usd=2.62, task="x")
    assert shell_designer.held(cfg, store, AiMode(), astra) == (
        (
            "today's AI spend $10.12 has reached the day cap $10.00 ([budget] "
            "day_max_usd): the shell designer is off until tomorrow, or raise day_max_usd"
        ),
        0.0,
    )
    st = shell_designer.status(cfg, store, AiMode(), toml)
    assert st["configured"] and not st["ready"] and "day cap" in st["why"]
    assert st["day_left_usd"] <= 0 and st["label"] == "astra"
    assert MODEL_ID not in json.dumps(st)  # never a model id
    assert shell_designer.status(cfg_of(home, ""), store) == {"configured": False}


# --- design and revise with the built-in fakes ---------------------------------------------------


def test_fake_plain_draws_a_site_design_on_its_first_answer(
    home: Path, toml: Path
) -> None:
    stages: list[str] = []
    got = shell_designer.design(ctx_of(home, toml), site_ask(), stages.append)
    assert got.ok, got.result
    res = got.result
    assert res["designer"] == fakes.PLAIN and res["rounds"] == 0
    assert res["cost_usd"] == 0 and res["version"] == 1
    assert stages[:2] == ["brief", f"asking {fakes.PLAIN}"] and "checking" in stages
    d = versions.get(home, res["design"])
    (v,) = d["versions"]
    assert v["by"] == "architect" and v["designer"] == fakes.PLAIN
    assert v["rounds"] == 0 and v["cost_usd"] == 0
    assert shell_store.site_mode(shell_store.load(home, res["build"]))
    assert versions.current_id(home) == res["design"]


def test_fake_sloppy_passes_after_one_repair(home: Path, toml: Path) -> None:
    got = shell_designer.design(
        ctx_of(home, toml, fakes.SLOPPY), site_ask(), lambda _s: None
    )
    assert got.ok, got.result
    v = versions.get(home, got.result["design"])["versions"][0]
    assert v["designer"] == fakes.SLOPPY and v["rounds"] == 1
    assert v["by"] == "architect" and v["cost_usd"] == 0


def test_fake_cramped_is_refused_after_exactly_two_repairs_and_nothing_is_saved(
    home: Path, toml: Path
) -> None:
    stages: list[str] = []
    got = shell_designer.design(
        ctx_of(home, toml, fakes.CRAMPED),
        shell_brief.DesignRequest(
            (), "an empty hall", mode="free", area=400, theme="th-foundry"
        ),
        stages.append,
    )
    assert got.status == shell_designer.REFUSED
    res = got.result
    assert len(res["attempts"]) == 3 and res["rounds"] == shell_rounds.REPAIR_ROUNDS
    assert [a["round"] for a in res["attempts"]] == [0, 1, 2]
    (why,) = res["refused"]
    assert why.startswith(f"{fakes.CRAMPED}'s 3 answers were all refused. Last: ")
    assert why.endswith("$0.00 spent, nothing saved.")
    assert "repair 1 of 2" in stages and "repair 2 of 2" in stages
    assert not (home / shell_store.ROOT).exists()
    assert not (home / versions.ROOT).exists()


def test_a_plan_free_design_and_its_revision(home: Path, toml: Path) -> None:
    ctx = ctx_of(home, toml)
    got = shell_designer.design(
        ctx,
        shell_brief.DesignRequest(
            (), "an empty marble warehouse", mode="free", area=500, theme="th-foundry"
        ),
        lambda _s: None,
    )
    assert got.ok, got.result
    rec = shell_store.load(home, got.result["build"])
    assert shell_store.free_mode(rec) and rec["fit"]["area"] == 500
    again = shell_designer.revise(
        ctx, got.result["design"], "make the roof steeper", lambda _s: None
    )
    assert again.ok, again.result
    assert again.result["version"] == 2 and again.result["designer"] == fakes.PLAIN
    new = shell_store.load(home, again.result["build"])
    assert shell_store.free_mode(new) and new["fit"]["area"] == 500


def test_a_site_revision_by_the_designer(
    conn: sqlite3.Connection, home: Path, toml: Path
) -> None:
    ds = first(conn, home)
    got = shell_designer.revise(
        ctx_of(home, toml), ds, "make the roof steeper", lambda _s: None
    )
    assert got.ok, got.result
    d = versions.get(home, ds)
    assert len(d["versions"]) == 2 and d["current"] == got.result["build"]
    v = d["versions"][-1]
    assert v["by"] == "architect" and v["designer"] == fakes.PLAIN and v["rounds"] == 0


def test_an_answer_cut_off_at_the_output_limit_says_so(
    home: Path, toml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cut(*_a: Any) -> llm.Reply:  # noqa: ANN401
        return llm.Reply('{"ops": [', Usage(), llm.MAX_TOKENS)

    monkeypatch.setattr(fakes.FakeDesigner, "complete", lambda self, *a: cut(*a))
    got = shell_designer.design(ctx_of(home, toml), site_ask(), lambda _s: None)
    assert got.status == shell_designer.REFUSED
    assert got.result["attempts"][0]["why"] == (
        f"the answer was cut off at the output limit ({shell_brief.MAX_OUTPUT_TOKENS} "
        "tokens): use fewer operations"
    )


def test_unset_and_not_ready_refuse_before_anything(home: Path, toml: Path) -> None:
    got = shell_designer.design(ctx_of(home, toml, ""), site_ask(), lambda _s: None)
    assert got.status == shell_designer.UNSET
    assert got.result["refused"] == [shell_designer.UNSET_WHY]
    got = shell_designer.design(
        ctx_of(home, toml, "zebra"), site_ask(), lambda _s: None
    )
    assert got.status == shell_designer.NOT_READY
    assert not (home / shell_store.ROOT).exists()


# --- the real seam against the fake OpenAI API: cost, meter, caps, errors -----------------------


@pytest.fixture
def astra(
    home: Path, toml: Path, ring: MemoryKeyring, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeResponsesAPI]:
    ring.set_password("gregtec-os/openai", "openai", OPENAI_KEY)
    api = FakeResponsesAPI(good, key=OPENAI_KEY)
    with api:
        monkeypatch.setattr(llm, "OPENAI_URL", api.url)
        yield api


def test_a_priced_design_and_revision_through_the_fake_api(
    home: Path, toml: Path, store: Store, astra: FakeResponsesAPI
) -> None:
    ctx = ctx_of(home, toml, "astra", store)
    got = shell_designer.design(ctx, site_ask(), lambda _s: None)
    assert got.ok, got.result
    one = (2000 * 10 + 400 * 50) / 1e6
    assert got.result["cost_usd"] == pytest.approx(one)
    assert got.result["tokens"] == {"in": 2000, "out": 400}
    v = versions.get(home, got.result["design"])["versions"][0]
    assert v["cost_usd"] == pytest.approx(one) and v["designer"] == "astra"
    again = shell_designer.revise(
        ctx, got.result["design"], "make the roof steeper", lambda _s: None
    )
    assert again.ok, again.result
    (body,) = astra.requests[1:]
    assert body["model"] == MODEL_ID and "tools" not in body
    assert body["input"].startswith("Revise the current design")
    rows = usage_rows(store)
    assert [(r["lane"], r["model"]) for r in rows] == [("designer", MODEL_ID)] * 2
    assert sum(r["usd"] for r in rows) == pytest.approx(2 * one)
    assert all(r["task"] == "designer:cli" for r in rows)
    # the model id only in the meter rows: never in a version, a result or a file
    for text in (json.dumps(got.result), json.dumps(again.result)):
        assert MODEL_ID not in text and OPENAI_KEY not in text
    for path in home.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert MODEL_ID not in text and OPENAI_KEY not in text


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (Answer(http=401), "astra refused the openai key: run `gos key set openai`"),
        (
            Answer(http=429, error_code="insufficient_quota"),
            "openai says astra's quota or credits are used up",
        ),
        (Answer(http=400), "openai rejected the request: OpenAI refused the request"),
    ],
)
def test_a_vendor_error_that_stops_the_model_is_one_try_and_never_no_ai(
    home: Path,
    toml: Path,
    store: Store,
    astra: FakeResponsesAPI,
    answer: Answer,
    why: str,
) -> None:
    astra.policy = lambda body: answer
    mode = AiMode()
    failed: list[BaseException] = []
    mode.failed = lambda exc: failed.append(exc)  # type: ignore[method-assign]
    got = shell_designer.design(
        ctx_of(home, toml, "astra", store, ai=mode), site_ask(), lambda _s: None
    )
    assert got.status == shell_designer.REFUSED
    (said,) = got.result["refused"]
    assert said.startswith(why) and said.endswith("($0.00 spent, nothing saved)")
    assert len(astra.requests) == 1 and failed == [] and mode.reasons() == []
    assert OPENAI_KEY not in said
    assert not (home / shell_store.ROOT).exists()


def test_a_server_error_is_retried_then_refused(
    home: Path, toml: Path, store: Store, astra: FakeResponsesAPI
) -> None:
    astra.policy = lambda body: Answer(http=503)
    got = shell_designer.design(
        ctx_of(home, toml, "astra", store), site_ask(), lambda _s: None
    )
    assert got.result["refused"] == [
        "astra didn't answer after 3 tries (a server error): $0.00 spent, nothing saved"
    ]
    assert len(astra.requests) == 1 + llm.RETRIES


def test_the_guard_stops_before_a_try_past_the_cap_with_the_figures(
    home: Path, toml: Path, store: Store, astra: FakeResponsesAPI
) -> None:
    sloppy = fakes.FakeDesigner(fakes.SLOPPY)
    astra.policy = lambda body: Answer(
        text=sloppy.complete("", body["input"], {}, 0).text,
        input_tokens=2000,
        output_tokens=400,
    )
    probe = ctx_of(home, toml, "astra", store)
    ready = shell_designer.resolve(probe.cfg, toml)
    assert isinstance(ready, shell_designer.Ready)
    conn = probe.connect()
    try:
        brief = shell_brief.prepare_design(conn, home, probe.vault, site_ask()).brief
    finally:
        conn.close()
    system = shell_brief.system_prompt("site")
    most = bo._most(system, ready.spec, brief, "site")[0]
    cap = round(most + 0.05, 2)  # the first try fits, the second can't
    ctx = ctx_of(home, toml, "astra", store, cfg={"shell_designer_max_usd": cap})
    got = shell_designer.design(ctx, site_ask(), lambda _s: None)
    (why,) = got.result["refused"]
    one = (2000 * 10 + 400 * 50) / 1e6
    assert why.startswith(
        f"stopped before try 2: ${one:.2f} spent and the next try could "
    )
    assert why.endswith(f"over this request's cap ${cap:.2f}. Nothing was saved.")
    assert len(astra.requests) == 1
    small = ctx_of(home, toml, "astra", store, cfg={"shell_designer_max_usd": 0.5})
    if most > 0.5:
        got = shell_designer.design(small, site_ask(), lambda _s: None)
        assert got.result["refused"] == [
            (
                f"one try of astra can cost up to ${most:.2f}; this request's cap is "
                "$0.50: raise [shell] designer_max_usd"
            )
        ]
        assert len(astra.requests) == 1  # nothing more was sent


@pytest.mark.parametrize(
    "answer",
    [
        Answer(text="{}" * 20, cut_after=3),  # broke off after it was sent
        Answer(text='{"ops": []}', usage=False),  # answered without a token count
    ],
)
def test_an_unmeasured_try_is_metered_at_its_bound(
    home: Path, toml: Path, store: Store, astra: FakeResponsesAPI, answer: Answer
) -> None:
    astra.policy = lambda body: answer
    shell_designer.design(
        ctx_of(home, toml, "astra", store), site_ask(), lambda _s: None
    )
    rows = usage_rows(store)
    bound = [r for r in rows if r["task"] == "designer:cli:bound"]
    assert bound and all(r["usd"] > 0 and r["lane"] == "designer" for r in bound)
    assert all(r["in_tok"] == 0 and r["out_tok"] == 0 for r in bound)


def test_a_cancel_is_heard_before_the_next_try(home: Path, toml: Path) -> None:
    asked = {"cancel": False}

    def report(name: str | None, k: int | None, n: int | None) -> None:
        if name == "checking":
            asked["cancel"] = True  # Cancel pressed while the first answer is checked
        elif asked["cancel"]:
            raise progress.Cancelled()

    with progress.reporting(report), pytest.raises(progress.Cancelled):
        shell_designer.design(
            ctx_of(home, toml, fakes.SLOPPY), site_ask(), progress.stage
        )
    assert not (home / versions.ROOT).exists()


# --- the shell job and the planner lock ----------------------------------------------------------


def test_an_ai_shell_job_never_holds_the_planner_lock_while_the_model_answers(
    home: Path, toml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gregtec.api import shell_routes

    lock = threading.RLock()
    jobs = design_jobs.DesignJobs(home, lock)
    inside, go = threading.Event(), threading.Event()
    real = fakes.FakeDesigner.complete

    def slow(self: fakes.FakeDesigner, *a: Any) -> llm.Reply:  # noqa: ANN401
        inside.set()
        assert go.wait(10)
        return real(self, *a)

    monkeypatch.setattr(fakes.FakeDesigner, "complete", slow)
    ctx = ctx_of(home, toml, lock=lock)
    title, work = shell_routes.shell_job(
        home,
        {"design": {"plans": ["pl-steel"], "theme": "th-foundry", "ai": True}},
        lock,
        lambda: ctx,
    )
    assert title == f"Shell: {fakes.PLAIN} designs around pl-steel"
    job = jobs.start("shell", title, work)
    assert inside.wait(10)
    assert lock.acquire(timeout=1)  # a power job would go ahead meanwhile
    lock.release()
    with pytest.raises(design_jobs.Busy):
        jobs.start("shell", title, work)  # one shell job at a time
    go.set()
    done = jobs.wait(job.id, 30)
    assert done.status == design_jobs.DONE, done.error
    assert done.result is not None and done.result["designer"] == fakes.PLAIN
    assert MODEL_ID not in json.dumps(done.result)


def test_a_procedural_shell_job_holds_the_lock_for_its_whole_work(home: Path) -> None:
    from gregtec.api import shell_routes

    lock = threading.RLock()
    jobs = design_jobs.DesignJobs(home, lock)
    title, work = shell_routes.shell_job(
        home, {"design": {"plans": ["pl-steel"], "theme": "th-foundry"}}, lock
    )
    assert lock.acquire()  # the planner is busy: the job waits
    try:
        job = jobs.start("shell", title, work)
        time.sleep(0.3)
        assert jobs.get(job.id).status == design_jobs.RUNNING  # type: ignore[union-attr]
    finally:
        lock.release()
    assert jobs.wait(job.id, 30).status == design_jobs.DONE


# --- the routes ----------------------------------------------------------------------------------


def _client(home: Path, toml: Path, label: str) -> Any:  # noqa: ANN401
    from gregtec.api.app import Core
    from gregtec.api.security import Auth

    store = Store(access.db_path(home))
    core = Core(
        cfg=cfg_of(home, label),
        store=store,
        auth=Auth(store, ADMIN),
        meter=UsageMeter(store, 100.0, (80, 100)),
        config_path=toml,
    )
    return core, store


def _wait(c: Any, job: str) -> dict[str, Any]:  # noqa: ANN401
    admin = {"Authorization": f"Bearer {ADMIN}"}
    for _ in range(600):
        j = c.get(f"/api/design-jobs/{job}", headers=admin).json()["job"]
        if j["status"] != "running":
            return j  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError("the job didn't end")


def test_the_routes_run_the_designer_as_a_shell_job(home: Path, toml: Path) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api.app import create_app

    admin = {"Authorization": f"Bearer {ADMIN}"}
    for label, status in (("", {"configured": False}), ("zebra", None)):
        core, store = _client(home, toml, label)
        try:
            with TestClient(
                create_app(core), base_url="http://gos.localhost:8765"
            ) as c:
                got = c.get("/api/shells/designer", headers=admin).json()
                if status is not None:
                    assert got == status
                else:
                    assert got["configured"] and not got["ready"]
                    assert got["why"] == "no [[bakeoff.models]] entry is labelled zebra"
                r = c.post(
                    "/api/shells/jobs",
                    headers=admin,
                    json={"design": {"plans": ["pl-steel"], "ai": True}},
                )
                assert r.status_code == 409, r.text
                why = (
                    shell_designer.UNSET_WHY
                    if not label
                    else "no [[bakeoff.models]] entry is labelled zebra"
                )
                assert r.json()["error"] == why
                assert c.get("/api/design-jobs", headers=admin).json()["running"] == []
        finally:
            store.close()
    core, store = _client(home, toml, fakes.PLAIN)
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            got = c.get("/api/shells/designer", headers=admin).json()
            assert (
                got["ready"]
                and got["label"] == fakes.PLAIN
                and got["provider"] == "fake"
            )
            bad = c.post(
                "/api/shells/jobs",
                headers=admin,
                json={"design": {"plans": ["pl-steel"], "ai": "yes"}},
            )
            assert bad.status_code == 400 and "ai is true or false" in bad.text
            r = c.post(
                "/api/shells/jobs",
                headers=admin,
                json={
                    "design": {
                        "mode": "free",
                        "area": 400,
                        "theme": "th-foundry",
                        "ai": True,
                    }
                },
            )
            assert r.status_code == 200, r.text
            started = r.json()["job"]
            assert started["title"] == (
                f"Shell: {fakes.PLAIN} designs a plan-free building (≥400 floor)"
            )
            j = _wait(c, started["id"])
            assert j["status"] == "done", j
            res = j["result"]
            assert res["designer"] == fakes.PLAIN and res["version"] == 1, res
            ds = res["design"]
            for body, code, why in (
                ({"design": ds, "words": "", "ai": True}, 400, "say what to change"),
                (
                    {"design": ds, "words": "taller", "headroom": 8, "ai": True},
                    400,
                    "from your words alone",
                ),
            ):
                r = c.post("/api/shells/jobs", headers=admin, json={"revise": body})
                assert r.status_code == code and why in r.text, r.text
            r = c.post(
                "/api/shells/jobs",
                headers=admin,
                json={
                    "revise": {
                        "design": ds,
                        "words": "make the roof steeper",
                        "ai": True,
                    }
                },
            )
            assert r.status_code == 200, r.text
            assert (
                r.json()["job"]["title"]
                == f"Shell: {fakes.PLAIN} revises {ds}: make the roof steeper"
            )
            j = _wait(c, r.json()["job"]["id"])
            assert j["status"] == "done" and j["result"]["version"] == 2, j
            d = c.get(f"/api/shells/designs/{ds}", headers=admin).json()["design"]
            assert [v.get("designer") for v in d["versions"]] == [fakes.PLAIN] * 2
    finally:
        store.close()


# --- chat and the architect's tools --------------------------------------------------------------


def _asker(
    home: Path, toml: Path, label: str, mode: AiMode, started: list[dict[str, Any]]
) -> tuple[ask.Asker, HandoffRecorder]:
    holder = ShellJobs(cfg_of(home, label), toml)

    def start(body: dict[str, Any]) -> dict[str, Any]:
        started.append(body)
        return {"id": "job-0123456789ab", "title": "Shell: x"}

    holder.start = start
    reg = build_registry(
        access.db_path(home), vault=VaultIO(home / "vault"), shell_jobs=holder
    )
    deep = HandoffRecorder()
    return ask.Asker(reg, deep, None, ai=mode, home=home, shell_jobs=holder), deep


def test_chat_revise_starts_the_designer_job_at_once(
    conn: sqlite3.Connection, home: Path, toml: Path
) -> None:
    from gregtec.agents import router

    ds = first(conn, home)
    started: list[dict[str, Any]] = []
    mode = AiMode()
    mode.key_seen(
        False
    )  # no Anthropic key: the designer isn't Claude, so it still runs
    a, deep = _asker(home, toml, fakes.PLAIN, mode, started)
    reply = a.ask("make the roof steeper")
    assert reply.lane == router.INSTANT and reply.intent == router.SHELL_REVISE
    assert reply.text == (
        f"Asked {fakes.PLAIN} to revise {ds} (job job-0123456789ab); the new version shows "
        "in the Shell workbench when it's done."
    )
    assert started == [
        {"revise": {"design": ds, "words": "make the roof steeper", "ai": True}}
    ]
    assert not deep.recent()
    # set but not ready: why first, then the architect as before
    started.clear()
    a, deep = _asker(home, toml, "zebra", AiMode(), started)
    reply = a.ask("make the roof steeper")
    assert reply.lane == router.DEEP and started == []
    assert reply.text.startswith(
        "The shell designer isn't ready (no [[bakeoff.models]] entry is labelled zebra). "
    )
    assert deep.recent()[-1].request == "make the roof steeper"
    # his switch off: GOS's own reviser, in code, as before
    off = AiMode()
    off.set_choice(False)
    a, deep = _asker(home, toml, fakes.PLAIN, off, started)
    reply = a.ask("make the roof steeper")
    assert reply.lane == "no_ai" and started == [] and "version 2" in reply.text
    # busy
    a, _ = _asker(home, toml, fakes.PLAIN, AiMode(), started)
    assert a.shell_jobs is not None

    def busy(body: dict[str, Any]) -> dict[str, Any]:
        raise ShellJobError("a shell design is already running (x): wait for it")

    a.shell_jobs.start = busy
    reply = a.ask("make the roof steeper")
    assert reply.text == (
        "No shell revision: a shell design is already running (x): wait for it."
    )


def _call(reg: Any, name: str, args: dict[str, Any]) -> Any:  # noqa: ANN401
    return reg.call(name, args, lane=DEEP)


def test_the_architect_asks_the_designer_and_may_not_write_operations(
    conn: sqlite3.Connection, home: Path, toml: Path
) -> None:
    started: list[dict[str, Any]] = []
    a, _ = _asker(home, toml, fakes.PLAIN, AiMode(), started)
    reg = a.registry
    out = _call(
        reg,
        "build_request",
        {
            "plans": "pl-steel",
            "theme": "th-foundry",
            "prompt": "a clock tower",
            "ask_designer": True,
        },
    )
    assert not out.is_error, out.text
    assert started == [
        {
            "design": {
                "mode": "site",
                "prompt": "a clock tower",
                "line_type": "general",
                "ai": True,
                "plans": ["pl-steel"],
                "theme": "th-foundry",
            }
        }
    ]
    assert "job-0123456789ab" in out.text and "Shell workbench" in out.text
    hand = _call(reg, "build_request", {"plans": "pl-steel", "design": "[]"})
    assert (
        hand.is_error
        and (
            f"a shell designer ({fakes.PLAIN}) is configured: call build_request with "
            "ask_designer=true"
        )
        in hand.text
    )
    look = _call(reg, "build_request", {"plans": "pl-steel", "save": False})
    assert not look.is_error, look.text
    assert parse(look.text).head["designer"] == f"{fakes.PLAIN} (ready)"
    mixed = _call(
        reg,
        "build_request",
        {"plans": "pl-steel", "palette": "x", "ask_designer": True},
    )
    assert mixed.is_error and "leave out palette" in mixed.text
    ds = first(conn, home)
    started.clear()
    out = _call(reg, "build_revise", {"words": "taller", "ask_designer": True})
    assert not out.is_error, out.text
    assert started == [{"revise": {"design": ds, "words": "taller", "ai": True}}]
    ops = _call(reg, "build_revise", {"words": "taller", "design_ops": "[]"})
    assert ops.is_error and "call build_revise with ask_designer=true" in ops.text
    # none set: the architect writes it; asking for a designer is refused
    a, _ = _asker(home, toml, "", AiMode(), started)
    none = _call(
        a.registry, "build_request", {"plans": "pl-steel", "ask_designer": True}
    )
    assert none.is_error and "no shell designer is set: write the design yourself" in (
        none.text
    )
    look = _call(a.registry, "build_request", {"plans": "pl-steel", "save": False})
    assert parse(look.text).head["designer"] == "none"
    # set but not ready: the architect may write it, and the head says why
    a, _ = _asker(home, toml, "zebra", AiMode(), started)
    look = _call(a.registry, "build_request", {"plans": "pl-steel", "save": False})
    assert parse(look.text).head["designer"] == (
        "zebra (not ready: no [[bakeoff.models]] entry is labelled zebra)"
    )
    # outside the core (gos ask in a terminal): no job starter
    a, _ = _asker(home, toml, fakes.PLAIN, AiMode(), started)
    assert a.shell_jobs is not None
    a.shell_jobs.start = None
    out = _call(
        a.registry, "build_request", {"plans": "pl-steel", "ask_designer": True}
    )
    assert out.is_error and "runs in the core (gos serve)" in out.text


# --- the CLI ---------------------------------------------------------------------------------------


def gos(toml: Path, *argv: str, typed: str = "") -> tuple[int, str]:
    out = io.StringIO()
    from gregtec.build import shell_cli

    real = shell_cli.run

    def run(args: Any, _out: Any = None) -> int:  # noqa: ANN401
        return real(args, out)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(shell_cli, "run", run)
        mp.setattr(sys, "stdin", io.StringIO(typed))
        code = cli.main(["shell", *argv, "--config", str(toml)])
    return code, out.getvalue()


def _with(toml: Path, label: str) -> Path:
    text = toml.read_text(encoding="utf-8")
    path = toml.with_name(f"gos-{label or 'none'}.toml")
    path.write_text(text + f'\n[shell]\ndesigner = "{label}"\n', encoding="utf-8")
    return path


def test_gos_shell_design_ai_with_a_fake(home: Path, toml: Path) -> None:
    code, text = gos(
        _with(toml, fakes.PLAIN), "design", "pl-steel", "--theme", "th-foundry", "--ai"
    )
    assert code == 0, text
    assert f"{fakes.PLAIN} · this request's cap $4.00 · one try up to $0.00" in text
    assert "by fake-plain: 0 repair round(s), $0.00" in text
    code, text = gos(
        _with(toml, fakes.CRAMPED),
        "design",
        "--mode",
        "free",
        "--area",
        "400",
        "--theme",
        "th-foundry",
        "--ai",
    )
    assert code == 1 and "REFUSED: nothing written" in text, text
    assert "fake-cramped's 3 answers were all refused" in text
    code, text = gos(_with(toml, ""), "design", "pl-steel", "--ai")
    assert code == 2 and shell_designer.UNSET_WHY in text
    code, text = gos(
        _with(toml, fakes.PLAIN), "design", "pl-steel", "--ai", "--design", "x"
    )
    assert code == 2 and "--ai: leave out --design" in text
    code, text = gos(_with(toml, fakes.PLAIN), "revise", "taller", "--ai")
    assert code == 0, text


def test_gos_shell_design_ai_with_a_priced_model_waits_for_yes(
    home: Path, toml: Path, astra: FakeResponsesAPI
) -> None:
    path = _with(toml, "astra")
    code, text = gos(
        path, "design", "pl-steel", "--theme", "th-foundry", "--ai", typed="no\n"
    )
    assert code == 1 and "Type yes to spend up to $4.00: " in text
    assert "not run: nothing was spent" in text and astra.requests == []
    code, text = gos(
        path, "design", "pl-steel", "--theme", "th-foundry", "--ai", typed="yes\n"
    )
    assert code == 0, text
    assert len(astra.requests) == 1 and "by astra" in text
    assert MODEL_ID not in text and OPENAI_KEY not in text
    code, text = gos(path, "revise", "make", "it", "taller", "--ai", "--yes")
    assert code == 0 and "Type yes" not in text, text


# --- the shared engine -------------------------------------------------------------------------------


def test_the_rounds_engine_repairs_with_the_reason_and_stops_at_two() -> None:
    asked: list[str] = []
    refused: list[str] = []
    rounds: list[int] = []

    def ask_(user: str) -> llm.Reply:
        asked.append(user)
        return llm.Reply('{"ops": []}', Usage())

    ok = shell_rounds.rounds(
        ask_,
        "NEED: x\nthe brief",
        "layout",
        lambda said: "too small",
        refused.append,
        rounds.append,
    )
    assert not ok and rounds == [0, 1, 2] and refused == ["too small"] * 3
    assert asked[0] == "NEED: x\nthe brief"
    assert "The code refused it, and nothing was written: too small." in asked[1]
    assert shell_rounds.rounds(
        ask_, "b", "layout", lambda said: None, refused.append, rounds.append
    )
    assert not shell_rounds.rounds(
        lambda user: "no answer",
        "b",
        "layout",
        lambda s: None,
        refused.append,
        rounds.append,
    )
    assert refused[-1] == "no answer"
    long = "x" * 100
    assert "\n" + "x" * 10 + " ...(cut)\n" in shell_brief.repair_brief(
        "b", long, "w", echo=10
    )


def test_a_fake_designer_needs_no_store_and_no_meter(home: Path, toml: Path) -> None:
    ctx = replace(ctx_of(home, toml), task="job-x")
    ready = shell_designer.resolve(ctx.cfg, toml)
    assert ctx.meter is None and shell_designer.held(ctx.cfg, None, None, ready) == (
        None,
        4.0,
    )
