"""The four telemetry alert rules and their thresholds (WO-7.5; plan 7.5 "Proactive notices",
7.9; D71, D280).

``gos.toml`` ``[alerts]`` (every key optional; the defaults are **pending Clayton**, D280). They
are configuration, not game numbers: each says when GOS speaks up about a telemetry reading, and
every reading itself is OC's, cited ``tel:<binding>/<address>`` (D219):

| key               | default | meaning                                                           |
|-------------------|---------|-------------------------------------------------------------------|
| rules             | all four| the rules that run: power_low, maintenance, machine_stalled, crafting_done |
| power_low_pct     | 20      | a store's fill (its own reading) below this fires ``power_low``    |
| power_clear_pct   | 30      | ... and it clears only at or above this (the hysteresis band)       |
| power_low_s       | 30      | the fill has stayed low this long before it fires                  |
| maintenance_s     | 0       | a Problems count above 0 (or an LSC's bad maintenance) this long  |
| stalled_s         | 60      | a machine stalled (or stopped, below) this long                   |
| stalled_stopped   | true    | a machine GT stopped (work disabled, not by GOS) counts as stalled |
| clear_s           | 30      | a firing alert clears after its condition is gone this long        |
| cooldown_s        | 600     | after a clear, the same alert can't fire again for this long (7.5)  |
| done_show_s       | 300     | how long "crafting done" stays on the dashboard and the board       |

The readings come from :class:`gregtec.bridge.live.Live` (WO-7.4): a store's ``pct`` (the stored
EU over the capacity the same reading carried), a machine's ``status`` and ``problems`` count and
the ``maintenance`` problems of its sensor lines, and the crafts GOS started (``ae2.crafts``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

RULES = ("power_low", "maintenance", "machine_stalled", "crafting_done")
LEVEL = {
    "power_low": "err",
    "maintenance": "err",
    "machine_stalled": "warn",
    "crafting_done": "info",
}


@dataclass(frozen=True)
class AlertRules:
    """``[alerts]`` in gos.toml (see the module docstring); defaults pending Clayton (D280)."""

    rules: tuple[str, ...] = RULES
    power_low_pct: float = 20.0
    power_clear_pct: float = 30.0
    power_low_s: int = 30
    maintenance_s: int = 0
    stalled_s: int = 60
    stalled_stopped: bool = True
    clear_s: int = 30
    cooldown_s: int = 600
    done_show_s: int = 300

    def hold_s(self, rule: str) -> int:
        """How long a condition must hold, on fresh readings, before ``rule`` fires."""
        return {
            "power_low": self.power_low_s,
            "maintenance": self.maintenance_s,
            "machine_stalled": self.stalled_s,
        }.get(rule, 0)


SECONDS = (
    "power_low_s",
    "maintenance_s",
    "stalled_s",
    "clear_s",
    "cooldown_s",
    "done_show_s",
)


def parse(table: Mapping[str, Any]) -> AlertRules:
    """``[alerts]`` as :class:`AlertRules`. Raises ValueError naming the bad key."""
    where = "gos.toml: [alerts]"
    known = {"rules", "power_low_pct", "power_clear_pct", "stalled_stopped", *SECONDS}
    unknown = sorted(set(table) - known)
    if unknown:
        raise ValueError(f"{where} has unknown keys {', '.join(unknown)}")
    d = AlertRules()
    rules = table.get("rules", list(d.rules))
    if not isinstance(rules, list) or not all(r in RULES for r in rules):
        raise ValueError(f"{where} rules must be a list of {', '.join(RULES)}")
    out: dict[str, Any] = {"rules": tuple(dict.fromkeys(rules))}
    for key in ("power_low_pct", "power_clear_pct"):
        v = table.get(key, getattr(d, key))
        if isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 100:
            raise ValueError(f"{where} {key} must be a percentage from 0 to 100")
        out[key] = float(v)
    if out["power_clear_pct"] < out["power_low_pct"]:
        raise ValueError(f"{where} power_clear_pct must be at least power_low_pct")
    for key in SECONDS:
        v = table.get(key, getattr(d, key))
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 86400:
            raise ValueError(f"{where} {key} must be whole seconds from 0 to 86400")
        out[key] = v
    stopped = table.get("stalled_stopped", d.stalled_stopped)
    if not isinstance(stopped, bool):
        raise ValueError(f"{where} stalled_stopped must be true or false")  # noqa: TRY004
    out["stalled_stopped"] = stopped
    return AlertRules(**out)


@dataclass(frozen=True)
class Seen:
    """One rule's view of one subject in one live reading. ``on`` None: no fresh reading."""

    rule: str
    subject: str  # the tel_machine id, <binding>/<address>
    on: bool | None
    name: str
    cite: str
    value: float | None = None
    unit: str | None = None
    word: str = ""  # stalled or stopped; for a store, its kind


def observe(
    base: Mapping[str, Any], cfg: AlertRules, firing: Callable[[str, str], bool]
) -> list[Seen]:
    """What each continuous rule sees in one binding's live reading (:meth:`Live.binding`).
    ``firing(rule, subject)`` says whether that alert is firing now (the power band)."""
    out: list[Seen] = []
    power = base.get("power") or []
    stores = [p for p in power if p.get("kind") == "storage"] or power
    if "power_low" in cfg.rules:
        for p in stores:
            pct = p.get("pct")
            on: bool | None = None
            if not p.get("stale") and isinstance(pct, int | float):
                limit = (
                    cfg.power_clear_pct
                    if firing("power_low", p["id"])
                    else cfg.power_low_pct
                )
                on = pct < limit
            out.append(
                Seen(
                    "power_low",
                    p["id"],
                    on,
                    p["name"],
                    p["cite"],
                    pct,
                    "%",
                    p.get("kind") or "",
                )
            )
    if "maintenance" in cfg.rules:
        bad = {
            pr["cite"]
            for pr in base.get("problems") or []
            if pr.get("kind") == "maintenance"
        }
        for p in power:
            on = None if p.get("stale") else p["cite"] in bad
            out.append(Seen("maintenance", p["id"], on, p["name"], p["cite"]))
        for m in base.get("machines") or []:
            n = m.get("problems")
            on = None if m.get("stale") or not isinstance(n, int) else n > 0
            out.append(
                Seen("maintenance", m["id"], on, m["name"], m["cite"], n, "problems")
            )
    if "machine_stalled" in cfg.rules:
        for m in base.get("machines") or []:
            status = m.get("status")
            if status in ("stale", "unknown"):
                on = None
            elif status == "stalled" or (status == "stopped" and cfg.stalled_stopped):
                on = True
            else:
                on = False
            out.append(
                Seen(
                    "machine_stalled",
                    m["id"],
                    on,
                    m["name"],
                    m["cite"],
                    word=status if on else "",
                )
            )
    return out
