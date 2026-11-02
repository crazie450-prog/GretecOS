"""The ``gos`` command (Appendix B). WO-0.2 adds ``gos doctor``; later WOs add the rest."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from gregtec import doctor
from gregtec.vault import deploy as vault_deploy


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gos", description="Gregtec OS")
    commands = parser.add_subparsers(dest="command", required=True)

    doc = commands.add_parser(
        "doctor",
        help="find the Prism instances and report pack version, Java build and mods",
        description=(
            "Read-only: finds Prism instances, reads each one's pack version, Java build "
            "and mod list with hashes, writes <GOS_HOME>/data/env.json and prints the "
            "VERIFY list (plan section 13.2)."
        ),
    )
    doc.add_argument(
        "--prism-root",
        action="append",
        type=Path,
        metavar="DIR",
        help="Prism data folder to search (repeatable). Default: the portable install "
        "in C:\\Games\\PrismLauncher, then %%APPDATA%%\\PrismLauncher.",
    )
    doc.add_argument(
        "--gos-home",
        type=Path,
        default=None,
        help="where data/env.json goes. Default: $GOS_HOME, else ~/GregtecOS.",
    )
    doc.add_argument(
        "--no-hash", action="store_true", help="list mods without hashing them (faster)"
    )

    vault = commands.add_parser("vault", help="vault tasks")
    vault_cmds = vault.add_subparsers(dest="vault_command", required=True)
    dep = vault_cmds.add_parser(
        "deploy",
        help="create the vault from vault-template/, or re-deploy its _gos/",
        description=(
            "First run copies vault-template/ and runs git init. Later runs mirror only "
            "_gos/ (repo-owned skills, agents, schema) and add missing folders; notes and "
            "me/ are never touched."
        ),
    )
    dep.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="the live vault. Default: $GOS_HOME/vault, else ~/GregtecOS/vault.",
    )
    dep.add_argument(
        "--template",
        type=Path,
        default=vault_deploy.DEFAULT_TEMPLATE,
        help=argparse.SUPPRESS,
    )

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor.main(
            prism_roots=args.prism_root,
            gos_home=args.gos_home,
            hash_mods=not args.no_hash,
            out=sys.stdout,
        )
    if args.command == "vault" and args.vault_command == "deploy":
        home = Path(os.environ.get("GOS_HOME") or Path.home() / "GregtecOS")
        target = args.vault or home / "vault"
        print(f"{vault_deploy.deploy(target, args.template)}: {target}")
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
