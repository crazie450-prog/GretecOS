"""WO-4.6: the Power view's backend (gregtec.planner.powerview), the power explanation's checks
(power_explain), the power-designer skill's tools (design_power, effort_estimate, plan_explain on
a power design), the core's /api/power routes and `gos eval power-modes`, on WO-4.2's made-up
fixture DB (tests/unit/planner/test_power.py: a test gas burnt in a test combustion generator,
closed on water through its loop). No pack data; every number here is the fixture's.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.agents.tools.catalog import LATER, build_registry
from gregtec.agents.tools.registry import DEEP, FAST
from gregtec.cards import validate as card_problems
from gregtec.db import access
from gregtec.planner import (
    chains,
    designer,
    ledger,
    power,
    power_explain,
    powereval,
    powerview,
    rank,
    validate,
)
from gregtec.vault import deploy as vault_deploy

REPO = Path(__file__).resolve().parents[3]


def _fixture(name: str, alias: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tpw = _fixture("test_power.py", "_wo46_power_fixture")


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tpw.make_home(tmp_path_factory.mktemp("power-view-src"))


@pytest.fixture
def home(db: Path, tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / "data").mkdir(parents=True)
    src = access.connect(db, readonly=True)
    dst = sqlite3.connect(h / "data" / "gos.sqlite")
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    vault_deploy.deploy(h / "vault", REPO / "vault-template")
    farms = h / "farms.tsv"
    farms.write_text("\t".join(tpw.farms.HEADER) + "\n", encoding="utf-8")
    return h


def design(home: Path, **spec: Any) -> powerview.PowerDesigned:  # noqa: ANN401
    effort = bool(spec.pop("effort", False))
    spec.setdefault("tier", "MV")
    spec.setdefault("free", list(tpw.FREE))
    return powerview.design(
        home,
        powerview.request_from_spec(spec),
        farms_path=home / "farms.tsv",
        with_effort=effort,
        templates=chains.EMPTY,
    )


# --- the request -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ({"mode": "fastest"}, "mode"),
        ({"mode": 3}, "mode is one of"),
        ({"target": "x"}, "unknown fields: target"),
        ({"tier": "XYZ"}, "tier"),
        ({"scale": "generators"}, "needs a bound"),
        ({"scale": "unit", "bound": 2}, "takes no bound"),
        ({"bound": "2"}, "bound is a number"),
        ({"bound": True}, "bound is a number"),
        ({"rotor": ["Steel"]}, r"rotor is \[material, size\]"),
        ({"options": 4}, "options"),
        ({"options": "3"}, "options is 1, 2 or 3"),
        ({"kinds": "gas_turbine"}, "kinds is a list"),
        ({"kinds": ["windmill"]}, "kinds"),
    ],
)
def test_request_from_spec_refuses(spec: dict[str, Any], match: str) -> None:
    with pytest.raises(powerview.PowerViewError, match=match):
        powerview.request_from_spec(spec)


def test_request_from_spec_defaults() -> None:
    req = powerview.request_from_spec({"mode": "max_output", "tier": "hv"})
    assert req.mode == "max_output" and req.tier_cap == 3
    assert req.scale == "unit" and req.options == 3
    assert req.free == tuple(sorted(power.DEFAULT_FREE))
    assert req.rotor == power.DEFAULT_ROTOR
    capped = powerview.request_from_spec(
        {"mode": "max_output", "scale": "eut_cap", "bound": 100}
    )
    assert capped.bound == 100.0 and capped.tier_cap is None  # My base's tier


def test_balanced_default_weights() -> None:
    """D67: the template's me/prefs.md gives every weight 1, routes and power lines alike."""
    prefs = power.read_prefs(None)
    assert prefs.name == "balanced" and prefs.notes == ()
    assert all(w == 1.0 for _, w in prefs.power) and all(
        w == 1.0 for _, w in prefs.weights
    )
    assert power.read_prefs(REPO / "vault-template") == prefs
    line = rank.describe_power(prefs, "best")
    assert "weights (balanced): net EU/t 1, effort 1, upkeep inputs 1" in line


# --- design, view, accept ----------------------------------------------------------------------


def test_design_saves_proposed_options_in_rank_order(home: Path) -> None:
    d = design(home, mode="best")
    assert 2 <= len(d.ids) <= 3
    assert [p["rank"]["position"] for p in d.plans] == list(range(1, len(d.ids) + 1))
    assert d.weights == rank.describe_power(rank.BALANCED, "best")
    assert powerview.latest(home) == d.ids
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        rows = dict(
            conn.execute("SELECT id, status FROM plan WHERE kind = 'power'").fetchall()
        )
    finally:
        conn.close()
    assert rows == {pid: "proposed" for pid in d.ids}
    for pid in d.ids:
        assert (home / "data" / "plans" / f"{pid}.json").is_file()
    # Every option of the design is its group, in rank order.
    assert [p["id"] for p in powerview.group(home, d.ids[-1])] == d.ids


def test_view_shows_every_option_with_its_checks(home: Path) -> None:
    d = design(home, mode="best")
    v = powerview.view(home, d.ids[1])
    assert v["ids"] == d.ids and [o["id"] for o in v["options"]] == d.ids
    assert v["mode"] == "best" and v["mode_label"] == "Best at tier"
    assert v["weights"] == d.weights and v["tier_cap"] == "MV"
    assert set(v["w"]) == set(rank.POWER_METRICS)
    assert v["notes"] == d.notes  # the latest design's notes
    for o, p in zip(v["options"], d.plans, strict=True):
        pw = p["power"]
        assert o["net_eut"] == pw["net_eut"] and o["gross_eut"] == pw["gross_eut"]
        assert o["chain_eut"] == pw["chain_eut"] and o["status"] == "proposed"
        assert o["weights"] == d.weights  # printed with every option
        assert o["fuel"]["ref"].startswith("r:") and o["fuel"]["name"]
        assert any(c.startswith("mte:") for c in o["generator"]["cites"])
        assert isinstance(o["closed"]["ok"], bool)  # the validator ran on every option
        assert o["effort"]["eff_h"] == 0.0  # solved without the effort model here
        kinds = {n["kind"] for n in o["nodes"]}
        assert "generator" in kinds
        for n in o["nodes"]:
            if n["kind"] == "recipe":
                assert n["recipe"].startswith("r:")
        # The ledger with this option accepted: a source and no lines, so no warnings.
        led = o["ledger"]
        assert not led["counted"] and card_problems(led["card"]) == []
        assert led["card"]["tiers"][0]["gen"] == pw["net_eut"]
        assert led["warnings"] == [] and led["added"] == [] and led["cleared"] == []
        # The fixture has no storage tables: no cables, said so in the ledger section.
        assert o["cables"] == []
    # Each option after the first says how it differs from option 1 (review verdict 7 (b)).
    first, *rest = v["options"]
    assert first["differs"] == [] and rest
    for o in rest:
        assert o["differs"] and o["differs"] == powerview.differs(first, o)
    gen = {"label": "G", "kind": "k", "tier": "MV", "eut": 32, "cites": []}
    a = {"id": "a", "generator": gen, "fuel": {"id": "f", "name": "F"}, "nodes": []}
    b = {**a, "id": "b", "nodes": [{"kind": "recipe", "recipe": "r:x"}]}
    assert powerview.differs(a, b) == ["1 recipe(s) option 1 doesn't use"]
    assert powerview.differs(a, {**a, "id": "c"}) == [
        "the same generator, fuel and recipes, run at other rates"
    ]
    lv = v["ledger"]
    assert card_problems(lv["card"]) == [] and card_problems(lv["running"]) == []
    assert lv["lines"] == 0 and lv["warnings"] == []
    assert any("storage" in n for n in lv["distribution"]["notes"])
    # Another design replaces the latest one: its notes go with it.
    other = design(home, mode="max_output")
    assert powerview.view(home, d.ids[0])["notes"] == []
    assert powerview.latest(home) == other.ids


def test_view_with_effort_has_the_breakdown_card(home: Path) -> None:
    d = design(home, mode="output_per_effort", effort=True)
    v = powerview.view(home, d.ids[0])
    for o in v["options"]:
        eff = o["effort"]
        assert eff is not None and eff["eff_h"] is not None
        assert card_problems(eff["card"]) == []
        assert eff["card"]["tool"] == "effort_estimate"


def test_window_is_checked(home: Path) -> None:
    d = design(home, mode="best")
    for bad in (-1.0, float("nan"), float("inf"), 1e9):
        with pytest.raises(powerview.PowerViewError, match="peak window"):
            powerview.view(home, d.ids[0], bad)
    assert powerview.ledger_view(home, 600.0)["window_s"] == 600.0


def test_accept_adds_the_source_and_the_view_follows(home: Path) -> None:
    d = design(home, mode="best")
    done = powerview.accept(home, d.ids[1])
    assert done.status == "accepted" and done.problems == []
    assert done.line == f"base/lines/ln-{d.ids[1]}.md"
    v = powerview.view(home, d.ids[0])
    by_id = {o["id"]: o for o in v["options"]}
    assert by_id[d.ids[1]]["status"] == "accepted"
    assert by_id[d.ids[1]]["ledger"]["counted"]
    assert v["ledger"]["lines"] == 1
    tiers = v["ledger"]["card"]["tiers"]
    assert tiers[0]["gen"] == d.plans[1]["power"]["net_eut"]
    # Option 1 on top of it: two sources at the same tier in its preview.
    first = by_id[d.ids[0]]["ledger"]["card"]["tiers"][0]
    assert first["gen"] == pytest.approx(
        d.plans[0]["power"]["net_eut"] + d.plans[1]["power"]["net_eut"]
    )
    # Accepting again is refused, as any plan's second accept is.
    with pytest.raises(designer.DesignerError, match="accepted"):
        powerview.accept(home, d.ids[1])
    with pytest.raises(designer.DesignerError, match="isn't saved"):
        powerview.accept(home, "pw-nope")


def test_ledger_preview_clears_and_adds_warnings() -> None:
    """The preview's warnings are the ledger's own, before and after (D239)."""

    def line(pid: str, *, draws: tuple[tuple[str, float], ...] = (),
             source: tuple[str, float, int] | None = None) -> ledger.Line:  # fmt: skip
        src = (
            None
            if source is None
            else ledger.Source(source[0], source[1], source[1], source[2], "gen")
        )
        dr = tuple(ledger.TierDraw(t, e, e, 1) for t, e in draws)
        kind = "power" if source else "production"
        return ledger.Line(ledger.Entry(pid, kind, pid, dr, src, ()), "accepted")

    now = ledger.build([line("pl-a", draws=(("LV", 100.0),))])
    kinds = {(w.kind, w.tier) for w in now.warnings if w.scope == "all"}
    assert kinds == {("deficit", ""), ("tier_mismatch", "LV")}
    after = ledger.build([*now.lines, line("pw-b", source=("LV", 200.0, 8))])
    assert [w for w in after.warnings if w.scope == "all"] == []
    before = powerview._warnings(now)
    cleared = [
        w
        for w in before
        if powerview._key(w)
        not in {powerview._key(x) for x in powerview._warnings(after)}
    ]
    assert {w["kind"] for w in cleared} == {"deficit", "tier_mismatch"}  # fmt: skip


# --- the explanation ---------------------------------------------------------------------------


def test_power_explanation_checks_numbers_and_order(home: Path) -> None:
    d = design(home, mode="best")
    plans = d.plans
    doc = power_explain.facts(plans)
    assert [o["id"] for o in doc["options"]] == d.ids
    # The template only says the JSON's numbers.
    text = power_explain.template(doc)
    assert validate.check(text, doc, power_explain.names(doc)).ok
    assert power_explain.explain(plans, None).source == "template"
    faithful = power_explain.explain(plans, powereval.faithful)
    assert faithful.source == "model" and faithful.attempts == 1
    # Reordered: refused twice, so the template.
    bad = power_explain.explain(plans, powereval.reordering)
    assert bad.source == "template" and bad.attempts == 2
    assert any("out of rank order" in x for x in bad.offending[0])
    # A number not in the JSON: refused, then a good retry is taken.
    calls: list[str] = []

    def once_wrong(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return f"Option 1 (plan:{d.ids[0]}) makes 987654 EU/t net."
        return powereval.faithful(prompt)

    out = power_explain.explain(plans, once_wrong)
    assert out.source == "model-retry" and "987654" in out.offending[0]
    assert "987654" in calls[1] and "Write it again" in calls[1]


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("plan:pw-a first, then plan:pw-b and plan:pw-c.", None),
        ("Option 1, then option 2.", None),
        ("plan:pw-b is good; plan:pw-a too.", "out of rank order (2, 1)"),
        ("Option 3 then option 1.", "out of rank order (3, 1)"),
        ("Option 2 is the best of them.", "calls option 2 the best"),
        ("plan:pw-c ranks first here.", "calls option 3 the best"),
        ("Option 1 is the best.", None),
        ("", None),
        # Review M1: a later recommendation in the same sentence, best before the id, and
        # "build X first".
        ("Option 1 trails badly, but option 2 is the best here.", "calls option 2"),
        (
            "plan:pw-a, plan:pw-b and plan:pw-c: build plan:pw-c first.",
            "calls option 3",
        ),
        ("The best line is plan:pw-c", "calls option 3"),
        ("Option 1 leads. Pick option 3 instead.", "calls option 3"),
        ("Option 1 leads. Go with option 3.", "calls option 3"),
        ("Opt for plan:pw-b if fuel is short.", "calls option 2"),
        ("Option 1 and option 2 are close; I'd recommend option 2.", "calls option 2"),
        ("Option 1 (plan:pw-a) is the best, option 2 is second best.", None),
        ("Option 1 costs more to build, option 2 is cheaper to build.", None),
        ("Option 1: 8,189 EU/t net. Option 2: 3.438 EU/t chain.", None),
        ("plan:pw-ab is not an option id.", None),
    ],
)
def test_in_order(text: str, why: str | None) -> None:
    got = power_explain.in_order(text, ["pw-a", "pw-b", "pw-c"])
    assert (got is None) if why is None else (got is not None and why in got)


# --- the tools ---------------------------------------------------------------------------------


def _writer(text: str) -> str:
    return powereval.faithful(text)


@pytest.fixture
def reg(home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    # The tools design with the curated farms; the fixture has none of their items.
    real = powerview.design

    def no_farms(h: Path, req: power.PowerRequest, **kw: Any) -> Any:  # noqa: ANN401
        return real(
            h,
            req,
            farms_path=home / "farms.tsv",
            with_effort=True,
            templates=chains.EMPTY,
        )

    monkeypatch.setattr(powerview, "design", no_farms)
    yield build_registry(access.db_path(home), writer=lambda: _writer)


def test_tools_are_registered_where_the_plan_says(reg: Any) -> None:  # noqa: ANN401
    deep = {t.name for t in reg.tools(DEEP)}
    fast = {t.name for t in reg.tools(FAST)}
    assert {"design_power", "effort_estimate", "plan_explain", "ledger_get"} <= deep
    assert "effort_estimate" in fast and "design_power" not in fast
    assert "design_power" not in LATER and "effort_estimate" not in LATER
    tool = reg.get("design_power")
    assert not tool.read_only and tool.gate


def _ids(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"plan:(pw-[0-9a-f]+)", text)))


def test_design_power_tool_rows_in_rank_order(reg: Any, home: Path) -> None:  # noqa: ANN401
    out = reg.call("design_power", {"mode": "best", "tier": "MV"}, lane=DEEP)
    assert not out.is_error, out.text
    ids = powerview.latest(home)
    assert 2 <= len(ids) <= 3
    body = out.text.split("\n", 3)[-1]
    at = [body.find(f"plan:{i}") for i in ids]
    assert all(a >= 0 for a in at) and at == sorted(at)
    assert rank.describe_power(rank.BALANCED, "best") in out.text
    assert "never reorder" in out.text and "accepting is his" in out.text
    # Review L5: each fuel by its key (the cite) and its name from the book.
    assert "\tfuel\tfuel_name\t" in out.text
    names = powerview.fuel_names(home, powerview.group(home, ids[0]))
    assert names and any(n != k for k, n in names.items()), names
    for key, name in names.items():
        assert f"{key}\t{name}\t" in out.text
        assert f"burning {name} ({key})" in out.text or name == key
    # The fast lane can't design.
    assert reg.call("design_power", {"mode": "best"}, lane=FAST).is_error
    # A bad request is the tool's error, not a crash.
    bad = reg.call("design_power", {"mode": "best", "tier": "XYZ"}, lane=DEEP)
    assert bad.is_error and "tier" in bad.text


def test_effort_estimate_and_plan_explain_on_a_power_design(
    reg: Any,  # noqa: ANN401
    home: Path,
) -> None:
    reg.call("design_power", {"mode": "output_per_effort", "tier": "MV"}, lane=DEEP)
    ids = powerview.latest(home)
    eff = reg.call("effort_estimate", {"id": f"plan:{ids[0]}"}, lane=FAST)
    assert not eff.is_error, eff.text
    assert "part\tmap\tmachine\trecipe\tcrafts\tmachines\thours" in eff.text
    assert f"plan: plan:{ids[0]}" in eff.text
    explained = reg.call("plan_explain", {"id": ids[0]}, lane=DEEP)
    assert not explained.is_error, explained.text
    assert re.search(r"^source: model$", explained.text, re.MULTILINE), explained.text
    missing = reg.call("effort_estimate", {"id": "pw-0000000000"}, lane=FAST)
    assert missing.is_error and "isn't saved" in missing.text


def test_the_power_designer_skill() -> None:
    """Authored in vault-template/_gos, deployed: plain front matter, only tools the deep lane
    has, no game numbers, and the rules that matter."""
    path = REPO / "vault-template" / "_gos" / "skills" / "power-designer" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines())
    assert meta["name"] == "power-designer" and set(meta) == {"name", "description"}
    assert len(meta["description"]) <= 1024
    for value in meta.values():
        assert not re.search(r": | #|^[-?:,\[\]{}#&*!|>'\"%@`]", value), value
    named = set(re.findall(r"`([a-z_]+)\(", body))
    assert {"design_power", "plan_explain", "effort_estimate", "ledger_get"} <= named
    reg = build_registry(REPO / "nowhere.sqlite")
    assert named <= {t.name for t in reg.tools(DEEP)}
    for mode in power.MODES:
        assert f"`{mode}`" in body
    assert "never change the order" in body
    assert "Accepting a power line is Clayton's" in body
    assert "!`" not in body  # no shell (D154)
    assert not re.search(r"r:[\w.]+/[0-9a-f]{12}", body)  # no game numbers
    assert not re.search(r"\b\d+ ?EU/t\b", body)


# --- the API -----------------------------------------------------------------------------------


def test_power_api(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from gregtec.api.app import create_app

    api = _Api
    real = powerview.design
    monkeypatch.setattr(
        powerview,
        "design",
        lambda h, req, **kw: real(
            h,
            req,
            farms_path=home / "farms.tsv",
            with_effort=False,
            templates=chains.EMPTY,
        ),
    )
    core = api.make_core(home)
    try:
        with TestClient(create_app(core), base_url="http://gos.localhost:8765") as c:
            spec = {"mode": "best", "tier": "MV", "free": list(tpw.FREE)}
            assert c.get("/api/power").status_code == 401
            voice = api.token(c)
            got = c.get("/api/power", headers=voice)
            assert got.status_code == 200, got.text
            assert got.json()["ids"] == [] and got.json()["ledger"]["lines"] == 0
            assert (
                c.post("/api/power/design", json=spec, headers=voice).status_code == 403
            )
            made = c.post("/api/power/design", json=spec, headers=api.admin())
            assert made.status_code == 200, made.text
            ids = made.json()["ids"]
            assert [o["id"] for o in made.json()["view"]["options"]] == ids
            assert c.get("/api/power", headers=voice).json()["ids"] == ids
            v = c.get(f"/api/power/{ids[0]}", params={"window": "60"}, headers=voice)
            assert v.status_code == 200 and v.json()["ledger"]["window_s"] == 60.0
            assert (
                c.get(
                    f"/api/power/{ids[0]}", params={"window": "x"}, headers=voice
                ).status_code
                == 400
            )
            assert c.get("/api/power/pl-abc", headers=voice).status_code == 400
            assert c.get("/api/power/pw-0000000000", headers=voice).status_code == 404
            bad = c.post("/api/power/design", json={"mode": "x"}, headers=api.admin())
            assert bad.status_code == 400 and "mode" in bad.text
            assert (
                c.post(f"/api/power/{ids[0]}/accept", headers=voice).status_code == 403
            )
            acc = c.post(f"/api/power/{ids[0]}/accept", headers=api.admin())
            assert acc.status_code == 200, acc.text
            body = acc.json()
            assert (
                body["status"] == "accepted"
                and body["view"]["options"][0]["status"] == "accepted"
            )
            again = c.post(f"/api/power/{ids[0]}/accept", headers=api.admin())
            assert again.status_code == 400
    finally:
        core.store.close()  # fmt: skip


ADMIN = "admin-" + "a" * 40


class _Api:
    """The core in-process, as tests/unit/tools/test_my_base_api.py builds it."""

    @staticmethod
    def make_core(home: Path) -> Any:  # noqa: ANN401
        from gregtec.api.app import Core
        from gregtec.api.security import Auth
        from gregtec.config import Config
        from gregtec.llm.usage import UsageMeter
        from gregtec.store import Store

        store = Store(access.db_path(home))
        return Core(
            cfg=Config(gos_home=home),
            store=store,
            auth=Auth(store, ADMIN),
            meter=UsageMeter(store, 100.0, (80, 100)),
        )

    @staticmethod
    def admin() -> dict[str, str]:
        return {"Authorization": f"Bearer {ADMIN}"}

    @classmethod
    def token(cls, client: Any, name: str = "voice") -> dict[str, str]:  # noqa: ANN401
        issued = client.post(f"/api/tokens/{name}", headers=cls.admin()).json()["token"]
        return {"Authorization": f"Bearer {issued}"}


# --- the eval ----------------------------------------------------------------------------------


def test_the_cases_cover_every_mode_at_lv_hv_iv() -> None:
    cases = powereval.load_cases()
    assert len(cases) == 12 and len({c.id for c in cases}) == 12
    assert {(c.mode, c.tier) for c in cases} == {
        (m, t) for m in power.MODES for t in ("LV", "HV", "IV")
    }


def test_the_eval_runs_and_reports(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    farms = tmp_path / "farms.tsv"
    farms.write_text("\t".join(tpw.farms.HEADER) + "\n", encoding="utf-8")
    real = powerview.design

    def fixture_design(h: Path, req: power.PowerRequest, **kw: Any) -> Any:  # noqa: ANN401
        # The fixture has none of the curated farms' or chain templates' items.
        return real(h, req, farms_path=farms, templates=chains.EMPTY)

    monkeypatch.setattr(powerview, "design", fixture_design)
    cases = [
        powereval.Case("mv-best", "best", "MV"),
        powereval.Case("mv-effort", "output_per_effort", "MV"),
    ]
    out = io.StringIO()
    results = powereval.run(db, cases, tmp_path / "scratch", out)
    for r in results:
        assert r.error is None, r.error
        assert r.ok, {k: v for k, v in r.checks.items() if not v[0]}
        assert set(r.checks) == {
            "options", "distinct", "order", "tool order", "weights", "numbers", "effort",
            "closed", "explain", "plan_explain", "view",
        }  # fmt: skip
    text = powereval.report(results, "the fixture")
    assert text.startswith("# WO-4.6 power modes")
    assert "| mv-best | best | MV |" in text and "Checks:" in text
    assert "How each option differs from option 1: option 2: " in text
    assert "PASS\tmv-best" in out.getvalue()
    # The source DB was copied, never written.
    conn = access.connect(db, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM plan").fetchone()[0] == 0
    finally:
        conn.close()  # fmt: skip


def test_the_eval_flags_the_same_line_twice_and_effort_not_counted() -> None:
    """Review M2 and L1: two options that read the same fail ``distinct``; options with
    effort 0 h and parts not counted are listed for the report."""

    def opt(pos: int, label: str, net: float, eff: float, uncounted: int) -> Any:  # noqa: ANN401
        return {
            "id": f"pw-{pos}",
            "rank": {"position": pos},
            "power": {
                "generator": {"label": label},
                "fuel": {"id": "fluid:x"},
                "net_eut": net,
                "chain_eut": 1.0,
            },
            "metrics": {
                "upkeep": 2.0,
                "eff_h": eff,
                "effort": {"uncounted": uncounted},
            },
            "nodes": [{"recipe": "r:a"}],
        }

    a, b, c = opt(1, "G", 10.0, 0, 3), opt(2, "G", 10.0, 0, 3), opt(3, "H", 9.0, 2.5, 1)
    ok, why = powereval._distinct([a, b, c])
    assert not ok and "options 1 and 2" in why
    assert powereval._distinct([a, c])[0]
    assert powereval.uncounted_effort([a, b, c]) == [
        "option 1 (3 not counted)",
        "option 2 (3 not counted)",
    ]


def test_the_eval_command(
    db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, list[powereval.Case]]] = []

    def fake_run(
        src: Path, cases: list[powereval.Case], root: Path, out: object
    ) -> list[powereval.CaseResult]:
        calls.append((src, cases))
        r = powereval.CaseResult(cases[0])
        r.checks["options"] = (True, "3 options")
        return [r]

    monkeypatch.setattr(powereval, "run", fake_run)
    report = tmp_path / "r.md"
    args = argparse.Namespace(config=None, db=db, cases=None, report=report, limit=1)
    out = io.StringIO()
    assert powereval.main(args, out) == 0
    assert calls[0][0] == db and len(calls[0][1]) == 1
    assert report.read_text(encoding="utf-8").startswith("# WO-4.6")
    assert "1 of 1 cases pass" in out.getvalue()
    missing = argparse.Namespace(
        config=None, db=tmp_path / "none.sqlite", cases=None, report=None, limit=None
    )
    assert powereval.main(missing, io.StringIO()) == 2
    # Wired into the CLI.
    from gregtec import cli

    parser_args = ["eval", powereval.COMMAND, "--db", str(db), "--limit", "1"]
    assert cli.main(parser_args) == 0


def test_json_is_canonical(home: Path) -> None:
    d = design(home, mode="best")
    raw = (home / "data" / "plans" / f"{d.ids[0]}.json").read_text(encoding="utf-8")
    assert json.loads(raw)["kind"] == "power"
