"""The shell designer bake-off: the same briefs to several models, through the same gate, rated
blind (WO-10.11, plan 7.10 "Shell designer", D374-D378).

Clayton, 2026-11-20: "I do think a bakeoff so to speak would be a good idea so i can also test
out the framework of the building generator app".

**What a run does.** For every case (a brief and one follow-up, ``tests/evals/shell_bakeoff.tsv``)
and every model named, it asks for a design (:mod:`gregtec.build.shell_brief`: brief in, shell
operations out, no tools), hands the answer to the product's own gate
(:func:`gregtec.build.shell_store.make`, so :func:`gregtec.build.shell.design`), and, when the
gate refuses it, sends the brief again with the answer and the gate's reasons, at most
:data:`REPAIR_ROUNDS` times: the same rounds for every model, each recorded. An accepted or
repaired design is saved as version 1 of a design; the follow-up then goes through
:func:`gregtec.build.shell_versions.revise` the same way and becomes version 2. Per step it
records: passed on the first try, repair rounds, why refused, blocks, palette entries used, the
follow-up's deterministic checks (:func:`gregtec.build.shell_eval.check` and ``kept``), seconds,
tokens and cost.

**Where it is kept.** ``GOS_HOME/data/bakeoff/<run-id>/``: ``run.json`` (everything above, the
seed, the ratings) and, per case and per **blind label**, a small GOS_HOME of its own
(``entries/<case>/<A|B|...>/data/build/...``) holding that answer's shell builds and its design
file. Nothing is written into ``data/build/shells`` or ``data/build/designs``: a bake-off shell is
never the current design, is not listed by ``gos shell list`` and cannot be exported by ``gos
shell export`` (it is not in the folder that command reads). :func:`delete` removes a whole run.

**Blind.** Per case the models are shuffled with ``random.Random(f"{seed}|{case id}")`` and
lettered A, B, C in that order; the seed is stored, so the lettering is reproducible. Folder
names, version notes, the workbench's views and :func:`blind` carry the letter only. The model
behind a letter is in ``run.json`` alone and is shown by :func:`report` once every rateable
case is rated (or Clayton gives up rating: ``reveal_unrated``); after that ratings are closed.

**Spending** is Clayton's: :func:`estimate` says what a run can cost and what it contacts,
:func:`execute` stops before any call that could take the run past its cap (USD for priced
models, tokens for the ones without a price in ``gos.toml``), and every real call is recorded in
``llm_usage`` (lane ``bakeoff``), so it counts towards the cycle and the day. A call that broke
off after it was sent (a timeout, a cut-off answer) reports no usage, yet the vendor may bill
it: each is counted against the cap at the most it can have cost (``run["unmeasured"]``), and
the report says how many there were. An answer that came without its token count is counted
the same way. A model that can't be asked at all (a refused key, a wrong model id, no credit)
stops the run before more is spent on the others (D378).

**Run.json on Windows.** The dashboard reads a run while the runner replaces its ``run.json``,
and Windows refuses a replace while another program has the target open (D356's table). So a
run is read with :func:`gregtec.gamestate.safe_open.read_bytes` (it gives way to the writer),
every write is tried again for a short while (:data:`SAVE_TRIES`), and a runner that still
can't replace ``run.json`` keeps its record beside it (``run.unsaved.json``; :func:`load` takes
the newer of the two). A run whose window was shut stays ``running``: :func:`close` ends it as
cancelled with its finished answers, once it has been silent for :data:`STALE_S`.

Nothing here writes a world or a ``.schematic``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
import shutil
import sqlite3
import time
import tomllib
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.build import layout as layout_lib
from gregtec.build import (
    palette,
    shell,
    shell_brief,
    shell_eval,
    shell_fakes,
    shell_store,
)
from gregtec.build import shell_versions as versions
from gregtec.build import themes as theme_lib
from gregtec.config import ConfigError
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest.fingerprint import atomic_write
from gregtec.llm import designers as llm
from gregtec.llm.prices import PRICES, Price, Usage, estimate_cost
from gregtec.llm.usage import UsageMeter
from gregtec.planner.outputs import store as plan_store
from gregtec.vault.io import VaultIO

SCHEMA = "gos-shell-bakeoff"
VERSION = 1
ROOT = Path("data", "bakeoff")  # in GOS_HOME
REPORTS = Path("data", "reports")
REPO = Path(__file__).resolve().parents[3]
CASES = REPO / "tests" / "evals" / "shell_bakeoff.tsv"
COLUMNS = (
    "id",
    "line",
    "theme",
    "line_type",
    "max_blocks",
    "brief",
    "followup",
    "expect",
    "touches",
)
# optional last columns, in this order: the case's headroom and relief ("default" or a number;
# WO-10.13, D386) and its own palette ("role registry@meta, ..." for a case without a theme, or
# extra entries for one with a theme; WO-10.14, D387)
HEADROOM_COLUMN = "headroom"
OPTIONAL = ("headroom", "relief", "palette")
LINES = ("small", "large")
RUN_ID = re.compile(r"bo-\d{8}-\d{6}-[0-9a-f]{4}")
CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")
LETTERS = "ABCDEFGH"
MAX_MODELS = len(LETTERS)
MAX_CASES = 24
REPAIR_ROUNDS = 2  # after the first answer, for every model and every step
TIGHT = 1.10  # a "tight" case allows this share of GOS's own procedural shell's blocks
DEFAULT_CAP_USD = 10.0
DEFAULT_CAP_TOKENS = 1_000_000
PROVIDERS = ("anthropic", "openai")
HOSTS = {"anthropic": llm.ANTHROPIC_HOST, "openai": llm.OPENAI_HOST}
TIE, BAD = "tie", "bad"
LANE = "bakeoff"
DONE, RUNNING, CAP, CANCELLED = "done", "running", "stopped at the cap", "cancelled"
STOPPED = "stopped"  # by something that is not a cap or a Cancel: ``why`` says what
RECORD, BESIDE = "run.json", "run.unsaved.json"
SAVE_TRIES = 12  # of one write, while Windows refuses it because the file is held open
SAVE_WAIT_S = 0.25  # between two tries
# A run that has saved nothing for this long is taken as gone (close). The runner saves before
# every try, and a try is over well inside this (the provider's timeout; an answer of the whole
# output limit streams in a few minutes). Should one ever last longer and the run be closed
# meanwhile, the runner finds that at its next save and stops without writing over it.
STALE_S = 900.0
assert STALE_S > llm.TIMEOUT_S + llm.MAX_WAIT_S
# The share of the output limit a first look assumes an answer uses (the estimate's "likely").
LIKELY_OUTPUT = 0.25


class BakeoffError(shell.ShellError):
    """A bake-off request that can't be done (said as is; nothing was written or spent)."""


def plain(text: str, limit: int = 1000) -> str:
    """Words a model or a vendor wrote, safe to print in a terminal and a table: one line,
    without control or format characters (ESC, newlines, bidi marks), clipped."""
    kept = (" " if unicodedata.category(c)[0] in "CZ" else c for c in text)
    return " ".join("".join(kept).split())[:limit]


def check_caps(cap_usd: float, cap_tokens: int) -> None:
    """A cap that is not a plain number above 0 never trips (nan compares false, inf is never
    reached): refused."""
    if isinstance(cap_usd, bool) or not isinstance(cap_usd, int | float):
        raise BakeoffError("the dollar cap must be a number above 0")
    if not math.isfinite(cap_usd) or cap_usd <= 0:
        raise BakeoffError(
            f"the dollar cap must be a plain number above 0, not {cap_usd}: for example "
            "--cap-usd 5"
        )
    if type(cap_tokens) is not int or cap_tokens <= 0:
        raise BakeoffError(
            f"the token cap must be a whole number above 0, not {cap_tokens}: for example "
            "--cap-tokens 500000"
        )


# --- the models ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    label: str
    provider: str
    model: str
    effort: str | None = None
    price: Price | None = None  # USD per million tokens; None: unknown

    @property
    def fake(self) -> bool:
        return self.provider == shell_fakes.PROVIDER

    def to_json(self) -> dict[str, Any]:
        p = self.price
        return {
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "price": None
            if p is None
            else {
                "input": p.input,
                "output": p.output,
                "cache_read": p.cache_read,
                "cache_write": p.cache_write,
            },
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> ModelSpec:
        p = d.get("price")
        return cls(
            d["label"],
            d["provider"],
            d["model"],
            d.get("effort"),
            None if p is None else Price(**p),
        )


FAKES = {n: ModelSpec(n, shell_fakes.PROVIDER, n) for n in shell_fakes.NAMES}
_MODEL_KEYS = frozenset(
    {
        "label",
        "provider",
        "model",
        "effort",
        "input_usd",
        "output_usd",
        "cached_input_usd",
        "cache_write_usd",
    }
)
_SECRETISH = re.compile(r"key|token|secret|password", re.IGNORECASE)


@dataclass(frozen=True)
class Settings:
    """``[bakeoff]`` of gos.toml: the models a run may name and the run's caps."""

    models: Mapping[str, ModelSpec]
    cap_usd: float = DEFAULT_CAP_USD
    cap_tokens: int = DEFAULT_CAP_TOKENS

    def pick(self, labels: Sequence[str]) -> list[ModelSpec]:
        if not 1 <= len(labels) <= MAX_MODELS:
            raise BakeoffError(f"name 1 to {MAX_MODELS} models")
        if len(set(labels)) != len(labels):
            raise BakeoffError("a model is named twice")
        out = []
        for name in labels:
            spec = FAKES.get(name) or self.models.get(name)
            if spec is None:
                known = ", ".join([*self.models, *FAKES])
                raise BakeoffError(
                    f"no bake-off model {name!r}: gos.toml's [[bakeoff.models]] and the "
                    f"built-in fakes are {known}"
                )
            out.append(spec)
        return out


def _usd(table: Mapping[str, Any], key: str, where: str) -> float | None:
    v = table.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 10_000:
        raise ConfigError(
            f"gos.toml: {where} {key} must be USD per million tokens (a number from 0)"
        )
    return float(v)


def settings_of(data: Mapping[str, Any]) -> Settings:
    """``[bakeoff]`` from a parsed gos.toml. Read only when a bake-off command runs, so a
    mistake here never stops the core. A key is never read from the file: it lives in the
    keyring (``gos key set openai``)."""
    table = data.get("bakeoff", {})
    if not isinstance(table, dict):
        raise ConfigError("gos.toml: bakeoff must be a table")
    extra = set(table) - {"models", "cap_usd", "cap_tokens"}
    if extra:
        raise ConfigError(
            f"gos.toml: [bakeoff] has no key {', '.join(sorted(extra))} "
            "(it has cap_usd, cap_tokens and [[bakeoff.models]])"
        )
    cap_usd = table.get("cap_usd", DEFAULT_CAP_USD)
    if (
        isinstance(cap_usd, bool)
        or not isinstance(cap_usd, int | float)
        or not math.isfinite(cap_usd)  # nan and inf are TOML numbers, and never trip
        or cap_usd <= 0
    ):
        raise ConfigError(
            "gos.toml: [bakeoff] cap_usd must be a number of USD above 0 (not nan or inf)"
        )
    cap_tokens = table.get("cap_tokens", DEFAULT_CAP_TOKENS)
    if type(cap_tokens) is not int or cap_tokens <= 0:
        raise ConfigError(
            "gos.toml: [bakeoff] cap_tokens must be a whole number above 0"
        )
    rows = table.get("models", [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ConfigError(
            "gos.toml: bake-off models are [[bakeoff.models]] tables (label, provider, model)"
        )
    models: dict[str, ModelSpec] = {}
    for n, row in enumerate(rows, 1):
        where = f"[[bakeoff.models]] #{n}"
        unknown = set(row) - _MODEL_KEYS
        if secret := sorted(k for k in unknown if _SECRETISH.search(k)):
            raise ConfigError(
                f"gos.toml: {where} has {', '.join(secret)}: a key never goes into gos.toml. "
                "Store it in the keyring: uv run gos key set openai (or anthropic)"
            )
        if unknown:
            raise ConfigError(
                f"gos.toml: {where} has no key {', '.join(sorted(unknown))} (it has "
                f"{', '.join(sorted(_MODEL_KEYS))})"
            )
        label, provider, model = row.get("label"), row.get("provider"), row.get("model")
        if not isinstance(label, str) or not LABEL.fullmatch(label):
            raise ConfigError(
                f"gos.toml: {where} label must be a short name (a-z, 0-9, -), e.g. 'astra'"
            )
        if label.startswith("fake") or label in models:
            raise ConfigError(
                f"gos.toml: {where} label {label!r} is taken (fake-* are the built-in fakes)"
            )
        if provider not in PROVIDERS:
            raise ConfigError(
                f"gos.toml: {where} provider must be one of {', '.join(PROVIDERS)}"
            )
        if (
            not isinstance(model, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:\-]{1,99}", model)
            or model.startswith("sk-")
        ):
            raise ConfigError(
                f"gos.toml: {where} model must be the model's id, e.g. 'gpt-6-astra'"
            )
        effort = row.get("effort")
        if effort is not None and effort not in llm.EFFORTS[provider]:
            raise ConfigError(
                f"gos.toml: {where} effort must be one of "
                f"{', '.join(llm.EFFORTS[provider])} for a model of {provider}"
            )
        cin, cout = _usd(row, "input_usd", where), _usd(row, "output_usd", where)
        if (cin is None) != (cout is None):
            raise ConfigError(
                f"gos.toml: {where} give input_usd and output_usd together, or neither"
            )
        price: Price | None = None
        if provider == "anthropic" and model in PRICES:
            price = PRICES[model]  # the repo's own table (gregtec.llm.prices) wins
        elif cin is not None and cout is not None:
            cached = _usd(row, "cached_input_usd", where)
            write = _usd(row, "cache_write_usd", where)
            price = Price(
                input=cin,
                output=cout,
                # never less than the vendors' pages say: cached input at most the input
                # price, a cache write 1.25 times it
                cache_read=cin if cached is None else cached,
                cache_write=cin * 1.25 if write is None else write,
            )
        models[label] = ModelSpec(label, provider, model, effort, price)
    return Settings(models, float(cap_usd), cap_tokens)


def read_settings(path: Path) -> Settings:
    """``[bakeoff]`` of the gos.toml at ``path`` (a missing file: no models, the defaults)."""
    if not path.is_file():
        return Settings({})
    with open(path, "rb") as f:
        return settings_of(tomllib.load(f))


def cost(model: ModelSpec, usage: Usage) -> float | None:
    """USD of one call (0 for a fake; None when the model has no price: never guessed)."""
    if model.fake:
        return 0.0
    if model.provider == "anthropic" and model.model in PRICES:
        return estimate_cost(model.model, usage)
    p = model.price
    if p is None:
        return None
    return (
        usage.input * p.input
        + usage.output * p.output
        + usage.cache_write * p.cache_write
        + usage.cache_read * p.cache_read
    ) / 1_000_000


# --- the cases -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    id: str
    line: str  # small or large: which of the run's plans it houses
    theme: str
    line_type: str
    max_blocks: str  # "default", "tight" or a number
    brief: str
    followup: str
    expect: tuple[str, ...]
    touches: frozenset[str]
    headroom: int | None = None  # None: shell.LIMITS' default
    relief: int | None = None
    palette: tuple[tuple[str, str], ...] = ()  # (role, registry@meta), the case's own


def load_cases(path: Path = CASES) -> list[Case]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    out = []
    for n, r in enumerate(rows, 2):
        where = f"{path.name} line {n}"
        extra = tuple(r)[len(COLUMNS) :]
        if tuple(r)[: len(COLUMNS)] != COLUMNS or extra != OPTIONAL[: len(extra)]:
            raise BakeoffError(
                f"{path.name}: the columns are {' '.join(COLUMNS)}, then optionally "
                f"{' '.join(OPTIONAL)}"
            )
        sizes: dict[str, int | None] = {}
        for key in ("headroom", "relief"):
            text = r.get(key) or "default"
            _, lo_v, hi_v = shell.LIMITS[key]
            if text != "default" and not (text.isdigit() and lo_v <= int(text) <= hi_v):
                raise BakeoffError(
                    f"{where}: {key} is default or a number from {lo_v} to {hi_v}"
                )
            sizes[key] = None if text == "default" else int(text)
        own: list[tuple[str, str]] = []
        for part in (r.get("palette") or "").split(","):
            if not part.strip():
                continue
            bits = part.split()
            if len(bits) != 2 or bits[0] not in theme_lib.ROLES or "@" not in bits[1]:
                raise BakeoffError(
                    f"{where}: a palette entry is '<role> <registry name>@<meta>', "
                    f"not {part.strip()!r}"
                )
            own.append((bits[0], bits[1]))
        if r["theme"] == "none" and not own:
            raise BakeoffError(f"{where}: a case without a theme needs its own palette")
        if not CASE_ID.fullmatch(r["id"]):
            raise BakeoffError(f"{where}: {r['id']!r} is not a case id (a-z, 0-9, -)")
        if r["line"] not in LINES:
            raise BakeoffError(f"{where}: line is one of {', '.join(LINES)}")
        if r["line_type"] not in theme_lib.LINE_TYPES:
            raise BakeoffError(f"{where}: {r['line_type']!r} is not a line type")
        mb = r["max_blocks"]
        if mb not in ("default", "tight") and not mb.isdigit():
            raise BakeoffError(f"{where}: max_blocks is default, tight or a number")
        if (
            len(r["brief"]) > theme_lib.MAX_TEXT * 5
            or not 0 < len(r["followup"]) <= 400
        ):
            raise BakeoffError(
                f"{where}: the brief or the follow-up is too long (or empty)"
            )
        expect = tuple(x.strip() for x in r["expect"].split(";") if x.strip())
        for x in expect:
            if x != shell_eval.CHANGED and not shell_eval._CHECK.match(x):
                raise BakeoffError(f"{where}: {x!r} is not a check")
        touches = frozenset(x.strip() for x in r["touches"].split(",") if x.strip())
        if not expect or not touches or not touches <= set(shell.OPS):
            raise BakeoffError(f"{where}: expect and touches (op kinds) are needed")
        out.append(
            Case(
                r["id"],
                r["line"],
                r["theme"],
                r["line_type"],
                mb,
                r["brief"],
                r["followup"],
                expect,
                touches,
                sizes["headroom"],
                sizes["relief"],
                tuple(own),
            )
        )
    if not out or len(out) > MAX_CASES:
        raise BakeoffError(f"{path.name}: 1 to {MAX_CASES} cases")
    if len({c.id for c in out}) != len(out):
        raise BakeoffError(f"{path.name}: case ids repeat")
    return out


@dataclass(frozen=True)
class Prepared:
    """A case ready to ask: its plans, sizes and the brief every model gets."""

    case: Case
    plans: tuple[str, ...]
    max_blocks: int
    baseline: int  # blocks of GOS's own procedural shell for this case (for the report)
    brief: str  # the user text (the same for every model)


def _preview(
    conn: sqlite3.Connection,
    home: Path,
    vault: VaultIO | None,
    case: Case,
    ids: Sequence[str],
    max_blocks: int | None,
) -> shell_store.Made:
    """GOS's own procedural shell for the case, never saved: its record gives the sizes, the
    palette and the doorways every model's brief states."""
    return shell_store.make(
        conn,
        home,
        vault,
        list(ids),
        theme_id=None if case.theme == "none" else case.theme,
        prompt=case.brief,
        line_type=case.line_type,
        max_blocks=max_blocks,
        headroom=case.headroom,
        relief=case.relief,
        extra=case.palette,
        save_it=False,
    )


def prepare(
    conn: sqlite3.Connection,
    home: Path,
    vault: VaultIO | None,
    cases: Sequence[Case],
    plans: Mapping[str, Sequence[str]],
) -> list[Prepared]:
    """Lay every case out and write its brief, before anything is estimated or spent. A case
    that can't be laid out (a plan that isn't saved, no such theme) refuses the whole run."""
    out = []
    for case in cases:
        ids = tuple(plans.get(case.line) or ())
        if not ids:
            raise BakeoffError(f"case {case.id} needs the {case.line} line's plans")
        try:
            loaded = [plan_store.load(home, pid) for pid in ids]
            lay = layout_lib.plan_layout(conn, loaded)

            made = _preview(conn, home, vault, case, ids, None)
            if not made.outcome.ok:
                raise BakeoffError(" | ".join(made.outcome.reasons))
            baseline = len(made.outcome.cells)
            if case.max_blocks != "default":
                # the record's sizes are what the brief states; the gate judges each answer
                limit = (
                    math.ceil(baseline * TIGHT)
                    if case.max_blocks == "tight"
                    else int(case.max_blocks)
                )
                made = _preview(conn, home, vault, case, ids, limit)
            rec = made.record
            brief = shell_brief.design_brief(
                rec,
                lay,
                shell_brief.names_of(conn, rec),
                case.brief,
                made.spec.theme_rules,
            )
        except (
            OSError,
            ValueError,
            layout_lib.LayoutError,
            palette.CatalogError,
        ) as exc:
            raise BakeoffError(f"case {case.id} can't be set up: {exc}") from None
        out.append(Prepared(case, ids, rec["max_blocks"], baseline, brief))
    return out


# --- the estimate ----------------------------------------------------------------------------


def _bound(text: str) -> int:
    """The most tokens ``text`` can be: a token is at least one byte."""
    return len(text.encode("utf-8"))


# What a request holds beside the two texts, as a bound in tokens: the schema (both vendors
# hand it to the model as input) and the messages' framing.
OVERHEAD = _bound(json.dumps(shell_brief.SCHEMA)) + 1_000


def _most(system: str, model: ModelSpec, user: str) -> tuple[float, int]:
    """The most one try of a call can cost: (USD, tokens). Its input counted as one token a
    byte plus :data:`OVERHEAD`, the whole output limit, the dearest input rate."""
    tokens_in = _bound(system) + _bound(user) + OVERHEAD
    tokens_out = shell_brief.MAX_OUTPUT_TOKENS
    p = model.price
    if p is None:
        return 0.0, tokens_in + tokens_out
    usd = (tokens_in * max(p.input, p.cache_write) + tokens_out * p.output) / 1_000_000
    return usd, tokens_in + tokens_out


def first_call(
    prepared: Sequence[Prepared], models: Sequence[ModelSpec]
) -> tuple[float, int]:
    """What the caps must at least allow for the run to get an answer from every model: the
    most the first case's design can cost for the dearest priced model (USD) and the most
    tokens it can take for a model without a price. A run with less would stop at once."""
    system = shell_brief.system_prompt()
    usd, tokens = 0.0, 0
    for m in models:
        if m.fake:
            continue
        one_usd, one_tokens = _most(system, m, prepared[0].brief)
        if m.price is None:
            tokens = max(tokens, one_tokens)
        else:
            usd = max(usd, one_usd)
    return usd, tokens


@dataclass(frozen=True)
class Estimate:
    lines: tuple[str, ...]
    worst_usd: float  # of the priced models, capped by nothing
    hosts: tuple[str, ...]
    unpriced: tuple[str, ...]


def estimate(
    prepared: Sequence[Prepared],
    models: Sequence[ModelSpec],
    cap_usd: float,
    cap_tokens: int,
    lowered: str = "",
) -> Estimate:
    """What the run asks, of whom, and what it can cost: cases x models x the token bounds x
    the prices. "Likely" assumes every answer passes first time and uses a quarter of the
    output limit; "worst case" every repair round and the whole limit. ``lowered`` (why the
    cap is less than he asked for) is said just before the cap it explains."""
    system = shell_brief.system_prompt()
    steps = 2 * len(prepared)
    most = steps * (1 + REPAIR_ROUNDS)
    in_typical = sum(
        2 * (len(system) + len(p.brief)) // 3 for p in prepared
    )  # characters / 3: a generous count of tokens
    in_worst = sum(
        2
        * (1 + REPAIR_ROUNDS)
        * (_bound(system) + _bound(p.brief) + shell_brief.MAX_ECHO + OVERHEAD)
        for p in prepared
    )
    out_limit = shell_brief.MAX_OUTPUT_TOKENS
    lines = [
        (
            f"Bake-off: {len(prepared)} cases x {len(models)} models, a design and a follow-up "
            f"each: {steps * len(models)} answers, at most {most * len(models)} calls with the "
            f"repair rounds ({REPAIR_ROUNDS} a step)."
        ),
        (
            f"Each call: at most {out_limit} output tokens (thinking included); the briefs are "
            f"about {in_typical // max(1, steps)} tokens each."
        ),
        (
            f"Designer prompt v{shell_brief.PROMPT_VERSION}: {len(system):,} characters, sent "
            "with every call."
        ),
    ]
    worst = 0.0
    unpriced = []
    for m in models:
        who = f"  {m.label}: {m.provider} {m.model}" + (
            f", effort {m.effort}" if m.effort else ""
        )
        if m.fake:
            lines.append(f"{who}: a fake, free, nothing leaves this PC")
            continue
        if m.price is None:
            unpriced.append(m.label)
            lines.append(
                f"{who}: no price in gos.toml, so its cost is unknown; capped by tokens "
                f"instead (at most {cap_tokens:,} for the run's unpriced models together). "
                "GOS can't count its money, so today's limit and the credit alerts don't "
                "see it: the vendor's bill says what it cost"
            )
            continue
        p = m.price
        likely = (
            in_typical * p.input + steps * out_limit * LIKELY_OUTPUT * p.output
        ) / 1e6
        top = (
            in_worst * max(p.input, p.cache_write) + most * out_limit * p.output
        ) / 1e6
        worst += top
        lines.append(
            f"{who}: ${p.input:.2f} in / ${p.output:.2f} out per million tokens: likely "
            f"about ${likely:.2f}, worst case ${top:.2f}"
        )
    hosts = tuple(dict.fromkeys(HOSTS[m.provider] for m in models if not m.fake))
    if hosts:
        lines.append(
            "Contacts: "
            + ", ".join(hosts)
            + ". Sent: the briefs (the request words, the theme, the palette's block names "
            "and the layout's boxes). No key, file or world leaves this PC otherwise."
        )
        if any(m.price is not None for m in models if not m.fake):
            cap = f"${cap_usd:.2f}" + (
                f" and {cap_tokens:,} tokens for the unpriced models"
                if unpriced
                else ""
            )
        else:  # a dollar cap would bound nothing here: it is never shown as if it did
            cap = f"{cap_tokens:,} tokens (no dollar cap: no model in it has a price)"
        if lowered:
            lines.append(lowered)
        lines.append(
            f"Hard cap for this run: {cap}. It stops before a call that could pass it, "
            "with what it has so far."
        )
    else:
        lines.append("Contacts: nothing. Fake models only: free.")
    return Estimate(tuple(lines), worst, hosts, tuple(unpriced))


# --- the store -------------------------------------------------------------------------------


def run_dir(home: Path, run_id: str) -> Path:
    if not RUN_ID.fullmatch(run_id):
        raise BakeoffError(
            f"{run_id!r} is not a bake-off run id (bo-<date>-<time>-<4 hex>)"
        )
    return home / ROOT / run_id


def entry_home(home: Path, run_id: str, case_id: str, letter: str) -> Path:
    """The GOS_HOME of one blind entry: its builds and design file live under it."""
    if not CASE_ID.fullmatch(case_id) or letter not in LETTERS or len(letter) != 1:
        raise BakeoffError("no such bake-off entry")
    return run_dir(home, run_id) / "entries" / case_id / letter


def _json(data: object) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


# Said when a write is still refused after every try (the routes answer 503 for it).
BUSY = "can't be saved right now: another program has one of its files open"


def _patient(op: Callable[[], object], held: type[OSError] = PermissionError) -> None:
    """``op()``, tried again for a short while when Windows refuses it because another program
    has the file open (the dashboard's look at a run, a virus scanner). A file that is not
    there is not waited for."""
    for attempt in range(SAVE_TRIES):
        try:
            op()
            return
        except FileNotFoundError:
            raise
        except held:
            if attempt == SAVE_TRIES - 1:
                raise
            time.sleep(SAVE_WAIT_S)


def _write(path: Path, data: bytes) -> None:
    _patient(lambda: atomic_write(path, data))


def _stamp(run: Mapping[str, Any]) -> float:
    """When the record was last saved (a run from before the stamp: when it was made)."""
    at = run.get("saved")
    return float(at) if isinstance(at, int | float) else 0.0


def _store(home: Path, run: dict[str, Any]) -> None:
    """Stamp the record and replace ``run.json`` with it (``OSError`` when that can't be)."""
    where = run_dir(home, run["id"])
    run["saved"] = round(max(time.time(), _stamp(run) + 0.001), 3)
    _write(where / RECORD, _json(run))
    try:  # run.json is the newer one again
        (where / BESIDE).unlink(missing_ok=True)
    except OSError:
        pass  # load() takes the newer of the two


def _save(home: Path, run: dict[str, Any]) -> None:
    """:func:`_store` for a command or a click: a file that stays held is said in a sentence,
    and nothing is changed."""
    try:
        _store(home, run)
    except OSError as exc:
        raise BakeoffError(
            f"run {run['id']} {BUSY} ({type(exc).__name__}). Nothing was changed: try again "
            "in a moment"
        ) from None


def load(home: Path, run_id: str) -> dict[str, Any]:
    """The run's record: ``run.json``, or the copy a runner kept beside it while ``run.json``
    could not be replaced, whichever was saved last. Read whole, then parsed, with the game
    files' reader: on Windows it never stands in the way of the runner's next save."""
    where = run_dir(home, run_id)
    best: dict[str, Any] | None = None
    problem = ""
    for name in (BESIDE, RECORD):
        try:
            run = json.loads(read_bytes(where / name).decode("utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, RecursionError) as exc:
            problem = f"run {run_id} can't be read: {exc}"
            continue
        if (
            not isinstance(run, dict)
            or run.get("schema") != SCHEMA
            or run.get("id") != run_id
        ):
            problem = f"run {run_id}'s {name} is not a {SCHEMA} record"
            continue
        if best is None or _stamp(run) >= _stamp(best):
            best = run
    if best is None:
        raise BakeoffError(problem or f"no bake-off run {run_id}")
    return best


def runs(home: Path) -> list[dict[str, Any]]:
    """Every run's blind summary, the newest first."""
    root = home / ROOT
    found = []
    if root.is_dir():
        for d in root.iterdir():
            if RUN_ID.fullmatch(d.name):
                try:
                    found.append(load(home, d.name))
                except BakeoffError:
                    continue
    found.sort(key=lambda r: (-r["created"], r["id"]))
    return [summary(r) for r in found]


def letters(seed: int, case_id: str, labels: Sequence[str]) -> dict[str, str]:
    """The blind lettering of one case: letter -> model label. The same seed, case and models
    give the same lettering."""
    order = sorted(labels)
    random.Random(f"{seed}|{case_id}").shuffle(order)
    return dict(zip(LETTERS, order, strict=False))


def create(
    home: Path,
    prepared: Sequence[Prepared],
    models: Sequence[ModelSpec],
    plans: Mapping[str, Sequence[str]],
    *,
    seed: int | None = None,
    cap_usd: float = DEFAULT_CAP_USD,
    cap_tokens: int = DEFAULT_CAP_TOKENS,
    now: float | None = None,
) -> dict[str, Any]:
    """A new run's record, written before the first call."""
    check_caps(cap_usd, cap_tokens)
    at = time.time() if now is None else now
    seed = random.SystemRandom().randrange(2**32) if seed is None else seed
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(at))
    run_id = f"bo-{stamp}-{random.SystemRandom().randrange(16**4):04x}"
    labels = [m.label for m in models]
    run = {
        "schema": SCHEMA,
        "v": VERSION,
        "id": run_id,
        "created": round(at, 3),
        "finished": None,
        "status": RUNNING,
        "why": "",
        "why_named": "",
        "seed": seed,
        "plans": {k: list(v) for k, v in plans.items()},
        "repair_rounds": REPAIR_ROUNDS,
        "max_output_tokens": shell_brief.MAX_OUTPUT_TOKENS,
        # the designer's instructions this run sent (WO-10.12): a run without it used version 1
        "prompt": prompt_record(),
        "caps": {"usd": cap_usd, "tokens": cap_tokens},
        "spent": {"usd": 0.0, "unpriced_tokens": 0, "calls": 0},
        # calls that broke off after they were sent: no usage reported, maybe billed
        "unmeasured": {"calls": 0, "usd": 0.0, "tokens": 0},
        "models": [m.to_json() for m in models],
        "cases": [
            {
                "id": p.case.id,
                "line": p.case.line,
                "theme": p.case.theme,
                "line_type": p.case.line_type,
                "max_blocks": p.max_blocks,
                "headroom": p.case.headroom,
                "relief": p.case.relief,
                "palette": [list(e) for e in p.case.palette],
                "baseline_blocks": p.baseline,
                "brief": p.case.brief,
                "followup": p.case.followup,
                "plans": list(p.plans),
                "letters": letters(seed, p.case.id, labels),
            }
            for p in prepared
        ],
        "results": [],
        "ratings": {},
        "revealed": None,
        "reveal_unrated": False,
        "report": None,
    }
    _save(home, run)
    return run


# --- running ---------------------------------------------------------------------------------


# Why a run stopped at a cap, as the blind views say it: no model, no amount, not which cap.
CAP_WHY = "the next call could have taken the run past its cap"


# Why a run stopped because a model can't be asked at all (D378): which model and why are the
# report's, never a blind view's.
MODEL_WHY = (
    "a model could not be asked at all (a refused key, a wrong model id or no credit), so "
    "nothing more was spent on the others"
)
FILE_WHY = "an error on this PC stopped it (a file could not be written); what was finished is kept"
CLOSED_WHY = "closed by hand after its window was shut or the PC restarted"


class _Closed(Exception):
    """The run was ended from another window (:func:`close`) while this runner was still at
    it: the runner stops and writes nothing over that record."""

    def __init__(self, run: dict[str, Any]) -> None:
        super().__init__(run["id"])
        self.run = run


class _Stop(Exception):
    """The run ends here. ``why`` is shown while the run is still blind, so it never says which
    model was next or anything that tells the models apart (a price, priced or not); ``named``
    is the same with the model in it, kept for the report."""

    def __init__(self, status: str, why: str, named: str = "") -> None:
        super().__init__(why)
        self.status = status
        self.why = why
        self.named = named


@dataclass
class _Step:
    """One design or one follow-up of one model: what it took and how it ended."""

    calls: int = 0
    rounds: int = 0  # repair rounds used
    seconds: float = 0.0
    usd: float | None = 0.0
    refused: str | None = None  # why there is no design (None once an answer passed)
    refusals: list[str] = field(
        default_factory=list
    )  # every refused answer's reason, in order
    build: str | None = None
    status: str | None = None
    blocks: int | None = None
    types: int | None = None
    tokens: Usage | None = None
    reasoning: int = 0

    def add(self, usage: Usage, usd: float | None, reasoning: int) -> None:
        if self.tokens is None:
            self.tokens = Usage()
        self.tokens.add(usage)
        self.reasoning += reasoning
        self.usd = None if usd is None or self.usd is None else self.usd + usd

    def refuse(self, why: str) -> None:
        """An answer (or the lack of one) was refused: kept per round, as plain text."""
        self.refused = plain(why)
        self.refusals.append(self.refused)

    def to_json(self) -> dict[str, Any]:
        t = self.tokens or Usage()
        return {
            "ok": self.build is not None,
            "first_try": self.build is not None and self.rounds == 0,
            "repair_rounds": self.rounds,
            "refused": self.refused,
            "refusals": list(self.refusals),
            "build": self.build,
            "status": self.status,
            "blocks": self.blocks,
            "types": self.types,
            "calls": self.calls,
            "seconds": round(self.seconds, 3),
            "tokens": {
                "input": t.input,
                "output": t.output,
                "cache_read": t.cache_read,
                "cache_write": t.cache_write,
                "reasoning": self.reasoning,
            },
            "usd": None if self.usd is None else round(self.usd, 6),
        }


Say = Callable[[str], None]


@dataclass
class _Capped:
    """A model's designer inside the run's caps: every try (:func:`gregtec.llm.designers.call`
    retries) passes the runner's guard first, and a try that broke off is counted."""

    runner: Runner
    spec: ModelSpec
    inner: llm.Designer

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    def complete(
        self, system: str, user: str, schema: Mapping[str, Any], max_output_tokens: int
    ) -> llm.Reply:
        self.runner._guard(self.spec, user)
        try:
            reply = self.inner.complete(system, user, schema, max_output_tokens)
        except llm.ProviderError as exc:
            if exc.maybe_billed:
                self.runner._broke(self.spec, user)
            raise
        if not reply.measured:  # answered, but the vendor sent no token count with it
            self.runner._broke(self.spec, user)
        return reply


@dataclass
class Runner:
    """One run's work. ``designers`` maps a model label to its :class:`~gregtec.llm.designers.
    Designer`; ``meter`` (the live ``llm_usage``) records every real call; ``cancel`` is asked
    before every call."""

    conn: sqlite3.Connection
    home: Path
    vault: VaultIO | None
    run: dict[str, Any]
    designers: Mapping[str, llm.Designer]
    meter: UsageMeter | None = None
    say: Say = lambda _text: None
    cancel: Callable[[], bool] = lambda: False
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.perf_counter

    def __post_init__(self) -> None:
        self.models = {m["label"]: ModelSpec.from_json(m) for m in self.run["models"]}
        self.system = shell_brief.system_prompt()
        self.beside = False  # said once: the record is being kept beside run.json

    # -- the record ---------------------------------------------------------------------------

    def _keep(self) -> None:
        """Save the record (and with it the sign that the run is alive). A run that was closed
        from another window is never written over; when ``run.json`` can't be replaced (held
        open by another program) the record goes beside it, where :func:`load` finds it."""
        try:
            disk = load(self.home, self.run["id"])
        except BakeoffError:
            disk = None  # not readable right now: this runner's record is the whole one
        if disk is not None and disk["status"] != RUNNING:
            raise _Closed(disk)
        try:
            _store(self.home, self.run)
        except OSError:
            where = run_dir(self.home, self.run["id"])
            _write(where / BESIDE, _json(self.run))
            if not self.beside:
                self.beside = True
                self.say(
                    f"{RECORD} is held open by another program: the run's record is kept "
                    f"beside it ({BESIDE}) and read from there. Nothing is lost."
                )

    # -- one call, inside the caps ------------------------------------------------------------

    def _most(self, model: ModelSpec, user: str) -> tuple[float, int]:
        return _most(self.system, model, user)

    def _guard(self, model: ModelSpec, user: str) -> None:
        """Asked before every try, a retry too: Cancel, the record (saved, so the run is seen
        to be alive, and a run closed from another window stops here), then the caps. What is
        counted is what was spent plus the most the broken-off tries can have cost."""
        if self.cancel():
            raise _Stop(CANCELLED, "cancelled")
        self._keep()
        if model.fake:
            return
        usd, tokens = self._most(model, user)
        spent, caps, lost = self.run["spent"], self.run["caps"], self.run["unmeasured"]
        if model.price is None:
            used = spent["unpriced_tokens"] + lost["tokens"]
            if used + tokens > caps["tokens"]:
                raise _Stop(
                    CAP,
                    CAP_WHY,
                    f"the next call ({model.label}) could take the unpriced models past "
                    f"{caps['tokens']:,} tokens ({used:,} used)",
                )
            return
        used_usd = spent["usd"] + lost["usd"]
        if used_usd + usd > caps["usd"]:
            raise _Stop(
                CAP,
                CAP_WHY,
                f"the next call ({model.label}, up to ${usd:.2f}) could take the run past "
                f"${caps['usd']:.2f} (${used_usd:.2f} spent)",
            )

    def _broke(self, model: ModelSpec, user: str) -> None:
        """A try that broke off after it was sent, or whose answer came without a token count:
        nothing came back to count, so the most it can have cost is held against the cap."""
        if model.fake:
            return
        usd, tokens = self._most(model, user)
        lost = self.run["unmeasured"]
        lost["calls"] += 1
        if model.price is None:
            lost["tokens"] += tokens
        else:
            lost["usd"] = round(lost["usd"] + usd, 6)

    def _ask(self, model: ModelSpec, user: str, step: _Step) -> llm.Reply | str:
        """One answer (inside the caps, metered), or why there is none."""
        started = self.clock()
        try:
            reply = llm.call(
                _Capped(self, model, self.designers[model.label]),
                self.system,
                user,
                shell_brief.SCHEMA,
                shell_brief.MAX_OUTPUT_TOKENS,
                sleep=self.sleep,
            )
        except llm.ProviderError as exc:
            step.calls += 1
            step.seconds += self.clock() - started
            if exc.kind in llm.STOPS_MODEL:
                # It can't succeed on another try either, and every further request would be
                # paid for the other models with nothing to compare them with (D378).
                raise _Stop(
                    STOPPED,
                    MODEL_WHY,
                    f"{model.label} ({model.provider} {model.model}) can't be asked: "
                    f"{plain(str(exc))}",
                ) from None
            return f"no answer: {exc}"
        step.calls += 1
        step.seconds += self.clock() - started
        usd = cost(model, reply.usage)
        step.add(reply.usage, usd, reply.reasoning_tokens)
        spent = self.run["spent"]
        spent["calls"] += 1
        if not model.fake:
            if usd is None:
                spent["unpriced_tokens"] += (
                    reply.usage.input
                    + reply.usage.output
                    + reply.usage.cache_read
                    + reply.usage.cache_write
                )
            else:
                spent["usd"] = round(spent["usd"] + usd, 6)
            if self.meter is not None:
                self.meter.record(
                    LANE,
                    model.model,
                    reply.usage,
                    usd=0.0 if usd is None else usd,
                    task=f"bakeoff:{self.run['id']}"
                    + (":unpriced" if usd is None else ""),
                )
        return reply

    def _rounds(
        self,
        model: ModelSpec,
        brief: str,
        gate: Callable[[list[dict[str, Any]]], str | None],
        step: _Step,
    ) -> None:
        """The first answer and at most REPAIR_ROUNDS more, each through ``gate`` (which saves
        an accepted design and returns None, or says why it refused)."""
        user = brief
        for attempt in range(REPAIR_ROUNDS + 1):
            step.rounds = attempt
            got = self._ask(model, user, step)
            if isinstance(got, str):
                step.refuse(got)
                return
            if got.stop == llm.REFUSAL:
                step.refuse(got.detail or "the model declined")
                return
            if got.stop == llm.MAX_TOKENS:
                why = (
                    f"the answer was cut off at the output limit "
                    f"({shell_brief.MAX_OUTPUT_TOKENS} tokens): use fewer operations"
                )
            else:
                try:
                    ops = shell_brief.parse(got.text)
                except shell_brief.BriefError as exc:
                    why = str(exc)
                else:
                    refused = gate(ops)
                    if refused is None:
                        return
                    why = refused
            step.refuse(why)
            user = shell_brief.repair_brief(brief, got.text, why)

    # -- the two steps ------------------------------------------------------------------------

    def _design(
        self, case: Mapping[str, Any], model: ModelSpec, brief: str, where: Path
    ) -> tuple[_Step, str | None]:
        step = _Step()
        design: list[str] = []

        def gate(ops: list[dict[str, Any]]) -> str | None:
            try:
                made = shell_store.make(
                    self.conn,
                    where,
                    self.vault,
                    case["plans"],
                    theme_id=None if case["theme"] == "none" else case["theme"],
                    prompt=case["brief"],
                    line_type=case["line_type"],
                    max_blocks=case["max_blocks"],
                    headroom=case.get("headroom"),
                    relief=case.get("relief"),
                    extra=[tuple(e) for e in case.get("palette") or ()],
                    ops=ops,
                )
            except (shell.ShellError, palette.CatalogError) as exc:
                return f"no shell: {exc}"
            if not made.outcome.ok:
                return " | ".join(made.outcome.reasons)
            d = versions.start(
                where,
                made.build,
                asked=case["brief"] or "designed",
                by="architect",
            )
            rec = made.record
            step.build, step.status = made.build, made.outcome.status
            step.blocks, step.types, step.refused = rec["blocks"], rec["types"], None
            design.append(d["id"])
            return None

        self._rounds(model, brief, gate, step)
        return step, (design[0] if design else None)

    def _revise(
        self,
        case: Mapping[str, Any],
        spec: Case,
        model: ModelSpec,
        where: Path,
        ds: str,
    ) -> tuple[_Step, dict[str, str]]:
        step = _Step()
        first = versions.get(where, ds)["current"]
        old = shell_store.load(where, first)
        lay = versions._layout_of(where, first)
        brief = shell_brief.revise_brief(
            old, lay, shell_brief.names_of(self.conn, old), case["followup"]
        )
        checks = {"asked": "-", "kept": "-", "why": ""}

        def gate(ops: list[dict[str, Any]]) -> str | None:
            try:
                got = versions.revise(
                    self.conn,
                    where,
                    self.vault,
                    ds,
                    case["followup"],
                    ops=ops,
                    by="architect",
                    asked=case["followup"],
                )
            except (shell.ShellError, palette.CatalogError) as exc:
                return f"no revision: {exc}"
            if got.reasons or got.record is None:
                return " | ".join(got.reasons)
            rec = got.record
            step.build, step.status = got.build, got.outcome.status
            step.blocks, step.types, step.refused = rec["blocks"], rec["types"], None
            misses = [
                w for w in (shell_eval.check(old, rec, x) for x in spec.expect) if w
            ]
            left = shell_eval.kept(old, rec, spec.touches)
            checks["asked"] = "pass" if not misses else "fail"
            checks["kept"] = "pass" if left is None else "fail"
            checks["why"] = "; ".join([*misses, *([left] if left else [])])
            return None

        self._rounds(model, brief, gate, step)
        return step, checks

    def _copy_plans(self, case: Mapping[str, Any], where: Path) -> None:
        for pid in case["plans"]:
            src = self.home / plan_store.json_rel(pid)
            dst = where / plan_store.json_rel(pid)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

    def _one(
        self, case: Mapping[str, Any], spec: Prepared, label: str, letter: str
    ) -> None:
        model = self.models[label]
        where = entry_home(self.home, self.run["id"], case["id"], letter)
        result: dict[str, Any] = {
            "case": case["id"],
            "model": label,
            "letter": letter,
            "design": None,
            "revise": None,
            "checks": None,
            "design_id": None,
        }
        self._copy_plans(case, where)
        try:
            step, ds = self._design(case, model, spec.brief, where)
        except BaseException:
            shutil.rmtree(where, ignore_errors=True)  # nothing half-written stays
            if where.parent.is_dir() and not any(where.parent.iterdir()):
                where.parent.rmdir()
            raise
        result["design"], result["design_id"] = step.to_json(), ds
        self.run["results"].append(result)
        self._keep()
        self.say(_line(case["id"], letter, "design", result["design"]))
        if ds is None:
            return
        rstep, checks = self._revise(case, spec.case, model, where, ds)
        result["revise"], result["checks"] = rstep.to_json(), checks
        self._keep()
        self.say(_line(case["id"], letter, "follow-up", result["revise"]))

    def execute(self, prepared: Sequence[Prepared]) -> dict[str, Any]:
        """Every case x model, in the run's order. Stops cleanly at a cap, on ``cancel`` or
        Ctrl+C, when a model can't be asked at all (D378) and when a file can't be written:
        what is finished stays, the step in hand is dropped, the run says why. It never ends
        as ``running``: a record that can't replace ``run.json`` is kept beside it."""
        by_id = {p.case.id: p for p in prepared}
        status, why, named = DONE, "", ""
        try:
            for case in self.run["cases"]:
                for letter, label in sorted(case["letters"].items()):
                    self._one(case, by_id[case["id"]], label, letter)
        except _Closed as closed:
            return self._gone(closed)
        except _Stop as stop:
            status, why, named = stop.status, stop.why, stop.named
        except KeyboardInterrupt:
            status, why = CANCELLED, "cancelled (Ctrl+C)"
        except OSError as exc:
            status, why = STOPPED, FILE_WHY
            named = f"{type(exc).__name__}: {plain(str(exc))}"
            self.say(f"Stopped: {named}")
        self.run["status"], self.run["why"] = status, why
        self.run["why_named"] = named  # the report's: never in a blind view
        self.run["finished"] = round(time.time(), 3)
        try:
            self._keep()
        except _Closed as closed:
            return self._gone(closed)
        except OSError as exc:
            self.say(
                f"The run's record could not be saved ({type(exc).__name__}): what was saved "
                f"before is in {run_dir(self.home, self.run['id'])}. Once this window is "
                f"closed and {STALE_S / 60:.0f} minutes have passed, `gos shell bakeoff close "
                f"{self.run['id']}` makes that rateable."
            )
        return self.run

    def _gone(self, closed: _Closed) -> dict[str, Any]:
        self.say(
            f"Run {closed.run['id']} was closed from another window (gos shell bakeoff "
            "close): stopped here, and nothing of it was written over."
        )
        self.run = closed.run
        return self.run


def _line(case: str, letter: str, what: str, step: Mapping[str, Any]) -> str:
    if step["ok"]:
        how = (
            "first try"
            if step["first_try"]
            else f"after {step['repair_rounds']} repair round(s)"
        )
        return f"{case} {letter} {what}: {step['blocks']} blocks, {how}"
    return f"{case} {letter} {what}: no design"


# --- the blind view --------------------------------------------------------------------------


def _result(run: Mapping[str, Any], case_id: str, letter: str) -> dict[str, Any] | None:
    for r in run["results"]:
        if r["case"] == case_id and r["letter"] == letter:
            found: dict[str, Any] = r
            return found
    return None


def _shown(step: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not step or not step.get("ok"):
        return None
    return {
        "build": step["build"],
        "blocks": step["blocks"],
        "types": step["types"],
        "status": step["status"],
    }


def rateable(run: Mapping[str, Any]) -> list[str]:
    """The cases with something to look at: a design from at least one model."""
    return [
        c["id"]
        for c in run["cases"]
        if any(
            (r := _result(run, c["id"], letter)) and r["design"] and r["design"]["ok"]
            for letter in c["letters"]
        )
    ]


def unrated(run: Mapping[str, Any]) -> list[str]:
    return [c for c in rateable(run) if c not in run["ratings"]]


def summary(run: Mapping[str, Any]) -> dict[str, Any]:
    can = rateable(run)
    return {
        "id": run["id"],
        "created": run["created"],
        "status": run["status"],
        "why": run["why"],
        "cases": len(run["cases"]),
        "models": len(run["models"]),
        "rateable": len(can),
        "rated": len([c for c in can if c in run["ratings"]]),
        "revealed": run["revealed"] is not None,
    }


def prompt_record() -> dict[str, Any]:
    """Which designer instructions a run sends: their version and a hash of the system text
    (so a change that forgot to bump the version still shows)."""
    text = shell_brief.system_prompt()
    return {
        "version": shell_brief.PROMPT_VERSION,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "chars": len(text),
    }


def prompt_label(run: Mapping[str, Any]) -> str:
    """``v2 (sha 1a2b3c4d5e6f)``; a run from before the version was recorded is v1."""
    p = run.get("prompt")
    if not isinstance(p, Mapping):
        return "v1 (recorded before WO-10.12: the op vocabulary only)"
    return f"v{p.get('version')} (sha {p.get('sha256')})"


def blind(run: Mapping[str, Any]) -> dict[str, Any]:
    """The run as the rater may see it: letters, never a model's label, id, provider, cost,
    tokens or time. Built from a fixed list of fields, so a new field in ``run.json`` never
    shows by accident."""
    cases = []
    for c in run["cases"]:
        entries = []
        for letter in sorted(c["letters"]):
            r = _result(run, c["id"], letter)
            entries.append(
                {
                    "letter": letter,
                    "asked": r is not None,
                    "design": _shown(r["design"]) if r else None,
                    "revise": _shown(r["revise"]) if r else None,
                }
            )
        rating = run["ratings"].get(c["id"])
        cases.append(
            {
                "id": c["id"],
                "line": c["line"],
                "theme": c["theme"],
                "max_blocks": c["max_blocks"],
                "brief": c["brief"],
                "followup": c["followup"],
                "entries": entries,
                "pick": rating["pick"] if rating else None,
            }
        )
    return {**summary(run), "cases": cases}


def _entry_build(run: Mapping[str, Any], case_id: str, letter: str, which: str) -> str:
    if which not in ("design", "revise"):
        raise BakeoffError("which is design or revise")
    r = _result(run, case_id, letter)
    step = r[which] if r else None
    if not step or not step.get("ok"):
        raise BakeoffError(f"no shell build for {case_id} {letter} ({which})")
    return str(step["build"])


def scene(
    conn: sqlite3.Connection | None,
    home: Path,
    run_id: str,
    case_id: str,
    letter: str,
    which: str,
) -> dict[str, Any]:
    """The 3D viewer's data of one blind entry (:func:`gregtec.build.shell_versions.viewer` on
    the entry's own home)."""
    run = load(home, run_id)
    build = _entry_build(run, case_id, letter, which)
    return versions.viewer(conn, entry_home(home, run_id, case_id, letter), build)


def compare(
    home: Path, run_id: str, case_id: str, a: str, b: str, which: str
) -> dict[str, Any]:
    """Two blind entries of one case side by side: blocks and materials. Not the operations:
    an operation can carry text the gate never reads (``parapet_role`` on a roof without a
    parapet), which a model could sign its answer with, so no blind view shows them."""
    run = load(home, run_id)
    recs = [
        shell_store.load(
            entry_home(home, run_id, case_id, letter),
            _entry_build(run, case_id, letter, which),
        )
        for letter in (a, b)
    ]
    return {**versions.compare_records(recs[0], recs[1]), "ops": []}


# --- rating ----------------------------------------------------------------------------------


def rate(home: Path, run_id: str, case_id: str, pick: str) -> dict[str, Any]:
    """Clayton's pick for one case: the better entry's letter, ``tie`` or ``bad`` (none is
    good). Refused once the models are revealed, and for a letter that has no design."""
    run = load(home, run_id)
    if run["revealed"] is not None:
        raise BakeoffError(
            f"run {run_id}'s models are revealed (its report is written): ratings are closed"
        )
    if run["status"] == RUNNING:
        raise BakeoffError(f"run {run_id} is still running")
    if case_id not in rateable(run):
        raise BakeoffError(
            f"run {run_id} has no case {case_id!r} with a design to rate"
        )
    case = next(c for c in run["cases"] if c["id"] == case_id)
    good = [
        letter
        for letter in sorted(case["letters"])
        if (r := _result(run, case_id, letter)) and r["design"]["ok"]
    ]
    if pick not in (*good, TIE, BAD):
        raise BakeoffError(
            f"pick one of {', '.join(good)}, {TIE} or {BAD} (none is good) for {case_id}"
        )
    run["ratings"][case_id] = {"pick": pick, "at": round(time.time(), 3)}
    _save(home, run)
    return run


def close(home: Path, run_id: str, *, now: float | None = None) -> dict[str, Any]:
    """End a run that still says ``running`` although nothing is running it (its window was
    shut, the PC restarted): cancelled, with every finished answer kept, so it can be rated.
    Refused while the run was saved less than :data:`STALE_S` ago: a live runner saves before
    every call, so that one may still be going in another window (and a runner that does find
    its run closed stops without writing over it)."""
    run = load(home, run_id)
    if run["status"] != RUNNING:
        raise BakeoffError(
            f"run {run_id} is not running (it is {run['status']}): there is nothing to close"
        )
    at = time.time() if now is None else now
    quiet = at - (_stamp(run) or float(run["created"]))
    if quiet < STALE_S:
        raise BakeoffError(
            f"run {run_id} was last heard from {max(0, int(quiet // 60))} minute(s) ago: it "
            "may be going in another window. Stop it there with Ctrl+C, or run this again "
            f"once it has been silent for {STALE_S / 60:.0f} minutes"
        )
    run["status"], run["why"] = CANCELLED, CLOSED_WHY
    run["finished"] = round(at, 3)
    _save(home, run)
    return run


# --- the report ------------------------------------------------------------------------------

ORDER = (
    "most cases picked, then most steps through the gate on the first try, then fewest repair "
    "rounds on average, then lowest cost (unknown last), then the label"
)
FEW = (
    "With a handful of cases this is a first look, not proof: one model ahead by one or two "
    "picks can be chance. The gate columns say how often the code had to refuse or repair an "
    "answer; they say nothing about looks, and your picks say nothing about cost."
)
COLS = (
    "model",
    "provider",
    "id",
    "effort",
    "picked",
    "first try",
    "passed",
    "repair rounds",
    "blocks",
    "asked",
    "kept",
    "calls",
    "tokens in/out",
    "cost",
    "seconds",
)


def _mean(values: Sequence[float]) -> str:
    return f"{sum(values) / len(values):.1f}" if values else "-"


def _stats(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    letter_of = {
        (c["id"], label): letter
        for c in run["cases"]
        for letter, label in c["letters"].items()
    }
    rows = []
    for m in run["models"]:
        label = m["label"]
        mine = [r for r in run["results"] if r["model"] == label]
        steps = [
            s for r in mine for s in (r["design"], r["revise"]) if s and s["calls"] > 0
        ]
        picked = ties = bad = 0
        for case_id, rating in run["ratings"].items():
            r = next((x for x in mine if x["case"] == case_id), None)
            if r is None or not r["design"]["ok"]:
                continue
            if rating["pick"] == letter_of[(case_id, label)]:
                picked += 1
            elif rating["pick"] == TIE:
                ties += 1
            elif rating["pick"] == BAD:
                bad += 1
        usd: float | None = 0.0
        for s in steps:
            usd = None if usd is None or s["usd"] is None else usd + s["usd"]
        checks = [r["checks"] for r in mine if r["checks"]]
        rows.append(
            {
                "label": label,
                "provider": m["provider"],
                "model": m["model"],
                "effort": m["effort"] or "default",
                "picked": picked,
                "ties": ties,
                "bad": bad,
                "steps": len(steps),
                "first": sum(1 for s in steps if s["first_try"]),
                "passed": sum(1 for s in steps if s["ok"]),
                "rounds": [float(s["repair_rounds"]) for s in steps],
                "blocks": [
                    float(r["design"]["blocks"]) for r in mine if r["design"]["ok"]
                ],
                "asked": sum(1 for c in checks if c["asked"] == "pass"),
                "kept": sum(1 for c in checks if c["kept"] == "pass"),
                "followups": sum(1 for r in mine if r["revise"] and r["revise"]["ok"]),
                "calls": sum(s["calls"] for s in steps),
                "in": sum(
                    s["tokens"]["input"]
                    + s["tokens"]["cache_read"]
                    + s["tokens"]["cache_write"]
                    for s in steps
                ),
                "out": sum(s["tokens"]["output"] for s in steps),
                "usd": usd,
                "fake": m["provider"] == shell_fakes.PROVIDER,
                "seconds": sum(s["seconds"] for s in steps),
            }
        )
    rows.sort(
        key=lambda s: (
            -s["picked"],
            -s["first"],
            sum(s["rounds"]) / len(s["rounds"]) if s["rounds"] else float("inf"),
            s["usd"] is None,
            s["usd"] or 0.0,
            s["label"],
        )
    )
    return rows


def _cost_text(s: Mapping[str, Any]) -> str:
    if s["fake"]:
        return "free (fake)"
    return "unknown (no price)" if s["usd"] is None else f"${s['usd']:.2f}"


def _table(stats: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    return [
        [
            s["label"],
            s["provider"],
            s["model"],
            s["effort"],
            f"{s['picked']}" + (f" (+{s['ties']} tie)" if s["ties"] else ""),
            f"{s['first']} of {s['steps']}",
            f"{s['passed']} of {s['steps']}",
            _mean(s["rounds"]),
            _mean(s["blocks"]),
            f"{s['asked']} of {s['followups']}",
            f"{s['kept']} of {s['followups']}",
            str(s["calls"]),
            f"{s['in']:,} / {s['out']:,}",
            _cost_text(s),
            f"{s['seconds']:.0f}",
        ]
        for s in stats
    ]


@dataclass(frozen=True)
class Report:
    text: str
    card: dict[str, Any]
    path: Path


def report(home: Path, run_id: str, *, reveal_unrated: bool = False) -> Report:
    """The run's report with the models named: written to ``GOS_HOME/data/reports/
    shell-bakeoff-<run>.md`` (and ``.json``, its card). Refused while a rateable case is unrated,
    unless ``reveal_unrated``: revealing closes the ratings, so that is Clayton's own choice."""
    from gregtec.cards import checked

    run = load(home, run_id)
    if run["status"] == RUNNING:
        raise BakeoffError(f"run {run_id} is still running")
    left = unrated(run)
    if left and run["revealed"] is None and not reveal_unrated:
        raise BakeoffError(
            f"{len(left)} case(s) are not rated yet ({', '.join(left)}): rate them first (gos "
            f"shell bakeoff rate {run_id}), or give up rating blind with --reveal"
        )
    if run["revealed"] is None:
        run["revealed"] = round(time.time(), 3)
        run["reveal_unrated"] = bool(left)
    stats = _stats(run)
    rows = _table(stats)
    notes = [f"Order: {ORDER}.", FEW]
    if run["status"] != DONE:
        notes.append(
            f"The run did not finish ({run['status']}: "
            f"{plain(str(run.get('why_named') or run['why']))}): a partial result."
        )
    if run["reveal_unrated"]:
        notes.append("The models were revealed before every case was rated.")
    spent = run["spent"]
    head = [
        ("run", run["id"]),
        ("cases", str(len(run["cases"]))),
        ("rated", f"{len(run['ratings'])} of {len(rateable(run))}"),
        ("repair rounds allowed", str(run["repair_rounds"])),
        (
            "spent",
            f"${spent['usd']:.2f}"
            + (
                f" and {spent['unpriced_tokens']:,} unpriced tokens"
                if spent["unpriced_tokens"]
                else ""
            ),
        ),
        ("seed", str(run["seed"])),
        ("designer prompt", prompt_label(run)),
    ]
    if broke := broken_off(run):
        notes.append(broke)
    card = checked(
        {
            "type": "table",
            "tool": "shell_bakeoff",
            "error": None,
            "title": f"Shell bake-off {run['id']}: the models, named",
            "head": [{"key": k, "value": v} for k, v in head],
            "columns": list(COLS),
            "rows": rows[:50],
            "span": None,
            "total": len(rows),
            "notes": notes,
        }
    )
    lines = [f"# Shell bake-off {run['id']}", ""]
    lines += [f"- {k}: {v}" for k, v in head]
    lines += ["", "| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    lines += ["", *[f"- {n}" for n in notes], "", "## Per case", ""]
    lines += [
        "| case | your pick | letter | model | design | follow-up | asked | kept | why refused |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in run["cases"]:
        pick = (run["ratings"].get(c["id"]) or {}).get("pick", "-")
        for letter, label in sorted(c["letters"].items()):
            r = _result(run, c["id"], letter)
            if r is None:
                lines.append(
                    f"| {c['id']} | {pick} | {letter} | {label} | not asked | | | | |"
                )
                continue
            d, v, k = r["design"], r["revise"], r["checks"] or {}
            why = " / ".join([*_refusals("design", d), *_refusals("follow-up", v)])
            lines.append(
                f"| {c['id']} | {pick} | {letter} | {label} | {_step_text(d)} | "
                f"{_step_text(v)} | {k.get('asked', '-')} | {k.get('kept', '-')} | {why} |"
            )
    text = "\n".join(lines) + "\n"
    path = home / REPORTS / f"shell-bakeoff-{run['id']}.md"
    try:
        _write(path, text.encode("utf-8"))
        _write(path.with_suffix(".json"), _json(card))
    except OSError as exc:
        raise BakeoffError(
            f"the report {BUSY} ({type(exc).__name__}). Run it again in a moment"
        ) from None
    run["report"] = (REPORTS / path.name).as_posix()
    _save(home, run)
    return Report(text, card, path)


def _refusals(what: str, step: Mapping[str, Any] | None) -> list[str]:
    """Why the gate (or the vendor) refused each answer of a step, try by try, for the
    report's table: an answer that was repaired later still shows why its first try failed.
    The words may be a model's: one line, no control characters, no table bar."""
    if not step:
        return []
    said = step.get("refusals") or ([step["refused"]] if step.get("refused") else [])
    return [
        f"{what} try {n}: {plain(str(r), 200).replace('|', ';')}"
        for n, r in enumerate(said, 1)
    ]


def broken_off(run: Mapping[str, Any]) -> str:
    """The run's calls that broke off after they were sent, in words ("" when none)."""
    lost = run.get("unmeasured") or {}
    if not lost.get("calls"):
        return ""
    most = " and ".join(
        x
        for x in (
            f"${lost['usd']:.2f}" if lost["usd"] else "",
            f"{lost['tokens']:,} tokens" if lost["tokens"] else "",
        )
        if x
    )
    return (
        f"{lost['calls']} call(s) broke off after they were sent (a timeout or a cut-off "
        "answer) or came back without their token count. The vendor may still bill them, at "
        f"most {most} in all: that is not in the cost column or the usage meter, but it was "
        "held against the run's cap."
    )


def _step_text(step: Mapping[str, Any] | None) -> str:
    if not step:
        return "-"
    if not step["ok"]:
        return "no design" if step["calls"] else "not asked"
    return f"{step['blocks']} blocks, {step['repair_rounds']} repair round(s)"


def card_of(home: Path, run: Mapping[str, Any]) -> dict[str, Any] | None:
    """The written report's card (None before the models are revealed)."""
    if run["revealed"] is None or not run.get("report"):
        return None
    try:
        card: dict[str, Any] = json.loads(
            read_bytes((home / run["report"]).with_suffix(".json")).decode("utf-8")
        )
    except (OSError, ValueError):
        return None
    return card


# --- deleting a run --------------------------------------------------------------------------


def removal(home: Path, run_id: str) -> list[str]:
    """What :func:`delete` removes, in words, for Clayton to read before he says yes."""
    run = load(home, run_id)
    where = run_dir(home, run_id)
    builds = sum(
        1 for r in run["results"] for s in (r["design"], r["revise"]) if s and s["ok"]
    )
    out = [
        (
            f"the folder {where} (run {run_id}: {len(run['cases'])} cases, "
            f"{len(run['models'])} models, {builds} shell builds, its ratings)"
        ),
    ]
    for p in _report_files(home, run_id):
        out.append(f"the report file {p}")
    out.append(
        "Nothing else: your own shells, designs, plans and the llm_usage records stay."
    )
    return out


def _report_files(home: Path, run_id: str) -> list[Path]:
    base = home / REPORTS / f"shell-bakeoff-{run_id}"
    return [
        p for p in (base.with_suffix(".md"), base.with_suffix(".json")) if p.is_file()
    ]


def _removed(op: Callable[[], object]) -> None:
    try:
        _patient(op, OSError)
    except FileNotFoundError:
        pass


def delete(home: Path, run_id: str) -> None:
    """Remove the run. Its buildings and reports go first and ``run.json`` last, so a delete
    that could not finish (Windows: the dashboard was reading one of the files) can simply be
    run again."""
    where = run_dir(home, run_id)
    load(home, run_id)  # only a real run's folder is ever removed
    try:
        _removed(lambda: shutil.rmtree(where / "entries"))
        for p in _report_files(home, run_id):
            _removed(p.unlink)
        _removed(lambda: shutil.rmtree(where))
    except OSError as exc:
        raise BakeoffError(
            f"run {run_id} is not all removed yet: another program has one of its files "
            f"open ({type(exc).__name__}). Run the same command again in a moment"
        ) from None
