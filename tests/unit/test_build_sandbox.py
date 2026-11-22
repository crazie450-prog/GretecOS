"""WO-10.12: freeform shell designs (Clayton's option B: the model's own Python, sandboxed).

- the sandbox's first layer, the syntax tree (:func:`gregtec.build.sandbox.check`): every way out
  of a Python sandbox we know of is refused before anything runs;
- the second layer alone (the child's builtins and audit hook, the syntax check skipped): the
  classic escapes stop at the audit hook, and nothing reaches a shell or a file;
- the third layer: time, memory and cell limits stop a runaway script;
- a script's cells go through the whole shell gate (palette, keep-outs, doorways, max_blocks);
- the designer's prompt: its example script passes the gate, it names every op and roof style,
  and the bake-off records its version;
- the vocabulary's volumes and roofs (hip, shed, sawtooth).

Each test that starts a child process starts one or two: they run on Linux and Windows CI.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gregtec.build import (
    layout,
    sandbox,
    shell,
    shell_bakeoff,
    shell_brief,
    shell_revise,
)
from gregtec.db import access

HERE = Path(__file__).parent
ts: Any = sys.modules.get("_wo1011_shell_tests")
if ts is None:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "_wo1011_shell_tests", HERE / "test_shell.py"
    )
    assert _spec is not None and _spec.loader is not None
    ts = importlib.util.module_from_spec(_spec)
    sys.modules["_wo1011_shell_tests"] = ts
    _spec.loader.exec_module(ts)

FACTS = {"bounds": {"lo": [0, 0, 0], "hi": [9, 9, 9]}, "wall_top": 4}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return ts.make_home(tmp_path, ts.lay_tests.production(), ts.lay_tests.power())  # type: ignore[no-any-return]


@pytest.fixture
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def spec(conn: sqlite3.Connection) -> shell.Spec:
    lay = layout.plan_layout(conn, [ts.lay_tests.production()])
    return ts.spec_for(conn, lay, ts.foundry(), headroom=8)  # type: ignore[no-any-return]


# --- layer 1: the syntax tree --------------------------------------------------------------


@pytest.mark.parametrize(
    "script",
    [
        "().__class__.__base__.__subclasses__()",
        "import random\nrandom._os.system('x')",
        "from random import _os",
        "import os",
        "import subprocess",
        "from os import system",
        "from math import *",
        "import math as __m",
        "open('x')",
        "eval('1')",
        "exec('1')",
        "compile('1', 'x', 'exec')",
        "getattr(1, 'real')",
        "type(1)",
        "object()",
        "globals()",
        "vars()",
        "'{0.__class__}'.format(1)",
        "'{x}'.format_map({})",
        "class A:\n    pass",
        "with x:\n    pass",
        "def f():\n    global g",
        "async def f():\n    pass",
        "__builtins__",
        "def __init__(self):\n    pass",
        "f = lambda __x: 1",
        "def g():\n    yield 1\nx = g().gi_frame",
        "x = (",
        "",
        "#" * (sandbox.MAX_SCRIPT_CHARS + 1),
    ],
)
def test_the_syntax_check_refuses_every_way_out(script: str) -> None:
    with pytest.raises(sandbox.ScriptError):
        sandbox.check(script)


def test_the_syntax_check_takes_plain_building_code() -> None:
    sandbox.check(
        "import math, random, itertools, functools\n"
        "from math import cos, sin\n"
        "def arch(cx, r, what='wall'):\n"
        "    return [(cx + r * cos(a), r * sin(a)) for a in range(0, 4)]\n"
        "for i, (x, y) in enumerate(arch(5, 3)):\n"
        "    set_block(x, y, 0, 'wall' if i % 2 else 'trim')\n"
        "_ = sorted(ROLES)\n"
        "try:\n    fill((0, 0, 0), (1, 1, 1), 'roof:1')\nexcept ValueError:\n    pass\n"
    )


# --- layer 2: the child's builtins and audit hook, with the syntax check skipped -------------


def child(script: str, tmp_path: Path) -> dict[str, Any]:
    """The child alone, as :func:`sandbox.run` starts it but without :func:`sandbox.check`."""
    req = json.dumps({"script": script, "facts": FACTS, "max_cells": 100})
    out = subprocess.run(
        [sys.executable, "-S", "-s", "-B", "-P", str(sandbox.CHILD)],
        input=req.encode(),
        capture_output=True,
        cwd=tmp_path,
        env=sandbox._env(),
        timeout=30,
        check=False,
    )
    return json.loads(out.stdout.decode())  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    "escape",
    [
        # os._wrap_close's globals hold os.system
        (
            "for c in ().__class__.__base__.__subclasses__():\n"
            "    if c.__name__ == '_wrap_close':\n"
            "        c.__init__.__globals__['system']('echo x > canary')\n"
        ),
        # the import machinery
        (
            "g = [c for c in ().__class__.__base__.__subclasses__()"
            " if c.__name__ == 'BuiltinImporter'][0]\n"
            "g.load_module('os').system('echo x > canary')\n"
        ),
        # a private module attribute
        "import random\nrandom._os.system('echo x > canary')\n",
        # the API functions' globals reach the child's modules
        "set_block.__globals__['sys'].modules['os'].system('echo x > canary')\n",
        "set_block.__globals__['sys'].modules['io'].open('canary', 'w')\n",
    ],
)
def test_the_audit_hook_stops_the_classic_escapes(escape: str, tmp_path: Path) -> None:
    got = child(escape, tmp_path)
    assert "not allowed in a build script" in got["error"], got
    assert not (tmp_path / "canary").exists()
    assert list(tmp_path.iterdir()) == []


def test_the_child_has_no_open_eval_or_import(tmp_path: Path) -> None:
    for script, word in (
        ("open('canary', 'w')", "NameError"),
        ("eval('1')", "NameError"),
        ("import os", "only"),
        ("import functools\nfunctools.namedtuple('A', 'b')", "not allowed"),
    ):
        assert word in child(script, tmp_path)["error"]
    assert list(tmp_path.iterdir()) == []


def test_the_child_gets_no_environment() -> None:
    env = sandbox._env()
    assert set(env) <= {
        "PYTHONHASHSEED",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "SYSTEMROOT",
    }
    assert env["PYTHONHASHSEED"] == "0"


# --- layer 3: limits, and the answer ----------------------------------------------------------


def test_a_script_builds_cells_the_same_way_every_time() -> None:
    script = (
        "import math, random\n"
        "for k in range(0, 360, 15):\n"
        "    a = math.radians(k)\n"
        "    set_block(5 + 4 * math.cos(a), random.randint(0, 3), 5 + 4 * math.sin(a), 'wall')\n"
        "fill((0, 0, 0), (1, 1, 1), 'roof:1')\n"
        "clear(0, 0, 0)\n"
        "print(BOUNDS['hi'], WALL_TOP, get_block(1, 1, 1))\n"
    )
    a, b = sandbox.run(script, FACTS, 1000), sandbox.run(script, FACTS, 1000)
    assert a.cells == b.cells and len(a.cells) > 10
    assert (0, 0, 0) not in a.cells and a.cells[(1, 1, 1)] == "roof:1"
    assert a.log == ("(9, 9, 9) 4 roof:1",)


@pytest.mark.parametrize(
    ("script", "why"),
    [
        ("for i in range(200):\n    set_block(i, 0, 0, 'wall')", "more than 100"),
        ("fill((0, 0, 0), (99, 99, 99), 'wall')", "more than 100"),
        ("x = [0] * (10 ** 10)", "MemoryError"),
        ("x = 1 / 0", "ZeroDivisionError: division by zero (line 1)"),
        ("def f(n):\n    return f(n + 1)\nf(0)", "recursed"),
        ("set_block(1, 2, 'wall')", "set_block"),
        ("set_block(1, 2, 3, 4)", "palette role"),
        ("set_block(float('nan'), 0, 0, 'wall')", "finite"),
    ],
)
def test_the_limits_stop_a_runaway_script(script: str, why: str) -> None:
    with pytest.raises(sandbox.ScriptError, match=re.escape(why)):
        sandbox.run(script, FACTS, 100)


@pytest.mark.serial
def test_a_script_that_never_ends_is_stopped() -> None:
    with pytest.raises(sandbox.ScriptError, match="longer than 2 seconds"):
        sandbox.run("while True:\n    pass", FACTS, 100, timeout=2)


# --- the gate ---------------------------------------------------------------------------------


def script_op(text: str) -> dict[str, Any]:
    return {"op": "script", "script": text}


def test_a_script_design_goes_through_the_whole_gate(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    script = (
        "import math\n"
        "lo, hi = BOUNDS['lo'], BOUNDS['hi']\n"
        "for y in range(WALL_TOP + 1):\n"
        "    for x in range(lo[0], hi[0] + 1):\n"
        "        set_block(x, y, lo[2], 'wall'); set_block(x, y, hi[2], 'wall')\n"
        "    for z in range(lo[2], hi[2] + 1):\n"
        "        set_block(lo[0], y, z, 'wall'); set_block(hi[0], y, z, 'wall')\n"
        "mid, r = (lo[2] + hi[2]) / 2, (hi[2] - lo[2]) / 2\n"
        "for x in range(lo[0], hi[0] + 1):\n"
        "    for k in range(0, 181, 4):\n"
        "        a = math.radians(k)\n"
        "        set_block(x, ROOF_Y + 0.6 * r * math.sin(a), mid + r * math.cos(a), 'roof:1')\n"
        "for d in DOORWAYS:\n"
        "    clear(d['lo'], d['hi'])\n"
    )
    out = shell.design(conn, spec, [script_op(script)], "architect")
    assert out.status == "accepted", (out.reasons, out.repairs)
    lay = layout.plan_layout(conn, [ts.lay_tests.production()])
    ts.rules_hold(lay, out.cells, spec.allowed(), spec.max_blocks, spec.headroom)
    assert shell.inspect(spec, out.cells).clean
    # deterministic: the same script, the same build id and the same cells
    again = shell.design(conn, spec, [script_op(script)], "architect")
    assert again.cells == out.cells


def test_a_script_is_held_to_the_gate_like_any_design(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    door = spec.doorways()[0].box
    keep = next(v for v in spec.clear if v.kind == "keepout").box
    ring = (
        "lo, hi = BOUNDS['lo'], BOUNDS['hi']\n"
        "for y in range(WALL_TOP + 1):\n"
        "    for x in range(lo[0], hi[0] + 1):\n"
        "        set_block(x, y, lo[2], 'wall'); set_block(x, y, hi[2], 'wall')\n"
        "    for z in range(lo[2], hi[2] + 1):\n"
        "        set_block(lo[0], y, z, 'wall'); set_block(hi[0], y, z, 'wall')\n"
    )
    # a block in the doorway and one in a keep-out: removed, said, never placed
    small = (
        ring
        + f"set_block({list(door.lo)}, 'wall')\nset_block({list(keep.lo)}, 'wall')\n"
    )
    out = shell.design(conn, spec, [script_op(small)], "architect")
    assert out.status == "repaired" and not shell.inspect(spec, out.cells).breaches
    assert door.lo not in out.cells and keep.lo not in out.cells
    # most of it inside the machines' space: refused
    big = f"fill({list(keep.lo)}, {list(keep.hi)}, 'wall')\n"
    assert shell.design(conn, spec, [script_op(big)], "architect").status == "refused"
    # a block outside the palette (a chest has an inventory): refused, nothing kept
    chest = ring + "set_block(BOUNDS['lo'], 'minecraft:chest@0')\n"
    out = shell.design(conn, spec, [script_op(chest)], "architect")
    assert out.status == "refused" and not out.cells
    # an unknown role, a script error, two scripts: refused with the reason
    for ops, why in (
        ([script_op(ring + "set_block(0, 9, 0, 'lava')")], "no 'lava' entry"),
        ([script_op("x = 1 / 0")], "ZeroDivisionError"),
        ([script_op("import os")], "import os"),
        ([script_op(ring), script_op(ring)], "at most one script"),
        ([{"op": "script"}], "needs script"),
    ):
        got = shell.design(conn, spec, ops, "architect")
        assert got.status == "refused" and why in " ".join(got.reasons), got.reasons


def test_a_script_op_mixes_with_the_vocabulary(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    ops = [
        *shell.procedural(spec)[:-1],
        script_op("set_block(BOUNDS['hi'][0], ROOF_Y + 1, BOUNDS['lo'][2], 'pillar')"),
        *[o for o in shell.procedural(spec) if o["op"] == "clear"],
    ]
    out = shell.design(conn, spec, ops, "architect")
    assert (
        out.ok
        and len(out.cells)
        > len(shell.design(conn, spec, shell.procedural(spec), "procedural").cells) - 1
    )
    # the reviser says a script changed by its size, never its text
    other = [dict(o, script="x = 1\ny = 2") if o["op"] == "script" else o for o in ops]
    assert "script: rewritten (1 -> 2 lines)" in shell_revise.describe(ops, other)


# --- the prompt ----------------------------------------------------------------------------


def example_script() -> str:
    text = shell_brief.system_prompt()
    start = text.index("import math\n", text.index("An example script"))
    return text[start : text.index("\n\nThe brief gives")]


def test_the_prompts_example_script_passes_the_gate(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    out = shell.design(conn, spec, [script_op(example_script())], "architect")
    assert out.status == "accepted", (out.reasons, out.repairs)


def test_the_prompt_names_every_op_roof_and_api_and_its_version_is_recorded(
    conn: sqlite3.Connection, home: Path
) -> None:
    text = shell_brief.system_prompt()
    for word in (*shell.OPS, *shell.ROOFS):
        assert re.search(rf"\b{word}\b", text), word
    for name in (
        "set_block",
        "fill",
        "clear",
        "get_block",
        "BOUNDS",
        "WALL_TOP",
        "ROOF_Y",
        "DOORWAYS",
        "KEEP_CLEAR",
        "PALETTE",
        "MAX_BLOCKS",
    ):
        assert name in text, name
    for principle in ("silhouette", "roofline", "base course", "rhythm", "entrance"):
        assert principle in text, principle
    assert (
        len(text) < 8_000
    )  # the per-call estimate in the runbook assumes about this size
    assert "script" in shell_brief.SCHEMA["properties"]["ops"]["items"]["required"]
    rec = shell_bakeoff.prompt_record()
    assert (
        rec["version"] == shell_brief.PROMPT_VERSION == 2 and len(rec["sha256"]) == 12
    )
    assert shell_bakeoff.prompt_label({"prompt": rec}).startswith("v2 (sha ")
    assert shell_bakeoff.prompt_label({}).startswith("v1")


# --- the vocabulary's volumes and roofs --------------------------------------------------------


def test_volumes_and_the_new_roofs_are_built_and_checked(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    b, top = spec.bounds, spec.wall_top
    lo, hi = list(b.lo), list(b.hi)
    tower = {"lo": [lo[0] + 2, top + 1, lo[2] + 2], "hi": [lo[0] + 6, hi[1], lo[2] + 6]}
    base = [{"op": "walls", "role": "wall", "y": [0, top]}]
    doors = [
        {"op": "clear", "lo": list(v.box.lo), "hi": list(v.box.hi)}
        for v in spec.doorways()
    ]
    for roof in (
        {"style": "hip", "pitch": 2},
        {"style": "shed", "sides": ["north"]},
        {"style": "sawtooth", "sides": ["north"], "every": 4, "edge": "window"},
        {
            "style": "gable",
            "lo": [lo[0], top + 1, lo[2]],
            "hi": [hi[0], top + 3, hi[2]],
        },
    ):
        ops = [
            *base,
            {"op": "roof", "role": "roof", **roof},
            {"op": "walls", "role": "pillar", **tower},
            {"op": "windows", "role": "window", "every": 3, "width": 1, **tower},
            {"op": "roof", "role": "roof", "style": "hip", **tower},
            *doors,
        ]
        out = shell.design(conn, spec, ops, "architect")
        assert out.status == "accepted", (roof, out.reasons, out.repairs)
        # the tower stands above the roofline, inside its own volume
        assert any(c[1] == hi[1] and lo[0] + 2 <= c[0] <= lo[0] + 6 for c in out.cells)
    # a volume below the roof inside the layout is the machines' space: removed or refused
    inner = {"lo": list(spec.interior.lo), "hi": list(spec.interior.hi)}
    out = shell.design(
        conn, spec, [*base, {"op": "walls", "role": "wall", **inner}], "architect"
    )
    assert out.status in ("repaired", "refused")
    assert not shell.inspect(spec, out.cells).breaches
    for op, why in (
        ({"op": "walls", "role": "wall", "lo": [0, 0, 0]}, "both lo and hi"),
        ({"op": "walls", "role": "wall", "y": [0, 99], **tower}, "inside its volume"),
        ({"op": "roof", "role": "roof", "style": "gable", "every": 4}, "sawtooth"),
        ({"op": "roof", "role": "roof", "style": "flat", "sides": ["north"]}, "shed"),
        (
            {"op": "roof", "role": "roof", "style": "shed", "sides": ["north", "east"]},
            "one side",
        ),
        ({"op": "roof", "role": "roof", "style": "dome"}, "style must be one of"),
    ):
        got = shell.design(conn, spec, [*base, op], "architect")
        assert got.status == "refused" and why in " ".join(got.reasons), (
            op,
            got.reasons,
        )
