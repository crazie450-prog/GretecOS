"""The ``gos`` command (Appendix B). WO-0.2 adds ``gos doctor``; later WOs add the rest.

The service commands (serve, stop, status, launch, token, pair, devices; WO-2.1) live in
``gregtec.servicecli``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import keyring
import keyring.errors

from gregtec import config, control, doctor, servicecli
from gregtec import deploy as app_deploy
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

    app = commands.add_parser(
        "deploy",
        help="install a merged commit of this clone into GOS_APP",
        description=(
            "Clayton runs this from his own clone, as himself. It installs REF (default "
            "HEAD; it must be in origin/main and the working tree clean) into "
            "GOS_APP\\current with its own .venv, builds its mod/ (GregtecLink) with Gradle "
            "and keeps only the jar, copies its scripts/ to GOS_APP\\scripts, and swaps "
            "both in by renames, so a failed deploy changes nothing. GOS_APP is "
            "[paths] gos_app in gos.toml and must already exist (setup_gos_dev.ps1 makes it)."
        ),
    )
    app.add_argument("ref", nargs="?", default="HEAD", help="commit, tag or branch")
    app.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
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

    servicecli.add_parsers(commands)

    args = parser.parse_args(argv)
    if args.command in servicecli.COMMANDS:
        return servicecli.run(args)
    if args.command == "doctor":
        return doctor.main(
            prism_roots=args.prism_root,
            gos_home=args.gos_home,
            hash_mods=not args.no_hash,
            out=sys.stdout,
        )
    if args.command == "deploy":
        return _deploy(args.ref, args.config)
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


def _deploy(ref: str, config_path: Path | None) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=sys.stderr)
        return 2
    home = Path(os.environ.get("GOS_HOME") or Path.home() / "GregtecOS")
    prism = [cfg.prism_instances, cfg.prism_instances.parent]
    forbidden = [*prism, *doctor.default_prism_roots(), home, cfg.gos_home]
    for problem in cfg.service_errors:
        print(f"Warning: {problem} (GOS won't start until it's fixed)", file=sys.stderr)
    # GOS is stopped only for the swap, after the minutes of staging and building, and not at
    # all for a deploy that is refused (a dirty tree, a ref not in main).
    stopped: list[dict[str, object]] = []

    def stop_gos() -> None:
        if (sup := _stop_for_deploy(cfg)) is not None:
            stopped.append(sup)

    try:
        release = app_deploy.deploy(
            app_deploy.REPO_ROOT,
            ref,
            cfg.gos_app,
            forbidden=forbidden,
            before_swap=stop_gos,
        )
    except (app_deploy.DeployError, OSError) as exc:
        print(f"Not deployed: {exc}", file=sys.stderr)
        return 1
    finally:  # also after Ctrl+C: GOS comes back, from whichever release is current
        if stopped:
            # As it ran: the gos.toml in its run file (the Run key's --config), if it says.
            ran_with = stopped[0].get("config", config_path)
            _restart_after_deploy(
                cfg, Path(ran_with) if isinstance(ran_with, str | Path) else None
            )
    was = f", replacing {release.replaced[:12]}" if release.replaced else ""
    print(f"Deployed {release.commit[:12]} ({ref}) to {release.path}{was}.")
    print(f"{release.path.parent / app_deploy.SCRIPTS} is a copy of its scripts/.")
    if release.gregteclink is not None:
        jar = release.path / app_deploy.MOD_DIST / release.gregteclink["jar"]
        print(f"Built GregtecLink: {jar}")
        print(f"  SHA256 {release.gregteclink['sha256']} (in {app_deploy.RECORD})")
        print("  sync_instances.py installs it into Export.")
    for path in release.leftovers:
        print(f"Couldn't delete {path} (in use?); the next deploy removes it.")
    gos = app_deploy.venv_exe(release.path, "gos")
    python = app_deploy.venv_exe(release.path, "python")
    scripts = release.path / "scripts"
    print("Run the tools that touch Play from the release, as yourself:")
    print(f"  {gos} doctor")
    print(f"  {python} {scripts / 'check_tweaks.py'} <instance folder> [--apply]")
    print(f"  {python} {scripts / 'sync_instances.py'} --dry-run")
    return 0


def _stop_for_deploy(cfg: config.Config) -> dict[str, object] | None:
    """Stop a supervisor running from GOS_APP: Windows can't rename ``current`` under it (D115).

    Returns its run file's data if it was stopped, so the deploy restarts it from the new
    release with the same gos.toml.
    """
    run_file = cfg.run_dir / control.SUPERVISOR_FILE
    sup = control.running(run_file, control.SUPERVISOR_SERVICE)
    if sup is None or not isinstance(sup.get("exe"), str):
        return None
    app = os.path.normcase(os.path.abspath(cfg.gos_app))
    exe = os.path.normcase(os.path.abspath(sup["exe"]))
    if not exe.startswith(app.rstrip("\\/") + os.sep):
        return None
    print("Stopping GOS for the swap (it runs from GOS_APP); it restarts afterwards.")
    try:
        control.call(sup["port"], "POST", "/shutdown", token=sup["token"])
    except control.ControlError as exc:
        print(f"  Couldn't stop it ({exc}); the swap may fail and change nothing.")
        return None
    servicecli.wait_until(
        lambda: control.running(run_file, control.SUPERVISOR_SERVICE), want=False
    )
    return sup


def _restart_after_deploy(cfg: config.Config, config_path: Path | None) -> None:
    """Start the supervisor of GOS_APP's current release, with the gos.toml it ran with."""
    exe = app_deploy.venv_exe(cfg.gos_app / app_deploy.CURRENT, "gos-supervisor")
    if not exe.is_file():
        print(f"Couldn't restart GOS: there is no {exe}. Log off and on, or run it.")
        return
    argv = [str(exe)] + (["--config", str(config_path)] if config_path else [])
    servicecli.spawn_detached(argv, cfg.gos_home)
    print(f"Restarted GOS: {exe}")


def _key(command: str, name: str | None) -> int:
    if command == "status":
        print(f"keyring backend: {', '.join(keys.backend_chain())}")
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
