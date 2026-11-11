"""The numeric validator for planner explanations (WO-3.5, plan §7.1 step 11, [QA:D4], D199).

The metrics JSON here is made up and shaped as :func:`gregtec.planner.explain.facts` writes it;
no pack data.
"""

from __future__ import annotations

from typing import Any

import pytest

from gregtec.planner import validate

DOC: dict[str, Any] = {
    "target": {"id": "test:item@1", "name": "Widget", "rate_s": 2.0},
    "weights": {"set": "balanced", "machines": 1.0, "effort": 1.0},
    "routes": [
        {
            "id": "pl-0123456789",
            "name": "Widget line · 2/s · MV",
            "position": 1,
            "of": 2,
            "score": 0.697,
            "metrics": {
                "machines": 10,
                "new_types": 1,
                "eut_avg": 1200.0,
                "eut_peak": 1500,
                "tier": "MV",
                "eff_h": 6.5,
                "scarcity": 0.4,
                "by_type": [
                    {"name": "Mixer", "tier": "MV", "count": 4, "confidence": 0.9},
                    {"name": "Quantum Tank IV", "tier": "MV", "count": 6, "confidence": 1},
                ],
                "raw_in_h": [{"name": "Circuit 1", "per_h": 7200.0}],
                "batch": {"qty": 64, "eta_s": 1800, "window_s": 3600},
                "util": 0.93,
            },
        },
        {
            "id": "pl-abcdef0123",
            "name": "Widget line · 2/s · MV · route 2",
            "position": 2,
            "of": 2,
            "score": 0.656,
            "metrics": {
                "machines": 15,
                "new_types": 2,
                "eut_avg": 366.0,
                "eut_peak": 400,
                "tier": "LV",
                "eff_h": 2.0,
                "scarcity": 0.0,
                "by_type": [],
                "raw_in_h": [],
            },
        },
    ],
}
NAMES = ["Widget", "Mixer", "Quantum Tank IV", "Circuit 1", "Widget line · 2/s · MV"]


def ok(text: str) -> bool:
    return validate.check(text, DOC, NAMES).ok


# Twenty normalisation cases (§8 row 3.5): each states a number the JSON holds, written another
# way: units, prefixes, rounding, "about", tiers, masked names, sums, percentages.
NORMALISED = [
    "Route 1 needs 10 machines.",  # a plain count
    "It makes 2/s of Widget.",  # a rate per second
    "It makes 120/min of Widget.",  # per minute: 2/s
    "It makes 7,200/h.",  # per hour, with a thousands comma
    "It makes 2 items per second.",  # per with a word
    "Circuit 1 goes in at 2 per second.",  # 7200 per hour, and a name with a digit
    "It draws 1.2k EU/t on average.",  # a k prefix
    "It draws 1,200 EU/t.",  # thousands commas
    "Average draw 1201 EU/t.",  # within 1% rounding
    "It needs about 1,250 EU/t.",  # within 5% after about
    "It needs ~1.25k EU/t.",  # within 5% after ~
    "Effort is 6.5 h.",  # hours
    "Effort is 390 minutes.",  # 6.5 h in minutes
    "The batch finishes in 30 min.",  # 1800 s
    "Utilisation is 93%.",  # a fraction as a percentage
    "The routes need 25 machines in all.",  # a sum of two routes' metrics
    "Route 2 needs 5 more machines.",  # a difference between routes (D199)
    "It is an MV line; route 2 stays at LV.",  # tier names the JSON holds
    "Quantum Tank IV holds the acid; tier 2 is enough.",  # a Roman numeral in a name; tier N
    "See plan:pl-0123456789 and mte:1000 for r:gt.recipe.mixer/0123456789ab.",  # ids masked
]


@pytest.mark.parametrize("text", NORMALISED)
def test_normalisation_cases_pass(text: str) -> None:
    result = validate.check(text, DOC, NAMES)
    assert result.ok, result.offending


def test_twenty_normalisation_cases() -> None:
    assert len(NORMALISED) == 20 and len(set(NORMALISED)) == 20


@pytest.mark.parametrize(
    ("text", "bad"),
    [
        ("Route 1 needs 11 machines.", "11"),
        ("It makes 3/s of Widget.", "3/s"),
        ("It makes 120/h of Widget.", "120/h"),  # right digits, wrong unit
        ("It draws 1.3k EU/t.", "1.3k"),
        ("It draws 1,230 EU/t.", "1,230"),  # 2.5% off without about
        ("It needs about 1,300 EU/t.", "1,300"),  # 8% off even with about
        ("Effort is 7 h.", "7 h"),
        ("Effort is 6.5 minutes.", "6.5 minutes"),
        ("Utilisation is 95%.", "95%"),
        ("It is an HV line.", "HV"),
        ("tier 4 is enough.", "tier 4"),
        ("The routes need 26 machines in all.", "26"),
    ],
)
def test_injected_wrong_numbers_are_caught(text: str, bad: str) -> None:
    result = validate.check(text, DOC, NAMES)
    assert not result.ok and bad in result.offending


def test_a_paragraph_lists_every_wrong_number_once() -> None:
    text = (
        "Route 1 (Widget line · 2/s · MV) needs 10 machines and 12 hours of effort; it draws "
        "1,200 EU/t, 12 hours is a lot, and makes 9/s."
    )
    result = validate.check(text, DOC, NAMES)
    assert result.offending == ("12 hours", "9/s") and result.checked >= 5


def test_zero_is_only_zero_and_digits_glued_to_words_are_not_numbers() -> None:
    assert ok("Route 2 has scarcity 0.")
    assert not ok("Route 2 has scarcity 0.001.")
    assert ok("H2O and x4 are words here.")


def test_names_are_masked_case_insensitively_longest_first() -> None:
    assert ok("quantum tank iv and CIRCUIT 1 are fine.")
    assert not validate.check("Quantum Tank IV", DOC, []).ok  # IV read as a tier


def test_ordinals_and_positions() -> None:
    assert ok("The 1st route beats the 2nd.")
    assert not ok("The 7th route.")


def test_an_empty_doc_allows_no_numbers() -> None:
    assert validate.check("nothing here", {}).ok
    assert not validate.check("3 machines", {}).ok
