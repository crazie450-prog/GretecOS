"""The router (plan 4.4, D150): which lane a request goes to, by rules alone.

:func:`route` reads the request text (typed, or the STT transcript) and returns a
:class:`Route`:

- **instant**: answered in code with no model call: ``repeat``, ``confirm``/``cancel``, "what's
  my tier", "recipe(s) for X" / "X recipe" (a recipe card) and "what uses X" (a uses card).
  These keep working with no API key or credits (plan 0.1 principle 9).
- **deep**: design, plans, comparisons, advice and anything quantitative over a line ("how many
  ... per second"), handed to the deep lane (WO-2.4) with a spoken acknowledgement.
- **fast**: everything else, with an ``intent`` and a ``subject`` when the words say what is
  asked about, so pre-retrieval (:mod:`gregtec.agents.prefetch`) can resolve it first.

There is no classifier call: a request no rule places goes to the fast lane, whose ``handoff``
tool passes it on when it turns out to be deep-lane work, so an unclear request costs no extra
round trip (D150). The rules only read the words; what an item *is* is decided by
``item_resolve``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gregtec.agents.tools.recipes import TIERS

INSTANT, FAST, DEEP = "instant", "fast", "deep"
# instant commands
REPEAT, CONFIRM, CANCEL, TIER, RECIPES, USES = (
    "repeat",
    "confirm",
    "cancel",
    "tier",
    "recipes",
    "uses",
)
# fast-lane intents (what pre-retrieval fetches)
RECIPE, ITEM, QUEST_ITEM, QUESTS, FRONTIER, GENERAL = (
    "recipe",
    "item",
    "quest_item",
    "quests",
    "frontier",
    "general",
)
MAX_SUBJECT_WORDS = 8

_TIER_NAMES = {t.casefold(): t for t in TIERS}
_TIER = "|".join(t.casefold() for t in TIERS)
_FILLER = re.compile(r"^(?:(?:hey |ok |okay )?gos[,:]? |please |can you |could you )+")
_TRAIL = re.compile(r"(?:,? please| for me| in gtnh| in game| again)+$")
_ARTICLE = re.compile(r"^(?:a|an|the|some|my|any)\s+")
_AT_TIER = re.compile(rf"\s+(?:at|in|for|on)\s+(?P<tier>{_TIER})(?:\s+tier)?$")
_QUESTION = re.compile(
    r"^(?:what|which|how|why|when|where|who|is|are|should|can|could|do|does|best)\b"
)


def _rx(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(rf"^(?:{p})$") for p in patterns]


INSTANT_RULES: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        REPEAT,
        _rx(
            r"repeat(?: that| it| the last answer)?",
            r"say (?:that|it) again",
            r"what did you (?:just )?say",
        ),
    ),
    (CONFIRM, _rx(r"(?:yes,? )?confirm(?: it)?")),
    (CANCEL, _rx(r"cancel(?: it| that)?", r"never ?mind")),
    (
        TIER,
        _rx(
            r"what(?:'s| is) my (?:current )?(?:gt )?tier",
            r"what tier am i(?: on| in| at)?(?: now)?",
            r"(?:my|current) tier",
        ),
    ),
    (
        RECIPES,
        _rx(
            r"(?:show |list )?(?:me )?(?:the |all )?recipes? (?:for|of) (?P<item>.+)",
            r"(?P<item>[^?]+?) recipes?",
        ),
    ),
    (
        USES,
        _rx(
            r"(?:what|which recipes?) uses? (?P<item>.+)",
            r"(?:show |list )?(?:me )?(?:the )?uses (?:of|for) (?P<item>.+)",
        ),
    ),
]

DEEP_RULES = [
    re.compile(p)
    for p in (
        r"\b(?:design|plan|layout|blueprint)\b",
        r"\bcompare\b|\bcomparison\b|\bversus\b|\bvs\.?\b",
        r"\boptimi[sz]e\b|\bbest way\b|\bmost efficient\b",
        r"\bworth (?:it|automating|building|making)\b|\bshould i\b",
        r"\bwhat (?:should i do|next|now)\b|\bnext steps?\b|\badvi[cs]e\b|\brecommend",
        r"\bhow many\b.*\b(?:machines?|do i need|need)\b",
        r"\bper (?:second|sec|minute|min|hour)\b|\b(?:/s|/min|/h)\b",
        r"\bratios?\b|\bcalculate\b|\bbudget\b|\boverclock",
        r"\b(?:set up|build|automate)\b.*\b(?:line|setup|factory|plant)\b",
        r"\bpower (?:line|plant|setup|generation)\b",
    )
]

FAST_RULES: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        FRONTIER,
        _rx(
            r"(?:what|which) quests? (?:can i do|are (?:open|available)|is next|next)"
            r"(?: now)?",
            r"(?:what are |show |list )?(?:me )?(?:my )?(?:next|open|available) quests?",
            r"quest frontier|what quests? (?:now|next)",
        ),
    ),
    (
        USES,
        _rx(
            r"what (?:can|could) (?:i|you|we) (?:make|do|craft) with (?P<item>.+)",
            r"what (?:is|are) (?P<item>.+?) (?:used for|good for)",
            r"where (?:is|are) (?P<item>.+?) used",
        ),
    ),
    (
        QUEST_ITEM,
        _rx(
            r"(?:what|which) quests? (?:needs?|asks? for|wants?|uses?|requires?|gives?|"
            r"rewards?|takes?) (?P<item>.+)",
            r"quests? (?:for|with|that (?:need|needs|use|uses|give|gives)) (?P<item>.+)",
        ),
    ),
    (
        QUESTS,
        _rx(
            r"(?:tell me about |what is |what's |find |show )?(?:the )?quests? "
            r"(?:about|called|named) (?P<text>.+)",
            r"(?:tell me about|what is|what's) the (?P<text>.+) quest",
        ),
    ),
    (
        RECIPE,
        _rx(
            r"how (?:do|can|would|should) (?:i|you|we) (?:make|craft|get|produce|create|"
            r"cook|smelt) (?P<item>.+)",
            r"how (?:to|is one supposed to) (?:make|craft|get|produce|create) (?P<item>.+)",
            r"how (?:is|are) (?P<item>.+?) (?:made|crafted|produced|created)",
            r"what (?:makes|produces|crafts|creates) (?P<item>.+)",
            r"what machines? (?:makes?|produces?|crafts?) (?P<item>.+)",
            r"where (?:do|can) i get (?P<item>.+)",
            r"(?:what's|what is) the recipe (?:for|of) (?P<item>.+)",
        ),
    ),
    (
        ITEM,
        _rx(
            r"what (?:does|do) (?P<item>.+?) do",
            r"what (?:is|are) (?P<item>.+?) for",
            r"(?:tell me about|info (?:on|about)|what is|what's|what are) (?P<item>.+)",
        ),
    ),
]


@dataclass(frozen=True)
class Route:
    kind: str  # instant, fast or deep
    intent: str  # an instant command, a fast-lane intent, or "handoff"
    text: str  # the request as given
    subject: str | None = None  # the item or quest words the request names
    tier: str | None = None  # "at LV": the recipe tool's tier_max


def normalise(text: str) -> str:
    """Lower case, one space, no wake words, no end punctuation."""
    out = " ".join(text.replace("’", "'").split()).casefold()
    out = out.strip(" .!?")
    out = _FILLER.sub("", out)
    return _TRAIL.sub("", out).strip(" .!?,")


def subject_of(words: str) -> str | None:
    """The item words, articles off; None when they don't look like a short noun phrase."""
    words = _ARTICLE.sub("", words.strip(" ,?")).strip()
    if not words or len(words.split()) > MAX_SUBJECT_WORDS or _QUESTION.match(words):
        return None
    return words


def route(text: str) -> Route:
    said = normalise(text)
    tier = None
    if m := _AT_TIER.search(said):  # "... at LV": the recipe tool's tier_max
        tier, said = _TIER_NAMES[m["tier"]], said[: m.start()]
    deep = any(d.search(said) for d in DEEP_RULES)
    for command, patterns in INSTANT_RULES:
        for p in patterns:
            if m := p.match(said):
                if command not in (RECIPES, USES):
                    return Route(INSTANT, command, text)
                subject = subject_of(m["item"])
                if subject is not None and not deep:
                    return Route(INSTANT, command, text, subject, tier)
    if deep:
        return Route(DEEP, "handoff", text)
    for intent, patterns in FAST_RULES:
        for p in patterns:
            if m := p.match(said):
                groups = m.groupdict()
                if intent == FRONTIER:
                    return Route(FAST, intent, text)
                if intent == QUESTS:
                    return Route(FAST, intent, text, groups["text"].strip())
                subject = subject_of(groups["item"])
                if subject is not None:
                    return Route(FAST, intent, text, subject, tier)
    return Route(FAST, GENERAL, text, tier=tier)
