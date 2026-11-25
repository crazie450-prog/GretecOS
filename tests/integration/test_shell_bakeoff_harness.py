"""WO-10.11 on real harness data: a whole bake-off with the fake models (D374-D377).

Runs only with GOS_REQUIRE_HARNESS_RUN=1, after the server and client harnesses (CI's mod job;
locally mod/README.md). The home is WO-10.10's (``test_shell_workbench_harness``: the client run
built into a GOS_HOME, so the block catalog is the real one, with the structure dump loaded) and
the plans are WO-10.5's two benchmark lines (G3's steel line and G4's benzene gas-turbine line,
designed on the server run's DB), saved into that home under ids of their own. Without a real
dump (CI) each benchmark multi gets the fixture's stand-in shape in that DB, as WO-10.5's test
gives them (``test_layout_harness._stand_ins``).

On that home the shipped cases (``tests/evals/shell_bakeoff.tsv``: the steel line alone for the
small cases, both lines for the large ones) run against the three fake models, free and with no
network: every brief is written from the real catalog's names, every answer goes through the
product's gate, and the run is then rated and reported. Checked:

- the plain fake's design passes the gate in every case (it is the procedural shell's walls and
  flat roof), the ornate one's passes or is refused for too many blocks (the tight case), and the
  sloppy one's chest is refused on the real catalog and repaired in the next round;
- nothing is written into ``data/build`` (his own shells and designs): the run lives under
  ``data/bakeoff`` alone, and a bake-off build is unknown to the shell store;
- the blind view, a blind entry's 3D viewer data (real block names) and a blind compare name no
  model; after rating every case the report names all three and is a catalog card;
- deleting the run leaves the home as it was.

Nothing is written outside tmp_path; no model is called.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec.build import shell, shell_fakes, shell_store
from gregtec.build import shell_bakeoff as bo
from gregtec.build import shell_versions as versions
from gregtec.cards import checked
from gregtec.db import access
from gregtec.vault.io import VaultIO

HERE = Path(__file__).parent


def _module(name: str, path: Path) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


workbench = _module(
    "_wo1011_workbench_harness", HERE / "test_shell_workbench_harness.py"
)
laid = workbench.laid

# WO-10.10's home (the client run's catalog and the structure dump) and WO-10.5's lines.
home = workbench.home
db = workbench.db
lines = workbench.lines

STEEL, POWER = "bake-steel", "bake-power"
PLANS = {"small": [STEEL], "large": [STEEL, POWER]}


def files_under(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    )


@pytest.fixture(scope="module")
def ready(home: Path, lines: list[dict[str, Any]]) -> Path:
    """The home with the two lines saved as plans and a footprint for each of their multis."""
    multis = {
        int(m["mte"])
        for p in lines
        for n in p["nodes"]
        for m in ([n["machine"]] if isinstance(n.get("machine"), dict) else [])
        if m.get("kind") == "multiblock" and m.get("mte") is not None
    } | {
        int(g["mte"])
        for p in lines
        for n in p["nodes"]
        if n.get("kind") == "generator"
        for g in n.get("machines", [])
    }
    given = [] if laid.DATASET else laid._stand_ins(access.db_path(home), multis)
    print(f"\nbenchmark multis: {sorted(multis)}; stand-ins: {given}")
    for pid, plan in zip((STEEL, POWER), lines, strict=True):
        path = home / "data" / "plans" / f"{pid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**plan, "id": pid}), encoding="utf-8")
    return home


@pytest.fixture(scope="module")
def conn(ready: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(ready), readonly=True)
    try:
        yield c
    finally:
        c.close()


def test_a_fake_bake_off_on_the_real_catalog(
    ready: Path, conn: sqlite3.Connection
) -> None:
    home = ready
    vault = VaultIO(home / "vault")
    own_before = files_under(home / "data" / "build")
    cases = bo.load_cases()
    prepared = bo.prepare(conn, home, vault, cases, PLANS)
    assert [p.case.id for p in prepared] == [c.id for c in cases]
    for p in (
        prepared
    ):  # the brief is written from the real catalog: registry names and names
        assert "Palette (role, n, block, name, note):" in p.brief
        assert "- wall 0 " in p.brief and "@" in p.brief and "facts: {" in p.brief
        assert p.baseline > 0 and p.max_blocks >= p.baseline
        # D425's big cases: code states the target; the plan-free ones house no plan
        if p.case.target is not None:
            assert p.brief.splitlines()[-1].startswith("TARGET: about ")
            assert p.plot is not None and p.max_blocks == p.case.target * 5 // 4
        if p.case.mode == "free":
            assert p.plans == () and p.machines == 0
    models = [bo.FAKES[n] for n in shell_fakes.NAMES]
    est = bo.estimate(prepared, models, 10.0, 1_000_000)
    assert est.hosts == () and est.worst_usd == 0.0  # fakes only: nothing is contacted
    run = bo.create(home, prepared, models, PLANS, seed=20261120)
    said: list[str] = []
    designers = {m.label: shell_fakes.FakeDesigner(m.model) for m in models}
    run = bo.Runner(conn, home, vault, run, designers, say=said.append).execute(
        prepared
    )
    assert run["status"] == bo.DONE, run["why"]
    assert len(run["results"]) == len(cases) * len(models)
    assert run["spent"]["usd"] == 0.0 and run["spent"]["unpriced_tokens"] == 0
    print("\n" + "\n".join(said))

    by = {(r["case"], r["model"]): r for r in run["results"]}
    for case in cases:
        plain = by[(case.id, shell_fakes.PLAIN)]
        assert plain["design"]["ok"] and plain["design"]["first_try"], plain["design"]
        ornate = by[(case.id, shell_fakes.ORNATE)]["design"]
        assert ornate["ok"] or "max_blocks" in ornate["refused"], ornate
        sloppy = by[(case.id, shell_fakes.SLOPPY)]["design"]
        # the chest in its first answer is refused on the real catalog, then repaired
        assert sloppy["ok"] and sloppy["repair_rounds"] == 1, sloppy
        for model in shell_fakes.NAMES:
            r = by[(case.id, model)]
            if r["design"]["ok"]:
                assert r["revise"] is not None
                assert r["revise"]["ok"] or r["revise"]["refused"], r["revise"]
    followed = [r for r in run["results"] if r["revise"] and r["revise"]["ok"]]
    assert followed, "no follow-up gave a second version"
    assert any(r["checks"]["asked"] == "pass" for r in followed)

    # apart from his own shells: nothing new in data/build, and the store doesn't know them
    assert files_under(home / "data" / "build") == own_before
    one = by[(cases[0].id, shell_fakes.PLAIN)]
    with pytest.raises(shell.ShellError, match="no shell build"):
        shell_store.load(home, one["design"]["build"])
    where = bo.entry_home(home, run["id"], cases[0].id, one["letter"])
    d = versions.get(where, one["design_id"])
    assert d["versions"][0]["asked"] == versions.clip(cases[0].brief)
    assert all(v["by"] == "architect" for v in d["versions"])

    # blind: letters only, in the view, the viewer's data and the compare
    view = bo.blind(run)
    scene = bo.scene(conn, home, run["id"], cases[0].id, one["letter"], "design")
    assert len(scene["cells"]) == one["design"]["blocks"]
    assert any(p["name"] for p in scene["palette"]), "the real catalog names the blocks"
    letters = sorted(e["letter"] for e in view["cases"][0]["entries"] if e["design"])
    cmp = bo.compare(home, run["id"], cases[0].id, letters[0], letters[1], "design")
    assert cmp["materials"] and all("@" in m["block"] for m in cmp["materials"])
    blind_text = json.dumps(view) + json.dumps(scene) + json.dumps(cmp)
    for word in ("fake-", "usd", "tokens", "seconds"):
        assert word not in blind_text, word
    root = bo.run_dir(home, run["id"])
    assert not [f for f in files_under(root) if "fake" in f]

    # rate every case, then the report names the three models
    assert bo.unrated(run) == [c.id for c in cases]
    for case in view["cases"]:
        good = [e["letter"] for e in case["entries"] if e["design"]]
        bo.rate(home, run["id"], case["id"], good[0])
    report = bo.report(home, run["id"])
    card = checked(report.card)
    assert {row[0] for row in card["rows"]} == set(shell_fakes.NAMES)
    assert report.path.is_file() and "free (fake)" in report.text
    print(report.text)

    # and the whole run goes with one call, leaving the rest of the home as it was
    before = [f for f in files_under(home) if run["id"] not in f]
    bo.delete(home, run["id"])
    assert files_under(home) == before and bo.runs(home) == []
