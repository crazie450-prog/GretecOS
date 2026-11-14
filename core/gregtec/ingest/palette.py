"""The block catalog's game data from an export run's ``palette.jsonl`` and ``textures.jsonl``
(WO-10.3, plan 7.10 "Palette rule", D307).

GregtecLink's palette pass runs on Export's client (docs/contracts/gregteclink-export-v1.md):

- ``palette.jsonl``: one line per placeable block state, sorted by block registry name, then
  meta, then the placing item's meta: the block's registry name and the meta it is placed at,
  the item that places it, its display name, Forge's ``hasTileEntity(meta)`` and what a shell
  needs to choose blocks (opaque, full cube, standard render, liquid, falls, light, light
  opacity, creative tab, mod), and the six faces' icon names. A block whose state can't be read
  is a line with ``error``.
- ``textures.jsonl``: one line per icon name a face uses: the PNG it resolves to in the loaded
  resource packs, its size, sha256 and average colour.

Keys are registry name + meta only (CLAUDE.md: never numeric IDs): a line carrying a numeric
``id`` is refused. Read only from runs whose ``parts.palette`` is ``ok``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILE = "palette.jsonl"
TEXTURES = "textures.jsonl"
SIDES = 6  # down, up, north, south, west, east (Minecraft's side order)
NUMERIC_ID_KEYS = frozenset({"id", "block_id", "item_id"})
_RGB = re.compile(r"#[0-9a-f]{6}")
_FLAGS = ("opaque", "full_cube", "normal_cube", "standard_render", "liquid")


class FormatError(ValueError):
    """Not the shape GregtecLink writes."""


@dataclass(frozen=True)
class BlockState:
    """One placeable block state: ``block``@``meta``, placed by ``item``@``item_meta``."""

    block: str
    meta: int
    item: str | None
    item_meta: int
    name: str | None
    unlocalized: str | None
    mod: str
    has_te: bool
    opaque: bool | None
    full_cube: bool | None
    normal_cube: bool | None
    standard_render: bool | None
    liquid: bool | None
    falls: bool
    light: int | None
    light_opacity: int | None
    tab: str | None
    hidden: bool  # in no creative tab: the plain item (meta 0) still places it
    faces: tuple[str | None, ...]  # SIDES icon names
    errors: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.block}@{self.meta}"


@dataclass(frozen=True)
class Texture:
    icon: str
    path: str
    sha256: str | None
    width: int | None
    height: int | None
    rgb: str | None  # "#rrggbb", the alpha-weighted mean of the first frame
    alpha: int | None  # mean alpha, 0..255
    error: str | None


@dataclass(frozen=True)
class Palette:
    states: tuple[BlockState, ...]
    textures: tuple[Texture, ...]
    errors: tuple[
        tuple[str, str], ...
    ]  # (block, why) for blocks whose state didn't read


def status(manifest: Mapping[str, Any]) -> str:
    """``parts.palette``; ``absent`` for a run made before WO-10.3."""
    parts = manifest.get("parts")
    value = parts.get("palette") if isinstance(parts, dict) else None
    return value if isinstance(value, str) else "absent"


def _str(d: Mapping[str, Any], key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v:
        raise FormatError(f"{key} is not a string")
    return v


def _opt_str(d: Mapping[str, Any], key: str) -> str | None:
    v = d.get(key)
    if v is not None and not isinstance(v, str):
        raise FormatError(f"{key} is not a string")
    return v


def _int(d: Mapping[str, Any], key: str) -> int:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise FormatError(f"{key} is not an integer")
    return v


def _opt_int(d: Mapping[str, Any], key: str) -> int | None:
    v = d.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise FormatError(f"{key} is not an integer")
    return v


def _opt_bool(d: Mapping[str, Any], key: str) -> bool | None:
    v = d.get(key)
    if v is not None and not isinstance(v, bool):
        raise FormatError(f"{key} is not a boolean")
    return v


def _bool(d: Mapping[str, Any], key: str, default: bool | None = None) -> bool:
    v = d.get(key, default)
    if not isinstance(v, bool):
        raise FormatError(f"{key} is not a boolean")
    return v


def _no_ids(d: Mapping[str, Any]) -> None:
    bad = NUMERIC_ID_KEYS & set(d)
    if bad:
        raise FormatError(f"numeric id key {min(bad)!r} (keys are registry names)")


def parse_state(d: Mapping[str, Any]) -> BlockState | tuple[str, str]:
    """One ``palette.jsonl`` line: a state, or (block, why) for a block that didn't read."""
    _no_ids(d)
    block = _str(d, "block")
    if "error" in d:
        return block, str(d["error"])
    placer = d.get("item")
    if not isinstance(placer, dict):
        raise FormatError("item is not an object")
    _no_ids(placer)
    faces = d.get("faces")
    if not isinstance(faces, list) or len(faces) != SIDES:
        raise FormatError(f"faces is not a list of {SIDES}")
    if not all(f is None or isinstance(f, str) for f in faces):
        raise FormatError("faces holds a value that is not a string")
    errors = d.get("errors", [])
    if not isinstance(errors, list):
        raise FormatError("errors is not a list")
    meta = _int(d, "meta")
    item_meta = _int(placer, "meta")
    flags = {k: _opt_bool(d, k) for k in _FLAGS}
    return BlockState(
        block=block,
        meta=meta,
        item=_opt_str(placer, "item"),
        item_meta=item_meta,
        name=_opt_str(d, "name"),
        unlocalized=_opt_str(d, "unlocalized"),
        mod=_str(d, "mod"),
        has_te=_bool(d, "has_te"),
        opaque=flags["opaque"],
        full_cube=flags["full_cube"],
        normal_cube=flags["normal_cube"],
        standard_render=flags["standard_render"],
        liquid=flags["liquid"],
        falls=_bool(d, "falls", False),
        light=_opt_int(d, "light"),
        light_opacity=_opt_int(d, "light_opacity"),
        tab=_opt_str(d, "tab"),
        hidden=_bool(d, "hidden", False),
        faces=tuple(faces),
        errors=tuple(str(e) for e in errors),
    )


def parse_texture(d: Mapping[str, Any]) -> Texture:
    rgb = _opt_str(d, "rgb")
    if rgb is not None and not _RGB.fullmatch(rgb):
        raise FormatError(f"rgb {rgb!r} is not #rrggbb")
    alpha = _opt_int(d, "alpha")
    if alpha is not None and not 0 <= alpha <= 255:
        raise FormatError(f"alpha {alpha} is not 0..255")
    return Texture(
        icon=_str(d, "icon"),
        path=_str(d, "path"),
        sha256=_opt_str(d, "sha256"),
        width=_opt_int(d, "width"),
        height=_opt_int(d, "height"),
        rgb=rgb,
        alpha=alpha,
        error=_opt_str(d, "error"),
    )


def _lines(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise FormatError(f"{path.name} line {n}: {exc}") from None
            if not isinstance(d, dict):
                raise FormatError(f"{path.name} line {n}: not an object")
            yield n, d


def read(run: Path) -> Palette:
    """A run's palette and textures; ``FormatError`` if a line isn't GregtecLink's shape, or a
    state (``block``@``meta``, placing item) is listed twice."""
    states: list[BlockState] = []
    errors: list[tuple[str, str]] = []
    seen: set[tuple[str, int, str | None, int]] = set()
    for n, d in _lines(run / FILE):
        try:
            got = parse_state(d)
        except (FormatError, KeyError, TypeError) as exc:
            raise FormatError(f"{FILE} line {n}: {exc}") from None
        if isinstance(got, tuple):
            errors.append(got)
            continue
        ident = (got.block, got.meta, got.item, got.item_meta)
        if ident in seen:
            raise FormatError(f"{FILE} line {n}: {got.key} listed twice for one item")
        seen.add(ident)
        states.append(got)
    textures: list[Texture] = []
    icons: set[str] = set()
    for n, d in _lines(run / TEXTURES):
        try:
            t = parse_texture(d)
        except FormatError as exc:
            raise FormatError(f"{TEXTURES} line {n}: {exc}") from None
        if t.icon in icons:
            raise FormatError(f"{TEXTURES} line {n}: icon {t.icon!r} listed twice")
        icons.add(t.icon)
        textures.append(t)
    return Palette(tuple(states), tuple(textures), tuple(errors))
