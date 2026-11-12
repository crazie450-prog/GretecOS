"""Catalog cards from what the core knows (D160): tool results, health, alerts, approvals.

- **Tool results** (:func:`from_tool`): ``recipe_find``/``recipe_uses`` become a recipe card,
  read again from the DB by the rows' r: ids (``recipes_by_refs``, ``recipe_slots``,
  ``recipe_conditions``), so every slot is structured with the DB's own numbers; the quest tools
  a quest card, nudge-first (a name the book doesn't show yet is null); ``ore_find`` an ore list
  in the tool's order (WO-6.6); anything else a table
  card. An error result is a table card whose ``error`` is the tool's message.
- **Alerts** come from the health strip (an item in error or warning; the Anthropic key is the
  "AI offline" state, not an alert), the stale flag (WO-1.4) and the credit cycle (D48).
- **Approvals**: a deep-lane task paused at its cap (D156) and a device asking to pair (D117,
  with its code: only for trusted callers).

Every builder returns a dict that :func:`gregtec.cards.validate` accepts; strings are clipped to
the schema's limits, so pack text can't make a card invalid.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from gregtec.agents.tools import items as item_tools
from gregtec.agents.tools import quests as quest_tools
from gregtec.agents.tools import recipes as recipe_tools
from gregtec.agents.tools.registry import DEADLINE_S, guard
from gregtec.agents.tools.tsv import Parsed, number, parse
from gregtec.db import access
from gregtec.ingest.ore_mixes import LAYERS as ORE_LAYERS

RECIPE_TOOLS = ("recipe_find", "recipe_uses")
QUEST_TOOLS = ("quest_find", "quest_frontier", "quest_for_item", "quest_get")
QUEST_STATES = ("done", "available", "repeatable", "locked")
STALE_ID = "data-stale"
CREDITS_ID = "credits"
NO_ALERT = ("anthropic_key",)  # shown as the "AI offline" state instead


def clip(text: object, limit: int) -> str:
    """``text`` as a string of at most ``limit`` characters (an ellipsis marks a cut)."""
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    t = text.strip()
    return int(t) if t.lstrip("-").isdigit() else None


def _head(parsed: Parsed) -> list[dict[str, str]]:
    return [
        {"key": clip(k, 100), "value": clip(v, 2000)}
        for k, v in list(parsed.head.items())[:30]
    ]


def table_card(tool: str, text: str, is_error: bool) -> dict[str, Any]:
    """Any tool result as its table (or its error)."""
    if is_error:
        return {
            "type": "table",
            "tool": tool,
            "error": clip(text.removeprefix("error: ").strip(), 2000),
            "title": tool,
            "head": [],
            "columns": [],
            "rows": [],
            "span": None,
            "total": None,
            "notes": [],
        }
    p = parse(text)
    return {
        "type": "table",
        "tool": tool,
        "error": None,
        "title": clip(_title(tool, p), 300),
        "head": _head(p),
        "columns": [clip(c, 100) for c in p.columns[:100]],
        "rows": [[clip(row[c], 2000) for c in p.columns[:100]] for row in p.rows[:50]],
        "span": clip(p.span, 100) if p.span else None,
        "total": p.total,
        "notes": [clip(n, 2000) for n in p.notes[:20]],
    }


def _title(tool: str, p: Parsed) -> str:
    for key in ("item", "name", "text", "quest"):
        if p.head.get(key):
            return p.head[key]
    return tool


# --- recipes --------------------------------------------------------------------------------


def _connect(db: Path) -> sqlite3.Connection:
    conn = access.connect(db, readonly=True)
    guard(conn, DEADLINE_S)
    return conn


def _slot(s: sqlite3.Row) -> dict[str, Any]:
    chance = s["chance_bp"]
    return {
        "key": clip(s["key"], 300),
        "name": clip(item_tools.clean(s["name"]), 300),
        "amount": int(s["amount"]),
        "fluid": bool(s["is_fluid"]),
        "chance_pct": None if chance == 10000 else recipe_tools.percent(chance)[:-1],
        "consumed": bool(s["consumed"]),
        "circuit": s["circuit"],
        "ore": clip(s["ore"], 200) if s["ore"] else None,
        "alts": int(s["alts"] or 0),
    }


def _flags(r: sqlite3.Row) -> list[str]:
    return [
        flag
        for flag, on in (
            ("disabled", r["enabled"] == 0),
            ("fake", r["fake"] == 1),
            ("fake?", r["fake"] is None),
            ("hidden", r["hidden"] == 1),
            ("recycling", r["recycling"] == 1),
        )
        if on
    ]


def recipes_by_ref(
    db: Path, refs: Iterable[str]
) -> tuple[list[dict[str, Any]], str | None]:
    """The recipes of the newest export run with these r: ids (in that order; unknown ones are
    left out), and the run's label. Raises sqlite3.Error when the DB can't be read."""
    wanted = list(dict.fromkeys(r for r in refs if r.startswith("r:")))[:50]
    conn = _connect(db)
    try:
        run = access.query(conn, "current_run")
        if not run:
            return [], None
        label = f"{run[0]['run_key']} (GT {run[0]['gt_version']})"
        rows = access.query(
            conn,
            "recipes_by_refs",
            {"run_id": run[0]["id"], "refs": json.dumps(wanted)},
        )
        ids = json.dumps([r["id"] for r in rows])
        slots: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for s in access.query(conn, "recipe_slots", {"ids": ids}):
            slots[(s["recipe_id"], s["part"])].append(_slot(s))
        conds: dict[int, list[dict[str, str]]] = defaultdict(list)
        for m in access.query(conn, "recipe_conditions", {"ids": ids}):
            value = (
                m["value_text"] if m["value_num"] is None else number(m["value_num"])
            )
            conds[m["recipe_id"]].append(
                {"key": clip(m["key"], 100), "value": clip(value, 300)}
            )
    finally:
        conn.close()
    out = []
    for r in rows:
        cond = conds[r["id"]]
        if (
            not cond and r["special_value"]
        ):  # GT's raw mSpecialValue, as recipe_find shows it
            cond = [{"key": "special", "value": str(r["special_value"])}]
        tier = recipe_tools.tier_name(r["voltage_tier"]) or None
        out.append(
            {
                "ref": r["ref"],
                "machine": clip(r["machine"], 300),
                "tier": tier if tier in recipe_tools.TIERS else None,
                "eut": r["eut"],
                "amps": r["amperage"],
                "dur_t": r["duration_ticks"],
                "dur_s": recipe_tools.seconds(r["duration_ticks"]) or None,
                "inputs": slots[(r["id"], "in")][:100],
                "outputs": slots[(r["id"], "out")][:100],
                "conditions": cond[:50],
                "flags": _flags(r),
            }
        )
    return out, label


def _item(head: dict[str, str]) -> dict[str, str] | None:
    """``Steel Ingot [gregtech:gt.metaitem.01@11305]`` -> its name and key."""
    text = head.get("item", "")
    name, sep, rest = text.rpartition(" [")
    if not sep or not rest.endswith("]"):
        return None
    return {"key": clip(rest[:-1], 300), "name": clip(name, 300)}


def recipe_card(
    db: Path,
    refs: Iterable[str],
    *,
    title: str,
    tool: str | None = None,
    item: dict[str, str] | None = None,
    total: int | None = None,
    notes: Iterable[str] = (),
) -> dict[str, Any]:
    found, run = recipes_by_ref(db, refs)
    return {
        "type": "recipe",
        "tool": tool,
        "error": None,
        "title": clip(title, 300),
        "item": item,
        "run": clip(run, 200) if run else None,
        "recipes": found,
        "total": total,
        "notes": [clip(n, 2000) for n in list(notes)[:20]],
    }


# --- quests ---------------------------------------------------------------------------------


def _quest_name(name: str | None) -> str | None:
    return None if not name or name == quest_tools.HIDDEN_NAME else clip(name, 300)


def _state(text: str | None) -> str:
    return text if text in QUEST_STATES else "unknown"


def _bit(text: str | None) -> bool | None:
    return None if text in (None, "") else text == "1"


def quest_card(tool: str, p: Parsed) -> dict[str, Any]:
    quests: dict[str, dict[str, Any]] = {}
    if tool == "quest_get":
        ref = p.head.get("quest", "")
        quests[ref] = {
            "ref": ref,
            "name": _quest_name(p.head.get("name")),
            "state": _state(p.head.get("state")),
            "lines": clip(p.head.get("lines"), 500) or None,
            "main": p.head.get("main") == "1",
            "shown": p.head.get("shown") == "1",
            "description": clip(p.head["description"], 2000)
            if p.head.get("description")
            else None,
            "rows": [
                {
                    "part": clip(r.get("part"), 40),
                    "what": clip(r.get("what"), 500) if r.get("what") else None,
                    "kind": clip(r.get("kind"), 100) if r.get("kind") else None,
                    "amount": _int(r.get("amount")),
                    "consume": _bit(r.get("consume")),
                    "state": clip(r.get("state"), 40) if r.get("state") else None,
                    "ref": r["cite"] if r.get("cite", "").startswith("q:") else None,
                }
                for r in p.rows[:100]
            ],
        }
    else:
        for r in p.rows:
            ref = r.get("quest", "")
            if not ref.startswith("q:"):
                continue
            entry = quests.setdefault(
                ref,
                {
                    "ref": ref,
                    "name": _quest_name(r.get("name")),
                    "state": _state(r.get("state")),
                    "lines": clip(r.get("lines"), 500) or None,
                    "main": r.get("main") == "1",
                    "shown": r.get("name") != quest_tools.HIDDEN_NAME,
                    "description": None,
                    "rows": [],
                },
            )
            if tool == "quest_for_item" and len(entry["rows"]) < 100:
                entry["rows"].append(
                    {
                        "part": clip(r.get("part"), 40),
                        "what": clip(r.get("match"), 500) or None,
                        "kind": clip(r.get("kind"), 100) or None,
                        "amount": _int(r.get("amount")),
                        "consume": _bit(r.get("consume")),
                        "state": None,
                        "ref": ref,
                    }
                )
    spoil = _int(p.head.get("spoil"))
    return {
        "type": "quest",
        "tool": tool,
        "error": None,
        "title": clip(_title(tool, p) if tool != "quest_frontier" else "Frontier", 300),
        "tier": clip(p.head["tier"], 200) if p.head.get("tier") else None,
        "spoil": spoil if spoil in (0, 1, 2) else None,
        "quests": list(quests.values())[:50],
        "total": p.total,
        "notes": [clip(n, 2000) for n in p.notes[:20]],
    }


def from_tool(tool: str, text: str, is_error: bool, db: Path | None) -> dict[str, Any]:
    """The card for one tool result (see the module docstring)."""
    if is_error:
        return table_card(tool, text, True)
    if tool in RECIPE_TOOLS and db is not None:
        p = parse(text)
        refs = [r["ref"] for r in p.rows if r.get("ref", "").startswith("r:")]
        if refs or not p.rows:
            try:
                return recipe_card(
                    db,
                    refs,
                    title=_title(tool, p),
                    tool=tool,
                    item=_item(p.head),
                    total=p.total,
                    notes=p.notes,
                )
            except sqlite3.Error:
                pass  # the table still shows what the tool said
    if tool in QUEST_TOOLS:
        return quest_card(tool, parse(text))
    if tool == "ore_find":
        return ore_card(parse(text))
    return table_card(tool, text, False)


def ore_card(p: Parsed) -> dict[str, Any]:
    """``ore_find``'s rows as the catalog's ore list, in the tool's order (WO-6.6)."""
    rows = []
    for r in p.rows[:50]:
        dim, x, z = _int(r.get("dim")), _int(r.get("x")), _int(r.get("z"))
        if dim is None or x is None or z is None:
            continue
        layers = (r.get("layer") or "").split(",")
        layer = next((name for name in ORE_LAYERS if name in layers), None)
        ref = r.get("ref") or ""
        try:
            ores = json.loads(
                r.get("ores") or "[]"
            )  # a JSON list: a name may hold a comma
        except ValueError:
            ores = []
        rows.append(
            {
                "mix": clip(r.get("mix_id"), 200),  # the mix's id, as intel.card's rows
                "name": clip(r.get("mix"), 300),
                "ores": [clip(o, 200) for o in ores if isinstance(o, str)][:8]
                if isinstance(ores, list)
                else [],
                "dim": dim,
                "x": x,
                "z": z,
                "distance": _int(r.get("distance")),
                "layer": layer,
                "depleted": r.get("depleted") == "1",
                "ref": ref if ref.startswith("vein:") else None,
            }
        )
    material = p.head.get("material")
    return {
        "type": "ore_list",
        "title": clip(f"Nearest {material} veins" if material else "Veins", 300),
        "material": clip(material, 200) if material else None,
        "rows": rows,
        "notes": [clip(n, 2000) for n in p.notes[:20]],
    }


# --- health, alerts, approvals ----------------------------------------------------------------


def health_card(health: dict[str, Any]) -> dict[str, Any]:
    def count(v: object) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None

    return {
        "type": "health",
        "title": "Health",
        "overall": health.get("overall")
        if health.get("overall") in ("ok", "warn", "error")
        else "warn",
        "items": [
            {
                "id": clip(i.get("id"), 100),
                "kind": i.get("kind")
                if i.get("kind") in ("process", "check")
                else "check",
                "label": clip(i.get("label"), 100),
                "status": i.get("status")
                if i.get("status") in ("ok", "warn", "error", "off")
                else "warn",
                "detail": clip(i.get("detail"), 500),
                "restarts": count(i.get("restarts")),
                "uptime_s": count(i.get("uptime_s")),
            }
            for i in health.get("items", [])[:50]
        ],
    }


def alert(
    alert_id: str,
    level: str,
    title: str,
    body: str,
    *,
    value: float | None = None,
    unit: str | None = None,
    source: str | None = None,
    ts: int | None = None,
) -> dict[str, Any]:
    return {
        "type": "alert",
        "id": clip(alert_id, 100),
        "level": level,
        "title": clip(title, 200),
        "value": value,
        "unit": unit,
        "body": clip(body, 1000),
        "source": source,
        "ts": ts,
        "ref": None,
    }


def health_alerts(
    health: dict[str, Any], *, said: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """An alert per health item in error (``err``) or warning (``warn``), errors first, except
    the items in ``said``, whose state the screen already shows another way."""
    quiet = set(NO_ALERT) | set(said)
    out = []
    for item in health.get("items", []):
        status = item.get("status")
        if status not in ("error", "warn") or item.get("id") in quiet:
            continue
        if item.get("id") == CREDITS_ID:
            continue  # credit_alert says it with the number
        level = "err" if status == "error" else "warn"
        out.append(
            alert(
                f"health-{item.get('id')}",
                level,
                f"{item.get('label')}: {'error' if level == 'err' else 'warning'}",
                str(item.get("detail") or ""),
                source="Health",
                ts=health.get("ts"),
            )
        )
    return sorted(out, key=lambda a: a["level"] != "err")


def credit_alert(cycle: dict[str, Any]) -> dict[str, Any] | None:
    """The D48 crossing as an alert: warn past the first threshold, err past the last."""
    crossed = cycle.get("crossed_pct") or []
    if not crossed:
        return None
    top = max(cycle.get("alerts_pct") or crossed)
    level = "err" if top in crossed else "warn"
    return alert(
        CREDITS_ID,
        level,
        "API credits",
        f"${cycle['spent_usd']:.2f} of ${cycle['budget_usd']:.2f} this cycle",
        value=float(cycle["pct"]),
        unit="%",
        source="llm_usage",
    )


def stale_alert(stale: bool | None, line: str | None) -> dict[str, Any] | None:
    if not stale:
        return None
    return alert(
        STALE_ID,
        "warn",
        "Recipe data is stale",
        line or "Play changed since the last export; export again to refresh it.",
        source="Fingerprint",
    )


def deep_approval(task: dict[str, Any]) -> dict[str, Any]:
    """A deep-lane task paused at its cap (D156): approve another cap, or stop it, counting down
    to when the lane stops it unanswered (``approve_by``)."""
    request = clip(" ".join(str(task.get("request") or "").split()), 300)
    return {
        "type": "approval",
        "id": clip(f"deep-{task.get('id')}", 100),
        "kind": "deep_budget",
        "title": "Deep lane paused at its cap",
        "readback": clip(
            f'"Spend up to ${float(task.get("cap_usd") or 0):.2f} more on: {request}"',
            1000,
        ),
        "detail": clip(
            f"Spent ${float(task.get('spent_usd') or 0):.2f} of ${float(task.get('cap_usd') or 0):.2f}; "
            f"approved {int(task.get('approvals') or 0)} times so far.",
            1000,
        ),
        "expires_at": task.get("approve_by"),
        "actions": [
            {"id": "approve", "label": "Approve", "primary": True},
            {"id": "stop", "label": "Stop", "primary": False},
        ],
    }


def action_approval(action: dict[str, Any]) -> dict[str, Any]:
    """The pending world action (WO-7.1, D59): its read-back in quotes, Confirm and Cancel (the
    card adds "Or just say confirm."), counting down to its 60 s expiry. The buttons confirm or
    cancel by its id, so an old card can't confirm a newer action."""
    return {
        "type": "approval",
        "id": clip(f"action-{action.get('id')}", 100),
        "kind": "action",
        "title": "Confirm a world action",
        "readback": clip(f'"{action.get("readback")}"', 1000),
        "detail": clip(
            f"Proposed by {action.get('source') or 'an agent'} for {action.get('target')}; "
            "nothing runs until you confirm.",
            1000,
        ),
        "expires_at": action.get("expires"),
        "actions": [
            {"id": "confirm", "label": "Confirm", "primary": True},
            {"id": "cancel", "label": "Cancel", "primary": False},
        ],
    }


def pairing_approval(pending: dict[str, Any], now: float) -> dict[str, Any]:
    """A device asking to pair (D117): its code, shown only on trusted displays, and Deny."""
    code = str(pending.get("code") or "")
    shown = f"{code[:4]} {code[4:]}" if len(code) == 8 else code
    return {
        "type": "approval",
        "id": clip(f"pair-{pending.get('request_id')}", 100),
        "kind": "pairing",
        "title": "A device wants to pair",
        "readback": clip(f'"Pair {pending.get("name")}" with code {shown}', 1000),
        "detail": "Type the code on that device to pair it. Deny it if you didn't ask.",
        "expires_at": int(now) + int(pending.get("expires_in") or 0),
        "actions": [{"id": "deny", "label": "Deny", "primary": False}],
    }
