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


def play_saved(ctx: Context) -> bool:
    """Whether gos.toml already says where Play is: its ``[paths]`` names Play or the instances
    folder, or setup was finished with it (Play is then the default the core runs with)."""
    return (
        ctx.toml("paths", "play") is not None
        or ctx.toml("paths", "prism_instances") is not None
        or ctx.toml("setup", "completed_utc") is not None
    )


def plays(ctx: Context) -> list[Path]:
    """Every folder that is Play to GOS now: gos.toml's (defaults for what it lacks) and the one
    the running core started with, which it syncs against until its next start."""
    found = [ctx.cfg.instance("play")]
    if ctx.running is not None:
        found.append(ctx.running.instance("play"))
    return found


def play_fixed(ctx: Context) -> bool:
    """Whether Play may no longer move by a click: gos.toml says where it is, or the Play GOS runs
    with (defaults included) is a folder there, so it may be the real Play (final check round 2).
    Only a Play that names no folder yet, on a first run, is chosen in the wizard."""
    return play_saved(ctx) or any(p.is_dir() for p in plays(ctx))


def play_guard(ctx: Context, cfg: Config) -> dict[str, str]:
    """Problems by field that would point GOS's writes at Play (H1 of the WO-2.9 review and the
    final check's round 2, D167): Play moved away from the folder GOS runs with, Export or Lab in
    or around a folder that is or was Play (``gregtec.sync`` mirrors into Export and Lab and
    deletes what the source lacks), or GOS_HOME or GOS_APP in or around one (GOS keeps its DB,
    logs and backups in GOS_HOME, and ``gos deploy`` writes GOS_APP). Since Play can't move once
    it is a folder, no sequence of saves turns the old Play into Export or Lab. Moving Play is a
    hand edit of gos.toml, never a click."""
    play_now = ctx.cfg.instance("play")
    problems: dict[str, str] = {}
    if play_fixed(ctx) and doctor.folded(cfg.instance("play")) != doctor.folded(
        play_now
    ):
        problems["play"] = (
            f"Play is {play_now}; the wizard never moves Play. To change it, edit [paths] in "
            "gos.toml by hand"
        )
    guarded = [*plays(ctx), cfg.instance("play")]
    for key, name in (("gos_home", "GOS_HOME"), ("gos_app", "GOS_APP")):
        play = doctor.play_overlap(getattr(cfg, key), guarded)
        if play is not None:
            problems[key] = (
                f"that overlaps Play ({play}); GOS writes in {name}, so it must be outside "
                "Play and must not hold it"
            )
    for role in ("export", "lab"):
        play = doctor.play_overlap(cfg.instance(role), guarded)
        if play is not None:
            problems[role] = (
                f"that is Play's folder ({play}); GOS writes to {role.capitalize()}, so it "
                "must never be Play"
            )
    return problems


def check(ctx: Context, values: Mapping[str, str]) -> Outcome:
    problems = {
        role: why
        for role in INSTANCE_ROLES
        if (why := doctor.name_problem(values.get(role, ""))) is not None
    }
    cfg = candidate(ctx, values)
    if not problems:
        problems = play_guard(ctx, cfg)
    # The guard runs first; the checklist then probes nothing in or around either Play.
    rows = doctor.checklist(cfg, plays=plays(ctx))
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
