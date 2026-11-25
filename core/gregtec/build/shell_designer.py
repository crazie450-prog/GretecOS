"""The live shell designer (WO-10.17, D423): a model named in gos.toml draws every new AI shell
design and revision through the bake-off's tool-less seam.

Clayton, 2026-11-24, after the paid bake-off: "Astra was way faster, way cheaper, and had way
fewer errors... we'll stick with Astra for now". ``[shell] designer`` names a
``[[bakeoff.models]]`` label (or a built-in fake's, for tests); unset, nothing here runs and the
architect writes designs as before.

**One request** (:func:`design`, :func:`revise`): the brief is built as the bake-off builds it
(:func:`gregtec.build.shell_brief.prepare_design` / ``prepare_revise``), the model answers with
operations (no tools, no conversation), the product's own gate checks them
(:func:`gregtec.build.shell_store.make` / :func:`gregtec.build.shell_versions.revise`), and a
refused answer is sent back with the gate's reasons at most
:data:`gregtec.build.shell_rounds.REPAIR_ROUNDS` times (the same engine as the bake-off). An
accepted design is a new version (``by`` architect, with the real cost, the designer's label and
the rounds it took); nothing is saved on any failure.

**Spending** (:func:`held`, the guard): refused while Clayton's switch or the credit pause has the
AI off (the Anthropic API's own problems stop only an Anthropic designer), once today's spend
has reached ``[budget] day_max_usd``, and when one try can cost more than the request's cap
(``[shell] designer_max_usd``, lowered to what the day cap leaves). Every try, retries included,
is checked against the cap first; every answered try is metered (lane ``designer``) at its real
cost, and a try that broke off after it was sent, or came without its token count, at the most
it can have cost. A designer's own errors never put the other roles into No-AI.

**Unpriced models never run here**, and the key is read from the keyring per request.
Connections to the recipe DB are opened for the brief and the gate only, never across a model
call; under a shell job the planner's lock is held only for those steps too.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gregtec.build import palette, shell, shell_brief, shell_rounds, shell_store
from gregtec.build import shell_bakeoff as bo
from gregtec.build import shell_versions as versions
from gregtec.llm import designers as llm
from gregtec.llm.prices import Usage
from gregtec.planner import progress

if TYPE_CHECKING:
    from gregtec.agents.no_ai import AiMode
    from gregtec.config import Config
    from gregtec.llm.usage import UsageMeter
    from gregtec.planner.design_jobs import Lock
    from gregtec.store import Store
    from gregtec.vault.io import VaultIO

LANE = "designer"
OK, REFUSED, UNSET, NOT_READY, NOT_RUN = (
    "ok",
    "refused",
    "unset",
    "not_ready",
    "not_run",  # the CLI's question was not answered yes
)
UNSET_WHY = "no shell designer is set ([shell] designer in gos.toml)"
# Why a try failed when every retry was used up, as the refusal says it.
_FAILED = {
    llm.RATE: "rate limited",
    llm.SERVER: "a server error",
    llm.TIMEOUT: "timed out",
    llm.NETWORK: "couldn't be reached",
    llm.CUT_OFF: "the answer broke off",
    llm.TOO_LARGE: "the answer was too large",
}

Say = Callable[[str], None]


@dataclass(frozen=True)
class Ready:
    spec: bo.ModelSpec
    designer: llm.Designer

    @property
    def label(self) -> str:
        return self.spec.label


@dataclass(frozen=True)
class NotReady:
    label: str
    why: str


def build(spec: bo.ModelSpec, key: str) -> llm.Designer:
    """The designer for one model (the bake-off's runs build theirs here too)."""
    from gregtec.build import shell_fakes

    if spec.fake:
        return shell_fakes.FakeDesigner(spec.model)
    if spec.provider == "anthropic":
        return llm.AnthropicDesigner(llm.anthropic_client(key), spec.model, spec.effort)
    return llm.OpenAIDesigner(key, spec.model, spec.effort, url=llm.OPENAI_URL)


def resolve(cfg: Config, path: Path | None = None) -> Ready | NotReady | None:
    """The configured designer, read now (None: none is set). ``path`` is the gos.toml whose
    ``[bakeoff]`` names the label (default :func:`gregtec.config.default_path`)."""
    from gregtec import config
    from gregtec.llm import keys

    label = cfg.shell_designer
    if not label:
        return None
    try:
        settings = bo.read_settings(path or config.default_path())
    except (OSError, ValueError) as exc:  # ConfigError, BakeoffError, a bad TOML
        return NotReady(label, f"the [bakeoff] table can't be read: {exc}")
    spec = settings.models.get(label) or bo.FAKES.get(label)
    if spec is None:
        return NotReady(label, f"no [[bakeoff.models]] entry is labelled {label}")
    if spec.fake:
        return Ready(spec, build(spec, ""))
    if bo.cost(spec, Usage()) is None:
        return NotReady(
            label,
            f"{label} has no price in gos.toml: add input_usd and output_usd to its "
            "[[bakeoff.models]] entry; the live designer never runs unpriced",
        )
    try:
        key = keys.get_key(spec.provider)
    except keys.MissingKeyError:
        hint = (
            " (the setup wizard doesn't ask for it)"
            if spec.provider != "anthropic"
            else ""
        )
        return NotReady(
            label,
            f"no {spec.provider} key is stored: run `gos key set {spec.provider}`{hint}",
        )
    except Exception as exc:  # noqa: BLE001 - whatever the keyring raises
        return NotReady(label, f"the keyring can't be read ({type(exc).__name__})")
    return Ready(spec, build(spec, key))


def _day(cfg: Config, store: Store | None) -> tuple[float, float, float] | None:
    """Today's spend, the day cap and what it leaves in whole cents (None: no store)."""
    if store is None:
        return None
    from gregtec.ops.costs import CostWatch

    day = CostWatch.of(store, cfg).caps()["day"]
    left = math.floor((day["cap_usd"] - day["spent_usd"]) * 100 + 1e-6) / 100
    return float(day["spent_usd"]), float(day["cap_usd"]), left


def _ai_off(ai: AiMode | None, ready: Ready) -> str | None:
    """Why the AI is off for this designer: his switch and the credit pause for every one, the
    Anthropic API's own problems (no key, refused, down) only for an Anthropic one."""
    if ai is None:
        return None
    from gregtec.agents.no_ai import CHOICE, CREDITS, LABELS

    reasons = ai.reasons()
    hard = [r for r in reasons if r in (CHOICE, CREDITS)]
    if not hard and ready.spec.provider == "anthropic":
        hard = reasons
    if not hard:
        return None
    return f"the AI is off ({LABELS.get(hard[0], hard[0])}): nothing is spent"


def held(
    cfg: Config,
    store: Store | None,
    ai: AiMode | None,
    ready: Ready | NotReady | None,
) -> tuple[str | None, float]:
    """Why a request may not start now (one sentence; nothing is spent), and this request's cap:
    ``[shell] designer_max_usd``, no more than what the day cap still leaves."""
    if ready is None:
        return UNSET_WHY, 0.0
    if isinstance(ready, NotReady):
        return ready.why, 0.0
    if off := _ai_off(ai, ready):
        return off, 0.0
    cap = cfg.shell_designer_max_usd
    if ready.spec.fake:
        return None, cap
    day = _day(cfg, store)
    if day is None:
        return None, cap
    spent, top, left = day
    if left <= 0:
        return (
            f"today's AI spend ${spent:.2f} has reached the day cap ${top:.2f} ([budget] "
            "day_max_usd): the shell designer is off until tomorrow, or raise day_max_usd"
        ), 0.0
    return None, min(cap, left)


def checked(
    cfg: Config,
    store: Store | None,
    ai: AiMode | None,
    found: Ready | NotReady | None,
) -> Ready | NotReady | None:
    """:func:`resolve`'s designer as :class:`NotReady` when :func:`held` says a request may not
    start now (the AI off for it, the day cap reached): the one "ready" that the status, chat
    and the architect's tools share."""
    if not isinstance(found, Ready):
        return found
    why, _ = held(cfg, store, ai, found)
    return found if why is None else NotReady(found.label, why)


def status(
    cfg: Config,
    store: Store | None = None,
    ai: AiMode | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """``GET /api/shells/designer``: whether one is set, its label and provider (never a model
    id), whether it is ready now and why not, this request's cap and what today leaves."""
    if not cfg.shell_designer:
        return {"configured": False}
    ready = resolve(cfg, path)
    why, cap = held(cfg, store, ai, ready)
    day = _day(cfg, store)
    return {
        "configured": True,
        "label": cfg.shell_designer,
        "provider": ready.spec.provider if isinstance(ready, Ready) else None,
        "ready": why is None,
        "why": why,
        "cap_usd": cfg.shell_designer_max_usd if why is not None else cap,
        "day_left_usd": None if day is None else day[2],
        "ai_off": _ai_off(ai, ready) if isinstance(ready, Ready) else None,
    }


# --- one request -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Ctx:
    """What a request reads: gos.toml (and its path), GOS_HOME, the meter, the AI mode, the
    planner's lock (a shell job's; None outside the core) and the meter's task name."""

    cfg: Config
    home: Path
    store: Store | None = None
    meter: UsageMeter | None = None
    ai: AiMode | None = None
    lock: Lock | None = None
    path: Path | None = None
    task: str = "cli"
    sleep: Callable[[float], None] = time.sleep
    # asked once the brief is built, with this request's cap and the most one try can cost
    # (the CLI's "Type yes"); False: nothing is asked or spent
    confirm: Callable[[float, float], bool] | None = None

    @property
    def vault(self) -> VaultIO:
        from gregtec.vault.io import VaultIO

        return VaultIO(self.home / "vault")

    def connect(self) -> sqlite3.Connection:
        from gregtec.db import access

        return access.connect(access.db_path(self.home), readonly=True)


@dataclass
class Outcome:
    """A request's end: ``status`` OK (a version saved), REFUSED, UNSET, NOT_READY or NOT_RUN;
    ``result`` is the job's result (never a model id)."""

    status: str
    result: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status == OK


class _Stop(Exception):
    """The request ends here: the sentence says why, what was spent, and that nothing was saved."""


@contextmanager
def _locked(lock: Lock | None) -> Iterator[None]:
    if lock is None:
        yield
        return
    from gregtec.planner.design_jobs import planner

    with planner(lock):
        yield


@dataclass
class _Run:
    """One request's tries: the guard, the meter and what was spent."""

    ctx: Ctx
    ready: Ready
    cap: float
    mode: str
    system: str
    spent: float = 0.0
    tries: int = 0
    round: int = 0
    tokens: Usage = field(default_factory=Usage)
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.ready.label

    def bound(self, user: str) -> float:
        """The most one try with ``user`` can cost (0 for a fake)."""
        if self.ready.spec.fake:
            return 0.0
        return bo._most(self.system, self.ready.spec, user, self.mode)[0]

    def guard(self, user: str) -> None:
        """Before every try, a retry too: a Cancel first, then the cap, then the day cap read
        again (another process, the CLI or a bake-off, may have spent since the start)."""
        progress.check()
        n, most = self.tries + 1, self.bound(user)
        if self.spent + most > self.cap + 1e-9:
            raise _Stop(
                f"stopped before try {n}: ${self.spent:.2f} spent and the next try could "
                f"cost up to ${most:.2f}, over this request's cap ${self.cap:.2f}. Nothing "
                "was saved."
            )
        day = _day(self.ctx.cfg, self.ctx.store) if most else None
        if day is not None and day[0] + most > day[1] + 1e-9:
            raise _Stop(
                f"stopped before try {n}: today's AI spend is ${day[0]:.2f} and the next try "
                f"could cost up to ${most:.2f}, over the day cap ${day[1]:.2f} ([budget] "
                f"day_max_usd). ${self.spent:.2f} spent on this request; nothing was saved."
            )
        self.tries = n

    def broke(self, user: str) -> None:
        """A try that may be billed with no usage to count: counted and metered at its most."""
        most = self.bound(user)
        if not most:
            return
        self.spent += most
        if self.ctx.meter is not None:
            self.ctx.meter.record(
                LANE,
                self.ready.spec.model,
                Usage(),
                usd=most,
                task=f"designer:{self.ctx.task}:bound",
            )

    def ask(self, user: str) -> llm.Reply | str:
        try:
            reply = llm.call(
                shell_rounds.Capped(self.ready.designer, self.guard, self.broke),
                self.system,
                user,
                shell_brief.schema(self.mode),
                shell_brief.MAX_OUTPUT_TOKENS,
                sleep=self.ctx.sleep,
            )
        except llm.ProviderError as exc:
            raise _Stop(self._failed(exc)) from None
        self.tokens.add(reply.usage)
        if reply.measured and not self.ready.spec.fake:
            usd = bo.cost(self.ready.spec, reply.usage) or 0.0
            self.spent += usd
            if self.ctx.meter is not None:
                self.ctx.meter.record(
                    LANE,
                    self.ready.spec.model,
                    reply.usage,
                    usd=usd,
                    task=f"designer:{self.ctx.task}",
                )
        return reply

    def _failed(self, exc: llm.ProviderError) -> str:
        label, provider = self.label, self.ready.spec.provider
        spent = f"${self.spent:.2f} spent, nothing saved"
        if exc.kind == llm.AUTH:
            return f"{label} refused the {provider} key: run `gos key set {provider}` ({spent})"
        if exc.kind == llm.QUOTA:
            return f"{provider} says {label}'s quota or credits are used up ({spent})"
        if exc.kind == llm.BAD_REQUEST:
            return f"{provider} rejected the request: {llm.scrub(str(exc))} ({spent})"
        why = _FAILED.get(exc.kind, llm.scrub(str(exc)))
        return f"{label} didn't answer after {self.tries} tries ({why}): {spent}"

    def refused(self, why: str) -> None:
        self.attempts.append({"round": self.round, "why": bo.plain(why)})

    def on_round(self, n: int, say: Say) -> None:
        self.round = n
        say(
            f"repair {n} of {shell_rounds.REPAIR_ROUNDS}"
            if n
            else f"asking {self.label}"
        )

    def result(self, started: float) -> dict[str, Any]:
        return {
            "designer": self.label,
            "rounds": self.round,
            "cost_usd": round(self.spent, 6),
            "tokens": {"in": self.tokens.input, "out": self.tokens.output},
            "seconds": round(time.monotonic() - started, 1),
        }


def _refused(why: str, status: str = REFUSED, **more: Any) -> Outcome:  # noqa: ANN401
    return Outcome(status, {"refused": [why], **more})


def _start(ctx: Ctx) -> tuple[Ready, float] | Outcome:
    """The pre-flight: set, ready, the AI on, the day cap not reached."""
    ready = resolve(ctx.cfg, ctx.path)
    why, cap = held(ctx.cfg, ctx.store, ctx.ai, ready)
    if why is None and isinstance(ready, Ready):
        return ready, cap
    kind = (
        UNSET
        if ready is None
        else NOT_READY
        if isinstance(ready, NotReady)
        else REFUSED
    )
    return _refused(why or UNSET_WHY, kind, designer=ctx.cfg.shell_designer or None)


def _one_try(run: _Run, brief: str) -> str | None:
    """Why the request's cap can't pay for even one try (None when it can)."""
    most = run.bound(brief)
    if most <= run.cap:
        return None
    lowered = (
        " (what today's [budget] day_max_usd leaves)"
        if run.cap < run.ctx.cfg.shell_designer_max_usd
        else ""
    )
    return (
        f"one try of {run.label} can cost up to ${most:.2f}; this request's cap is "
        f"${run.cap:.2f}{lowered}: raise [shell] designer_max_usd"
    )


def _ask(
    run: _Run,
    brief: str,
    gate: Callable[[shell_brief.Answer], str | None],
    say: Say,
    started: float,
    saved: dict[str, Any],
) -> Outcome:
    """The rounds and the request's outcome."""
    if why := _one_try(run, brief):
        return _refused(why, designer=run.label, cost_usd=0.0)
    confirm = run.ctx.confirm
    if confirm is not None and not confirm(run.cap, run.bound(brief)):
        return _refused("not run: nothing was spent", NOT_RUN, designer=run.label)
    try:
        ok = shell_rounds.rounds(
            run.ask,
            brief,
            run.mode,
            gate,
            run.refused,
            lambda n: run.on_round(n, say),
        )
    except _Stop as stop:
        return _refused(str(stop), **run.result(started), attempts=run.attempts)
    if ok:
        return Outcome(OK, {**saved, **run.result(started)})
    n = len(run.attempts)
    last = run.attempts[-1]["why"] if run.attempts else "no answer"
    all_ = (
        f"{run.label}'s {n} answers were all refused"
        if n > 1
        else (f"{run.label}'s answer was refused")
    )
    return _refused(
        f"{all_}. Last: {last}. ${run.spent:.2f} spent, nothing saved.",
        **run.result(started),
        attempts=run.attempts,
    )


def _brief_problem(exc: BaseException) -> str:
    return f"GOS can't brief the designer: {exc}"


def design(
    ctx: Ctx, req: shell_brief.DesignRequest, say: Say = progress.stage
) -> Outcome:
    """A new design drawn by the configured designer (see the module). ``say(stage)`` reports
    the stage (under a shell job: :func:`gregtec.planner.progress.stage`, a cancel point)."""
    started = time.monotonic()
    pre = _start(ctx)
    if isinstance(pre, Outcome):
        return pre
    ready, cap = pre
    say("brief")
    try:
        with _locked(ctx.lock):
            conn = ctx.connect()
            try:
                got = shell_brief.prepare_design(conn, ctx.home, ctx.vault, req)
            finally:
                conn.close()
    except (ValueError, OSError, sqlite3.Error, palette.CatalogError) as exc:
        return _refused(_brief_problem(exc), designer=ready.label, cost_usd=0.0)
    run = _Run(ctx, ready, cap, req.mode, shell_brief.system_prompt(req.mode))
    saved: dict[str, Any] = {}

    def gate(said: shell_brief.Answer) -> str | None:
        say("checking")
        with _locked(ctx.lock):
            conn = ctx.connect()
            try:
                made = shell_store.make(
                    conn,
                    ctx.home,
                    ctx.vault,
                    list(req.plans),
                    theme_id=req.theme,
                    prompt=req.words,
                    line_type=req.line_type,
                    max_blocks=req.max_blocks,
                    headroom=req.headroom,
                    relief=req.relief,
                    extra=req.extra,
                    layout_options=req.layout_options,
                    ops=said.ops,
                    mode=req.mode,
                    entrance=said.entrance,
                    zones=said.zones,
                    area=req.area,
                    site_factor=req.site_factor,
                )
            except (shell.ShellError, palette.CatalogError) as exc:
                return f"no shell: {exc}"
            finally:
                conn.close()
            if not made.outcome.ok:
                return " | ".join(made.outcome.reasons)
            d = versions.start(
                ctx.home,
                made.build,
                asked=req.words or "designed",
                by="architect",
                cost_usd=round(run.spent, 6),
                designer=run.label,
                rounds=run.round,
            )
        v = next(v for v in d["versions"] if v["build"] == made.build)
        saved.update(design=d["id"], build=made.build, version=v["n"])
        return None

    return _ask(run, got.brief, gate, say, started, saved)


def revise(ctx: Ctx, ds: str, words: str, say: Say = progress.stage) -> Outcome:
    """A new version of design ``ds`` drawn by the configured designer from Clayton's words."""
    started = time.monotonic()
    pre = _start(ctx)
    if isinstance(pre, Outcome):
        return pre
    ready, cap = pre
    say("brief")
    try:
        with _locked(ctx.lock):
            conn = ctx.connect()
            try:
                brief, old = shell_brief.prepare_revise(conn, ctx.home, ds, words)
            finally:
                conn.close()
    except (ValueError, OSError, sqlite3.Error, palette.CatalogError) as exc:
        return _refused(_brief_problem(exc), designer=ready.label, cost_usd=0.0)
    hinted = shell_store.site_mode(old) or shell_store.free_mode(old)
    mode = (
        "free"
        if shell_store.free_mode(old)
        else "site"
        if shell_store.site_mode(old)
        else "layout"
    )
    run = _Run(ctx, ready, cap, mode, shell_brief.system_prompt(mode))
    saved: dict[str, Any] = {}

    def gate(said: shell_brief.Answer) -> str | None:
        say("checking")
        with _locked(ctx.lock):
            conn = ctx.connect()
            try:
                got = versions.revise(
                    conn,
                    ctx.home,
                    ctx.vault,
                    ds,
                    words,
                    ops=said.ops,
                    by="architect",
                    asked=words,
                    cost_usd=round(run.spent, 6),
                    designer=run.label,
                    rounds=run.round,
                    # the answer's door and zones replace the parent's (the brief shows them)
                    **(
                        {
                            "entrance": versions.AUTO
                            if said.entrance is None
                            else said.entrance,
                            "zones": said.zones,
                        }
                        if hinted
                        else {}
                    ),
                )
            except (shell.ShellError, palette.CatalogError) as exc:
                return f"no revision: {exc}"
            finally:
                conn.close()
        if got.reasons or got.record is None or got.version is None:
            return " | ".join(got.reasons) or "the revision was not saved"
        saved.update(
            design=ds,
            build=got.build,
            version=got.version["n"],
            changes=list(got.changes),
        )
        return None

    return _ask(run, brief, gate, say, started, saved)
