r"""Update Play from the tested Lab: the script Clayton runs himself (WO-1.9, D149).

    GOS_APP\current\.venv\Scripts\python.exe GOS_APP\current\scripts\update_play.py
    ... update_play.py --apply [--update ID] [--config PATH]
    ... update_play.py --rollback [--update ID] [--config PATH]

CLAUDE.md: anything that touches Play is a script Clayton runs from GOS_APP; Claude Code writes
it and tests it on temp folders and Lab copies, never on Play. It mirrors Lab's ``mods/``,
``config/`` and ``scripts/`` into Play the way ``sync_instances.py`` mirrors Play into Lab
(:func:`gregtec.sync.plan_target`, Lab's ``[sync.lab] exclude`` files never copied or deleted),
so Play ends with exactly the mod set the Lab export tested. Worlds, saves, options and every
other folder are never touched; Prism's components (``mmc-pack.json``: Forge, LWJGL, Java) are
left for Clayton to match in Prism, and the script says when they differ.

Without ``--apply`` it only prints the plan (a simulated update records that dry run, which is
its ``play`` step). ``--apply`` is refused unless: it runs as Clayton, not gos-dev; the update
is open (not finished or abandoned), adopted (``gos pack adopt yes``) and isn't simulated; a
world backup from the last 24 hours is there (checked again now, not only at the backup step);
Play wasn't already updated for it (``--rollback`` first); Lab still has the fingerprint its
export was tested with; no GTNH client runs; and it gets the instance lock (no export at the
same time).

The way back: before writing, every Play file it replaces or deletes is copied to
``updates/<id>/play-before/``, with ``manifest.json`` listing those files, the files and folders
the update adds (with each added file's SHA-256) and the folders it removes. Then the plan is
applied with sync's temp-file-and-rename writes. ``--rollback`` puts Play back from that folder:
it restores the copied files, deletes the added ones (refusing, before writing anything, if one
was changed since) and the folders the update made, re-makes the ones it removed, then renames
the folder ``play-before-rolled-back-<UTC>``. It has the same account, client and lock checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from gregtec import config, locks, playhooks, prism, sync
from gregtec.gamestate.safe_open import read_bytes, sha256_file
from gregtec.ingest import fingerprint
from gregtec.packupdate import playbook

BEFORE = "play-before"
MANIFEST = "manifest.json"
FORMAT = "gos-play-before/1"


def plan_update(cfg: config.Config) -> tuple[sync.Plan | None, list[str]]:
    """Play's plan with Lab as the source. Reads only."""
    refusals: list[str] = []
    play = sync._instance(cfg.instance("play"), "Play", refusals)
    lab = sync._instance(cfg.instance("lab"), "Lab", refusals)
    if play is None or lab is None:
        return None, refusals
    if sync._overlap(play[0], lab[0]):
        refusals.append(f"{lab[0]} is Play, is inside it or holds it")
    if refusals:
        return None, refusals
    plan = sync.plan_target(
        "play", lab[1], play[1], cfg.sync_exclude.get("lab", ()), refusals, "Lab"
    )
    return (None if refusals else plan), refusals


def _mmc_differs(cfg: config.Config) -> bool:
    try:
        return read_bytes(cfg.instance("play") / fingerprint.MMC_PACK) != read_bytes(
            cfg.instance("lab") / fingerprint.MMC_PACK
        )
    except OSError:
        return False


def _account_and_clients(cfg: config.Config, clients: playhooks.Clients) -> list[str]:
    out = []
    if config.running_as_dev():
        out.append(
            f"update_play.py writes Play: it runs as Clayton, never as {config.DEV_ACCOUNT}"
        )
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


def refusals_for_apply(
    cfg: config.Config,
    u: playbook.Update,
    clients: playhooks.Clients,
    now: float | None = None,
) -> list[str]:
    out = _account_and_clients(cfg, clients)
    if u.finished():
        out.append(f"update {u.id} is finished ({u.state.get('outcome')})")
    if u.simulated:
        out.append(f"update {u.id} is simulated: Play is never changed for it")
    if u.decision().get("status") != playbook.ADOPTED_YES:
        out.append(f"update {u.id} isn't adopted (gos pack adopt yes comes first)")
    backup = playbook.backup_problem(
        cfg.gos_home, u, time.time() if now is None else now
    )
    if backup is not None:
        out.append(backup)
    if (u.folder / BEFORE / MANIFEST).is_file():
        out.append(
            f"Play was already updated for {u.id} ({u.folder / BEFORE}): "
            "update_play.py --rollback first to apply it again"
        )
    want = u.step("lab").get("data", {}).get("lab_fingerprint")
    try:
        lab = playbook.instance_fingerprint(cfg, "lab").digest
        if lab != want:
            out.append(
                "Lab changed since its export was tested: gos pack update --redo lab"
            )
    except fingerprint.FingerprintError as exc:
        out.append(f"can't fingerprint Lab: {exc}")
    return out


def _rel(plan: sync.Plan, path: Path) -> str:
    return path.relative_to(plan.game).as_posix()


def backup_before(plan: sync.Plan, dest: Path, update_id: str) -> dict[str, Any]:
    """Copy each Play file the plan replaces or deletes to ``dest``, then write ``dest``'s
    manifest (last, so a manifest means every copy is there). Returns the manifest."""
    replaced = [_rel(plan, t) for _, t in plan.update]
    deleted = [_rel(plan, p) for p in plan.delete]
    for rel in replaced + deleted:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(read_bytes(plan.game / rel))
    manifest = {
        "format": FORMAT,
        "update": update_id,
        "written_utc": playbook._now_utc(),
        "game": str(plan.game),
        "replaced": replaced,
        "deleted": deleted,
        # What the update adds: deleted again by --rollback, unless changed since.
        "added": {
            _rel(plan, t): sha256_file(plan.source / f.rel) for f, t in plan.copy
        },
        "made_dirs": [_rel(plan, p) for p in plan.mkdir],  # parents first
        "removed_dirs": [_rel(plan, p) for p in plan.rmdir],  # deepest first
    }
    fingerprint.atomic_write(
        dest / MANIFEST, json.dumps(manifest, indent=1).encode("utf-8")
    )
    return manifest


def _summary(plan: sync.Plan) -> str:
    return (
        f"{len(plan.copy)} copied, {len(plan.update)} replaced, "
        f"{len(plan.delete)} deleted"
    )


def run(
    cfg: config.Config,
    update_id: str | None,
    apply: bool,
    out: TextIO,
    clients: playhooks.Clients = prism.running_clients,
    now: Callable[[], float] = time.time,
) -> int:
    """0: planned (or applied); 1: refused or failed."""
    try:
        u = playbook.find(cfg.gos_home, update_id)
    except playbook.UpdateError as exc:
        print(f"refused: {exc}", file=out)
        return 1
    if u.finished():
        print(
            f"refused: update {u.id} is finished ({u.state.get('outcome')}); "
            "only --rollback works on it",
            file=out,
        )
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
    summary = _summary(plan)
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
    problems = refusals_for_apply(cfg, u, clients, now())
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
            backup_before(plan, dest, u.id)
        except OSError as exc:
            print(f"refused: can't back up Play's files to {dest}: {exc}", file=out)
            return 1
        print(
            f"backed up {len(plan.update) + len(plan.delete)} Play files to {dest}",
            file=out,
        )
        try:
            sync.apply(plan)
        except OSError as exc:
            print(
                f"FAILED partway: {exc}. Put Play back with update_play.py --rollback "
                f"--update {u.id} (or restore from the world backup and Prism) before "
                "playing.",
                file=out,
            )
            return 1
    step.setdefault("data", {}).update(
        applied_utc=playbook._now_utc(), summary=summary, backup=str(dest)
    )
    u.save()
    print(
        f"Play updated ({summary}). The way back: update_play.py --rollback --update "
        f"{u.id}. Next: gos pack update.",
        file=out,
    )
    return 0


def _read_before(dest: Path) -> dict[str, Any]:
    try:
        m = json.loads((dest / MANIFEST).read_bytes())
    except FileNotFoundError:
        raise ValueError(
            f"{dest / MANIFEST} doesn't exist: Play wasn't updated for this update"
        ) from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"can't read {dest / MANIFEST}: {exc}") from None
    lists = ("replaced", "deleted", "made_dirs", "removed_dirs")
    if (
        not isinstance(m, dict)
        or m.get("format") != FORMAT
        or not isinstance(m.get("added"), dict)
        or not all(
            isinstance(m.get(k), list) and all(isinstance(x, str) for x in m[k])
            for k in lists
        )
    ):
        raise ValueError(f"{dest / MANIFEST} isn't a {FORMAT} manifest")
    for rel in [*m["added"], *(x for k in lists for x in m[k])]:
        parts = rel.split("/")
        if not rel or rel.startswith("/") or ".." in parts or ":" in rel:
            raise ValueError(f"{dest / MANIFEST} names a path outside Play: {rel!r}")
    return m


def rollback_problems(m: dict[str, Any], dest: Path, game: Path) -> list[str]:
    """Why Play can't be put back from ``dest`` (checked before anything is written)."""
    out = []
    if os.path.normcase(str(Path(m.get("game", "")))) != os.path.normcase(str(game)):
        out.append(f"the backup is of {m.get('game')}, but Play is {game}")
        return out
    for rel in m["replaced"] + m["deleted"]:
        if not (dest / rel).is_file():
            out.append(f"{dest / rel} is missing from the backup")
    for rel, digest in m["added"].items():
        path = game / rel
        if path.is_file() and sha256_file(path) != digest:
            out.append(
                f"{path} was changed since the update (move it out of Play by hand if "
                "it should go)"
            )
    return out


def rollback(
    cfg: config.Config,
    update_id: str | None,
    out: TextIO,
    clients: playhooks.Clients = prism.running_clients,
) -> int:
    """Put Play back as it was before ``--apply``. 0: done; 1: refused or failed."""
    try:
        u = playbook.find(cfg.gos_home, update_id)
    except playbook.UpdateError as exc:
        print(f"refused: {exc}", file=out)
        return 1
    dest = u.folder / BEFORE
    refusals: list[str] = []
    play = sync._instance(cfg.instance("play"), "Play", refusals)
    problems = _account_and_clients(cfg, clients) + refusals
    try:
        m = _read_before(dest)
    except ValueError as exc:
        problems.append(str(exc))
    else:
        if play is not None:
            problems += rollback_problems(m, dest, play[1])
    if problems or play is None:
        print("refused: " + "; ".join(problems), file=out)
        return 1
    game = play[1]
    try:
        lock = locks.InstanceLock(cfg.gos_home).acquire(
            "play-update", f"update_play.py --rollback ({u.id})"
        )
    except locks.LockHeld as exc:
        print(f"refused: {exc}", file=out)
        return 1
    with lock:
        try:
            for rel in reversed(m["removed_dirs"]):  # parents first
                (game / rel).mkdir(parents=True, exist_ok=True)
            for rel in m["replaced"] + m["deleted"]:
                target = game / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sync._write(target, read_bytes(dest / rel))
            for rel in m["added"]:
                (game / rel).unlink(missing_ok=True)
        except OSError as exc:
            print(
                f"FAILED partway: {exc}. Nothing in {dest} was changed: close what holds "
                "the file and run --rollback again.",
                file=out,
            )
            return 1
        kept = []
        for rel in reversed(m["made_dirs"]):  # deepest first
            try:
                (game / rel).rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                kept.append(rel)  # not empty: something else was put there since
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        done = u.folder / f"{BEFORE}-rolled-back-{stamp}"
        os.replace(dest, done)
    print(
        f"Play is back as before update {u.id}: {len(m['replaced'])} restored, "
        f"{len(m['deleted'])} put back, {len(m['added'])} added files deleted "
        f"(the backup is now {done})",
        file=out,
    )
    for rel in kept:
        print(f"  kept {game / rel}: it isn't empty", file=out)
    u.step("play").setdefault("data", {}).update(
        rolled_back_utc=playbook._now_utc(), backup=str(done)
    )
    u.save()
    if u.finished():
        print(
            f"note: update {u.id} was finished ({u.state.get('outcome')}). If it was "
            "adopted, GOS still records that pack and Play's export of it: run gos export "
            "run, gos export collect and gos db build so the DB matches Play again.",
            file=out,
        )
    else:
        playbook.redo(u, "play")
        print(
            "Next: gos pack update (it waits on the Play step again), or gos pack update "
            "--abandon.",
            file=out,
        )
    return 0


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        prog="update_play.py",
        description="Update GTNH-Play from the Lab the adopted pack update tested (WO-1.9). "
        "Clayton runs this himself. Without --apply it only prints the plan.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write Play")
    mode.add_argument(
        "--rollback",
        action="store_true",
        help="put Play back as it was before --apply (from the update's play-before)",
    )
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
    if args.rollback:
        return rollback(cfg, args.update, out)
    return run(cfg, args.update, args.apply, out)
