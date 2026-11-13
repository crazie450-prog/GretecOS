"""The OC bridge as the core runs it (WO-7.1): the ingest, the telemetry tables and the action desk,
wired together once per core (:func:`build`).

- :class:`gregtec.bridge.ingest.Bridge` with :class:`~gregtec.bridge.telemetry.Telemetry` as its
  sink and the :class:`~gregtec.bridge.actions.ActionDesk` as its command queue: M2's
  ``POST /oc/telemetry`` and M1's mailboxes share it (D217).
- The in-game displays (WO-7.4): :class:`~gregtec.bridge.live.Live` reads the telemetry for the
  Base view and the displays, :class:`~gregtec.bridge.display.Displays` builds each reply's
  ``display`` (the status board, the OCGlasses HUD, chat notices about confirmed actions), and
  :class:`~gregtec.bridge.names.MetaNames` maps GT's meta names to the DB's machines.
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
from gregtec.bridge.display import Displays, quest_context
from gregtec.bridge.ingest import Bridge
from gregtec.bridge.live import Live
from gregtec.bridge.names import MetaNames
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
    live: Live | None = None  # the live base (WO-7.4)
    displays: Displays | None = None  # the in-game displays (WO-7.4)

    def changed(
        self, then: Callable[[Action, str], None] | None
    ) -> Callable[[Action, str], None]:
        """The desk's ``on_change``: the displays' chat notice, then ``then`` (the core's push)."""

        def on_change(action: Action, say: str) -> None:
            if self.displays is not None:
                try:
                    self.displays.on_action(action, say)
                except Exception:
                    log.exception("chat notice not queued", extra={"action": action.id})
            if then is not None:
                then(action, say)

        return on_change

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

    names = MetaNames(cfg.gos_home)

    def resolve(meta: str | None) -> int | None:
        found = names.get(meta)
        return found.mte_id if found is not None else None

    telemetry = Telemetry(store, resolve=resolve)
    item_name = item_namer(access.db_path(cfg.gos_home))
    live = Live(telemetry, names=names, item_name=item_name)
    telemetry.observers.append(live.observe)
    desk = ActionDesk(
        store,
        locate=telemetry.locate,
        name=item_name,
        mark=mark,
        journal=journal_to(cfg.gos_home / "vault"),
    )
    live.paused_by = desk.paused  # GOS's pauses vs GT's own shutdowns (D279)
    telemetry.results = desk.results
    displays = Displays(live, context=quest_context(cfg.gos_home), pending=desk.pending)
    ops = Ops(Bridge(telemetry, desk, displays), telemetry, desk, cfg, live, displays)
    desk.on_change = ops.changed(on_change)
    return ops
