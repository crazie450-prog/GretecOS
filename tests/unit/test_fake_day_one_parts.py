"""WO-11.3: the rehearsal's own parts (tests/rehearsal): the plan-10 report's statuses, the file
keyring and mailboxes the fakes are driven through (D336)."""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import keyring.errors
import pytest

REPO = Path(__file__).resolve().parents[2]
REHEARSAL = REPO / "tests" / "rehearsal"


def mod(name: str) -> ModuleType:
    if str(REHEARSAL) not in sys.path:
        sys.path.insert(0, str(REHEARSAL))
    return importlib.import_module(name)


def test_every_plan_10_line_is_in_the_checklist_once() -> None:
    cl = mod("day_one_checklist")
    plan = (REPO / "docs" / "implementation_plan.md").read_text(encoding="utf-8")
    section = plan.split("## 10. Day-one readiness checklist (G11)")[1].split("\n## ")[
        0
    ]
    lines = [ln for ln in section.splitlines() if ln.startswith("- [ ]")]
    assert len(lines) == len(cl.CHECKLIST) == 12
    assert [line.id for line in cl.CHECKLIST] == [f"C{i}" for i in range(1, 13)]
    assert [s for s, _, _ in cl.SCENARIOS] == [f"L{i}" for i in range(1, 11)]


def test_every_pc_item_names_a_runbook_that_exists() -> None:
    cl = mod("day_one_checklist")
    for line in cl.CHECKLIST:
        for item in line.items:
            assert item.runbook, item
            assert (REPO / "docs" / "runbooks" / item.runbook).is_file(), item.runbook


def test_the_runbook_covers_every_line_and_scenario() -> None:
    text = (REPO / "docs" / "runbooks" / "fake_day_one.md").read_text(encoding="utf-8")
    for i in range(1, 11):
        assert re.search(rf"\bL{i}\b", text), f"L{i}"
    for name in re.findall(r"\(([a-z_0-9]+\.md)\)", text):
        assert (REPO / "docs" / "runbooks" / name).is_file(), name


def test_statuses() -> None:
    cl = mod("day_one_checklist")
    R = cl.Result
    item = cl.Item("x", ("a", "b"))
    assert cl.item_status(item, {"a": R(cl.GREEN), "b": R(cl.GREEN)}) == cl.GREEN
    assert cl.item_status(item, {"a": R(cl.GREEN), "b": R(cl.RED)}) == cl.RED
    assert cl.item_status(item, {"a": R(cl.GREEN)}) == cl.NOT_RUN
    assert cl.item_status(cl.Item("y", (), "ops.md"), {}) == cl.PC
    line = cl.Line("Z", "z", (item, cl.Item("y", (), "ops.md")))
    ok = {"a": R(cl.GREEN), "b": R(cl.GREEN)}
    assert cl.line_status(line, ok) == cl.PC
    assert cl.line_status(line, {**ok, "b": R(cl.RED)}) == cl.RED
    assert cl.line_status(cl.Line("W", "w", (item,)), ok) == cl.GREEN


def test_green_needs_every_here_step_green() -> None:
    cl = mod("day_one_checklist")
    every = {s: cl.Result(cl.GREEN) for s in cl.ALL_STEPS}
    assert cl.green(every)
    assert "Rehearsal: GREEN" in cl.report(every)
    one_short = dict(every)
    one_short["soak"] = cl.Result(cl.NOT_RUN, "skipped")
    assert not cl.green(one_short)
    red = {**every, "oc_action": cl.Result(cl.RED, "no read-back")}
    text = cl.report(red)
    assert "C8 [red]" in text and "oc_action: red: no read-back" in text
    assert "Rehearsal: NOT GREEN" in text
    assert cl.unknown_steps(["soak", "sok"]) == ["sok"]


def test_file_keyring(tmp_path: Path) -> None:
    rf = mod("rfakes")
    k = rf.FileKeyring(tmp_path / "k.json")
    assert k.get_password("s", "u") is None
    k.set_password("s", "u", "v")
    assert rf.FileKeyring(tmp_path / "k.json").get_password("s", "u") == "v"
    k.delete_password("s", "u")
    with pytest.raises(keyring.errors.PasswordDeleteError):
        k.delete_password("s", "u")


def test_mailbox_in_order_once(tmp_path: Path) -> None:
    rf = mod("rfakes")
    driver, fake = rf.Mailbox(tmp_path, "voice"), rf.Mailbox(tmp_path, "voice")
    driver.send({"op": "talk", "text": "a"})
    driver.send({"op": "wake", "text": "b"})
    assert [c["text"] for c in fake.commands()] == ["a", "b"]
    assert list(fake.commands()) == []
    fake.record({"said": "x"})
    with open(fake.out, "a", encoding="utf-8") as f:
        f.write('{"half')  # a line still being written
    assert driver.results() == [{"said": "x"}]


def test_file_idle(tmp_path: Path) -> None:
    rf = mod("rfakes")
    idle = rf.FileIdle(tmp_path)
    assert idle.idle_s() == 0.0  # no file: present
    (tmp_path / "idle_s").write_text(json.dumps(600), encoding="utf-8")
    assert idle.idle_s() == 600.0
