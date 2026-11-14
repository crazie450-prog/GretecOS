"""The alert engine, the ops tick and the advisor import on their own (G7 review).

WO-6.5's advisor checks read ``place`` from the alert engine, and the engine imports the cards,
which import the tool catalog, which imports the advisor: imported first, in a fresh interpreter,
``gregtec.alerts.engine``, ``gregtec.bridge.ops`` or ``gregtec.advisor.steps`` failed with a
circular import, so ``tests/unit/test_oc_actions.py`` and ``tests/unit/test_alert_rules.py``
couldn't run alone. The full suite hid it, because another test had imported the catalog first.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "gregtec.alerts.engine",
        "gregtec.bridge.ops",
        "gregtec.advisor.checks",
        "gregtec.advisor.steps",
    ],
)
def test_imports_first_in_a_fresh_interpreter(module: str) -> None:
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
