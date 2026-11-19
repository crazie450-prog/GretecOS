"""``gos eval wiki`` (WO-8.4, plan 9's 15 wiki cases, D363): questions whose right answer cites
pages of Clayton's own wiki crawl by their ``w:<Title>@<revid>`` ids (D291).

The 15 real cases can't be written in the cloud: the ``w:`` ids are revisions of pages in the
crawl of his PC, which waits on the licence check (checklist 3.9.6), and no id or page is made
up. So the case file lives where his crawl does, ``$GOS_HOME/data/evals/wiki_questions.tsv``
(``--cases`` for another; never committed by GOS: it is drawn from his personal cache), and
``gos eval wiki --make-cases --page TITLE ...`` helps him write it: for each page he names it
reads the page from his store, proposes a question, fills in the page's ``w:`` id, shows the
page's first lines and asks him for the facts a right answer must contain; a case is written
only when he says yes. CI runs the same runner and scorer on fixture cases over the wiki tests'
own made-up pages (``tests/unit/wiki``).

A case (``id kind question cites facts absent confirmed``): ``kind`` ``fact`` or ``injected``
(the page holds an instruction or a claim the answer must not repeat); ``cites`` the ``w:``
ids a right answer cites (``;`` between them), at the revision he confirmed; ``facts`` the
phrases it must contain (no digits: game numbers come from the DB, never from a case file);
``absent`` what it must not say (an injected claim); ``confirmed`` the day he confirmed it.

The scorer (:func:`score`) is deterministic and reads only the store: a case whose page is gone
from the cache, or whose page changed since he confirmed it, is reported ``not checked`` and
fails (re-confirm it with ``--make-cases``), never passed silently. Otherwise the answer fails
when it cites a ``w:`` id the cache doesn't hold at that revision (an invented or stale id),
doesn't cite each case page, misses a fact, says an ``absent`` phrase, states a number that no
cited page's text holds, or lacks the wiki's attribution line (D291, added by the deep lane in
code).

The real run asks each question as a deep-lane task (the orchestrator hands it to the read-only
researcher, D292), ``llm_usage`` task ``eval:wiki``, each task capped at ``[budget]
task_max_usd`` (D253's measured per-task cap; a task that reaches it is stopped, never
approved, and fails as ``capped``: re-run it, it says nothing about the answer). It prints its
worst case first (every task at its cap), refuses while the AI is paused or off or when the
worst case would pass today's ``[budget] day_max_usd`` (D334, refined for paid evals by D363:
raise it for the sitting or run another day), and waits for "yes". ``--fake`` answers with
:class:`WikiOracle`, which calls the researcher's own tools (``wiki_search``, ``wiki_fetch``)
through the registry and adds the attribution line as the deep lane does: it proves the case
format, the store, the tools and the scorer, not a model or the deep lane's subagent path.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol, TextIO

from gregtec import config
from gregtec.agents import fast_lane
from gregtec.agents.tools.items import words
from gregtec.agents.tools.registry import DEEP, Registry
from gregtec.agents.tools.tsv import parse
from gregtec.db import access
from gregtec.wiki.cache import Licence, Page, licence_of, normalize, page_of

COLUMNS = ("id", "kind", "question", "cites", "facts", "absent", "confirmed")
KINDS = ("fact", "injected")
TASK = "eval:wiki"
PLAN_CASES = 15  # plan 9
DEEP_WAIT_S = 1800.0
NOT_CHECKED = "not checked"
CAPPED = "capped"
CITE = re.compile(r"w:(?P<title>[^\s@]+)@(?P<revid>\d+)")
_SPLIT = re.compile(r"\s*;\s*")
_UNWRITABLE = ("\t", "\n", "\r", '"')  # the file is plain TSV, read without quoting
_BODY = re.compile(
    r"begins:[^\]]*\]\n(?P<text>.*)\n\[wiki text", re.DOTALL
)  # wiki_fetch's
_MARKUP = re.compile(r"'''?|\[\[|\]\]")
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "what",
        "whats",
        "which",
        "who",
        "how",
        "does",
        "do",
        "did",
        "say",
        "says",
        "said",
        "about",
        "tell",
        "me",
        "wiki",
        "gtnh",
        "please",
        "can",
        "you",
        "i",
        "my",
        "this",
        "that",
        "it",
    ]
)


def cases_path(cfg: config.Config) -> Path:
    """Where his real cases live: next to the crawl they cite, never in the repo."""
    return cfg.data_dir / "evals" / "wiki_questions.tsv"


class EvalError(ValueError):
    """The case file is malformed."""


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    question: str
    cites: tuple[str, ...]  # w:<Title>@<revid>, as confirmed
    facts: tuple[str, ...]
    absent: tuple[str, ...]
    confirmed: str

    def row(self) -> list[str]:
        return [
            self.id,
            self.kind,
            self.question,
            "; ".join(self.cites),
            "; ".join(self.facts),
            "; ".join(self.absent),
            self.confirmed,
        ]


def _list(value: str) -> tuple[str, ...]:
    return tuple(v for v in _SPLIT.split(value.strip()) if v)


def case(cells: Sequence[str], where: str) -> Case:
    """One row of the file, checked (``where`` names it in an error)."""
    if len(cells) != len(COLUMNS):
        raise EvalError(f"{where}: {len(cells)} cells, not {len(COLUMNS)}")
    id_, kind, question, cites, facts, absent, confirmed = (c.strip() for c in cells)
    if not id_ or kind not in KINDS:
        raise EvalError(f"{where}: needs an id and a kind ({', '.join(KINDS)})")
    if not question:
        raise EvalError(f"{where}: the question can't be empty")
    got = _list(cites)
    if not got or not all(CITE.fullmatch(c) for c in got):
        raise EvalError(f"{where}: cites must be w:<Title>@<revid> ids, ';' between")
    found = _list(facts)
    if not found:
        raise EvalError(f"{where}: at least one fact a right answer must contain")
    if any(re.search(r"\d", f) for f in found):
        raise EvalError(
            f"{where}: a fact holds a number: game numbers come from the DB, never a case"
        )
    kept = (id_, kind, question, cites, facts, absent, confirmed)
    if any(ch in cell for cell in kept for ch in _UNWRITABLE):
        raise EvalError(f"{where}: a tab, a line break or a \" can't go in a case")
    gone = _list(absent)
    if kind == "injected" and not gone:
        raise EvalError(f"{where}: an injected case names what the answer must not say")
    return Case(id_, kind, question, got, found, gone, confirmed)


def load(path: Path) -> list[Case]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, None)
        if header is None or tuple(header) != COLUMNS:
            raise EvalError(f"{path}: the header must be {' '.join(COLUMNS)}")
        out = [
            case(row, f"{path}:{n}")
            for n, row in enumerate(reader, start=2)
            if row and "".join(row).strip()
        ]
    if len({c.id for c in out}) != len(out):
        raise EvalError(f"{path}: ids repeat")
    return out


def write(path: Path, cases: Sequence[Case]) -> None:
    """The whole file, replaced at once (a temp file then ``os.replace``): an interrupted
    write leaves the cases as they were."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(
                f, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE
            )
            writer.writerow(COLUMNS)
            for c in cases:
                writer.writerow(c.row())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# --- the store and the scorer -----------------------------------------------------------


class Store:
    """The wiki cache in GOS_HOME's DB, read-only: pages by title, and the licence."""

    def __init__(self, db: Path) -> None:
        if not db.is_file():
            raise FileNotFoundError(
                f"no DB at {db}: run gos db build, then gos wiki crawl"
            )
        self.conn = access.connect(db, readonly=True)
        self.licence: Licence | None = licence_of(self.conn)

    def close(self) -> None:
        self.conn.close()

    def current(self, cite_id: str) -> Page | None:
        """The cached page a ``w:`` id names (by title, any revision), else None."""
        named = parts_of(cite_id)
        return None if named is None else page_of(self.conn, named[0], hops=0)


def parts_of(cite_id: str) -> tuple[str, int] | None:
    """(the normalised title, the revid) of a ``w:`` id."""
    m = CITE.fullmatch(cite_id)
    return None if m is None else (normalize(m["title"]), int(m["revid"]))


def stale(store: Store, c: Case) -> list[str]:
    """Why ``c`` can't be checked on this cache (its pages gone, or changed since)."""
    out = []
    for want in c.cites:
        page = store.current(want)
        if page is None:
            out.append(f"{NOT_CHECKED}: {want}'s page isn't in the wiki cache")
        elif page.cite != want and parts_of(page.cite) != parts_of(want):
            out.append(
                f"{NOT_CHECKED}: {want} changed since it was confirmed (the cache has "
                f"{page.cite}): re-confirm it with --make-cases"
            )
    return out


def score(c: Case, text: str, store: Store) -> list[str]:
    """Why ``text`` fails ``c`` (empty: it passes)."""
    why = stale(store, c)
    if why:
        return why
    cited = [r for r in dict.fromkeys(fast_lane.refs(text)) if r.startswith("w:")]
    pages: list[str] = []
    titles = set()
    for r in cited:
        page = store.current(r)
        if page is None or parts_of(page.cite) != parts_of(r):
            why.append(f"cites {r}, which the wiki cache doesn't hold at that revision")
        else:
            pages.append(page.text)
            titles.add(normalize(page.title))
    for want in c.cites:
        named = parts_of(want)
        if named is not None and named[0] not in titles:
            why.append(f"doesn't cite {want}")
    folded = text.casefold()
    for fact in c.facts:
        if fact.casefold() not in folded:
            why.append(f"doesn't say {fact!r}")
    bare = fast_lane.REF.sub(" ", text)
    if store.licence is not None:  # the licence's own version and URL are not claims
        bare = bare.replace(store.licence.attribution(), " ")
    said_numbers = fast_lane.numbers(bare)
    for bad in c.absent:
        if fast_lane.NUMBER.fullmatch(bad):
            if fast_lane.value(bad) in said_numbers:
                why.append(f"says {bad!r}")
        elif bad.casefold() in bare.casefold():
            why.append(f"says {bad!r}")
    backed = set().union(*(fast_lane.numbers(p) for p in pages)) if pages else set()
    if uncited := sorted(said_numbers - backed, key=lambda n: (len(n), n)):
        why.append(f"numbers no cited page holds: {', '.join(uncited[:5])}")
    if store.licence is None:
        why.append("the wiki cache has no recorded licence (gos wiki licence)")
    elif store.licence.attribution() not in text:
        why.append("no attribution line (D291)")
    return why


# --- answering --------------------------------------------------------------------------


@dataclass
class Answer:
    text: str
    usd: float = 0.0
    why: str | None = None  # why there is no answer (a task stopped at its cap, ...)


class Answerer(Protocol):
    def __call__(self, question: str) -> Answer: ...


def keywords(question: str) -> str:
    """The words of a question worth searching the wiki for."""
    return " ".join(w for w in words(question) if w not in _STOP)


@dataclass
class WikiOracle:
    """``--fake``'s answerer: the researcher's tools through the registry (as the deep lane's
    researcher calls them), the best page's opening sentence with its ``w:`` id, and the
    attribution line the deep lane adds in code. Knows nothing of the case."""

    registry: Registry
    calls: list[str] = field(default_factory=list)

    def __call__(self, question: str) -> Answer:
        found = self.registry.call("wiki_search", {"q": keywords(question)}, lane=DEEP)
        self.calls.append("wiki_search")
        rows = [] if found.is_error else parse(found.text).rows
        if not rows:
            return Answer("The wiki cache has no page about that.")
        fetched = self.registry.call(
            "wiki_fetch", {"title": rows[0]["page"]}, lane=DEEP
        )
        self.calls.append("wiki_fetch")
        if fetched.is_error:
            return Answer("I couldn't read that wiki page.")
        p = parse(fetched.text)
        body = _BODY.search(fetched.text)
        lines = [
            line
            for line in (body["text"] if body else "").splitlines()
            if line.strip() and not line.startswith(("{{", "==", "<!--", "#"))
        ]
        first = _MARKUP.sub("", lines[0]) if lines else p.head["name"]
        sentence = first.split(". ")[0].rstrip(".")
        cache = self.registry.env.wiki
        lic = cache.licence() if cache is not None else None
        line = lic.attribution() if lic is not None else "Wiki text: GTNH wiki."
        return Answer(f"{sentence} ({p.head['cite']}).\n\n{line}")


def deep_answerer(cfg: config.Config) -> tuple[Answerer, Callable[[], None]]:
    """The real deep lane (its own key, the researcher's wiki tools), each task capped at
    ``[budget] task_max_usd``."""
    from gregtec.agents import recipe_eval
    from gregtec.agents.ask import setup
    from gregtec.agents.deep import DeepLaneService

    agents = setup(cfg)
    deep = agents.deep
    assert isinstance(deep, DeepLaneService)  # setup() without fake gives the real one
    deep.task_label = TASK

    def answer(question: str) -> Answer:
        before = {t.id for t in deep.tasks()}
        deep.handoff(question, question, "eval")
        text, cost, why = recipe_eval._deep_answer(deep, before, DEEP_WAIT_S)
        if why and "reached its cap" in why:  # says nothing about the answer: re-run it
            return Answer(text, cost, f"{CAPPED}: {why}")
        return Answer(text, cost, why if not text else None)

    return answer, agents.close


# --- the run ----------------------------------------------------------------------------


def run(
    answer: Answerer,
    cases: Sequence[Case],
    store: Store,
    out: TextIO,
    *,
    fake: bool,
    stop_usd: float | None = None,
) -> int:
    print("id\tkind\tpass\tusd\twhy\ttext", file=out)
    passed, unchecked, capped, spent = 0, 0, 0, 0.0
    for n, c in enumerate(cases):
        if stop_usd is not None and spent >= stop_usd:
            print(
                f"# stopped: ${spent:.4f} spent reached the stated ${stop_usd:.2f}; "
                f"{len(cases) - n} cases not asked",
                file=out,
            )
            break
        if why := stale(store, c):  # nothing is asked (or spent) for a case not checked
            unchecked += 1
            got = Answer("")
        else:
            got = answer(c.question)
            spent += got.usd
            why = [got.why] if got.why else score(c, got.text, store)
            capped += bool(got.why and got.why.startswith(CAPPED))
        passed += not why
        text = " ".join(got.text.split())[:200]
        print(
            f"{c.id}\t{c.kind}\t{int(not why)}\t{got.usd:.4f}\t{'; '.join(why)}\t{text}",
            file=out,
        )
    print(file=out)
    print(f"passed: {passed} of {len(cases)} (plan 9 asks for {PLAN_CASES})", file=out)
    if unchecked:
        print(f"{NOT_CHECKED}: {unchecked} (each fails; re-confirm them)", file=out)
    if capped:
        print(
            f"{CAPPED}: {capped} (each fails as stopped at the task cap, not as a wrong "
            "answer: re-run them)",
            file=out,
        )
    print(f"cost: ${spent:.4f}{' (no model: the oracle)' if fake else ''}", file=out)
    ok = bool(cases) and passed == len(cases)
    print("Result: " + ("PASS" if ok else "FAIL"), file=out)
    return 0 if ok else 1


def worst_case_usd(cfg: config.Config, cases: int) -> float:
    """Every case's task stopped at its cap: the most the paid run can spend (the cap is
    checked as the session goes, so a task can pass it by one model call; reported here as
    the cap, the deep lane meters the rest)."""
    return cfg.task_max_usd * cases


def _refusal(cfg: config.Config, worst: float) -> str | None:
    """Why the paid run may not start (D48's pause, his switch, D334's day cap)."""
    from gregtec.agents.no_ai import AiMode, CreditWatch
    from gregtec.llm.usage import UsageMeter
    from gregtec.ops.costs import CostWatch
    from gregtec.store import Store as CoreStore

    db = access.db_path(cfg.gos_home)
    core = CoreStore(db)
    try:
        meter = UsageMeter(
            core, cfg.monthly_included_usd, cfg.credit_alerts_pct, cfg.cycle_day
        )
        mode = AiMode(core)
        CreditWatch(meter, mode).check()
        if reasons := mode.reasons():
            return f"the AI is off ({', '.join(reasons)}): nothing is spent"
        day = CostWatch.of(core, cfg).caps()["day"]
        if day["spent_usd"] + worst > day["cap_usd"]:
            return (
                f"today's spend ${day['spent_usd']:.2f} plus this run's ${worst:.2f} would "
                f"pass the day cap ${day['cap_usd']:.2f} ([budget] day_max_usd)"
            )
        return None
    finally:
        core.close()


# --- making the cases -------------------------------------------------------------------


def make_cases(
    store: Store,
    titles: Sequence[str],
    path: Path,
    out: TextIO,
    ask: Callable[[str], str],
    today: str | None = None,
) -> int:
    """Propose a case for each page he names from his own cache, fill its ``w:`` id, and
    write the ones he confirms (appended to ``path``'s cases; an id is never reused)."""
    if store.licence is None:
        print(
            "No wiki licence is recorded: run gos wiki licence, then gos wiki crawl.",
            file=out,
        )
        return 1
    have = load(path) if path.is_file() else []
    ids = {c.id for c in have}
    added = 0
    today = today or date.today().isoformat()
    for title in titles:
        page = page_of(store.conn, title, hops=1)
        if page is None:
            print(
                f"{title!r} isn't in your wiki cache (gos wiki crawl, or check the title).",
                file=out,
            )
            continue
        if page.redirected_from:
            print(
                f"{title!r} redirects to {page.title!r}: that page is used.", file=out
            )
        proposed = f"What does the wiki say about the {page.title}?"
        print(f"\n{page.cite}  {page.url}", file=out)
        for line in [ln for ln in page.text.splitlines() if ln.strip()][:6]:
            print(f"  | {line[:160]}", file=out)
        question = ask(f"Question [{proposed}]: ").strip() or proposed
        kind = ask("Kind, fact or injected [fact]: ").strip() or "fact"
        facts = ask("Facts a right answer must contain, ';' between (no numbers): ")
        absent = ask("Phrases it must not say, ';' between (Enter for none): ")
        n = len(ids) + 1
        while f"w{n:02d}" in ids:
            n += 1
        try:
            new = case(
                [f"w{n:02d}", kind, question, page.cite, facts, absent, today],
                page.cite,
            )
        except EvalError as exc:
            print(f"Not kept: {exc}", file=out)
            continue
        text = page.text.casefold()
        for fact in new.facts:
            if fact.casefold() not in text:  # a typo makes a case no answer can pass
                print(f"Check: {fact!r} isn't in the page's text as typed.", file=out)
        if ask(f"Keep {new.id} ({new.question})? [y/N]: ").strip().casefold() not in (
            "y",
            "yes",
        ):
            print("Not kept.", file=out)
            continue
        have.append(new)
        ids.add(new.id)
        added += 1
        write(
            path, have
        )  # each kept case is saved at once: an interrupted session keeps it
    print(
        f"\n{added} kept; {len(have)} in {path} (plan 9 asks for {PLAN_CASES}).",
        file=out,
    )
    return 0


def main(
    config_path: Path | None,
    cases_file: Path | None,
    fake: bool,
    yes: bool,
    pages: Sequence[str],
    make: bool,
    out: TextIO,
    ask: Callable[[str], str] | None = None,
    answerer: Answerer | None = None,
    limit: int | None = None,
) -> int:
    """``ask`` (a prompt's reply) and ``answerer`` (the real run's) are for tests;
    ``limit``: only the first N cases."""
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    path = cases_file or cases_path(cfg)
    try:
        store = Store(access.db_path(cfg.gos_home))
    except (OSError, sqlite3.Error, access.SchemaError) as exc:
        print(f"Not run: {exc}", file=out)
        return 1

    def typed(prompt: str) -> str:
        print(prompt, end="", file=out, flush=True)
        return sys.stdin.readline()

    reply = ask or typed
    try:
        if make:
            if not pages:
                print(
                    "Name the pages: --make-cases --page TITLE [--page TITLE ...]",
                    file=out,
                )
                return 2
            try:
                return make_cases(store, pages, path, out, reply)
            except (OSError, EvalError) as exc:
                print(f"Can't read the cases: {exc}", file=out)
                return 2
        try:
            cases = load(path)
        except FileNotFoundError:
            print(
                f"No cases at {path}: write them with gos eval wiki --make-cases --page TITLE "
                "(docs/runbooks/wiki.md E).",
                file=out,
            )
            return 2
        except (OSError, EvalError) as exc:
            print(f"Can't read the cases: {exc}", file=out)
            return 2
        if limit is not None:
            cases = cases[:limit]
        if fake:
            from gregtec.agents.tools import build_registry
            from gregtec.wiki.cache import WikiCache

            registry = build_registry(
                access.db_path(cfg.gos_home),
                wiki=WikiCache(access.db_path(cfg.gos_home)),
            )
            return run(WikiOracle(registry), cases, store, out, fake=True)
        if unchecked := [c.id for c in cases if stale(store, c)]:
            print(
                f"Not run: {', '.join(unchecked)} can't be checked on this cache (a page gone "
                "or changed): re-confirm them with --make-cases. Nothing was spent.",
                file=out,
            )
            return 2
        worst = worst_case_usd(cfg, len(cases))
        print(
            f"# {len(cases)} deep-lane tasks at ${cfg.task_max_usd:.2f} "
            f"each at most: worst case ${worst:.2f}; the run stops if it gets there.",
            file=out,
        )
        if why := _refusal(cfg, worst):
            print(f"Not run: {why}.", file=out)
            return 1
        if not yes and reply("Type yes to spend it: ").strip().casefold() != "yes":
            print("Not run.", file=out)
            return 1
        close: Callable[[], None] | None = None
        if answerer is None:
            answerer, close = deep_answerer(cfg)
        try:
            return run(answerer, cases, store, out, fake=False, stop_usd=worst)
        finally:
            if close is not None:
                close()
    finally:
        store.close()
