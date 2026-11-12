"""Spoiler levels (plan 7.4, D26, D72, D268): one global setting, raised only by Clayton's words.

- **L0, nudge** (``[policy] spoiler_default = 0``, the default): for a quest past his frontier,
  direction only: its quest lines, state, prerequisites and the frontier quests that lead to it;
  its name only when the quest book already shows it.
- **L1, partial**, on "partial answer", "a bit more" or "more of a hint": its name and what its
  tasks ask for.
- **L2, full**, on "full answer" or "spoil it": everything, description and rewards too.

A quest he has done or can do now is never a spoiler and is always shown in full. Ore locations
are exempt (D27): no ore tool reads this.

The level is decided in code, never by a model (:func:`allowed`): the global default, raised
only by an explicit spoiler command in Clayton's own request (:func:`asked_level`). A tool argument can ask for less, never
for more, so a model can't spoil what he didn't ask for, and the text of quests, wiki pages or
chat (data, never instructions) can't raise it either: only the request is read.
"""

from __future__ import annotations

import re

NUDGE, PARTIAL, FULL = 0, 1, 2
LEVELS = (NUDGE, PARTIAL, FULL)
NAMES = {NUDGE: "nudge", PARTIAL: "partial", FULL: "full"}

# How the level is read (D268): only an explicit spoiler command raises it. The request is
# normalised like the router's (case, curly apostrophes, spaces) and cut into sentences at
# . ? ! and ;. A sentence raises only when it IS a command: one of the closed set below at its
# head (after the router's filler and "ok", "just", "please"...), with no negation anywhere in
# it, followed by nothing but a short trailer, "about <it or a named quest>", or a colon or
# comma and the question it is for. Any other sentence that merely contains the words ("i
# dont need the full answer", "show me everything i can make", "if i get stuck, spoil it", "he
# said, spoil it", a quotation) keeps the current level: fail safe, when in doubt nothing is
# raised.
_CUT = re.compile(r"[.?!;]")
# Whole-word negations, the apostrophe-less forms listed one by one (a generic "n'?t" would
# match "want" and "went"). A sentence holding one never raises.
_NEGATION = re.compile(
    r"n't\b|\b(?:no|not|never|nor|nothing|none|without|avoid|stop|cannot|dont|doesnt|didnt"
    r"|wont|cant|isnt|arent|wasnt|werent|shouldnt|wouldnt|couldnt|havent|hasnt|hadnt|aint"
    r"|mustnt|neednt)\b"
)
# A sentence that says not to spoil ("don't spoil anything", "no full answer") vetoes the whole
# request, so "don't spoil anything, just spoil it" stays at the default.
_REFUSED = re.compile(
    "(?:"
    + _NEGATION.pattern
    + r")[^.?!;,]*(?:\bspoil|\bfull\b|\beverything\b|\bpartial\b)"
)
# Quoted words are someone else's: a request holding a quotation never raises.
_QUOTE = re.compile(r"[\"\u201c\u201d\u2018]")
# The router's filler and the words that may open a command.
_LEAD = (
    r"(?:(?:ok|okay|fine|yes|yeah|sure|alright|just|please|hey|gos|then|now|go\s+on"
    r"|go\s+ahead(?:\s+and)?|(?:can|could|would)\s+you)[,:]?\s+)*"
)
# What a command is about: it, that, a named quest ("the secret alloy quest", "q:AAQ").
_QUEST_REF = r"(?:(?:the\s+)?(?:[\w'-]+\s+){1,6}quest(?:\s+line)?|q:\S+)"
_ABOUT = r"(?:it|that|this|them|" + _QUEST_REF + r")"
# What may follow a command in its clause: "please", "then", "now", "for me", "about the
# secret alloy quest" (a quest, not "about steel"), or a colon or comma and the question.
_TAIL = (
    r"(?:\s+(?:please|then|now|for\s+me|again))*"
    r"(?:\s+(?:about|on|for|of)\s+" + _ABOUT + r")?"
    r"(?:\s+(?:please|then|now|for\s+me|again))*(?:\s*[:,].*)?$"
)
_WANT = r"(?:(?:give|tell|show)\s+me\s+|i\s+want\s+|i'd\s+like\s+)?"
_FULL = re.compile(
    "^"
    + _LEAD
    + r"(?:spoil\s+(?:me|everything|all|"
    + _ABOUT
    + r")"
    + r"|"
    + _WANT
    + r"(?:the\s+)?full\s+(?:answer|spoilers?)"
    + r"|tell\s+me\s+everything)"
    + _TAIL
)
_PARTIAL = re.compile(
    "^"
    + _LEAD
    + r"(?:"
    + _WANT
    + r"(?:a\s+|the\s+)?partial(?:\s+(?:answer|spoilers?|spoiler|hint))?"
    + r"|(?:give\s+me\s+)?(?:a\s+(?:bit|little)\s+more|more\s+of\s+a\s+hint))"
    + _TAIL
)


def _sentences(request: str) -> list[str]:
    said = " ".join(request.replace("\u2019", "'").casefold().split())
    return [c.strip() for c in _CUT.split(said) if c.strip()]


def asked_level(request: str | None) -> int:
    """The level Clayton's words ask for: :data:`FULL` when a clause is "spoil it/that/me/the
    <quest>", "(give me) the full answer", "full spoilers" or "tell me everything (about X)";
    :data:`PARTIAL` when one is "(give me a) partial answer", "a bit more" or "more of a
    hint"; else :data:`NUDGE`. A command must head its sentence with no negation in it, no
    sentence may refuse a spoiler and nothing may be quoted (D268)."""
    if not request or _QUOTE.search(request):
        return NUDGE
    sentences = _sentences(request)
    if any(_REFUSED.search(c) for c in sentences):
        return NUDGE
    level = NUDGE
    for c in sentences:
        if _NEGATION.search(c):
            continue
        if _FULL.match(c):
            return FULL
        if _PARTIAL.match(c):
            level = PARTIAL
    return level


def check_level(value: object) -> int:
    """A level from settings: 0, 1 or 2 (a bool is not one)."""
    if type(value) is not int or value not in LEVELS:
        raise ValueError(f"a spoiler level is 0, 1 or 2, not {value!r}")
    return value


def allowed(request: str | None, default: int) -> int:
    """The highest level a lane's tools may show for ``request``: the global ``default``
    (``[policy] spoiler_default``), or more when his words ask for more."""
    return max(check_level(default), asked_level(request))
