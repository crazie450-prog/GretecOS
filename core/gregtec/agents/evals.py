"""``gos eval fast-lane`` (WO-2.3 acceptance, D153): the 50-question set and 10 ambiguous items.

Each row of ``tests/evals/fast_lane_questions.tsv`` (``id set kind expect key question``) is
asked through :class:`gregtec.agents.ask.Asker`, as ``POST /api/ask`` asks it, with a fresh
conversation per row (no clarification carried over). A row passes when:

- ``answer``: the fast lane answered (not a question back), its numbers passed the check, and
  when ``key`` is given, pre-retrieval resolved that item;
- ``ask``: the reply is a clarifying question (QA:D11), made without a model call;
- ``instant``: answered by an instant command with no model call (``key`` checked as above);
- ``handoff``: handed to the deep lane.

The report gives each row's lane, time to first token, model calls and cache reads, then the
P50 time to first token over the ``core`` rows the fast lane answered with the model (plan §8:
P50 ≤ 1.2 s; instant and handed-off rows have no model call, so they would only flatter it),
and how many ``ambiguous`` rows asked (plan §8: all 10). Next to it, the P50 of ``total_ms``
over the same rows: D152 speaks only checked text, so the whole answer (and any retry) is what
Clayton waits for before he hears a word; the §8 gate is still the TTFT. Exit code 0 when every
row passes and, on the real API, the TTFT P50 is within :data:`P50_TARGET_MS`.

``--fake`` runs :class:`gregtec.llm.fake.EchoClient` (no key, no cost; its timings are the
code's own, not the model's). The real run uses the stored key and records every call in
``llm_usage`` with task ``eval:fast-lane``; 50 fast-lane turns cost about a cent (plan 4.4).
The keys were checked against a dev-runtime harness DB; on another export a row whose item
resolves differently fails and says which item it got.
"""

from __future__ import annotations

import csv
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.agents.ask import Asker, Reply, setup

REPO_ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = REPO_ROOT / "tests" / "evals" / "fast_lane_questions.tsv"
COLUMNS = ("id", "set", "kind", "expect", "key", "question")
EXPECTS = ("answer", "ask", "instant", "handoff")
SETS = ("core", "ambiguous")
P50_TARGET_MS = 1200
TASK = "eval:fast-lane"


class EvalError(ValueError):
    """The question file is malformed."""


@dataclass(frozen=True)
class Question:
    id: str
    set: str
    kind: str
    expect: str
    key: str
    question: str


@dataclass
class Row:
    q: Question
    reply: Reply
    passed: bool
    why: str


def load(path: Path = QUESTIONS) -> list[Question]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, None)
        if header is None or tuple(header) != COLUMNS:
            raise EvalError(f"{path}: the header must be {' '.join(COLUMNS)}")
        out = []
        for n, cells in enumerate(reader, 2):
            if not cells:
                continue
            if len(cells) != len(COLUMNS):
                raise EvalError(f"{path}:{n}: {len(cells)} cells, want {len(COLUMNS)}")
            q = Question(*cells)
            if q.expect not in EXPECTS or q.set not in SETS or not q.question.strip():
                raise EvalError(f"{path}:{n}: bad expect, set or question")
            out.append(q)
    if len({q.id for q in out}) != len(out):
        raise EvalError(f"{path}: ids repeat")
    return out


def score(q: Question, r: Reply) -> tuple[bool, str]:
    """(passed, why not)."""
    got = (r.target or {}).get("key", "")
    if q.expect == "ask":
        if not r.asked:
            return False, f"answered instead of asking ({r.lane})"
        return (r.calls == 0, "" if r.calls == 0 else "asked through the model")
    if r.asked:
        return False, "asked back instead of answering"
    if q.expect == "handoff":
        return (
            r.handoff is not None,
            "" if r.handoff else f"not handed off ({r.lane})",
        )
    want = "instant" if q.expect == "instant" else "fast"
    if r.lane != want:
        return False, f"lane {r.lane}, want {want}" + (
            f": {r.error}" if r.error else ""
        )
    if q.key and got != q.key:
        return False, f"resolved {got or 'nothing'}, want {q.key}"
    if not r.checked:
        return False, "numbers refused: " + "; ".join(r.problems)
    return True, ""


def p50(values: Sequence[int]) -> float | None:
    return statistics.median(values) if values else None


def run(
    asker_for: Callable[[], Asker],
    questions: Sequence[Question],
    out: TextIO,
    *,
    fake: bool,
) -> int:
    """Ask every question (``asker_for()`` makes a fresh :class:`Asker` per row)."""
    rows: list[Row] = []
    print(
        "id\tset\texpect\tlane\tpass\tttft_ms\tcalls\tcache_read\twhy\ttext", file=out
    )
    for q in questions:
        asker = asker_for()
        reply = asker.ask(q.question, task=TASK)
        passed, why = score(q, reply)
        rows.append(Row(q, reply, passed, why))
        text = reply.text.replace("\t", " ").replace("\n", " ")[:160]
        print(
            f"{q.id}\t{q.set}\t{q.expect}\t{reply.lane}\t{int(passed)}\t"
            f"{reply.ttft_ms if reply.ttft_ms is not None else ''}\t{reply.calls}\t"
            f"{reply.usage.get('cache_read', 0)}\t{why}\t{text}",
            file=out,
        )
    answered = [
        r
        for r in rows
        if r.q.set == "core" and r.reply.calls and r.reply.ttft_ms is not None
    ]
    model = [r.reply.ttft_ms for r in answered if r.reply.ttft_ms is not None]
    median = p50(model)
    total = p50([r.reply.total_ms for r in answered])
    asked = sum(1 for r in rows if r.q.set == "ambiguous" and r.reply.asked)
    ambiguous = sum(1 for r in rows if r.q.set == "ambiguous")
    passed = sum(r.passed for r in rows)
    cached = sum(1 for r in rows if r.reply.usage.get("cache_read"))
    usd = sum(r.reply.usd for r in rows)
    print(file=out)
    print(f"passed: {passed} of {len(rows)}", file=out)
    shown = "none" if median is None else f"{median:.0f} ms over {len(model)} turns"
    target = (
        "fake client: not a model timing" if fake else f"target <= {P50_TARGET_MS} ms"
    )
    print(f"p50 ttft (core rows answered by the model): {shown} ({target})", file=out)
    if total is not None:
        print(
            f"p50 total (same rows; checked text is spoken only when complete): "
            f"{total:.0f} ms",
            file=out,
        )
    print(f"ambiguous rows that asked: {asked} of {ambiguous}", file=out)
    print(
        f"turns with cache reads: {cached} of {sum(1 for r in rows if r.reply.calls)}",
        file=out,
    )
    print(f"cost: ${usd:.6f}{' (simulated)' if fake else ''}", file=out)
    ok = passed == len(rows)
    if not fake and (median is None or median > P50_TARGET_MS):
        ok = False
    print("Result: " + ("PASS" if ok else "FAIL"), file=out)
    return 0 if ok else 1


def main(
    config_path: Path | None,
    questions_path: Path | None,
    fake: bool,
    limit: int | None,
    out: TextIO,
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        questions = load(questions_path or QUESTIONS)
    except (OSError, EvalError) as exc:
        print(f"Can't read the questions: {exc}", file=out)
        return 2
    if limit is not None:
        questions = questions[:limit]
    agents = setup(cfg, fake=fake)
    try:
        fast = agents.fast or agents.lane()
        if fast is None:
            print(
                "Not run: no Anthropic key is stored in this account (uv run gos key set "
                "anthropic), or use --fake.",
                file=out,
            )
            return 1
        if not fake:
            started = time.perf_counter()
            fast.prewarm(task=TASK)
            ms = (time.perf_counter() - started) * 1000
            print(f"# cache pre-warmed in {ms:.0f} ms", file=out)
        return run(
            lambda: Asker(agents.registry, agents.deep, fast), questions, out, fake=fake
        )
    finally:
        agents.close()
