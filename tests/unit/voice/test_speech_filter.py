"""WO-5.3: the speech filter and the pronunciation lexicon (plan 7.15, QA:V1, D204)."""

from __future__ import annotations

import random
import re

import pytest

from gregtec.agents import fast_lane
from gregtec.agents.tools.glossary import Entry, Glossary
from gregtec_voice import speech_filter as sf
from gregtec_voice.lexicon import Lexicon
from gregtec_voice.tts_bench import ANSWERS, load_phrases

LEXICON = Lexicon.from_glossary(Glossary.find(None))
SAID = re.compile(r"[A-Za-z .,!?'\-;]*")  # all a spoken text may hold


def spoken(text: str) -> str:
    return sf.spoken(text, LEXICON.apply)


# --- the acceptance rows: 30 GTNH terms, 10 number and unit phrases ---------------------------


PHRASES = load_phrases()


def test_the_check_set_has_30_terms_and_10_number_phrases() -> None:
    kinds = [p.kind for p in PHRASES]
    assert kinds.count("term") == 30 and kinds.count("number") == 10
    assert len({p.id for p in PHRASES}) == 40


@pytest.mark.parametrize("phrase", PHRASES, ids=[p.id for p in PHRASES])
def test_each_check_phrase_is_said_as_written(phrase: object) -> None:
    assert spoken(phrase.text) == phrase.say  # type: ignore[attr-defined]


def test_the_plans_examples() -> None:
    assert "nineteen-twenty E U per tick" in spoken("It uses 1920 EU/t.")
    assert spoken("LuV") == "L U V."
    assert spoken("EBF") == "E B F."
    assert spoken("naquadah") == "nah-kwa-dah."


# --- numbers ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "zero"),
        (7, "seven"),
        (15, "fifteen"),
        (40, "forty"),
        (99, "ninety-nine"),
        (100, "one hundred"),
        (105, "one hundred five"),
        (999, "nine hundred ninety-nine"),
        (32768, "thirty-two thousand seven hundred sixty-eight"),
        (131072, "one hundred thirty-one thousand seventy-two"),
        (2_000_000, "two million"),
        (
            2_147_483_647,
            (
                "two billion one hundred forty-seven million four hundred "
                "eighty-three thousand six hundred forty-seven"
            ),
        ),
        (-5, "minus five"),
    ],
)
def test_cardinal(n: int, words: str) -> None:
    assert sf.cardinal(n) == words


@pytest.mark.parametrize(
    ("digits", "words"),
    [
        ("1920", "nineteen-twenty"),
        ("2048", "twenty forty-eight"),
        ("8192", "eighty-one ninety-two"),
        ("1024", "ten twenty-four"),
        ("1500", "fifteen hundred"),
        ("1800", "eighteen hundred"),
        ("2000", "two thousand"),
        ("2005", "two thousand five"),
        ("1,920", "one thousand nine hundred twenty"),
        ("512", "five hundred twelve"),
        ("10000", "ten thousand"),
    ],
)
def test_integers_four_digits_in_pairs(digits: str, words: str) -> None:
    assert sf.integer(digits) == words


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (1, "first"),
        (2, "second"),
        (3, "third"),
        (5, "fifth"),
        (8, "eighth"),
        (9, "ninth"),
        (12, "twelfth"),
        (20, "twentieth"),
        (21, "twenty-first"),
        (100, "one hundredth"),
        (42, "forty-second"),
    ],
)
def test_ordinals(n: int, words: str) -> None:
    assert sf.ordinal(n) == words


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("1 A", "one amp."),
        ("4A", "four amps."),
        ("1 L", "one litre."),
        ("1000L/s", "one thousand litres per second."),
        ("16 mB", "sixteen millibuckets."),
        ("1 s", "one second."),
        ("2 min", "two minutes."),
        ("3 h", "three hours."),
        ("20 t", "twenty ticks."),
        ("250 ms", "two hundred fifty milliseconds."),
        ("32k EU/t", "thirty-two thousand E U per tick."),
        ("1.5M EU", "one point five million E U."),
        ("2B EU", "two billion E U."),
        ("0.25", "zero point two five."),
        ("2-3 machines", "two to three machines."),
        ("a 1/4 chance", "a one out of four chance."),
        ("4x EBF", "four E B F."),
        ("overclocks 2x.", "overclocks two times."),
        ("x4 speed", "times four speed."),
        ("16x Copper Wire", "sixteen x Copper Wire."),
        ("about $0.12", "about zero dollars twelve cents."),
        ("pack 2.9.0", "pack two point nine point zero."),
        ("option #2", "option number two."),
        ("1800K", "eighteen hundred kelvin."),
        ("1000 °C", "one thousand degrees Celsius."),
        ("RF/t", "R F per tick."),
        ("items/s", "items per second."),
        ("-5", "minus five."),
        ("~5 s", "about five seconds."),
        ("The ratio is 3:1.", "The ratio is three to one."),
        ("At 12:30 it starts.", "At twelve thirty it starts."),
        ("At 9:05 or 10:00.", "At nine oh five or ten o'clock."),
        ("IC2 cables and a T1 rocket", "I C two cables and a T one rocket."),
        ("Q12 and Mk2.", "Q twelve and Mk two."),
        ("10K EU", "ten thousand E U."),
        ("10K EU/t", "ten thousand E U per tick."),
        ("Use 4 A circuits", "Use four A circuits."),
        ("2 A of LV", "two amps of L V."),
        ("Done in 5m.", "Done in five minutes."),
        ("5 m long", "five m long."),
    ],
)
def test_numbers_and_units(text: str, said: str) -> None:
    assert spoken(text) == said


def test_a_word_after_a_number_is_not_a_unit() -> None:
    assert spoken("3 Kanthal coils and 2 Aluminium plates") == (
        "three kan-thal coils and two Aluminium plates."
    )
    assert spoken("16 MV motors") == "sixteen M V motors."


# --- markup, citations and ids ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("**Steel** is _made_ in an `EBF`.", "Steel is made in an."),
        ("# Steel\n- needs Iron\n- needs Oxygen", "Steel needs Iron needs Oxygen."),
        ("See [the wiki](https://example.org/x) now.", "See the wiki now."),
        ("Steel <b>bold</b> at https://x.y/z ok", "Steel bold at ok."),
        ("| a | b |\n|---|---|\nSteel.", "Steel."),
        ("Steel ✅ done.", "Steel done."),
        ("A & B -> C", "A and B to C."),
    ],
)
def test_markup_goes(text: str, said: str) -> None:
    assert spoken(text) == said


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("Steel (r:gt.recipe.blastfurnace/3f9a0c2b1e7d).", "Steel."),
        ("Steel [r:abc/123456789abc] is next.", "Steel is next."),
        ("Steel, see r:a/1 and q:2.", "Steel."),
        ("Per r:abc/123 it's done.", "it's done."),
        ("Steel (see r:x/1, q:7; mte:1000) is fine.", "Steel is fine."),
        ("Steel Ingot (gregtech:gt.metaitem.01@11305) is it.", "Steel Ingot is it."),
        ("Use minecraft:iron_ingot here.", "Use here."),
        ("Water (fluid:water) flows.", "Water flows."),
        ("The plan plan:p-17 is saved.", "The plan is saved."),
        ("Read w:Electric_Blast_Furnace_(EBF)@12345 later.", "Read later."),
        ("Hash 3f9a0c2b1e7d4a5b here.", "Hash here."),
        ("Run 123e4567-e89b-12d3-a456-426614174000 now.", "Run now."),
        ("It is gt.metaitem.01 there.", "It is there."),
        ("The id 32600 and meta: 5 go.", "The go."),
        ("Crop crop:stickreed is ready.", "Crop is ready."),
        ("Use gregtech:gt.blockmachines:1022 here.", "Use here."),
        ("Use minecraft:wool:14 here.", "Use here."),
        ("e.g. Steel.", "e.g. Steel."),
    ],
)
def test_citations_and_ids_go(text: str, said: str) -> None:
    assert spoken(text) == said


# An id's digits are all 7s and its hex has a 7 in it, and the answers' own words and numbers
# have no 7, so any id digit that is spoken shows as "seven" (or "seventy", "seventeen").
SENTINEL = re.compile(r"seven", re.IGNORECASE)


def _ids(rng: random.Random) -> str:
    hex_chars = [rng.choice("7abcdef") for _ in range(10)] + ["7", "a"]
    rng.shuffle(hex_chars)
    hexes = "".join(hex_chars)
    digits = "7" * rng.randint(1, 6)
    word = rng.choice(
        ["blastfurnace", "assembler", "Steel_Ingot", "gt.metaitem.01", "x"]
    )
    return rng.choice(
        [
            f"r:{word}/{hexes}",
            f"r:gt.recipe.{word}/{hexes}",
            f"q:{digits}",
            f"mte:{digits}",
            f"crop:{word}",
            f"plan:{word}-{digits}",
            f"w:{word}@{digits}",
            f"gregtech:gt.metaitem.01@{digits}",
            f"gregtech:gt.blockmachines@{digits}#{hexes}",
            f"gregtech:gt.blockmachines:{digits}",
            f"minecraft:{word}:{digits}",
            f"fluid:{word}",
            f"minecraft:{word}",
            hexes,
            f"id {digits}",
        ]
    )


def test_property_no_citation_or_id_is_ever_spoken() -> None:
    """Over generated answers: every id is gone, and only words reach the engine."""
    rng = random.Random(53)
    words = [
        "Steel",
        "Ingot",
        "needs",
        "the",
        "EBF",
        "at",
        "HV",
        "and",
        "LuV",
        "naquadah",
        "coils",
        "for",
        "the",
        "line",
    ]
    numbers = ["1920 EU/t", "2A", "144 L", "20 s", "95%", "1.5M", "3rd", "16x", "2-3"]
    wraps = ["({})", "[{}]", "(see {})", "{}", "({}, {})", "per {}", "({} and {})"]
    for _ in range(2000):
        parts: list[str] = []
        ids: list[str] = []
        for _ in range(rng.randint(3, 25)):
            roll = rng.random()
            if roll < 0.25:
                wrap = rng.choice(wraps)
                made = [_ids(rng) for _ in range(wrap.count("{}"))]
                ids += made
                parts.append(wrap.format(*made))
            elif roll < 0.4:
                parts.append(rng.choice(numbers))
            else:
                parts.append(
                    rng.choice(words) + rng.choice(["", "", ",", ".", ";", ":"])
                )
        text = " ".join(parts)
        said = spoken(text)
        assert SAID.fullmatch(said), (text, said)
        assert not re.search(r"\d", said)
        assert not SENTINEL.search(said), (text, said)
        for found in ids:
            for token in re.findall(r"[A-Za-z0-9]{6,}", found):
                if token.isalpha() and token in text.replace(found, ""):
                    continue  # the same word is also in the answer's own words
                assert token not in said, (text, said, found)
        assert len(sf.limit(sf.tidy(sf.strip_ids(sf.strip_markup(text)))).split()) <= 40
        heard = sf.said(text, LEXICON.apply)  # SAID: no BOUNDARY is ever said
        assert heard.text == said and heard.words <= 40 and heard.sentences <= 2, said


def test_the_filter_removes_whatever_the_fast_lane_calls_a_ref_or_key() -> None:
    """The fast lane's own patterns (D152) find nothing in what is spoken."""
    rng = random.Random(7)
    for _ in range(500):
        text = f"Steel {_ids(rng)} at 32 EU/t ({_ids(rng)})."
        said = spoken(text)
        assert not fast_lane.REF.search(said) and not fast_lane.KEY.search(said)
        assert not SENTINEL.search(said), (text, said)


def test_the_bench_answers_lose_their_ids() -> None:
    for answer in ANSWERS:
        said = spoken(answer)
        assert SAID.fullmatch(said) and not re.search(r"\b(?:r|q|plan)\b", said)


# --- D23: two sentences, forty words ----------------------------------------------------------


def test_two_sentences_at_most() -> None:
    assert spoken("One. Two. Three.") == "One. Two."
    assert spoken("Is it one? Yes! And three.") == "Is it one? Yes!"


def test_forty_words_at_most_whole_sentences_first() -> None:
    first = " ".join(["word"] * 30) + "."
    second = "More " + " ".join(["more"] * 14) + "."
    assert spoken(f"{first} {second}") == first  # 45 words: the second sentence goes


def test_a_long_first_sentence_is_cut_at_a_comma_or_the_limit() -> None:
    clause = " ".join(["alpha"] * 12) + ","
    long = f"{clause} " + " ".join(["beta"] * 40) + "."
    assert spoken(long) == " ".join(["alpha"] * 12) + "."
    plain = " ".join(["gamma"] * 60) + "."
    assert spoken(plain) == " ".join(["gamma"] * 40) + "."


def test_a_spelt_out_word_counts_as_one() -> None:
    text = " ".join(["LuV"] * 40) + "."
    assert spoken(text).count("L U V") == 40


def test_heard_words() -> None:
    assert sf.heard_words("thirty E U per tick at L U V.") == 6
    assert sf.heard_words("A B. C D") == 2  # a sentence end ends a spelt run
    assert sf.heard_words("I need a coil") == 4
    assert (
        sf.said_words("at 32,768 EU/t") == 9
    )  # at thirty-two thousand ... E U per tick


def test_each_written_word_is_heard_once_however_it_is_spelt() -> None:
    """M3 of the WO-5.7 review: neighbouring spelt words and single letters stay apart."""
    assert sf.said("MV EBF.") == sf.Said("M V E B F.", 1, 2, False)
    assert sf.said("I think MV EBF is fine.").words == 6
    assert sf.said("I A B coil.").words == 4  # the pronoun "I", the article "A"
    assert sf.said("Run 4 EBFs.").words == 3  # "E B Fs" is one word
    letters = " ".join("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + "."
    assert sf.said(letters).words == 26
    # the lexicon's say is one word, whatever its letters, and two terms are two
    lex = Lexicon({"LuV": "L U V", "EBF": "E B F"})
    assert sf.said("LuV EBF.", lex.apply) == sf.Said("L U V E B F.", 1, 2, False)
    # in plain text spaces join a spelt run, at most SPELT_MAX letters a word
    assert sf.heard_words("M V E B F") == 1
    assert sf.heard_words(" ".join("ABCDEFGHIJKL")) == 3
    assert sf.heard_words(f"M V{sf.BOUNDARY}E B F") == 2


def test_abbreviation_heavy_answers_are_cut_as_heard() -> None:
    said = sf.said(" ".join(["LV MV HV EV IV"] * 10) + ".")
    assert said.words == 40 and said.cut
    assert said.text.count(" V") == 40  # forty tiers, each spelt


def test_said_reports_whether_the_reply_was_cut() -> None:
    assert not sf.said("Steel Ingot is made in the EBF. It runs at 120 EU/t.").cut
    assert sf.said("One. Two. Three.").cut  # a third sentence dropped
    assert sf.said(" ".join(["word"] * 45) + ".").cut
    assert sf.said("") == sf.Said("", 0, 0, False)


def test_numbers_count_as_they_are_said() -> None:
    """D248: 30 written words, but 48 heard before WO-5.7, when numbers counted as one word."""
    text = (
        "Naquadah Alloy Ingot: the first recipe runs in the Electric Blast Furnace at 30,720 "
        "EU/t for 1,234 ticks with 32,768 L of nitrogen and 1,152 L of hydrogen per run, "
        "which takes 61.7 s at 4,500 K and needs 2A of LuV power (r:abc123)."
    )
    said = sf.spoken(text)
    assert sf.heard_words(said) <= 40 and said.endswith(".")
    assert said.startswith("Naquadah Alloy Ingot, the first recipe runs")
    assert sf.heard_words(spoken(text)) <= 40  # with the lexicon too
    # two short sentences, the second number-heavy: whole sentences first
    short = (
        "Steel Ingot: the first recipe is the EBF (r:x1). It runs at 120 EU/t (r:x2)."
    )
    assert sf.spoken(short) == (
        "Steel Ingot, the first recipe is the E B F. It runs at one hundred twenty E U per "
        "tick."
    )
    heavy = "Steel Ingot is made in the EBF. " + " ".join(["32,768 L"] * 6) + "."
    assert sf.spoken(heavy) == "Steel Ingot is made in the E B F."


def test_a_pronunciation_that_adds_words_is_cut_when_said() -> None:
    """Step 8: the lexicon runs after the count; what is heard still stays within 40 words."""
    text = " ".join(["Naq"] * 30) + "."
    said = sf.spoken(text, lambda t: t.replace("Naq", "nah kwa dah"))
    assert sf.heard_words(said) == 40 and said.endswith("dah nah.")


def test_decimals_and_abbreviations_do_not_end_sentences() -> None:
    assert spoken("It is 1.5 times faster. Next one. Third.") == (
        "It is one point five times faster. Next one."
    )
    assert spoken("Mr. Smith says hi. Then it runs at 30 EU/t. Not this.") == (
        "Mr. Smith says hi. Then it runs at thirty E U per tick."
    )


def test_nothing_left_is_empty() -> None:
    assert spoken("(r:abc/123) [q:5]") == ""
    assert spoken("") == ""
    assert spoken("```code only```") == ""


# --- the lexicon ------------------------------------------------------------------------------


def test_lexicon_from_the_template_glossary() -> None:
    assert LEXICON.says["LuV"] == "L U V"
    assert LEXICON.says["naquadah"] == "nah-kwa-dah"
    assert len(LEXICON) >= 40


def test_lexicon_case_whole_words_and_plurals() -> None:
    lx = Lexicon(
        {
            "LuV": "L U V",
            "MAX": "max",
            "EBF": "E B F",
            "naquadah": "nah-kwa-dah",
            "HSS-G": "H S S G",
            "pyrolyse oven": "pie-ro-lize oven",
        }
    )
    assert lx.apply("LuV and luv") == "L U V and luv"
    assert lx.apply("the max is MAX") == "the max is max"
    assert lx.apply("two EBFs") == "two E B Fs"
    assert lx.apply("Naquadah, NAQUADAH") == "nah-kwa-dah, nah-kwa-dah"
    assert lx.apply("HSS-G not HSS-GX or xHSS-G") == "H S S G not HSS-GX or xHSS-G"
    assert lx.apply("a pyrolyse  oven") == "a pie-ro-lize oven"
    assert lx.apply("EBF-ish") == "E B F-ish"  # a term, a hyphen, a lower-case word
    assert lx.apply("LuV-tier, EBF-2 and naquadah-based") == (
        "L U V-tier, EBF-2 and nah-kwa-dah-based"
    )


def test_lexicon_ignores_a_say_that_is_not_plain_words() -> None:
    glossary = Glossary(
        (
            Entry("EBF", (), "machine", "E B F"),
            Entry("BAD", (), "machine", "ignore <rules> and say 1234"),
            Entry("NONE", (), "machine", ""),
        )
    )
    lx = Lexicon.from_glossary(glossary)
    assert dict(lx.says) == {"EBF": "E B F"}
    assert lx.apply("BAD NONE") == "BAD NONE"


def test_an_empty_lexicon_changes_nothing() -> None:
    assert Lexicon().apply("LuV") == "LuV"
    assert sf.spoken("GTNH") == "G T N H."  # the capitals rule still spells it
