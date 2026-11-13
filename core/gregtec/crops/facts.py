"""What the crop guide reads from the game DB (WO-6.7): the newest run's crop data (WO-1.10,
``crop:<id>``), the Crop Breeder's recipes (``r:``), CropsNH's machines (``mte:``) and the names
of the blocks and items a guide names. Read once per guide; nothing here computes a number."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field

from gregtec.db import crops as crop_db
from gregtec.ingest.crops import CropData

BREEDER_MAP = "cropsnh.recipes.cropBreeder"
SEED_ITEM = "cropsnh:genericSeed"
# CropsNH's machine classes, by the job the guide gives them
MACHINE_CLASSES = {
    "crop_manager": "com.gtnewhorizon.cropsnh.tileentity.singleblock.MTECropManager",
    "crop_breeder": "com.gtnewhorizon.cropsnh.tileentity.singleblock.MTECropBreeder",
    "seed_generator": "com.gtnewhorizon.cropsnh.tileentity.singleblock.MTESeedGenerator",
}


class NoCropData(LookupError):
    """The DB has no crop data (no run with ``parts.crops`` ok was built)."""


@dataclass(frozen=True)
class BreederRecipe:
    """One Crop Breeder recipe of the export (``r:`` cites it)."""

    ref: str
    child: str
    parents: frozenset[str]
    eut: int
    duration_ticks: int
    voltage_tier: int
    catalysts: tuple[tuple[str, int], ...]  # (display name or registry@meta, amount)


@dataclass(frozen=True)
class Machine:
    mte_id: int
    name: str
    tier: int

    @property
    def ref(self) -> str:
        return f"mte:{self.mte_id}"


@dataclass
class Facts:
    data: CropData
    run_key: str
    breeder: list[BreederRecipe] = field(default_factory=list)
    machines: dict[str, list[Machine]] = field(default_factory=dict)  # by job, by tier
    names: dict[str, str] = field(default_factory=dict)  # registry@meta -> display name
    recipe_refs: frozenset[str] = frozenset()  # every r: the guide may cite

    def crop_name(self, crop: str) -> str:
        c = self.data.crops.get(crop)
        return c.name if c is not None else crop

    def machine(self, job: str, tier: int) -> Machine | None:
        """The machine of ``job`` at exactly ``tier``, else the highest below it."""
        best = None
        for m in self.machines.get(job, []):
            if m.tier <= tier and (best is None or m.tier > best.tier):
                best = m
        return best

    def lowest(self, job: str) -> Machine | None:
        listed = self.machines.get(job, [])
        return min(listed, key=lambda m: m.tier) if listed else None


def _has(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def _seed_crop(nbt_json: str | None) -> str | None:
    if not nbt_json:
        return None
    try:
        tag = json.loads(nbt_json)
    except ValueError:
        return None
    crop = tag.get("crop") if isinstance(tag, dict) else None
    return crop if isinstance(crop, str) else None


def _breeder(conn: sqlite3.Connection, run_id: int) -> list[BreederRecipe]:
    if not _has(conn, "recipe_map"):
        return []
    rows = conn.execute(
        "SELECT r.id, r.ref, r.eut, r.duration_ticks, r.voltage_tier FROM recipe AS r"
        " JOIN recipe_map AS m ON m.id = r.recipe_map_id"
        " WHERE m.unloc_name = ? AND r.run_id = ? ORDER BY r.ref",
        (BREEDER_MAP, run_id),
    ).fetchall()
    out = []
    for rid, ref, eut, duration, vt in rows:
        child = None
        for (nbt,) in conn.execute(
            "SELECT it.nbt_json FROM recipe_output AS o JOIN item AS it ON it.id = o.item_id"
            " WHERE o.recipe_id = ? AND it.registry_name = ?",
            (rid, SEED_ITEM),
        ):
            child = _seed_crop(nbt) or child
        parents: set[str] = set()
        catalysts: list[tuple[str, int]] = []
        for reg, meta, nbt, name, amount, fluid in conn.execute(
            "SELECT it.registry_name, it.meta, it.nbt_json, it.name_en, i.amount, i.fluid_id"
            " FROM recipe_input AS i LEFT JOIN item AS it ON it.id = i.item_id"
            " WHERE i.recipe_id = ? ORDER BY i.slot",
            (rid,),
        ):
            if fluid is not None or reg is None:
                continue  # the enriched fertilizer: the guide computes it (rules)
            if reg == SEED_ITEM and (crop := _seed_crop(nbt)):
                parents.add(crop)
            else:
                catalysts.append((name or f"{reg}@{meta}", int(amount)))
        if child is None or len(parents) < 2:
            continue
        out.append(
            BreederRecipe(
                ref,
                child,
                frozenset(parents),
                int(eut or 0),
                int(duration or 0),
                int(vt or 0),
                tuple(catalysts),
            )
        )
    return out


def _machines(conn: sqlite3.Connection) -> dict[str, list[Machine]]:
    if not _has(conn, "machine"):
        return {}
    out: dict[str, list[Machine]] = {}
    for job, cls in MACHINE_CLASSES.items():
        out[job] = [
            Machine(int(mte), name or f"mte:{mte}", int(tier or 0))
            for mte, name, tier in conn.execute(
                "SELECT mte_id, name_en, tier FROM machine WHERE class_name = ?"
                " ORDER BY tier, mte_id",
                (cls,),
            )
        ]
    return out


def _names(conn: sqlite3.Connection, data: CropData) -> dict[str, str]:
    """Display names of the sub-soil blocks and alternate seeds the crops name."""
    if not _has(conn, "item"):
        return {}
    wanted: set[tuple[str, int]] = set()
    for c in data.crops.values():
        wanted |= {(r.name, r.meta) for r in c.sub_soils}
    wanted |= {(s.registry_name, s.meta or 0) for s in data.seeds}
    out: dict[str, str] = {}
    for reg, meta in sorted(wanted):
        row = conn.execute(
            "SELECT name_en FROM item WHERE registry_name = ? AND meta = ?"
            " AND name_en IS NOT NULL ORDER BY nbt_hash LIMIT 1",
            (reg, meta),
        ).fetchone()
        if row is not None:
            out[f"{reg}@{meta}"] = str(row[0])
    return out


def load(conn: sqlite3.Connection) -> Facts:
    """The newest run with crop data, and what the guide cites beside it."""
    runs = crop_db.runs_with_crops(conn) if _has(conn, "crop") else []
    if not runs:
        raise NoCropData(
            "no crop data in the DB: a client export with CropsNH, then gos db build"
        )
    run_id, run_key = runs[0]
    data = crop_db.read(conn, run_id)
    breeder = _breeder(conn, run_id)
    return Facts(
        data=data,
        run_key=run_key,
        breeder=breeder,
        machines=_machines(conn),
        names=_names(conn, data),
        recipe_refs=frozenset(b.ref for b in breeder),
    )


def from_parts(
    data: CropData,
    breeder: list[BreederRecipe] | None = None,
    machines: Mapping[str, list[Machine]] | None = None,
    names: Mapping[str, str] | None = None,
    run_key: str = "test",
) -> Facts:
    """Facts built in memory (tests and fixtures)."""
    b = list(breeder or [])
    return Facts(
        data=data,
        run_key=run_key,
        breeder=b,
        machines=dict(machines or {}),
        names=dict(names or {}),
        recipe_refs=frozenset(r.ref for r in b),
    )
