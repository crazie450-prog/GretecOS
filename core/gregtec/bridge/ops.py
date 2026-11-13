"""The OC bridge as the core runs it (WO-7.1): the ingest, the telemetry tables and the action desk,
wired together once per core (:func:`build`).

- :class:`gregtec.bridge.ingest.Bridge` with :class:`~gregtec.bridge.telemetry.Telemetry` as its
  sink and the :class:`~gregtec.bridge.actions.ActionDesk` as its command queue: M2's
  ``POST /oc/telemetry`` and M1's mailboxes share it (D217).
- The desk reads item names from the game DB (read-only, by registry name and damage with no
  NBT), writes waypoints through :mod:`gregtec.bridge.waypoint`, and journals each change to the
  day's log through :class:`gregtec.vault.io.VaultIO` (the note grammar's ``key: value`` line).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gregtec.bridge import waypoint
from gregtec.bridge.actions import Action, ActionDesk
from gregtec.bridge.ingest import Bridge
from gregtec.bridge.telemetry import Telemetry
from gregtec.config import Config
from gregtec.db import access
from gregtec.store import Store
from gregtec.vault.io import VaultError, VaultIO

log = logging.getLogger("gregtec.bridge")


@dataclass
class Ops:
    bridge: Bridge
    telemetry: Telemetry
    desk: ActionDesk
    cfg: Config | None = None  # where a vein's waypoint goes (WO-6.6, ore_waypoint)

    def tick(self) -> None:
        """Every second from the core: expiries, timeouts, staleness."""
        self.desk.tick()
        self.telemetry.sweep()


def item_namer(db: Path) -> Callable[[str, int], str | None]:
    """An item's English name from the game DB, by registry name and damage with no NBT."""

    def name(registry_name: str, damage: int) -> str | None:
        if not db.is_file():
            return None
        try:
            conn = access.connect(db, readonly=True)
        except (access.SchemaError, sqlite3.Error):
            return None
        try:
            row = conn.execute(
                "SELECT name_en FROM item WHERE registry_name = ? AND meta = ? AND nbt_hash = ''",
                (registry_name, damage),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        return str(row[0]) if row and row[0] else None

    return name


def journal_to(vault: Path) -> Callable[[list[str]], None]:
    """Append lines to today's journal (``log/YYYY-MM-DD.md``), if the vault exists."""
    io = VaultIO(vault)

    def write(lines: list[str]) -> None:
        if not vault.is_dir():
            return
        rel = f"log/{io.today().isoformat()}.md"
        try:
            io.append(rel, "\n".join(lines))
        except VaultError as exc:
            log.warning("action not journalled", extra={"why": str(exc)[:200]})

    return write


def build(
    cfg: Config,
    store: Store,
    *,
    on_change: Callable[[Action, str], None] | None = None,
) -> Ops:
    """The core's OC bridge over ``store`` (the core's own DB)."""

    def mark(target: str, args: Mapping[str, Any]) -> str:
        return waypoint.write(waypoint.place(cfg, target), args)

    telemetry = Telemetry(store)
    desk = ActionDesk(
        store,
        locate=telemetry.locate,
        name=item_namer(access.db_path(cfg.gos_home)),
        mark=mark,
        journal=journal_to(cfg.gos_home / "vault"),
        on_change=on_change,
    )
    telemetry.results = desk.results
    return Ops(Bridge(telemetry, desk), telemetry, desk, cfg)
