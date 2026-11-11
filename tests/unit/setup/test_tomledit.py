"""WO-2.9: gos.toml edits keep everything else, read back exactly, and replace the file atomically."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from gregtec import config
from gregtec.setup import tomledit
from gregtec.setup.tomledit import TomlEditError, format_value, set_values, write_atomic

APPENDIX_C = """\
# Clayton's notes stay
[paths]
gos_app = "C:/GregtecOS-app"                 # release install (D75)
prism_instances = "C:/Games/PrismLauncher/instances"
play = "GTNH-Play"

[core]
port = 8765

[voice]
talk_key = "TBD (unbound in GTNH and Discord)"
talk_mode = "hold"            # hold | toggle
"""


def test_a_key_is_replaced_and_its_comment_and_neighbours_kept() -> None:
    out = set_values(APPENDIX_C, {("paths", "gos_app"): "D:/GOS-app"})
    assert 'gos_app = "D:/GOS-app"                 # release install (D75)' in out
    assert "# Clayton's notes stay" in out and "port = 8765" in out
    data = tomllib.loads(out)
    assert data["paths"]["play"] == "GTNH-Play" and data["core"]["port"] == 8765


def test_a_hash_inside_a_string_is_not_a_comment() -> None:
    text = '[voice]\nwake_phrase = "hey # there" # mine\n'
    out = set_values(text, {("voice", "wake_phrase"): "hey archivist"})
    assert out == '[voice]\nwake_phrase = "hey archivist" # mine\n'


def test_missing_keys_go_after_the_tables_last_key_and_tables_at_the_end() -> None:
    out = set_values(
        APPENDIX_C,
        {
            ("paths", "lab"): "GTNH-Lab",
            ("setup", "completed_utc"): "2026-11-11T10:00:00+00:00",
        },
    )
    lines = out.splitlines()
    assert lines.index('lab = "GTNH-Lab"') == lines.index('play = "GTNH-Play"') + 1
    assert out.endswith('\n[setup]\ncompleted_utc = "2026-11-11T10:00:00+00:00"\n')


def test_none_removes_a_key() -> None:
    out = set_values(APPENDIX_C, {("voice", "talk_mode"): None})
    assert "talk_mode" not in out and "talk_key" in out


def test_an_empty_file_gets_the_tables() -> None:
    out = set_values("", {("paths", "play"): "Play"})
    assert out == '[paths]\nplay = "Play"\n'


def test_windows_paths_and_quotes_round_trip() -> None:
    value = 'C:\\Games\\Prism "portable"\\instances'
    out = set_values("", {("paths", "prism_instances"): value})
    assert tomllib.loads(out)["paths"]["prism_instances"] == value


@pytest.mark.parametrize(
    "text",
    [
        'paths.play = "x"\n',  # a dotted key: the [paths] edit wouldn't read back
        'paths = { play = "x" }\n',
        '[paths]\nplay = """\nx\n"""\n',  # over several lines
        "[paths]\nplay = 'x'\n[paths]\nlab = 'y'\n",  # not valid TOML at all
        "[paths\n",
    ],
)
def test_edits_that_would_not_read_back_are_refused(text: str) -> None:
    with pytest.raises(TomlEditError):
        set_values(text, {("paths", "play"): "GTNH-Play"})


def test_format_value() -> None:
    assert format_value(True) == "true" and format_value(7) == "7"
    assert format_value(["a", 'b"']) == '["a", "b\\""]'
    assert format_value("tab\there\x01") == '"tab\\there\\u0001"'
    with pytest.raises(TomlEditError):
        format_value(1.5)  # type: ignore[arg-type]


def test_write_atomic_keeps_a_backup_and_verifies_first(tmp_path: Path) -> None:
    path = tmp_path / "config" / "gos.toml"
    write_atomic(path, "[paths]\nplay = 'A'\n")
    assert not (tmp_path / "config" / "gos.toml.bak").exists()
    write_atomic(path, "[paths]\nplay = 'B'\n", verify=config.load)
    assert config.load(path).play == "B"
    assert "'A'" in path.with_name("gos.toml.bak").read_text("utf-8")

    def refuse(tmp: Path) -> None:
        raise ValueError("no")

    with pytest.raises(ValueError):
        write_atomic(path, "[paths]\nplay = 'C'\n", verify=refuse)
    assert config.load(path).play == "B"  # unchanged
    assert sorted(p.name for p in path.parent.iterdir()) == ["gos.toml", "gos.toml.bak"]


def test_write_atomic_retries_a_busy_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "gos.toml"
    calls = []
    real = tomledit.os.replace

    def busy(src: str, dst: str) -> None:
        calls.append(src)
        if len(calls) < 2:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(tomledit.os, "replace", busy)
    monkeypatch.setattr(tomledit.time, "sleep", lambda s: None)
    write_atomic(path, "[a]\nb = 1\n")
    assert len(calls) == 2 and path.read_text("utf-8") == "[a]\nb = 1\n"
