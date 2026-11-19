"""docs/deployment_checklist.md stays complete as the repo changes (WO-DEPLOY, D338).

Every runbook is linked from it, every open VERIFY item and every decision row still marked
pending Clayton appears in it, every gate report is linked, and its links resolve.
"""

from __future__ import annotations

import re
from pathlib import Path

from gregtec.doctor import load_verify_list, verify_status

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
CHECKLIST = DOCS / "deployment_checklist.md"
SECTIONS = (
    "## 1. Before you start",
    "## 2. Deploy and first-run wizard",
    "## 3. Phase-by-phase PC checks",
    "## 4. The 2-hour fake day one",
    "## 5. Decisions waiting for you",
    "## 6. Gate sign-offs G1 to G11",
    "## 7. Known gaps and flakes",
)
# A VERIFY row is open unless it starts with one of these: gos doctor's rule
# (gregtec/doctor.py verify_status, which also special-cases V01 and V02 with live Prism roots).
# test_the_open_rule_matches_gos_doctor keeps the two in step.
CLOSED = ("**Closed**", "**Dropped**", "**Resolved**")
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _text() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


def _mentions(text: str, ident: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(ident)}(?![\w-])", text) is not None


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    words = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return words.replace(" ", "-")


def test_the_sections_are_there_in_order() -> None:
    text = _text()
    places = [text.find(f"\n{s}\n") for s in SECTIONS]
    assert -1 not in places, [s for s, p in zip(SECTIONS, places) if p == -1]
    assert places == sorted(places)


def test_every_runbook_is_linked() -> None:
    text = _text()
    runbooks = sorted(p.name for p in (DOCS / "runbooks").glob("*.md"))
    assert runbooks
    missing = [n for n in runbooks if f"](runbooks/{n})" not in text]
    assert missing == []


def test_every_gate_report_is_linked_and_g11_has_a_row() -> None:
    text = _text()
    reports = sorted(p.name for p in (DOCS / "reviews").glob("G*_review.md"))
    assert len(reports) >= 11  # G0 to G10
    missing = [
        n for n in reports if n != "G0_review.md" and f"](reviews/{n})" not in text
    ]
    assert missing == []
    gates = text[text.index("\n## 6. ") : text.index("\n## 7. ")]
    for n in range(1, 12):
        assert re.search(rf"^\| G{n} ", gates, re.MULTILINE), f"G{n}"


def test_every_open_verify_item_is_in_the_checklist_and_its_table() -> None:
    text = _text()
    table = text[text.index("### 3.16 ") : text.index("\n## 4. ")]
    items = load_verify_list()
    open_ids = [i.id for i in items if not i.item.startswith(CLOSED)]
    assert open_ids
    assert [v for v in open_ids if not _mentions(text, v)] == []
    rows = set(re.findall(r"^\| (V\d\d) \|", table, re.MULTILINE))
    assert sorted(set(open_ids) - rows) == []
    closed = {i.id for i in items} - set(open_ids)
    assert sorted(rows & closed) == []  # the table lists open items only


def test_the_open_rule_matches_gos_doctor() -> None:
    for item in load_verify_list():
        if item.id in ("V01", "V02"):  # doctor reads these from the live Prism roots
            continue
        is_open = verify_status(item, []).startswith("open")
        assert is_open == (not item.item.startswith(CLOSED)), item.id


def test_every_decision_pending_clayton_is_in_the_checklist() -> None:
    """Every row whose text says "pending Clayton" is named by its own D-number.

    A range such as "D290-D292" doesn't count as naming D291, and rows that leave Clayton's
    review open in other words (for example "Clayton's review open") aren't matched, so those
    are covered only by the checklist's prose ranges, not by this test.
    """
    text = _text()
    pending = []
    for line in (DOCS / "decisions.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (D\d+) \|", line)
        if m and re.search(r"pending clayton", line, re.IGNORECASE):
            pending.append(m.group(1))
    assert len(pending) > 40
    assert [d for d in pending if not _mentions(text, d)] == []


def test_every_relative_link_resolves() -> None:
    text = _text()
    anchors = {
        _slug(m.group(1)) for m in re.finditer(r"^#{2,4} (.+)$", text, re.MULTILINE)
    }
    broken = []
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://")):
            continue
        path, _, anchor = target.partition("#")
        ok = (CHECKLIST.parent / path).exists() if path else anchor in anchors
        if not ok:
            broken.append(target)
    assert broken == []


def test_the_slug_rule() -> None:
    assert (
        _slug("3.15 Steps that touch Play (yours, last)")
        == "315-steps-that-touch-play-yours-last"
    )
