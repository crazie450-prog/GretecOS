"""The staged MILP (WO-3.4, plan §7.1 steps 3, 4 and 6, [E§3.4], D11, D14, D61, D192-D195).

The solver works on a :class:`Network`, a plain list of recipe nodes (:class:`Proc`) with what
each consumes and gives per craft and how many machine-seconds a craft takes. :mod:`.plan`
builds one from WO-3.3's graph and WO-3.2's machine sizing; the tests also build them from
gtnh-flow charts (PlanNH's test resources, D192). Nothing here knows what an item is worth: the
objectives are structural only (**no per-item cost values**, §7.1 step 4).

**Variables** (scipy.optimize.milp on HiGHS with SciPy's defaults: its MIP search is serial,
and SciPy exposes no thread option to set): ``x_r >= 0`` crafts/s per recipe;
``imp_k``, ``exp_k >= 0`` per item port that may cross the boundary; a gate binary ``g_k`` per
connected item whose crossing isn't free; a choice binary ``z_r`` per recipe of a primary
choice (§7.1 step 3: exactly one recipe per primary item, the others idle).

**Rows:** conservation per item, ``sum_r (out_rk - in_rk) x_r + imp_k - exp_k = 0`` (the target's
export is its rate), each divided by ``c_k = max(1, the item's largest amount per craft)``, the
row scaling PlanNH uses, so ``imp_k`` and ``exp_k`` are solved in row units. EU is an item like
any other: a machine recipe consumes ``EU/t x ticks`` per craft from the ``eu`` port, which
nothing in a production line makes, so it is imported (WO-4's generators will supply it).
An item is **free** to import when nothing in the network makes it or the graph names it a
boundary (raw, stock, line outputs, request, locked, depth) and free to export when nothing in
the network uses it or the graph names it a boundary; any other crossing is a **gate**,
``imp_k + exp_k <= M g_k``. M is per row in row units: 1e4, then 1e5 if HiGHS finds the stage
infeasible (1e7 caused spurious infeasibility, [E§3.3]). Each recipe's column is scaled by its
largest port too, and the target rate is normalised to 1 while solving, so small rates stay
clear of HiGHS's tolerances. Those tolerances let a "closed" gate leak about ``M x 1e-6``, so
a gate whose item crosses at all counts as open (:func:`_open_gates`), and stage 3 runs with
stage 2's gates fixed.

**Stages** (lexicographic; each optimum is kept as a row of the next stage): (1) minimise
gates; (2) minimise the gated quantity, with a ``1e-6`` tiebreak on the raw inputs (free
imports, EU left out); (3) minimise total machines ``sum_r x_r t_r``; (4) the every-machine-runs
replay: every recipe of the route gets a floor of ``1e-3`` x the smallest running rate of
stage 3 (at least :data:`FLOOR_MIN` row units) and stages 1-3 run again (a gate shortcut that leaves machines idle is refused,
[E§3.3]). Integerising (step 6) is ``ceil`` and utilisation in :mod:`.sizing`, or, on request,
:func:`exact_counts`: an integer machine count per recipe, minimising their sum under the
same optima.

**Routes** (:func:`routes`): the stages run with the choice binaries free and propose the
primary choices of a route; it is solved on its own (:func:`solve_route`, on the recipes it
reaches from the target, :func:`active`, replay included) and keeps only the choices it
depends on (:func:`effective`: a choice for an input of a target recipe it didn't pick is
dropped); a no-good cut on those (at least one of them differs) is added and the MILP runs
again. A route whose recipes equal an earlier one's is dropped. Of ``n`` + :data:`EXPLORE` routes the best
``n`` by their exact objectives are kept, in that order; it is not a ranking (planner/rank.py's,
WO-3.5).
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

EU_KEY = "eu"
BIG_M = (1e4, 1e5)
FLOOR = 1e-3  # x the smallest running rate (stage 4)
FLOOR_MIN = 1e-6  # a floor in row units (target 1/s) that HiGHS doesn't round to 0
TIEBREAK = 1e-6  # raw-input weight in stage 2
REL_TOL = 1e-7  # an optimum kept as a row may be this much worse (continuous stages)
ABS_TOL = 1e-9
RUNNING = 1e-7  # crafts/s at a target rate of 1/s: above this a recipe runs (below: solver noise)
TIME_LIMIT_S = 60.0
EXPLORE = (
    2  # routes solved beyond those asked for, the best kept (the proposals are rough)
)
MAX_TRIES = 3  # choice proposals per route solved, before giving up on more
# No HiGHS presolve (D245): SciPy 1.17-1.18's crashes the process (SIGSEGV) on some of the power
# designer's MILPs (LV networks on the harness DB), and 1.16's finds no solution where there is
# one; the optimum is the same without it (mip_rel_gap 0).
_OPTIONS = {"disp": False, "presolve": False, "mip_rel_gap": 0.0}


class SolveError(RuntimeError):
    """No route can be solved; the message says why."""


@dataclass(frozen=True)
class Proc:
    """One recipe node: per-craft consumption and expected output, machine-seconds a craft."""

    id: str
    inputs: tuple[tuple[str, float], ...]  # consumed per craft (catalysts left out)
    outputs: tuple[tuple[str, float], ...]  # expected per craft (amount x chance)
    seconds: float  # machine-seconds per craft: 1 / crafts per second of one machine
    makes: tuple[str, ...] = ()  # items it was chosen to make; () = all its outputs

    def made(self) -> tuple[str, ...]:
        return self.makes or tuple(dict.fromkeys(k for k, _ in self.outputs))


@dataclass(frozen=True)
class Network:
    procs: tuple[Proc, ...]
    targets: tuple[tuple[str, float], ...] = ()  # item, export per second
    fixed: tuple[
        tuple[str, float], ...
    ] = ()  # proc, machine count (a gtnh-flow `number`)
    free_in: frozenset[str] = frozenset()  # boundary items, imported without a gate
    free_out: frozenset[str] = frozenset()  # boundary items, exported without a gate
    choices: tuple[tuple[str, tuple[str, ...]], ...] = ()  # primary item -> its recipes
    pinned: tuple[tuple[str, str], ...] = ()  # item -> recipe, fixed choices (D61)
    prune: bool = (
        True  # a route runs only what it reaches from the targets (False: all)
    )
    banned: frozenset[str] = (
        frozenset()
    )  # items never imported (a power line's own EU, D233)

    def proc(self, pid: str) -> Proc:
        return next(p for p in self.procs if p.id == pid)


@dataclass(frozen=True)
class Flow:
    """One solved route, in real units (per second)."""

    choice: tuple[tuple[str, str], ...]  # primary item -> its recipe
    crafts: tuple[
        tuple[str, float], ...
    ]  # recipe -> crafts/s (every recipe of the route)
    imports: tuple[tuple[str, float], ...]  # item -> per second, above zero
    exports: tuple[tuple[str, float], ...]  # item -> per second (targets included)
    gates: tuple[str, ...]  # items crossing through a gate
    all_run: bool  # every recipe of the route runs
    floor: float | None  # the stage-4 floor, crafts/s, when the replay ran
    # gates, gated quantity (row units x the target rate), machines
    objectives: tuple[int, float, float]
    big_m: float
    warnings: tuple[str, ...] = ()

    def rate(self, pid: str) -> float:
        return dict(self.crafts).get(pid, 0.0)


# --- the model ---------------------------------------------------------------------------------


@dataclass
class _Model:
    """The variables and rows of one network, at one big-M."""

    net: Network
    procs: list[Proc]
    big_m: float
    scale: float
    floors: Mapping[str, float]
    cuts: Sequence[Mapping[str, str]]
    n: int = 0
    lb: list[float] = field(default_factory=list)
    ub: list[float] = field(default_factory=list)
    integral: list[int] = field(default_factory=list)
    rows: list[tuple[dict[int, float], float, float]] = field(default_factory=list)
    x: dict[str, int] = field(default_factory=dict)
    imp: dict[str, int] = field(default_factory=dict)
    exp: dict[str, int] = field(default_factory=dict)
    gate: dict[str, int] = field(default_factory=dict)
    z: dict[str, int] = field(default_factory=dict)
    c: dict[str, float] = field(default_factory=dict)  # item -> its row scale
    cr: dict[str, float] = field(default_factory=dict)  # recipe -> its column scale
    gated: dict[str, list[int]] = field(default_factory=dict)
    raw_imports: list[int] = field(default_factory=list)
    target_rows: dict[str, dict[int, float]] = field(default_factory=dict)

    def var(self, lb: float = 0.0, ub: float = math.inf, integer: bool = False) -> int:
        self.lb.append(lb)
        self.ub.append(ub)
        self.integral.append(1 if integer else 0)
        self.n += 1
        return self.n - 1

    def row(self, coefs: dict[int, float], lo: float, hi: float) -> None:
        self.rows.append(({k: v for k, v in coefs.items() if v != 0.0}, lo, hi))


def _items(procs: Iterable[Proc]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    made: dict[str, list[str]] = {}
    used: dict[str, list[str]] = {}
    for p in procs:
        for k, a in p.outputs:
            if a > 0:
                made.setdefault(k, []).append(p.id)
        for k, a in p.inputs:
            if a > 0:
                used.setdefault(k, []).append(p.id)
    return made, used


def _build(
    net: Network,
    procs: list[Proc],
    big_m: float,
    floors: Mapping[str, float],
    cuts: Sequence[Mapping[str, str]],
) -> _Model:
    scale = max((r for _, r in net.targets), default=1.0)
    m = _Model(net, procs, big_m, scale, floors, cuts)
    ids = {p.id for p in procs}
    fixed = {pid: n for pid, n in net.fixed if pid in ids}
    for p in procs:
        m.cr[p.id] = max([1.0, *(a for k, a in (*p.inputs, *p.outputs) if k != EU_KEY)])
        if p.id in fixed and p.seconds > 0:
            v = fixed[p.id] / p.seconds / scale * m.cr[p.id]
            m.x[p.id] = m.var(v, v)
        else:
            m.x[p.id] = m.var(floors.get(p.id, 0.0))
    made, used = _items(procs)
    targets = {k: r / scale for k, r in net.targets}
    keys = sorted(set(made) | set(used) | set(targets))
    for k in keys:
        amounts = [a for p in procs for kk, a in (*p.inputs, *p.outputs) if kk == k]
        m.c[k] = max([1.0, *amounts])
    for k in keys:
        coefs: dict[int, float] = {}
        for p in procs:
            a = sum(v for kk, v in p.outputs if kk == k) - sum(
                v for kk, v in p.inputs if kk == k
            )
            if a:
                v = a / m.c[k] / m.cr[p.id]
                coefs[m.x[p.id]] = coefs.get(m.x[p.id], 0.0) + v
        gated: list[int] = []
        if k in targets:
            # At least the rate: a byproduct share of the target is exported with it.
            m.row(coefs, targets[k] / m.c[k], math.inf)
            m.target_rows[k] = coefs
            continue
        if k in used:
            i = m.imp[k] = m.var(0.0, 0.0 if k in net.banned else math.inf)
            coefs[i] = 1.0
            if k in made and k not in net.free_in:
                gated.append(i)
            elif k != EU_KEY:
                m.raw_imports.append(i)
        if k in made:
            e = m.exp[k] = m.var()
            coefs[e] = -1.0
            if k in used and k not in net.free_out:
                gated.append(e)
        m.row(coefs, 0.0, 0.0)
        if gated:
            g = m.gate[k] = m.var(0.0, 1.0, integer=True)
            m.gated[k] = gated
            m.row({**dict.fromkeys(gated, 1.0), g: -big_m}, -math.inf, 0.0)
    groups = [(k, [r for r in rs if r in ids]) for k, rs in net.choices]
    for _, rs in groups:
        for r in rs:
            if r not in m.z:
                m.z[r] = m.var(0.0, 1.0, integer=True)
                m.row({m.x[r]: 1.0, m.z[r]: -big_m}, -math.inf, 0.0)
    for _, rs in groups:
        if rs:
            m.row(dict.fromkeys((m.z[r] for r in rs), 1.0), 1.0, 1.0)
    for cut in cuts:
        picked = [m.z[r] for r in cut.values() if r in m.z]
        if picked:
            m.row(dict.fromkeys(picked, 1.0), -math.inf, len(picked) - 1.0)
    return m


def _objectives(m: _Model) -> list[dict[int, float]]:
    gates = dict.fromkeys(m.gate.values(), 1.0)
    quantity: dict[int, float] = {}
    for vs in m.gated.values():
        for v in vs:
            quantity[v] = 1.0
    for v in m.raw_imports:
        quantity[v] = quantity.get(v, 0.0) + TIEBREAK
    machines = {m.x[p.id]: p.seconds / m.cr[p.id] for p in m.procs if p.seconds > 0}
    return [gates, quantity, machines]


def _solve(
    m: _Model,
    objective: dict[int, float],
    extra: list[tuple[dict[int, float], float, float]],
) -> np.ndarray | None:
    rows = m.rows + extra
    c = np.zeros(m.n)
    for k, v in objective.items():
        c[k] = v
    if not rows:
        constraints: list[LinearConstraint] = []
    else:
        ri: list[int] = []
        ci: list[int] = []
        vals: list[float] = []
        for i, (coefs, _, _) in enumerate(rows):
            for k in sorted(coefs):
                ri.append(i)
                ci.append(k)
                vals.append(coefs[k])
        a = coo_array((vals, (ri, ci)), shape=(len(rows), m.n)).tocsr()
        constraints = [LinearConstraint(a, [r[1] for r in rows], [r[2] for r in rows])]
    with _quiet_stdout():
        res = milp(
            c,
            constraints=constraints,
            integrality=np.array(m.integral),
            bounds=Bounds(np.array(m.lb), np.array(m.ub)),
            options={**_OPTIONS, "time_limit": TIME_LIMIT_S},
        )
    if res.status != 0 or res.x is None:
        return None
    return np.asarray(res.x)


_STDOUT_LOCK = threading.Lock()


def _flush_c_stdio() -> None:
    """Flush the C runtime's stdio buffers (HiGHS prints through them)."""
    try:
        if sys.platform == "win32":
            ctypes.cdll.ucrtbase.fflush(None)
        else:
            ctypes.CDLL(None).fflush(None)
    except (OSError, AttributeError):
        pass


@contextmanager
def _quiet_stdout() -> Iterator[None]:
    """fd 1 points at the null device while HiGHS runs: some HiGHS builds print MIP debug
    lines straight to the C stdout, past ``disp=False`` and ``sys.stdout``, which would break
    ``gos plan --json`` and any service whose stdout is a protocol. One solve at a time."""
    with _STDOUT_LOCK:
        try:
            sys.stdout.flush()
        except (AttributeError, ValueError, OSError):
            pass
        try:
            saved = os.dup(1)
        except OSError:  # no fd 1 (a windowless process): nothing to protect
            yield
            return
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, 1)
            yield
        finally:
            _flush_c_stdio()
            os.dup2(saved, 1)
            os.close(saved)
            os.close(null)


def _value(obj: dict[int, float], x: np.ndarray) -> float:
    return float(sum(v * x[k] for k, v in obj.items()))


def _keep(obj: dict[int, float], best: float) -> tuple[dict[int, float], float, float]:
    """``obj`` kept at its optimum ``best`` (a row of the next stage)."""
    return obj, -math.inf, best + max(ABS_TOL, REL_TOL * abs(best))


def _open_gates(m: _Model, x: np.ndarray) -> dict[str, float]:
    """Each gate's state in ``x``: open when its binary is, or when its item crosses at all.
    HiGHS accepts a binary within its integrality tolerance of 0, which lets
    ``imp + exp <= M g`` leak up to about ``M x 1e-6``; such a crossing counts as a gate."""
    return {
        k: 1.0 if x[g] > 0.5 or sum(float(x[v]) for v in m.gated[k]) > RUNNING else 0.0
        for k, g in m.gate.items()
    }


def _lexicographic(
    m: _Model, strict: bool = True
) -> tuple[np.ndarray, tuple[float, float, float]] | None:
    """Stages 1-3; None when a stage finds no solution (``strict`` False: the last stage that
    found one). Stage 2 keeps stage 1's gate count; stage 3 keeps stage 2's gates (fixed, so
    nothing leaks) and its gated quantity."""
    gates, quantity, machines = _objectives(m)

    def result(x: np.ndarray) -> tuple[np.ndarray, tuple[float, float, float]]:
        return x, (_value(gates, x), _value(quantity, x), _value(machines, x))

    x = _solve(m, gates, [])
    if x is None:
        return None
    # Stage 1's count, leaks included; if exact arithmetic needs more (many leaks below the
    # noise threshold), one more gate at a time.
    n1 = sum(_open_gates(m, x).values())
    x2 = None
    for n in range(int(n1), len(m.gate) + 1):
        x2 = _solve(m, quantity, [(gates, -math.inf, n + 0.5)])
        if x2 is not None:
            break
    if x2 is None:
        return None if strict else result(x)
    for k, value in _open_gates(m, x2).items():
        m.lb[m.gate[k]] = m.ub[m.gate[k]] = value
    x2b = _solve(m, quantity, [])
    if x2b is None:
        return None if strict else result(x2)
    q = _value(quantity, x2b)
    x3 = _solve(m, machines, [_keep(quantity, q)])
    if x3 is None:
        return None if strict else result(x2b)
    return result(x3)


def _run(
    net: Network,
    procs: list[Proc],
    floors: Mapping[str, float] | None = None,
    cuts: Sequence[Mapping[str, str]] = (),
    strict: bool = True,
) -> tuple[_Model, np.ndarray, tuple[float, float, float]] | None:
    for big_m in BIG_M:
        m = _build(net, procs, big_m, floors or {}, cuts)
        out = _lexicographic(m, strict)
        if out is not None:
            return m, out[0], out[1]
    return None


# --- routes ---------------------------------------------------------------------------------


def _excluded(net: Network, choice: Mapping[str, str]) -> set[str]:
    """Recipes of a primary choice that ``choice`` didn't pick (idle on this route)."""
    out: set[str] = set()
    for k, rs in net.choices:
        if k in choice:
            out.update(r for r in rs if r != choice[k])
    picked = set(choice.values())
    return out - picked


def active(net: Network, choice: Mapping[str, str]) -> list[Proc]:
    """The recipes a route runs: those reached from the targets and fixed recipes through the
    items each was chosen to make, without the primary choices it didn't pick."""
    skip = _excluded(net, choice)
    procs = [p for p in net.procs if p.id not in skip]
    if not net.prune:
        return procs
    makers: dict[str, list[Proc]] = {}
    for p in procs:
        for k in p.made():
            makers.setdefault(k, []).append(p)
    keep: dict[str, Proc] = {}
    fixed = {pid for pid, _ in net.fixed}
    todo = [k for k, _ in net.targets]
    for p in procs:
        if p.id in fixed:
            keep[p.id] = p
            todo.extend(k for k, _ in p.inputs)
    seen: set[str] = set()
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        for p in makers.get(k, ()):
            if p.id not in keep:
                keep[p.id] = p
                todo.extend(kk for kk, _ in p.inputs)
    return [p for p in procs if p.id in keep]


def effective(net: Network, choice: Mapping[str, str]) -> dict[str, str]:
    """The entries of ``choice`` the route depends on: an entry whose recipe the route doesn't
    run (:func:`active`) is dropped when leaving it out gives the same recipes, e.g. the
    choice for an input of a target recipe the route didn't pick. Two routes with the same
    effective choice run the same recipes."""
    ids = {p.id for p in active(net, choice)}
    out = dict(choice)
    for k in sorted(choice):
        if choice[k] in ids:
            continue
        trial = {kk: r for kk, r in out.items() if kk != k}
        if {p.id for p in active(net, trial)} == ids:
            out = trial
    return out


def _choice(m: _Model, x: np.ndarray) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, rs in m.net.choices:
        on = [r for r in rs if r in m.z and x[m.z[r]] > 0.5]
        if on:
            out[k] = on[0]
    return out


def _flow(
    m: _Model,
    x: np.ndarray,
    objectives: tuple[float, float, float],
    choice: Mapping[str, str],
    floor: float | None,
    warnings: Sequence[str],
) -> Flow:
    s = m.scale

    def val(i: int, c: float = 1.0) -> float:
        v = float(x[i])
        return 0.0 if abs(v) < RUNNING else v * c * s

    crafts = tuple(sorted((p.id, val(m.x[p.id], 1 / m.cr[p.id])) for p in m.procs))
    imports = tuple(
        (k, val(i, m.c[k])) for k, i in sorted(m.imp.items()) if val(i, m.c[k]) > 0
    )
    exports = [
        (k, val(i, m.c[k])) for k, i in sorted(m.exp.items()) if val(i, m.c[k]) > 0
    ]
    exports += [
        (k, max(r, _value(m.target_rows[k], x) * m.c[k] * s)) for k, r in m.net.targets
    ]
    gates = tuple(sorted(k for k, g in m.gate.items() if x[g] > 0.5))
    return Flow(
        choice=tuple(sorted(choice.items())),
        crafts=crafts,
        imports=imports,
        exports=tuple(sorted(exports)),
        gates=gates,
        all_run=all(v > 0 for _, v in crafts),
        floor=floor,
        objectives=(round(objectives[0]), objectives[1] * s, objectives[2] * s),
        big_m=m.big_m,
        warnings=tuple(warnings),
    )


def solve_route(net: Network, choice: Mapping[str, str] | None = None) -> Flow:
    """Stages 1-4 for one route: the primary choices fixed to ``choice`` (by default the
    network's only recipe per choice; pins are always fixed)."""
    pick = dict(net.pinned)
    for k, rs in net.choices:
        if k not in pick and len(rs) == 1:
            pick[k] = rs[0]
    pick.update(choice or {})
    missing = sorted(k for k, _ in net.choices if k not in pick)
    if missing:
        raise SolveError(f"no recipe chosen for {', '.join(missing)}")
    procs = active(net, pick)
    if not procs:
        raise SolveError("no recipe makes the target")
    fixed = Network(
        procs=tuple(procs),
        targets=net.targets,
        fixed=net.fixed,
        free_in=net.free_in,
        free_out=net.free_out,
        prune=False,
        banned=net.banned,
    )
    first = _run(fixed, procs)
    if first is None:
        raise SolveError(
            "the route has no solution (the target can't be made at this rate)"
        )
    m, x, objs = first
    pick = effective(net, pick)
    flow = _flow(m, x, objs, pick, None, ())
    if flow.all_run:
        return flow
    running = [
        float(x[m.x[p.id]]) / m.cr[p.id] for p in procs if x[m.x[p.id]] >= RUNNING
    ]
    if not running:
        raise SolveError("no recipe runs")
    floor = FLOOR * min(running)  # crafts/s at a target of 1/s
    replay = _run(fixed, procs, floors=_floors(m, procs, floor))
    if replay is None:
        idle = [pid for pid, v in flow.crafts if v <= 0]
        return _flow(
            m,
            x,
            objs,
            pick,
            None,
            [f"every-machine-runs replay infeasible; idle: {', '.join(idle)}"],
        )
    m2, x2, objs2 = replay
    return _flow(m2, x2, objs2, pick, floor * m2.scale, ())


def _floors(m: _Model, procs: Sequence[Proc], floor: float) -> dict[str, float]:
    """Each recipe's stage-4 floor in its column's units: ``floor`` crafts/s (at a target of
    1/s), but at least FLOOR_MIN, which HiGHS doesn't round to 0."""
    return {p.id: max(floor * m.cr[p.id], FLOOR_MIN) for p in procs}


def routes(net: Network, n: int = 3) -> list[Flow]:
    """Up to ``n`` routes, each differing from the others in at least one primary choice.

    The choice MILP runs on the whole network, where a recipe of a branch that isn't chosen
    still counts as a user of what it takes, and HiGHS may let an unchosen recipe run a
    little within its integrality tolerance; so it only proposes a choice (stages 1-3, as far
    as they go). Each proposal is solved exactly as a route (:func:`solve_route`), a no-good
    cut on its effective choices (:func:`effective`) excludes it, and the MILP runs again,
    until ``n`` + :data:`EXPLORE` routes run every machine (at most that many x
    :data:`MAX_TRIES` proposals). A route with the same recipes as an earlier one is dropped,
    and so is a route whose every-machine-runs replay failed when another route runs every
    machine. The best ``n`` by their exact objectives (gates, gated quantity, machines), then
    their choices, are returned in that order: a fixed order, not a ranking (WO-3.5 ranks)."""
    pins = dict(net.pinned)
    open_choices = [(k, rs) for k, rs in net.choices if k not in pins and len(rs) > 1]
    narrowed = Network(
        procs=net.procs,
        targets=net.targets,
        fixed=net.fixed,
        free_in=net.free_in,
        free_out=net.free_out,
        prune=net.prune,
        choices=tuple(
            (k, (pins[k],)) if k in pins else (k, rs) for k, rs in net.choices
        ),
        pinned=net.pinned,
        banned=net.banned,
    )
    if not open_choices:
        return [solve_route(narrowed)]
    procs = [p for p in narrowed.procs if p.id not in _excluded(narrowed, pins)]
    cuts: list[dict[str, str]] = []
    out: list[Flow] = []
    seen: set[frozenset[str]] = set()
    failed: list[str] = []
    while (
        sum(f.all_run for f in out) < n + EXPLORE
        and len(cuts) < (n + EXPLORE) * MAX_TRIES
    ):
        found = _run(narrowed, procs, cuts=cuts, strict=False)
        if found is None:
            break
        m, x, _ = found
        choice = _choice(m, x)
        proposed = {k: choice[k] for k, _ in open_choices if k in choice}
        if len(proposed) < len(open_choices) or proposed in cuts:
            break
        try:
            flow = solve_route(narrowed, choice)
        except SolveError as exc:
            failed.append(str(exc))
            cuts.append(proposed)
            continue
        # The cut is on the choices the route depends on: every other assignment of the
        # choices it doesn't reach gives the same recipes, so it is excluded at once.
        chosen = dict(flow.choice)
        cut = {k: chosen[k] for k, _ in open_choices if k in chosen}
        cuts.append(cut if cut and cut not in cuts else proposed)
        recipes = frozenset(pid for pid, _ in flow.crafts)
        if recipes not in seen:
            seen.add(recipes)
            out.append(flow)
        if not cut:  # no open choice is reached: every route is this one
            break
    if not out:
        why = f": {failed[0]}" if failed else ""
        raise SolveError(f"no route has a solution{why}")
    if any(
        f.all_run for f in out
    ):  # a route that leaves machines idle is refused (step 4)
        out = [f for f in out if f.all_run]
    out.sort(key=lambda f: (len(f.gates), f.objectives[1:], f.choice))
    return out[:n]


def exact_counts(
    net: Network, flow: Flow, crafts_per_s: Mapping[str, float]
) -> dict[str, int]:
    """Step 6 on request: a whole machine count per recipe of ``flow``'s route, minimising
    their sum with the route's gates and gated quantity (and the stage-4 floor) kept. A
    recipe without a machine (``crafts_per_s`` 0 or missing) gets none."""
    route = {pid for pid, _ in flow.crafts}
    procs = [p for p in net.procs if p.id in route]
    fixed = Network(
        procs=tuple(procs),
        targets=net.targets,
        fixed=net.fixed,
        free_in=net.free_in,
        free_out=net.free_out,
        prune=False,
        banned=net.banned,
    )
    scale = max((r for _, r in net.targets), default=1.0)
    floor = flow.floor / scale if flow.floor else 0.0
    for big_m in BIG_M:
        m = _build(fixed, procs, big_m, {}, ())
        if floor:
            m = _build(fixed, procs, big_m, _floors(m, procs, floor), ())
        _, quantity, _ = _objectives(m)
        for k, g in m.gate.items():
            m.lb[g] = m.ub[g] = 1.0 if k in flow.gates else 0.0
        extra = [_keep(quantity, flow.objectives[1] / m.scale)]
        counts: dict[str, int] = {}
        for p in procs:
            cps = crafts_per_s.get(p.id, 0.0)
            if cps > 0:
                counts[p.id] = m.var(0.0, math.inf, integer=True)
                # x (normalised) x scale <= count x crafts/s of one machine
                x_real = m.scale / m.cr[p.id]
                extra.append(({m.x[p.id]: x_real, counts[p.id]: -cps}, -math.inf, 0.0))
        x = _solve(m, dict.fromkeys(counts.values(), 1.0), extra)
        if x is not None:
            return {pid: round(float(x[i])) for pid, i in sorted(counts.items())}
    raise SolveError("no whole machine counts keep the route's optimum")
