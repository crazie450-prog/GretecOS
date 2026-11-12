"""Vault I/O for agents (WO-2.5; plan sections 6.1, 6.2, 6.4 and 6.5).

The note tools (``note_read``, ``note_write``, ``note_append``, ``inbox_propose``; ``vault_search``
is :mod:`gregtec.vault.fts`) call :class:`VaultIO`. It owns the **path guard** (plan 6.5), which
WO-2.4's ``PreToolUse`` hook also asks, through :meth:`VaultIO.refusal` (the guard plus the link
check; :func:`write_refusal` is the guard alone). Both take the operation: ``write`` (create or
replace a note: ``note_write``, and an SDK ``Write`` or ``Edit``), ``append`` (``note_append``)
or ``propose`` (a new inbox note: ``inbox_propose`` only):

| path                                    | agents may                                         |
|-----------------------------------------|----------------------------------------------------|
| ``me/``                                 | read; propose changes through ``inbox_propose``    |
| ``_gos/``                               | read (repo-owned, deployed by ``gos vault deploy``)|
| ``INDEX.md``, ``base/{tier,machines,storage}.md`` | read (generated: the curator, My base)   |
| ``base/ plans/ quests/ themes/``         | read, write, append                                |
| ``personas/``                           | read; write and append drafts in ``personas/drafts/`` only |
| ``log/``                                | read; ``append`` to today's daily log only         |
| ``inbox/``                              | read, ``append``; new notes only by ``propose``    |
| anything else                           | read if it is a note; never write                  |

- A path is vault-relative with ``/`` between folders, at most 6 parts and 200 characters, and
  names a ``.md`` note. Each part is letters, digits, ``_``, ``.`` and ``-``, not starting with a
  dot (so ``.git``, ``.obsidian`` and ``.claude`` are never reached) and not a Windows device name
  (``con``, ``nul``, ...). A path to write is lower-case only: Windows' file names ignore case, so
  ``ME/goals.md`` would otherwise reach ``me/``. The resolved path must be the same path inside the
  vault, so no link or junction can lead a write elsewhere.
- Every write is checked against ``_gos/schema.md`` (:func:`schema.check_text`) before it lands:
  the frontmatter (exactly ``id``, ``t``, ``upd``) comes from the path, never from the agent.
- Writes are atomic: a temporary file in the same folder, then ``os.replace`` (retried ×3 while
  another program holds the note open). Logs are append-only: a daily log is only ever opened to
  append, and only today's.
- Note text is data, never instructions (plan 0.1 principle 8); the tools say so.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from gregtec.vault import schema

log = logging.getLogger(__name__)

SCHEMA_NOTE = "_gos/schema.md"
MAX_PATH = 200
MAX_DEPTH = 6
MAX_READ_BYTES = 256 * 1024  # a note is at most 150 lines; me/ notes are Clayton's own
MAX_TITLE = 120
_READ_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}")
_WRITE_PART = re.compile(r"[a-z0-9_][a-z0-9_.-]{0,79}")
_DEVICES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)
DAILY = re.compile(r"log/(\d{4}-\d{2}-\d{2})(-[2-9]|-[1-9][0-9])?\.md")
REPLACE_TRIES = 3
WRITE, APPEND, PROPOSE = (
    "write",
    "append",
    "propose",
)  # the operations the guard decides on
OPS = (WRITE, APPEND, PROPOSE)

# Who writes where (plan 6.5): the first part of the path, or the whole path.
CLAYTON, REPO, GENERATED, AGENTS, BOTH, JOURNAL, INBOX, NOBODY = (
    "clayton",
    "repo",
    "generated",
    "agents",
    "both",
    "journal",
    "inbox",
    "nobody",
)
GENERATED_NOTES = frozenset(
    {"INDEX.md", "base/tier.md", "base/machines.md", "base/storage.md"}
)
FOLDERS = {
    "me": CLAYTON,
    "_gos": REPO,
    "base": AGENTS,
    "plans": AGENTS,
    "quests": AGENTS,
    "themes": AGENTS,
    "personas": BOTH,
    "log": JOURNAL,
    "inbox": INBOX,
}
# Persona packs (WO-5.6, plan 6.5 "agents draft on request"): a pack in personas/ is live in
# every prompt, so agents write only drafts, which the packs loader skips; Clayton makes one live
# by moving it up a folder.
PERSONA_DRAFTS = "personas/drafts/"
# Folders the curator and the index skip: skills and agents are loaded by the SDK, not read;
# the shipped persona packs are the repo's and read by gregtec.agents.personas (WO-5.6).
NOT_NOTES = ("_gos/skills/", "_gos/agents/", "_gos/personas/")


class VaultError(ValueError):
    """Refused or failed; the vault is as it was. The message is for the model."""


def owner(rel: str) -> str:
    """Who writes ``rel`` (a cleaned path), per the plan 6.5 table above."""
    if rel in GENERATED_NOTES:
        return GENERATED
    head = rel.split("/", 1)[0].lower()
    return FOLDERS.get(head, NOBODY) if "/" in rel else NOBODY


def clean(rel: object, *, write: bool = False) -> str:
    """``rel`` if it is a well-formed note path (see the module docstring); else VaultError."""
    if not isinstance(rel, str) or not rel:
        raise VaultError("give the note's path inside the vault, e.g. plans/pl-0007.md")
    if "\\" in rel:
        raise VaultError(f"{rel!r}: use / between folders")
    parts = rel.split("/")
    if len(rel) > MAX_PATH or len(parts) > MAX_DEPTH:
        raise VaultError(f"{rel!r}: too long a path")
    part = _WRITE_PART if write else _READ_PART
    for p in parts:
        if not part.fullmatch(p) or p.endswith("."):
            if write and _READ_PART.fullmatch(p) and not p.endswith("."):
                raise VaultError(f"{rel!r}: a path to write is lower-case only")
            raise VaultError(
                f"{rel!r}: each part of a path is letters, digits, _ . and -, "
                "and doesn't start with a dot"
            )
        if p.split(".", 1)[0].lower() in _DEVICES:
            raise VaultError(f"{rel!r}: {p} is a Windows device name")
    if not rel.endswith(".md") or rel.endswith("/.md"):
        raise VaultError(f"{rel!r}: notes are .md files")
    return rel


def write_refusal(rel: object, op: str, *, today: dt.date | None = None) -> str | None:
    """Why an agent may not ``op`` (``write``, ``append`` or ``propose``) ``rel``, or None if it
    may (plan 6.5). ``today`` (default: the local date) is the one daily log that takes lines.
    The link check needs the vault: WO-2.4's ``PreToolUse`` hook asks :meth:`VaultIO.refusal`."""
    if op not in OPS:
        raise ValueError(f"op must be one of {OPS}, not {op!r}")
    day_now = today or dt.date.today()
    try:
        path = clean(rel)
    except VaultError as exc:
        return str(exc)
    who = owner(path)
    if who == CLAYTON:
        return (
            f"{path} is Clayton's (me/): agents only read it; propose a change with "
            "inbox_propose"
        )
    if who == REPO:
        return f"{path} is the repo's (_gos/): runtime agents can't change skills or the schema; propose with inbox_propose"
    if who == GENERATED:
        return f"{path} is generated (the curator or My base writes it); agents don't edit it"
    if who == NOBODY:
        return (
            f"{path} isn't in a folder agents write: base/, plans/, quests/, themes/, "
            "personas/drafts/, log/ (today's log, append only), inbox/"
        )
    if path.split("/", 1)[0].lower() == "personas" and not path.startswith(
        PERSONA_DRAFTS
    ):
        return (
            f"{path}: live persona packs are Clayton's; draft one at "
            f"{PERSONA_DRAFTS}pe-<name>.md and he moves it up to make it live"
        )
    if who == JOURNAL:
        day = DAILY.fullmatch(path)
        if day is None:
            return f"{path}: agents append only to a daily log, log/YYYY-MM-DD.md (weekly and monthly logs are the curator's)"
        if op != APPEND:
            return f"{path}: logs are append-only; use note_append"
        if day[1] != day_now.isoformat():
            return f"{path}: logs are append-only, and only today's log ({day_now.isoformat()}) takes new lines"
    elif who == INBOX:
        if op == WRITE:
            return f"{path}: new inbox notes come from inbox_propose; note_append adds to one"
    elif op == PROPOSE:
        return f"{path}: proposals go in inbox/, through inbox_propose"
    try:
        clean(path, write=True)
    except VaultError as exc:
        return str(exc)
    return None


def notes(root: Path) -> Iterator[str]:
    """Every note in the vault, as a path :func:`clean` accepts, sorted: hidden folders,
    links and ``_gos/skills/``, ``_gos/agents/`` and ``_gos/personas/`` are skipped."""
    found: list[str] = []
    for top, dirs, files in os.walk(root):
        here = Path(top)
        dirs[:] = sorted(  # a Windows junction isn't a symlink, and os.walk enters it
            d
            for d in dirs
            if _READ_PART.fullmatch(d)
            and not (here / d).is_symlink()
            and not (here / d).is_junction()
        )
        for name in files:
            full = here / name
            rel = full.relative_to(root).as_posix()
            if full.is_symlink() or full.is_junction() or rel.startswith(NOT_NOTES):
                continue
            try:
                found.append(clean(rel))
            except VaultError:
                continue
    yield from sorted(found)


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temporary file in its folder and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.gos-tmp")
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: another program has the note open
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(0.2 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)


def frontmatter(note_id: str, t: str, upd: dt.date) -> str:
    return f"---\nid: {note_id}\nt: {t}\nupd: {upd.isoformat()}\n---\n"


def _body_lines(body: object, what: str = "body") -> list[str]:
    if not isinstance(body, str):
        raise VaultError(f"{what} must be text")
    text = body.replace("\r\n", "\n")
    if "\r" in text or "\x00" in text:
        raise VaultError(f"{what} has a carriage return or NUL in it")
    return text.strip("\n").split("\n") if text.strip("\n") else []


def _strip_frontmatter(lines: list[str]) -> list[str]:
    """A body that starts with a frontmatter block (an agent writing back what note_read showed
    it) loses that block; the tool writes its own."""
    if lines and lines[0] == "---" and "---" in lines[1:]:
        return lines[lines.index("---", 1) + 1 :]
    return lines


@dataclass
class VaultIO:
    """The live vault as the note tools see it. ``on_write`` gets each path written (the core
    mirrors it into ``note_fts``); its failure is logged, never the write's."""

    root: Path
    today: Callable[[], dt.date] = dt.date.today
    on_write: Callable[[str], None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def types(self) -> dict[str, schema.NoteType]:
        try:
            return schema.load_types(self.root / SCHEMA_NOTE)
        except (OSError, ValueError) as exc:
            raise VaultError(
                f"the vault's {SCHEMA_NOTE} can't be read ({exc}); run gos vault deploy"
            ) from None

    def path(self, rel: object, *, write: bool = False) -> tuple[str, Path]:
        """The cleaned path and the file it names, which must stay that path in the vault."""
        name = clean(rel, write=write)
        root = self.root.resolve()
        if not root.is_dir():
            raise VaultError(f"there is no vault at {self.root}: run gos vault deploy")
        target = root / name
        try:
            inside = target.resolve().relative_to(root).as_posix()
        except ValueError:
            inside = None
        if inside != name:
            raise VaultError(f"{name}: a link leads elsewhere; refused")
        return name, target

    # --- reading

    def read(self, rel: object) -> tuple[str, str]:
        """(path, text) of one note."""
        name, target = self.path(rel)
        if not target.is_file():
            raise VaultError(
                f"no note at {name}; INDEX.md lists the notes and vault_search finds them"
            )
        with open(target, "rb") as f:
            data = f.read(MAX_READ_BYTES + 1)
        if len(data) > MAX_READ_BYTES:
            raise VaultError(f"{name} is over {MAX_READ_BYTES // 1024} KiB; not read")
        return name, data.decode("utf-8", "replace").replace("\r\n", "\n")

    # --- writing

    def refusal(self, rel: object, op: str) -> str | None:
        """Why an agent may not ``op`` ``rel`` in this vault, or None if it may: the guard
        (:func:`write_refusal`, with this vault's today) and the link check of :meth:`path`.
        WO-2.4's ``PreToolUse`` hook asks this, with the path made vault-relative."""
        why = write_refusal(rel, op, today=self.today())
        if why is not None:
            return why
        try:
            self.path(rel, write=True)
        except VaultError as exc:
            return str(exc)
        return None

    def _refuse(self, rel: object, op: str) -> None:
        why = self.refusal(rel, op)
        if why is not None:
            raise VaultError(why)

    def _compose(
        self, name: str, lines: list[str], types: dict[str, schema.NoteType]
    ) -> str:
        kind = schema.type_for(name, types)
        if kind is None:
            raise VaultError(f"{SCHEMA_NOTE} has no note type at {name}")
        text = frontmatter(schema.id_for(name, kind), kind.t, self.today())
        text += "".join(line + "\n" for line in lines)
        problems = schema.check_text(name, text, types)
        if problems:
            raise VaultError(
                f"{name} doesn't follow the note grammar: " + "; ".join(problems)
            )
        return text

    def write(self, rel: object, body: object) -> str:
        """Create or replace a note; the frontmatter is made from its path and today's date."""
        self._refuse(rel, WRITE)
        name, target = self.path(rel, write=True)
        lines = _strip_frontmatter(_body_lines(body))
        with self._lock:
            text = self._compose(name, lines, self.types())
            atomic_write(target, text.encode("utf-8"))
        self._written(name)
        return name

    def append(self, rel: object, lines_text: object, *, computed: bool = False) -> str:
        """Add lines to the end of a note: today's daily log (made if missing, then only ever
        appended to), or an existing note in an agent folder (rewritten atomically, ``upd``
        set to today). ``computed``: the session scribe's journal (WO-6.3, D263), whose
        ``n_<kind>`` count lines code computed; an agent's lines never hold an ``n_`` key."""
        self._refuse(rel, APPEND)
        name, target = self.path(rel, write=True)
        lines = _body_lines(lines_text, "lines")
        if not lines:
            raise VaultError("nothing to append")
        if computed and owner(name) != JOURNAL:
            raise VaultError(f"{name}: computed counts go in a daily log only")
        if not computed and any(line.startswith(schema.COUNT_PREFIX) for line in lines):
            raise VaultError(
                "n_ keys are computed counts (the curator's counts and the session "
                "scribe's); agents don't write them"
            )
        if lines[0] == "---":
            raise VaultError("append body lines only; the note keeps its frontmatter")
        with self._lock:
            types = self.types()
            if owner(name) == JOURNAL:
                self._append_log(name, target, lines, types)
            else:
                if not target.is_file():
                    maker = "inbox_propose" if owner(name) == INBOX else "note_write"
                    raise VaultError(f"no note at {name} yet: {maker} makes it")
                old = target.read_text(encoding="utf-8").replace("\r\n", "\n")
                try:
                    schema.parse(old)
                except ValueError as exc:
                    raise VaultError(
                        f"{name} doesn't follow the grammar now ({exc}); note_write a fixed copy"
                    ) from None
                rows = old.split("\n")
                body = rows[rows.index("---", 1) + 1 :]  # parse() found the closing ---
                while body and not body[-1]:
                    body.pop()
                text = self._compose(name, body + lines, types)
                atomic_write(target, text.encode("utf-8"))
        self._written(name)
        return name

    def _append_log(
        self,
        name: str,
        target: Path,
        lines: list[str],
        types: dict[str, schema.NoteType],
    ) -> None:
        added = "".join(line + "\n" for line in lines)
        try:
            old = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = self._compose(name, lines, types)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(target, "xb") as f:
                    f.write(text.encode("utf-8"))
                return
            except FileExistsError:
                old = target.read_text(encoding="utf-8")
        old = old.replace("\r\n", "\n")
        joined = old if old.endswith("\n") else old + "\n"
        problems = schema.check_text(name, joined + added, types)
        if problems:
            day = DAILY.fullmatch(name)
            hint = ""
            if day and len((joined + added).splitlines()) > schema.MAX_LINES:
                n = int(day[2][1:]) + 1 if day[2] else 2
                hint = f" (this log is full: append to log/{day[1]}-{n}.md)"
            raise VaultError(
                f"{name} doesn't follow the note grammar: " + "; ".join(problems) + hint
            )
        with open(target, "ab") as f:  # append-only: the old lines are never rewritten
            f.write((added if old.endswith("\n") else "\n" + added).encode("utf-8"))

    def propose(self, title: object, body: object) -> str:
        """A new ``inbox/`` note for Clayton: ``title:`` and the body. Never replaces one."""
        if not isinstance(title, str) or not title.strip():
            raise VaultError("give the proposal a one-line title")
        title = " ".join(title.split())
        if len(title) > MAX_TITLE:
            raise VaultError(f"keep the title under {MAX_TITLE} characters")
        lines = _strip_frontmatter(_body_lines(body))
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
        stem = f"ib-{self.today():%Y%m%d}-{slug or 'proposal'}"
        with self._lock:
            types = self.types()
            for n in range(1, 100):
                name = f"inbox/{stem}{'' if n == 1 else f'-{n}'}.md"
                self._refuse(name, PROPOSE)
                _, target = self.path(name, write=True)
                if target.exists():
                    continue
                text = self._compose(name, [f"title: {title}", *lines], types)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(target, "xb") as f:
                        f.write(text.encode("utf-8"))
                except FileExistsError:
                    continue
                break
            else:
                raise VaultError("too many proposals with this title today")
        self._written(name)
        return name

    def _written(self, name: str) -> None:
        if self.on_write is None:
            return
        try:
            self.on_write(name)
        except Exception:  # the note is written; search catches up at the daily mirror
            log.exception("mirroring %s into note_fts failed", name)
