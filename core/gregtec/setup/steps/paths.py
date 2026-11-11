"""Step 10, paths (plan 4.3, Appendix C): Prism, the three instances, GOS_APP and GOS_HOME,
checked with ``gos doctor``'s checks before they are saved."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path

from gregtec import doctor
from gregtec.config import Config
from gregtec.setup.registry import WARN, Check, Context, Field, Outcome, Step

INSTANCE_ROLES = ("play", "export", "lab")
_BAD_NAME = set('/\\:*?"<>|')


def _name_problem(value: str) -> str | None:
    if value in (".", "..") or any(c in _BAD_NAME for c in value):
        return "an instance's folder name in the instances folder, not a path"
    return None


def candidate(ctx: Context, values: Mapping[str, str]) -> Config:
    """The config these values would make (the rest as gos.toml has it)."""
    changes: dict[str, object] = {}
    for key in ("gos_app", "gos_home", "prism_root", "prism_instances"):
        if values.get(key):
            changes[key] = Path(values[key])
    for role in INSTANCE_ROLES:
        if values.get(role):
            changes[role] = values[role]
    return dataclasses.replace(ctx.cfg, **changes)  # type: ignore[arg-type]


def check(ctx: Context, values: Mapping[str, str]) -> Outcome:
    problems = {
        role: why
        for role in INSTANCE_ROLES
        if (why := _name_problem(values.get(role, ""))) is not None
    }
    cfg = candidate(ctx, values)
    rows = doctor.checklist(cfg)
    config_home = ctx.path.parent.parent
    if cfg.gos_home.resolve() != config_home.resolve():
        rows.append(
            Check(
                "paths.config",
                "Where gos.toml lives",
                WARN,
                f"gos.toml stays in {ctx.path}; GOS keeps its data in {cfg.gos_home}",
            )
        )
    return Outcome(problems=problems, checks=rows)


def _str(attr: str) -> Callable[[Context], str]:
    return lambda ctx: str(getattr(ctx.cfg, attr))


STEPS = [
    Step(
        id="paths",
        title="Paths",
        order=10,
        intro=(
            "Where Prism keeps the instances, which instance is Play, Export and Lab, where "
            "the GOS release is installed and where GOS keeps its data. GOS only reads Play."
        ),
        fields=(
            Field(
                "prism_root",
                "Prism folder",
                "path",
                toml=("paths", "prism_root"),
                help="The portable Prism install (V24), e.g. C:/Games/PrismLauncher",
                required=True,
                default=_str("prism_root"),
            ),
            Field(
                "prism_instances",
                "Prism instances folder",
                "path",
                toml=("paths", "prism_instances"),
                help="Usually the instances folder inside the Prism folder",
                required=True,
                default=_str("prism_instances"),
            ),
            Field(
                "play",
                "Play instance",
                "text",
                toml=("paths", "play"),
                help="The instance you play in (read only for GOS)",
                required=True,
                default=_str("play"),
            ),
            Field(
                "export",
                "Export instance",
                "text",
                toml=("paths", "export"),
                help="The instance that exports recipes (plan 5.1)",
                required=True,
                default=_str("export"),
            ),
            Field(
                "lab",
                "Lab instance",
                "text",
                toml=("paths", "lab"),
                help="The creative test instance",
                required=True,
                default=_str("lab"),
            ),
            Field(
                "gos_app",
                "GOS_APP",
                "path",
                toml=("paths", "gos_app"),
                help="The release install that gos deploy makes (D91)",
                required=True,
                default=_str("gos_app"),
            ),
            Field(
                "gos_home",
                "GOS_HOME",
                "path",
                toml=("paths", "gos_home"),
                help="GOS's data, vault, logs and backups (plan 4.3)",
                required=True,
                default=_str("gos_home"),
            ),
        ),
        check=check,
    )
]
