"""Spoiler levels (plan 7.4, D26, D72, D268): one global setting, raised only by Clayton's words.

- **L0, nudge** (``[policy] spoiler_default = 0``, the default): for a quest past his frontier,
  direction only: its quest lines, state, prerequisites and the frontier quests that lead to it;
  its name only when the quest book already shows it.
- **L1, partial**, on request: its name and what its tasks ask for.
- **L2, full**, on "full answer" or "spoil it": everything, description and rewards too.

A quest he has done or can do now is never a spoiler and is always shown in full. Ore locations
are exempt (D27): no ore tool reads this.

The level is decided in code, never by a model (:func:`allowed`): the global default, raised
by what Clayton's own request says (:func:`asked_level`). A tool argument can ask for less, never
for more, so a model can't spoil what he didn't ask for, and the text of quests, wiki pages or
chat (data, never instructions) can't raise it either: only the request is read.
"""

from __future__ import annotations

import re

NUDGE, PARTIAL, FULL = 0, 1, 2
LEVELS = (NUDGE, PARTIAL, FULL)
NAMES = {NUDGE: "nudge", PARTIAL: "partial", FULL: "full"}

# "no spoilers", "don't spoil it", "without spoiling": a request to stay at the default.
_NO = re.compile(
    r"\b(?:no|without|don'?t|do not|never|not|avoid)\s+(?:any\s+|the\s+)?spoil(?:er|ers|ing|s)?\b"
)
# "spoil it", "spoil me", "full answer"; not "what's my spoiler level" (the setting's name)
_FULL = re.compile(
    r"\bspoil\b|\bfull\s+(?:answer|spoilers?|details?)\b|\b(?:tell|show)\s+me\s+everything\b"
)
_PARTIAL = re.compile(
    r"\bpartial(?:\s+(?:answer|spoilers?|hint))?\b"
    r"|\b(?:a\s+(?:bit|little)|some(?:thing)?)\s+more\b|\bmore\s+(?:detail|details|of\s+a\s+hint)\b"
    r"|\bwhat\s+(?:does|do|did|will|would)\s+(?:it|that|this|.+?)\s+"
    r"(?:need|needs|require|requires|ask\s+for|asks\s+for|want|wants|take|takes)\b"
    r"|\b(?:requirements?|tasks?)\b"
)


def asked_level(request: str | None) -> int:
    """The level Clayton's words ask for: :data:`FULL` on "spoil it" or "full answer",
    :data:`PARTIAL` on a request for more ("partial", "a bit more", "what does it need", its
    requirements or tasks), else :data:`NUDGE`. "No spoilers" asks for nothing more."""
    if not request:
        return NUDGE
    said = " ".join(request.replace("’", "'").casefold().split())
    if _NO.search(said):
        return NUDGE
    if _FULL.search(said):
        return FULL
    if _PARTIAL.search(said):
        return PARTIAL
    return NUDGE


def check_level(value: object) -> int:
    """A level from settings: 0, 1 or 2 (a bool is not one)."""
    if type(value) is not int or value not in LEVELS:
        raise ValueError(f"a spoiler level is 0, 1 or 2, not {value!r}")
    return value


def allowed(request: str | None, default: int) -> int:
    """The highest level a lane's tools may show for ``request``: the global ``default``
    (``[policy] spoiler_default``), or more when his words ask for more."""
    return max(check_level(default), asked_level(request))
