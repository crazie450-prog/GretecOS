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
from collections.abc import Iterable
from dataclasses import dataclass

NUDGE, PARTIAL, FULL = 0, 1, 2
LEVELS = (NUDGE, PARTIAL, FULL)
NAMES = {NUDGE: "nudge", PARTIAL: "partial", FULL: "full"}

# How the level is read (D268): only an explicit spoiler command raises it. The request is
# normalised like the router's (case, curly apostrophes, spaces). A command is one of the closed
# set below (after the router's filler and "ok", "just", "please"...) with nothing but a short
# trailer: "please", "now", "about <it or a named quest>". It raises only when it stands as its
# own sentence: the request is the command alone, or
# - before it: whole sentences ending in . ? ! ; or : ("what comes after it? spoil it"), never a
#   clause cut off by a comma ("if i get stuck, spoil it", "he said, spoil it"); and
# - after it: a separator (. ? ! ; : or ,) and the question it is for, which must open with a
#   question word ("spoil it: what does it give", "spoil it, what comes after the steam age").
#   "tell me everything" takes no question after it ("tell me everything, how do i make
#   steel" is about steel).
# Neither side may hold a conditional, a retraction or reported speech (_VETO: if, unless, when,
# until, once, only, but, except, just kidding, jk, never mind, nah, wait, later, maybe, says,
# said...), and the question after it no negation ("spoil it? no", "spoil it. never mind").
# Anything else that merely contains the words keeps the current level: fail safe, when in
# doubt nothing is raised.
_SENTENCE_END = re.compile(r"[.?!;:]\s*")
# Whole-word negations, the apostrophe-less forms listed one by one (a generic "n'?t" would
# match "want" and "went").
_NEGATION = re.compile(
    r"n't\b|\b(?:no|not|never|nor|nothing|none|without|avoid|stop|cannot|dont|doesnt|didnt"
    r"|wont|cant|isnt|arent|wasnt|werent|shouldnt|wouldnt|couldnt|havent|hasnt|hadnt|aint"
    r"|mustnt|neednt|nah|nope)\b"
)
# A sentence that says not to spoil ("don't spoil anything", "no full answer") vetoes the whole
# request, so "don't spoil anything, just spoil it" stays at the default.
_REFUSED = re.compile(
    "(?:"
    + _NEGATION.pattern
    + r")[^.?!;,]*(?:\bspoil|\bfull\b|\beverything\b|\bpartial\b)"
)
# Conditionals, retractions, hedges and reported speech: next to a command, it isn't one.
_VETO = re.compile(
    r"\b(?:if|unless|when|whenever|until|till|once|only|but|except|besides|kidding|joking|jk"
    r"|nevermind|mind|wait|hold|later|maybe|perhaps|says|said|say|saying|told|tells|typed"
    r"|wrote|asked|asks|means|meant)\b"
)
# What the question after a command opens with.
_QUESTION = re.compile(
    r"(?:what|what's|whats|which|how|where|who|whose|why|is|are|was|were|do|does|did|can"
    r"|could|should|will|tell\s+me|show\s+me|give\s+me|list|explain)\b"
)
# Quoted words are someone else's: a request holding a quotation never raises.
_QUOTE = re.compile(r"[\"\u201c\u201d\u2018]")
# The router's filler and the words that may open a command.
_LEAD = (
    r"(?:(?:ok|okay|fine|yes|yeah|sure|alright|just|please|hey|gos|then|now|go\s+on"
    r"|go\s+ahead(?:\s+and)?|(?:can|could|would)\s+you)[,:]?\s+)*"
)
# A named quest ("the secret alloy quest", "the steam quest line", "q:AAQ"): up to six words
# that name it, none of them a pronoun, article or preposition ("the steel for my next quest"
# names no quest).
_NAME_WORD = (
    r"(?!(?:the|a|an|my|your|his|her|its|our|their|this|that|these|those|for|of|about|on|to"
    r"|in|at|with|from|by|next|last|every|all|some|any)\b)[\w'-]+"
)
_QUEST_REF = (
    r"(?:(?:the\s+)?(?:" + _NAME_WORD + r"\s+){1,6}quest(?:\s+line)?\b|q:[\w-]+)"
)
_ABOUT = r"(?:it|that|this|them|" + _QUEST_REF + r")"
# What may follow a command in its clause: "please", "then", "now", "for me", "about the
# secret alloy quest" (a quest, not "about steel").
_TRAIL = r"(?:\s+(?:please|then|now|for\s+me|again))*"
_TAIL = _TRAIL + r"(?:\s+(?:about|on|for|of)\s+" + _ABOUT + r")?" + _TRAIL
_WANT = r"(?:(?:give|tell|show)\s+me\s+|i\s+want\s+|i'd\s+like\s+)?"
_FULL = re.compile(
    _LEAD
    + r"(?:spoil\s+(?:me|everything|all|"
    + _ABOUT
    + r")|"
    + _WANT
    + r"(?:the\s+)?full\s+(?:answer|spoilers?))"
    + _TAIL
)
_EVERYTHING = re.compile(_LEAD + r"tell\s+me\s+everything" + _TAIL)
_PARTIAL = re.compile(
    _LEAD
    + r"(?:"
    + _WANT
    + r"(?:a\s+|the\s+)?partial(?:\s+(?:answer|spoilers?|spoiler|hint))?"
    + r"|(?:give\s+me\s+)?(?:a\s+(?:bit|little)\s+more|more\s+of\s+a\s+hint))"
    + _TAIL
)
# (command, its level, whether a question may follow it)
_COMMANDS = ((_FULL, FULL, True), (_EVERYTHING, FULL, False), (_PARTIAL, PARTIAL, True))
# The quest a command names, and how a quest's name is compared with it.
_NAMED = re.compile(
    r"\b(?:spoil|about|on|for|of)\s+(?:the\s+)?(?P<name>(?:"
    + _NAME_WORD
    + r"\s+){1,6})quest\b|\bq:(?P<id>[\w-]+)"
)
_FORMAT = re.compile("\u00a7.")
_WORD = re.compile(r"[^\W_]+")
_QUEST_ID = re.compile(r"\bq:([A-Za-z0-9_-]{22})\b")


@dataclass(frozen=True)
class Asked:
    """What Clayton's words ask for: the ``level``, and ``quests``, the quest his command names
    (:func:`quest_keys` of it) when it names one: the raised level is for that quest only
    (D268). None: a command about "it", the quest his question is about."""

    level: int = NUDGE
    quests: frozenset[str] | None = None


def name_key(text: str | None) -> str:
    """A quest or line name as a command names it: words only, any case, no formatting codes,
    no leading "the" or trailing "quest"/"quest line"."""
    found = _WORD.findall(_FORMAT.sub("", text or "").casefold())
    if found[:1] == ["the"]:
        found = found[1:]
    if found[-2:] == ["quest", "line"]:
        found = found[:-2]
    elif found[-1:] == ["quest"]:
        found = found[:-1]
    return " ".join(found)


def quest_keys(qid: str, name: str | None, lines: Iterable[str] = ()) -> frozenset[str]:
    """The keys a command can name quest ``qid`` by: its id, its name, its quest lines."""
    keys = {f"q:{qid}", name_key(name), *(name_key(n) for n in lines)}
    return frozenset(k for k in keys if k)


def _side_ok(text: str, question: bool) -> bool:
    """``text`` after a command: nothing, or a separator and (when ``question``) the question
    it is for, with no veto and no negation in it."""
    rest = text.strip()
    if not rest:
        return True
    if rest[0] not in ".?!;:,":
        return False
    rest = rest.lstrip(".?!;:, ")
    if not rest:
        return True
    return (
        question
        and _QUESTION.match(rest) is not None
        and not _VETO.search(rest)
        and not _NEGATION.search(rest)
    )


def _named(command: str, request: str) -> frozenset[str] | None:
    m = _NAMED.search(command)
    if m is None:
        return None
    if m["id"] is not None:  # ids are case-sensitive: read them from his own words
        ids = [i for i in _QUEST_ID.findall(request) if i.casefold() == m["id"]]
        return frozenset(f"q:{i}" for i in ids)
    return frozenset({name_key(m["name"])})


def _sentences(request: str) -> list[str]:
    said = " ".join(request.replace("\u2019", "'").casefold().split())
    return [c.strip() for c in re.split(r"[.?!;]", said) if c.strip()]


def asked(request: str | None) -> Asked:
    """What his words ask for (D268): :data:`FULL` when the request is "spoil it/that/me/the
    <quest>", "(give me) the full answer", "full spoilers" or "tell me everything (about the
    <quest>)" standing as its own sentence; :data:`PARTIAL` for "(give me a) partial answer",
    "a bit more" or "more of a hint"; else :data:`NUDGE`. No sentence may refuse a spoiler,
    nothing may be quoted, and what stands before or after the command must pass
    :func:`_side_ok` and the veto (the comment above)."""
    if not request or _QUOTE.search(request):
        return Asked()
    if any(_REFUSED.search(c) for c in _sentences(request)):
        return Asked()
    said = " ".join(request.replace("\u2019", "'").casefold().split())
    starts = [0] + [m.end() for m in _SENTENCE_END.finditer(said)]
    best = Asked()
    for start in starts:
        before = said[:start]
        if _VETO.search(before):
            continue
        for pattern, level, question in _COMMANDS:
            m = pattern.match(said, start)
            if m is None or level <= best.level or _NEGATION.search(m[0]):
                continue
            if _side_ok(said[m.end() :], question):
                best = Asked(level, _named(m[0], request))
    return best


def asked_level(request: str | None) -> int:
    """The level his words ask for (:func:`asked`)."""
    return asked(request).level


def check_level(value: object) -> int:
    """A level from settings: 0, 1 or 2 (a bool is not one)."""
    if type(value) is not int or value not in LEVELS:
        raise ValueError(f"a spoiler level is 0, 1 or 2, not {value!r}")
    return value


def allowed(request: str | None, default: int) -> int:
    """The highest level a lane's tools may show for ``request``: the global ``default``
    (``[policy] spoiler_default``), or more when his words ask for more."""
    return max(check_level(default), asked_level(request))
