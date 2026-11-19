"""A design's stages and its cancel checkpoints (WO-3.9, D353).

The planner calls :func:`stage` where a design moves on (the graph, each solver call, each
route's metrics, the ranking) and :func:`check` between smaller steps (each item the graph
builder expands, each component the effort walk makes). Both do nothing unless a design job
(:mod:`gregtec.planner.design_jobs`) runs the code under :func:`reporting`: then a stage is reported to
the job, and either raises :class:`Cancelled` once the job is cancelled. Nothing is written
before the last checkpoint of a design (saving comes after it), so a cancelled design leaves no
plan, vault note or ledger line behind.

The reporter lives in a context variable, so a design run by the CLI, an agent tool, the voice
client or the Discord bot (no job) works exactly as before, and two threads never see each
other's job.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# (stage, k, n) -> None; raises Cancelled when the job was cancelled
Reporter = Callable[[str | None, int | None, int | None], None]

_current: ContextVar[Reporter | None] = ContextVar("gos_design_reporter", default=None)


class Cancelled(Exception):
    """The design job was cancelled at a checkpoint; nothing was saved."""


def stage(name: str, k: int | None = None, n: int | None = None) -> None:
    """The design is at ``name`` (``k`` of ``n`` when it counts); a cancel stops it here."""
    report = _current.get()
    if report is not None:
        report(name, k, n)


def check() -> None:
    """A cancel checkpoint that reports no new stage."""
    report = _current.get()
    if report is not None:
        report(None, None, None)


@contextmanager
def reporting(report: Reporter) -> Iterator[None]:
    """Run the block with ``report`` as the design's reporter."""
    token = _current.set(report)
    try:
        yield
    finally:
        _current.reset(token)
