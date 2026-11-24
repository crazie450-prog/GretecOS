"""Shell builds: make, save, list, rebuild and export (WO-10.7, plan 7.10, D316-D318).

:func:`make` is the one path every caller takes (the architect's ``build_request`` tool, ``gos
shell design`` and the dashboard's No-AI button): it lays the plans out (WO-10.5), picks the theme
(WO-10.6's three modes: a theme with an optional prompt, a prompt only with the caller's own
palette, or blank: the theme most used nearby, else one that fits the line type), checks the
palette against the block catalog, compiles the design (the model's operations, or the procedural
generator's when none is given), and inspects, repairs or refuses it (:mod:`gregtec.build.shell`).
Only an accepted or repaired design is saved; a refused one writes nothing.

A saved build is ``GOS_HOME/data/build/shells/<sh-id>/``:

- ``design.json``: what it was made from (layout id, plans, theme, prompt, palette, options, the
  operations), its status, repairs, notes and counts;
- ``layout.json``: the layout it was designed against, so an export re-checks the same volumes;
- ``palette.tsv``: the palette manifest (role, block, name, count);
- ``preview.json``: the preview mesh for the dashboard (:func:`gregtec.build.shell.mesh`);
- ``notes.txt``: the notes and the paste steps.

:func:`export` (``gos shell export``, Clayton's) refuses when the plans' layout today is not the
saved one (:func:`check_layout`), rebuilds the design from those files, inspects it again, applies the palette rule against the catalog **and the Lab world's registry**
(:func:`gregtec.build.schematic.write_shell`) and writes ``GOS_HOME/data/schematics/shells/
<sh-id>.schematic`` (parts ``-part-N`` above 255 block types). GOS never writes into the Lab or
Play: Clayton copies the file and pastes it in the Lab (runbook ``shells_lab.md``, L6).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gregtec.build import layout as layout_lib
from gregtec.build import palette, sandbox, schematic, shell
from gregtec.build import themes as theme_lib
from gregtec.build.worldreg import WorldRegistry
from gregtec.ingest.fingerprint import atomic_write
from gregtec.planner import progress
from gregtec.planner.outputs import store as plan_store
from gregtec.vault.io import VaultIO

if TYPE_CHECKING:
    from gregtec.build import fit as fit_lib

ROOT = Path("data", "build", "shells")
SCHEMATICS = Path("data", "schematics", "shells")
BUILD_ID = re.compile(r"sh-[0-9a-f]{12}")
FILES = ("design.json", "layout.json", "palette.tsv", "preview.json", "notes.txt")
# a site build (D422) also keeps its machine program, so it is fitted again without the plans
SITE_FILES = (*FILES, "program.json")
MODES = ("layout", "site")
MAX_PLANS = shell.MAX_PLANS
MAX_CARD_FACES = 6000  # the shell card's limit (core/gregtec/cards/schema.json)
MAX_CARD_PALETTE = 64


def folder(home: Path, build: str) -> Path:
    if not BUILD_ID.fullmatch(build):
        raise shell.ShellError(f"{build!r} is not a shell build id (sh-<12 hex>)")
    return home / ROOT / build


@dataclass(frozen=True)
class Site:
    """Where the build stands (its centre), for blank mode's "themes used nearby"."""

    dim: int
    x: int
    z: int


@dataclass(frozen=True)
class Made:
    spec: shell.Spec
    ops: tuple[dict[str, Any], ...]
    outcome: shell.Outcome
    brief: theme_lib.Brief
    build: str  # the id it has (or would have, refused)
    saved: Path | None  # its folder; None when refused
    # a site build (D422): its site, need, entrance, zones and the fit's stats
    fit: dict[str, Any] | None = None

    @property
    def record(self) -> dict[str, Any]:
        return record(
            self.spec, self.ops, self.outcome, self.brief, self.build, self.fit
        )


def site_mode(rec: Mapping[str, Any]) -> bool:
    """Whether a saved build is a site build (D422; a build without ``fit`` is layout-first)."""
    got = rec.get("fit")
    return isinstance(got, Mapping) and got.get("mode") == "site"


def _theme_store(vault: VaultIO | None) -> theme_lib.Themes | None:
    return None if vault is None else theme_lib.Themes(vault)


def make(
    conn: sqlite3.Connection,
    home: Path,
    vault: VaultIO | None,
    plan_ids: Sequence[str],
    *,
    theme_id: str | None = None,
    prompt: str = "",
    line_type: str = theme_lib.GENERAL,
    extra: Sequence[tuple[str, str]] = (),
    exclude: Sequence[str] = (),
    max_blocks: int | None = None,
    headroom: int | None = None,
    ops: Sequence[Mapping[str, Any]] | None = None,
    relief: int | None = None,
    layout_options: layout_lib.Options | None = None,
    site: Site | None = None,
    save_it: bool = True,
    mode: str = "layout",
    entrance: Mapping[str, Any] | None = None,
    zones: Sequence[Mapping[str, Any]] = (),
) -> Made:
    """Design a shell around the plans' layout and save it unless refused (see the module).
    Under a shell job (WO-10.10) each step is a stage and a cancel point, all before saving.

    ``mode="site"`` (D422) designs the building first inside a SITE sized from the plans'
    machines and fits the machines into it afterwards (:func:`_make_site`); ``entrance`` and
    ``zones`` are the answer's optional door hint and steering boxes."""
    progress.stage("layout")
    if mode not in MODES:
        raise shell.ShellError(f"mode {mode!r} is not one of {', '.join(MODES)}")
    if mode == "layout" and (entrance is not None or zones):
        raise shell.ShellError("an entrance or zones are for a site build")
    if not plan_ids or len(plan_ids) > MAX_PLANS:
        raise shell.ShellError(f"name 1 to {MAX_PLANS} saved plans")
    plans = []
    for pid in plan_ids:
        try:
            plans.append(plan_store.load(home, pid))
        except (OSError, ValueError) as exc:
            raise shell.ShellError(f"plan {pid} isn't saved: {exc}") from None
    prog: layout_lib.Program | None = None
    lay: dict[str, Any] = {}
    try:
        if mode == "site":
            prog = layout_lib.program(conn, plans)
        else:
            lay = layout_lib.plan_layout(conn, plans, layout_options)
    except layout_lib.LayoutError as exc:
        raise shell.ShellError(f"no layout: {exc}") from None
    if (not prog.instances) if prog is not None else not lay["placements"]:
        raise shell.ShellError("the layout places no machine: nothing to house")
    store = _theme_store(vault)
    found: list[theme_lib.Theme] = []
    if store is not None:
        found, _ = store.load()
    nearby = (
        theme_lib.near(theme_lib.load_uses(home), site.dim, site.x, site.z)
        if site is not None
        else []
    )
    try:
        brief = theme_lib.select(
            found,
            theme_id=theme_id,
            prompt=prompt or None,
            line_type=line_type,
            nearby=nearby,
        )
    except theme_lib.ThemeError as exc:
        raise shell.ShellError(str(exc)) from None
    if brief.theme is None and not extra:
        raise shell.ShellError(
            "no theme for this build: name one (theme_list), or give the palette entries "
            "(role and block, from block_find) for a prompt-only shell"
        )
    swatches = shell.swatches_of(brief.theme, extra, exclude)
    progress.stage("palette")
    try:
        shell.check_palette(conn, swatches)
    except palette.CatalogError as exc:
        raise shell.ShellError(str(exc)) from None
    if prog is not None:
        return _make_site(
            conn,
            home,
            prog,
            swatches,
            brief,
            max_blocks=max_blocks,
            headroom=headroom,
            ops=ops,
            relief=relief,
            opts=layout_options or layout_lib.Options(),
            site=site,
            save_it=save_it,
            entrance=entrance,
            zones=zones,
        )
    spec = shell.spec_from_layout(
        lay,
        swatches,
        theme=brief.theme,
        prompt=brief.prompt,
        line_type=brief.line_type,
        max_blocks=max_blocks,
        headroom=headroom,
        relief=relief,
    )
    source = "procedural" if ops is None else "architect"
    progress.stage("compose")
    design_ops = tuple(
        dict(o)
        for o in (shell.procedural(spec) if ops is None else ops)
        if isinstance(o, Mapping)
    )
    if ops is not None and len(design_ops) != len(ops):
        outcome = shell.Outcome(
            "refused", source, {}, {}, ("every operation is an object",)
        )
    else:
        progress.stage("gate")
        outcome = shell.design(conn, spec, design_ops, source)
    build = shell.build_id(spec, design_ops)
    made = Made(spec, design_ops, outcome, brief, build, None)
    if not outcome.ok or not save_it:
        return made
    progress.stage("saving")
    path = save(home, made, lay)
    if site is not None and brief.theme is not None:
        theme_lib.record_use(
            home, theme_lib.Use(build, brief.theme.id, site.dim, site.x, site.z)
        )
    return Made(spec, design_ops, outcome, brief, build, path)


def _make_site(
    conn: sqlite3.Connection,
    home: Path,
    prog: layout_lib.Program,
    swatches: Sequence[shell.Swatch],
    brief: theme_lib.Brief,
    *,
    max_blocks: int | None,
    headroom: int | None,
    ops: Sequence[Mapping[str, Any]] | None,
    relief: int | None,
    opts: layout_lib.Options,
    site: Site | None,
    save_it: bool,
    entrance: Mapping[str, Any] | None,
    zones: Sequence[Mapping[str, Any]],
) -> Made:
    """:func:`make`'s site branch (D422, design §2.6): the SITE and its spec, the design
    compiled once, the palette and size gate, the fit, then the gate on the fitted volumes."""
    from gregtec.build import fit as fit_lib

    box = shell.site_of(prog, opts, headroom)
    spec = shell.spec_for_site(
        prog,
        box,
        swatches,
        opts,
        theme=brief.theme,
        prompt=brief.prompt,
        line_type=brief.line_type,
        max_blocks=max_blocks,
        headroom=headroom,
        relief=relief,
    )
    source = "procedural" if ops is None else "architect"
    progress.stage("compose")
    design_ops = tuple(
        dict(o)
        for o in (shell.procedural(spec, fit=True) if ops is None else ops)
        if isinstance(o, Mapping)
    )
    program = prog.to_json(opts)
    hint = None if entrance is None else dict(entrance)
    zone_list = [dict(z) for z in zones]
    build = shell.build_id(spec, design_ops, program, hint, zone_list)
    info: dict[str, Any] = {
        "mode": "site",
        "site": box.to_json(),
        "need": dict(spec.need or {}),
        "entrance": hint,
        "zones": zone_list,
        "buffer": opts.buffer,
    }

    def refused(*why: str) -> Made:
        out = shell.Outcome("refused", source, {}, {}, tuple(why))
        return Made(spec, design_ops, out, brief, build, None, info)

    if ops is not None and len(design_ops) != len(ops):
        return refused("every operation is an object")
    progress.stage("gate")
    try:
        got = shell.compile_ops(spec, design_ops)
    except shell.ShellError as exc:
        return refused(str(exc))
    # the palette, the share outside the site and max_blocks first, as for any design
    first = shell.design(conn, spec, design_ops, source, compiled=got)
    if not first.ok:
        return Made(spec, design_ops, first, brief, build, None, info)
    progress.stage("fit")
    res = fit_lib.fit(prog, first.cells, box, opts, hint, zone_list)
    info["fit"] = res.stats
    if not res.ok or res.layout is None:
        return refused(*res.reasons)
    lay = res.layout
    fitted = shell.fitted_spec(spec, lay)
    outcome = shell.design(conn, fitted, design_ops, source, compiled=got)
    made = Made(fitted, design_ops, outcome, brief, build, None, info)
    if not outcome.ok or not save_it:
        return made
    progress.stage("saving")
    path = save(home, made, lay, program)
    if site is not None and brief.theme is not None:
        theme_lib.record_use(
            home, theme_lib.Use(build, brief.theme.id, site.dim, site.x, site.z)
        )
    return replace(made, saved=path)


def fit_line(fit: Mapping[str, Any]) -> str | None:
    """The accepted site build's note: how its machines fit (None before the fit)."""
    st = fit.get("fit") or {}
    if "spare_pct" not in st:
        return None
    return (
        f"{st['placed']} machines fitted in the building (pass {st['pass']}), interior "
        f"{st['volume']} of {st['need']['required']} needed (+{fit.get('buffer')}%), "
        f"{st['spare_pct']}% spare, entrance {st['entrance_from']}"
    )


def notes(
    spec: shell.Spec,
    outcome: shell.Outcome,
    brief: theme_lib.Brief,
    fit: Mapping[str, Any] | None = None,
) -> list[str]:
    out = []
    if brief.theme is not None:
        out.append(
            f"theme {brief.theme.name} ({spec.theme}): {brief.mode} mode, {brief.why}"
        )
    elif spec.theme:  # a revision that kept its version's theme and palette (WO-10.10)
        out.append(f"theme {spec.theme_name} ({spec.theme}): kept from {brief.why}")
    else:
        out.append("prompt only: the palette given with the request")
    out.append(f"concept for {brief.line_type}: {brief.concept}")
    if brief.adaptation:
        out.append(f"the theme's adaptation for {brief.line_type}: {brief.adaptation}")
    if spec.prompt:
        out.append(f"prompt: {spec.prompt}")
    out += [f"repaired: {r}" for r in outcome.repairs]
    if outcome.report is not None:
        out += [
            f"palette warning: {v.entry.key}: {w}"
            for v in outcome.report.verdicts
            for w in v.warnings
        ]
    if fit is not None and (line := fit_line(fit)) is not None:
        out.append(line)
        out.append(
            "no tile entity, no GT machine block, nothing in a fitted keep-out, corridor, "
            f"chase or doorway (checked: {len(spec.clear)} volumes)"
        )
        return out
    out.append(
        "no tile entity, no GT machine block, nothing in a keep-out, corridor, chase, doorway or "
        f"the layout's interior below the roof (checked: {len(spec.clear)} volumes)"
    )
    return out


def record(
    spec: shell.Spec,
    ops: Sequence[Mapping[str, Any]],
    outcome: shell.Outcome,
    brief: theme_lib.Brief,
    build: str,
    fit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    mats = shell.manifest(outcome)
    extra = {} if fit is None else {"fit": dict(fit)}
    return {
        "schema": shell.SCHEMA,
        "v": shell.VERSION,
        "id": build,
        "status": outcome.status,
        "source": outcome.source,
        "mode": brief.mode,
        "theme_name": spec.theme_name,
        **shell.spec_json(spec),
        "ops": list(ops),
        "blocks": len(outcome.cells),
        "types": len(mats),
        "repairs": list(outcome.repairs),
        "reasons": list(outcome.reasons),
        "catalog": None if outcome.report is None else outcome.report.run_key,
        "materials": [
            {"role": m.role, "block": m.entry.key, "name": m.name, "count": m.count}
            for m in mats
        ],
        "notes": notes(spec, outcome, brief, fit),
        **extra,
    }


def preview(spec: shell.Spec, outcome: shell.Outcome) -> dict[str, Any]:
    order = [m.entry for m in shell.manifest(outcome)]
    return {
        "size": list(spec.bounds.size),
        "origin": list(spec.bounds.lo),
        "palette": [e.key for e in order],
        "faces": shell.mesh(outcome.cells, order, spec.bounds.lo),
    }


def paste_steps(rec: Mapping[str, Any]) -> tuple[str, ...]:
    """A saved build's Lab-only paste steps, naming the cell its WEOffset is relative to."""
    return shell.paste_steps(rec["origin"])


def _json(data: object) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def save(
    home: Path,
    made: Made,
    lay: Mapping[str, Any],
    program: Mapping[str, Any] | None = None,
) -> Path:
    """Write the build's files (atomically, each); a refused outcome is never saved. A site
    build (D422) also writes its ``program.json``."""
    if not made.outcome.ok:
        raise shell.ShellError("a refused design is never written")
    rec = made.record
    where = folder(home, made.build)
    if program is not None:
        atomic_write(where / "program.json", _json(program))
    rows = ["role\tblock\tname\tcount"] + [
        f"{m['role']}\t{m['block']}\t{m['name'] or ''}\t{m['count']}"
        for m in rec["materials"]
    ]
    atomic_write(where / "layout.json", _json(lay))
    atomic_write(where / "palette.tsv", ("\n".join(rows) + "\n").encode("utf-8"))
    atomic_write(where / "preview.json", _json(preview(made.spec, made.outcome)))
    text = "\n".join([*rec["notes"], "", "Paste (Lab only):", *paste_steps(rec)]) + "\n"
    atomic_write(where / "notes.txt", text.encode("utf-8"))
    atomic_write(
        where / "design.json", _json(rec)
    )  # last: its presence marks a whole build
    return where


def load(home: Path, build: str) -> dict[str, Any]:
    path = folder(home, build) / "design.json"
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise shell.ShellError(f"no shell build {build}") from None
    except (OSError, ValueError) as exc:
        raise shell.ShellError(f"build {build} can't be read: {exc}") from None
    if (
        not isinstance(rec, dict)
        or rec.get("id") != build
        or rec.get("schema") != shell.SCHEMA
    ):
        raise shell.ShellError(
            f"build {build}'s design.json is not a {shell.SCHEMA} record"
        )
    return rec


def builds(home: Path) -> list[dict[str, Any]]:
    """Every saved build's summary, the newest first (by when its design.json was written)."""
    root = home / ROOT
    found = []
    if root.is_dir():
        for d in root.iterdir():
            if not BUILD_ID.fullmatch(d.name) or not (d / "design.json").is_file():
                continue
            try:
                rec = load(home, d.name)
            except shell.ShellError:
                continue
            found.append(((d / "design.json").stat().st_mtime_ns, rec))
    found.sort(key=lambda p: (-p[0], p[1]["id"]))
    return [summary(rec) for _, rec in found]


def summary(rec: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": rec["id"],
        "status": rec["status"],
        "source": rec["source"],
        "theme": rec.get("theme"),
        "theme_name": rec.get("theme_name"),
        "plans": rec.get("plans", []),
        "layout": rec.get("layout"),
        "size": rec["bounds"]["size"],
        "blocks": rec["blocks"],
    }


def rebuild(
    conn: sqlite3.Connection, home: Path, build: str
) -> tuple[shell.Spec, shell.Outcome]:
    """The saved build designed again from its own files, through the same gate; refused if it
    no longer passes (the catalog changed) or doesn't give the saved blocks."""
    rec = load(home, build)
    try:
        lay = json.loads(
            (folder(home, build) / "layout.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise shell.ShellError(
            f"build {build}'s layout.json can't be read: {exc}"
        ) from None
    swatches = _swatches(rec)
    shell.check_palette(conn, swatches)
    if site_mode(rec):
        spec, got = _site_cells(home, build, rec, lay, swatches)
        outcome = shell.design(
            conn, spec, rec["ops"], rec["source"], sandbox.REPLAY_TIMEOUT_S, got
        )
        return _same_blocks(build, rec, spec, outcome)
    spec = shell.spec_from_layout(
        lay,
        swatches,
        prompt=rec.get("prompt", ""),
        line_type=rec.get("line_type", theme_lib.GENERAL),
        max_blocks=rec["max_blocks"],
        headroom=rec["headroom"],
        relief=rec.get("relief", 0),  # a build from before D386 had none
        interior_open=rec.get("interior_open", False),  # nor before D389
    )
    spec = replace(spec, theme=rec.get("theme"), theme_name=rec.get("theme_name"))
    outcome = shell.design(
        conn, spec, rec["ops"], rec["source"], sandbox.REPLAY_TIMEOUT_S
    )
    return _same_blocks(build, rec, spec, outcome)


def _same_blocks(
    build: str, rec: Mapping[str, Any], spec: shell.Spec, outcome: shell.Outcome
) -> tuple[shell.Spec, shell.Outcome]:
    if not outcome.ok:
        raise shell.ShellError(
            f"build {build} no longer passes: " + " | ".join(outcome.reasons)
        )
    if len(outcome.cells) != rec["blocks"]:
        raise shell.ShellError(
            f"build {build} gives {len(outcome.cells)} blocks, not the {rec['blocks']} saved"
        )
    return spec, outcome


def cells_of(
    home: Path, build: str
) -> tuple[shell.Spec, dict[shell.Cell, palette.Entry]]:
    """The saved build's cells from its own files alone, no DB (the 3D viewer's, WO-10.10): its
    operations compiled against its saved layout and palette, less what the gate removed (cells
    outside the bounding volume or in a volume left empty). The same cells the gate saved:
    refused when the count differs from the saved one (a changed file). The palette rule is
    not run here (nothing is written): :func:`rebuild` and the export run it."""
    rec = load(home, build)
    try:
        lay = json.loads(
            (folder(home, build) / "layout.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise shell.ShellError(
            f"build {build}'s layout.json can't be read: {exc}"
        ) from None
    swatches = _swatches(rec)
    if site_mode(rec):
        spec, got = _site_cells(home, build, rec, lay, swatches)
    else:
        spec = shell.spec_from_layout(
            lay,
            swatches,
            prompt=rec.get("prompt", ""),
            line_type=rec.get("line_type", theme_lib.GENERAL),
            max_blocks=rec["max_blocks"],
            headroom=rec["headroom"],
            relief=rec.get("relief", 0),  # a build from before D386 had none
            interior_open=rec.get("interior_open", False),  # nor before D389
        )
        got = shell.compile_ops(spec, rec["ops"], sandbox.REPLAY_TIMEOUT_S)
    vols = shell._index(spec)
    cells = {
        c: e
        for c, e in got.cells.items()
        if spec.bounds.contains(c) and shell.breach_of(spec, c, vols) is None
    }
    if len(cells) != rec["blocks"]:
        raise shell.ShellError(
            f"build {build} gives {len(cells)} blocks, not the {rec['blocks']} saved"
        )
    return spec, cells


def _swatches(rec: Mapping[str, Any]) -> list[shell.Swatch]:
    return [
        shell.Swatch(r["role"], palette.parse_entry(r["block"]), r.get("note", ""))
        for r in rec["palette"]
    ]


def _program(
    home: Path, build: str
) -> tuple[dict[str, Any], layout_lib.Program, layout_lib.Options]:
    """A site build's saved program.json, as saved and read back (D422)."""
    try:
        saved = json.loads(
            (folder(home, build) / "program.json").read_text(encoding="utf-8")
        )
        prog, opts = layout_lib.Program.from_json(saved)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise shell.ShellError(
            f"build {build}'s program.json can't be read: {exc}"
        ) from None
    return saved, prog, opts


def _site_spec(
    home: Path, build: str, rec: Mapping[str, Any], swatches: Sequence[shell.Swatch]
) -> tuple[shell.Spec, layout_lib.Program, layout_lib.Options]:
    """A site build's design-time spec from its own files (KEEP_CLEAR [] and its NEED: the
    facts its script was run with), never the fitted one (design §2.7)."""
    _, prog, opts = _program(home, build)
    spec = shell.spec_for_site(
        prog,
        layout_lib.box_of(rec["fit"]["site"]),
        swatches,
        opts,
        prompt=rec.get("prompt", ""),
        line_type=rec.get("line_type", theme_lib.GENERAL),
        max_blocks=rec["max_blocks"],
        headroom=rec["headroom"],
        relief=rec.get("relief", 0),
    )
    return (
        replace(spec, theme=rec.get("theme"), theme_name=rec.get("theme_name")),
        prog,
        opts,
    )


def _site_cells(
    home: Path,
    build: str,
    rec: Mapping[str, Any],
    lay: Mapping[str, Any],
    swatches: Sequence[shell.Swatch],
) -> tuple[shell.Spec, shell.Compiled]:
    """A site build's operations compiled once with its design-time spec, its machines fitted
    again from its saved program and the saved layout required back; the fitted spec (the
    saved layout's volumes) and the compiled cells to gate against it. The fit runs as it did
    at design time (no prior: a prior pass names itself in the layout's ``fit``, so its id
    would never be the saved one); fit is deterministic, so the same files give the same id."""
    spec, prog, opts = _site_spec(home, build, rec, swatches)
    try:
        got = shell.compile_ops(spec, rec["ops"], sandbox.REPLAY_TIMEOUT_S)
    except shell.ShellError as exc:
        raise shell.ShellError(f"build {build} no longer passes: {exc}") from None
    res = _refit(prog, opts, spec, got, rec, None)
    if res.layout is None:
        raise shell.ShellError(
            f"build {build} no longer fits its machines: " + " | ".join(res.reasons)
        )
    if res.layout["id"] != lay.get("id"):
        raise shell.ShellError(
            f"build {build} fits its machines as {res.layout['id']} now, not as the saved "
            f"{lay.get('id')}"
        )
    return shell.fitted_spec(spec, lay), got


def _refit(
    prog: layout_lib.Program,
    opts: layout_lib.Options,
    spec: shell.Spec,
    got: shell.Compiled,
    rec: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
) -> fit_lib.FitResult:
    from gregtec.build import fit as fit_lib  # late: layout's import chain reaches here

    assert spec.site is not None
    info = rec["fit"]
    return fit_lib.fit(
        prog,
        got.cells,
        spec.site,
        opts,
        info.get("entrance"),
        info.get("zones") or (),
        prior=prior,
    )


def _saved_options(home: Path, rec: Mapping[str, Any]) -> dict[str, int]:
    """The layout options a build was laid out with (its layout.json; none: the defaults)."""
    try:
        lay = json.loads(
            (folder(home, str(rec["id"])) / "layout.json").read_text(encoding="utf-8")
        )
        got = lay.get("options") or {}
    except (OSError, ValueError, KeyError):
        return {}
    return {k: int(v) for k, v in got.items() if k in layout_lib.LIMITS}


def check_layout(conn: sqlite3.Connection, home: Path, rec: Mapping[str, Any]) -> None:
    """The layout of the build's plans laid out again now must be the saved one (its id hashes
    the geometry, WO-10.5): after a re-solve or new footprint data its keep-outs and corridors
    moved, and the shell would stand in them."""
    redo = "design the shell again (gos shell design, or the Designer's button)"
    plans = []
    for pid in rec.get("plans") or []:
        try:
            plans.append(plan_store.load(home, pid))
        except (OSError, ValueError):
            raise shell.ShellError(f"plan {pid} isn't saved any more: {redo}") from None
    if site_mode(rec):
        _check_site(conn, home, rec, plans)
        return
    try:
        # laid out with the options it was saved with (D389: spacing, band_gap, max_run)
        opts = layout_lib.Options(**_saved_options(home, rec))
        now = layout_lib.plan_layout(conn, plans, opts)["id"] if plans else None
    except layout_lib.LayoutError as exc:
        raise shell.ShellError(
            f"the plans can't be laid out now ({exc}): {redo}"
        ) from None
    if now != rec.get("layout"):
        raise shell.ShellError(
            f"the layout changed since this shell was designed (it was {rec.get('layout')}, "
            f"now {now}): {redo}"
        )


def _check_site(
    conn: sqlite3.Connection,
    home: Path,
    rec: Mapping[str, Any],
    plans: Sequence[Mapping[str, Any]],
) -> None:
    """:func:`check_layout` for a site build (D422, design §2.7): today's machines (the plans'
    footprints now) fitted again into the saved building as at design time. The same layout:
    export. Otherwise refused (export never changes a build): with the refit message when
    they still fit (from scratch, or with the saved layout as the prior: a prior pass can
    fail where a fresh one fits), else with the fit's reasons."""
    build = str(rec["id"])
    try:
        lay = json.loads(
            (folder(home, build) / "layout.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise shell.ShellError(
            f"build {build}'s layout.json can't be read: {exc}"
        ) from None
    try:
        now = layout_lib.program(conn, plans)
    except layout_lib.LayoutError as exc:
        raise shell.ShellError(
            f"the plans' machines can't be read now ({exc}): revise the building"
        ) from None
    spec, _, opts = _site_spec(home, build, rec, _swatches(rec))
    try:
        got = shell.compile_ops(spec, rec["ops"], sandbox.REPLAY_TIMEOUT_S)
    except shell.ShellError as exc:
        raise shell.ShellError(f"build {build} no longer passes: {exc}") from None
    res = _refit(now, opts, spec, got, rec, None)
    if res.layout == lay:
        return
    # changed: do they still fit, from scratch or the machines that can staying put?
    if res.layout is None and _refit(now, opts, spec, got, rec, lay).layout is None:
        raise shell.ShellError(
            "the machines changed since this shell was designed and no longer fit the "
            "building: " + " | ".join(res.reasons) + ": revise the building"
        )
    raise shell.ShellError(
        "the machines changed since this shell was designed; the building still holds "
        f"them: `gos shell refit {build}` makes a version with them fitted again"
    )


@dataclass(frozen=True)
class Exported:
    paths: tuple[Path, ...]
    written: schematic.Written


def export(
    conn: sqlite3.Connection,
    home: Path,
    build: str,
    world: WorldRegistry,
    over: schematic.Over = "split",
    out: Path | None = None,
) -> Exported:
    """The build's ``.schematic`` file(s) after every check again and the palette rule against
    the Lab world's registry (:class:`gregtec.build.schematic.ShellRefused` when refused).
    Refused (:class:`~gregtec.build.shell.ShellError`) when the plans' layout today is not the
    one the shell was designed against: the 3D guide and the floor plan show today's."""
    check_layout(conn, home, load(home, build))
    spec, outcome = rebuild(conn, home, build)
    written = schematic.write_shell(
        conn, world, shell.to_schematic(spec, outcome.cells), over
    )
    target = out or home / SCHEMATICS / f"{build}.schematic"
    paths = tuple(
        target
        if not part.name
        else target.with_name(f"{target.stem}-{part.name}.schematic")
        for part in written.parts
    )
    return Exported(paths, written)


def write(exported: Exported) -> None:
    for part, path in zip(exported.written.parts, exported.paths, strict=True):
        atomic_write(path, part.data)


# --- the dashboard's card ------------------------------------------------------------------


def card(home: Path, build: str) -> dict[str, Any]:
    """The shell card (plan 7.14: data, never HTML): status, theme, size, the palette manifest,
    the preview mesh's faces (at most :data:`MAX_CARD_FACES`) and the paste steps."""
    from gregtec.cards import checked

    rec = load(home, build)
    try:
        prev = json.loads(
            (folder(home, build) / "preview.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        prev = {"faces": []}
    faces = prev.get("faces", [])
    notes = [layout_lib.clip(n, 2000) for n in rec["notes"]][: layout_lib.MAX_NOTES - 2]
    if len(faces) > MAX_CARD_FACES:
        notes.append(
            f"the preview has {len(faces)} faces, more than the card draws ({MAX_CARD_FACES}): "
            "only the roof and the outer walls are shown"
        )
        faces = _outer(faces, rec["bounds"]["size"])[:MAX_CARD_FACES]
    sx, sy, sz = rec["bounds"]["size"]
    plans = rec.get("plans") or []
    title = f"Shell {build}" + (
        f" ({rec['theme_name']})" if rec.get("theme_name") else ""
    )
    out = {
        "type": "shell",
        "title": layout_lib.clip(title, 300),
        "ref": f"plan:{plans[0]}" if plans else None,
        "build": build,
        "layout": rec["layout"],
        "status": rec["status"],
        "source": rec["source"],
        "theme": rec.get("theme"),
        "size": {"x": sx, "y": sy, "z": sz},
        "blocks": rec["blocks"],
        "max_blocks": rec["max_blocks"],
        "palette": [
            {
                "role": m["role"],
                "block": m["block"],
                "name": m["name"],
                "count": m["count"],
            }
            for m in rec["materials"][:MAX_CARD_PALETTE]
        ],
        "faces": faces,
        "paste": [*paste_steps(rec), f"Export first: gos shell export {build}"],
        "notes": notes,
    }
    return checked(out)


def _outer(faces: list[list[int]], size: Sequence[int]) -> list[list[int]]:
    """The faces on the outside of the bounding volume's walls and every upward face."""
    sx, _, sz = size
    keep = []
    for f in faces:
        d, x, _, z = f[0], f[1], f[2], f[3]
        if (
            d == 2
            or (d == 0 and x == sx - 1)
            or (d == 1 and x == 0)
            or (d == 4 and z == sz - 1)
            or (d == 5 and z == 0)
        ):
            keep.append(f)
    return keep
