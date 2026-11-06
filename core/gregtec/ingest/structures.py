"""Reader for gtnh-extractor's multiblock dataset, schema v3 (plan sections 5.2 step 7 and 7.10).

MrBruh's gtnh-extractor (Apache-2.0, run as an external tool by :mod:`gregtec.ingest.extractor`,
D124) builds every StructureLib controller in a dev server and writes ``<out>/multiblocks/``: one
``<registry>_<meta>.json`` per controller plus ``_meta.json`` [G§3.1][A§4.2]. This module is a
re-implementation from that output format, not a copy of the extractor's own Python reader.

- A controller file: ``{schema, controller{registry_name, meta, display_name, source_class,
  facing_convention}, variants[], substitutions{channel: [{channel_value, block, meta}]},
  failures[]}``. Each variant (one per shape the trigger stack size selects):
  ``{trigger_stack_size, channels{}, blocks[{d, block, meta}], hints[], hatch_slots[{d, kinds[],
  output_layer?}], bbox}``. ``d = [dx, dy, dz]`` is the offset from the controller, whose front
  faces NORTH (-Z).
- ``_meta.json``: ``{schema, pack_version, mod_versions{}, generated_at, extractor_sha,
  controller_count, failures[{registry_name, reason}], untranslated_names[], census?}``.

Every block is keyed by registry name + meta, never a numeric ID (EndlessIDs). Hints (hologram
dots) are read past: ``hatch_slots`` carries what may go in a cell. Unknown keys are ignored; the
``schema`` field gates the format. The dataset is data, not instructions.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3
MULTIBLOCKS = "multiblocks"
META = "_meta.json"
GT_MOD = "GT5-Unofficial"  # the label the extractor records GT's version under
FACING = "NORTH (-Z)"  # facing_convention must say this (offsets assume it)
# GT's getLocalName() hands back the lang key when a translation is missing [G§3.1].
_LANG_KEY = re.compile(r"[\w.]+\.name")

Offset = tuple[int, int, int]


class FormatError(ValueError):
    """A dataset file is missing, malformed, or not what this reader understands."""


class _Malformed(ValueError):
    """One value in a file is wrong; the reader re-raises it as a FormatError naming the file."""


@dataclass(frozen=True, slots=True)
class Block:
    d: Offset
    block: str  # registry name
    meta: int


@dataclass(frozen=True, slots=True)
class HatchSlot:
    d: Offset
    kinds: tuple[str, ...]  # GT HatchElement names, e.g. "InputBus"
    output_layer: int | None  # a tower layer's output list index, else None


@dataclass(frozen=True, slots=True)
class Substitution:
    channel_value: int
    block: str
    meta: int


@dataclass(frozen=True, slots=True)
class Variant:
    trigger_stack_size: int
    channels: Mapping[str, int]
    blocks: tuple[Block, ...]
    hatch_slots: tuple[HatchSlot, ...]
    size: Offset  # bbox: x, y, z extent of the blocks

    @property
    def key(self) -> str:
        """``t<stack>`` plus ``;<channel>=<value>`` per set channel, sorted: stable per shape."""
        return "".join(
            [f"t{self.trigger_stack_size}"]
            + [f";{k}={v}" for k, v in sorted(self.channels.items())]
        )


@dataclass(frozen=True, slots=True)
class Controller:
    registry_name: str
    meta: int
    name: str | None  # None when the dump recorded an untranslated lang key
    class_name: str
    variants: tuple[Variant, ...]
    substitutions: Mapping[str, tuple[Substitution, ...]]
    failures: tuple[str, ...]  # the extractor's caveats for this controller

    @property
    def family(self) -> str:
        """The top-level package of the controller class: gregtech, gtPlusPlus, tectech, ..."""
        return self.class_name.split(".", 1)[0]


@dataclass(frozen=True, slots=True)
class Failure:
    registry_name: str  # "<registry>#<meta>"
    reason: str


@dataclass(frozen=True, slots=True)
class Meta:
    pack_version: str
    mod_versions: Mapping[str, str]
    generated_at: str
    extractor_sha: str
    controller_count: int
    failures: tuple[Failure, ...]
    untranslated: int  # names the extractor recovered from GregTech.lang (#231)

    @property
    def gt_version(self) -> str | None:
        return self.mod_versions.get(GT_MOD)


@dataclass(frozen=True, slots=True)
class Dataset:
    path: Path  # the multiblocks/ folder
    meta: Meta
    controllers: tuple[Controller, ...]  # sorted by (registry_name, meta)


def multiblocks_dir(path: Path) -> Path:
    """``path`` itself if it holds ``_meta.json``, else ``path/multiblocks``."""
    return path if (path / META).is_file() else path / MULTIBLOCKS


def read_dataset(path: Path, *, gt_version: str | None) -> Dataset:
    """Read and check a whole dump: the extractor's ``-PdatasetOut`` folder or its ``multiblocks/``.

    ``gt_version`` is the GT pin the dump must have been made against (``None`` skips the check).
    One bad file fails the whole read, so a broken dump never loads halfway.
    """
    folder = multiblocks_dir(path)
    if not (folder / META).is_file():
        raise FormatError(
            f"{path}: no {MULTIBLOCKS}/{META}; not a gtnh-extractor dataset"
        )
    meta = _read_meta(folder / META)
    if gt_version is not None and meta.gt_version != gt_version:
        raise FormatError(
            f"{folder / META}: made against {GT_MOD} {meta.gt_version!r}, but the pin is "
            f"{gt_version}; re-run `gos structures extract` for this pack"
        )
    controllers = []
    seen: dict[tuple[str, int], str] = {}
    for f in sorted(folder.glob("*.json")):
        if f.name == META:
            continue
        c = _read_controller(f)
        key = (c.registry_name, c.meta)
        if key in seen:
            raise FormatError(f"{f.name}: controller {key} is also in {seen[key]}")
        seen[key] = f.name
        controllers.append(c)
    if len(controllers) != meta.controller_count:
        raise FormatError(
            f"{folder}: {len(controllers)} controller files, but {META} says "
            f"{meta.controller_count}; the dump is incomplete"
        )
    controllers.sort(key=lambda c: (c.registry_name, c.meta))
    return Dataset(folder, meta, tuple(controllers))


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormatError(f"{path.name}: {exc}") from None
    if not isinstance(data, dict):
        raise FormatError(f"{path.name}: not a JSON object")
    if data.get("schema") != SCHEMA_VERSION:
        raise FormatError(
            f"{path.name}: dataset schema {data.get('schema')!r}; this reader reads "
            f"{SCHEMA_VERSION}"
        )
    return data


def _read_meta(path: Path) -> Meta:
    d = _load(path)
    try:
        versions = _obj(d.get("mod_versions", {}), "mod_versions")
        failures = tuple(
            Failure(
                _str(_obj(f, "failure").get("registry_name"), "failure registry_name"),
                _str(f.get("reason"), "failure reason"),
            )
            for f in _list(d.get("failures", []), "failures")
        )
        return Meta(
            pack_version=_str(d.get("pack_version"), "pack_version"),
            mod_versions={
                _str(k, "mod name"): _str(v, f"{k} version")
                for k, v in versions.items()
            },
            generated_at=_str(d.get("generated_at"), "generated_at"),
            extractor_sha=_str(d.get("extractor_sha"), "extractor_sha"),
            controller_count=_int(d.get("controller_count"), "controller_count"),
            failures=failures,
            untranslated=len(
                _list(d.get("untranslated_names", []), "untranslated_names")
            ),
        )
    except _Malformed as exc:
        raise FormatError(f"{path.name}: {exc}") from None


def _read_controller(path: Path) -> Controller:
    d = _load(path)
    try:
        c = _obj(d.get("controller"), "controller")
        facing = c.get("facing_convention")
        if not isinstance(facing, str) or FACING not in facing:
            raise _Malformed(f"facing_convention does not say the front faces {FACING}")
        name = _str(c.get("display_name"), "display_name")
        variants = tuple(
            _variant(v) for v in _list(d.get("variants"), "variants", nonempty=True)
        )
        keys = [v.key for v in variants]
        if len(set(keys)) != len(keys):
            raise _Malformed(f"two variants with one key in {keys}")
        subs = {
            _str(channel, "channel"): tuple(
                Substitution(
                    _int(_obj(s, "substitution").get("channel_value"), "channel_value"),
                    _str(s.get("block"), "substitution block"),
                    _int(s.get("meta", 0), "substitution meta"),
                )
                for s in _list(entries, f"substitutions.{channel}")
            )
            for channel, entries in _obj(
                d.get("substitutions", {}), "substitutions"
            ).items()
        }
        return Controller(
            registry_name=_str(c.get("registry_name"), "registry_name"),
            meta=_int(c.get("meta"), "meta"),
            name=None if _LANG_KEY.fullmatch(name) else name,
            class_name=_str(c.get("source_class"), "source_class"),
            variants=variants,
            substitutions=subs,
            failures=tuple(
                _str(f, "failure") for f in _list(d.get("failures", []), "failures")
            ),
        )
    except _Malformed as exc:
        raise FormatError(f"{path.name}: {exc}") from None


def _variant(raw: object) -> Variant:
    v = _obj(raw, "variant")
    stack = _int(v.get("trigger_stack_size"), "trigger_stack_size")
    if stack < 1:
        raise _Malformed(f"trigger_stack_size {stack} is below 1")
    where = f"variant t{stack}"
    blocks = tuple(
        Block(
            _offset(_obj(b, "block").get("d"), f"{where} block d"),
            _str(b.get("block"), f"{where} block"),
            _int(b.get("meta", 0), f"{where} block meta"),
        )
        for b in _list(v.get("blocks"), f"{where} blocks", nonempty=True)
    )
    cells = {b.d for b in blocks}
    if len(cells) != len(blocks):
        raise _Malformed(f"{where}: a cell is listed twice")
    slots = []
    for raw_slot in _list(v.get("hatch_slots", []), f"{where} hatch_slots"):
        s = _obj(raw_slot, "hatch slot")
        d = _offset(s.get("d"), f"{where} hatch slot d")
        if d not in cells:
            raise _Malformed(f"{where}: hatch slot {list(d)} is not a block cell")
        layer = s.get("output_layer")
        slots.append(
            HatchSlot(
                d,
                tuple(
                    _str(k, "hatch kind")
                    for k in _list(s.get("kinds"), "kinds", nonempty=True)
                ),
                None if layer is None else _int(layer, "output_layer"),
            )
        )
    size = _offset(v.get("bbox"), f"{where} bbox")
    extent = tuple(
        max(b.d[i] for b in blocks) - min(b.d[i] for b in blocks) + 1 for i in range(3)
    )
    if size != extent:
        raise _Malformed(
            f"{where}: bbox {list(size)} is not the blocks' extent {list(extent)}"
        )
    channels = {
        _str(k, "channel"): _int(x, f"channel {k}")
        for k, x in _obj(v.get("channels", {}), f"{where} channels").items()
    }
    return Variant(stack, channels, blocks, tuple(slots), (size[0], size[1], size[2]))


def _int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Malformed(f"{what} is not an integer: {value!r}")
    return value


def _str(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise _Malformed(f"{what} is not a non-empty string: {value!r}")
    return value


def _obj(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Malformed(f"{what} is not an object")
    return value


def _list(value: object, what: str, *, nonempty: bool = False) -> Sequence[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        raise _Malformed(f"{what} is not a {'non-empty ' if nonempty else ''}list")
    return value


def _offset(value: object, what: str) -> Offset:
    items = _list(value, what)
    if len(items) != 3:
        raise _Malformed(f"{what} is not [x, y, z]: {value!r}")
    return (_int(items[0], what), _int(items[1], what), _int(items[2], what))
