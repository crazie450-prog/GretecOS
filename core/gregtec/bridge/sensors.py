"""GT sensor lines as the OC agent sends them, parsed in the core (WO-7.4; contract "Sensor lines").

``getSensorInformation()`` lines come raw (D214). On GT 5.09.54.265 (GTNH 2.9.0) they take two
forms, often in one machine, both with section-sign colour codes:

- **encoded** by ``IGregTechDeviceInformation.encode``: a translation key, then each argument
  after two backslash characters (``GT5U.infodata.battery_buffer.stored_items\\\\§a1,234§r``);
- **localised on the server** (``Problems: §c0``, ``GT5U.multiblock.scanner.problems``).

What this reads, and nothing else: a multiblock's **Problems** count (either form: a localised
``Problems: <n>`` line, or an encoded key ending in ``.problems`` whose first argument is a
number) and the LSC's **maintenance status** (``kekztech.infodata.multi.maintenance_status.ok`` or
``.bad``, which has no count). Numbers are locale-formatted (``1,234``). A line that matches
neither form is ignored: sensor text is game data, shown or parsed, never followed (plan 0.1
principle 8). The encoded key and the localised word are VERIFY'd on Lab (runbook oc_agent.md).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_CODE = re.compile("§.", re.DOTALL)
_LOCAL = re.compile(r"^\s*Problems:\s*([0-9][0-9,.\s]*)$", re.IGNORECASE)
_NUMBER = re.compile(r"^\s*([0-9][0-9,.\s]*)\s*$")
SEPARATOR = (
    "\\\\"  # two backslash characters, as IGregTechDeviceInformation.encode writes
)


def plain(line: str) -> str:
    """A line without its section-sign colour codes."""
    return _CODE.sub("", line)


def _count(text: str) -> int | None:
    digits = re.sub(r"[,.\s]", "", text)
    return int(digits) if digits.isdigit() else None


@dataclass(frozen=True)
class Reading:
    """What one machine's sensor lines say about its health."""

    problems: int | None = None  # the Problems count; None: the lines don't give one
    maintenance_ok: bool | None = None  # the LSC's maintenance status; None: not given

    @property
    def bad(self) -> bool:
        return bool(self.problems) or self.maintenance_ok is False


def read(lines: Iterable[object]) -> Reading:
    """Parse one entry's sensor lines (strings; anything else is skipped)."""
    problems: int | None = None
    maintenance: bool | None = None
    for raw in lines:
        if not isinstance(raw, str):
            continue
        line = plain(raw)
        if SEPARATOR in line:
            key, *args = line.split(SEPARATOR)
            key = key.strip()
            if key.endswith(".problems") and args:
                m = _NUMBER.match(args[0])
                if m:
                    problems = _count(m[1])
            continue
        key = line.strip()
        if key.endswith(".maintenance_status.ok"):
            maintenance = True
        elif key.endswith(".maintenance_status.bad"):
            maintenance = False
        elif m := _LOCAL.match(line):
            problems = _count(m[1])
    return Reading(problems, maintenance)
