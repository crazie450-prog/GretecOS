"""Deterministic route ranking and the ranking prefs (WO-3.5, plan §7.1 step 8, [QA:D2], D67).

Plans here carry only what :func:`gregtec.planner.rank.order` reads (made-up metrics).
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from typing import Any

import pytest

from gregtec.planner import rank


def plan(
    pid: str,
    *,
    machines: int = 10,
    new: int = 1,
    eut: float = 100.0,
    eff: float = 1.0,
    scarcity: float = 0.0,
    cx: int = 5,
    tier: str = "MV",
    column: str = "base",
    recipes: tuple[str, ...] = (),
    unsourced: tuple[str, ...] = (),
) -> dict[str, Any]:
    route: dict[str, Any] = {
        "column": column,
        "choices": [{"node": f"n:{r}", "recipe": r} for r in recipes],
    }
    if unsourced:
        route["unsourced"] = list(unsourced)
    return {
        "id": pid,
        "nodes": [{"recipe": r} for r in recipes],
        "route": route,
        "metrics": {
            "machines": machines,
            "new_types": [f"mte:{i}" for i in range(new)],
            "eut_avg": eut,
            "eff_h": eff,
            "scarcity": scarcity,
            "complexity": {"score": cx},
            "tier": tier,
        },
    }


def ids(plans: list[dict[str, Any]]) -> list[str]:
    return [p["id"] for p in plans]


def test_same_order_whatever_the_input_order() -> None:
    routes = [
        plan("pl-a", machines=10, eut=300),
        plan("pl-b", machines=20, eut=100),
        plan("pl-c", machines=12, eut=120, eff=3),
    ]
    first = ids(rank.order([dict(p) for p in routes]))
    for perm in itertools.permutations(routes):
        assert ids(rank.order([dict(p) for p in perm])) == first


def test_score_is_one_minus_the_weighted_mean_of_normalised_terms() -> None:
    a, b = plan("pl-a", machines=10, eut=50), plan("pl-b", machines=20, eut=100)
    out = rank.order([a, b])
    assert ids(out) == ["pl-a", "pl-b"]
    ra = out[0]["rank"]
    assert ra["terms"]["machines"] == 0.5 and ra["terms"]["eut"] == 0.5
    assert ra["terms"]["new_types"] == 1.0 and ra["terms"]["scarcity"] == 0.0
    expected = 1 - (0.5 + 1.0 + 0.5 + 1.0 + 0.0 + 1.0) / 6
    assert ra["score"] == pytest.approx(expected, abs=1e-9)
    assert ra["position"] == 1 and ra["of"] == 2 and ra["weights"] == "balanced"
    assert ra["w"] == {m: 1.0 for m in rank.METRICS}
    assert ra["values"]["machines"] == 10.0 and ra["values"]["new_types"] == 1.0
    assert 0 <= out[1]["rank"]["score"] <= 1


def test_ties_break_on_new_types_then_tier_then_id() -> None:
    # Equal scores by construction: every term the same but new types / tier.
    x = plan("pl-z", new=1, tier="HV")
    y = plan("pl-y", new=1, tier="MV")
    z = plan("pl-x", new=1, tier="MV")
    out = rank.order([x, y, z], rank.Prefs(tuple((m, 0.0 if m == "new_types" else 1.0)
                                                 for m in rank.METRICS), "custom"))  # fmt: skip
    assert ids(out) == ["pl-x", "pl-y", "pl-z"]
    fewer = plan("pl-b", new=0)
    more = plan("pl-a", new=2)
    weights = tuple((m, 0.0 if m == "new_types" else 1.0) for m in rank.METRICS)
    out = rank.order([more, fewer], rank.Prefs(weights, "custom"))
    assert ids(out) == ["pl-b", "pl-a"]


def test_a_locked_or_depth_import_ranks_below_every_route_without_one() -> None:
    """D404 (6): an unsourced import (plan.UNSOURCED) goes below, however good its score;
    among themselves such routes rank by score as usual."""
    locked = plan("pl-a", machines=1, eut=1, unsourced=("x:locked@0",))
    worse = plan("pl-b", machines=40, eut=900)
    deep = plan("pl-c", machines=2, eut=1, unsourced=("x:deep@0",))
    out = rank.order([locked, worse, deep])
    assert ids(out) == ["pl-b", "pl-a", "pl-c"]
    # scores are kept as they are
    assert out[1]["rank"]["score"] > out[0]["rank"]["score"]
    assert [p["rank"]["position"] for p in out] == [1, 2, 3]


def test_exact_ties_break_on_the_route_not_the_request() -> None:
    """D404 (8): two exactly tied routes keep their order when only the plan ids (a hash of
    the request, which ``simplest`` changes) differ: the route's own recipes decide."""
    one, two = ("r:m/1", "r:m/2"), ("r:m/1", "r:m/3")
    plain = [plan("pl-a", recipes=one), plan("pl-b", recipes=two)]
    flagged = [plan("pl-d", recipes=one), plan("pl-c", recipes=two)]  # ids swap order
    order = [p["nodes"] for p in rank.order(plain)]
    assert [p["nodes"] for p in rank.order(flagged)] == order
    digests = [rank.recipe_digest(p) for p in rank.order(plain)]
    assert digests == sorted(digests)


def test_float_noise_cannot_decide_the_order() -> None:
    a = plan("pl-b", eut=100.0)
    b = plan("pl-a", eut=100.0 + 1e-12)
    out = rank.order([a, b])
    assert out[0]["rank"]["score"] == out[1]["rank"]["score"]
    assert ids(out) == ["pl-a", "pl-b"]  # the id breaks the tie, not the noise


def test_all_zero_metrics_score_one() -> None:
    p = plan("pl-a", machines=0, new=0, eut=0, eff=0, cx=0)
    assert rank.order([p])[0]["rank"]["score"] == 1.0


def test_addon_column_comes_last_scored_against_the_base_routes() -> None:
    base = [plan("pl-b", machines=20), plan("pl-a", machines=10)]
    addon = plan("pl-0", machines=5, column="addon")
    out = rank.order([addon, *base])
    assert ids(out) == ["pl-a", "pl-b", "pl-0"]
    r = out[-1]["rank"]
    assert r["column"] == "addon" and r["position"] == 1 and r["of"] == 1
    assert r["terms"]["machines"] == 0.25  # 5 of the largest, 20
    assert (
        out[0]["rank"]["terms"]["machines"] == 0.5
    )  # base routes ranked among themselves


def test_simplest_column_follows_the_base_routes_before_the_addon_column() -> None:
    """D427 (4): the simplest route is scored like the addon column; the base order holds."""
    base = [plan("pl-b", machines=20), plan("pl-a", machines=10)]
    alone = [p["rank"] for p in rank.order([dict(p) for p in base])]
    simplest = plan("pl-1", machines=4, column="simplest")
    addon = plan("pl-0", machines=5, column="addon")
    out = rank.order([addon, simplest, *base])
    assert ids(out) == ["pl-a", "pl-b", "pl-1", "pl-0"]
    assert [p["rank"] for p in out[:2]] == alone
    r = out[2]["rank"]
    assert r["column"] == "simplest" and r["position"] == 1 and r["of"] == 1
    assert r["terms"]["machines"] == 0.2  # 4 of the largest, 20
    assert r["score"] > out[0]["rank"]["score"]  # better scored, still after the base


def test_weights_change_the_order() -> None:
    cheap = plan("pl-a", machines=30, eut=80)
    small = plan("pl-b", machines=10, eut=100)
    assert ids(rank.order([cheap, small])) == ["pl-b", "pl-a"]
    eu_heavy = rank.parse_prefs("weights: balanced\nw_eut: 5\n")
    assert eu_heavy.name == "custom"
    assert ids(rank.order([cheap, small], eu_heavy)) == ["pl-a", "pl-b"]


# --- me/prefs.md ---------------------------------------------------------------------------


def test_parse_prefs_reads_the_grammar() -> None:
    text = (
        "---\nid: me-prefs\nt: prefs\nupd: 2026-11-11\n---\n"
        "weights: balanced\nw_scarcity: 2\nw_effort: 0.5\ncraft_s: 3\nhand_fed_s: 90\n"
        "note: w_machines: 9 in a note is not a weight\n"
        "```tsv\nw_eut: 7\n```\n"
    )
    p = rank.parse_prefs(text)
    assert p.weight("scarcity") == 2 and p.weight("effort") == 0.5
    assert p.weight("machines") == 1 and p.weight("eut") == 1
    assert p.craft_s == 3 and p.name == "custom" and p.notes == ()
    assert p.hand_fed_s == 90  # D417 (1)
    assert rank.describe(p) == (
        "ranked by weights (custom): machines 1, new machine types 1, EU/t 1, effort 0.5, "
        "scarcity 2, complexity 1"
    )


def test_parse_prefs_keeps_defaults_on_bad_values() -> None:
    p = rank.parse_prefs(
        "weights: greedy\nw_eut: -1\nw_speed: 2\ncraft_s: 0\nw_machines: lots\n"
    )
    assert p == rank.Prefs(notes=p.notes)
    assert len(p.notes) == 5 and p.name == "balanced"
    # D417 (1): hand_fed_s is checked as craft_s is: a time > 0, else 300 s and a note.
    for bad in ("0", "-5", "soon", "inf"):
        q = rank.parse_prefs(f"hand_fed_s: {bad}\n")
        assert q == rank.Prefs(notes=q.notes) and q.hand_fed_s == 300
        assert q.notes == (f"hand_fed_s: {bad!r} is not a time > 0; 300 s used",)
    assert rank.BALANCED.hand_fed_s == 300
    zero = rank.parse_prefs("".join(f"w_{m}: 0\n" for m in rank.METRICS))
    assert zero.as_dict() == rank.BALANCED.as_dict() and zero.notes


def test_balanced_by_default(tmp_path: Path) -> None:
    assert rank.read_prefs(None) == rank.BALANCED
    assert rank.read_prefs(tmp_path) == rank.BALANCED
    assert rank.describe(rank.BALANCED).startswith("ranked by weights (balanced)")


def test_read_prefs_reads_the_template_and_never_writes(tmp_path: Path) -> None:
    template = (
        Path(__file__).resolve().parents[3] / "vault-template" / "me" / "prefs.md"
    )
    me = tmp_path / "me"
    me.mkdir()
    (me / "prefs.md").write_bytes(template.read_bytes())
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in me.iterdir()}
    os.chmod(me / "prefs.md", 0o444)
    try:
        p = rank.read_prefs(tmp_path)
    finally:
        os.chmod(me / "prefs.md", 0o644)
    assert p == rank.BALANCED
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in me.iterdir()}
    assert after == before


def test_unreadable_prefs_fall_back_to_balanced(tmp_path: Path) -> None:
    (tmp_path / "me").mkdir()
    (tmp_path / "me" / "prefs.md").write_bytes(b"\xff\xfe w_eut: 3")
    p = rank.read_prefs(tmp_path)
    assert p.as_dict() == rank.BALANCED.as_dict() and p.notes
