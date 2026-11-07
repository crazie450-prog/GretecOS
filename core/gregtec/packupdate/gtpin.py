"""The GT pin check (WO-1.9, CLAUDE.md: GT pin 5.09.54.265, D148).

GOS's GT math (WO-3.1, ``planner/gtmath``) is a port of one GT version and is gated by that
version's own unit tests. An export of another GT version must never be planned with it quietly:
:func:`problem` says so loudly, and the playbook refuses to adopt such an update until the pin has
been bumped by the procedure in ``docs/runbooks/pack_update.md`` (which changes
:data:`gregtec.config.GT_PIN` in the repo, so the deployed code carries the new pin).
"""

from __future__ import annotations

from gregtec import config

RUNBOOK = "docs/runbooks/pack_update.md (GT pin bump)"


def problem(gt_version: object, pin: str | None = None) -> str | None:
    """Why an export of ``gt_version`` doesn't match the pin (default
    :data:`gregtec.config.GT_PIN`), or None when it does."""
    pin = config.GT_PIN if pin is None else pin
    if gt_version == pin:
        return None
    seen = "no GT version" if gt_version in (None, "") else f"GT {gt_version}"
    return (
        f"GT PIN MISMATCH: the export has {seen}, GOS is pinned to GT {pin}. GOS's GT math "
        f"must not be used on it until the GT pin bump is done ({RUNBOOK})"
    )
