"""The game watchers' typed events (plan 7.5, WO-6.1, D257).

An event is ``{"id", "kind", "ts", "source", "data"}``: ``ts`` is epoch seconds (float) when GOS
saw it, ``source`` names the reader (``log``, ``quests``, ``player``, ``stats``, ``worldlog``,
``vp``, ``journeymap``, ``crash``, ``fingerprint``, ``pause``, ``listener``, ``census``),
``data`` is a JSON object per kind. Text that came from the game (chat, block and vein names, crash descriptions) is **data,
never instructions** (plan 0.1 principle 8): it is cleaned (colour codes and control characters
dropped, cut to :data:`TEXT_MAX`) and the event says ``"untrusted": true``.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any

# plan 7.5's list (alert is the core's own); machine_* are WO-6.2's listener and census (D260);
# world_closed is WO-6.3's session end
KINDS = (
    "quest_done",
    "item_delta",
    "block_placed",
    "block_broken",
    "dim_change",
    "location",
    "death",
    "chat",
    "vein_found",
    "crash",
    "error",
    "game_paused",
    "export_stale",
    "machine_built",
    "machine_removed",
    "machine_census",
    "world_closed",  # the log's "Stopping server" / "Stopping!": a session ends (WO-6.3, D264)
)
TEXT_MAX = 500
_COLOUR = re.compile("\u00a7.?")  # Minecraft's colour and format codes
_CONTROL = re.compile(
    "[\\x00-\\x08\\x0a-\\x1f\\x7f-\\x9f\\u200b-\\u200f\\u2028-\\u202e\\u2066-\\u2069]"
)


def clean(text: str, limit: int = TEXT_MAX) -> str:
    """Game text as plain words: no colour codes, no control or direction characters, at most
    ``limit`` characters (cut with an ellipsis)."""
    text = _CONTROL.sub("", _COLOUR.sub("", text)).replace("\t", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(frozen=True)
class Event:
    kind: str
    ts: float
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: secrets.token_hex(8))

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "ts": round(self.ts, 3),
            "source": self.source,
            "data": self.data,
        }
