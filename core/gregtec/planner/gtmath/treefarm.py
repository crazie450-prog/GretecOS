"""GT's Tree Growth Simulator (``MTETreeFarm``, 2.9) per operation (WO-4.2, D233).

Re-implemented from GT 5.09.54.265's behaviour (GT is LGPL-3.0; source read at the tag, nothing
copied). One operation takes :data:`TICKS` ticks at ``VP[tier]`` EU/t, the tier being the energy
hatches' (``getTierExtended(voltage x amps)``, at least 1). For each mode the sapling has an
output in and a tool is present for, it gives the sapling's product for that mode x the mode's
multiplier x :func:`tier_multiplier` x the tool's multiplier. GT's NEI page
(``treeGrowthSimulatorFakeRecipes``, exported as ``gt.recipe.treefarm``) lists the products with
the mode multiplier already applied (outputs in mode order: log, sapling, leaves, fruit), so a
planner output is the NEI amount x the tier and tool multipliers. The sapling sits in the
controller and is never used up. A tool loses 1 durability (a mechanical tool) or 32 EU of charge
(an electric one) per operation.
"""

from __future__ import annotations

from gregtec.planner.gtmath.tiers import VP

TICKS = 100  # TICKS_PER_OPERATION
TOOL_CHARGE = 32  # TOOL_CHARGE_PER_OPERATION, EU per operation from an electric tool
MODES = ("log", "sapling", "leaves", "fruit")  # Mode, in NEI's output order
# Tool multipliers per mode (TOOL_MULTIPLIER_*): name -> (mode, multiplier, electric).
TOOLS: dict[str, tuple[str, int, bool]] = {
    "saw": ("log", 1, False),
    "buzzsaw": ("log", 2, True),
    "chainsaw": ("log", 4, True),
    "branch_cutter": ("sapling", 1, False),
    "grafter": ("sapling", 4, False),
    "shears": ("leaves", 1, False),
    "wire_cutter": ("leaves", 2, False),
    "electric_wire_cutter": ("leaves", 4, True),
    "knife": ("fruit", 1, False),
}


def tier_multiplier(tier: int) -> int:
    """``getTierMultiplier``: 2 t^2 - 2 t + 5."""
    return 2 * tier * tier - 2 * tier + 5


def eut(tier: int) -> int:
    """EU/t of one operation at ``tier`` (``GTValues.VP``)."""
    return VP[max(1, tier)]


def output(nei_amount: int, tier: int, tool: str) -> int:
    """Items one operation gives in the tool's mode, from NEI's amount for that mode."""
    return nei_amount * tier_multiplier(max(1, tier)) * TOOLS[tool][1]
