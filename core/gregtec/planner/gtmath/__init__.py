"""GT's own math, ported from one GT version (WO-3.1, D173-D175).

The planner sizes machines with these rules, never with estimates (plan §0.1 principle 1):

- :mod:`.tiers`: voltage tiers, tier and amperage helpers, heating coils;
- :mod:`.overclock`: ``OverclockCalculator`` (standard, perfect, heat, laser and no overclocks);
- :mod:`.parallel`: ``ParallelHelper`` (parallels from EU, inputs and output room; batch mode);
- :mod:`.processing`: the processing-logic power rules (energy hatches, single blocks, a run);
- :mod:`.jmath`: the Java arithmetic they rely on.

The port is pinned to :data:`GT_VERSION`, which must equal ``config.GT_PIN``: a GT pin bump
re-ports from the new tag and re-vendors GT's unit tests (``docs/runbooks/pack_update.md``).
Turbines (``TurbineStatCalculator``) are WO-4.1's.
"""

from __future__ import annotations

from gregtec.planner.gtmath.overclock import (
    Overclock,
    OverclockResult,
    calculate,
    max_allowed_recipe_eut,
    multiplier_under_one_tick,
)
from gregtec.planner.gtmath.parallel import (
    Check,
    Output,
    Parallel,
    ParallelResult,
    Supply,
    determine,
    expected_output,
)
from gregtec.planner.gtmath.processing import (
    EnergyHatch,
    Logic,
    Power,
    Recipe,
    Run,
    basic_machine,
    multiblock_power,
    process,
    total_eu_power,
)
from gregtec.planner.gtmath.tiers import VN, VP, V, coil_heat, ebf_heat, get_tier

# The GT release this is a port of, and the commit its tag names (GT5-Unofficial).
GT_VERSION = "5.09.54.265"
GT_COMMIT = "bb00f3f1023e20d3cce84cf09abb442d22a3a5a5"

__all__ = [
    "GT_COMMIT",
    "GT_VERSION",
    "VN",
    "VP",
    "Check",
    "EnergyHatch",
    "Logic",
    "Output",
    "Overclock",
    "OverclockResult",
    "Parallel",
    "ParallelResult",
    "Power",
    "Recipe",
    "Run",
    "Supply",
    "V",
    "basic_machine",
    "calculate",
    "coil_heat",
    "determine",
    "ebf_heat",
    "expected_output",
    "get_tier",
    "max_allowed_recipe_eut",
    "multiblock_power",
    "multiplier_under_one_tick",
    "process",
    "total_eu_power",
]
