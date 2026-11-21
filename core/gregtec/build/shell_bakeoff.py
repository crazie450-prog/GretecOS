"""The shell designer bake-off: the same briefs to several models, through the same gate, rated
blind (WO-10.11, plan 7.10 "Shell designer", D374-D377).

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
the report says how many there were.

Nothing here writes a world or a ``.schematic``.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
import shutil
import sqlite3
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
# The share of the output limit a first look assumes an answer uses (the estimate's "likely").
LIKELY_OUTPUT = 0.25


class BakeoffError(shell.ShellError):
    """A bake-off request that can't be done (said as is; nothing was written or spent)."""


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
        or cap_usd <= 0
    ):
        raise ConfigError("gos.toml: [bakeoff] cap_usd must be a number of USD above 0")
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
        if effort is not None and effort not in llm.EFFORTS:
            raise ConfigError(
                f"gos.toml: {where} effort must be one of {', '.join(llm.EFFORTS)}"
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


def load_cases(path: Path = CASES) -> list[Case]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    out = []
    for n, r in enumerate(rows, 2):
        where = f"{path.name} line {n}"
        if tuple(r) != COLUMNS:
            raise BakeoffError(f"{path.name}: the columns are {' '.join(COLUMNS)}")
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
            if not shell_eval._CHECK.match(x):
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
        theme_id=case.theme,
        prompt=case.brief,
        line_type=case.line_type,
        max_blocks=max_blocks,
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
) -> Estimate:
    """What the run asks, of whom, and what it can cost: cases x models x the token bounds x
    the prices. "Likely" assumes every answer passes first time and uses a quarter of the
    output limit; "worst case" every repair round and the whole limit."""
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
                f"instead (at most {cap_tokens:,} for the run's unpriced models together)"
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
        lines.append(
            f"Hard cap for this run: ${cap_usd:.2f}"
            + (
                f" and {cap_tokens:,} tokens for the unpriced models"
                if unpriced
                else ""
            )
            + ". It stops before a call that could pass it, with what it has so far."
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


def _save(home: Path, run: Mapping[str, Any]) -> None:
    atomic_write(run_dir(home, run["id"]) / "run.json", _json(run))


def load(home: Path, run_id: str) -> dict[str, Any]:
    path = run_dir(home, run_id) / "run.json"
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BakeoffError(f"no bake-off run {run_id}") from None
    except (OSError, ValueError) as exc:
        raise BakeoffError(f"run {run_id} can't be read: {exc}") from None
    if (
        not isinstance(run, dict)
        or run.get("schema") != SCHEMA
        or run.get("id") != run_id
    ):
        raise BakeoffError(f"run {run_id}'s run.json is not a {SCHEMA} record")
    return run


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
        "seed": seed,
        "plans": {k: list(v) for k, v in plans.items()},
        "repair_rounds": REPAIR_ROUNDS,
        "max_output_tokens": shell_brief.MAX_OUTPUT_TOKENS,
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


class _Stop(Exception):
    def __init__(self, status: str, why: str) -> None:
        super().__init__(why)
        self.status = status
        self.why = why


@dataclass
class _Step:
    """One design or one follow-up of one model: what it took and how it ended."""

    calls: int = 0
    rounds: int = 0  # repair rounds used
    seconds: float = 0.0
    usd: float | None = 0.0
    refused: str | None = None
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

    def to_json(self) -> dict[str, Any]:
        t = self.tokens or Usage()
        return {
            "ok": self.build is not None,
            "first_try": self.build is not None and self.rounds == 0,
            "repair_rounds": self.rounds,
            "refused": self.refused,
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
            return self.inner.complete(system, user, schema, max_output_tokens)
        except llm.ProviderError as exc:
            if exc.maybe_billed:
                self.runner._broke(self.spec, user)
            raise


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
        self.dead: dict[str, str] = {}

    # -- one call, inside the caps ------------------------------------------------------------

    def _most(self, model: ModelSpec, user: str) -> tuple[float, int]:
        """The most one try of this call can cost: (USD, tokens). Its input counted as one
        token a byte plus :data:`OVERHEAD`, the whole output limit, the dearest input rate."""
        tokens_in = _bound(self.system) + _bound(user) + OVERHEAD
        tokens_out = shell_brief.MAX_OUTPUT_TOKENS
        p = model.price
        if p is None:
            return 0.0, tokens_in + tokens_out
        usd = (
            tokens_in * max(p.input, p.cache_write) + tokens_out * p.output
        ) / 1_000_000
        return usd, tokens_in + tokens_out

    def _guard(self, model: ModelSpec, user: str) -> None:
        """Asked before every try, a retry too: Cancel, then the caps. What is counted is what
        was spent plus the most the broken-off tries can have cost."""
        if self.cancel():
            raise _Stop(CANCELLED, "cancelled")
        if model.fake:
            return
        usd, tokens = self._most(model, user)
        spent, caps, lost = self.run["spent"], self.run["caps"], self.run["unmeasured"]
        if model.price is None:
            used = spent["unpriced_tokens"] + lost["tokens"]
            if used + tokens > caps["tokens"]:
                raise _Stop(
                    CAP,
                    f"the next call ({model.label}) could take the unpriced models past "
                    f"{caps['tokens']:,} tokens ({used:,} used)",
                )
            return
        used_usd = spent["usd"] + lost["usd"]
        if used_usd + usd > caps["usd"]:
            raise _Stop(
                CAP,
                f"the next call ({model.label}, up to ${usd:.2f}) could take the run past "
                f"${caps['usd']:.2f} (${used_usd:.2f} spent)",
            )

    def _broke(self, model: ModelSpec, user: str) -> None:
        """A try that broke off after it was sent: nothing came back to count, so the most it
        can have cost is held against the cap."""
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
                self.dead[model.label] = str(exc)
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
                step.refused = got
                return
            if got.stop == llm.REFUSAL:
                step.refused = got.detail or "the model declined"
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
            step.refused = why
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
                    theme_id=case["theme"],
                    prompt=case["brief"],
                    line_type=case["line_type"],
                    max_blocks=case["max_blocks"],
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
        if label in self.dead:
            skipped = _Step(refused=f"not asked: {self.dead[label]}")
            result["design"] = skipped.to_json()
            self.run["results"].append(result)
            return
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
        _save(self.home, self.run)
        self.say(_line(case["id"], letter, "design", result["design"]))
        if ds is None:
            return
        rstep, checks = self._revise(case, spec.case, model, where, ds)
        result["revise"], result["checks"] = rstep.to_json(), checks
        _save(self.home, self.run)
        self.say(_line(case["id"], letter, "follow-up", result["revise"]))

    def execute(self, prepared: Sequence[Prepared]) -> dict[str, Any]:
        """Every case x model, in the run's order. Stops cleanly at a cap, on ``cancel`` or
        Ctrl+C: what is finished stays, the step in hand is dropped, the run says why."""
        by_id = {p.case.id: p for p in prepared}
        status, why = DONE, ""
        try:
            for case in self.run["cases"]:
                for letter, label in sorted(case["letters"].items()):
                    self._one(case, by_id[case["id"]], label, letter)
        except _Stop as stop:
            status, why = stop.status, stop.why
        except KeyboardInterrupt:
            status, why = CANCELLED, "cancelled (Ctrl+C)"
        self.run["status"], self.run["why"] = status, why
        self.run["finished"] = round(time.time(), 3)
        _save(self.home, self.run)
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
    """Two blind entries of one case side by side (materials, blocks, operations)."""
    run = load(home, run_id)
    recs = [
        shell_store.load(
            entry_home(home, run_id, case_id, letter),
            _entry_build(run, case_id, letter, which),
        )
        for letter in (a, b)
    ]
    return versions.compare_records(recs[0], recs[1])


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
            f"The run did not finish ({run['status']}: {run['why']}): a partial result."
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
            why = " / ".join(
                str(s["refused"]).replace("|", ";")[:200]
                for s in (d, v)
                if s and s["refused"]
            )
            lines.append(
                f"| {c['id']} | {pick} | {letter} | {label} | {_step_text(d)} | "
                f"{_step_text(v)} | {k.get('asked', '-')} | {k.get('kept', '-')} | {why} |"
            )
    text = "\n".join(lines) + "\n"
    path = home / REPORTS / f"shell-bakeoff-{run['id']}.md"
    atomic_write(path, text.encode("utf-8"))
    atomic_write(path.with_suffix(".json"), _json(card))
    run["report"] = (REPORTS / path.name).as_posix()
    _save(home, run)
    return Report(text, card, path)


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
        f"answer). The vendor may still bill them, at most {most} in all: that is not in the "
        "cost column or the usage meter, but it was held against the run's cap."
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
            (home / run["report"]).with_suffix(".json").read_text(encoding="utf-8")
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


def delete(home: Path, run_id: str) -> None:
    where = run_dir(home, run_id)
    load(home, run_id)  # only a real run's folder is ever removed
    for p in _report_files(home, run_id):
        p.unlink()
    shutil.rmtree(where)
