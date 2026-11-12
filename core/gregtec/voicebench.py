r"""``gos voice benchmark`` (alias ``bench``; WO-5.7, plan 8, PM14, D23, QA:A7; D248, D249).

One harness for the voice acceptance with GTNH running. It reports four sections and checks
each against its target in code:

| section   | measured                                                 | target                     |
|-----------|----------------------------------------------------------|----------------------------|
| latency   | each stage of each turn, talk key and wake word, from    | PM14: P50 to first audio   |
|           | the voice client's ``turn`` records (or a synthetic run) | key <= 1.7 s, wake <= 2.2 s|
| resources | peak RAM of the voice client and the core, RAM free at   | >= 4 GB free at peak       |
|           | its lowest, VRAM (psutil, nvidia-smi)                    | (others: recorded)         |
| game      | FPS from F3 and TPS from ``/forge tps``, before GOS runs | FPS drop <= 10%,           |
|           | and while it runs, as Clayton pastes or saves them       | no TPS drop (D249)         |
| spoken    | the fast lane's eval set (tests/evals/fast_lane_         | >= 95% of answers within   |
|           | questions.tsv) with the fake model, through the speech   | 2 sentences and 40 words   |
|           | filter and lexicon the speaker uses                      | as heard (D23, D248)       |

**Sources.** Without ``--synthetic`` the turns are the voice client's own (``logs/voice.log`` and
its rotated copies): the last ``--last`` turns, or with ``--minutes M`` the turns logged while
this command samples the resources for M minutes (Clayton plays Lab and asks meanwhile;
Ctrl+C ends early). The core is found as the process running ``gregtec.api`` and the voice
client as the one running ``gos voice run`` (``--pid name=PID`` names one); their memory is
read, never changed. That reading is why this module is in the core: the voice package never
reaches another process (D247's audit, D248). ``--synthetic`` runs the voice client in this
process with the fakes: a fake key hook and microphone, synthetic audio (a tone for speech,
zeros for silence), a wake engine that hears the tone (end of speech after
:data:`gregtec_voice.wake.END_SILENCE_S` of silence, as the real one), a fixed transcript,
the fast lane on the fake model when GOS_HOME has a DB (else a canned answer), and a speaker
that runs the real speech filter; ``--stt-ms``, ``--core-ms`` and ``--tts-ms`` add a stage's
time. Its numbers time GOS's own code, not a PC: they show the harness works end to end.

**Game numbers.** Nothing is injected into GTNH (Play is off limits; the runbook uses Lab): the
FPS and TPS come from what GTNH already shows. ``--fps-before`` and ``--fps-during`` take F3's
"N fps" readings, ``--tps-before`` and ``--tps-during`` the output of ``/forge tps`` (the last
``Overall`` line counts), each as text or as a file (a copy of Lab's ``logs/latest.log`` works:
chat goes there), read once with gamestate.safe_open.

**Report.** A TSV (``section scope metric value target result note``) and a text summary in
``GOS_HOME/data/reports`` (or ``--out``). The result is PASS when every check was measured and
met, FAIL when one missed, INCOMPLETE otherwise. The exit code is 1 when a check failed or no
turn was measured at all, else 0.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from gregtec.config import Config
from gregtec.gamestate.safe_open import read_bytes

if TYPE_CHECKING:
    from gregtec_voice.speech_filter import Said

log = logging.getLogger(__name__)

TARGETS_MS = {"key": 1700, "wake": 2200}  # PM14: to first audio, P50
TRIGGER_NAMES = {"key": "talk key", "wake": "wake word"}
STAGES = ("held_ms", "close_ms", "stt_ms", "core_ms", "speak_ms", "total_ms")
FPS_DROP_MAX = 0.10  # plan 8 WO-5.7
TPS_TOLERANCE = (
    0.1  # D249: /forge tps prints 3 decimals; below 20 this is noise, not impact
)
RAM_FREE_MIN_MB = 4096  # plan 8 WO-5.7, QA:A7
SPOKEN_SHARE_MIN = 0.95  # plan 8 WO-5.7, D23
CUT_SHARE_MAX = 0.05  # D248: the rest of the 95% (L1 of the WO-5.7 review)
SAMPLE_S = 1.0  # RAM is read this often
VRAM_EVERY = 5  # nvidia-smi once every this many samples (it costs CPU next to GTNH)
SPEECH_S = 0.6  # a synthetic question's tone
TURN_TIMEOUT_S = 30.0
QUESTION = "How do I make steel?"  # q01 of the eval set
CANNED = "Steel Ingot: the first recipe runs in the Electric Blast Furnace at 120 EU/t (r:x)."
PASS, FAIL, MISSING, INFO = "pass", "FAIL", "not measured", "info"
COLUMNS = ("section", "scope", "metric", "value", "target", "result", "note")
EXIT_PROBLEM, EXIT_SETUP = 1, 2


@dataclass(frozen=True)
class Check:
    section: str  # latency, resources, game or spoken
    scope: str  # key, wake, voice, core, system, gpu, an eval id
    metric: str
    value: str = ""
    target: str = ""
    result: str = INFO  # pass, FAIL, not measured or info
    note: str = ""


def percentile(values: Sequence[int], q: float) -> int:
    """Nearest rank."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


# --- latency ---------------------------------------------------------------------------------


def turn_records(logs: Path, msg: str = "turn") -> list[dict[str, Any]]:
    """Every ``turn`` record (or ``msg`` record: ``wake``, D185) in ``logs/voice.log`` and its
    rotated copies, oldest first (applog keeps ``voice.log.1`` to ``.5``, ``.1`` the newest)."""
    rotated = []
    for path in logs.glob("voice.log.*"):
        number = path.name.removeprefix("voice.log.")
        if number.isdigit():
            rotated.append((int(number), path))
    paths = [path for _, path in sorted(rotated, reverse=True)]
    found = []
    for path in [*paths, logs / "voice.log"]:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("msg") == msg:
                found.append(entry)
    return found


def since(records: list[dict[str, Any]], start: datetime) -> list[dict[str, Any]]:
    """The records logged at or after ``start`` (applog's ``ts``, UTC ISO 8601)."""
    kept = []
    for r in records:
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is not None and ts >= start:
            kept.append(r)
    return kept


def latency(
    turns: list[dict[str, Any]], wakes: list[dict[str, Any]], out: TextIO
) -> list[Check]:
    """Each stage's P50 and P90 per trigger, and PM14 on the P50 to first audio (printed as
    ``gos voice bench`` always has)."""
    if wakes:  # D185: each wake's outcome; Clayton counts the chimes he didn't mean
        outcomes: dict[str, int] = {}
        for w in wakes:
            outcomes[str(w.get("outcome"))] = outcomes.get(str(w.get("outcome")), 0) + 1
        chimed = sum(n for o, n in outcomes.items() if o in ("asked", "no question"))
        listed = ", ".join(f"{n} {o}" for o, n in sorted(outcomes.items()))
        print(
            f"wake word: {len(wakes)} detections, {chimed} chimed ({listed})", file=out
        )
    answered = [t for t in turns if t.get("outcome") in ("answered", "stopped")]
    if not answered:
        print("No answered turns yet.", file=out)
    checks = []
    for trigger in ("key", "wake"):
        rows = [t for t in answered if t.get("trigger") == trigger]
        target = f"P50 <= {TARGETS_MS[trigger]} ms"
        if not rows:
            checks.append(
                Check(
                    "latency", trigger, "to first audio", target=target, result=MISSING
                )
            )
            continue
        print(f"{trigger}: {len(rows)} turns", file=out)
        for stage in STAGES:
            values = [int(t[stage]) for t in rows if isinstance(t.get(stage), int)]
            if values:
                p50, p90 = percentile(values, 0.5), percentile(values, 0.9)
                print(
                    f"  {stage[:-3]:>6}: P50 {p50} ms, P90 {p90} ms ({len(values)})",
                    file=out,
                )
                checks.append(
                    Check(
                        "latency",
                        trigger,
                        stage[:-3],
                        f"P50 {p50} ms, P90 {p90} ms",
                        note=f"{len(values)} turns",
                    )
                )
        totals = [
            int(t["total_ms"]) for t in rows if isinstance(t.get("total_ms"), int)
        ]
        if not totals:
            print("  no turn reached first audio", file=out)
            checks.append(
                Check(
                    "latency",
                    trigger,
                    "to first audio",
                    target=target,
                    result=FAIL,
                    note="no turn reached first audio",
                )
            )
            continue
        p50 = percentile(totals, 0.5)
        ok = p50 <= TARGETS_MS[trigger]
        print(
            f"  PM14: P50 {p50} ms to first audio, target {TARGETS_MS[trigger]} ms: "
            + ("met" if ok else "MISSED"),
            file=out,
        )
        checks.append(
            Check(
                "latency",
                trigger,
                "to first audio",
                f"{p50} ms",
                target,
                PASS if ok else FAIL,
                f"P50 of {len(totals)} turns",
            )
        )
    return checks


# --- resources -------------------------------------------------------------------------------

CORE, VOICE = "core", "voice"


def find_processes(own: int | None = None) -> dict[str, list[int]]:
    """GOS's core (``python -m gregtec.api``) and voice client (``gos voice run``) processes by
    their command lines (a uv launcher and its Python both match; the larger one counts)."""
    import psutil

    own = os.getpid() if own is None else own
    found: dict[str, list[int]] = {CORE: [], VOICE: []}
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmd = [str(c) for c in proc.info.get("cmdline") or []]
        if proc.info["pid"] == own or not cmd:
            continue
        role = role_of(cmd)
        if role is not None:
            found[role].append(proc.info["pid"])
    return found


def role_of(cmd: Sequence[str]) -> str | None:
    """``core``, ``voice`` or None for one command line: ``gos voice run`` (``gos``, the
    ``gos.exe`` launcher uv installs, or Python running that launcher) or ``python -m
    gregtec_voice[.cli] run``. A shell shim (``gos.cmd``, ``gos.ps1``) starts one of those, which
    is found instead."""
    if "gregtec.api" in cmd:
        return CORE
    names = [re.split(r"[\\/]", c)[-1].lower() for c in cmd]  # Windows paths on any OS
    for i, name in enumerate(names):
        rest = names[i + 1 : i + 3]
        if name in ("gos", "gos.exe") and rest == ["voice", "run"]:
            return VOICE
        if name in ("gregtec_voice", "gregtec_voice.cli") and rest[:1] == ["run"]:
            return VOICE  # python -m gregtec_voice[.cli] run
    return None


def nvidia_smi(*query: str) -> list[list[str]] | None:
    """One ``nvidia-smi`` CSV query's rows, None without an NVIDIA GPU or its tool."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        done = subprocess.run(
            ["nvidia-smi", *query, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [
        [cell.strip() for cell in line.split(",")]
        for line in done.stdout.splitlines()
        if line.strip()
    ]


@dataclass
class Peaks:
    """What :class:`Sampler` saw, in MB."""

    free_min: int | None = None
    ram_total: int | None = None
    rss: dict[str, int] = field(default_factory=dict)  # role -> peak
    vram_used: int | None = None
    vram_total: int | None = None
    vram: dict[str, int] = field(default_factory=dict)  # role -> peak (CUDA processes)
    samples: int = 0
    problem: str | None = None  # why RAM wasn't measured


class Sampler:
    """Samples RAM every :data:`SAMPLE_S` and VRAM every :data:`VRAM_EVERY` samples on its own
    thread, from :meth:`start` to :meth:`stop`. ``processes`` maps each role to its pids."""

    def __init__(
        self,
        processes: dict[str, list[int]],
        *,
        interval: float = SAMPLE_S,
        smi: Callable[..., list[list[str]] | None] = nvidia_smi,
    ) -> None:
        self.processes = processes
        self.interval = interval
        self.smi = smi
        self.peaks = Peaks()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gos-bench", daemon=True)
        try:
            import psutil

            self._psutil: Any = psutil
        except ImportError:
            self._psutil = None
            self.peaks.problem = "psutil isn't installed (uv sync)"

    def start(self) -> Sampler:
        self.sample()
        self._thread.start()
        return self

    def stop(self) -> Peaks:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(10)
        self.sample()
        return self.peaks

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.sample()

    def sample(self) -> None:
        p = self.peaks
        if self._psutil is not None:
            mem = self._psutil.virtual_memory()
            free = mem.available // 2**20
            p.free_min = free if p.free_min is None else min(p.free_min, free)
            p.ram_total = mem.total // 2**20
            for role, pids in self.processes.items():
                for pid in pids:
                    mb = self._rss(pid)
                    if mb is not None:
                        p.rss[role] = max(p.rss.get(role, 0), mb)
        if p.samples % VRAM_EVERY == 0:
            self._vram()
        p.samples += 1

    def _rss(self, pid: int) -> int | None:
        """The process's resident memory, or its peak working set on Windows, in MB."""
        try:
            info = self._psutil.Process(pid).memory_info()
        except (self._psutil.Error, OSError):
            return None
        return max(info.rss, getattr(info, "peak_wset", 0)) // 2**20

    def _vram(self) -> None:
        p = self.peaks
        rows = self.smi("--query-gpu=memory.used,memory.total")
        if not rows:
            return
        try:  # the busiest GPU, should there be more than one
            used, total = max((int(r[0]), int(r[1])) for r in rows)
        except (ValueError, IndexError):
            return
        p.vram_used = used if p.vram_used is None else max(p.vram_used, used)
        p.vram_total = total
        apps = self.smi("--query-compute-apps=pid,used_memory") or []
        for role, pids in self.processes.items():
            for row in apps:
                if len(row) == 2 and row[0].isdigit() and int(row[0]) in pids:
                    with contextlib.suppress(ValueError):  # "[N/A]" under WDDM
                        p.vram[role] = max(p.vram.get(role, 0), int(row[1]))


def resource_checks(
    peaks: Peaks | None, processes: dict[str, list[int]], why: str = ""
) -> list[Check]:
    """``peaks`` None: not sampled (``why``)."""
    target = f">= {RAM_FREE_MIN_MB} MB"
    if peaks is None or peaks.problem is not None or peaks.free_min is None:
        note = why if peaks is None else (peaks.problem or "no sample")
        return [
            Check("resources", "system", "RAM free at peak", target=target, result=MISSING,
                  note=note)
        ]  # fmt: skip
    ok = peaks.free_min >= RAM_FREE_MIN_MB
    checks = [
        Check(
            "resources",
            "system",
            "RAM free at peak",
            f"{peaks.free_min} MB",
            target,
            PASS if ok else FAIL,
            f"of {peaks.ram_total} MB, {peaks.samples} samples",
        )
    ]
    for role, pids in processes.items():
        value = peaks.rss.get(role)
        checks.append(
            Check(
                "resources",
                role,
                "peak RAM",
                "" if value is None else f"{value} MB",
                result=INFO if value is not None else MISSING,
                note=f"pid {', '.join(map(str, pids))}" if pids else "not running",
            )
        )
    if peaks.vram_used is None:
        checks.append(
            Check("resources", "gpu", "VRAM used at peak", result=MISSING,
                  note="no NVIDIA GPU or nvidia-smi")
        )  # fmt: skip
    else:
        headroom = (peaks.vram_total or 0) - peaks.vram_used
        checks.append(
            Check(
                "resources",
                "gpu",
                "VRAM used at peak",
                f"{peaks.vram_used} MB",
                note=f"of {peaks.vram_total} MB, {headroom} MB free (L7: >= 1024 MB)",
            )
        )
        for role, mb in peaks.vram.items():
            checks.append(Check("resources", role, "VRAM at peak", f"{mb} MB"))
    return checks


# --- game: FPS and TPS -------------------------------------------------------------------------

_FPS = re.compile(r"(\d+(?:[.,]\d+)?)\s*fps\b", re.IGNORECASE)
_BARE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$", re.MULTILINE)
_LIST = re.compile(r"\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)+")
# Forge 1.7.10's /forge tps (its DecimalFormat follows the locale: 2,345 or 2.345)
_TPS = re.compile(
    r"Overall\s*:\s*Mean tick time:\s*([\d.,]+)\s*ms\.?\s*Mean TPS:\s*([\d.,]+)",
    re.IGNORECASE,
)


def _num(text: str) -> float:
    return float(text.replace(",", "."))


_PATHLIKE = re.compile(r"[\\/]|\.(?:txt|log|tsv)$", re.IGNORECASE)


def text_of(value: str) -> str:
    """A file's text (read once, share-delete, as GOS reads game files) or ``value`` itself; ""
    for a file that isn't there (its name's digits are no reading)."""
    path = Path(value)
    try:
        if path.is_file():
            return read_bytes(path).decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""
    return "" if _PATHLIKE.search(value) else value


def parse_fps(text: str) -> list[float]:
    """F3's "N fps" readings in ``text``, else numbers alone on a line, else one line that is
    only numbers and commas ("60, 62, 61"). Nothing else: a log's timestamps are no FPS."""
    found = [_num(m) for m in _FPS.findall(text)]
    if found:
        return found
    found = [_num(m) for m in _BARE.findall(text)]
    if found:
        return found
    if _LIST.fullmatch(text.strip()):
        return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    return []


def parse_tps(text: str) -> tuple[float, float] | None:
    """The last ``Overall`` line of ``/forge tps``: (mean tick ms, mean TPS)."""
    found = _TPS.findall(text)
    if not found:
        return None
    tick, tps = found[-1]
    return _num(tick), _num(tps)


_TIMESTAMP = re.compile(r"\[\d{1,2}:\d{2}:\d{2}\]")


def last_tps_line(text: str) -> str | None:
    """The whole line of the last ``Overall`` reading in ``text`` (its timestamp too)."""
    last = None
    for last in _TPS.finditer(text):
        pass
    if last is None:
        return None
    start = text.rfind("\n", 0, last.start()) + 1
    end = text.find("\n", last.end())
    return text[start : None if end < 0 else end].strip()


def stale_tps(before: str, during: str) -> bool:
    """The reading "while GOS runs" is the one from before (M1 of the WO-5.7 review): the same
    log copied twice with no new ``/forge tps`` in between (a mistyped command, or Lab didn't
    log the reply) ends with the same timestamped ``Overall`` line. Pasted readings without a
    timestamp are stale only when the second text holds the whole first one."""
    line = last_tps_line(during)
    if line is None or line != last_tps_line(before):
        return False
    return bool(_TIMESTAMP.search(line)) or (bool(before.strip()) and before in during)


def game_checks(
    fps_before: str | None,
    fps_during: str | None,
    tps_before: str | None,
    tps_during: str | None,
) -> list[Check]:
    checks = []
    target = f"drop <= {FPS_DROP_MAX:.0%}"
    if fps_before is None or fps_during is None:
        checks.append(
            Check("game", "gtnh", "FPS drop", target=target, result=MISSING,
                  note="give --fps-before and --fps-during")
        )  # fmt: skip
    else:
        before, during = parse_fps(text_of(fps_before)), parse_fps(text_of(fps_during))
        if not before or not during or sum(before) <= 0:
            checks.append(
                Check("game", "gtnh", "FPS drop", target=target, result=MISSING,
                      note="no FPS reading in what was given (or no such file)")
            )  # fmt: skip
        else:
            b, d = sum(before) / len(before), sum(during) / len(during)
            drop = round((b - d) / b, 4)
            checks.append(
                Check(
                    "game",
                    "gtnh",
                    "FPS drop",
                    f"{drop:.1%}",
                    target,
                    PASS if drop <= FPS_DROP_MAX else FAIL,
                    f"{b:.1f} fps before ({len(before)}), {d:.1f} with GOS ({len(during)})",
                )
            )
    target = f"drop <= {TPS_TOLERANCE} TPS"
    if tps_before is None or tps_during is None:
        checks.append(
            Check("game", "gtnh", "TPS", target=target, result=MISSING,
                  note="give --tps-before and --tps-during")
        )  # fmt: skip
        return checks
    before_text, during_text = text_of(tps_before), text_of(tps_during)
    before_tps, during_tps = parse_tps(before_text), parse_tps(during_text)
    if before_tps is None or during_tps is None:
        checks.append(
            Check("game", "gtnh", "TPS", target=target, result=MISSING,
                  note="no /forge tps Overall line in what was given (or no such file)")
        )  # fmt: skip
        return checks
    if stale_tps(before_text, during_text):
        checks.append(
            Check("game", "gtnh", "TPS", target=target, result=MISSING,
                  note="no new /forge tps reading: the last one is from before GOS ran")
        )  # fmt: skip
        return checks
    drop = round(before_tps[1] - during_tps[1], 3)  # as printed
    checks.append(
        Check(
            "game",
            "gtnh",
            "TPS",
            f"{before_tps[1]:.3f} -> {during_tps[1]:.3f}",
            target,
            PASS if drop <= TPS_TOLERANCE else FAIL,
            f"mean tick {before_tps[0]:.3f} -> {during_tps[0]:.3f} ms",
        )
    )
    return checks


# --- spoken length -----------------------------------------------------------------------------


SPOKEN_SCOPES = ("eval set", "live")


def _spoken_gates(
    scope: str, counts: Sequence[tuple[int, int, bool]], why: str
) -> list[Check]:
    """D23 on (sentences, words, cut) as heard: the share within it, and the share the filter
    had to cut to get there (L1 of the WO-5.7 review: an answer cut mid-thought may have lost
    its point, which the prompt rules are there to prevent)."""
    from gregtec_voice import speech_filter as sf

    within_target = f">= {SPOKEN_SHARE_MIN:.0%} within {sf.MAX_SENTENCES} sentences, {sf.MAX_WORDS} words"
    cut_target = f"<= {CUT_SHARE_MAX:.0%}"
    if not counts:
        return [
            Check("spoken", scope, "within D23", target=within_target, result=MISSING,
                  note=why),
            Check("spoken", scope, "cut by the filter", target=cut_target, result=MISSING,
                  note=why),
        ]  # fmt: skip
    n = len(counts)
    within = sum(s <= sf.MAX_SENTENCES and w <= sf.MAX_WORDS for s, w, _ in counts)
    cut = sum(c for _, _, c in counts)
    return [
        Check(
            "spoken",
            scope,
            "within D23",
            f"{within / n:.1%}",
            within_target,
            PASS if within / n >= SPOKEN_SHARE_MIN else FAIL,
            f"{within} of {n} answers",
        ),
        Check(
            "spoken",
            scope,
            "cut by the filter",
            f"{cut / n:.1%}",
            cut_target,
            PASS if cut / n <= CUT_SHARE_MAX else FAIL,
            f"{cut} of {n} answers",
        ),
    ]


def spoken_checks(
    answers: Sequence[tuple[str, str]] | None,
    said: Callable[[str], Said],
    why: str = "",
) -> list[Check]:
    """D23 over the eval set's answers ((id, reply text)), each as the speaker says it
    (``said``: :meth:`gregtec_voice.tts.Prepare.said`)."""
    from gregtec_voice import speech_filter as sf

    rows = []
    counts = []
    for item, text in answers or ():
        heard = said(text)
        ok = heard.sentences <= sf.MAX_SENTENCES and heard.words <= sf.MAX_WORDS
        counts.append((heard.sentences, heard.words, heard.cut))
        note = (
            "nothing said"
            if not heard.text
            else "cut by the filter"
            if heard.cut
            else ""
        )
        rows.append(
            Check(
                "spoken",
                item,
                "sentences, words",
                f"{heard.sentences}, {heard.words}",
                result=PASS if ok else FAIL,
                note=note,
            )
        )
    return [*_spoken_gates("eval set", counts, why), *rows]


def live_spoken_checks(turns: Sequence[dict[str, Any]], source: str) -> list[Check]:
    """D23 on what the voice client said in the measured turns (M2 of the WO-5.7 review): the
    real model's answers on the PC, or the synthetic core's here. Each ``turn`` record has its
    answer's length as heard (counts only, D82) since WO-5.7."""
    counts = [
        (t["spoken_sentences"], t["spoken_words"], bool(t.get("spoken_cut")))
        for t in turns
        if t.get("outcome") in ("answered", "stopped")
        and isinstance(t.get("spoken_sentences"), int)
        and isinstance(t.get("spoken_words"), int)
    ]
    why = f"no answered turn in {source} logged its spoken length (a client before WO-5.7?)"
    return _spoken_gates("live", counts, why)


def eval_answers(cfg: Config) -> tuple[list[tuple[str, str]] | None, str]:
    """The fast lane's eval set asked with the fake model (D153), a fresh conversation per row
    as ``gos eval fast-lane --fake``: ([(id, reply text)], "") or (None, why not)."""
    from gregtec.agents import ask, evals
    from gregtec.db import access

    if not access.db_path(cfg.gos_home).is_file():
        return None, "no DB: gos export collect and gos db build first"
    agents = ask.setup(cfg, fake=True, deep_lane=False)
    try:
        return [
            (q.id, agents.asker().ask(q.question, task=evals.TASK).text)
            for q in evals.load()
        ], ""
    finally:
        agents.close()


# --- the synthetic run -------------------------------------------------------------------------


class SyntheticWake:
    """A wake engine for the synthetic run: while armed, a tone after silence is the wake
    phrase (``wake``), and :data:`gregtec_voice.wake.END_SILENCE_S` of silence after it ends
    the question at the last loud block (``end_of_speech``, as the real engine reports)."""

    name = "synthetic"

    def __init__(self, bus: Any, end_silence_s: float) -> None:  # noqa: ANN401 - TriggerBus
        self.bus = bus
        self.end_silence_s = end_silence_s
        self.armed = False
        self._loud_until: float | None = None

    def feed(self, block: Any) -> None:  # noqa: ANN401 - gregtec_voice.audio.Block
        loud = bool(block.pcm.strip(b"\x00"))
        if loud and self.armed:
            if self._loud_until is None:
                self.bus.wake(block.t_end)
            self._loud_until = block.t_end
        elif self._loud_until is not None and (
            block.t_end - self._loud_until >= self.end_silence_s
        ):
            self.bus.end_of_speech(self._loud_until)
            self._loud_until = None

    def close(self) -> None:
        pass


class SyntheticSpeaker:
    """Says through ``say`` (the speech filter and lexicon), first audio after ``delay_s``."""

    name = "synthetic"

    def __init__(self, say: Callable[[str], str], delay_s: float) -> None:
        self.say = say
        self.delay_s = delay_s
        self.said: list[str] = []

    def heard(self, text: str) -> Said | None:
        """As :meth:`gregtec_voice.tts.Speaker.heard`: ``say``'s counts, if it gives them."""
        said = getattr(self.say, "said", None)
        return said(text) if callable(said) else None

    def speak(
        self, text: str, on_first_audio: Callable[[], None], on_done: Callable[[], None]
    ) -> None:
        def run() -> None:
            self.said.append(self.say(text))
            time.sleep(self.delay_s)
            on_first_audio()
            on_done()

        threading.Thread(target=run, name="gos-bench-speak", daemon=True).start()

    def stop(self) -> None:
        pass


class SlowTranscriber:
    name = "synthetic"

    def __init__(self, text: str, delay_s: float) -> None:
        self.text = text
        self.delay_s = delay_s

    def transcribe(self, segment: object) -> str:
        time.sleep(self.delay_s)
        return self.text


class SyntheticCore:
    """The core as the voice client sees it: ``answer(text)`` (the fast lane on the fake model,
    or :data:`CANNED`) after ``delay_s``."""

    def __init__(
        self, answer: Callable[[str], dict[str, Any]] | None, delay_s: float
    ) -> None:
        self.answer = answer
        self.delay_s = delay_s

    def ask(self, text: str) -> dict[str, Any]:
        time.sleep(self.delay_s)
        if self.answer is not None:
            return self.answer(text)
        return {"text": CANNED, "lane": "fast", "intent": "recipe", "checked": True}

    def prewarm(self) -> None:
        pass

    def ui(self, action: str) -> None:
        pass

    def state(self, state: str, trigger: str | None) -> None:
        pass


class _Turns(logging.Handler):
    """The voice client's ``turn`` records as dicts."""

    def __init__(self, fields: Sequence[str]) -> None:
        super().__init__(logging.INFO)
        self.fields = fields
        self.records: list[dict[str, Any]] = []
        self.changed = threading.Condition()

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() != "turn":
            return
        with self.changed:
            self.records.append({k: getattr(record, k, None) for k in self.fields})
            self.changed.notify_all()

    def wait(self, count: int, timeout: float) -> bool:
        with self.changed:
            return self.changed.wait_for(lambda: len(self.records) >= count, timeout)


@contextlib.contextmanager
def _captured(fields: Sequence[str]) -> Iterator[_Turns]:
    logger = logging.getLogger("gregtec_voice.client")
    handler, level = _Turns(fields), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def synthetic_turns(
    voice_settings: Any,  # noqa: ANN401 - gregtec_voice.settings.VoiceSettings
    core: Any,  # noqa: ANN401 - gregtec_voice.client.CoreApi
    *,
    turns: int,
    transcript: str = QUESTION,
    say: Callable[[str], str] = lambda text: text,
    stt_s: float = 0.0,
    tts_s: float = 0.0,
    speech_s: float = SPEECH_S,
) -> tuple[list[dict[str, Any]], list[str]]:
    """``turns`` talk-key turns, then ``turns`` wake-word turns, through the real voice client
    on synthetic audio in real time: (their turn records, what was said)."""
    from gregtec_voice import audio, wake
    from gregtec_voice.audio import AudioPipe, FakeSource
    from gregtec_voice.client import Process, Turn, VoiceClient
    from gregtec_voice.ptt import FakeHook, KeyFilter
    from gregtec_voice.settings import HOLD, KEY, WAKE

    settings = replace(
        voice_settings, primary_trigger=WAKE, backup_trigger=KEY, talk_mode=HOLD
    )
    speaker = SyntheticSpeaker(say, tts_s)
    voice = VoiceClient(
        settings, core, transcriber=SlowTranscriber(transcript, stt_s), speaker=speaker
    )
    engine = SyntheticWake(voice, wake.END_SILENCE_S)
    source = FakeSource()
    hook = FakeHook(KeyFilter(voice.watched, voice.post))
    process = Process(
        voice, AudioPipe(source, voice.recorder, [engine]), hook, wake=engine
    )
    stop = threading.Event()
    loud = threading.Event()
    tone, quiet = (
        audio.tone(audio.BLOCK_S),
        bytes(audio.BLOCK_FRAMES * audio.SAMPLE_BYTES),
    )

    def feed() -> None:  # the microphone: a 20 ms block every 20 ms
        next_at = time.monotonic()
        while not stop.is_set():
            source.push(tone if loud.is_set() else quiet)
            next_at += audio.BLOCK_S
            time.sleep(max(0.0, next_at - time.monotonic()))

    vk = settings.talk_key.vk
    with _captured(list(Turn(0, KEY, 0.0).record())) as captured:
        runner = threading.Thread(
            target=process.run, args=(stop,), name="gos-bench-voice"
        )
        feeder = threading.Thread(target=feed, name="gos-bench-mic", daemon=True)
        runner.start()
        feeder.start()
        try:
            deadline = time.monotonic() + 10
            while not (hook.alive() and source._on_block is not None):
                if time.monotonic() > deadline:
                    raise RuntimeError("the synthetic voice client didn't start")
                time.sleep(0.02)
            for n in range(2 * turns):
                time.sleep(0.3)  # the pre-roll fills
                if n < turns:
                    hook.press(vk)
                    loud.set()
                    time.sleep(speech_s)
                    loud.clear()
                    hook.release(vk)
                else:
                    engine.armed = True
                    loud.set()
                    time.sleep(speech_s)
                    loud.clear()
                if not captured.wait(n + 1, TURN_TIMEOUT_S):
                    raise RuntimeError(f"synthetic turn {n + 1} didn't finish")
                engine.armed = False
        finally:
            stop.set()
            runner.join(15)
        return list(captured.records), list(speaker.said)


# --- the report --------------------------------------------------------------------------------


def verdict(checks: Sequence[Check]) -> tuple[str, list[str]]:
    """PASS, FAIL or INCOMPLETE, and what wasn't measured (the per-item rows don't count)."""
    gates = [c for c in checks if c.target]
    missing = [
        f"{c.section} {c.scope} {c.metric}" for c in gates if c.result == MISSING
    ]
    if any(c.result == FAIL for c in gates):
        return "FAIL", missing
    return ("INCOMPLETE" if missing else "PASS"), missing


def summary(checks: Sequence[Check], source: str) -> str:
    lines = [f"Voice benchmark ({source})"]
    for c in checks:
        if c.section == "spoken" and c.scope not in SPOKEN_SCOPES:
            continue
        if c.section == "latency" and c.metric != "to first audio":
            continue
        name = {
            "latency": f"PM14 {TRIGGER_NAMES.get(c.scope, c.scope)}",
            "spoken": f"Spoken length ({c.scope})",
        }.get(c.section, f"{c.scope}")
        value = c.value or "-"
        target = f" (target {c.target})" if c.target else ""
        note = f"; {c.note}" if c.note else ""
        lines.append(f"  {name} {c.metric}: {value}{target}: {c.result}{note}")
    result, missing = verdict(checks)
    if missing:
        lines.append("  not measured: " + "; ".join(missing))
    lines.append(f"Result: {result}")
    return "\n".join(lines) + "\n"


def write(checks: Sequence[Check], text: str, path: Path) -> Path:
    """The TSV at ``path`` and the summary beside it (.txt): the summary's path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(COLUMNS)
        for c in checks:
            w.writerow(
                [c.section, c.scope, c.metric, c.value, c.target, c.result, c.note]
            )
    txt = path.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    return txt


# --- the command -------------------------------------------------------------------------------


def _pid(value: str) -> tuple[str, int]:
    name, sep, pid = value.partition("=")
    if name not in (CORE, VOICE) or not sep or not pid.isdigit():
        raise argparse.ArgumentTypeError("give core=PID or voice=PID")
    return name, int(pid)


def _ms(value: str) -> float:
    ms = float(value)
    if not 0 <= ms <= 20000:
        raise argparse.ArgumentTypeError("0 to 20000 ms")
    return ms / 1000


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gos voice benchmark",
        description=(
            "Latency on both trigger paths (PM14), peak RAM and VRAM, GTNH's FPS and TPS, and "
            "the spoken length of the eval set's answers (D23), each checked against plan 8 "
            "WO-5.7's targets. docs/runbooks/voice_benchmarks.md has the steps."
        ),
    )
    p.add_argument("--config", type=Path, help="gos.toml (default: GOS_HOME's)")
    p.add_argument(
        "--last", type=int, default=100, help="turns from the log (default 100)"
    )
    p.add_argument(
        "--minutes",
        type=float,
        default=0,
        help="sample RAM and VRAM this long and use the turns logged meanwhile",
    )
    p.add_argument(
        "--pid", type=_pid, action="append", default=[], metavar="ROLE=PID",
        help="core=PID or voice=PID instead of finding them by their command lines",
    )  # fmt: skip
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="run the voice client here on fakes and synthetic audio instead of the log",
    )
    p.add_argument("--turns", type=int, default=5, help="synthetic turns per trigger")
    p.add_argument(
        "--transcript", default=QUESTION, help="what each synthetic turn says"
    )
    for stage in ("stt", "core", "tts"):
        p.add_argument(
            f"--{stage}-ms",
            type=_ms,
            default=0.0,
            help=f"synthetic: add to the {stage} stage",
        )
    for name in ("fps-before", "fps-during"):
        p.add_argument(f"--{name}", metavar="TEXT|FILE", help="F3's fps readings")
    for name in ("tps-before", "tps-during"):
        p.add_argument(f"--{name}", metavar="TEXT|FILE", help="/forge tps output")
    p.add_argument("--out", type=Path, help="the TSV report's path")
    return p


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    from gregtec_voice import settings, tts

    out = out or sys.stdout
    args = parser().parse_args(argv)
    if args.turns < 1 or args.last < 1 or args.minutes < 0:
        print("--turns and --last must be at least 1, --minutes at least 0", file=out)
        return EXIT_SETUP
    try:
        cfg, voice_settings = settings.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return EXIT_SETUP
    say = tts.prepare(tts.lexicon(cfg.gos_home))  # what the speaker says (plan 7.15)
    checks: list[Check] = []
    peaks: Peaks | None = None
    if args.synthetic:
        source = (
            "synthetic: fakes and synthetic audio on this machine, not a PC measurement"
        )
        processes: dict[str, list[int]] = {"voice+core": [os.getpid()]}
        sampler = Sampler(processes).start()
        answer = _fast_lane(cfg)
        try:
            core = SyntheticCore(answer, args.core_ms)
            print(f"Synthetic run: {args.turns} talk-key and {args.turns} wake turns...",
                  file=out)  # fmt: skip
            turns, _ = synthetic_turns(
                voice_settings,
                core,
                turns=args.turns,
                transcript=args.transcript,
                say=say,
                stt_s=args.stt_ms,
                tts_s=args.tts_ms,
            )
        except RuntimeError as exc:
            print(f"The synthetic run failed: {exc}", file=out)
            return EXIT_PROBLEM
        finally:
            peaks = sampler.stop()
            _close(answer)
        wakes: list[dict[str, Any]] = []
        why_resources = ""
    else:
        source = f"the voice client's log in {cfg.logs_dir}"
        processes = {}
        why_resources = "give --minutes to sample while GOS runs"
        started = datetime.now(UTC)
        if args.minutes > 0:
            found = _processes(args.pid)
            if isinstance(found, str):
                why_resources = found
            else:
                processes = found
                print(
                    f"Sampling for {args.minutes:g} minutes (core: "
                    f"{_pids(found[CORE])}, voice: {_pids(found[VOICE])}); play "
                    "and ask GOS now. Ctrl+C ends early.",
                    file=out,
                )
                peaks = _sample(found, args.minutes * 60)
        turns = turn_records(cfg.logs_dir)
        wakes = turn_records(cfg.logs_dir, "wake")
        if args.minutes > 0:
            turns, wakes = since(turns, started), since(wakes, started)
        turns = turns[-args.last :]
    checks += latency(turns, wakes, out)
    checks += resource_checks(peaks, processes, why_resources)
    checks += game_checks(
        args.fps_before, args.fps_during, args.tps_before, args.tps_during
    )
    checks += live_spoken_checks(turns, source)
    # after the sampling, so its CPU and RAM aren't counted as GOS's
    try:
        answers, why = eval_answers(cfg)
    except (
        KeyboardInterrupt
    ):  # L4 of the WO-5.7 review: what was measured is still reported
        answers, why = None, "interrupted"
    checks += spoken_checks(answers, say.said, why)
    text = summary(checks, source)
    print("\n" + text, end="", file=out)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = args.out or cfg.data_dir / "reports" / f"voice-benchmark-{stamp}.tsv"
    txt = write(checks, text, path)
    print(f"report: {path}\nsummary: {txt}", file=out)
    measured = any(c.section == "latency" and c.result != MISSING for c in checks)
    return EXIT_PROBLEM if verdict(checks)[0] == "FAIL" or not measured else 0


def _pids(pids: list[int]) -> str:
    return ", ".join(map(str, pids)) or "not running"


def _processes(given: list[tuple[str, int]]) -> dict[str, list[int]] | str:
    """The roles' pids (``--pid`` first), or why they can't be found."""
    try:
        found = find_processes()
    except ImportError:
        return "psutil isn't installed (uv sync)"
    for role, pid in given:
        found[role] = [pid]
    return found


def _sample(processes: dict[str, list[int]], seconds: float) -> Peaks:
    sampler = Sampler(processes).start()
    ticks = threading.Event()
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline and not ticks.wait(0.5):
            pass  # short waits: on Windows, Ctrl+C can't interrupt one long wait
    except KeyboardInterrupt:
        pass
    while True:  # a second Ctrl+C while the sampler stops still keeps what it saw
        try:
            return sampler.stop()
        except KeyboardInterrupt:
            continue


@dataclass
class _Lane:
    """The fast lane on the fake model over GOS_HOME's DB, for the synthetic core."""

    agents: Any

    def __call__(self, text: str) -> dict[str, Any]:
        return self.agents.asker().ask(text).as_dict()


def _fast_lane(cfg: Config) -> _Lane | None:
    from gregtec.agents import ask
    from gregtec.db import access

    if not access.db_path(cfg.gos_home).is_file():
        return None
    return _Lane(ask.setup(cfg, fake=True, deep_lane=False))


def _close(answer: _Lane | None) -> None:
    if answer is not None:
        answer.agents.close()
