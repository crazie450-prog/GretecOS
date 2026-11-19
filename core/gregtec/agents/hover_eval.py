"""``gos eval hover`` (WO-8.4, plan 9's 10 hover-item cases, D362): a question about what Clayton
points at in game, answered through the asker with GregtecLink's report of the item.

Each row of ``tests/evals/hover_items.tsv`` (``id kind target item meta nbt question expect
lab``) is one hovered item exactly as the client module reports it to the core
(docs/contracts/gregteclink-client-v1.md): ``target`` ``item`` (hovered in a GUI or NEI) or
``block`` (looked at; ``item`` is then its pick item), the registry name, the meta and the NBT
in the export's typing (a JSON object, or empty). Never a numeric id. ``question`` is what he
says or types while pointing; ``expect`` what a right answer must do; ``lab`` how he points at it
in his Lab. The case holds no game number and no id: everything the answer is scored against is
read from the DB in use at run time, by stable keys, so the same file runs on the client
harness's DB and on his full pack's:

- **The item** (:func:`resolve`): the report's DB keys as "what's this?" tries them
  (:func:`gregtec.gamelink.whatsthis.keys_for`); the exact NBT variant when the case has NBT. A
  case whose item (or NBT variant) the DB lacks is reported ``not in this DB`` and fails the run
  (never passed silently); the real run checks every case first and spends nothing then.
- **The rows** (:class:`gregtec.agents.recipe_eval.Ground`, the recipe eval's own SQL): the
  recipes that make the item and those that take it (for a variant also the plain item's, which
  "what's this?" may name, D286), and the machine (``mte:``) when the DB's machine table has it.

The scorer (:func:`score`) is deterministic and fails a reply that: asks back (the hover says
which item); is about no item or another one than the hovered variant (the reply's resolved
item, which the card shows); doesn't name the item by the DB's name (an answer that ignores
the hover context); cites an id the DB doesn't have, or a row that neither makes nor takes the
item; states a number no cited row backs (counts of the item's recipes allowed, as "what's
this?" gives them); or misses its ``expect``: ``name`` (nothing more), ``make``/``use`` (cites
a row that makes or takes the exact item, an unflagged one when there is one, D172), ``tier``
(cites one of its lowest-tier rows that make it and names that row's tier, no other),
``machine`` (cites the machine's ``mte:``), ``none`` (no recipe makes or takes it in this DB;
the reply says so, cites no recipe and states no number).

``--fake`` (CI's run, free) answers with :class:`HoverOracle`, a fake fast-lane model that knows
nothing of the case: it reads only the pre-retrieved tool results, so it can only answer right
when the core looked up the hovered item (WO-8.4 finding F1, D362). The real run (the stored
key, ``llm_usage`` task ``eval:hover``) prints its worst-case cost first, waits for "yes" (or
``--yes``), refuses while the AI is paused for credits or switched off, or when today's spend
plus that estimate would pass ``[budget] day_max_usd`` (D334), and stops if the metered cost
reaches the estimate. Deep-lane handoffs are only recorded (a hover question is a lookup).
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from gregtec import config
from gregtec.agents import fast_lane, recipe_eval, recipe_oracle
from gregtec.agents.ask import Agents, Asker, Reply, setup
from gregtec.agents.fast_lane import MAX_ROUNDS, MAX_TOKENS, FastLane
from gregtec.agents.recipe_eval import MACHINE, MAKE, USE, Ground, Step, Truth
from gregtec.agents.tools.items import WILDCARD
from gregtec.agents.tools.recipes import TIERS
from gregtec.db import access
from gregtec.gamelink import whatsthis
from gregtec.llm import fake
from gregtec.llm.fake import FakeClient, Turn
from gregtec.llm.prices import PRICES

REPO_ROOT = Path(__file__).resolve().parents[3]
CASES = REPO_ROOT / "tests" / "evals" / "hover_items.tsv"
COLUMNS = ("id", "kind", "target", "item", "meta", "nbt", "question", "expect", "lab")
KINDS = (
    "whats_this",
    "make",
    "uses",
    "tier",
    "ambiguous",
    "nbt_variant",
    "nbt_cell",
    "none",
    "addon",
    "block",
)
TARGETS = ("item", "block")
EXPECTS = ("name", "make", "use", "tier", "machine", "none")
TASK = "eval:hover"
NOT_IN_DB = "not in this DB"
CONVERSATION_TOKENS = 12_000  # a generous turn of pre-retrieved rows and tool results


class EvalError(ValueError):
    """The case file is malformed."""


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    target: str  # item (hovered) or block (looked at)
    item: dict[str, Any]  # {"item": registry name, "meta": int, "nbt"?: object}
    question: str
    expect: str
    lab: str

    def message(self) -> dict[str, Any]:
        """The ``target`` message GregtecLink's client module sends for this case."""
        return {"type": "target", "kind": self.target, "item": dict(self.item)}


def _case(cells: Mapping[str, str], where: str) -> Case:
    if cells["kind"] not in KINDS:
        raise EvalError(f"{where}: kind must be one of {', '.join(KINDS)}")
    if cells["target"] not in TARGETS:
        raise EvalError(f"{where}: target must be item or block")
    if cells["expect"] not in EXPECTS:
        raise EvalError(f"{where}: expect must be one of {', '.join(EXPECTS)}")
    if not cells["question"].strip() or not cells["lab"].strip():
        raise EvalError(f"{where}: question and lab can't be empty")
    try:
        meta = int(cells["meta"])
    except ValueError:
        raise EvalError(f"{where}: meta must be a whole number") from None
    item: dict[str, Any] = {"item": cells["item"].strip(), "meta": meta}
    if cells["nbt"].strip():
        try:
            nbt = json.loads(cells["nbt"])
        except ValueError as exc:
            raise EvalError(f"{where}: nbt isn't JSON: {exc}") from None
        if not isinstance(nbt, dict) or not nbt:
            raise EvalError(f"{where}: nbt must be a non-empty JSON object")
        item["nbt"] = nbt
    if whatsthis.item_of({"item": item}) is None:
        raise EvalError(
            f"{where}: {item['item']}@{meta} isn't a registry name and meta"
        )
    return Case(
        cells["id"].strip(),
        cells["kind"],
        cells["target"],
        item,
        " ".join(cells["question"].split()),
        cells["expect"],
        cells["lab"].strip(),
    )


def load(path: Path = CASES) -> list[Case]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, None)
        if header is None or tuple(header) != COLUMNS:
            raise EvalError(f"{path}: the header must be {' '.join(COLUMNS)}")
        cases = []
        for n, row in enumerate(reader, start=2):
            if not row or not "".join(row).strip():
                continue
            if len(row) != len(COLUMNS):
                raise EvalError(f"{path}:{n}: {len(row)} cells, not {len(COLUMNS)}")
            cases.append(_case(dict(zip(COLUMNS, row, strict=True)), f"{path}:{n}"))
    if len({c.id for c in cases}) != len(cases):
        raise EvalError(f"{path}: ids repeat")
    return cases


# --- the item and its rows, from the DB in use ------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """The DB's item for a case's report."""

    key: str  # registry@meta, with #<full NBT hash> for a variant
    name: str  # the DB's English name
    plain: str | None  # registry@meta of a variant's plain item (None: not a variant)
    mte: str | None  # mte:<id> when the DB's machine table has the item
    mod: str = ""  # the DB's mod id (a name with digits, "IC2", isn't a number)


def resolve(ground: Ground, case: Case) -> Resolved | str:
    """The case's item in ``ground``'s DB, or why it isn't there (:data:`NOT_IN_DB`)."""
    keys = whatsthis.keys_for(case.item)
    if "nbt" in case.item:
        keys = keys[
            :1
        ]  # an NBT case is about that variant: the plain item doesn't stand in
    for key in keys:
        reg, _, rest = key.partition("@")
        meta, _, nbt = rest.partition("#")
        row = ground.conn.execute(
            "SELECT id, name_en, mod_id FROM item WHERE registry_name = ? AND meta = ? AND "
            "nbt_hash = ?",
            (reg, int(meta), nbt),
        ).fetchone()
        if row is None:
            continue
        mte = ground.conn.execute(
            "SELECT m.mte_id FROM machine AS m JOIN item AS i ON i.id = m.item_id "
            "WHERE i.registry_name = ? AND i.meta = ? AND i.nbt_hash = ''",
            (reg, int(meta)),
        ).fetchone()
        return Resolved(
            key,
            str(row["name_en"] or key),
            f"{reg}@{meta}" if nbt else None,
            None if mte is None else f"mte:{mte[0]}",
            str(row["mod_id"] or ""),
        )
    what = f"{case.item['item']}@{case.item['meta']}"
    return f"{NOT_IN_DB}: {what}{' with this NBT' if 'nbt' in case.item else ''}"


@dataclass
class Rows:
    """What the DB holds about a resolved item: the truths the scorer reads."""

    item: Resolved
    make: Truth
    use: Truth
    also: list[Truth] = field(
        default_factory=list
    )  # a variant's plain item's make and use
    machine: Truth | None = None

    def citable(self) -> set[str]:
        """Every id an answer about this item may cite."""
        out = set(self.make.all) | set(self.use.all)
        for t in self.also:
            out |= set(t.all)
        if self.machine is not None:
            out |= set(self.machine.rows)
        return out

    def counts(self) -> set[str]:
        """The counts an answer may give: how many recipes make it, how many take it (a
        variant's counted with its plain and any-meta rows', as the recipe tools match it)."""
        out: set[str] = set()
        for t in (self.make, self.use, *self.also):
            out |= t.counts
            out.add(recipe_eval.value(len(t.all)))
        for op in (MAKE, USE):
            refs = set().union(
                *(t.all for t in (self.make, self.use, *self.also) if t.step.op == op)
            )
            out.add(recipe_eval.value(len(refs)))
        return out


def rows(ground: Ground, item: Resolved) -> Rows:
    also = []
    if item.plain is not None:
        also = [
            ground.truth(Step(MAKE, item.plain)),
            ground.truth(Step(USE, item.plain)),
        ]
        reg, _, meta = item.plain.partition("@")
        if int(meta) != WILDCARD:
            wild = f"{reg}@{WILDCARD}"
            also += [ground.truth(Step(MAKE, wild)), ground.truth(Step(USE, wild))]
    return Rows(
        item,
        ground.truth(Step(MAKE, item.key)),
        ground.truth(Step(USE, item.key)),
        also,
        ground.truth(Step(MACHINE, item.mte)) if item.mte else None,
    )


# --- scoring ----------------------------------------------------------------------------


def same_item(said: str, key: str) -> bool:
    """Whether ``said`` (a key as the tools print it: an NBT hash cut to 8 characters) is
    ``key``: the same registry name and meta, and the same NBT variant or none."""
    a, _, a_nbt = said.partition("#")
    b, _, b_nbt = key.partition("#")
    if a != b or bool(a_nbt) != bool(b_nbt):
        return False
    return a_nbt == b_nbt or (
        min(len(a_nbt), len(b_nbt)) >= 8
        and (a_nbt.startswith(b_nbt) or b_nbt.startswith(a_nbt))
    )


def _pick(t: Truth, cited: Sequence[str]) -> tuple[list[recipe_eval.Row], str]:
    """The cited rows of ``t`` and, when none, why (the recipe eval's flag rule, D172)."""
    hits = [t.rows[c] for c in cited if c in t.rows]
    if hits:
        return hits, ""
    if not t.rows:
        return [], f"the DB has no row for {t.step.text()}"
    if flagged := [c for c in cited if c in t.flagged]:
        return [], (
            f"cites only flagged rows of {t.step.text()} ({flagged[0]}); "
            f"{len(t.rows)} have no flag"
        )
    return [], f"cites no row of {t.step.text()} ({len(t.rows)} in the DB)"


def score(
    case: Case,
    held: Rows,
    text: str,
    *,
    target_key: str | None,
    asked: bool,
    ground: Ground,
) -> list[str]:
    """Why ``text`` (the reply's words) fails ``case`` (empty: it passes). ``target_key``: the
    item the reply resolved (``Reply.target``'s key, what the card shows), None for none."""
    item = held.item
    if asked:
        return ["asked back: the hover already says which item"]
    if target_key is None:
        return ["the reply is about no item: the hover context wasn't used"]
    if not same_item(target_key, item.key):
        return [f"answered about {target_key}, not the hovered {item.key}"]
    why: list[str] = []
    if item.name.casefold() not in text.casefold():
        why.append(f"doesn't name {item.name!r}")
    cited = list(dict.fromkeys(fast_lane.refs(text)))
    real = {c: r for c in cited if (r := ground.row(c)) is not None}
    if unknown := [c for c in cited if c not in real and c.startswith(("r:", "mte:"))]:
        why.append(f"cites ids the DB doesn't have: {', '.join(unknown[:3])}")
    citable = held.citable()
    if stray := [c for c in real if c not in citable]:
        why.append(
            f"cites {', '.join(stray[:3])}, which neither makes nor takes {item.key}"
        )
    names = {item.name, item.mod}
    for r in real.values():
        names |= r.names
    said = recipe_eval._Answer(text, names)
    if case.expect == "none":
        found = len(held.make.all) + len(held.use.all)
        if found:
            why.append(
                f"not a none case on this DB: {len(held.make.all)} rows make "
                f"{item.key} and {len(held.use.all)} take it"
            )
        if any(c.startswith("r:") for c in cited):
            why.append("cites recipes where none exists")
        if said.stated:
            why.append(
                f"numbers where no recipe exists: {', '.join(sorted(said.stated))}"
            )
        if not recipe_eval.NONE_SAID.search(said.bare):
            why.append("doesn't say that no recipe in the DB makes it")
        return why
    if case.expect in ("make", "tier"):
        hits, miss = _pick(held.make, cited)
        if miss:
            why.append(miss)
        elif case.expect == "tier":
            low = [h for h in hits if h.cite in held.make.simplest()]
            tiers = set().union(*(h.fields.get("tier", set()) for h in low))
            stated = recipe_eval._tiers(said.masked)
            if not low:
                why.append(f"cites none of {item.key}'s lowest-tier rows")
            elif not tiers:  # made without a voltage tier (crafting, smelting)
                if stated:
                    why.append(
                        f"names {', '.join(sorted(stated))}, but its lowest-tier "
                        "recipe has no tier"
                    )
            elif not stated & tiers:
                why.append(f"doesn't name the tier ({', '.join(sorted(tiers))})")
            elif other := stated - tiers:
                why.append(f"names a tier no cited row has: {', '.join(sorted(other))}")
    elif case.expect == "use":
        _, miss = _pick(held.use, cited)
        if miss:
            why.append(miss)
    elif case.expect == "machine":
        if item.mte is None:
            why.append(f"the DB's machine table has no {item.key}")
        elif item.mte not in cited:
            why.append(f"doesn't cite {item.mte}")
    counts = held.counts()
    uncited = sorted(
        {
            m.value
            for m in said.said
            if m.value
            not in (
                counts
                if m.role == "count"
                else recipe_eval._role_values(real.values(), m.role)
            )
        },
        key=lambda n: (len(n), n),
    )
    if uncited:
        why.append(f"uncited numbers: {', '.join(uncited[:5])}")
    return why


# --- the fake model ---------------------------------------------------------------------


def _first(
    rows: Sequence[Mapping[str, str]], *, low: bool = False
) -> Mapping[str, str]:
    """The first row without a flag, else the first (D172); ``low``: of those, the first at
    the lowest tier (a row with no tier is lowest, as plan 7.3's simplest recipe)."""
    plain = [r for r in rows if not recipe_oracle.flagged(r)] or list(rows)
    if not low:
        return plain[0]

    def rank(r: Mapping[str, str]) -> int:
        tier = r.get("tier", "")
        return TIERS.index(tier) if tier in TIERS else -1

    return min(plain, key=rank)


@dataclass
class HoverOracle(FakeClient):
    """``--fake``'s fast-lane model: answers from the pre-retrieved results alone, as a careful
    model would, knowing nothing of the eval case. Without the hovered item among them (the
    core didn't look it up) it can only say it doesn't know which item is meant."""

    def turn(self, request: dict[str, Any]) -> Turn:
        messages = request.get("messages") or []
        last = fake._texts(messages[-1].get("content", "")) if messages else []
        if any(t.startswith("Gregtec OS didn't send") for t in last):
            return Turn("I don't know that number; I can look it up.")
        said, seen = recipe_oracle.fast_seen(messages)
        return Turn(answer(said, seen))


def answer(said: str, seen: Sequence[recipe_oracle.Seen]) -> str:
    """The oracle's answer to ``said`` from the results ``seen``."""
    resolved = next(
        (s for s in seen if s.tool == "item_resolve" and s.parsed.rows), None
    )
    if resolved is None or resolved.parsed.head.get("ambiguous") == "1":
        return "I can't tell which item you mean; hover over it and ask again."
    top = resolved.parsed.rows[0]
    key, name = top["key"], top.get("name") or top["key"]

    def about(tool: str) -> recipe_oracle.Seen | None:
        return next(
            (
                s
                for s in seen
                if s.tool == tool
                and not s.text.startswith("error")
                and recipe_oracle.head_key(s.parsed) == key
            ),
            None,
        )

    found, uses, info = (
        about("recipe_find"),
        about("recipe_uses"),
        about("machine_info"),
    )
    words = said.casefold()
    if "tier" in words and found is not None:
        if not found.parsed.rows:
            return f"No recipe in the DB makes the {name}."
        row = _first(found.parsed.rows, low=True)
        if row.get("tier"):
            return (
                f"The {name} is first made at {row['tier']}: the {row['machine']} "
                f"({row['ref']}) makes it at {row['tier']}."
            )
        return f"The {name} needs no voltage tier: the {row['machine']} ({row['ref']}) makes it."
    if uses is not None and ("use" in words or found is None):
        if not uses.parsed.rows:
            return f"No recipe in the DB takes the {name}."
        row = _first(uses.parsed.rows)
        return f"The {name} goes into the {row['machine']} ({row['ref']})."
    if found is not None:
        if not found.parsed.rows:
            return f"No recipe in the DB makes the {name}."
        row = _first(found.parsed.rows)
        return f"The {name} is made in the {row['machine']} ({row['ref']})."
    if info is not None:
        return f"The {name} is a GT machine ({recipe_oracle.head_key(info.parsed)})."
    return f"That's the {name}."


# --- the run ----------------------------------------------------------------------------


@dataclass
class Pointing:
    """The game link as the asker sees it (:class:`whatsthis.Pointer`): one case's report."""

    target: dict[str, Any] | None
    linked: bool = True

    def current_target(self) -> tuple[dict[str, Any] | None, bool]:
        return self.target, self.linked


@dataclass
class Result:
    case: Case
    reply: Reply | None
    passed: bool
    why: list[str]


def unresolved(ground: Ground, cases: Sequence[Case]) -> dict[str, str]:
    """The cases whose item (or NBT variant) the DB in use lacks, with why."""
    return {c.id: got for c in cases if isinstance(got := resolve(ground, c), str)}


def run(
    asker_for: Callable[[], Asker],
    cases: Sequence[Case],
    ground: Ground,
    out: TextIO,
    *,
    fake_model: bool,
    stop_usd: float | None = None,
) -> tuple[int, list[Result]]:
    """Ask every case with its report as the game link's target; score; summarise. A case the
    DB lacks is reported :data:`NOT_IN_DB` and fails the run. With ``stop_usd``, the cases left
    once the metered cost reaches it are not asked and the run fails."""
    print(f"# DB run {ground.run_key}", file=out)
    print("id\tkind\texpect\tlane\tpass\tusd\twhy\ttext", file=out)
    results: list[Result] = []
    spent = 0.0
    for case in cases:
        if stop_usd is not None and spent >= stop_usd:
            print(
                f"# stopped: ${spent:.4f} spent reached the stated ${stop_usd:.2f}; "
                f"{len(cases) - len(results)} cases not asked",
                file=out,
            )
            break
        item = resolve(ground, case)
        if isinstance(item, str):
            results.append(Result(case, None, False, [item]))
            print(f"{case.id}\t{case.kind}\t{case.expect}\t-\t0\t0\t{item}\t", file=out)
            continue
        asker = asker_for()
        asker.game = Pointing(case.message())
        reply = asker.ask(case.question, task=TASK)
        spent += reply.usd
        why = score(
            case,
            rows(ground, item),
            reply.text,
            target_key=(reply.target or {}).get("key"),
            asked=recipe_eval.asks(reply, reply.text),
            ground=ground,
        )
        if reply.handoff is not None:
            why.insert(0, "handed to the deep lane (a hover question is a lookup)")
        results.append(Result(case, reply, not why, why))
        text = " ".join(reply.text.split())[:200]
        print(
            f"{case.id}\t{case.kind}\t{case.expect}\t{reply.lane}\t{int(not why)}\t"
            f"{reply.usd:.6f}\t{'; '.join(why)}\t{text}",
            file=out,
        )
    passed = sum(r.passed for r in results)
    missing = sum(1 for r in results if r.reply is None)
    usd = sum(r.reply.usd for r in results if r.reply is not None)
    print(file=out)
    print(f"passed: {passed} of {len(cases)} (target: all)", file=out)
    if missing:
        print(f"{NOT_IN_DB}: {missing} (each fails; see the rows)", file=out)
    print(
        f"cost: ${usd:.6f}{' (simulated: the fake model)' if fake_model else ''}",
        file=out,
    )
    ok = passed == len(cases) and len(results) == len(cases)
    print("Result: " + ("PASS" if ok else "FAIL"), file=out)
    return (0 if ok else 1), results


def worst_case_usd(model: str, lane: FastLane, cases: int) -> float:
    """An estimate of the most a real run costs, not a bound: every case takes the lane's
    every call (:data:`MAX_ROUNDS` and the number check's retry), none reads the cache (the
    prefix written each time), each with a long conversation and the most output the lane
    allows. The prefix is counted as characters/3, so :func:`run` also stops at it."""
    price = PRICES[model]
    request = lane._request([], tools_off=False)
    prefix = (
        len(json.dumps(request["system"])) + len(json.dumps(request["tools"]))
    ) // 3
    per_call = (
        (prefix + 1) * price.cache_write
        + CONVERSATION_TOKENS * price.input
        + MAX_TOKENS * price.output
    ) / 1_000_000
    return per_call * (MAX_ROUNDS + 1) * cases


def fake_asker_for(
    cfg: config.Config,
) -> tuple[Callable[[], Asker], Callable[[], None]]:
    """Askers whose fast lane is :class:`HoverOracle` (no key, no cost, nothing recorded)."""
    agents = setup(cfg, fake=True)
    lane = FastLane(HoverOracle(), agents.registry, meter=None, vault=agents.vault)
    return (lambda: Asker(agents.registry, agents.deep, lane)), agents.close


def _caps(cfg: config.Config, agents: Agents, worst: float) -> str | None:
    """Why the paid run may not start now (D48's pause, his switch, D334's day cap)."""
    from gregtec.agents.no_ai import AiMode, CreditWatch
    from gregtec.ops.costs import CostWatch

    if agents.meter is None or agents.store is None:
        return "no DB to meter the run in (gos db build first)"
    mode = AiMode(agents.store)
    CreditWatch(agents.meter, mode).check()
    if reasons := mode.reasons():
        return f"the AI is off ({', '.join(reasons)}): nothing is spent"
    day = CostWatch.of(agents.store, cfg).caps()["day"]
    if day["spent_usd"] + worst > day["cap_usd"]:
        return (
            f"today's spend ${day['spent_usd']:.2f} plus this run's ${worst:.2f} would pass "
            f"the day cap ${day['cap_usd']:.2f} ([budget] day_max_usd)"
        )
    return None


def main(
    config_path: Path | None,
    cases_path: Path | None,
    fake_model: bool,
    yes: bool,
    limit: int | None,
    out: TextIO,
    answer_in: Callable[[], str] = lambda: sys.stdin.readline(),
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        cases = load(cases_path or CASES)
    except (OSError, EvalError) as exc:
        print(f"Can't read the cases: {exc}", file=out)
        return 2
    if limit is not None:
        cases = cases[:limit]
    try:
        ground = Ground(access.db_path(cfg.gos_home))
    except (OSError, LookupError, sqlite3.Error, access.SchemaError) as exc:
        print(f"Not run: {exc}", file=out)
        return 1
    try:
        if fake_model:
            asker_for, close = fake_asker_for(cfg)
            try:
                return run(asker_for, cases, ground, out, fake_model=True)[0]
            finally:
                close()
        if missing := unresolved(ground, cases):  # before any cost is stated or spent
            for cid, why in missing.items():
                print(f"{cid}: {why}", file=out)
            print("Not run: every case must resolve in this DB first.", file=out)
            return 2
        agents = setup(cfg, deep_lane=False)
        try:
            # the estimate from the prompt the real lane sends (same rules, tools, persona)
            sized = FastLane(HoverOracle(), agents.registry, vault=agents.vault)
            if agents.personas is not None:
                sized.persona = agents.personas.block
            worst = worst_case_usd(sized.model, sized, len(cases))
            print(
                f"# {len(cases)} cases on {sized.model}: worst case ${worst:.2f}, an "
                "estimate (every call, no cache, the most output); the run stops if it "
                "gets there.",
                file=out,
            )
            if why := _caps(cfg, agents, worst):
                print(f"Not run: {why}.", file=out)
                return 1
            if not yes:
                print("Type yes to spend it: ", end="", file=out, flush=True)
                if answer_in().strip().casefold() != "yes":
                    print("Not run.", file=out)
                    return 1
            lane = agents.lane()
            if lane is None:
                print(
                    "Not run: no Anthropic key is stored in this account (uv run gos key "
                    "set anthropic), or use --fake.",
                    file=out,
                )
                return 1
            return run(
                lambda: Asker(agents.registry, agents.deep, lane),
                cases,
                ground,
                out,
                fake_model=False,
                stop_usd=worst,
            )[0]
        finally:
            agents.close()
    finally:
        ground.close()
