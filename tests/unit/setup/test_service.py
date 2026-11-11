"""WO-2.9: the wizard's steps, their checks, gos.toml writes that config.py reads back, secrets in
the keyring (never echoed), the key's test call, the device list and finishing setup."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import anthropic
import httpx2
import pytest

from gregtec import config, doctor
from gregtec.agents.no_ai import AUTH, CHOICE, AiMode
from gregtec.config import Config
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
    """No gos.toml yet: the core runs with its defaults, so their Play is guarded too."""
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
    assert set(outcome.problems) == {"export"}  # Play itself isn't saved yet
    assert "Play's folder" in outcome.problems["export"]


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
    assert doctor._writable(tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert not doctor._writable(tmp_path / "missing")


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
