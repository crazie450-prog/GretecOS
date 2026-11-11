"""WO-5.1: gos.toml's [voice] keys (Appendix C; D180, D181)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gregtec_voice import settings
from gregtec_voice.settings import SettingsError, VoiceSettings


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "gos.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_follow_appendix_c(tmp_path: Path) -> None:
    cfg, voice = settings.load(
        write(tmp_path, f'[paths]\ngos_home = "{tmp_path.as_posix()}"\n')
    )
    assert cfg.gos_home == tmp_path
    assert voice == VoiceSettings(gos_home=tmp_path)  # the engines' models and glossary
    assert (voice.stt_engine, voice.stt_model) == ("auto", "large-v3-turbo")  # D182
    assert (voice.primary_trigger, voice.backup_trigger, voice.talk_mode) == (
        "wake",
        "key",
        "hold",
    )
    assert [k.label for k in voice.all_keys().values()] == ["F13", "F14", "F15", "F16"]
    assert voice.key_trigger and voice.wake_trigger
    assert voice.input_device == ""


def test_voice_keys_are_read(tmp_path: Path) -> None:
    _, voice = settings.load(
        write(
            tmp_path,
            """
[voice]
primary_trigger = "key"
backup_trigger = "none"
talk_key = "Scroll Lock"
talk_mode = "toggle"
confirm_key = "F20"
cancel_key = "F21"
next_card_key = "Pause"
input_device = " Arctis Nova Elite "
wake_phrase = " Okay,  Tessera "
wake_engine = "openwakeword"
wake_model = " C:/GOS/models/okay_tessera.onnx "
wake_threshold = 0.6
stt_engine = "parakeet"
stt_model = " distil-large-v3 "
""",
        )
    )
    assert (voice.stt_engine, voice.stt_model) == ("parakeet", "distil-large-v3")
    assert voice.talk_key.name == "scroll_lock" and voice.talk_mode == "toggle"
    assert [k.name for k in voice.hotkeys().values()] == ["f20", "f21", "pause"]
    assert voice.key_trigger and not voice.wake_trigger
    assert voice.input_device == "Arctis Nova Elite"
    assert (voice.wake_phrase, voice.wake_engine, voice.wake_threshold) == (
        "okay tessera",
        "openwakeword",
        0.6,
    )
    assert voice.wake_model == "C:/GOS/models/okay_tessera.onnx"


def test_wake_defaults(tmp_path: Path) -> None:
    """D185, D186: sherpa-onnx with PM08's provisional phrase and its tuned threshold."""
    _, voice = settings.load(write(tmp_path, "[voice]\n"))
    assert (voice.wake_engine, voice.wake_phrase) == ("sherpa-onnx", "hey archivist")
    assert (voice.wake_model, voice.wake_threshold) == ("", 0.0)


@pytest.mark.parametrize(
    ("toml", "problem"),
    [
        ('primary_trigger = "voice"', "primary_trigger must be one of wake, key"),
        ('backup_trigger = "wake"', "backup_trigger must differ from primary_trigger"),
        ('talk_mode = "push"', "talk_mode must be one of hold, toggle"),
        ('talk_key = "mouse5"', "talk_key: 'mouse5': it is a mouse button"),
        ('talk_key = "TBD (unbound in GTNH and Discord)"', "talk_key: 'TBD"),
        ("talk_key = 13", 'talk_key must be a key name, e.g. "F13"'),
        (
            'confirm_key = "F13"',
            "talk and confirm are both F13: each needs its own key",
        ),
        ("input_device = 3", "input_device must be a string"),
        (
            'stt_engine = "whisper"',
            "stt_engine must be one of auto, whisper-gpu, whisper-cpu, parakeet",
        ),
        ('stt_model = "../models/x"', "stt_model must be a Whisper model name"),
        ('stt_model = "C:/models/turbo"', "stt_model must be a Whisper model name"),
        ("stt_model = 3", "stt_model must be a Whisper model name"),
        ('wake_phrase = "Hey GregTech"', 'must not contain "GregTech"'),
        ('wake_phrase = "okay gregtec"', 'must not contain "GregTech"'),
        ('wake_phrase = "greg"', 'must not contain "GregTech"'),
        ('wake_phrase = "TBD (PM08)"', "must be 1 to 4 plain words"),
        ('wake_phrase = "hey you there a b"', "must be 1 to 4 plain words"),
        ('wake_phrase = "hi you"', "is too short"),
        ("wake_phrase = 3", "wake_phrase must be a phrase in quotes"),
        ('wake_engine = "porcupine"', "wake_engine must be one of sherpa-onnx"),
        ('wake_engine = "openwakeword"', "wake_model must name the custom"),
        ("wake_model = 3", "wake_model must be a path in quotes"),
        ("wake_threshold = 1.5", "wake_threshold must be 0"),
        ("wake_threshold = 0.01", "wake_threshold must be 0"),
        ("wake_threshold = true", "wake_threshold must be 0"),
    ],
)
def test_bad_voice_keys_are_refused(tmp_path: Path, toml: str, problem: str) -> None:
    with pytest.raises(SettingsError, match=problem.replace("(", r"\(")):
        settings.load(write(tmp_path, f"[voice]\n{toml}\n"))


def test_every_problem_is_listed_at_once(tmp_path: Path) -> None:
    with pytest.raises(SettingsError) as raised:
        settings.load(write(tmp_path, '[voice]\ntalk_mode = "x"\ncancel_key = "a"\n'))
    assert "talk_mode" in str(raised.value) and "cancel_key" in str(raised.value)


def test_voice_must_be_a_table(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"\[voice\] must be a table"):
        settings.load(write(tmp_path, 'voice = "on"\n'))


def test_no_config_file_means_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOS_HOME", str(tmp_path / "nowhere"))
    cfg, voice = settings.load(None)
    assert cfg.gos_home == tmp_path / "nowhere" and voice == VoiceSettings()
    assert voice.gos_home == tmp_path / "nowhere"


@pytest.mark.parametrize(
    "model", ["large-v3-turbo", "small.en", "Systran/faster-whisper-small.en"]
)
def test_whisper_model_names_and_hub_ids_are_taken(tmp_path: Path, model: str) -> None:
    _, voice = settings.load(write(tmp_path, f'[voice]\nstt_model = "{model}"\n'))
    assert voice.stt_model == model


def test_the_plans_appendix_c_skeleton_loads(tmp_path: Path) -> None:
    """QA W4: the gos.toml skeleton in the plan, copied as it is, starts the voice client and
    names D180's keys."""
    plan = Path(__file__).parents[3] / "docs" / "implementation_plan.md"
    text = plan.read_text(encoding="utf-8").split("**`gos.toml` skeleton:**", 1)[1]
    block = text.split("```toml\n", 1)[1].split("```", 1)[0]
    _, voice = settings.load(write(tmp_path, block))
    assert [k.label for k in (voice.talk_key, voice.confirm_key)] == ["F13", "F14"]
    assert [k.label for k in (voice.cancel_key, voice.next_card_key)] == ["F15", "F16"]

