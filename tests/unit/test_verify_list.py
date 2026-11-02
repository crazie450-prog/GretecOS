"""The VERIFY list `gos doctor` prints must match plan section 13.2 exactly."""

from __future__ import annotations

import re
from pathlib import Path

from gregtec.doctor import load_verify_list

PLAN = Path(__file__).resolve().parents[2] / "docs" / "implementation_plan.md"
ROW = re.compile(r"^\| (V\d\d) \| (.*) \| (.*) \| (.*) \|\s*$")


def test_matches_plan_13_2() -> None:
    plan_rows = []
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        if m := ROW.match(line):
            plan_rows.append(tuple(c.replace("\\|", "|").strip() for c in m.groups()))
    packaged = [(v.id, v.item, v.how, v.wo) for v in load_verify_list()]
    assert packaged == plan_rows
    assert len(packaged) >= 33
