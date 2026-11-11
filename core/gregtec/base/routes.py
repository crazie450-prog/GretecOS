"""My base in the core's API (WO-3.8, D212). Every route sits behind the core's Host/Origin guard.

| route                              | who                   | what                                  |
|------------------------------------|-----------------------|---------------------------------------|
| GET    /api/base                   | admin, client, device | tier, owned machines, stock, lines    |
| GET    /api/base/find?q=&kind=     | admin, client, device | items/fluids or machines to add       |
| POST   /api/base/tier              | admin, device         | ``{"stage": "HV"}`` overrides the     |
|                                    |                       | tier, ``{"stage": null}`` clears it   |
| POST   /api/base/machines          | admin, device         | ``{"mte_id", "count"}``: a manual     |
|                                    |                       | count (0 = none)                      |
| DELETE /api/base/machines/{mte_id} | admin, device         | drop the manual count                 |
| POST   /api/base/stock             | admin, device         | ``{"key", "qty"}``: on hand           |
| DELETE /api/base/stock?key=        | admin, device         | drop a stock row                      |

Edits are Clayton's, from a paired dashboard (or his ``gos`` commands' admin token): the client
tokens (voice, Discord, OC) read My base and never change it, and no agent tool writes it (plan
7.0: ``base_set`` is UI-only). Every edit answers with the whole of My base, and a tier change is
pushed to every dashboard with the next state (the header's tier chip and theme).

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from gregtec import store
from gregtec.agents.tools import items
from gregtec.agents.tools.registry import Call, Env, ToolError
from gregtec.api import dashboard
from gregtec.api.security import Principal
from gregtec.base import state
from gregtec.db import access

if TYPE_CHECKING:
    from gregtec.api.app import Core

log = logging.getLogger("gregtec.api")

JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]
FIND_MAX = 10  # candidates /api/base/find returns
KINDS = ("item", "machine")


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    trusted: Callable[..., Principal],
    json_body: JsonBody,
) -> None:
    Anyone = Annotated[Principal, Depends(principal)]
    Trusted = Annotated[Principal, Depends(trusted)]
    path = core.store.path

    @contextmanager
    def opened() -> Iterator[sqlite3.Connection]:
        # Its own connection (WAL, 5 s busy wait, the core's tables made if missing), so an edit's
        # BEGIN IMMEDIATE never meets the Store's shared connection mid-transaction.
        conn = store.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    def read() -> dict[str, Any]:
        with opened() as conn:
            return state.snapshot(conn)

    def edit(fn: Callable[[sqlite3.Connection], object]) -> dict[str, Any]:
        try:
            with opened() as conn:
                fn(conn)
                return state.snapshot(conn)
        except state.BaseError as exc:
            raise HTTPException(400, str(exc)) from None
        except sqlite3.Error as exc:
            raise HTTPException(
                503, f"the DB can't be written now ({type(exc).__name__})"
            ) from None

    async def edited(
        who: Principal, what: str, fn: Callable[[sqlite3.Connection], object]
    ) -> dict[str, Any]:
        out = await asyncio.to_thread(edit, fn)
        log.info("my base edited", extra={"what": what, "by": who.name})
        if what == "tier":  # the header's tier and theme follow at once, not in 5 s
            await asyncio.to_thread(dashboard.board_of(core).refresh_data)
            asyncio.ensure_future(dashboard.push_state(core))
        return out

    @app.get("/api/base")
    async def get_base(_: Anyone) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(read)
        except sqlite3.Error as exc:
            raise HTTPException(
                503, f"the DB can't be read now ({type(exc).__name__})"
            ) from None

    @app.get("/api/base/find")
    async def find(
        _: Anyone,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        kind: Annotated[str, Query()] = "item",
    ) -> dict[str, Any]:
        """Candidates to add: items and fluids, or machines, as the lookup tools resolve them
        (names, glossary aliases, keys)."""
        if kind not in KINDS:
            raise HTTPException(400, f"kind must be one of {', '.join(KINDS)}")
        return await asyncio.to_thread(_find, core, q, kind)

    @app.post("/api/base/tier")
    async def set_tier(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        if "stage" not in body:
            raise HTTPException(
                400, "stage is required: a stage name, or null to clear"
            )
        stage = body["stage"]
        if stage is not None and not isinstance(stage, str):
            raise HTTPException(400, "stage must be a stage name or null")
        return await edited(who, "tier", lambda c: state.set_tier(c, stage))

    @app.post("/api/base/machines")
    async def set_machine(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        mte_id, count = body.get("mte_id"), body.get("count")
        return await edited(
            who,
            "machine",
            lambda c: state.set_machine(c, mte_id, count),
        )

    @app.delete("/api/base/machines/{mte_id}")
    async def clear_machine(mte_id: int, who: Trusted) -> dict[str, Any]:
        return await edited(who, "machine", lambda c: state.clear_machine(c, mte_id))

    @app.post("/api/base/stock")
    async def set_stock(request: Request, who: Trusted) -> dict[str, Any]:
        body = await json_body(request)
        key, qty = body.get("key"), body.get("qty")
        return await edited(
            who,
            "stock",
            lambda c: state.set_stock(c, key, qty),
        )

    @app.delete("/api/base/stock")
    async def remove_stock(
        who: Trusted, key: Annotated[str, Query(min_length=1, max_length=300)]
    ) -> dict[str, Any]:
        return await edited(who, "stock", lambda c: state.remove_stock(c, key))


def _find(core: "Core", q: str, kind: str) -> dict[str, Any]:
    db = access.db_path(core.cfg.gos_home)
    if not db.is_file():
        raise HTTPException(404, "no recipe DB yet: run gos db build")
    env = core.asker.registry.env if core.asker is not None else Env(db=db)
    try:
        conn = access.connect(db, readonly=True)
    except (access.SchemaError, sqlite3.Error) as exc:
        raise HTTPException(503, f"can't open the DB ({type(exc).__name__})") from None
    try:
        if not state.has_table(conn, "machine"):
            raise HTTPException(404, "no recipe DB yet: run gos db build")
        res = items.resolve(
            Call(conn, env), q, "machine" if kind == "machine" else None
        )
        wanted = ("machine",) if kind == "machine" else ("item", "fluid")
        found = [c for c in res.candidates if c.kind in wanted][:FIND_MAX]
        return {
            "q": q,
            "kind": kind,
            "ambiguous": res.ambiguous,
            "candidates": [
                {
                    "kind": c.kind,
                    "key": c.key,
                    "name": c.name,
                    "mod": c.mod,
                    **(
                        {"mte_id": int(c.key.removeprefix("mte:"))}
                        if c.kind == "machine"
                        else {}
                    ),
                }
                for c in found
            ],
        }
    except ToolError as exc:
        raise HTTPException(400, str(exc)) from None
    except sqlite3.Error as exc:
        raise HTTPException(
            503, f"the DB can't be read now ({type(exc).__name__})"
        ) from None
    finally:
        conn.close()
