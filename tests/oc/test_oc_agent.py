"""WO-7.2: the OpenComputers agent's Lua tests, and its messages against the bridge contract.

tests/oc/run.lua runs oc/gos-agent.lua in a fake OpenComputers world (tests/oc/fakeoc.lua) on
plain Lua 5.3. This module runs that suite when a Lua 5.3 is on PATH (CI's Linux job installs one
and sets GOS_REQUIRE_LUA=1, so a missing one fails there instead of skipping), validates every
message the agent sent in it (the two-hour run's included: the runner writes each one to a
``*-req.jsonl`` file as it is sent), and every reply the standard fake core built, against
docs/contracts/oc_bridge.schema.json, and checks the golden examples in tests/fixtures/oc/ (the
ones WO-7.1 builds the core side from) both against the schema and against what the agent sends
today. The schema and golden checks need no Lua, so they run on Windows too.

To refresh the golden files after a deliberate contract change:
``lua5.3 tests/oc/run.lua --dump DIR golden`` and copy DIR/golden-*.json over
tests/fixtures/oc/ (without the ``golden-`` prefix, pretty-printed).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "docs" / "contracts" / "oc_bridge.schema.json"
CONTRACT_DOC = REPO / "docs" / "contracts" / "oc_bridge.md"
GOLDEN = REPO / "tests" / "fixtures" / "oc"
REQUIRE_LUA = os.environ.get("GOS_REQUIRE_LUA") == "1"


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator(definition: str) -> Draft202012Validator:
    schema = _schema()
    return Draft202012Validator(
        {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]}
    )


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _need(tool: str | None, what: str) -> str:
    if tool:
        return tool
    if REQUIRE_LUA:
        pytest.fail(f"GOS_REQUIRE_LUA=1 but no {what} on PATH")
    pytest.skip(f"no {what} on PATH (apt install lua5.3 lua5.2)")


def _lua53() -> str | None:
    found = _which("lua5.3", "lua53")
    if found:
        return found
    plain = shutil.which("lua")
    if plain:
        out = subprocess.run(
            [plain, "-v"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if "Lua 5.3" in out.stdout + out.stderr:
            return plain
    return None


@pytest.fixture(scope="module")
def dump(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the whole Lua suite once, writing what the agent sent into a temp folder."""
    lua = _need(_lua53(), "Lua 5.3")
    out = tmp_path_factory.mktemp("oc-dump")
    run = subprocess.run(
        [lua, "tests/oc/run.lua", "--dump", str(out)],
        check=False,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert run.returncode == 0, run.stdout[-6000:] + run.stderr[-2000:]
    return out


def _kind(path: Path) -> str:
    name = path.name
    if "-reply-" in name or name.startswith(("golden-reply", "reply-")):
        return "reply"
    return "telemetry"


def test_the_schema_is_a_valid_2020_12_schema() -> None:
    Draft202012Validator.check_schema(_schema())


@pytest.mark.parametrize("path", sorted(GOLDEN.glob("*.json")), ids=lambda p: p.name)
def test_golden_examples_match_the_schema(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = [e.message for e in _validator(_kind(path)).iter_errors(doc)]
    assert errors == []


def test_golden_examples_cover_both_documents() -> None:
    kinds = [_kind(p) for p in GOLDEN.glob("*.json")]
    assert kinds.count("telemetry") >= 2
    assert kinds.count("reply") >= 3


def test_golden_replies_fit_in_one_oc_read() -> None:
    """Plan 8 WO-7.1: the reply is at most 2 KB, the most OC reads in one call [C 1.4]."""
    for path in GOLDEN.glob("reply-*.json"):
        compact = json.dumps(
            json.loads(path.read_text(encoding="utf-8")), separators=(",", ":")
        )
        assert len(compact.encode("utf-8")) <= 2048, path.name


def test_the_lua_suite_passes(dump: Path) -> None:
    assert (dump / "golden-telemetry-first.json").is_file()


def _documents(dump: Path) -> list[tuple[str, str, str]]:
    """(kind, where, text) for every message and reply the runner dumped."""
    docs: list[tuple[str, str, str]] = []
    for path in sorted(dump.glob("*.jsonl")):
        kind = "reply" if path.name.endswith("-reply.jsonl") else "telemetry"
        lines = path.read_text(encoding="utf-8").splitlines()
        docs.extend((kind, f"{path.name}:{n}", line) for n, line in enumerate(lines, 1))
    for path in sorted(dump.glob("golden-*.json")):
        docs.append((_kind(path), path.name, path.read_text(encoding="utf-8")))
    return docs


def test_every_message_the_agent_sent_matches_the_schema(dump: Path) -> None:
    validators = {"telemetry": _validator("telemetry"), "reply": _validator("reply")}
    checked = {"telemetry": 0, "reply": 0}
    bad: list[str] = []
    for kind, where, text in _documents(dump):
        checked[kind] += 1
        for error in validators[kind].iter_errors(json.loads(text)):
            bad.append(
                f"{where}: {'/'.join(map(str, error.absolute_path))}: {error.message[:200]}"
            )
    assert bad == []
    # About 2,800 messages and 650 replies; the two-hour run alone sends over 1,300 messages.
    assert checked["telemetry"] >= 2000, checked
    assert checked["reply"] >= 400, checked


def test_the_agent_sends_ascii_only(dump: Path) -> None:
    """OC writes the POST body with the JVM's default charset, so non-ASCII text goes as \\u escapes."""
    for path in dump.glob("*-req.jsonl"):
        assert max(path.read_bytes(), default=0) < 0x80, path.name


def test_the_golden_examples_are_what_the_agent_sends(dump: Path) -> None:
    for path in sorted(GOLDEN.glob("*.json")):
        generated = dump / ("golden-" + path.name)
        assert generated.is_file(), (
            f"the Lua golden test no longer writes {generated.name}"
        )
        want = json.loads(path.read_text(encoding="utf-8"))
        got = json.loads(generated.read_text(encoding="utf-8"))
        assert got == want, (
            f"{path.name} differs from what the agent sends now; refresh it (module docstring)"
        )


def test_sensor_lines_go_out_raw() -> None:
    """2.9's lines come either encoded (key, two backslashes, args) or localised on the server, both
    with section-sign colour codes; the agent passes them on untouched, the core parses them."""
    doc = json.loads((GOLDEN / "telemetry-first.json").read_text(encoding="utf-8"))
    ebf = next(m for m in doc["machines"] if m["name"] == "multimachine.blastfurnace")
    assert "Problems: §c0" in ebf["sensor"]
    assert "GT5U.multiblock.scanner.efficiency\\\\§e100§r" in ebf["sensor"]
    buffer = next(p for p in doc["power"] if p["kind"] == "battery_buffer")
    assert buffer["sensor"][1].startswith(
        "GT5U.infodata.battery_buffer.stored_items\\\\"
    )


def test_the_agent_compiles_on_lua_5_2() -> None:
    """OC computers default to Lua 5.2; the agent (and the M1 mailbox it loads) must load there
    to say it needs 5.3."""
    luac = _need(_which("luac5.2", "luac52"), "luac5.2")
    run = subprocess.run(
        [
            luac,
            "-p",
            "oc/gos-agent.lua",
            "oc/json.lua",
            "oc/mailbox.lua",
            "oc/display.lua",
        ],
        check=False,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert run.returncode == 0, run.stderr


def _property_names(node: object, out: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                out.update(value)
            _property_names(value, out)
    elif isinstance(node, list):
        for value in node:
            _property_names(value, out)


def test_the_contract_document_names_every_field() -> None:
    names: set[str] = set()
    _property_names(_schema()["$defs"], names)
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    documented = set(re.findall(r"`([a-z0-9_]+)`", text))
    assert sorted(names - documented) == []
