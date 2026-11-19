"""docs/reviews/G11_review.md covers what WO-G11 asks of it (D339).

A row for every plan-10 line and every Phase 11 row, each with one of the agreed statuses; a row
for every eval plan 9 lists, with the size its file really has; and its relative links resolve.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLAN = REPO / "docs" / "implementation_plan.md"
REPORT = REPO / "docs" / "reviews" / "G11_review.md"
EVALS = REPO / "tests" / "evals"
STATUSES = ("met with fakes", "not met", "not merged", "met")
STATUS = re.compile(r"(?:automated: )?\*\*(met with fakes|not met|not merged|met)\*\*")
VERDICT = re.compile(
    r"^\*\*Verdict: (met with fakes|not met|not merged|met)\.?\*\*", re.MULTILINE
)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _text() -> str:
    return REPORT.read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end, text.index(start))]


def _plan_10_lines() -> list[str]:
    plan = PLAN.read_text(encoding="utf-8")
    block = _section(plan, "## 10. Day-one readiness checklist", "## 11. ")
    return re.findall(r"^- \[ \] (.+)$", block, re.MULTILINE)


def _rows(text: str) -> int:
    """Data rows of an eval file: no blank, comment or header line."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return len(lines) - 1


def test_every_plan_10_line_has_a_row_with_a_status() -> None:
    lines = _plan_10_lines()
    assert len(lines) == 12
    table = _section(_text(), "## Plan §10, line by line", "## Phase 11 work orders")
    rows = re.findall(r"^\| (\d+) \| [^|]+ \| ([^|]+) \|", table, re.MULTILINE)
    assert [int(n) for n, _ in rows] == list(range(1, len(lines) + 1))
    for n, status in rows:
        assert STATUS.match(status), (n, status)


def test_every_phase_11_row_and_g11_has_a_verdict() -> None:
    text = _text()
    for wo in ("WO-11.1", "WO-11.2", "WO-11.3", "G11"):
        head = re.search(rf"^### {re.escape(wo)}: .+$", text, re.MULTILINE)
        assert head, wo
        after = re.split(r"^#{2,3} ", text[head.end() :], flags=re.MULTILINE)[0]
        verdict = VERDICT.search(after)
        assert verdict and verdict.group(1) in STATUSES, wo


def test_every_plan_9_eval_is_listed_and_the_sizes_are_the_files() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    listed = re.search(r"\*\*Evals\*\* \(`tests/evals/\*\.tsv`\): (.+)", plan)
    assert listed
    asked = re.findall(r"(\d+) ([a-z/ -]+?)(?:,| Q&A,| utterances)", listed.group(1))
    assert len(asked) == 9
    table = _section(_text(), "## Plan §9's eval list", "## Every gate report")
    for size, _ in asked:
        assert re.search(rf"^\| [^|]+ \| {size} \|", table, re.MULTILINE), size
    assert _check_sizes(table) >= 6


def _check_sizes(table: str) -> int:
    """Each row whose 'where' cell starts with a `tests/evals/*.tsv` file gives that file's
    real size; returns how many rows were checked."""
    checked = 0
    for where, rows in re.findall(
        r"^\| [^|]+ \| \d+ \| ([^|]+) \| (\d+) \|", table, re.MULTILINE
    ):
        name = re.match(r"`tests/evals/([\w.]+\.tsv)`", where)
        if not name:
            continue
        text = (EVALS / name.group(1)).read_text(encoding="utf-8")
        subset = re.search(r"rows of set `(\w+)`", where)
        if subset:  # the ambiguous-item eval is one set of the fast-lane file
            got = sum(
                1
                for ln in text.splitlines()
                if ln.split("\t")[1:2] == [subset.group(1)]
            )
        else:
            got = _rows(text)
        assert got == int(rows), where
        checked += 1
    return checked


def test_every_relative_link_resolves() -> None:
    broken = []
    for target in LINK.findall(_text()):
        if target.startswith(("http://", "https://", "#")):
            continue
        if not (REPORT.parent / target.partition("#")[0]).exists():
            broken.append(target)
    assert broken == []


ADDENDUM = re.compile(r"^## Addendum, \d{4}-\d{2}-\d{2}: .+$", re.MULTILINE)


def _addendum() -> str:
    text = _text()
    head = ADDENDUM.search(text)
    assert head, "the addendum after WO-6.8, WO-8.4 and WO-11.4"
    return text[head.start() :]


def test_the_addendum_comes_last_and_keeps_the_verdict() -> None:
    """The addendum is appended after the body (which stays the record of its date) and
    repeats the verdict; it reuses none of the body's section headings, which the checks
    above find by their first occurrence."""
    text, add = _text(), _addendum()
    assert text.index("## After the review") < text.index(add)
    for heading in (
        "## Plan §10, line by line",
        "## Phase 11 work orders",
        "## Plan §9's eval list",
        "## Every gate report",
        "### WO-11.",
        "### G11:",
    ):
        assert heading not in add, heading
    verdict = VERDICT.search(add)
    assert verdict and verdict.group(1) in STATUSES


def test_the_addendum_s_section_10_rows_have_statuses_and_stay_red() -> None:
    """Every row of the addendum's §10 table has an agreed status; lines 3 and 5 stay
    **not met** until a reviewed edit of this test says otherwise."""
    table = _section(_addendum(), "### §10 lines now", "\n### ")
    rows = dict(re.findall(r"^\| (\d+) \| [^|]+ \| ([^|]+) \|", table, re.MULTILINE))
    assert rows, "no §10 rows in the addendum"
    for n, status in rows.items():
        assert STATUS.match(status), (n, status)
    for n in ("3", "5"):
        assert rows[n].startswith("**not met**"), (n, rows[n])


def test_the_addendum_s_eval_sizes_are_the_files() -> None:
    """The addendum's §9 rows give the real sizes of their files, and no row says an eval
    has no file ('none') when `tests/evals` has one for it."""
    table = _section(_addendum(), "### §9 evals now", "\n### ")
    assert _check_sizes(table) >= 1  # the hover-item row
    for name, where in re.findall(
        r"^\| ([^|]+) \| \d+ \| ([^|]+) \|", table, re.MULTILINE
    ):
        if where.strip().lower().startswith("none"):
            stem = name.strip().replace("-", "_").split()[0]
            assert not list(EVALS.glob(f"{stem}*.tsv")), name
