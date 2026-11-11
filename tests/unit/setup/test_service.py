"""WO-2.9: the wizard's steps, their checks, gos.toml writes that config.py reads back, secrets in
the keyring (never echoed), the key's test call, the device list and finishing setup."""

from __future__ import annotations

import functools
import logging
import os
import shutil
import sqlite3
import tomllib
from pathlib import Path

import anthropic
import httpx2
import pytest

from gregtec import config, doctor
from gregtec.agents.no_ai import AUTH, CHOICE, AiMode
from gregtec.config import Config
from gregtec.db import access
from gregtec.llm import keys
from gregtec.llm.fake import ScriptedClient
from gregtec.llm.usage import UsageMeter
from gregtec.setup import registry
from gregtec.setup.devices import Device, DeviceError, FakeDevices, listing
from gregtec.setup.registry import Context, Field, Outcome, Step
from gregtec.setup.service import SetupError, SetupService
from gregtec.setup.steps import voice
from gregtec.store import Store

from .conftest import path_values
from .fakes import (
    ANTHROPIC_KEY,
    DISCORD_TOKEN,
    MemoryKeyring,
    link_folder,
    make_instance,
    make_prism,
)


def by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["id"]: r for r in rows}


# --- the registry ------------------------------------------------------------------------------


def test_the_registry_finds_every_built_in_step_in_order() -> None:
    ids = [s.id for s in registry.steps()]
    assert ids == [
        "paths",
        "anthropic",
        "discord",
        "elevenlabs",
        "headset",
        "talk_key",
        "wake",
        "checklist",
    ]


def test_a_later_phase_adds_a_step_with_a_module_of_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No wizard file changes: a module in gregtec.setup.steps with STEPS is enough (D167)."""
    from gregtec.setup import steps as package

    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "zz_later.py").write_text(
        "from gregtec.setup.registry import Field, Step\n"
        "STEPS = [Step('later', 'Later', 70, 'A later phase.', "
        "(Field('x', 'X', 'text', toml=('later', 'x')),))]\n",
        "utf-8",
    )
    monkeypatch.setattr(package, "__path__", [*package.__path__, str(extra)])
    ids = [s.id for s in registry.steps()]
    assert ids.index("wake") < ids.index("later") < ids.index("checklist")


def test_fields_are_checked_when_declared() -> None:
    with pytest.raises(ValueError):
        Field("a", "A", "colour", toml=("t", "a"))
    with pytest.raises(ValueError):
        Field("a", "A", "secret")
    with pytest.raises(ValueError):
        Field("a", "A", "text")
    with pytest.raises(ValueError):
        Field("a", "A", "device", toml=("voice", "a"))


# --- describing and the defaults ---------------------------------------------------------------


def test_a_fresh_install_shows_the_running_configs_defaults(
    svc: SetupService, home: Path
) -> None:
    out = svc.describe()
    assert out["done"] is False and out["config_error"] is None
    assert out["config_path"] == str(home / "config" / "gos.toml")
    steps = {s["id"]: s for s in out["steps"]}
    paths = {f["id"]: f for f in steps["paths"]["fields"]}
    assert paths["gos_home"]["value"] == str(home)
    assert (
        paths["play"]["value"] == "GTNH-Play" and paths["play"]["key"] == "[paths] play"
    )
    talk = {f["id"]: f for f in steps["talk_key"]["fields"]}
    assert (
        talk["talk_key"]["value"] == "F13" and "F24" in talk["talk_key"]["suggestions"]
    )
    secret = steps["anthropic"]["fields"][0]
    assert secret == secret | {"kind": "secret", "stored": False, "test": True}
    assert "value" not in secret
    headset = {f["id"]: f for f in steps["headset"]["fields"]}
    assert headset["output_endpoint"]["value"] == "Chat"
    assert headset["input_device"]["devices"] == "inputs"


def test_a_broken_gos_toml_is_reported_not_overwritten(svc: SetupService) -> None:
    svc.path.parent.mkdir(parents=True)
    svc.path.write_text("[paths\n", "utf-8")
    assert "fix it by hand" in svc.describe()["config_error"]
    with pytest.raises(SetupError) as err:
        svc.save("play_never", {})
    assert err.value.status == 404
    with pytest.raises(SetupError) as err:
        svc.save("talk_key", {"talk_key": "F18", "talk_mode": "hold"})
    assert err.value.status == 409
    assert svc.path.read_text("utf-8") == "[paths\n"


# --- the paths step ----------------------------------------------------------------------------


def test_paths_are_checked_with_doctors_checks_and_saved(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    app = tmp_path / "GregtecOS-app"
    app.mkdir()
    values = path_values(prism, home, app)
    outcome, _ = svc.check("paths", values)
    assert not outcome.blocked, outcome
    rows = {c.id: c for c in outcome.checks}
    assert (
        rows["instance.play"].status == "ok"
        and "GTNH 2.9.0" in rows["instance.play"].detail
    )
    assert rows["instance.lab"].status == "ok"
    assert rows["paths.gos_home"].detail == "not there yet: GOS makes it"
    saved = svc.save("paths", values)
    assert "[paths] prism_instances" in saved["saved"]
    cfg = config.load(svc.path)  # config.py's loader reads it back (WO-2.9)
    assert cfg.prism_instances == prism / "instances" and cfg.prism_root == prism
    assert (cfg.play, cfg.export, cfg.lab) == ("GTNH-Play", "GTNH-Export", "GTNH-Lab")
    assert cfg.gos_home == home and cfg.gos_app == app


def test_a_missing_play_instance_or_relative_path_blocks_the_save(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    values = path_values(prism, home, tmp_path / "app")
    values["play"] = "Nope"
    outcome, _ = svc.check("paths", values)
    assert outcome.blocked
    assert {c.id: c.status for c in outcome.checks}["instance.play"] == "error"
    with pytest.raises(SetupError) as err:
        svc.save("paths", values)
    assert err.value.status == 400 and not svc.path.exists()
    values = path_values(prism, home, tmp_path / "app") | {
        "prism_root": "relative/prism"
    }
    outcome, _ = svc.check("paths", values)
    assert set(outcome.problems) == {"prism_root"}
    outcome, _ = svc.check(
        "paths", path_values(prism, home, tmp_path / "app") | {"lab": "a/b"}
    )
    assert set(outcome.problems) == {"lab"}


def test_missing_export_and_lab_and_gos_app_only_warn(
    svc: SetupService, tmp_path: Path, home: Path
) -> None:
    prism = tmp_path / "OnlyPlay"
    make_instance(prism / "instances", "GTNH-Play")
    outcome, _ = svc.check("paths", path_values(prism, home, tmp_path / "no-app"))
    rows = {c.id: c.status for c in outcome.checks}
    assert (
        rows["instance.export"]
        == rows["instance.lab"]
        == rows["paths.gos_app"]
        == "warn"
    )
    assert not outcome.blocked


def test_the_same_instance_twice_is_an_error(
    svc: SetupService, prism: Path, home: Path
) -> None:
    values = path_values(prism, home, prism) | {"lab": "gtnh-play"}
    outcome, _ = svc.check("paths", values)
    assert {c.id: c.status for c in outcome.checks}["paths.instances"] == "error"


def test_a_wrong_pack_version_warns(
    svc: SetupService, tmp_path: Path, home: Path
) -> None:
    prism = tmp_path / "Old"
    for name in ("GTNH-Play", "GTNH-Export", "GTNH-Lab"):
        make_instance(prism / "instances", name, version="2.8.4")
    outcome, _ = svc.check("paths", path_values(prism, home, prism))
    play = next(c for c in outcome.checks if c.id == "instance.play")
    assert play.status == "warn" and "pinned to 2.9.0" in play.detail


def test_gos_home_away_from_gos_toml_warns(
    svc: SetupService, prism: Path, tmp_path: Path
) -> None:
    outcome, _ = svc.check("paths", path_values(prism, tmp_path / "elsewhere", prism))
    assert {c.id: c.status for c in outcome.checks}["paths.config"] == "warn"


def test_unknown_or_bad_values_are_refused(svc: SetupService) -> None:
    for values, why in (
        ({"nope": "x"}, "no value field"),
        ({"play": 3}, "a string"),
        ({"play": "a\x00b"}, "control"),
        ({"play": "x" * 401}, "a string"),
    ):
        with pytest.raises(SetupError, match=why):
            svc.check("paths", values)


# --- voice steps ------------------------------------------------------------------------------


def test_the_talk_key_follows_the_voice_clients_rules(svc: SetupService) -> None:
    outcome, _ = svc.check("talk_key", {"talk_key": "F1", "talk_mode": "hold"})
    assert "Minecraft hard-codes F1" in outcome.problems["talk_key"]
    outcome, _ = svc.check("talk_key", {"talk_key": "F14", "talk_mode": "hold"})
    assert "both F14" in outcome.problems["talk_key"]  # the confirm hotkey's default
    outcome, _ = svc.check("talk_key", {"talk_key": "F20", "talk_mode": "sideways"})
    assert outcome.problems == {"talk_mode": "pick one of the choices"}
    svc.save("talk_key", {"talk_key": "Scroll Lock", "talk_mode": "toggle"})
    from gregtec_voice import settings

    _, read = settings.load(svc.path)
    assert read.talk_key.label == "Scroll Lock" and read.talk_mode == "toggle"


def test_the_headset_step_saves_device_names(svc: SetupService) -> None:
    outcome, _ = svc.check(
        "headset", {"input_device": "Arctis", "output_endpoint": "Elite Chat"}
    )
    rows = {c.id: c for c in outcome.checks}
    assert rows["voice.input_device"].detail == "Microphone (Arctis Nova Elite)"
    assert rows["voice.provider"].status == "warn"  # fake devices off Windows
    outcome, _ = svc.check("headset", {"input_device": "", "output_endpoint": "Arctis"})
    rows = {c.id: c for c in outcome.checks}
    assert "Windows' default" in rows["voice.input_device"].detail
    assert rows["voice.output_endpoint"].status == "warn"  # matches Game and Chat
    svc.save(
        "headset",
        {"input_device": "", "output_endpoint": "Headphones (Arctis Nova Elite Chat)"},
    )
    data = tomllib.loads(svc.path.read_text("utf-8"))
    assert data["voice"] == {"output_endpoint": "Headphones (Arctis Nova Elite Chat)"}


def test_device_listing() -> None:
    fake = FakeDevices(inputs=[Device("Mic", True)], outputs=[])
    assert listing(fake) == {
        "provider": "fake",
        "inputs": [{"name": "Mic", "default": True}],
        "outputs": [],
        "error": None,
    }

    class Broken(FakeDevices):
        def inputs(self) -> list[Device]:
            raise DeviceError("PortAudio didn't start")

    assert listing(Broken())["error"] == "PortAudio didn't start"


@pytest.mark.parametrize(
    ("phrase", "why"),
    [
        ("hey gregtech", "PM08"),
        ("okay greg tec", "PM08"),
        ("Hey GregTek", "PM08"),
        ("archivist", "two to five words"),
        ("hey 2 you", "two to five words"),
    ],
)
def test_wake_phrases_never_name_gregtech(
    svc: SetupService, phrase: str, why: str
) -> None:
    outcome, _ = svc.check("wake", {"wake_phrase": phrase})
    assert why in outcome.problems["wake_phrase"]


def test_a_wake_phrase_is_saved_and_risky_words_warn(svc: SetupService) -> None:
    outcome, _ = svc.check("wake", {"wake_phrase": "hey computer"})
    assert outcome.checks[0].status == "warn" and not outcome.blocked
    outcome, _ = svc.check("wake", {"wake_phrase": ""})
    assert outcome.checks[0].status == "warn"
    svc.save("wake", {"wake_phrase": "hey archivist"})
    assert (
        tomllib.loads(svc.path.read_text("utf-8"))["voice"]["wake_phrase"]
        == "hey archivist"
    )
    svc.save("wake", {"wake_phrase": ""})  # empty: the key goes away
    assert "wake_phrase" not in svc.path.read_text("utf-8")


def test_voice_readback_names_what_the_voice_client_refuses() -> None:
    assert voice.readback({"talk_key": "F13"})[0].status == "ok"
    row = voice.readback({"talk_key": "a"})[0]
    assert row.status == "error" and "talk_key" in row.detail


# --- secrets ----------------------------------------------------------------------------------


def test_secrets_are_stored_in_the_keyring_and_never_echoed(
    svc: SetupService, ring: MemoryKeyring, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    out = svc.store_secret("anthropic", f"  {ANTHROPIC_KEY}\n")
    assert out == {"name": "anthropic", "stored": True, "replaced": False}
    assert ring.entries[("gregtec-os/anthropic", "anthropic")] == ANTHROPIC_KEY
    svc.store_secret("discord", DISCORD_TOKEN)
    assert keys.get_key(keys.DISCORD) == DISCORD_TOKEN
    described = repr(svc.describe())
    assert ANTHROPIC_KEY not in described and DISCORD_TOKEN not in described
    assert ANTHROPIC_KEY not in caplog.text and DISCORD_TOKEN not in caplog.text
    assert not svc.path.exists()  # never in gos.toml
    assert svc.delete_secret("discord") == {
        "name": "discord",
        "stored": False,
        "deleted": True,
    }
    assert not keys.has_key(keys.DISCORD)


@pytest.mark.parametrize(
    ("name", "value", "why"),
    [
        ("anthropic", "sk-ant-admin01-" + "x" * 40, "Admin API key"),
        ("anthropic", "hello", "starts with sk-ant-"),
        ("discord", "Bot " + DISCORD_TOKEN, "without 'Bot '"),
        ("discord", "not.a-token", "bot token"),
        ("anthropic", "", "nothing to store"),
        ("anthropic", None, "nothing to store"),
    ],
)
def test_bad_secrets_are_refused_without_saying_them(
    svc: SetupService, ring: MemoryKeyring, name: str, value: str | None, why: str
) -> None:
    with pytest.raises(SetupError, match=why) as err:
        svc.store_secret(name, value)
    if value:
        assert value not in str(err.value.body)
    assert ring.entries == {}


def test_only_secrets_a_step_names_can_be_stored(svc: SetupService) -> None:
    with pytest.raises(SetupError) as err:
        svc.store_secret("token-voice", "x" * 50)
    assert err.value.status == 404


def test_the_key_test_uses_the_fast_lanes_model_and_meters_it(
    svc: SetupService, clients: list[ScriptedClient], tmp_path: Path
) -> None:
    with pytest.raises(SetupError, match="store it first"):
        svc.test_secret("anthropic")
    svc.store_secret("anthropic", ANTHROPIC_KEY)
    store = Store(tmp_path / "gos.sqlite")
    try:
        svc.env.meter = UsageMeter(store, 100.0, (80, 100))
        out = svc.test_secret("anthropic")
        assert out["ok"] is True and "claude-haiku-5-5" in out["detail"]
        request = clients[-1].requests[0]
        assert request["model"] == "claude-haiku-5-5"
        assert (
            request["thinking"] == {"type": "disabled"} and request["max_tokens"] == 8
        )
        assert clients[-1].key == ANTHROPIC_KEY  # type: ignore[attr-defined]
        rows = store.rows("SELECT lane, model, task FROM llm_usage")
        assert [tuple(r) for r in rows] == [("fast", "claude-haiku-5-5", "setup-test")]
    finally:
        store.close()
    with pytest.raises(SetupError) as err:
        svc.test_secret("discord")
    assert err.value.status == 404


def _status_error(status: int, message: str) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(
        status, request=request, json={"type": "error", "error": {"message": message}}
    )
    return anthropic.APIStatusError(message, response=response, body=response.json())


def test_a_refused_key_turns_the_ai_off_and_says_why(svc: SetupService) -> None:
    svc.store_secret("anthropic", ANTHROPIC_KEY)
    ai = AiMode()
    svc.env.ai = ai

    def refusing(key: str) -> object:
        class Messages:
            def create(self, **request: object) -> object:
                raise _status_error(401, "invalid x-api-key")

        class Client:
            messages = Messages()

        return Client()

    svc.env.client_factory = refusing
    out = svc.test_secret("anthropic")
    assert out["ok"] is False and AUTH in ai.reasons()
    assert ANTHROPIC_KEY not in repr(out)


def test_no_test_call_while_the_ai_is_switched_off(
    svc: SetupService, clients: list[ScriptedClient]
) -> None:
    svc.store_secret("anthropic", ANTHROPIC_KEY)
    ai = AiMode()
    ai.set_choice(False)
    svc.env.ai = ai
    out = svc.test_secret("anthropic")
    assert out["ok"] is False and "you turned the AI off" in out["detail"]
    assert clients == []  # nothing was called
    assert CHOICE in ai.reasons()


def test_the_core_is_told_when_a_secret_changes(svc: SetupService) -> None:
    told: list[int] = []
    svc.on_secret = lambda: told.append(1)
    svc.store_secret("anthropic", ANTHROPIC_KEY)
    svc.delete_secret("anthropic")
    assert told == [1, 1]


# --- the checklist and finishing ---------------------------------------------------------------


def configure(svc: SetupService, prism: Path, home: Path, app: Path) -> None:
    app.mkdir(exist_ok=True)
    svc.save("paths", path_values(prism, home, app))
    svc.store_secret("anthropic", ANTHROPIC_KEY)
    svc.save("talk_key", {"talk_key": "F13", "talk_mode": "hold"})


def test_finish_needs_a_clean_checklist_and_marks_setup_done(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    with pytest.raises(
        SetupError
    ) as err:  # nothing configured: Prism isn't at the default
        svc.finish()
    assert any(c["status"] == "error" for c in err.value.body["checks"])
    configure(svc, prism, home, tmp_path / "app")
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["secret.anthropic"]["status"] == "ok"
    assert rows["secret.discord"]["status"] == "warn"
    # The default voice is local: ElevenLabs' key isn't needed (WO-5.3, D203).
    assert rows["secret.elevenlabs"] == {
        **rows["secret.elevenlabs"],
        "status": "ok",
        "detail": "not stored (not needed)",
    }
    assert (
        rows["voice.settings"]["status"] == "ok" and rows["keyring"]["status"] == "ok"
    )
    assert rows["db.gt_pin"]["status"] == "ok"
    out = svc.finish()
    assert out["done"] is True
    assert svc.describe()["done"] is True
    cfg = config.load(svc.path)  # [setup] doesn't upset the loader
    assert cfg.prism_root == prism
    # Re-entering later edits settings; done stays.
    svc.save("talk_key", {"talk_key": "F18", "talk_mode": "hold"})
    assert svc.describe()["done"] is True
    assert 'talk_key = "F18"' in svc.path.read_text("utf-8")


def test_a_bad_voice_table_saved_by_hand_fails_the_checklist(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    configure(svc, prism, home, tmp_path / "app")
    svc.path.write_text(svc.path.read_text("utf-8").replace('"F13"', '"Q"'), "utf-8")
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["voice.settings"]["status"] == "error"
    with pytest.raises(SetupError):
        svc.finish()


def test_a_save_that_config_would_refuse_writes_nothing(
    svc: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc.path.parent.mkdir(parents=True)
    svc.path.write_text("[paths]\nplay = 'A'\n", "utf-8")

    def refuse(path: Path | None = None) -> Config:
        raise config.ConfigError("nope")

    monkeypatch.setattr(config, "load", refuse)
    with pytest.raises(SetupError, match="wouldn't read back"):
        svc._write({("paths", "play"): "B"})
    assert svc.path.read_text("utf-8") == "[paths]\nplay = 'A'\n"


def test_doctor_checklist_on_a_fake_prism_tree_passes(
    prism: Path, tmp_path: Path
) -> None:
    (tmp_path / "app").mkdir()
    cfg = Config(
        prism_root=prism,
        prism_instances=prism / "instances",
        gos_app=tmp_path / "app",
        gos_home=tmp_path / "home",
    )
    rows = doctor.checklist(cfg)
    assert [r.status for r in rows] == ["ok"] * len(rows), rows
    assert [r.id for r in rows] == [
        "paths.prism_root",
        "paths.prism_instances",
        "instance.play",
        "instance.export",
        "instance.lab",
        "paths.gos_app",
        "paths.gos_home",
        "db.gt_pin",
    ]


def test_a_step_check_is_called_with_the_merged_values(svc: SetupService) -> None:
    seen: list[dict[str, str]] = []

    def check(ctx: Context, values: dict[str, str]) -> Outcome:
        seen.append(dict(values))
        return Outcome()

    svc.steps = [
        Step(
            "x",
            "X",
            1,
            "",
            (
                Field("a", "A", "text", toml=("x", "a"), default="one"),
                Field("b", "B", "text", toml=("x", "b"), default="two"),
            ),
            check=check,  # type: ignore[arg-type]
        )
    ]
    svc.check("x", {"b": " three "})
    assert seen == [{"a": "one", "b": "three"}]


# --- review fixes: Play is never moved or written by a click (H1), nothing locks the dashboard
# out (H2), PortAudio one at a time (M1), devices and keys (L1, L2, L5, verdict 5) ----------------


def test_swapping_play_with_lab_is_refused_once_play_is_saved(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    values = path_values(prism, home, tmp_path / "app")
    svc.save("paths", values)
    before = svc.path.read_text("utf-8")
    swapped = values | {"play": "GTNH-Lab", "lab": "GTNH-Play"}
    outcome, _ = svc.check("paths", swapped)
    assert set(outcome.problems) == {"play", "lab"}
    assert "never moves Play" in outcome.problems["play"]
    with pytest.raises(SetupError) as err:
        svc.save("paths", swapped)
    assert err.value.status == 400 and svc.path.read_text("utf-8") == before
    # A new instances folder moves Play too.
    other = make_prism(tmp_path / "Other")
    moved = values | {
        "prism_root": str(other),
        "prism_instances": str(other / "instances"),
    }
    assert "play" in svc.check("paths", moved)[0].problems
    # The rest can still be changed.
    svc.save("paths", values | {"gos_app": str(tmp_path / "app2")})


def test_export_or_lab_can_never_be_the_play_the_core_runs_with(
    ring: MemoryKeyring, prism: Path, home: Path, tmp_path: Path
) -> None:
    """No gos.toml yet: the core runs with its defaults, so their Play is guarded too, and as it
    is a folder there it can't move either (final check round 2)."""
    svc = SetupService(
        home / "config" / "gos.toml",
        Config(gos_home=home, prism_instances=prism / "instances"),
        provider=FakeDevices(),
    )
    values = path_values(prism, home, tmp_path / "app") | {
        "play": "GTNH-Lab",
        "export": "GTNH-Play",
        "lab": "GTNH-Export",
    }
    outcome, _ = svc.check("paths", values)
    assert set(outcome.problems) == {"play", "export"}
    assert "Play's folder" in outcome.problems["export"]
    assert "never moves Play" in outcome.problems["play"]


def test_a_first_run_chooses_play_while_the_defaults_name_no_folder(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    """The defaults' Play (C:/Games/PrismLauncher/...) isn't there: nothing is Play yet, so the
    first save picks it. Export or Lab is still never the Play entered, and once saved Play is
    fixed (final check round 2)."""
    values = path_values(prism, home, tmp_path / "app")
    chosen = values | {"play": "GTNH-Lab", "lab": "GTNH-Play"}
    assert svc.check("paths", chosen)[0].problems == {}
    clash = values | {"play": "GTNH-Lab", "export": "GTNH-Lab", "lab": "GTNH-Play"}
    assert "export" in svc.check("paths", clash)[0].problems
    svc.save("paths", chosen)
    back = values | {"play": "GTNH-Play", "lab": "GTNH-Lab"}
    assert set(svc.check("paths", back)[0].problems) == {"play", "lab"}


@pytest.fixture
def defaults_at(monkeypatch: pytest.MonkeyPatch, prism: Path) -> Path:
    """The core's default Prism folders are ``prism`` (as C:/Games/PrismLauncher is on Clayton's
    PC), so a gos.toml that doesn't name them still runs with the real Play."""
    monkeypatch.setattr(
        config,
        "Config",
        functools.partial(
            Config, prism_root=prism, prism_instances=prism / "instances"
        ),
    )
    return prism


def _install(home: Path, toml: str | None) -> SetupService:
    """The wizard of a core started with ``toml`` as its gos.toml (None: no gos.toml)."""
    path = home / "config" / "gos.toml"
    if toml is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(toml, "utf-8")
    base = config.load(path) if path.exists() else config.Config(gos_home=home)
    return SetupService(path, base, provider=FakeDevices())


def _try(svc: SetupService, values: dict[str, str], restart: bool) -> bool:
    try:
        svc.save("paths", values)
    except SetupError:
        return False
    if restart:  # the core now runs with what was saved
        svc.base = config.load(svc.path)
    return True


def _assert_play_kept(svc: SetupService, play: Path) -> None:
    cfg = config.load(svc.path) if svc.path.exists() else svc.base
    assert doctor.folded(cfg.instance("play")) == doctor.folded(play)
    for role in ("export", "lab"):
        assert not doctor.within(cfg.instance(role), play), role


OLD_TOML = "[paths]\ngos_home = '{home}'\nprism_root = '{prism}'\ngos_app = '{app}'\n"


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "toml",
    [
        None,  # a first run: the core's default Play is the real folder
        "",  # an empty gos.toml
        # Final check round 2: a gos.toml from before the wizard that names neither Play nor
        # the instances folder, and has no [setup].
        OLD_TOML,
    ],
)
def test_two_saves_never_swap_play_and_lab(
    ring: MemoryKeyring,
    defaults_at: Path,
    home: Path,
    tmp_path: Path,
    toml: str | None,
    restart: bool,
) -> None:
    """Final check round 2 (H1): save 1 moves Play to Lab and Lab elsewhere, save 2 makes Lab the
    old Play, save 3 rotates all three. Each is refused, and gos.toml never has Export or Lab at
    the old Play folder."""
    prism = defaults_at
    app = tmp_path / "app"
    app.mkdir()
    make_instance(prism / "instances", "GTNH-Play2")
    text = (
        None
        if toml is None
        else toml.format(
            home=home.as_posix(), prism=prism.as_posix(), app=app.as_posix()
        )
    )
    svc = _install(home, text)
    play = prism / "instances" / "GTNH-Play"
    values = path_values(prism, home, app)
    for step in (
        {"play": "GTNH-Lab", "lab": "GTNH-Play2"},
        {"play": "GTNH-Lab", "lab": "GTNH-Play"},
        {"play": "GTNH-Export", "export": "GTNH-Lab", "lab": "GTNH-Play"},
        {"play": "GTNH-Lab", "export": "GTNH-Play", "lab": "GTNH-Export"},
    ):
        assert not _try(svc, values | step, restart), step
        assert "play" in svc.check("paths", values | step)[0].problems
    # The paths as they are still save, and Play is the real folder.
    assert _try(svc, values, restart)
    _assert_play_kept(svc, play)


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        ({"lab": "gtnh-play"}, "lab"),  # case: Windows' names ignore it
        ({"export": "GTNH-PLAY"}, "export"),
        ({"lab": "GTNH-Play."}, "lab"),  # Windows drops a trailing dot
        ({"lab": "GTNH-Play.."}, "lab"),
        ({"lab": "GTNH-Play ."}, "lab"),
        ({"lab": "GTNH-Play/"}, "lab"),  # a path, not a name
        ({"lab": "GTNH-Play\\"}, "lab"),
        ({"play": "gtnh-lab"}, "play"),
    ],
)
def test_export_and_lab_never_reach_play_by_spelling(
    ring: MemoryKeyring,
    defaults_at: Path,
    home: Path,
    tmp_path: Path,
    change: dict[str, str],
    refused: str,
) -> None:
    prism = defaults_at
    app = tmp_path / "app"
    app.mkdir()
    svc = _install(
        home,
        OLD_TOML.format(
            home=home.as_posix(), prism=prism.as_posix(), app=app.as_posix()
        ),
    )
    before = svc.path.read_text("utf-8")
    values = path_values(prism, home, app) | change
    assert refused in svc.check("paths", values)[0].problems
    assert not _try(svc, values, restart=True)
    assert svc.path.read_text("utf-8") == before


def test_a_trailing_slash_on_the_instances_folder_is_the_same_play(
    ring: MemoryKeyring, defaults_at: Path, home: Path, tmp_path: Path
) -> None:
    prism = defaults_at
    svc = _install(home, None)
    values = path_values(prism, home, tmp_path / "app")
    slashed = values | {"prism_instances": str(prism / "instances") + os.sep}
    assert svc.check("paths", slashed)[0].problems == {}
    swapped = slashed | {"play": "GTNH-Lab", "lab": "GTNH-Play"}
    assert set(svc.check("paths", swapped)[0].problems) == {"play", "lab"}


def test_a_link_to_play_is_play(
    ring: MemoryKeyring, defaults_at: Path, home: Path, tmp_path: Path
) -> None:
    """Symlinks (junctions on Windows) resolve to the folder they name: Lab through a link to
    Play is Play, and an instances folder whose same names lead elsewhere moves Play."""
    prism = defaults_at
    play = prism / "instances" / "GTNH-Play"
    try:
        link_folder(prism / "instances" / "Lab-Link", play)
    except OSError:
        pytest.skip("this account can't make folder links here")
    svc = _install(home, None)
    values = path_values(prism, home, tmp_path / "app")
    assert "lab" in svc.check("paths", values | {"lab": "Lab-Link"})[0].problems
    # Another instances folder whose GTNH-Play is a link to the real Play: the same Play.
    other = tmp_path / "Other" / "instances"
    other.mkdir(parents=True)
    link_folder(other / "GTNH-Play", play)
    link_folder(other / "GTNH-Lab", play)
    make_instance(other, "GTNH-Export")
    moved = values | {"prism_instances": str(other)}
    problems = svc.check("paths", moved)[0].problems
    assert "play" not in problems and "lab" in problems
    # The same names in a folder of other instances: Play would move.
    third = make_prism(tmp_path / "Third") / "instances"
    elsewhere = values | {"prism_instances": str(third)}
    assert "play" in svc.check("paths", elsewhere)[0].problems
    assert not _try(svc, elsewhere, restart=True)


def _files_under(folder: Path) -> set[Path]:
    return set(folder.rglob("*"))


@pytest.mark.parametrize(
    ("key", "inside"),
    [
        ("gos_home", ".minecraft/GregtecOS"),  # not there yet: its parent is in Play
        ("gos_home", ".minecraft"),  # there: the probe would write in it
        ("gos_app", ""),  # Play's own folder
        ("gos_app", ".minecraft/mods"),
    ],
)
def test_gos_home_and_gos_app_can_never_be_in_play(
    svc: SetupService,
    prism: Path,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    inside: str,
) -> None:
    """Review MEDIUM: a check never writes in Play, and a save never points GOS there."""
    values = path_values(prism, home, tmp_path / "app")
    svc.save("paths", values)
    before = svc.path.read_text("utf-8")
    play = prism / "instances" / "GTNH-Play"
    target = play / inside if inside else play
    files = _files_under(play)
    probed: list[Path] = []
    real = doctor.tempfile.TemporaryFile

    def spy(*args: object, **kwargs: object) -> object:
        probed.append(Path(str(kwargs["dir"])))
        return real(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(doctor.tempfile, "TemporaryFile", spy)
    bad = values | {key: str(target)}
    outcome, _ = svc.check("paths", bad)
    assert set(outcome.problems) == {key}
    assert "overlaps Play" in outcome.problems[key]
    rows = by_id([c.as_dict() for c in outcome.checks])
    assert rows[f"paths.{key}"]["status"] == "error"
    assert not any(doctor.within(p, play) for p in probed)
    assert _files_under(play) == files
    with pytest.raises(SetupError) as err:
        svc.save("paths", bad)
    assert err.value.status == 400 and svc.path.read_text("utf-8") == before
    assert not any(doctor.within(p, play) for p in probed)
    assert _files_under(play) == files


@pytest.mark.parametrize("key", ["gos_home", "gos_app"])
def test_gos_home_and_gos_app_can_never_hold_play(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path, key: str
) -> None:
    values = path_values(prism, home, tmp_path / "app")
    outcome, _ = svc.check("paths", values | {key: str(prism / "instances")})
    assert set(outcome.problems) == {key}


def test_the_doctor_checklist_never_probes_a_gos_home_in_play(
    prism: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gos.toml edited by hand: the checklist row is an error and nothing is written."""
    play = prism / "instances" / "GTNH-Play"
    files = _files_under(play)

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("probed a folder")

    monkeypatch.setattr(doctor.tempfile, "TemporaryFile", refuse)
    cfg = Config(gos_home=play / ".minecraft", prism_instances=prism / "instances")
    rows = by_id([c.as_dict() for c in doctor.checklist(cfg)])
    assert rows["paths.gos_home"]["status"] == "error"
    assert _files_under(play) == files


@pytest.mark.parametrize("key", ["gos_home", "gos_app"])
@pytest.mark.parametrize(
    "inside",
    [
        "GTNH-Lab/.minecraft/mods/GregtecOS",  # not there yet: Lab's mods folder is
        "GTNH-Export/.minecraft/config",  # there: a sync would delete it
        "GregtecOS",  # a folder of its own in the instances folder
    ],
)
def test_gos_home_and_gos_app_stay_out_of_every_instance(
    svc: SetupService,
    prism: Path,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    inside: str,
) -> None:
    """Final check round 2 (lows): GOS_HOME or GOS_APP in Export's or Lab's mirrored folders would
    be deleted by a sync, and the GOS_HOME probe never writes in an instance."""
    instances = prism / "instances"
    probed: list[Path] = []
    real = doctor.tempfile.TemporaryFile

    def spy(*args: object, **kwargs: object) -> object:
        probed.append(Path(str(kwargs["dir"])))
        return real(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(doctor.tempfile, "TemporaryFile", spy)
    files = _files_under(instances)
    values = path_values(prism, home, tmp_path / "app") | {key: str(instances / inside)}
    outcome, _ = svc.check("paths", values)
    row = by_id([c.as_dict() for c in outcome.checks])[f"paths.{key}"]
    assert row["status"] == "error" and "instances folder" in row["detail"]
    assert outcome.blocked
    assert not any(doctor.within(p, instances) for p in probed)
    assert _files_under(instances) == files


def test_a_gt_pin_mismatch_only_warns(
    svc: SetupService,
    prism: Path,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan 0.4: a stale DB is a warning; it never blocks a paths save or Finish (H2)."""
    from gregtec.packupdate import gtpin

    monkeypatch.setattr(gtpin, "check_db", lambda home: (False, "!!! stale"))
    configure(svc, prism, home, tmp_path / "app")
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["db.gt_pin"]["status"] == "warn"
    assert svc.finish()["done"] is True


def test_an_unavailable_keyring_only_warns(
    svc: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> list[str]:
        raise RuntimeError("no backend")

    monkeypatch.setattr(keys, "backend_chain", broken)
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["keyring"]["status"] == "warn"


def test_describe_says_whether_gos_toml_exists(svc: SetupService) -> None:
    """Only a first run (no gos.toml) opens the dashboard on the wizard (H2, D167)."""
    assert svc.describe()["config_exists"] is False
    svc.save("wake", {"wake_phrase": "hey archivist"})
    out = svc.describe()
    assert out["config_exists"] is True and out["done"] is False


def test_the_spoken_answers_output_is_always_a_name(svc: SetupService) -> None:
    """L1: no empty value (it would be dropped and read back as "Chat")."""
    outcome, _ = svc.check("headset", {"input_device": "", "output_endpoint": ""})
    assert outcome.problems == {"output_endpoint": "needed"}


def test_an_ambiguous_microphone_is_an_error(svc: SetupService) -> None:
    """L2: the voice client refuses a name that matches two devices."""
    outcome, _ = svc.check(
        "headset", {"input_device": "Microphone", "output_endpoint": "Chat"}
    )
    assert {c.id: c.status for c in outcome.checks}["voice.input_device"] == "error"
    assert outcome.blocked


def test_a_clash_of_keys_this_step_doesnt_show_is_a_row(svc: SetupService) -> None:
    """L5: confirm and cancel set by hand to the same key aren't pinned on the talk key."""
    svc.path.parent.mkdir(parents=True)
    svc.path.write_text('[voice]\nconfirm_key = "F20"\ncancel_key = "F20"\n', "utf-8")
    outcome, _ = svc.check("talk_key", {"talk_key": "F13", "talk_mode": "hold"})
    assert outcome.problems == {}
    other = {c.id: c for c in outcome.checks}["voice.other"]
    assert other.status == "error" and "confirm and cancel" in other.detail
    outcome, _ = svc.check("talk_key", {"talk_key": "F20", "talk_mode": "hold"})
    assert "talk and" in outcome.problems["talk_key"]


def test_the_talk_key_is_checked_against_plays_bindings(
    ring: MemoryKeyring, prism: Path, home: Path
) -> None:
    """Verdict 5 (QA:V8): Play's options.txt, read once, never written."""
    from gregtec_voice import keys as voice_keys

    svc = SetupService(
        home / "config" / "gos.toml",
        Config(gos_home=home, prism_instances=prism / "instances"),
        provider=FakeDevices(),
    )
    outcome, _ = svc.check("talk_key", {"talk_key": "F13", "talk_mode": "hold"})
    row = {c.id: c for c in outcome.checks}["voice.talk_key_gtnh"]
    assert row.status == "warn" and "no options.txt" in row.detail
    options = prism / "instances" / "GTNH-Play" / ".minecraft" / "options.txt"
    code = voice_keys.parse("F13").lwjgl
    options.write_text(f"key_key.drop:16\nkey_gui.zoom:{code}\n", "utf-8")
    before = options.read_bytes()
    outcome, _ = svc.check("talk_key", {"talk_key": "F13", "talk_mode": "hold"})
    row = {c.id: c for c in outcome.checks}["voice.talk_key_gtnh"]
    assert row.status == "warn" and "gui.zoom" in row.detail
    outcome, _ = svc.check("talk_key", {"talk_key": "F18", "talk_mode": "hold"})
    row = {c.id: c for c in outcome.checks}["voice.talk_key_gtnh"]
    assert row.status == "ok"
    assert options.read_bytes() == before


def test_gos_home_writability_is_tried_not_assumed(tmp_path: Path) -> None:
    """L4: a temp file is made and deleted (os.access ignores Windows ACLs)."""
    assert doctor._writable(tmp_path, [])
    assert list(tmp_path.iterdir()) == []
    assert not doctor._writable(tmp_path / "missing", [])
    # D223: never even tried in or under a guarded folder, nor through a link into one.
    (tmp_path / "Play").mkdir()
    assert not doctor._writable(tmp_path / "Play", [("Play", tmp_path / "Play")])
    link_folder(tmp_path / "link", tmp_path / "Play")
    assert not doctor._writable(tmp_path / "link", [("Play", tmp_path / "Play")])
    assert list((tmp_path / "Play").iterdir()) == []


def test_portaudio_lists_devices_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """M1: a fake sounddevice counts the callers inside PortAudio at once."""
    import sys
    import threading
    import time
    import types

    from gregtec.setup import devices as devices_mod

    state = {"inside": 0, "most": 0, "restarts": 0}
    lock = threading.Lock()

    def enter() -> None:
        with lock:
            state["inside"] += 1
            state["most"] = max(state["most"], state["inside"])
        time.sleep(0.002)
        with lock:
            state["inside"] -= 1

    def terminate() -> None:
        enter()
        with lock:
            state["restarts"] += 1

    def hostapis() -> list[dict[str, object]]:
        enter()
        return [
            {
                "name": "Windows WASAPI",
                "default_input_device": 0,
                "default_output_device": 1,
            }
        ]

    def query() -> list[dict[str, object]]:
        enter()
        return [
            {
                "name": "Mic",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
            {
                "name": "Chat",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
        ]

    fake = types.ModuleType("sounddevice")
    fake._terminate = terminate  # type: ignore[attr-defined]
    fake._initialize = enter  # type: ignore[attr-defined]
    fake.query_hostapis = hostapis  # type: ignore[attr-defined]
    fake.query_devices = query  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sounddevice", fake)

    provider = devices_mod.WasapiDevices()
    results: list[dict[str, object]] = []
    threads = [
        threading.Thread(target=lambda: results.append(listing(provider)))
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["most"] == 1
    assert (
        state["restarts"] == 8
    )  # one restart per listing, inputs and outputs together
    assert results[0] == {
        "provider": "wasapi",
        "inputs": [{"name": "Mic", "default": True}],
        "outputs": [{"name": "Chat", "default": True}],
        "error": None,
    }


# --- final check round 3: finishing guards Export and Lab like a paths save (d), and a check
# never even opens a DB in Play (c) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "name"),
    [
        ("lab", "../instances/GTNH-Play"),  # resolves to Play
        ("export", "..\\instances\\GTNH-Play"),
        ("lab", "GTNH-Play."),  # Windows drops the dot
    ],
)
def test_the_doctor_checklist_refuses_export_or_lab_at_play(
    prism: Path, tmp_path: Path, role: str, name: str
) -> None:
    (tmp_path / "app").mkdir()
    cfg = Config(
        prism_root=prism,
        prism_instances=prism / "instances",
        gos_app=tmp_path / "app",
        gos_home=tmp_path / "home",
        **{role: name},  # type: ignore[arg-type]
    )
    rows = by_id([c.as_dict() for c in doctor.checklist(cfg)])
    assert rows["paths.instances"]["status"] == "error"
    assert "instance.lab" not in rows


def test_the_doctor_checklist_refuses_export_and_lab_as_one_folder(
    prism: Path, tmp_path: Path
) -> None:
    try:
        link_folder(
            prism / "instances" / "LabLink", prism / "instances" / "GTNH-Export"
        )
    except OSError:
        pytest.skip("this account can't make folder links here")
    cfg = Config(prism_root=prism, prism_instances=prism / "instances", lab="LabLink")
    rows = by_id([c.as_dict() for c in doctor.checklist(cfg)])
    assert "same folder" in rows["paths.instances"]["detail"]


def test_finish_refuses_a_lab_that_became_a_link_to_play(
    svc: SetupService, prism: Path, home: Path, tmp_path: Path
) -> None:
    """Final check round 3 (d), gos.toml untouched: Lab is saved as a name with no folder yet,
    then that name becomes a link (a junction on Windows) to Play. Finish says no."""
    configure(svc, prism, home, tmp_path / "app")
    svc.save("paths", path_values(prism, home, tmp_path / "app") | {"lab": "LabLink"})
    try:
        link_folder(prism / "instances" / "LabLink", prism / "instances" / "GTNH-Play")
    except OSError:
        pytest.skip("this account can't make folder links here")
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["paths.instances"]["status"] == "error"
    assert "Play's folder" in rows["paths.instances"]["detail"]
    with pytest.raises(SetupError):
        svc.finish()
    assert "completed_utc" not in svc.path.read_text("utf-8")


def test_finish_guards_the_play_the_running_core_syncs_against(
    ring: MemoryKeyring, defaults_at: Path, home: Path, tmp_path: Path
) -> None:
    """gos.toml hand-edited to a new instances folder whose Lab is a link to the Play the core
    still runs with: the checklist is an error until the core restarts with the new paths."""
    prism = defaults_at
    app = tmp_path / "app"
    app.mkdir()
    svc = _install(home, None)
    configure(svc, prism, home, app)
    other = make_prism(tmp_path / "Other") / "instances"
    shutil.rmtree(other / "GTNH-Lab")
    try:
        link_folder(other / "GTNH-Lab", prism / "instances" / "GTNH-Play")
    except OSError:
        pytest.skip("this account can't make folder links here")
    # As a hand edit would: straight to gos.toml, no paths step.
    svc._write({("paths", "prism_instances"): other.as_posix()})
    assert config.load(svc.path).prism_instances == other
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["paths.instances"]["status"] == "error"
    with pytest.raises(SetupError):
        svc.finish()


def test_a_paths_check_never_opens_a_db_in_play(
    svc: SetupService,
    prism: Path,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final check round 3 (c): Play holds a GOS DB in WAL mode (left by an earlier GOS_HOME
    there). A read-only open would leave -wal and -shm files in Play; a check opens nothing."""
    play = prism / "instances" / "GTNH-Play"
    db = access.db_path(play)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
    finally:
        conn.close()
    files = _files_under(play)

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("opened a DB")

    monkeypatch.setattr(access, "connect", refuse)
    values = path_values(prism, home, tmp_path / "app")
    for target in (play, prism / "instances" / "GTNH-Lab"):
        outcome, _ = svc.check("paths", values | {"gos_home": str(target)})
        assert "gos_home" in outcome.problems or any(
            c.status == "error" for c in outcome.checks
        )
        rows = by_id([c.as_dict() for c in outcome.checks])
        assert rows["db.gt_pin"]["detail"].startswith("not read")
    assert _files_under(play) == files


# --- the merged OC bridge and TTS work (WO-7.1, WO-7.3, WO-5.3) ---------------------------------

MERGED_TOML = """\
[voice]
tts = "piper"                 # chosen by hand (D203)
tts_device = "gpu"
tts_voice = ""                # the engine's default
output_endpoint = "Chat"

[mailbox.lab]                 # M1 (WO-7.3)
world = "Lab World"
drive = "1d9f2c3a-6b1e-4c1b-9a7e-2f3c4d5e6f70"
dir = "home/gos/mailbox"
"""


def test_saves_keep_the_mailbox_and_tts_settings(
    home: Path, ring: MemoryKeyring, prism: Path, tmp_path: Path
) -> None:
    svc = _install(home, MERGED_TOML)
    app = tmp_path / "app"
    app.mkdir()
    svc.save("paths", path_values(prism, home, app))
    svc.save("headset", {"input_device": "Arctis", "output_endpoint": "Elite Chat"})
    svc.save("talk_key", {"talk_key": "F13", "talk_mode": "hold"})
    text = svc.path.read_text("utf-8")
    for line in MERGED_TOML.splitlines():
        if not line.startswith("output_endpoint"):
            assert line in text, line
    cfg = config.load(svc.path)
    assert [(m.role, m.world, m.drive) for m in cfg.mailboxes] == [
        ("lab", "Lab World", "1d9f2c3a-6b1e-4c1b-9a7e-2f3c4d5e6f70")
    ]
    voice_table = tomllib.loads(text)["voice"]
    assert voice_table["tts"] == "piper" and voice_table["tts_device"] == "gpu"
    assert voice.voice_problem(voice_table) is None


def test_every_key_gos_key_manages_has_a_wizard_step(svc: SetupService) -> None:
    named = {f.secret for s in registry.steps() for f in s.fields if f.secret}
    assert named == set(keys.NAMES)


def test_elevenlabs_key_is_stored_and_needed_only_for_the_cloud_voice(
    svc: SetupService, ring: MemoryKeyring
) -> None:
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["secret.elevenlabs"]["status"] == "ok"
    svc.path.parent.mkdir(parents=True, exist_ok=True)
    svc.path.write_text('[voice]\ntts = "elevenlabs"\n', "utf-8")
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["secret.elevenlabs"]["status"] == "warn"
    svc.store_secret("elevenlabs", "sk_" + "a" * 48)
    assert keys.get_key(keys.ELEVENLABS) == "sk_" + "a" * 48
    rows = by_id([c.as_dict() for c in svc.checklist()])
    assert rows["secret.elevenlabs"]["status"] == "ok"
