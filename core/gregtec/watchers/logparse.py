"""What the client logs say, line by line (plan 5.4 and 7.5, [B§2], WO-6.1, D257).

Forge 1.7.10's ``log4j2.xml`` (what GTNH 2.9.0 runs) writes ``logs/latest.log`` as
``[HH:mm:ss] [thread/LEVEL]: msg`` (vanilla's ``net.minecraft`` loggers) and
``logs/fml-client-latest.log`` as ``[HH:mm:ss] [thread/LEVEL] [logger]: msg`` (everything)
[B§2.1]. Which file gets the chat lines is VERIFY (plan 5.4), so both are read with the same
rules and a line seen in both counts once (:class:`Dedupe`).

| line (the message part)                                   | gives                         |
|-----------------------------------------------------------|-------------------------------|
| ``[CHAT] You found a <vein> vein <where>!`` and the other | ``vein_found`` (VisualProspecting, |
| VisualProspecting notices                                 | its en_US text, [B§2.2])      |
| ``[CHAT] <player> <death message>`` (vanilla's wordings)  | ``death``                     |
| ``[CHAT] <anything else>``                                | ``chat``                      |
| ``Saving and pausing game...`` (Server thread)            | the pause signal (V29, D258)  |
| ``<name>[local:...] logged in with entity id N at (x, y, z)`` | ``location`` (login), the player's name |
| ``Stopping server`` / ``Stopping!``                       | the world closed              |
| ``... Crash report saved to: <path>`` / BetterCrashes'    | ``crash``                     |
| ``Report saved to: <path>``                               |                               |
| ``[thread/ERROR]`` or ``/FATAL`` in latest.log, ``/FATAL``| ``error`` (rate-limited)      |
| in the FML log                                            |                               |

All of it is data (plan 0.1 principle 8): chat is kept as cleaned text with ``untrusted``, never
followed. A death is a chat line that starts with the tracked player's name followed by one of
vanilla 1.7.10's death wordings; a player's own chat (``<name> text``) never is.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from gregtec.watchers.events import clean

LINE = re.compile(
    r"^\[(?P<time>\d\d:\d\d:\d\d)\] \[(?P<thread>[^\]/]+)/(?P<level>[A-Z]+)\]"
    r"(?: \[(?P<logger>[^\]]*)\])?: (?P<msg>.*)$"
)
CHAT = "[CHAT] "
PAUSE = "Saving and pausing game..."
SAVE_CHUNKS = re.compile(r"^Saving chunks for level '.*'/.*$")
LOGIN = re.compile(
    r"^(?P<name>[A-Za-z0-9_]{1,32})\[[^\]]*\] logged in with entity id -?\d+ at "
    r"\((?P<x>-?[\d.]+), (?P<y>-?[\d.]+), (?P<z>-?[\d.]+)\)$"
)
STOPPING = ("Stopping server", "Stopping!")
CRASH = re.compile(
    r"(?:Crash report saved to:|Report saved to:)\s*(?:#@!@#\s*)?(?P<path>\S.*?)\s*$"
)
# VisualProspecting's en_US notices ([B§2.2]: "You found a %s vein %s!", "You may find: %s",
# "You found %s new ore veins!", "... oil fields!")
VEIN = re.compile(r"^You found an? (?P<name>.+?) vein(?: (?P<where>.*?))?!$")
VEINS_SHARED = re.compile(r"^You found (?P<n>\d+) new (?P<what>ore veins|oil fields)!$")
WHERE = re.compile(r"\(?\s*(?P<x>-?\d+)\s*,\s*(?:-?\d+\s*,\s*)?(?P<z>-?\d+)\s*\)?")
# Vanilla 1.7.10's death messages after the player's name (en_US death.*), plus "died".
DEATH = re.compile(
    r"^(?:was (?:slain|shot|killed|fireballed|pummeled|squashed|blown up|pricked|"
    r"knocked into the void|doomed to fall|struck by lightning)\b|"
    r"died\b|drowned\b|blew up\b|burned to death\b|suffocated in a wall\b|starved to death\b|"
    r"fell\b|hit the ground too hard\b|tried to swim in lava\b|went up in flames\b|"
    r"walked into (?:fire|a cactus)\b|withered away\b|got finished off\b|"
    r"was slain\b)"
)
ERROR_EVERY_S = 60.0  # the same error message at most once a minute
ERRORS_MAX = 30  # and at most this many error events per hour


@dataclass(frozen=True)
class Parsed:
    time: str
    thread: str
    level: str
    logger: str | None
    msg: str


def parse(line: str) -> Parsed | None:
    m = LINE.match(line)
    if m is None:
        return None
    return Parsed(m["time"], m["thread"], m["level"], m["logger"], m["msg"])


@dataclass(frozen=True)
class Found:
    """One thing a line says: an event kind and its data, or a signal for the pause tracker."""

    kind: str  # an event kind, or "pause", "login", "quit", "server_tick"
    data: dict[str, object]


def _where(text: str | None) -> dict[str, object]:
    if not text:
        return {}
    m = WHERE.search(text)
    return {"x": int(m["x"]), "z": int(m["z"])} if m else {}


def read_line(p: Parsed, fml: bool, player: str | None) -> list[Found]:
    """What ``p`` says, from latest.log (``fml`` False) or the FML log."""
    msg = p.msg
    out: list[Found] = []
    server = p.thread == "Server thread"
    if msg.startswith(CHAT):
        text = clean(msg[len(CHAT) :])
        if m := VEIN.match(text):
            out.append(
                Found(
                    "vein_found",
                    {
                        "name": m["name"],
                        **_where(m["where"]),
                        "via": "chat",
                        "untrusted": True,
                    },
                )
            )
        elif m := VEINS_SHARED.match(text):
            out.append(
                Found(
                    "vein_found",
                    {
                        "count": int(m["n"]),
                        "what": m["what"],
                        "via": "chat",
                        "untrusted": True,
                    },
                )
            )
        elif (
            player
            and text.startswith(player + " ")
            and DEATH.match(text[len(player) + 1 :])
        ):
            out.append(
                Found("death", {"message": text, "via": "chat", "untrusted": True})
            )
        else:
            out.append(Found("chat", {"text": text, "untrusted": True}))
        return out
    if msg == PAUSE:
        return [Found("pause", {})]
    if server and SAVE_CHUNKS.match(msg):
        return []  # part of a save (the pause's, or a quit's): not a sign of ticking
    if m := LOGIN.match(msg):
        out.append(
            Found(
                "login",
                {
                    "player": m["name"],
                    "x": round(float(m["x"]), 1),
                    "y": round(float(m["y"]), 1),
                    "z": round(float(m["z"]), 1),
                },
            )
        )
    elif msg.strip() in STOPPING:
        out.append(Found("quit", {}))
    elif m := CRASH.search(msg):
        name = m["path"].replace("\\", "/").rsplit("/", 1)[-1]
        out.append(
            Found(
                "crash", {"report": clean(name, 200), "via": "log", "untrusted": True}
            )
        )
    elif p.level == "FATAL" or (p.level == "ERROR" and not fml):
        out.append(
            Found(
                "error",
                {
                    "level": p.level,
                    "thread": clean(p.thread, 60),
                    "message": clean(msg, 300),
                    "untrusted": True,
                },
            )
        )
    if server and not any(f.kind in ("quit",) for f in out):
        out.append(Found("server_tick", {}))
    return out


class Dedupe:
    """Drops a line already seen in the other log (same time, kind and data) and error floods."""

    def __init__(self) -> None:
        self._recent: deque[tuple[str, str, str]] = deque(maxlen=256)
        self._errors: dict[str, float] = {}
        self._error_times: deque[float] = deque()

    def keep(self, time_text: str, found: Found, now: float) -> bool:
        if found.kind == "server_tick":
            return True
        key = (time_text, found.kind, repr(sorted(found.data.items())))
        if key in self._recent:
            return False
        self._recent.append(key)
        if found.kind != "error":
            return True
        message = str(found.data.get("message"))
        last = self._errors.get(message)
        while self._error_times and now - self._error_times[0] > 3600:
            self._error_times.popleft()
        if (last is not None and now - last < ERROR_EVERY_S) or len(
            self._error_times
        ) >= ERRORS_MAX:
            return False
        self._errors[message] = now
        self._error_times.append(now)
        return True
