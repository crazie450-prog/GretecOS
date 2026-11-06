"""CropsNH crop data (WO-1.10, D134-D137) from a small self-authored export run.

The files follow CropsNH 2.0.140's dumper formats in shape only (``gregtec.ingest.crops``): made-up
crops, recipes, pools and soils that exercise each parser rule (quoted CSV fields, the unpadded
chance, NBT after a drop's name, compound soil lists, wildcard alternate seeds, catalysts, CRLF
lines, another charset), plus NEI lines of CropsNH's tabs to key drops. Never pack data.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gregtec import cli
from gregtec.db import access, build, report
from gregtec.db import crops as crop_db
from gregtec.ingest import collect, crops, nei
from gregtec.ingest.normalize import ITEM, Ref

RUN = "20261106T120000Z"
SEED = crops.GENERIC_SEED
HEADER = (
    "ID,Name,Unlocalised Name,Flavour Text,Unlocalized Flavour Text,Creator,Color 1,Color 2,"
    "Seed Shape,Render Shape,Soil Type,Tier,Min Seed Bed Tier,Liked Biomes,Growth Duration,"
    "Breeding Threshold,Crossing Threshold,Starts Spreading Weeds If Growth Above,"
    "Growth Requirements,Drop Count Multiplier,Drop Table,Alternate Seeds"
)


def crop_row(
    cid: str,
    name: str,
    soil: str,
    tier: int,
    reqs: str = "",
    drops: str = "",
    alts: str = "",
    creator: str = "Test Team",
    biomes: str = "",
) -> str:
    def q(s: str) -> str:
        return f'"{s}"' if ("," in s or '"' in s) else s

    cells = [
        cid,
        name,
        f"cropsnh_crops.{cid.split(':')[1]}",
        "",
        "",
        creator,
        "0xFF000000",
        "0xFFFFFFFF",
        "vanilla",
        "Hash",
        soil,
        str(tier),
        "-1",
        biomes,
        "600",
        "0.8",
        "0.8",
        "24",
        reqs,
        "0.95",
        drops,
        alts,
    ]
    return ",".join(q(c) for c in cells)


CROP_ROWS = [
    crop_row(
        "cropsnh:alpha",
        "Alpha Flower",
        "dirtGrass",
        1,
        drops="1 x Alpha Petal @ 25.0% | 1 x Red Dye @ 75.0%",
        alts="Alpha Petal",
        biomes="LUSH, FOREST",
    ),
    crop_row(
        "cropsnh:beta",
        "Beta Reed",
        "sand",
        2,
        reqs="Needs a light level of 9 or higher",
        drops="2 x Sugar @ 100.0%",
    ),
    crop_row(
        "cropsnh:delta",
        "Delta Berry",
        "mushroom(stone+dirtGrass)",
        6,
        reqs="Can only be harvested in the Industrial Farm",
        drops="1 x Delta Berry @ 12.50% | 1 x Delta Berry{rare:1b} @ 50.0%",
        creator="Test, Team",
        biomes="HOT, DRY",
    ),
    crop_row(
        "cropsnh:gamma",
        "Gamma Lily",
        "stone",
        4,
        reqs="Needs a block of tin under it to grow | Needs a light level of 10 or lower",
        drops="3 x Tin Dust{x:1b} @ 12.5%",
    ),
    crop_row("cropsnh:weed", "Weed", "global", 1),
]
RECIPE_ROWS = [
    "output,parent1,parent2,parent3,parent4,conditions",
    "Gamma Lily,Alpha Flower,Beta Reed,,,Needs a block of tin under it to grow",
    (
        "Delta Berry,Gamma Lily,Beta Reed,Alpha Flower,,"
        "MachineCatalyst(ore:dustTin x 2 | Bricks x 5),Requires the crop breeder"
    ),
    "Beta Reed,Alpha Flower,Alpha Flower,,",
]
POOL_ROWS = [
    "Crop,Flower,Reed",
    "Alpha Flower,TRUE,FALSE",
    "Beta Reed,FALSE,TRUE",
    "Gamma Lily,TRUE,TRUE",
]
SOILS = (
    "dirtGrass\n- minecraft:dirt\n- minecraft:grass\n\n"
    "sand\n- minecraft:sand\n\n"
    "stone\n- minecraft:stone\n- TConstruct:decoration.multibrick:3\n\n"
    "thaumLogs\n# empty\n"
)
SEED_ROWS = [
    "Item,Meta,CropCard",
    "test:petal,0,cropsnh:alpha",
    "test:reed,*,cropsnh:beta",
]


def seed(crop: str) -> dict[str, object]:
    tag = {"crop": crop, "ga": "1B", "gr": "1B", "re": "1B", "scan": "1B"}
    name = {"cropsnh:alpha": "Alpha Flower", "cropsnh:beta": "Beta Reed"}.get(
        crop, crop.split(":")[1].title()
    )
    return {"Count": 1, "Damage": 0, "strId": SEED, "tag": tag, "name": f"{name} Seeds"}


def st(item: str, meta: int, n: int, name: str, **kw: object) -> dict[str, object]:
    return {"Count": n, "Damage": meta, "strId": item, "name": name, **kw}


def page(
    crop: str, drops: list[dict[str, object]], sub: list[Any] | None = None
) -> dict:
    soil = st(
        "minecraft:dirt", 0, 1, "Dirt", anyOf=[st("minecraft:dirt", 0, 1, "Dirt")]
    )
    ins: list[object] = [seed(crop), soil]
    if sub:
        ins.append({**sub[0], "anyOf": sub})
    return {"handler": crops.NEI_CROPS, "ingredients": ins, "results": drops}


def nei_lines() -> list[dict[str, object]]:
    tin = [st("test:tin_block", 0, 1, "Tin Block"), st("test:tin_ore", 2, 1, "Tin Ore")]
    return [
        page(
            "cropsnh:alpha",
            [
                st("test:petal", 0, 1, "Alpha Petal"),
                st("minecraft:dye", 1, 1, "Red Dye"),
            ],
        ),
        page("cropsnh:beta", [st("minecraft:sugar", 0, 2, "Sugar")]),
        page(
            "cropsnh:gamma", [st("test:dust", 7, 3, "Tin Dust", tag={"x": "1B"})], tin
        ),
        page(
            "cropsnh:delta",
            [
                st("test:berry", 0, 1, "Delta Berry"),
                st("test:berry", 0, 1, "§6Delta Berry", tag={"rare": "1B"}),
            ],
        ),
        {
            "handler": crops.NEI_BREEDING,
            "ingredients": [seed("cropsnh:alpha"), seed("cropsnh:beta")],
            "results": [seed("cropsnh:gamma")],
        },
        {
            "handler": crops.NEI_BREEDING,
            "ingredients": [seed("cropsnh:alpha"), seed("cropsnh:alpha")],
            "results": [seed("cropsnh:beta")],
        },
        {
            "handler": crops.NEI_POOLS,
            "ingredients": [seed("cropsnh:alpha"), seed("cropsnh:gamma")],
            "results": [],
        },
        {
            "handler": crops.NEI_POOLS,
            "ingredients": [seed("cropsnh:beta"), seed("cropsnh:gamma")],
            "results": [],
        },
    ]


def crop_files(
    encoding: str = "utf-8", eol: str = "\r\n", crop_rows: list[str] | None = None
) -> dict[str, bytes]:
    def text(lines: list[str]) -> bytes:
        return eol.join(lines).encode(encoding)

    return {
        f"crops/{crops.CROPS}": text(
            [HEADER, *(CROP_ROWS if crop_rows is None else crop_rows)]
        ),
        f"crops/{crops.RECIPES}": text(RECIPE_ROWS),
        f"crops/{crops.POOLS}": text(POOL_ROWS),
        f"crops/{crops.SOILS}": SOILS.replace("\n", eol).encode(encoding),
        f"crops/{crops.SEEDS}": text(SEED_ROWS),
        "crops/fertilizerItems.csv": b"Item,Meta,Potency\r\nempty",
    }


def base_files(nei_ok: bool = True) -> dict[str, bytes]:
    files = {
        "gt_recipe_maps.json": b"[]\n",
        "gt_recipes.jsonl": b"",
        "gt_machines.jsonl": b"",
        "items.jsonl": b"",
        "fluids.jsonl": b"",
    }
    if nei_ok:
        files["nei/Crops.jsonl"] = "".join(
            json.dumps(x, ensure_ascii=False) + "\r\n" for x in nei_lines()
        ).encode()
    return files


def make_run(
    home: Path,
    run_id: str = RUN,
    created: str = "2026-11-06T12:00:00Z",
    files: dict[str, bytes] | None = None,
    crops_status: str = "ok",
    charset: str | None = "UTF-8",
    **manifest: object,
) -> Path:
    if files is None:
        files = {**base_files(), **crop_files()}
    run = home / collect.DATA_EXPORTS / run_id
    for rel, data in files.items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_bytes(data)
    java: dict[str, object] = {"version": "25", "vendor": "test"}
    if charset is not None:
        java["charset"] = charset
    m: dict[str, object] = {
        "format": "gregteclink-export/1",
        "run_id": run_id,
        "created_utc": created,
        "complete": True,
        "mode": "autoexport",
        "side": "client",
        "java": java,
        "gt_version": "5.09.54.265",
        "nei_version": "2.8.172-GTNH",
        "gregteclink": {"version": "abcdef123456"},
        "mods": [],
        "files": {
            rel: {
                "sha256": hashlib.sha256(d).hexdigest(),
                "bytes": len(d),
                "lines": d.count(b"\n"),
            }
            for rel, d in files.items()
        },
        "counts": {},
        "parts": {
            "gt_dump": "ok",
            "machines": "ok",
            "refs": "ok",
            "nei": "ok" if any(r.startswith("nei/") for r in files) else "absent",
            "crops": crops_status,
            "autoexport": "ok",
        },
    }
    m.update(manifest)
    (run / collect.MANIFEST).write_text(json.dumps(m), encoding="utf-8")
    return run


def parsed(run: Path, with_nei: bool = True) -> crops.CropData:
    m = json.loads((run / collect.MANIFEST).read_text(encoding="utf-8"))
    if not with_nei:
        return crops.read(run, m)
    refs = nei.NeiRefs({}, {}, {})
    lines = [r for _, r in nei.read_nei(run, nei.nei_files(m), refs)]
    return crops.read(run, m, lines, refs)


def rows(db: Path, sql: str, *args: object) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    try:
        return [tuple(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


# --- the parser ------------------------------------------------------------------------------


def test_the_dumps_parse(tmp_path: Path) -> None:
    data = parsed(make_run(tmp_path), with_nei=False)
    assert list(data.crops) == [
        "cropsnh:alpha",
        "cropsnh:beta",
        "cropsnh:delta",
        "cropsnh:gamma",
        "cropsnh:weed",
    ]
    alpha = data.crops["cropsnh:alpha"]
    assert alpha.ref == "crop:cropsnh:alpha"
    assert (alpha.name, alpha.tier, alpha.soil, alpha.soil_lists) == (
        "Alpha Flower",
        1,
        "dirtGrass",
        ("dirtGrass",),
    )
    assert alpha.liked_biomes == ("FOREST", "LUSH")  # a hash set in CropsNH: sorted
    assert [(d.amount, d.chance_bp, d.name) for d in alpha.drops] == [
        (1, 2500, "Alpha Petal"),
        (1, 7500, "Red Dye"),
    ]
    assert alpha.alternate_seeds == ("Alpha Petal",)
    assert alpha.in_nei is None and all(d.item is None for d in alpha.drops)
    delta = data.crops["cropsnh:delta"]
    assert delta.creator == "Test, Team"  # a quoted field
    assert delta.soil_lists == ("stone", "dirtGrass")
    # CropsNH writes value / 100 "." value % 100 unpadded: 12.50% is 1250, 12.5% is 1205.
    assert [(d.chance_bp, d.nbt) for d in delta.drops] == [
        (1250, None),
        (5000, "{rare:1b}"),
    ]
    gamma = data.crops["cropsnh:gamma"]
    assert [(d.amount, d.chance_bp, d.name, d.nbt) for d in gamma.drops] == [
        (3, 1205, "Tin Dust", "{x:1b}")
    ]
    assert [(r.kind, r.value) for r in gamma.requirements] == [
        ("light_max", 10),
        ("sub_soil", None),
    ]
    assert data.crops["cropsnh:beta"].requirements == (
        crops.Requirement("light_min", 9, "Needs a light level of 9 or higher"),
    )
    assert delta.requirements[0].kind == "industrial_farm"
    assert data.problems == []


def test_recipes_pools_soils_and_seeds(tmp_path: Path) -> None:
    data = parsed(make_run(tmp_path), with_nei=False)
    keys = [r.key for r in data.recipes]
    assert keys == [
        "cropsnh:beta<cropsnh:alpha+cropsnh:alpha",
        "cropsnh:delta<cropsnh:alpha+cropsnh:beta+cropsnh:gamma",
        "cropsnh:gamma<cropsnh:alpha+cropsnh:beta",
    ]
    delta = data.recipes[1]
    assert delta.parents == ("cropsnh:gamma", "cropsnh:beta", "cropsnh:alpha")
    assert delta.machine_only and not delta.world_only
    assert [(r.kind, r.value, r.text) for r in delta.requirements] == [
        ("catalyst", 5, "Bricks"),
        ("catalyst", 2, "ore:dustTin"),
        ("machine_only", None, "Requires the crop breeder"),
    ]
    assert [(p.name, p.members) for p in data.pools] == [
        ("Flower", ("cropsnh:alpha", "cropsnh:gamma")),
        ("Reed", ("cropsnh:beta", "cropsnh:gamma")),
    ]
    soils = {s.name: s.blocks for s in data.soils}
    assert soils["stone"] == (
        crops.SoilBlock("TConstruct:decoration.multibrick", 3),
        crops.SoilBlock("minecraft:stone", None),
    )
    assert soils["thaumLogs"] == ()
    assert data.seeds == [
        crops.Seed("test:petal", 0, "cropsnh:alpha"),
        crops.Seed("test:reed", None, "cropsnh:beta"),
    ]


def test_neis_tabs_key_drops_and_check_coverage(tmp_path: Path) -> None:
    data = parsed(make_run(tmp_path))
    assert data.problems == []
    check = data.nei
    assert check is not None
    assert (check.crop_pages, check.breeding, check.pools, check.pools_matched) == (
        4,
        2,
        2,
        2,
    )
    assert check.breeding_crops == {"cropsnh:alpha", "cropsnh:beta", "cropsnh:gamma"}
    assert check.drops == check.drops_keyed == 6
    gamma = data.crops["cropsnh:gamma"]
    assert gamma.drops[0].item is not None and gamma.drops[0].item.name == "test:dust"
    assert gamma.drops[0].item.meta == 7 and gamma.drops[0].item.nbt_hash
    assert [r.name for r in gamma.sub_soils] == ["test:tin_block", "test:tin_ore"]
    # The rare berry is told from the plain one by its formatting-free name and the amount.
    delta = data.crops["cropsnh:delta"]
    assert [d.item.nbt_hash != "" for d in delta.drops if d.item] == [False, True]
    assert data.crops["cropsnh:weed"].in_nei is False
    assert {r.key: r.in_nei for r in data.recipes} == {
        "cropsnh:beta<cropsnh:alpha+cropsnh:alpha": True,
        "cropsnh:delta<cropsnh:alpha+cropsnh:beta+cropsnh:gamma": False,
        "cropsnh:gamma<cropsnh:alpha+cropsnh:beta": True,
    }
    assert crops.item_refs(data) >= {
        Ref(ITEM, "test:petal", 0),
        Ref(ITEM, "test:tin_ore", 2),
        Ref(ITEM, "minecraft:sugar", 0),
    }


def test_nei_showing_what_the_dumps_dont_is_a_problem(tmp_path: Path) -> None:
    files = {**base_files(), **crop_files()}
    extra = {
        "handler": crops.NEI_BREEDING,
        "ingredients": [seed("cropsnh:alpha"), seed("cropsnh:beta")],
        "results": [seed("cropsnh:omega")],
    }
    files["nei/Crops.jsonl"] += (json.dumps(extra) + "\r\n").encode()
    rows_ = [r for r in RECIPE_ROWS if not r.startswith("Beta Reed")]
    files[f"crops/{crops.RECIPES}"] = "\r\n".join(rows_).encode()
    data = parsed(make_run(tmp_path, files=files))
    assert "NEI shows crop cropsnh:omega, which CropsNH's crop dump doesn't list" in (
        data.problems
    )
    assert any("cropsnh:beta<cropsnh:alpha+cropsnh:alpha" in p for p in data.problems)
    assert any("cropsnh:omega<" in p for p in data.problems)


def test_unknown_or_shared_names_leave_a_recipe_out(tmp_path: Path) -> None:
    files = {**base_files(nei_ok=False), **crop_files()}
    files[f"crops/{crops.RECIPES}"] = "\r\n".join(
        [*RECIPE_ROWS, "Omega Vine,Alpha Flower,Beta Reed,,"]
    ).encode()
    shared = [*CROP_ROWS, crop_row("cropsnh:alpha2", "Alpha Flower", "dirtGrass", 1)]
    files[f"crops/{crops.CROPS}"] = "\r\n".join([HEADER, *shared]).encode()
    data = parsed(make_run(tmp_path, files=files), with_nei=False)
    # Every recipe names Alpha Flower, which two crops now share.
    assert data.recipes == []
    shared = [p for p in data.problems if "several crops are named 'Alpha Flower'" in p]
    assert sum(p.startswith("recipe left out: ") for p in shared) == 3
    assert any(p.startswith("pool membership left out: ") for p in shared)
    assert any("no crop is named 'Omega Vine'" in p for p in data.problems)


def test_another_charset_and_unix_lines(tmp_path: Path) -> None:
    files = {**base_files(nei_ok=False), **crop_files("cp1252", "\n")}
    rows_ = [*CROP_ROWS[:-1], crop_row("cropsnh:weed", "Café Weed", "global", 1)]
    files.update(crop_files("cp1252", "\n", rows_))
    data = parsed(
        make_run(tmp_path, files=files, charset="windows-1252"), with_nei=False
    )
    assert data.crops["cropsnh:weed"].name == "Café Weed"
    assert len(data.crops) == 5


@pytest.mark.parametrize(
    ("damage", "match"),
    [
        (lambda f: f.pop(f"crops/{crops.SOILS}"), "soilRegistry.txt is missing"),
        (
            lambda f: f.update({f"crops/{crops.CROPS}": HEADER.encode() + b"\r\nx,y"}),
            "2 fields, the header has 22",
        ),
        (
            lambda f: f.update(
                {
                    f"crops/{crops.CROPS}": (
                        HEADER
                        + "\r\n"
                        + CROP_ROWS[0].replace("1 x Alpha", "one x Alpha")
                    ).encode()
                }
            ),
            "is not 'N x name @ chance%'",
        ),
        (
            lambda f: f.update(
                {f"crops/{crops.POOLS}": b"Crop,Flower\r\nAlpha Flower,maybe"}
            ),
            "is not TRUE or FALSE",
        ),
    ],
)
def test_a_bad_file_is_a_format_error(
    tmp_path: Path, damage: Callable[[dict[str, bytes]], object], match: str
) -> None:
    files = {**base_files(nei_ok=False), **crop_files()}
    damage(files)
    run = make_run(tmp_path, files=files)
    with pytest.raises(crops.FormatError, match=match):
        parsed(run, with_nei=False)


@pytest.mark.parametrize("name", ["no-such-charset", "base64"])
def test_an_unknown_charset_is_a_format_error(tmp_path: Path, name: str) -> None:
    run = make_run(tmp_path, charset=name)  # base64 is a Python codec, not a charset
    with pytest.raises(crops.FormatError, match="not a known charset"):
        parsed(run)


def test_an_empty_first_line_is_a_format_error(tmp_path: Path) -> None:
    files = {**base_files(nei_ok=False), **crop_files()}
    files[f"crops/{crops.POOLS}"] = b"\r\nAlpha Flower,TRUE"
    with pytest.raises(crops.FormatError, match="no 'Crop,...' header"):
        parsed(make_run(tmp_path, files=files), with_nei=False)


def test_a_crop_only_on_neis_pool_or_seed_tabs_is_found_missing(tmp_path: Path) -> None:
    files = {**base_files(), **crop_files()}
    extra = [
        {
            "handler": crops.NEI_POOLS,
            "ingredients": [seed("cropsnh:alpha"), seed("cropsnh:zeta")],
            "results": [],
        },
        {
            "handler": crops.NEI_SEEDS,
            "ingredients": [st("test:zeta", 0, 1, "Zeta")],
            "results": [seed("cropsnh:eta")],
        },
    ]
    files["nei/Crops.jsonl"] += "".join(json.dumps(x) + "\r\n" for x in extra).encode()
    make_run(tmp_path, files=files)
    data = parsed(tmp_path / collect.DATA_EXPORTS / RUN)
    assert data.nei is not None and {"cropsnh:eta", "cropsnh:zeta"} <= data.nei.shown
    result = build.build(tmp_path)
    text = "\n".join(report.lines(result, full=False))
    assert (
        "2 crops they show are NOT in the crop dump: cropsnh:eta, cropsnh:zeta" in text
    )


def test_requirement_kinds() -> None:
    assert crops.requirement("Needs a skull under it to grow")[0].kind == "sub_soil"
    assert crops.requirement("Must use crop sticks")[0].kind == "world_only"
    assert crops.requirement("Braucht Licht")[0] == crops.Requirement(
        "other", None, "Braucht Licht"
    )
    assert crops.requirement("MachineCatalyst(nonsense)")[0].kind == "other"
    # All or nothing: one item that doesn't read makes the whole condition "other".
    assert crops.requirement("MachineCatalyst(Bricks x 5 | weird)") == [
        crops.Requirement("other", None, "MachineCatalyst(Bricks x 5 | weird)")
    ]
    assert crops.soil_lists("global") == ("global",)
    assert crops.clean("§6Gold §rLeaf") == "Gold Leaf"


# --- the DB ----------------------------------------------------------------------------------


def test_db_build_loads_the_crop_tables(tmp_path: Path) -> None:
    make_run(tmp_path)
    result = build.build(tmp_path)
    db = result.db
    assert rows(db, "SELECT count(*) FROM crop") == [(5,)]
    assert rows(
        db, "SELECT ref, tier, in_nei FROM crop WHERE id = 'cropsnh:gamma'"
    ) == [("crop:cropsnh:gamma", 4, 1)]
    drop = rows(
        db,
        "SELECT d.amount, d.chance_bp, d.registry_name, d.meta, i.registry_name FROM crop_drop"
        " AS d JOIN item AS i ON i.id = d.item_id WHERE d.crop_id = 'cropsnh:beta'",
    )
    assert drop == [(2, 10000, "minecraft:sugar", 0, "minecraft:sugar")]
    assert rows(
        db,
        "SELECT crop_id FROM crop_parent WHERE recipe_key LIKE 'cropsnh:gamma<%' ORDER BY slot",
    ) == [("cropsnh:alpha",), ("cropsnh:beta",)]
    assert rows(db, "SELECT count(*) FROM crop_mutation") == [(4,)]
    assert rows(db, "SELECT registry_name, meta FROM crop_seed ORDER BY slot") == [
        ("test:petal", 0),
        ("test:reed", None),
    ]
    assert rows(db, "SELECT registry_name FROM crop_soil WHERE soil = 'thaumLogs'") == [
        (None,)
    ]
    # Every crop of NEI's Crop Breeding tab is in the DB (plan section 8).
    tab = result.loaded.crops.nei.breeding_crops  # type: ignore[union-attr]
    ids = {r[0] for r in rows(db, "SELECT id FROM crop")}
    assert tab and tab <= ids
    found = rows(db, "SELECT kind, ref_id FROM name_fts WHERE name_fts MATCH 'gamma'")
    assert ("crop", "cropsnh:gamma") in found
    text = "\n".join(report.lines(result, full=True))
    assert (
        "Crops: 5 crops (4 on NEI's crop pages), 3 parent-combination recipes (2 in NEI's Crop "
        "Breeding tab, 1 for the Crop Breeder only), 2 mutation pools (4 memberships), 4 soil "
        "lists (5 blocks), 2 alternate seeds, 6 drops (6 keyed by item)"
    ) in text
    assert "all 4 crops they show are in the crop dump" in text
    assert "  crop:cropsnh:weed\tWeed\t1\tglobal\tno" in text


def test_the_db_gives_back_what_was_parsed(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    result = build.build(tmp_path)
    conn = sqlite3.connect(result.db)
    try:
        back = crop_db.read(conn, result.run_id)
        assert crop_db.runs_with_crops(conn) == [(result.run_id, RUN)]
    finally:
        conn.close()
    assert back == parsed(run)
    assert crops.diff(back, parsed(run)).empty()


def test_a_reload_replaces_and_a_prune_deletes_crop_rows(tmp_path: Path) -> None:
    for day in (6, 7, 8):
        make_run(tmp_path, f"202611{day:02}T120000Z", f"2026-11-{day:02}T12:00:00Z")
    first = build.build(tmp_path, "20261106T120000Z")
    again = build.build(tmp_path, "20261106T120000Z")
    assert again.replaced and rows(first.db, "SELECT count(*) FROM crop") == [(5,)]
    build.build(tmp_path, "20261107T120000Z", keep=1)
    last = build.build(tmp_path, "20261108T120000Z", keep=1)
    assert last.pruned == ["20261107T120000Z"]  # 06 went when 07 was built
    for table in crop_db.TABLES:
        assert rows(last.db, f"SELECT DISTINCT run_id FROM {table}") == [(last.run_id,)]


def test_the_name_index_never_keeps_crops_of_a_pruned_run(tmp_path: Path) -> None:
    make_run(tmp_path, "20261106T120000Z", "2026-11-06T12:00:00Z")
    make_run(
        tmp_path, "20261107T120000Z", "2026-11-07T12:00:00Z", crops_status="failed"
    )
    build.build(tmp_path, "20261106T120000Z")
    # The new run has no crops and the old one is pruned in the same build: no crop is indexed.
    last = build.build(tmp_path, "20261107T120000Z", keep=1)
    assert last.pruned == ["20261106T120000Z"]
    assert rows(last.db, "SELECT count(*) FROM name_fts WHERE kind = 'crop'") == [(0,)]
    assert rows(last.db, "SELECT count(*) FROM crop") == [(0,)]


@pytest.mark.parametrize(
    ("status", "expect"),
    [
        ("absent", "Crops: none (parts.crops is 'absent'"),
        ("skipped_server", "Crops: none (parts.crops is 'skipped_server'"),
        ("failed", "WARNING: crops not loaded (parts.crops is 'failed': boom)"),
    ],
)
def test_a_run_without_crop_data_still_builds(
    tmp_path: Path, status: str, expect: str
) -> None:
    make_run(tmp_path, crops_status=status, part_errors={"crops": "boom"})
    result = build.build(tmp_path)
    assert rows(result.db, "SELECT count(*) FROM crop") == [(0,)]
    assert any(line.startswith(expect) for line in report.lines(result, full=False))


def test_crop_files_that_dont_read_leave_the_run_without_crops(tmp_path: Path) -> None:
    files = {**base_files(), **crop_files()}
    files[f"crops/{crops.CROPS}"] = b"ID,Name\r\nx,y"
    make_run(tmp_path, files=files)
    result = build.build(tmp_path)
    assert rows(result.db, "SELECT count(*) FROM crop") == [(0,)]
    lines = report.lines(result, full=False)
    assert any(
        line.startswith("WARNING: crops not loaded: crops/cropRegistry.csv: no column")
        for line in lines
    )


def test_without_neis_data_drops_have_no_item_key(tmp_path: Path) -> None:
    make_run(tmp_path, files={**base_files(nei_ok=False), **crop_files()})
    result = build.build(tmp_path)
    assert rows(result.db, "SELECT count(*), count(item_id) FROM crop_drop") == [(6, 0)]
    assert rows(result.db, "SELECT count(*) FROM crop WHERE in_nei IS NULL") == [(5,)]
    text = "\n".join(report.lines(result, full=False))
    assert "NEI's CropsNH tabs not checked (parts.nei is 'absent')" in text


def test_nei_data_without_cropsnhs_tabs_is_a_problem_not_a_pass(tmp_path: Path) -> None:
    # parts.nei is ok, but NEI's lines hold none of CropsNH's tabs (a renamed handler id, or
    # NEI's dump left them out): every crop would look hidden and the check pass empty.
    files = {**base_files(), **crop_files()}
    files["nei/Crops.jsonl"] = b""
    make_run(tmp_path, files=files)
    result = build.build(tmp_path)
    data = parsed(tmp_path / collect.DATA_EXPORTS / RUN)
    assert data.nei is not None and not data.nei.shown
    assert any(p.startswith("NEI's data has no crop pages") for p in data.problems)
    assert any(
        p.startswith("NEI's data has no Crop Breeding recipes") for p in data.problems
    )
    text = "\n".join(report.lines(result, full=False))
    assert "WARNING: they show no crop, so nothing was checked" in text
    assert "WARNING: crop data: 2 problems" in text
    assert "crops they show are in the crop dump" not in text


def test_gos_db_build_prints_the_crop_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    make_run(home)
    toml = tmp_path / "gos.toml"
    toml.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    assert cli.main(["db", "build", "--config", str(toml)]) == 0
    out = capsys.readouterr().out
    assert "Crops: 5 crops (4 on NEI's crop pages)" in out
    assert "crop:cropsnh:weed" not in out  # the crop list is in the saved report only
    saved = (home / build.REPORTS / f"db-{RUN}.txt").read_text(encoding="utf-8")
    assert "crop:cropsnh:weed" in saved


def test_a_version_2_db_gets_the_crop_tables(tmp_path: Path) -> None:
    path = tmp_path / "gos.sqlite"
    conn = access.connect(path)
    for table in crop_db.TABLES:
        conn.execute(f"DROP TABLE {table}")
    conn.execute("INSERT INTO kv(key, value) VALUES ('tier.override', 'mv')")
    conn.execute("PRAGMA user_version = 2")
    conn.close()
    conn = access.connect(path)
    try:
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0]
            == access.SCHEMA_VERSION
            == 3
        )
        for table in crop_db.TABLES:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT value FROM kv").fetchone()[0] == "mv"
    finally:
        conn.close()


# --- the diff ---------------------------------------------------------------------------------


def test_the_diff_of_one_run_with_itself_is_empty(tmp_path: Path) -> None:
    data = parsed(make_run(tmp_path))
    d = crops.diff(data, data)
    assert d.empty() and crops.diff_lines(d) == ["Crops: no change"]


def test_the_diff_reports_each_kind_of_change(tmp_path: Path) -> None:
    old = parsed(make_run(tmp_path))
    new = parsed(make_run(tmp_path / "b"))
    gamma = new.crops["cropsnh:gamma"]
    new.crops["cropsnh:gamma"] = replace(gamma, tier=5, drops=gamma.drops[:0])
    del new.crops["cropsnh:weed"]
    new.crops["cropsnh:omega"] = replace(gamma, id="cropsnh:omega", name="Omega")
    new.recipes = [
        replace(r, requirements=()) if r.child == "cropsnh:gamma" else r
        for r in new.recipes
        if r.child != "cropsnh:beta"
    ]
    new.pools = [
        crops.Pool("Flower", ("cropsnh:alpha",)),
        crops.Pool("Vine", ("cropsnh:omega",)),
    ]
    new.soils = [s if s.name != "sand" else crops.Soil("sand", ()) for s in new.soils]
    new.seeds = new.seeds[:1]
    d = crops.diff(old, new)
    assert d.crops_added == ["cropsnh:omega"] and d.crops_removed == ["cropsnh:weed"]
    assert {(c.key, c.field) for c in d.crops_changed} == {
        ("cropsnh:gamma", "tier"),
        ("cropsnh:gamma", "drops"),
    }
    tier = next(c for c in d.crops_changed if c.field == "tier")
    assert (tier.old, tier.new) == ("4", "5")
    assert d.recipes_removed == ["cropsnh:beta<cropsnh:alpha+cropsnh:alpha"]
    assert [(c.key, c.field) for c in d.recipes_changed] == [
        ("cropsnh:gamma<cropsnh:alpha+cropsnh:beta", "requirements")
    ]
    assert d.pools_added == ["Vine"] and d.pools_removed == ["Reed"]
    assert d.pool_members_removed == [("Flower", "cropsnh:gamma")]
    assert [c.key for c in d.soils_changed] == ["sand"]
    assert d.seeds_removed == ["test:reed@* -> crop:cropsnh:beta"]
    text = crops.diff_lines(d)
    assert text[0].startswith(
        "Crops: 1 added, 1 removed, 1 changed; parent recipes 0 added"
    )
    assert "  ~ crop crop:cropsnh:gamma tier: 4 -> 5" in text


def test_nei_facts_are_compared_only_when_both_runs_have_them(tmp_path: Path) -> None:
    with_nei = parsed(make_run(tmp_path))
    without = parsed(
        make_run(tmp_path / "b", files={**base_files(False), **crop_files()}), False
    )
    d = crops.diff(with_nei, without)
    assert d.empty() and not d.nei_compared
    moved = replace(
        with_nei.crops["cropsnh:gamma"], sub_soils=(Ref(ITEM, "test:other", 0),)
    )
    other = parsed(make_run(tmp_path / "c"))
    other.crops["cropsnh:gamma"] = moved
    assert [c.field for c in crops.diff(with_nei, other).crops_changed] == ["sub_soils"]


# --- gos crops show ---------------------------------------------------------------------------


def test_gos_crops_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    toml = tmp_path / "gos.toml"
    toml.write_text(f'[paths]\ngos_home = "{home.as_posix()}"\n', encoding="utf-8")
    assert cli.main(["crops", "show", "--config", str(toml)]) == 1
    assert "run `gos db build` first" in capsys.readouterr().out
    make_run(home)
    build.build(home)
    assert (
        cli.main(
            ["crops", "show", "gamma lily", "crop:cropsnh:beta", "--config", str(toml)]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "crop:cropsnh:gamma  Gamma Lily" in out
    assert "  soil: stone (TConstruct:decoration.multibrick@3, minecraft:stone)" in out
    assert (
        "  requirements: Needs a light level of 10 or lower; Needs a block of tin"
        in out
    )
    assert "  sub-soil blocks: test:tin_block@0, test:tin_ore@2" in out
    assert "  drops: 3 x Tin Dust{x:1b} (test:dust@7#" in out and " 12.05%" in out
    assert (
        "  bred from: Alpha Flower + Beta Reed [in NEI's Crop Breeding tab] (Needs"
        in out
    )
    assert "  parent of: Delta Berry" in out
    assert "  mutation pools: Flower, Reed" in out
    assert "  alternate seeds: test:reed@*" in out  # Beta Reed's
    assert cli.main(["crops", "show", "delta", "--config", str(toml)]) == 0
    out = capsys.readouterr().out
    assert "[Crop Breeder only] (catalyst 5 x Bricks; catalyst 2 x ore:dustTin)" in out
    assert cli.main(["crops", "show", "omega", "--config", str(toml)]) == 1
    assert "No crop matches 'omega'" in capsys.readouterr().out
    assert cli.main(["crops", "show", "--config", str(toml)]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed[0] == f"# run {RUN}: 5 crops" and len(listed) == 6
