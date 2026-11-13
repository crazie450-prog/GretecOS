r"""Install GregtecLink with the listener and client modules into Play or Lab (WO-8.1, D287).

Clayton runs this himself from GOS_APP, with Play (or Lab) closed and the core running:

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\install_gregteclink.py
    ... install_gregteclink.py --apply [--instance play|lab] [--config PATH]

Without ``--apply`` it prints what it would do and writes nothing. With it:

1. The core issues the instance's token (``gregteclink-play`` or ``gregteclink-lab``, D285; it
   replaces an earlier one, whose link the core closes) and the script writes it to
   ``GOS_HOME\config\gregteclink-<instance>.token``, where gos-dev can't read it (D82 gives
   gos-dev ``data``, ``vault`` and ``logs`` only).
2. The jar ``gos deploy`` built (``GOS_APP\current\mod-dist``, checked against the SHA256 in
   ``release.json``, as sync checks Export's) goes into the instance's ``mods\``, after every other
   ``mods\gregteclink*.jar`` there is deleted.
3. ``config\gregteclink.cfg`` gets the listener and client modules on and export off, the core's
   port and the token file; a feed turned off in the file before stays off.

Refused, writing nothing: as gos-dev, while any GTNH client runs, an instance that isn't a plain
Prism instance, Lab at Play's folder, Lab without GregtecLink in ``[sync.lab] exclude`` (the
mirror from Play would delete it or bring Play's token file), no deployed jar, or no core.
Every file is written to a new temp file and renamed into place (:func:`gregtec.sync._write`).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from gregtec import config, playhooks, prism, sync
from gregtec.gamestate.safe_open import read_bytes

ROLES = ("play", "lab")
TOKEN_FILE = "gregteclink-{role}.token"
FEEDS = ("position", "target", "chat", "notices")
_OFF = re.compile(
    r"^\s*B:\"?(position|target|chat|notices)\"?\s*=\s*false\s*$",
    re.IGNORECASE | re.MULTILINE,
)
CoreCall = Callable[[config.Config, str, str], Any]


@dataclass
class Plan:
    role: str
    game: Path
    token_file: Path
    jar_source: Path
    jar: Path
    jar_data: bytes
    commit: str
    delete: list[Path] = field(default_factory=list)
    config_path: Path = Path()
    config_text: str = ""


def token_name(role: str) -> str:
    return f"gregteclink-{role}"


def config_text(role: str, port: int, token_file: Path, off: Sequence[str]) -> str:
    """The cfg the client module reads (mod/README.md, ClientConfig)."""
    feeds = "\n".join(f"    B:{f}={'false' if f in off else 'true'}" for f in FEEDS)
    return (
        f"# GregtecLink in GTNH-{role.capitalize()}: the listener and client modules (WO-8.1, D287).\n"
        "# install_gregteclink.py writes this file; a feed set to false here stays off when it\n"
        "# runs again. The core's token is in the file token_file names.\n"
        "\n"
        "modules {\n"
        "    B:client=true\n"
        "    B:export=false\n"
        "    B:listener=true\n"
        "}\n"
        "\n"
        "client {\n"
        f"{feeds}\n"
        f"    I:port={port}\n"
        f"    S:token_file={token_file.as_posix()}\n"
        "}\n"
    )


def feeds_off(path: Path) -> list[str]:
    """The feeds an existing cfg turns off."""
    try:
        text = read_bytes(path).decode("utf-8", "replace")
    except OSError:
        return []
    return sorted({m.lower() for m in _OFF.findall(text)})


def plan(
    cfg: config.Config,
    role: str,
    clients: playhooks.Clients = prism.running_clients,
) -> tuple[Plan | None, list[str]]:
    """What ``--apply`` would do, or the refusals. Reads only."""
    refusals: list[str] = []
    if role not in ROLES:
        return None, [f"--instance is play or lab, not {role}"]
    if config.running_as_dev():
        refusals.append(
            f"install_gregteclink.py runs as Clayton, never as {config.DEV_ACCOUNT}"
        )
    try:
        running = clients(cfg)
    except OSError as exc:
        refusals.append(f"can't tell whether a GTNH client runs ({exc})")
    else:
        if running:
            names = ", ".join(cfg.instance(r).name for r in running)
            refusals.append(f"{names} is running; close it")
    label = "Play" if role == "play" else "lab"
    found = sync._instance(cfg.instance(role), label, refusals)
    if role == "lab" and found is not None:
        if sync._overlap(found[0], cfg.instance("play")):
            refusals.append(f"lab: {found[0]} is Play, is inside Play or holds it")
        patterns = cfg.sync_exclude.get("lab", ())
        for rel in ("mods/gregteclink-x.jar", sync.LINK_CONFIG):
            if not sync.excluded(rel, patterns):
                refusals.append(
                    f"lab: {rel.replace('-x.jar', '*')} isn't in [sync.lab] exclude, so "
                    "sync would overwrite Lab's GregtecLink with Play's"
                )
                break
    jar = sync.deployed_jar(cfg.gos_app, role, refusals)
    if jar is None and not refusals:
        refusals.append(
            f"no GregtecLink jar is deployed in {cfg.gos_app}: run gos deploy first"
        )
    if refusals or found is None or jar is None:
        return None, refusals
    game = found[1]
    source, commit, data = jar
    mods = game / "mods"
    jars = sorted(
        (p for p in (mods.iterdir() if mods.is_dir() else ()) if _is_link_jar(p)),
        key=lambda p: p.name,
    )
    for p in [*jars, mods, game / "config"]:
        if p.exists() and sync._is_link(os.lstat(p)):
            refusals.append(f"{role}: {p} is a symlink or junction")
    if refusals:
        return None, refusals
    token_file = cfg.gos_home / "config" / TOKEN_FILE.format(role=role)
    target = mods / source.name
    config_path = game / sync.LINK_CONFIG
    return (
        Plan(
            role=role,
            game=game,
            token_file=token_file,
            jar_source=source,
            jar=target,
            jar_data=data,
            commit=commit,
            delete=[p for p in jars if p.name != source.name],
            config_path=config_path,
            config_text=config_text(
                role, cfg.core_port, token_file, feeds_off(config_path)
            ),
        ),
        [],
    )


def _is_link_jar(p: Path) -> bool:
    name = p.name.lower() if sys.platform == "win32" else p.name
    return name.startswith("gregteclink") and name.endswith(".jar")


def apply(cfg: config.Config, p: Plan, core_call: CoreCall) -> None:
    """Issue the token, then write the token file, the jar and the cfg."""
    result = core_call(cfg, "POST", f"/api/tokens/{token_name(p.role)}")
    token = result.get("token") if isinstance(result, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("the core issued no token")
    p.token_file.parent.mkdir(parents=True, exist_ok=True)
    sync._write(p.token_file, (token + "\n").encode())
    if sys.platform != "win32":
        os.chmod(
            p.token_file, 0o600
        )  # on Windows GOS_HOME's ACL keeps gos-dev out (D82)
    for old in p.delete:
        old.unlink()
    p.jar.parent.mkdir(exist_ok=True)
    sync._write(p.jar, p.jar_data)
    p.config_path.parent.mkdir(exist_ok=True)
    sync._write(p.config_path, p.config_text.encode())


def _core_call(cfg: config.Config, method: str, path: str) -> Any:  # noqa: ANN401 - JSON
    from gregtec import servicecli

    return servicecli._core_call(cfg, method, path)


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    clients: playhooks.Clients = prism.running_clients,
    core_call: CoreCall = _core_call,
) -> int:
    parser = argparse.ArgumentParser(
        prog="install_gregteclink.py",
        description="Install GregtecLink's listener and client modules into Play or Lab.",
    )
    parser.add_argument("--instance", choices=ROLES, default="play")
    parser.add_argument("--apply", action="store_true", help="write (default: show)")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    found, refusals = plan(cfg, args.instance, clients)
    if found is None:
        print("Refused; nothing was written:", file=out)
        for r in refusals:
            print(f"  - {r}", file=out)
        return 1
    print(f"GregtecLink {found.commit} into {found.game}:", file=out)
    print(f"  token: {token_name(found.role)} -> {found.token_file}", file=out)
    for old in found.delete:
        print(f"  delete {old}", file=out)
    print(f"  write {found.jar}", file=out)
    print(f"  write {found.config_path} (listener and client on)", file=out)
    if not args.apply:
        print("Nothing written; run again with --apply.", file=out)
        return 0
    try:
        apply(cfg, found, core_call)
    except (OSError, RuntimeError, SystemExit) as exc:
        print(f"Failed: {exc}", file=out)
        return 1
    print(
        "Done. Start the game; the client module connects to the core by itself.",
        file=out,
    )
    return 0
