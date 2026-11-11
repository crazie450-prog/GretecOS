"""Step 10, paths (plan 4.3, Appendix C): Prism, the three instances, GOS_APP and GOS_HOME,
checked with ``gos doctor``'s checks before they are saved."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path

from gregtec import doctor
from gregtec.config import Config
from gregtec.setup.registry import ERROR, WARN, Check, Context, Field, Outcome, Step

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
    """Every folder that is or was Play to GOS: gos.toml's (defaults for what it lacks), the one
    the running core started with (it syncs against that until its next start) and every former
    Play on record (``[setup] former_plays`` and ``former_plays.txt`` beside gos.toml, D223), so
    a hand edit of ``[paths] play`` or a restart never frees the old Play to be Export or Lab."""
    found = [ctx.cfg.instance("play"), *ctx.cfg.former_plays]
    if ctx.running is not None:
        found += [ctx.running.instance("play"), *ctx.running.former_plays]
    return list(dict.fromkeys(found))


def same_folder(a: Path, b: Path) -> bool:
    """Whether ``a`` and ``b`` are one folder (resolved, case ignored); one that can't be
    resolved (a link loop) is never the same."""
    try:
        return doctor.folded(a) == doctor.folded(b)
    except (OSError, RuntimeError):
        return False


def play_fixed(ctx: Context) -> bool:
    """Whether Play may no longer move by a click: gos.toml says where it is, or the Play GOS runs
    with (defaults included) is a folder there, so it may be the real Play (final check round 2).
    Only a Play that names no folder yet, on a first run, is chosen in the wizard."""
    now = [ctx.cfg.instance("play")]
    if ctx.running is not None:
        now.append(ctx.running.instance("play"))
    # Former Plays don't fix Play (a new gos.toml may choose one); they only stay off Export
    # and Lab (D223).
    return play_saved(ctx) or any(p.is_dir() for p in now)


def play_guard(ctx: Context, cfg: Config) -> dict[str, str]:
    """Problems by field that would point GOS's writes at Play (H1 of the WO-2.9 review and the
    final check's round 2, D167): Play moved away from the folder GOS runs with, Export or Lab in
    or around a folder that is or was Play (``gregtec.sync`` mirrors into Export and Lab and
    deletes what the source lacks), or GOS_HOME or GOS_APP in or around one (GOS keeps its DB,
    logs and backups in GOS_HOME, and ``gos deploy`` writes GOS_APP; D223: GOS_HOME's own folders
    resolved too, and Prism's instances folder, Export and Lab as well as every Play). Moving
    Play is a hand edit of gos.toml, never a click, and every Play is on record (:func:`plays`),
    so neither a hand edit nor a restart turns an old Play into Export or Lab (D223)."""
    play_now = ctx.cfg.instance("play")
    problems: dict[str, str] = {}
    if play_fixed(ctx) and not same_folder(cfg.instance("play"), play_now):
        problems["play"] = (
            f"Play is {play_now}; the wizard never moves Play. To change it, edit [paths] in "
            "gos.toml by hand (the old Play stays on record and is never Export or Lab)"
        )
    guarded = [*plays(ctx), cfg.instance("play")]
    folders = doctor.guarded(cfg, guarded)
    if (why := doctor.home_problem(cfg.gos_home, folders)) is not None:
        problems["gos_home"] = why
    if (why := doctor.touch_problem(cfg.gos_app, folders)) is not None:
        problems["gos_app"] = (
            f"GOS_APP {why}; gos deploy writes in GOS_APP, so it must be outside every Play "
            "and Prism's instances folder"
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
    row = config_row(ctx, cfg)
    if row is not None:
        rows.append(row)
    elif not same_folder(cfg.gos_home, ctx.path.parent.parent):
        rows.append(
            Check(
                "paths.config",
                "Where gos.toml lives",
                WARN,
                f"gos.toml stays in {ctx.path}; GOS keeps its data in {cfg.gos_home}",
            )
        )
    return Outcome(problems=problems, checks=rows)


def config_row(ctx: Context, cfg: Config | None = None) -> Check | None:
    """An error row if gos.toml itself (``ctx.path``, and so the former-Plays record beside it)
    is in or around a folder GOS never writes in (D223): every Play of :func:`plays`, gos.toml's
    instances and, given ``cfg`` (the values being checked), its Play and instances as well."""
    found = plays(ctx)
    folders = doctor.guarded(ctx.cfg, found)
    if cfg is not None:
        folders += doctor.guarded(cfg, found)
    why = doctor.touch_problem(ctx.path, folders)
    if why is None:
        return None
    return Check(
        "paths.config",
        "Where gos.toml lives",
        ERROR,
        f"gos.toml ({ctx.path}) {why}: GOS writes gos.toml and its former-Plays record "
        "there, so start GOS with GOS_HOME (or --config) outside every Play and instance",
    )


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
