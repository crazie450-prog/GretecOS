"""The ``gos`` command (Appendix B). WO-0.2 adds ``gos doctor``; later WOs add the rest."""

from __future__ import annotations

import argparse
import getpass
import io
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import keyring
import keyring.errors

from gregtec import config, doctor
from gregtec import deploy as app_deploy
from gregtec.db import build as recipe_db
from gregtec.db import ores as ore_db
from gregtec.db import quests as quest_db
from gregtec.db import structures as structure_db
from gregtec.ingest import collect as export_collect
from gregtec.ingest import extractor
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

    export = commands.add_parser("export", help="GregtecLink export runs")
    export_cmds = export.add_subparsers(dest="export_command", required=True)
    collect = export_cmds.add_parser(
        "collect",
        help="copy GTNH-Export's newest complete export run into GOS_HOME",
        description=(
            "Read-only on Export: finds the newest complete run in GTNH-Export's "
            "gregteclink/exports/ (no .partial or incomplete runs), checks every file "
            "against its manifest's sha256, size and line count, and copies the run into "
            "GOS_HOME/data/exports/<run_id>/ by a staging folder and one rename. An "
            "identical run already there is left alone. Keeps the newest [export] "
            "keep_collected runs (default 5). Paths are [paths] in gos.toml."
        ),
    )
    collect.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
    )

    db = commands.add_parser(
        "db", help="the recipe database (GOS_HOME/data/gos.sqlite)"
    )
    db_cmds = db.add_subparsers(dest="db_command", required=True)
    db_build = db_cmds.add_parser(
        "build",
        help="load a collected export run into GOS_HOME/data/gos.sqlite",
        description=(
            "Normalises a run that `gos export collect` copied into GOS_HOME/data/exports/ "
            "(default: the newest) and loads it into GOS_HOME/data/gos.sqlite in one "
            "transaction: recipes with their r:<map>/<sig12> keys, items, fluids, machines, "
            "NEI's recipes merged into GT's, and the name index. A collision (two different "
            "recipes with one key) fails the build and writes a curation task to "
            "GOS_HOME/data/curation/. Keeps the newest [db] keep_runs runs (default 2) and "
            "prints the counts report, also saved to GOS_HOME/data/reports/."
        ),
    )
    db_build.add_argument("run", nargs="?", default=None, help="collected run id")
    db_build.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
    )

    quests = commands.add_parser(
        "quests", help="the quest book, progress, frontier and tier (BetterQuesting)"
    )
    quest_cmds = quests.add_subparsers(dest="quests_command", required=True)
    q_load = quest_cmds.add_parser(
        "load",
        help="read a world's quest book and progress into GOS_HOME/data/gos.sqlite",
        description=(
            "Read-only on the game: reads the world's betterquesting/QuestDatabase.json (or "
            "the pack's DefaultQuests when the world has none yet) and every player's "
            "QuestProgress, works out the tracked player's frontier and tier, and replaces "
            "the quest tables of GOS_HOME/data/gos.sqlite in one transaction. Default world: "
            "[paths] world in Play."
        ),
    )
    q_load.add_argument(
        "--instance",
        choices=("play", "lab"),
        default="play",
        help="the instance whose saves/ holds the world (default play)",
    )
    q_where = q_load.add_mutually_exclusive_group()
    q_where.add_argument(
        "--world", default=None, help="the world's folder name in saves/"
    )
    q_where.add_argument(
        "--world-dir",
        type=Path,
        default=None,
        help="a world folder anywhere (its DefaultQuests fallback: ../../config/)",
    )
    q_load.add_argument(
        "--player",
        default=None,
        help="the player to track, by name or UUID (default: the only one)",
    )
    q_front = quest_cmds.add_parser(
        "frontier",
        help="list the quests available now, by quest line, as the book shows them",
    )
    q_front.add_argument(
        "--line",
        default=None,
        help="only this quest line (its name as the book shows it)",
    )
    q_front.add_argument(
        "--limit", type=int, default=-1, metavar="N", help="at most N rows"
    )
    q_tier = quest_cmds.add_parser(
        "tier",
        help="show the tier detected from quests, or set or clear an override (D64)",
    )
    q_set = q_tier.add_mutually_exclusive_group()
    q_set.add_argument(
        "--set", dest="set_stage", choices=quest_db.bq.STAGES, default=None
    )
    q_set.add_argument("--clear", action="store_true", help="drop the override")
    for each in (q_load, q_front, q_tier):
        each.add_argument(
            "--config",
            type=Path,
            default=None,
            help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
            "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
        )

    ores = commands.add_parser(
        "ores", help="GT's ore mixes and a world's veins (VisualProspecting)"
    )
    ore_cmds = ores.add_subparsers(dest="ores_command", required=True)
    o_load = ore_cmds.add_parser(
        "load",
        help="load ore mixes from the collected export and a world's veins into gos.sqlite",
        description=(
            "Read-only on the game: reads GT's ore mixes from the newest collected export "
            "run (gt_ore_mixes.json) and a world's VisualProspecting caches (the server "
            "cache of every generated vein, and the player's client cache of the veins he "
            "knows), and replaces the vein table of GOS_HOME/data/gos.sqlite, and ore_mix "
            "when the run has mixes, in one transaction. Default world: [paths] lab_world in "
            "the Lab, never Play's unless --instance play."
        ),
    )
    o_load.add_argument("--run", default=None, help="collected run id (default newest)")
    o_load.add_argument(
        "--instance",
        choices=("lab", "play"),
        default=None,
        help="the instance whose saves/ holds the world (default lab); with --world-dir, "
        "play allows a world in Play",
    )
    o_where = o_load.add_mutually_exclusive_group()
    o_where.add_argument(
        "--world", default=None, help="the world's folder name in saves/"
    )
    o_where.add_argument(
        "--world-dir", type=Path, default=None, help="a world folder anywhere"
    )
    o_load.add_argument(
        "--game-dir",
        type=Path,
        default=None,
        help="with --world-dir: the game folder holding visualprospecting/ (default: the "
        "folder that holds the world's saves/)",
    )
    o_load.add_argument(
        "--player",
        default=None,
        help="whose client cache to read, by UUID (default: the only one)",
    )
    o_list = ore_cmds.add_parser(
        "list",
        help="veins per dimension, or one dimension's veins with --dim",
    )
    o_list.add_argument(
        "--dim", type=int, default=None, metavar="N", help="list dimension N's veins"
    )
    o_list.add_argument(
        "--limit", type=int, default=-1, metavar="N", help="at most N rows"
    )
    for each in (o_load, o_list):
        each.add_argument(
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

    structures = commands.add_parser(
        "structures", help="multiblock footprints from gtnh-extractor (WO-1.8)"
    )
    structure_cmds = structures.add_subparsers(dest="structures_command", required=True)
    extract = structure_cmds.add_parser(
        "extract",
        help="run gtnh-extractor re-pinned to GTNH 2.9.0 and write its dataset",
        description=(
            "Fetches MrBruh's gtnh-extractor at its pinned commit into WORK, swaps in this "
            "repo's Gradle wrapper, re-pins it to GTNH 2.9.0 (GT 5.09.54.265, StructureLib "
            "1.4.47) and runs its headless dev server, which builds every base-pack "
            "multiblock and writes OUT/multiblocks/. Reads and writes no Prism instance. The "
            "first run downloads several GB."
        ),
    )
    extract.add_argument(
        "--work",
        type=Path,
        default=None,
        help="checkout and build folder. Default: $GOS_HOME/data/cache/gtnh-extractor.",
    )
    extract.add_argument(
        "--out",
        type=Path,
        default=None,
        help="new dataset folder. Default: $GOS_HOME/data/structures/2.9.0-<UTC time>.",
    )
    load = structure_cmds.add_parser(
        "load",
        help="load a gtnh-extractor dataset into GOS_HOME/data/gos.sqlite",
        description=(
            "Checks the dataset (schema v3, labelled GT 5.09.54.265) and replaces the "
            "structure table's gtnh-extractor rows with it in one transaction, then prints "
            "the counts. A controller file that doesn't read is set aside and listed; the "
            "rest load."
        ),
    )
    load.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=None,
        help=(
            "the dataset folder. Default: the newest in $GOS_HOME/data/structures that "
            "reads."
        ),
    )
    for each in (extract, load):
        each.add_argument(
            "--gos-home",
            type=Path,
            default=None,
            help="Default: $GOS_HOME, else ~/GregtecOS.",
        )

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
    if args.command == "export" and args.export_command == "collect":
        return export_collect.main(args.config, sys.stdout)
    if args.command == "db" and args.db_command == "build":
        return recipe_db.main(args.config, args.run, sys.stdout)
    if args.command == "quests":
        # Quest text is pack data (CJK, symbols). Windows writes a pipe or a file in its ANSI
        # code page (cp1252), which can't encode all of it: escape what it can't, never crash.
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(errors="backslashreplace")
        if args.quests_command == "load":
            return quest_db.load_main(
                args.config,
                args.instance,
                args.world,
                args.world_dir,
                args.player,
                sys.stdout,
            )
        if args.quests_command == "frontier":
            return quest_db.frontier_main(
                args.config, args.line, args.limit, sys.stdout
            )
        return quest_db.tier_main(args.config, args.set_stage, args.clear, sys.stdout)
    if args.command == "ores":
        # Paths and pack names, as for quests: escape what a cp1252 pipe can't, never crash.
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(errors="backslashreplace")
        if args.ores_command == "load":
            if args.game_dir is not None and args.world_dir is None:
                parser.error("--game-dir goes with --world-dir")
            return ore_db.load_main(
                args.config,
                args.run,
                args.instance,
                args.world,
                args.world_dir,
                args.game_dir,
                args.player,
                sys.stdout,
            )
        return ore_db.list_main(args.config, args.dim, args.limit, sys.stdout)
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
    if args.command == "structures" and args.structures_command == "extract":
        return extractor.main(args.gos_home, args.work, args.out, sys.stdout)
    if args.command == "structures" and args.structures_command == "load":
        return structure_db.main(args.gos_home, args.dataset, sys.stdout)
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
