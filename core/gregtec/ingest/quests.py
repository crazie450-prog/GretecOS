"""BetterQuesting's quest book and progress, read from a world (plan sections 5.4 and 7.4, WO-1.5).

The formats are BetterQuesting 3.8.92-GTNH's (GTNH 2.9.0), re-implemented from its source (MIT,
Copyright (c) 2019 Funwayguy; licence checked) and checked against 2.9.0's DefaultQuests
[B§1]. Nothing here writes a file; every game file is read whole through
``gamestate.safe_open.read_bytes`` (share-delete, one read, handle closed at once, 3 tries) and
parsed from memory.

- **The book.** The world's ``betterquesting/QuestDatabase.json`` is the book that world shows
  (BQ loads it whenever it exists); without one (a world BQ hasn't saved yet), the pack's
  ``config/betterquesting/DefaultQuests/`` (split format: ``QuestSettings.json``,
  ``QuestLinesOrder.txt``, ``QuestLines/<line>/``, ``Quests/**``), as BQ itself falls back.
  BQ drops a default quest line that ``QuestLinesOrder.txt`` doesn't list, so this does too.
- **Typed JSON.** Every key carries its NBT type id (``name:8``); split on the last colon. A
  list (``:9``) is an object keyed ``"0:10"``, ``"1:10"``... in file order.
- **IDs.** A quest or quest line id is a UUID stored as two signed longs (``questIDHigh/Low``,
  ``questLineIDHigh/Low``). GOS writes it as BQ's own compact form without padding (URL-safe
  base64 of the 16 big-endian bytes, as in BQ's lang keys), so a quest is cited
  ``q:AAAAAAAAAAAAAAAAAAABug`` and ``/bq_admin complete AAAAAAAAAAAAAAAAAAABug`` takes the same
  string (D112).
- **Items** in tasks and rewards are registry name + damage (never numeric IDs). Their key is
  GOS's item key (registry name, meta, NBT hash, D104): the stack's ``tag`` in NEI's ``NBTJson``
  typing (GregtecLink's), hashed as :func:`gregtec.ingest.normalize.nbt_hash` does. A task that
  ignores NBT accepts the item with any NBT, so its key has no NBT hash.
- **Text**: names and descriptions lose ``§`` colour codes and the book's markup tags
  (``[note]``, ``[warn]``, ``[url]``...), keeping the words. Quest text is data, never
  instructions (plan section 0.1 principle 8).

Unlocking and visibility follow BQ's ``QuestInstance.isUnlocked`` and
``QuestCache.isQuestShown`` with view mode off (:func:`states`, D113): the **frontier** is the
quests that are unlocked, not complete, shown, and in a quest line the book shows.
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from gregtec.gamestate.safe_open import read_bytes
from gregtec.ingest.normalize import Json, nbt_hash, normalize_nbt

WORLD_DIR = "betterquesting"  # in the world folder (single-player saves/<world>/)
DATABASE = "QuestDatabase.json"
PROGRESS_DIR = "QuestProgress"
NAME_CACHE = "NameCache.json"
DEFAULTS = Path("config", "betterquesting", "DefaultQuests")  # in the game folder
SETTINGS_FILE = "QuestSettings.json"
LINE_ORDER_FILE = "QuestLinesOrder.txt"
LINE_FILE = "QuestLine.json"
# Bytes. 2.9.0's book is about 12 MB as one QuestDatabase.json; a player's progress about 1 MB.
JSON_LIMIT = 256 << 20

LOGICS = ("AND", "NAND", "OR", "NOR", "XOR", "XNOR")
VISIBILITIES = (
    "HIDDEN",
    "SECRET",
    "UNLOCKED",
    "NORMAL",
    "COMPLETED",
    "CHAIN",
    "ALWAYS",
)
ITEM_PARTS = ("requiredItems", "rewards", "choices")  # task, item reward, choice reward
FLUID_PARTS = ("requiredFluids",)

_COLOUR = re.compile("§.", re.DOTALL)
_IMG = re.compile(r"\[img[^\]]*\].*?\[/img\]", re.DOTALL)
_MARKUP = re.compile(r"\[/?(?:note|warn|url|quest|questlink|bold)\]")
_ID = re.compile(r"[A-Za-z0-9_-]{22}")
_INT_SUFFIX = {1: "B", 2: "S", 4: "L"}  # NBT byte, short, long in NBTJson


class FormatError(ValueError):
    """A BetterQuesting file that doesn't parse or has no ids."""


# --- typed JSON -----------------------------------------------------------------------------


class JFloat(float):
    """A JSON float that remembers its text: Java's ``Float.toString`` / ``Double.toString``
    as Gson wrote it, which NBT typing (``1.5F``) needs exactly."""

    text: str

    def __new__(cls, text: str) -> Self:
        value = super().__new__(cls, text)
        value.text = text
        return value


def loads(data: bytes) -> Json:
    """BQ's JSON (UTF-8, Gson), floats kept with their text."""
    return json.loads(data.decode("utf-8"), parse_float=JFloat)


def split_key(key: str) -> tuple[str, int | None]:
    """``"name:8"`` -> ``("name", 8)``; a key with no type suffix -> ``(key, None)``."""
    base, sep, tail = key.rpartition(":")
    if sep and tail.isdigit():
        return base, int(tail)
    return key, None


def _items(value: Json) -> list[tuple[int | None, Json]]:
    """A typed list's elements with their types: an object ``{"0:10": ...}`` or an array."""
    if isinstance(value, dict):
        return [(split_key(k)[1], v) for k, v in value.items()]
    if isinstance(value, list):
        return [(None, v) for v in value]
    return []


class Typed:
    """A typed compound: ``get("name")`` finds ``"name:8"`` (or a bare ``"name"``)."""

    def __init__(self, value: Json) -> None:
        self.fields: dict[str, tuple[int | None, Json]] = {}
        if isinstance(value, dict):
            for k, v in value.items():
                base, tag = split_key(k)
                self.fields[base] = (tag, v)

    def __contains__(self, name: str) -> bool:
        return name in self.fields

    def get(self, name: str, default: Json = None) -> Json:
        return self.fields.get(name, (None, default))[1]

    def typed(self, name: str) -> tuple[int | None, Json]:
        return self.fields.get(name, (None, None))

    def sub(self, name: str) -> Typed:
        """A compound field."""
        return Typed(self.get(name))

    def seq(self, name: str) -> list[Json]:
        """A list field's elements."""
        return [v for _, v in _items(self.get(name))]

    def num(self, name: str, default: int = 0) -> int:
        v = self.get(name)
        return int(v) if isinstance(v, int | float) else default

    def flag(self, name: str, default: bool = False) -> bool:
        v = self.get(name)
        return default if v is None else bool(v)

    def text(self, name: str, default: str = "") -> str:
        v = self.get(name)
        return v if isinstance(v, str) else default


def nbt_json(tag: int | None, value: Json) -> Json:
    """A BQ typed NBT value in NEI's ``NBTJson`` typing, as GregtecLink writes NBT (contract):
    ints bare, byte/short/long/float/double as strings with ``B S L F D``, lists and int arrays
    as arrays, byte arrays as arrays of ``"<n>B"``, an empty list or array as its
    ``__custom_type``."""
    if tag == 10 or (tag is None and isinstance(value, dict)):
        return {
            split_key(k)[0]: nbt_json(split_key(k)[1], v)
            for k, v in (value.items() if isinstance(value, dict) else ())
        }
    if tag == 9:
        items = _items(value)
        if not items:
            return {"__custom_type": "net.minecraft.nbt.NBTTagList"}
        return [nbt_json(t, v) for t, v in items]
    if tag in (7, 11):
        values = value if isinstance(value, list) else []
        if not values:
            kind = "NBTTagByteArray" if tag == 7 else "NBTTagIntArray"
            return {"__custom_type": f"net.minecraft.nbt.{kind}"}
        return [f"{int(v)}B" for v in values] if tag == 7 else [int(v) for v in values]
    if tag in _INT_SUFFIX and isinstance(value, int | float):
        return f"{int(value)}{_INT_SUFFIX[tag]}"
    if tag in (5, 6) and isinstance(value, int | float):
        text = value.text if isinstance(value, JFloat) else repr(float(value))
        return text + ("F" if tag == 5 else "D")
    if tag == 3 and isinstance(value, int | float):
        return int(value)
    return value


# --- ids and text ---------------------------------------------------------------------------


def encode_id(high: int, low: int) -> str:
    """BQ's compact UUID string without padding (``UuidConverter.encodeUuidStripPadding``)."""
    raw = struct.pack(">qq", high, low)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_id(text: str) -> tuple[int, int]:
    """(high, low) of a compact id, with or without its ``==``; ValueError if it isn't one."""
    text = text.strip().removeprefix("q:").rstrip("=")
    if not _ID.fullmatch(text):
        raise ValueError(f"not a quest id: {text!r}")
    high, low = struct.unpack(">qq", base64.urlsafe_b64decode(text + "=="))
    return high, low


def read_id(t: Typed, field_name: str = "questID") -> str | None:
    """A quest's (``questID``) or quest line's (``questLineID``) id, or None if it has none."""
    high, low = t.get(field_name + "High"), t.get(field_name + "Low")
    if not (isinstance(high, int) and isinstance(low, int)):
        return None
    if not all(-(1 << 63) <= v < 1 << 63 for v in (high, low)):
        return None  # not a Java long: a damaged file
    return encode_id(high, low)


def clean(text: str) -> str:
    """Book text without colour codes or markup tags."""
    text = _IMG.sub("", _COLOUR.sub("", text))
    return _MARKUP.sub("", text).strip()


# --- the book -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Stack:
    """An item or fluid a task asks for or a reward gives."""

    amount: int
    registry_name: str | None = None  # items
    meta: int = 0
    nbt_hash: str = ""  # the key the task matches: "" when it ignores NBT
    ore: str | None = None  # BQ's OreDict: any member is accepted
    fluid: str | None = None  # fluids


@dataclass(frozen=True)
class Part:
    """A task or a reward."""

    idx: int
    kind: str  # taskID or rewardID, e.g. bq_standard:retrieval, bq_standard:choice
    consume: bool | None  # retrieval and fluid tasks: whether the items are taken
    stacks: tuple[Stack, ...]
    raw: Json


@dataclass(frozen=True)
class Quest:
    id: str
    name: str
    descr: str
    logic: str  # questLogic over the prerequisites
    task_logic: str
    visibility: str
    main: bool
    is_global: bool  # complete for everyone once anyone completes it
    repeat_time: int  # -1: not repeatable
    prereqs: tuple[str, ...]
    tasks: tuple[Part, ...]
    rewards: tuple[Part, ...]
    raw: Json


@dataclass(frozen=True)
class Entry:
    """A quest's place in a quest line."""

    quest: str
    x: int
    y: int
    size_x: int
    size_y: int


@dataclass(frozen=True)
class Line:
    id: str
    name: str
    descr: str
    visibility: str
    ord: int  # tab order
    entries: tuple[Entry, ...]


@dataclass
class Book:
    """One world's quest book."""

    # "world" (its QuestDatabase.json) or "defaults" (the pack's DefaultQuests)
    source: str
    path: Path
    quests: dict[str, Quest]
    lines: list[Line]  # in tab order
    pack_name: str | None = None
    pack_version: int | None = None
    format: str | None = None
    build: str | None = None  # BQ's version that wrote the world copy
    problems: list[str] = field(default_factory=list)


def _stack(t: Typed, ignore_nbt: bool) -> Stack:
    if "FluidName" in t:
        return Stack(amount=t.num("Amount"), fluid=t.text("FluidName") or None)
    nbt_type, nbt = t.typed("tag")
    hashed = ""
    if nbt is not None and not ignore_nbt:
        hashed = nbt_hash(normalize_nbt(nbt_json(nbt_type or 10, nbt)))
    return Stack(
        amount=t.num("Count", 1),
        registry_name=t.text("id") or None,
        meta=t.num("Damage"),
        nbt_hash=hashed,
        ore=t.text("OreDict") or None,
    )


def _part(value: Json, n: int, kind_key: str) -> Part:
    t = Typed(value)
    ignore_nbt = t.flag("ignoreNBT")
    stacks: list[Stack] = []
    for name in (*ITEM_PARTS, *FLUID_PARTS):
        stacks.extend(_stack(Typed(s), ignore_nbt) for s in t.seq(name))
    consume = t.flag("consume") if "consume" in t else None
    return Part(t.num("index", n), t.text(kind_key), consume, tuple(stacks), value)


def _enum(value: str, allowed: tuple[str, ...], default: str) -> str:
    value = value.upper()
    return value if value in allowed else default


def parse_quest(value: Json, problems: list[str] | None = None) -> Quest:
    """One quest's typed JSON (a DefaultQuests file, or an entry of ``questDatabase``)."""
    t = Typed(value)
    qid = read_id(t)
    if qid is None:
        raise FormatError("a quest with no questIDHigh/questIDLow")
    props = t.sub("properties").sub("betterquesting")
    prereqs: list[str] = []
    for p in t.seq("preRequisites"):
        pid = read_id(Typed(p))
        if pid is None:
            if problems is not None:
                problems.append(f"quest {qid}: a prerequisite with no id")
            continue
        if pid not in prereqs:
            prereqs.append(pid)
    tasks = tuple(_part(v, n, "taskID") for n, v in enumerate(t.seq("tasks")))
    rewards = tuple(_part(v, n, "rewardID") for n, v in enumerate(t.seq("rewards")))
    return Quest(
        id=qid,
        name=clean(props.text("name")),
        descr=clean(props.text("desc")),
        logic=_enum(props.text("questLogic", "AND"), LOGICS, "AND"),
        task_logic=_enum(props.text("taskLogic", "AND"), LOGICS, "AND"),
        visibility=_enum(props.text("visibility", "NORMAL"), VISIBILITIES, "NORMAL"),
        main=props.flag("isMain"),
        is_global=props.flag("isGlobal"),
        repeat_time=props.num("repeatTime", -1),
        prereqs=tuple(prereqs),
        tasks=tasks,
        rewards=rewards,
        raw=value,
    )


def _line(value: Json, entries: Iterable[Json], ord_: int) -> Line | None:
    t = Typed(value)
    lid = read_id(t, "questLineID")
    if lid is None:
        return None
    props = t.sub("properties").sub("betterquesting")
    placed: list[Entry] = []
    for e in entries:
        et = Typed(e)
        qid = read_id(et)
        if qid is None:
            continue
        size = et.num("size", 24)  # legacy single size
        placed.append(
            Entry(
                qid,
                et.num("x"),
                et.num("y"),
                et.num("sizeX", size),
                et.num("sizeY", size),
            )
        )
    return Line(
        id=lid,
        name=clean(props.text("name")),
        descr=clean(props.text("desc")),
        visibility=_enum(props.text("visibility", "NORMAL"), VISIBILITIES, "NORMAL"),
        ord=ord_,
        entries=tuple(placed),
    )


def _settings(book: Book, value: Json) -> None:
    t = Typed(value)
    props = t.sub("betterquesting") if "betterquesting" in t else t
    book.pack_name = props.text("pack_name") or None
    book.pack_version = props.num("pack_version") if "pack_version" in props else None


def _add(book: Book, quest: Quest) -> None:
    if quest.id in book.quests:
        book.problems.append(f"quest {quest.id} is listed twice; the last one is kept")
    book.quests[quest.id] = quest


def read_json(path: Path) -> Json:
    """A BQ JSON file, read whole with ``safe_open`` and parsed in memory (a partial read is
    retried)."""
    parsed: list[Json] = []

    def check(data: bytes) -> None:
        if len(data) >= JSON_LIMIT:
            raise ValueError(f"{path} is over {JSON_LIMIT >> 20} MiB")
        parsed.append(loads(data))

    try:
        read_bytes(path, validate=check, limit=JSON_LIMIT)
    except ValueError as exc:
        raise FormatError(f"{path}: {exc}") from None
    return parsed[-1]


def read_world_book(path: Path) -> Book:
    """``<world>/betterquesting/QuestDatabase.json``."""
    t = Typed(read_json(path))
    book = Book("world", path, {}, [])
    _settings(book, t.get("questSettings"))
    book.format = t.text("format") or None
    book.build = t.text("build") or None
    for n, q in enumerate(t.seq("questDatabase")):
        try:
            _add(book, parse_quest(q, book.problems))
        except FormatError as exc:
            book.problems.append(f"questDatabase entry {n}: {exc}")
    ordered: list[tuple[int, int, Json]] = []
    for n, value in enumerate(t.seq("questLines")):
        order = Typed(value).get("order")
        # BQ's tab order: the lines with an order by it, the others after them.
        ordered.append(
            (order if isinstance(order, int) and order >= 0 else 1 << 30, n, value)
        )
    for ord_, (_, n, value) in enumerate(sorted(ordered, key=lambda o: (o[0], o[1]))):
        line = _line(value, Typed(value).seq("quests"), ord_)
        if line is None:
            book.problems.append(f"questLines entry {n} has no id")
        else:
            book.lines.append(line)
    return book


def _json_files(folder: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".json"):
                yield Path(dirpath, name)


def read_default_book(folder: Path) -> Book:
    """The pack's split-format ``config/betterquesting/DefaultQuests/``."""
    book = Book("defaults", folder, {}, [])
    settings = read_json(folder / SETTINGS_FILE)
    _settings(book, settings)
    book.format = Typed(settings).text("format") or None
    lines: dict[str, tuple[Json, list[Json]]] = {}
    for sub in sorted((folder / "QuestLines").iterdir()):
        if not sub.is_dir():
            continue
        line_file = sub / LINE_FILE
        if not line_file.is_file():
            book.problems.append(f"{sub.name}: no {LINE_FILE}")
            continue
        value = read_json(line_file)
        lid = read_id(Typed(value), "questLineID")
        if lid is None:
            book.problems.append(f"{line_file}: no questLineID")
            continue
        entries = [
            read_json(f)
            for f in sorted(sub.iterdir())
            if f.suffix == ".json" and f.name != LINE_FILE
        ]
        lines[lid] = (value, entries)
    order = read_bytes(folder / LINE_ORDER_FILE).decode("utf-8", errors="replace")
    listed: list[str] = []
    for text in order.splitlines():
        head = text.split(":", 1)[0].strip()
        if not head:
            continue
        try:
            lid = encode_id(*decode_id(head))
        except ValueError:
            book.problems.append(f"{LINE_ORDER_FILE}: not a quest line id: {head!r}")
            continue
        if lid not in lines:
            book.problems.append(f"{LINE_ORDER_FILE}: line {lid} has no folder")
        elif lid not in listed:
            listed.append(lid)
    for lid in lines:
        if lid not in listed:
            # BQ's loader keeps only the lines the order file lists.
            book.problems.append(f"line {lid} is not in {LINE_ORDER_FILE}; BQ drops it")
    for ord_, lid in enumerate(listed):
        value, entries = lines[lid]
        line = _line(value, entries, ord_)
        assert line is not None
        book.lines.append(line)
    for path in _json_files(folder / "Quests"):
        try:
            _add(book, parse_quest(read_json(path), book.problems))
        except FormatError as exc:
            book.problems.append(f"{path.relative_to(folder)}: {exc}")
    return book


def read_book(world: Path, game: Path | None) -> Book:
    """The world's book, else the pack's DefaultQuests (``game`` = the instance's game folder;
    None: the folder two up from the world, if the world sits in ``saves/``)."""
    world_db = world / WORLD_DIR / DATABASE
    if world_db.is_file():
        return read_world_book(world_db)
    if game is None and world.parent.name == "saves":
        game = world.parent.parent
    defaults = game / DEFAULTS if game is not None else None
    if defaults is None or not (defaults / SETTINGS_FILE).is_file():
        raise FormatError(
            f"{world_db} doesn't exist and no DefaultQuests was found"
            + (f" at {defaults}" if defaults is not None else "")
        )
    book = read_default_book(defaults)
    book.problems.insert(
        0,
        f"{world_db} doesn't exist yet (BQ writes it on the first save): read {defaults}",
    )
    return book


def default_pack_version(game: Path | None) -> int | None:
    """The pack's DefaultQuests ``pack_version``: BQ re-imports the defaults into a world whose
    book has the same ``pack_name`` and a lower ``pack_version`` when it next loads."""
    if game is None:
        return None
    path = game / DEFAULTS / SETTINGS_FILE
    if not path.is_file():
        return None
    props = Typed(read_json(path)).sub("betterquesting")
    return props.num("pack_version") if "pack_version" in props else None


# --- progress -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Completion:
    ts: int  # epoch ms: set on completion, again on claim, 0 after a repeatable resets
    claimed: bool


@dataclass
class Progress:
    """Every player's completions in one world, and BQ's name cache."""

    done: dict[str, dict[str, Completion]]  # player uuid -> quest id -> completion
    names: dict[str, str]  # player uuid -> name
    files: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _uuid(text: object) -> str | None:
    try:
        return str(uuid.UUID(str(text)))
    except ValueError:
        return None


def read_progress(world: Path) -> Progress:
    """``<world>/betterquesting/QuestProgress/<uuid>.json`` (one per player) and
    ``NameCache.json``. A world BQ hasn't saved yet has neither: no completions."""
    folder = world / WORLD_DIR
    progress = Progress({}, {})
    names = folder / NAME_CACHE
    if names.is_file():
        for entry in Typed(read_json(names)).seq("nameCache"):
            t = Typed(entry)
            pid = _uuid(t.text("uuid"))
            if pid is not None:
                progress.names[pid] = t.text("name")
    pdir = folder / PROGRESS_DIR
    files = sorted(pdir.glob("*.json")) if pdir.is_dir() else []
    for path in files:
        owner = _uuid(path.stem)
        if owner is None:
            progress.problems.append(
                f"{path.name}: not named by a player UUID; skipped"
            )
            continue
        progress.files.append(path)
        progress.done.setdefault(owner, {})
        for entry in Typed(read_json(path)).seq("questProgress"):
            t = Typed(entry)
            qid = read_id(t)
            if qid is None:
                continue
            for c in t.seq("completed"):
                ct = Typed(c)
                who = _uuid(ct.text("uuid")) or owner
                progress.done.setdefault(who, {})[qid] = Completion(
                    ct.num("timestamp"), ct.flag("claimed")
                )
    return progress


def pick_player(progress: Progress, wanted: str | None = None) -> str:
    """The player whose frontier and tier GOS tracks: ``wanted`` (a UUID or a name), else the
    only player with a progress file, else the only name BQ has seen. ValueError otherwise."""
    known = set(progress.done) | set(progress.names)
    if wanted:
        pid = _uuid(wanted)
        if pid is not None:
            return pid
        match = [p for p, n in progress.names.items() if n.lower() == wanted.lower()]
        if len(match) == 1:
            return match[0]
        raise ValueError(
            f"no player named {wanted!r} in {NAME_CACHE}: {_who(progress, known)}"
        )
    with_files = sorted(_uuid(p.stem) or "" for p in progress.files)
    if len(with_files) == 1:
        return with_files[0]
    if not with_files and len(progress.names) == 1:
        return next(iter(progress.names))
    if not known:
        raise ValueError(
            "BQ has recorded no player in this world yet (play and save once)"
        )
    raise ValueError(
        f"several players; name one with --player: {_who(progress, known)}"
    )


def _who(progress: Progress, known: Iterable[str]) -> str:
    return (
        ", ".join(f"{progress.names.get(p, '?')} ({p})" for p in sorted(known))
        or "none"
    )


# --- unlocking, visibility, frontier --------------------------------------------------------

DONE, AVAILABLE, LOCKED = "done", "available", "locked"


def logic_result(logic: str, complete: int, total: int) -> bool:
    """BQ's ``EnumLogic.getResult``."""
    return {
        "AND": complete >= total,
        "NAND": complete < total,
        "NOR": complete == 0,
        "OR": complete > 0,
        "XNOR": complete == total - 1,
        "XOR": complete == 1,
    }.get(logic, False)


@dataclass(frozen=True)
class State:
    state: str  # done, available (unlocked, not done) or locked
    shown: bool  # the book shows it (view mode off)


def states(
    book: Book, done: Mapping[str, Mapping[str, Completion]], player: str
) -> dict[str, State]:
    """Each quest's state for ``player`` under BQ's rules:

    - complete: the player has a completion (a global quest: anyone has);
    - unlocked: no prerequisites, or ``questLogic`` holds over how many of them are complete,
      out of all it lists (a prerequisite missing from the book counts, never complete);
    - shown (``QuestCache.isQuestShown``, view mode off): ALWAYS yes, HIDDEN no, SECRET and
      UNLOCKED when complete or unlocked, NORMAL also when every prerequisite in the book is
      unlocked, COMPLETED when complete, CHAIN when it has no prerequisites or one is shown.
    """
    mine = done.get(player, {})
    anyone = {q for player_done in done.values() for q in player_done}
    quests = book.quests

    def complete(qid: str) -> bool:
        q = quests.get(qid)
        if q is None:
            return False
        return qid in anyone if q.is_global else qid in mine

    unlocked: dict[str, bool] = {}
    for qid, q in quests.items():
        if not q.prereqs:
            unlocked[qid] = True
        else:
            n = sum(1 for p in q.prereqs if complete(p))
            unlocked[qid] = logic_result(q.logic, n, len(q.prereqs))

    shown: dict[str, bool] = {}

    def is_shown(qid: str, visiting: frozenset[str] = frozenset()) -> bool:
        if qid in shown:
            return shown[qid]
        q = quests[qid]
        vis = q.visibility
        open_ = complete(qid) or unlocked[qid]
        in_book = [p for p in q.prereqs if p in quests]
        if vis == "ALWAYS":
            result = True
        elif vis == "HIDDEN":
            result = False
        elif vis in ("SECRET", "UNLOCKED"):
            result = open_
        elif vis == "NORMAL":
            result = open_ or all(unlocked[p] for p in in_book)
        elif vis == "COMPLETED":
            result = complete(qid)
        elif vis == "CHAIN":
            # BQ recurses without a guard; a cycle (never in GTNH's book) counts as not shown.
            result = not q.prereqs or any(
                is_shown(p, visiting | {qid}) for p in in_book if p not in visiting
            )
        else:
            result = True
        shown[qid] = result
        return result

    out: dict[str, State] = {}
    for qid in quests:
        state = DONE if complete(qid) else AVAILABLE if unlocked[qid] else LOCKED
        out[qid] = State(state, is_shown(qid))
    return out


def lines_shown(book: Book, st: Mapping[str, State]) -> dict[str, bool]:
    """Which quest lines the book lists (``GuiQuestLines.refreshChapterVisibility``): never a
    HIDDEN one, a COMPLETED one once one of its quests is done; UNLOCKED lines are listed
    because GTNH sets "View mode all quest line" (its default, true)."""
    out: dict[str, bool] = {}
    for line in book.lines:
        if line.visibility == "HIDDEN":
            out[line.id] = False
        elif line.visibility == "COMPLETED":
            out[line.id] = any(
                e.quest in st and st[e.quest].state == DONE for e in line.entries
            )
        else:
            out[line.id] = True
    return out


def frontier(book: Book, st: Mapping[str, State]) -> list[tuple[Line, Entry, Quest]]:
    """The quests the player can work on now, as the book shows them: unlocked, not complete,
    shown, in a listed quest line; in tab order, then top to bottom, left to right. A quest in
    two lines is listed in each."""
    listed = lines_shown(book, st)
    out: list[tuple[Line, Entry, Quest]] = []
    for line in book.lines:
        if not listed[line.id]:
            continue
        for e in sorted(line.entries, key=lambda e: (e.y, e.x, e.quest)):
            s = st.get(e.quest)
            if s is not None and s.state == AVAILABLE and s.shown:
                out.append((line, e, book.quests[e.quest]))
    return out


# --- tier detection (D64) -------------------------------------------------------------------

# Progression stages and their GT voltage tiers (ULV = 0 ... UXV = 13). Stone and steam are
# both before electricity, so both are tier 0.
STAGES: dict[str, int] = {
    "stone": 0,
    "steam": 0,
    "LV": 1,
    "MV": 2,
    "HV": 3,
    "EV": 4,
    "IV": 5,
    "LuV": 6,
    "ZPM": 7,
    "UV": 8,
    "UHV": 9,
    "UEV": 10,
    "UIV": 11,
    "UMV": 12,
    "UXV": 13,
}
FIRST_STAGE = "stone"  # no key quest: where a new world starts


@dataclass(frozen=True)
class TierKey:
    """A curated key quest: completing it means the player has reached ``stage``."""

    stage: str
    quest: str
    name: str
    note: str


@dataclass(frozen=True)
class Tier:
    stage: str
    tier: int  # GT voltage tier
    quest: str | None  # the key quest that shows it (None for the first stage)
    name: str = ""


def read_tier_keys(path: Path) -> list[TierKey]:
    """``data/curated/tier_quests.tsv``: ``stage quest name note``, stages in progression order
    (each listed at least once, after the one before it), any number of key quests per stage.
    ValueError names the line of a bad row."""
    keys: list[TierKey] = []
    order = list(STAGES)
    header: list[str] | None = None
    for n, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip() or text.startswith("#"):
            continue
        cells = text.split("\t")
        if header is None:
            header = cells
            if header[:4] != ["stage", "quest", "name", "note"]:
                raise ValueError(
                    f"{path.name}:{n}: header must be stage, quest, name, note"
                )
            continue
        if len(cells) != 4:
            raise ValueError(f"{path.name}:{n}: {len(cells)} cells, not 4")
        stage, quest, name, note = cells
        if stage not in STAGES or stage == FIRST_STAGE:
            raise ValueError(f"{path.name}:{n}: unknown stage {stage!r}")
        if keys and order.index(stage) < order.index(keys[-1].stage):
            raise ValueError(f"{path.name}:{n}: {stage} comes after {keys[-1].stage}")
        try:
            qid = encode_id(*decode_id(quest))
        except ValueError as exc:
            raise ValueError(f"{path.name}:{n}: {exc}") from None
        keys.append(TierKey(stage, qid, name, note))
    missing = [s for s in order[1:] if s not in {k.stage for k in keys}]
    if missing:
        raise ValueError(f"{path.name}: no key quest for {', '.join(missing)}")
    return keys


def detect_tier(keys: Iterable[TierKey], done: Iterable[str]) -> Tier:
    """The highest stage with a complete key quest; the first stage when none is."""
    finished = set(done)
    best = Tier(FIRST_STAGE, STAGES[FIRST_STAGE], None)
    order = list(STAGES)
    for k in keys:
        if k.quest in finished and order.index(k.stage) > order.index(best.stage):
            best = Tier(k.stage, STAGES[k.stage], k.quest, k.name)
    return best
