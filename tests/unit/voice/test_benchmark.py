"""WO-5.7: ``gos voice benchmark``: each section's numbers and its thresholds, checked by code, and
the synthetic run end to end through the real voice client on fakes and synthetic audio."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gregtec import voicebench as vb
from gregtec_voice import cli
from gregtec_voice import speech_filter as sf
from gregtec_voice.settings import VoiceSettings


def turn(trigger: str, total: int | None, **kw: object) -> dict[str, Any]:
    return {
        "msg": "turn",
        "trigger": trigger,
        "outcome": "answered",
        "held_ms": 900,
        "close_ms": 130,
        "stt_ms": 250,
        "core_ms": 700,
        "speak_ms": 300,
        "total_ms": total,
        **kw,
    }


def toml(tmp_path: Path) -> Path:
    path = tmp_path / "gos.toml"
    path.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n', encoding="utf-8"
    )
    return path


# --- latency -----------------------------------------------------------------------------------


def test_pm14_is_checked_per_trigger_on_the_p50() -> None:
    turns = [turn("key", t) for t in (1600, 1700, 1800)]
    turns += [turn("wake", t) for t in (2100, 2300, 2400)]
    turns.append(
        turn("key", 99999, outcome="nothing said")
    )  # not answered: not counted
    checks = vb.latency(turns, [], io.StringIO())
    gate = {c.scope: c for c in checks if c.metric == "to first audio"}
    assert gate["key"].result == vb.PASS and gate["key"].value == "1700 ms"
    assert gate["wake"].result == vb.FAIL and gate["wake"].value == "2300 ms"
    stages = {(c.scope, c.metric) for c in checks if c.result == vb.INFO}
    assert ("key", "stt") in stages and ("wake", "speak") in stages


def test_a_trigger_with_no_turns_is_not_measured() -> None:
    checks = vb.latency([turn("key", 900)], [], io.StringIO())
    gate = {c.scope: c.result for c in checks if c.metric == "to first audio"}
    assert gate == {"key": vb.PASS, "wake": vb.MISSING}
    checks = vb.latency([turn("wake", None)], [], io.StringIO())
    assert [c.result for c in checks if c.metric == "to first audio"] == [
        vb.MISSING,
        vb.FAIL,  # answered but never heard
    ]


def test_since_keeps_the_records_logged_in_the_window() -> None:
    start = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
    records = [
        {"ts": "2026-11-12T09:59:59.999+00:00"},
        {"ts": "2026-11-12T10:00:00.000+00:00"},
        {"ts": "not a time"},
        {},
    ]
    assert vb.since(records, start) == [records[1]]


# --- resources ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cmd", "role"),
    [
        (["C:\\GOS\\.venv\\Scripts\\python.exe", "-m", "gregtec.api", "--supervised"], "core"),
        (["C:\\GOS\\.venv\\Scripts\\gos.exe", "voice", "run"], "voice"),
        (["/usr/bin/python3", "/home/x/.venv/bin/gos", "voice", "run", "--supervised"], "voice"),
        (["python", "-m", "gregtec_voice", "run"], "voice"),
        (["python", "-m", "gregtec_voice.cli", "run"], "voice"),
        # uv's trampoline: Python running the gos.exe launcher
        (["C:\\GOS\\.venv\\Scripts\\python.exe", "C:\\GOS\\.venv\\Scripts\\gos.exe", "voice",
          "run"], "voice"),
        (["python", "-m", "gregtec_voice.cli", "keys"], None),
        (["gos", "voice", "benchmark", "--minutes", "5"], None),
        (["gos", "serve"], None),
        (["javaw.exe", "-Xmx6G", "net.minecraft.launchwrapper.Launch"], None),
    ],
)  # fmt: skip
def test_role_of(cmd: list[str], role: str | None) -> None:
    assert vb.role_of(cmd) == role


def test_find_processes_by_command_line() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", "gregtec.api"]
    )
    try:
        deadline = time.monotonic() + 10
        while child.pid not in vb.find_processes()[vb.CORE]:
            assert time.monotonic() < deadline, "the stand-in core wasn't found"
            time.sleep(0.1)
    finally:
        child.kill()
        child.wait()


class Smi:
    """nvidia-smi's answers: the GPU's totals and the CUDA processes."""

    def __init__(self, apps: list[list[str]]) -> None:
        self.apps = apps
        self.calls = 0

    def __call__(self, *query: str) -> list[list[str]] | None:
        self.calls += 1
        if query[0].startswith("--query-gpu"):
            return [["1200", "8192"], ["6200", "16376"]]  # the busiest GPU counts
        return self.apps


def test_the_sampler_reads_ram_and_vram() -> None:
    import os

    me = os.getpid()
    smi = Smi([[str(me), "812"], ["4", "[N/A]"]])
    sampler = vb.Sampler({"voice": [me], "core": [999999999]}, interval=0.01, smi=smi)
    sampler.start()
    time.sleep(0.1)
    peaks = sampler.stop()
    assert peaks.samples >= 3 and peaks.free_min is not None and peaks.ram_total
    assert peaks.rss["voice"] > 10 and "core" not in peaks.rss  # no such process
    assert peaks.vram_used == 6200 and peaks.vram == {"voice": 812}
    checks = vb.resource_checks(peaks, sampler.processes)
    by = {(c.scope, c.metric): c for c in checks}
    assert by[("core", "peak RAM")].result == vb.MISSING
    assert by[("gpu", "VRAM used at peak")].note.startswith(
        "of 16376 MB, 10176 MB free"
    )


def test_ram_free_at_peak_is_the_gate() -> None:
    low = vb.Peaks(free_min=4095, ram_total=32768, rss={"core": 400}, samples=5)
    ok = vb.Peaks(free_min=4096, ram_total=32768, samples=5)
    gate = "RAM free at peak"
    assert [c.result for c in vb.resource_checks(low, {}) if c.metric == gate] == [
        vb.FAIL
    ]
    assert [c.result for c in vb.resource_checks(ok, {}) if c.metric == gate] == [
        vb.PASS
    ]
    missing = vb.resource_checks(None, {}, "give --minutes")
    assert missing[0].result == vb.MISSING and missing[0].note == "give --minutes"
    no_psutil = vb.Peaks(problem="psutil isn't installed (uv sync)")
    assert vb.resource_checks(no_psutil, {})[0].note.startswith("psutil")


# --- game --------------------------------------------------------------------------------------

LATEST_LOG = """\
[18:01:02] [Client thread/INFO]: [CHAT] Dim  0 : Mean tick time: 31.002 ms. Mean TPS: 20.000
[18:01:02] [Client thread/INFO]: [CHAT] Overall : Mean tick time: 31.250 ms. Mean TPS: 20.000
[18:09:40] [Client thread/INFO]: [CHAT] Overall : Mean tick time: 52,500 ms. Mean TPS: 19,048
"""


def test_parse_tps_takes_the_last_overall_line() -> None:
    assert vb.parse_tps(LATEST_LOG) == (52.5, 19.048)
    assert vb.parse_tps("Dim  0 : Mean tick time: 1.0 ms. Mean TPS: 20.000") is None


def test_parse_fps() -> None:
    assert vb.parse_fps("120 fps, 34 chunk updates\n118 fps, 2 chunk updates") == [
        120,
        118,
    ]
    assert vb.parse_fps("60\n58.5\n") == [60, 58.5]
    assert vb.parse_fps("60, 62, 61") == [60, 62, 61]
    # L2 of the review: a log's timestamps and other digits are no FPS reading
    assert vb.parse_fps(LATEST_LOG) == []
    assert vb.parse_fps("[18:01:02] [Client thread/INFO]: Loaded 12 chunks") == []


def test_a_stale_tps_reading_is_not_measured(tmp_path: Path) -> None:
    """M1 of the review: the same latest.log copied twice with no new /forge tps between."""
    before, during = tmp_path / "tps-before.log", tmp_path / "tps-during.log"
    before.write_text(LATEST_LOG, encoding="utf-8")
    during.write_text(
        LATEST_LOG + "[18:12:00] [Client thread/INFO]: [CHAT] Unknown command\n",
        encoding="utf-8",
    )
    (check,) = [c for c in vb.game_checks(None, None, str(before), str(during)) if
                c.metric == "TPS"]  # fmt: skip
    assert check.result == vb.MISSING and "no new /forge tps reading" in check.note
    # a new reading later in the same log counts
    during.write_text(
        LATEST_LOG
        + "[18:20:00] [Client thread/INFO]: [CHAT] Overall : Mean tick time: "
        "50.000 ms. Mean TPS: 20.000\n",
        encoding="utf-8",
    )
    (check,) = [c for c in vb.game_checks(None, None, str(before), str(during)) if
                c.metric == "TPS"]  # fmt: skip
    assert check.result == vb.PASS and check.value == "19.048 -> 20.000"
    # pasted lines without a timestamp: the same paste twice is no new reading, a second
    # paste of its own is one (if only its tick time differs)
    t = "Overall : Mean tick time: {} ms. Mean TPS: 20.000"
    assert vb.stale_tps(t.format("31.250"), t.format("31.250"))
    assert vb.stale_tps(t.format("31.250"), "/forge tps\n" + t.format("31.250") + "\n")
    assert not vb.stale_tps(t.format("31.250"), t.format("31.251"))
    assert not vb.stale_tps("Dim 0 : x\n" + t.format("31.250"), t.format("31.250"))


def test_fps_drop_and_tps_thresholds(tmp_path: Path) -> None:
    def results(**kw: str | None) -> dict[str, str]:
        given = {"fps_before": None, "fps_during": None, "tps_before": None,
                 "tps_during": None, **kw}  # fmt: skip
        return {c.metric: c.result for c in vb.game_checks(**given)}

    assert results() == {"FPS drop": vb.MISSING, "TPS": vb.MISSING}
    assert results(fps_before="100 fps", fps_during="90 fps")["FPS drop"] == vb.PASS
    assert results(fps_before="100 fps", fps_during="89.9 fps")["FPS drop"] == vb.FAIL
    assert results(fps_before="nothing", fps_during="90 fps")["FPS drop"] == vb.MISSING
    t = "Overall : Mean tick time: {} ms. Mean TPS: {}"
    assert results(tps_before=t.format(10, 20.0), tps_during=t.format(49, 19.9))[
        "TPS"
    ] == (vb.PASS)
    assert results(tps_before=t.format(10, 20.0), tps_during=t.format(55, 19.8))[
        "TPS"
    ] == (vb.FAIL)
    log = tmp_path / "latest.log"  # a copy of Lab's log: read, never written
    log.write_text(LATEST_LOG, encoding="utf-8")
    checks = vb.game_checks(None, None, t.format(30, 20.0), str(log))
    assert checks[1].result == vb.FAIL and checks[1].value == "20.000 -> 19.048"
    # a file that isn't there yet is no reading (its name's "57" is not 57 fps)
    gone = str(tmp_path / "gos57" / "fps-during.txt")
    assert results(fps_before="60 fps", fps_during=gone)["FPS drop"] == vb.MISSING


# --- spoken length -----------------------------------------------------------------------------


def unfiltered(text: str) -> sf.Said:
    """The reply as written, counted as heard: no filter, nothing cut."""
    return sf.Said(text, len(sf.sentences(text)), sf.said_words(text), False)


def gates(checks: list[vb.Check], scope: str = "eval set") -> dict[str, str]:
    return {c.metric: c.result for c in checks if c.scope == scope and c.target}


def test_spoken_share_must_reach_95_percent() -> None:
    long = " ".join(["word"] * 41) + "."
    answers = [(f"q{i}", "Short answer." if i else long) for i in range(20)]
    checks = vb.spoken_checks(answers, unfiltered)
    assert checks[0].result == vb.PASS and checks[0].value == "95.0%"  # 19 of 20
    assert [c.scope for c in checks if c.result == vb.FAIL] == ["q0"]
    answers[1] = ("q1", "One. Two. Three.")
    assert vb.spoken_checks(answers, unfiltered)[0].result == vb.FAIL
    missing = vb.spoken_checks(None, unfiltered, "no DB")
    assert gates(missing) == {"within D23": vb.MISSING, "cut by the filter": vb.MISSING}


def test_the_speech_filter_brings_long_answers_within_d23() -> None:
    long = (
        "Naquadah Alloy Ingot: the first recipe runs in the Electric Blast Furnace at 30,720 "
        "EU/t for 1,234 ticks with 32,768 L of nitrogen (r:abc). Then 1,152 L more. And more."
    )
    assert vb.spoken_checks([("x", long)], sf.said)[0].result == vb.PASS
    assert vb.spoken_checks([("x", long)], unfiltered)[0].result == vb.FAIL


def test_answers_the_filter_cuts_are_counted_and_gated() -> None:
    """L1 of the review: within D23 by construction, so the share cut is reported too."""
    long = " ".join(["word"] * 45) + "."
    answers = [(f"q{i}", long if i == 0 else "Short answer.") for i in range(20)]
    checks = vb.spoken_checks(answers, sf.said)
    assert gates(checks) == {"within D23": vb.PASS, "cut by the filter": vb.PASS}
    cut = next(c for c in checks if c.metric == "cut by the filter")
    assert (cut.value, cut.note) == ("5.0%", "1 of 20 answers")
    assert next(c for c in checks if c.scope == "q0").note == "cut by the filter"
    answers[1] = ("q1", "One. Two. Three.")
    assert gates(vb.spoken_checks(answers, sf.said)) == {
        "within D23": vb.PASS,
        "cut by the filter": vb.FAIL,  # 2 of 20
    }


def test_the_live_turns_spoken_length() -> None:
    """M2 of the review: the voice client's turn records carry their answer's counts."""
    turns = [
        turn("key", 900, spoken_sentences=2, spoken_words=30, spoken_cut=False)
    ] * 19
    turns.append(
        turn("wake", 900, spoken_sentences=2, spoken_words=40, spoken_cut=True)
    )
    turns.append(turn("key", 900, outcome="nothing said"))  # not answered: not counted
    turns.append(turn("key", 900))  # a client before WO-5.7: no counts
    checks = vb.live_spoken_checks(turns, "the log")
    assert gates(checks, "live") == {
        "within D23": vb.PASS,
        "cut by the filter": vb.PASS,
    }
    assert [c.note for c in checks] == ["20 of 20 answers", "1 of 20 answers"]
    over = [
        *turns,
        turn("key", 900, spoken_sentences=3, spoken_words=12, spoken_cut=True),
    ]
    assert gates(vb.live_spoken_checks(over, "the log"), "live") == {
        "within D23": vb.PASS,  # 20 of 21
        "cut by the filter": vb.FAIL,  # 2 of 21: 9.5%
    }
    none = vb.live_spoken_checks([turn("key", 900)], "the log")
    assert gates(none, "live") == {
        "within D23": vb.MISSING,
        "cut by the filter": vb.MISSING,
    }
    assert "no answered turn in the log logged its spoken length" in none[0].note


def test_eval_answers_needs_a_db(tmp_path: Path) -> None:
    from gregtec.config import Config

    assert vb.eval_answers(Config(gos_home=tmp_path)) == (
        None,
        "no DB: gos export collect and gos db build first",
    )


# --- the report --------------------------------------------------------------------------------


def test_verdict_and_report(tmp_path: Path) -> None:
    met = vb.Check(
        "latency", "key", "to first audio", "900 ms", "P50 <= 1700 ms", vb.PASS
    )
    missing = vb.Check("game", "gtnh", "TPS", target="drop", result=vb.MISSING)
    failed = vb.Check(
        "resources", "system", "RAM free at peak", "1 MB", ">= 4096", vb.FAIL
    )
    item = vb.Check("spoken", "q1", "sentences, words", "3, 50", result=vb.FAIL)
    assert vb.verdict([met]) == ("PASS", [])
    assert vb.verdict([met, item]) == ("PASS", [])  # per-item rows aren't gates
    assert vb.verdict([met, missing]) == ("INCOMPLETE", ["game gtnh TPS"])
    assert vb.verdict([met, missing, failed])[0] == "FAIL"
    text = vb.summary([met, missing], "test")
    assert "PM14 talk key to first audio: 900 ms (target P50 <= 1700 ms): pass" in text
    assert text.endswith("Result: INCOMPLETE\n")
    txt = vb.write([met, item], text, tmp_path / "r" / "bench.tsv")
    lines = (tmp_path / "r" / "bench.tsv").read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == list(vb.COLUMNS)
    assert lines[1].split("\t")[:6] == ["latency", "key", "to first audio", "900 ms",
                                         "P50 <= 1700 ms", "pass"]  # fmt: skip
    assert txt.read_text(encoding="utf-8") == text


# --- the command and the synthetic run ---------------------------------------------------------


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(list(argv), out), out.getvalue()


def test_bench_and_benchmark_read_the_log_window(tmp_path: Path) -> None:
    config = toml(tmp_path)
    logs = tmp_path / "home" / "logs"
    logs.mkdir(parents=True)
    old = {**turn("key", 5000), "ts": "2020-01-01T00:00:00.000+00:00"}
    (logs / "voice.log").write_text(json.dumps(old) + "\n", encoding="utf-8")
    code, out = run("bench", "--config", str(config), "--out", str(tmp_path / "a.tsv"))
    assert (
        code == 1 and "PM14: P50 5000 ms to first audio, target 1700 ms: MISSED" in out
    )
    assert "Result: FAIL" in out and (tmp_path / "a.txt").is_file()
    # --minutes: only what is logged while it samples (nothing here: no turn measured)
    code, out = run("benchmark", "--config", str(config), "--minutes", "0.01")
    assert code == 1 and "No answered turns yet." in out and "Sampling for" in out
    code, out = run("benchmark", "--config", str(config), "--last", "0")
    assert code == 2


def test_benchmark_help_is_its_own() -> None:
    with pytest.raises(SystemExit) as stop:
        cli.main(["benchmark", "--help"], io.StringIO())
    assert stop.value.code == 0


def test_the_synthetic_run_goes_through_both_triggers() -> None:
    core = vb.SyntheticCore(None, 0.0)
    records, said = vb.synthetic_turns(VoiceSettings(), core, turns=2)
    assert [r["trigger"] for r in records] == ["key", "key", "wake", "wake"]
    assert all(r["outcome"] == "answered" for r in records)
    assert all(
        isinstance(r[s], int) for r in records for s in vb.STAGES if s != "stt_ms"
    )
    wake = [r for r in records if r["trigger"] == "wake"]
    # the wake path includes end-of-speech detection (PM14: up to ~0.6 s)
    assert all(r["close_ms"] >= 500 for r in wake)
    assert said == [vb.CANNED] * 4  # say() here is the identity
    assert all(r["spoken_words"] is None for r in records)  # and it can't count


def test_the_synthetic_turns_log_their_spoken_length() -> None:
    from gregtec_voice import tts
    from gregtec_voice.lexicon import Lexicon

    say = tts.prepare(Lexicon())
    core = vb.SyntheticCore(None, 0.0)
    records, said = vb.synthetic_turns(VoiceSettings(), core, turns=1, say=say)
    heard = say.said(vb.CANNED)
    assert said == [heard.text] * 2
    assert [
        (r["spoken_sentences"], r["spoken_words"], r["spoken_cut"]) for r in records
    ] == [(heard.sentences, heard.words, False)] * 2
    checks = vb.live_spoken_checks(records, "synthetic")
    assert gates(checks, "live") == {
        "within D23": vb.PASS,
        "cut by the filter": vb.PASS,
    }


def test_a_slow_synthetic_turn_fails_pm14_by_code(tmp_path: Path) -> None:
    code, out = run(
        "benchmark", "--synthetic", "--turns", "1", "--tts-ms", "2300",
        "--config", str(toml(tmp_path)), "--out", str(tmp_path / "s.tsv"),
    )  # fmt: skip
    assert code == 1, out
    assert "PM14 talk key to first audio" in out and "Result: FAIL" in out
    rows = (tmp_path / "s.tsv").read_text(encoding="utf-8").splitlines()
    gates = [r.split("\t") for r in rows if "\tto first audio\t" in r]
    assert [(g[1], g[5]) for g in gates] == [("key", "FAIL"), ("wake", "FAIL")]
    assert "spoken\teval set\twithin D23\t\t" in "\n".join(
        rows
    )  # no DB here: not measured


def test_a_quick_synthetic_run_meets_pm14(tmp_path: Path) -> None:
    code, out = run(
        "benchmark", "--synthetic", "--turns", "1", "--config", str(toml(tmp_path)),
        "--fps-before", "60 fps", "--fps-during", "57 fps",
        "--out", str(tmp_path / "q.tsv"),
    )  # fmt: skip
    assert code == 0, out
    assert out.count(": met") == 2 and "FPS drop: 5.0%" in out
    assert "Result: INCOMPLETE" in out  # no TPS, no DB for the spoken check
