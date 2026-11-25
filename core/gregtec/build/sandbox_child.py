"""The child side of the build-script sandbox (D385; see gregtec.build.sandbox).

Run only by :func:`gregtec.build.sandbox.run`, as ``python -S -s -B -P <this file>`` with an
empty environment, in an empty folder, under OS limits (a Windows Job Object; rlimits on POSIX).
It imports the standard library only. It reads one JSON request from stdin (the script, the
build's facts, the cell cap), runs the script with the building API below and a short list of
builtins, and writes one JSON answer to stdout: the cells, or why it stopped. In site mode
(D422) the script also has ``covered()``: the sheltered volume of its blocks so far against
NEED (:func:`measure`, the rule of gregtec.build.floor, which the child can't import).

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
            raise BuildError(f"{what} is too far from the site")
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

    def _corners(
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
        return lo, hi

    def _volume(lo: tuple[int, ...], hi: tuple[int, ...]) -> int:
        return (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)

    def _outline(dx: int, dz: int) -> int:
        """The distinct cells of one layer of a box's four walls, dx by dz apart."""
        return (dx + 1) * (dz + 1) - max(dx - 1, 0) * max(dz - 1, 0)

    def _cap(name: str, places: int) -> None:
        """A helper is refused up front when it would place more than the cap (D425: by the
        cells it places, not its box's volume)."""
        if places > cap:
            raise BuildError(f"{name}: the shape holds more than {cap} cells")

    def _box(
        args: tuple[object, ...], name: str
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """A box whose every cell is placed (fill, a solid box): its volume is capped."""
        lo, hi = _corners(args, name)
        if _volume(lo, hi) > cap:
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
        # it places nothing, so it has no cap (D425): the box's cells or the placed ones,
        # whichever is fewer
        lo, hi = _corners(args, "clear")
        if _volume(lo, hi) > len(cells):
            gone = [c for c in cells if all(lo[i] <= c[i] <= hi[i] for i in range(3))]
            for c in gone:
                del cells[c]
            return
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    cells.pop((x, y, z), None)

    def get_block(*args: object) -> str | None:
        """The block placed at a cell so far, or None."""
        return cells.get(_xyz(args, 3, "get_block"))

    # --- shape helpers: each places blocks with _put, so the same caps hold -------------

    def _pt(v: object, n: int, name: str) -> tuple[int, ...]:
        if not isinstance(v, (tuple, list)):
            raise BuildError(f"{name} takes points as (x, y, z) or (x, z)")
        return _xyz((v,), n, name)

    def _r(v: object, name: str) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 64:
            raise BuildError(f"{name}: a radius is a number above 0, at most 64")
        return float(v)

    def line(a: object, b: object, what: object) -> None:
        """Every block on the straight line from a to b (both (x, y, z))."""
        w = _what(what)
        p, q = _pt(a, 3, "line"), _pt(b, 3, "line")
        steps = max(abs(q[i] - p[i]) for i in range(3))
        for k in range(steps + 1):
            t = k / steps if steps else 0.0
            _put(tuple(round(p[i] + (q[i] - p[i]) * t) for i in range(3)), w)

    def ring(lo: object, hi: object, what: object) -> None:
        """The four walls of the box lo..hi (its outline at every height), no top or floor."""
        w = _what(what)
        a, b = _corners((lo, hi), "ring")
        _cap("ring", _outline(b[0] - a[0], b[2] - a[2]) * (b[1] - a[1] + 1))
        for y in range(a[1], b[1] + 1):
            for x in range(a[0], b[0] + 1):
                _put((x, y, a[2]), w)
                _put((x, y, b[2]), w)
            for z in range(a[2], b[2] + 1):
                _put((a[0], y, z), w)
                _put((b[0], y, z), w)

    def box(lo: object, hi: object, what: object, hollow: bool = False) -> None:
        """A solid box, or with hollow=True only its six faces."""
        w = _what(what)
        if not hollow:
            a, b = _box((lo, hi), "box")
        else:  # only its faces are placed, and walked
            a, b = _corners((lo, hi), "box")
            inner = 1
            for i in range(3):
                inner *= max(b[i] - a[i] - 1, 0)
            _cap("box", _volume(a, b) - inner)
        for x in range(a[0], b[0] + 1):
            for y in range(a[1], b[1] + 1):
                if hollow and x not in (a[0], b[0]) and y not in (a[1], b[1]):
                    zs: range | tuple[int, ...] = tuple(sorted({a[2], b[2]}))
                else:
                    zs = range(a[2], b[2] + 1)
                for z in zs:
                    _put((x, y, z), w)

    def cylinder(
        center: object,
        radius: object,
        y0: object,
        y1: object,
        what: object,
        hollow: bool = True,
    ) -> None:
        """A round tower or stack: center (x, z), from y0 to y1; hollow=False fills it."""
        w = _what(what)
        cx, cz = _pt(center, 2, "cylinder")
        r = _r(radius, "cylinder")
        lo_y, hi_y = sorted((_num(y0, "cylinder: y0"), _num(y1, "cylinder: y1")))
        n = int(r) + 1
        for y in range(lo_y, hi_y + 1):
            for dx in range(-n, n + 1):
                for dz in range(-n, n + 1):
                    d = math.hypot(dx, dz)
                    if d <= r + 0.5 and (not hollow or d > r - 0.5):
                        _put((cx + dx, y, cz + dz), w)

    def sphere(
        center: object,
        radius: object,
        what: object,
        hollow: bool = True,
        dome: bool = False,
    ) -> None:
        """A ball around center (x, y, z); dome=True keeps the top half (from center's y up)."""
        w = _what(what)
        cx, cy, cz = _pt(center, 3, "sphere")
        r = _r(radius, "sphere")
        n = int(r) + 1
        for dx in range(-n, n + 1):
            for dy in range(0 if dome else -n, n + 1):
                for dz in range(-n, n + 1):
                    d = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if d <= r + 0.5 and (not hollow or d > r - 0.5):
                        _put((cx + dx, cy + dy, cz + dz), w)

    def pyramid(lo: object, hi: object, what: object, rise: object = 1) -> None:
        """A stepped hip roof or spire: from the footprint lo..hi (x and z; y from lo), each
        course one block in on every side, `rise` blocks high, up to hi's y."""
        w = _what(what)
        a, b = _corners((lo, hi), "pyramid")
        step = _num(rise, "pyramid: rise")
        if not 1 <= step <= 8:
            raise BuildError("pyramid: rise is 1 to 8")
        i, y, places = 0, a[1], 0  # its courses' cells, counted first
        while a[0] + i <= b[0] - i and a[2] + i <= b[2] - i and y <= b[1]:
            rows = min(step, b[1] - y + 1)
            places += rows * _outline(b[0] - a[0] - 2 * i, b[2] - a[2] - 2 * i)
            y += rows
            i += 1
        _cap("pyramid", places)
        i, y = 0, a[1]
        while a[0] + i <= b[0] - i and a[2] + i <= b[2] - i and y <= b[1]:
            for _ in range(step):
                if y > b[1]:
                    break
                for x in range(a[0] + i, b[0] - i + 1):
                    _put((x, y, a[2] + i), w)
                    _put((x, y, b[2] - i), w)
                for z in range(a[2] + i, b[2] - i + 1):
                    _put((a[0] + i, y, z), w)
                    _put((b[0] - i, y, z), w)
                y += 1
            i += 1

    def arch(a: object, b: object, rise: object, what: object) -> None:
        """A half-round (or flattened) arch from foot a to foot b (both (x, y, z), same y),
        rising `rise` blocks at the middle."""
        w = _what(what)
        p, q = _pt(a, 3, "arch"), _pt(b, 3, "arch")
        h = float(_num(rise, "arch: rise"))
        span = max(abs(q[0] - p[0]), abs(q[2] - p[2]), 1)
        for k in range(4 * span + 1):
            t = k / (4 * span)
            lift = h * math.sin(math.pi * t)
            _put(
                (
                    round(p[0] + (q[0] - p[0]) * t),
                    round(p[1] + lift),
                    round(p[2] + (q[2] - p[2]) * t),
                ),
                w,
            )

    def covered() -> dict[str, int]:
        """Site mode (D422): the sheltered volume of the blocks placed so far against NEED,
        {"volume", "floor", "need_volume", "short_by", "need_floor"}; the building is big
        enough for the fitter to try once short_by <= 0 and floor >= need_floor."""
        return measure(cells, facts["site"], facts["need"])

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
        "line": line,
        "ring": ring,
        "box": box,
        "cylinder": cylinder,
        "sphere": sphere,
        "pyramid": pyramid,
        "arch": arch,
        **({"covered": covered} if "site" in facts and "need" in facts else {}),
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


def measure(
    cells: dict[tuple[int, ...], str], site: dict, need: dict
) -> dict[str, int]:
    """gregtec.build.floor.measure, again here: the child imports the standard library only
    (``-S``), so the rule is kept in step by a test that both give the same numbers. Every
    site column whose y 0 is air under a block above, with at least door_height of air,
    counts its air up to cap_height."""
    lo, hi = site["lo"], site["hi"]
    low: dict[tuple[int, int], int] = {}  # each column's lowest block in the site
    for c in cells:
        x, y, z = c
        if lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1] and lo[2] <= z <= hi[2]:
            k = (x, z)
            if k not in low or y < low[k]:
                low[k] = y
    ch, cap = int(need["door_height"]), int(need["cap_height"])
    volume = floor = 0
    for y in low.values():
        h = y - lo[1]
        if h >= ch:  # y 0 is air (h > 0) and a roof is above
            volume += min(h, cap)
            floor += 1
    want = int(need["required"])
    return {
        "volume": volume,
        "floor": floor,
        "need_volume": want,
        "short_by": want - volume,
        "need_floor": int(need.get("floor_required", 0)),
    }


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
