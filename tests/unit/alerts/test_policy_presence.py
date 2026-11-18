"""WO-9.2: ``[notify]`` (D298), quiet hours, the routing decision's reasons, and input idle
(:mod:`gregtec.presence`, D76) with its Windows source behind an interface."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

from gregtec import config, presence
from gregtec.alerts import policy as pol


def test_notify_defaults_pending_clayton() -> None:
    p = pol.parse({})
    assert p == pol.NotifyPolicy()
    assert (p.quiet_hours, p.quiet_min_level, p.discord_min_level) == (
        None,
        "err",
        "warn",
    )
    assert (p.cooldown_s, p.offline_after_s) == (1800, 60)


@pytest.mark.parametrize(
    "table",
    [
        {"quiet_hours": "23-07"},
        {"quiet_hours": "25:00-07:00"},
        {"quiet_hours": "07:00-07:00"},
        {"quiet_hours": 7},
        {"quiet_min_level": "loud"},
        {"discord_min_level": None},
        {"cooldown_s": -1},
        {"cooldown_s": True},
        {"offline_after_s": 5},
        {"quiet": "23:00-07:00"},
    ],
)
def test_bad_notify_values_are_refused(table: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"\[notify\]"):
        pol.parse(table)


def test_a_bad_notify_is_a_service_error_that_keeps_the_defaults(
    tmp_path: Path,
) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text('[notify]\nquiet_hours = "late"\n', encoding="utf-8")
    cfg = config.load(toml)
    assert cfg.notify == pol.NotifyPolicy()
    assert any("[notify]" in e for e in cfg.service_errors)
    toml.write_text('[notify]\nquiet_hours = "22:30-06:15"\n', encoding="utf-8")
    assert config.load(toml).notify.quiet_hours == (22 * 60 + 30, 6 * 60 + 15)


@pytest.mark.parametrize(
    ("hours", "hhmm", "inside"),
    [
        ("23:00-07:00", "23:00", True),
        ("23:00-07:00", "02:30", True),
        ("23:00-07:00", "06:59", True),
        ("23:00-07:00", "07:00", False),
        ("23:00-07:00", "22:59", False),
        ("01:00-05:00", "03:00", True),
        ("01:00-05:00", "05:00", False),
        ("01:00-05:00", "00:59", False),
    ],
)
def test_quiet_hours_wrap_midnight(hours: str, hhmm: str, inside: bool) -> None:
    p = pol.parse({"quiet_hours": hours})
    h, m = (int(x) for x in hhmm.split(":"))
    assert p.in_quiet_hours(datetime(2033, 5, 18, h, m)) is inside


def test_no_quiet_hours_by_default() -> None:
    assert pol.NotifyPolicy().in_quiet_hours(datetime(2033, 5, 18, 3, 0)) is False


def test_decide_says_why_not() -> None:
    p = pol.NotifyPolicy()
    here = pol.Context(away=False, quiet_hours=False)
    gone = pol.Context(away=True, quiet_hours=False)
    assert pol.decide("dashboard", "power_low", "err", gone, p) == pol.SEND
    assert pol.decide("discord", "machine_stalled", "warn", here, p) == "present"
    assert pol.decide("discord", "crafting_done", "info", gone, p) == (
        "below discord_min_level"
    )
    assert pol.decide("discord", "machine_stalled", "warn", gone, p, {"voice"}) == (
        "already said"
    )
    assert pol.decide("voice", "machine_stalled", "warn", here, p, {"discord"}) == (
        "already DMed"
    )
    assert pol.decide("voice", "credits", "err", here, p) == "not a voice alert"
    assert pol.decide("in_game", "game_paused", "warn", here, p) == (
        "not an in-game alert"
    )
    off = pol.Context(away=True, quiet_hours=False, discord_mode="off")
    assert pol.decide("discord", "credits", "err", off, p) == "/alerts off"
    nobody = pol.Context(away=False, quiet_hours=False, voice_up=False, game_up=False)
    assert pol.decide("voice", "power_low", "err", nobody, p) == "no voice client"
    assert pol.decide("in_game", "power_low", "err", nobody, p) == "no game connected"
    with pytest.raises(ValueError):
        pol.decide("email", "power_low", "err", here, p)


def test_every_kind_has_a_group() -> None:
    assert {pol.group_of(k) for k in pol.KINDS} == {"base", "direct", "away"}
    assert pol.group_of("gos_offline") == "away"
    assert pol.group_of("credits") == "direct"


def test_credit_buttons_name_the_cycle_and_follow_the_banner() -> None:
    state = {
        "cycle_start": "2033-05-01T00:00:00",
        "actions": ["billing", "resume", "stop"],
    }
    got = pol.credit_buttons(state)
    assert [b["custom_id"] for b in got] == [
        "gos:credit:billing:2033-05-01",
        "gos:credit:resume:2033-05-01",
        "gos:credit:stop:2033-05-01",
    ]
    assert [b["danger"] for b in got] == [False, False, True]
    assert pol.credit_buttons({"actions": []}) == ()


# --- input idle (D76)


def test_away_is_ten_minutes_without_input() -> None:
    assert presence.AWAY_S == 600.0
    assert presence.is_away(599.9) is False
    assert presence.is_away(600.0) is True
    assert presence.is_away(None) is False  # unknown is present: no away-only DM


def test_tick_counts_wrap_every_49_days() -> None:
    assert presence.idle_ms(5_000, 2_000) == 3_000
    assert presence.idle_ms(1_000, 2**32 - 4_000) == 5_000


def test_fake_idle_on_a_fake_clock() -> None:
    t = [100.0]
    idle = presence.FakeIdle(lambda: t[0])
    t[0] = 700.0
    assert idle.idle_s() == 600.0
    idle.touch()
    assert idle.idle_s() == 0.0
    idle.known = False
    assert idle.idle_s() is None


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux fake")
def test_off_windows_presence_is_unknown() -> None:
    src = presence.system_idle()
    assert isinstance(src, presence.NoIdle)
    assert src.idle_s() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' GetLastInputInfo")
def test_on_windows_get_last_input_info_answers() -> None:
    src = presence.system_idle()
    assert isinstance(src, presence.WindowsIdle)
    idle = src.idle_s()
    assert idle is None or idle >= 0.0  # a runner's service session may have no input


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' GetLastInputInfo")
def test_windows_idle_leaves_the_schedulers_input_check_working() -> None:
    # Regression: WindowsIdle used to set argtypes on the process-wide
    # ctypes.windll.user32.GetLastInputInfo, after which the vault scheduler's own call
    # (its own LastInputInfo) raised ctypes.ArgumentError and maintenance never ran.
    from gregtec.vault import scheduler

    first, second = presence.WindowsIdle(), presence.system_idle()
    for got in (scheduler.seconds_since_input(), first.idle_s(), second.idle_s()):
        assert got is None or got >= 0.0
    after = scheduler.seconds_since_input()
    assert after is None or after >= 0.0


class _Fn:
    def __init__(self, result: int) -> None:
        self.result = result
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> int:
        return self.result


class _Dll:
    def __init__(self) -> None:
        self.GetLastInputInfo = _Fn(1)
        self.GetTickCount = _Fn(5000)


def test_windows_idle_never_touches_the_shared_windll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same regression, on every OS: WindowsIdle loads its own DLL handles and never sets
    # argtypes or restype on ctypes.windll's process-wide function objects.
    import ctypes

    shared = type("WinDll", (), {"user32": _Dll(), "kernel32": _Dll()})()
    private: list[_Dll] = []

    def win_dll(name: str) -> _Dll:
        private.append(_Dll())
        return private[-1]

    monkeypatch.setattr(ctypes, "windll", shared, raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    src = presence.WindowsIdle()
    assert len(private) == 2
    for dll in (shared.user32, shared.kernel32):
        assert (
            dll.GetLastInputInfo.argtypes is None
            and dll.GetLastInputInfo.restype is None
        )
        assert dll.GetTickCount.restype is None
    assert src.idle_s() == 5.0  # dwTime 0 against tick 5000 ms
