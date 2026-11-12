"""The Crops view's payload and routes (plan 7.14 "Crops: what should I breed next, and how?",
7.17; WO-6.7, D275).

| route                     | who                    | what                                       |
|---------------------------|------------------------|--------------------------------------------|
| GET    /api/crops         | admin, client, device  | the view: his crops, the target and its    |
|                           |                        | guide (``crop`` picks another to look at)  |
| POST   /api/crops/have    | admin, device          | ``{"crop", "stats": [g, ga, r]}``          |
| DELETE /api/crops/have    | admin, device          | ``?crop=``: drop a crop from his list      |
| POST   /api/crops/target  | admin, device          | ``{"crop", "aim"?}`` or ``{"crop": null}`` |

The guide is :func:`gregtec.crops.planner.guide`'s, in its order; the view only draws it. Edits
are Clayton's, from a paired dashboard, as My base's are (D212): client tokens read, never write.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from gregtec import store
from gregtec.api.security import Principal
from gregtec.crops import facts as crop_facts
from gregtec.crops import planner, state
from gregtec.db import access

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.api")

# The PM's five default targets for WO-6.7's acceptance, pending Clayton's own picks (D275):
# an early LV crop, two that need a sub-soil block, one Crop Breeder-only crop and a fertilizer
# crop.
PM_PICKS = (
    "cropsnh:stickyCane",
    "cropsnh:ferrofern",
    "cropsnh:coppon",
    "cropsnh:bauxia",
    "cropsnh:fertilia",
)


def tier_of(conn: sqlite3.Connection) -> int:
    """His GT voltage tier (D64: quests, or his override); 0 before LV or without quest data."""
    from gregtec.db import quests as quest_db
    from gregtec.ingest import quests as bq

    try:
        stage, _, _ = quest_db.effective_tier(conn)
    except sqlite3.Error:
        return 0
    return bq.STAGES.get(stage, 0)


@dataclass
class Current:
    facts: crop_facts.Facts
    have: dict[str, planner.Stats]
    target: state.Target | None
    tier: int
    stock: frozenset[str]
    owned: frozenset[int] = frozenset()

    def guide(self, crop: str | None = None) -> planner.Guide | None:
        """The guide to ``crop``, else to his target; None with neither."""
        cid = crop or (self.target.crop if self.target else None)
        if cid is None:
            return None
        aim = (
            self.target.aim
            if self.target is not None and self.target.crop == cid
            else planner.DEFAULT_TARGET
        )
        return planner.guide(
            self.facts,
            cid,
            self.have,
            self.tier,
            aim=aim,
            stock=self.stock,
            owned=self.owned,
        )


def current(conn: sqlite3.Connection) -> Current:
    """Everything a guide is built from, read from one DB (the game DB holds the core's kv)."""
    return Current(
        crop_facts.load(conn),
        state.have(conn),
        state.target(conn),
        tier_of(conn),
        state.stock_keys(conn),
        _owned(conn),
    )


def _owned(conn: sqlite3.Connection) -> frozenset[int]:
    from gregtec.base import state as base_state

    try:
        return frozenset(base_state.owned_machines(conn))
    except sqlite3.Error:
        return frozenset()


def find(facts: crop_facts.Facts, words: str) -> list[str]:
    """Crop ids matching ``words``: an id, ``crop:<id>`` or a name (any case), else names
    containing them."""
    q = words.strip().removeprefix("crop:").casefold()
    crops = facts.data.crops.values()
    exact = [c.id for c in crops if q in (c.id.casefold(), c.name.casefold())]
    return exact or [c.id for c in crops if q and q in c.name.casefold()]


def payload(conn: sqlite3.Connection, crop: str | None) -> dict[str, Any]:
    cur = current(conn)
    f = cur.facts
    shown = None
    error = None
    if crop:
        found = find(f, crop)
        if not found:
            error = f"no crop matches {crop!r}"
        else:
            shown = found[0]
    g = None
    try:
        g = cur.guide(shown)
    except planner.GuideError as exc:
        error = str(exc)
    return {
        "run": f.run_key,
        "tier": cur.tier,
        "tier_name": planner.TIER_NAMES[min(cur.tier, 9)] if cur.tier else "Steam",
        "have": [
            {
                "crop": cid,
                "ref": f"crop:{cid}",
                "name": f.crop_name(cid),
                "stats": list(s),
            }
            for cid, s in cur.have.items()
        ],
        "target": None
        if cur.target is None
        else {
            "crop": cur.target.crop,
            "ref": f"crop:{cur.target.crop}",
            "name": f.crop_name(cur.target.crop),
            "aim": list(cur.target.aim),
        },
        "crops": [
            {"crop": c.id, "ref": c.ref, "name": c.name, "tier": c.tier}
            for c in sorted(f.data.crops.values(), key=lambda c: (c.name, c.id))
        ],
        "picks": [
            {"crop": p, "ref": f"crop:{p}", "name": f.crop_name(p)}
            for p in PM_PICKS
            if p in f.data.crops
        ],
        "picks_note": "the PM's default picks, pending Clayton's",
        "setup": {
            "water": planner.Setup().water,
            "sky": planner.Setup().sky,
            "liked_biome_tags": planner.Setup().liked_biome_tags,
            # the times' best case: topped up by a Crop Manager My base lists, else by hand
            "crop_manager": bool(
                {m.mte_id for m in f.machines.get("crop_manager", [])} & cur.owned
            ),
        },
        "guide": None if g is None else planner.guide_dict(g),
        "error": error,
    }


def _open_ro(cfg_home: Path) -> sqlite3.Connection:
    db = access.db_path(cfg_home)
    if not db.is_file():
        raise HTTPException(404, "no crop data yet: run gos db build")
    return access.connect(db, readonly=True)


def read(cfg_home: Path, crop: str | None) -> dict[str, Any]:
    conn = _open_ro(cfg_home)
    try:
        return payload(conn, crop)
    except crop_facts.NoCropData as exc:
        raise HTTPException(404, str(exc)) from None
    finally:
        conn.close()


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    trusted: Callable[..., Principal],
    json_body: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]
    Trusted = Annotated[Principal, Depends(trusted)]

    @contextmanager
    def opened() -> Iterator[sqlite3.Connection]:
        conn = store.connect(core.store.path)
        try:
            yield conn
        finally:
            conn.close()

    def edit(
        fn: Callable[[sqlite3.Connection, dict[str, Any]], object],
        checks_crop: bool = True,
    ) -> dict[str, Any]:
        """Run a write; ``checks_crop`` False for one that names no crop to check (removing
        from his list, clearing the target), which works without crop data too."""
        try:
            with opened() as conn:
                known: dict[str, Any] = {}
                if checks_crop:
                    try:
                        known = crop_facts.load(conn).data.crops
                    except crop_facts.NoCropData as exc:
                        raise HTTPException(404, str(exc)) from None
                fn(conn, known)
                try:
                    return payload(conn, None)
                except crop_facts.NoCropData as exc:
                    raise HTTPException(404, f"saved; {exc}") from None
        except state.CropStateError as exc:
            raise HTTPException(400, str(exc)) from None
        except sqlite3.Error as exc:
            raise HTTPException(
                503, f"the DB can't be written now ({type(exc).__name__})"
            ) from None

    async def edited(
        who: Principal,
        what: str,
        fn: Callable[[sqlite3.Connection, dict[str, Any]], object],
        checks_crop: bool = True,
    ) -> dict[str, Any]:
        out = await asyncio.to_thread(edit, fn, checks_crop)
        log.info("crops edited", extra={"what": what, "by": who.name})
        return out

    @app.get("/api/crops")
    async def crops(
        _: Anyone, crop: Annotated[str | None, Query(max_length=120)] = None
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(read, core.cfg.gos_home, crop or None)
        except sqlite3.Error as exc:
            raise HTTPException(
                503, f"the DB can't be read now ({type(exc).__name__})"
            ) from None

    @app.post("/api/crops/have")
    async def set_have(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        return await edited(
            who,
            "have",
            lambda c, k: state.set_have(c, body.get("crop"), body.get("stats"), k),
        )

    @app.delete("/api/crops/have")
    async def remove_have(
        who: Trusted, crop: Annotated[str, Query(min_length=1, max_length=120)]
    ) -> dict[str, Any]:
        return await edited(
            who, "have", lambda c, k: state.remove_have(c, crop), checks_crop=False
        )

    @app.post("/api/crops/target")
    async def set_target(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        if "crop" not in body:
            raise HTTPException(400, "crop is required: a crop id, or null to clear")
        return await edited(
            who,
            "target",
            lambda c, k: state.set_target(c, body.get("crop"), body.get("aim"), k),
            checks_crop=body.get("crop") is not None,
        )
