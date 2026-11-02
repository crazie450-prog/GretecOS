"""``gos doctor`` (WO-0.2): a read-only survey of the Prism instances on this PC.

For each instance it reports the pack version (``dreamcraft.cfg`` ``S:ModPackVersion=``), the
Java build (Java 8 or Java 17+; V01, D68), the game folder (``minecraft`` or ``.minecraft``; V02)
and the mod list with SHA-256 hashes. It writes ``<GOS_HOME>/data/env.json`` and prints the
VERIFY list (plan section 13.2).

It never writes inside a Prism folder: every game file is read through ``safe_open``.
"""

from __future__ import annotations

import configparser
import datetime as dt
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import TextIO

from gregtec.gamestate.safe_open import read_text, sha256_file

PLAY = "GTNH-Play"  # gos.toml [paths] play (Appendix C)
JAVA8 = "java8"
JAVA17 = "java17+"
UNKNOWN = "unknown"


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
    name: str
    folder: str
    game_dir: str | None  # "minecraft", ".minecraft" or None
    pack_version: str | None
    java_build: str  # JAVA8, JAVA17 or UNKNOWN
    java_path: str | None
    components: list[str]
    mods: list[Mod] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class PrismRoot:
    path: str
    portable: bool
    instances_dir: str
    instances: list[Instance] = field(default_factory=list)


def load_verify_list() -> list[VerifyItem]:
    text = resources.files("gregtec").joinpath("verify.tsv").read_text(encoding="utf-8")
    rows = [
        line.split("\t")
        for line in text.splitlines()
        if line and not line.startswith("#")
    ]
    return [VerifyItem(*row) for row in rows[1:]]


def default_prism_roots() -> list[Path]:
    """The portable install from plan section 4.3, then Prism's normal per-user data folder."""
    if sys.platform == "win32":
        roots = [Path(r"C:\Games\PrismLauncher")]
        if appdata := os.environ.get("APPDATA"):
            roots.append(Path(appdata) / "PrismLauncher")
        return roots
    return [Path.home() / ".local" / "share" / "PrismLauncher"]


def read_ini(path: Path) -> dict[str, str]:
    """Read a Qt-style INI file (prismlauncher.cfg, instance.cfg) into a flat dict.

    Keys outside any section, and keys in [General], come back unprefixed.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keep key case
    text = read_text(path)
    if not text.lstrip().startswith("["):
        text = "[General]\n" + text
    parser.read_string(text)
    values: dict[str, str] = {}
    for section in parser.sections():
        prefix = "" if section == "General" else f"{section}/"
        for key, value in parser.items(section):
            values[prefix + key] = _unquote(value)
    return values


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def detect_java_build(components: list[str]) -> str:
    """Java 17+ builds load LWJGL 3 through lwjgl3ify; Java 8 builds use LWJGL 2.

    The component uids are those of the GTNH 2.9.0 Prism instance zips (mmc-pack.json).
    """
    uids = set(components)
    if "org.lwjgl3" in uids or any("lwjgl3ify" in uid for uid in uids):
        return JAVA17
    if "org.lwjgl" in uids and "net.minecraftforge" in uids:
        return JAVA8
    return UNKNOWN


def read_components(folder: Path) -> list[str]:
    pack = folder / "mmc-pack.json"
    if not pack.is_file():
        return []
    data = json.loads(read_text(pack))
    return [
        str(c.get("uid", "")) for c in data.get("components", []) if isinstance(c, dict)
    ]


def read_pack_version(game: Path) -> str | None:
    cfg = game / "config" / "GTNewHorizons" / "dreamcraft.cfg"
    if not cfg.is_file():
        return None
    for line in read_text(cfg).splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key == "S:ModPackVersion":
            return value.strip()
    return None


def list_mods(game: Path, hash_mods: bool) -> list[Mod]:
    mods_dir = game / "mods"
    if not mods_dir.is_dir():
        return []
    mods = []
    for path in sorted(p for p in mods_dir.rglob("*") if p.is_file()):
        mods.append(
            Mod(
                file=path.relative_to(mods_dir).as_posix(),
                enabled=not path.name.endswith(".disabled"),
                size=path.stat().st_size,
                sha256=sha256_file(path) if hash_mods else None,
            )
        )
    return mods


def read_instance(folder: Path, hash_mods: bool) -> Instance:
    cfg = read_ini(folder / "instance.cfg")
    game = next(
        (folder / d for d in ("minecraft", ".minecraft") if (folder / d).is_dir()), None
    )
    problems = []
    try:
        components = read_components(folder)
    except (OSError, ValueError) as exc:
        components = []
        problems.append(f"mmc-pack.json unreadable: {exc}")
    inst = Instance(
        name=cfg.get("name", folder.name),
        folder=str(folder),
        game_dir=game.name if game else None,
        pack_version=read_pack_version(game) if game else None,
        java_build=detect_java_build(components),
        java_path=cfg.get("JavaPath") or None,
        components=components,
        problems=problems,
    )
    if game is None:
        inst.problems.append("no minecraft or .minecraft folder")
    else:
        inst.mods = list_mods(game, hash_mods)
    return inst


def survey(roots: list[Path], hash_mods: bool = True) -> list[PrismRoot]:
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        settings = root / "prismlauncher.cfg"
        cfg = read_ini(settings) if settings.is_file() else {}
        instances_dir = root / cfg.get("InstanceDir", "instances")
        prism = PrismRoot(
            path=str(root),
            portable=(root / "portable.txt").is_file(),
            instances_dir=str(instances_dir),
        )
        if instances_dir.is_dir():
            for folder in sorted(
                p for p in instances_dir.iterdir() if (p / "instance.cfg").is_file()
            ):
                prism.instances.append(read_instance(folder, hash_mods))
        found.append(prism)
    return found


def verify_status(item: VerifyItem, roots: list[PrismRoot]) -> str:
    instances = [i for r in roots for i in r.instances]
    play = next((i for i in instances if i.name == PLAY), None)
    if item.id == "V01":
        return (
            f"{PLAY}: {play.java_build}"
            if play
            else f"open: no {PLAY} instance found yet"
        )
    if item.id == "V02":
        if not instances:
            return "open: no Prism instances found"
        return "; ".join(
            f"{i.name}: {Path(i.folder) / (i.game_dir or '?')}" for i in instances
        )
    for word in ("Closed", "Dropped", "Resolved"):
        if item.item.startswith(f"**{word}**"):
            return word.lower()
    return f"open: {item.how} (WO-{item.wo})"


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
        if not root.instances:
            print(f"  no instances in {root.instances_dir}", file=out)
        for inst in root.instances:
            enabled = sum(m.enabled for m in inst.mods)
            print(
                f"  {inst.name}\tpack={inst.pack_version or '?'}\tjava={inst.java_build}"
                f"\tgame={inst.game_dir or '?'}\tmods={enabled} enabled, "
                f"{len(inst.mods) - enabled} disabled",
                file=out,
            )
            for problem in inst.problems:
                healthy = False
                print(f"    problem: {problem}", file=out)

    verify = {v.id: verify_status(v, roots) for v in load_verify_list()}
    env = write_env(roots, home, verify)
    print(f"\nWrote {env}\n\nVERIFY (plan section 13.2)", file=out)
    for v in load_verify_list():
        print(f"  {v.id}\t{verify[v.id]}\t{v.item}", file=out)
    return 0 if healthy else 1
