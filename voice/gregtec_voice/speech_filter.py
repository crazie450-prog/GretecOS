"""The speech filter (plan 7.15, QA:V1; WO-5.3, D204): what of an answer is said, and how.

:func:`spoken` turns the core's reply text into what the speaker says, in this order:

1. **Markup out**: code, tables, headings, list marks, emphasis, links (their text stays), tags,
   URLs and symbols nobody says.
2. **Citations and ids out** (plan 0.1 principle 2 puts them in every answer; they are for the
   card, never for the ear): ``r:``, ``q:``, ``mte:``, ``crop:``, ``plan:`` and ``w:...@rev``
   references, item keys (``mod:name@meta#hash``, ``fluid:name``), registry names, dotted and
   snake_case names, hex hashes and UUIDs, "id 1234", and the brackets they leave empty.
3. **D23's length**: at most :data:`MAX_SENTENCES` sentences and :data:`MAX_WORDS` words, counted
   here, before pronunciations and numbers are spelt out (the card has the rest).
4. **The pronunciation lexicon** (``gregtec_voice.lexicon``): LuV is "L U V".
5. **Numbers and units in words**: "1920 EU/t" is "nineteen-twenty E U per tick" (a four-digit
   number is read in pairs, as players say voltages), "2A" "two amps", "1.5M" "one point five
   million", "95%" "ninety-five percent", "3rd" "third", "2-3" "two to three".
6. **Abbreviations spelt**: a word of 2 to 5 capitals the lexicon doesn't know ("GTNH") is said
   letter by letter.
7. **Only words leave**: anything but letters, spaces and ``. , ! ? ' - ;`` is dropped, so no
   digit, colon, slash or underscore (no id) can reach the speech engine whatever slipped
   through the steps above.

The reply is data, never instructions (plan 0.1 principle 8): this module only reshapes it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

MAX_SENTENCES = 2  # D23, plan 8 WO-5.7: <= 2 sentences / 40 words
MAX_WORDS = 40
MIN_CLAUSE_WORDS = 8  # a cut at a comma keeps at least this many words

# --- 1. markup ---------------------------------------------------------------------------------

_FENCE = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_TABLE_ROW = re.compile(r"^\s*\|.*$", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.MULTILINE)
_LINK = re.compile(r"!?\[([^\]\n]*)\]\([^)\n]*\)")
_TAG = re.compile(r"</?[A-Za-z][^<>\n]{0,200}>")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_EMPHASIS = re.compile(r"(?<![\w*])(\*{1,3}|_{1,3}|~~)(?=\S)(.+?)(?<=\S)\1(?![\w*])")

# --- 2. citations and ids ------------------------------------------------------------------------

# Plan 0.1 principle 2's references (as gregtec.agents.fast_lane.REF, plus wiki pages whose
# names hold brackets): their digits are ids, never numbers.
REF = re.compile(
    r"\bw:[^\s@]+@\d+"
    r"|\b(?:r|q|mte|crop|plan|w):[A-Za-z0-9_./@#:+%-]*[A-Za-z0-9_/#%-]"
)
# Item keys (registry name @ meta # NBT hash, fluid:name) and bare registry names.
KEY = re.compile(
    r"\bfluid:[A-Za-z0-9_.]+"
    r"|[A-Za-z0-9_.|-]+:[A-Za-z0-9_.|/-]+(?:@\d+)?(?:#[0-9a-fA-F]{4,64})?"
)
_UUID = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
# A hash: 8+ hex digits with a letter and a digit in them ("3f9a0c2b1e7d").
_HEX = re.compile(r"\b(?=[0-9a-fA-F]*[a-fA-F])(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b")
_SNAKE = re.compile(r"\b\w*_\w*\b")
# A dotted name ("gt.metaitem.01", "GT5U.machines"); "e.g." and "i.e." are kept.
_DOTTED = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+\b")
_ID_WORD = re.compile(r"\b(?:id|ID|Id|meta|damage)\s*[:=#]?\s*\d+\b")
_GONE = "\x00"  # where an id was, until the words that only pointed at it go too
# "see r:... and q:...", "(r:..., r:...)": the ids listed together and the word before them.
_GONE_RUN = re.compile(
    r"(?:\b(?:see|cf|refs?|references?|sources?|cites?|cited|per|from|via)\b[\s:.]*)?"
    r"\x00(?:\s*(?:,|;|/|\band\b|\bor\b)\s*\x00)*",
    re.IGNORECASE,
)
# Brackets that held only ids (and the words listing them): "(see r:..., q:...)", "[]".
_EMPTY_BRACKETS = re.compile(
    r"[(\[{](?:[\s,;:/.\x00]|\b(?:and|or|see|cf|refs?|references?|sources?|cites?|cited"
    r"|ids?|keys?|per|from|via)\b)*[)\]}]",
    re.IGNORECASE,
)

# --- 3. sentences ------------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(]?[A-Z0-9])")
_CLAUSE_END = re.compile(r"[,;:]$")

# --- 5. numbers --------------------------------------------------------------------------------

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = [
    "_",
    "_",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
]
_SCALES = (
    (10**12, "trillion"),
    (10**9, "billion"),
    (10**6, "million"),
    (1000, "thousand"),
)
_ORDINAL = {
    "one": "first",
    "two": "second",
    "three": "third",
    "five": "fifth",
    "eight": "eighth",
    "nine": "ninth",
    "twelve": "twelfth",
}
# "k" is thousand; a capital K after a number is kelvin (GT's heat: "1800K").
_MAGNITUDE = {"k": "thousand", "M": "million", "B": "billion", "G": "billion"}

# Units after a number: (pattern, singular, plural). Longest first; case matters ("mB", "K").
_UNITS: tuple[tuple[str, str, str], ...] = (
    (r"EU/t(?:ick)?", "E U per tick", "E U per tick"),
    (r"EU/s(?:ec)?", "E U per second", "E U per second"),
    (r"EU", "E U", "E U"),
    (r"RF/t", "R F per tick", "R F per tick"),
    (r"RF", "R F", "R F"),
    (r"(?:L|mB)/t", "litre per tick", "litres per tick"),
    (r"(?:L|mB)/s(?:ec)?", "litre per second", "litres per second"),
    (r"(?:L|mB)/min", "litre per minute", "litres per minute"),
    (r"L", "litre", "litres"),
    (r"mB", "millibucket", "millibuckets"),
    (r"A", "amp", "amps"),
    (r"ms", "millisecond", "milliseconds"),
    (r"(?:s|sec|secs)", "second", "seconds"),
    (r"min", "minute", "minutes"),
    (r"h", "hour", "hours"),
    (r"t", "tick", "ticks"),
    (r"%", "percent", "percent"),
    (r"°C", "degree Celsius", "degrees Celsius"),
    (r"K", "kelvin", "kelvin"),
)
_NUMBER = r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_MAG = r"(?P<mag>[kMBG](?![A-Za-z0-9]))?"
_UNIT_ALT = "|".join(f"(?P<u{i}>{pattern})" for i, (pattern, _, _) in enumerate(_UNITS))
_MEASURE = re.compile(
    rf"(?<![\w.]){_NUMBER}{_MAG}(?:\s?(?:{_UNIT_ALT})(?![A-Za-z0-9]))?"
)
_VERSION = re.compile(r"(?<![\w.])\d+(?:\.\d+){2,}(?![\w.])")
_RANGE = re.compile(r"(?<=\d)\s*[-–]\s*(?=\d)")
_NEGATIVE = re.compile(r"(?<![\w.])[-−](?=\d)")
_ORDINAL_NUM = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b")
_TIMES_AFTER = re.compile(
    r"(?<![\w.])(\d+)\s?[x×](?![A-Za-z0-9])(?P<next>\s+[A-Za-z])?"
)
_TIMES_BEFORE = re.compile(r"(?<![\w.])[x×]\s?(?=\d)")
_WIRE = re.compile(r"(?<![\w.])(\d+)x(?=\s+(?:[A-Z][\w-]*\s+){0,3}(?:Wire|Cable)s?\b)")
_FRACTION = re.compile(r"(?<![\w./])(\d+)/(\d+)(?![\w/])")
_DOLLARS = re.compile(r"\$(\d+)(?:\.(\d{2}))?")
_HASH_NUMBER = re.compile(r"#\s?(?=\d)")
_PER_WORD = re.compile(r"(?<=[A-Za-z])/(s|sec|t|tick|min|h)\b")

# --- 6, 7 --------------------------------------------------------------------------------------

_CAPS = re.compile(r"\b([A-Z]{2,5})(s?)\b")
_COLON = re.compile(r"[:—–](?=\s)")  # a pause: said as a comma
_NOT_SAID = re.compile(r"[^A-Za-z\s.,!?'\-;]")
_SPACES = re.compile(r"[ \t]+")


def cardinal(n: int) -> str:
    """``n`` in words, US style ("one hundred five", no "and")."""
    if n < 0:
        return f"minus {cardinal(-n)}"
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {cardinal(rest)}" if rest else "")
    parts = []
    for value, name in _SCALES:
        count, n = divmod(n, value)
        if count:
            parts.append(f"{cardinal(count)} {name}")
    if n:
        parts.append(cardinal(n))
    return " ".join(parts)


def integer(digits: str) -> str:
    """An integer as written ("1920", "32,768") in words. A plain four-digit number from 1000
    to 9999 is read in pairs, as voltages are said ("nineteen-twenty", "twenty forty-eight",
    "fifteen hundred"), unless it ends in 00-09 ("two thousand five") or is whole thousands."""
    n = int(digits.replace(",", ""))
    if len(digits) == 4 and 1000 <= n <= 9999 and n % 1000:
        high, low = divmod(n, 100)
        if low == 0:
            return f"{cardinal(high)} hundred"
        if low >= 10:
            first, second = cardinal(high), cardinal(low)
            joint = " " if "-" in first or "-" in second else "-"
            return f"{first}{joint}{second}"
    return cardinal(n)


def number(text: str) -> str:
    """A number as written ("1920", "1.5", "32,768.25") in words."""
    whole, _, fraction = text.partition(".")
    said = integer(whole)
    if fraction:
        said += " point " + " ".join(_ONES[int(d)] for d in fraction)
    return said


def ordinal(n: int) -> str:
    words = cardinal(n)
    head, sep, last = words.rpartition(" ")
    stem, dash, unit = last.rpartition("-")
    if unit in _ORDINAL:
        unit = _ORDINAL[unit]
    elif unit.endswith("y"):
        unit = unit[:-1] + "ieth"
    else:
        unit += "th"
    return f"{head}{sep}{stem}{dash}{unit}"


def _measure(m: re.Match[str]) -> str:
    num, mag = m.group("num"), m.group("mag")
    said = number(num)
    plural = num.replace(",", "") != "1" or mag is not None
    if mag:
        said += " " + _MAGNITUDE[mag]
    for i, (_, singular, many) in enumerate(_UNITS):
        if m.group(f"u{i}") is not None:
            said += " " + (many if plural else singular)
            break
    return said


def numbers(text: str) -> str:
    """Every number in ``text`` in words, with its unit (step 5 of the module docstring)."""
    text = _VERSION.sub(
        lambda m: " point ".join(number(p) for p in m.group().split(".")), text
    )
    text = _DOLLARS.sub(
        lambda m: (
            f"{cardinal(int(m[1]))} dollar{'' if m[1] == '1' else 's'}"
            + (f" {cardinal(int(m[2]))} cents" if m[2] and int(m[2]) else "")
        ),
        text,
    )
    text = _HASH_NUMBER.sub("number ", text)
    text = _ORDINAL_NUM.sub(lambda m: ordinal(int(m[1])), text)
    text = _WIRE.sub(lambda m: f"{number(m[1])} x", text)
    text = _TIMES_AFTER.sub(
        lambda m: number(m[1]) + (m["next"] if m["next"] else " times"), text
    )
    text = _TIMES_BEFORE.sub("times ", text)
    text = _FRACTION.sub(lambda m: f"{number(m[1])} out of {number(m[2])}", text)
    text = _RANGE.sub(" to ", text)
    text = _NEGATIVE.sub("minus ", text)
    text = _PER_WORD.sub(
        lambda m: (
            " per "
            + {
                "s": "second",
                "sec": "second",
                "t": "tick",
                "tick": "tick",
                "min": "minute",
            }.get(m[1], "hour")
        ),
        text,
    )
    return _MEASURE.sub(_measure, text)


def strip_markup(text: str) -> str:
    text = _FENCE.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _TABLE_ROW.sub(" ", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _TAG.sub(" ", text)
    text = _URL.sub(" ", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = unicodedata.normalize("NFKC", text)
    return (
        text.replace("~", " about ")
        .replace("≈", " about ")
        .replace("&", " and ")
        .replace("→", " to ")
        .replace("->", " to ")
        .replace("—", ", ")
        .replace("…", ".")
        .replace("’", "'")
        .replace("‘", "'")
    )


def strip_ids(text: str) -> str:
    """Citations, keys and ids out (step 2), with the brackets they leave empty."""
    text = text.replace(_GONE, " ")
    for pattern in (REF, KEY, _UUID, _HEX, _SNAKE, _ID_WORD):
        text = pattern.sub(_GONE, text)
    text = _DOTTED.sub(lambda m: m.group() if _abbreviation(m.group()) else _GONE, text)
    for _ in range(2):  # "([r:...])" leaves one pair inside another
        text = _EMPTY_BRACKETS.sub(_GONE, text)
    return _GONE_RUN.sub(" ", text).replace(_GONE, " ")


def _abbreviation(token: str) -> bool:
    """ "e.g", "i.e", "a.m": letters one by one, not a dotted name."""
    return all(len(part) <= 1 for part in token.split("."))


def tidy(text: str) -> str:
    """One line: single spaces, no space before punctuation, no doubled punctuation."""
    text = _SPACES.sub(" ", " ".join(text.split()))
    text = re.sub(r"\s+([.,!?;])", r"\1", text)
    text = re.sub(r"([,;])(?:\s*[,;])+", r"\1", text)
    text = re.sub(r"[,;]\s*([.!?])", r"\1", text)
    text = re.sub(r"([.!?])(?:\s*[.!?,;])+", r"\1", text)
    text = re.sub(r"^(?:[\s,.;!?]|-(?!\d))+", "", text)
    return text.strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def limit(
    text: str, max_sentences: int = MAX_SENTENCES, max_words: int = MAX_WORDS
) -> str:
    """At most ``max_sentences`` sentences and ``max_words`` words (D23): whole sentences when
    they fit, else the first one cut at its last comma (keeping at least
    :data:`MIN_CLAUSE_WORDS` words) or at the word limit."""
    kept: list[str] = []
    words = 0
    for sentence in sentences(text)[:max_sentences]:
        count = len(sentence.split())
        if words + count > max_words:
            if kept:
                break
            return _cut(sentence, max_words)
        kept.append(sentence)
        words += count
    return " ".join(kept)


def _cut(sentence: str, max_words: int) -> str:
    tokens = sentence.split()[:max_words]
    for end in range(len(tokens), MIN_CLAUSE_WORDS - 1, -1):
        if _CLAUSE_END.search(tokens[end - 1]):
            tokens = tokens[:end]
            break
    return " ".join(tokens).rstrip(",;:") + "."


def spell_caps(text: str) -> str:
    """Words of 2 to 5 capitals letter by letter ("GTNH" is "G T N H"; "EBFs" "E B Fs")."""
    return _CAPS.sub(lambda m: " ".join(m[1]) + m[2], text)


def spoken(
    text: str,
    pronounce: Callable[[str], str] | None = None,
    *,
    max_sentences: int = MAX_SENTENCES,
    max_words: int = MAX_WORDS,
) -> str:
    """What the speaker says for ``text`` (the module docstring's seven steps); ``pronounce`` is
    the lexicon (:meth:`gregtec_voice.lexicon.Lexicon.apply`). "" when nothing is left."""
    text = strip_ids(strip_markup(text))
    text = limit(tidy(text), max_sentences, max_words)
    if pronounce is not None:
        text = pronounce(text)
    text = spell_caps(numbers(text))
    text = tidy(_NOT_SAID.sub(" ", _COLON.sub(",", text)))
    if text and text[-1] not in ".!?":
        text += "."
    return "" if not any(ch.isalpha() for ch in text) else text
