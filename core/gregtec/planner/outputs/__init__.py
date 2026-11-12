"""The designer's outputs (WO-3.6, plan §7.1 steps 9-10, D10, D66, [E§1.5], D200-D202).

One module per output, each a pure function of a plan (the canonical plan JSON of
:mod:`gregtec.planner.plan`) and, where it needs names or a recipe's own slots, a :class:`.book.Book`
read from the DB the plan was solved on. No output computes a game number: every amount, rate,
EU/t and duration is the plan's or the DB's, copied or summed.

- :mod:`.bom`: the BOM as TSV (machine, tier, count, OC, parallel, EU/t, utilisation, ``r:`` id);
  its totals equal the plan's ``metrics``;
- :mod:`.footprint`: a bounding box per machine from ``structure.size_*`` plus clearance, summed
  into floor area and volume (the plan's ``layout``);
- :mod:`.mermaid`: the flow diagram for the vault note;
- :mod:`.png`: the flow diagram as a PNG for Discord (Pillow, D201);
- :mod:`.gtnhplanner`: gtnhplanner.com project JSON, the primary export (D66);
- :mod:`.gtnhflow`: gtnh-flow YAML;
- :mod:`.cards`: the dashboard's flow and BOM cards (WO-2.6's catalog, D160);
- :mod:`.store`: persistence (§7.1 step 10): ``data/plans/<id>.json``, the ``plan`` row, the
  vault note and the export files beside the JSON.
"""

from __future__ import annotations

# The export files :mod:`.store` writes beside a plan's JSON, by format name.
FILES: dict[str, str] = {
    "bom": "bom.tsv",
    "mermaid": "flow.mmd",
    "png": "flow.png",
    "gtnhplanner": "gtnhplanner.json",
    "gtnhflow": "gtnh-flow.yaml",
}
