"""The setup wizard's steps (WO-2.9, D167): a small registry, so later phases add settings
without editing the wizard.

A step is a :class:`Step` in a module of the :mod:`gregtec.setup.steps` package, listed in that
module's ``STEPS``. :func:`steps` imports every module there and sorts the steps by ``order``, so a
later work order adds a file (``steps/discord_bot.py`` with ``STEPS = [Step(...)]``) and nothing
else. The dashboard draws every step from its description (:meth:`Step.describe`), so a new step
needs no dashboard code either.

A field is one of:

- ``path``, ``text``, ``choice``, ``device`` (a choice from the audio devices, WO-5.x enumerates
  them), ``key`` (a talk key or hotkey) or ``phrase``: a value in gos.toml (``toml`` is its
  ``(table, key)``), saved with the step;
- ``secret``: a value in the keyring (``secret`` is its name in :mod:`gregtec.llm.keys`), stored
  on its own and never sent back; ``test`` adds a "Test" button;
- ``checks``: no value, the step shows a checklist (``gos doctor``'s checks) with a re-run button.

``check(ctx, values)`` checks a step's values before they are saved: problems by field id (any
problem blocks the save) and checklist rows (``error`` rows block it too; ``warn`` rows don't).
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gregtec.config import Config

KINDS = ("path", "text", "choice", "device", "key", "phrase", "secret", "checks")
OK, WARN, ERROR = "ok", "warn", "error"
VALUE_MAX = 400  # characters in one value (paths included)


@dataclass(frozen=True)
class Check:
    """One checklist row: what was checked, how it went (ok, warn, error) and why."""

    id: str
    label: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class TestEnv:
    """What a secret's test call may use: the Messages API client maker (the fast lane's, a fake
    in tests), the usage meter and no-AI mode (both None when the core has none)."""

    __test__ = False  # not a pytest class

    client_factory: Callable[[str], Any]
    meter: Any = None  # gregtec.llm.usage.UsageMeter
    ai: Any = None  # gregtec.agents.no_ai.AiMode


@dataclass
class Context:
    """What a step sees: the config as gos.toml has it now (defaults for what it lacks), its raw
    tables, where it lives, and the keyring and device lookups of the wizard's service."""

    cfg: Config
    data: Mapping[str, Any]
    path: Path
    has_secret: Callable[[str], bool]
    devices: Callable[[], dict[str, Any]]

    def toml(self, table: str, key: str) -> Any:  # noqa: ANN401 - any TOML value
        section = self.data.get(table)
        return section.get(key) if isinstance(section, Mapping) else None


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    kind: str
    toml: tuple[str, str] | None = None
    secret: str | None = None
    help: str = ""
    required: bool = False
    placeholder: str = ""
    options: tuple[tuple[str, str], ...] = ()  # (value, label) for a choice
    suggestions: tuple[str, ...] = ()
    devices: str = ""  # device: "inputs" or "outputs" (:mod:`gregtec.setup.devices`)
    # secret: a test call with the stored value -> (ok, what happened); None: no test
    tester: Callable[[TestEnv, str], tuple[bool, str]] | None = None
    # The value shown when gos.toml doesn't set the key (str, or computed from the context).
    default: str | Callable[[Context], str] = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"field {self.id}: unknown kind {self.kind!r}")
        if self.kind == "secret" and not self.secret:
            raise ValueError(f"field {self.id}: a secret field names its keyring entry")
        if self.kind == "device" and self.devices not in ("inputs", "outputs"):
            raise ValueError(f"field {self.id}: devices must be inputs or outputs")
        if self.kind not in ("secret", "checks") and self.toml is None:
            raise ValueError(f"field {self.id}: a value field names its gos.toml key")

    def current(self, ctx: Context) -> str:
        """The value to show: gos.toml's, else the default."""
        if self.toml is not None:
            found = ctx.toml(*self.toml)
            if found is not None:
                return str(found)
        return self.default(ctx) if callable(self.default) else self.default


@dataclass(frozen=True)
class Outcome:
    """A step's check: problems by field id, the checklist, and values to save (normalised)."""

    problems: dict[str, str] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.problems) or any(c.status == ERROR for c in self.checks)


StepCheck = Callable[[Context, Mapping[str, str]], Outcome]


def _no_check(ctx: Context, values: Mapping[str, str]) -> Outcome:
    return Outcome()


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    order: (
        int  # where it goes; built-in steps use 10, 20, ... so later ones fit between
    )
    intro: str
    fields: tuple[Field, ...]
    check: StepCheck = _no_check
    optional: bool = False  # the wizard may be finished without it

    def field(self, field_id: str) -> Field | None:
        return next((f for f in self.fields if f.id == field_id), None)

    def describe(self, ctx: Context) -> dict[str, Any]:
        """The step for the dashboard. Secrets show only whether one is stored."""
        out = []
        for f in self.fields:
            item: dict[str, Any] = {
                "id": f.id,
                "label": f.label,
                "kind": f.kind,
                "help": f.help,
                "required": f.required,
                "placeholder": f.placeholder,
                "options": [{"value": v, "label": label} for v, label in f.options],
                "suggestions": list(f.suggestions),
            }
            if f.kind == "secret":
                item["secret"] = f.secret
                item["stored"] = ctx.has_secret(str(f.secret))
                item["test"] = f.tester is not None
            elif f.kind != "checks":
                item["devices"] = f.devices or None
                item["value"] = f.current(ctx)
                item["key"] = f"[{f.toml[0]}] {f.toml[1]}" if f.toml else None
            out.append(item)
        return {
            "id": self.id,
            "title": self.title,
            "intro": self.intro,
            "optional": self.optional,
            "fields": out,
        }


def steps() -> list[Step]:
    """Every registered step, by ``order`` (then id): the modules of :mod:`gregtec.setup.steps`."""
    from gregtec.setup import steps as package

    found: dict[str, Step] = {}
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        for step in getattr(module, "STEPS", ()):
            if not isinstance(step, Step):
                raise TypeError(f"{info.name}.STEPS holds a {type(step).__name__}")
            if step.id in found:
                raise ValueError(f"two setup steps are called {step.id!r}")
            found[step.id] = step
    return sorted(found.values(), key=lambda s: (s.order, s.id))
