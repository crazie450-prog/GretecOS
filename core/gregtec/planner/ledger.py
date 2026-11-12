"""The EU ledger hook (plan §7.1 step 10, §7.2; WO-3.7 stub, D252).

Accepting a plan "adds it to the EU ledger". The ledger itself (generation and use per tier,
``ledger_get``/``ledger_update``) is WO-4.4's; until then :func:`ledger_update` is the hook the
Designer's accept calls: it builds the entry WO-4.4 will record (the plan's own EU/t per tier,
copied from its metrics, never computed here) and records nothing. WO-4.4 replaces the body and
keeps the signature, so the accept flow doesn't change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PENDING = "the EU ledger arrives with WO-4.4: nothing recorded yet"


@dataclass(frozen=True)
class TierDraw:
    tier: str
    eut_avg: float
    eut_peak: float
    amps: int


@dataclass(frozen=True)
class LedgerUpdate:
    plan: str  # plan:<id>
    status: str  # the plan's lifecycle status the entry is for
    draws: tuple[TierDraw, ...]  # the plan's EU/t per tier, as its metrics give them
    recorded: bool  # False until WO-4.4
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "status": self.status,
            "draws": [d.__dict__ for d in self.draws],
            "recorded": self.recorded,
            "note": self.note,
        }


def ledger_update(plan: Mapping[str, Any], status: str) -> LedgerUpdate:
    """The ledger entry for ``plan`` at ``status`` (the stub: nothing is recorded)."""
    draws = tuple(
        TierDraw(
            str(e["tier"]),
            float(e.get("eut_avg") or 0),
            float(e.get("eut_peak") or 0),
            int(e.get("amps") or 0),
        )
        for e in (plan.get("metrics") or {}).get("eu") or ()
        if isinstance(e, Mapping) and e.get("tier")
    )
    return LedgerUpdate(f"plan:{plan['id']}", status, draws, False, PENDING)
