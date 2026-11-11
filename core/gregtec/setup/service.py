"""The setup wizard's logic, behind the core's ``/api/setup`` routes (WO-2.9, D167-D169).

- :meth:`SetupService.describe`: every step (:mod:`gregtec.setup.registry`) with the values
  gos.toml has now (defaults where it has none), whether each secret is stored (never its value),
  whether setup is done (``[setup] completed_utc`` in gos.toml) and whether gos.toml exists.
- :meth:`check` and :meth:`save`: a step's values are checked (each field by its kind, then the
  step's own check, ``gos doctor``'s for the paths); a save writes them to gos.toml atomically
  (:mod:`gregtec.setup.tomledit`), and only after the new file has read back through
  ``config.load`` (and the voice client's parser, for ``[voice]``) from its temp copy.
- :meth:`store_secret`, :meth:`delete_secret`, :meth:`test_secret`: the keyring (plan 4.6). A
  secret never goes into gos.toml, a log line or a reply.
- :meth:`finish`: the checklist again; with no error row, ``[setup] completed_utc`` is written.
- Every Play is remembered (D223): core start and ``gos sync`` (:func:`record_plays`) and each
  describe, check, save and finish add gos.toml's Play and the running core's (or sync's) to
  ``former_plays.txt`` beside gos.toml, and each save and finish writes the
  same list to ``[setup] former_plays``. The paths step and the checklist count them all as Play,
  and so does ``gregtec.sync``. Nothing GOS writes here goes in or around a Play or an instance
  (``doctor.touch_problem``).

Settings take effect when GOS next starts (the core read gos.toml when it started); the
secrets at once (the core looks for the key every 30 s, and is told now).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import threading
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import keyring.errors

from gregtec import config, doctor
from gregtec.config import Config
from gregtec.llm import keys
from gregtec.setup import devices as devices_mod
from gregtec.setup import registry, tomledit
from gregtec.setup.registry import Check, Context, Outcome, Step, TestEnv
from gregtec.setup.steps import checklist, paths, voice

log = logging.getLogger("gregtec.setup")

SECRET_MAX = 400
RECORD_HEADER = (
    "# Every folder that has been Play for this GOS (D223). The setup wizard adds to it; sync\n"
    "# never writes Export or Lab in or around one. Remove a line only by hand, on purpose, and\n"
    "# from [setup] former_plays in gos.toml too (docs/runbooks/setup_wizard.md, Former Plays).\n"
)
CONTROL = {chr(c) for c in range(0x20)} | {"\x7f"}


class SetupError(ValueError):
    """A request the wizard refuses: ``status`` is its HTTP status, ``body`` its JSON."""

    def __init__(self, status: int, message: str, **body: Any) -> None:  # noqa: ANN401
        super().__init__(message)
        self.status = status
        self.body = {"error": message, **body}


def _fast_client(key: str) -> Any:  # noqa: ANN401 - anthropic.Anthropic or a fake
    # Looked up per call, so the e2e harness can fake it.
    from gregtec.agents import fast_lane

    return fast_lane.client_for(key)


def record_plays(path: Path, cfg: Config) -> str | None:
    """Put the Play of the gos.toml at ``path`` and of ``cfg`` (what this process runs or syncs
    with) on record beside gos.toml (D223). The core calls this as it starts and ``gos sync``
    before it plans, so a hand edit of ``[paths] play`` between restarts never frees a Play GOS
    has used. Returns why it couldn't be written, or None (a first run records nothing)."""
    try:
        SetupService(path, cfg, provider=devices_mod.FakeDevices(), steps=[]).record()
    except SetupError as exc:
        return str(exc.body["error"])
    return None


class SetupService:
    def __init__(
        self,
        path: Path,
        base: Config,
        *,
        provider: devices_mod.DeviceProvider | None = None,
        client_factory: Callable[[str], Any] = _fast_client,
        meter: Any = None,  # noqa: ANN401 - gregtec.llm.usage.UsageMeter
        ai: Any = None,  # noqa: ANN401 - gregtec.agents.no_ai.AiMode
        on_secret: Callable[[], None] | None = None,
        steps: list[Step] | None = None,
    ) -> None:
        self.path = path
        self.base = base
        self.provider = provider or devices_mod.default_provider()
        self.env = TestEnv(client_factory, meter, ai)
        self.on_secret = on_secret
        self.steps = steps if steps is not None else registry.steps()
        self._lock = threading.RLock()  # save() checks under it

    # --- reading

    def _read(self) -> tuple[Config, dict[str, Any], str | None]:
        if not self.path.is_file():
            try:  # no gos.toml (a first run, or deleted): the record beside it still counts
                found = config.read_former_plays(self.path.parent)
            except config.ConfigError as exc:
                return self.base, {}, str(exc)
            former = tuple(dict.fromkeys([*self.base.former_plays, *found]))
            return dataclasses.replace(self.base, former_plays=former), {}, None
        try:
            with open(self.path, "rb") as f:
                data = tomllib.load(f)
            return config.load(self.path), data, None
        except (OSError, ValueError) as exc:
            return self.base, {}, f"gos.toml can't be read ({exc}); fix it by hand"

    def has_secret(self, name: str) -> bool:
        try:
            return keys.has_key(name)
        except keyring.errors.KeyringError:
            return False

    def devices(self) -> dict[str, Any]:
        return devices_mod.listing(self.provider)

    def context(self) -> tuple[Context, str | None]:
        cfg, data, error = self._read()
        ctx = Context(cfg, data, self.path, self.has_secret, self.devices, self.base)
        return ctx, error

    def step(self, step_id: str) -> Step:
        found = next((s for s in self.steps if s.id == step_id), None)
        if found is None:
            raise SetupError(404, "no such setup step")
        return found

    def done(self, ctx: Context) -> str | None:
        value = ctx.toml("setup", "completed_utc")
        return value if isinstance(value, str) and value else None

    def describe(self) -> dict[str, Any]:
        ctx, error = self.context()
        self._record_quietly(ctx)  # the dashboard's first request after a start (D223)
        return {
            "done": self.done(ctx) is not None,
            "completed_utc": self.done(ctx),
            # A first run has no gos.toml: only then does the dashboard open on the wizard (D167).
            "config_exists": self.path.is_file(),
            "config_path": str(self.path),
            "config_error": error,
            "steps": [s.describe(ctx) for s in self.steps],
        }

    # --- checking and saving a step

    def _values(self, step: Step, given: Mapping[str, Any]) -> dict[str, str]:
        """``given`` checked for shape: known fields of ``step``, strings, no control chars."""
        out: dict[str, str] = {}
        for fid, value in given.items():
            f = step.field(fid)
            if f is None or f.kind in ("secret", "checks"):
                raise SetupError(400, f"{step.id} has no value field {fid!r}")
            if not isinstance(value, str) or len(value) > registry.VALUE_MAX:
                raise SetupError(
                    400, f"{fid}: a string of at most {registry.VALUE_MAX} characters"
                )
            if CONTROL & set(value):
                raise SetupError(400, f"{fid}: no control characters")
            out[fid] = value.strip()
        return out

    def _field_problems(self, step: Step, values: Mapping[str, str]) -> dict[str, str]:
        problems: dict[str, str] = {}
        for f in step.fields:
            if f.id not in values:
                continue
            value = values[f.id]
            if not value:
                if f.required:
                    problems[f.id] = "needed"
                continue
            if f.kind == "path" and not Path(value).is_absolute():
                problems[f.id] = "a full path, e.g. C:/Games/PrismLauncher"
            elif f.kind == "choice" and value not in {v for v, _ in f.options}:
                problems[f.id] = "pick one of the choices"
        return problems

    def check(self, step_id: str, given: Mapping[str, Any]) -> tuple[Outcome, Context]:
        step = self.step(step_id)
        ctx, _ = self.context()
        values = {
            f.id: f.current(ctx)
            for f in step.fields
            if f.kind not in ("secret", "checks")
        }
        values.update(self._values(step, given))
        # A check still answers when the record can't be written, and a save then refuses.
        self._record_quietly(ctx)
        problems = self._field_problems(step, values)
        if problems:  # the step's own check may count on well-formed values
            return Outcome(problems), ctx
        return step.check(ctx, values), ctx

    def save(self, step_id: str, given: Mapping[str, Any]) -> dict[str, Any]:
        step = self.step(step_id)
        values = self._values(step, given)
        with self._lock:
            outcome, ctx = self.check(step_id, given)
            if outcome.blocked:
                raise SetupError(
                    400,
                    "not saved: fix the marked fields or checks first",
                    problems=outcome.problems,
                    checks=[c.as_dict() for c in outcome.checks],
                )
            updates: dict[tuple[str, str], tomledit.Value | None] = {}
            for fid, value in values.items():
                f = step.field(fid)
                assert f is not None and f.toml is not None
                updates[f.toml] = value if value else None
            # The Play this save makes is on record before gos.toml names it (D223).
            new_play = paths.candidate(ctx, values).instance("play")
            updates[("setup", "former_plays")] = self._remember(ctx, [new_play])
            self._write(updates)
        log.info("setup step saved", extra={"step": step.id, "keys": sorted(values)})
        return {
            "saved": sorted(f"[{t}] {k}" for t, k in updates),
            "checks": [c.as_dict() for c in outcome.checks],
        }

    # --- every Play, remembered (D223)

    def _guard(self, ctx: Context, target: Path) -> None:
        """Refuse to write ``target`` (gos.toml or the record) in or around a Play or an instance."""
        why = doctor.touch_problem(target, doctor.guarded(ctx.cfg, paths.plays(ctx)))
        if why is not None:
            raise SetupError(409, f"not written: {target} {why}")

    def record(self) -> None:
        """Put every Play GOS knows of now on record (D223). A first run (no gos.toml) makes no
        file; its first save records. Raises :class:`SetupError` if it can't be written."""
        if self.path.is_file():
            self._remember(self.context()[0])

    def _record_quietly(self, ctx: Context) -> None:
        if self.path.is_file():
            try:
                self._remember(ctx)
            except SetupError as exc:
                log.warning("former Plays not recorded: %s", exc.body["error"])

    def _remember(self, ctx: Context, extra: list[Path] | None = None) -> list[str]:
        """Add every Play ``ctx`` knows of (and ``extra``) to ``former_plays.txt`` beside gos.toml,
        resolved; return the whole record for ``[setup] former_plays``. Entries are only ever
        added here; a hand edit removes one (D223)."""
        record: dict[str, str] = {}
        for p in [*paths.plays(ctx), *(extra or [])]:
            if not p.is_absolute():  # e.g. the Windows default on Linux: no folder here
                continue
            try:
                text = str(p.resolve())
            except (OSError, RuntimeError):  # a link loop: keep it as written
                text = str(p.absolute())
            record.setdefault(text.casefold(), text)
        target = self.path.parent / config.FORMER_PLAYS_FILE
        listed = list(record.values())
        text = RECORD_HEADER + "".join(f"{p}\n" for p in listed)
        with self._lock:
            try:
                if target.is_file() and target.read_text("utf-8") == text:
                    return listed
            except (OSError, UnicodeDecodeError):
                pass  # rewritten below; config.load refuses a record it can't read
            self._guard(ctx, target)
            try:
                tomledit.write_atomic(target, text)
            except OSError as exc:
                raise SetupError(
                    409, f"not saved: can't write {target} ({exc})"
                ) from None
        return listed

    def _write(self, updates: Mapping[tuple[str, str], tomledit.Value | None]) -> None:
        ctx, _ = self.context()
        self._guard(ctx, self.path)
        try:
            text = self.path.read_text("utf-8") if self.path.is_file() else ""
            new = tomledit.set_values(text, updates)
        except (OSError, UnicodeDecodeError) as exc:
            raise SetupError(409, f"gos.toml can't be read ({exc})") from None
        except tomledit.TomlEditError as exc:
            raise SetupError(409, str(exc)) from None

        def verify(tmp: Path) -> None:  # config.py's loader must read it back (WO-2.9)
            config.load(tmp)
            with open(tmp, "rb") as f:
                table = tomllib.load(f).get("voice", {})
            if any(t == "voice" for t, _ in updates):
                why = voice.voice_problem(table if isinstance(table, dict) else {})
                if why is not None:
                    raise ValueError(why)

        try:
            tomledit.write_atomic(self.path, new, verify)
        except ValueError as exc:
            raise SetupError(
                409, f"not saved: gos.toml wouldn't read back ({exc})"
            ) from None
        except OSError as exc:
            raise SetupError(409, f"not saved: can't write gos.toml ({exc})") from None

    # --- secrets

    def _secret_field(self, name: str) -> registry.Field:
        for step in self.steps:
            for f in step.fields:
                if f.kind == "secret" and f.secret == name:
                    return f
        raise SetupError(404, "no such secret")

    def store_secret(self, name: str, value: Any) -> dict[str, Any]:  # noqa: ANN401
        self._secret_field(name)
        if not isinstance(value, str) or not value.strip():
            raise SetupError(400, "nothing to store")
        value = value.strip()
        if len(value) > SECRET_MAX or CONTROL & set(value):
            raise SetupError(400, "that doesn't look like a key; copy it again")
        if why := keys.secret_problem(name, value):
            raise SetupError(400, f"not stored: {why}")
        try:
            replaced = keys.set_key(name, value)
        except keyring.errors.KeyringError as exc:
            raise SetupError(
                503, f"the keyring is unavailable ({type(exc).__name__})"
            ) from None
        log.info("secret stored", extra={"secret": name, "replaced": replaced})
        self._secret_changed()
        return {"name": name, "stored": True, "replaced": replaced}

    def delete_secret(self, name: str) -> dict[str, Any]:
        self._secret_field(name)
        try:
            deleted = keys.delete_key(name)
        except keyring.errors.KeyringError as exc:
            raise SetupError(
                503, f"the keyring is unavailable ({type(exc).__name__})"
            ) from None
        log.info("secret deleted", extra={"secret": name, "deleted": deleted})
        self._secret_changed()
        return {"name": name, "stored": False, "deleted": deleted}

    def _secret_changed(self) -> None:
        if self.on_secret is not None:
            try:
                self.on_secret()
            except Exception:  # the core's key check runs again in 30 s anyway
                log.exception("can't tell the core about the new secret")

    def test_secret(self, name: str) -> dict[str, Any]:
        f = self._secret_field(name)
        if f.tester is None:
            raise SetupError(404, "this secret has no test")
        try:
            value = keys.get_key(name)
        except keys.MissingKeyError:
            raise SetupError(409, "store it first, then test it") from None
        except keyring.errors.KeyringError as exc:
            raise SetupError(
                503, f"the keyring is unavailable ({type(exc).__name__})"
            ) from None
        ok, detail = f.tester(self.env, value)
        log.info("secret tested", extra={"secret": name, "ok": ok})
        return {"name": name, "ok": ok, "detail": detail}

    # --- the checklist and finishing

    def checklist(self) -> list[Check]:
        ctx, error = self.context()
        rows = checklist.rows(ctx)
        if error:
            rows.insert(0, Check("config", "gos.toml", registry.ERROR, error))
        return rows

    def finish(self) -> dict[str, Any]:
        with self._lock:
            rows = self.checklist()
            if any(r.status == registry.ERROR for r in rows):
                raise SetupError(
                    400,
                    "setup isn't done: fix the rows marked with an error, then re-run",
                    checks=[r.as_dict() for r in rows],
                )
            when = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            ctx, _ = self.context()
            self._write(
                {
                    ("setup", "completed_utc"): when,
                    ("setup", "former_plays"): self._remember(ctx),
                }
            )
        log.info("setup finished")
        return {
            "done": True,
            "completed_utc": when,
            "checks": [r.as_dict() for r in rows],
        }
