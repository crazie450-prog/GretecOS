"""The ``gos`` command (Appendix B). WO-0.2 adds ``gos doctor``; later WOs add the rest."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import keyring
import keyring.errors

from gregtec import config, doctor, locks, playhooks
from gregtec import deploy as app_deploy
from gregtec.ingest import fingerprint, rundiff
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

    config_help = (
        "gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults."
    )
    hook = commands.add_parser(
        "hook",
        help="Play's Prism hooks (WO-1.4): pre-launch and post-exit",
        description=(
            "Prism runs these through GOS_APP\\scripts\\prism_prelaunch.ps1 and "
            "prism_postexit.ps1. pre-launch exits 3 (Prism then doesn't start the game) while "
            "the instance lock is held or Export runs, and records whether recipe data is "
            "stale. post-exit asks to export when Play changed since the last export (D69)."
        ),
    )
    playhooks.add_hook_arguments(hook)
    hook.add_argument("--config", type=Path, default=None, help=config_help)

    export = commands.add_parser("export", help="export runs (WO-1.4)")
    export_cmds = export.add_subparsers(dest="export_command", required=True)
    run = export_cmds.add_parser(
        "run",
        help="sync Play to Export, launch Export to autoexport, record the fingerprint, diff",
        description=(
            "As Clayton, with no GTNH client running: holds the instance lock, fingerprints "
            "Play, syncs it to GTNH-Export, launches Export through Prism's CLI (it exports and "
            "quits by itself, D100), waits for the new complete run and for Export to quit, "
            "records Play's fingerprint for the run and writes the diff against the previous "
            "export to GOS_HOME/data/reports/."
        ),
    )
    run.add_argument("--config", type=Path, default=None, help=config_help)
    run.add_argument(
        "--notify",
        action="store_true",
        help="show the result in a message box (Windows)",
    )
    # Set by the post-exit hook, which waits until the export holding the lock shows it.
    run.add_argument("--ticket", default="", help=argparse.SUPPRESS)
    diff = export_cmds.add_parser(
        "diff",
        help="compare two export runs (manifest and file-hash level)",
        description=(
            "OLD and NEW are run folders or run ids (looked up in GOS_HOME/data/exports, then "
            "in Export's gregteclink/exports). Without them: the last two recorded exports."
        ),
    )
    diff.add_argument("runs", nargs="*", metavar="RUN", help="OLD NEW")
    diff.add_argument("--config", type=Path, default=None, help=config_help)

    fp = commands.add_parser(
        "fingerprint",
        help="Play's fingerprint and whether recipe data is stale (read-only)",
        description=(
            "Fingerprints GTNH-Play (mods, scripts, dreamcraft.cfg, [fingerprint] configs, "
            "mmc-pack.json; Export's sync excludes left out) and compares it with the last "
            "recorded export. Reads Play only; writes nothing but GOS's hash cache."
        ),
    )
    fp.add_argument(
        "--list", action="store_true", help="print every file and its SHA-256"
    )
    fp.add_argument("--no-cache", action="store_true", help="hash every file again")
    fp.add_argument("--config", type=Path, default=None, help=config_help)

    lock = commands.add_parser("lock", help="the instance lock (Export vs Play, V25)")
    lock_cmds = lock.add_subparsers(dest="lock_command", required=True)
    lock_status = lock_cmds.add_parser("status", help="say who holds the instance lock")
    lock_status.add_argument("--config", type=Path, default=None, help=config_help)
    lock_hold = lock_cmds.add_parser(
        "hold",
        help="hold the instance lock (to test that a launch is refused)",
        description="Holds the lock until Ctrl+C or --minutes pass.",
    )
    lock_hold.add_argument("--minutes", type=float, default=10.0)
    lock_hold.add_argument("--reason", default="manual test (gos lock hold)")
    lock_hold.add_argument("--config", type=Path, default=None, help=config_help)

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
    if args.command == "deploy":
        return _deploy(args.ref, args.config)
    if args.command == "hook":
        return playhooks.hook_main(args, sys.stdout, os.environ)
    if args.command == "export":
        return _export(args)
    if args.command == "fingerprint":
        return _fingerprint(args)
    if args.command == "lock":
        return _lock(args)
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
    forbidden = [*prism, *doctor.default_prism_roots(), home]
    try:
        release = app_deploy.deploy(
            app_deploy.REPO_ROOT, ref, cfg.gos_app, forbidden=forbidden
        )
    except (app_deploy.DeployError, OSError) as exc:
        print(f"Not deployed: {exc}", file=sys.stderr)
        return 1
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


def _load(path: Path | None) -> config.Config | None:
    try:
        return config.load(path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=sys.stderr)
        return None


def _export(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    if cfg is None:
        return 2
    if args.export_command == "run":
        notify = playhooks.notify_dialog if args.notify else None
        return playhooks.run_export(cfg, sys.stdout, notify=notify, ticket=args.ticket)
    refs = args.runs
    if len(refs) not in (0, 2):
        print(
            "Give two runs (OLD NEW), or none for the last two exports.",
            file=sys.stderr,
        )
        return 2
    if not refs:
        exports = fingerprint.load_state(cfg.gos_home)["exports"]
        if len(exports) < 2:
            print(
                "Fewer than two exports are recorded: name two runs.", file=sys.stderr
            )
            return 2
        refs = [exports[-2]["run_id"], exports[-1]["run_id"]]
    try:
        old, new = (rundiff.load_run(rundiff.find_run(cfg, r)) for r in refs)
    except rundiff.RunError as exc:
        print(f"Can't diff: {exc}", file=sys.stderr)
        return 1
    print(rundiff.report(rundiff.diff_runs(cfg.gos_home, old, new)), end="")
    return 0


def _fingerprint(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    if cfg is None:
        return 2
    try:
        fp = fingerprint.compute_play(cfg, use_cache=not args.no_cache)
    except fingerprint.FingerprintError as exc:
        print(f"Can't fingerprint Play: {exc}", file=sys.stderr)
        return 1
    if args.list:
        sys.stdout.write(fingerprint.listing(fp.entries).decode("utf-8"))
    print(f"Play ({cfg.instance('play')}): {fp.digest}")
    print(f"  {len(fp.entries)} files: {fp.counts()}")
    for note in fp.notes:
        print(f"  note: {note}")
    status = fingerprint.status(cfg.gos_home, fp)
    print(f"  {status.line()}")
    for row in status.changes.lines(limit=20):
        print(f"    {row}")
    return 0


def _lock(args: argparse.Namespace) -> int:
    cfg = _load(args.config)
    if cfg is None:
        return 2
    if args.lock_command == "status":
        holder = locks.held(cfg.gos_home)
        print("free" if holder is None else f"held by {holder.describe()}")
        return 0
    try:
        lock = locks.InstanceLock(cfg.gos_home).acquire("test", args.reason)
    except locks.LockHeld as exc:
        print(f"Can't hold it: {exc}", file=sys.stderr)
        return 1
    with lock:
        print(
            f"Holding the instance lock for {args.minutes:g} min (Ctrl+C releases it)."
        )
        try:
            time.sleep(args.minutes * 60)
        except KeyboardInterrupt:
            pass
    print("Released.")
    return 0


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
