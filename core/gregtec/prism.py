"""Prism Launcher: its CLI, the running clients, and Play's custom commands (WO-1.4, D121).

What Prism 11.1.1 does with an instance's custom commands (its source, ``launcher/launch/``,
``minecraft/MinecraftInstance.cpp``; read 2026-11-06):

- The **pre-launch** command runs after the Java checks and before the game starts. If it exits
  non-zero, crashes or can't start, the launch fails there and the game never starts (V25): the
  console logs "Pre-Launch command failed with code N" and opens if "show console on error" is on.
- The **post-exit** command runs only after the game exits with code 0. After a crash (non-zero
  exit) or a kill from Prism it doesn't run. Until it ends, Prism still counts the instance as
  running, so launching it again only shows its console.
- Both run with the game folder as working directory, with Prism's own environment (minus a few
  Java variables) plus ``INST_NAME``, ``INST_ID``, ``INST_DIR``, ``INST_MC_DIR``, ``INST_JAVA``
  and ``INST_JAVA_ARGS``. The command is split by ``QProcess::splitCommand`` (double quotes group,
  no shell, so ``%VAR%`` is not expanded) after Prism expands ``$VAR`` and ``${VAR}``.
- They are stored in the instance's ``instance.cfg`` (a QSettings INI file, ``[General]``
  section) as ``PreLaunchCommand`` and ``PostExitCommand``, used only with
  ``OverrideCommands=true``. Prism keeps the file in memory and rewrites it whole (on every
  launch, for the play time), so it is only edited while Prism is closed.
- ``prismlauncher --launch <instance id>`` (the id is the instance's folder name) asks a running
  Prism to launch it and returns at once; without a running Prism it starts one (D100).
"""

from __future__ import annotations

import argparse
import base64
import codecs
import datetime as dt
import getpass
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from gregtec import config
from gregtec.gamestate.safe_open import read_bytes

PRISM_EXE = "prismlauncher.exe" if sys.platform == "win32" else "prismlauncher"
JAVA_NAMES = ("java.exe", "javaw.exe") if sys.platform == "win32" else ("java", "javaw")
CREATE_NO_WINDOW = 0x08000000
INSTANCE_CFG = "instance.cfg"
PRE_SCRIPT = "prism_prelaunch.ps1"  # in GOS_APP\scripts (D91)
POST_SCRIPT = "prism_postexit.ps1"
DEV_ACCOUNT = "gos-dev"  # Claude Code's account (D75)
KEYS = ("OverrideCommands", "PreLaunchCommand", "PostExitCommand")


def launch_command(cfg: config.Config, role: str) -> list[str]:
    """Prism's CLI launch of ``role``'s instance (D100: ``--launch`` takes the folder name)."""
    return [str(cfg.prism_root / PRISM_EXE), "--launch", cfg.instance(role).name]


# --- running processes ------------------------------------------------------------------------

# As WO-2.1's supervisor lists them: a CIM failure stops the script (non-zero exit, so OSError),
# and setting the output encoding can fail without a console, which doesn't matter.
_PS_LIST = (
    "$ErrorActionPreference = 'Stop'; "
    "try {{ [Console]::OutputEncoding = [Text.Encoding]::UTF8 }} catch {{ }}; "
    '$p = @(Get-CimInstance -ClassName Win32_Process -Filter "{filter}" | '
    "ForEach-Object {{ $_.CommandLine }} | Where-Object {{ $_ }}); "
    "ConvertTo-Json -InputObject $p -Compress"
)


def command_lines(names: Sequence[str] = JAVA_NAMES) -> list[str]:
    """Command lines of this PC's processes whose program is one of ``names``.

    Windows: Win32_Process through PowerShell (about a second; Clayton's processes see his own
    processes' command lines). Elsewhere: /proc. Raises OSError if it can't tell.
    """
    if sys.platform == "win32":
        flt = " OR ".join(f"Name = '{n}'" for n in names)
        script = _PS_LIST.format(filter=flt)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        powershell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        try:
            done = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded,
                ],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise OSError("PowerShell didn't list the processes within 60 s") from None
        if done.returncode != 0:
            err = done.stderr.decode("utf-8", "replace").strip()[:300]
            raise OSError(f"PowerShell exited {done.returncode}: {err}")
        try:
            found = json.loads(done.stdout.decode("utf-8-sig", "replace") or "[]")
        except ValueError as exc:
            raise OSError(f"PowerShell's process list isn't JSON ({exc})") from None
        if isinstance(found, str):
            found = [found]
        if not isinstance(found, list):
            raise OSError("PowerShell's process list isn't a list")
        return [c for c in found if isinstance(c, str)]
    proc = Path("/proc")
    if not proc.is_dir():
        raise OSError("no /proc to list processes from")
    lines = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue  # gone, or not ours
        if (
            argv
            and argv[0]
            and os.path.basename(argv[0].decode("utf-8", "replace")) in names
        ):
            lines.append(" ".join(a.decode("utf-8", "replace") for a in argv if a))
    return lines


def folder_in(folder: Path, command_line: str) -> bool:
    """Whether ``command_line`` names ``folder`` or a path inside it (case and slashes ignored)."""
    norm = command_line.replace("\\", "/").lower()
    forms = {str(folder)}
    try:
        forms.add(str(folder.resolve()))
    except OSError:
        pass
    for form in forms:
        path = re.escape(form.replace("\\", "/").rstrip("/").lower())
        if re.search(path + r'(?=[/"\s;:]|$)', norm):
            return True
    return False


def running_clients(
    cfg: config.Config, lines: Sequence[str] | None = None
) -> list[str]:
    """The roles ("play", "export", "lab") whose instance has a running Java client.

    Prism starts each client's Java with paths inside its instance folder on the command line
    (its natives and libraries). Raises OSError if the processes can't be listed.
    """
    found = command_lines() if lines is None else lines
    return [
        role
        for role in ("play", "export", "lab")
        if any(folder_in(cfg.instance(role), line) for line in found)
    ]


def prism_running() -> bool:
    """Whether Prism Launcher runs. Raises OSError if the processes can't be listed."""
    return bool(command_lines((PRISM_EXE,)))


# --- instance.cfg (QSettings INI) ----------------------------------------------------------------

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def qt_escape(value: str) -> str:
    """``value`` as QSettings (Qt 6, INI format) writes a string: backslash and quote escaped,
    the whole value quoted when it has ``;``, ``,`` or ``=`` or starts or ends with a space."""
    if any(ord(c) < 0x20 and c not in _ESCAPES for c in value):
        raise ValueError(f"control character in {value!r}")
    out = "".join(_ESCAPES.get(c, c) for c in value)
    if any(c in value for c in ";,=") or value[:1] == " " or value[-1:] == " ":
        out = f'"{out}"'
    if value.startswith("@"):
        out = "@" + out  # Qt marks its own @Variant(...) values with a leading @
    return out


_UNESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}


def qt_unescape(raw: str) -> str:
    """A single string value as QSettings reads it from an INI file (the inverse of
    :func:`qt_escape`; an unquoted ``,`` would make Qt read a list, which is refused)."""
    out: list[str] = []
    quoted = False
    i = 0
    raw = raw.strip()
    if raw.startswith("@@"):
        raw = raw[1:]
    while i < len(raw):
        c = raw[i]
        if c == '"':
            quoted = not quoted
        elif c == "\\" and i + 1 < len(raw):
            i += 1
            e = raw[i]
            if e == "x":
                m = re.match(r"[0-9A-Fa-f]{1,4}", raw[i + 1 :])
                if not m:
                    raise ValueError(f"bad \\x escape in {raw!r}")
                out.append(chr(int(m.group(), 16)))
                i += len(m.group())
            else:
                out.append(_UNESCAPES.get(e, e))
        elif c in ";," and not quoted:
            raise ValueError(f"unquoted {c!r} in {raw!r}: Qt would not read one string")
        else:
            out.append(c)
        i += 1
    return "".join(out)


@dataclass
class IniFile:
    """instance.cfg as lines, editing only the keys asked for in ``[General]``."""

    lines: list[str]
    start: int  # index of the line after "[General]"
    end: int  # index of the next section header (or len(lines))
    newline: str

    @classmethod
    def parse(cls, text: str) -> IniFile:
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        try:
            start = (
                next(i for i, ln in enumerate(lines) if ln.strip() == "[General]") + 1
            )
        except StopIteration:
            raise ValueError(
                "instance.cfg has no [General] section (Prism writes one once it has saved "
                "the instance: launch it once, quit, then try again)"
            ) from None
        end = next(
            (i for i in range(start, len(lines)) if lines[i].lstrip().startswith("[")),
            len(lines),
        )
        return cls(lines, start, end, newline)

    def _find(self, key: str) -> int | None:
        for i in range(self.start, self.end):
            k, sep, _ = self.lines[i].partition("=")
            if sep and k.strip() == key:
                return i
        return None

    def get(self, key: str) -> str | None:
        i = self._find(key)
        return None if i is None else qt_unescape(self.lines[i].partition("=")[2])

    def set(self, key: str, value: str) -> None:
        line = f"{key}={qt_escape(value)}"
        i = self._find(key)
        if i is None:
            self.lines.insert(self.end, line)
            self.end += 1
        else:
            self.lines[i] = line

    def text(self) -> str:
        return self.newline.join(self.lines) + self.newline


def powershell_path() -> Path:
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def hook_commands(gos_app: Path, powershell: Path | None = None) -> tuple[str, str]:
    """Play's pre-launch and post-exit commands: GOS_APP's hook scripts through PowerShell."""
    ps = powershell or powershell_path()
    for path in (ps, gos_app):
        if any(c in str(path) for c in '"$'):
            raise ValueError(
                f"{path} has a quote or $, which Prism's command line can't hold"
            )
    scripts = gos_app / "scripts"

    def command(script: str) -> str:
        return (
            f'"{ps}" -NoProfile -NonInteractive -ExecutionPolicy Bypass '
            f'-File "{scripts / script}"'
        )

    return command(PRE_SCRIPT), command(POST_SCRIPT)


def ours(command: str | None) -> bool:
    return bool(command) and (PRE_SCRIPT in command or POST_SCRIPT in command)


@dataclass
class HookPlan:
    path: Path  # instance.cfg
    raw: bytes  # its bytes now
    new: str  # its text with the hooks set (or removed), without the BOM
    current: dict[str, str | None]

    @property
    def old(self) -> str:
        return self.raw.decode("utf-8-sig")

    def new_bytes(self) -> bytes:
        bom = codecs.BOM_UTF8 if self.raw.startswith(codecs.BOM_UTF8) else b""
        return bom + self.new.encode("utf-8")

    def changed(self) -> bool:
        return self.raw != self.new_bytes()


def plan_hooks(
    instance: Path,
    pre: str,
    post: str,
    *,
    uninstall: bool = False,
    replace: bool = False,
) -> HookPlan:
    """The edit of ``instance``'s instance.cfg. Raises ValueError (and reads only)."""
    path = instance / INSTANCE_CFG
    raw = read_bytes(path)
    ini = IniFile.parse(raw.decode("utf-8-sig"))
    current = {k: ini.get(k) for k in (*KEYS, "WrapperCommand")}
    if uninstall:
        for key in ("PreLaunchCommand", "PostExitCommand"):
            if ours(current[key]):
                ini.set(key, "")
        commands = ("PreLaunchCommand", "PostExitCommand", "WrapperCommand")
        if current["OverrideCommands"] is not None and not any(
            ini.get(k) for k in commands
        ):
            ini.set("OverrideCommands", "false")
    else:
        foreign = [
            f"{k}={current[k]!r}"
            for k in ("PreLaunchCommand", "PostExitCommand")
            if current[k] and not ours(current[k])
        ]
        if foreign and not replace:
            raise ValueError(
                "the instance already has its own custom commands ("
                + "; ".join(foreign)
                + "); run again with --replace to overwrite them"
            )
        ini.set("OverrideCommands", "true")
        ini.set("PreLaunchCommand", pre)
        ini.set("PostExitCommand", post)
    return HookPlan(path, raw, ini.text(), current)


def write_hooks(plan: HookPlan) -> Path | None:
    """Back up instance.cfg, then write the new text through a temp file and a rename.
    Returns the backup's path (None when nothing changed)."""
    if not plan.changed():
        return None
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    for n in range(1, 100):
        backup = plan.path.with_name(
            f"{INSTANCE_CFG}.gos-backup-{stamp}" + (f"-{n}" if n > 1 else "")
        )
        try:
            with open(backup, "xb") as f:
                f.write(plan.raw)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"no free backup name for {plan.path}")
    tmp = plan.path.with_name(f"{INSTANCE_CFG}.gos-tmp-{os.getpid()}")
    with open(tmp, "xb") as f:
        f.write(plan.new_bytes())
    os.replace(tmp, plan.path)
    return backup


def install_main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    """``scripts/install_prism_hooks.py``: Clayton runs it himself, with Prism closed."""
    parser = argparse.ArgumentParser(
        prog="install_prism_hooks.py",
        description="Set GOS's pre-launch and post-exit hooks (GOS_APP\\scripts\\"
        "prism_prelaunch.ps1, prism_postexit.ps1) on GTNH-Play, or on GTNH-Lab to test them. "
        "Close Prism first: it rewrites instance.cfg from memory.",
    )
    parser.add_argument("--instance", choices=("play", "lab"), default="play")
    parser.add_argument("--uninstall", action="store_true", help="remove GOS's hooks")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="overwrite custom commands that aren't GOS's",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show the change; write nothing"
    )
    parser.add_argument("--config", type=Path, default=None, help="gos.toml to read")
    args = parser.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    if getpass.getuser().lower() == DEV_ACCOUNT:
        print(
            "Refused: gos-dev never sets Play's commands (D75). Clayton runs this.",
            file=out,
        )
        return 1
    instance = cfg.instance(args.instance)
    pre_ps1 = cfg.gos_app / "scripts" / PRE_SCRIPT
    if not args.uninstall and not (
        pre_ps1.is_file() and pre_ps1.with_name(POST_SCRIPT).is_file()
    ):
        print(
            f"Refused: {pre_ps1.parent} has no {PRE_SCRIPT} and {POST_SCRIPT}; "
            "run gos deploy first (D91).",
            file=out,
        )
        return 1
    if not args.dry_run:
        try:
            running = prism_running()
        except OSError as exc:
            print(f"Refused: can't tell whether Prism is running ({exc}).", file=out)
            return 1
        if running:
            print(
                "Refused: Prism Launcher is running and would overwrite instance.cfg. "
                "Close it (File > Exit, or the tray icon), then run this again.",
                file=out,
            )
            return 1
    try:
        pre, post = hook_commands(cfg.gos_app)
        plan = plan_hooks(
            instance, pre, post, uninstall=args.uninstall, replace=args.replace
        )
    except (OSError, ValueError) as exc:
        print(f"Refused: {instance}: {exc}", file=out)
        return 1
    print(f"{args.instance}: {plan.path}", file=out)
    for key in ("PreLaunchCommand", "PostExitCommand"):
        print(f"  {key} now: {plan.current[key] or '(none)'}", file=out)
    if not args.uninstall:
        print(f"  PreLaunchCommand: {pre}", file=out)
        print(f"  PostExitCommand:  {post}", file=out)
    if not plan.changed():
        print("  already set; nothing to do", file=out)
        return 0
    if args.dry_run:
        print("Dry run: nothing written.", file=out)
        return 0
    try:
        backup = write_hooks(plan)
    except OSError as exc:
        print(f"Write failed: {exc}", file=out)
        return 1
    print(f"  written (the old file is {backup})", file=out)
    return 0
