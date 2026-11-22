"""The child side of the build-script sandbox (D385; see gregtec.build.sandbox).

Run only by :func:`gregtec.build.sandbox.run`, as ``python -S -s -B -P <this file>`` with an
empty environment, in an empty folder, under OS limits (a Windows Job Object; rlimits on POSIX).
It imports the standard library only. It reads one JSON request from stdin (the script, the
build's facts, the cell cap), runs the script with the building API below and a short list of
builtins, and writes one JSON answer to stdout: the cells, or why it stopped.

Before the script runs, an audit hook (PEP 578) is installed that refuses every audited event
but the one ``exec`` of the script's own code: no file, socket, process, import, ctypes, frame
or code-object event is allowed from then on. The hook can't be removed from Python. The parent
checks the script's syntax tree first (:func:`gregtec.build.sandbox.check`) and the parent's
limits stop a script that loops or eats memory. What comes back is data the parent checks
again, and every cell then goes through the shell gate.
"""

from __future__ import annotations

import functools
import itertools
import json
import math
import random
import sys

MODULES = {
    "math": math,
    "random": random,
    "itertools": itertools,
    "functools": functools,
}
MAX_LOG = 50  # print() lines kept
MAX_LINE = 200
MAX_WHAT = 100


class BuildError(Exception):
    """A misuse of the building API (said to the model as is)."""


def _main() -> None:
    req = json.loads(sys.stdin.read())
    script, facts, cap = req["script"], req["facts"], int(req["max_cells"])
    cells = {}
    log = []

    def _num(v: object, what: str) -> int:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise BuildError(f"{what} must be a number, not {type(v).__name__}")
        if isinstance(v, float):
            if not math.isfinite(v):
                raise BuildError(f"{what} must be finite")
            v = round(v)
        if abs(v) > 100_000:
            raise BuildError(f"{what} is too far from the layout")
        return int(v)

    def _xyz(args: tuple[object, ...], n: int, name: str) -> tuple[int, ...]:
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            args = tuple(args[0])
        if len(args) != n:
            raise BuildError(f"{name} takes x, y, z (or one (x, y, z))")
        return tuple(_num(a, f"{name}: a coordinate") for a in args)

    def _what(w: object) -> str | None:
        if w is None:
            return None
        if not isinstance(w, str) or not w or len(w) > MAX_WHAT:
            raise BuildError(
                "a block is a palette role ('wall', 'roof:1') or 'registry@meta'"
            )
        return w

    def _put(c: tuple[int, ...], w: str | None) -> None:
        if w is None:
            cells.pop(c, None)
            return
        cells[c] = w
        if len(cells) > cap:
            raise BuildError(f"the script placed more than {cap} blocks")

    def set_block(*args: object) -> None:
        """set_block(x, y, z, what) or set_block((x, y, z), what)."""
        if not args:
            raise BuildError("set_block needs a position and a block")
        *pos, w = args
        _put(_xyz(pos, 3, "set_block"), _what(w))

    def _box(
        args: tuple[object, ...], name: str
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        if len(args) == 2:
            a, b = _xyz((args[0],), 3, name), _xyz((args[1],), 3, name)
        elif len(args) == 6:
            a, b = _xyz(args[:3], 3, name), _xyz(args[3:], 3, name)
        else:
            raise BuildError(f"{name} takes (x0, y0, z0), (x1, y1, z1) or six numbers")
        lo = tuple(min(a[i], b[i]) for i in range(3))
        hi = tuple(max(a[i], b[i]) for i in range(3))
        if (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1) > cap:
            raise BuildError(f"{name}: the box holds more than {cap} cells")
        return lo, hi

    def fill(*args: object) -> None:
        """fill(lo, hi, what): every cell of the box, both corners included."""
        if len(args) not in (3, 7):
            raise BuildError("fill takes two corners and a block")
        *corners, w = args
        w = _what(w)
        lo, hi = _box(corners, "fill")
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    _put((x, y, z), w)

    def clear(*args: object) -> None:
        """clear(lo, hi): air in every cell of the box (also clear(x, y, z) for one)."""
        if len(args) in (1, 3):
            _put(_xyz(args, 3, "clear"), None)
            return
        lo, hi = _box(args, "clear")
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    cells.pop((x, y, z), None)

    def get_block(*args: object) -> str | None:
        """The block placed at a cell so far, or None."""
        return cells.get(_xyz(args, 3, "get_block"))

    def _print(*args: object, sep: object = " ", end: object = "") -> None:
        if len(log) < MAX_LOG:
            log.append(str(sep).join(str(a) for a in args)[:MAX_LINE])

    def _import(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: object = (),
        level: int = 0,
    ) -> object:
        if level or name not in MODULES:
            raise ImportError(f"only {', '.join(sorted(MODULES))} may be imported")
        return MODULES[name]

    safe = {
        name: getattr(__builtins__, name)
        if not isinstance(__builtins__, dict)
        else __builtins__[name]
        for name in (
            "abs",
            "all",
            "any",
            "bool",
            "dict",
            "divmod",
            "enumerate",
            "filter",
            "float",
            "frozenset",
            "int",
            "isinstance",
            "len",
            "list",
            "map",
            "max",
            "min",
            "pow",
            "range",
            "reversed",
            "round",
            "set",
            "slice",
            "sorted",
            "str",
            "sum",
            "tuple",
            "zip",
            "ArithmeticError",
            "Exception",
            "IndexError",
            "KeyError",
            "ValueError",
            "ZeroDivisionError",
            "True",
            "False",
            "None",
        )
    }
    safe["print"] = _print
    safe["__import__"] = _import
    env = {
        "__builtins__": safe,
        "set_block": set_block,
        "block": set_block,
        "fill": fill,
        "clear": clear,
        "get_block": get_block,
        **{k.upper(): _freeze(v) for k, v in facts.items()},
        **MODULES,
    }
    random.seed(0)  # the same script gives the same building
    code = compile(script, "<build script>", "exec")
    sys.setrecursionlimit(200)
    armed = {"exec_left": 1}

    def hook(event: str, args: tuple[object, ...]) -> None:
        if event == "exec" and armed["exec_left"] and args and args[0] is code:
            armed["exec_left"] = 0
            return
        if event in ("sys.excepthook",):
            return
        raise RuntimeError(f"not allowed in a build script: {event}")

    _disarm_os()
    sys.addaudithook(hook)
    try:
        exec(code, env)  # noqa: S102 - the sandboxed script (see the module's docstring)
    except BuildError as exc:
        _answer({"error": str(exc), "log": log})
        return
    except RecursionError:
        _answer({"error": "the script recursed too deep", "log": log})
        return
    except Exception as exc:  # noqa: BLE001 - the script's own bug: said to the model
        # the line of the script's top-level statement that failed (a frame's code is not
        # readable under the audit hook, so the second entry of the traceback is used)
        tb = exc.__traceback__
        tb = tb.tb_next if tb is not None else None
        line = f" (line {tb.tb_lineno})" if tb is not None else ""
        _answer({"error": f"{type(exc).__name__}: {exc}{line}"[:500], "log": log})
        return
    _answer({"cells": [[c[0], c[1], c[2], w] for c, w in cells.items()], "log": log})


# The modules that reach the operating system. Some of their functions raise no audit event
# (pidfd_send_signal, mkfifo, mknod, setsid, nice, os.write, ...), so the audit hook can't stop
# them: every function in them is removed before the script runs. The child needs none of them
# afterwards (its answer goes through sys.stdout, which is C code with its own file handle), and
# it imports nothing more.
OS_MODULES = (
    "os",
    "posix",
    "nt",
    "_signal",
    "signal",
    "_posixsubprocess",
    "resource",
    "_winapi",
    "msvcrt",
    "fcntl",
    "termios",
    "select",
    "_socket",
    "socket",
    "mmap",
    "_ctypes",
    "_thread",
    # the import machinery: it can make a fresh copy of a built-in module (posix, with all its
    # functions); every module a script may import is already loaded (MODULES)
    "_imp",
    "_frozen_importlib",
    "_frozen_importlib_external",
    "importlib",
    "zipimport",
)


def _disarm_os() -> None:
    for name in OS_MODULES:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        for attr in list(vars(mod)):
            if attr.startswith("__"):
                continue
            if callable(getattr(mod, attr, None)):
                try:
                    delattr(mod, attr)
                except (AttributeError, TypeError):
                    pass


def _freeze(v: object) -> object:
    if isinstance(v, list):
        return tuple(_freeze(x) for x in v)
    if isinstance(v, dict):
        return {k: _freeze(x) for k, x in v.items()}
    return v


def _answer(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    _main()
