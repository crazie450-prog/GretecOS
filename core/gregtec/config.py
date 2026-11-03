"""``gos.toml`` (plan section 4.3, Appendix C). Only the keys the existing commands read so far.

| key                      | default                                  | read by        |
|--------------------------|------------------------------------------|----------------|
| [paths] prism_instances  | C:/Games/PrismLauncher/instances         | sync (WO-0.3)  |
| [paths] play, export, lab| GTNH-Play, GTNH-Export, GTNH-Lab         | sync           |
| [sync.export] exclude    | the NEI jar and GregtecLink (plan 5.1)   | sync           |
| [sync.lab] exclude       | nothing                                  | sync           |

``play``, ``export`` and ``lab`` are instance folder names inside ``prism_instances``. An
``exclude`` list adds to its default, so the NEI and GregtecLink patterns can't be dropped by
accident (Clayton, 2026-11-03). Each pattern is posix, relative to the game folder and starts in a mirrored
folder (``config/lab-only.cfg``); one that could never match is an error, since sync would then
delete the file it was meant to keep. Every other key in the Appendix C skeleton is ignored until
its WO reads it.
"""

from __future__ import annotations

import fnmatch
import os
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


@dataclass(frozen=True)
class Config:
    prism_instances: Path = Path("C:/Games/PrismLauncher/instances")
    play: str = "GTNH-Play"
    export: str = "GTNH-Export"
    lab: str = "GTNH-Lab"
    sync_exclude: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_SYNC_EXCLUDE)
    )

    def instance(self, role: str) -> Path:
        """The instance folder of ``role`` ("play", "export" or "lab")."""
        name: str = getattr(self, role)
        return self.prism_instances / name


class ConfigError(ValueError):
    """A known gos.toml key has the wrong type, or an exclude pattern can never match."""


def default_path() -> Path:
    home = Path(os.environ.get("GOS_HOME") or Path.home() / "GregtecOS")
    return home / "config" / "gos.toml"


def _string(table: Mapping[str, Any], key: str, default: str, where: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"gos.toml: {where} {key} must be a non-empty string")
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
    return Config(
        prism_instances=Path(
            _string(paths, "prism_instances", str(defaults.prism_instances), "[paths]")
        ),
        play=_string(paths, "play", defaults.play, "[paths]"),
        export=_string(paths, "export", defaults.export, "[paths]"),
        lab=_string(paths, "lab", defaults.lab, "[paths]"),
        sync_exclude=exclude,
    )
