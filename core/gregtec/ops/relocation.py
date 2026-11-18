"""``gos relocate check``: the relocation config check (WO-11.2; plan 4.2 "Moving later", 4.6;
D03, D06, D07, D45, D335). Documentation and config validation only: no server code.

Moving the core to the spare PC is mostly config plus three pieces of work (plan 4.2), and none
of them is built before day one (D45). This check reads gos.toml and an OpenComputers.cfg (Lab's
by default; read through :func:`gregtec.gamestate.safe_open.read_text`, never written) and says,
for each step of docs/runbooks/relocation.md, what holds today and what the move still needs:

1. ``client_forwarder``: logs, JourneyMap ``mp`` data and the VisualProspecting client cache stay
   on the gaming PC [B§8]: the forwarder is unbuilt work, always ``todo``.
2. ``oc_rule``: OC's first ``filteringRules`` entry. Today it must be ``allow ip:127.0.0.1``
   (the core on the same PC); with ``--core-ip`` it must be ``allow ip:<that address>``, first,
   because GTNH's shipped rules deny 192.168.0.0/16 [C§1.3]. A wrong rule is a ``problem``.
3. ``core_lan``: ``[core] host`` is loopback, the only value config.py allows (plan 4.6); the LAN
   listener with a token and a firewall rule is unbuilt work (``todo``).
4. ``core_ip``: ``--core-ip`` must be a private IPv4 address (RFC 1918) and not loopback: the
   core is never put on a public address (plan 4.6, D53).
5. ``world``: ``[paths] world`` names Play's world; after the move the watchers read the
   server's copy of it (a ``todo`` when it is set, a ``problem`` when it is not).
6. ``tailnet``: the phone's name in ``[core] allowed_hosts`` belongs to the PC that runs the core,
   so it changes with the move (``gos tailnet setup`` on the new PC, D301).

Exit 0 with only ``ok`` and ``todo``; 1 with any ``problem``.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from gregtec import config, tweaks
from gregtec.gamestate.safe_open import read_text

OK, TODO, PROBLEM = "ok", "todo", "problem"
LOOPBACK_RULE = tweaks.LOOPBACK


@dataclass(frozen=True)
class Item:
    step: str
    status: str
    detail: str


def oc_rules(text: str) -> list[str] | None:
    """OpenComputers.cfg's ``internet.filteringRules`` items (None: no such list)."""
    doc = tweaks.Doc(text)
    span = tweaks._block(doc.lines, ["opencomputers", "internet"])
    found = tweaks._oc_list(doc.lines, span, "filteringRules") if span else None
    return None if found is None else found.items


def check_ip(raw: str) -> Item:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return Item("core_ip", PROBLEM, f"{raw!r} is not an IP address")
    if ip.version != 4 or ip.is_loopback or not ip.is_private or ip.is_link_local:
        return Item(
            "core_ip",
            PROBLEM,
            f"{raw} is not a private LAN IPv4 address (10/8, 172.16/12, 192.168/16): "
            "the core never listens on a public address (plan 4.6)",
        )
    return Item("core_ip", OK, f"{raw} is a private LAN address")


def check_oc(rules: list[str] | None, core_ip: str | None) -> Item:
    if rules is None:
        return Item("oc_rule", PROBLEM, "no opencomputers.internet.filteringRules list")
    want = f"allow ip:{core_ip}" if core_ip else LOOPBACK_RULE
    first = rules[0] if rules else "(empty)"
    if first == want:
        return Item("oc_rule", OK, f'first rule is "{want}"')
    if want in rules:
        return Item(
            "oc_rule",
            PROBLEM,
            f'"{want}" is there but not first (first is "{first}"): a deny before it wins',
        )
    return Item("oc_rule", PROBLEM, f'first rule is "{first}"; it must be "{want}"')


def check(cfg: config.Config, core_ip: str | None, oc_text: str | None) -> list[Item]:
    items = [
        Item(
            "client_forwarder",
            TODO,
            "unbuilt (D45): logs, JourneyMap mp data and the VisualProspecting cache stay on "
            "the gaming PC [B§8]",
        )
    ]
    ip_item = check_ip(core_ip) if core_ip is not None else None
    if ip_item is not None:
        items.append(ip_item)
    usable_ip = core_ip if ip_item is not None and ip_item.status == OK else None
    if oc_text is None:
        items.append(Item("oc_rule", TODO, "no OpenComputers.cfg found to check"))
    elif core_ip is None or usable_ip is not None:
        items.append(check_oc(oc_rules(oc_text), usable_ip))
    items.append(
        Item(
            "core_lan",
            TODO,
            f"[core] host is {cfg.core_host} (loopback only, plan 4.6); the LAN listener "
            "with a token and a firewall rule is unbuilt (D45)",
        )
    )
    if cfg.world:
        items.append(
            Item(
                "world",
                TODO,
                f"[paths] world = {cfg.world!r}: after the move the watchers read the "
                "server's copy of it",
            )
        )
    else:
        items.append(Item("world", PROBLEM, "[paths] world is not set (Play's world)"))
    name = cfg.tailnet_host
    items.append(
        Item(
            "tailnet",
            TODO if name else OK,
            f"{name} is this PC's tailnet name: run gos tailnet setup on the new PC and "
            "replace it in [core] allowed_hosts"
            if name
            else "no tailnet name in [core] allowed_hosts (the phone isn't set up)",
        )
    )
    return items


def default_oc_config(cfg: config.Config) -> Path | None:
    try:
        _, game = tweaks.locate(cfg.instance("lab"))
    except ValueError:
        return None
    path = game / "config" / "OpenComputers.cfg"
    return path if path.is_file() else None


def run(
    cfg: config.Config, core_ip: str | None, oc_config: Path | None, out: TextIO
) -> int:
    path = oc_config if oc_config is not None else default_oc_config(cfg)
    text = None
    if path is not None:
        try:
            text = read_text(path)
        except OSError as exc:
            print(f"Can't read {path}: {exc}", file=out)
            return 2
    items = check(cfg, core_ip, text)
    print(f"OpenComputers.cfg: {path or 'none found'}", file=out)
    for item in items:
        print(f"{item.status:<8} {item.step:<17} {item.detail}", file=out)
    print(
        "The steps, their order and the rollback: docs/runbooks/relocation.md", file=out
    )
    return 1 if any(i.status == PROBLEM for i in items) else 0
