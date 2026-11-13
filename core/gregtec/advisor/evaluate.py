"""``gos eval advisor`` (WO-6.5 acceptance, D266): the advisor's three scenarios, end to end.

Each scenario of ``tests/evals/advisor_scenarios.py`` (early, trouble, stocked: self-authored,
never pack data) is built in a temporary GOS_HOME, and "What should I do next?" goes where the
router sends it (the deep lane: plan 4.4 "advise") with the ``advisor`` skill from the repo's
vault template and the scenario's tools, one fresh session each. The answer is checked by code:

- **structure**: the router sent it to the deep lane; the answer says the steps GOS ranked
  (:func:`gregtec.advisor.steps.advise` on the scenario's DB, the truth), one ``step<n>:`` line
  each, in GOS's order, and the scenario's first step kinds are those it was built for;
- **citations**: every number in the answer stands on a line that cites an id, every id it
  cites (``plan:``, ``q:``, ``tel:``, ``mte:``) is in the scenario's DB, and the ids the
  scenario rests on are all cited; the spoken first part holds no id and no number;
- **spoiler level**: nothing past the quest frontier shows above the global level (0): a
  locked quest's hidden name, its tasks, rewards and description (the quest eval's
  :func:`gregtec.quests.questeval.leaks`; his own goal words don't count).

``--fake`` runs an **adversarial oracle** behind the real Claude Code CLI
(:class:`gregtec.llm.fake_api.FakeMessagesAPI`): it loads the skill, calls ``advisor_next``,
then calls ``quest_get`` with ``spoil: 2`` on every quest id it has seen (the steps', then what
those quests unlock and need) and repeats every tool result in its answer. So a fake pass shows
the code's guards and the skill's plumbing, not a model's manners: no key, no cost, nothing
recorded. The real run (Clayton's PC step) uses the stored key and the deep lane's models,
records each call in ``llm_usage`` (task ``eval:advisor``), runs the same checks and writes a
ratings sheet, ``GOS_HOME/data/reports/advisor-<date>.tsv``, with an empty ``useful`` column
for Clayton to fill in (plan 8: he rates all three "useful").
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO

from gregtec import config

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = REPO_ROOT / "tests" / "evals" / "advisor_scenarios.py"
TASK = "eval:advisor"
SKILL = "gos:advisor"
SKILL_HEADING = "# Advisor"
WAIT_S = 900.0
WALK_MAX = 12  # quests the adversary asks for at full spoilers in one round
_REQUEST = re.compile(r"<request>\n(?P<text>.*?)\n</request>", re.DOTALL)
_CITE = re.compile(r"\b(?:plan|q|tel|mte):[^\s,;()\"'|\]]+")
_QID = re.compile(r"q:[A-Za-z0-9_-]{22}")
_NUMBER = re.compile(r"(?<![\w:.@/-])-?\d+(?:\.\d+)?")
_STEP = re.compile(r"^step(\d+): (.*?)(?: \| why: |$)", re.MULTILINE)
TABLE = ("scenario", "lane", "structure", "citations", "spoilers", "useful", "answer")


class EvalError(ValueError):
    """The scenarios can't be loaded."""


def scenarios() -> ModuleType:
    name = "advisor_scenarios"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCENARIOS)
    if spec is None or spec.loader is None:
        raise EvalError(f"can't load {SCENARIOS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --- the checks ------------------------------------------------------------------------------


@dataclass
class Checked:
    scenario: str
    lane: str
    answer: str
    problems: dict[str, list[str]] = field(
        default_factory=lambda: {"structure": [], "citations": [], "spoilers": []}
    )

    @property
    def passed(self) -> bool:
        return not any(self.problems.values())


def _exists(conn: sqlite3.Connection, cite: str) -> bool:
    kind, _, rest = cite.partition(":")
    if kind == "plan":
        sql, arg = "SELECT 1 FROM plan WHERE id = ?", rest
    elif kind == "q":
        sql, arg = "SELECT 1 FROM quest WHERE id = ?", rest
    elif kind == "tel":
        sql, arg = "SELECT 1 FROM tel_machine WHERE id = ?", rest
    else:
        sql, arg = "SELECT 1 FROM machine WHERE mte_id = ?", rest
    try:
        return conn.execute(sql, (arg,)).fetchone() is not None
    except sqlite3.Error:
        return False


def spoken(answer: str) -> str:
    """The spoken part: everything before the first ``step<n>:`` line."""
    m = re.search(r"^step\d+:", answer, re.MULTILINE)
    return answer[: m.start()] if m else answer


def check(
    sc: Any,  # noqa: ANN401  (an advisor_scenarios.Scenario)
    lane: str,
    answer: str,
    want: Sequence[Mapping[str, Any]],
) -> Checked:
    """Score one answer against the steps GOS ranked (``want``: their ``as_dict``)."""
    from gregtec.quests import questeval

    out = Checked(sc.name, lane, answer)
    s, c, sp = (out.problems[k] for k in ("structure", "citations", "spoilers"))
    if lane != "deep":
        s.append(f"the router sent it to the {lane} lane, not the deep lane")
    got = [(int(m[1]), m[2].strip()) for m in _STEP.finditer(answer)]
    expect = [(int(w["rank"]), str(w["title"])) for w in want]
    if got != expect:
        s.append(f"steps {got} are not GOS's {expect}")
    kinds = tuple(str(w["kind"]) for w in want[: len(sc.kinds)])
    if kinds != tuple(sc.kinds):
        s.append(f"GOS ranked {kinds}, the scenario wants {tuple(sc.kinds)}")
    conn = sqlite3.connect(sc.db)
    try:
        cited = set(_CITE.findall(answer))
        for cite in sorted(cited):
            if not _exists(conn, cite):
                c.append(f"{cite} is not in the DB")
        for cite in sc.cites:
            if cite not in cited:
                c.append(f"{cite} is not cited")
        for line in answer.splitlines():
            bare = re.sub(r"^step\d+:", "", _CITE.sub(" ", line))
            if _NUMBER.search(bare) and not _CITE.search(line):
                c.append(f"a number with no citation: {line.strip()[:120]}")
        voice = spoken(answer)
        if _CITE.search(voice) or _NUMBER.search(voice):
            c.append(f"the spoken part holds an id or a number: {voice.strip()[:120]}")
        fields = questeval.spoiler_fields(conn)
    finally:
        conn.close()
    sp += questeval.leaks(answer, fields, said=scenarios().pack().GOALS)
    return out


# --- the adversarial oracle ----------------------------------------------------------------


def _blocks(content: object) -> list[dict[str, Any]]:
    return (
        [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    )


def _text(block: Mapping[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    return "".join(str(c.get("text", "")) for c in _blocks(content))


def _seen(
    body: Mapping[str, Any],
) -> tuple[list[tuple[str, dict[str, Any], str]], bool]:
    """(the gos tools' calls with their results, whether the skill is loaded)."""
    from gregtec.agents.tools.mcp import PREFIX

    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    seen: list[tuple[str, dict[str, Any], str]] = []
    loaded = False
    for message in body.get("messages") or []:
        for b in _blocks(message.get("content")):
            kind = b.get("type")
            if kind == "tool_use":
                calls[str(b.get("id"))] = (
                    str(b.get("name")),
                    dict(b.get("input") or {}),
                )
            elif kind == "tool_result":
                name, args = calls.get(str(b.get("tool_use_id")), ("", {}))
                if name.startswith(PREFIX):
                    seen.append((name.removeprefix(PREFIX), args, _text(b)))
            elif kind == "text" and message.get("role") == "user":
                loaded = loaded or SKILL_HEADING in str(b.get("text", ""))
    return seen, loaded


def compose(seen: Sequence[tuple[str, dict[str, Any], str]]) -> str:
    """The oracle's answer: the skill's shape (a spoken line per step, then a ``step<n>:`` line
    per step), then every other tool result, repeated whole (the adversary's leak)."""
    from gregtec.agents.tools.tsv import parse

    steps = next((t for n, _, t in seen if n == "advisor_next"), "")
    rows = parse(steps).rows if steps else []
    voice = " ".join(
        f"{'First' if i == 0 else 'Then'}, {r['step'][:1].lower()}{r['step'][1:]}."
        for i, r in enumerate(rows)
    )
    lines = [voice or "I have no steps for you yet."]
    for r in rows:
        why = r["why"] or "-"
        lines.append(
            f"step{r['rank']}: {r['step']} | why: {why} | refs: {r['refs']} | link: {r['link']}"
        )
    for name, args, text in seen:
        if name != "advisor_next":
            lines.append(f"detail {name} {json.dumps(args)}:")
            lines += [f"  {line} ({args.get('id', '')})" for line in text.splitlines()]
    return "\n".join(lines)


def policy(question: str) -> Callable[[dict[str, Any]], Any]:
    """The adversarial deep-lane model (see the module docstring)."""
    from gregtec.agents.tools.mcp import PREFIX
    from gregtec.llm import fake_api

    def turn(body: dict[str, Any]) -> fake_api.ApiTurn:
        found = _REQUEST.search(fake_api.request_text(body))
        if not found or " ".join(found["text"].split()) != question:
            return fake_api.ApiTurn(text="Done.")
        seen, loaded = _seen(body)
        if not loaded:
            offered = SKILL in json.dumps(body.get("system")) + json.dumps(
                body.get("tools")
            ) + json.dumps(body.get("messages"))
            if not offered:
                return fake_api.ApiTurn(text="The advisor skill isn't offered.")
            return fake_api.ApiTurn(
                text="Loading the advisor skill.", tools=[("Skill", {"skill": SKILL})]
            )
        if not any(n == "advisor_next" for n, _, _ in seen):
            return fake_api.ApiTurn(
                text="Working out your next steps.",
                tools=[(PREFIX + "advisor_next", {})],
            )
        asked = {str(a.get("id")) for n, a, _ in seen if n == "quest_get"}
        ids = []
        for _, _, text in seen:
            ids += [q for q in _QID.findall(text) if q not in asked and q not in ids]
        if ids:
            return fake_api.ApiTurn(
                text="Looking closer.",
                tools=[
                    (PREFIX + "quest_get", {"id": q, "spoil": 2})
                    for q in ids[:WALK_MAX]
                ],
            )
        return fake_api.ApiTurn(text=compose(seen))

    return turn


# --- the run ---------------------------------------------------------------------------------


def _ask(
    home: Path,
    sc: Any,  # noqa: ANN401
    lane_for: Callable[[Path], Any],
) -> tuple[str, str, list[dict[str, Any]]]:
    """(lane, answer, GOS's steps) for one scenario."""
    from gregtec.advisor import steps
    from gregtec.agents import router
    from gregtec.agents.tools.catalog import default_registry
    from gregtec.vault.io import VaultIO

    question = scenarios().QUESTION
    route = router.route(question)
    conn = sqlite3.connect(sc.db)
    try:
        advice = steps.advise(
            conn, vault=VaultIO(home / "vault"), db=sc.db, home=home, spoil=0
        )
    finally:
        conn.close()
    want = [s.as_dict(i) for i, s in enumerate(advice.steps, 1)]
    if route.kind != router.DEEP:
        return route.kind, "", want
    lane = lane_for(home)
    try:
        lane.registry = default_registry(home, deep=lane)
        handed = lane.handoff(question, question, "router")
        task = lane.wait(handed.id, WAIT_S)
    finally:
        lane.stop()
    if task is None:
        return "deep", "(the deep lane gave no answer)", want
    return "deep", task.text or f"({task.status}: {task.error})", want


def run(
    lane_for: Callable[[Path], Any],
    out: TextIO,
    *,
    names: Sequence[str] | None = None,
    sheet: Path | None = None,
) -> tuple[int, list[Checked]]:
    """Ask every scenario; returns (exit code, the checked answers)."""
    S = scenarios()
    results: list[Checked] = []
    for name in names or S.NAMES:
        with tempfile.TemporaryDirectory(
            prefix=f"gos-advisor-{name}-",
            ignore_cleanup_errors=True,  # Windows: the CLI lets go late
        ) as d:
            home = Path(d)
            sc = S.build(home, name, time.time())
            lane, answer, want = _ask(home, sc, lane_for)
            checked = check(sc, lane, answer, want)
        results.append(checked)
        print(f"== {name}: {'PASS' if checked.passed else 'FAIL'}", file=out)
        print(answer, file=out)
        for kind, problems in checked.problems.items():
            for p in problems:
                print(f"# {kind}: {p}", file=out)
    passed = sum(1 for r in results if r.passed)
    print(f"advisor: {passed} of {len(results)} scenarios pass", file=out)
    if sheet is not None:
        sheet.parent.mkdir(parents=True, exist_ok=True)
        with open(sheet, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(TABLE)
            for r in results:
                w.writerow(
                    [
                        r.scenario,
                        r.lane,
                        *(
                            "ok" if not r.problems[k] else "; ".join(r.problems[k])
                            for k in ("structure", "citations", "spoilers")
                        ),
                        "",  # Clayton: yes or no
                        " / ".join(r.answer.split("\n")),
                    ]
                )
        print(f"# ratings sheet: {sheet} (fill in `useful`: yes or no)", file=out)
    return (0 if passed == len(results) else 1), results


def fake_lane(api: Any) -> Callable[[Path], Any]:  # noqa: ANN401
    """A deep lane per scenario behind the fake API (no key, no cost, nothing recorded)."""
    from gregtec.agents.deep import DeepLaneService
    from gregtec.agents.deep_config import Settings
    from gregtec.agents.deep_eval import FAKE_KEY
    from gregtec.vault import deploy as vault_deploy

    def make(home: Path) -> DeepLaneService:
        lane = DeepLaneService(
            None,
            Settings.from_config(config.Config(gos_home=home)),
            home=home / "data" / "agent-eval-advisor",
            key=lambda: FAKE_KEY,
            meter=None,
            env=api.env(),
            task_label=TASK,
            template=vault_deploy.DEFAULT_TEMPLATE,
        )
        return lane

    return make


def main(
    config_path: Path | None, fake: bool, out: TextIO, limit: int | None = None
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    S = scenarios()
    question = S.QUESTION
    names = S.NAMES[:limit] if limit else S.NAMES
    if fake:
        from gregtec.llm.fake_api import FakeMessagesAPI

        with FakeMessagesAPI(policy(question)) as api:
            return run(fake_lane(api), out, names=names)[0]
    import keyring.errors

    from gregtec.agents.deep import DeepLaneService
    from gregtec.agents.deep_config import Settings
    from gregtec.llm import keys
    from gregtec.llm.usage import UsageMeter
    from gregtec.store import Store
    from gregtec.vault import deploy as vault_deploy

    try:
        keys.get_key(keys.ANTHROPIC)
    except (keys.MissingKeyError, keyring.errors.KeyringError) as exc:
        print(f"Not run: {exc} (or use --fake)", file=out)
        return 2
    store = Store(cfg.data_dir / "gos.sqlite")  # his own meter: the eval's calls count
    meter = UsageMeter(
        store, cfg.monthly_included_usd, cfg.credit_alerts_pct, cfg.cycle_day
    )

    def make(home: Path) -> DeepLaneService:
        return DeepLaneService(
            None,
            Settings.from_config(cfg),
            home=home / "data" / "agent-eval-advisor",
            key=lambda: keys.get_key(keys.ANTHROPIC),
            meter=meter,
            task_label=TASK,
            template=vault_deploy.DEFAULT_TEMPLATE,
        )

    stamp = dt.date.today().isoformat()
    try:
        sheet = cfg.data_dir / "reports" / f"advisor-{stamp}.tsv"
        return run(make, out, names=names, sheet=sheet)[0]
    finally:
        store.close()
