"""The game-state snapshot that goes with a frame (plan 7.7, D289).

What the core knows about the game when the screen key is pressed, as data blocks for the
screen lane's user turn:

- the game link (WO-8.1): which instance is connected, its dimension's name, whether it is
  paused (no coordinates: no tool backs them, so the lane couldn't state them anyway);
- what Clayton points at, asked of the game as "what's this?" is (D286), answered from the DB
  with its citations (:mod:`gregtec.gamelink.whatsthis`), so a hovered item's name and key are
  the DB's, not read off the pixels;
- his tier, the head of ``quest_frontier`` (as pre-retrieval gives the fast lane).

Each part is quoted as data with its source, and only the DB-backed parts (the "what's this?"
answer and the tier) count as sources for the number check.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from gregtec.agents import prefetch
from gregtec.agents.tools.registry import Registry
from gregtec.gamelink import whatsthis

log = logging.getLogger(__name__)


@dataclass
class Snapshot:
    game: str  # one line: connected or not, instance, dimension, paused
    target: whatsthis.Answer | None = None  # what he points at, from the DB
    tier: str | None = None
    lines: list[str] = field(default_factory=list)  # the game lines, for the card

    def prefetch(self, request: str) -> prefetch.Prefetch:
        """The snapshot as the lane's pre-retrieved data: the "what's this?" answer is a
        tool-made block (its text holds only DB numbers and ids), the tier its line."""
        blocks = []
        if self.target is not None and self.target.found:
            blocks.append(prefetch.Block("whats_this", {}, _as_tsv(self.target), False))
        return prefetch.Prefetch(blocks=blocks, tier=self.tier, request=request)

    def context(self) -> str:
        """The game line as a ``<context>`` block (data, not a source of numbers)."""
        return f"<context>\nGame state: {prefetch._safe(self.game)}\n</context>"


def _as_tsv(answer: whatsthis.Answer) -> str:
    rows = [
        "# pointing_at: what the game reports under the crosshair or cursor, from the DB",
        f"# key: {answer.key or ''}",
        f"# name: {answer.name or ''}",
        f"# refs: {' '.join(answer.refs)}",
        "said",
        answer.text.replace("\t", " ").replace("\n", " "),
    ]
    return "\n".join(rows) + "\n"


def game_line(state: dict[str, Any] | None) -> tuple[str, list[str]]:
    """The game link's state (:meth:`GameLink.state`) as one line, and its parts."""
    if not state or not state.get("links"):
        return "the game isn't connected to GOS (no GregtecLink client link)", [
            "not connected"
        ]
    links = state["links"]
    status = state.get("status") or {}
    newest = max(links, key=lambda k: k.get("last_seen") or 0)
    parts = [f"connected ({newest.get('binding') or 'unknown instance'})"]
    dim = status.get("dim_name")
    if isinstance(dim, str) and dim.strip():
        parts.append(f"dimension {dim.strip()[:64]}")
    if status.get("paused") is True:
        parts.append("paused")
    return "; ".join(parts), parts


def take(
    registry: Registry,
    *,
    state: Callable[[], dict[str, Any]] | None,
    pointer: whatsthis.Pointer | None,
) -> Snapshot:
    """The snapshot now. Any part that fails is left out (logged): a frame is still read."""
    try:
        game, lines = game_line(state() if state is not None else None)
    except Exception:  # the game link's state is best effort here
        log.exception("game state not read")
        game, lines = "unknown (the game link's state didn't read)", ["unknown"]
    target = None
    if pointer is not None:
        try:
            found, linked = pointer.current_target()
            if found is not None:
                target = whatsthis.answer(registry, found, linked=linked)
        except Exception:
            log.exception("what's this? not read for the screen")
    try:
        tier = prefetch.tier_line(registry)
    except Exception:
        log.exception("tier not read for the screen")
        tier = None
    return Snapshot(game, target, tier, lines)
