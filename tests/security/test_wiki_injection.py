"""Injected instructions in wiki text (plan 9, security tests; WO-8.3, D292).

The tests live with the fake wiki they need, in ``tests/unit/wiki/test_wiki_tools.py``: an
injected instruction never becomes a tool call, the page text stays between nonce markers, and
snippets and titles outside the markers stay plain data. This pointer fails if they go away.
"""

from __future__ import annotations

from pathlib import Path

TOOLS_TESTS = (
    Path(__file__).resolve().parents[1] / "unit" / "wiki" / "test_wiki_tools.py"
)


def test_the_wiki_injection_tests_are_there() -> None:
    text = TOOLS_TESTS.read_text(encoding="utf-8")
    for name in (
        "test_an_injected_instruction_never_becomes_a_tool_call",
        "test_fetch_shows_licence_attribution_and_frames_the_text",
        "test_an_injection_in_a_snippet_or_title_stays_plain_data",
        "test_wiki_tools_never_reach_the_fast_lane_or_a_prompt",
    ):
        assert f"def {name}(" in text, name
