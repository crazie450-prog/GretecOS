"""``gos quests``: a world's quest book and progress in ``GOS_HOME/data/gos.sqlite`` (WO-1.5).

- ``gos quests load`` reads one world (default: Play's ``[paths] world``; ``--instance lab
  --world lab-progress`` for the Lab, which always names its world) with
  :mod:`gregtec.ingest.quests`, read-only, and replaces every quest table in one transaction
  (D112): the book (``quest_line``, ``quest_line_entry``, ``quest``, ``quest_prereq``,
  ``quest_task``, ``quest_reward``, ``quest_item``, ``quest_fts``), every player's completions
  (``quest_progress``) and, for the tracked player, each quest's state (``quest_state``: done,
  repeatable, available or locked, and whether the book shows it, D113). A load that fails
  changes nothing. Quest text is stored as data, never instructions. Reading Play prints a
  reminder to pause the game first (D84) until WO-6.1's save loop times its own reads.
- Items in tasks and rewards are linked to ``item``/``fluid`` (WO-1.3's catalog) by item key
  when the recipe DB has them; ``quest_item`` keeps the key too, so named queries can join by
  key after a later ``gos db build`` adds items.
- **Tier** (D64, D114): the highest stage in ``data/curated/tier_quests.tsv`` whose key quest
  the player has completed, saved in ``kv`` as ``tier.detected``. ``gos quests tier --set``
  stores an override (``tier.override``, D64's "overridable"); the effective tier is the
  override when there is one. The source of the load is ``kv`` ``quests.source``.
- ``gos quests frontier`` lists the frontier (named query ``quest_frontier``) as the book shows
  it, chapter by chapter, for Clayton's in-game check (docs/runbooks/quests_check.md).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.db import access
from gregtec.ingest import quests as bq
from gregtec.ingest.normalize import canonical
from gregtec.sync import _instance, _is_link

REPO_ROOT = Path(__file__).resolve().parents[3]
TIER_KEYS = REPO_ROOT / "data" / "curated" / "tier_quests.tsv"
KV_SOURCE, KV_TIER, KV_OVERRIDE = "quests.source", "tier.detected", "tier.override"
# Until WO-6.1's save loop times reads to the game's saves (D84, V27).
PLAY_READ_NOTE = (
    "Reading Play's world. Do this with the game paused (Esc) or closed: the read takes"
    " milliseconds, but a BetterQuesting save that lands during it fails (D84)."
)
QUEST_TABLES = (
    "quest_line",
    "quest_line_entry",
    "quest",
    "quest_prereq",
    "quest_task",
    "quest_reward",
    "quest_item",
    "quest_progress",
    "quest_state",
    "quest_fts",
)


class QuestError(RuntimeError):
    """Refused or failed; the DB is as it was."""


@dataclass
class Loaded:
    book: bq.Book
    progress: bq.Progress
    player: str
    states: dict[str, bq.State]
    tier: bq.Tier
    keys: list[bq.TierKey]
    world: Path
    default_pack: tuple[str, int] | None = None  # the DefaultQuests' name and version
    missing_keys: list[bq.TierKey] = field(default_factory=list)


@dataclass
class Summary:
    loaded: Loaded
    frontier: int  # distinct quests
    items: int  # stacks in tasks and rewards
    linked: int  # of them, found in the recipe DB's item or fluid table
    override: str | None = None  # the tier override in kv, if any


# --- reading -------------------------------------------------------------------------------


def world_folder(
    cfg: config.Config, instance: str, world: str | None
) -> tuple[Path, Path]:
    """(world folder, game folder) of ``world`` in ``instance`` ("play" or "lab"): the
    instance and its game folder are checked as ``sync_instances.py`` checks them."""
    refusals: list[str] = []
    found = _instance(cfg.instance(instance), instance.capitalize(), refusals)
    if found is None:
        raise QuestError("; ".join(refusals))
    # [paths] world is Play's world; the Lab's is always named.
    name = world or (cfg.world if instance == "play" else None)
    if not name:
        raise QuestError(
            "name the world: --world <folder in saves/>"
            + (" or [paths] world in gos.toml" if instance == "play" else "")
        )
    if name in (".", "..") or any(c in name for c in "/\\:"):
        raise QuestError(f"--world takes a folder name in saves/, not {name!r}")
    path = found[1] / "saves" / name
    if os.path.lexists(path) and _is_link(os.lstat(path)):
        raise QuestError(f"{path} is a link or junction; refusing it")
    if not path.is_dir():
        raise QuestError(f"{path} doesn't exist (check the world's folder name)")
    return path, found[1]


def read(
    world: Path,
    game: Path | None,
    player: str | None = None,
    tier_keys: Path | None = None,
) -> Loaded:
    """Read a world's book and progress, and work out states and tier (no DB)."""
    try:
        keys = bq.read_tier_keys(tier_keys or TIER_KEYS)
    except (OSError, ValueError) as exc:
        raise QuestError(f"curated tier quests: {exc}") from None
    try:
        book = bq.read_book(world, game)
        progress = bq.read_progress(world)
        who = bq.pick_player(progress, player)
    except (OSError, ValueError) as exc:
        raise QuestError(f"{world}: {exc}") from None
    if game is None and world.parent.name == "saves":
        game = world.parent.parent
    try:
        default_pack = bq.default_pack(game)
    except (OSError, ValueError):
        default_pack = None
    st = bq.states(book, progress.done, who, progress.tasks)
    done = [q for q, s in st.items() if s.state in bq.COMPLETE]
    return Loaded(
        book=book,
        progress=progress,
        player=who,
        states=st,
        tier=bq.detect_tier(keys, done),
        keys=keys,
        world=world,
        default_pack=default_pack,
        missing_keys=[k for k in keys if k.quest not in book.quests],
    )


# --- writing -------------------------------------------------------------------------------


def _bit(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _kv(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM kv WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO kv(key, value, upd) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE"
            " SET value = excluded.value, upd = excluded.upd",
            (key, value, int(time.time())),
        )


def _source(loaded: Loaded) -> dict[str, object]:
    b = loaded.book
    pack_name, pack_version = loaded.default_pack or (None, None)
    return {
        "source": b.source,
        "path": str(b.path),
        "world": str(loaded.world),
        "pack_name": b.pack_name,
        "pack_version": b.pack_version,
        "defaults_pack_name": pack_name,
        "defaults_pack_version": pack_version,
        "format": b.format,
        "build": b.build,
        "quests": len(b.quests),
        "lines": len(b.lines),
        "player": loaded.player,
        "player_name": loaded.progress.names.get(loaded.player),
        "players": sorted(loaded.progress.done),
        "problems": b.problems[:50] + loaded.progress.problems[:50],
        "loaded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _single(part: bq.Part) -> bq.Stack | None:
    return part.stacks[0] if len(part.stacks) == 1 else None


def write(conn: sqlite3.Connection, loaded: Loaded) -> Summary:
    """Replace every quest table with ``loaded`` in one transaction."""
    book = loaded.book
    first_line: dict[str, str] = {}
    for line in book.lines:
        for e in line.entries:
            first_line.setdefault(e.quest, line.id)
    conn.execute("BEGIN IMMEDIATE")  # waits up to 30 s for another writer
    try:
        for table in QUEST_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            "INSERT INTO quest_line(id, name, ord, descr, visibility) VALUES (?,?,?,?,?)",
            [(ln.id, ln.name, ln.ord, ln.descr, ln.visibility) for ln in book.lines],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO quest_line_entry(line_id, quest_id, x, y, size_x, size_y)"
            " VALUES (?,?,?,?,?,?)",
            [
                (ln.id, e.quest, e.x, e.y, e.size_x, e.size_y)
                for ln in book.lines
                for e in ln.entries
            ],
        )
        quests, prereqs, tasks, rewards, items, fts = [], [], [], [], [], []
        for q in book.quests.values():
            quests.append(
                (
                    q.id,
                    first_line.get(q.id),
                    q.name,
                    q.descr,
                    q.logic,
                    q.visibility,
                    int(q.main),
                    canonical(q.raw),
                    q.task_logic,
                    int(q.is_global),
                    q.repeat_time,
                )
            )
            prereqs.extend((q.id, p) for p in q.prereqs)
            fts.append((q.id, q.name, q.descr))
            for part_name, parts in (("task", q.tasks), ("reward", q.rewards)):
                for n, p in enumerate(parts):
                    one = _single(p)
                    row = (
                        q.id,
                        n,
                        p.kind,
                        one.amount if one else None,
                        canonical(p.raw),
                    )
                    if part_name == "task":
                        tasks.append((*row, _bit(p.consume)))
                    else:
                        rewards.append(row)
                    items.extend(
                        (
                            q.id,
                            part_name,
                            n,
                            slot,
                            s.registry_name,
                            s.meta if s.registry_name else None,
                            s.nbt_hash if s.registry_name else None,
                            s.ore,
                            s.fluid,
                            s.amount,
                        )
                        for slot, s in enumerate(p.stacks)
                    )
        conn.executemany(
            "INSERT INTO quest(id, line_id, name, descr, logic, visibility, main, raw_json,"
            " task_logic, is_global, repeat_time) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            quests,
        )
        conn.executemany(
            "INSERT INTO quest_prereq(quest_id, prereq_id) VALUES (?,?)", prereqs
        )
        # A task's or reward's position in its list is its idx: BQ's own index may repeat.
        conn.executemany(
            "INSERT INTO quest_task(quest_id, idx, kind, amount, raw_json, consume)"
            " VALUES (?,?,?,?,?,?)",
            tasks,
        )
        conn.executemany(
            "INSERT INTO quest_reward(quest_id, idx, kind, amount, raw_json)"
            " VALUES (?,?,?,?,?)",
            rewards,
        )
        conn.executemany(
            "INSERT INTO quest_item(quest_id, part, idx, slot, registry_name, meta, nbt_hash,"
            " ore, fluid, amount) VALUES (?,?,?,?,?,?,?,?,?,?)",
            items,
        )
        conn.execute(
            "UPDATE quest_item SET item_id = (SELECT i.id FROM item AS i"
            " WHERE i.registry_name = quest_item.registry_name AND i.meta = quest_item.meta"
            " AND i.nbt_hash = quest_item.nbt_hash) WHERE registry_name IS NOT NULL"
        )
        conn.execute(
            "UPDATE quest_item SET fluid_id = (SELECT f.id FROM fluid AS f"
            " WHERE f.name = quest_item.fluid) WHERE fluid IS NOT NULL"
        )
        # Appendix D's single-item columns: filled when a task or reward has exactly one stack.
        one = (
            "(SELECT i.{col} FROM quest_item AS i WHERE i.quest_id = {t}.quest_id"
            " AND i.part = '{part}' AND i.idx = {t}.idx AND i.slot = 0)"
        )
        conn.execute(
            "UPDATE quest_task SET item_id = "
            + one.format(col="item_id", t="quest_task", part="task")
            + ", fluid_id = "
            + one.format(col="fluid_id", t="quest_task", part="task")
            + " WHERE amount IS NOT NULL"
        )
        conn.execute(
            "UPDATE quest_reward SET item_id = "
            + one.format(col="item_id", t="quest_reward", part="reward")
            + " WHERE amount IS NOT NULL"
        )
        conn.executemany(
            "INSERT INTO quest_fts(quest_id, name, descr) VALUES (?,?,?)", fts
        )
        conn.executemany(
            "INSERT INTO quest_progress(player_uuid, quest_id, completed_ts, claimed)"
            " VALUES (?,?,?,?)",
            [
                (who, qid, c.ts, int(c.claimed))
                for who, done in sorted(loaded.progress.done.items())
                for qid, c in sorted(done.items())
            ],
        )
        conn.executemany(
            "INSERT INTO quest_state(player_uuid, quest_id, state, shown) VALUES (?,?,?,?)",
            [
                (loaded.player, qid, s.state, int(s.shown))
                for qid, s in loaded.states.items()
            ],
        )
        _kv(conn, KV_SOURCE, canonical(_source(loaded)))
        t = loaded.tier
        _kv(
            conn,
            KV_TIER,
            canonical(
                {
                    "stage": t.stage,
                    "tier": t.tier,
                    "quest": t.quest,
                    "name": t.name,
                    "player": loaded.player,
                }
            ),
        )
        counts = conn.execute(
            "SELECT count(*), count(item_id) + count(fluid_id) FROM quest_item"
        ).fetchone()
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    shown = {r[2] for r in frontier_rows(conn, loaded.player)}
    stage, how, _ = effective_tier(conn)
    return Summary(
        loaded, len(shown), counts[0], counts[1], stage if how == "override" else None
    )


# --- reading back --------------------------------------------------------------------------


def frontier_rows(
    conn: sqlite3.Connection,
    player: str | None = None,
    line: str | None = None,
    limit: int = -1,
) -> list[tuple[str, str, str, str]]:
    """(line name, quest name, quest id, state) of the frontier, as the book shows it; the
    state is available or repeatable."""
    if player is None:
        player = tracked_player(conn)
    rows = access.query(
        conn, "quest_frontier", {"player": player, "line": line, "limit": limit}
    )
    return [(r[0], r[1], r[2], r[3]) for r in rows]  # line, name, quest_id, state


def _json_kv(conn: sqlite3.Connection, key: str) -> dict[str, object] | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def tracked_player(conn: sqlite3.Connection) -> str:
    source = _json_kv(conn, KV_SOURCE)
    if source is None:
        raise QuestError("no quests loaded yet: run `gos quests load` first")
    return str(source.get("player") or "")


def effective_tier(conn: sqlite3.Connection) -> tuple[str, str, str | None]:
    """(effective stage, how it was set: "override" or "quests", detected stage or None)."""
    detected = _json_kv(conn, KV_TIER)
    stage = str(detected["stage"]) if detected else None
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (KV_OVERRIDE,)).fetchone()
    if row is not None and row[0] in bq.STAGES:
        return row[0], "override", stage
    return stage or bq.FIRST_STAGE, "quests", stage


def set_override(conn: sqlite3.Connection, stage: str | None) -> None:
    if stage is not None and stage not in bq.STAGES:
        raise QuestError(f"unknown stage {stage!r}: one of {', '.join(bq.STAGES)}")
    conn.execute("BEGIN IMMEDIATE")
    try:
        _kv(conn, KV_OVERRIDE, stage)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


# --- the command ---------------------------------------------------------------------------


def _db(cfg: config.Config, readonly: bool = False) -> sqlite3.Connection:
    home = cfg.gos_home
    data = home / "data"
    for path in (home, data):
        if os.path.lexists(path) and (_is_link(os.lstat(path)) or not path.is_dir()):
            raise QuestError(f"{path} is a link, a junction or not a folder")
    path = access.db_path(home)
    if readonly:
        if not path.is_file():
            raise QuestError(f"{path} doesn't exist: run `gos quests load` first")
        return access.connect(path, readonly=True)
    if os.path.lexists(path) and _is_link(os.lstat(path)):
        raise QuestError(f"{path} is a link; refusing it")
    data.mkdir(parents=True, exist_ok=True)
    return access.connect(path)


def _stage_text(stage: str) -> str:
    return f"{stage} (GT voltage tier {bq.STAGES[stage]})"


def report(summary: Summary, out: TextIO) -> None:
    ld = summary.loaded
    b = ld.book
    where = "the world's QuestDatabase.json" if b.source == "world" else "DefaultQuests"
    print(f"Quest book: {where}, {b.path}", file=out)
    print(
        f"  {len(b.quests)} quests in {len(b.lines)} quest lines; pack {b.pack_name}"
        f" version {b.pack_version}",
        file=out,
    )
    if bq.updates_book(b, ld.default_pack):
        assert ld.default_pack is not None
        print(
            f"  The pack's DefaultQuests is version {ld.default_pack[1]}: BetterQuesting"
            " imports it into this world when the world next loads, so load again after.",
            file=out,
        )
    for problem in (b.problems + ld.progress.problems)[:20]:
        print(f"  note: {problem}", file=out)
    name = ld.progress.names.get(ld.player, "?")
    done = sum(1 for s in ld.states.values() if s.state in bq.COMPLETE)
    again = sum(1 for s in ld.states.values() if s.state == bq.REPEATABLE)
    print(
        f"Player: {name} ({ld.player}): {done} quests complete"
        + (f", {again} of them repeatable again" if again else ""),
        file=out,
    )
    print(
        f"Frontier: {summary.frontier} quests (gos quests frontier lists them)",
        file=out,
    )
    t = ld.tier
    why = f" from q:{t.quest} ({t.name})" if t.quest else " (no key quest complete yet)"
    print(f"Tier detected: {_stage_text(t.stage)}{why}", file=out)
    if summary.override is not None:
        print(
            f"Tier in use: {_stage_text(summary.override)}, your override (D64);"
            " `gos quests tier --clear` goes back to the detected tier",
            file=out,
        )
    for k in ld.missing_keys:
        print(
            f"  warning: tier key quest q:{k.quest} ({k.stage}, {k.name}) is not in this"
            " book: check data/curated/tier_quests.tsv against this pack",
            file=out,
        )
    print(
        f"Items in tasks and rewards: {summary.items}, {summary.linked} found in the"
        " recipe DB",
        file=out,
    )


def load_main(
    config_path: Path | None,
    instance: str,
    world: str | None,
    world_dir: Path | None,
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
                raise QuestError(f"{world_dir} is not a folder")
            path, game = world_dir, None
        else:
            path, game = world_folder(cfg, instance, world)
            if instance == "play":
                print(PLAY_READ_NOTE, file=out)
        loaded = read(path, game, player)
        conn = _db(cfg)
        try:
            summary = write(conn, loaded)
        finally:
            conn.close()
    except (QuestError, access.SchemaError, OSError, sqlite3.Error) as exc:
        print(f"Not loaded: {exc}", file=out)
        return 1
    report(summary, out)
    return 0


def frontier_main(
    config_path: Path | None, line: str | None, limit: int, out: TextIO
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        conn = _db(cfg, readonly=True)
        try:
            rows = frontier_rows(conn, line=line, limit=limit)
            source = _json_kv(conn, KV_SOURCE) or {}
        finally:
            conn.close()
    except (QuestError, access.SchemaError, OSError, sqlite3.Error) as exc:
        print(f"No frontier: {exc}", file=out)
        return 1
    print(
        f"# {source.get('player_name') or '?'} in {source.get('world')}, loaded"
        f" {source.get('loaded_utc')}",
        file=out,
    )
    print("line\tquest\tid\tstate", file=out)
    for line_name, name, qid, state in rows:
        print(f"{line_name}\t{name}\tq:{qid}\t{state}", file=out)
    print(f"# {len(rows)} rows, {len({r[2] for r in rows})} quests", file=out)
    return 0


def tier_main(
    config_path: Path | None, set_stage: str | None, clear: bool, out: TextIO
) -> int:
    try:
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    try:
        conn = _db(cfg, readonly=set_stage is None and not clear)
        try:
            if set_stage is not None or clear:
                set_override(conn, None if clear else set_stage)
            stage, how, detected = effective_tier(conn)
            evidence = _json_kv(conn, KV_TIER) or {}
        finally:
            conn.close()
    except (QuestError, access.SchemaError, OSError, sqlite3.Error) as exc:
        print(f"No tier: {exc}", file=out)
        return 1
    print(f"Tier: {_stage_text(stage)} ({how})", file=out)
    if detected is None:
        print("Detected: nothing yet (run `gos quests load`)", file=out)
    else:
        quest = evidence.get("quest")
        why = f" from q:{quest} ({evidence.get('name')})" if quest else ""
        print(f"Detected from quests: {_stage_text(detected)}{why}", file=out)
    return 0
