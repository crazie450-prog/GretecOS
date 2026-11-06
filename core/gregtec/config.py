"""``gos.toml`` (plan section 4.3, Appendix C). Only the keys the existing commands read so far.

| key                      | default                                  | read by                     |
|--------------------------|------------------------------------------|-----------------------------|
| [paths] gos_app          | C:/GregtecOS-app                         | deploy (WO-0.8), sync (D98) |
| [paths] prism_instances  | C:/Games/PrismLauncher/instances         | sync (WO-0.3)               |
| [paths] play, export, lab| GTNH-Play, GTNH-Export, GTNH-Lab         | sync                        |
| [sync.export] exclude    | the NEI jar and GregtecLink (plan 5.1)   | sync                        |
| [sync.lab] exclude       | nothing                                  | sync                        |
| [paths] gos_home         | $GOS_HOME, else ~/GregtecOS              | core, supervisor (WO-2.1)   |
| [paths] prism_root       | C:/Games/PrismLauncher                   | gos launch (WO-2.1)         |
| [core] host              | 127.0.0.1 (the only value allowed)       | core (WO-2.1, plan 4.6)     |
| [core] port              | 8765                                     | core, clients               |
| [core] supervisor_port   | 8766                                     | supervisor, gos stop/launch |
| [core] allowed_hosts     | 127.0.0.1:<port>, localhost:<port>       | Host/Origin checks (QA:A8)  |
| [startup] core_at_login  | true                                     | supervisor --login (D77)    |
| [budget] monthly_included_usd | 100                                 | llm_usage meter (D47)       |
| [budget] credit_alerts_pct    | [80, 100]                           | llm_usage meter (D48)       |
| [budget] cycle_day       | 1 (credits reset on this day each month) | llm_usage meter (D118)      |
| [launch] home            | gos_home                                 | gos launch (D115)           |

``play``, ``export`` and ``lab`` are instance folder names inside ``prism_instances``. An
``exclude`` list adds to its default, so the NEI and GregtecLink patterns can't be dropped by
accident (Clayton, 2026-11-03). Each pattern is posix, relative to the game folder and starts in a mirrored
folder (``config/lab-only.cfg``); one that could never match is an error, since sync would then
delete the file it was meant to keep. ``allowed_hosts`` likewise adds to its default. Every other
key in the Appendix C skeleton is ignored until its WO reads it.
"""

from __future__ import annotations

import fnmatch
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MIRRORED = ("mods", "config", "scripts")  # the game folder's folders that sync mirrors

# Glob patterns relative to the game folder (plan 5.1 sync_exclude). Export runs NEI >= 2.8.172
# while Play ships 2.8.171 [A§5.2], and only Export has the GregtecLink export module.
DEFAULT_SYNC_EXCLUDE: dict[str, tuple[str, ...]] = {
    "export": (
        "mods/NotEnoughItems-*.jar",
        "mods/gregteclink*",
        "config/gregteclink*",
    ),
    "lab": (),
}


# The core binds to loopback only (plan 4.6). The phone reaches it through Tailscale Serve, which
# proxies from this PC, so no other address is ever needed (the LAN move in 4.2 is a later WO).
LOOPBACK = "127.0.0.1"
# An allowed_hosts entry: a lower-case DNS name or IPv4 address, optionally with :port.
_HOST_ENTRY = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?$"
)


def default_home() -> Path:
    """GOS_HOME: ``$GOS_HOME``, else ``~/GregtecOS`` (plan 4.3)."""
    return Path(os.environ.get("GOS_HOME") or Path.home() / "GregtecOS")


@dataclass(frozen=True)
class Config:
    gos_app: Path = Path("C:/GregtecOS-app")
    prism_instances: Path = Path("C:/Games/PrismLauncher/instances")
    play: str = "GTNH-Play"
    export: str = "GTNH-Export"
    lab: str = "GTNH-Lab"
    sync_exclude: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_SYNC_EXCLUDE)
    )
    gos_home: Path = field(default_factory=default_home)
    prism_root: Path = Path("C:/Games/PrismLauncher")
    core_host: str = LOOPBACK
    core_port: int = 8765
    supervisor_port: int = 8766
    extra_allowed_hosts: tuple[str, ...] = ()
    core_at_login: bool = True
    monthly_included_usd: float = 100.0
    credit_alerts_pct: tuple[int, ...] = (80, 100)
    cycle_day: int = 1
    launch_home: Path | None = None

    def instance(self, role: str) -> Path:
        """The instance folder of ``role`` ("play", "export" or "lab")."""
        name: str = getattr(self, role)
        return self.prism_instances / name

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Host header values (and Origin host:port) the core accepts (plan 4.6, QA:A8)."""
        port = self.core_port
        base = (f"{LOOPBACK}:{port}", f"localhost:{port}")
        return tuple(dict.fromkeys([*base, *self.extra_allowed_hosts]))

    @property
    def data_dir(self) -> Path:
        return self.gos_home / "data"

    @property
    def logs_dir(self) -> Path:
        return self.gos_home / "logs"

    @property
    def run_dir(self) -> Path:
        """Run files with live tokens. Clayton only: gos-dev reads data, vault and logs (D82)."""
        return self.gos_home / "run"


class ConfigError(ValueError):
    """A known gos.toml key has the wrong type, or an exclude pattern can never match."""


def default_path() -> Path:
    return default_home() / "config" / "gos.toml"


def _string(table: Mapping[str, Any], key: str, default: str, where: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"gos.toml: {where} {key} must be a non-empty string")
    return value


def _int(
    table: Mapping[str, Any], key: str, default: int, where: str, lo: int, hi: int
) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ConfigError(
            f"gos.toml: {where} {key} must be a whole number from {lo} to {hi}"
        )
    return value


def _bool(table: Mapping[str, Any], key: str, default: bool, where: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"gos.toml: {where} {key} must be true or false")
    return value


def _table(parent: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"gos.toml: {where}{key} must be a table")
    return value


def _matchable(pattern: str) -> bool:
    """Whether an exclude pattern can match a path sync walks (see gregtec.sync.excluded)."""
    parts = pattern.rstrip("/").split("/")
    return (
        "\\" not in pattern
        and all(p not in ("", ".", "..") for p in parts)  # also catches a leading /
        # fnmatch.fnmatch ignores case on Windows only, as gregtec.sync does.
        and any(fnmatch.fnmatch(root, parts[0]) for root in MIRRORED)
    )


def load(path: Path | None = None) -> Config:
    """Read ``path`` (default: :func:`default_path`; a missing default file means defaults).

    A file that doesn't parse raises ``tomllib.TOMLDecodeError``, a known key of the wrong type
    or a bad exclude pattern :class:`ConfigError`; both are ``ValueError``.
    """
    if path is None:
        path = default_path()
        if not path.is_file():
            return Config()
    with open(path, "rb") as f:
        data = tomllib.load(f)  # TOMLDecodeError is a ValueError
    defaults = Config()
    paths = _table(data, "paths", "")
    sync = _table(data, "sync", "")
    exclude: dict[str, tuple[str, ...]] = {}
    for role, default in DEFAULT_SYNC_EXCLUDE.items():
        patterns = _table(sync, role, "sync.").get("exclude", [])
        if not isinstance(patterns, list) or not all(
            isinstance(p, str) and p for p in patterns
        ):
            raise ConfigError(
                f"gos.toml: [sync.{role}] exclude must be a list of non-empty strings"
            )
        patterns = list(
            dict.fromkeys([*default, *patterns])
        )  # defaults first, no repeats
        for p in patterns:
            if not _matchable(p):
                raise ConfigError(
                    f"gos.toml: [sync.{role}] exclude {p!r} can never match: write it "
                    "with /, relative to the game folder and starting with mods/, "
                    "config/ or scripts/ (e.g. 'config/lab-only.cfg')"
                )
        exclude[role] = tuple(patterns)
    core = _table(data, "core", "")
    host = _string(core, "host", defaults.core_host, "[core]")
    if host != LOOPBACK:
        raise ConfigError(
            f"gos.toml: [core] host must be {LOOPBACK}: the core listens on loopback only "
            "(plan 4.6); the phone reaches it through Tailscale Serve"
        )
    port = _int(core, "port", defaults.core_port, "[core]", 1024, 65535)
    supervisor_port = _int(
        core, "supervisor_port", defaults.supervisor_port, "[core]", 1024, 65535
    )
    if supervisor_port == port:
        raise ConfigError("gos.toml: [core] supervisor_port must differ from port")
    hosts = core.get("allowed_hosts", [])
    if not isinstance(hosts, list) or not all(
        isinstance(h, str) and _HOST_ENTRY.match(h) for h in hosts
    ):
        raise ConfigError(
            "gos.toml: [core] allowed_hosts must be a list of lower-case host names or "
            "host:port values, e.g. 'gaming-pc.tail1234.ts.net' (no scheme, path or wildcard)"
        )
    startup = _table(data, "startup", "")
    budget = _table(data, "budget", "")
    monthly = budget.get("monthly_included_usd", defaults.monthly_included_usd)
    if (
        isinstance(monthly, bool)
        or not isinstance(monthly, int | float)
        or monthly <= 0
    ):
        raise ConfigError(
            "gos.toml: [budget] monthly_included_usd must be a number above 0"
        )
    alerts = budget.get("credit_alerts_pct", list(defaults.credit_alerts_pct))
    if (
        not isinstance(alerts, list)
        or not alerts
        or not all(
            isinstance(a, int) and not isinstance(a, bool) and 0 < a <= 1000
            for a in alerts
        )
    ):
        raise ConfigError(
            "gos.toml: [budget] credit_alerts_pct must be a list of whole percentages, e.g. [80, 100]"
        )
    launch = _table(data, "launch", "")
    launch_home = launch.get("home")
    if launch_home is not None and (
        not isinstance(launch_home, str) or not launch_home
    ):
        raise ConfigError("gos.toml: [launch] home must be a non-empty string")
    return Config(
        gos_app=Path(_string(paths, "gos_app", str(defaults.gos_app), "[paths]")),
        prism_instances=Path(
            _string(paths, "prism_instances", str(defaults.prism_instances), "[paths]")
        ),
        play=_string(paths, "play", defaults.play, "[paths]"),
        export=_string(paths, "export", defaults.export, "[paths]"),
        lab=_string(paths, "lab", defaults.lab, "[paths]"),
        sync_exclude=exclude,
        gos_home=Path(_string(paths, "gos_home", str(defaults.gos_home), "[paths]")),
        prism_root=Path(
            _string(paths, "prism_root", str(defaults.prism_root), "[paths]")
        ),
        core_host=host,
        core_port=port,
        supervisor_port=supervisor_port,
        extra_allowed_hosts=tuple(dict.fromkeys(hosts)),
        core_at_login=_bool(
            startup, "core_at_login", defaults.core_at_login, "[startup]"
        ),
        monthly_included_usd=float(monthly),
        credit_alerts_pct=tuple(sorted(set(alerts))),
        cycle_day=_int(budget, "cycle_day", defaults.cycle_day, "[budget]", 1, 28),
        launch_home=Path(launch_home) if launch_home else None,
    )
