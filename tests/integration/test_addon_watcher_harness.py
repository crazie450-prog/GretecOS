"""WO-11.1's acceptance on real exports: swapping an addon jar gives a correct diff note and flags
the plans it affects (plan 7.13, D69, QA:D9, D329-D331).

The client harness's run (``./gradlew runClient25 -Pharness``, mod/README.md) is the pack Play
has, with GoodGenerator 1.0. :func:`alter` makes a "GoodGenerator 1.1" run from a copy of it, as a
new addon jar would: one of its recipes rebalanced (the assembler recipe of a Fluid Core, which an
accepted Designer plan uses), one removed and one added (both Neutron Activator recipes), and one
new multiblock controller; the manifest is re-hashed so the run reads as a genuine export. Three
fake Prism instances in tmp_path stand in for Play, Lab and Export, and a fake Prism "launch"
exports the altered run when Export holds the new jar (synced from Play), else the original.

The chain, as Clayton would meet it: the first export is collected and built into the DB; he
designs and accepts a line; the jar is swapped in Play; pre-launch sets the stale flag and the
core says it once; post-exit asks on the dashboard, he answers there, and the export runs under
the instance lock, is collected and built; the signature diff is in the vault inbox, the new
controller is a curation task, exactly the plans citing the changed or removed recipe are flagged,
the Designer shows the flag, and its re-solve gives a route on the new export. Then a round where
only a config changed (the addon didn't) flags nothing.

It runs only with GOS_REQUIRE_HARNESS_RUN=1 and GOS_ADDON_WATCHER_HARNESS=1 (about 4 minutes,
so not in CI's mod job, which is near its limit; locally after the client harness, mod/README.md).
Nothing is written outside tmp_path; the harness's run is only read.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gregtec import cards, config, locks, playhooks
from gregtec.db import access
from gregtec.ingest import collect, fingerprint
from gregtec.packupdate import addons, plans
from gregtec.packupdate import routes as addon_routes
from gregtec.planner import designer
from gregtec.planner.request import Request
from gregtec.store import Store
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema as vault_schema

REPO = Path(__file__).resolve().parents[2]
ADDON = "GoodGenerator"
OLD_JAR, NEW_JAR = "GoodGenerator-1.0.jar", "GoodGenerator-1.1.jar"
TARGET = (
    "GoodGenerator:fluidCore@6"  # an accepted line made in the addon's assembler recipe
)
NA = "gg.recipe.neutron_activator"
NA_CONTROLLER = 32013  # the Neutron Activator, copied as the "new" controller


def harness_run() -> Path:
    if os.environ.get("GOS_REQUIRE_HARNESS_RUN") != "1":
        pytest.skip("set GOS_REQUIRE_HARNESS_RUN=1 after running the harnesses")
    if os.environ.get("GOS_ADDON_WATCHER_HARNESS") != "1":
        # About 4 minutes (three DB builds, a design and a re-solve): CI's mod job is near its
        # 60-minute limit, so it runs on request (PM note 2026-11-14). The chain runs on every
        # CI run on tiny exports (tests/unit/test_addon_watcher.py).
        pytest.skip("slow: set GOS_ADDON_WATCHER_HARNESS=1 as well to run it")
    root = REPO / "mod" / "run" / "client" / collect.EXPORTS
    runs, _ = collect.find_runs(root, allow_harness=True) if root.is_dir() else ([], [])
    if not runs:
        pytest.fail(f"no client harness run in {root} (mod/README.md)")
    return runs[0].path


def _rehash(run: Path, rel: str, m: dict[str, Any]) -> None:
    data = (run / rel).read_bytes()
    m["files"][rel] = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "lines": data.count(b"\n"),
    }


def copy_run(src: Path, dst: Path, run_id: str, hours: int) -> dict[str, Any]:
    shutil.copytree(src, dst)
    m: dict[str, Any] = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    created = dt.datetime.strptime(m["created_utc"], "%Y-%m-%dT%H:%M:%SZ")
    m["created_utc"] = (created + dt.timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    m["run_id"] = run_id
    return m


def write_manifest(dst: Path, m: dict[str, Any]) -> None:
    (dst / "manifest.json").write_text(
        json.dumps(m, indent=1), encoding="utf-8", newline="\n"
    )


def _owner(d: dict[str, Any]) -> str:
    return str((d.get("owners") or [""])[0])


def _jsonl(lines: Sequence[str]) -> str:
    return "\n".join(lines) + "\n"


def alter(src: Path, dst: Path, run_id: str, hours: int) -> int:
    """The "GoodGenerator 1.1" run (module docstring); returns the new controller's mte id."""
    m = copy_run(src, dst, run_id, hours)
    kept: list[str] = []
    done: set[str] = set()
    for line in (dst / "gt_recipes.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d["map"] == NA and _owner(d) == ADDON and "removed" not in done:
            done.add("removed")
            continue
        if d["map"] == NA and _owner(d) == ADDON and d["out"] and "added" not in done:
            done.add("added")
            new = json.loads(line)
            new["idx"] = 100_000
            new["out"][0]["n"] = int(new["out"][0]["n"]) + 1
            kept += [line, json.dumps(new, separators=(",", ":"))]
            continue
        out = d["out"][0] if d["out"] else {}
        if (
            d["map"] == "gt.recipe.assembler"
            and _owner(d) == ADDON
            and f"{out.get('item')}@{out.get('meta')}" == TARGET
            and "changed" not in done
        ):
            done.add("changed")
            d["duration"] = int(d["duration"]) * 4 // 5
            kept.append(json.dumps(d, separators=(",", ":")))
            continue
        kept.append(line)
    assert done == {"removed", "added", "changed"}, done
    (dst / "gt_recipes.jsonl").write_text(_jsonl(kept), encoding="utf-8", newline="\n")
    _rehash(dst, "gt_recipes.jsonl", m)  # counts.recipes stays: one line out, one in

    machines = (dst / "gt_machines.jsonl").read_text(encoding="utf-8").splitlines()
    used = {json.loads(x)["mte_id"] for x in machines}
    mte = next(i for i in range(32029, 32768) if i not in used)
    [na] = [json.loads(x) for x in machines if json.loads(x)["mte_id"] == NA_CONTROLLER]
    na.update(mte_id=mte, meta=mte, name="Neutron Activator Mk2")
    na["class"] = str(na["class"]) + "Mk2"
    machines.append(json.dumps(na, separators=(",", ":")))
    (dst / "gt_machines.jsonl").write_text(
        _jsonl(machines), encoding="utf-8", newline="\n"
    )
    _rehash(dst, "gt_machines.jsonl", m)
    m["counts"]["machines"] = int(m["counts"]["machines"]) + 1

    items = (dst / "items.jsonl").read_text(encoding="utf-8").splitlines()
    for x in items:
        d = json.loads(x)
        if (
            d.get("item") == "gregtech:gt.blockmachines"
            and d.get("meta") == NA_CONTROLLER
        ):
            d.update(meta=mte, name="Neutron Activator Mk2")
            items.append(json.dumps(d, separators=(",", ":")))
            break
    (dst / "items.jsonl").write_text(_jsonl(items), encoding="utf-8", newline="\n")
    _rehash(dst, "items.jsonl", m)
    m["counts"]["items"] = int(m["counts"]["items"]) + 1
    for mod in m["mods"]:
        if mod.get("modid") == ADDON:
            mod["version"] = "1.1"
    write_manifest(dst, m)
    return mte


def make_instance(root: Path, name: str) -> Path:
    inst = root / name
    game = inst / ".minecraft"
    for d in ("mods", "scripts", "config/GTNewHorizons"):
        (game / d).mkdir(parents=True)
    (inst / "instance.cfg").write_text("InstanceType=OneSix\n", encoding="utf-8")
    (inst / "mmc-pack.json").write_text('{"components": []}', encoding="utf-8")
    (game / "config/GTNewHorizons/dreamcraft.cfg").write_text("x\n", encoding="utf-8")
    (game / "mods" / OLD_JAR).write_bytes(b"gg 1.0")
    return inst


class FakePrism:
    def __init__(self, cfg: config.Config, src: Path) -> None:
        self.cfg, self.src, self.n = cfg, src, 0
        self.mte: int | None = None
        self.wait: threading.Event | None = None

    def launch(self, argv: Sequence[str]) -> None:
        assert argv[-1] == self.cfg.export
        if self.wait is not None:
            assert self.wait.wait(60), "post-exit never saw the export's lock"
        game = self.cfg.instance("export") / ".minecraft"
        exports = game / "gregteclink" / "exports"
        self.n += 1
        run_id = f"addon-{self.n}"
        if (game / "mods" / NEW_JAR).is_file():
            self.mte = alter(self.src, exports / run_id, run_id, self.n)
        else:
            write_manifest(
                exports / run_id, copy_run(self.src, exports / run_id, run_id, 0)
            )

    def clients(self, cfg: config.Config) -> list[str]:
        return []


@dataclass
class FakeCore:
    cfg: config.Config
    store: Store
    pushed: list[dict[str, Any]] = field(default_factory=list)

    def broadcast(self, message: dict[str, Any], *, trusted_only: bool = False) -> None:
        self.pushed.append(message)


def no_tell(cfg: config.Config, event: str, role: str, out: object) -> bool:
    return True


QUIET: dict[str, Any] = {"sleep": lambda s: None, "poll_s": 0.0}


def post_exit_answered(
    cfg: config.Config,
    prism: FakePrism,
    core: FakeCore,
    monkeypatch: pytest.MonkeyPatch,
    ticket: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Post-exit for real: the question on the dashboard, answered there (a thread, as his
    click); the export it starts runs in a thread under the lock, as ``gos export run`` does."""
    home = cfg.gos_home
    seen: list[dict[str, Any]] = []
    holding = threading.Event()
    held = locks.held

    def watched(h: Path) -> locks.Holder | None:
        holder = held(h)
        if holder is not None and holder.ticket == ticket:
            holding.set()
        return holder

    monkeypatch.setattr(playhooks.locks, "held", watched)

    def dashboard() -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            q = addons.current(home)
            if q is not None:
                seen.extend(addon_routes.cards(core))  # type: ignore[arg-type]
                addon_routes.tick(core)  # type: ignore[arg-type]
                addons.answer(home, q.id, True, "dashboard")
                return
            time.sleep(0.05)

    exported = io.StringIO()
    result: dict[str, int] = {}
    threads: list[threading.Thread] = []

    def spawn() -> playhooks.Started:
        def go() -> None:
            result["code"] = playhooks.run_export(
                cfg,
                exported,
                launch=prism.launch,
                clients=prism.clients,
                allow_harness=True,
                ticket=ticket,
                **QUIET,
            )

        t = threading.Thread(target=go)
        t.start()
        threads.append(t)
        return playhooks.Started(ticket, home / "logs" / "export.log", None)

    prism.wait = holding
    clicker = threading.Thread(target=dashboard)
    clicker.start()
    out = io.StringIO()
    code = playhooks.post_exit(
        cfg,
        "play",
        out,
        ask=playhooks.ask_everywhere(cfg, dialog=lambda t, q, s: None),
        spawn=spawn,
        tell=no_tell,
    )
    clicker.join(60)
    for t in threads:
        t.join(1800)
    prism.wait = None
    assert code == 0, out.getvalue()
    assert result.get("code") == 0, exported.getvalue()
    return exported.getvalue(), seen


def refs(db: Path, run_key: str, map_name: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT r.ref FROM recipe r JOIN export_run e ON e.id = r.run_id"
                " WHERE e.run_key = ? AND r.ref LIKE ? ORDER BY r.ref",
                (run_key, f"r:{map_name}/%"),
            )
        ]
    finally:
        conn.close()


def add_fixture(home: Path, pid: str, run_key: str, cites: list[str]) -> None:
    db = access.db_path(home)
    path = home / "data" / "plans" / f"{pid}.json"
    path.write_bytes(
        plans.dumps(plans.Plan(pid, pid, "accepted", run_key, tuple(cites)))
    )
    conn = sqlite3.connect(db)
    try:
        (run_id,) = conn.execute(
            "SELECT id FROM export_run WHERE run_key = ?", (run_key,)
        ).fetchone()
        conn.execute(
            "INSERT INTO plan(id, name, status, json_path, run_id) VALUES (?,?,?,?,?)",
            (pid, pid, "accepted", f"data/plans/{pid}.json", run_id),
        )
        conn.commit()
    finally:
        conn.close()


def flags(home: Path) -> dict[str, int | None]:
    conn = sqlite3.connect(access.db_path(home))
    try:
        return dict(conn.execute("SELECT id, affected_run FROM plan"))
    finally:
        conn.close()


def note_of(home: Path, text: str) -> str:
    [rel] = [
        ln.split("vault note: ", 1)[1]
        for ln in text.splitlines()
        if "vault note: " in ln
    ]
    root = home / "vault"
    body = (root / rel).read_text(encoding="utf-8")
    types = vault_schema.load_types(root / "_gos" / "schema.md")
    assert vault_schema.check_text(rel, body, types) == []
    day = dt.date.today()
    assert rel.startswith(f"inbox/ib-{day:%Y%m%d}-export-diff-{day.isoformat()}")
    return body


def test_an_addon_jar_swap_gives_the_diff_note_and_flags_the_plans_it_affects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = harness_run()
    root = tmp_path / "instances"
    for name in ("GTNH-Play", "GTNH-Lab", "GTNH-Export"):
        make_instance(root, name)
    cfg = config.Config(
        prism_instances=root,
        gos_home=tmp_path / "home",
        gos_app=tmp_path / "app",
        prism_root=tmp_path / "prism",
    )
    home = cfg.gos_home
    vault_deploy.deploy(home / "vault", REPO / "vault-template")
    prism = FakePrism(cfg, src)

    # Play's first export: recorded, collected and built into the DB, all by gos export run.
    out = io.StringIO()
    assert (
        playhooks.run_export(
            cfg,
            out,
            launch=prism.launch,
            clients=prism.clients,
            allow_harness=True,
            **QUIET,
        )
        == 0
    ), out.getvalue()
    assert "collected and built into" in out.getvalue(), out.getvalue()
    db = access.db_path(home)
    base = fingerprint.last_export(home)["run_id"]  # type: ignore[index]

    # Clayton designs a Fluid Core line and accepts it: it uses the addon's assembler recipe.
    d = designer.design(home, Request(TARGET, qty=1, tier_cap="IV"))
    [line] = [
        p
        for p in d.plans
        if any(n["recipe"].startswith("r:gt.recipe.assembler/") for n in p["nodes"])
    ]
    assembler = next(
        n["recipe"]
        for n in line["nodes"]
        if n["recipe"].startswith("r:gt.recipe.assembler/")
    )
    designer.accept(home, line["id"])
    # And two fixture plans: every Neutron Activator recipe (one of which 1.1 removes), and
    # recipes of the addon's other map, which 1.1 leaves alone.
    na_refs = refs(db, base, NA)
    same = refs(db, base, "gg.recipe.extreme_heat_exchanger")[:20]
    assert na_refs and same
    add_fixture(home, "pl-na", base, na_refs)
    add_fixture(home, "pl-same", base, same)
    core = FakeCore(cfg, Store(db))

    # Nothing changed yet: post-exit asks nothing.
    asked: list[object] = []
    out = io.StringIO()
    assert (
        playhooks.post_exit(
            cfg, "play", out, ask=lambda *a: asked.append(a), tell=no_tell
        )
        == 0
    )
    assert asked == [] and "nothing to do" in out.getvalue()

    # The jar swap in Play, then pre-launch: the stale flag and one notice.
    mods = cfg.instance("play") / ".minecraft" / "mods"
    (mods / OLD_JAR).unlink()
    (mods / NEW_JAR).write_bytes(b"gg 1.1")
    out = io.StringIO()
    assert (
        playhooks.pre_launch(cfg, "play", out, clients=prism.clients, tell=no_tell) == 0
    )
    assert "recipe data is stale" in out.getvalue()
    said = addon_routes.tick(core)  # type: ignore[arg-type]
    assert [n["kind"] for n in said] == ["recipes_stale"]
    assert addon_routes.tick(core) == []  # type: ignore[arg-type]

    # Post-exit: asked on the dashboard and answered there; export, collect, build, diff.
    exported, seen = post_exit_answered(cfg, prism, core, monkeypatch, "t-swap")
    assert [c["kind"] for c in seen] == ["export"] and cards.validate(seen[0]) == []
    assert fingerprint.load_state(home)["stale"] is False
    new_key = fingerprint.last_export(home)["run_id"]  # type: ignore[index]
    assert new_key != base and prism.mte is not None
    conn = sqlite3.connect(db)
    (new_id,) = conn.execute(
        "SELECT id FROM export_run WHERE run_key = ?", (new_key,)
    ).fetchone()
    conn.close()

    # Exactly one of the addon's recipes changed, one vanished and one appeared.
    new_na = refs(db, new_key, NA)
    assert len(set(na_refs) - set(new_na)) == 1 and len(set(new_na) - set(na_refs)) == 1
    [removed] = set(na_refs) - set(new_na)
    [added] = set(new_na) - set(na_refs)
    assert assembler in refs(db, new_key, "gt.recipe.assembler")

    # The signature diff per mod, in the vault inbox.
    note = note_of(home, exported)
    assert "recipes_added: 1\n" in note and "recipes_removed: 1\n" in note
    assert "recipes_changed: 1\n" in note and "machines_added: 1\n" in note
    assert f"\n{ADDON}\t1\t1\t1\t" in note
    assert f"removed\t{removed}\t{ADDON}\t" in note
    assert f"added\t{added}\t{ADDON}\t" in note
    assert f"changed\t{assembler}\t{ADDON}\t" in note and "ticks 200 -> 160" in note
    assert (
        f"new controller\tmte:{prism.mte}\tNeutron Activator Mk2\tmachine_fact, structure"
        in note
    )
    assert (
        f"plans_affected: plan:{line['id']}, plan:pl-na\n" in note
        or f"plans_affected: plan:pl-na, plan:{line['id']}\n" in note
    )
    task = home / addons.CURATION / f"controllers-{new_key}.tsv"
    assert f"\n{prism.mte}\tgregtech:gt.blockmachines@{prism.mte}\t" in task.read_text(
        encoding="utf-8"
    )

    # The plans it affects, and only those; the Designer shows the flag.
    got = flags(home)
    assert got[line["id"]] == new_id and got["pl-na"] == new_id
    assert got["pl-same"] is None
    assert all(v is None for k, v in got.items() if k not in (line["id"], "pl-na"))
    view = designer.view(home, line["id"])
    [shown] = [r for r in view["routes"] if r["id"] == line["id"]]
    assert shown["affected"]["run"] == new_key
    assert {"cite": assembler, "why": f"changed in {new_key}"} in shown["affected"][
        "hits"
    ]

    # One click: re-solve on the new export. Its routes are on the new run and not flagged.
    again = designer.resolve(home, line["id"])
    assert again.ids and line["id"] not in again.ids
    assert all(p["run"] == new_key for p in again.plans)
    fresh = designer.view(home, again.ids[0])
    assert all(r["affected"] is None for r in fresh["routes"])

    # A round where only a config changed (the addon didn't): nothing new is flagged.
    cfgfile = (
        cfg.instance("play") / ".minecraft" / "config/GTNewHorizons/dreamcraft.cfg"
    )
    cfgfile.write_text("x\ny\n", encoding="utf-8")
    out = io.StringIO()
    playhooks.pre_launch(cfg, "play", out, clients=prism.clients, tell=no_tell)
    assert "recipe data is stale" in out.getvalue()
    exported, _ = post_exit_answered(cfg, prism, core, monkeypatch, "t-same")
    assert "0 added, 0 removed, 0 changed" in exported
    assert "0 plans newly affected" in exported
    note = note_of(home, exported)
    assert "plans_affected: none\n" in note and "machines_added: 0\n" in note
    assert flags(home) == got | {i: None for i in again.ids}
    core.store.close()
