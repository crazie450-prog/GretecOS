"""``gos eval power-modes``: the power designer's four modes at LV, HV and IV (WO-4.6, D245).

Plan 8 row 4.6's acceptance: "all 4 modes at LV, HV and IV reviewed, each returning 2-3 ranked
options". Every row of ``tests/evals/power_modes.tsv`` (12 cases) runs on a **copy** of the DB
(``GOS_HOME/data/gos.sqlite``, or ``--db``) in a scratch GOS_HOME with the vault template (so the
weights are the balanced defaults, D67), through the path the deep lane takes: the
``design_power`` tool (which saves each option as a proposed plan), then ``plan_explain`` with a
**fake planner model** (no key, no cost, D158 (4)), then the Power view's payload. Each case is
checked by code:

- ``options``: 2-3 options (D11);
- ``distinct``: no two options are the same line as Clayton reads it (generator, fuel, net and
  chain EU/t, upkeep and the chain's recipes all equal; review M2);
- ``order``: rank positions 1..n, in the order the mode's own metric gives (``best`` and
  ``self_sustaining`` by score, ``max_output`` by net EU/t, ``output_per_effort`` by net EU/t per
  effort hour after D235/D236's grouping), and the tool's rows in that order;
- ``weights``: the weights line printed, balanced (every weight 1, D67);
- ``numbers``: net EU/t above 0 and equal to gross less the chain's use; every generator cited
  ``mte:``, every fuel ``r:``, every chain node ``r:`` (or ``farm:``);
- ``effort``: an effort breakdown whose card passes the catalog (D160, D237);
- ``closed``: the validator's verdict on every option; ``self_sustaining``'s all proven closed,
  each with its bootstrap kit;
- ``explain``: the fake model's faithful text is accepted as it is (``source: model``) and a fake
  that names the options out of order is refused (GOS's template is used instead), so a model
  explains code's order and never changes it;
- ``view``: the Power view's payload lists the same options in the same order.

The report (Markdown, ``--report``) lists every case with its options, weights and checks, and
flags options whose effort is 0 h with parts not counted (the effort term can't separate them;
the numbers are WO-4.3's), for Clayton's review (his review is a PC step). Exit 0 when every case passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sqlite3
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from gregtec.planner.explain import fmt

COMMAND = "power-modes"
REPO = Path(__file__).resolve().parents[3]
CASES = REPO / "tests" / "evals" / "power_modes.tsv"
TEMPLATE = REPO / "vault-template"


@dataclass(frozen=True)
class Case:
    id: str
    mode: str
    tier: str


@dataclass
class CaseResult:
    case: Case
    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)
    plans: list[dict[str, Any]] = field(default_factory=list)
    weights: str = ""
    notes: list[str] = field(default_factory=list)
    rejected: int = 0
    explanation: str = ""
    fuels: dict[str, str] = field(
        default_factory=dict
    )  # fuel key -> name, from the book
    seconds: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and all(ok for ok, _ in self.checks.values())


def load_cases(path: Path = CASES) -> list[Case]:
    rows = [
        line.split("\t")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    head, body = rows[0], rows[1:]
    if head != ["id", "mode", "tier"]:
        raise ValueError(f"{path}: header is id, mode, tier")
    return [Case(*r) for r in body]


# --- the fake planner model --------------------------------------------------------------------


def _doc(prompt: str) -> dict[str, Any]:
    return json.loads(prompt[prompt.rindex("\n\n") + 2 :])


def faithful(prompt: str) -> str:
    """A fake planner model that explains the options in the order given, copying numbers."""
    doc = _doc(prompt)
    lines = []
    for o in doc["options"]:
        lines.append(
            f"Option {o['position']} (plan:{o['id']}) burns {o['fuel']['name']} in its "
            f"{o['generator']['label']}: {fmt(o['net_eut'])} EU/t net, score "
            f"{fmt(o['score'])}, effort {fmt(o['eff_h'])} h."
        )
    return "\n".join(lines)


def reordering(prompt: str) -> str:
    """A fake planner model that puts the last option first (the order check must refuse it)."""
    doc = _doc(prompt)
    opts = list(reversed(doc["options"]))
    return "\n".join(
        f"plan:{o['id']} with {fmt(o['net_eut'])} EU/t net is the one to build."
        for o in opts
    )


# --- one case ----------------------------------------------------------------------------------


def _copy_db(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    a = sqlite3.connect(f"{src.as_uri()}?mode=ro", uri=True)
    b = sqlite3.connect(dest)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()


def _order_ok(plans: Sequence[dict[str, Any]], mode: str) -> tuple[bool, str]:
    pos = [p["rank"]["position"] for p in plans]
    if pos != list(range(1, len(plans) + 1)):
        return False, f"positions {pos}"

    def key(p: dict[str, Any]) -> tuple[float, ...]:
        r = p["rank"]
        if mode == "max_output":
            return (-float(r["net_eut"]),)
        if mode == "output_per_effort":
            ratio = r.get("eut_per_effort_h")
            uncounted = int(p["metrics"].get("effort", {}).get("uncounted", 0))
            return (
                float(ratio is None),
                float(uncounted > 0),
                -float(ratio if ratio is not None else r["net_eut"]),
            )
        return (-float(r["score"]),)

    keys = [tuple(round(x, 9) for x in key(p)) for p in plans]
    if any(a > b for a, b in itertools.pairwise(keys)):
        return False, f"keys out of order: {keys}"
    what = {
        "max_output": "net EU/t",
        "output_per_effort": "net EU/t per effort hour (D235/D236 groups first)",
    }.get(mode, "score")
    return True, f"positions 1..{len(plans)}, by {what}"


def _in_text_order(text: str, ids: Sequence[str]) -> bool:
    at = [text.find(i) for i in ids]
    return all(a >= 0 for a in at) and at == sorted(at)


def _numbers_ok(plans: Sequence[dict[str, Any]]) -> tuple[bool, str]:
    bad: list[str] = []
    for p in plans:
        pw = p["power"]
        net, gross, chain = (
            float(pw[k]) for k in ("net_eut", "gross_eut", "chain_eut")
        )
        if not net > 0:
            bad.append(f"{p['id']}: net {net}")
        if not math.isclose(net, gross - chain, rel_tol=1e-6, abs_tol=1e-6):
            bad.append(f"{p['id']}: net {net} != gross {gross} - chain {chain}")
        if not any(
            str(c).startswith("mte:") for c in pw["generator"].get("cites") or ()
        ):
            bad.append(f"{p['id']}: generator not cited mte:")
        if not str(pw["fuel"].get("ref") or "").startswith("r:"):
            bad.append(f"{p['id']}: fuel not cited r:")
        for n in p["nodes"]:
            kind = n.get("kind")
            if kind == "recipe" and not str(n["recipe"]).startswith("r:"):
                bad.append(f"{p['id']}: {n['n']} cited {n['recipe']}")
            if kind == "farm" and not str(n["recipe"]).startswith("farm:"):
                bad.append(f"{p['id']}: {n['n']} cited {n['recipe']}")
    return (not bad, "; ".join(bad[:4]) or "net > 0 = gross - chain; cited")


def _distinct(plans: Sequence[dict[str, Any]]) -> tuple[bool, str]:
    def sig(p: dict[str, Any]) -> tuple[Any, ...]:
        pw = p["power"]
        return (
            pw["generator"]["label"],
            pw["fuel"]["id"],
            round(float(pw["net_eut"]), 6),
            round(float(pw["chain_eut"]), 6),
            p["metrics"].get("upkeep"),
            tuple(sorted(str(n.get("recipe")) for n in p["nodes"])),
        )

    seen: dict[tuple[Any, ...], int] = {}
    same: list[str] = []
    for i, p in enumerate(plans, 1):
        j = seen.setdefault(sig(p), i)
        if j != i:
            same.append(f"options {j} and {i}")
    if same:
        return False, "the same line twice: " + ", ".join(same)
    return True, "no two options the same line"


def uncounted_effort(plans: Sequence[dict[str, Any]]) -> list[str]:
    """Options whose effort is 0 h with parts not counted (review L1)."""
    out = []
    for p in plans:
        m = p["metrics"]
        n = int((m.get("effort") or {}).get("uncounted") or 0)
        if m.get("eff_h") is not None and float(m["eff_h"]) == 0 and n > 0:
            out.append(f"option {p['rank']['position']} ({n} not counted)")
    return out


def run_case(case: Case, home: Path) -> CaseResult:
    from gregtec.agents.tools.catalog import build_registry
    from gregtec.agents.tools.registry import DEEP
    from gregtec.cards import validate as card_problems
    from gregtec.db import access
    from gregtec.planner import power_explain, powerview, rank
    from gregtec.planner.outputs import cards

    res = CaseResult(case)
    t0 = time.monotonic()
    db = access.db_path(home)
    reg = build_registry(db, writer=lambda: faithful)
    try:
        out = reg.call(
            "design_power", {"mode": case.mode, "tier": case.tier}, lane=DEEP
        )
        if out.is_error:
            raise RuntimeError(out.text)
        tool_text = out.text
        plans = powerview.group(home, powerview.latest(home)[0])
        res.plans = plans
        saved = powerview._latest(home)
        res.weights = str(saved.get("weights") or "")
        res.notes = [str(n) for n in saved.get("notes") or ()]
        res.rejected = len(saved.get("rejected") or ())
        n = len(plans)
        res.checks["options"] = (2 <= n <= 3, f"{n} options")
        res.checks["distinct"] = _distinct(plans)
        res.checks["order"] = _order_ok(plans, case.mode)
        listed = _in_text_order(tool_text, [f"plan:{p['id']}" for p in plans])
        res.checks["tool order"] = (listed, "design_power's rows in rank order")
        want = rank.describe_power(rank.BALANCED, case.mode)
        w_ok = res.weights == want and all(
            p["rank"]["weights"] == "balanced"
            and all(float(v) == 1.0 for v in p["rank"]["w"].values())
            for p in plans
        )
        res.checks["weights"] = (w_ok, res.weights or "no weights line")
        res.checks["numbers"] = _numbers_ok(plans)
        problems: list[str] = []
        for p in plans:
            if "effort" not in p["metrics"]:
                problems.append(f"{p['id']}: no effort")
            else:
                problems += card_problems(cards.effort_card(p))
        res.checks["effort"] = (
            not problems,
            "; ".join(problems[:3])
            or "breakdown on every option; cards pass the catalog",
        )
        if case.mode == "self_sustaining":
            closed = all(p["closed"]["ok"] and p.get("bootstrap") for p in plans)
            res.checks["closed"] = (closed, "every option proven closed, with its kit")
        else:
            have = all("ok" in (p.get("closed") or {}) for p in plans)
            res.checks["closed"] = (have, "the validator's verdict on every option")
        res.fuels = powerview.fuel_names(home, plans)
        good = power_explain.explain(plans, faithful, res.fuels)
        bad = power_explain.explain(plans, reordering, res.fuels)
        res.explanation = good.text
        exp_ok = good.source == "model" and (n < 2 or bad.source == "template")
        res.checks["explain"] = (
            exp_ok,
            f"faithful fake: {good.source}; reordering fake: {bad.source}",
        )
        told = reg.call("plan_explain", {"id": f"plan:{plans[0]['id']}"}, lane=DEEP)
        src = re.search(r"^source: (\S+)", told.text, re.MULTILINE)
        res.checks["plan_explain"] = (
            not told.is_error and src is not None and src[1] == "model",
            f"the deep lane's tool, faithful fake: {src[1] if src else told.text[:80]}",
        )
        v = powerview.view(home, plans[0]["id"])
        res.checks["view"] = (
            [o["id"] for o in v["options"]] == [p["id"] for p in plans],
            "the Power view's options in the same order",
        )
    except Exception as exc:  # noqa: BLE001 - a case that fails is reported, the rest run
        res.error = f"{type(exc).__name__}: {exc}"
    res.seconds = time.monotonic() - t0
    return res


def scratch_home(src_db: Path, root: Path) -> Path:
    """A scratch GOS_HOME at ``root``: a copy of ``src_db`` and the vault template."""
    from gregtec.db import access
    from gregtec.vault import deploy as vault_deploy

    _copy_db(src_db, access.db_path(root))
    vault_deploy.deploy(root / "vault", TEMPLATE)
    return root


def run(
    src_db: Path,
    cases: Sequence[Case],
    root: Path,
    out: TextIO | None = None,
) -> list[CaseResult]:
    home = scratch_home(src_db, root)
    results = []
    for c in cases:
        r = run_case(c, home)
        results.append(r)
        if out is not None:
            print(
                f"{'PASS' if r.ok else 'FAIL'}\t{c.id}\t{len(r.plans)} options\t"
                f"{r.seconds:.0f} s" + (f"\t{r.error}" if r.error else ""),
                file=out,
                flush=True,
            )
    return results


# --- the report --------------------------------------------------------------------------------


def _cell(v: object) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return fmt(v)
    return str(v).replace("|", "/")


def _fuel(names: dict[str, str], key: str) -> str:
    name = names.get(key, key)
    return key if name == key else f"{name} ({key})"


def report(results: Sequence[CaseResult], source: str) -> str:
    passed = sum(r.ok for r in results)
    lines = [
        "# WO-4.6 power modes: 4 modes at LV, HV and IV",
        "",
        (
            f"`gos eval power-modes` on {source}: **{passed} of {len(results)} cases pass**. "
            "Every number below is the power designer's (plan 7.2) on that DB, ranked by "
            "planner/rank.py with the balanced default weights (D67); the explanation checks use "
            "fake planner models (no key). Clayton's review of these options is a PC step."
        ),
        "",
        "| case | mode | tier | options | result | s |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.case.id} | {r.case.mode} | {r.case.tier} | {len(r.plans)} | "
            f"{'pass' if r.ok else 'FAIL'} | {r.seconds:.0f} |"
        )
    for r in results:
        lines += ["", f"## {r.case.id}: {r.case.mode} at {r.case.tier}", ""]
        if r.error:
            lines += [f"Error: `{r.error}`", ""]
        if r.weights:
            lines += [f"Weights: {r.weights}", ""]
        if r.plans:
            lines += [
                (
                    "| # | plan | generator | fuel | net EU/t | gross | chain | out | score "
                    "| EU/t per effort h | effort h | not counted | upkeep /h | closed |"
                ),
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for p in r.plans:
                pw, rk, m = p["power"], p["rank"], p["metrics"]
                dist = pw.get("distribution") or {}
                lines.append(
                    "| "
                    + " | ".join(
                        _cell(x)
                        for x in (
                            rk["position"],
                            f"plan:{p['id']}",
                            pw["generator"]["label"],
                            _fuel(r.fuels, str(pw["fuel"]["id"])),
                            pw["net_eut"],
                            pw["gross_eut"],
                            pw["chain_eut"],
                            f"{dist.get('tier')} x{dist.get('amps')}",
                            rk["score"],
                            rk.get("eut_per_effort_h"),
                            m.get("eff_h"),
                            (m.get("effort") or {}).get("uncounted"),
                            m.get("upkeep"),
                            "yes" if p["closed"]["ok"] else "no",
                        )
                    )
                    + " |"
                )
            lines.append("")
        lines.append("Checks:")
        lines += [
            f"- {name}: {'pass' if ok else 'FAIL'} ({detail})"
            for name, (ok, detail) in r.checks.items()
        ]
        zero = uncounted_effort(r.plans)
        if zero:
            lines += [
                "",
                "Effort 0 h with parts not counted: "
                + "; ".join(zero)
                + ". The effort "
                "term can't tell these options apart (WO-4.3's effort numbers).",
            ]
        warn = [w for p in r.plans for w in p.get("warnings") or ()]
        if warn:
            lines += ["", "Warnings: " + "; ".join(dict.fromkeys(warn))[:600]]
        if r.rejected:
            lines += ["", f"{r.rejected} candidates rejected (see `gos power design`)."]
        if r.explanation:
            lines += ["", "Explanation (faithful fake, accepted):", ""]
            lines += [f"> {ln}" for ln in r.explanation.splitlines()]
    return "\n".join(lines) + "\n"


# --- the command -------------------------------------------------------------------------------


def add_parsers(eval_cmds: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    e = eval_cmds.add_parser(
        COMMAND,
        help="the power designer's 4 modes at LV, HV and IV on a copy of the DB (WO-4.6)",
        description=(
            "Runs every row of tests/evals/power_modes.tsv on a copy of GOS_HOME's DB (never "
            "the DB itself) through design_power, plan_explain (fake planner models: no key) "
            "and the Power view, checks each case by code and writes a Markdown report."
        ),
    )
    e.add_argument("--config", type=Path, default=None, help="gos.toml to read")
    e.add_argument("--db", type=Path, default=None, help="another DB to copy")
    e.add_argument("--cases", type=Path, default=None, help="another case file")
    e.add_argument("--report", type=Path, default=None, help="write the report here")
    e.add_argument("--limit", type=int, default=None, help="only the first N cases")


def main(args: argparse.Namespace, out: TextIO) -> int:
    from gregtec import config
    from gregtec.db import access

    if args.db is not None:
        src = Path(args.db)
    else:
        try:
            cfg = config.load(args.config)
        except (OSError, ValueError) as exc:
            print(f"Can't read gos.toml: {exc}", file=out)
            return 2
        src = access.db_path(cfg.gos_home)
    if not src.is_file():
        print(f"{src} doesn't exist: run `gos db build` first", file=out)
        return 2
    cases = load_cases(args.cases or CASES)[: args.limit]
    with tempfile.TemporaryDirectory(prefix="gos-power-eval-") as tmp:
        results = run(src, cases, Path(tmp) / "home", out)
    text = report(results, f"`{src.name}`")
    if args.report is not None:
        Path(args.report).write_text(text, encoding="utf-8")
        print(f"report: {args.report}", file=out)
    passed = sum(r.ok for r in results)
    print(f"{passed} of {len(results)} cases pass", file=out)
    return 0 if passed == len(results) else 1
