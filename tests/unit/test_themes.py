"""WO-10.6: the theme engine (D314, D315) on fixtures: the note format, the catalog check,
versions kept on refinement, the starter themes, the three selection modes, the tools, the
architect skill and ``gos theme``."""

from __future__ import annotations

import argparse
import datetime as dt
import io
import re
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gregtec.agents import deep_config
from gregtec.agents.tools import DEEP, FAST, build_registry
from gregtec.agents.tools import themes as theme_tools
from gregtec.agents.tools.registry import Registry
from gregtec.build import theme_cli, themes
from gregtec.db import access
from gregtec.db import blocks as block_db
from gregtec.ingest import palette as ingest
from gregtec.vault import index
from gregtec.vault import schema as vault_schema
from gregtec.vault.io import VaultError, VaultIO

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "vault-template"
TODAY = dt.date(2026, 11, 18)


def state(block: str, meta: int = 0, *, has_te: bool = False) -> ingest.BlockState:
    return ingest.BlockState(
        block=block,
        meta=meta,
        item=block,
        item_meta=meta,
        name=f"{block.split(':')[1]} {meta}",
        unlocalized="tile.x",
        mod=block.split(":")[0],
        has_te=has_te,
        opaque=True,
        full_cube=True,
        normal_cube=True,
        standard_render=True,
        liquid=False,
        falls=False,
        light=0,
        light_opacity=255,
        tab="buildingBlocks",
        hidden=False,
        faces=(),
        errors=(),
    )


# Every state the starter themes use, plus the ones the refusal tests need.
STATES = [state("gregtech:gt.blockconcretes", m) for m in (0, 3, 7)] + [
    state("gregtech:gt.blockcasings2", 0),
    state("gregtech:gt.blockcasings", 11),
    state("gregtech:gt.blockglass1", 0),
    state("gregtech:gt.blockglass1", 10),
    *(state("gregtech:gt.blockstones", m) for m in (3, 6, 7, 11, 15)),
    state("minecraft:stone_slab", 0),
    state("minecraft:glowstone", 0),
    state("minecraft:glass", 0),
    state("minecraft:quartz_block", 2),
    state("minecraft:wool", 20),
    state("minecraft:chest", 0, has_te=True),
    state("gregtech:gt.blockmachines", 0, has_te=True),
]


def make_db(home: Path, states: list[ingest.BlockState] = STATES) -> Path:
    db = access.db_path(home)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = access.connect(db)
    try:
        conn.execute(
            "INSERT INTO export_run(id, fingerprint, run_key) VALUES (1, '', 'run-a')"
        )
        block_db.write(conn, 1, ingest.Palette(tuple(states), (), ()))
        conn.commit()
    finally:
        conn.close()
    return db


@pytest.fixture
def home(tmp_path: Path) -> Path:
    shutil.copytree(TEMPLATE, tmp_path / "vault")
    make_db(tmp_path)
    return tmp_path


@pytest.fixture
def conn(home: Path) -> Iterator[sqlite3.Connection]:
    c = access.connect(access.db_path(home), readonly=True)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def vault(home: Path) -> VaultIO:
    return VaultIO(home / "vault", today=lambda: TODAY)


@pytest.fixture
def store(vault: VaultIO) -> themes.Themes:
    return themes.Themes(vault)


BRASS = {
    "name": "Brass Works",
    "prompt": "steampunk brass and dark wood, lots of windows",
    "summary": "bronze plated bricks and dark concrete",
    "fits": ["smelting"],
    "palette": [
        themes.Swatch("wall", "gregtech:gt.blockconcretes@3"),
        themes.Swatch("roof", "gregtech:gt.blockconcretes@7"),
        themes.Swatch("window", "minecraft:glass@0", "plain"),
        themes.Swatch("light", "minecraft:glowstone@0"),
    ],
    "roof": ["steep roofs"],
    "adapt": [("smelting", "brick chimneys")],
}


def note_ok(vault: VaultIO, rel: str) -> str:
    text = (vault.root / rel).read_text(encoding="utf-8")
    types = vault_schema.load_types(vault.root / "_gos" / "schema.md")
    assert vault_schema.check_text(rel, text, types) == []
    return text


# --- the note ------------------------------------------------------------------------------


def test_the_starter_themes_load_round_trip_and_pass_the_catalog(
    store: themes.Themes, conn: sqlite3.Connection
) -> None:
    found, problems = store.load()
    assert problems == []
    assert [(t.id, t.v, t.source) for t in found] == [
        ("th-foundry", 1, "shipped"),
        ("th-marble-works", 1, "shipped"),
    ]
    for t in found:
        head = "---\nid: x\nt: theme\nupd: 2026-11-18\n---\n"
        assert themes.parse(head + themes.body(t), t.id, "shipped") == t
        result = themes.check(conn, t)
        assert result.ok, result.refused
        assert set(themes.REQUIRED_ROLES) <= {s.role for s in t.palette}
        assert t.fits and all(f in themes.LINE_TYPES for f in t.fits)
        assert all(not re.search(r"@\d{4,}|:\d+@", s.block) for s in t.palette)


def test_parse_refuses_what_isnt_a_theme() -> None:
    head = "---\nid: th-x\nt: theme\nupd: 2026-11-18\n---\n"
    good = themes.body(themes.build(BRASS, "th-x", 1, "vault"))
    assert themes.parse(head + good, "th-x").name == "Brass Works"
    for broken, why in [
        (good.replace("v: 1", "v: one"), "whole number"),
        (good.replace("name: Brass Works", "name: <b>"), "<, >"),
        (good + "tools: Bash\n", "isn't a theme key"),
        (
            good.replace("window\tminecraft:glass@0", "window\t6000:0"),
            "never a numeric id",
        ),
        (good.replace("light\t", "lamp\t"), "palette role"),
        (good.replace("light\tminecraft:glowstone@0\t\n", ""), "no light entry"),
        (good.replace("adapt: smelting", "adapt: rocketry"), "not a line type"),
        (good.replace("name: Brass Works\n", "name: A\nname: B\n"), "given twice"),
    ]:
        with pytest.raises(themes.ThemeError, match=why):
            themes.parse(head + broken, "th-x")
    with pytest.raises(themes.ThemeError, match="not a t: theme"):
        themes.parse(head.replace("t: theme", "t: plan") + good, "th-x")
    with pytest.raises(themes.ThemeError, match="not a theme id"):
        themes.build(BRASS, "brass", 1, "vault")


def test_slug() -> None:
    assert themes.slug("Brass Works!") == "th-brass-works"
    with pytest.raises(themes.ThemeError):
        themes.slug("!!!")


# --- validation ----------------------------------------------------------------------------


def test_the_catalog_check_refuses_tile_entities_unknown_states_and_high_meta(
    conn: sqlite3.Connection,
) -> None:
    bad = {
        **BRASS,
        "palette": [
            *BRASS["palette"],
            themes.Swatch("accent", "minecraft:chest@0"),
            themes.Swatch("accent", "gregtech:gt.blockmachines@0"),
            themes.Swatch("accent", "minecraft:wool@20"),
            themes.Swatch("trim", "gregtech:gt.blockstones@9"),
            themes.Swatch("trim", "nomod:nothing@0"),
            themes.Swatch("floor", "minecraft:air@0"),
        ],
    }
    result = themes.check(conn, themes.build(bad, "th-bad", 1, "vault"))
    assert not result.ok and result.run_key == "run-a"
    text = "\n".join(result.refused)
    assert "minecraft:chest@0: has a tile entity" in text
    assert "gregtech:gt.blockmachines@0: GT's machine block" in text
    assert "minecraft:wool@20: meta 20 > 15" in text
    assert "gregtech:gt.blockstones@9: meta 9 is not a placeable state" in text
    assert "nomod:nothing@0: not in the block catalog" in text
    assert "minecraft:air@0: air" in text
    # no world registry in a theme check: nothing says "not in the world"
    assert "registry" not in text


def test_no_catalog_is_a_catalog_error(tmp_path: Path) -> None:
    db = access.db_path(tmp_path)
    db.parent.mkdir(parents=True)
    access.connect(db).close()
    c = access.connect(db, readonly=True)
    try:
        with pytest.raises(themes.palette.CatalogError):
            themes.check(c, themes.build(BRASS, "th-x", 1, "vault"))
    finally:
        c.close()


# --- saving and versions -------------------------------------------------------------------


def test_create_then_refine_twice_keeps_every_version(
    store: themes.Themes, conn: sqlite3.Connection, vault: VaultIO
) -> None:
    first = store.save(conn, BRASS)
    assert (first.theme.id, first.theme.v, first.archived) == (
        "th-brass-works",
        1,
        None,
    )
    assert first.path == "themes/th-brass-works.md"
    note_ok(vault, first.path)
    second = store.save(
        conn,
        {
            "change": "reinforced glass windows",
            "palette": [
                *BRASS["palette"][:2],
                themes.Swatch("window", "gregtech:gt.blockglass1@10"),
                BRASS["palette"][3],
            ],
        },
        "th-brass-works",
    )
    assert (
        second.theme.v == 2
        and second.archived == "themes/versions/th-brass-works--v1.md"
    )
    third = store.save(
        conn, {"change": "flat roofs", "roof": ["flat roofs"]}, "th-brass-works"
    )
    assert third.theme.v == 3
    assert store.versions("th-brass-works") == [1, 2]
    for rel in (
        third.path,
        *(f"themes/versions/th-brass-works--v{n}.md" for n in (1, 2)),
    ):
        note_ok(vault, rel)
    now = store.get("th-brass-works")
    assert now.v == 3 and now.roof == ("flat roofs",)
    assert now.palette[2].block == "gregtech:gt.blockglass1@10"  # kept from v2
    assert now.prompt == BRASS["prompt"]  # the prompt it was made from
    assert now.change == ("v2 reinforced glass windows", "v3 flat roofs")
    v1 = store.get("th-brass-works", 1)
    assert (
        v1.v == 1
        and v1.roof == ("steep roofs",)
        and v1.palette[2].block == "minecraft:glass@0"
    )
    assert store.get("th-brass-works", 2).roof == ("steep roofs",)
    with pytest.raises(themes.ThemeError, match="no version 7"):
        store.get("th-brass-works", 7)


def test_a_refused_entry_writes_nothing(
    store: themes.Themes, conn: sqlite3.Connection, vault: VaultIO
) -> None:
    store.save(conn, BRASS)
    before = sorted(
        p.relative_to(vault.root) for p in vault.root.rglob("*") if p.is_file()
    )
    with pytest.raises(
        themes.ThemeError, match="not saved: minecraft:chest@0: has a tile"
    ):
        store.save(
            conn,
            {
                "change": "chests as trim",
                "palette": [
                    *BRASS["palette"],
                    themes.Swatch("trim", "minecraft:chest@0"),
                ],
            },
            "th-brass-works",
        )
    with pytest.raises(themes.ThemeError, match="not saved"):
        store.save(
            conn,
            {
                **BRASS,
                "name": "Chest House",
                "palette": [
                    *BRASS["palette"],
                    themes.Swatch("trim", "minecraft:chest@0"),
                ],
            },
        )
    after = sorted(
        p.relative_to(vault.root) for p in vault.root.rglob("*") if p.is_file()
    )
    assert after == before and store.get("th-brass-works").v == 1


def test_create_and_refine_need_their_fields(
    store: themes.Themes, conn: sqlite3.Connection
) -> None:
    no_prompt = {k: v for k, v in BRASS.items() if k != "prompt"}
    with pytest.raises(themes.ThemeError, match="needs the prompt"):
        store.save(conn, no_prompt)
    store.save(conn, BRASS)
    with pytest.raises(themes.ThemeError, match="already a theme th-brass-works"):
        store.save(conn, BRASS)
    with pytest.raises(themes.ThemeError, match="says what changes"):
        store.save(conn, {"roof": ["flat"]}, "th-brass-works")
    with pytest.raises(themes.ThemeError, match="no theme th-nope to change"):
        store.save(conn, {**BRASS, "change": "x"}, "th-nope")


def test_refining_a_starter_makes_claytons_copy_and_never_touches_gos(
    store: themes.Themes, conn: sqlite3.Connection, vault: VaultIO, home: Path
) -> None:
    shipped = (TEMPLATE / "_gos" / "themes" / "th-foundry.md").read_bytes()
    live_gos = (vault.root / "_gos" / "themes" / "th-foundry.md").read_bytes()
    saved = store.save(
        conn, {"change": "more glass", "window": ["glass walls"]}, "th-foundry"
    )
    assert saved.theme.v == 2 and saved.theme.source == "vault"
    assert saved.archived == "themes/versions/th-foundry--v1.md"
    assert store.get("th-foundry").source == "vault"
    assert (
        store.get("th-foundry", 1).window
        == themes.parse(shipped.decode("utf-8"), "th-foundry").window
    )
    assert (TEMPLATE / "_gos" / "themes" / "th-foundry.md").read_bytes() == shipped
    assert (vault.root / "_gos" / "themes" / "th-foundry.md").read_bytes() == live_gos
    assert not list((vault.root / "me").glob("th-*"))


def test_the_note_tools_cant_write_themes(vault: VaultIO) -> None:
    for call in (
        lambda: vault.write("themes/th-x.md", "name: x"),
        lambda: vault.append("themes/th-foundry.md", "name: x"),
    ):
        with pytest.raises(VaultError, match="saved with theme_save"):
            call()
    with pytest.raises(VaultError, match="theme versions go in themes/ only"):
        vault.write("plans/pl-1.md", "title: x", op="theme")


def test_the_index_skips_the_starters_and_lists_claytons(
    store: themes.Themes, conn: sqlite3.Connection, vault: VaultIO
) -> None:
    store.save(conn, BRASS)
    assert index.build(vault.root, TODAY)
    text = (vault.root / "INDEX.md").read_text(encoding="utf-8")
    assert "themes/th-brass-works.md" in text and "_gos/themes" not in text


# --- selection (D315) ----------------------------------------------------------------------


def test_the_three_selection_modes(store: themes.Themes) -> None:
    found, _ = store.load()
    chosen = themes.select(
        found, theme_id="th-foundry", prompt="taller", line_type="smelting"
    )
    assert (chosen.mode, chosen.theme and chosen.theme.id, chosen.prompt) == (
        themes.THEME_MODE,
        "th-foundry",
        "taller",
    )
    assert chosen.adaptation and "chimneys" in chosen.adaptation
    bare = themes.select(found, theme_id="th-foundry")
    assert bare.mode == themes.THEME_MODE and bare.prompt == ""
    prompt = themes.select(found, prompt="a glass dome", line_type="chemistry")
    assert prompt.mode == themes.PROMPT_MODE and prompt.theme is None
    assert prompt.concept == themes.LINE_TYPES["chemistry"]
    blank = themes.select(found, line_type="chemistry")
    assert blank.mode == themes.BLANK_MODE
    assert blank.theme is not None and blank.theme.id == "th-marble-works"
    assert blank.why == "fits chemistry" and "pipework and tanks" in blank.concept
    heavy = themes.select(found, line_type="smelting")
    assert heavy.theme is not None and heavy.theme.id == "th-foundry"
    assert "heavy industrial" in heavy.concept
    with pytest.raises(themes.ThemeError, match="no theme 'th-nope'"):
        themes.select(found, theme_id="th-nope")
    with pytest.raises(themes.ThemeError, match="not a line type"):
        themes.select(found, line_type="rocketry")
    assert themes.select([], line_type="power").theme is None


def test_blank_mode_follows_the_themes_used_nearby(
    store: themes.Themes, home: Path
) -> None:
    found, _ = store.load()
    for use in (
        themes.Use("b1", "th-marble-works", 0, 100, 100),
        themes.Use("b2", "th-marble-works", 0, 300, 100),
        themes.Use("b3", "th-foundry", 0, 50, 0),
        themes.Use("b4", "th-foundry", -1, 0, 0),  # the Nether: not nearby
        themes.Use("b5", "th-gone", 0, 0, 0),  # a theme that no longer exists
        themes.Use("b6", "th-foundry", 0, 5000, 0),  # too far
    ):
        themes.record_use(home, use)
    themes.record_use(home, themes.Use("b6", "th-foundry", 0, 60, 0))  # b6 moved
    (home / themes.USES_FILE).open("a", encoding="utf-8").close()
    with (home / themes.USES_FILE).open("a", encoding="utf-8") as f:
        f.write("not json\n")
    uses = themes.load_uses(home)
    assert len(uses) == 6
    nearby = themes.near(uses, 0, 0, 0)
    assert [u.build for u, _ in nearby] == [
        "b5",
        "b3",
        "b6",
        "b1",
    ]  # b2 is 316 blocks away
    # smelting fits the foundry, but two builds nearby use the foundry: consistency first
    brief = themes.select(found, line_type="chemistry", nearby=nearby)
    assert brief.theme is not None and brief.theme.id == "th-foundry"
    assert brief.why.startswith("used by 2 builds within 256 blocks")
    # a tie in count goes to the nearest
    one_each = themes.near(uses[:3], 0, 0, 0)
    tie = themes.select(found, nearby=one_each)
    assert tie.theme is not None and tie.theme.id == "th-foundry"


# --- the tools -----------------------------------------------------------------------------


@pytest.fixture
def reg(home: Path, vault: VaultIO) -> Registry:
    return build_registry(access.db_path(home), vault=vault)


def test_the_tools_lanes(reg: Registry) -> None:
    fast = {s["name"] for s in reg.specs(FAST)}
    deep = {s["name"] for s in reg.specs(DEEP)}
    assert {"theme_list", "theme_get"} <= fast and "theme_save" not in fast
    assert {"theme_list", "theme_get", "theme_save"} <= deep
    main, agents = deep_config.tool_sets(reg)
    assert (
        "mcp__gos__theme_save" in agents["architect"] and "mcp__gos__theme_save" in main
    )
    assert "mcp__gos__theme_save" not in agents["planner"]


def test_theme_tools_create_refine_and_refuse(reg: Registry, vault: VaultIO) -> None:
    listed = reg.call("theme_list", {}, lane=FAST).text
    assert "th-foundry\tFoundry\t1\tshipped" in listed and "rows: 1-2 of 2" in listed
    made = reg.call(
        "theme_save",
        {
            "name": "Brass Works",
            "prompt": "brass and dark concrete",
            "fits": "smelting, power",
            "palette": "wall gregtech:gt.blockconcretes@3 dark bricks\n"
            "roof gregtech:gt.blockconcretes@7\nwindow minecraft:glass@0\n"
            "light minecraft:glowstone@0",
            "roof": "steep roofs\nslate edges",
            "adapt": "smelting: brick chimneys",
        },
        lane=DEEP,
    )
    assert not made.is_error, made.text
    assert "saved: themes/th-brass-works.md" in made.text
    assert "say: Saved Brass Works as a new theme, version 1." in made.text
    refined = reg.call(
        "theme_save",
        {"id": "th-brass-works", "change": "flat roofs", "roof": "flat roofs"},
        lane=DEEP,
    )
    assert not refined.is_error, refined.text
    assert "kept: themes/versions/th-brass-works--v1.md" in refined.text
    got = reg.call("theme_get", {"id": "th-brass-works"}, lane=FAST).text
    assert (
        "v: 2" in got and "roof: flat roofs" in got and "change: v2 flat roofs" in got
    )
    assert "wall\tgregtech:gt.blockconcretes@3\tdark bricks" in got
    assert "versions: 1, 2 (current 2)" in got
    old = reg.call("theme_get", {"id": "th-brass-works", "v": 1}, lane=FAST).text
    assert "roof: steep roofs" in old and "roof: slate edges" in old
    refused = reg.call(
        "theme_save",
        {
            "id": "th-brass-works",
            "change": "chest trim",
            "palette": "wall gregtech:gt.blockconcretes@3\nroof gregtech:gt.blockconcretes@7\n"
            "window minecraft:glass@0\nlight minecraft:glowstone@0\ntrim minecraft:chest@0",
        },
        lane=DEEP,
    )
    assert refused.is_error
    assert (
        "th-brass-works not saved: minecraft:chest@0: has a tile entity" in refused.text
    )
    for bad, why in [
        ({"id": "th-brass-works", "change": "x", "palette": "wall"}, "a palette line"),
        ({"id": "th-brass-works", "change": "x", "adapt": "chimneys"}, "adapt is"),
        ({"id": "th-nope"}, "no theme 'th-nope'"),
    ]:
        out = reg.call("theme_save" if "change" in bad else "theme_get", bad, lane=DEEP)
        assert out.is_error and why in out.text, out.text
    assert reg.call("theme_save", {"name": "x"}, lane=FAST).is_error  # deep lane only


def test_theme_save_without_a_catalog(tmp_path: Path) -> None:
    shutil.copytree(TEMPLATE, tmp_path / "vault")
    make_db(tmp_path, [])
    r = build_registry(access.db_path(tmp_path), vault=VaultIO(tmp_path / "vault"))
    out = r.call("theme_save", {"id": "th-foundry", "change": "x"}, lane=DEEP)
    assert out.is_error and "no block catalog" in out.text


def test_fields_from_tool_arguments() -> None:
    out = theme_tools.fields_from(
        {"fits": "chemistry, ,power", "motif": " a \n\n b ", "palette": "wall x:y@1"}
    )
    assert out["fits"] == ["chemistry", "power"] and out["motif"] == ["a", "b"]
    assert out["palette"] == [themes.Swatch("wall", "x:y@1", "")]


# --- the skill and the CLI -----------------------------------------------------------------


def test_the_architect_skill(reg: Registry, tmp_path: Path) -> None:
    path = TEMPLATE / "_gos" / "skills" / "architect" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines())
    assert meta["name"] == "architect" and set(meta) == {"name", "description"}
    assert len(meta["description"]) <= 1024
    for value in meta.values():
        assert not re.search(r": | #|^[-?:,\[\]{}#&*!|>'\"%@`]", value), value
    named = set(re.findall(r"`([a-z_]+)\(", body))
    assert {"theme_list", "theme_get", "theme_save", "block_find"} <= named
    assert named <= {t.name for t in reg.tools(DEEP)}
    assert "!`" not in body and not re.search(r"r:[\w.]+/[0-9a-f]{12}", body)
    assert "GOS never pastes machines" in body and "first sentence" in body
    plugin = deep_config.build_plugin(TEMPLATE / "_gos", tmp_path / "plugin")
    assert "gos:architect" in deep_config.skill_names(plugin)


def test_gos_theme(home: Path, vault: VaultIO, conn: sqlite3.Connection) -> None:
    cfg = home / "gos.toml"
    cfg.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")

    def run(*argv: str) -> tuple[int, str]:
        out = io.StringIO()
        parser = argparse.ArgumentParser(prog="gos")
        theme_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["theme", *argv, "--config", str(cfg)])
        return theme_cli.run(args, out), out.getvalue()

    code, text = run("list")
    assert code == 0 and "th-foundry\tv1\tshipped\tFoundry" in text
    code, text = run("check")
    assert code == 0 and "ok      th-foundry v1 (catalog run run-a)" in text
    themes.Themes(vault).save(conn, BRASS)
    (vault.root / "themes" / "th-broken.md").write_text(
        "no frontmatter", encoding="utf-8"
    )
    code, text = run("check", "th-foundry")
    assert code == 0
    code, text = run("check")
    assert code == 1 and "not loaded: th-broken" in text
    code, text = run("check", "th-nope")
    assert code == 2
    code, text = run("select", "--line-type", "smelting")
    assert (
        code == 0
        and "mode: blank" in text
        and "theme: th-brass-works (fits smelting)" in text
    )
    code, text = run("select", "--prompt", "a glass dome")
    assert code == 0 and "mode: prompt" in text and "theme: -" in text
    code, text = run("select", "--theme", "th-brass-works", "--line-type", "smelting")
    assert code == 0 and "adaptation: brick chimneys" in text
    themes.record_use(home, themes.Use("b1", "th-brass-works", 0, 10, 10))
    code, text = run("select", "--at", "0", "0", "0")
    assert code == 0 and "theme: th-brass-works (used by 1 build within" in text
