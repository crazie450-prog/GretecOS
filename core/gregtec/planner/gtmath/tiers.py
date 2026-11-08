"""GT's voltage tiers, the tier and amperage helpers, and heating coils (WO-3.1, D173).

From GT 5.09.54.265 [E§2.1, E§2.5]: ``GTValues.V``, ``VP`` and ``VN``; ``GTUtility.getTier``,
``getTierExtended`` and ``getAmperageForTier``; ``HeatingCoilLevel``'s heat; and the Electric
Blast Furnace's heating capacity, the coil rule the EBF family shares.
"""

from __future__ import annotations

from gregtec.planner.gtmath.jmath import ceil_div

# GTValues.V: one amp of each tier, ULV..MAX, then GT's "error tier" (index 15, MAX+).
V: tuple[int, ...] = (
    8,
    32,
    128,
    512,
    2048,
    8192,
    32768,
    131072,
    524288,
    2097152,
    8388608,
    33554432,
    134217728,
    536870912,
    2147483640,  # Integer.MAX_VALUE - 7
    8589934592,
)
# GTValues.VP: the "practical" recipe voltage of each tier, V * 30 / 32 rounded down.
VP: tuple[int, ...] = tuple(v * 30 // 32 for v in V)
# GTValues.VN.
VN: tuple[str, ...] = (
    "ULV",
    "LV",
    "MV",
    "HV",
    "EV",
    "IV",
    "LuV",
    "ZPM",
    "UV",
    "UHV",
    "UEV",
    "UIV",
    "UMV",
    "UXV",
    "MAX",
    "MAX+",
)


def get_tier(eu: int) -> int:
    """``GTUtility.getTier``: the lowest tier whose voltage is at least ``eu``; 0 up to 8 EU
    (negative included), 15 above ``V[14]``."""
    if eu > V[14]:
        return 15
    if eu <= V[0]:
        return 0
    return ((eu - 1).bit_length() - 2) // 2


def get_tier_extended(eu: int) -> int:
    """``GTUtility.getTierExtended``: :func:`get_tier` with no cap at 15 (mega machines' tier of
    their total input EU/t)."""
    if eu <= V[0]:
        return 0
    return ((eu - 1).bit_length() - 2) // 2


def amperage_for_tier(voltage: int, tier: int) -> int:
    """``GTUtility.getAmperageForTier``: amps of ``tier`` that carry ``voltage`` EU/t, rounded
    up."""
    return ceil_div(voltage, V[tier])


# HeatingCoilLevel, by coil tier (HeatingCoilLevel.getTier(): Cupronickel 0 ... Eternal 13), the
# numbering of the machine facts' coil_tier (D109).
COILS: tuple[str, ...] = (
    "Cupronickel",
    "Kanthal",
    "Nichrome",
    "TPV-Alloy",
    "HSS-G",
    "HSS-S",
    "Naquadah",
    "Naquadah Alloy",
    "Trinium",
    "Electrum Flux",
    "Awakened Draconium",
    "Infinity",
    "Hypogen",
    "Eternal",
)
# MTEElectricBlastFurnace: HEAT_PER_VOLTAGE_TIER and BASE_TIER_FOR_HEAT_INCREASE (MV).
HEAT_PER_VOLTAGE_TIER = 100
BASE_TIER_FOR_HEAT_INCREASE = 2


def coil_heat(coil_tier: int) -> int:
    """``HeatingCoilLevel.getHeat()`` of the coil at ``coil_tier``: 1 + 900 x its level's
    ordinal (Cupronickel 1801 K ... Eternal 13501 K)."""
    if not 0 <= coil_tier < len(COILS):
        raise ValueError(f"no heating coil of tier {coil_tier} (0..{len(COILS) - 1})")
    return 1 + 900 * (coil_tier + 2)


def ebf_heat(coil_tier: int, max_input_voltage: int) -> int:
    """The Electric Blast Furnace's heating capacity: its coils' heat plus 100 K for each voltage
    tier of ``max_input_voltage`` (the hatches' summed voltage, ``getMaxInputVoltage()``) above
    MV, minus 100 K per tier below it."""
    tier = get_tier(max_input_voltage)
    return coil_heat(coil_tier) + HEAT_PER_VOLTAGE_TIER * (
        tier - BASE_TIER_FOR_HEAT_INCREASE
    )
