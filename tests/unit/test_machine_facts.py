"""Machine facts (WO-1.7): the tooltip parser, formulas, the curated table and coverage.

Tooltip lines are self-authored in the shape of GT's MultiblockTooltipBuilder templates (en_US
lang keys GT5U.MBTT.*), with section-sign colour codes as the export keeps them. Never pack data.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gregtec.ingest import machine_facts as mf
from gregtec.ingest.machines import Machine
from gregtec.ingest.normalize import ITEM, Ref

S = "§"
HEADER = "\t".join(mf.HEADER)


def facts(*lines: str, maps: bool = True) -> dict[str, mf.Fact]:
    return {f.key: f for f in mf.parse_tooltip(lines, maps)}


def machine(
    mte_id: int = 1000,
    cls: str = "test.MTEThing",
    maps: tuple[str, ...] = ("gt.recipe.mixer",),
    tooltip: tuple[str, ...] = (),
    mod: str = "gregtech",
) -> Machine:
    return Machine(
        mte_id=mte_id,
        ref=Ref(ITEM, "gregtech:gt.blockmachines", mte_id, ""),
        class_name=cls,
        mod=mod,
        meta_name=None,
        name=f"Thing {mte_id}",
        tier=None,
        multiblock=True,
        recipe_maps=maps,
        flags={},
        tooltip=tooltip,
        structure_tooltip=(),
        errors=(),
    )


def table(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "machine_facts.tsv"
    path.write_text("# test table\n" + HEADER + "\n" + "".join(r + "\n" for r in rows))
    return path


def row(**cells: str) -> str:
    base = {
        "machine": "test.MTEThing",
        "name": "Thing",
        "processing": "logic",
        "parallel": "",
        "speed": "",
        "eu_modifier": "",
        "oc": "",
        "extra": "",
        "confidence": "0.95",
        "cite": "gt@5.09.54.265:test/MTEThing.java#createProcessingLogic",
        "note": "",
    }
    base.update(cells)
    return "\t".join(base[c] for c in mf.HEADER)


# --- tooltips ------------------------------------------------------------------------------


def test_gt_template_lines_are_read_at_085() -> None:
    f = facts(
        f"{S}7Machine Type: Canner, TCP",
        f"{S}7{S}b8{S}7 Parallels per {S}9Voltage{S}7 Tier",
        f"{S}a200%{S}7 Speed",
        f"{S}880%{S}7 EU Usage",
        f"{S}bMachine does not lose efficiency when overclocked",
        f"{S}aSupports Multi-Amp and Laser Hatches!",
        "Runs any recipe regardless of tier, if given enough energy",
    )
    assert {k: (v.value, v.confidence) for k, v in f.items()} == {
        "parallel": ("8*vt", 0.85),
        "speed": ("2", 0.85),
        "eu_modifier": ("0.8", 0.85),
        "oc": ("perfect", 0.85),
        "exotic_hatches": ("any", 0.85),
        "tier_skips": ("unlimited", 0.85),
    }
    assert f["parallel"].note == "8 Parallels per Voltage Tier"
    assert f["parallel"].source == "tooltip" and f["parallel"].unit == "recipes"


def test_static_parallels_tier_skips_and_hatch_lines() -> None:
    f = facts(
        "16 Parallels",
        "This machine can only run recipes up to energy hatch tier + 2",
        "Supports Laser Hatches!",
    )
    assert f["parallel"].value == "16"
    assert f["tier_skips"].value == "2"
    assert f["exotic_hatches"].value == "laser"
    assert (
        facts(
            "This machine can only run recipes matching its average energy hatch tier"
        )["tier_skips"].value
        == "0"
    )
    assert facts("1 Parallel")["parallel"].value == "1"


def test_other_tiers_map_to_variables_at_lower_confidence() -> None:
    f = facts(
        "12 Parallels per Item Pipe Casing Tier", "+25% Speed per Heating Coil Tier"
    )
    assert (f["parallel"].value, f["parallel"].confidence) == ("12*item_pipe_tier", 0.7)
    # A bonus line with no base line adds to 100%; tooltips count coils from 1. Both may be
    # wrong for a machine (its code counting coils from 0, or a bonus from 0%), so the fact is
    # at most 0.5 and the other readings are in its note.
    assert (f["speed"].value, f["speed"].confidence) == (
        "1 + 0.25*(coil_tier + 1)",
        mf.COIL,
    )
    assert "or 0.25*(coil_tier + 1)" in f["speed"].note
    assert "from 0: 1 + 0.25*coil_tier" in f["speed"].note
    f = facts("100% Speed", "+50% Speed per Item Pipe Casing Tier")
    assert (f["speed"].value, f["speed"].confidence) == (
        "1 + 0.5*item_pipe_tier",
        mf.FREE,
    )
    assert "100% Speed" in f["speed"].note and "+50%" in f["speed"].note
    f = facts("+50% Speed per Item Pipe Casing Tier")  # no base line, no coil
    assert (f["speed"].value, f["speed"].confidence) == (
        "1 + 0.5*item_pipe_tier",
        mf.COIL,
    )
    f = facts("250% Speed +5% per Heating Coil Tier")
    assert (f["speed"].value, f["speed"].confidence) == (
        "2.5 + 0.05*(coil_tier + 1)",
        mf.COIL,
    )
    assert "from 0: 2.5 + 0.05*coil_tier" in f["speed"].note
    assert facts("200% Speed +10% per Heating Coil Tier")["speed"].value == (
        "2 + 0.1*(coil_tier + 1)"
    )
    # An unknown tier is not guessed: the default, with the line in the note.
    f = facts("4 Parallels per Flux Capacitor Tier")
    assert (f["parallel"].value, f["parallel"].confidence) == ("1", mf.UNREAD)
    assert "Flux Capacitor" in f["parallel"].note


def test_heat_overclocks_and_heat() -> None:
    f = facts(
        "Every 1,800K over the recipe requirement grants 1 Perfect Overclock",
        "Heat increases by +100K for every Voltage Tier past MV",
    )
    assert f["oc"].value == "heat"
    assert f["machine_heat"].value == "coil_heat + 100*(vt - 2)"
    assert mf.evaluate(f["machine_heat"].value, {"coil_heat": 1801, "vt": 4}) == 2001


def test_defaults_when_the_tooltip_is_silent_or_unreadable() -> None:
    f = facts("Mixes things")
    assert {k: (f[k].value, f[k].confidence) for k in mf.DEFAULTS} == {
        "parallel": ("1", 0.5),
        "speed": ("1", 0.5),
        "eu_modifier": ("1", 0.5),
        "oc": ("standard", 0.5),
    }
    f = facts("Parallels scale with the moon phase", "Overclocks on Tuesdays")
    assert f["parallel"].confidence == mf.UNREAD
    assert f["oc"].confidence == mf.UNREAD
    assert "moon phase" in f["parallel"].note
    # No recipe maps: no defaults at all.
    assert facts("Mixes things", maps=False) == {}


def test_a_tooltip_that_contradicts_itself_is_less_sure() -> None:
    f = facts("200% Speed", "150% Speed")
    assert (f["speed"].value, f["speed"].confidence) == ("2", 0.6)
    assert "also: 150% Speed" in f["speed"].note


def test_numbers_in_either_notation_and_deprecation() -> None:
    assert mf.number("1,800") == 1800 == mf.number("1.800")
    assert mf.number("8,33") == 8.33 == mf.number("8.33")
    assert mf.number("1,234,567") == 1234567
    f = facts(f"{S}4DEPRECATED - Controller will be removed in next major update!")
    assert f["deprecated"].value == "true"
    assert facts("Uses 75% of the EU normally required")["eu_modifier"].value == "0.75"


# --- formulas ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "8*vt",
        "4 + 2*coil_tier",
        "max(1, (2 if machine_tier == 1 else 8)*max(1, vt))",
        "floor((4 + 4*ramp)*vt)",
        "64*(2 if recipe_startup_eu < 160000000 else 1)",
        "2**(4 + casing_tier + 1)",
        "0.9**(coil_tier + 1)",
        "256*(1 + height//2)",
        "-1 + vt",
    ],
)
def test_formulas_that_check(text: str) -> None:
    mf.check_formula(text)


@pytest.mark.parametrize(
    "text",
    [
        "8*tier",  # unknown variable
        "8*x",  # no unnamed per-row inputs
        "vt.real",  # attribute access
        "__import__('os')",
        "open('x')",
        "lambda: 1",
        "[1, 2]",
        "vt if",  # not Python
        "1 < vt < 3",  # chained comparison
        "'8'",
    ],
)
def test_formulas_that_dont(text: str) -> None:
    with pytest.raises(ValueError):
        mf.check_formula(text)


def test_evaluate_and_compare() -> None:
    assert mf.evaluate("8*vt", {"vt": 3}) == 24
    assert mf.evaluate("2 if machine_tier == 1 else 8", {"machine_tier": 2}) == 8
    assert mf.evaluate("floor(2.7) + ceil(0.2) + sqrt(4)", {}) == 5
    with pytest.raises(ValueError, match="no value for vt"):
        mf.evaluate("8*vt", {})
    assert mf.same_value("parallel", "8*vt", "vt*8")
    assert mf.same_value("speed", "(1 + coil_tier)/2", "0.5 + 0.5*coil_tier")
    assert not mf.same_value("parallel", "8*vt", "8*vt + 1")
    assert not mf.same_value("oc", "perfect", "standard")
    assert not mf.same_value("parallel", "n/a", "1")
    # A branch taken only at 0 is compared too.
    assert not mf.same_value(
        "parallel", "16 if casing_tier == 0 else 32", "32 if casing_tier == 0 else 32"
    )
    assert mf.same_value(
        "parallel", "16 if casing_tier == 0 else 32", "32 - 16*(casing_tier == 0)"
    )
    # A point where neither formula has a value is skipped; where one has, they differ.
    assert mf.same_value("speed", "1/vt", "2/(2*vt)")
    assert not mf.same_value("speed", "1/vt", "1/max(vt, 1)")


def test_every_variable_is_named_and_says_where_it_comes_from() -> None:
    assert not {"x", "y", "z"} & set(mf.VARIABLES)
    assert {v.kind for v in mf.VARIABLES.values()} == set(mf.KINDS)
    assert all(v.doc for v in mf.VARIABLES.values())
    facts = [
        mf.Fact("speed", "1 + 2*ramp", "curated", 0.9, ""),
        mf.Fact("max_overclocks", "2 - recipe_fusion_tier", "curated", 0.9, ""),
        mf.Fact("parallel", "8*vt", "curated", 0.9, ""),
        mf.Fact("oc", "perfect", "curated", 0.9, ""),
        mf.Fact("tier_skips", "unlimited", "curated", 0.9, ""),
    ]
    assert mf.inputs(facts) == {"build", "state", "recipe"}
    assert mf.inputs(facts[2:]) == {"build"}


def test_values_per_key() -> None:
    mf.check_value("oc", "limited_perfect")
    mf.check_value("parallel", "n/a")
    mf.check_value("tier_skips", "unlimited")
    mf.check_value("parallel[gt.recipe.create-condensate]", "4")
    for key, value in [
        ("oc", "fast"),
        ("processing", "n/a"),
        ("colour", "red"),
        ("flag[gt.recipe.mixer]", "review"),
        ("parallel", "unlimited"),
    ]:
        with pytest.raises(ValueError):
            mf.check_value(key, value)


# --- the curated table ---------------------------------------------------------------------


def test_a_curated_table_reads_into_facts(tmp_path: Path) -> None:
    path = table(
        tmp_path,
        row(
            parallel="8*vt",
            speed="2",
            eu_modifier="1",
            oc="standard",
            note="casing note",
        ),
        row(
            machine="test.MTEOther@1001",
            extra="parallel[gt.recipe.mixer]=4;tier_skips=unlimited",
            confidence="0.8",
        ),
        row(machine="test.MTEDrill", processing="none", parallel="n/a", oc="n/a"),
    )
    rows = mf.read_curated(path)
    assert [(r.machine, r.meta) for r in rows] == [
        ("test.MTEThing", None),
        ("test.MTEOther", 1001),
        ("test.MTEDrill", None),
    ]
    first = {f.key: f for f in rows[0].facts}
    assert first["parallel"].value == "8*vt"
    assert (
        first["parallel"].source == "curated" and first["parallel"].confidence == 0.95
    )
    assert first["parallel"].note.endswith("#createProcessingLogic; casing note")
    assert {f.key for f in rows[1].facts} == {
        "processing",
        "parallel[gt.recipe.mixer]",
        "tier_skips",
    }


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (row(confidence="1.5"), "between 0 and 1"),
        (row(confidence="high"), "not a number"),
        (row(cite=""), "no citation"),
        (row(parallel="8*tier"), "unknown variable"),
        (row(oc="fast"), "oc is one of"),
        (row(extra="parallel=4", parallel="8"), "parallel is given twice"),
        (row(extra="parallel"), "not key=value"),
        (row(processing="generator", parallel="1"), "parallel is n/a"),
        (row(machine="not a class"), "not a class name"),
        (row(machine="test.MTEThing@abc"), "is not an mte_id"),
        (row(processing="", parallel="", confidence="0.9"), "no facts"),
        ("test.MTEThing\tonly three", "columns"),
    ],
)
def test_curated_rows_are_checked_with_their_line(
    tmp_path: Path, bad: str, message: str
) -> None:
    path = table(tmp_path, row(machine="test.MTEOk", parallel="1"), bad)
    with pytest.raises(ValueError, match=rf"machine_facts\.tsv:4: .*{message}"):
        mf.read_curated(path)


def test_a_key_once_per_machine_across_rows_and_a_header(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="parallel is given twice"):
        mf.read_curated(
            table(tmp_path, row(parallel="1"), row(processing="", parallel="2"))
        )
    # A class row covers every machine of the class: it and a class@meta row can't share a key,
    # in either order, but rows for two metas of one class can.
    for rows in (
        (
            row(parallel="1"),
            row(machine="test.MTEThing@1001", processing="", parallel="2"),
        ),
        (
            row(machine="test.MTEThing@1001", parallel="2"),
            row(processing="", parallel="1"),
        ),
    ):
        with pytest.raises(
            ValueError, match="parallel is given for both test.MTEThing"
        ):
            mf.read_curated(table(tmp_path, *rows))
    mf.read_curated(
        table(
            tmp_path,
            row(machine="test.MTEThing@1001", parallel="2"),
            row(machine="test.MTEThing@1002", parallel="4"),
            row(processing="", oc="standard"),
        )
    )
    path = tmp_path / "bad.tsv"
    path.write_text("machine\tname\n")
    with pytest.raises(ValueError, match="header"):
        mf.read_curated(path)
    path.write_text("# only comments\n")
    with pytest.raises(ValueError, match="no header"):
        mf.read_curated(path)


def test_the_repos_curated_table() -> None:
    rows = mf.read_curated()
    assert len(rows) >= 250
    classes = [(r.machine, r.meta) for r in rows]
    assert len(classes) == len(set(classes))
    cite = re.compile(
        r"gt@5\.09\.54\.265:[\w/.$-]+\.java(#[\w,]+)?(;[\w/.$-]+\.java(#[\w,]+)?)*"
    )
    for r in rows:
        for f in r.facts:
            assert cite.match(f.note), (r.line, f.note)
            assert mf.KEYS[f.base_key]  # every key is in the vocabulary
        keys = {f.base_key for f in r.facts}
        # A machine that runs recipes gives both parallels and OC rules, or is flagged.
        proc = next((f.value for f in r.facts if f.key == "processing"), None)
        if proc in ("logic", "custom") and "flag" not in keys:
            assert "parallel" in keys and "oc" in keys, (r.line, r.machine)
        # Every formula uses named variables only, each with where its value comes from.
        for f in r.facts:
            if mf.KEYS[f.base_key].formula and f.value not in (mf.NA, "unlimited"):
                assert mf.variables(f.value) <= set(mf.VARIABLES), (r.line, f.key)


def curated_row(cls: str) -> dict[str, str]:
    [r] = [r for r in mf.read_curated() if r.machine == cls]
    return {f.key: f.value for f in r.facts}


def test_the_cutting_factorys_sawblade_makes_it_faster() -> None:
    # SawbladeTiers stores 1/speedBoost and ProcessingLogic multiplies the duration by it, so
    # the Tungsten-Titanium Carbide blade is 250% speed (its tooltip), Transcendent Metal 450%.
    icf = curated_row(
        "gregtech.common.tileentities.machines.multi.MTEIndustrialCuttingMachine"
    )
    speeds = [mf.evaluate(icf["speed"], {"sawblade_tier": t}) for t in (1, 2, 3, 4)]
    assert speeds == [2.5, 3, 3.5, 4.5]
    assert mf.evaluate(icf["parallel"], {"sawblade_tier": 4, "vt": 8}) == 48


def test_the_dangote_distillus_parallels_per_mode() -> None:
    # Tower mode (distillationtower) is DT_MODE_MAX_PARALLELS, 12; distillery mode grows with
    # the tower's height and the voltage tier (getMaxParallelRecipes).
    adt = curated_row(
        "gtPlusPlus.xmod.gregtech.common.tileentities.machines.multi.processing.advanced."
        "MTEAdvDistillationTower"
    )
    assert adt["parallel[gt.recipe.distillationtower]"] == "12"
    distillery = adt["parallel[gt.recipe.distillery]"]
    assert mf.evaluate(distillery, {"height": 11, "vt": 8}) == 64
    assert (
        adt["speed[gt.recipe.distillationtower]"],
        adt["speed[gt.recipe.distillery]"],
    ) == (
        "3",
        "2",
    )


# --- assembling and coverage ---------------------------------------------------------------


def test_curated_wins_over_tooltip_and_per_map_keys_cover(tmp_path: Path) -> None:
    tip = ("8 Parallels per Voltage Tier",)
    rows = mf.read_curated(
        table(
            tmp_path,
            row(parallel="8*vt", oc="standard"),
            row(
                machine="test.MTEModes",
                parallel="",
                oc="perfect",
                extra="parallel[gt.recipe.mixer]=4;parallel[gt.recipe.blender]=2",
            ),
            row(machine="test.MTEGone", parallel="1", oc="none"),
            row(
                machine="test.MTEWrongMap",
                extra="parallel[gt.recipe.nope]=1",
                oc="none",
            ),
        )
    )
    ms = [
        machine(1000, tooltip=tip),
        machine(1001, "test.MTEModes", ("gt.recipe.mixer", "gt.recipe.blender")),
        machine(1002, "test.MTETipOnly", tooltip=tip),
        machine(1003, "test.MTEDrill", ()),
        machine(1004, "test.MTEWrongMap"),
    ]
    fs = mf.assemble(ms, rows)
    assert [c.machine for c in fs.unmatched] == ["test.MTEGone"]
    assert [(c.machine, key) for c, key in fs.bad_maps] == [
        ("test.MTEWrongMap", "parallel[gt.recipe.nope]")
    ]
    best = mf.best(fs.facts[1000])
    assert best["parallel"].source == "curated"
    states = {m.mte_id: mf.status(m, fs.facts.get(m.mte_id, [])) for m in ms}
    assert {i: s.state for i, s in states.items()} == {
        1000: "covered",
        1001: "covered",
        1002: "missing",  # the tooltip's 0.85 parallels, but only GT's default OC (0.5)
        1003: "n/a",
        1004: "missing",  # its only parallel names a map it doesn't have
    }
    assert states[1002].why == "parallel 0.85 (tooltip), oc 0.50 (tooltip)"
    assert mf.disagreements(fs.facts[1000]) == []


def test_flags_generators_and_disagreements(tmp_path: Path) -> None:
    rows = mf.read_curated(
        table(
            tmp_path,
            row(parallel="1", oc="standard", extra="flag=review", note="moon phase"),
            row(
                machine="test.MTEBurner",
                processing="generator",
                parallel="n/a",
                oc="n/a",
            ),
            row(machine="test.MTEStale", parallel="4*vt", oc="perfect"),
        )
    )
    ms = [
        machine(1000),
        machine(1001, "test.MTEBurner", ("gt.recipe.fuels",)),
        machine(1002, "test.MTEStale", tooltip=("8 Parallels per Voltage Tier",)),
    ]
    fs = mf.assemble(ms, rows)
    states = [mf.status(m, fs.facts[m.mte_id]) for m in ms]
    assert [s.state for s in states] == ["flagged", "n/a", "covered"]
    assert states[0].why.endswith("moon phase")
    assert states[1].why == "processing generator"
    [(tip, cur)] = mf.disagreements(fs.facts[1002])
    assert (tip.value, cur.value) == ("8*vt", "4*vt")


def test_a_class_at_meta_row_applies_to_that_machine_only(tmp_path: Path) -> None:
    rows = mf.read_curated(
        table(
            tmp_path,
            row(processing="", oc="standard"),
            row(machine="test.MTEThing@1001", parallel="4*vt"),
            row(machine="test.MTEThing@1009", parallel="1"),
        )
    )
    ms = [machine(1000), machine(1001)]
    fs = mf.assemble(ms, rows)
    assert [c.machine for c in fs.unmatched] == [
        "test.MTEThing"
    ]  # nothing has mte_id 1009
    assert fs.unmatched[0].meta == 1009
    curated = {
        i: {f.key: f.value for f in fs.facts[i] if f.source == "curated"}
        for i in (1000, 1001)
    }
    assert curated[1000] == {"oc": "standard"}
    assert curated[1001] == {
        "oc": "standard",
        "processing": "logic",
        "parallel": "4*vt",
    }
    states = {m.mte_id: mf.status(m, fs.facts[m.mte_id]).state for m in ms}
    assert states == {1000: "missing", 1001: "covered"}


def test_mode_rows_are_compared_with_the_tooltip_apart(tmp_path: Path) -> None:
    # A tooltip mixes a machine's modes: its "12 Parallels" is one mode's, so the other mode's
    # row differs from it and is listed apart, to check (the Dangote Distillus's swap showed so).
    rows = mf.read_curated(
        table(
            tmp_path,
            row(
                oc="standard",
                extra="parallel[gt.recipe.distillationtower]=12;"
                "parallel[gt.recipe.distillery]=2*floor((height + 1)/3)*vt",
            ),
        )
    )
    m = machine(
        maps=("gt.recipe.distillationtower", "gt.recipe.distillery"),
        tooltip=("12 Parallels",),
    )
    fs = mf.assemble([m], rows)
    assert mf.disagreements(fs.facts[1000]) == []
    [(tip, cur)] = mf.disagreements(fs.facts[1000], per_map=True)
    assert (tip.key, tip.value) == ("parallel", "12")
    assert cur.key == "parallel[gt.recipe.distillery]"


def test_a_flagged_custom_machine_without_a_map_and_a_probe_clearing_a_flag(
    tmp_path: Path,
) -> None:
    rows = mf.read_curated(
        table(
            tmp_path,
            row(machine="test.MTEOreFactory", processing="custom", extra="flag=review"),
            row(machine="test.MTEStorage", processing="none", parallel="n/a", oc="n/a"),
            row(parallel="1", oc="standard", extra="flag=review", note="moon phase"),
        )
    )
    ms = [
        machine(1000),
        machine(1001, "test.MTEOreFactory", ()),
        machine(1002, "test.MTEStorage", ()),
    ]
    fs = mf.assemble(ms, rows)
    states = {m.mte_id: mf.status(m, fs.facts[m.mte_id]) for m in ms}
    assert [states[i].state for i in (1000, 1001, 1002)] == [
        "flagged",
        "flagged",
        "n/a",
    ]
    # A Lab probe of parallels and OC rules at the threshold clears the curator's flag.
    probe = [
        mf.Fact("parallel", "1", "probe", 0.95, "Lab run"),
        mf.Fact("oc", "standard", "probe", 0.95, "Lab run"),
    ]
    assert mf.status(ms[0], [*fs.facts[1000], *probe]).state == "covered"
    # A probe of one of the two leaves it flagged.
    assert mf.status(ms[0], [*fs.facts[1000], probe[0]]).state == "flagged"


def test_base_mods_are_the_ones_in_gts_jar() -> None:
    mods = [
        {"modid": "gregtech", "file": "mods/gt.jar", "sha256": "a" * 64},
        {"modid": "miscutils", "file": "mods/gt.jar", "sha256": "a" * 64},
        {"modid": "TwistSpaceTechnology", "file": "mods/tst.jar", "sha256": "b" * 64},
    ]
    assert mf.base_mods({"mods": mods}) == {"gregtech", "miscutils"}
    # Without hashes (a jar the mod couldn't read), the file name decides.
    no_hash = [{**m, "sha256": None} for m in mods]
    assert mf.base_mods({"mods": no_hash}) == {"gregtech", "miscutils"}
    assert mf.base_mods({"mods": mods[2:]}) == set()
    assert mf.base_mods({}) == set()


# --- custom machines with no recipe map (D142 (j), D144) -----------------------------------

IOF = "gregtech.common.tileentities.machines.multi.MTEIntegratedOreFactory"
EIG = "kubatech.tileentity.gregtech.multiblock.MTEExtremeIndustrialGreenhouse"
LMA = "gregtech.common.tileentities.machines.multi.MTELargeMolecularAssembler"


def custom_status(cls: str) -> mf.Status:
    """The coverage the repo's row for ``cls`` gives a multiblock with no recipe map."""
    m = machine(1000, cls, ())
    fs = mf.assemble([m], mf.read_curated())
    assert fs.bad_maps == []
    return mf.status(m, fs.facts[1000])


def test_custom_keys_name_a_fluid_or_item_or_a_mode() -> None:
    mf.check_value("consumes[fluid:lubricant]", "2*run_parallel")
    mf.check_value("consumes[minecraft:dye@15]", "min(fertilizer, 2*run_parallel)")
    mf.check_value("duration", "600 if mode == 0 else 20")
    mf.check_value("outputs", "drops")
    mf.check_value("partial", "outputs")
    mf.check_value(
        "chain[4]",
        "gt.recipe.macerator(ore) > gt.recipe.chemicalbath(crushed+crushedPurified)+fluids",
    )
    for key, value in [
        ("consumes", "2"),  # names nothing
        ("consumes[lubricant]", "2"),  # neither fluid:<name> nor registry@meta
        ("consumes[minecraft:dye]", "2"),
        ("consumes[fluid:water]", "lots"),
        ("duration[gt.recipe.mixer]", "20"),  # not a per-map key
        ("chain", "gt.recipe.macerator(ore)"),  # no mode
        ("chain[a]", "gt.recipe.macerator(ore)"),
        ("chain[0]", "gt.recipe.macerator(ingot)"),  # not one of the IOF's classes
        ("chain[0]", "gt.recipe.macerator"),
        ("chain[0]", "gt.recipe.macerator(ore) >"),
        ("outputs", "recipe"),
        ("partial", "parallel"),
    ]:
        with pytest.raises(ValueError):
            mf.check_value(key, value)
    [_, bath] = mf.chain_steps(
        "gt.recipe.macerator(ore) > gt.recipe.chemicalbath(crushed+crushedPurified)+fluids"
    )
    assert bath == mf.Step(
        "gt.recipe.chemicalbath", ("crushed", "crushedPurified"), True
    )


def test_a_custom_machine_without_a_map_is_covered_by_its_plain_keys(
    tmp_path: Path,
) -> None:
    custom = {"processing": "custom", "oc": "none"}
    rows = mf.read_curated(
        table(
            tmp_path,
            row(machine="test.MTEFull", parallel="4", **custom),
            row(machine="test.MTEHalf", **custom),
            row(machine="test.MTEWeak", parallel="4", confidence="0.6", **custom),
            row(
                machine="test.MTEPart",
                parallel="4",
                extra="outputs=drops;partial=outputs",
                **custom,
            ),
        )
    )
    ms = [
        machine(1000, "test.MTEFull", ()),
        machine(1001, "test.MTEHalf", ()),
        machine(1002, "test.MTEWeak", ()),
        machine(1003, "test.MTEPart", ()),
        machine(1004, "test.MTENothing", ()),
    ]
    fs = mf.assemble(ms, rows)
    states = [mf.status(m, fs.facts.get(m.mte_id, [])) for m in ms]
    assert [s.state for s in states] == [
        "covered",
        "missing",
        "missing",
        "covered",
        "n/a",
    ]
    assert states[0].why == ""
    assert states[3].why == "outputs not modelled (see the note)"


def test_the_integrated_ore_factory() -> None:
    # MTEIntegratedOreFactory.checkProcessing: getMaxInputEu() / 30 ores, 30 EU/t, 2 L of
    # lubricant and 200 L of distilled water each, a fixed time per mode, no overclocks.
    iof = curated_row(IOF)
    assert "flag" not in iof and iof["processing"] == "custom" and iof["oc"] == "none"
    assert mf.evaluate(iof["parallel"], {"input_eu": 4 * 131072}) == 17476  # 4 A LuV
    assert mf.evaluate(iof["parallel"], {"input_eu": 29}) == 0
    seconds = [mf.evaluate(iof["duration"], {"mode": m}) / 20 for m in range(7)]
    assert seconds == [30, 15, 10, 20, 17, 32, 1]
    run = {"run_parallel": 1000}
    assert mf.evaluate(iof["eu_t"], run) == 30000
    assert mf.evaluate(iof["consumes[fluid:lubricant]"], run) == 2000
    assert mf.evaluate(iof["consumes[fluid:ic2distilledwater]"], run) == 200000
    assert iof["outputs"] == "chain"
    chains = [mf.chain_steps(iof[f"chain[{m}]"]) for m in range(7)]
    maps = [[s.recipe_map.removeprefix("gt.recipe.") for s in c] for c in chains]
    assert maps == [
        ["macerator", "orewasher", "thermalcentrifuge", "macerator"],
        ["macerator", "orewasher", "macerator", "centrifuge"],
        ["macerator", "macerator", "centrifuge"],
        ["macerator", "orewasher", "sifter"],
        ["macerator", "chemicalbath", "macerator", "centrifuge"],
        ["macerator", "chemicalbath", "thermalcentrifuge", "macerator"],
        ["hammer", "hammer", "simplewasher"],
    ]
    # Only the chemical bath uses up its recipe's fluid; every chain starts on the ores.
    assert {s.recipe_map for c in chains for s in c if s.fluids} == {
        "gt.recipe.chemicalbath"
    }
    assert all(c[0].classes == ("ore",) for c in chains)
    assert chains[0][2].classes == ("crushedPurified", "crushed")
    s = custom_status(IOF)
    assert (s.state, s.why) == ("covered", "")
    assert s.parallel is not None and s.parallel.confidence >= mf.THRESHOLD
    # The legacy controller (deprecated, its own OverclockCalculator) keeps its flag.
    assert "flag" in curated_row(IOF + "Legacy")


def test_the_extreme_industrial_greenhouse() -> None:
    # Normal mode (EIGNormalMode): 1 slot of 64 seeds at EV, twice the slots per tier; time
    # 100 / (tier - 3) ticks, at least 1 s; (long)(V[tier] * 0.99) EU/t.
    eig = curated_row(EIG)
    assert "flag" not in eig and eig["oc"] == "none"
    at = [{"vt_eu_floor": t} for t in range(3, 10)]  # HV .. UHV
    assert [mf.evaluate(eig["slots"], e) for e in at] == [0, 1, 2, 4, 8, 16, 32]
    seeds = [mf.evaluate(eig["parallel"], e) for e in at]
    assert seeds == [0, 64, 128, 256, 512, 1024, 2048]
    ticks = [mf.evaluate(eig["duration"], e) for e in at]
    assert ticks == [100, 100, 50, 33, 25, 20, 20]
    assert [mf.evaluate(eig["eu_t"], e) for e in at[1:4]] == [2027, 8110, 32440]
    water, weedex = eig["consumes[fluid:water]"], eig["consumes[fluid:weedex9000]"]
    assert mf.evaluate(water, {"run_parallel": 256}) == 256000
    assert mf.evaluate(weedex, {"run_parallel": 999}) == 0
    assert mf.evaluate(weedex, {"run_parallel": 1000}) == 1000
    # Fertilizer: at most 2 per seed, up to +400% drops.
    bone, mul = eig["consumes[minecraft:dye@15]"], eig["output_mul"]
    for given, used, x in [(0, 0, 1), (64, 64, 3), (128, 128, 5), (500, 128, 5)]:
        env = {"run_parallel": 64, "fertilizer": given}
        assert (mf.evaluate(bone, env), mf.evaluate(mul, env)) == (used, x)
    # The seeds' drops are the game's own simulation: covered, with the outputs flagged.
    assert (eig["outputs"], eig["partial"]) == ("drops", "outputs")
    s = custom_status(EIG)
    assert (s.state, s.why) == ("covered", "outputs not modelled (see the note)")


def test_the_large_molecular_assembler() -> None:
    # checkRecipe: MV is the base (64 EU/t, 2 jobs in 1 s); HV and EV halve the time, each
    # tier past EV doubles the jobs; every overclock quadruples the EU.
    lma = curated_row(LMA)
    assert "flag" not in lma and lma["oc"] == "custom" and lma["outputs"] == "patterns"
    got = [
        tuple(mf.evaluate(lma[k], {"vt": vt}) for k in ("duration", "eu_t", "parallel"))
        for vt in range(1, 8)
    ]
    assert got == [
        (20, 64, 2),  # LV: no overclock below MV
        (20, 64, 2),
        (10, 256, 2),
        (5, 1024, 2),
        (5, 4096, 4),
        (5, 16384, 8),
        (5, 65536, 16),
    ]
    assert custom_status(LMA).state == "covered"
