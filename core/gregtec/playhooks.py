"""Play's Prism hooks and the ask-on-quit export (WO-1.4, plan section 5.2 step 4, D69, D121).

Prism runs ``GOS_APP\\scripts\\prism_prelaunch.ps1`` and ``prism_postexit.ps1`` for Play (set by
``scripts/install_prism_hooks.py``, which Clayton runs himself, through a bootstrap that lets Play
start when they are missing: ``gregtec.prism.bootstrap``); they run ``gos hook pre-launch`` and
``gos hook post-exit`` from GOS_APP's release, as Clayton.

**Pre-launch** (any instance with the hooks): while the instance lock is held, or while an Export
client runs, it exits :data:`EXIT_REFUSE` and the wrapper passes that to Prism, which then doesn't
start the game (V25). For Play it then fingerprints Play and records the stale flag (D120). Any
other failure only warns: Play still launches (plan 0.1 principle 9). It marks the launch
(:data:`LAUNCHING`) before checking the lock, so an export started before Prism has started the
client's Java refuses instead of running beside it; post-exit removes the mark.

**Post-exit** (Play only; Prism runs it only after a clean exit): if Play's fingerprint equals the
last export's, nothing runs. Otherwise, with ``[policy] export_on_quit = "ask"`` (D69), it asks
"Mods changed. Export now?" in a dialog that times out after 5 minutes as a no (plan 4.2). The
dashboard and voice take that question over in WO-2.6 and Phase 5. On yes it starts
``gos export run`` as a separate process, waits until that holds the instance lock, and returns,
so Prism is free and Play can't launch until the export is done.

**``gos export run``**: hold the instance lock; refuse while any GTNH client runs or was just
launched; fingerprint Play (before sync); sync Play to Export (``gregtec.sync``, Export only,
GregtecLink included, D98); ``prismlauncher --launch GTNH-Export``, which autoexports (D100); wait
for a new complete run in Export's ``gregteclink/exports/`` and for Export to quit; record the
fingerprint for that run, unless NEI's part isn't ok, the oredict part failed (D145) or the
autoexport failed (:func:`run_problems`: then recipe data stays stale); write the diff against the previous export (D122) to
``GOS_HOME/data/reports/``. Then release the lock and show the result, part warnings included.
Collecting the run into GOS_HOME (WO-1.2) and loading it into the DB (WO-1.3) join this pipeline
once those are merged; flagging affected plans comes with plans (WO-1.9).
"""

from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import secrets
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from gregtec import config, control, locks, prism, sync
from gregtec.gamestate.safe_open import read_bytes, read_text
from gregtec.ingest import fingerprint, rundiff

# pre-launch: don't start this game (the wrapper passes only this code to Prism)
EXIT_REFUSE = 3
ASK_TIMEOUT_S = 300  # plan 4.2: the post-exit prompt times out after 5 minutes
LOCK_WAIT_S = 60.0  # post-exit: how long the started export may take to hold the lock
START_TIMEOUT_S = 600.0  # Export must start (or a run appear) within 10 minutes
# The whole export. GregtecLink's own deadline is 30 minutes in the world (D100).
DEADLINE_S = 3600.0
# Export still running this long after its run appeared: go on anyway.
QUIT_WAIT_S = 600.0
# The processes can't be listed: go on this long after the run appeared.
UNKNOWN_GRACE_S = 120.0
POLL_S = 10.0  # one process listing (about a second of PowerShell) per poll
REPORTS = Path("data", "reports")  # in GOS_HOME
LOGS = Path("logs")
MMC_PACK = "mmc-pack.json"
# Written by pre-launch, removed by post-exit: a client may be starting. Prism runs its account
# and update steps after pre-launch, so its Java appears only later; meanwhile ``gos export run``
# refuses. After this long the launch has started its Java, or failed.
LAUNCHING = Path("data", "locks", "launching.json")  # in GOS_HOME
LAUNCH_GRACE_S = 300.0

# The instances whose launch starts the game watchers and voice (D259): Lab's for the Lab test.
WATCHED = ("play", "lab")
TELL_TIMEOUT_S = 3.0  # never hold the launch for long
# (cfg, "launch" | "exit", role, out) -> whether the supervisor took it
Tell = Callable[[config.Config, str, str, TextIO], bool]
# (title, question, timeout in s) -> yes, no, or None for timed out
Ask = Callable[[str, str, int], bool | None]
Clients = Callable[[config.Config], list[str]]


def _say(out: TextIO, text: str) -> None:
    print(f"GOS: {text}", file=out, flush=True)


def role_of(cfg: config.Config, inst_dir: str | None) -> str | None:
    """Which configured instance ``inst_dir`` (Prism's ``INST_DIR``) is, if any."""
    if not inst_dir:
        return None
    for role in ("play", "export", "lab"):
        folder = cfg.instance(role)
        try:
            if os.path.samefile(inst_dir, folder):
                return role
        except OSError:
            if os.path.normcase(os.path.abspath(inst_dir)) == os.path.normcase(
                os.path.abspath(folder)
            ):
                return role
    return None


# --- pre-launch ----------------------------------------------------------------------------


def pre_launch(
    cfg: config.Config,
    role: str | None,
    out: TextIO,
    clients: Clients = prism.running_clients,
    tell: Tell | None = None,
) -> int:
    """0: launch; :data:`EXIT_REFUSE`: don't. Never raises for a failed stale check."""
    if role == "export":
        _say(out, "Export runs under the export pipeline: no hook checks here.")
        return 0
    # Marked before the lock is checked, and the export checks the mark after taking the lock:
    # so either this sees the lock or the export sees the mark.
    _mark_launch(cfg.gos_home, role or "other", out)
    holder = locks.held(cfg.gos_home)
    if holder is not None:
        _say(
            out,
            f"not launching: the instance lock is held by {holder.describe()}. "
            "Play and Export never run at once; launch again when the export has finished "
            f"(its log is in {cfg.gos_home / LOGS}).",
        )
        _clear_launch(cfg.gos_home)
        return EXIT_REFUSE
    try:
        running = clients(cfg)
    except OSError as exc:
        _say(out, f"warning: can't tell whether Export is running ({exc}).")
        running = []
    if "export" in running:
        _say(
            out,
            f"not launching: {cfg.export} is running. Launch again once it has quit "
            "(Play and Export never run at once).",
        )
        _clear_launch(cfg.gos_home)
        return EXIT_REFUSE
    if role in WATCHED:
        (tell or tell_supervisor)(cfg, "launch", role, out)
    if role != "play":
        _say(out, "not Play: no stale check.")
        return 0
    try:
        status = fingerprint.check(
            cfg.gos_home, fingerprint.compute_play(cfg), "pre-launch"
        )
    except (OSError, fingerprint.FingerprintError) as exc:
        _say(out, f"warning: couldn't check whether recipe data is stale: {exc}")
        return 0
    _say(out, status.line())
    for note in status.play.notes:
        _say(out, f"note: {note}")
    return 0


def _mark_launch(home: Path, role: str, out: TextIO) -> None:
    record = {"role": role, "utc": fingerprint.utc_now(), "time": time.time()}
    try:
        fingerprint.atomic_write(home / LAUNCHING, json.dumps(record).encode("utf-8"))
    except OSError as exc:
        _say(out, f"warning: couldn't mark the launch ({exc}).")


def _clear_launch(home: Path) -> None:
    try:
        (home / LAUNCHING).unlink(missing_ok=True)
    except OSError:
        pass  # it ages out after LAUNCH_GRACE_S


def launching(home: Path) -> tuple[str, float] | None:
    """(role, seconds ago) of a launch that may still be starting its client, or None."""
    try:
        record = json.loads(read_bytes(home / LAUNCHING))
        role, age = str(record["role"]), time.time() - float(record["time"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (role, age) if 0 <= age < LAUNCH_GRACE_S else None


def tell_supervisor(cfg: config.Config, event: str, role: str, out: TextIO) -> bool:
    """Ask the supervisor to start (``launch``) or stop (``exit``) the game watchers and voice
    for ``role`` (D77, D259). Best effort: a supervisor that isn't running or doesn't answer is
    a warning, and the game launches all the same (plan 0.1 principle 9)."""
    sup = control.running(
        cfg.run_dir / control.SUPERVISOR_FILE, control.SUPERVISOR_SERVICE
    )
    if sup is None or "token" not in sup:
        _say(
            out, "note: GOS isn't running, so the game watchers and voice don't start."
        )
        return False
    try:
        control.call(
            sup["port"],
            "POST",
            "/play",
            token=sup["token"],
            body={"event": event, "instance": role},
            timeout=TELL_TIMEOUT_S,
        )
    except control.ControlError as exc:
        _say(out, f"warning: couldn't tell GOS the game {event}ed: {exc}")
        return False
    what = "start" if event == "launch" else "stop"
    _say(out, f"the game watchers and voice {what} ({role}).")
    return True


# --- post-exit -----------------------------------------------------------------------------


def ask_dialog(title: str, question: str, timeout_s: int) -> bool | None:
    """A Windows Yes/No box on top of everything; None when it times out (or can't be shown)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
    flags = 0x4 | 0x20 | 0x40000 | 0x10000
    # MessageBoxTimeoutW: in user32 since XP, undocumented; MessageBox has no timeout.
    box = getattr(user32, "MessageBoxTimeoutW", None)
    if box is None:
        return None
    box.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
        wintypes.WORD,
        wintypes.DWORD,
    ]
    box.restype = ctypes.c_int
    answer = box(None, question, title, flags, 0, timeout_s * 1000)
    return {6: True, 7: False}.get(answer)  # IDYES, IDNO; 32000 = timed out


def notify_dialog(title: str, text: str) -> None:
    """A Windows message box that closes itself after 10 minutes (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    box = getattr(user32, "MessageBoxTimeoutW", None)
    if box is not None:  # MB_ICONINFORMATION | MB_TOPMOST
        box(None, text, title, 0x40 | 0x40000, 0, 600_000)


@dataclass
class Started:
    ticket: str  # the started export puts it in the lock's record
    log: Path
    proc: subprocess.Popen[bytes] | None = None


def spawn_export(cfg: config.Config, config_path: Path | None) -> Started:
    """Start ``gos export run --notify`` on its own, logging to ``GOS_HOME/logs/``."""
    logs = cfg.gos_home / LOGS
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / time.strftime("export-%Y%m%dT%H%M%SZ.log", time.gmtime())
    ticket = secrets.token_hex(8)
    # -P and the working directory GOS_HOME: the hook runs in Play's game folder (Prism sets it),
    # which must neither be on the export's sys.path nor held as its directory for an hour.
    argv = [sys.executable, "-P", "-m", "gregtec.cli", "export", "run", "--notify"]
    argv += ["--ticket", ticket]
    if config_path is not None:
        argv += ["--config", str(config_path)]
    # Its own process, without a console window, outside any job the hook runs in (a job that
    # doesn't allow breakaway refuses the flag: then without it).
    if sys.platform == "win32":
        base = 0x00000200 | prism.CREATE_NO_WINDOW  # CREATE_NEW_PROCESS_GROUP
        tries = [base | 0x01000000, base]  # CREATE_BREAKAWAY_FROM_JOB first
    else:
        tries = [0]
    # Unbuffered, so its log is complete up to the moment it stops, however it stops.
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with open(log, "ab") as f:
        for i, flags in enumerate(tries):
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    cwd=cfg.gos_home,
                    env=env,
                    creationflags=flags,
                    start_new_session=sys.platform != "win32",
                )
                break
            except OSError:
                if i == len(tries) - 1:
                    raise
    return Started(ticket, log, proc)


def post_exit(
    cfg: config.Config,
    role: str | None,
    out: TextIO,
    ask: Ask = ask_dialog,
    spawn: Callable[[], Started] | None = None,
    wait_s: float = LOCK_WAIT_S,
    tell: Tell | None = None,
) -> int:
    """The export question (Play), then the watchers and voice stop (D259): plan 4.2 stops
    them once the question is answered or has timed out."""
    try:
        return _post_exit(cfg, role, out, ask, spawn, wait_s)
    finally:
        if role in WATCHED:
            (tell or tell_supervisor)(cfg, "exit", role, out)


def _post_exit(
    cfg: config.Config,
    role: str | None,
    out: TextIO,
    ask: Ask,
    spawn: Callable[[], Started] | None,
    wait_s: float,
) -> int:
    _clear_launch(cfg.gos_home)  # its client has run and quit
    if role != "play":
        _say(out, "not Play: nothing to do after exit.")
        return 0
    try:
        status = fingerprint.check(
            cfg.gos_home, fingerprint.compute_play(cfg), "post-exit"
        )
    except (OSError, fingerprint.FingerprintError) as exc:
        _say(out, f"couldn't fingerprint Play, so not asking to export: {exc}")
        return 1
    if not status.stale:
        _say(out, f"Play unchanged since export {status.export_run}: nothing to do.")
        return 0
    _say(out, status.line())
    if cfg.export_on_quit == "never":
        _say(
            out,
            '[policy] export_on_quit = "never": not asking. Recipe data stays stale.',
        )
        return 0
    holder = locks.held(cfg.gos_home)
    if holder is not None:
        _say(out, f"an export is already running ({holder.describe()}).")
        return 0
    what = (
        "No export is recorded yet."
        if status.export_run is None
        else f"Play's mods or configs changed since the last export ({status.changes.summary()})."
    )
    question = (
        f"{what}\n\nExport now? This syncs Play to {cfg.export} and launches it to export "
        "recipes; it quits by itself (up to about 30 minutes). Play can't start until it is "
        f"done.\n\nNo answer in {ASK_TIMEOUT_S // 60} minutes counts as No."
    )
    answer = ask("Gregtec OS: mods changed. Export now?", question, ASK_TIMEOUT_S)
    if answer is None:
        _say(
            out,
            "no answer (5 minutes passed, or no dialog could be shown): not exporting. "
            "Recipe data stays stale.",
        )
        return 0
    if not answer:
        _say(
            out,
            "not exporting (No). Recipe data stays stale; you'll be asked next quit.",
        )
        return 0
    started = (spawn or (lambda: spawn_export(cfg, None)))()
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        holder = locks.held(cfg.gos_home)
        if holder is not None and holder.ticket == started.ticket:
            _say(out, f"export started (pid {holder.pid}); its log is {started.log}.")
            return 0
        if started.proc is not None and started.proc.poll() is not None:
            _say(
                out,
                f"the export stopped at once (exit {started.proc.returncode}); see {started.log}.",
            )
            return 1
        time.sleep(0.2)
    _say(
        out,
        f"the export didn't take the instance lock within {wait_s:.0f} s; see {started.log}.",
    )
    return 1


# --- the export pipeline -------------------------------------------------------------------


@dataclass(frozen=True)
class FoundRun:
    name: str
    complete: bool
    manifest: Mapping[str, object]


def scan_runs(exports: Path, allow_harness: bool = False) -> dict[str, FoundRun]:
    """Finished runs in Export's ``gregteclink/exports/``: no ``.partial``, a readable manifest
    of the contract's format whose run_id is the folder's name. Reads manifests only."""
    found: dict[str, FoundRun] = {}
    try:
        names = sorted(os.listdir(exports))
    except FileNotFoundError:
        return found
    for name in names:
        if name.endswith(".partial") or not fingerprint.RUN_ID.fullmatch(name):
            continue
        try:
            data = read_bytes(exports / name / rundiff.MANIFEST)
            if len(data) > rundiff.MANIFEST_LIMIT:
                continue
            m = json.loads(data)
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict) or m.get("format") != rundiff.FORMAT:
            continue
        if m.get("run_id") != name or (
            m.get("mode") == "harness" and not allow_harness
        ):
            continue
        found[name] = FoundRun(name, m.get("complete") is True, m)
    return found


def _no_run_reason(export_game: Path) -> str | None:
    """The last ``autoexport: no run: <why>`` line in Export's FML log (D100)."""
    try:
        text = read_text(export_game / "logs" / "fml-client-latest.log")
    except OSError:
        return None
    hits = [ln for ln in text.splitlines() if "autoexport: no run:" in ln]
    return hits[-1].split("autoexport: no run:", 1)[1].strip() if hits else None


def _start_prism(argv: Sequence[str]) -> None:
    """Start Prism's CLI and don't wait: ``--launch`` returns at once only if Prism is open."""
    windows = sys.platform == "win32"
    subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x00000200 if windows else 0,  # CREATE_NEW_PROCESS_GROUP
        start_new_session=not windows,
    )


class ExportFailed(Exception):
    pass


def wait_for_export(
    cfg: config.Config,
    exports: Path,
    before: set[str],
    out: TextIO,
    clients: Clients,
    *,
    allow_harness: bool = False,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = POLL_S,
) -> FoundRun:
    """Wait until Export has written a new complete run and quit. Raises ExportFailed.

    Without a process list (``clients`` raising OSError every time) it can only watch for the
    run: no start timeout then, only the deadline.
    """

    def new_runs() -> tuple[list[FoundRun], list[FoundRun]]:
        found = [
            r for k, r in scan_runs(exports, allow_harness).items() if k not in before
        ]
        return [r for r in found if r.complete], [r for r in found if not r.complete]

    def no_run_reason() -> str | None:
        return _no_run_reason(fingerprint.game_folder(cfg.instance("export")))

    start = clock()
    seen_running = listed = warned = False
    run: FoundRun | None = None
    run_at = 0.0
    while True:
        complete, _ = new_runs()
        if run is None and complete:
            run = complete[-1]
            run_at = clock()
            _say(out, f"Export wrote run {run.name}; waiting for it to quit.")
        try:
            running: bool | None = "export" in clients(cfg)
            listed = True
        except OSError as exc:
            running = None
            if not warned and run is None:
                _say(
                    out,
                    f"warning: can't list processes ({exc}); watching for the run only.",
                )
                warned = True
        seen_running = seen_running or bool(running)
        now = clock()
        if run is not None:  # bounded by QUIT_WAIT_S, not by the deadline
            if running is False:
                return run
            if running is None and now - run_at >= UNKNOWN_GRACE_S:
                return run
            if running and now - run_at >= QUIT_WAIT_S:
                _say(
                    out,
                    f"warning: Export is still running {QUIT_WAIT_S / 60:.0f} min after its run.",
                )
                return run
        elif seen_running and running is False:
            # The run is renamed before the quit, but give a slow disk a moment.
            sleep(poll_s)
            complete, incomplete = new_runs()
            if complete:
                return complete[-1]
            if incomplete:
                last = incomplete[-1]
                raise ExportFailed(
                    f"Export quit after an incomplete run {last.name} (parts "
                    f"{last.manifest.get('parts')}, errors {last.manifest.get('part_errors')})"
                )
            why = no_run_reason()
            raise ExportFailed(
                "Export quit without a complete run"
                + (f": {why}" if why else " (no 'autoexport: no run' line in its log)")
            )
        elif listed and not seen_running and now - start >= START_TIMEOUT_S:
            why = no_run_reason()
            raise ExportFailed(
                f"{cfg.export} didn't start within {START_TIMEOUT_S / 60:.0f} minutes "
                + (
                    f"(or quit at once: {why})"
                    if why
                    else "(is Prism showing an error?)"
                )
            )
        elif now - start >= DEADLINE_S:
            raise ExportFailed(
                f"no finished export after {DEADLINE_S / 60:.0f} minutes"
            )
        sleep(poll_s)


def _mmc_differs(cfg: config.Config) -> bool:
    try:
        play = read_bytes(cfg.instance("play") / MMC_PACK)
        export = read_bytes(cfg.instance("export") / MMC_PACK)
    except OSError:
        return False
    return play != export


def run_export(
    cfg: config.Config,
    out: TextIO,
    *,
    launch: Callable[[Sequence[str]], None] = _start_prism,
    clients: Clients = prism.running_clients,
    notify: Callable[[str, str], None] | None = None,
    allow_harness: bool = False,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = POLL_S,
    ticket: str = "",
    config_path: Path | None = None,
) -> int:
    """``gos export run``. 0: exported; 1: refused or failed (the stale flag is kept).
    ``config_path`` is the gos.toml ``cfg`` was read from (default: ``config`` in GOS_HOME):
    its former-Plays record gets Play before Export is planned (D223)."""
    if getpass.getuser().lower() == prism.DEV_ACCOUNT:
        _say(
            out,
            "refused: the export pipeline runs as Clayton (D75); gos-dev uses gos launch.",
        )
        return 1
    home = cfg.gos_home
    try:
        lock = locks.InstanceLock(home).acquire(
            "export", "gos export run", ticket=ticket
        )
    except locks.LockHeld as exc:
        _say(out, f"refused: {exc}")
        return 1
    result: tuple[int, str]
    with lock:
        try:
            result = _export_locked(
                cfg,
                out,
                launch,
                clients,
                allow_harness,
                clock,
                sleep,
                poll_s,
                config_path or home / "config" / "gos.toml",
            )
        except ExportFailed as exc:
            _say(out, f"export failed: {exc}. Recipe data stays stale.")
            result = (1, f"Export failed: {exc}")
        except Exception as exc:  # noqa: BLE001 (any failure: release the lock and tell Clayton)
            traceback.print_exc(file=out)
            _say(out, f"export failed: {exc!r}. Recipe data stays stale.")
            result = (1, f"Export failed: {exc!r}. See the log in {home / LOGS}.")
    _say(out, "instance lock released.")
    if notify is not None:
        notify("Gregtec OS export", result[1])
    return result[0]


def _export_locked(
    cfg: config.Config,
    out: TextIO,
    launch: Callable[[Sequence[str]], None],
    clients: Clients,
    allow_harness: bool,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    poll_s: float,
    config_path: Path,
) -> tuple[int, str]:
    home = cfg.gos_home
    try:
        running = clients(cfg)
    except OSError as exc:
        raise ExportFailed(
            f"can't tell whether a GTNH client is running ({exc})"
        ) from None
    if running:
        raise ExportFailed(
            f"{', '.join(cfg.instance(r).name for r in running)} is running; close it first "
            "(never two clients at once)"
        )
    starting = launching(home)
    if starting is not None:
        role, age = starting
        what = (
            cfg.instance(role).name if role in ("play", "export", "lab") else "a client"
        )
        raise ExportFailed(
            f"{what} was launched {age:.0f} s ago and may still be starting (never two clients "
            f"at once); try again once it has quit, or in {LAUNCH_GRACE_S - age:.0f} s if it "
            "didn't start"
        )
    try:
        exported = fingerprint.compute_play(cfg)  # before sync (plan 5.2 step 4)
    except fingerprint.FingerprintError as exc:
        raise ExportFailed(f"can't fingerprint Play: {exc}") from None
    _say(out, f"Play's fingerprint {exported.digest[:12]} ({exported.counts()}).")

    # The Play this export syncs from is on record before Export is planned, as gos sync does
    # (D223): a later hand edit of [paths] play never frees it. Not recorded: refuse.
    from gregtec.setup.service import record_plays  # lazy: doctor imports sync

    if (why := record_plays(config_path, cfg)) is not None:
        raise ExportFailed(f"former Plays not recorded: {why}")
    plans, refusals = sync.plan_all(cfg, ["export"])
    if refusals:
        raise ExportFailed("sync refused: " + "; ".join(refusals))
    for plan in plans:
        sync.report(plan, out)
        sync.report_install(plan, out)
        try:
            sync.apply(plan)
            sync.apply_install(plan)
        except OSError as exc:
            raise ExportFailed(f"sync failed: {exc}") from None
    warnings: list[str] = []  # shown with the result, too
    if _mmc_differs(cfg):
        warnings.append(
            f"{cfg.export}'s mmc-pack.json differs from Play's (Forge, LWJGL or Java "
            "components). Sync doesn't copy it: update Export's components in Prism "
            "(pack-update playbook, WO-1.9)."
        )
        _say(out, f"warning: {warnings[-1]}")

    try:
        exports = rundiff.export_runs_folder(cfg)
    except fingerprint.FingerprintError as exc:
        raise ExportFailed(str(exc)) from None
    before = set(scan_runs(exports, allow_harness=True))
    argv = prism.launch_command(cfg, "export")
    _say(out, f"launching: {' '.join(argv)}")
    try:
        launch(argv)
    except OSError as exc:
        raise ExportFailed(f"can't start Prism ({argv[0]}): {exc}") from None
    run = wait_for_export(
        cfg,
        exports,
        before,
        out,
        clients,
        allow_harness=allow_harness,
        clock=clock,
        sleep=sleep,
        poll_s=poll_s,
    )

    for part, why in _table(run.manifest, "part_warnings").items():
        warnings.append(f"{part} left something out: {why}")
        _say(out, f"warning: {warnings[-1]}")
    previous = fingerprint.last_export(home)
    problems = run_problems(run.manifest)
    if problems:
        # Not recorded: recipe data stays stale, so Clayton is asked again on the next quit.
        code = 1
        summary = (
            f"Export {run.name} is incomplete: {'; '.join(problems)}. It isn't recorded, so "
            "recipe data stays stale and you'll be asked again after the next quit. "
            f"The run is {exports / run.name}."
        )
        _say(out, f"not recording export {run.name}: {'; '.join(problems)}.")
    else:
        try:
            play_now = fingerprint.compute_play(cfg)
        except fingerprint.FingerprintError as exc:
            raise ExportFailed(
                f"can't fingerprint Play after the export: {exc}"
            ) from None
        status = fingerprint.record_export(home, run.name, exported, play_now)
        _say(out, f"recorded export {run.name}: {status.line()}")
        code, summary = 0, f"Export {run.name} done. {status.line()}"
    if warnings:
        summary += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)

    if previous is None or previous["run_id"] == run.name:
        _say(out, "no earlier export is recorded: nothing to diff against.")
        return code, f"{summary}\n\nFirst recorded export: nothing to diff against."
    try:
        old = rundiff.load_run(rundiff.find_run(cfg, previous["run_id"]))
        new = rundiff.load_run(exports / run.name)
    except rundiff.RunError as exc:
        _say(out, f"no diff against the previous export: {exc}")
        return code, summary
    play_old = fingerprint.run_entries(home, previous["run_id"])
    d = rundiff.diff(old, new, play_old, exported.entries)
    path = write_report(home, d)
    print(rundiff.report(d), file=out)
    _say(out, f"diff report: {path}")
    return code, f"{summary}\n\n{d.summary()}\n\nReport: {path}"


_table = rundiff._table  # a manifest's table, or {} when it isn't one


def run_problems(manifest: Mapping[str, object]) -> list[str]:
    """Why a complete run isn't a whole export (``complete`` covers only the GT parts): NEI's
    recipes are missing (the contract's readers need ``parts.nei == "ok"``; a dedicated server
    has no NEI step), the oredict part failed (``gos db build`` refuses such a run: its NEI-only
    recipes would get other citation keys, D145) or the autoexport hit its deadline."""
    parts, errors = _table(manifest, "parts"), _table(manifest, "part_errors")

    def why(part: str) -> str:
        return f" ({errors[part]})" if part in errors else ""

    problems = []
    nei = parts.get("nei")
    if nei != "ok" and not (
        nei == "skipped_server" and manifest.get("side") == "server"
    ):
        problems.append(f"NEI's part is {nei}{why('nei')}")
    if parts.get("oredict") == "failed":
        problems.append(f"the oredict part failed{why('oredict')}")
    if parts.get("autoexport") == "failed":
        problems.append(f"the autoexport failed{why('autoexport')}")
    return problems


def write_report(home: Path, d: rundiff.Diff) -> Path:
    path = home / REPORTS / f"export-diff-{d.old.run_id}-{d.new.run_id}.txt"
    fingerprint.atomic_write(path, rundiff.report(d).encode("utf-8"))
    return path


# --- gos commands --------------------------------------------------------------------------


def add_hook_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("event", choices=("pre-launch", "post-exit"))
    parser.add_argument(
        "--instance",
        choices=("play", "export", "lab"),
        default=None,
        help="the instance to act for (default: from Prism's INST_DIR)",
    )
    parser.add_argument(
        "--answer",
        choices=("yes", "no"),
        default=None,
        help="post-exit: answer the export question without the dialog",
    )


def hook_main(args: argparse.Namespace, out: TextIO, env: Mapping[str, str]) -> int:
    if isinstance(out, io.TextIOWrapper):
        # Prism reads the hook's output through a pipe in the ANSI code page: a path that
        # code page can't show must not crash the hook.
        out.reconfigure(errors="replace")
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        _say(out, f"can't read gos.toml: {exc}")
        return 2
    role = args.instance or role_of(cfg, env.get("INST_DIR"))
    if role is None and not env.get("INST_DIR"):
        _say(out, "no INST_DIR (Prism sets it for hooks): pass --instance.")
        return 2
    if args.event == "pre-launch":
        return pre_launch(cfg, role, out)
    ask: Ask = ask_dialog
    if args.answer is not None:
        answer = args.answer == "yes"

        def ask(title: str, question: str, timeout_s: int) -> bool | None:
            return answer

    return post_exit(
        cfg, role, out, ask=ask, spawn=lambda: spawn_export(cfg, args.config)
    )
