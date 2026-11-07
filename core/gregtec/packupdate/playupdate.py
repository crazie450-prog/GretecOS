r"""Update Play from the tested Lab: the script Clayton runs himself (WO-1.9, D149).

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\update_play.py
    ... update_play.py --apply [--update ID] [--config PATH]

CLAUDE.md: anything that touches Play is a script Clayton runs from GOS_APP; Claude Code writes
it and tests it on temp folders and Lab copies, never on Play. It mirrors Lab's ``mods/``,
``config/`` and ``scripts/`` into Play the way ``sync_instances.py`` mirrors Play into Lab
(:func:`gregtec.sync.plan_target`, Lab's ``[sync.lab] exclude`` files never copied or deleted),
so Play ends with exactly the mod set the Lab export tested. Worlds, saves, options and every
other folder are never touched; Prism's components (``mmc-pack.json``: Forge, LWJGL, Java) are
left for Clayton to match in Prism, and the script says when they differ.

Without ``--apply`` it only prints the plan (a simulated update records that dry run, which is
its ``play`` step). ``--apply`` is refused unless: it runs as Clayton, not gos-dev; the update
is adopted (``gos pack adopt yes``) and isn't simulated; its world backup step is done; Lab
still has the fingerprint its export was tested with; no GTNH client runs; and it gets the
instance lock (no export at the same time). Before writing, every Play file it replaces or
deletes is copied to ``updates/<id>/play-before/`` (the way back), then the plan is applied with
sync's temp-file-and-rename writes.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from gregtec import config, locks, playhooks, prism, sync
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import fingerprint
from gregtec.packupdate import playbook

BEFORE = "play-before"


def plan_update(cfg: config.Config) -> tuple[sync.Plan | None, list[str]]:
    """Play's plan with Lab as the source. Reads only."""
    refusals: list[str] = []
    play = sync._instance(cfg.instance("play"), "Play", refusals)
    lab = sync._instance(cfg.instance("lab"), "lab", refusals)
    if play is None or lab is None:
        return None, refusals
    if sync._overlap(play[0], lab[0]):
        refusals.append(f"{lab[0]} is Play, is inside it or holds it")
    if refusals:
        return None, refusals
    plan = sync.plan_target(
        "play", lab[1], play[1], cfg.sync_exclude.get("lab", ()), refusals
    )
    return (None if refusals else plan), refusals


def _mmc_differs(cfg: config.Config) -> bool:
    try:
        return read_bytes(cfg.instance("play") / fingerprint.MMC_PACK) != read_bytes(
            cfg.instance("lab") / fingerprint.MMC_PACK
        )
    except OSError:
        return False


def refusals_for_apply(
    cfg: config.Config, u: playbook.Update, clients: playhooks.Clients
) -> list[str]:
    out = []
    if config.running_as_dev():
        out.append(
            f"update_play.py --apply runs as Clayton, never as {config.DEV_ACCOUNT}"
        )
    if u.simulated:
        out.append(f"update {u.id} is simulated: Play is never changed for it")
    if u.decision().get("status") != playbook.ADOPTED_YES:
        out.append(f"update {u.id} isn't adopted (gos pack adopt yes comes first)")
    if u.status("backup") != playbook.DONE:
        out.append("its world backup step isn't done (gos pack update)")
    want = u.step("lab").get("data", {}).get("lab_fingerprint")
    try:
        lab = playbook.instance_fingerprint(cfg, "lab").digest
        if lab != want:
            out.append(
                "Lab changed since its export was tested: gos pack update --redo lab"
            )
    except fingerprint.FingerprintError as exc:
        out.append(f"can't fingerprint Lab: {exc}")
    try:
        running = clients(cfg)
    except OSError as exc:
        out.append(f"can't tell whether a GTNH client runs ({exc})")
    else:
        if running:
            out.append(
                f"{', '.join(cfg.instance(r).name for r in running)} is running; close it"
            )
    return out


def backup_before(plan: sync.Plan, dest: Path) -> int:
    """Copy each Play file the plan replaces or deletes to ``dest``; how many."""
    n = 0
    targets = [t for _, t in plan.update] + list(plan.delete)
    for target in targets:
        rel = target.relative_to(plan.game)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(read_bytes(target))
        n += 1
    return n


def run(
    cfg: config.Config,
    update_id: str | None,
    apply: bool,
    out: TextIO,
    clients: playhooks.Clients = prism.running_clients,
) -> int:
    """0: planned (or applied); 1: refused or failed."""
    try:
        u = playbook.find(cfg.gos_home, update_id)
    except playbook.UpdateError as exc:
        print(f"refused: {exc}", file=out)
        return 1
    try:
        plan, refusals = plan_update(cfg)
    except OSError as exc:
        print(f"refused: {exc}", file=out)
        return 1
    if plan is None:
        print("refused: " + "; ".join(refusals), file=out)
        return 1
    print(f"Update {u.id}: Lab ({cfg.instance('lab')}) -> Play", file=out)
    sync.report(plan, out)
    summary = (
        f"{len(plan.copy)} copied, {len(plan.update)} replaced, "
        f"{len(plan.delete)} deleted"
    )
    if _mmc_differs(cfg):
        print(
            "note: Play's mmc-pack.json (Forge, LWJGL, Java components) differs from Lab's. "
            "This script doesn't change it: match Play's components to Lab's in Prism "
            "(Edit instance > Version).",
            file=out,
        )
    step = u.step("play")
    if not apply:
        print("Dry run: nothing written. --apply updates Play.", file=out)
        step.setdefault("data", {}).update(
            dry_run_utc=playbook._now_utc(), summary=summary
        )
        u.save()
        return 0
    problems = refusals_for_apply(cfg, u, clients)
    if problems:
        print("refused: " + "; ".join(problems), file=out)
        return 1
    try:
        lock = locks.InstanceLock(cfg.gos_home).acquire(
            "play-update", f"update_play.py ({u.id})"
        )
    except locks.LockHeld as exc:
        print(f"refused: {exc}", file=out)
        return 1
    with lock:
        dest = u.folder / BEFORE
        try:
            n = backup_before(plan, dest)
            print(f"backed up {n} Play files to {dest}", file=out)
            sync.apply(plan)
        except OSError as exc:
            print(
                f"FAILED partway: {exc}. Play's replaced files are in {dest}; copy them "
                "back (or restore from the world backup and Prism) before playing.",
                file=out,
            )
            return 1
    step.setdefault("data", {}).update(
        applied_utc=playbook._now_utc(), summary=summary, backup=str(dest)
    )
    u.save()
    print(
        f"Play updated ({summary}). The way back: {dest}. Next: gos pack update.",
        file=out,
    )
    return 0


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="update_play.py",
        description="Update GTNH-Play from the Lab the adopted pack update tested (WO-1.9). "
        "Clayton runs this himself. Without --apply it only prints the plan.",
    )
    parser.add_argument("--apply", action="store_true", help="write Play")
    parser.add_argument(
        "--update", default=None, help="update id (default: the open one)"
    )
    parser.add_argument("--config", type=Path, default=None, help="gos.toml to read")
    args = parser.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    return run(cfg, args.update, args.apply, out)
