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
    assert checked >= 6


def test_every_relative_link_resolves() -> None:
    broken = []
    for target in LINK.findall(_text()):
        if target.startswith(("http://", "https://", "#")):
            continue
        if not (REPORT.parent / target.partition("#")[0]).exists():
            broken.append(target)
    assert broken == []
