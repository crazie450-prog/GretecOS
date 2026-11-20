"""The router (plan 4.4, D150): which lane a request goes to, by rules alone.

:func:`route` reads the request text (typed, or the STT transcript) and returns a
:class:`Route`:

- **instant**: answered in code with no model call: ``repeat``, ``confirm``/``cancel``, a
  spoiler command, when it is the whole request ("spoil it", "a bit more", "spoil <quest>":
  :func:`gregtec.quests.spoilers.command`, WO-6.4, D26, D268), "what's my tier", "recipe(s) for X" / "X recipe" (a recipe card), "what uses X" (a uses card), and
  saved plans: "my plans" (the list) and "show plan X" / "BOM for X" / ``plan:<id>`` (its BOM
  card, WO-2.8), and "persona <name>" / "which persona" (WO-5.6, plan 7.11: a switch with no
  model call, D206), "next crop step" (WO-6.7, plan 7.17: the crop guide's next step),
  "undo" / "go back to the previous version" after a shell (WO-10.10, D369: the current
  shell design's previous version made current again, in code),
  and "what's this?" (WO-8.1, D286: the item Clayton hovers over or the
  block he looks at, as GregtecLink's client module reports it, answered from the DB). These
  keep working with no API key or credits (plan 0.1 principle 9, 7.16).
- **deep**: design, plans, comparisons, advice, making or refining a building theme (WO-10.6),
  designing a decorative shell (WO-10.7), changing the current shell ("make the roof
  steeper": ``shell_revise``, WO-10.10, D369)
  and anything quantitative over a line ("how many ... per second"), handed to the deep lane (WO-2.4) with a spoken acknowledgement.
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
from gregtec.quests import spoilers

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
PLANS, PLAN = "plans", "plan"  # saved plans (WO-2.8, plan 7.16)
PERSONA = "persona"  # switch or name the persona (WO-5.6, plan 7.11)
WHATS_THIS = "whats_this"  # the hovered item or looked-at block (WO-8.1, plan 7.8)
SPOIL = "spoil"  # the last answer's locked quests, partial or full (WO-6.4, D26, D72)
CROP_STEP = (
    "crop_step"  # the next step of the crop guide he follows (WO-6.7, plan 7.17)
)
# the current shell design (WO-10.10, D369): revise it (deep) or undo its last change (instant)
SHELL_REVISE, SHELL_UNDO = "shell_revise", "shell_undo"
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
MAX_PERSONA_WORDS = 3  # "gt veteran", "the coop buddy one": a pack's name is short
# Words no persona name holds: "persona recipes for steel" isn't a switch (it goes on)
_NOT_A_NAME = frozenset(
    {"for", "of", "about", "with", "from", "in", "on", "is", "are", "do", "does"}
    | {"recipe", "recipes", "uses", "plan", "plans", "tier", "what", "how", "why"}
)

_TIER_NAMES = {t.casefold(): t for t in TIERS}
_TIER = "|".join(t.casefold() for t in TIERS)
_FILLER = re.compile(r"^(?:(?:hey |ok |okay )?gos[,:]? |please |can you |could you )+")
_TRAIL = re.compile(r"(?:,? please| for me| in gtnh| in game)+$")
_ARTICLE = re.compile(r"^(?:a|an|the|some|my|any)\s+")
_COUNT = re.compile(
    r"^\d+\s+(?=[^\d\s])"
)  # "2 steel": the count isn't part of the item
# Words that make no item on their own ("show me the recipes" names nothing)
_NO_ITEM = frozenset(
    {"show", "list", "give", "tell", "me", "us", "the", "all", "a", "an", "some", "my"}
    | {"any", "of", "for"}
)
_AT_TIER = re.compile(rf"\s+(?:at|in|for|on)\s+(?P<tier>{_TIER})(?:\s+tier)?$")
_QUESTION = re.compile(
    r"^(?:what|which|how|why|when|where|who|is|are|should|can|could|do|does|best)\b"
)


def _rx(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(rf"^(?:{p})$") for p in patterns]


INSTANT_RULES: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        PERSONA,  # first: a persona's name could hold any other rule's words
        _rx(
            # STT punctuates: "Persona, ship AI." / "Persona: coop buddy!"
            r"(?:switch |change |set )?(?:to |the )?persona[,:;]? (?:to )?(?P<name>[\w' ,-]+)",
            r"(?:use|switch to|change to),? (?:the )?(?P<name>[\w' -]+?) persona",
            r"(?:what|which) persona(?: is (?:this|on|active|it)| are you)?",
            r"(?:list |show )?(?:me )?(?:the |my )?personas|(?:current )?persona",
        ),
    ),
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
        WHATS_THIS,  # what GregtecLink's client module says Clayton points at (D286)
        _rx(
            r"what(?:'s| is) (?:this|that)(?: (?:item|block|thing|one))?",
            r"what am i (?:looking at|pointing at|holding|hovering(?: over)?)",
            r"what(?:'s| is) (?:this|that) (?:i'm|i am) (?:looking at|pointing at|hovering(?: over)?)",
        ),
    ),
    (
        CROP_STEP,
        _rx(
            r"(?:what(?:'s| is) (?:the |my )?)?next (?:crop|breeding|crop breeding) step",
            r"(?:show |give |tell )?(?:me )?(?:the |my )?(?:next )?(?:crop|breeding) step",
            r"what (?:crop )?should i breed (?:next|now)",
        ),
    ),
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
        PLANS,
        _rx(
            r"(?:show |list |open )?(?:me )?(?:all )?(?:of )?(?:my |the )?(?:saved )?plans",
            r"what plans (?:do i have|are saved)",
        ),
    ),
    (
        PLAN,
        _rx(
            r"(?:show|open|load|view) (?:me )?(?:the |my )?(?:saved )?plan (?P<plan>.+)",
            r"(?:show |open )?(?:me )?(?:the )?(?:bom|bill of materials) (?:for|of) "
            r"(?:the |my )?(?:saved )?(?:plan )?(?P<plan>.+)",
            r"(?P<plan>plan:\S+)",
        ),
    ),
    (
        USES,
        _rx(
            r"(?:what|which recipes?) uses? (?P<item>.+)",
            r"(?:show |list )?(?:me )?(?:the )?uses (?:of|for) (?P<item>.+)",
            # "what uses X" said aloud: speech-to-text often writes the homophone (D183)
            r"what use is (?P<item>.+)",
        ),
    ),
]

DEEP_RULES = [
    re.compile(p)
    for p in (
        # "plan" as a verb or a plan to make; "what does the plan say" is a note lookup
        r"\b(?:design|layout|blueprint|planning)\b",
        r"\bplan (?:a|an|my|the|out|for|how)\b|\b(?:make|write|draw up) (?:me )?a plan\b",
        r"\b(?:line|factory|setup|base|power|build) plans?\b",
        r"\bcompare\b|\bcomparison\b|\bversus\b|\bvs\.?\b",
        r"\boptimi[sz]e\b|\bbest way\b|\bmost efficient\b",
        r"\bworth (?:it|automating|building|making)\b|\bshould i\b",
        r"\bwhat (?:should i do|next|now)\b|\bnext steps?\b|\badvi[cs]e\b|\brecommend",
        # machine counts and amounts for a goal; "how many X does the EBF need" is a lookup
        r"\bhow many\b.*\b(?:machines?|do i need|i need|we need)\b",
        r"\bper (?:second|sec|minute|min|hour)\b|\b(?:/s|/min|/h)\b",
        r"\bratios?\b|\bcalculate\b|\bbudget\b|\boverclock",
        r"\b(?:set up|build|automate)\b.*\b(?:line|setup|factory|plant)\b",
        r"\bpower (?:line|plant|setup|generation)\b",
        # making or refining a building theme (WO-10.6): theme_save is deep-lane only
        r"\b(?:create|make|refine|change|tweak|update|edit|adjust|swap|rework)\b.*"
        + r"\bthemes?\b",
        r"\bnew theme\b",
        r"\bthemes?\b.*\b(?:refine|change|tweak|swap|update|edit|adjust)\b",
        # designing a decorative shell around a line (WO-10.7): build_request is deep-lane only
        # ("how do I make/craft a turtle shell" is a recipe question, not a building)
        r"^(?!.*\bhow (?:do|can|to|would|should)\b(?: (?:i|you|we))? (?:make|craft|get)\b)"
        + r".*\b(?:design|make|build|draw|wrap|put)\b.*\b(?:shells?|buildings?|fa[cç]ades?)\b",
    )
]

# Changing the shell Clayton is working on (WO-10.10, D369): a change word whose object is
# a part of a building ("no windows on the north wall", "add more lights"), a part or the whole
# building with a comparative after it ("make the roof steeper", "the building taller"), or a
# bare comparative ("taller, and no windows ..."); never a question ("which roof is steeper"),
# never when he asks for a new one ("design another shell", "make a shell for plan p-1 with a
# steeper roof", "make a gothic theme with no lights"), and never when the words name some other
# thing to change ("add more lights to my base", "remove the walls in plan x").
_SHELL_PARTS = (
    r"(?:roofs?|walls?|windows?|pillars?|columns?|lights?|lamps?|parapets?|plinth|trim|"
    r"eaves|band|doorways?|fa[cç]ades?)"
)
_SHELL_MORE = (
    r"(?:steeper|flatter|flat|shallower|pitched|peaked|gabled?|taller|higher|shorter|lower|"
    r"bigger|larger|wider|smaller|narrower)"
)
_SHELL_CHANGE_OF = re.compile(
    r"\b(?:more|fewer|less|no|without|remove|drop|add|change|swap|revise|tweak|adjust|raise|"
    + _SHELL_MORE
    + r")\b(?:\W+\w+){0,3}?\W+"
    + _SHELL_PARTS
    + r"\b"
)
_SHELL_MADE_MORE = re.compile(
    r"\b(?:"
    + _SHELL_PARTS
    + r"|shells?|buildings?)\b(?:\W+\w+){0,2}?\W+"
    + _SHELL_MORE
    + r"\b"
)
_SHELL_COMPARATIVE = re.compile(
    r"^(?:(?:a bit|a little|much|slightly|way) )?(?:steeper|flatter|taller|higher|shorter|"
    r"lower|wider|narrower|bigger|smaller)(?:$|[,;]| (?:and|by \d+|please|still|again|too)\b)"
)
_SHELL_NEW = re.compile(
    r"\b(?:new|another|fresh|second|different|separate)\b.*\b(?:shells?|buildings?|designs?|"
    r"fa[cç]ades?)\b|\bfrom scratch\b|\bstart (?:over|again|afresh)\b"
    # a creation verb with a thing to create: a new design (build_request) or theme (theme_save)
    r"|\b(?:design|make|build|create|draw|wrap|put|give)\b(?: me)? (?:a|an|one)\b.*"
    r"\b(?:shells?|buildings?|fa[cç]ades?|themes?|designs?)\b"
    r"|\b(?:create|make|new)\b.*\bthemes?\b"
)
# The words name some other thing: a plan, or a part of his base that is not this shell.
_SHELL_ELSEWHERE = re.compile(
    r"\bplans?\b|\bplan:|\b(?:to|in|on|for|of|around|at) (?:my|the|our|this|that) "
    r"(?:\w+ )?(?:base|factory|line|setup|world|house|room|plant|island|power|ae|network)\b"
)
_SHELL_QUESTION = re.compile(
    r"^(?:what|which|why|who|when|where|is|are|does|do|did|should|will|would|shall|"
    r"how (?:do|does|did|can|to|would|should|many|much|long|tall|big))\b"
)
SHELL_UNDO_RULES = _rx(
    r"undo(?: (?:that|it|this|the last (?:change|revision|version)|the (?:shell|building|roof)"
    r"(?: change)?))?",
    r"go back(?: to)? (?:the |a )?(?:previous|last|earlier|old|former) "
    r"(?:version|one|shell|building|design)",
    r"revert(?: (?:that|it|the shell|the building|the change))?",
    r"(?:put|change) it back",
)


def shell_change(said: str) -> str | None:
    """``undo`` or ``revise`` when the normalised words change the current shell, else None."""
    if any(p.match(said) for p in SHELL_UNDO_RULES):
        return SHELL_UNDO
    if (
        _SHELL_QUESTION.match(said)
        or _SHELL_NEW.search(said)
        or _SHELL_ELSEWHERE.search(said)
    ):
        return None
    if (
        _SHELL_COMPARATIVE.match(said)
        or _SHELL_CHANGE_OF.search(said)
        or _SHELL_MADE_MORE.search(said)
    ):
        return SHELL_REVISE
    return None


FAST_RULES: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        FRONTIER,
        _rx(
            r"(?:what|which) quests? (?:can i do|are (?:open|available)|is next|next)"
            r"(?: now| next)?",  # "... can I do next": found by the fake day one (D337)
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
    words = _COUNT.sub("", _ARTICLE.sub("", words.strip(" ,?")).strip())
    if not words or len(words.split()) > MAX_SUBJECT_WORDS or _QUESTION.match(words):
        return None
    if set(words.split()) <= _NO_ITEM:
        return None
    return words


def route(text: str, *, plans: bool = True, shell: bool = False) -> Route:
    """``plans=False``: a saved-plan command (PLAN) isn't matched, for a question that named no
    saved plan and goes to the AI as before WO-2.8. ``shell``: Clayton has a shell design he is
    working on (WO-10.10): words that change a building revise it (deep lane, ``build_revise``)
    and "undo" makes its previous version current (instant), instead of starting over."""
    said = normalise(text)
    tier = None
    if m := _AT_TIER.search(said):  # "... at LV": the recipe tool's tier_max
        tier, said = _TIER_NAMES[m["tier"]], said[: m.start()]
    if spoilers.command(text) is not None:  # the whole request is the command (D268)
        return Route(INSTANT, SPOIL, text)
    if shell and (change := shell_change(said)) is not None:
        return Route(INSTANT if change == SHELL_UNDO else DEEP, change, text)
    deep = any(d.search(said) for d in DEEP_RULES)
    for command, patterns in INSTANT_RULES:
        for p in patterns:
            if m := p.match(said):
                if (
                    command == PLAN
                ):  # a saved plan's name or id; deep words still outrank it
                    words = m["plan"].strip()
                    if (
                        plans
                        and not deep
                        and 0 < len(words.split()) <= MAX_SUBJECT_WORDS
                    ):
                        return Route(INSTANT, command, text, words)
                    continue
                if command == PERSONA:  # the name, if one was said
                    name = " ".join(
                        (m.groupdict().get("name") or "").replace(",", " ").split()
                    )
                    if name and (
                        deep
                        or len(name.split()) > MAX_PERSONA_WORDS
                        or _NOT_A_NAME & set(name.split())
                    ):
                        break  # not a pack's name: the other rules place it
                    return Route(INSTANT, command, text, name or None)
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
