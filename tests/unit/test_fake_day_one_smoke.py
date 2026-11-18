"""WO-11.3: the fake day one's short smoke, on Ubuntu and Windows CI (D336).

The same rehearsal as tests/integration/test_fake_day_one.py (tests/rehearsal/day_one.py: the
real supervisor, core, watchers, voice client and Discord bot, the fake model, mic, gateway and
game), on the dashboard e2e core's made-up data instead of a harness DB, with a 20-second soak.
The steps that need real game data (the designer, power, crops, the harness suites) are left
"not run" here; everything else must be green. ``serial``: it starts five processes and times
their restarts, so CI runs it with no other xdist worker beside it (D340).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REHEARSAL = Path(__file__).resolve().parents[1] / "rehearsal"


@pytest.fixture
def rehearsal_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.syspath_prepend(str(REHEARSAL))
    monkeypatch.setenv("GOS_ALLOW_ELEVATED", "1")  # GitHub's Windows runners run elevated
    yield


@pytest.mark.serial
@pytest.mark.usefixtures("rehearsal_path")
def test_the_fake_day_one_smoke_on_made_up_data(tmp_path: Path) -> None:
    import day_one
    import day_one_checklist as cl

    r = day_one.Rehearsal(
        tmp_path / "day-one",
        day_one.fixture_home,
        day_one.Options(db="fixture", soak_s=float(os.environ.get("GOS_REHEARSAL_SOAK_S", 20))),
    )
    results = r.run()
    text = day_one.main_report(results, tmp_path / "report.md", "Fake day one (smoke)")
    print(text)
    print("\n".join(r.log))
    assert cl.unknown_steps(list(results)) == []
    red = {k: v.detail for k, v in results.items() if v.status == cl.RED}
    assert red == {}, text
    harness_only = {
        "design_accept",
        "design_pin_rank",
        "design_batch",
        "design_export",
        "power_design",
        "crop_step",
        "suite_g3",
        "suite_g4",
        "suite_pack_update",
        "suite_supervisor_crash",
    }
    not_run = {k for k, v in results.items() if v.status == cl.NOT_RUN}
    assert not_run <= harness_only, not_run
    assert sys.platform == "win32" or results["crash_core"].status == cl.GREEN
