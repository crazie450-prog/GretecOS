"""gregtec.prism (WO-1.4): Prism's CLI, running clients and Play's custom commands.

The INI escaping was also checked against real QSettings (PySide6 6.x, Qt 6 as in Prism 11.1.1)
when this was written: every value below was read back exactly and Qt wrote the same line (D121).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gregtec import config, prism

# The shape of a Prism 11.1.1 instance.cfg once Prism has saved it (self-written sample).
INSTANCE_CFG = """[General]
AutomaticJava=true
ConfigVersion=1.3
InstanceType=OneSix
JavaPath=C:/Games/PrismLauncher/java/java-runtime-delta/bin/javaw.exe
JvmArgs=-Dgregtec.autoexport=false
MaxMemAlloc=8192
OverrideCommands=false
OverrideJavaArgs=true
PostExitCommand=
PreLaunchCommand=
WrapperCommand=
iconKey=gtnh
lastLaunchTime=1762380000000
name=GTNH-Play
notes="first line, with a comma"
totalTimePlayed=12345

[UI]
mods_Page\\Columns=@ByteArray(AAAA)
"""
APP = Path("C:/GregtecOS-app")
PS = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
COMMANDS = [
    prism.hook_commands(APP, PS)[0],
    r'"C:\Program Files\GOS app\x.exe" -File "D:\a;b=c,d\y.ps1"',
    "plain value",
    " leading space",
    "trailing space ",
    "tab\tnew\nline",
    "@at start",
    "ünïcødé path C:\\Users\\Clä",
    "",
]


@pytest.mark.parametrize("value", COMMANDS)
def test_qt_escape_round_trips(value: str) -> None:
    assert prism.qt_unescape(prism.qt_escape(value)) == value


def test_qt_escape_matches_qsettings() -> None:
    """Lines QSettings itself wrote for these values (PySide6, 2026-11-06)."""
    assert prism.qt_escape(r'"C:\a b\x.exe" -File "C:\y.ps1"') == (
        r"\"C:\\a b\\x.exe\" -File \"C:\\y.ps1\""
    )
    assert prism.qt_escape("a;b") == '"a;b"'
    assert prism.qt_escape("@x") == "@@x"
    assert prism.qt_unescape('"first line, with a comma"') == "first line, with a comma"
    with pytest.raises(ValueError):
        prism.qt_unescape("a, b")  # Qt reads that as a list
    with pytest.raises(ValueError):
        prism.qt_escape("bell\a")


def test_hook_commands() -> None:
    pre, post = prism.hook_commands(APP, PS)
    assert pre == (
        f'"{PS}" -NoProfile -NonInteractive -ExecutionPolicy Bypass '
        f'-File "{APP / "scripts" / "prism_prelaunch.ps1"}"'
    )
    assert post.endswith(f'"{APP / "scripts" / "prism_postexit.ps1"}"')
    with pytest.raises(ValueError):
        prism.hook_commands(Path("C:/a$b"), PS)


def make_instance(tmp: Path, text: str = INSTANCE_CFG, newline: str = "\n") -> Path:
    inst = tmp / "instances" / "GTNH-Play"
    inst.mkdir(parents=True)
    with open(inst / "instance.cfg", "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", newline))
    return inst


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_install_sets_three_keys_and_keeps_the_rest(
    tmp_path: Path, newline: str
) -> None:
    inst = make_instance(tmp_path, newline=newline)
    pre, post = prism.hook_commands(APP, PS)
    plan = prism.plan_hooks(inst, pre, post)
    old = plan.old.split(newline)
    new = plan.new.split(newline)
    assert len(new) == len(old)
    diff = [(a, b) for a, b in zip(old, new) if a != b]
    assert diff == [
        ("OverrideCommands=false", "OverrideCommands=true"),
        ("PostExitCommand=", f"PostExitCommand={prism.qt_escape(post)}"),
        ("PreLaunchCommand=", f"PreLaunchCommand={prism.qt_escape(pre)}"),
    ]
    backup = prism.write_hooks(plan)
    assert backup is not None and backup.read_bytes() == plan.raw
    assert (inst / "instance.cfg").read_bytes() == plan.new.encode()
    again = prism.plan_hooks(inst, pre, post)
    assert not again.changed()  # idempotent
    assert again.current["PreLaunchCommand"] == pre
    assert again.current["PostExitCommand"] == post
    assert prism.write_hooks(again) is None

    removed = prism.plan_hooks(inst, pre, post, uninstall=True)
    assert removed.new.replace("\r\n", "\n") == INSTANCE_CFG


def test_missing_keys_are_added_to_general(tmp_path: Path) -> None:
    inst = make_instance(
        tmp_path, "[General]\nConfigVersion=1.3\nname=GTNH-Lab\n[UI]\nx=1\n"
    )
    plan = prism.plan_hooks(inst, "pre", "post")
    assert plan.new == (
        "[General]\nConfigVersion=1.3\nname=GTNH-Lab\nOverrideCommands=true\n"
        "PreLaunchCommand=pre\nPostExitCommand=post\n[UI]\nx=1\n"
    )


def test_foreign_commands_need_replace(tmp_path: Path) -> None:
    text = INSTANCE_CFG.replace(
        "PreLaunchCommand=", 'PreLaunchCommand="$INST_JAVA" -jar packwiz.jar'
    )
    inst = make_instance(tmp_path, text)
    with pytest.raises(ValueError, match="--replace"):
        prism.plan_hooks(inst, "pre", "post")
    assert prism.plan_hooks(inst, "pre", "post", replace=True).changed()
    # uninstall leaves a command that isn't GOS's
    plan = prism.plan_hooks(inst, "pre", "post", uninstall=True)
    assert not plan.changed()


def test_headerless_instance_cfg_is_refused(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, "InstanceType=OneSix\nname=GTNH-Play\n")
    with pytest.raises(ValueError, match="General"):
        prism.plan_hooks(inst, "pre", "post")


def test_bom_is_kept(tmp_path: Path) -> None:
    inst = make_instance(tmp_path, "\ufeff" + INSTANCE_CFG)
    pre, post = prism.hook_commands(APP, PS)
    plan = prism.plan_hooks(inst, pre, post)
    assert plan.new_bytes().startswith(b"\xef\xbb\xbf[General]")
    prism.write_hooks(plan)
    assert not prism.plan_hooks(inst, pre, post).changed()


class Installer:
    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.inst = make_instance(tmp)
        self.lab = tmp / "instances" / "GTNH-Lab"
        self.lab.mkdir()
        (self.lab / "instance.cfg").write_text("[General]\nname=GTNH-Lab\n")
        self.app = tmp / "app"
        (self.app / "scripts").mkdir(parents=True)
        for name in (prism.PRE_SCRIPT, prism.POST_SCRIPT):
            (self.app / "scripts" / name).write_text("# hook")
        self.config = tmp / "gos.toml"
        self.config.write_text(
            f'[paths]\nprism_instances = "{(tmp / "instances").as_posix()}"\n'
            f'gos_app = "{self.app.as_posix()}"\n'
        )
        self.running = False
        self.user = "clayton"
        monkeypatch.setattr(prism, "prism_running", lambda: self.running)
        monkeypatch.setattr(prism.getpass, "getuser", lambda: self.user)

    def run(self, *args: str) -> tuple[int, str]:
        import io

        out = io.StringIO()
        code = prism.install_main(["--config", str(self.config), *args], out)
        return code, out.getvalue()


def test_install_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = Installer(tmp_path, monkeypatch)
    before = (env.inst / "instance.cfg").read_bytes()
    code, text = env.run("--dry-run")
    assert code == 0 and "Dry run" in text
    assert (env.inst / "instance.cfg").read_bytes() == before

    env.running = True
    code, text = env.run()
    assert code == 1 and "Prism Launcher is running" in text
    assert (env.inst / "instance.cfg").read_bytes() == before

    env.running = False
    code, text = env.run()
    assert code == 0 and "written" in text
    ini = prism.IniFile.parse((env.inst / "instance.cfg").read_text(encoding="utf-8"))
    assert ini.get("OverrideCommands") == "true"
    assert prism.ours(ini.get("PreLaunchCommand")) and prism.ours(
        ini.get("PostExitCommand")
    )
    assert len(list(env.inst.glob("instance.cfg.gos-backup-*"))) == 1
    code, text = env.run()
    assert code == 0 and "nothing to do" in text

    code, text = env.run("--instance", "lab")
    assert code == 0
    assert prism.ours(
        prism.IniFile.parse((env.lab / "instance.cfg").read_text()).get(
            "PreLaunchCommand"
        )
    )

    code, text = env.run("--uninstall")
    assert code == 0
    ini = prism.IniFile.parse((env.inst / "instance.cfg").read_text(encoding="utf-8"))
    assert ini.get("PreLaunchCommand") == "" and ini.get("OverrideCommands") == "false"


def test_install_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = Installer(tmp_path, monkeypatch)
    env.user = "gos-dev"
    code, text = env.run()
    assert code == 1 and "gos-dev" in text
    env.user = "clayton"
    (env.app / "scripts" / prism.PRE_SCRIPT).unlink()
    code, text = env.run()
    assert code == 1 and "gos deploy" in text


def test_launch_command(tmp_path: Path) -> None:
    cfg = config.Config(
        prism_root=tmp_path / "Prism", prism_instances=tmp_path / "inst"
    )
    assert prism.launch_command(cfg, "export") == [
        str(tmp_path / "Prism" / prism.PRISM_EXE),
        "--launch",
        "GTNH-Export",
    ]


def test_running_clients_from_command_lines(tmp_path: Path) -> None:
    cfg = config.Config(prism_instances=Path("C:/Games/PrismLauncher/instances"))
    lines = [
        (
            r'"C:\Games\PrismLauncher\java\bin\javaw.exe" -Xmx8192m '
            r"-Djava.library.path=C:\Games\PrismLauncher\instances\GTNH-Export\natives -cp x"
        ),
        r"javaw.exe -Djava.library.path=C:\Games\PrismLauncher\instances\GTNH-Export-old\natives",
    ]
    assert prism.running_clients(cfg, lines) == ["export"]
    assert prism.running_clients(cfg, [lines[1]]) == []
    assert prism.folder_in(
        Path("C:/A/GTNH-Play"), 'java "-Dx=c:\\a\\gtnh-play\\natives"'
    )


@pytest.mark.skipif(sys.platform == "win32", reason="/proc")
def test_command_lines_sees_a_java_process(tmp_path: Path) -> None:
    java = tmp_path / "bin" / "java"
    java.parent.mkdir()
    java.symlink_to(sys.executable)
    natives = tmp_path / "instances" / "GTNH-Export" / "natives"
    child = subprocess.Popen(
        [
            str(java),
            "-c",
            "import time; time.sleep(30)",
            f"-Djava.library.path={natives}",
        ]
    )
    try:
        cfg = config.Config(prism_instances=tmp_path / "instances")
        deadline = time.monotonic() + 10
        while (
            "export" not in prism.running_clients(cfg) and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        assert prism.running_clients(cfg) == ["export"]
    finally:
        child.kill()
        child.wait()
    assert prism.running_clients(cfg) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Win32_Process through PowerShell")
def test_command_lines_on_windows() -> None:
    lines = prism.command_lines(("python.exe",))
    assert any(
        os.path.basename(sys.executable).lower() in line.lower() for line in lines
    )
