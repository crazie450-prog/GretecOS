"""``gos.toml`` (plan section 4.3, Appendix C). Only the keys the existing commands read so far.

| key                      | default                                  | read by                     |
|--------------------------|------------------------------------------|-----------------------------|
| [paths] gos_app          | C:/GregtecOS-app                         | deploy (WO-0.8), sync (D98) |
| [paths] prism_instances  | C:/Games/PrismLauncher/instances         | sync (WO-0.3), collect      |
| [paths] play, export, lab| GTNH-Play, GTNH-Export, GTNH-Lab         | sync, collect               |
| [paths] world            | none (Play's world: a folder in saves/)  | quests load (WO-1.5)        |
| [paths] lab_world        | none (a Lab world: a folder in saves/)   | ores load (WO-1.6)          |
| [sync.export] exclude    | the NEI jar and GregtecLink (plan 5.1)   | sync                        |
| [sync.lab] exclude       | nothing                                  | sync                        |
| [export] keep_collected  | 5 (D102)                                 | export collect              |
| [db] keep_runs           | 2 (D107)                                 | db build (WO-1.3)           |
| [policy] ore_server_cache| true (D27: every generated vein)         | ores load (WO-1.6)          |
| [paths] gos_home         | $GOS_HOME, else ~/GregtecOS              | WO-1.2, WO-1.4, WO-2.1      |
| [paths] prism_root       | C:/Games/PrismLauncher                   | gos launch, export run      |
| [core] host              | 127.0.0.1 (the only value allowed)       | core (WO-2.1, plan 4.6)     |
| [core] port              | 8765                                     | core, clients               |
| [core] supervisor_port   | 8766                                     | supervisor, gos stop/launch |
| [core] dashboard_host    | gos.localhost (pairing and the dashboard)| core (D117)                 |
| [core] allowed_hosts     | 127.0.0.1, localhost, dashboard_host     | Host/Origin checks (QA:A8)  |
| [startup] core_at_login  | true                                     | supervisor --login (D77)    |
| [budget] monthly_included_usd | 100                                 | llm_usage meter (D47)       |
| [budget] credit_alerts_pct    | [80, 100]                           | llm_usage meter (D48)       |
| [budget] cycle_day       | 1 (credits reset on this day each month) | llm_usage meter (D118)      |
| [budget] task_max_usd    | 2.0 (placeholder until WO-3.7, plan 4.4) | deep lane (WO-2.4, D156)    |
| [models] orchestrator, planner, architect, analyst, heavy | plan 4.4's models | deep lane (D155) |
| [models] use_heavy       | false (planner and architect on heavy)   | deep lane (D155)            |
| [launch] home            | gos_home                                 | gos launch (D115)           |
| [maintenance] idle_minutes | 30 (QA:A9)                             | idle maintenance (WO-2.5)   |
| [maintenance] keep_backups | 7 daily DB backups (D139)              | idle maintenance (WO-2.5)   |
| [fingerprint] configs    | GT's recipe configs (D120)               | fingerprint (WO-1.4)        |
| [policy] export_on_quit  | ask (D69); never turns it off            | post-exit hook (WO-1.4)     |
| [mailbox.play] / [mailbox.lab] drive | none (no M1 mailbox)         | core (WO-7.3, D217)         |
| [mailbox.<role>] dir     | home/gos/mailbox                         | core (WO-7.3)               |
| [mailbox.<role>] world   | [paths] world (play), lab_world (lab)    | core (WO-7.3)               |

``play``, ``export`` and ``lab`` are instance folder names inside ``prism_instances``. An
``exclude`` list adds to its default, so the NEI and GregtecLink patterns can't be dropped by
accident (Clayton, 2026-11-03). Each pattern is posix, relative to the game folder and starts in a mirrored
folder (``config/lab-only.cfg``); one that could never match is an error, since sync would then
delete the file it was meant to keep. ``allowed_hosts`` likewise adds to its default (each of the
three names with ``:<port>``). Every other key in the Appendix C skeleton is ignored until its WO
reads it.

**Service keys** (``[core]``, ``[startup]``, ``[budget]``, ``[launch]``, ``[models]``, ``[mailbox]``) are read only by the core,
the supervisor and the service commands. A bad one doesn't stop :func:`load`, so ``gos deploy``,
``gos doctor`` and the sync and tweak scripts still run (Appendix C's own placeholder tailnet name
is such a value): the key keeps its default and the problem goes into
``Config.service_errors``. :func:`service_problems` adds one more, gos-dev using Clayton's ports or
dashboard name (D115); the core and the supervisor refuse to start while there is any.
delete the file it was meant to keep. ``[fingerprint] configs`` likewise adds to its default; each
pattern is matched as an exclude pattern is, and must start in ``config/``. Every other key in the
Appendix C skeleton is ignored until its WO reads it.
"""

from __future__ import annotations

import fnmatch
import getpass
import math
import os
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from gregtec.llm.prices import PRICES

T = TypeVar("T")

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

# Recipe-affecting configs in Play's fingerprint besides dreamcraft.cfg, which is always in it
# (plan 5.2 step 4; the whitelist is a VERIFY [A§5.5], D120). GT's files from the 2.9.0 pack;
# GT's Client.cfg (client preferences) and Other.cfg (tool ranges) are left out.
DEFAULT_FINGERPRINT_CONFIGS = (
    "config/GregTech/GregTech.cfg",
    "config/GregTech/MachineStats.cfg",
    "config/GregTech/OverpoweredStuff.cfg",
    "config/GregTech/Pollution.cfg",
    "config/GregTech/WorldGeneration.cfg",
)
EXPORT_ON_QUIT = ("ask", "never")  # [policy] export_on_quit (D69)
# The pack and GT versions GOS is pinned to (plan 0.1 principle 4, CLAUDE.md). Repo constants, not
# gos.toml keys: a GT bump needs the GT pin-bump procedure (docs/runbooks/pack_update.md, D148),
# which changes code, so it can't be done by editing a config file (WO-1.9).
PACK_PIN = "2.9.0"
GT_PIN = "5.09.54.265"
# [models] keys the deep lane reads (plan 4.4, Appendix C; vision arrives with WO-8.2)
MODEL_ROLES = ("orchestrator", "planner", "architect", "analyst", "heavy")
# M1, the OC file mailbox (plan 7.9, WO-7.3): the instances whose worlds can hold one, and the
# agent's default folder on its drive (mailbox/ next to /home/gos/gos-agent.lua, oc/README.md).
MAILBOX_ROLES = ("play", "lab")
MAILBOX_DIR = "home/gos/mailbox"
_OC_ADDRESS = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_MAILBOX_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_WORLD_NAME = re.compile(r'^[^/\\:*?"<>|\x00-\x1f]+$')


def default_home() -> Path:
    """GOS_HOME: ``$GOS_HOME``, else ``~/GregtecOS`` (plan 4.3)."""
    return Path(os.environ.get("GOS_HOME") or Path.home() / "GregtecOS")


# The core binds to loopback only (plan 4.6). The phone reaches it through Tailscale Serve, which
# proxies from this PC, so no other address is ever needed (the LAN move in 4.2 is a later WO).
LOOPBACK = "127.0.0.1"
# Browsers resolve every *.localhost name to loopback. Paired dashboards use this name only, never
# bare 127.0.0.1 or localhost: cookies ignore ports, so a cookie for localhost would also go to
# every other local web server, a dev server of gos-dev's included (D117).
DASHBOARD_HOST = "gos.localhost"
# An allowed_hosts entry: a lower-case DNS name or IPv4 address, optionally with :port.
_HOST_ENTRY = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?$"
)
_DASHBOARD_ENTRY = re.compile(r"^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+localhost$")
# Claude Code's account (setup_gos_dev.ps1 -DevUser). Its own core and supervisor must not use
# Clayton's ports or dashboard name (D115): see service_problems.
DEV_ACCOUNT = "gos-dev"


@dataclass(frozen=True)
class OcMailbox:
    """``[mailbox.<role>]``: one OC agent's file mailbox (M1, WO-7.3), the folder
    ``<world>/opencomputers/<drive>/<dir>/`` in a world of the play or lab instance."""

    role: str  # "play" or "lab"
    world: str  # the world's folder in saves/
    drive: str  # the OC drive's address
    dir: str = MAILBOX_DIR  # posix, relative to the drive's folder


@dataclass(frozen=True)
class Config:
    gos_app: Path = Path("C:/GregtecOS-app")
    prism_instances: Path = Path("C:/Games/PrismLauncher/instances")
    play: str = "GTNH-Play"
    export: str = "GTNH-Export"
    lab: str = "GTNH-Lab"
    world: str | None = None  # Play's world: its folder name in saves/ (WO-1.5)
    lab_world: str | None = None  # the Lab world `gos ores load` reads (WO-1.6)
    sync_exclude: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_SYNC_EXCLUDE)
    )
    gos_home: Path = field(default_factory=default_home)
    prism_root: Path = Path("C:/Games/PrismLauncher")
    core_host: str = LOOPBACK
    core_port: int = 8765
    supervisor_port: int = 8766
    dashboard_host: str = DASHBOARD_HOST
    extra_allowed_hosts: tuple[str, ...] = ()
    core_at_login: bool = True
    monthly_included_usd: float = 100.0
    credit_alerts_pct: tuple[int, ...] = (80, 100)
    cycle_day: int = 1
    task_max_usd: float = (
        2.0  # [budget]: one deep-lane task's cap before it asks (D156)
    )
    # [models] (plan 4.4, Appendix C): the deep lane's orchestrator and subagents (D155)
    model_orchestrator: str = "claude-sonnet-5-5"
    model_planner: str = "claude-opus-5-5"
    model_architect: str = "claude-opus-5-5"
    model_analyst: str = "claude-haiku-5-5"
    model_heavy: str = "claude-fable-5-1"
    use_heavy: bool = False  # planner and architect on model_heavy (opt-in only)
    launch_home: Path | None = None
    idle_minutes: int = 30  # [maintenance]: no input this long, game closed (plan 6.6)
    keep_backups: int = 7  # [maintenance]: VACUUM INTO backups kept (D139)
    mailboxes: tuple[OcMailbox, ...] = ()  # [mailbox.play], [mailbox.lab] (WO-7.3)
    # Problems with the service keys; the core and the supervisor refuse to start while any.
    service_errors: tuple[str, ...] = ()
    fingerprint_configs: tuple[str, ...] = DEFAULT_FINGERPRINT_CONFIGS
    export_on_quit: str = "ask"
    keep_collected: int = 5  # collected export runs kept in GOS_HOME (D102)
    keep_runs: int = 2  # export runs kept in gos.sqlite (D107)
    ore_server_cache: bool = True  # read VisualProspecting's server cache (D27)

    def instance(self, role: str) -> Path:
        """The instance folder of ``role`` ("play", "export" or "lab")."""
        name: str = getattr(self, role)
        return self.prism_instances / name

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Host header values (and Origin host:port) the core accepts (plan 4.6, QA:A8)."""
        port = self.core_port
        base = (
            f"{LOOPBACK}:{port}",
            f"localhost:{port}",
            f"{self.dashboard_host}:{port}",
        )
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


def running_as_dev() -> bool:
    """Whether this process runs as gos-dev (Claude Code's account, D75)."""
    try:
        return getpass.getuser().lower() == DEV_ACCOUNT
    except (KeyError, OSError):  # no user name at all: not gos-dev
        return False


def service_problems(cfg: Config) -> list[str]:
    """Why the core or the supervisor must not start with ``cfg``; empty if they may.

    Besides ``cfg.service_errors``: as gos-dev, Clayton's default ports and dashboard name. A
    gos-dev core on 8765 while Clayton's GOS is down (a deploy, ``gos stop --all``) would keep his
    supervisor from starting and receive his clients' tokens, and his browser sends his dashboard
    cookie to any port of the dashboard name (D115, D117).
    """
    problems = list(cfg.service_errors)
    if running_as_dev():
        taken = sorted(
            {cfg.core_port, cfg.supervisor_port}
            & {Config.core_port, Config.supervisor_port}
        )
        if taken:
            problems.append(
                f"gos.toml: as {DEV_ACCOUNT}, [core] port and supervisor_port must not be "
                f"Clayton's {Config.core_port} and {Config.supervisor_port} (now {taken}); "
                "set e.g. port = 8775 and supervisor_port = 8776 in gos-dev's own gos.toml"
            )
        if cfg.dashboard_host == DASHBOARD_HOST:
            problems.append(
                f"gos.toml: as {DEV_ACCOUNT}, [core] dashboard_host must not be Clayton's "
                f'{DASHBOARD_HOST}; set e.g. dashboard_host = "gos-dev.localhost"'
            )
        if any(m.role == "play" for m in cfg.mailboxes):
            problems.append(
                f"gos.toml: as {DEV_ACCOUNT}, [mailbox.play] must not be set: Play's OC mailbox "
                "is Clayton's core's (D75); test with [mailbox.lab]"
            )
    return problems


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
    or a bad exclude pattern :class:`ConfigError`; both are ``ValueError``. A bad service key
    doesn't raise: it goes into ``service_errors`` (see the module docstring).
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
    service = _service_keys(data, defaults)
    configs = _table(data, "fingerprint", "").get("configs", [])
    if not isinstance(configs, list) or not all(
        isinstance(p, str) and p for p in configs
    ):
        raise ConfigError(
            "gos.toml: [fingerprint] configs must be a list of non-empty strings"
        )
    for p in configs:
        if not (_matchable(p) and p.startswith("config/")):
            raise ConfigError(
                f"gos.toml: [fingerprint] configs {p!r} can never match: write it with /, "
                "relative to the game folder and starting with config/ "
                "(e.g. 'config/bartworks.cfg')"
            )
    on_quit = _string(
        _table(data, "policy", ""),
        "export_on_quit",
        defaults.export_on_quit,
        "[policy]",
    )
    if on_quit not in EXPORT_ON_QUIT:
        raise ConfigError(
            f"gos.toml: [policy] export_on_quit must be one of {', '.join(EXPORT_ON_QUIT)}"
        )
    keep = _table(data, "export", "").get("keep_collected", defaults.keep_collected)
    if type(keep) is not int or keep < 1:
        raise ConfigError(
            "gos.toml: [export] keep_collected must be a whole number >= 1"
        )
    keep_runs = _table(data, "db", "").get("keep_runs", defaults.keep_runs)
    if type(keep_runs) is not int or keep_runs < 1:
        raise ConfigError("gos.toml: [db] keep_runs must be a whole number >= 1")
    server_cache = _table(data, "policy", "").get(
        "ore_server_cache", defaults.ore_server_cache
    )
    if type(server_cache) is not bool:
        raise ConfigError("gos.toml: [policy] ore_server_cache must be true or false")
    return Config(
        gos_app=Path(_string(paths, "gos_app", str(defaults.gos_app), "[paths]")),
        prism_instances=Path(
            _string(paths, "prism_instances", str(defaults.prism_instances), "[paths]")
        ),
        play=_string(paths, "play", defaults.play, "[paths]"),
        export=_string(paths, "export", defaults.export, "[paths]"),
        lab=_string(paths, "lab", defaults.lab, "[paths]"),
        world=_string(paths, "world", "", "[paths]") if "world" in paths else None,
        lab_world=(
            _string(paths, "lab_world", "", "[paths]") if "lab_world" in paths else None
        ),
        sync_exclude=exclude,
        gos_home=Path(_string(paths, "gos_home", str(defaults.gos_home), "[paths]")),
        prism_root=Path(
            _string(paths, "prism_root", str(defaults.prism_root), "[paths]")
        ),
        **service,
        fingerprint_configs=tuple(
            dict.fromkeys([*DEFAULT_FINGERPRINT_CONFIGS, *configs])
        ),
        export_on_quit=on_quit,
        keep_collected=keep,
        keep_runs=keep_runs,
        ore_server_cache=server_cache,
    )


def _service_keys(data: Mapping[str, Any], defaults: Config) -> dict[str, Any]:
    """The service keys as Config fields. A bad one keeps its default; its problem is recorded."""
    problems: list[str] = []

    def soft(parse: Callable[[], T], default: T) -> T:
        try:
            return parse()
        except ConfigError as exc:
            problems.append(str(exc))
            return default

    def table(key: str) -> Mapping[str, Any]:
        return soft(lambda: _table(data, key, ""), {})

    core, startup, budget, launch = (
        table(k) for k in ("core", "startup", "budget", "launch")
    )
    maintenance = table("maintenance")
    models = table("models")
    host = soft(lambda: _core_host(core, defaults.core_host), defaults.core_host)
    port = soft(
        lambda: _int(core, "port", defaults.core_port, "[core]", 1024, 65535),
        defaults.core_port,
    )
    supervisor_port = soft(
        lambda: _int(
            core, "supervisor_port", defaults.supervisor_port, "[core]", 1024, 65535
        ),
        defaults.supervisor_port,
    )
    if supervisor_port == port:
        problems.append("gos.toml: [core] supervisor_port must differ from port")
    dashboard = soft(
        lambda: _dashboard_host(core, defaults.dashboard_host), defaults.dashboard_host
    )
    hosts = soft(lambda: _allowed_hosts(core), ())
    monthly = soft(
        lambda: _monthly(budget, defaults.monthly_included_usd),
        defaults.monthly_included_usd,
    )
    alerts = soft(
        lambda: _alerts(budget, defaults.credit_alerts_pct), defaults.credit_alerts_pct
    )
    launch_home = soft(lambda: _launch_home(launch), None)
    return {
        "core_host": host,
        "core_port": port,
        "supervisor_port": supervisor_port,
        "dashboard_host": dashboard,
        "extra_allowed_hosts": hosts,
        "core_at_login": soft(
            lambda: _bool(
                startup, "core_at_login", defaults.core_at_login, "[startup]"
            ),
            defaults.core_at_login,
        ),
        "monthly_included_usd": monthly,
        "credit_alerts_pct": alerts,
        "cycle_day": soft(
            lambda: _int(budget, "cycle_day", defaults.cycle_day, "[budget]", 1, 28),
            defaults.cycle_day,
        ),
        "launch_home": launch_home,
        "task_max_usd": soft(
            lambda: _usd(budget, "task_max_usd", defaults.task_max_usd),
            defaults.task_max_usd,
        ),
        **{
            f"model_{role}": soft(
                lambda role=role: _model(
                    models, role, getattr(defaults, f"model_{role}")
                ),
                getattr(defaults, f"model_{role}"),
            )
            for role in MODEL_ROLES
        },
        "use_heavy": soft(
            lambda: _bool(models, "use_heavy", defaults.use_heavy, "[models]"),
            defaults.use_heavy,
        ),
        "idle_minutes": soft(
            lambda: _int(
                maintenance,
                "idle_minutes",
                defaults.idle_minutes,
                "[maintenance]",
                1,
                1440,
            ),
            defaults.idle_minutes,
        ),
        "keep_backups": soft(
            lambda: _int(
                maintenance,
                "keep_backups",
                defaults.keep_backups,
                "[maintenance]",
                1,
                365,
            ),
            defaults.keep_backups,
        ),
        "mailboxes": soft(lambda: _mailboxes(data), ()),
        "service_errors": tuple(problems),
    }


def _core_host(core: Mapping[str, Any], default: str) -> str:
    host = _string(core, "host", default, "[core]")
    if host != LOOPBACK:
        raise ConfigError(
            f"gos.toml: [core] host must be {LOOPBACK}: the core listens on loopback only "
            "(plan 4.6); the phone reaches it through Tailscale Serve"
        )
    return host


def _dashboard_host(core: Mapping[str, Any], default: str) -> str:
    name = _string(core, "dashboard_host", default, "[core]")
    if not _DASHBOARD_ENTRY.match(name):
        raise ConfigError(
            "gos.toml: [core] dashboard_host must be a lower-case name ending in .localhost "
            f"(browsers resolve those to loopback), e.g. {DASHBOARD_HOST!r}"
        )
    return name


def _allowed_hosts(core: Mapping[str, Any]) -> tuple[str, ...]:
    hosts = core.get("allowed_hosts", [])
    if not isinstance(hosts, list) or not all(
        isinstance(h, str) and _HOST_ENTRY.match(h) for h in hosts
    ):
        raise ConfigError(
            "gos.toml: [core] allowed_hosts must be a list of lower-case host names or "
            "host:port values, e.g. 'gaming-pc.tail1234.ts.net' (no scheme, path or wildcard)"
        )
    return tuple(dict.fromkeys(hosts))


def _monthly(budget: Mapping[str, Any], default: float) -> float:
    monthly = budget.get("monthly_included_usd", default)
    if (
        isinstance(monthly, bool)
        or not isinstance(monthly, int | float)
        or monthly <= 0
    ):
        raise ConfigError(
            "gos.toml: [budget] monthly_included_usd must be a number above 0"
        )
    return float(monthly)


def _usd(budget: Mapping[str, Any], key: str, default: float) -> float:
    value = budget.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ConfigError(f"gos.toml: [budget] {key} must be a number of USD above 0")
    return float(value)


def _model(models: Mapping[str, Any], role: str, default: str) -> str:
    """A model id the price table knows: the deep lane meters every call (D118, D156)."""
    value = models.get(role, default)
    if value not in PRICES:
        raise ConfigError(
            f"gos.toml: [models] {role} must be one of {', '.join(sorted(PRICES))}"
        )
    return str(value)


def _alerts(budget: Mapping[str, Any], default: tuple[int, ...]) -> tuple[int, ...]:
    alerts = budget.get("credit_alerts_pct", list(default))
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
    return tuple(sorted(set(alerts)))


def _launch_home(launch: Mapping[str, Any]) -> Path | None:
    home = launch.get("home")
    if home is not None and (not isinstance(home, str) or not home):
        raise ConfigError("gos.toml: [launch] home must be a non-empty string")
    return Path(home) if home else None


def _mailboxes(data: Mapping[str, Any]) -> tuple[OcMailbox, ...]:
    """``[mailbox.play]`` and ``[mailbox.lab]`` (WO-7.3). A table without ``drive`` is an error,
    so a half-filled one can't silently do nothing."""
    tables = _table(data, "mailbox", "")
    paths = _table(data, "paths", "")
    out: list[OcMailbox] = []
    for role, spec in tables.items():
        where = f"[mailbox.{role}]"
        if role not in MAILBOX_ROLES:
            raise ConfigError(
                f"gos.toml: {where}: a mailbox belongs to the play or lab instance "
                "([mailbox.play] or [mailbox.lab])"
            )
        if not isinstance(spec, dict):
            raise ConfigError(f"gos.toml: {where} must be a table")
        unknown = sorted(set(spec) - {"world", "drive", "dir"})
        if unknown:
            raise ConfigError(
                f"gos.toml: {where} has unknown keys {', '.join(unknown)} "
                "(it takes world, drive and dir)"
            )
        world = spec.get("world", paths.get("world" if role == "play" else "lab_world"))
        if (
            not isinstance(world, str)
            or not _WORLD_NAME.match(world)
            or world.strip(" .") != world
            or len(world) > 255
        ):
            raise ConfigError(
                f"gos.toml: {where} world must be a world's folder name in saves/ "
                f"(or set [paths] {'world' if role == 'play' else 'lab_world'})"
            )
        drive = spec.get("drive")
        if not isinstance(drive, str) or not _OC_ADDRESS.match(drive):
            raise ConfigError(
                f"gos.toml: {where} drive must be the OC drive's address, as the agent prints "
                'it when it starts (e.g. "1d9f2c3a-6b1e-4c1b-9a7e-2f3c4d5e6f70")'
            )
        folder = spec.get("dir", MAILBOX_DIR)
        parts = folder.split("/") if isinstance(folder, str) else []
        if (
            not parts
            or len(parts) > 8
            or len(folder) > 128
            or not all(_MAILBOX_PART.match(p) for p in parts)
        ):
            raise ConfigError(
                f"gos.toml: {where} dir must be a folder on the drive, written with / and "
                f'relative to the drive, as the agent prints it (e.g. "{MAILBOX_DIR}")'
            )
        out.append(OcMailbox(role=role, world=world, drive=drive, dir=folder))
    return tuple(out)
