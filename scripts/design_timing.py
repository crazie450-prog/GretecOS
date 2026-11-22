"""Time the three slow designs of WO-3.9 on a GOS_HOME's DB, cold and warm (D352).

    uv run python scripts/design_timing.py <GOS_HOME> [--repeat N] [--cases designer,best,self]

Runs, in one process, the Designer's ``steel ingot`` at HV (rate 1/s, 3 routes), the Power
view's ``best`` at LV and its ``self_sustaining`` at LV with 3 options, through the same
functions the dashboard's design jobs and ``POST /api/designer/design`` / ``POST
/api/power/design`` call. The first run of each case is cold (an empty planner cache), the
next ``--repeat`` warm. Each design saves its plans into GOS_HOME, as from the dashboard: run it
on a copy (a harness home, or a backup restored into a scratch folder), never on Play's DB.
``GOS_PLANNER_CACHE=0`` times it without the cache. Prints one line per run, then JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gregtec.planner import designer, powerview


def _steel(home: Path) -> designer.Designed:
    req = designer.request_from_spec(home, {"target": "steel ingot", "tier": "HV"})
    if not isinstance(req, designer.Request):
        raise SystemExit(f"steel ingot is ambiguous on this DB: {req}")
    return designer.design(home, req)


def _power(spec: dict[str, Any]) -> Callable[[Path], Any]:
    return lambda home: powerview.design(home, powerview.request_from_spec(spec))


CASES: dict[str, tuple[str, Callable[[Path], Any]]] = {
    "designer": ("Designer steel ingot at HV", _steel),
    "best": ("Power best at LV", _power({"mode": "best", "tier": "LV"})),
    "self": (
        "Power self_sustaining at LV, 3 options",
        _power({"mode": "self_sustaining", "tier": "LV", "options": 3}),
    ),
}


def _clear() -> None:
    try:
        from gregtec.planner import cache
    except ImportError:  # a checkout from before WO-3.9: nothing to clear
        return
    cache.clear()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Time WO-3.9's three designs")
    ap.add_argument("home", type=Path)
    ap.add_argument("--repeat", type=int, default=1, help="warm runs per case")
    ap.add_argument("--cases", default=",".join(CASES))
    args = ap.parse_args(argv)
    out: dict[str, dict[str, Any]] = {}
    for name in args.cases.split(","):
        label, run = CASES[name]
        _clear()  # cold: nothing from an earlier case
        times: list[float] = []
        ids: list[str] = []
        for i in range(1 + args.repeat):
            t = time.perf_counter()
            done = run(args.home)
            s = time.perf_counter() - t
            times.append(round(s, 2))
            ids = [str(x) for x in getattr(done, "ids", [])]
            print(
                f"{label}: {'cold' if i == 0 else 'warm'} {s:.2f} s, {ids}", flush=True
            )
        out[name] = {
            "label": label,
            "cold_s": times[0],
            "warm_s": times[1:],
            "ids": ids,
        }
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
