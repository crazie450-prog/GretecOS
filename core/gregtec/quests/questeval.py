"""``gos eval quest-guide`` (WO-6.4 acceptance, D270): 20 questions on the quest guide's pack.

``tests/evals/quest_guide.tsv`` (``id kind level expect target words question``) holds the
questions. **Truth is references into the quest DB**, never text: ``expect`` lists what the
answer must name: ``q<n>`` (the pack's quest n, cited ``q:<id>``), an item or fluid key (its
key or its DB name in the answer) and ``lane=instant`` (answered with no model call);
``level`` is the spoiler level the question asks for and ``target`` the quests it may show at
that level; ``words`` is how the question names its quest, item or line (only the fake model
reads it). The pack (``tests/evals/quest_pack.py``: self-authored, never pack data) is built
into a temporary GOS_HOME for each run, so the eval never touches a real DB or vault.

Each row is asked through :class:`gregtec.agents.ask.Asker` (router, pre-retrieval, the fast
lane and its tools, as ``POST /api/ask`` asks it) with a fresh conversation, and passes when:

- it was answered (fast lane, or an instant command where ``lane=instant``), its numbers
  passed the fast lane's check, and every ``expect`` reference is in the answer;
- **no spoiler leaked** (:func:`leaks`): for every quest past the frontier, nothing above the
  level the row allows for it (none but its ``target`` quests, at its ``level``) is in the
  answer or the reply's card: at level 0 its name when the book doesn't show it, what its
  tasks ask for, its rewards and its description; level 1 allows its name and tasks; level 2
  everything. Words the question itself says (a name he typed) don't count.

``--fake`` runs :class:`QuestOracle`, a fake model that follows the skill's lookups and then
**asks for full spoilers every time** (``quest_get`` with ``spoil: 2``) and repeats everything
the tools return. So a fake pass shows the code's guard, not a model's manners: whatever a
model asks for, the tools show only what Clayton asked for. The real run uses the stored key
(the fast lane's Haiku), records its calls in ``llm_usage`` with task ``eval:quest-guide``, and
prints the cost; the fake run prints the same estimate from its simulated token counts.
"""

from __future__ import annotations

import csv
import html
import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO

from gregtec import config
from gregtec.agents.ask import Asker, Reply
from gregtec.agents.deep_lane import HandoffRecorder
from gregtec.agents.fast_lane import FastLane, refs
from gregtec.agents.recipe_oracle import fast_seen
from gregtec.agents.tools.catalog import default_registry
from gregtec.agents.tools.tsv import parse
from gregtec.db import access
from gregtec.db import quests as quest_db
from gregtec.ingest import quests as bq
from gregtec.llm import fake
from gregtec.llm.fake import FakeClient, Turn
from gregtec.quests import guide, spoilers
from gregtec.vault.io import VaultIO

REPO_ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = REPO_ROOT / "tests" / "evals" / "quest_guide.tsv"
PACK = REPO_ROOT / "tests" / "evals" / "quest_pack.py"
COLUMNS = ("id", "kind", "level", "expect", "target", "words", "question")
KINDS = ("frontier", "quest", "requires", "unlocks", "item", "line", "tier")
QUEST_KINDS = ("quest", "requires", "unlocks")
TASK = "eval:quest-guide"
INSTANT = "lane=instant"
_QN = re.compile(r"q(\d+)")
_KEY = re.compile(r"(?:fluid:\S+|\S+@\d+)")
_NOTE_ROW = re.compile(r"^(q:[A-Za-z0-9_-]{22})\t([^\t]*)\t", re.MULTILINE)
_INDEX_ROW = re.compile(r"^[^\t\n]+\t[^\t\n]+\t(quests/qs-[^\t\n]+\.md)\t([^\t\n]*)", re.MULTILINE)


class EvalError(ValueError):
    """The question file is malformed."""


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    level: int
    expect: tuple[str, ...]
    target: tuple[str, ...]
    words: str
    question: str


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
            qid, kind, level, expect, target, words, question = cells
            if kind not in KINDS or level not in ("0", "1", "2") or not question.strip():
                raise EvalError(f"{path}:{n}: bad kind, level or question")
            tokens = tuple(expect.split())
            for t in tokens:
                if not (_QN.fullmatch(t) or _KEY.fullmatch(t) or t == INSTANT):
                    raise EvalError(f"{path}:{n}: {t!r} is not q<n>, a key or {INSTANT}")
            targets = tuple(target.split())
            if any(not _QN.fullmatch(t) for t in targets):
                raise EvalError(f"{path}:{n}: target must be q<n> tokens")
            if kind != "tier" and kind != "frontier" and not words.strip():
                raise EvalError(f"{path}:{n}: {kind} rows name their words")
            out.append(Question(qid, kind, int(level), tokens, targets, words, question))
    if len({q.id for q in out}) != len(out):
        raise EvalError(f"{path}: ids repeat")
    return out


def pack() -> ModuleType:
    """tests/evals/quest_pack.py, the eval's self-authored pack."""
    name = "quest_pack"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PACK)
    if spec is None or spec.loader is None:
        raise EvalError(f"can't load {PACK}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def ref(token: str) -> str:
    """``q5`` -> ``q:<the pack's id of quest 5>``."""
    m = _QN.fullmatch(token)
    if m is None:
        raise EvalError(f"{token!r} is not q<n>")
    return f"q:{bq.encode_id(0, int(m[1]))}"


# --- spoilers --------------------------------------------------------------------------------


@dataclass
class Spoiler:
    """What a quest past the frontier must not show above each level."""

    quest: str  # q:<id>
    name: str | None  # its name, when the book doesn't show it (else None: not a spoiler)
    description: str | None
    tasks: list[str] = field(default_factory=list)  # item keys and names its tasks ask for
    rewards: list[str] = field(default_factory=list)

    def above(self, level: int) -> list[str]:
        """The strings that are spoilers above ``level``."""
        out: list[str] = []
        if level < spoilers.PARTIAL:
            out += ([self.name] if self.name else []) + self.tasks
        if level < spoilers.FULL:
            out += ([self.description] if self.description else []) + self.rewards
        return [s for s in out if s]

    def shown_at(self, level: int) -> list[str]:
        """The strings that level shows (allowed for a target quest)."""
        every = self.above(-1)
        hidden = set(self.above(level))
        return [s for s in every if s not in hidden]


def spoiler_fields(conn: sqlite3.Connection, player: str | None = None) -> dict[str, Spoiler]:
    """Every locked quest's spoiler fields. An item that a quest in reach also asks for or
    gives is no spoiler (the frontier shows it), so it isn't listed."""
    if player is None:
        player = quest_db.tracked_player(conn)
    states = {
        q: (s or bq.LOCKED, bool(shown))
        for q, s, shown in conn.execute(
            "SELECT q.id, s.state, s.shown FROM quest AS q LEFT JOIN quest_state AS s"
            " ON s.quest_id = q.id AND s.player_uuid = ?",
            (player,),
        )
    }
    parts: dict[str, list[tuple[str, str, str]]] = {}
    for qid, part, reg, meta, fluid, name in conn.execute(
        "SELECT qi.quest_id, qi.part, qi.registry_name, qi.meta, qi.fluid,"
        " coalesce(i.name_en, f.name_en) FROM quest_item AS qi"
        " LEFT JOIN item AS i ON i.id = qi.item_id LEFT JOIN fluid AS f ON f.id = qi.fluid_id"
    ):
        key = f"fluid:{fluid}" if fluid else (f"{reg}@{meta}" if reg else "")
        parts.setdefault(qid, []).append((part, key, name or ""))
    seen = {
        s
        for q, (state, _) in states.items()
        if state != bq.LOCKED
        for _, key, name in parts.get(q, [])
        for s in (key, name)
        if s
    }
    out = {}
    for qid, name, descr in conn.execute("SELECT id, name, descr FROM quest"):
        state, shown = states.get(qid, (bq.LOCKED, False))
        if state != bq.LOCKED:
            continue
        sp = Spoiler(f"q:{qid}", None if shown else name, descr or None)
        for part, key, iname in parts.get(qid, []):
            got = [s for s in (key, iname) if s and s not in seen]
            (sp.tasks if part == "task" else sp.rewards).extend(got)
        out[sp.quest] = sp
    return out


def _strip(text: str, allowed: Sequence[str]) -> str:
    out = text.casefold()
    for s in sorted({a.casefold() for a in allowed if a}, key=len, reverse=True):
        out = out.replace(s, " ")
    return out


def leaks(
    text: str,
    fields: Mapping[str, Spoiler],
    allow: Mapping[str, int] | None = None,
    said: Sequence[str] = (),
) -> list[str]:
    """The spoilers in ``text``: for each quest past the frontier, what lies above the level
    ``allow`` gives it (default 0). What ``said`` (his own words) holds doesn't count."""
    allow = allow or {}
    allowed = [s for s in said if s]
    for q, level in allow.items():
        if q in fields:
            allowed += fields[q].shown_at(level)
    plain = _strip(" ".join(text.split()), allowed)
    out = []
    for q, sp in fields.items():
        for s in sp.above(allow.get(q, spoilers.NUDGE)):
            needle = " ".join(s.casefold().split())
            if needle and needle in plain:
                out.append(f"{q}: {s}")
    return out


# --- scoring ---------------------------------------------------------------------------------


def _name_of(conn: sqlite3.Connection, key: str) -> str | None:
    if key.startswith("fluid:"):
        row = conn.execute(
            "SELECT name_en FROM fluid WHERE name = ?", (key.removeprefix("fluid:"),)
        ).fetchone()
    else:
        reg, _, meta = key.rpartition("@")
        row = conn.execute(
            "SELECT name_en FROM item WHERE registry_name = ? AND meta = ?", (reg, int(meta))
        ).fetchone()
    return row[0] if row else None


def score(
    q: Question,
    reply: Reply,
    conn: sqlite3.Connection,
    fields: Mapping[str, Spoiler],
    goals: Sequence[str] = (),
) -> tuple[bool, str]:
    """(passed, why not). ``goals``: his goals' words, which the frontier's reasons quote."""
    want_instant = INSTANT in q.expect
    if reply.lane != ("instant" if want_instant else "fast"):
        return False, f"lane {reply.lane}" + (f": {reply.error}" if reply.error else "")
    if not reply.checked:
        return False, "numbers refused: " + "; ".join(reply.problems)
    text = reply.text
    cited = set(refs(text))
    missing = []
    for t in q.expect:
        if t == INSTANT:
            continue
        if _QN.fullmatch(t):
            if ref(t) not in cited:
                missing.append(t)
            continue
        name = _name_of(conn, t)
        if t not in text and not (name and name.casefold() in text.casefold()):
            missing.append(t)
    if missing:
        return False, "doesn't name " + ", ".join(missing)
    allow = {ref(t): q.level for t in q.target}
    # His own words: the question, the item it resolved to, and his goals (the rank's reasons
    # quote them).
    said = [q.question, q.words, *goals]
    if reply.target:
        said += [reply.target.get("key", ""), reply.target.get("name", "")]
    leaked = leaks(text, fields, allow, said)
    card_text = json.dumps(reply.card or {}, ensure_ascii=False)
    leaked += [f"card {x}" for x in leaks(card_text, fields, allow, said)]
    if leaked:
        return False, "spoiled " + "; ".join(leaked[:3])
    return True, ""


# --- the fake model -----------------------------------------------------------------------


def _system(request: Mapping[str, Any]) -> str:
    blocks = request.get("system") or []
    return "\n".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict))


def decide(q: Question, seen: Sequence[Any], system: str) -> list[dict[str, Any]]:
    """The next tool calls the skill names for ``q``, given the results ``seen``; none: answer."""
    tools = {s.tool for s in seen}
    if q.kind == "frontier":
        return [] if "quest_frontier" in tools else [{"name": "quest_frontier", "input": {}}]
    if q.kind == "item":
        if "quest_for_item" in tools:
            return []
        return [{"name": "quest_for_item", "input": {"item": q.words}}]
    if q.kind == "line":
        if "note_read" in tools:
            return []
        said = q.words.casefold()
        for m in _INDEX_ROW.finditer(html.unescape(system)):
            if said in m[2].casefold():
                return [{"name": "note_read", "input": {"path": m[1]}}]
        found = [s for s in seen if s.tool == "vault_search"]
        if found:
            rows = parse(found[-1].text).rows
            if rows:
                return [{"name": "note_read", "input": {"path": rows[0]["path"]}}]
            return []
        return [{"name": "vault_search", "input": {"q": q.words}}]
    if "quest_get" in tools:
        return []
    found = [s for s in seen if s.tool == "quest_find"]
    if not found:
        return [{"name": "quest_find", "input": {"text": q.words}}]
    rows = parse(found[-1].text).rows
    if not rows:
        return []
    pick = next((r for r in rows if r.get("name", "").casefold() == q.words), rows[0])
    # the adversary: always the full spoiler, whatever he asked for
    return [{"name": "quest_get", "input": {"id": pick["quest"], "spoil": spoilers.FULL}}]


def compose(seen: Sequence[Any]) -> str:
    """Everything the last result says, as sentences (a model that repeats it all)."""
    if not seen:
        return "I couldn't look that up."
    last = seen[-1]
    if last.tool == "note_read":
        rows = _NOTE_ROW.findall(last.text)
        facts = dict(re.findall(r"^(line|done|total): (.+)$", last.text, re.MULTILINE))
        start = ", ".join(f"{n} ({r})" for r, n in rows) or "nothing"
        return (
            f"{facts.get('line', 'That line')}: {facts.get('done', '?')} of "
            f"{facts.get('total', '?')} done; you can start {start}."
        )
    p = parse(last.text)
    if last.tool == "quest_get":
        out = [f"{p.head.get('name', 'The quest')} ({p.head.get('quest', '')}) is "
               f"{p.head.get('state', 'unknown')}."]
        for r in p.rows:
            amount = f" x{r['amount']}" if r.get("amount") else ""
            out.append(f"{r['part']}: {r['what']}{amount} ({r['cite']}).")
        if p.head.get("description"):
            out.append(f"It says: {p.head['description']} ({p.head.get('quest', '')})")
        return " ".join(out)
    out = []
    for r in p.rows[:6]:
        bits = [f"{r.get('name', '')} ({r.get('quest', '')}) {r.get('state', '')}"]
        if r.get("part"):
            bits.append(f"{r['part']}" + (f" x{r['amount']}" if r.get("amount") else ""))
        if r.get("why"):
            bits.append(r["why"])
        out.append(", ".join(b for b in bits if b))
    return "; ".join(out) + "." if out else "No quest matches."


@dataclass
class QuestOracle(FakeClient):
    """The fast lane's model in ``--fake``: :func:`decide`, then :func:`compose`."""

    plans: Mapping[str, Question] = field(default_factory=dict)

    def turn(self, request: dict[str, Any]) -> Turn:
        messages = request.get("messages") or []
        last = fake._texts(messages[-1].get("content", "")) if messages else []
        if any(t.startswith("Gregtec OS didn't send") for t in last):
            return Turn("I don't know that; I can look it up.")
        said, seen = fast_seen(messages)
        q = self.plans.get(said)
        if q is None:
            return Turn(fake.answer([(s.tool, s.text) for s in seen]))
        tools_on = (request.get("tool_choice") or {}).get("type") != "none"
        calls = decide(q, seen, _system(request)) if tools_on else []
        if calls:
            return Turn("Looking that up.", calls)
        return Turn(compose(seen))


# --- running ----------------------------------------------------------------------------------


@dataclass
class Row:
    q: Question
    reply: Reply
    passed: bool
    why: str


def run(
    asker_for: Callable[[], Asker],
    questions: Sequence[Question],
    db: Path,
    out: TextIO,
    *,
    fake_model: bool,
) -> int:
    conn = access.connect(db, readonly=True)
    try:
        fields = spoiler_fields(conn)
        goals = guide.read_goals(VaultIO(db.parent.parent / "vault"))
        rows: list[Row] = []
        print("id\tkind\tlevel\tlane\tpass\tcalls\tusd\twhy\ttext", file=out)
        for q in questions:
            reply = asker_for().ask(q.question, task=TASK)
            passed, why = score(q, reply, conn, fields, goals)
            rows.append(Row(q, reply, passed, why))
            text = reply.text.replace("\t", " ").replace("\n", " ")[:200]
            print(
                f"{q.id}\t{q.kind}\t{q.level}\t{reply.lane}\t{int(passed)}\t{reply.calls}\t"
                f"{reply.usd:.6f}\t{why}\t{text}",
                file=out,
            )
    finally:
        conn.close()
    passed = sum(r.passed for r in rows)
    spoiled = sum(1 for r in rows if r.why.startswith("spoiled"))
    usd = sum(r.reply.usd for r in rows)
    calls = sum(r.reply.calls for r in rows)
    print(file=out)
    print(f"passed: {passed} of {len(rows)}", file=out)
    print(f"spoiled: {spoiled}", file=out)
    print(f"model calls: {calls}", file=out)
    print(f"cost: ${usd:.6f}{' (simulated)' if fake_model else ''}", file=out)
    ok = passed == len(rows)
    print("Result: " + ("PASS" if ok else "FAIL"), file=out)
    return 0 if ok else 1


def fake_asker(home: Path, questions: Sequence[Question]) -> Callable[[], Asker]:
    """Askers over ``home`` (a built pack) with :class:`QuestOracle`: no key, no cost."""
    deep = HandoffRecorder()
    registry = default_registry(home, deep=deep)
    fast = FastLane(
        QuestOracle(plans={" ".join(q.question.split()): q for q in questions}),
        registry,
        meter=None,
        vault=home / "vault",
    )
    return lambda: Asker(registry, deep, fast)


def main(
    config_path: Path | None,
    questions_path: Path | None,
    fake_model: bool,
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
        module = pack()
    except (OSError, EvalError) as exc:
        print(f"Can't read the questions or the pack: {exc}", file=out)
        return 2
    if limit is not None:
        questions = questions[:limit]
    with tempfile.TemporaryDirectory(prefix="gos-quest-eval-", ignore_cleanup_errors=True) as tmp:
        home = Path(tmp) / "home"
        db = module.build(home)
        print(f"# quest pack built in {home}", file=out)
        if fake_model:
            return run(fake_asker(home, questions), questions, db, out, fake_model=True)
        from gregtec.agents.ask import setup

        agents = setup(cfg, deep_lane=False)  # the real meter: the calls count as spend
        try:
            deep = HandoffRecorder()
            registry = default_registry(home, deep=deep)
            fast = FastLane.from_keyring(registry, meter=agents.meter, vault=home / "vault")
            if fast is None:
                print(
                    "Not run: no Anthropic key is stored in this account (uv run gos key "
                    "set anthropic), or use --fake.",
                    file=out,
                )
                return 1
            return run(
                lambda: Asker(registry, deep, fast), questions, db, out, fake_model=False
            )
        finally:
            agents.close()
