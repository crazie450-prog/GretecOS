"""WO-11.3: the automated fake day one on a real harness DB (plan 8 row 11.3, plan 10; D336).

tests/rehearsal/day_one.py starts the real supervisor, core, watchers, voice client and Discord
bot on a fake Prism tree and a GOS_HOME built from the server harness's export (``gos export
collect`` + ``gos db build``, D340's cache), and drives one scripted session through every
subsystem with fakes for the model, the mic, Discord's gateway and the game; the game's files are
written by the watchers' soak writer for :data:`SOAK_S` (default a real 10 minutes, time-
compressed: a save every 0.5 s), while the G3, G4 and pack-update harness suites and the
supervisor's hard-kill test run beside it. The report (each plan-10 line green, red or PC only)
is printed and written to ``GOS_REHEARSAL_REPORT`` when set.

As the other harness tests: only with GOS_REQUIRE_HARNESS_RUN=1 after the server harnesses
(the export and the listener's, mod/README.md); nothing is written outside tmp_path.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gregtec.ingest import collect

REPO = Path(__file__).resolve().parents[2]
REHEARSAL = REPO / "tests" / "rehearsal"
GAME = REPO / "mod" / "run" / "server"
LISTENER = GAME / "gregteclink" / "events" / "machines.jsonl"
SOAK_S = float(os.environ.get("GOS_REHEARSAL_SOAK_S", 600))
SUITES = {
    "suite_g3": ["tests/integration/test_g3_lines_harness.py"],
    "suite_g4": ["tests/integration/test_g4_lines_harness.py"],
    "suite_pack_update": ["tests/integration/test_pack_update_harness.py"],
    "suite_supervisor_crash": [
        "tests/unit/ops/test_crash_recovery.py",
        "-k",
        "supervisor_killed_hard",
    ],
}


@pytest.fixture
def rehearsal_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if not (GAME / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {GAME / collect.EXPORTS} (mod/README.md)")
    monkeypatch.syspath_prepend(str(REHEARSAL))
    monkeypatch.setenv("GOS_ALLOW_ELEVATED", "1")
    yield


@pytest.mark.usefixtures("rehearsal_path")
def test_the_fake_day_one_on_a_real_harness_db(
    tmp_path: Path, built_home: Callable[[Path, Path], Path]
) -> None:
    import day_one
    import day_one_checklist as cl

    r = day_one.Rehearsal(
        tmp_path / "day-one",
        day_one.harness_home(GAME, built_home),
        day_one.Options(
            db="harness",
            soak_s=SOAK_S,
            suites=SUITES,
            listener_events=LISTENER if LISTENER.is_file() else None,
        ),
    )
    results = r.run()
    out = os.environ.get("GOS_REHEARSAL_REPORT")
    text = day_one.main_report(
        results, Path(out) if out else tmp_path / "report.md", "Fake day one (harness)"
    )
    print(text)
    print("\n".join(r.log))
    assert cl.unknown_steps(list(results)) == []
    assert cl.green(results), text
