"""WO-4.6's acceptance on a real server harness DB: the four objective modes at LV, HV and IV,
each returning 2-3 ranked options (plan 8 row 4.6), through `gos eval power-modes`'s cases.

As test_power_objectives_harness.py, these run only with GOS_REQUIRE_HARNESS_RUN=1 (CI's mod
job, after the harnesses; locally: mod/README.md). Every case goes through the deep lane's
``design_power`` tool, ``plan_explain`` with fake planner models and the Power view's payload
(gregtec.planner.powereval); the report lands in the test's tmp_path (``power_modes.md``) for the
PR's evidence. Nothing is written outside tmp_path: the eval copies the DB.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gregtec.db import access, build
from gregtec.ingest import collect
from gregtec.planner import powereval, powerview

REPO = Path(__file__).resolve().parents[2]


def _require_harness() -> None:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    _require_harness()
    game = REPO / "mod" / "run" / "server"
    if not (game / collect.EXPORTS).is_dir():
        pytest.fail(f"no harness run in {game / collect.EXPORTS} (mod/README.md)")
    home = tmp_path_factory.mktemp("power-view")
    collect.collect(game, home, 1, allow_harness=True)
    result = build.build(home, allow_harness=True)
    assert result.loaded.power_status == "ok", result.loaded.power_error
    return access.db_path(home)


def test_all_four_modes_at_lv_hv_iv_return_two_or_three_ranked_options(
    db: Path, tmp_path: Path
) -> None:
    cases = powereval.load_cases()
    assert len(cases) == 12
    results = powereval.run(db, cases, tmp_path / "scratch")
    report = tmp_path / "power_modes.md"
    report.write_text(powereval.report(results, "the server harness DB"), "utf-8")
    print(f"report: {report}")
    failed = {
        r.case.id: r.error or {k: v for k, v in r.checks.items() if not v[0]}
        for r in results
        if not r.ok
    }
    assert failed == {}
    for r in results:
        assert 2 <= len(r.plans) <= 3
        # Each option's view shows its distribution checks on the real storage tables.
        home = tmp_path / "scratch"
        v = powerview.view(home, r.plans[0]["id"], 600.0)
        assert v["ledger"]["window_s"] == 600.0
        assert v["ledger"]["distribution"]["notes"] is not None
        for o in v["options"]:
            for c in o["cables"]:  # GT's own cable fields, cited (D240)
                assert c["ref"].startswith("mte:") and c["amps"] >= 1
