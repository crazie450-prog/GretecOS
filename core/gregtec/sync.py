"""Mirror Play's mods/, config/ and scripts/ into Export and Lab (WO-0.3, plan section 5.1).

``scripts/sync_instances.py`` runs this (CLAUDE.md: the only way to write Export and Lab, with check_tweaks). For each target (GTNH-Export, GTNH-Lab):
new and changed files are copied, files Play no longer has are deleted, and anything that matches
the target's ``[sync.<target>] exclude`` list in gos.toml is never copied over, deleted or looked
inside. A file is unchanged when its size and mtime match Play's. With the same size and another
mtime, SHA-256 decides: the same content only gets Play's mtime, other content is copied. Copies
keep Play's mtime. Each game folder is ``minecraft`` unless only ``.minecraft`` exists (Prism).

Safety (CLAUDE.md, plan 4.6 residual risk, D82):

- Play is only read, through ``safe_open``: nothing in it is written, renamed or locked.
- Each target file is written to a new ``<name>.<random>.gos-tmp`` beside it, then
  ``os.replace``'d. The temp file is created with O_EXCL, so a link planted at a temp path
  (gos-dev can write Export and Lab) makes the write fail instead of writing through it.
- Everything is planned before anything is written, and any refusal writes nothing at all:
  a symlink, junction or other reparse point anywhere sync would traverse (both trees, the
  instance and game folders included); a hard link in a target, which is what Prism's "Link
  files" copy makes and would let the target write through to Play; a target that is Play or
  the other target, is inside it or holds it; a target that doesn't exist (sync never creates
  instances: use Prism's Copy instance).

Then, for Export only, sync installs GregtecLink from the release ``gos deploy`` built (D98):
GOS_APP's ``current/release.json`` names the jar in ``current/mod-dist/`` and its SHA256. The
jar's bytes must match it. Every other ``mods/gregteclink*.jar`` in Export is deleted first (so a
jar Export still holds stops the step before there are two), then the jar is written and
``config/gregteclink.cfg`` with the export module on and listener and client off, each through
the same O_EXCL temp file as the mirror. Both paths are in Export's exclude list, so the mirror
never copies, deletes or link-checks them. With no deployed jar this step is skipped with a note;
a jar that doesn't match ``release.json`` is a refusal.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from gregtec import config, deploy
from gregtec.gamestate.safe_open import read_bytes, sha256_file
from gregtec.tweaks import GAME_DIRS

MIRRORED = config.MIRRORED
TARGETS = ("export", "lab")
TMP_SUFFIX = ".gos-tmp"
IGNORE_CASE = sys.platform == "win32"  # file names and exclude patterns
LINK_JARS = "gregteclink*.jar"  # Export's mods/ jars that the deployed one replaces
LINK_JAR_NAME = re.compile(r"gregteclink-[A-Za-z0-9][A-Za-z0-9._+-]*\.jar")
LINK_CONFIG = "config/gregteclink.cfg"
# What GregtecLink's ModulesConfig reads; the modules block is mod/harness/config/gregteclink.cfg's.
EXPORT_CONFIG = b"""# GregtecLink in GTNH-Export: the export module only (D98).
# sync_instances.py rewrites this file at every sync; Play never gets it.

modules {
    B:client=false
    B:export=true
    B:listener=false
}
"""


def _fold(text: str) -> str:
    return text.lower() if IGNORE_CASE else text


def excluded(rel: str, patterns: Sequence[str]) -> bool:
    """Whether ``rel`` (posix, relative to the game folder) or a folder above it matches a pattern.

    Patterns are posix globs relative to the game folder, matched part by part with fnmatch, so
    ``*`` never crosses a ``/``: ``mods/*.jar`` doesn't match ``mods/1.7.10/x.jar``.
    """
    parts = _fold(rel).split("/")
    for pattern in patterns:
        pat = _fold(pattern).strip("/").split("/")
        if len(pat) <= len(parts) and all(
            fnmatch.fnmatchcase(part, p) for part, p in zip(parts, pat)
        ):
            return True
    return False


def _is_link(st: os.stat_result) -> bool:
    """A symlink (POSIX or Windows), a junction or any other Windows reparse point."""
    attributes = getattr(st, "st_file_attributes", 0)
    return stat.S_ISLNK(st.st_mode) or bool(
        attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


@dataclass(frozen=True)
class File:
    rel: str  # posix, relative to the game folder
    size: int
    mtime_ns: int
    atime_ns: int
    links: int


@dataclass
class Tree:
    """What sync sees of one game folder. Keys are ``rel``, case-folded on Windows."""

    files: dict[str, File] = field(default_factory=dict)
    dirs: dict[str, str] = field(default_factory=dict)  # in walk order: parents first
    excluded: dict[str, str] = field(default_factory=dict)
    refusals: list[str] = field(default_factory=list)


def _walk(
    game: Path, rel: str, patterns: Sequence[str], tree: Tree, label: str
) -> None:
    if excluded(rel, patterns):
        tree.excluded[_fold(rel)] = rel
        return
    path = game / rel
    try:
        st = os.lstat(path)
        link = _is_link(st)  # checked before listing: never look through a link
        names = (
            sorted(os.listdir(path)) if stat.S_ISDIR(st.st_mode) and not link else []
        )
    except OSError as exc:
        tree.refusals.append(f"{label}: can't read {path}: {exc}")
        return
    if link:
        tree.refusals.append(f"{label}: {path} is a symlink or junction")
    elif stat.S_ISDIR(st.st_mode):
        tree.dirs[_fold(rel)] = rel
        for name in names:
            _walk(game, f"{rel}/{name}", patterns, tree, label)
    elif stat.S_ISREG(st.st_mode):
        tree.files[_fold(rel)] = File(
            rel, st.st_size, st.st_mtime_ns, st.st_atime_ns, st.st_nlink
        )
    else:
        tree.refusals.append(f"{label}: {path} is not a file or folder")


def scan(game: Path, roots: Sequence[str], patterns: Sequence[str], label: str) -> Tree:
    tree = Tree()
    for root in roots:
        if os.path.lexists(game / root):
            _walk(game, root, patterns, tree, label)
    return tree


@dataclass
class Plan:
    role: str
    source: Path  # Play's game folder
    game: Path  # the target's game folder
    delete: list[Path] = field(default_factory=list)
    rmdir: list[Path] = field(default_factory=list)  # deepest first
    mkdir: list[Path] = field(default_factory=list)  # parents first
    copy: list[tuple[File, Path]] = field(default_factory=list)  # (Play file, target)
    update: list[tuple[File, Path]] = field(default_factory=list)
    retime: list[tuple[File, Path]] = field(default_factory=list)  # same bytes
    excluded: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    install: Install | None = None  # Export only

    def empty(self) -> bool:
        return not (
            self.delete
            or self.rmdir
            or self.mkdir
            or self.copy
            or self.update
            or self.retime
        )


def _instance(path: Path, label: str, refusals: list[str]) -> tuple[Path, Path] | None:
    """(instance folder, game folder), or None after saying why in ``refusals``."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        hint = (
            "check [paths] in gos.toml"
            if label == "Play"
            else "create it with Prism's Copy instance of Play"
        )
        refusals.append(f"{label}: {path} doesn't exist ({hint})")
        return None
    if _is_link(st):
        refusals.append(f"{label}: {path} is a symlink or junction")
        return None
    if not (path / "instance.cfg").is_file():
        refusals.append(f"{label}: {path} is not a Prism instance (no instance.cfg)")
        return None
    game = next((path / d for d in GAME_DIRS if (path / d).is_dir()), None)
    if game is None:
        refusals.append(f"{label}: {path} has no minecraft or .minecraft folder")
        return None
    if _is_link(os.lstat(game)):
        refusals.append(f"{label}: {game} is a symlink or junction")
        return None
    return path, game


def _overlap(a: Path, b: Path) -> bool:
    a, b = (Path(os.path.normcase(p.resolve())) for p in (a, b))
    return a == b or a in b.parents or b in a.parents


def plan_target(
    role: str, source: Path, game: Path, patterns: Sequence[str], refusals: list[str]
) -> Plan:
    plan = Plan(role, source, game)
    roots = [r for r in MIRRORED if os.path.lexists(source / r)]
    plan.notes = [
        f"Play has no {r}/ folder, so {r}/ is left as it is"
        for r in MIRRORED
        if r not in roots
    ]
    src = scan(source, roots, patterns, "Play")
    dst = scan(game, roots, patterns, role)
    refusals.extend(src.refusals + dst.refusals)
    refusals.extend(
        f"{role}: {game / f.rel} is a hard link (made by Prism's 'Link files' copy?); "
        "re-create the instance with a plain Copy"
        for f in dst.files.values()
        if f.links > 1
    )
    for key, f in src.files.items():
        d = dst.files.get(key)
        if d is None:
            plan.copy.append((f, game / f.rel))
        elif d.size != f.size:
            plan.update.append((f, game / d.rel))
        elif d.mtime_ns != f.mtime_ns:
            same = sha256_file(source / f.rel) == sha256_file(game / d.rel)
            (plan.retime if same else plan.update).append((f, game / d.rel))
    plan.delete = [game / d.rel for key, d in dst.files.items() if key not in src.files]
    plan.mkdir = [game / rel for key, rel in src.dirs.items() if key not in dst.dirs]
    plan.rmdir = [
        game / rel
        for key, rel in reversed(dst.dirs.items())
        if key not in src.dirs
        and not any(e.startswith(key + "/") for e in dst.excluded)
    ]
    plan.excluded = sorted({**src.excluded, **dst.excluded}.values())
    return plan


@dataclass(frozen=True)
class Install:
    """GregtecLink from GOS_APP's deployed release into Export's game folder (D98)."""

    source: Path  # GOS_APP\current\mod-dist\<jar>
    commit: str
    data: bytes  # the jar's bytes, already checked against release.json's SHA256
    jar: Path | None  # Export's mods/<jar> to write; None if it already has these bytes
    delete: tuple[Path, ...]  # Export's other mods/gregteclink*.jar
    config: Path | None  # config/gregteclink.cfg to write; None if it already says this


def _same(path: Path, data: bytes) -> bool:
    return path.is_file() and not _is_link(os.lstat(path)) and read_bytes(path) == data


def plan_install(
    plan: Plan, gos_app: Path, patterns: Sequence[str], refusals: list[str]
) -> None:
    """Set ``plan.install`` (Export's plan), or add a note or a refusal. Reads only."""
    release = gos_app / deploy.CURRENT
    record_path = release / deploy.RECORD
    try:
        record = json.loads(read_bytes(record_path))
    except FileNotFoundError:
        record = None
    except (OSError, ValueError) as exc:
        refusals.append(f"export: can't read {record_path}: {exc}")
        return
    entry = record.get("gregteclink") if isinstance(record, dict) else None
    if entry is None:
        plan.notes.append(
            f"no GregtecLink jar is deployed in {gos_app} (gos deploy builds it), so "
            "Export's GregtecLink is left as it is"
        )
        return
    name = entry.get("jar") if isinstance(entry, dict) else None
    want = entry.get("sha256") if isinstance(entry, dict) else None
    if not (isinstance(name, str) and LINK_JAR_NAME.fullmatch(name)):
        refusals.append(f"export: {record_path} names no GregtecLink jar: {entry!r}")
        return
    for rel in (f"mods/{name}", LINK_CONFIG):
        if not excluded(rel, patterns):  # the mirror would delete it again
            refusals.append(f"export: {rel} isn't in [sync.export] exclude")
            return
    source = release / deploy.MOD_DIST / name
    try:
        data = read_bytes(source)
    except OSError as exc:
        refusals.append(f"export: can't read the deployed jar {source}: {exc}")
        return
    if hashlib.sha256(data).hexdigest() != want:
        refusals.append(
            f"export: {source} doesn't match the SHA256 in {record_path}; deploy again"
        )
        return
    mods = plan.game / "mods"
    jars = sorted(
        (
            p
            for p in (mods.iterdir() if mods.is_dir() else ())
            if fnmatch.fnmatchcase(_fold(p.name), LINK_JARS)
        ),
        key=lambda p: p.name,
    )
    target = next((p for p in jars if _fold(p.name) == _fold(name)), mods / name)
    config_path = plan.game / LINK_CONFIG
    plan.install = Install(
        source=source,
        commit=str(record.get("commit", "?")),
        data=data,
        jar=None if _same(target, data) else target,
        delete=tuple(p for p in jars if p != target),
        config=None if _same(config_path, EXPORT_CONFIG) else config_path,
    )


def plan_all(cfg: config.Config, roles: Sequence[str]) -> tuple[list[Plan], list[str]]:
    """Plans for ``roles``, and every refusal. Reads only."""
    refusals: list[str] = []
    play = _instance(cfg.instance("play"), "Play", refusals)
    targets = []
    for role in roles:
        found = _instance(cfg.instance(role), role, refusals)
        if found is None:
            continue
        if play is not None and _overlap(found[0], play[0]):
            refusals.append(f"{role}: {found[0]} is Play, is inside Play or holds it")
            continue
        # Against every configured target, not just those in ``roles``: ``--only lab`` with
        # lab = Export's folder would mirror into Export without Export's excludes.
        clash = [
            o for o in TARGETS if o != role and _overlap(found[0], cfg.instance(o))
        ]
        if clash:
            refusals.append(
                f"{role}: {found[0]} is the {clash[0]} instance, is inside it or holds it"
            )
            continue
        targets.append((role, found[1]))
    if play is None:
        return [], refusals
    plans = [
        plan_target(role, play[1], game, cfg.sync_exclude.get(role, ()), refusals)
        for role, game in targets
    ]
    for plan in plans:
        if plan.role == "export":
            plan_install(plan, cfg.gos_app, cfg.sync_exclude["export"], refusals)
    return plans, refusals


def plan_lab_to_export(cfg: config.Config) -> tuple[Plan | None, list[str]]:
    """Export's plan with **Lab** as the source, for the pack-update playbook's Lab export
    (WO-1.9, D148): Lab's mods/, config/ and scripts/ mirrored into Export, never touching what
    Export's or Lab's exclude list matches (Lab's test-only files stay out of Export), then
    GregtecLink installed as for every export. Play is never touched; Lab is only read. The
    next ``gos export run`` mirrors Play into Export again. Reads only; (None, refusals) when
    anything is refused."""
    refusals: list[str] = []
    play = _instance(cfg.instance("play"), "Play", refusals)
    lab = _instance(cfg.instance("lab"), "lab", refusals)
    export = _instance(cfg.instance("export"), "export", refusals)
    if play is None or lab is None or export is None:
        return None, refusals
    for a, b, what in (
        (export, play, "Play"),
        (export, lab, "Lab"),
        (lab, play, "Play"),
    ):
        if _overlap(a[0], b[0]):
            refusals.append(f"{a[0]} is {what}, is inside it or holds it")
    if refusals:
        return None, refusals
    patterns = tuple(
        dict.fromkeys([*cfg.sync_exclude["export"], *cfg.sync_exclude.get("lab", ())])
    )
    plan = plan_target("export", lab[1], export[1], patterns, refusals)
    plan_install(plan, cfg.gos_app, cfg.sync_exclude["export"], refusals)
    return (None if refusals else plan), refusals


def apply(plan: Plan) -> None:
    for path in plan.delete:
        path.unlink()
    for path in plan.rmdir:
        path.rmdir()
    for path in plan.mkdir:
        path.mkdir()
    for f, target in plan.copy + plan.update:
        _write(target, read_bytes(plan.source / f.rel), (f.atime_ns, f.mtime_ns))
    for f, target in plan.retime:
        os.utime(target, ns=(f.atime_ns, f.mtime_ns))


def _write(target: Path, data: bytes, times: tuple[int, int] | None = None) -> None:
    """Write ``data`` to a new temp file beside ``target``, then rename it onto ``target``.

    mkstemp creates the temp file with O_EXCL under a random name, so it never opens a file or
    link that is already there. A rename replaces a link at ``target`` itself, never its target.
    """
    fd, name = tempfile.mkstemp(
        dir=target.parent, prefix=target.name + ".", suffix=TMP_SUFFIX
    )
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(data)
        if times is not None:
            os.utime(name, ns=times)
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def apply_install(plan: Plan) -> None:
    """Write ``plan.install`` into Export. Never Lab or Play: anything else raises first."""
    install = plan.install
    if install is None:
        return
    paths = [p for p in (install.jar, install.config, *install.delete) if p is not None]
    allowed = {plan.game / "mods", (plan.game / LINK_CONFIG).parent}
    if (
        plan.role != "export"
        or _overlap(plan.game, plan.source)
        or any(p.parent not in allowed for p in paths)
    ):
        raise ValueError(f"GregtecLink is installed into Export only, not {plan.game}")
    # Old jars first: if one is locked (Export running), Export never has two GregtecLinks.
    for path in install.delete:
        path.unlink()
    if install.jar is not None:
        install.jar.parent.mkdir(exist_ok=True)
        _write(install.jar, install.data)
    if install.config is not None:
        install.config.parent.mkdir(exist_ok=True)
        _write(install.config, EXPORT_CONFIG)


def report_install(plan: Plan, out: TextIO) -> None:
    install = plan.install
    if install is None:
        return
    print(
        f"  gregteclink: {install.source.name} from {install.source.parent.parent} "
        f"(release {install.commit[:12]}), SHA256 matches",
        file=out,
    )
    lines = [
        *(("delete", p, "") for p in install.delete),
        *(("install", p, "") for p in (install.jar,) if p is not None),
        *(
            ("write", p, " (export module on, listener and client off)")
            for p in (install.config,)
            if p is not None
        ),
    ]
    for verb, path, why in lines:
        print(f"  {verb:<7} {path.relative_to(plan.game).as_posix()}{why}", file=out)
    if not lines:
        print("  gregteclink: jar and config already installed", file=out)


def report(plan: Plan, out: TextIO) -> None:
    print(f"{plan.role}: {plan.game}", file=out)
    for note in plan.notes:
        print(f"  note: {note}", file=out)
    lines = [
        *(("delete", p) for p in plan.delete),
        *(("rmdir", p) for p in plan.rmdir),
        *(("mkdir", p) for p in plan.mkdir),
        *(("copy", t) for _, t in plan.copy),
        *(("update", t) for _, t in plan.update),
        *(("retime", t) for _, t in plan.retime),
    ]
    for verb, path in lines:
        print(f"  {verb:<7} {path.relative_to(plan.game).as_posix()}", file=out)
    if plan.excluded:
        print(
            f"  excluded (never copied or deleted): {', '.join(plan.excluded)}",
            file=out,
        )
    if plan.empty():
        print(f"  nothing to do ({len(plan.excluded)} excluded)", file=out)
    else:
        print(
            f"  copied {len(plan.copy)}, updated {len(plan.update)}, "
            f"retimed {len(plan.retime)}, deleted {len(plan.delete)}, "
            f"folders +{len(plan.mkdir)} -{len(plan.rmdir)}, "
            f"excluded {len(plan.excluded)}",
            file=out,
        )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--only", choices=TARGETS, help="sync just this target")
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would change; write nothing"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
    )


def run(args: argparse.Namespace, out: TextIO) -> int:
    """0: synced (or nothing to do); 1: refused or a write failed; 2: bad gos.toml."""
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        plans, refusals = plan_all(cfg, [args.only] if args.only else TARGETS)
    except OSError as exc:  # e.g. a file went away mid-scan; planning wrote nothing
        plans, refusals = [], [f"can't read: {exc}"]
    if refusals:
        print("Refused, nothing written:", file=out)
        for refusal in refusals:
            print(f"  {refusal}", file=out)
        return 1
    print(f"Play: {plans[0].source}", file=out)
    if args.dry_run:
        print("Dry run: nothing is written.", file=out)
    for p in plans:
        report(p, out)
        report_install(p, out)
        if args.dry_run:
            continue
        try:
            apply(p)
            apply_install(p)  # after the mirror (D98)
        except OSError as exc:
            print(
                f"Write failed: {exc}\n{p.role} is part synced. Close its game, then "
                "run sync again.",
                file=out,
            )
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sync_instances.py",
        description="Mirror mods/, config/ and scripts/ from GTNH-Play to GTNH-Export "
        "and GTNH-Lab (plan 5.1). Play is only read.",
    )
    add_arguments(parser)
    return run(parser.parse_args(argv), sys.stdout)
