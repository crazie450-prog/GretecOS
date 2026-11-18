"""Building themes (WO-10.6; plan 7.10 "Themes (D36)", 6.2, 6.5; D314, D315).

**A theme** is a ``t: theme`` note, ``th-<name>``, in the vault grammar (plan 6.2):

- ``name`` (display name), ``v`` (its version, set by code), ``summary`` (one line), ``fits``
  (the line types it suits, :data:`LINE_TYPES`), ``prompt`` (the words it was first made
  from), ``change`` (one line per refinement, newest last), the rules ``motif``, ``massing``,
  ``roof``, ``window``, ``lighting`` (one rule a line), ``adapt`` (``<line type>: <rule>``, its
  adaptation per line type), ``sample`` (references to sample builds) and ``note``;
- its **palette**: one ```` ```tsv ```` table ``role  block  note``, each ``block`` a block state
  ``<registry name>@<meta>`` from Clayton's block catalog (WO-10.3), never a numeric id, each
  ``role`` one of :data:`ROLES`; a theme has at least one ``wall``, ``roof``, ``window`` and
  ``light`` entry.

**Validation** (:func:`check`): every palette entry goes through plan 7.10's palette rule on the
catalog alone (:func:`gregtec.build.palette.check` with no world: a theme names no world; the
world check is the schematic writer's when a shell is written): refused when it is air or
``gregtech:gt.blockmachines``, not a placeable state in the catalog, has a tile entity, or has a
meta above 15 (worldedit-gtnh #30). A theme with a refused entry is never saved.

**Where** (D314): the starter themes are authored in the repo's ``vault-template/_gos/themes/``
and deployed with ``_gos/`` (read from the repo's template first, else the live vault's
``_gos/themes/``, as persona packs are; never edited in the live vault). Themes made or refined
in conversation are ``themes/th-<name>.md`` in the live vault (the current version; one with a
starter's id replaces it) and every earlier version is kept as
``themes/versions/th-<name>--v<N>.md``. All writes go through :class:`~gregtec.vault.io.VaultIO`
with its ``theme`` operation, which only this module uses: the note tools can't write
``themes/``, so no palette lands there unchecked, and nothing here can reach ``me/`` or
``_gos/``.

**Selection** (:func:`select`, D315), per build: a theme with an optional prompt, a prompt only,
or blank: the architect gets a concept from the line type (:data:`LINE_TYPES`) and the theme
most used by builds near the site (:func:`near`, from :func:`record_use`'s file), else the first
theme that fits the line type.

Theme text is data, never instructions: it reaches a model only as a tool result.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gregtec.build import palette
from gregtec.vault import deploy as vault_deploy
from gregtec.vault import schema
from gregtec.vault.io import THEME, VaultError, VaultIO

TYPE = "theme"
ID = re.compile(r"th-[a-z0-9]+(?:-[a-z0-9]+){0,7}")
MAX_ID = 48
SHIPPED = Path(vault_deploy.OWNED) / "themes"  # _gos/themes/, repo-owned
OWN = "themes"  # the live vault's: current versions
VERSIONS = "themes/versions"  # the live vault's: every earlier version
ROLES = ("wall", "trim", "pillar", "floor", "roof", "window", "light", "accent")
REQUIRED_ROLES = ("wall", "roof", "window", "light")
# The line types a build can be (D315), each with the concept blank mode starts from (plan 7.10:
# chemistry gets pipework and tanks, EBF gets heavy industrial).
LINE_TYPES: dict[str, str] = {
    "chemistry": "pipework and tanks: exposed pipe runs, round or tall tank volumes, catwalks",
    "smelting": "heavy industrial: thick walls, chimneys, heat-proof cladding (EBF and other "
    "furnaces)",
    "power": "a generator hall: tall and plain, cable chases along the walls",
    "processing": "an ore mill: stepped sheds, conveyor galleries, hoppers",
    "assembly": "a clean workshop: long hall, big windows, bright even light",
    "general": "a plain hall that matches its neighbours",
}
GENERAL = "general"
RULE_KEYS = ("motif", "massing", "roof", "window", "lighting")
LIST_KEYS = (*RULE_KEYS, "change", "sample", "note")
KEYS = frozenset({"name", "v", "summary", "fits", "prompt", "adapt", *LIST_KEYS})
PALETTE_HEADER = ["role", "block", "note"]
MAX_TEXT = 200  # characters of one value
MAX_RULES = 8  # lines per rule key
MAX_SWATCHES = 32
MAX_CHANGES = (
    12  # change lines kept (the oldest drop off; every version is kept as a note)
)
MAX_NAME = 40
NEAR_BLOCKS = 256  # blank mode looks for themes used within this distance (D315)
USES_FILE = Path("data", "build", "theme_uses.jsonl")
_BAD_TEXT = re.compile(r"[<>\t]")
_VERSION_NOTE = re.compile(r"(th-[a-z0-9-]+)--v([1-9][0-9]{0,4})\.md")


class ThemeError(ValueError):
    """Refused or not found; nothing was written. The message is for the model and Clayton."""


@dataclass(frozen=True)
class Swatch:
    role: str
    block: str  # <registry name>@<meta>
    note: str = ""


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    v: int
    palette: tuple[Swatch, ...]
    summary: str = ""
    fits: tuple[str, ...] = ()
    prompt: str = ""
    change: tuple[str, ...] = ()
    motif: tuple[str, ...] = ()
    massing: tuple[str, ...] = ()
    roof: tuple[str, ...] = ()
    window: tuple[str, ...] = ()
    lighting: tuple[str, ...] = ()
    adapt: tuple[tuple[str, str], ...] = ()  # (line type, rule)
    sample: tuple[str, ...] = ()
    note: tuple[str, ...] = ()
    source: str = "vault"  # shipped or vault

    def adaptation(self, line_type: str) -> str | None:
        rules = [r for t, r in self.adapt if t == line_type]
        return "; ".join(rules) if rules else None

    def entries(self) -> list[palette.Entry]:
        return [palette.parse_entry(s.block) for s in self.palette]


# --- the note ------------------------------------------------------------------------------


def _text(value: object, what: str, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ThemeError(f"{what} must be text")
    text = " ".join(value.split())
    if not text and not empty:
        raise ThemeError(f"{what} is empty")
    if len(text) > MAX_TEXT:
        raise ThemeError(f"{what} is over {MAX_TEXT} characters")
    if _BAD_TEXT.search(text) or not text.isprintable():
        raise ThemeError(f"{what} has a <, > or a control character in it")
    return text


def _line_type(value: str, what: str) -> str:
    if value not in LINE_TYPES:
        raise ThemeError(
            f"{what}: {value!r} is not a line type ({', '.join(LINE_TYPES)})"
        )
    return value


def _swatch(role: object, block: object, note: object = "") -> Swatch:
    r = _text(role, "a palette role").lower()
    if r not in ROLES:
        raise ThemeError(f"palette role {r!r} is not one of {', '.join(ROLES)}")
    try:
        entry = palette.parse_entry(_text(block, "a palette block"))
    except palette.PaletteError as exc:
        raise ThemeError(str(exc)) from None
    return Swatch(r, entry.key, _text(note, "a palette note", empty=True))


def build(fields: Mapping[str, Any], theme_id: str, v: int, source: str) -> Theme:
    """A theme from its fields (a parsed note's, or theme_save's): every value checked."""
    if not ID.fullmatch(theme_id) or len(theme_id) > MAX_ID:
        raise ThemeError(
            f"{theme_id!r} is not a theme id: th- then lower-case words joined by -"
        )
    name = _text(fields.get("name"), "name")
    if len(name) > MAX_NAME:
        raise ThemeError(f"name is over {MAX_NAME} characters")
    swatches = tuple(fields.get("palette") or ())
    if not swatches:
        raise ThemeError("the palette is empty")
    if len(swatches) > MAX_SWATCHES:
        raise ThemeError(f"{len(swatches)} palette entries; at most {MAX_SWATCHES}")
    roles = {s.role for s in swatches}
    if missing := [r for r in REQUIRED_ROLES if r not in roles]:
        raise ThemeError(f"the palette has no {', '.join(missing)} entry")
    if len({(s.role, s.block) for s in swatches}) != len(swatches):
        raise ThemeError("the palette lists a role and block twice")
    lists: dict[str, tuple[str, ...]] = {}
    for key in LIST_KEYS:
        values = tuple(_text(x, key) for x in fields.get(key) or ())
        cap = MAX_CHANGES if key == "change" else MAX_RULES
        if key == "change":
            values = values[-cap:]
        elif len(values) > cap:
            raise ThemeError(f"{len(values)} {key} lines; at most {cap}")
        lists[key] = values
    adapt = tuple(
        (_line_type(t, "adapt"), _text(r, f"adapt {t}"))
        for t, r in fields.get("adapt") or ()
    )
    if len(adapt) > MAX_RULES * 2:
        raise ThemeError(f"{len(adapt)} adapt lines; at most {MAX_RULES * 2}")
    fits = tuple(dict.fromkeys(_line_type(t, "fits") for t in fields.get("fits") or ()))
    return Theme(
        id=theme_id,
        name=name,
        v=v,
        palette=swatches,
        summary=_text(fields.get("summary", ""), "summary", empty=True),
        fits=fits,
        prompt=_text(fields.get("prompt", ""), "prompt", empty=True),
        adapt=adapt,
        source=source,
        **lists,
    )


def parse(text: str, theme_id: str, source: str = "vault") -> Theme:
    """A theme from a note's text (frontmatter and all); ThemeError says what is wrong."""
    try:
        note = schema.parse(text.removeprefix("﻿").replace("\r\n", "\n"))
    except ValueError as exc:
        raise ThemeError(f"{theme_id}: {exc}") from None
    if note.meta.get("t") != TYPE:
        raise ThemeError(f"{theme_id}: not a t: {TYPE} note")
    fields: dict[str, Any] = {k: [] for k in LIST_KEYS}
    fields.update(fits=[], adapt=[])
    for key, value in note.facts:
        if key not in KEYS:
            raise ThemeError(f"{theme_id}: key {key} isn't a theme key")
        if key in LIST_KEYS:
            fields[key].append(value)
        elif key == "fits":
            fields["fits"] += [t.strip() for t in value.split(",") if t.strip()]
        elif key == "adapt":
            kind, sep, rule = value.partition(":")
            if not sep:
                raise ThemeError(
                    f"{theme_id}: adapt is <line type>: <rule>, not {value!r}"
                )
            fields["adapt"].append((kind.strip(), rule.strip()))
        elif key in fields:
            raise ThemeError(f"{theme_id}: {key} is given twice")
        else:
            fields[key] = value
    tables = [t for t in note.tables if t and t[0] == PALETTE_HEADER]
    if len(tables) != 1 or len(note.tables) != 1:
        raise ThemeError(
            f"{theme_id}: a theme has one tsv table, its palette: {', '.join(PALETTE_HEADER)}"
        )
    fields["palette"] = [_swatch(*row) for row in tables[0][1:]]
    v = fields.get("v", "")
    if not (isinstance(v, str) and v.isascii() and v.isdigit() and 0 < int(v) < 100000):
        raise ThemeError(f"{theme_id}: v is the theme's version, a whole number from 1")
    try:
        return build(fields, theme_id, int(v), source)
    except ThemeError as exc:
        raise ThemeError(f"{theme_id}: {exc}") from None


def body(theme: Theme) -> str:
    """The note's body (after the frontmatter, which the vault writes) for ``theme``."""
    lines = [f"name: {theme.name}", f"v: {theme.v}"]
    if theme.summary:
        lines.append(f"summary: {theme.summary}")
    if theme.fits:
        lines.append(f"fits: {', '.join(theme.fits)}")
    if theme.prompt:
        lines.append(f"prompt: {theme.prompt}")
    lines += [f"change: {c}" for c in theme.change]
    for key in RULE_KEYS:
        lines += [f"{key}: {r}" for r in getattr(theme, key)]
    lines += [f"adapt: {t}: {r}" for t, r in theme.adapt]
    lines += [f"sample: {s}" for s in theme.sample]
    lines += [f"note: {n}" for n in theme.note]
    lines += ["", "```tsv", "\t".join(PALETTE_HEADER)]
    lines += [f"{s.role}\t{s.block}\t{s.note}" for s in theme.palette]
    lines.append("```")
    return "\n".join(lines) + "\n"


# --- validation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """A theme's palette against the block catalog: refusals (never saved with any) and
    warnings, each naming the entry."""

    run_key: str
    refused: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    names: Mapping[str, str] = field(default_factory=dict)  # block -> catalog name

    @property
    def ok(self) -> bool:
        return not self.refused


def check(conn: sqlite3.Connection, theme: Theme) -> Check:
    """Plan 7.10's palette rule on the catalog alone for every palette entry (D314).
    ``palette.CatalogError`` when the DB has no block catalog."""
    report = palette.check(conn, theme.entries(), None, max_types=None)
    refused: list[str] = []
    warnings: list[str] = []
    names: dict[str, str] = {}
    for v in report.verdicts:
        if v.name:
            names[v.entry.key] = v.name
        refused += [f"{v.entry.key}: {r}" for r in v.refused]
        # a block can serve two roles; "listed twice" is not news in a theme
        warnings += [f"{v.entry.key}: {w}" for w in v.warnings if w != "listed twice"]
    refused += report.problems
    if report.newer_run is not None:
        warnings.append(
            f"the newest export run {report.newer_run} has no block catalog; checked "
            f"against {report.run_key}"
        )
    return Check(report.run_key, tuple(dict.fromkeys(refused)), tuple(warnings), names)


# --- the store -----------------------------------------------------------------------------


def _version_rel(theme_id: str, v: int) -> str:
    return f"{VERSIONS}/{theme_id}--v{v}.md"


@dataclass(frozen=True)
class Saved:
    theme: Theme
    path: str  # the current version's note
    archived: str | None  # the earlier version's note, when this was a refinement
    check: Check


@dataclass
class Themes:
    """The starter themes and the live vault's (``vault``); ``template`` is the repo's
    vault-template, read first for the starters (None: the live vault's ``_gos/themes/``)."""

    vault: VaultIO
    template: Path | None = vault_deploy.DEFAULT_TEMPLATE

    def _shipped_dir(self) -> Path:
        if self.template is not None and (self.template / SHIPPED).is_dir():
            return self.template / SHIPPED
        return self.vault.root / SHIPPED

    def _read(self, path: Path, theme_id: str, source: str) -> Theme:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ThemeError(f"{theme_id} can't be read: {exc}") from None
        return parse(text, theme_id, source)

    def _files(self) -> dict[str, tuple[Path, str]]:
        found: dict[str, tuple[Path, str]] = {}
        for folder, source in (
            (self._shipped_dir(), "shipped"),
            (self.vault.root / OWN, "vault"),
        ):
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("th-*.md")):
                stem = path.stem
                if ID.fullmatch(stem) and path.is_file() and not path.is_symlink():
                    found[stem] = (path, source)  # the vault's replaces a starter
        return found

    def load(self) -> tuple[list[Theme], list[str]]:
        """Every theme's current version, by id, and the problems of those that don't load."""
        themes: list[Theme] = []
        problems: list[str] = []
        for theme_id, (path, source) in sorted(self._files().items()):
            try:
                themes.append(self._read(path, theme_id, source))
            except ThemeError as exc:
                problems.append(str(exc))
        return themes, problems

    def versions(self, theme_id: str) -> list[int]:
        """The kept earlier versions of ``theme_id``, oldest first."""
        folder = self.vault.root / VERSIONS
        if not folder.is_dir():
            return []
        found = []
        for path in folder.iterdir():
            m = _VERSION_NOTE.fullmatch(path.name)
            if m and m[1] == theme_id:
                found.append(int(m[2]))
        return sorted(found)

    def get(self, theme_id: str, v: int | None = None) -> Theme:
        """The current version of ``theme_id``, or its version ``v``."""
        files = self._files()
        if theme_id not in files:
            known = ", ".join(sorted(files)) or "none yet"
            raise ThemeError(f"no theme {theme_id!r}; the themes: {known}")
        path, source = files[theme_id]
        current = self._read(path, theme_id, source)
        if v is None or v == current.v:
            return current
        if v not in self.versions(theme_id):
            kept = ", ".join(str(n) for n in [*self.versions(theme_id), current.v])
            raise ThemeError(f"{theme_id} has no version {v} (versions: {kept})")
        return self._read(
            self.vault.root / _version_rel(theme_id, v), theme_id, "vault"
        )

    def save(
        self,
        conn: sqlite3.Connection,
        fields: Mapping[str, Any],
        theme_id: str | None = None,
    ) -> Saved:
        """Create a theme (no such id yet: ``name``, ``prompt`` and the palette needed) or
        refine one (a new version: ``change`` needed; fields left out keep their values, and
        the prompt stays the one it was made from).
        Every palette entry is checked against the block catalog first; any refusal and
        nothing is written. The earlier version is kept in ``themes/versions/``."""
        files = self._files()
        if theme_id is None:
            theme_id = slug(_text(fields.get("name"), "name"))
            if theme_id in files:
                raise ThemeError(
                    f"there is already a theme {theme_id}; to refine it, give its id and "
                    "what changes"
                )
        current = self.get(theme_id) if theme_id in files else None
        merged: dict[str, Any]
        if current is None:
            if not str(fields.get("prompt") or "").strip():
                raise ThemeError("a new theme needs the prompt it is made from")
            if fields.get("change"):
                raise ThemeError(f"there is no theme {theme_id} to change yet")
            merged = dict(fields)
            merged["change"] = []
            v = 1
        else:
            change = _text(fields.get("change", ""), "change", empty=True)
            if not change:
                raise ThemeError(
                    f"{theme_id} exists: a refinement says what changes (change)"
                )
            merged = _fields(current)
            # the prompt stays the one the theme was made from; change says what changed
            merged.update(
                {
                    k: val
                    for k, val in fields.items()
                    if val is not None and k not in ("prompt", "change")
                }
            )
            v = current.v + 1
            merged["change"] = [*current.change, f"v{v} {change}"]
        theme = build(merged, theme_id, v, "vault")
        checked = check(conn, theme)
        if not checked.ok:
            raise ThemeError(
                f"{theme_id} not saved: "
                + "; ".join(checked.refused[:6])
                + (
                    f"; and {len(checked.refused) - 6} more"
                    if len(checked.refused) > 6
                    else ""
                )
            )
        archived = None
        try:
            if current is not None:
                archived = _version_rel(theme_id, current.v)
                self.vault.write(archived, body(current), op=THEME)
            path = self.vault.write(f"{OWN}/{theme_id}.md", body(theme), op=THEME)
        except VaultError as exc:
            raise ThemeError(f"{theme_id} not saved: {exc}") from None
        return Saved(theme, path, archived, checked)


def _fields(theme: Theme) -> dict[str, Any]:
    return {
        "name": theme.name,
        "summary": theme.summary,
        "fits": list(theme.fits),
        "prompt": theme.prompt,
        "adapt": list(theme.adapt),
        "palette": list(theme.palette),
        **{k: list(getattr(theme, k)) for k in LIST_KEYS},
    }


def slug(name: str) -> str:
    """``th-`` and the name's words, lower case, joined by ``-`` (at most 8 words)."""
    words = re.findall(r"[a-z0-9]+", name.lower())[:8]
    if not words:
        raise ThemeError(f"{name!r} has no letters or digits to make an id from")
    out = "th-" + "-".join(words)
    return out[:MAX_ID].rstrip("-")


def swatches(rows: Iterable[Mapping[str, Any]]) -> list[Swatch]:
    """theme_save's palette rows (``role``, ``block``, ``note``) as swatches."""
    return [_swatch(r.get("role"), r.get("block"), r.get("note", "")) for r in rows]


# --- selection (D315) ----------------------------------------------------------------------

THEME_MODE, PROMPT_MODE, BLANK_MODE = "theme", "prompt", "blank"


@dataclass(frozen=True)
class Use:
    """A build that used a theme, and where (its site's centre)."""

    build: str
    theme: str
    dim: int
    x: int
    z: int


@dataclass(frozen=True)
class Brief:
    """What the architect starts a shell from: the mode, the theme (none for prompt only),
    Clayton's prompt, the line type's concept and the theme's adaptation for it, and why
    that theme."""

    mode: str
    line_type: str
    concept: str
    theme: Theme | None = None
    prompt: str = ""
    adaptation: str | None = None
    why: str = ""


def near(
    uses: Iterable[Use], dim: int, x: int, z: int, radius: int = NEAR_BLOCKS
) -> list[tuple[Use, float]]:
    """The uses in dimension ``dim`` within ``radius`` blocks of (x, z), nearest first."""
    found = [(u, math.hypot(u.x - x, u.z - z)) for u in uses if u.dim == dim]
    return sorted(
        ((u, d) for u, d in found if d <= radius), key=lambda p: (p[1], p[0].build)
    )


def select(
    themes: Sequence[Theme],
    *,
    theme_id: str | None = None,
    prompt: str | None = None,
    line_type: str = GENERAL,
    nearby: Sequence[tuple[Use, float]] = (),
) -> Brief:
    """The brief for one build (plan 7.10's three modes): ``theme_id`` (with ``prompt``
    if given), else ``prompt`` alone, else blank: the line type's concept with the theme
    most used nearby (most builds, then the nearest), else the first that fits the line
    type, else the first theme."""
    kind = _line_type(line_type, "line type")
    concept = LINE_TYPES[kind]
    words = _text(prompt or "", "prompt", empty=True)
    by_id = {t.id: t for t in themes}
    if theme_id is not None:
        if theme_id not in by_id:
            known = ", ".join(sorted(by_id)) or "none yet"
            raise ThemeError(f"no theme {theme_id!r}; the themes: {known}")
        t = by_id[theme_id]
        return Brief(THEME_MODE, kind, concept, t, words, t.adaptation(kind), "chosen")
    if words:
        return Brief(PROMPT_MODE, kind, concept, None, words, None, "prompt only")
    counts = Counter(u.theme for u, _ in nearby if u.theme in by_id)
    if counts:
        nearest = {}
        for u, d in nearby:
            nearest.setdefault(u.theme, d)
        best = min(counts, key=lambda tid: (-counts[tid], nearest[tid], tid))
        t = by_id[best]
        why = (
            f"used by {counts[best]} build{'s' if counts[best] != 1 else ''} within "
            f"{NEAR_BLOCKS} blocks, the nearest {round(nearest[best])} blocks away"
        )
        return Brief(BLANK_MODE, kind, concept, t, "", t.adaptation(kind), why)
    fitting = [t for t in themes if kind in t.fits]
    if fitting:
        t = fitting[0]
        return Brief(
            BLANK_MODE, kind, concept, t, "", t.adaptation(kind), f"fits {kind}"
        )
    if themes:
        t = themes[0]
        why = f"no theme fits {kind} and none is used nearby; the first theme"
        return Brief(BLANK_MODE, kind, concept, t, "", t.adaptation(kind), why)
    return Brief(BLANK_MODE, kind, concept, None, "", None, "no themes yet")


def record_use(home: Path, use: Use) -> None:
    """Note that a build used a theme (the shell designer calls it, WO-10.7): one JSON line
    in ``GOS_HOME/data/build/theme_uses.jsonl``; a build's newest line wins."""
    path = home / USES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(use.__dict__) + "\n")


def load_uses(home: Path) -> list[Use]:
    """Every build's newest use; unreadable lines are skipped."""
    path = home / USES_FILE
    by_build: dict[str, Use] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        try:
            raw = json.loads(line)
            use = Use(
                str(raw["build"]),
                str(raw["theme"]),
                int(raw["dim"]),
                int(raw["x"]),
                int(raw["z"]),
            )
        except (ValueError, KeyError, TypeError):
            continue
        by_build[use.build] = use
    return list(by_build.values())
