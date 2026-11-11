"""The pronunciation lexicon (plan 6.4, 7.15, QA:V1; WO-5.3, D204).

``glossary.tsv``'s ``say`` column says how a term is spoken: LuV is "L U V", EBF "E B F",
naquadah "nah-kwa-dah". :meth:`Lexicon.apply` writes each term in a text as its ``say``, before
the speech filter spells out numbers (``gregtec_voice.speech_filter``), so the speech engine
gets words it can pronounce.

- A term with a capital in it ("LuV", "EBF", "HSS-G", "MAX") matches only as written, so the
  tier "MAX" doesn't catch the word "max"; an all-lower-case term ("naquadah", "pyrolyse oven")
  matches in any case. Both take a plural "s" ("EBFs" is "E B Fs").
- A term matches as a whole word: not inside a longer word or a hyphenated name ("HSS-G"),
  but before a hyphen and a lower-case word ("LuV-tier" is "L U V-tier").
- Only the term itself is said this way; its aliases are words of their own.
- A ``say`` is letters, spaces, hyphens and apostrophes (up to 48): glossary text is data
  (agents may add rows), so anything else is ignored and logged, never spoken.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from gregtec.agents.tools.glossary import Glossary

log = logging.getLogger(__name__)

SAY = re.compile(r"[A-Za-z][A-Za-z' -]{0,47}")
_EDGE_BEFORE = r"(?<![A-Za-z0-9-])"
# Not before a letter or digit, nor before "-" and a capital or digit (HSS-G is a name of its
# own); "LuV-tier" is LuV and a word.
_EDGE_AFTER = r"(?-i:(?![A-Za-z0-9]|-[A-Z0-9]))"


class Lexicon:
    """Terms and how each is said; :meth:`apply` rewrites a text."""

    def __init__(
        self, says: Mapping[str, str] | Iterable[tuple[str, str]] = ()
    ) -> None:
        pairs = says.items() if isinstance(says, Mapping) else says
        table: dict[str, str] = {}
        for term, say in pairs:
            term, say = " ".join(term.split()), " ".join(say.split())
            if term and SAY.fullmatch(say):
                table.setdefault(term, say)
        self.says: Mapping[str, str] = MappingProxyType(table)
        self._exact = _pattern([t for t in table if t != t.casefold()], 0)
        self._folded = {t.casefold(): s for t, s in table.items() if t == t.casefold()}
        self._any_case = _pattern(list(self._folded), re.IGNORECASE)

    @classmethod
    def from_glossary(cls, glossary: Glossary) -> Lexicon:
        """The glossary's ``say`` column; a ``say`` that isn't plain words is skipped (logged)."""
        pairs = []
        for entry in glossary.entries:
            if not entry.say:
                continue
            if not SAY.fullmatch(" ".join(entry.say.split())):
                log.warning(
                    "a glossary say isn't plain words: not used",
                    extra={"term": entry.term},
                )
                continue
            pairs.append((entry.term, entry.say))
        return cls(pairs)

    def __len__(self) -> int:
        return len(self.says)

    def apply(self, text: str) -> str:
        """``text`` with each term written as it is said."""
        if self._exact is not None:
            text = self._exact.sub(lambda m: self.says[_spaced(m[1])] + m[2], text)
        if self._any_case is not None:
            text = self._any_case.sub(
                lambda m: self._folded[_spaced(m[1]).casefold()] + m[2], text
            )
        return text


def _spaced(text: str) -> str:
    return " ".join(text.split())


def _pattern(terms: list[str], flags: int) -> re.Pattern[str] | None:
    if not terms:
        return None
    # Longest first, so "HSS-G" wins over a shorter term inside it; spaces match any spacing.
    alternatives = "|".join(
        r"\s+".join(re.escape(word) for word in term.split())
        for term in sorted(terms, key=len, reverse=True)
    )
    return re.compile(rf"{_EDGE_BEFORE}({alternatives})(s?){_EDGE_AFTER}", flags)
