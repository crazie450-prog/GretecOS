"""WO-11.2: ``gos relocate check`` (D335): config validation only, no server code (D45)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from gregtec import cli, config
from gregtec.ops import relocation

SHIPPED = """opencomputers {
  internet {
    filteringRules=[
      "allow ip:127.0.0.1",
      "deny ip:127.0.0.0/8", "deny ip:192.168.0.0/16", "deny private", "allow default"
    ]
    requestTimeout=10
  }
}
"""


def statuses(items: list[relocation.Item]) -> dict[str, str]:
    return {i.step: i.status for i in items}


def test_todays_layout_is_ok_with_the_work_still_todo() -> None:
    cfg = config.Config(world="World")
    items = relocation.check(cfg, None, SHIPPED)
    assert statuses(items) == {
        "client_forwarder": "todo",
        "oc_rule": "ok",
        "core_lan": "todo",
        "world": "todo",
        "tailnet": "ok",
    }


@pytest.mark.parametrize(
    ("ip", "status"),
    [
        ("192.168.1.20", "ok"),
        ("10.0.0.5", "ok"),
        ("172.16.3.4", "ok"),
        ("8.8.8.8", "problem"),
        ("127.0.0.1", "problem"),
        ("169.254.1.1", "problem"),
        ("fd00::1", "problem"),
        ("spare-pc", "problem"),
    ],
)
def test_the_core_ip_must_be_a_private_lan_address(ip: str, status: str) -> None:
    assert relocation.check_ip(ip).status == status


def test_the_lan_rule_must_come_first() -> None:
    moved = SHIPPED.replace('"allow ip:127.0.0.1"', '"allow ip:192.168.1.20"')
    assert (
        relocation.check_oc(relocation.oc_rules(moved), "192.168.1.20").status == "ok"
    )
    late = SHIPPED.replace(
        '"allow default"', '"allow default", "allow ip:192.168.1.20"'
    )
    item = relocation.check_oc(relocation.oc_rules(late), "192.168.1.20")
    assert item.status == "problem" and "not first" in item.detail
    item = relocation.check_oc(relocation.oc_rules(SHIPPED), "192.168.1.20")
    assert item.status == "problem" and "must be" in item.detail
    assert relocation.check_oc(relocation.oc_rules("x {}"), None).status == "problem"


def test_the_tailnet_name_moves_with_the_core() -> None:
    cfg = config.Config(world="World", extra_allowed_hosts=("pc.tail1234.ts.net",))
    [tail] = [i for i in relocation.check(cfg, None, None) if i.step == "tailnet"]
    assert tail.status == "todo" and "pc.tail1234.ts.net" in tail.detail


def test_the_command_reads_a_given_oc_config_and_writes_nothing(tmp_path: Path) -> None:
    oc = tmp_path / "OpenComputers.cfg"
    oc.write_text(SHIPPED, encoding="utf-8")
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\nworld = "World"\n',
        encoding="utf-8",
    )
    before = sorted(p.name for p in tmp_path.rglob("*"))
    out = io.StringIO()
    code = relocation.run(config.load(toml), "192.168.1.20", oc, out)
    assert code == 1  # OC's first rule is still the loopback one
    assert "problem  oc_rule" in out.getvalue()
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    assert (
        cli.main(["relocate", "check", "--config", str(toml), "--oc-config", str(oc)])
        == 0
    )
