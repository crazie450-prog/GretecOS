"""The bot's settings: ``[discord]`` in gos.toml and its two secrets (WO-9.1, D296).

| key                       | default                                  | what                          |
|---------------------------|------------------------------------------|-------------------------------|
| [discord] owner_id        | none: the bot won't start                | Clayton's Discord user id (the only one it answers) |
| [discord] guild_id        | none: commands sync globally (up to an hour) | his private server's id: commands appear there at once |
| [discord] dashboard_url   | ``https://`` + the first ``*.ts.net`` name in ``[core] allowed_hosts`` | the phone dashboard's address for plan links (D53) |

Ids are Discord's 17-20 digit snowflakes, read only from gos.toml, never from the repo. The bot
token is ``gregtec-os/discord`` in the keyring (the setup wizard or ``gos key set discord``),
and the core token ``gregtec-os/token-discord`` (``gos token new discord``, D117). Neither is
ever logged or written anywhere else.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from gregtec import config
from gregtec.config import Config

CORE_CLIENT = "discord"  # its client token's name in the core (D117, D165, D206)
CORE_TOKEN_KEY = f"token-{CORE_CLIENT}"
SNOWFLAKE_MIN, SNOWFLAKE_MAX = 10**15, 2**63 - 1


class SettingsError(ValueError):
    """``[discord]`` is missing or wrong; the message says how to fix it."""


@dataclass(frozen=True)
class BotSettings:
    owner_id: int  # Clayton's user id: every command and interaction from anyone else is ignored
    guild_id: int | None = None
    dashboard_url: str | None = None  # https://<pc>.<tailnet>.ts.net, no trailing /

    def plan_link(self, plan_id: str) -> str | None:
        """The plan in the phone dashboard's Designer (D250) or Power view (D243)."""
        if self.dashboard_url is None:
            return None
        view = "power" if plan_id.startswith("pw-") else "designer"
        return f"{self.dashboard_url}/#/{view}/{plan_id}"


def _snowflake(table: Mapping[str, Any], key: str, *, required: bool) -> int | None:
    value = table.get(key)
    if value is None or value == 0:
        if required:
            raise SettingsError(
                f"gos.toml: [discord] {key} is missing: your Discord user id "
                "(Discord > Settings > Advanced > Developer Mode, then right-click your "
                "name > Copy User ID)"
            )
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not SNOWFLAKE_MIN <= value <= SNOWFLAKE_MAX
    ):
        raise SettingsError(f"gos.toml: [discord] {key} must be a Discord id (digits)")
    return value


def _dashboard_url(table: Mapping[str, Any], cfg: Config) -> str | None:
    value = table.get("dashboard_url")
    if value is None:
        for host in cfg.extra_allowed_hosts:
            name, _, port = host.partition(":")
            if name.endswith(".ts.net") and port in ("", "443"):
                return f"https://{name}"
        return None
    if not isinstance(value, str):
        raise SettingsError("gos.toml: [discord] dashboard_url must be text")
    parts = urlsplit(value.strip())
    # The phone reaches the dashboard only through Tailscale Serve (D53, PM06): https, and a
    # host the core accepts, so a link never points anywhere else.
    if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/"):
        raise SettingsError(
            "gos.toml: [discord] dashboard_url must be https://<pc>.<tailnet>.ts.net"
        )
    allowed = {h.lower() for h in cfg.allowed_hosts} | {
        h.lower().partition(":")[0] for h in cfg.allowed_hosts if h.endswith(":443")
    }
    if parts.netloc.lower() not in allowed:
        raise SettingsError(
            "gos.toml: [discord] dashboard_url's host must be in [core] allowed_hosts"
        )
    return f"https://{parts.netloc}"


def load(path: Path | None = None) -> tuple[Config, BotSettings]:
    """gos.toml's Config and ``[discord]``; :class:`SettingsError` (or the Config's own
    ``ValueError``) says what is wrong."""
    cfg = config.load(path)
    file = path if path is not None else config.default_path()
    data: dict[str, Any] = {}
    if file.is_file():
        with open(file, "rb") as f:
            data = tomllib.load(f)
    table = data.get("discord", {})
    if not isinstance(table, dict):
        raise SettingsError("gos.toml: [discord] must be a table")
    unknown = sorted(set(table) - {"owner_id", "guild_id", "dashboard_url"})
    if unknown:
        raise SettingsError(
            f"gos.toml: [discord] has unknown keys {', '.join(unknown)}"
        )
    owner = _snowflake(table, "owner_id", required=True)
    assert owner is not None
    return cfg, BotSettings(
        owner_id=owner,
        guild_id=_snowflake(table, "guild_id", required=False),
        dashboard_url=_dashboard_url(table, cfg),
    )
