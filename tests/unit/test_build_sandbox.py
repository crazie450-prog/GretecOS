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

import dataclasses
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
        [sandbox.interpreter(), "-S", "-s", "-B", "-P", str(sandbox.CHILD)],
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
        # functions no audit event covers (review of WO-10.12): a signal to GOS, a FIFO, a
        # fresh copy of posix from the import machinery, a forged answer on stdout
        (
            "m = set_block.__globals__['sys'].modules\n"
            "m['_signal'].pidfd_send_signal(m['os'].pidfd_open(m['os'].getppid()), 0)\n"
        ),
        "set_block.__globals__['sys'].modules['posix'].mkfifo('canary')\n",
        (
            "m = set_block.__globals__['sys'].modules\n"
            "p = m['_imp'].create_builtin(m['_frozen_importlib'].ModuleSpec('posix', None))\n"
            "p.mkfifo('canary')\n"
        ),
        (
            "m = set_block.__globals__['sys'].modules\n"
            "s = m['math'].__spec__\n"
            "s.loader.create_module(s.__class__('posix', None)).mkfifo('canary')\n"
        ),
        "set_block.__globals__['__builtins__'].__import__('ctypes')\n",
        "set_block.__globals__['sys'].modules['os'].write(1, b'{\"cells\": []}')\n",
    ],
)
def test_the_audit_hook_stops_the_classic_escapes(escape: str, tmp_path: Path) -> None:
    got = child(escape, tmp_path)
    assert "error" in got and "cells" not in got, got
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


def test_the_child_is_started_without_the_venv_launcher() -> None:
    exe = sandbox.interpreter()
    assert Path(exe).is_file()
    if (
        sys.platform == "win32"
    ):  # the venv's python.exe would start a second process (D380)
        assert Path(exe).resolve() == Path(sys._base_executable).resolve()


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
        "lo, hi = WALLS['lo'], WALLS['hi']\n"
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
    ts.rules_hold(
        lay, out.cells, spec.allowed(), spec.max_blocks, spec.headroom, spec.relief
    )
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
        "lo, hi = WALLS['lo'], WALLS['hi']\n"
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
        "WALLS",
        "RELIEF",
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
        rec["version"] == shell_brief.PROMPT_VERSION == 3 and len(rec["sha256"]) == 12
    )
    assert shell_bakeoff.prompt_label({"prompt": rec}).startswith("v3 (sha ")
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


# --- what the adversarial review of WO-10.12 found ----------------------------------------------


def test_a_huge_volume_is_clamped_not_looped_over(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    import time

    far = {"lo": [-100_000, 0, -100_000], "hi": [100_000, 100_000, 100_000]}
    start = time.monotonic()
    out = shell.design(
        conn, spec, [{"op": "roof", "role": "roof", "style": "hip", **far}], "architect"
    )
    assert time.monotonic() - start < 10
    assert out.ok or out.reasons
    away = {"lo": [500, 0, 500], "hi": [600, 5, 600]}
    got = shell.design(
        conn, spec, [{"op": "walls", "role": "wall", **away}], "architect"
    )
    assert got.status == "refused" and "outside the bounding volume" in got.reasons[0]


def test_a_unicode_digit_is_not_a_role_number(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    for what in ("wall:²", "wall:٠"):
        got = shell.design(
            conn, spec, [script_op(f"set_block(0, 9, 0, {what!r})")], "architect"
        )
        assert got.status == "refused" and "role:number" in got.reasons[0]


def test_the_child_flooding_its_pipes_is_stopped(tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys\nwhile True: sys.stdout.buffer.write(b'x' * 65536)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with pytest.raises(sandbox.ScriptError, match="too large|longer than"):
        sandbox._talk(proc, b"", 20)
    proc.wait(timeout=10)


def rec_of(spec: shell.Spec, ops: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ops": ops,
        "headroom": spec.headroom,
        "wall_top": spec.wall_top,
        "roof_y": spec.roof_y,
        "bounds": spec.bounds.to_json(),
        "palette": [{"role": s.role, "block": s.entry.key} for s in spec.swatches],
    }


def test_the_reviser_on_new_roofs_volumes_and_scripts(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    top = spec.wall_top
    base = [{"op": "walls", "role": "wall", "y": [0, top]}]
    doors = [
        {"op": "clear", "lo": list(v.box.lo), "hi": list(v.box.hi)}
        for v in spec.doorways()
    ]
    # a flat roof drops a shed's high side and a sawtooth's tooth: the gate takes it
    for roof in (
        {"style": "shed", "sides": ["south"]},
        {"style": "sawtooth", "sides": ["north"], "every": 5},
    ):
        ops = [*base, {"op": "roof", "role": "roof", **roof}, *doors]
        for words in ("flat roof", "flatter"):
            r = shell_revise.revise(rec_of(spec, ops), words)
            assert r.ok, (roof, words, r)
            assert shell.design(conn, spec, list(r.ops), "architect").ok
    # steeper keeps a hip a hip
    r = shell_revise.revise(
        rec_of(spec, [*base, {"op": "roof", "role": "roof", "style": "hip"}]), "steeper"
    )
    assert next(o for o in r.ops if o["op"] == "roof")["style"] == "hip"
    # taller moves a capped roof's volume and a tower with it
    b = spec.bounds
    roof_vol = {
        "lo": [b.lo[0], top + 1, b.lo[2]],
        "hi": [b.hi[0], b.hi[1] - 2, b.hi[2]],
    }
    tower = {
        "lo": [b.lo[0], top + 1, b.lo[2]],
        "hi": [b.lo[0] + 3, b.hi[1], b.lo[2] + 3],
    }
    ops = [
        *base,
        {"op": "roof", "role": "roof", "style": "gable", **roof_vol},
        {"op": "walls", "role": "pillar", **tower},
        *doors,
    ]
    r = shell_revise.revise(rec_of(spec, ops), "taller")
    assert r.ok and r.headroom == spec.headroom + 2
    moved = [o for o in r.ops if "lo" in o and o["op"] != "clear"]
    assert all(o["lo"][1] == top + 3 for o in moved)
    grown = ts.spec_for(
        conn,
        layout.plan_layout(conn, [ts.lay_tests.production()]),
        ts.foundry(),
        headroom=r.headroom,
    )
    assert shell.design(conn, grown, list(r.ops), "architect").ok
    # a shell drawn only by its script can't be made taller by GOS's reviser: it says so
    r = shell_revise.revise(rec_of(spec, [script_op("x = 1"), *doors]), "taller")
    assert not r.ok and any("build script" in w for w in r.impossible)
    # a parapet on a roof that closes inside its volume needs no more headroom
    flat_vol = [
        *base,
        {
            "op": "roof",
            "role": "roof",
            "style": "flat",
            "lo": [b.lo[0], top + 1, b.lo[2]],
            "hi": [b.hi[0], b.hi[1], b.hi[2]],
        },
    ]
    r = shell_revise.revise(rec_of(spec, flat_vol), "add a parapet")
    assert r.ok and r.headroom == spec.headroom


def test_a_repair_round_echoes_a_whole_script() -> None:
    script = "\n".join(f"set_block({i}, 0, 0, 'wall')" for i in range(400))
    answer = json.dumps({"ops": [{"op": "script", "script": script}]})
    assert len(answer) > 4_000
    text = shell_brief.repair_brief("brief", answer, "why")
    assert (
        script.splitlines()[-1]
        in json.loads(
            text.split("Your previous answer was:\n", 1)[1].split(
                "\n\nThe code refused", 1
            )[0]
        )["ops"][0]["script"]
    )


def test_the_prompts_example_passes_at_the_default_headroom(
    conn: sqlite3.Connection,
) -> None:
    lay = layout.plan_layout(conn, [ts.lay_tests.production()])
    spec = ts.spec_for(conn, lay, ts.foundry())  # headroom 4, as the bake-off's cases
    out = shell.design(conn, spec, [script_op(example_script())], "architect")
    assert out.status == "accepted", (out.reasons, out.repairs)


# --- facades that stand out: the relief zone (WO-10.13, D386) ----------------------------------


def test_facades_stand_out_into_the_relief_zone(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    r, w, b = spec.relief, spec.walls, spec.bounds
    assert r == shell.LIMITS["relief"][0] == 3
    assert b.lo[0] == w.lo[0] - r and b.hi[2] == w.hi[2] + r and b.hi[1] == w.hi[1]
    doors = [
        {"op": "clear", "lo": list(v.box.lo), "hi": list(v.box.hi)}
        for v in spec.doorways()
    ]
    # the doorway runs out through the relief zone: nothing may block the way in
    assert all(
        v.box.lo[0] == b.lo[0]
        or v.box.hi[0] == b.hi[0]
        or v.box.lo[2] == b.lo[2]
        or v.box.hi[2] == b.hi[2]
        for v in spec.doorways()
    )
    # a ring with a negative inset (a stepped plinth), buttresses from a script, an
    # overhanging eave: all accepted, and all of it outside the wall line
    script = (
        "lo, hi = WALLS['lo'], WALLS['hi']\n"
        "for x in range(lo[0], hi[0] + 1, 4):\n"
        "    fill((x, 0, lo[2] - RELIEF), (x, WALL_TOP - 1, lo[2] - 1), 'pillar')\n"
        "fill((BOUNDS['lo'][0], ROOF_Y, BOUNDS['lo'][2]), (BOUNDS['hi'][0], ROOF_Y, "
        "BOUNDS['hi'][2]), 'roof')\n"
    )
    ops = [
        {"op": "walls", "role": "wall", "y": [0, spec.wall_top]},
        {"op": "walls", "role": "trim", "y": [0, 0], "inset": -1},
        script_op(script),
        *doors,
    ]
    out = shell.design(conn, spec, ops, "architect")
    assert out.status == "accepted", (out.reasons, out.repairs)
    outside = [c for c in out.cells if not w.contains(c)]
    assert outside and all(b.contains(c) for c in outside)
    assert (w.lo[0] - 1, 0, w.lo[2] + 2) in out.cells  # the stepped plinth
    assert any(
        c[2] == w.lo[2] - r and c[1] < spec.wall_top for c in out.cells
    )  # buttress
    assert (b.lo[0], spec.roof_y, b.lo[2]) in out.cells  # the eave's corner
    # an inset past the relief zone is refused
    bad = {"op": "walls", "role": "wall", "inset": -(r + 1)}
    assert shell.design(conn, spec, [bad], "architect").status == "refused"


def test_a_build_from_before_the_relief_zone_keeps_its_size_and_id(
    conn: sqlite3.Connection,
) -> None:
    lay = layout.plan_layout(conn, [ts.lay_tests.production()])
    old = ts.spec_for(conn, lay, ts.foundry(), relief=0)
    new = ts.spec_for(conn, lay, ts.foundry())
    assert old.bounds == old.walls == new.walls
    ops = shell.procedural(old)
    # the same cells: the vocabulary is drawn on the wall line either way
    assert (
        shell.design(conn, old, ops, "procedural").cells
        == shell.design(conn, new, ops, "procedural").cells
    )
    assert "relief" not in json.dumps(
        {k: v for k, v in shell.spec_json(old).items() if k != "relief"}
    )
    assert shell.build_id(old, ops) != shell.build_id(new, ops)
    # an id from before D386 is unchanged: relief 0 adds nothing to the hash
    assert shell.build_id(old, ops) == shell.build_id(
        dataclasses.replace(new, relief=0, bounds=old.bounds), ops
    )
