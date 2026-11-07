"""The pack-update checklist and the "adopt?" decision (WO-1.9, plan 7.13, D44, D148, D149).

``gos pack update --start VERSION`` opens an update; each later ``gos pack update`` walks its
checklist from the first step not yet done, runs what it can, records every step, and stops at
the first one that waits for Clayton, saying exactly what to do. Steps, in order:

1. ``release``: the release summary (GT and NEI versions, mods changed) from DAXXL's manifests.
2. ``backup``: a world backup of Play from the last :data:`BACKUP_MAX_AGE_H` hours in
   ``GOS_HOME/backups/world/`` (the backup job's copies, plan 4.6), before anything changes.
3. ``lab``: Clayton updates GTNH-Lab to the new pack in Prism. Checked: Lab's fingerprint
   differs from Play's, and the plan 5.6 tweaks are in Lab (warned, not blocking).
4. ``lab_export``: Lab mirrored into Export (``gregtec.sync.plan_lab_to_export``), Export
   launched to autoexport (D100), the run collected and built into the update's own sandbox
   GOS_HOME (``updates/<id>/home/``), never into the main DB, so no tool answers from a pack
   that isn't adopted.
5. ``diff``: the run against the main DB's newest run (:mod:`.diff`), the GT pin check
   (:mod:`.gtpin`) and the plans it would affect (:mod:`.plans`), written to the update folder,
   ``GOS_HOME/data/reports/`` and, when the vault exists, an ``inbox/`` note. Then the
   **decision** is pending.
6. ``adopt``: waits until Clayton answers ``gos pack adopt yes|no`` (or the dashboard later).
   GOS never adopts by itself, and a ``yes`` is refused while the GT pin doesn't match.
7. ``play``: Clayton updates Play himself with ``GOS_APP\\scripts\\update_play.py``
   (:mod:`.playupdate`). Checked: Play's fingerprint now equals Lab's tested one, and Play has the
   tweaks.
8. ``play_export``: the normal export pipeline on Play (``gos export run``), collected and built
   into the main DB with Play's fingerprint.
9. ``flag``: plans citing a recipe or crop that changed or vanished get ``plan.affected_run``;
   the new pack becomes the adopted one (``data/packupdate/adopted.json``).

A **simulated** update (``--simulate``, the WO-1.9 and L10 dry run) walks the same steps, but
``update_play.py`` only plans for it (Play never changes), ``play_export`` is skipped, the flags
are the diff's preview, and nothing is adopted.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from gregtec import config, locks, playhooks, prism, sync, tweaks
from gregtec.db import access, build
from gregtec.ingest import collect, fingerprint, rundiff
from gregtec.packupdate import diff, gtpin, plans, releases
from gregtec.vault import fts
from gregtec.vault.io import VaultError, VaultIO

FORMAT = "gos-pack-update/1"
ROOT = Path("data", "packupdate")  # in GOS_HOME
UPDATES = ROOT / "updates"
ADOPTED = ROOT / "adopted.json"
WORLD_BACKUPS = Path("backups", "world")  # plan 4.3
REPORTS = Path("data", "reports")
BACKUP_MAX_AGE_H = 24
STEPS = (
    "release",
    "backup",
    "lab",
    "lab_export",
    "diff",
    "adopt",
    "play",
    "play_export",
    "flag",
)
TITLES = {
    "release": "Read the release summary",
    "backup": "Back up Play's world first",
    "lab": "Update GTNH-Lab to the new pack (Clayton, in Prism)",
    "lab_export": "Export from Lab (Lab -> Export, autoexport, sandbox DB)",
    "diff": "Diff Lab's export against Play's, check the GT pin, preview plan flags",
    "adopt": "Clayton adopts or declines the update",
    "play": "Update Play with GOS_APP's update_play.py (Clayton)",
    "play_export": "Export Play and load it into the DB",
    "flag": "Flag affected plans; record the adopted pack",
}
DONE, WAITING, FAILED, SKIPPED, PENDING = (
    "done",
    "waiting",
    "failed",
    "skipped",
    "pending",
)
PENDING_DECISION, ADOPTED_YES, DECLINED = "pending", "adopted", "declined"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class UpdateError(RuntimeError):
    pass


# --- state -----------------------------------------------------------------------------------


def updates_dir(home: Path) -> Path:
    return home / UPDATES


def _now_utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Update:
    folder: Path
    state: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.state["id"])

    @property
    def version(self) -> str:
        return str(self.state["version"])

    @property
    def simulated(self) -> bool:
        return bool(self.state.get("simulated"))

    @property
    def sandbox(self) -> Path:
        """The update's own GOS_HOME for the Lab run (data/exports, data/gos.sqlite)."""
        return self.folder / "home"

    def step(self, name: str) -> dict[str, Any]:
        steps: dict[str, Any] = self.state.setdefault("steps", {})
        return steps.setdefault(name, {"status": PENDING})

    def status(self, name: str) -> str:
        return str(self.step(name).get("status", PENDING))

    def decision(self) -> dict[str, Any]:
        d: dict[str, Any] = self.state.setdefault("decision", {"status": None})
        return d

    def finished(self) -> bool:
        return bool(self.state.get("finished_utc"))

    def save(self) -> None:
        fingerprint.atomic_write(
            self.folder / "state.json",
            json.dumps(self.state, indent=1).encode("utf-8"),
        )


def load(folder: Path) -> Update:
    try:
        state = json.loads((folder / "state.json").read_bytes())
    except (OSError, ValueError) as exc:
        raise UpdateError(f"can't read {folder / 'state.json'}: {exc}") from None
    if not isinstance(state, dict) or state.get("format") != FORMAT:
        raise UpdateError(f"{folder / 'state.json'} isn't a {FORMAT} state")
    return Update(folder, state)


def all_updates(home: Path) -> list[Update]:
    """Every update, oldest first."""
    root = updates_dir(home)
    out = []
    for folder in sorted(root.iterdir()) if root.is_dir() else ():
        try:
            out.append(load(folder))
        except UpdateError:
            continue
    out.sort(key=lambda u: (str(u.state.get("started_utc")), u.id))
    return out


def active(home: Path) -> Update | None:
    """The newest update not finished."""
    pending = [u for u in all_updates(home) if not u.finished()]
    return pending[-1] if pending else None


def find(home: Path, update_id: str | None) -> Update:
    if update_id is None:
        u = active(home)
        if u is None:
            raise UpdateError(
                "no pack update is open: start one with gos pack update --start VERSION"
            )
        return u
    if not _ID.fullmatch(update_id):
        raise UpdateError(f"not an update id: {update_id!r}")
    folder = updates_dir(home) / update_id
    if not folder.is_dir():
        raise UpdateError(f"no update {update_id} in {updates_dir(home)}")
    return load(folder)


def adopted(home: Path) -> dict[str, Any]:
    """The adopted pack: ``adopted.json``, else the repo's pins."""
    try:
        data = json.loads((home / ADOPTED).read_bytes())
        if isinstance(data, dict) and releases.version_key(str(data.get("version"))):
            return data
    except (OSError, ValueError):
        pass
    return {"version": config.PACK_PIN, "gt_version": config.GT_PIN, "run": None}


def start(
    home: Path, version: str, *, simulate: bool, now: float | None = None
) -> Update:
    """Open an update to ``version``. A real one must be a stable release newer than the
    adopted pack; a simulated one may be any label (``2.9.1-sim``)."""
    if active(home) is not None:
        u = active(home)
        assert u is not None
        raise UpdateError(
            f"update {u.id} is still open: finish it, or close it with "
            f"gos pack update --abandon"
        )
    current = str(adopted(home)["version"])
    if not simulate:
        new, base = releases.version_key(version), releases.version_key(current)
        if new is None or base is None or new <= base:
            raise UpdateError(
                f"{version} isn't a stable release newer than the adopted {current} "
                "(a dry run takes --simulate)"
            )
    if not _ID.fullmatch(version):
        raise UpdateError(f"{version!r}: use letters, digits, . _ and -")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    uid = f"{version}-{stamp}"
    folder = updates_dir(home) / uid
    folder.mkdir(parents=True, exist_ok=False)
    u = Update(
        folder,
        {
            "format": FORMAT,
            "id": uid,
            "version": version,
            "from_version": current,
            "simulated": simulate,
            "started_utc": _now_utc(),
            "steps": {s: {"status": PENDING} for s in STEPS},
            "decision": {"status": None},
        },
    )
    u.save()
    return u


def prune_sandboxes(home: Path) -> list[str]:
    """Delete the sandbox GOS_HOME (a DB and the collected Lab run) of every finished update;
    called when a new update starts, so the last one's stays to look at until then. Its
    ``state.json``, ``diff.txt`` and ``play-before/`` stay."""
    lines = []
    for u in all_updates(home):
        if u.finished() and u.sandbox.is_dir():
            try:
                shutil.rmtree(u.sandbox)
            except OSError as exc:
                lines.append(
                    f"couldn't delete {u.sandbox} ({exc}); the next start retries"
                )
            else:
                lines.append(f"deleted update {u.id}'s sandbox {u.sandbox}")
    return lines


def abandon(u: Update, why: str) -> None:
    if u.finished():
        raise UpdateError(_finished(u))
    u.state["finished_utc"] = _now_utc()
    u.state["outcome"] = f"abandoned: {why}"
    u.save()


# --- context ---------------------------------------------------------------------------------


def _tweak_problems(instance: Path) -> list[str]:
    try:
        results = tweaks.check(instance)
    except (OSError, ValueError) as exc:
        return [f"the tweaks can't be checked: {exc}"]
    return [
        f"{r.tweak} {r.status} in {r.file} {r.detail}".rstrip()
        for r in results
        if r.status not in ("ok", "n/a")
    ]


@dataclass
class Ctx:
    """What the steps touch, so tests can stand in for Prism, the network and the game."""

    cfg: config.Config
    out: TextIO
    fetch: releases.Fetch = releases.http_fetch
    launch: Callable[[Sequence[str]], None] = playhooks._start_prism
    clients: playhooks.Clients = prism.running_clients
    tweak_problems: Callable[[Path], list[str]] = _tweak_problems
    allow_harness: bool = False  # tests only: the harness's runs aren't game data
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    poll_s: float = playhooks.POLL_S
    now: Callable[[], float] = time.time

    @property
    def home(self) -> Path:
        return self.cfg.gos_home

    def say(self, text: str) -> None:
        print(f"GOS: {text}", file=self.out, flush=True)


@dataclass
class Outcome:
    status: str
    lines: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


def fingerprint_excludes(cfg: config.Config) -> tuple[str, ...]:
    """What Play-vs-Lab fingerprints leave out: Export's excludes (as Play's own fingerprint)
    and Lab's test-only files."""
    return tuple(
        dict.fromkeys([*cfg.sync_exclude["export"], *cfg.sync_exclude.get("lab", ())])
    )


def instance_fingerprint(cfg: config.Config, role: str) -> fingerprint.Fingerprint:
    return fingerprint.compute(
        cfg.instance(role), fingerprint_excludes(cfg), cfg.fingerprint_configs
    )


# --- the steps -------------------------------------------------------------------------------


def step_release(ctx: Ctx, u: Update) -> Outcome:
    state = releases.load_state(ctx.home)
    web = releases.Web(ctx.home, state, ctx.fetch, now=ctx.now)
    old = web.json(releases.MANIFEST_URL.format(version=u.state["from_version"]))
    new = web.json(releases.MANIFEST_URL.format(version=u.version))
    releases.save_state(ctx.home, state)
    lines: list[str] = []
    data: dict[str, Any] = {}
    if isinstance(old, dict) and isinstance(new, dict):
        d = releases.manifest_diff(u.state["from_version"], old, u.version, new)
        lines += d.lines()
        data = {"gt": d.gt[1], "nei": d.nei[1]}
        if d.gt[1] != config.GT_PIN:
            lines.append(
                f"GT changes ({config.GT_PIN} -> {d.gt[1]}): the GT pin bump "
                f"({gtpin.RUNBOOK}) comes before adopting"
            )
    elif u.simulated:
        lines.append(
            f"simulated update {u.version}: no release manifest, the Lab export shows what "
            "changed"
        )
    else:
        lines.append(
            f"DAXXL has no manifest for {u.version} (yet): the Lab export shows what changed"
        )
    lines += [f"note: {n}" for n in web.notes]
    return Outcome(DONE, lines, data)


def _backups(home: Path) -> list[Path]:
    """The world backups in ``home``, oldest first."""
    folder = home / WORLD_BACKUPS
    return sorted(
        (p for p in folder.glob("*.zip") if p.is_file()) if folder.is_dir() else (),
        key=lambda p: p.stat().st_mtime,
    )


def backup_problem(home: Path, u: Update, now: float) -> str | None:
    """Why Play may not be written now for want of a recent world backup, or None. Checked
    again when Play is written, not only at step 2: the newest backup file, or Clayton's hand
    confirm, must be from the last :data:`BACKUP_MAX_AGE_H` hours."""
    if u.status("backup") != DONE:
        return "its world backup step isn't done (gos pack update)"
    times = [p.stat().st_mtime for p in _backups(home)[-1:]]
    confirmed = u.step("backup").get("data", {}).get("confirmed_at")
    if isinstance(confirmed, int | float):
        times.append(float(confirmed))
    if times and (now - max(times)) / 3600 <= BACKUP_MAX_AGE_H:
        return None
    seen = f"the newest is {(now - max(times)) / 3600:.0f} h old" if times else "none"
    return (
        f"no world backup from the last {BACKUP_MAX_AGE_H} h ({seen}): make one first "
        "(/backup start in Play, then the backup job's copy in "
        f"{home / WORLD_BACKUPS}, or gos pack update --confirm backup for a copy made "
        "another way)"
    )


def step_backup(ctx: Ctx, u: Update) -> Outcome:
    folder = ctx.home / WORLD_BACKUPS
    zips = _backups(ctx.home)
    if zips:
        newest = zips[-1]
        age_h = (ctx.now() - newest.stat().st_mtime) / 3600
        if age_h <= BACKUP_MAX_AGE_H:
            return Outcome(
                DONE,
                [f"world backup {newest.name} ({age_h:.1f} h old) in {folder}"],
                {"backup": newest.name},
            )
    have = f"the newest is {zips[-1].name}" if zips else "there is none"
    return Outcome(
        WAITING,
        [
            f"no world backup from the last {BACKUP_MAX_AGE_H} h in {folder} ({have}).",
            (
                "Before anything changes: in Play, run /backup start (ServerUtilities), let the "
                "backup job copy it (GOS_APP\\scripts\\backup_world.ps1, every 30 min), then run "
                "gos pack update again. (A copy made another way: gos pack update --confirm backup.)"
            ),
        ],
    )


def step_lab(ctx: Ctx, u: Update) -> Outcome:
    try:
        lab = instance_fingerprint(ctx.cfg, "lab")
        play = instance_fingerprint(ctx.cfg, "play")
    except fingerprint.FingerprintError as exc:
        return Outcome(FAILED, [f"can't fingerprint Lab or Play: {exc}"])
    if lab.digest == play.digest:
        return Outcome(
            WAITING,
            [
                f"{ctx.cfg.lab} still has Play's mods, scripts and configs.",
                (
                    f"Update {ctx.cfg.lab} to GTNH {u.version} in Prism (Play stays as it is): "
                    "the new pack's mods/, config/ and scripts/, then the addons that load on it "
                    "(D02). Then run gos pack update again."
                ),
            ],
        )
    changes = fingerprint.changes(play.entries, lab.entries)
    fingerprint.atomic_write(u.folder / "lab.tsv", fingerprint.listing(lab.entries))
    lines = [
        (
            f"{ctx.cfg.lab} differs from Play: {changes.summary()} "
            f"(fingerprint {lab.digest[:12]})"
        ),
        *(f"  {row}" for row in changes.lines(limit=40)),
    ]
    problems = ctx.tweak_problems(ctx.cfg.instance("lab"))
    lines += [
        f"warning: Lab's tweak {p} (check_tweaks.py --apply on Lab fixes it)"
        for p in problems
    ]
    return Outcome(DONE, lines, {"lab_fingerprint": lab.digest})


def _lab_changed(ctx: Ctx, u: Update) -> str | None:
    want = u.step("lab").get("data", {}).get("lab_fingerprint")
    try:
        now = instance_fingerprint(ctx.cfg, "lab").digest
    except fingerprint.FingerprintError as exc:
        return f"can't fingerprint Lab: {exc}"
    if now != want:
        return (
            f"Lab changed since its check ({str(want)[:12]} -> {now[:12]}): run "
            "gos pack update --redo lab"
        )
    return None


def step_lab_export(ctx: Ctx, u: Update) -> Outcome:
    if config.running_as_dev():
        return Outcome(
            FAILED, ["the Lab export runs as Clayton (D75), like gos export run"]
        )
    why = _lab_changed(ctx, u)
    if why is not None:
        return Outcome(FAILED, [why])
    home = ctx.home
    try:
        lock = locks.InstanceLock(home).acquire(
            "export", f"gos pack update {u.id} (Lab export)"
        )
    except locks.LockHeld as exc:
        return Outcome(WAITING, [f"the instance lock is held: {exc}"])
    with lock:
        try:
            run = _lab_export_locked(ctx)
            # At once, complete or not: gos export collect and gos db build never take it into
            # the main DB, so no tool answers from a pack that isn't adopted (D148).
            collect.mark_lab_run(home, run.name)
        except playhooks.ExportFailed as exc:
            return Outcome(FAILED, [f"Lab export failed: {exc}"])
        except OSError as exc:
            return Outcome(FAILED, [f"Lab export: can't record it as a Lab run: {exc}"])
    problems = playhooks.run_problems(run.manifest)
    if problems:
        return Outcome(FAILED, [f"Lab export {run.name}: {'; '.join(problems)}"])
    try:
        export_game = fingerprint.game_folder(ctx.cfg.instance("export"))
        got = collect.collect(
            export_game,
            u.sandbox,
            2,
            allow_harness=ctx.allow_harness,
        )
        built = build.build(
            u.sandbox, got.run.run_id, keep=1, allow_harness=ctx.allow_harness
        )
    except (collect.CollectError, build.BuildError, OSError) as exc:
        return Outcome(FAILED, [f"Lab run {run.name} couldn't be loaded: {exc}"])
    return Outcome(
        DONE,
        [
            (
                f"Lab export {run.name} built into the update's sandbox DB {built.db} "
                "(the main DB is unchanged; gos export collect and gos db build skip this "
                f"run, listed in {home / collect.LAB_RUNS})"
            ),
            (
                f"note: {ctx.cfg.export} now holds Lab's mods; the next gos export run "
                "mirrors Play into it again"
            ),
        ],
        {"run": run.name},
    )


def _lab_export_locked(ctx: Ctx) -> playhooks.FoundRun:
    cfg = ctx.cfg
    try:
        running = ctx.clients(cfg)
    except OSError as exc:
        raise playhooks.ExportFailed(
            f"can't tell whether a GTNH client runs ({exc})"
        ) from None
    if running:
        raise playhooks.ExportFailed(
            f"{', '.join(cfg.instance(r).name for r in running)} is running; close it first"
        )
    if playhooks.launching(cfg.gos_home) is not None:
        raise playhooks.ExportFailed(
            "a client was just launched; try again once it quit"
        )
    plan, refusals = sync.plan_lab_to_export(cfg)
    if plan is None:
        raise playhooks.ExportFailed("sync refused: " + "; ".join(refusals))
    sync.report(plan, ctx.out)
    sync.report_install(plan, ctx.out)
    try:
        sync.apply(plan)
        sync.apply_install(plan)
    except OSError as exc:
        raise playhooks.ExportFailed(f"sync failed: {exc}") from None
    try:
        exports = rundiff.export_runs_folder(cfg)
    except fingerprint.FingerprintError as exc:
        raise playhooks.ExportFailed(str(exc)) from None
    before = set(playhooks.scan_runs(exports, allow_harness=True))
    argv = prism.launch_command(cfg, "export")
    ctx.say(f"launching: {' '.join(argv)}")
    try:
        ctx.launch(argv)
    except OSError as exc:
        raise playhooks.ExportFailed(f"can't start Prism: {exc}") from None
    return playhooks.wait_for_export(
        cfg,
        exports,
        before,
        ctx.out,
        ctx.clients,
        allow_harness=ctx.allow_harness,
        clock=ctx.clock,
        sleep=ctx.sleep,
        poll_s=ctx.poll_s,
    )


def _all_plans(
    ctx: Ctx, u: Update, conn: sqlite3.Connection
) -> tuple[list[plans.Plan], list[str]]:
    found, problems = plans.from_db(conn, ctx.home)
    extra, more = plans.read_folder(u.folder / "fixture-plans")
    known = {p.id for p in found}
    return [*found, *(p for p in extra if p.id not in known)], problems + more


def step_diff(ctx: Ctx, u: Update) -> Outcome:
    main_db = access.db_path(ctx.home)
    run_key = u.step("lab_export").get("data", {}).get("run")
    try:
        old = diff.side(main_db, None, ctx.home)
        new = diff.side(u.sandbox / access.DB_FILE, run_key, u.sandbox)
    except diff.DiffError as exc:
        return Outcome(FAILED, [f"{exc}: Play's export must be in the main DB first"])
    lab = None
    try:
        lab = fingerprint.parse_listing((u.folder / "lab.tsv").read_bytes())
    except (OSError, ValueError):
        pass
    s = diff.summarise(
        old,
        new,
        play_old=fingerprint.run_entries(ctx.home, old.run_key),
        play_new=lab,
    )
    main = diff.connect(main_db)
    sand = diff.connect(new.db)
    try:
        found, problems = _all_plans(ctx, u, main)
        flagged = plans.check(
            found,
            lambda key: plans.view(main, key),
            plans.RunView(sand, new.run_id, new.run_key),
        )
    finally:
        main.close()
        sand.close()
    plan_lines = plans.lines(flagged, len(found), problems)
    text = diff.report(s, plan_lines)
    report_path = u.folder / "diff.txt"
    fingerprint.atomic_write(report_path, text.encode("utf-8"))
    copy = ctx.home / REPORTS / f"pack-diff-{old.run_key}-{new.run_key}.txt"
    fingerprint.atomic_write(copy, text.encode("utf-8"))
    ids = [f.plan.id for f in flagged]
    lines = [s.headline(), *plan_lines, f"report: {report_path} (and {copy})"]
    if s.gt_problem is not None:
        lines.insert(0, f"!!! {s.gt_problem}")
    note = _vault_note(ctx, u, s, ids)
    if note:
        lines.append(note)
    d = u.decision()
    d.update(
        status=PENDING_DECISION,
        asked_utc=_now_utc(),
        question=f"Adopt GTNH {u.version}? {s.headline()}",
    )
    return Outcome(
        DONE,
        lines,
        {
            "old_run": old.run_key,
            "new_run": new.run_key,
            "gt_old": old.gt_version,
            "gt_new": new.gt_version,
            "gt_problem": s.gt_problem,
            "flagged_preview": ids,
            "recipes": [
                len(s.recipes.added),
                len(s.recipes.removed),
                len(s.recipes.changed),
            ],
        },
    )


def _vault_note(ctx: Ctx, u: Update, s: diff.Summary, flagged: list[str]) -> str | None:
    root = ctx.home / "vault"
    if not root.is_dir():
        return None
    title = f"Pack update {u.version}: adopt? ({s.old.run_key} -> {s.new.run_key})"
    body = [
        f"update: {u.id}",
        f"simulated: {'yes' if u.simulated else 'no'}",
        *diff.note_lines(s, flagged),
        f"note: answer with gos pack adopt yes or no; the report is {u.folder / 'diff.txt'}",
    ]
    db = access.db_path(ctx.home)

    def mirror(rel: str) -> None:  # searchable at once, as the agents' notes (WO-2.5)
        fts.mirror_one(db, root, rel)

    try:
        vault = VaultIO(root, on_write=mirror)
        return f"vault note: {vault.propose(title, '\n'.join(body))}"
    except (VaultError, OSError) as exc:
        return f"warning: no vault note ({exc})"


def question(u: Update) -> list[str]:
    d = u.decision()
    data = u.step("diff").get("data", {})
    lines = [
        f"ADOPT? {d.get('question')}",
        f"  The report: {u.folder / 'diff.txt'}",
        f"  Plans the update would flag: {', '.join(data.get('flagged_preview') or []) or 'none'}",
    ]
    if data.get("gt_problem"):
        lines.append(f"  !!! {data['gt_problem']}")
    lines.append(
        "  Answer: gos pack adopt yes (then update Play with update_play.py) or "
        "gos pack adopt no (Play stays on the adopted pack). GOS never adopts by itself."
    )
    return lines


def step_adopt(ctx: Ctx, u: Update) -> Outcome:
    status = u.decision().get("status")
    if status == ADOPTED_YES:
        return Outcome(
            DONE,
            [f"adopted by Clayton at {u.decision().get('answered_utc')}"],
        )
    if status == DECLINED:
        return Outcome(DONE, ["declined by Clayton"], {"declined": True})
    return Outcome(WAITING, question(u))


def answer(home: Path, u: Update, yes: bool, note: str = "") -> list[str]:
    """Record Clayton's answer to "adopt?". Refused as gos-dev (Clayton decides, D44), before
    the diff, and for ``yes`` while the Lab export's GT doesn't match the pin (a simulated
    update records the mismatch and goes on)."""
    if config.running_as_dev():
        raise UpdateError(f"{config.DEV_ACCOUNT} can't answer: Clayton decides (D44)")
    if u.finished():
        raise UpdateError(_finished(u))
    d = u.decision()
    if d.get("status") != PENDING_DECISION:
        raise UpdateError(
            f"update {u.id} has no pending question (decision: {d.get('status')}); "
            "gos pack update runs the steps before it"
        )
    lines = []
    # Checked against the pin as it is now (a pin bump deployed since the diff counts), and
    # refused when the diff recorded no GT version at all.
    gt_problem = gtpin.problem(u.step("diff").get("data", {}).get("gt_new"))
    if yes and gt_problem is not None:
        if not u.simulated:
            raise UpdateError(
                f"not adopted: {gt_problem}. Do the GT pin bump, deploy it (gos deploy), "
                "then answer again"
            )
        lines.append(f"simulated: a real update would be refused here: {gt_problem}")
    d.update(
        status=ADOPTED_YES if yes else DECLINED,
        answered_utc=_now_utc(),
        by="Clayton (gos pack adopt)",
        note=note,
    )
    u.save()
    lines.append(
        f"update {u.id}: {'adopted' if yes else 'declined'}; gos pack update goes on"
    )
    return lines


def step_play(ctx: Ctx, u: Update) -> Outcome:
    script = f"{ctx.cfg.gos_app}\\current\\scripts\\update_play.py"
    python = f"{ctx.cfg.gos_app}\\current\\.venv\\Scripts\\python.exe"
    data = u.step("play").get("data", {})
    if u.simulated:
        if data.get("dry_run_utc"):
            return Outcome(
                DONE,
                [
                    (
                        f"simulated: update_play.py planned at {data['dry_run_utc']} "
                        f"({data.get('summary')}); Play was not changed"
                    )
                ],
                data,
            )
        return Outcome(
            WAITING,
            [
                (
                    "simulated update: Play is never changed. Run the script's dry run to check "
                    f"its plan: {python} {script}"
                ),
                "Then run gos pack update again.",
            ],
        )
    try:
        play = instance_fingerprint(ctx.cfg, "play")
    except fingerprint.FingerprintError as exc:
        return Outcome(FAILED, [f"can't fingerprint Play: {exc}"])
    want = u.step("lab").get("data", {}).get("lab_fingerprint")
    if play.digest != want:
        return Outcome(
            WAITING,
            [
                (
                    f"Play doesn't match the tested Lab yet ({play.digest[:12]}, Lab "
                    f"{str(want)[:12]})."
                ),
                f"Close Play, then run (as yourself, never as {config.DEV_ACCOUNT}):",
                f"  {python} {script}            (dry run: shows the plan)",
                f"  {python} {script} --apply    (backs up what it replaces, then updates)",
                (
                    "Match Play's components (Forge, LWJGL, Java) to Lab's in Prism if the script "
                    "says they differ. Then run gos pack update again."
                ),
            ],
        )
    problems = ctx.tweak_problems(ctx.cfg.instance("play"))
    if problems:
        return Outcome(
            WAITING,
            [
                "Play matches Lab, but the plan 5.6 tweaks are missing:",
                *(f"  {p}" for p in problems),
                (
                    f"Run {python} {ctx.cfg.gos_app}\\current\\scripts\\check_tweaks.py --apply "
                    f"{ctx.cfg.instance('play')} (yourself), then gos pack update again."
                ),
            ],
        )
    return Outcome(DONE, [f"Play matches the tested Lab ({play.digest[:12]})"], data)


def step_play_export(ctx: Ctx, u: Update) -> Outcome:
    if u.simulated:
        return Outcome(SKIPPED, ["simulated update: Play unchanged, nothing to export"])
    code = playhooks.run_export(
        ctx.cfg,
        ctx.out,
        launch=ctx.launch,
        clients=ctx.clients,
        allow_harness=ctx.allow_harness,
        clock=ctx.clock,
        sleep=ctx.sleep,
        poll_s=ctx.poll_s,
    )
    last = fingerprint.last_export(ctx.home)
    if code != 0 or last is None:
        return Outcome(
            FAILED, ["Play's export failed (above); gos pack update retries"]
        )
    try:
        got = collect.collect(
            fingerprint.game_folder(ctx.cfg.instance("export")),
            ctx.home,
            ctx.cfg.keep_collected,
            allow_harness=ctx.allow_harness,
        )
        if got.run.run_id != last["run_id"]:
            return Outcome(
                FAILED,
                [
                    f"collected {got.run.run_id}, but the recorded export is {last['run_id']}"
                ],
            )
        built = build.build(
            ctx.home,
            got.run.run_id,
            keep=ctx.cfg.keep_runs,
            fingerprint=str(last["fingerprint"]),
            allow_harness=ctx.allow_harness,
        )
    except (collect.CollectError, build.BuildError, OSError) as exc:
        return Outcome(FAILED, [f"Play's export couldn't be loaded: {exc}"])
    return Outcome(
        DONE,
        [f"Play's export {got.run.run_id} built into {built.db}"],
        {"run": got.run.run_id},
    )


def step_flag(ctx: Ctx, u: Update) -> Outcome:
    if u.simulated:
        ids = u.step("diff").get("data", {}).get("flagged_preview") or []
        return Outcome(
            DONE,
            [
                "simulated: the plans the diff flagged (not written; nothing adopted): "
                + (", ".join(ids) or "none")
            ],
            {"flagged": ids},
        )
    run_key = u.step("play_export").get("data", {}).get("run")
    db = access.db_path(ctx.home)
    try:
        new = diff.side(db, run_key, ctx.home)
    except diff.DiffError as exc:
        return Outcome(FAILED, [str(exc)])
    lines = []
    problem = gtpin.problem(new.gt_version)
    if problem is not None:
        lines.append(f"!!! {problem}")
    conn = access.connect(db)
    try:
        found, problems = plans.from_db(conn, ctx.home)
        flagged = plans.check(
            found,
            lambda key: plans.view(conn, key),
            plans.RunView(conn, new.run_id, new.run_key),
        )
        n = plans.mark_affected(conn, [f.plan.id for f in flagged], new.run_id)
    finally:
        conn.close()
    lines += plans.lines(flagged, len(found), problems)
    lines.append(f"{n} plans marked affected by run {new.run_key}")
    fingerprint.atomic_write(
        ctx.home / ADOPTED,
        json.dumps(
            {
                "version": u.version,
                "gt_version": new.gt_version,
                "run": new.run_key,
                "update": u.id,
                "adopted_utc": _now_utc(),
            },
            indent=1,
        ).encode("utf-8"),
    )
    lines.append(f"GTNH {u.version} is the adopted pack ({ctx.home / ADOPTED})")
    lines.append(
        "Re-check the golden recipes against NEI in the Lab (G1) when they exist."
    )
    return Outcome(DONE, lines, {"flagged": [f.plan.id for f in flagged]})


STEP_FUNCS: Mapping[str, Callable[[Ctx, Update], Outcome]] = {
    "release": step_release,
    "backup": step_backup,
    "lab": step_lab,
    "lab_export": step_lab_export,
    "diff": step_diff,
    "adopt": step_adopt,
    "play": step_play,
    "play_export": step_play_export,
    "flag": step_flag,
}


# --- walking ---------------------------------------------------------------------------------


def _record(u: Update, name: str, o: Outcome) -> None:
    s = u.step(name)
    s.update(status=o.status, utc=_now_utc(), lines=o.lines)
    if o.data:
        s["data"] = {**s.get("data", {}), **o.data}
    u.save()


def _finished(u: Update) -> str:
    return (
        f"update {u.id} is finished ({u.state.get('outcome')}); start a new one with "
        "gos pack update --start VERSION"
    )


def confirm(u: Update, name: str, note: str, now: float | None = None) -> None:
    """Clayton marks the backup step done by hand (a backup made another way). Its time is
    recorded: like a backup file, it counts for :data:`BACKUP_MAX_AGE_H` hours."""
    if name != "backup":
        raise UpdateError("only the backup step can be confirmed by hand")
    if config.running_as_dev():
        raise UpdateError(f"{config.DEV_ACCOUNT} can't confirm steps for Clayton")
    if u.finished():
        raise UpdateError(_finished(u))
    _record(
        u,
        name,
        Outcome(
            DONE,
            [f"confirmed by Clayton: {note or 'no note'}"],
            {"confirmed_at": time.time() if now is None else now},
        ),
    )


def redo(u: Update, name: str) -> None:
    """Mark ``name`` and every step after it to run again (the decision too, from diff on)."""
    if u.finished():
        raise UpdateError(_finished(u))
    i = STEPS.index(name)
    for later in STEPS[i:]:
        u.state["steps"][later] = {"status": PENDING}
    if i <= STEPS.index("diff"):
        u.state["decision"] = {"status": None}
    u.save()


def walk(ctx: Ctx, u: Update) -> int:
    """Run steps from the first not done. 0: finished; 3: waiting for Clayton; 1: failed."""
    for i, name in enumerate(STEPS, start=1):
        if u.status(name) in (DONE, SKIPPED):
            continue
        ctx.say(f"step {i}/{len(STEPS)} {name}: {TITLES[name]}")
        o = STEP_FUNCS[name](ctx, u)
        _record(u, name, o)
        for line in o.lines:
            print(f"  {line}", file=ctx.out)
        if o.status not in (DONE, SKIPPED):
            ctx.say(
                f"step {name} is {o.status}; run gos pack update again once it can go on"
            )
            return 3 if o.status == WAITING else 1
        if name == "adopt" and o.data.get("declined"):
            u.state["finished_utc"] = _now_utc()
            u.state["outcome"] = "declined"
            u.save()
            ctx.say(
                f"update {u.id} declined: Play stays on {u.state['from_version']}. Put "
                f"Lab back with sync_instances.py --only lab when you like."
            )
            return 0
    u.state["finished_utc"] = _now_utc()
    u.state["outcome"] = "simulated" if u.simulated else "adopted"
    u.save()
    ctx.say(f"update {u.id}: every step done ({u.state['outcome']}).")
    return 0


def status_lines(u: Update) -> list[str]:
    head = (
        f"Pack update {u.id}: GTNH {u.state['from_version']} -> {u.version}"
        + (" (SIMULATED)" if u.simulated else "")
        + (f", {u.state.get('outcome')}" if u.finished() else "")
    )
    out = [head]
    for i, name in enumerate(STEPS, start=1):
        s = u.step(name)
        out.append(f"  {i}. [{s.get('status')}] {TITLES[name]}")
    d = u.decision()
    if d.get("status") == PENDING_DECISION:
        out += question(u)
    elif d.get("status"):
        out.append(f"  decision: {d.get('status')} at {d.get('answered_utc')}")
    return out


def copy_fixture_plans(u: Update, source: Path) -> list[str]:
    """Put the ``gos-plan/0`` plans in ``source`` into the update (dry runs: checked at the
    diff, never written to the DB)."""
    dest = u.folder / "fixture-plans"
    dest.mkdir(exist_ok=True)
    lines = []
    for path in sorted(source.glob("*.json")):
        try:
            plan = plans.read_plan(path)
        except (OSError, ValueError) as exc:
            lines.append(f"not copied: {exc}")
            continue
        shutil.copyfile(path, dest / f"{plan.id}.json")
        lines.append(
            f"fixture plan {plan.id} ({len(plan.cites)} cites, run {plan.run})"
        )
    return lines
