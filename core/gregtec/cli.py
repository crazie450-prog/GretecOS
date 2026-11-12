"""The ``gos`` command (Appendix B). WO-0.2 adds ``gos doctor``; later WOs add the rest.

The service commands (serve, stop, status, launch, token, pair, devices; WO-2.1) live in
``gregtec.servicecli``.
"""

from __future__ import annotations

import argparse
import getpass
import io
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import keyring
import keyring.errors

from gregtec import (
    appwindow,
    config,
    control,
    doctor,
    locks,
    personacli,
    playhooks,
    servicecli,
    voicecli,
)
from gregtec import deploy as app_deploy
from gregtec.agents import design_cost_cli, recipe_eval_cli
from gregtec.agents.tools import command as tool_command
from gregtec.db import build as recipe_db
from gregtec.db import crops as crop_db
from gregtec.db import ores as ore_db
from gregtec.db import quests as quest_db
from gregtec.db import structures as structure_db
from gregtec.ingest import collect as export_collect
from gregtec.ingest import extractor, fingerprint, rundiff
from gregtec.llm import keys
from gregtec.packupdate import cli as pack_cli
from gregtec.planner import graphcli, plancli, powercli
from gregtec.quests import cli as quest_cli
from gregtec.vault import curator as vault_curator
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
        help="Prism data folder to search (repeatable). Default: gos.toml's [paths] "
        "prism_root, then $PRISMLAUNCHER_DATA_DIR, C:\\Games\\PrismLauncher and "
        "%%APPDATA%%\\PrismLauncher.",
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

    export = commands.add_parser(
        "export", help="GregtecLink export runs (WO-1.2, WO-1.4)"
    )
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
    collect.add_argument("--config", type=Path, default=None, help=config_help)
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

    crops = commands.add_parser(
        "crops", help="CropsNH crop data in the DB (gos db build loads it)"
    )
    crop_cmds = crops.add_subparsers(dest="crops_command", required=True)
    c_show = crop_cmds.add_parser(
        "show",
        help="a crop's facts, or every crop's id, name and tier",
        description=(
            "Prints what the DB holds for each crop named (its crop:<id>, CropsNH id or "
            "name, any case; else every crop whose name contains it), from the newest run "
            "with crop data: tier, soil, requirements, drops with chances, parent recipes and "
            "mutation pools, for comparing with NEI in the Lab. With no name, lists them all."
        ),
    )
    c_show.add_argument("name", nargs="*", help="crop name or id")
    c_show.add_argument(
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
    tool = commands.add_parser(
        "tool",
        help="run a read-only agent tool against GOS_HOME's DB (for testing)",
        description=(
            "Runs one of the agents' read-only tools (WO-2.2) against "
            "GOS_HOME/data/gos.sqlite and prints its TSV result, as the fast and deep lanes "
            "see it. With no NAME, lists the tools."
        ),
    )
    tool.add_argument(
        "name", nargs="?", default=None, help="the tool (none: list them)"
    )
    tool.add_argument(
        "args",
        nargs="?",
        default="{}",
        help="its arguments, a JSON object (default {})",
    )
    tool.add_argument(
        "--lane",
        choices=("fast", "deep"),
        default=None,
        help="call it as this lane would",
    )
    tool.add_argument(
        "--all", action="store_true", help="follow its more cursors to the last page"
    )
    tool.add_argument(
        "--schema",
        action="store_true",
        help="print the tool's schema instead of running it",
    )
    tool.add_argument(
        "--config",
        type=Path,
        default=None,
        help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
        "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
    )
    ask = commands.add_parser(
        "ask",
        help="ask Gregtec OS a question (router, instant commands, fast lane)",
        description=(
            "Routes the text as POST /api/ask does (WO-2.3): instant commands and unsure "
            "item matches answer without a model; lookups go to the fast lane (Haiku 5.5, "
            "the stored key); design and planning are handed to the deep lane. Runs in this "
            "process against GOS_HOME's DB and vault."
        ),
    )
    ask.add_argument("text", help='the question, e.g. "how do I make steel"')
    ask.add_argument(
        "--json", action="store_true", help="print the whole reply as JSON"
    )
    eval_parser = commands.add_parser("eval", help="run an eval set")
    eval_cmds = eval_parser.add_subparsers(dest="eval_command", required=True)
    fast_eval = eval_cmds.add_parser(
        "fast-lane",
        help="the fast lane's 50 questions and 10 ambiguous items (WO-2.3)",
        description=(
            "Asks every row of tests/evals/fast_lane_questions.tsv and reports pass/fail, "
            "time to first token (P50 target 1.2 s) and whether the ambiguous items asked. "
            "Uses the stored Anthropic key (about a cent a run) unless --fake."
        ),
    )
    fast_eval.add_argument(
        "--fake",
        action="store_true",
        help="a fake model client: no key, no cost, no real timings",
    )
    fast_eval.add_argument(
        "--questions", type=Path, default=None, help="another question file"
    )
    fast_eval.add_argument(
        "--limit", type=int, default=None, help="only the first N rows"
    )
    deep_eval = eval_cmds.add_parser(
        "deep-lane",
        help="the deep lane's cost table over 10 representative tasks (WO-2.4)",
        description=(
            "Hands every row of tests/evals/deep_lane_tasks.tsv to the deep lane, one fresh "
            "session each, and prints the cost table (SDK cost by model, turns, tools, "
            "subagents, seconds; total, mean, median). A task that reaches [budget] "
            "task_max_usd is stopped, never approved, so a run costs at most 10 caps. Uses "
            "the stored Anthropic key and records llm_usage unless --fake."
        ),
    )
    deep_eval.add_argument(
        "--fake",
        action="store_true",
        help="the real CLI against a local scripted model: no key, no cost",
    )
    deep_eval.add_argument("--tasks", type=Path, default=None, help="another task file")
    deep_eval.add_argument(
        "--limit", type=int, default=None, help="only the first N rows"
    )
    deep_eval.add_argument(
        "--out",
        type=Path,
        default=None,
        help="where to write the table (TSV). Default: "
        "GOS_HOME/data/reports/deep-lane-costs-<date>.tsv",
    )
    for each in (ask, fast_eval, deep_eval):
        each.add_argument(
            "--config",
            type=Path,
            default=None,
            help="gos.toml to read. Default: $GOS_HOME/config/gos.toml, else "
            "~/GregtecOS/config/gos.toml; without one, the Appendix C defaults.",
        )
    recipe_eval_cli.add_parsers(eval_cmds)
    design_cost_cli.add_parsers(eval_cmds)
    quest_cli.add_parsers(quest_cmds, eval_cmds)

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
    maintain = vault_cmds.add_parser(
        "maintain",
        help="run the curator's idle-maintenance jobs now (plan 6.6)",
        description=(
            "Runs the daily jobs now, whatever the idle rule says: compact old logs (a Haiku "
            "call each, with the stored key; periods held after refused summaries are tried "
            "again), check the notes against _gos/schema.md, rebuild INDEX.md, mirror them "
            "into note_fts, back up the DB, commit to the vault's git. The core runs the same jobs once a day when the game is closed and the PC "
            "has been idle for [maintenance] idle_minutes."
        ),
    )
    maintain.add_argument(
        "--job",
        action="append",
        choices=vault_curator.JOBS,
        help="run only this job (repeatable). Default: all, in order.",
    )
    maintain.add_argument(
        "--rerun-today",
        action="store_true",
        help="run no job; make the core's daily run due again today (it then waits for the "
        "idle rule)",
    )
    measure = vault_cmds.add_parser(
        "measure",
        help="count the note formats' tokens with the count_tokens endpoint (WO-2.5)",
        description=(
            "Renders the live vault's notes (never me/, D142), plan 6.2's example and two DB "
            "tables in each "
            "candidate format (the grammar, CSV, TOON, Markdown, key: value rows, compact and "
            "pretty JSON, YAML), counts their tokens with the Messages API's count_tokens "
            "(free) using the stored Anthropic key, and writes "
            "GOS_HOME/data/reports/note-formats-YYYYMMDD.txt."
        ),
    )
    measure.add_argument(
        "--model",
        action="append",
        help="a model to count for (repeatable). Default: Haiku, Sonnet and Opus 5.5.",
    )
    measure.add_argument(
        "--no-db",
        action="store_true",
        help="leave out the DB tables (vault notes only)",
    )
    for sub in (maintain, measure):
        sub.add_argument(
            "--config",
            type=Path,
            default=None,
            help="gos.toml to read. Default: $GOS_HOME/config/gos.toml.",
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
    pack_cli.add_parsers(commands)
    graphcli.add_parsers(commands)
    plancli.add_parsers(commands)
    powercli.add_parsers(commands)
    appwindow.add_parsers(commands)
    voicecli.add_parsers(commands)
    personacli.add_parsers(commands)
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
    if args.command in servicecli.COMMANDS:
        return servicecli.run(args)
    if args.command in pack_cli.COMMANDS:
        return pack_cli.run(args, sys.stdout)
    if args.command in graphcli.COMMANDS:
        return graphcli.run(args, sys.stdout)
    if args.command in plancli.COMMANDS:
        return plancli.run(args, sys.stdout)
    if args.command in powercli.COMMANDS:
        return powercli.run(args, sys.stdout)
    if args.command in appwindow.COMMANDS:
        return appwindow.run(args)
    if args.command in voicecli.COMMANDS:
        return voicecli.run(args)
    if args.command in personacli.COMMANDS:
        return personacli.run(args)
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
    if args.command == "crops" and args.crops_command == "show":
        # Pack names, as for quests: escape what a cp1252 pipe can't, never crash.
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(errors="backslashreplace")
        return crop_db.show_main(args.config, args.name, sys.stdout)
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
        if args.quests_command == quest_cli.SUMMARIES:
            return quest_cli.run_summaries(args, sys.stdout)
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
    if args.command == "tool":
        # Tool results hold pack text (CJK, symbols): escape what a Windows pipe can't encode.
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(errors="backslashreplace")
        return tool_command.main(
            args.config,
            args.name,
            args.args,
            args.lane,
            args.all,
            args.schema,
            sys.stdout,
        )
    if args.command in ("ask", "eval"):
        # Answers hold pack text (CJK, symbols): escape what a Windows pipe can't encode.
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(errors="backslashreplace")
        # Imported here: the agents import the anthropic SDK, which takes a moment.
        if args.command == "ask":
            from gregtec.agents import ask as agents_ask

            return agents_ask.main(args.config, args.text, args.json, sys.stdout)
        if args.limit is not None and args.limit < 1:
            parser.error("--limit must be at least 1")
        if args.eval_command == recipe_eval_cli.COMMAND:
            return recipe_eval_cli.run(args, sys.stdout)
        if args.eval_command == design_cost_cli.COMMAND:
            return design_cost_cli.run(args, sys.stdout)
        if args.eval_command == quest_cli.EVAL:
            return quest_cli.run_eval(args, sys.stdout)
        if args.eval_command == "deep-lane":
            from gregtec.agents import deep_eval

            return deep_eval.main(
                args.config, args.tasks, args.fake, args.limit, args.out, sys.stdout
            )
        from gregtec.agents import evals

        return evals.main(
            args.config, args.questions, args.fake, args.limit, sys.stdout
        )
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
    if args.command == "vault" and args.vault_command == "maintain":
        return _maintain(args.config, args.job, args.rerun_today)
    if args.command == "vault" and args.vault_command == "measure":
        from gregtec.vault import measure as vault_measure

        cfg = _load(args.config)
        if cfg is None:
            return 2
        return vault_measure.main(
            cfg.gos_home, args.model or vault_measure.MODELS, not args.no_db, sys.stdout
        )
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
    if release.dashboard is not None:
        print(f"Built the dashboard: {release.path / app_deploy.DASHBOARD / 'dist'}")
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


def _maintain(config_path: Path | None, jobs: list[str] | None, rerun: bool) -> int:
    """``gos vault maintain``: 0 when no job failed, 1 otherwise, 2 for a bad gos.toml."""
    import datetime as dt

    from gregtec.llm.summarise import HaikuSummariser
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import DB_NAME, Store
    from gregtec.vault.scheduler import KV_LAST_DAY

    cfg = _load(config_path)
    if cfg is None:
        return 2
    store = Store(cfg.data_dir / DB_NAME)
    try:
        if rerun:
            with store.transaction() as conn:
                conn.execute("DELETE FROM kv WHERE key = ?", (KV_LAST_DAY,))
            print(
                "The daily run is due again: the core runs it at the next minute when the "
                f"game is closed and the PC has been idle {cfg.idle_minutes} min."
            )
            return 0
        cur = vault_curator.Curator(
            vault=cfg.gos_home / "vault",
            db=cfg.data_dir / DB_NAME,
            backups=cfg.data_dir / "backups",
            summariser=HaikuSummariser.from_keyring,
            meter=UsageMeter(
                store, cfg.monthly_included_usd, cfg.credit_alerts_pct, cfg.cycle_day
            ),
            keep_backups=cfg.keep_backups,
            retry_held=True,  # Clayton's own run: held log periods get another call
        )
        results = cur.run(dt.date.today(), jobs or vault_curator.JOBS)
    finally:
        store.close()
    for result in results:
        print(result.line())
    return 1 if any(r.status == vault_curator.FAILED for r in results) else 0


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
        return playhooks.run_export(
            cfg,
            sys.stdout,
            notify=notify,
            ticket=args.ticket,
            config_path=args.config or config.default_path(),
        )
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
    if problem := keys.secret_problem(name, value):
        print(f"Not stored: {problem}.", file=sys.stderr)
        return 1
    replaced = keys.set_key(name, value)
    print(f"{name}: {'replaced' if replaced else 'stored'} in the OS keyring")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
