"""``gos doctor`` (WO-0.2): a read-only survey of the Prism instances on this PC.

For each instance it reports whether it is GTNH, the pack version, the Java build (Java 8 or
Java 17+; V01, D68) and the Java it runs on, the game folder (``minecraft`` or ``.minecraft``;
V02) and the mod list with SHA-256 hashes. It writes ``<GOS_HOME>/data/env.json`` and prints the
VERIFY list (plan section 13.2).

It never writes inside a Prism folder: every game file is read through ``safe_open``.
Formats are those of the GTNH 2.9.0 Prism zips and Prism Launcher 11.1.1.
"""

from __future__ import annotations

import configparser
import datetime as dt
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import TextIO

from gregtec.config import PACK_PIN, Config
from gregtec.db import access
from gregtec.gamestate.safe_open import read_text, sha256_file
from gregtec.packupdate import gtpin

PLAY = "GTNH-Play"  # gos.toml [paths] play (Appendix C)
JAVA8 = "java8"
JAVA17 = "java17+"
UNKNOWN = "unknown"
NOT_APPLICABLE = "n/a"  # java_build of an instance that isn't Minecraft 1.7.10
LWJGL3_UIDS = {
    "org.lwjgl3",
    "me.eigenraven.lwjgl3ify.forgepatches",
    "me.eigenraven.lwjgl3ify.launchargs",
}
GTNH_COMPONENTS = {"net.minecraft": "1.7.10", "net.minecraftforge": "10.13.4.1614"}
MOD_SUFFIXES = (".jar", ".zip", ".jar.disabled", ".zip.disabled")


@dataclass(frozen=True)
class VerifyItem:
    id: str
    item: str
    how: str
    wo: str


@dataclass
class Mod:
    file: str  # relative to the mods folder, forward slashes
    enabled: bool
    size: int
    sha256: str | None


@dataclass
class Instance:
    folder_name: str  # Prism's instance id; name= changes on import, so this is the key
    name: str
    folder: str
    gtnh: bool
    game_dir: str | None  # "minecraft", ".minecraft" or None
    pack_version: str | None
    pack_version_sources: dict[str, str]
    java_build: str  # JAVA8, JAVA17, UNKNOWN or NOT_APPLICABLE
    java_path: str | None
    java_version: str | None
    java_source: (
        str  # "instance" (OverrideJavaLocation) or "global" (prismlauncher.cfg)
    )
    components: list[str]
    mods: list[Mod] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class PrismRoot:
    path: str
    portable: bool
    instances_dir: str
    instances: list[Instance] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # informational; not problems


def load_verify_list() -> list[VerifyItem]:
    text = resources.files("gregtec").joinpath("verify.tsv").read_text(encoding="utf-8")
    rows = [
        line.split("\t")
        for line in text.splitlines()
        if line and not line.startswith("#")
    ]
    return [VerifyItem(*row) for row in rows[1:]]


def _registry_install_dir() -> Path | None:
    """Where Prism's Windows installer put it (HKCU\\Software\\PrismLauncher InstallDir)."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrismLauncher") as key:
            value, _kind = winreg.QueryValueEx(key, "InstallDir")
    except OSError:
        return None
    return Path(value)


def default_prism_roots() -> list[Path]:
    """Prism's data folders, in the order Prism itself would pick them."""
    roots: list[Path] = []
    if env := os.environ.get("PRISMLAUNCHER_DATA_DIR"):
        roots.append(Path(env))
    if sys.platform == "win32":
        roots.append(
            Path(r"C:\Games\PrismLauncher")
        )  # the portable install (plan section 4.3)
        if installed := _registry_install_dir():
            roots.append(installed)  # counts only if it is portable (see survey)
        if appdata := os.environ.get("APPDATA"):
            roots.append(Path(appdata) / "PrismLauncher")
    else:
        roots.append(Path.home() / ".local" / "share" / "PrismLauncher")
    return roots


def read_ini(path: Path) -> dict[str, str]:
    """Read a Qt-style INI file (prismlauncher.cfg, instance.cfg) into a flat dict.

    The GTNH zip ships a headerless instance.cfg; once Prism saves it, it has [General]. Keys in
    [General] (or outside any section) come back unprefixed. A UTF-8 BOM is skipped, as Qt does.
    A file that doesn't parse raises ``ValueError``.
    """
    parser = configparser.RawConfigParser(strict=False)
    parser.optionxform = str  # keep key case
    text = read_text(path, encoding="utf-8-sig")
    if not text.lstrip().startswith("["):
        text = "[General]\n" + text
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ValueError(str(exc)) from exc
    values: dict[str, str] = {}
    for section in parser.sections():
        prefix = "" if section == "General" else f"{section}/"
        for key, value in parser.items(section):
            values[prefix + key] = _unquote(value)
    return values


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('\\"', '"')
    return value.replace("\\\\", "\\")  # Qt doubles backslashes


def detect_java_build(components: list[str]) -> str:
    """Java 17+ builds load LWJGL 3 through lwjgl3ify; Java 8 builds use LWJGL 2.

    These are the mmc-pack.json component uids of the GTNH 2.9.0 Prism zips. config/lwjgl3ify.cfg
    is no signal: both builds ship it.
    """
    uids = set(components)
    if uids & LWJGL3_UIDS:
        return JAVA17
    if "org.lwjgl" in uids and "net.minecraftforge" in uids:
        return JAVA8
    return UNKNOWN


def read_pack(folder: Path) -> dict[str, str]:
    """mmc-pack.json components as uid -> version."""
    pack = folder / "mmc-pack.json"
    if not pack.is_file():
        return {}
    data = json.loads(read_text(pack))
    return {
        str(c.get("uid", "")): str(c.get("version", ""))
        for c in data.get("components", [])
        if isinstance(c, dict)
    }


def read_components(folder: Path) -> list[str]:
    return list(read_pack(folder))


def _placeholder(version: str) -> bool:
    return (
        re.fullmatch(r"\d+\.\d+\.x", version) is not None
    )  # "2.9.x": the repo default


# Every pack-version signal the GTNH builder (DreamAssemblerXXL) writes into an instance.
PACK_VERSION_FILES = (
    (
        "dreamcraft.cfg",
        "config/GTNewHorizons/dreamcraft.cfg",
        r"^\s*S:ModPackVersion=(.*)$",
    ),
    (
        "DreamCoreMod.properties",
        "config/DreamCoreMod.properties",
        r"^displayedModpackVersion=(.*)$",
    ),
    (
        "mainmenu/version.txt",
        "config/txloader/load/mainmenu/version.txt",
        r"^GTNH (\S+)",
    ),
)


def read_pack_versions(game: Path, problems: list[str]) -> dict[str, str]:
    """Pack-version signals; a file that can't be read is added to ``problems``."""
    found: dict[str, str] = {}
    for label, rel, pattern in PACK_VERSION_FILES:
        path = game / rel
        try:
            m = (
                re.search(pattern, read_text(path), re.MULTILINE)
                if path.is_file()
                else None
            )
        except OSError as exc:
            problems.append(f"{rel} unreadable: {exc}")
            continue
        if m:
            found[label] = m[1].strip()
    return found


def list_mods(game: Path, hash_mods: bool, problems: list[str]) -> list[Mod]:
    """The archives FML 1.7.10 loads, enabled or .disabled: files directly in mods/ and in
    mods/1.7.10/. FML treats any other subfolder as a class folder, so jars in it never load.

    A mod file that can't be read is added to ``problems``; the rest are still listed.
    """
    mods_dir = game / "mods"
    paths: list[Path] = []
    for folder in (mods_dir, mods_dir / "1.7.10"):
        try:
            if folder.is_dir():
                paths.extend(folder.iterdir())
        except OSError as exc:
            problems.append(f"{folder.relative_to(game).as_posix()} unreadable: {exc}")
    mods = []
    for path in sorted(paths):
        rel = path.relative_to(mods_dir).as_posix()
        if not path.name.endswith(MOD_SUFFIXES):
            continue
        try:
            if not path.is_file():
                continue
            mod = Mod(
                file=rel,
                enabled=not path.name.endswith(".disabled"),
                size=path.stat().st_size,
                sha256=sha256_file(path) if hash_mods else None,
            )
        except OSError as exc:
            problems.append(f"mods/{rel} unreadable: {exc}")
            continue
        mods.append(mod)
    return mods


def read_instance(folder: Path, hash_mods: bool, prism_cfg: dict[str, str]) -> Instance:
    problems: list[str] = []
    try:
        cfg = read_ini(folder / "instance.cfg")  # a parse error arrives as ValueError
    except (OSError, ValueError) as exc:
        cfg = {}
        problems.append(f"instance.cfg unreadable: {exc}")
    # Prism's rule: minecraft/ unless only .minecraft/ exists (the GTNH zips use .minecraft/).
    try:
        game = next(
            (folder / d for d in ("minecraft", ".minecraft") if (folder / d).is_dir()),
            None,
        )
    except OSError as exc:  # Python 3.12's is_dir() re-raises EACCES
        game = None
        problems.append(f"game folder unreadable: {exc}")
    try:
        pack = read_pack(folder)
    except (OSError, ValueError) as exc:
        pack = {}
        problems.append(f"mmc-pack.json unreadable: {exc}")
    components = list(pack)
    versions = read_pack_versions(game, problems) if game else {}
    concrete = {v for v in versions.values() if not _placeholder(v)}
    if len(concrete) > 1:
        problems.append(f"pack version files disagree: {versions}")
    override = cfg.get("OverrideJavaLocation", "false").lower() == "true"

    def java_setting(key: str) -> str | None:
        # Prism: an overriding instance without its own key uses the global value.
        return (cfg if override and key in cfg else prism_cfg).get(key) or None

    # LWJGL 2 vs 3 tells Java 8 from 17+ only on Minecraft 1.7.10. With no mmc-pack.json the
    # version is unknown, and so is the build.
    minecraft = pack.get("net.minecraft")
    inst = Instance(
        folder_name=folder.name,
        name=cfg.get("name", folder.name),
        folder=str(folder),
        gtnh=all(pack.get(uid) == ver for uid, ver in GTNH_COMPONENTS.items())
        and "dreamcraft.cfg" in versions,
        game_dir=game.name if game else None,
        pack_version=next((v for v in versions.values() if not _placeholder(v)), None),
        pack_version_sources=versions,
        java_build=detect_java_build(components)
        if minecraft in (None, "1.7.10")
        else NOT_APPLICABLE,
        java_path=java_setting("JavaPath"),
        java_version=java_setting("JavaVersion"),
        java_source="instance" if override else "global",
        components=components,
        problems=problems,
    )
    if game is None:
        return inst  # not launched yet: Prism creates minecraft/ on first launch
    inst.mods = list_mods(game, hash_mods, inst.problems)
    lwjgl3ify_jar = any(
        m.enabled and Path(m.file).name.lower().startswith("lwjgl3ify-")
        for m in inst.mods
    )
    if inst.java_build == JAVA8 and lwjgl3ify_jar:
        inst.problems.append(
            "Java 8 components but an enabled lwjgl3ify jar: mixed build"
        )
    if inst.java_build == JAVA17 and inst.gtnh and not lwjgl3ify_jar:
        inst.problems.append(
            "Java 17+ components but no enabled lwjgl3ify jar: mixed build"
        )
    return inst


def _probe(check: Callable[[], bool]) -> bool | None:
    """Stat one of Prism's own files. None: this account may not look (gos-dev, plan 4.6)."""
    try:
        return check()
    except PermissionError:
        return None


def survey(roots: list[Path], hash_mods: bool = True) -> list[PrismRoot]:
    found: list[PrismRoot] = []
    seen: set[Path] = set()
    for root in roots:
        # A portable install keeps its data next to the exe, or in UserData\ beside it.
        userdata = _probe((root / "UserData").is_dir)
        portable_txt = _probe((root / "portable.txt").is_file)
        exe = _probe((root / "prismlauncher.exe").is_file)
        data = root / "UserData" if userdata else root
        unknown = None in (userdata, portable_txt, exe)
        # If we may not look, the data is still right here: call it portable.
        portable = data != root or bool(portable_txt) or unknown
        if not data.is_dir() or data.resolve() in seen:
            continue
        if exe and not portable:
            continue  # an installed copy: its data is in %APPDATA%\PrismLauncher
        seen.add(data.resolve())
        notes = []
        if unknown:
            notes.append(
                "can't see Prism's own files (running as gos-dev?); assumed portable"
            )
        settings = data / "prismlauncher.cfg"
        try:
            cfg = read_ini(settings) if settings.is_file() else {}
        except PermissionError:
            cfg = {}
            notes.append(
                "can't read Prism's own settings (running as gos-dev?); global Java unknown"
            )
        instances_dir = data / cfg.get("InstanceDir", "instances")
        prism = PrismRoot(
            path=str(data),
            portable=portable,
            instances_dir=str(instances_dir),
            notes=notes,
        )
        if instances_dir.is_dir():
            for folder in sorted(instances_dir.iterdir()):
                if folder.name == ".tmp":
                    continue
                try:
                    is_instance = (folder / "instance.cfg").is_file()
                except OSError:  # can't tell: read_instance reports what it can't read
                    is_instance = True
                if is_instance:
                    prism.instances.append(read_instance(folder, hash_mods, cfg))
        found.append(prism)
    return found


def find_play(roots: list[PrismRoot]) -> Instance | None:
    instances = [i for r in roots for i in r.instances]
    return next((i for i in instances if i.folder_name == PLAY), None) or next(
        (i for i in instances if i.name == PLAY), None
    )


def verify_status(item: VerifyItem, roots: list[PrismRoot]) -> str:
    instances = [i for r in roots for i in r.instances]
    play = find_play(roots)
    if item.id == "V01":
        if play is None:
            return f"open: no {PLAY} instance found yet"
        runtime = play.java_version or "Java version unknown until first launch"
        return f"{PLAY}: {play.java_build} build ({runtime})"
    if item.id == "V02":
        if not instances:
            return "open: no Prism instances found"
        return "; ".join(
            f"{i.folder_name}: {Path(i.folder) / (i.game_dir or '?')}"
            for i in instances
        )
    for word in ("Closed", "Dropped", "Resolved"):
        if item.item.startswith(f"**{word}**"):
            return word.lower()
    return f"open: {item.how} (WO-{item.wo})"


OK, WARN, ERROR = "ok", "warn", "error"  # a checklist row's status


@dataclass(frozen=True)
class Check:
    """One checklist row (``checklist``, the setup wizard's, WO-2.9): what was checked, how it
    went (ok, warn, error) and why."""

    id: str
    label: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
        }


def _instance_check(cfg: Config, role: str) -> Check:
    """One of Play, Export or Lab: there, GTNH, the pinned pack (read only, mods not hashed)."""
    name = getattr(cfg, role)
    folder = cfg.instance(role)
    label = f"{role.capitalize()} instance ({name})"
    try:
        there = (folder / "instance.cfg").is_file()
    except OSError as exc:
        return Check(f"instance.{role}", label, ERROR, f"can't look in {folder}: {exc}")
    if not there:
        # Play is the one GOS can't make; Export and Lab come from it (WO-0.3, WO-0.7).
        status = ERROR if role == "play" else WARN
        return Check(
            f"instance.{role}", label, status, f"no Prism instance at {folder}"
        )
    inst = read_instance(folder, False, {})
    notes = list(inst.problems)
    if not inst.gtnh:
        notes.append("not a GTNH instance (no GTNH components or dreamcraft.cfg)")
    elif inst.pack_version not in (None, PACK_PIN):
        notes.append(f"pack {inst.pack_version}; GOS is pinned to {PACK_PIN}")
    if notes:
        return Check(f"instance.{role}", label, WARN, "; ".join(notes))
    game = inst.game_dir or "not launched yet"
    return Check(
        f"instance.{role}",
        label,
        OK,
        f"GTNH {inst.pack_version or '?'}, {inst.java_build} build, game folder {game}",
    )


def folded(path: Path) -> Path:
    """``path`` resolved and case-folded: Windows' file names ignore case. Raises
    ``RuntimeError`` or ``OSError`` on a link loop; :func:`within` treats that as an overlap."""
    return Path(str(path.resolve()).casefold())


def within(path: Path, folder: Path) -> bool:
    """Whether ``path`` is ``folder`` or inside it (resolved, case ignored). A path that can't be
    resolved (a link loop) counts as inside, so a check refuses it instead of raising."""
    try:
        p, f = folded(path), folded(folder)
    except (OSError, RuntimeError):
        return True
    return p == f or f in p.parents


# What no setup or doctor code may open, probe or create anything in or around (D223): each
# folder with what it is, for the message.
Guarded = Sequence[tuple[str, Path]]


def guarded(cfg: Config, plays: Sequence[Path] = ()) -> list[tuple[str, Path]]:
    """Every Play (``cfg``'s and ``plays``: the running core's and the former Plays, D223),
    Prism's instances folder, Export and Lab: GOS's own files never go there."""
    return [
        *(("Play", p) for p in dict.fromkeys([cfg.instance("play"), *plays])),
        ("Prism's instances folder", cfg.prism_instances),
        ("Export", cfg.instance("export")),
        ("Lab", cfg.instance("lab")),
    ]


def touch_problem(path: Path, folders: Guarded) -> str | None:
    """Why GOS must not open, probe or create ``path`` (D223), or None if it may: ``path`` or its
    parent resolves (links and junctions followed, case ignored) into one of ``folders``, or
    ``path`` holds one. The one gate in front of every file of GOS's own that the wizard and
    ``gos doctor`` touch (the DB's GT pin, the GOS_HOME write probe, env.json)."""
    for label, folder in folders:
        if within(path, folder) or within(path.parent, folder) or within(folder, path):
            return f"overlaps {label} ({folder})"
    return None


def _writable(folder: Path, folders: Guarded) -> bool:
    """Whether this account can make a file in ``folder``: a temp file is made there and deleted
    at once, unless :func:`touch_problem` refuses the folder. ``os.access`` isn't enough: on
    Windows it reads only the read-only attribute, never the folder's ACL (the gos-dev account,
    WO-0.8)."""
    if touch_problem(folder / "gos-probe.tmp", folders) is not None:
        return False
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


def play_overlap(path: Path, plays: Sequence[Path]) -> Path | None:
    """The Play folder ``path`` is in or holds, if any: GOS never writes in Play (CLAUDE.md)."""
    return next((p for p in plays if within(path, p) or within(p, path)), None)


HOME_FOLDERS = ("data", "logs", "vault", "backups", "run")  # what GOS keeps in GOS_HOME


def home_problem(home: Path, folders: Guarded) -> str | None:
    """Why ``home`` can't be GOS_HOME, if it can't (D223): it, or its data, logs, vault, backups or
    run folder (resolved, so a link one level down counts), is in or around a guarded folder."""
    for path in (home, *(home / name for name in HOME_FOLDERS)):
        why = touch_problem(path, folders)
        if why is not None:
            where = "GOS_HOME" if path == home else f"GOS_HOME's {path.name} folder"
            return (
                f"{where} {why}: GOS writes in GOS_HOME, so it and its folders must resolve "
                "outside every Play and Prism's instances folder"
            )
    return None


_BAD_NAME = set('/\\:*?"<>|')


def name_problem(value: str) -> str | None:
    """Why ``value`` can't be an instance's folder name (Play, Export or Lab), if it can't."""
    if value in (".", "..") or any(c in _BAD_NAME for c in value):
        return "an instance's folder name in the instances folder, not a path"
    if value != value.rstrip(". "):
        # Windows drops a trailing dot or space: "GTNH-Play." is the folder GTNH-Play.
        return "a folder name can't end in a dot or a space"
    return None


def _instances_problem(cfg: Config, plays: Sequence[Path]) -> str | None:
    """Why Play, Export and Lab aren't three separate instances with Export and Lab clear of
    every Play, if they aren't: the names, then the folders resolved and case-folded, so a
    hand-edited ``../GTNH-Play`` or a link (a junction on Windows) to Play is Play (final check
    round 3, D167). ``gregtec.sync`` writes and deletes in Export and Lab."""
    for role in ("play", "export", "lab"):
        why = name_problem(getattr(cfg, role))
        if why is not None:
            return f"{role.capitalize()} ({getattr(cfg, role)}): {why}"
    if len({getattr(cfg, r).casefold() for r in ("play", "export", "lab")}) < 3:
        return "Play, Export and Lab must be three different instances"
    for role in ("export", "lab"):
        play = play_overlap(cfg.instance(role), plays)
        if play is not None:
            return (
                f"{role.capitalize()} ({cfg.instance(role)}) is Play's folder ({play}); GOS "
                f"writes to {role.capitalize()}, so it must never be Play"
            )
    export, lab = cfg.instance("export"), cfg.instance("lab")
    if within(export, lab) or within(lab, export):
        return f"Export ({export}) and Lab ({lab}) are the same folder"
    return None


def _home_check(home: Path, folders: Guarded) -> Check:
    label = f"GOS_HOME ({home})"
    # Before any probe: a check never makes a file in Play or another instance (D223).
    why = home_problem(home, folders)
    if why is not None:
        return Check("paths.gos_home", label, ERROR, why)
    if home.is_dir():
        if _writable(home, folders):
            return Check("paths.gos_home", label, OK, "there and writable")
        return Check("paths.gos_home", label, ERROR, "this account can't write there")
    if home.exists():
        return Check("paths.gos_home", label, ERROR, "that is a file, not a folder")
    parent = home.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if parent.is_dir() and _writable(parent, folders):
        return Check("paths.gos_home", label, OK, "not there yet: GOS makes it")
    return Check(
        "paths.gos_home", label, ERROR, f"can't be made: {parent} isn't writable"
    )


def checklist(cfg: Config, plays: Sequence[Path] = ()) -> list[Check]:
    """``gos doctor``'s checks of one config, as checklist rows (the setup wizard, WO-2.9): the
    Prism folders, Play, Export and Lab, GOS_APP, GOS_HOME and the DB's GT pin. Read only: game
    files go through ``safe_open`` and mods aren't hashed; the one write is a temp file made and
    deleted at once in GOS_HOME (or the folder it will be made in), to see that it is writable.
    Every file of GOS's own goes through :func:`touch_problem` first (D223): GOS_APP, or GOS_HOME
    or its data, logs, vault, backups or run folder, resolving in or around any Play (``cfg``'s
    and ``plays``: the running core's and the former Plays), Prism's instances folder, Export or
    Lab is an error, and then nothing there is probed or opened. So is Export or Lab at a Play
    folder, by name, resolved path or link, or a name that isn't a folder name."""
    plays = [cfg.instance("play"), *plays]
    rows: list[Check] = []
    for key, label, path in (
        ("paths.prism_root", "Prism folder", cfg.prism_root),
        ("paths.prism_instances", "Prism instances folder", cfg.prism_instances),
    ):
        found = path.is_dir()
        rows.append(
            Check(
                key,
                f"{label} ({path})",
                OK if found else ERROR,
                "found" if found else "no such folder",
            )
        )
    instances_problem = _instances_problem(cfg, plays)
    if instances_problem is not None:
        rows.append(
            Check("paths.instances", "Instance names", ERROR, instances_problem)
        )
    elif cfg.prism_instances.is_dir():
        rows += [_instance_check(cfg, role) for role in ("play", "export", "lab")]
    folders = guarded(cfg, plays)
    app_why = touch_problem(cfg.gos_app, folders)
    app = app_why is None and cfg.gos_app.is_dir()
    rows.append(
        Check(
            "paths.gos_app",
            f"GOS_APP ({cfg.gos_app})",
            ERROR if app_why is not None else OK if app else WARN,
            f"{app_why}: gos deploy writes in GOS_APP, so it must be outside every Play and "
            "Prism's instances folder (a sync of Export or Lab can delete what is there)"
            if app_why is not None
            else "found"
            if app
            else "no release installed there yet: Clayton's gos deploy makes it (D91)",
        )
    )
    rows.append(_home_check(cfg.gos_home, folders))
    rows.append(pin_check(cfg.gos_home, folders))
    return rows


def pin_check(home: Path, folders: Guarded) -> Check:
    """The DB's GT pin row (plan 0.4: a mismatch is a warning, the recipe data is stale). The DB
    is opened only when :func:`touch_problem` allows it, and then immutable, so not even SQLite's
    -wal and -shm files are made (D223)."""
    db = access.db_path(home)
    why = touch_problem(db, folders)
    if why is not None:
        return Check("db.gt_pin", "Recipe DB and GT pin", WARN, f"not read: {db} {why}")
    pin_ok, pin_line = gtpin.check_db(home)
    return Check("db.gt_pin", "Recipe DB and GT pin", OK if pin_ok else WARN, pin_line)


def write_env(roots: list[PrismRoot], gos_home: Path, verify: dict[str, str]) -> Path:
    data = gos_home / "data"
    data.mkdir(parents=True, exist_ok=True)
    env = {
        "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "prism_roots": [asdict(r) for r in roots],
        "verify": verify,
    }
    target = data / "env.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(env, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def main(
    prism_roots: list[Path] | None, gos_home: Path | None, hash_mods: bool, out: TextIO
) -> int:
    roots = survey(prism_roots or default_prism_roots(), hash_mods)
    home = gos_home or Path(os.environ.get("GOS_HOME") or Path.home() / "GregtecOS")
    healthy = bool(roots) and any(r.instances for r in roots)

    if not roots:
        print("No Prism folder found. Pass --prism-root DIR.", file=out)
    for root in roots:
        kind = "portable" if root.portable else "installed"
        print(f"Prism ({kind}): {root.path}", file=out)
        for note in root.notes:
            print(f"  note: {note}", file=out)
        if not root.instances:
            print(f"  no instances in {root.instances_dir}", file=out)
        for inst in root.instances:
            enabled = sum(m.enabled for m in inst.mods)
            # No game folder is "not launched yet" only if nothing failed to read.
            game = inst.game_dir or ("?" if inst.problems else "? (not launched yet)")
            print(
                f"  {inst.folder_name}\t{'GTNH' if inst.gtnh else 'not GTNH'}"
                f"\tpack={inst.pack_version or '?'}\tjava={inst.java_build}"
                f"\tgame={game}\tmods={enabled} enabled, "
                f"{len(inst.mods) - enabled} disabled",
                file=out,
            )
            print(
                f"    runs on: {inst.java_path or '?'} ({inst.java_version or 'version unknown'};"
                f" {inst.java_source} setting)",
                file=out,
            )
            for problem in inst.problems:
                healthy = False
                print(f"    problem: {problem}", file=out)

    # GOS's own files never go in or around an instance (D223): no DB read, no env.json.
    folders = [("Prism's instances folder", Path(r.instances_dir)) for r in roots]
    home_why = home_problem(home, folders)
    if home_why is not None:
        healthy = False
        print(f"\nproblem: {home_why}; nothing read or written there", file=out)
    else:
        pin_ok, pin_line = gtpin.check_db(home)  # WO-1.9, D148: loud on a mismatch
        healthy = healthy and pin_ok
        print(f"\n{pin_line}", file=out)

    verify = {v.id: verify_status(v, roots) for v in load_verify_list()}
    if home_why is None:
        env = write_env(roots, home, verify)
        print(f"\nWrote {env}", file=out)
    print("\nVERIFY (plan section 13.2)", file=out)
    for v in load_verify_list():
        print(f"  {v.id}\t{verify[v.id]}\t{v.item}", file=out)
    return 0 if healthy else 1
