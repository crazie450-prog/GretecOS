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


def fragments() -> str:
    """The prompt's helper fragments with their placeholders filled in (a whole building is no
    longer shown, D387)."""
    text = shell_brief.system_prompt()
    start = text.index("\n", text.index("Fragments that show the helpers")) + 1
    body = text[start : text.index("\n\nThe code refuses")]
    head = (
        "lo, hi = WALLS['lo'], WALLS['hi']\n"
        "x, z = lo[0] + 2, lo[2] + 2\n"
        "x0, z0, x1, z1 = lo[0], lo[2], hi[0], hi[2]\n"
    )
    return head + body


def test_the_prompts_fragments_run_and_pass_the_gate(
    conn: sqlite3.Connection, spec: shell.Spec
) -> None:
    out = shell.design(conn, spec, [script_op(fragments())], "architect")
    assert out.ok, (out.reasons, out.repairs)
    assert len(out.cells) > 100


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
        "cylinder",
        "sphere",
        "pyramid",
        "arch",
        "ring",
        "DOORWAYS",
        "KEEP_CLEAR",
        "PALETTE",
        "MAX_BLOCKS",
    ):
        assert name in text, name
    for principle in (
        "never a box",
        "Massing",
        "Roofline",
        "Facades with depth",
        "Rhythm",
        "entrance",
        "suggestions",
        "silhouette",
    ):
        assert principle in text, principle
    assert (
        len(text) < 8_000
    )  # the per-call estimate in the runbook assumes about this size
    assert shell_brief.SCHEMA["required"] == ["script", "ops"]
    # layout mode keeps v5's text; site mode (D422) is v6, and a run records each it sent
    rec = shell_bakeoff.prompt_record()
    assert rec["version"] == shell_brief.PROMPT_VERSIONS["layout"] == 5
    assert len(rec["sha256"]) == 12 and rec["mode"] == "layout"
    assert shell_bakeoff.prompt_label({"prompt": rec}).startswith("v5 (sha ")
    assert shell_bakeoff.prompt_label({}).startswith("v1")
    site = shell_bakeoff.prompt_record("site")
    assert site["version"] == shell_brief.PROMPT_VERSIONS["site"] == 6
    assert site["sha256"] != rec["sha256"]
    both = {"prompt": site, "prompts": {"site": site, "layout": rec}}
    assert shell_bakeoff.prompt_label(both) == (
        f"v6 site (sha {site['sha256']}); v5 layout (sha {rec['sha256']})"
    )


def test_the_site_prompt_states_the_site_rules_and_its_hall_fragment_fits(
    conn: sqlite3.Connection, home: Path
) -> None:
    """v6 (D422): the same ambition and API, the site's rules in place of the layout's, the
    answer's entrance and zones; its growing-hall fragment runs, sizes itself with covered()
    and the machines fit into it."""
    from gregtec.build import shell_store
    from gregtec.vault.io import VaultIO

    text = shell_brief.system_prompt("site")
    layout_text = shell_brief.system_prompt()
    assert text == shell_brief.site_prompt() and text != layout_text
    assert text.startswith(layout_text[: layout_text.index("The space")])
    assert shell_brief.SCRIPT_API in text and shell_brief.SITE_API in text
    for name in (
        "SITE",
        "NEED",
        "PROGRAM",
        "FLOOR_Y",
        "covered()",
        "entrance",
        "zones",
    ):
        assert name in text, name
    for rule in (
        "y 0 is the machine floor",
        "door_height",
        "aisle_height",
        "cap_height",
    ):
        assert rule in text, rule
    assert "you never place or avoid machines" in text
    assert "KEEP_CLEAR lists" not in text and "for d in DOORWAYS" not in text
    assert f"{shell_brief.ROOM} times NEED.required" in text
    assert len(text) < 10_000
    with pytest.raises(ValueError):
        shell_brief.system_prompt("rows")
    start = text.index('top, x1, z1 = NEED["cap_height"]')
    script = text[start : text.index("\n\nThe code refuses", start)]
    made = shell_store.make(
        conn,
        home,
        VaultIO(home / "vault"),
        ["pl-steel"],
        theme_id="th-foundry",
        ops=[{"op": "script", "script": script}],
        mode="site",
        save_it=False,
    )
    assert made.outcome.ok, made.outcome.reasons
    st = made.fit["fit"]  # type: ignore[index]
    assert st["placed"] == st["of"] > 0 and st["entrance_from"] == "auto"
    assert st["volume"] >= shell_brief.ROOM * st["need"]["required"]


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


# --- the answer is a script first; varied cases (WO-10.14, D387) ---------------------------------


def test_the_answer_is_a_script_first_and_the_old_shape_still_reads() -> None:
    ops = shell_brief.parse(
        json.dumps(
            {
                "script": "set_block(0, 9, 0, 'wall')",
                "ops": [
                    {
                        "op": "clear",
                        "lo": [0, 0, 0],
                        "hi": [0, 0, 0],
                        "role": "",
                        "n": None,
                    }
                ],
            }
        )
    )
    assert ops[0] == {"op": "script", "script": "set_block(0, 9, 0, 'wall')"}
    assert ops[1] == {"op": "clear", "lo": [0, 0, 0], "hi": [0, 0, 0]}
    assert shell_brief.parse(json.dumps({"script": "  ", "ops": []})) == []
    assert shell_brief.parse(
        json.dumps({"ops": [{"op": "walls", "role": "wall"}]})
    ) == [{"op": "walls", "role": "wall"}]
    for bad in (
        {"script": 3, "ops": []},
        {"script": "", "ops": [], "x": 1},
        {"script": ""},
    ):
        with pytest.raises(shell_brief.BriefError):
            shell_brief.parse(json.dumps(bad))
    # a saved design round-trips through the answer a fake gives
    saved = [
        {"op": "walls", "role": "wall"},
        script_op("x = 1"),
        {"op": "clear", "lo": [0, 0, 0], "hi": [0, 0, 0]},
    ]
    back = shell_brief.parse(shell_brief.answer(saved))
    assert back[0] == script_op("x = 1") and back[1:] == [saved[0], saved[2]]


def test_a_revision_shows_the_current_script_on_its_own() -> None:
    from gregtec.build import shell_eval

    old = {"ops": [script_op("x = 1"), {"op": "walls", "role": "wall"}], "headroom": 4}
    assert shell_eval.check(old, dict(old), shell_eval.CHANGED) == "nothing changed"
    new = {**old, "ops": [script_op("x = 2"), {"op": "walls", "role": "wall"}]}
    assert shell_eval.check(old, new, shell_eval.CHANGED) is None
    assert shell_eval.check(old, {**old, "headroom": 6}, shell_eval.CHANGED) is None
    script, rest = shell_brief.split(new["ops"])
    assert script == "x = 2" and rest == [{"op": "walls", "role": "wall"}]


def test_a_case_may_bring_its_own_sizes_and_palette(tmp_path: Path) -> None:
    head = "\t".join([*shell_bakeoff.COLUMNS, *shell_bakeoff.OPTIONAL])
    good = (
        "c1\tsmall\tnone\tgeneral\tdefault\ta tower\tmake it taller\tchanged\t"
        "script\t12\t5\twall minecraft:stonebrick@0, roof minecraft:nether_brick@0"
    )
    path = tmp_path / "cases.tsv"
    path.write_text(head + "\n" + good + "\n", encoding="utf-8")
    c = shell_bakeoff.load_cases(path)[0]
    assert (c.headroom, c.relief) == (12, 5)
    assert c.palette == (
        ("wall", "minecraft:stonebrick@0"),
        ("roof", "minecraft:nether_brick@0"),
    )
    for bad in (
        good.replace("\t12\t", "\t99\t"),
        good.replace("\t5\t", "\tdeep\t"),
        good.replace("wall minecraft:stonebrick@0", "wall"),
        good.replace("wall minecraft:stonebrick@0, roof minecraft:nether_brick@0", ""),
    ):
        path.write_text(head + "\n" + bad + "\n", encoding="utf-8")
        with pytest.raises(shell_bakeoff.BakeoffError):
            shell_bakeoff.load_cases(path)


# --- an open interior, machine arrangements, the runner's progress (WO-10.15, D389) ------------


def test_the_planner_spreads_machines_when_asked(conn: sqlite3.Connection) -> None:
    from gregtec.build import layout as layout_lib

    plans = [ts.lay_tests.production(), ts.lay_tests.power()]
    flush = layout.plan_layout(conn, plans)
    spread = layout.plan_layout(
        conn, plans, layout_lib.Options(max_run=16, spacing=2, band_gap=4)
    )
    assert "spacing" not in flush["options"]  # layouts from before D389 keep their ids
    assert spread["options"]["spacing"] == 2 and spread["id"] != flush["id"]
    keeps = [layout_lib.box_of(k) for k in spread["keepouts"]]
    assert not any(a.overlaps(b) for i, a in enumerate(keeps) for b in keeps[i + 1 :])
    assert all(p.get("reach", {}).get("ok", True) for p in spread["placements"])
    fx, fz = flush["bounds"]["size"][0], flush["bounds"]["size"][2]
    sx, sz = spread["bounds"]["size"][0], spread["bounds"]["size"][2]
    assert (sx, sz) != (fx, fz)


def test_a_case_picks_its_machine_arrangement() -> None:
    every = shell_bakeoff.load_cases()
    # D425's big cases have none: a site case's arrangement is reset anyway (D422), and a
    # plan-free one has no machines to arrange (D424)
    cases = [c for c in every if c.target is None]
    assert len(cases) == 7 and all(not c.layout for c in every if c.target is not None)
    assert len({c.layout for c in cases}) == len(
        cases
    )  # every case arranged its own way
    for c in every:
        c.layout_options()  # each is a valid set of the planner's options


def test_the_runner_says_it_is_still_waiting_and_what_it_spent() -> None:
    import threading

    said: list[str] = []
    beat = shell_bakeoff._Heartbeat(said.append, 0.02)
    threading.Event().wait(0.1)
    beat.stop()
    assert said and all("still waiting for the answer" in s for s in said)
    n = len(said)
    threading.Event().wait(0.06)
    assert len(said) == n  # stopped means stopped
    run = {
        "spent": {"usd": 1.234, "unpriced_tokens": 0, "calls": 3},
        "caps": {"usd": 20.0, "tokens": 1_000_000},
    }
    assert shell_bakeoff.spend_line(run) == (
        "The run has spent $1.23 of its $20.00 cap (3 calls)."
    )
    line = shell_bakeoff._line(
        "c1",
        "A",
        "design",
        {"ok": True, "first_try": True, "blocks": 9, "seconds": 125.4},
    )
    assert line == "c1 A design: 9 blocks, first try in 2m 05s"
    line = shell_bakeoff._line(
        "c1", "B", "design", {"ok": False, "seconds": 3, "refused": "no answer: credit"}
    )
    assert line == "c1 B design: no design in 3s (no answer: credit)"


# --- site mode (WO-10.16, D422): covered() ------------------------------------------------------


def _need(door: int = 3, cap: int = 6, required: int = 100) -> dict[str, int]:
    return {"door_height": door, "cap_height": cap, "required": required}


def test_covered_keeps_the_floor_rule() -> None:
    """The child can't import gregtec (``-S``): its copy of floor.measure must agree with it on
    any building, cells outside the site, solid floors and overhangs included."""
    import random

    from gregtec.build import floor, sandbox_child

    site = layout.Box((0, 0, 0), (11, 9, 11))
    rng = random.Random(422)
    for _ in range(200):
        cells = {
            (rng.randint(-2, 13), rng.randint(0, 11), rng.randint(-2, 13)): "wall"
            for _ in range(rng.randint(0, 300))
        }
        need = _need(rng.randint(2, 4), rng.randint(3, 9), rng.randint(0, 400))
        assert sandbox_child.measure(cells, site.to_json(), need) == floor.measure(
            cells, site, need
        )


ROOFED = """
fill((0, 5, 0), (7, 5, 7), 'roof')
ring((0, 0, 0), (7, 4, 7), 'wall')
set_block(3, 0, 3, 'wall')
fill((20, 9, 20), (21, 9, 21), 'roof')
a = covered()
fill((2, 3, 2), (2, 3, 2), 'roof')
b = covered()
print(a['volume'], a['floor'], a['need_volume'], a['short_by'])
print(b['volume'], b['floor'])
"""


def test_covered_measures_the_blocks_placed_so_far() -> None:
    from gregtec.build import floor

    site = layout.Box((0, 0, 0), (15, 9, 15))
    facts = {**FACTS, "site": site.to_json(), "need": _need(3, 4, 200)}
    ran = sandbox.run(ROOFED, facts, 5000)
    # 6x6 roofed floor 5 high, less the solid column at [3,3]: 35 columns, capped at 4
    # (the slab far outside the site is not the building's); then one column 3 high
    assert ran.log == ("140 35 200 60", "139 35")
    got = floor.measure(ran.cells, site, facts["need"])
    assert got == {
        "volume": 139,
        "floor": 35,
        "need_volume": 200,
        "short_by": 61,
        "need_floor": 0,
    }


def test_covered_is_a_site_builds_only() -> None:
    with pytest.raises(sandbox.ScriptError, match="NameError: name 'covered'"):
        sandbox.run("covered()", FACTS, 100)
    # it is geometry only: no argument, and the cell cap still holds
    facts = {**FACTS, "site": {"lo": [0, 0, 0], "hi": [9, 9, 9]}, "need": _need()}
    with pytest.raises(sandbox.ScriptError, match="TypeError"):
        sandbox.run("covered(1)", facts, 100)
    with pytest.raises(sandbox.ScriptError, match="more than 100"):
        sandbox.run(
            "while covered()['short_by'] > 0:\n    fill((0, 0, 0), (9, 9, 9), 'wall')",
            facts,
            100,
        )


def test_a_site_spec_gives_the_script_its_site_facts(conn: sqlite3.Connection) -> None:
    prog = layout.program(conn, [ts.lay_tests.production()])
    sw = shell.swatches_of(ts.foundry())
    spec = shell.spec_for_site(prog, shell.site_of(prog), sw)
    script = (
        "print(KEEP_CLEAR, DOORWAYS, FLOOR_Y, SITE['lo'], NEED['door_height'])\n"
        "print(sum(r['count'] for r in PROGRAM), covered()['volume'])\n"
        "fill((0, 0, 0), (0, 0, 0), 'wall')\n"
    )
    ran = sandbox.run(script, shell.script_facts(spec), 100)
    need = dict(spec.need or {})
    assert ran.log == (
        f"() () 0 (0, 0, 0) {need['door_height']}",
        f"{len(prog.instances)} 0",
    )


# --- shape helpers capped by the cells they place (WO-10.17, D425) ---------------------------


def test_hollow_helpers_are_capped_by_what_they_place() -> None:
    """A big building's ring, hollow box, pyramid and clear are held to the cells they place,
    not their box's volume: a 100 x 28 x 100 ring at max_blocks 20,000 (cap 80,000) passes;
    fill and a solid box still check their volume."""
    ring = sandbox.run("ring((0, 0, 0), (99, 27, 99), 'wall')", FACTS, 80_000)
    assert len(ring.cells) == 396 * 28
    hollow = sandbox.run(
        "box((0, 0, 0), (77, 27, 77), 'wall', hollow=True)", FACTS, 100_000
    )
    assert len(hollow.cells) == 78 * 28 * 78 - 76 * 26 * 76
    assert (1, 1, 1) not in hollow.cells and (0, 5, 5) in hollow.cells
    spire = sandbox.run("pyramid((0, 0, 0), (99, 40, 99), 'roof')", FACTS, 80_000)
    assert len(spire.cells) == sum(4 * (99 - 2 * i) for i in range(41))
    for script in (
        "fill((0, 0, 0), (99, 27, 99), 'wall')",
        "box((0, 0, 0), (99, 27, 99), 'wall')",
    ):
        with pytest.raises(sandbox.ScriptError, match="the box holds more than 80000"):
            sandbox.run(script, FACTS, 80_000)
    for script, name in (
        ("ring((0, 0, 0), (99, 27, 99), 'wall')", "ring"),
        ("box((0, 0, 0), (99, 27, 99), 'wall', hollow=True)", "box"),
        ("pyramid((0, 0, 0), (99, 40, 99), 'roof')", "pyramid"),
    ):
        with pytest.raises(sandbox.ScriptError, match=f"{name}: the shape holds more"):
            sandbox.run(script, FACTS, 5_000)
    # _put still stops a script that places more than the cap, helper by helper
    with pytest.raises(sandbox.ScriptError, match="placed more than 20000 blocks"):
        sandbox.run(
            "for y in range(0, 80, 4):\n    ring((0, y, 0), (99, y + 3, 99), 'wall')",
            FACTS,
            20_000,
        )


@pytest.mark.serial
def test_clearing_a_huge_box_walks_only_what_was_placed() -> None:
    import time

    script = (
        "ring((0, 0, 0), (77, 27, 77), 'wall')\n"
        "fill((0, 28, 0), (77, 28, 77), 'roof')\n"
        "clear((0, 0, 0), (77, 28, 77))\n"  # 176,904 cells: far over the cap, places none
        "ring((0, 0, 0), (9, 3, 9), 'wall')\n"
        "clear((0, 0, 0), (0, 2, 9))\n"  # a small box: walked
    )
    start = time.monotonic()
    ran = sandbox.run(script, FACTS, 100_000)
    assert time.monotonic() - start < 5
    assert len(ran.cells) == 36 * 4 - 3 * 10  # the west wall's doorway rows gone
    assert (0, 3, 5) in ran.cells and (0, 2, 5) not in ran.cells


def _reference(
    name: str, a: tuple[int, ...], b: tuple[int, ...]
) -> set[tuple[int, ...]]:
    """The helpers' cells by brute force over the whole box (the rule before D425)."""
    out = set()
    for x in range(a[0], b[0] + 1):
        for y in range(a[1], b[1] + 1):
            for z in range(a[2], b[2] + 1):
                side = x in (a[0], b[0]) or z in (a[2], b[2])
                keep = {
                    "ring": side,
                    "hollow": side or y in (a[1], b[1]),
                    "solid": True,
                }[name]
                if keep:
                    out.add((x, y, z))
    return out


def test_the_helpers_build_the_same_cells_as_before() -> None:
    import random

    rng = random.Random(425)
    for _ in range(12):
        a = tuple(rng.randint(-3, 3) for _ in range(3))
        b = tuple(a[i] + rng.randint(0, 6) for i in range(3))
        script = (
            f"ring({a}, {b}, 'wall')\n"
            f"box({tuple(v + 10 for v in a)}, {tuple(v + 10 for v in b)}, 'roof', "
            "hollow=True)\n"
            f"box({tuple(v + 20 for v in a)}, {tuple(v + 20 for v in b)}, 'trim')\n"
            f"clear({tuple(v + 20 for v in a)}, {tuple(v + 21 for v in a)})\n"
        )
        ran = sandbox.run(script, FACTS, 5_000)
        want = {c: "wall" for c in _reference("ring", a, b)}
        shift = [tuple(v + 10 for v in p) for p in (a, b)]
        want |= {c: "roof" for c in _reference("hollow", shift[0], shift[1])}
        lo, hi = [tuple(v + 20 for v in p) for p in (a, b)]
        gone = _reference("solid", lo, tuple(v + 21 for v in a))
        want |= {c: "trim" for c in _reference("solid", lo, hi) - gone}
        assert ran.cells == want
