"""Spoiler levels (plan 7.4, D26, D72, D268): one global setting, raised only by a command.

- **L0, nudge** (``[policy] spoiler_default = 0``, the default): for a quest past his frontier,
  direction only: its quest lines, state, prerequisites and the frontier quests that lead to it;
  its name only when the quest book already shows it.
- **L1, partial**: its name and what its tasks ask for.
- **L2, full**: everything, description and rewards too.

A quest he has done or can do now is never a spoiler and is always shown in full. Ore locations
are exempt (D27): no ore tool reads this.

**Whole-utterance commands only (D268, R1).** The level rises above the global setting only when
the ENTIRE request, after normalisation and a closed set of leading and trailing fillers, is one
of the closed set of commands in :func:`command`. Anything else (another sentence, a question, a
label and colon, a condition, a quotation, a negation) keeps the global level; the reply then
says how to ask (:data:`HOW_TO_ASK`). There is no veto list and no sentence grammar: a request
either is a command, or it is not.

**Scope comes from code (D268, R2).** :func:`command` returns the level and, for "spoil <quest>",
the quest he named. The asker (:mod:`gregtec.agents.ask`) resolves that quest, or for a bare
command takes the quests the previous turn's code-built card showed him, and grants the level to
those quest ids only (:attr:`gregtec.agents.tools.registry.Call.spoil_grant`). A lane's own tool
calls never get a grant, so a model can't raise anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NUDGE, PARTIAL, FULL = 0, 1, 2
LEVELS = (NUDGE, PARTIAL, FULL)
NAMES = {NUDGE: "nudge", PARTIAL: "partial", FULL: "full"}
HOW_TO_ASK = "Say 'spoil it' on its own to see more."

# The fillers a command may come with (R1): the router's own (hey/ok gos, please, can you, could
# you; a trailing "please", "for me", "in gtnh", "in game") and um, uh, so, now, then, pls.
_LEAD = re.compile(
    r"^(?:(?:hey |ok |okay )?gos[,:]?|please|pls|ok|okay|um|uh|so|now|then|can you"
    r"|could you),? "
)
_TRAIL = re.compile(r",? (?:please|pls|now|then|for me|in gtnh|in game)$")
# The closed set of commands (R1), after the fillers. Nothing else raises the level.
FULL_COMMANDS = frozenset(
    {
        "spoil it",
        "spoil that",
        "spoil this",
        "spoil me",
        "spoil it fully",
        "spoil that fully",
        "full answer",
        "the full answer",
        "give me the full answer",
        "i want the full answer",
        "id like the full answer",
        "i'd like the full answer",
        "full spoilers",
    }
)
PARTIAL_COMMANDS = frozenset(
    {
        "partial answer",
        "a partial answer",
        "the partial answer",
        "give me a partial answer",
        "give me the partial answer",
        "i want a partial answer",
        "id like a partial answer",
        "i'd like a partial answer",
        "a bit more",
        "a little more",
        "give me a bit more",
        "a hint",
        "give me a hint",
        "more of a hint",
        "more detail",
        "more details",
    }
)
# "spoil <quest name or q:id> (fully)": the name is words only (letters, digits, ' and -), at
# most eight of them, not opening with a pronoun ("spoil it what does that do" names nothing);
# "the" before and "quest" after it are no part of the name. It raises only when the asker
# finds a quest with exactly that name.
_NAMED = re.compile(
    r"spoil (?:the )?(?P<name>q:[a-z0-9_-]{22}"
    r"|(?!(?:it|that|this|me|them|everything|all|anything)\b)"
    r"[a-z0-9'-]+(?: [a-z0-9'-]+){0,7}?)"
    r"(?: quest)?(?: fully)?"
)
_QUEST_ID = re.compile(r"\bq:([A-Za-z0-9_-]{22})\b")
_FORMAT = re.compile("§.")
_WORD = re.compile(r"[^\W_]+")
# Only for HOW_TO_ASK: a request that mentions a command without being one.
_MENTION = re.compile(r"\bspoil\b|\bfull answer\b|\bpartial answer\b")
_NEGATED = re.compile(r"\b(?:no|not|never|dont|don't|do not)\b")


@dataclass(frozen=True)
class Command:
    """A spoiler command: its ``level`` (:data:`PARTIAL` or :data:`FULL`) and ``quest``, the
    quest he named (``q:<id>`` as he wrote it, else :func:`name_key` of its name); None for a
    bare command, which is about the quests the last answer's card showed him."""

    level: int
    quest: str | None = None


def said(request: str) -> str:
    """The request as the commands are matched: lower case, one space, straight apostrophes,
    no end punctuation, the fillers off."""
    out = " ".join(request.replace("’", "'").split()).casefold().strip(" .!?,")
    while True:
        cut = _TRAIL.sub("", _LEAD.sub("", out)).strip(" .!?,")
        if cut == out:
            return out
        out = cut


def command(request: str | None) -> Command | None:
    """The command the whole request is (R1), or None: then the level stays where it is."""
    if not request:
        return None
    text = said(request)
    if text in FULL_COMMANDS:
        return Command(FULL)
    if text in PARTIAL_COMMANDS:
        return Command(PARTIAL)
    m = _NAMED.fullmatch(text)
    if m is None:
        return None
    name = m["name"]
    if name.startswith("q:"):  # ids are case-sensitive: read it from his own words
        ids = [i for i in _QUEST_ID.findall(request) if f"q:{i.casefold()}" == name]
        return Command(FULL, f"q:{ids[0]}") if len(ids) == 1 else None
    key = name_key(name)
    return Command(FULL, key) if key else None


def mentions(request: str | None) -> bool:
    """Whether a request that is no command still mentions one, and isn't a refusal ("don't
    spoil it"): its reply ends with :data:`HOW_TO_ASK`."""
    if not request or command(request) is not None:
        return False
    text = said(request)
    return bool(_MENTION.search(text)) and not _NEGATED.search(text)


def name_key(text: str | None) -> str:
    """A quest name as a command names it: words only, any case, no formatting codes, no
    leading "the" or trailing "quest"."""
    found = _WORD.findall(_FORMAT.sub("", text or "").casefold())
    if found[:1] == ["the"]:
        found = found[1:]
    if found[-1:] == ["quest"]:
        found = found[:-1]
    return " ".join(found)


def check_level(value: object) -> int:
    """A level from settings: 0, 1 or 2 (a bool is not one)."""
    if type(value) is not int or value not in LEVELS:
        raise ValueError(f"a spoiler level is 0, 1 or 2, not {value!r}")
    return value
