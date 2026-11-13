"""The EU ledger (WO-4.4, D238, D239): entries, per-tier sums, each warning, the note and the card,
the OC telemetry hook on fake telemetry, running lines' outputs into My base, and end to end
through the plan-accept path (production and power) with a temp vault on the WO-4.2 fixture DB.
Every item, recipe, machine and number is made up; no pack data.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from gregtec import store as core_store
from gregtec.base import state as base_state
from gregtec.base.state import MyBase
from gregtec.cards import validate as card_problems
from gregtec.db import access
from gregtec.planner import designer, graph, ledger, ledgercli
from gregtec.planner.request import Request
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema as vault_schema
from gregtec.vault.io import VaultIO

REPO = Path(__file__).resolve().parents[3]


def _fixture(name: str, alias: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


tpw = _fixture("test_power.py", "_wo44_power_fixture")
WIDGET, PLATE = tpw.tg.WIDGET, tpw.tg.PLATE


# --- pure: entries, sums, warnings ------------------------------------------------------------


def line(
    pid: str,
    status: str = "accepted",
    draws: tuple[tuple[str, float, float, int], ...] = (),
    source: tuple[str, float, int] | None = None,
) -> ledger.Line:
    return ledger.Line(
        ledger.Entry(
            pid,
            "power" if source else "production",
            pid.upper(),
            tuple(ledger.TierDraw(*d) for d in draws),
            None
            if source is None
            else ledger.Source(source[0], source[1], source[1], source[2], "gen"),
            (),
        ),
        status,
    )


def kinds(led: ledger.Ledger) -> set[tuple[str, str, str]]:
    return {(w.kind, w.scope, w.tier) for w in led.warnings}


def test_entry_of_a_production_and_a_power_plan() -> None:
    prod = {
        "id": "pl-a",
        "name": "A  line",
        "kind": "production",
        "target": {"id": "x:a@0", "rate_s": 1.5},
        "metrics": {
            "eu": [
                {"tier": "LV", "eut_avg": 10.5, "eut_peak": 30, "amps": 2},
                {"tier": "MV", "eut_avg": 64, "eut_peak": 120, "amps": 1},
            ]
        },
    }
    e = ledger.entry(prod)
    assert e.name == "A line" and e.source is None
    assert e.draws == (
        ledger.TierDraw("LV", 10.5, 30.0, 2),
        ledger.TierDraw("MV", 64.0, 120.0, 1),
    )
    assert e.outputs == (("x:a@0", 1.5),)
    power = {
        "id": "pw-b",
        "name": "B",
        "kind": "power",
        "target": {"id": "eu", "eut": 19.76},
        "metrics": {"eu": [{"tier": "MV", "eut_avg": 9.6, "eut_peak": 60, "amps": 1}]},
        "power": {
            "net_eut": 19.76,
            "gross_eut": 32.0,
            "generator": {"label": "Gen", "tier": "LV"},
            "distribution": {"tier": "LV", "voltage": 32, "amps": 1},
        },
    }
    e = ledger.entry(power)
    # The chain's own draws are inside net_eut: a source, never a line.
    assert e.draws == () and e.outputs == ()
    assert e.source == ledger.Source("LV", 19.76, 32.0, 1, "Gen")
    assert ledger.Entry.from_dict(json.loads(json.dumps(e.as_dict()))) == e


def test_per_tier_sums_and_scopes() -> None:
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 10, 30, 2), ("MV", 64, 120, 1))),
            line("pl-b", "accepted", (("LV", 5.5, 8, 1),)),
            line("pw-c", "running", source=("LV", 100, 4)),
            line("pw-d", "building", source=("MV", 256, 1)),
        ]
    )
    all_ = {t.tier: t for t in led.tiers["all"]}
    assert list(all_) == ["LV", "MV"]  # GT's tier order
    assert all_["LV"].gen_eut == 100 and all_["LV"].use_eut_avg == 15.5
    assert all_["LV"].use_eut_peak == 38 and all_["LV"].amps_out == 3
    assert all_["LV"].amps_in == 4 and all_["LV"].net_eut == 84.5
    assert all_["LV"].plans == ("pl-a", "pl-b", "pw-c")
    run = {t.tier: t for t in led.tiers["running"]}
    assert run["LV"].use_eut_avg == 10 and run["MV"].gen_eut == 0
    assert led.total("all", "gen_eut") == 356
    assert led.total("running", "use_eut_avg") == 74


def test_no_warning_when_every_tier_is_fed() -> None:
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 10, 30, 1),)),
            line("pw-c", "running", source=("LV", 32, 1)),
        ]
    )
    assert led.warnings == []


def test_deficit_warning() -> None:
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 20, 30, 1),)),
            line("pl-b", "accepted", (("LV", 20, 30, 1),)),
            line("pw-c", "running", source=("LV", 32, 2)),
        ]
    )
    # Running: 20 of 32 is fine; with the accepted line, 40 > 32.
    assert kinds(led) == {("deficit", "all", "")}
    w = led.warnings[0]
    assert w.plans == ("pl-a", "pl-b")
    assert "short 8 EU/t" in w.detail


def test_deficit_counts_every_tier_base_wide() -> None:
    # GT transformers move EU between tiers: an MV source covers LV use in the EU sum,
    # and the LV tier gets a tier mismatch instead.
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 20, 30, 1),)),
            line("pw-c", "running", source=("MV", 128, 1)),
        ]
    )
    assert ("deficit", "all", "") not in kinds(led)
    assert ("tier_mismatch", "all", "LV") in kinds(led)


def test_tier_mismatch_warning() -> None:
    led = ledger.build(
        [
            line("pl-a", "running", (("HV", 300, 500, 1), ("LV", 1, 2, 1))),
            line("pw-c", "running", source=("LV", 512, 16)),
        ]
    )
    assert kinds(led) == {
        ("tier_mismatch", "all", "HV"),
        ("tier_mismatch", "running", "HV"),
    }
    w = led.warnings[0]
    assert w.plans == ("pl-a",) and "sources at: LV" in w.detail


def test_amp_overload_warning() -> None:
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 20, 64, 3),)),
            line("pw-c", "running", source=("LV", 64, 2)),
        ]
    )
    assert kinds(led) == {
        ("amp_overload", "all", "LV"),
        ("amp_overload", "running", "LV"),
    }
    w = led.warnings[0]
    assert "3 A at once" in w.detail and "give 2 A" in w.detail
    assert w.plans == ("pl-a", "pw-c")


def test_a_running_source_scope() -> None:
    # The source isn't built yet: the running scope has a deficit and a mismatch, all doesn't.
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 20, 30, 1),)),
            line("pw-c", "building", source=("LV", 32, 1)),
        ]
    )
    assert kinds(led) == {
        ("deficit", "running", ""),
        ("tier_mismatch", "running", "LV"),
    }


# --- the note and the card --------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    vault_deploy.deploy(root, REPO / "vault-template")
    return root


def note_ok(root: Path, rel: str) -> str:
    text = (root / rel).read_text(encoding="utf-8")
    types = vault_schema.load_types(root / "_gos" / "schema.md")
    assert vault_schema.check_text(rel, text, types) == []
    return text


def test_the_note_follows_the_grammar(vault: Path) -> None:
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 20, 64, 3),)),
            line("pw-c", "running", source=("LV", 64, 2)),
        ]
    )
    VaultIO(vault).write(ledger.NOTE, ledger.render(led))
    text = note_ok(vault, ledger.NOTE)
    assert "id: base-power\n" in text and "t: power\n" in text
    assert "lines: 1\nsources: 1\n" in text
    assert "gen_eut: 64\nuse_eut_avg: 20\nuse_eut_peak: 64\nnet_eut: 44\n" in text
    assert "run_net_eut: 44\n" in text and "warnings: 2\n" in text
    assert "tel: not reconciled: no fresh OC power telemetry\n" in text
    assert "plan:pl-a\tPL-A\trunning\tLV\t20\t64\t3\n" in text
    assert "plan:pw-c\tgen\trunning\tLV\t64\t64\t2\n" in text
    assert "all\tLV\t64\t20\t64\t44\t2\t3\n" in text
    assert "amp_overload\tall\tLV\t" in text


def test_a_long_ledger_is_cut_to_the_cap(vault: Path) -> None:
    many = [
        line(f"pl-{i:03d}", "accepted", (("LV", 1, 1, 1), ("MV", 1, 1, 1)))
        for i in range(90)
    ]
    led = ledger.build(many)
    body = ledger.render(led)
    VaultIO(vault).write(ledger.NOTE, body)
    text = note_ok(vault, ledger.NOTE)
    assert len(text.splitlines()) <= ledger.NOTE_MAX
    assert "note: the lines table shows " in text and " of 180 rows" in text
    assert "deficit\tall\t-\t" in text  # the warnings are never cut first
    full = ledger.render(led, cap=None)
    assert full.count("\tLV\t1\t1\t1\n") == 90


def test_card_is_a_catalog_card() -> None:
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 20, 64, 3), ("HV", 100, 100, 1))),
            line("pw-c", "accepted", source=("LV", 64, 2)),
        ]
    )
    c = ledger.card(led)
    assert card_problems(c) == []
    by = {t["tier"]: t for t in c["tiers"]}
    assert by["LV"] == {
        "tier": "LV",
        "gen": 64.0,
        "use": 20.0,
        "net": 44.0,
        "ref": None,
    }
    assert by["HV"]["ref"] == "plan:pl-a"  # one plan at HV: its citation
    assert any(n.startswith("tier_mismatch (all, HV)") for n in c["notes"])
    assert c["source"] == {"kind": "plan", "label": "Accepted plans", "age_s": None}
    empty = ledger.card(ledger.build([]))
    assert card_problems(empty) == [] and empty["tiers"] == []
    assert empty["notes"] == ["no accepted plans yet: accept a plan to add it"]


# --- the telemetry hook on fake telemetry ------------------------------------------------------


@pytest.fixture
def tel_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = core_store.connect(
        tmp_path / "gos.sqlite"
    )  # the core's tables, WO-7.1's included
    try:
        yield conn
    finally:
        conn.close()


def fake_power(conn: sqlite3.Connection, addr: str, last_seen: int, status: str,
               samples: dict[str, list[tuple[int, float]]]) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO tel_machine(id, name, last_seen, status) VALUES (?,?,?,?)",
        (f"oc-lab/{addr}", "lsc", last_seen, status),
    )
    for key, rows in samples.items():
        for ts, value in rows:
            conn.execute(
                "INSERT INTO tel_sample(ts, src, key, value) VALUES (?,?,?,?)",
                (ts, f"oc-lab/{addr}", key, value),
            )


def test_reconcile_reads_fresh_power_entries_only(tel_db: sqlite3.Connection) -> None:
    now = 1_000_000
    assert ledger.reconcile(tel_db, now) is None  # no telemetry yet
    fake_power(tel_db, "aa", now - 5, "power",
               {"eu_in": [(now - 70, 90.0), (now - 5, 100.0)], "eu_out": [(now - 5, 40.0)]})  # fmt: skip
    fake_power(tel_db, "bb", now - 2, "power", {"eu_in": [(now - 2, 28.5)]})
    fake_power(tel_db, "cc", now - 100, "stale", {"eu_in": [(now - 100, 999.0)]})
    fake_power(tel_db, "dd", now - 1, "running", {"eu_in": [(now - 1, 777.0)]})
    # A fresh entry whose reading stopped days ago (its call fails): never carried forward.
    fake_power(tel_db, "ee", now - 2, "power", {"eu_in": [(now - 6 * 86400, 5000.0)]})
    tel = ledger.reconcile(tel_db, now)
    assert tel is not None
    assert tel.entries == 2 and tel.refs == ("tel:oc-lab/aa", "tel:oc-lab/bb")
    assert tel.eu_in == 128.5 and tel.eu_out == 40.0  # the newest samples only
    assert tel.age_s == 5
    # Age is the sample's, not the entry's: an unchanged reading rewritten 50 s ago is read.
    fake_power(tel_db, "ff", now - 1, "power", {"eu_out": [(now - 50, 2.0)]})
    tel = ledger.reconcile(tel_db, now)
    assert (
        tel is not None and tel.entries == 3 and tel.eu_out == 42.0 and tel.age_s == 50
    )
    # Older than a heartbeat before the entry was last heard: left out since, not read.
    assert ledger.HEARTBEAT_S == 60
    fake_power(tel_db, "gg", now - 1, "power", {"eu_out": [(now - 62, 7.0)]})
    tel = ledger.reconcile(tel_db, now)
    assert tel is not None and tel.entries == 3 and tel.eu_out == 42.0
    # An entry swept stale is never read, even if its row still says power within the window.
    assert ledger.reconcile(tel_db, now + 60) is None


def test_reconciled_ledger_shows_both(tel_db: sqlite3.Connection, vault: Path) -> None:
    now = 2_000_000
    fake_power(tel_db, "aa", now - 3, "power", {"eu_in": [(now - 3, 30.0)], "eu_out": [(now - 3, 18.0)]})  # fmt: skip
    led = ledger.build(
        [
            line("pl-a", "running", (("LV", 20, 30, 1),)),
            line("pw-c", "running", source=("LV", 32, 1)),
        ],
        ledger.reconcile(tel_db, now),
    )
    VaultIO(vault).write(ledger.NOTE, ledger.render(led))
    text = note_ok(vault, ledger.NOTE)
    assert "tel_eu_in: 30\ntel_eu_out: 18\n" in text
    assert (
        "tel: 1 OC power entries (summed, may double-count chained entries), oldest 3 s:"
        " tel:oc-lab/aa\n"
    ) in text
    assert "run_gen_eut: 32\n" in text  # the ledger's numbers stay the plans'
    c = ledger.card(led, "running")
    assert card_problems(c) == []
    assert c["source"] == {"kind": "oc", "label": "Accepted plans · OC", "age_s": 3}
    assert any("OC measured 30 EU/t in and 18 EU/t out" in n for n in c["notes"])


def test_the_core_store_makes_the_ledger_table(tel_db: sqlite3.Connection) -> None:
    cols = [r[1] for r in tel_db.execute(f"PRAGMA table_info({ledger.TABLE})")]
    assert cols == ["plan", "entry", "upd"]
    assert ledger.DDL.split("(", 1)[1] in core_store.SCHEMA.replace("\n", " ")


# --- end to end: accept, move, supersede, power, line outputs ----------------------------------


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tpw.make_home(tmp_path_factory.mktemp("ledger-db"))


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
    return h


def read(home: Path) -> ledger.Ledger:
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        return ledger.read(conn)
    finally:
        conn.close()


def my_base(home: Path) -> MyBase:
    conn = core_store.connect(access.db_path(home))
    try:
        return MyBase.from_db(conn)
    finally:
        conn.close()


def power_options(home: Path) -> list[dict[str, Any]]:
    farms = home / "farms.tsv"
    farms.write_text("\t".join(tpw.farms.HEADER) + "\n", encoding="utf-8")
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        return list(tpw.design(conn, farms, mode="best").options)
    finally:
        conn.close()


def test_accept_and_lifecycle_update_the_ledger(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    a, b = d.ids[0], d.ids[1]
    assert read(home).lines == []
    done = designer.accept(home, a)
    assert done.ledger["recorded"] and done.problems == []
    led = read(home)
    assert [(ln.entry.plan, ln.status) for ln in led.lines] == [(a, "accepted")]
    draws = {e["tier"]: e for e in d.plans[0]["metrics"]["eu"]}
    assert {t.tier: t.use_eut_avg for t in led.tiers["all"]} == {
        k: float(v["eut_avg"]) for k, v in draws.items()
    }
    note = note_ok(home / "vault", ledger.NOTE)
    assert f"plan:{a}\t" in note and "sources: 0\n" in note
    assert "deficit\tall\t-\t" in note  # lines, no source yet
    assert my_base(home).line_outputs() == {}  # accepted isn't running

    designer.set_status(home, a, "building")
    designer.set_status(home, a, "running")
    assert read(home).lines[0].status == "running"
    assert "deficit\trunning\t-\t" in note_ok(home / "vault", ledger.NOTE)
    assert my_base(home).line_outputs() == {WIDGET: 2.0}
    line_note = note_ok(home / "vault", f"base/lines/{designer.line_id(a)}.md")
    assert "st: running\n" in line_note

    # Superseding: b replaces a in one step; a retires and leaves the ledger.
    done = designer.accept(home, b, supersedes=a)
    assert done.status == "accepted"
    assert [(ln.entry.plan, ln.status) for ln in read(home).lines] == [(b, "accepted")]
    assert "st: retired\n" in note_ok(
        home / "vault", f"base/lines/{designer.line_id(a)}.md"
    )
    assert my_base(home).line_outputs() == {}
    # Retiring the last one empties it.
    out = designer.set_status(home, b, "retired")
    assert out.ledger["note"].startswith("taken out of the EU ledger")
    assert read(home).lines == []
    note = note_ok(home / "vault", ledger.NOTE)
    assert "lines: 0\n" in note and "warnings: 0\n" in note


def test_lifecycle_refusals(home: Path) -> None:
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    a = d.ids[0]
    with pytest.raises(designer.DesignerError, match="proposed: it can go to accepted"):
        designer.set_status(home, a, "running")
    with pytest.raises(designer.DesignerError, match="accepting is its own step"):
        designer.set_status(home, a, "accepted")
    with pytest.raises(designer.DesignerError, match="supersede itself"):
        designer.accept(home, a, supersedes=a)
    # Superseding a plan that isn't accepted refuses the whole step: a stays proposed.
    with pytest.raises(designer.DesignerError, match="it can go to accepted"):
        designer.accept(home, a, supersedes=d.ids[1])
    assert read(home).lines == []
    conn = access.connect(access.db_path(home), readonly=True)
    try:
        assert (
            conn.execute("SELECT status FROM plan WHERE id = ?", (a,)).fetchone()[0]
            == "proposed"
        )
    finally:
        conn.close()


def test_power_plan_accept_adds_a_source(home: Path) -> None:
    opt = power_options(home)[0]
    done = designer.accept_power(home, opt)
    assert done.status == "accepted" and done.problems == []
    assert done.line == f"base/lines/ln-{opt['id']}.md"
    text = note_ok(home / "vault", done.line)
    assert f"eut: {opt['power']['net_eut']:g}\n" in text
    assert f"tier: {opt['power']['distribution']['tier']}\n" in text
    led = read(home)
    [ln] = led.lines
    assert ln.entry.kind == "power" and ln.entry.source is not None
    assert ln.entry.source.eut == opt["power"]["net_eut"]
    assert led.warnings == []  # a source with no lines
    # A production line on top: the ledger compares them, with the plans' own numbers.
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    designer.accept(home, d.ids[0])
    led = read(home)
    gen, use = led.total("all", "gen_eut"), led.total("all", "use_eut_avg")
    assert gen == opt["power"]["net_eut"]
    assert (("deficit", "all", "") in kinds(led)) == (use > gen)
    src_tier = opt["power"]["distribution"]["tier"]
    for t in led.tiers["all"]:
        if t.use_eut_avg > 0 and t.tier != src_tier:
            assert ("tier_mismatch", "all", t.tier) in kinds(led)
    # The power line moves along its lifecycle too, and the record is saved data.
    designer.set_status(home, opt["id"], "building")
    assert {ln.entry.plan: ln.status for ln in read(home).lines}[
        opt["id"]
    ] == "building"
    assert (home / "data" / "plans" / f"{opt['id']}.json").is_file()
    # Accepting the same option again is refused (its row says accepted... now building).
    with pytest.raises(designer.DesignerError, match="is building"):
        designer.accept_power(home, opt)


def test_running_line_outputs_reach_the_designer(home: Path) -> None:
    def widget_graph() -> graph.Graph:
        conn = access.connect(access.db_path(home), readonly=True)
        try:
            return graph.build(
                conn, Request(WIDGET, qty=1, tier_cap="MV"), my_base(home)
            )
        finally:
            conn.close()

    assert widget_graph().item(PLATE).boundary is None  # made in the line
    d = designer.design(home, Request(PLATE, qty=2, tier_cap="MV"))
    designer.accept(home, d.ids[0])
    assert widget_graph().item(PLATE).boundary is None  # accepted isn't running
    designer.set_status(home, d.ids[0], "building")
    designer.set_status(home, d.ids[0], "running")
    assert my_base(home).line_outputs() == {PLATE: 2.0}
    assert widget_graph().item(PLATE).boundary == "line"  # D211, D252
    # Never a boundary for its own target (review H1): the running line can be redesigned
    # (scaled up here) and the new design supersedes it end to end.
    bigger = designer.design(home, Request(PLATE, qty=4, tier_cap="MV"))
    assert bigger.ids
    done = designer.accept(home, bigger.ids[0], supersedes=d.ids[0])
    assert done.status == "accepted" and done.problems == []
    assert {ln.entry.plan: ln.status for ln in read(home).lines} == {
        bigger.ids[0]: "accepted"
    }
    assert my_base(home).line_outputs() == {}  # the new line isn't running yet
    assert widget_graph().item(PLATE).boundary is None


def set_row_status(home: Path, pid: str, status: str) -> None:
    conn = core_store.connect(access.db_path(home))
    try:
        conn.execute("UPDATE plan SET status = ? WHERE id = ?", (status, pid))
    finally:
        conn.close()


def test_plans_accepted_before_the_ledger_join_it(home: Path) -> None:
    # WO-3.7's stub accepted plans without recording them (review M1): a later move or
    # `gos ledger --write` records them from their saved JSON.
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    a, b = d.ids[0], d.ids[1]
    set_row_status(home, a, "running")
    assert read(home).lines == []
    done = designer.accept(home, b)  # any move syncs, in its transaction
    assert done.problems == []
    assert {ln.entry.plan: ln.status for ln in read(home).lines} == {
        a: "running",
        b: "accepted",
    }
    assert my_base(home).line_outputs() == {WIDGET: 2.0}
    assert f"plan:{a}\t" in note_ok(home / "vault", ledger.NOTE)
    conn = core_store.connect(access.db_path(home))
    try:  # My base's panel cites the running plans (review L1)
        assert base_state.snapshot(conn)["lines"] == [
            {"key": WIDGET, "rate_s": 2.0, "plans": [f"plan:{a}"]}
        ]
    finally:
        conn.close()

    # gos ledger --write syncs too; a plan whose JSON is gone stays out and says so.
    c = designer.design(home, Request(WIDGET, qty=3, tier_cap="MV")).ids[0]
    e = designer.design(home, Request(WIDGET, qty=5, tier_cap="MV")).ids[0]
    set_row_status(home, c, "accepted")
    set_row_status(home, e, "building")
    (home / "data" / "plans" / f"{c}.json").unlink()
    code, text = cli(home, ledgercli, "ledger", "--write")
    assert code == 1  # a warning (deficit), not a failure
    assert f"plan:{c} isn't in the EU ledger: its JSON: " in text
    assert "wrote base/power.md" in text
    assert {ln.entry.plan for ln in read(home).lines} == {a, b, e}


# --- the CLIs ---------------------------------------------------------------------------------


def cli(home: Path, module: Any, name: str, *argv: str) -> tuple[int, str]:  # noqa: ANN401
    cfg = home / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    p = argparse.ArgumentParser()
    module.add_parsers(p.add_subparsers(dest="command"))
    args = p.parse_args([name, "--config", str(cfg), *argv])
    out = io.StringIO()
    return module.run(args, out), out.getvalue()


def test_cli_ledger_status_and_power_accept(home: Path) -> None:
    from gregtec.planner import plancli, powercli

    code, text = cli(home, ledgercli, "ledger")
    assert code == 0 and "lines: 0\n" in text
    pid = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV")).ids[0]
    code, text = cli(home, plancli, "plan", "--accept", pid)
    assert code == 0 and "EU ledger: added to the EU ledger (base/power.md)" in text
    assert "  warning: deficit (all): " in text
    code, text = cli(home, plancli, "plan", "--status", pid, "building")
    assert code == 0 and f"{pid}: building; line note base/lines/" in text
    code, text = cli(home, plancli, "plan", "--status", pid, "accepted")
    assert code == 2 and text.startswith("refused: ")
    code, text = cli(home, plancli, "plan", "--supersedes", pid)
    assert code == 2 and "--supersedes goes with --accept" in text
    code, text = cli(home, ledgercli, "ledger")
    assert code == 1 and f"plan:{pid}\t" in text  # a warning: exit 1
    code, text = cli(home, ledgercli, "ledger", "--json")
    assert json.loads(text)["lines"][0]["status"] == "building"
    (home / "vault" / ledger.NOTE).unlink()
    code, text = cli(home, ledgercli, "ledger", "--write")
    assert "wrote base/power.md" in text and (home / "vault" / ledger.NOTE).is_file()

    code, text = cli(home, powercli, "power", "design", "best", "--tier", "MV", "--no-effort",
                     "--accept", "9")  # fmt: skip
    assert code == 2 and "--accept 9: there are " in text
    code, text = cli(home, powercli, "power", "design", "best", "--tier", "MV", "--no-effort",
                     "--accept", "1", "--json")  # fmt: skip
    assert code == 2 and "leave out --json" in text
    code, text = cli(home, powercli, "power", "design", "best", "--tier", "MV", "--no-effort",
                     "--accept", "1")  # fmt: skip
    assert code == 0 and ": accepted; line note base/lines/ln-pw-" in text
    assert any(ln.entry.kind == "power" for ln in read(home).lines)


def test_ledger_get_tool_reads_the_ledger(home: Path) -> None:
    from gregtec.agents.tools import build_registry
    from gregtec.agents.tools.registry import FAST

    reg = build_registry(access.db_path(home))
    empty = reg.call("ledger_get", {}, lane=FAST)
    assert not empty.is_error and "no accepted plans: the ledger is empty" in empty.text
    d = designer.design(home, Request(WIDGET, qty=2, tier_cap="MV"))
    designer.accept(home, d.ids[0])
    out = reg.call("ledger_get", {}, lane=FAST)  # read-only: the fast lane has it
    assert not out.is_error
    assert f"plan:{d.ids[0]} (accepted)" in out.text
    assert "warning deficit (all): " in out.text
    assert "no fresh OC power telemetry" in out.text
    from gregtec.agents.tools import TOOLS

    assert next(t for t in TOOLS if t.name == "ledger_get").read_only
