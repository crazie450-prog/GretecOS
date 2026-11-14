"""WO-9.1, D296: ``[discord]`` in gos.toml, the bot's secrets (names only), ``gos discord
check|run`` without a setup, and the supervisor's spec for the bot."""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest

from gregtec import applog, cli, supervisor
from gregtec.discordbot import cli as discord_cli
from gregtec.discordbot import settings
from gregtec.discordbot.settings import BotSettings, SettingsError
from gregtec.llm import keys

OWNER = 111_111_111_111_111_111


def toml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "gos.toml"
    path.write_text(
        f'[paths]\ngos_home = "{tmp_path.as_posix()}"\n{body}', encoding="utf-8"
    )
    return path


def test_owner_id_is_required_and_must_be_a_snowflake(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="owner_id is missing"):
        settings.load(toml(tmp_path, ""))
    with pytest.raises(SettingsError, match="owner_id is missing"):
        settings.load(toml(tmp_path, "[discord]\nowner_id = 0\n"))
    for bad in ('"clayton"', "12345", "true", "-5"):
        with pytest.raises(SettingsError, match="must be a Discord id"):
            settings.load(toml(tmp_path, f"[discord]\nowner_id = {bad}\n"))
    _, s = settings.load(toml(tmp_path, f'[discord]\nowner_id = "{OWNER}"\n'))
    assert s == BotSettings(owner_id=OWNER)
    _, s = settings.load(
        toml(tmp_path, f"[discord]\nowner_id = {OWNER}\nguild_id = {OWNER + 1}\n")
    )
    assert s.guild_id == OWNER + 1


def test_unknown_keys_are_refused(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="unknown keys token"):
        settings.load(toml(tmp_path, f'[discord]\nowner_id = {OWNER}\ntoken = "x"\n'))


def test_the_phone_link_comes_from_the_tailnet_name(tmp_path: Path) -> None:
    core = '[core]\nallowed_hosts = ["pc.tail1.ts.net"]\n'
    _, s = settings.load(toml(tmp_path, f"{core}[discord]\nowner_id = {OWNER}\n"))
    assert s.dashboard_url == "https://pc.tail1.ts.net"
    assert (
        s.plan_link("pl-0a1b2c3d") == "https://pc.tail1.ts.net/#/designer/pl-0a1b2c3d"
    )
    assert s.plan_link("pw-0a1b2c3d") == "https://pc.tail1.ts.net/#/power/pw-0a1b2c3d"
    _, s = settings.load(
        toml(
            tmp_path,
            f'{core}[discord]\nowner_id = {OWNER}\ndashboard_url = "https://pc.tail1.ts.net/"\n',
        )
    )
    assert s.dashboard_url == "https://pc.tail1.ts.net"
    _, none = settings.load(toml(tmp_path, f"[discord]\nowner_id = {OWNER}\n"))
    assert none.dashboard_url is None and none.plan_link("pl-0a1b2c3d") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://pc.tail1.ts.net",  # never plain http
        "https://evil.example.com",  # not a host the core accepts
        "https://pc.tail1.ts.net/phish",
        "https://",
    ],
)
def test_a_dashboard_url_is_https_on_an_allowed_host(tmp_path: Path, url: str) -> None:
    body = f'[core]\nallowed_hosts = ["pc.tail1.ts.net"]\n[discord]\nowner_id = {OWNER}\ndashboard_url = "{url}"\n'
    with pytest.raises(SettingsError, match="dashboard_url"):
        settings.load(toml(tmp_path, body))


def ns(path: Path, command: str = "check") -> argparse.Namespace:
    return argparse.Namespace(config=path, discord_command=command, supervised=True)


def test_check_names_missing_secrets_without_their_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: set[str] = set()
    monkeypatch.setattr(keys, "has_key", lambda name: name in stored)
    path = toml(tmp_path, f"[discord]\nowner_id = {OWNER}\n")
    out = io.StringIO()
    assert discord_cli.run(ns(path), out) == 1
    text = out.getvalue()
    assert "problem: no Discord bot token: gos key set discord" in text
    assert "problem: no core token for the bot: gos token new discord" in text
    assert str(OWNER) not in text  # ids aren't printed
    stored |= {keys.DISCORD, settings.CORE_TOKEN_KEY}
    out = io.StringIO()
    assert discord_cli.run(ns(path), out) == 0 and "problem" not in out.getvalue()


def test_run_exits_with_the_setup_code_until_it_is_set_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The supervisor leaves the bot stopped on exit 2 (no owner, no secrets)."""
    monkeypatch.setattr(keys, "has_key", lambda name: False)
    logs: list[str] = []  # applog.setup would rewire the test run's logging
    monkeypatch.setattr(applog, "setup", lambda process, logs_dir: logs.append(process))

    def never(*a: object, **kw: object) -> int:
        raise AssertionError("the bot started without its setup")

    monkeypatch.setattr(discord_cli, "_run_bot", never)
    out = io.StringIO()
    assert discord_cli.run(ns(toml(tmp_path, ""), "run"), out) == discord_cli.EXIT_SETUP
    assert "owner_id is missing" in out.getvalue()
    path = toml(tmp_path, f"[discord]\nowner_id = {OWNER}\n")
    assert discord_cli.run(ns(path, "run"), io.StringIO()) == discord_cli.EXIT_SETUP
    assert discord_cli.EXIT_SETUP == supervisor.EXIT_SETUP
    assert logs == ["discord", "discord"]  # both refusals go to logs/discord.log


def test_gos_discord_is_wired_into_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(keys, "has_key", lambda name: True)
    path = toml(tmp_path, f"[discord]\nowner_id = {OWNER}\n")
    assert cli.main(["discord", "check", "--config", str(path)]) == 0
    assert "owner_id: set" in capsys.readouterr().out


def test_the_supervisor_runs_the_bot_from_login(tmp_path: Path) -> None:
    spec = supervisor.discord_spec(tmp_path / "gos.toml")
    assert spec.name == "discord" and not spec.with_play  # at login with the core (D77)
    assert spec.argv[1:5] == ("-m", "gregtec.discordbot", "run", "--supervised")
    assert spec.argv[-2:] == ("--config", str(tmp_path / "gos.toml"))
    assert "owner_id" in dict(spec.stay_stopped)[supervisor.EXIT_SETUP]
    assert supervisor.discord_spec(None).argv[-1] == "--supervised"
