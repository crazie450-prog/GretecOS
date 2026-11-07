"""A simulated pack update for WO-1.9's tests: a copy of a real harness export run, altered as a
GT update would alter it (``tests/integration/test_pack_update_harness.py``).

:func:`alter` copies ``src`` (a complete GregtecLink run) to ``dst`` as run ``run_id``, one hour
later, labelled GT :data:`NEW_GT`, with three GT recipe changes (one removed, one rebalanced, one
added), one crop's tier raised and one new oredict name, then re-hashes the manifest so the run
reads as a genuine export. What it changed is returned, so a test can check the diff finds it.
Nothing outside ``dst`` is written.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NEW_GT = "5.09.54.266"
MAP = "gt.recipe.blastfurnace"
CROP = "cropsnh:allium"
ORE = "gosSimulatedOre"


@dataclass
class Altered:
    removed: dict[str, Any] = field(default_factory=dict)  # the GT line taken out
    changed: dict[str, Any] = field(
        default_factory=dict
    )  # the GT line after the change
    added: dict[str, Any] = field(default_factory=dict)  # the new GT line


def _rehash(run: Path, rel: str, m: dict[str, Any]) -> None:
    data = (run / rel).read_bytes()
    m["files"][rel] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "lines": data.count(b"\n"),
    }


def alter(src: Path, dst: Path, run_id: str) -> Altered:
    shutil.copytree(src, dst)
    m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    out = Altered()
    lines = (dst / "gt_recipes.jsonl").read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    for line in lines:
        d = json.loads(line)
        if d.get("map") == MAP and d.get("idx") == 0 and not out.removed:
            out.removed = d
            continue
        if d.get("map") == MAP and d.get("idx") == 1 and not out.changed:
            d["duration"] = int(d["duration"]) * 4 // 5
            out.changed = d
            new = json.loads(json.dumps(d))
            new["idx"] = 10_000
            new["out"][0]["n"] = int(new["out"][0]["n"]) + 1
            out.added = new
            kept.append(json.dumps(d, separators=(",", ":")))
            kept.append(json.dumps(new, separators=(",", ":")))
            continue
        kept.append(line)
    assert out.removed and out.changed and out.added, "no EBF recipes in the run"
    (dst / "gt_recipes.jsonl").write_text(
        "\n".join(kept) + "\n", encoding="utf-8", newline="\n"
    )
    _rehash(dst, "gt_recipes.jsonl", m)
    # counts.recipes stays: one line out, one in

    if (dst / "crops" / "cropRegistry.csv").is_file():
        path = dst / "crops" / "cropRegistry.csv"
        rows = path.read_text(encoding="utf-8").split("\n")
        for i, row in enumerate(rows):
            if row.startswith(CROP + ","):
                cells = row.split(",")
                cells[11] = str(int(cells[11]) + 1)  # Tier
                rows[i] = ",".join(cells)
        path.write_text("\n".join(rows), encoding="utf-8", newline="")
        _rehash(dst, "crops/cropRegistry.csv", m)

    if (dst / "oredict.jsonl").is_file():
        path = dst / "oredict.jsonl"
        names = path.read_text(encoding="utf-8").splitlines()
        names.append(
            json.dumps(
                {"ore": ORE, "members": [{"item": "minecraft:dirt", "meta": 0}]},
                separators=(",", ":"),
            )
        )
        names.sort(key=lambda s: json.loads(s)["ore"])
        path.write_text("\n".join(names) + "\n", encoding="utf-8", newline="\n")
        _rehash(dst, "oredict.jsonl", m)
        m["counts"]["oredict"] = len(names)
        m["counts"]["oredict_members"] = int(m["counts"]["oredict_members"]) + 1

    created = dt.datetime.strptime(m["created_utc"], "%Y-%m-%dT%H:%M:%SZ")
    m["created_utc"] = (created + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    m["run_id"] = run_id
    m["gt_version"] = NEW_GT
    (dst / "manifest.json").write_text(
        json.dumps(m, indent=1), encoding="utf-8", newline="\n"
    )
    return out
