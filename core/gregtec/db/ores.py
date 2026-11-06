"""``gos ores``: GT's ore mixes and a world's veins in ``GOS_HOME/data/gos.sqlite`` (WO-1.6).

- ``gos ores load`` replaces ``vein``, and ``ore_mix`` when the run has mixes, in one
  transaction (D128), so a load that fails changes nothing:
  - **Mixes** from a collected export run's ``gt_ore_mixes.json`` (default: the newest run in
    ``GOS_HOME/data/exports/``, checked against its manifest), which GregtecLink writes from
    GT's worldgen registry (D126, :mod:`gregtec.ingest.ore_mixes`). When the newest run has none
    (a GregtecLink from before WO-1.6, or a failed pass) or there is no collected run, the load
    says so, keeps the mixes an earlier load left and still loads the veins (D128); a run named
    with ``--run`` must have them. ``ore_mix.dims`` is JSON:
    ``{dimension name: {"ids": [dimension IDs in that instance], "ymin"?, "ymax"?}}``, the
    heights only where the mix overrides them; ``ore_mix.ores`` is JSON, the four layers in
    order: ``[{"layer", "material", "name", "item"?, "meta"?}]``.
  - **Veins** from one world's VisualProspecting caches (D127, :mod:`gregtec.ingest.vp`),
    read-only: by default the Lab world ``[paths] lab_world`` names, never Play's unless asked
    (``--instance play``); ``--world-dir`` for any world folder. ``vein.known`` is 1 when the
    tracked player's client cache has the vein, ``depleted`` when he marked it, ``src``
    ``<cache>/<VP source>`` (``server/generated``). The server cache (every generated vein,
    D27) is read unless ``[policy] ore_server_cache = false``.
  - ``kv`` ``ores.mixes`` and ``ores.veins`` say where each came from.
- ``gos ores list`` prints veins per dimension; ``--dim N`` lists one dimension's veins (named
  query ``ore_veins``).
- :func:`find` is ``ore_find(material, dim?, near?)`` (plan section 7.6) over the named query
  ``ore_find``; WO-2.2 wraps it as a tool.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.db import access
from gregtec.db import build as recipe_db
from gregtec.db import quests as quest_db
from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest import collect, ore_mixes, vp
from gregtec.ingest.normalize import canonical
from gregtec.sync import _is_link

KV_MIXES, KV_VEINS = "ores.mixes", "ores.veins"
PART = "ore_mixes"  # manifest parts.ore_mixes
PLAY_READ_NOTE = (
    "Reading Play's world (read-only). VisualProspecting saves its server cache on every"
    " autosave and the client cache when you leave the world, so the newest veins show after"
    " the next save."
)


class OresError(RuntimeError):
    """Refused or failed; the DB is as it was."""


class NoMixes(OresError):
    """The run has no ore mixes (or there is no collected run); veins can still load."""


@dataclass
class Veins:
    located: vp.Located
    server: vp.Cache | None
    client: vp.Cache | None
    merged: list[vp.Merged]
    differ: int  # chunks the two caches give different mixes for
    server_skipped: bool = False  # [policy] ore_server_cache = false


@dataclass
class Summary:
    run_id: str | None  # None: no mixes loaded this time
    mixes: ore_mixes.OreMixes | None
    veins: Veins
    unknown: dict[str, int] = field(
        default_factory=dict
    )  # vein mix not among the mixes
    kept_mixes: int = 0  # with no mixes loaded: how many an earlier load left
    no_mixes: str | None = None  # why none were loaded


@dataclass(frozen=True)
class Found:
    """One row of :func:`find`."""

    dim: int
    x: int  # the vein's centre block
    z: int
    distance: int | None  # horizontal, in blocks, from ``near``
    mix_id: str
    mix: str
    layers: tuple[
        str, ...
    ]  # where the material is: primary, secondary, between, sporadic
    ymin: int
    ymax: int
    known: bool
    depleted: bool
    src: str


# --- reading -------------------------------------------------------------------------------


def read_mixes(
    gos_home: Path, run_key: str | None = None, allow_harness: bool = False
) -> tuple[str, ore_mixes.OreMixes]:
    """(run id, its ore mixes) of the newest (or the named) collected run, the file checked
    against its manifest under collect's lock."""
    try:
        with collect.lock(gos_home):
            try:
                run = recipe_db.find_run(gos_home, run_key, allow_harness)
            except recipe_db.BuildError as exc:
                if run_key is None:  # no collected run at all
                    raise NoMixes(str(exc)) from None
                raise
            parts = run.manifest.get("parts")
            status = parts.get(PART) if isinstance(parts, dict) else None
            if status != "ok":
                errors = run.manifest.get("part_errors")
                why = errors.get(PART) if isinstance(errors, dict) else None
                raise NoMixes(
                    f"run {run.run_id} has no ore mixes (parts.{PART} is"
                    f" {status or 'missing'}{f': {why}' if why else ''}); export with a"
                    " GregtecLink from WO-1.6 on, collect, and load again"
                )
            entry = next(
                (e for e in collect.entries(run) if e.rel == ore_mixes.FILE), None
            )
            if entry is None:
                raise OresError(f"run {run.run_id}: {ore_mixes.FILE} is not listed")
            data = read_bytes(run.path / entry.rel, limit=entry.size + 1)
            problem = collect._mismatch(data, entry)
            if problem is not None:
                raise OresError(
                    f"run {run.run_id}: {ore_mixes.FILE} doesn't match the manifest"
                    f" ({problem}); collect it again"
                )
    except (collect.CollectError, recipe_db.BuildError) as exc:
        raise OresError(str(exc)) from None
    try:
        return run.run_id, ore_mixes.parse(data)
    except ValueError as exc:
        raise OresError(f"run {run.run_id}: {exc}") from None


def world_folder(
    cfg: config.Config, instance: str, world: str | None
) -> tuple[Path, Path]:
    """(world folder, game folder): ``world`` in ``instance``'s saves/, by default the Lab's
    ``[paths] lab_world`` (or Play's ``[paths] world`` with ``instance`` "play")."""
    name = world or (cfg.lab_world if instance == "lab" else cfg.world)
    if not name:
        key = "lab_world" if instance == "lab" else "world"
        raise OresError(
            f"name the world: --world <folder in {instance.capitalize()}'s saves/> or"
            f" [paths] {key} in gos.toml"
        )
    try:
        return quest_db.world_folder(cfg, instance, name)
    except quest_db.QuestError as exc:
        raise OresError(str(exc)) from None


def read_veins(
    world: Path, game: Path, player: str | None = None, server: bool = True
) -> Veins:
    """A world's veins from its VisualProspecting caches (no DB)."""
    try:
        located = vp.locate(world, game, player)
        s, c = vp.read_all(located, server)
    except (vp.VpError, OSError) as exc:
        raise OresError(str(exc)) from None
    merged, differ = vp.merge(s, c)
    return Veins(located, s, c, merged, differ, not server)


# --- writing -------------------------------------------------------------------------------


def _kv(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE"
        " SET value = excluded.value, upd = excluded.upd",
        (key, value, int(time.time())),
    )


def _mix_row(m: ore_mixes.Mix, ids: dict[str, list[int]]) -> tuple[object, ...]:
    dims: dict[str, dict[str, object]] = {}
    for name in m.dims:
        entry: dict[str, object] = {"ids": sorted(ids.get(name, []))}
        if name in m.heights:
            entry["ymin"], entry["ymax"] = m.heights[name]
        dims[name] = entry
    ores = []
    for o in m.ores:
        ore: dict[str, object] = {
            "layer": o.layer,
            "material": o.material,
            "name": o.name,
        }
        if o.registry_name is not None:
            ore["item"], ore["meta"] = o.registry_name, o.meta
        ores.append(ore)
    return (
        m.id,
        m.name,
        canonical(dims),
        m.ymin,
        m.ymax,
        m.weight,
        m.density,
        m.size,
        canonical(ores),
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _veins_source(
    veins: Veins, unknown: dict[str, int], no_mixes: str | None
) -> dict[str, object]:
    loc = veins.located
    caches = {"server": veins.server, "client": veins.client}
    return {
        "world": str(loc.world),
        "game": str(loc.game),
        "world_id": loc.world_id,
        "player": loc.player,
        "server_cache": None if veins.server is None else str(veins.server.path),
        "server_skipped": veins.server_skipped,
        "client_cache": None if veins.client is None else str(veins.client.path),
        "files": {k: c.files for k, c in caches.items() if c is not None},
        "veins": len(veins.merged),
        "per_dim": {
            str(d): {"veins": n, "known": k, "depleted": dep}
            for d, (n, k, dep) in sorted(vp.counts(veins.merged).items())
        },
        "unknown_mixes": unknown,
        "no_mixes": no_mixes,
        "caches_differ": veins.differ,
        "notes": list(loc.notes)
        + [p for c in caches.values() if c is not None for p in c.problems][:50],
        "loaded_utc": _now(),
    }


def write(
    conn: sqlite3.Connection,
    run_id: str | None,
    mixes: ore_mixes.OreMixes | None,
    veins: Veins,
    no_mixes: str | None = None,
) -> Summary:
    """Replace ``vein``, and ``ore_mix`` unless ``mixes`` is None (then the mixes an earlier
    load left stay, and ``no_mixes`` says why), in one transaction."""
    conn.execute("BEGIN IMMEDIATE")  # waits up to 30 s for another writer
    try:
        if mixes is not None:
            ids = mixes.ids_by_name()
            known_mixes = {m.id for m in mixes.mixes}
            conn.execute("DELETE FROM ore_mix")
            conn.executemany(
                "INSERT INTO ore_mix(id, name, dims, ymin, ymax, weight, density, size,"
                " ores) VALUES (?,?,?,?,?,?,?,?,?)",
                [_mix_row(m, ids) for m in mixes.mixes],
            )
            _kv(
                conn,
                KV_MIXES,
                canonical(
                    {
                        "run": run_id,
                        "mixes": len(mixes.mixes),
                        "dimensions": [[d.dim, d.name] for d in mixes.dimensions],
                        "problems": mixes.problems[:50],
                        "loaded_utc": _now(),
                    }
                ),
            )
        else:
            known_mixes = {r[0] for r in conn.execute("SELECT id FROM ore_mix")}
        unknown: dict[
            str, int
        ] = {}  # with no mixes in the DB at all, none is "unknown"
        for m in veins.merged if known_mixes else ():
            if m.vein.mix not in known_mixes:
                unknown[m.vein.mix] = unknown.get(m.vein.mix, 0) + 1
        conn.execute("DELETE FROM vein")
        conn.executemany(
            "INSERT INTO vein(dim, cx, cz, mix_id, known, depleted, src)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                (
                    m.vein.dim,
                    m.vein.cx,
                    m.vein.cz,
                    m.vein.mix,
                    int(m.known),
                    int(m.vein.depleted),
                    f"{m.cache}/{m.vein.source}",
                )
                for m in veins.merged
            ],
        )
        _kv(conn, KV_VEINS, canonical(_veins_source(veins, unknown, no_mixes)))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    kept = len(known_mixes) if mixes is None else 0
    return Summary(run_id, mixes, veins, unknown, kept, no_mixes)


# --- reading back --------------------------------------------------------------------------


def find(
    conn: sqlite3.Connection,
    material: str,
    dim: int | None = None,
    near: tuple[int, int] | None = None,
    limit: int = 50,
) -> list[Found]:
    """``ore_find``: the veins whose mix holds ``material`` (a GT material name or its display
    name, any case), live ones first, nearest first. ``near`` is an (x, z) block position in
    ``dim``, so it needs ``dim``; distances are horizontal, to the vein's centre block."""
    if near is not None and dim is None:
        raise ValueError("near is a position in a dimension: give dim too")
    x, z = near if near is not None else (None, None)
    rows = access.query(
        conn,
        "ore_find",
        {"material": material, "dim": dim, "x": x, "z": z, "limit": limit},
    )
    return [
        Found(
            dim=r["dim"],
            x=r["x"],
            z=r["z"],
            distance=None if r["dist2"] is None else round(math.sqrt(r["dist2"])),
            mix_id=r["mix_id"],
            mix=r["mix"],
            layers=tuple(r["layers"].split(",")),
            ymin=r["ymin"],
            ymax=r["ymax"],
            known=bool(r["known"]),
            depleted=bool(r["depleted"]),
            src=r["src"],
        )
        for r in rows
    ]


def _json_kv(conn: sqlite3.Connection, key: str) -> dict[str, object]:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    try:
        value = json.loads(row[0]) if row is not None else None
    except ValueError:
        value = None
    return value if isinstance(value, dict) else {}


def dimension_names(conn: sqlite3.Connection) -> dict[int, str]:
    """{dimension ID: name} from the loaded export (``kv`` ``ores.mixes``)."""
    dims = _json_kv(conn, KV_MIXES).get("dimensions")
    out: dict[int, str] = {}
    for d in dims if isinstance(dims, list) else []:
        if isinstance(d, list) and len(d) == 2 and isinstance(d[0], int) and d[1]:
            out[d[0]] = str(d[1])
    return out


# --- the commands --------------------------------------------------------------------------


def _db(cfg: config.Config, readonly: bool = False) -> sqlite3.Connection:
    home = cfg.gos_home
    data = home / "data"
    for path in (home, data):
        if os.path.lexists(path) and (_is_link(os.lstat(path)) or not path.is_dir()):
            raise OresError(f"{path} is a link, a junction or not a folder")
    path = access.db_path(home)
    if readonly:
        if not path.is_file():
            raise OresError(f"{path} doesn't exist: run `gos ores load` first")
        return access.connect(path, readonly=True)
    if os.path.lexists(path) and _is_link(os.lstat(path)):
        raise OresError(f"{path} is a link; refusing it")
    data.mkdir(parents=True, exist_ok=True)
    return access.connect(path)


def report(summary: Summary, names: dict[int, str], out: TextIO) -> None:
    m, v = summary.mixes, summary.veins
    loc = v.located
    if m is not None:
        print(
            f"Ore mixes: {len(m.mixes)} from export run {summary.run_id}"
            f" ({len(m.dimensions)} dimensions named)",
            file=out,
        )
        for problem in m.problems[:10]:
            print(f"  note: {problem}", file=out)
    else:
        kept = (
            f"kept the {summary.kept_mixes} an earlier load left"
            if summary.kept_mixes
            else "the DB has none yet, so veins show only their mix's GT name"
        )
        print(f"Ore mixes: not loaded ({summary.no_mixes}); {kept}", file=out)
    print(f"World: {loc.world}", file=out)
    print(f"  VisualProspecting world id: {loc.world_id or 'none yet'}", file=out)
    if v.server_skipped:
        print("  server cache: not read ([policy] ore_server_cache = false)", file=out)
    for label, cache in (("server", v.server), ("client", v.client)):
        if cache is not None:
            who = f" (player {loc.player})" if label == "client" else ""
            print(
                f"  {label} cache{who}: {cache.path}, {len(cache.files)} dimension"
                f" files, {len(cache.veins)} veins",
                file=out,
            )
    for note in list(loc.notes) + [
        p for c in (v.server, v.client) if c is not None for p in c.problems
    ]:
        print(f"  note: {note}", file=out)
    if v.differ:
        print(
            f"  note: {v.differ} chunks have a different mix in the client cache; the"
            " server's is kept",
            file=out,
        )
    if summary.unknown:
        listed = ", ".join(f"{k} ({n})" for k, n in sorted(summary.unknown.items()))
        print(f"  warning: veins of mixes this export doesn't have: {listed}", file=out)
    print(f"Veins: {len(v.merged)}", file=out)
    for dim, (n, k, d) in sorted(vp.counts(v.merged).items()):
        print(
            f"  dim {dim}{f' ({names[dim]})' if dim in names else ''}: {n} veins,"
            f" {k} known, {d} depleted",
            file=out,
        )


def load_main(
    config_path: Path | None,
    run_key: str | None,
    instance: str,
    world: str | None,
    world_dir: Path | None,
    game_dir: Path | None,
    player: str | None,
    out: TextIO,
) -> int:
    """0: loaded; 1: refused or failed; 2: bad gos.toml."""
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        if world_dir is not None:
            if not world_dir.is_dir():
                raise OresError(f"{world_dir} is not a folder")
            game = game_dir
            if game is None:
                if world_dir.resolve().parent.name != "saves":
                    raise OresError(
                        f"{world_dir} is not in a saves/ folder: say which game folder holds"
                        " its visualprospecting/ with --game-dir"
                    )
                game = world_dir.resolve().parent.parent
            path = world_dir
        else:
            path, game = world_folder(cfg, instance, world)
            if instance == "play":
                print(PLAY_READ_NOTE, file=out)
        run_id: str | None
        mixes: ore_mixes.OreMixes | None
        no_mixes = None
        try:
            run_id, mixes = read_mixes(cfg.gos_home, run_key)
        except NoMixes as exc:
            if run_key is not None:
                raise
            run_id, mixes, no_mixes = None, None, str(exc)
        veins = read_veins(path, game, player, cfg.ore_server_cache)
        conn = _db(cfg)
        try:
            summary = write(conn, run_id, mixes, veins, no_mixes)
            names = dimension_names(conn)
        finally:
            conn.close()
    except (OresError, access.SchemaError, OSError, sqlite3.Error) as exc:
        print(f"Not loaded: {exc}", file=out)
        return 1
    report(summary, names, out)
    return 0


def list_main(
    config_path: Path | None, dim: int | None, limit: int, out: TextIO
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        conn = _db(cfg, readonly=True)
        try:
            source = _json_kv(conn, KV_VEINS)
            names = dimension_names(conn)
            if dim is None:
                rows = access.query(conn, "ore_dims")
            else:
                rows = access.query(conn, "ore_veins", {"dim": dim, "limit": limit})
        finally:
            conn.close()
    except (OresError, access.SchemaError, OSError, sqlite3.Error) as exc:
        print(f"No veins: {exc}", file=out)
        return 1
    if not source:
        print("No veins: run `gos ores load` first", file=out)
        return 1
    print(
        f"# {source.get('world')} (player {source.get('player') or 'none'}), loaded"
        f" {source.get('loaded_utc')}",
        file=out,
    )
    if dim is None:
        print("dim\tname\tveins\tknown\tdepleted\tmixes", file=out)
        for r in rows:
            print(
                f"{r['dim']}\t{names.get(r['dim'], '')}\t{r['veins']}\t{r['known']}"
                f"\t{r['depleted']}\t{r['mixes']}",
                file=out,
            )
        print(f"# {len(rows)} dimensions; `gos ores list --dim N` lists one", file=out)
        return 0
    print(
        f"# dim {dim}{f' ({names[dim]})' if dim in names else ''}; x and z are each"
        " vein's centre block",
        file=out,
    )
    print("x\tz\tmix\tores\ty\tknown\tdepleted\tsrc", file=out)
    for r in rows:
        y = "" if r["ymin"] is None else f"{r['ymin']}-{r['ymax']}"
        print(
            f"{r['x']}\t{r['z']}\t{r['mix']}\t{r['ores'] or ''}\t{y}\t{r['known']}"
            f"\t{r['depleted']}\t{r['src']}",
            file=out,
        )
    print(f"# {len(rows)} veins", file=out)
    return 0
