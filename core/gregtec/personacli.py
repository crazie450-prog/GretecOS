"""``gos persona [NAME]`` (WO-5.6, plan 7.11): list the persona packs, or switch.

- ``gos persona`` lists every pack (the shipped ones and the live vault's ``personas/``), marks
  the active one and prints each problem a pack has (a line left out, a pack that can't load);
  exit 1 when there is a problem, so a new pack made from ``_template.md`` can be checked.
- ``gos persona NAME`` (or ``random``) switches through the running core's ``POST
  /api/persona``, as the dashboard and the Discord bot do; with no core running it says so.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from gregtec import config, control
from gregtec.agents.personas import STATE_FILE, Personas

COMMANDS = ("persona",)


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser("persona", help="list the persona packs, or switch persona")
    p.add_argument("name", nargs="?", help="a pack's name (or random) to switch to")
    p.add_argument(
        "--config", type=Path, default=None, help="gos.toml (default: GOS_HOME's)"
    )


def run(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    if args.name:
        return _switch(cfg, args.name, out)
    vault = cfg.gos_home / "vault"
    personas = Personas(
        vault if vault.is_dir() else None,
        default=cfg.persona_default,
        state=cfg.data_dir / STATE_FILE,
    )
    packs, problems = personas.packs()
    active = personas.active
    for name, pack in sorted(packs.items()):
        mark = "*" if active is not None and name == active.id else " "
        voice = ", ".join(pack.voices) or "the engine's own voice"
        print(f"{mark} {name:<16} {pack.name} ({pack.source}; {voice})", file=out)
    for problem in problems:
        print(f"  problem: {problem}", file=out)
    if not packs:
        print("No persona pack loads.", file=out)
        return 1
    return 1 if problems else 0


def _switch(cfg: config.Config, name: str, out: TextIO) -> int:
    core = control.running(cfg.run_dir / control.CORE_FILE, control.CORE_SERVICE)
    if core is None:
        print("The core isn't running: start it (gos serve), then switch.", file=out)
        return 1
    try:
        result = control.call(
            core["port"], "POST", "/api/persona", token=core["token"], body={"id": name}
        )
    except control.ControlError as exc:
        print(str(exc), file=out)
        return 1
    persona = result.get("persona") or {}
    print(f"Persona: {persona.get('name')} ({persona.get('id')}).", file=out)
    return 0
