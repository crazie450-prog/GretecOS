"""The ``gos`` command (Appendix B). WO-0.2 adds ``gos doctor``; later WOs add the rest."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import keyring
import keyring.errors

from gregtec import doctor
from gregtec.llm import keys
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

    key_parser = commands.add_parser(
        "key",
        help="store, check or delete secrets in the OS keyring",
        description=(
            "Secrets live in this Windows account's Credential Manager (as "
            f"{keys.SERVICE}/<name>), never in the repo or the vault. Values are never "
            "printed."
        ),
    )
    key_cmds = key_parser.add_subparsers(dest="key_command", required=True)
    key_set = key_cmds.add_parser(
        "set", help="store a secret (asks for it with hidden input)"
    )
    key_set.add_argument("name", choices=keys.NAMES)
    key_cmds.add_parser("status", help="say which secrets are stored")
    key_del = key_cmds.add_parser("delete", help="remove a stored secret")
    key_del.add_argument("name", choices=keys.NAMES)

    smoke = commands.add_parser(
        "smoke",
        help="check that the fast lane (Haiku) and deep lane (Agent SDK) answer",
        description=(
            "Uses the stored Anthropic key: N streamed Haiku 5.5 calls with thinking off "
            "(prints time to first token, V21), then one Agent SDK query on Sonnet 5.5 "
            "with no tools, capped at $0.10. Expect about a cent at most."
        ),
    )
    smoke.add_argument(
        "--runs", type=int, default=5, metavar="N", help="fast-lane calls (default 5)"
    )
    smoke.add_argument("--skip-sdk", action="store_true", help="run the fast lane only")

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
    if args.command == "key":
        try:
            return _key(args.key_command, getattr(args, "name", None))
        except keyring.errors.KeyringError as exc:
            print(f"keyring error: {exc}", file=sys.stderr)
            return 1
    if args.command == "smoke":
        if args.runs < 1:
            parser.error("--runs must be at least 1")
        # Imported here: anthropic and the Agent SDK take seconds to import.
        from gregtec.llm import smoke as llm_smoke

        return llm_smoke.run(runs=args.runs, skip_sdk=args.skip_sdk, out=sys.stdout)
    parser.error(f"unknown command {args.command}")
    return 2


def _key(command: str, name: str | None) -> int:
    if command == "status":
        print(f"keyring backend: {keyring.get_keyring()}")
        for each in keys.NAMES:
            print(f"{each}: {'stored' if keys.has_key(each) else 'not stored'}")
        return 0
    assert name is not None
    if command == "delete":
        print(f"{name}: {'deleted' if keys.delete_key(name) else 'nothing stored'}")
        return 0
    value = getpass.getpass(f"{name} key (input hidden): ").strip()
    if not value:
        print("Nothing entered; nothing stored.", file=sys.stderr)
        return 1
    if name == keys.ANTHROPIC and (problem := keys.anthropic_key_problem(value)):
        print(f"Not stored: {problem}.", file=sys.stderr)
        return 1
    replaced = keys.set_key(name, value)
    print(f"{name}: {'replaced' if replaced else 'stored'} in the OS keyring")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
