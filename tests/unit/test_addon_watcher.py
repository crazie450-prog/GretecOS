"""WO-11.1: the addon watcher end to end, on temp folders (plan 7.13, D69, QA:D9, D329-D331).

Three fake Prism instances (Play, Lab, Export) and a fake Prism launch that "exports" the recipes
of the addon jar Export holds: GoodGenerator 1.0, or 1.1, which rebalances one of its recipes,
drops one, adds one and adds a multiblock controller. The chain: Play's first export is built
into the DB; plans cite its recipes; the jar is swapped in Play; pre-launch sets the stale flag
and the core says it once; post-exit asks on the dashboard (answered there) and the export runs
under the lock, is collected and built, and the signature diff, the curation task and the plan
flags follow. A second round where only a config changed (the addon didn't) flags nothing. Play
is a temp folder; nothing outside tmp_path is read or written.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gregtec import cards, config, locks, playhooks
from gregtec.api.security import Principal
from gregtec.db import access
from gregtec.ingest import fingerprint
from gregtec.packupdate import addons, plans
from gregtec.packupdate import routes as addon_routes
from gregtec.store import Store
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema as vault_schema

REPO = Path(__file__).resolve().parents[2]
OLD_JAR, NEW_JAR = "GoodGenerator-1.0.jar", "GoodGenerator-1.1.jar"
ADDON = "GoodGenerator"
NEW_MTE = 32029


# --- a tiny export run: one GT recipe, three of the addon's ---------------------------------------


def _recipe(idx: int, ingot: int, duration: int, owner: str) -> dict[str, Any]:
    return {
        "map": "gt.recipe.blastfurnace",
        "idx": idx,
        "category": "gt.recipe.blastfurnace",
        "eut": 120,
        "duration": duration,
        "special": 1000,
        "in": [{"item": "test:dust", "meta": ingot, "n": 1}],
        "out": [{"item": "test:ingot", "meta": ingot, "n": 1}],
        "fin": [],
        "fout": [],
        "metadata": {"coil_heat": 1000},
        "enabled": True,
        "hidden": False,
        "fake": False,
        "can_be_buffered": True,
        "needs_empty_output": False,
        "nbt_sensitive": False,
        "owners": [owner],
    }


def recipes(new: bool) -> list[dict[str, Any]]:
    """1.0: GT ingot 1; the addon's ingots 2, 3, 4. 1.1: 2 rebalanced, 3 gone, 5 new, 4 kept."""
    base = [_recipe(0, 1, 1000, "gregtech")]
    if not new:
        return base + [
            _recipe(1, 2, 500, ADDON),
            _recipe(2, 3, 500, ADDON),
            _recipe(3, 4, 500, ADDON),
        ]
    return base + [
        _recipe(1, 2, 400, ADDON),
        _recipe(3, 4, 500, ADDON),
        _recipe(4, 5, 500, ADDON),
    ]


def _machine(mte: int, name: str, mod: str) -> dict[str, Any]:
    return {
        "mte_id": mte,
        "item": "gregtech:gt.blockmachines",
        "meta": mte,
        "class": f"test.{name.replace(' ', '')}",
        "mod": mod,
        "meta_name": name.lower().replace(" ", "."),
        "name": name,
        "tier": 2,
        "multiblock": True,
        "recipe_maps": ["gt.recipe.blastfurnace"],
        "tooltip": [],
        "structure_tooltip": [],
    }


def machine_lines(new: bool) -> list[dict[str, Any]]:
    out = [_machine(1000, "Electric Blast Furnace", "gregtech")]
    if new:
        out.append(_machine(NEW_MTE, "Neutron Activator Mk2", ADDON))
    return out


def _jsonl(lines: Sequence[object]) -> bytes:
    return "".join(json.dumps(x) + "\n" for x in lines).encode("utf-8")


def write_run(exports: Path, run_id: str, created: str, new: bool) -> Path:
    lines = recipes(new)
    items = [
        {"item": f"test:{k}", "meta": m, "unlocalized": f"{k}.{m}", "name": f"{k} {m}"}
        for k in ("dust", "ingot")
        for m in range(1, 6)
    ] + [
        {
            "item": "gregtech:gt.blockmachines",
            "meta": m["meta"],
            "unlocalized": f"gt.blockmachines.{m['meta']}",
            "name": m["name"],
        }
        for m in machine_lines(new)
    ]
    maps = [
        {
            "name": "gt.recipe.blastfurnace",
            "amperage": 1,
            "backend": "gregtech.api.recipe.RecipeMapBackend",
            "frontend": "gregtech.api.recipe.RecipeMapFrontend",
            "ui": {
                "max_item_in": 6,
                "max_item_out": 6,
                "max_fluid_in": 2,
                "max_fluid_out": 2,
            },
            "nei_register": True,
            "owner_mod": "gregtech",
            "categories": [{"name": "gt.recipe.blastfurnace", "owner_mod": "gregtech"}],
            "count": len(lines),
        }
    ]
    files = {
        "gt_recipe_maps.json": json.dumps(maps).encode("utf-8") + b"\n",
        "gt_recipes.jsonl": _jsonl(lines),
        "gt_machines.jsonl": _jsonl(machine_lines(new)),
        "items.jsonl": _jsonl(items),
        "fluids.jsonl": b"",
    }
    run = exports / run_id
    run.mkdir(parents=True)
    for rel, data in files.items():
        (run / rel).write_bytes(data)
    manifest = {
        "format": "gregteclink-export/1",
        "run_id": run_id,
        "created_utc": created,
        "complete": True,
        "mode": "autoexport",
        "side": "server",
        "pack_version": "2.9.0",
        "gt_version": "5.09.54.265",
        "nei_version": None,
        "gregteclink": {"version": "test"},
        "mods": [
            {
                "modid": ADDON,
                "version": "1.1" if new else "1.0",
                "file": NEW_JAR if new else OLD_JAR,
                "sha256": None,
            }
        ],
        "files": {
            rel: {
                "sha256": hashlib.sha256(d).hexdigest(),
                "bytes": len(d),
                "lines": d.count(b"\n"),
            }
            for rel, d in files.items()
        },
        "counts": {
            "recipes": len(lines),
            "recipe_maps": 1,
            "machines": len(machine_lines(new)),
        },
        "parts": {
            "gt_dump": "ok",
            "machines": "ok",
            "refs": "ok",
            "nei": "skipped_server",
            "autoexport": "ok",
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


def make_instance(root: Path, name: str) -> Path:
    inst = root / name
    game = inst / ".minecraft"
    for d in ("mods", "scripts", "config/GTNewHorizons"):
        (game / d).mkdir(parents=True)
    (inst / "instance.cfg").write_text("InstanceType=OneSix\n", encoding="utf-8")
    (inst / "mmc-pack.json").write_text('{"components": []}', encoding="utf-8")
    (game / "config/GTNewHorizons/dreamcraft.cfg").write_text(
        "S:ModPackVersion=2.9.0\n", encoding="utf-8"
    )
    (game / "mods" / OLD_JAR).write_bytes(OLD_JAR.encode("utf-8"))
    return inst


class FakePrism:
    """``launch``: Export "exports" what the addon jar it holds makes. ``wait``: set, launch
    first waits for it (post-exit must see the export hold the lock before it finishes)."""

    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.n = 0
        self.wait: threading.Event | None = None

    def launch(self, argv: Sequence[str]) -> None:
        assert argv[-1] == self.cfg.export, "only Export is ever launched"
        if self.wait is not None:
            assert self.wait.wait(30), "post-exit never saw the export's lock"
        game = self.cfg.instance("export") / ".minecraft"
        self.n += 1
        write_run(
            game / "gregteclink" / "exports",
            f"20261114T0{self.n}0000Z",
            f"2026-11-14T0{self.n}:00:00Z",
            (game / "mods" / NEW_JAR).is_file(),
        )

    def clients(self, cfg: config.Config) -> list[str]:
        return []


@dataclass
class FakeCore:
    """What :mod:`gregtec.packupdate.routes` needs of the core."""

    cfg: config.Config
    store: Store
    pushed: list[dict[str, Any]] = field(default_factory=list)

    def broadcast(self, message: dict[str, Any], *, trusted_only: bool = False) -> None:
        self.pushed.append(message)


@pytest.fixture
def world(tmp_path: Path) -> tuple[config.Config, FakePrism]:
    root = tmp_path / "instances"
    for name in ("GTNH-Play", "GTNH-Lab", "GTNH-Export"):
        make_instance(root, name)
    cfg = config.Config(
        prism_instances=root,
        gos_home=tmp_path / "home",
        gos_app=tmp_path / "app",
        prism_root=tmp_path / "prism",
    )
    vault_deploy.deploy(cfg.gos_home / "vault", REPO / "vault-template")
    return cfg, FakePrism(cfg)


def quiet() -> dict[str, Any]:
    return {"sleep": lambda s: None, "poll_s": 0.0}


def no_tell(cfg: config.Config, event: str, role: str, out: object) -> bool:
    return True


def ref_of(db: Path, ingot: int) -> str:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT r.ref FROM recipe r JOIN recipe_output o ON o.recipe_id = r.id"
            " JOIN item i ON i.id = o.item_id WHERE i.registry_name = 'test:ingot'"
            " AND i.meta = ? ORDER BY r.run_id LIMIT 1",
            (ingot,),
        ).fetchone()
    finally:
        conn.close()
    return str(row[0])


def add_plans(cfg: config.Config) -> dict[str, str]:
    """Accepted plans on the DB's run: the addon's ingot 2 (rebalanced by 1.1), 3 (removed),
    4 (kept) and GT's ingot 1."""
    db = access.db_path(cfg.gos_home)
    conn = sqlite3.connect(db)
    run_id, run_key = conn.execute("SELECT id, run_key FROM export_run").fetchone()
    cites = {
        "pl-changed": ref_of(db, 2),
        "pl-removed": ref_of(db, 3),
        "pl-kept": ref_of(db, 4),
        "pl-gt": ref_of(db, 1),
    }
    folder = cfg.gos_home / "data" / "plans"
    folder.mkdir(parents=True, exist_ok=True)
    for pid, cite in cites.items():
        (folder / f"{pid}.json").write_bytes(
            plans.dumps(plans.Plan(pid, pid, "accepted", run_key, (cite,)))
        )
        conn.execute(
            "INSERT INTO plan(id, name, status, json_path, run_id) VALUES (?,?,?,?,?)",
            (pid, pid, "accepted", f"data/plans/{pid}.json", run_id),
        )
    conn.commit()
    conn.close()
    return cites


def flags(cfg: config.Config) -> dict[str, int | None]:
    conn = sqlite3.connect(access.db_path(cfg.gos_home))
    try:
        return dict(conn.execute("SELECT id, affected_run FROM plan").fetchall())
    finally:
        conn.close()


def run_id(cfg: config.Config, key: str) -> int:
    conn = sqlite3.connect(access.db_path(cfg.gos_home))
    try:
        return int(
            conn.execute(
                "SELECT id FROM export_run WHERE run_key = ?", (key,)
            ).fetchone()[0]
        )
    finally:
        conn.close()


def post_exit_answered(
    cfg: config.Config,
    prism: FakePrism,
    core: FakeCore,
    monkeypatch: pytest.MonkeyPatch,
    answer_by: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Post-exit for real: the question goes to the dashboard (answered there by a thread,
    as Clayton's click would, or by the dialog), and the started export runs in a thread
    under the lock, as ``gos export run`` does. Returns post-exit's and the export's output and
    the question's approval cards seen."""
    home = cfg.gos_home
    seen: list[dict[str, Any]] = []
    holding = threading.Event()
    held = locks.held

    def watched(h: Path) -> locks.Holder | None:
        holder = held(h)
        if holder is not None and holder.ticket == "t-1":
            holding.set()
        return holder

    monkeypatch.setattr(playhooks.locks, "held", watched)

    def dashboard() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            q = addons.current(home)
            if q is not None:
                seen.extend(addon_routes.cards(core))  # type: ignore[arg-type]
                addon_routes.tick(core)  # type: ignore[arg-type]
                addons.answer(home, q.id, True, "dashboard")
                return
            time.sleep(0.02)

    exported = io.StringIO()
    result: dict[str, int] = {}

    def spawn() -> playhooks.Started:
        def go() -> None:
            result["code"] = playhooks.run_export(
                cfg,
                exported,
                launch=prism.launch,
                clients=prism.clients,
                ticket="t-1",
                **quiet(),
            )

        t = threading.Thread(target=go)
        t.start()
        result_threads.append(t)
        return playhooks.Started("t-1", home / "logs" / "export.log", None)

    result_threads: list[threading.Thread] = []
    prism.wait = holding
    if answer_by == "dashboard":
        clicker = threading.Thread(target=dashboard)
        clicker.start()
        ask = playhooks.ask_everywhere(cfg, dialog=lambda t, q, s: None)
    else:
        clicker = None
        ask = playhooks.ask_everywhere(cfg, dialog=lambda t, q, s: True)
    out = io.StringIO()
    code = playhooks.post_exit(cfg, "play", out, ask=ask, spawn=spawn, tell=no_tell)
    if clicker is not None:
        clicker.join(30)
    for t in result_threads:
        t.join(60)
    prism.wait = None
    assert code == 0, out.getvalue()
    assert result.get("code") == 0, exported.getvalue()
    return out.getvalue(), exported.getvalue(), seen


def note_text(cfg: config.Config, rel: str) -> str:
    root = cfg.gos_home / "vault"
    text = (root / rel).read_text(encoding="utf-8")
    types = vault_schema.load_types(root / "_gos" / "schema.md")
    assert vault_schema.check_text(rel, text, types) == []
    return text


# --- the whole chain ----------------------------------------------------------------------------


def test_an_addon_jar_swap_end_to_end(
    world: tuple[config.Config, FakePrism], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, prism = world
    home = cfg.gos_home
    out = io.StringIO()
    assert (
        playhooks.run_export(
            cfg, out, launch=prism.launch, clients=prism.clients, **quiet()
        )
        == 0
    ), out.getvalue()
    assert "collected and built into" in out.getvalue()
    assert "nothing to diff against" in out.getvalue()
    db = access.db_path(home)
    cites = add_plans(cfg)
    core = FakeCore(cfg, Store(db))

    # Nothing changed: post-exit asks nothing, and no notice is due.
    asked: list[object] = []
    out = io.StringIO()
    assert (
        playhooks.post_exit(
            cfg, "play", out, ask=lambda *a: asked.append(a), tell=no_tell
        )
        == 0
    )
    assert asked == [] and "nothing to do" in out.getvalue()
    assert addon_routes.tick(core) == []  # type: ignore[arg-type]

    # Clayton swaps the addon jar in Play (Prism, his part).
    mods = cfg.instance("play") / ".minecraft" / "mods"
    (mods / OLD_JAR).unlink()
    (mods / NEW_JAR).write_bytes(NEW_JAR.encode("utf-8"))

    # Pre-launch: the stale flag, and one notice (spoken and on the dashboard).
    out = io.StringIO()
    assert (
        playhooks.pre_launch(cfg, "play", out, clients=prism.clients, tell=no_tell) == 0
    )
    assert "recipe data is stale" in out.getvalue()
    assert fingerprint.load_state(home)["stale"] is True
    said = addon_routes.tick(core)  # type: ignore[arg-type]
    assert [n["kind"] for n in said] == ["recipes_stale"]
    assert said[0]["say"] == addons.SAY_STALE and not re.search(r"\d", said[0]["say"])
    assert addon_routes.tick(core) == []  # type: ignore[arg-type]  # once per episode

    # Post-exit: asked on the dashboard, answered there; the export runs under the lock.
    asked_out, exported, seen = post_exit_answered(
        cfg, prism, core, monkeypatch, "dashboard"
    )
    assert "export started" in asked_out
    assert [c["kind"] for c in seen] == ["export"]
    assert cards.validate(seen[0]) == []
    assert [m["data"]["kind"] for m in core.pushed if m["type"] == "notice"] == [
        "recipes_stale",
        "export_question",
    ]
    assert addons.read_question(home) is None  # closed once answered
    assert fingerprint.load_state(home)["stale"] is False
    assert "1 added, 1 removed, 1 changed" in exported
    new_key = fingerprint.last_export(home)["run_id"]  # type: ignore[index]
    new_run = run_id(cfg, new_key)

    # The plans it affects, and only those.
    assert flags(cfg) == {
        "pl-changed": new_run,
        "pl-removed": new_run,
        "pl-kept": None,
        "pl-gt": None,
    }
    conn = access.connect(db, readonly=True)
    try:
        got = addons.affected(conn, home, "pl-changed")
        assert addons.affected(conn, home, "pl-kept") is None
    finally:
        conn.close()
    assert got == {
        "run": new_key,
        "hits": [{"cite": cites["pl-changed"], "why": f"changed in {new_key}"}],
    }

    # The signature diff in the vault inbox.
    [rel] = [
        ln.split("vault note: ", 1)[1]
        for ln in exported.splitlines()
        if "vault note: " in ln
    ]
    day = dt.date.today()
    assert rel == f"inbox/ib-{day:%Y%m%d}-export-diff-{day.isoformat()}.md"
    note = note_text(cfg, rel)
    assert "recipes_added: 1\n" in note and "recipes_removed: 1\n" in note
    assert "recipes_changed: 1\n" in note and "machines_added: 1\n" in note
    assert f"{ADDON}\t1\t1\t1\t1\n" in note
    assert "plans_affected: plan:pl-changed, plan:pl-removed\n" in note
    assert f"removed\t{cites['pl-removed']}\t{ADDON}\t" in note
    assert f"changed\t{cites['pl-changed']}\t{ADDON}\t" in note
    assert "ticks 500 -> 400" in note
    assert (
        f"new controller\tmte:{NEW_MTE}\tNeutron Activator Mk2\tmachine_fact, structure"
        in note
    )

    # The curation task for the new controller.
    task = (home / addons.CURATION / f"controllers-{new_key}.tsv").read_text(
        encoding="utf-8"
    )
    assert task.splitlines()[1].startswith(
        f"{NEW_MTE}\tgregtech:gt.blockmachines@{NEW_MTE}\tNeutron Activator Mk2\t{ADDON}\t"
    )
    assert task.splitlines()[1].endswith("\tmachine_fact, structure")

    # A second round where the addon didn't change (only a config did): it flags nothing new,
    # and the flags of the first update stay with the run that made them.
    cfgfile = (
        cfg.instance("play") / ".minecraft" / "config/GTNewHorizons/dreamcraft.cfg"
    )
    cfgfile.write_text("S:ModPackVersion=2.9.0\nB:tweak=true\n", encoding="utf-8")
    out = io.StringIO()
    playhooks.pre_launch(cfg, "play", out, clients=prism.clients, tell=no_tell)
    assert "recipe data is stale" in out.getvalue()
    _, exported, _ = post_exit_answered(cfg, prism, core, monkeypatch, "dialog")
    assert "0 added, 0 removed, 0 changed" in exported
    assert "0 plans newly affected" in exported
    assert "still affected: pl-changed, pl-removed" in exported
    assert flags(cfg) == {
        "pl-changed": new_run,
        "pl-removed": new_run,
        "pl-kept": None,
        "pl-gt": None,
    }
    [rel2] = [
        ln.split("vault note: ", 1)[1]
        for ln in exported.splitlines()
        if "vault note: " in ln
    ]
    note2 = note_text(cfg, rel2)
    assert rel2 != rel and "plans_affected: none\n" in note2
    assert "recipes_added: 0\n" in note2 and "machines_added: 0\n" in note2


def test_a_plan_fine_again_is_cleared(
    world: tuple[config.Config, FakePrism], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The addon goes back to 1.0: the plans its 1.1 broke match their own run again."""
    cfg, prism = world
    out = io.StringIO()
    assert (
        playhooks.run_export(
            cfg, out, launch=prism.launch, clients=prism.clients, **quiet()
        )
        == 0
    )
    add_plans(cfg)
    core = FakeCore(cfg, Store(access.db_path(cfg.gos_home)))
    mods = cfg.instance("play") / ".minecraft" / "mods"
    (mods / OLD_JAR).unlink()
    (mods / NEW_JAR).write_bytes(b"1.1")
    post_exit_answered(cfg, prism, core, monkeypatch, "dialog")
    assert {k for k, v in flags(cfg).items() if v} == {"pl-changed", "pl-removed"}
    (mods / NEW_JAR).unlink()
    (mods / OLD_JAR).write_bytes(b"1.0 again")
    _, exported, _ = post_exit_answered(cfg, prism, core, monkeypatch, "dialog")
    assert "no longer affected: pl-changed, pl-removed" in exported
    assert set(flags(cfg).values()) == {None}


# --- the export question ------------------------------------------------------------------------


def test_the_question_takes_one_answer_by_its_id(tmp_path: Path) -> None:
    q = addons.open_question(tmp_path, "T", "Export now?", 300, now=1000.0)
    assert addons.current(tmp_path, now=1001.0) == q
    assert addons.answer(tmp_path, "other", True, "dashboard", now=1001.0) is None
    with pytest.raises(ValueError, match="via"):
        addons.answer(tmp_path, q.id, True, "model", now=1001.0)
    done = addons.answer(tmp_path, q.id, False, "phone", now=1001.0)
    assert done is not None and (done.answer, done.via) == ("no", "phone")
    assert addons.answer(tmp_path, q.id, True, "dashboard", now=1002.0) is None
    assert addons.current(tmp_path, now=1002.0) is None
    late = addons.open_question(tmp_path, "T", "Export now?", 300, now=2000.0)
    assert addons.current(tmp_path, now=2301.0) is None  # expired
    assert addons.answer(tmp_path, late.id, True, "dashboard", now=2301.0) is None
    addons.close(tmp_path, "someone-else")
    assert addons.read_question(tmp_path) is not None
    addons.close(tmp_path, late.id)
    assert addons.read_question(tmp_path) is None


def test_ask_takes_the_first_answer_and_times_out_as_no_answer(tmp_path: Path) -> None:
    # The dialog answers first.
    assert (
        addons.ask(tmp_path, "T", "Q", 5, dialog=lambda t, q, s: False, poll_s=0.01)
        is False
    )
    assert addons.read_question(tmp_path) is None
    # No dialog, nobody answers: None at the deadline, and the question is gone.
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(s: float) -> None:
        now[0] += 1.0

    assert addons.ask(tmp_path, "T", "Q", 3, clock=clock, sleep=sleep) is None
    assert addons.read_question(tmp_path) is None

    # The dashboard answers while the dialog shows nothing.
    def answer_soon() -> None:
        for _ in range(500):
            q = addons.current(tmp_path)
            if q is not None:
                addons.answer(tmp_path, q.id, True, "dashboard")
                return
            time.sleep(0.01)

    t = threading.Thread(target=answer_soon)
    t.start()
    assert (
        addons.ask(tmp_path, "T", "Q", 10, dialog=lambda t_, q, s: None, poll_s=0.01)
        is True
    )
    t.join()


def test_a_question_that_cannot_be_written_falls_back_to_the_dialog(
    tmp_path: Path,
) -> None:
    (tmp_path / "home").write_text("a file, not a folder", encoding="utf-8")
    cfg = config.Config(gos_home=tmp_path / "home")
    ask = playhooks.ask_everywhere(cfg, dialog=lambda t, q, s: True)
    assert ask("T", "Q", 5) is True


def test_notices_are_due_once_and_say_no_numbers(tmp_path: Path) -> None:
    home = tmp_path
    assert addons.due_notices(home, []) == []
    state = {
        "format": fingerprint.FORMAT,
        "exports": [],
        "stale": True,
        "play": {"fingerprint": "ab" * 32},
    }
    fingerprint.atomic_write(
        home / fingerprint.STATE, json.dumps(state).encode("utf-8")
    )
    q = addons.open_question(home, "Mods changed. Export now?", "Export now?", 300)
    due = addons.due_notices(home, [])
    assert [k for k, _ in due] == [f"stale:{'ab' * 32}", f"question:{q.id}"]
    assert all(not re.search(r"\d", n["say"]) for _, n in due)
    assert addons.due_notices(home, [k for k, _ in due]) == []
    # A new Play fingerprint is a new episode.
    state["play"] = {"fingerprint": "cd" * 32}
    fingerprint.atomic_write(
        home / fingerprint.STATE, json.dumps(state).encode("utf-8")
    )
    assert [k for k, _ in addons.due_notices(home, [k for k, _ in due])] == [
        f"stale:{'cd' * 32}"
    ]


# --- the routes ---------------------------------------------------------------------------------


def app_for(core: FakeCore, who: Principal) -> TestClient:
    app = FastAPI()

    def principal(request: Request) -> Principal:
        return who

    addon_routes.add_routes(app, core, principal)  # type: ignore[arg-type]
    return TestClient(app)


def test_the_routes_answer_by_id_and_only_clayton_says_yes(tmp_path: Path) -> None:
    cfg = config.Config(gos_home=tmp_path)
    core = FakeCore(cfg, Store(tmp_path / "gos.sqlite"))
    device = app_for(core, Principal("device", "monitor-2", "d1"))
    assert device.get("/api/export/question").json() == {"question": None}
    q = addons.open_question(tmp_path, "Mods changed. Export now?", "Export now?", 300)
    got = device.get("/api/export/question").json()["question"]
    assert got["id"] == q.id and got["title"] == "Mods changed. Export now?"
    assert device.post("/api/export/question/nope/confirm").status_code == 409
    admin = app_for(core, Principal("admin", "admin"))
    assert admin.post(f"/api/export/question/{q.id}/confirm").status_code == 403
    oc = app_for(core, Principal("client", "oc"))
    assert oc.post(f"/api/export/question/{q.id}/cancel").status_code == 403
    r = device.post(
        f"/api/export/question/{q.id}/confirm", headers={"host": cfg.dashboard_host}
    )
    assert r.json() == {"ok": True, "answer": "yes", "via": "dashboard"}
    assert device.post(f"/api/export/question/{q.id}/cancel").status_code == 409
    q2 = addons.open_question(tmp_path, "T", "Q", 300)
    assert admin.post(f"/api/export/question/{q2.id}/cancel").json()["via"] == "admin"
    voice = app_for(core, Principal("client", "voice"))
    q3 = addons.open_question(tmp_path, "T", "Q", 300)
    assert voice.post(f"/api/export/question/{q3.id}/confirm").json()["via"] == "voice"
    core.store.close()


def test_the_approval_card_is_a_catalog_card(tmp_path: Path) -> None:
    q = addons.open_question(tmp_path, "Mods changed. Export now?", "x" * 2000, 300)
    card = addons.approval_card(q)
    assert cards.validate(card) == []
    assert [a["id"] for a in card["actions"]] == ["confirm", "cancel"]
