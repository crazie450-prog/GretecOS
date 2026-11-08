"""The card catalog (WO-2.6, D160): the schema both sides read, its validator, the TypeScript it
generates, and the cards built from health, alerts and approvals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gregtec import cards
from gregtec.cards import build, typescript

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cards"
VALID: dict[str, Any] = {
    k: v
    for k, v in json.loads((FIXTURES / "valid.json").read_text("utf-8")).items()
    if not k.startswith("_")
}
INVALID: list[dict[str, Any]] = json.loads(
    (FIXTURES / "invalid.json").read_text("utf-8")
)


def test_the_catalog_is_plan_7_14s() -> None:
    """Rule 1's ten cards, plus the unsure-match question and the plain table."""
    assert list(cards.card_types()) == [
        "recipe",
        "route_comparison",
        "bom",
        "flow",
        "quest",
        "ore_list",
        "power_ledger",
        "approval",
        "alert",
        "health",
        "choices",
        "table",
    ]


def test_every_object_lists_all_its_properties_as_required() -> None:
    """So both sides always get every field (null when absent), and none is extra."""

    def objects(node: object) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                found.append(node)
            for value in node.values():
                found += objects(value)
        elif isinstance(node, list):
            for value in node:
                found += objects(value)
        return found

    for node in objects(cards.schema()):
        assert sorted(node["required"]) == sorted(node["properties"]), node
        assert node["additionalProperties"] is False


@pytest.mark.parametrize("name", sorted(VALID))
def test_each_fixture_card_is_valid(name: str) -> None:
    assert cards.validate(VALID[name]) == []
    assert VALID[name]["type"] == name


@pytest.mark.parametrize("case", INVALID, ids=[c["why"] for c in INVALID])
def test_invalid_cards_are_refused_with_a_reason(case: dict[str, Any]) -> None:
    errors = cards.validate(case["card"])
    assert errors, case["why"]
    assert any(case["error"] in e for e in errors), errors
    with pytest.raises(cards.CardError):
        cards.checked(case["card"])


def test_html_can_never_be_a_card() -> None:
    """Agents return cards, never HTML (plan 7.14 rule 1): no field takes markup as such, and
    an unknown field is refused."""
    card = dict(VALID["alert"], body="<script>alert(1)</script>")
    assert cards.validate(card) == []  # it is text: the dashboard shows it as text
    assert cards.validate({**VALID["alert"], "html": "<b>x</b>"})


def test_the_typescript_types_are_generated_from_the_schema() -> None:
    """dashboard/src/cards/types.gen.ts is exactly what the schema gives."""
    assert typescript.TARGET.read_text("utf-8") == typescript.generate()
    assert typescript.main(["--check"]) == 0


def test_the_typescript_names_every_card() -> None:
    text = typescript.generate()
    for name in cards.card_types().values():
        assert f"export interface {name} " in text
    assert 'export type CardType = Card["type"];' in text
    assert "chance_pct: string | null;" in text
    assert 'layer: "primary" | "secondary" | "between" | "sporadic" | null;' in text


def test_clip_keeps_cards_within_the_schema() -> None:
    assert build.clip("x" * 10, 5) == "xxxx…"
    assert build.clip(None, 5) == ""
    assert build.clip("short", 5) == "short"


# --- health, alerts, approvals ------------------------------------------------------------


HEALTH = {
    "ts": 1700000000,
    "overall": "error",
    "items": [
        {"id": "supervisor", "kind": "process", "label": "Supervisor", "status": "ok",
         "detail": "running", "pid": 1, "uptime_s": 50},
        {"id": "core", "kind": "process", "label": "Core", "status": "error",
         "detail": "exited with code 1; restarting in 2 s", "restarts": 3},
        {"id": "db", "kind": "check", "label": "Database", "status": "ok", "detail": "gos.sqlite"},
        {"id": "anthropic_key", "kind": "check", "label": "Anthropic key", "status": "warn",
         "detail": "not stored"},
        {"id": "credits", "kind": "check", "label": "API credits", "status": "warn",
         "detail": "$85 of $100"},
        {"id": "maintenance", "kind": "check", "label": "Maintenance", "status": "warn",
         "detail": "no run for 3 days"},
    ],
}  # fmt: skip


def test_the_health_card() -> None:
    card = cards.checked(build.health_card(HEALTH))
    assert card["overall"] == "error"
    assert [i["id"] for i in card["items"]] == [i["id"] for i in HEALTH["items"]]
    core = card["items"][1]
    assert core["restarts"] == 3 and core["uptime_s"] is None


def test_health_alerts_skip_the_key_and_the_credits() -> None:
    """The key is the "AI offline" state; the credits get their own alert with the number."""
    alerts = [cards.checked(a) for a in build.health_alerts(HEALTH)]
    assert [a["id"] for a in alerts] == ["health-core", "health-maintenance"]
    assert alerts[0]["level"] == "err" and "restarting" in alerts[0]["body"]


def test_health_alerts_skip_what_the_screen_already_says() -> None:
    """The deep lane's warning while the AI is offline or a task waits at its cap (the state
    chip and the approval card say it), never anything else."""
    health = {
        **HEALTH,
        "items": [
            *HEALTH["items"],
            {"id": "deep_lane", "kind": "check", "label": "Deep lane", "status": "warn",
             "detail": "offline: LookupError: no key"},
        ],
    }  # fmt: skip
    ids = [a["id"] for a in build.health_alerts(health)]
    assert ids == ["health-core", "health-maintenance", "health-deep_lane"]
    quiet = [a["id"] for a in build.health_alerts(health, said=("deep_lane",))]
    assert quiet == ["health-core", "health-maintenance"]


def test_credit_alerts_follow_d48() -> None:
    cycle = {"spent_usd": 82.5, "budget_usd": 100.0, "pct": 82.5,
             "alerts_pct": [80, 100], "crossed_pct": [80]}  # fmt: skip
    warn = cards.checked(build.credit_alert(cycle) or {})
    assert warn["level"] == "warn" and warn["value"] == 82.5 and warn["unit"] == "%"
    over = cards.checked(
        build.credit_alert({**cycle, "pct": 101.0, "crossed_pct": [80, 100]}) or {}
    )
    assert over["level"] == "err"
    assert build.credit_alert({**cycle, "crossed_pct": []}) is None


def test_the_stale_alert() -> None:
    assert build.stale_alert(None, None) is None
    assert build.stale_alert(False, "x") is None
    card = cards.checked(build.stale_alert(True, "Play changed since export r1.") or {})
    assert card["title"] == "Recipe data is stale" and card["level"] == "warn"


def test_a_paused_deep_task_is_an_approval() -> None:
    task = {"id": "abc", "request": "plan a  steel\nline", "cap_usd": 2.0,
            "spent_usd": 2.013, "approvals": 1, "approve_by": 1700001800}  # fmt: skip
    card = cards.checked(build.deep_approval(task))
    assert card["id"] == "deep-abc" and card["kind"] == "deep_budget"
    assert card["readback"] == '"Spend up to $2.00 more on: plan a steel line"'
    assert [a["id"] for a in card["actions"]] == ["approve", "stop"]
    # Counts down to when the lane stops it unanswered (plan 7.14: "with countdown").
    assert card["expires_at"] == 1700001800


def test_a_pairing_request_is_an_approval_with_its_code() -> None:
    pending = {
        "request_id": "r1",
        "name": "Pixel 9",
        "code": "12345678",
        "expires_in": 120,
    }
    card = cards.checked(build.pairing_approval(pending, now=1000.0))
    assert card["readback"] == '"Pair Pixel 9" with code 1234 5678'
    assert card["expires_at"] == 1120
    assert [a["id"] for a in card["actions"]] == ["deny"]


def test_an_error_result_is_a_table_card_with_its_error() -> None:
    card = cards.checked(
        build.from_tool("recipe_find", "error: no DB yet\n", True, None)
    )
    assert card["type"] == "table" and card["error"] == "no DB yet"
    assert card["tool"] == "recipe_find"
