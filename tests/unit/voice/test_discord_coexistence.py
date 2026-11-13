"""WO-5.5 (D58, D246, D247): the voice client's audio routing and key hook leave Discord alone.

Each audited property has a test here, with the Windows fakes (a fake PortAudio, a fake pynput,
a fake doctor probe) so it runs on Linux too:

1. capture opens the microphone in WASAPI shared mode, never exclusive;
2. speech and the wake chime play on the configured endpoint (Arctis Chat, D56), shared, and
   nothing sets Windows' (or PortAudio's) default devices;
3. the key hook never suppresses or injects a key;
4. nothing in the voice client touches Discord's process, settings or keybinds, mutes an
   endpoint, or writes the registry (a static audit of the package's code);
5. ``gos voice keys`` and ``gos voice doctor`` warn about keys Discord users often bind;
6. ``gos voice doctor`` reports capture mode, devices, ducking, hook and keys.
"""

from __future__ import annotations

import ast
import io
import re
import sys
import types
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

import gregtec_voice
from gregtec_voice import audio, cli, doctor, keys, playback, ptt, wake
from gregtec_voice.audio import AudioError, Device
from gregtec_voice.settings import VoiceSettings, parse

PACKAGE = Path(gregtec_voice.__file__).parent
MIC = "Microphone (Arctis Nova Elite Chat)"
CHAT = "Headphones (Arctis Nova Elite Chat)"
GAME = "Headphones (Arctis Nova Elite Game)"


class NoDefaults:
    """``sounddevice.default``: PortAudio's in-process defaults. Nothing may set them."""

    def __setattr__(self, name: str, value: object) -> None:
        raise AssertionError(f"sounddevice.default.{name} was set")


class FakeSoundDevice:
    """``sounddevice`` as capture and playback use it, recording every stream's settings."""

    class PortAudioError(Exception):
        pass

    def __init__(self) -> None:
        self.default = NoDefaults()
        self.settings: list[dict[str, object]] = []
        self.inputs: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.written: list[tuple[int, bytes]] = []

    def query_hostapis(self) -> list[dict[str, object]]:
        return [
            {"name": "MME"},
            {
                "name": "Windows WASAPI",
                "default_input_device": 1,
                "default_output_device": 2,  # Game is Windows' default output
            },
        ]

    def query_devices(self) -> list[dict[str, object]]:
        return [
            {
                "name": "-",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 1,
            },
            {
                "name": MIC,
                "hostapi": 1,
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
            {
                "name": GAME,
                "hostapi": 1,
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
            {
                "name": CHAT,
                "hostapi": 1,
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
        ]

    def WasapiSettings(self, **kw: object) -> dict[str, object]:
        self.settings.append(kw)
        return kw

    def _stream(self, kw: dict[str, Any]) -> object:
        written = self.written

        class Stream:
            def start(self) -> None:
                pass

            def write(self, pcm: bytes) -> None:
                written.append((kw["device"], pcm))

            def stop(self) -> None:
                pass

            def abort(self) -> None:
                pass

            def close(self) -> None:
                pass

        return Stream()

    def RawInputStream(self, **kw: Any) -> object:  # noqa: ANN401
        self.inputs.append(kw)
        return self._stream(kw)

    def RawOutputStream(self, **kw: Any) -> object:  # noqa: ANN401
        self.outputs.append(kw)
        return self._stream(kw)


@pytest.fixture
def sd(monkeypatch: pytest.MonkeyPatch) -> FakeSoundDevice:
    fake = FakeSoundDevice()
    monkeypatch.setattr(audio, "_sounddevice", lambda: fake)
    monkeypatch.setattr(playback, "_sounddevice", lambda: fake)
    return fake


# --- 1. capture: shared, never exclusive ------------------------------------------------------


def test_capture_opens_the_microphone_shared(sd: FakeSoundDevice) -> None:
    assert audio.WASAPI_EXCLUSIVE is False
    source = audio.WasapiSource("")
    source.start(lambda block: None)
    source.stop()
    (stream,) = sd.inputs
    assert stream["device"] == 1
    assert stream["extra_settings"] == {"exclusive": False, "auto_convert": True}


# --- 2. output: the configured endpoint, shared; no default device changed -------------------


def test_speech_plays_shared_on_the_chat_endpoint_not_the_default(
    sd: FakeSoundDevice,
) -> None:
    out = playback.WasapiOutput("Chat")
    stream = out.open(24_000)
    stream.write(b"\0\0")
    stream.close()
    (opened,) = sd.outputs
    assert opened["device"] == 3  # Chat, though Game is Windows' default
    assert opened["extra_settings"] == {"exclusive": False, "auto_convert": True}


def test_the_wake_chime_plays_on_the_configured_endpoint(
    sd: FakeSoundDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to play on Windows' default output device (WO-5.5 fix): with the Arctis that is
    the Game endpoint, outside the ChatMix balance, and on a PC whose default is speakers
    the chime could reach the open microphone and Discord."""
    asked: list[str] = []

    def output(endpoint: str) -> playback.Output:
        asked.append(endpoint)
        return playback.WasapiOutput(endpoint)

    monkeypatch.setattr(wake, "default_output", output)
    monkeypatch.setattr(wake, "detector", lambda settings: object())
    monkeypatch.setattr(wake, "vad", lambda: object())

    class Bus:
        def __getattr__(self, name: str) -> Callable[..., None]:
            return lambda *args: None

    engine = wake.create(parse({"output_endpoint": "Chat"}), Bus())
    assert isinstance(engine, wake.WakeWord) and asked == ["Chat"]
    chime = engine.chime
    assert isinstance(chime, wake.OutputChime)
    chime._play()  # what play() runs on its own thread
    assert [d for d, _ in sd.written] == [3]
    assert sd.outputs[0]["extra_settings"] == {"exclusive": False, "auto_convert": True}


def test_a_chime_that_fails_is_logged_not_raised() -> None:
    class Broken:
        name = "broken"

        def open(self, rate: int) -> playback.Stream:
            raise AudioError("no device")

    wake.OutputChime(Broken())._play()


# --- 3. the hook: never suppresses, never injects --------------------------------------------


class FakeListener:
    made: ClassVar[list[FakeListener]] = []

    def __init__(self, **kw: Any) -> None:  # noqa: ANN401
        self.kw = kw
        self.native_id = 7
        self.suppressed = 0
        self.running = False
        FakeListener.made.append(self)

    def suppress_event(self) -> None:
        self.suppressed += 1

    def start(self) -> None:
        self.running = True

    def wait(self) -> None:
        pass

    def stop(self) -> None:
        self.running = False

    def join(self, timeout: float | None = None) -> None:
        pass

    def is_alive(self) -> bool:
        return self.running


@pytest.fixture
def fake_pynput(monkeypatch: pytest.MonkeyPatch) -> type[FakeListener]:
    FakeListener.made = []
    keyboard = types.ModuleType("pynput.keyboard")
    keyboard.Listener = FakeListener  # type: ignore[attr-defined]
    package = types.ModuleType("pynput")
    package.keyboard = keyboard  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pynput", package)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard)
    monkeypatch.setattr(ptt, "raise_priority", lambda native_id: True)
    return FakeListener


class KBD:
    def __init__(self, vk: int) -> None:
        self.vkCode = vk
        self.time = 0


def test_the_hook_passes_every_key_on(fake_pynput: type[FakeListener]) -> None:
    """pynput suppresses only with ``suppress=True`` or a filter that calls
    ``suppress_event()``; the hook does neither, for watched keys and every other key alike.
    The filter returns False: pynput's own translation is skipped, the key is not."""
    got: list[ptt.KeyEvent] = []
    f13, space = keys.KEYS["f13"].vk, 0x20
    hook = ptt.WindowsHook(ptt.KeyFilter({f13}, got.append))
    hook.install()
    (listener,) = fake_pynput.made
    assert not listener.kw.get("suppress", False)
    assert set(listener.kw) == {"win32_event_filter"}
    event_filter = listener.kw["win32_event_filter"]
    for msg in (ptt.WM_KEYDOWN, ptt.WM_KEYUP, ptt.WM_SYSKEYDOWN, ptt.WM_SYSKEYUP):
        for vk in (f13, space):
            assert event_filter(msg, KBD(vk)) is False
    assert listener.suppressed == 0
    assert [(e.vk, e.down) for e in got] == [
        (f13, True),
        (f13, False),
        (f13, True),
        (f13, False),
    ]
    hook.uninstall()


def test_the_watchdog_passes_missed_keys_on_inside_gos_only() -> None:
    """A press the hook missed goes to GOS's own queue, never back into Windows' input."""
    got: list[ptt.KeyEvent] = []
    f13 = keys.KEYS["f13"].vk
    key_filter = ptt.KeyFilter({f13}, got.append)
    hook = ptt.FakeHook(key_filter)
    hook.install()
    hook.dead = True
    state = ptt.FakeKeyState(down={f13})
    dog = ptt.Watchdog(hook, state, key_filter, clock=lambda: 5.0)
    for _ in range(ptt.MISSES):
        dog.check()
    assert [(e.vk, e.down, e.source) for e in got] == [(f13, True, "watchdog")]


# --- 4. a static audit: nothing touches Discord, devices, other processes or the registry ----

# Calls that would inject keys, suppress them, change audio devices or endpoints, reach into
# another process or window, or write the registry.
FORBIDDEN_NAMES = {
    "SendInput",
    "keybd_event",
    "mouse_event",
    "suppress_event",
    "SetDefaultEndpoint",
    "IPolicyConfig",
    "SetMute",
    "SetMasterVolumeLevel",
    "SetMasterVolumeLevelScalar",
    "OpenProcess",
    "TerminateProcess",
    "EnumProcesses",
    "CreateToolhelp32Snapshot",
    "FindWindowW",
    "FindWindowA",
    "PostMessageW",
    "SendMessageW",
    "SetValue",
    "SetValueEx",
    "CreateKey",
    "CreateKeyEx",
    "DeleteKey",
    "DeleteValue",
}
FORBIDDEN_IMPORTS = {
    "psutil",
    "pycaw",
    "comtypes",
    "pywinauto",
    "pypresence",
    "win32api",
    "win32gui",
    "win32process",
    "win32con",
    "keyboard",
    "pyautogui",
}
# The one program the package runs: nvidia-smi, for the TTS bench's VRAM figure (WO-5.3).
SUBPROCESS_OK = {"tts_bench.py"}


def _trees() -> list[tuple[str, ast.Module]]:
    return [
        (p.relative_to(PACKAGE).as_posix(), ast.parse(p.read_text(encoding="utf-8")))
        for p in sorted(PACKAGE.rglob("*.py"))
    ]


def test_the_voice_client_calls_nothing_that_touches_discord_or_devices() -> None:
    found = []
    for name, tree in _trees():
        for node in ast.walk(tree):
            ident = None
            if isinstance(node, ast.Name):
                ident = node.id
            elif isinstance(node, ast.Attribute):
                ident = node.attr
            if ident in FORBIDDEN_NAMES:
                found.append(f"{name}:{node.lineno}: {ident}")
            if isinstance(node, ast.keyword) and node.arg == "suppress":
                found.append(f"{name}:{node.value.lineno}: suppress=")
            shared = (
                isinstance(node, ast.keyword)
                and isinstance(node.value, ast.Name)
                and node.value.id == "WASAPI_EXCLUSIVE"
            )
            if isinstance(node, ast.keyword) and node.arg == "exclusive" and not shared:
                found.append(f"{name}:{node.value.lineno}: exclusive=")
    assert found == []


def test_the_voice_client_imports_nothing_that_reaches_other_programs() -> None:
    found = []
    for name, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            else:
                continue
            for mod in mods:
                top = mod.split(".")[0]
                if top in FORBIDDEN_IMPORTS:
                    found.append(f"{name}: {mod}")
                if top == "subprocess" and name not in SUBPROCESS_OK:
                    found.append(f"{name}: subprocess")
                if top == "winreg" and name != "doctor.py":
                    found.append(f"{name}: winreg")
    assert found == []


# The core's modules the package may import (D247): the audit above doesn't follow imports, so
# each is named here. gregtec.voicebench reads other processes' memory (psutil, nvidia-smi): it is
# the one exception, and only ``gos voice benchmark`` (cli.main) may reach it (WO-5.7, D248).
CORE_IMPORTS_OK = {
    "gregtec.agents.prefetch",
    "gregtec.agents.router",
    "gregtec.agents.tools.build_registry",
    "gregtec.agents.tools.glossary.Glossary",
    "gregtec.agents.tools.registry.Registry",
    "gregtec.applog",
    "gregtec.config",
    "gregtec.config.Config",
    "gregtec.control",
    "gregtec.db.access",
    "gregtec.gamestate.safe_open.read_bytes",
    "gregtec.llm.keys",
}
CORE_IMPORT_IN = {"gregtec.voicebench": ("cli.py", "main")}


def _core_imports() -> list[tuple[str, str, str]]:
    """(file, function or "", what) for each import of the core in the package."""
    found = []
    for name, tree in _trees():
        for top in tree.body:
            named = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            where = top.name if isinstance(top, named) else ""
            for node in ast.walk(top):
                if isinstance(node, ast.Import):
                    whats = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    whats = [f"{node.module}.{a.name}" for a in node.names]
                else:
                    continue
                for what in whats:
                    if what.split(".")[0] == "gregtec":
                        found.append((name, where, what))
    return found


def test_the_package_imports_only_the_core_modules_it_names() -> None:
    """L7 of the WO-5.7 review: a new import of the core is a decision, not an accident."""
    imports = _core_imports()
    assert ("cli.py", "main", "gregtec.voicebench") in imports

    def wrong(found: list[tuple[str, str, str]]) -> list[str]:
        return [
            f"{name}:{where}: {what}"
            for name, where, what in found
            if what not in CORE_IMPORTS_OK and CORE_IMPORT_IN.get(what) != (name, where)
        ]

    assert wrong(imports) == []
    # the audit's own check: voicebench anywhere else, or another core module, is caught
    assert wrong([("client.py", "", "gregtec.voicebench")]) != []
    assert wrong([("cli.py", "_eval", "gregtec.voicebench")]) != []
    assert wrong([("cli.py", "main", "gregtec.ingest.collect")]) != []


DISCORD_PLACES = re.compile(
    r"discord-ipc|[\\/]discord[\\/]|discord\.exe|appdata", re.IGNORECASE
)


def test_no_code_names_discord_s_files_or_pipe() -> None:
    """Discord's settings live under %APPDATA%\\discord, its RPC on \\\\?\\pipe\\discord-ipc-N:
    no string in the package's code points there (messages to Clayton may say "Discord")."""
    found = []
    for name, tree in _trees():
        for node in ast.walk(tree):
            text = node.value if isinstance(node, ast.Constant) else None
            if isinstance(text, str) and DISCORD_PLACES.search(text):
                found.append(f"{name}:{node.lineno}")
    assert found == []


def test_every_wasapi_stream_is_opened_shared() -> None:
    """Each ``WasapiSettings(...)`` call in the package passes ``exclusive=WASAPI_EXCLUSIVE``."""
    calls = []
    for name, tree in _trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "WasapiSettings"
            ):
                calls.append((name, {k.arg for k in node.keywords}))
    assert calls and all("exclusive" in kws for _, kws in calls), calls


# The denylist above can't see a name built at run time (``getattr(dll, "Send" + "Input")``),
# so the Windows functions and the modules the package loads are also allowlisted.
DLL_LOADERS = {"WinDLL", "CDLL", "OleDLL", "PyDLL", "LoadLibrary"}
DLL_SHORTCUTS = {"windll", "oledll", "cdll", "pydll"}
# ptt.py: the hook thread's priority, the watchdog's key state and the hook's clock.
DLL_FUNCTIONS_OK = {
    "OpenThread",
    "SetThreadPriority",
    "CloseHandle",
    "GetAsyncKeyState",
    "GetTickCount",
    # screen.py: the foreground game window's title and client rectangle, read only, for the
    # screen key's one frame (WO-8.2, D288); no other window is looked for or at
    "GetForegroundWindow",
    "GetWindowTextLengthW",
    "GetWindowTextW",
    "GetClientRect",
    "ClientToScreen",
}


def _parents(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }


def _scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    while not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        node = parents[node]
    return node


def _called(node: ast.AST, names: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    ident = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return ident in names


def test_only_known_windows_functions_are_called() -> None:
    """A DLL the package loads is either only loaded (stt.py's CUDA probe) or bound to a name
    whose every use is an allowlisted function: never chained, never passed to ``getattr``."""
    found = []
    for name, tree in _trees():
        parents = _parents(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in DLL_SHORTCUTS:
                found.append(f"{name}:{node.lineno}: ctypes.{node.attr}")
            if (
                isinstance(node, ast.Name)
                and node.id == "ctypes"
                and not isinstance(parents[node], ast.Attribute)
                and not isinstance(node.ctx, ast.Store)
            ):
                found.append(f"{name}:{node.lineno}: ctypes used other than ctypes.X")
            if not _called(node, DLL_LOADERS):
                continue
            up = parents[node]
            if isinstance(up, ast.Expr):
                continue  # loaded only, nothing called
            if not (
                isinstance(up, ast.Assign)
                and len(up.targets) == 1
                and isinstance(up.targets[0], ast.Name)
            ):
                found.append(f"{name}:{node.lineno}: a DLL used without a name")
                continue
            dll = up.targets[0].id
            for use in ast.walk(_scope(up, parents)):
                if (
                    isinstance(use, ast.Name)
                    and use.id == dll
                    and isinstance(use.ctx, ast.Load)
                ):
                    attr = parents[use]
                    if not (
                        isinstance(attr, ast.Attribute)
                        and attr.attr in DLL_FUNCTIONS_OK
                    ):
                        found.append(
                            f"{name}:{use.lineno}: {dll} used for something else"
                        )
    assert found == []


def _package_module_name(arg: ast.expr, scope: ast.AST) -> bool:
    """``arg`` is "gregtec_voice.<x>", as a literal or a local f-string assigned only that."""

    def ours(value: ast.AST) -> bool:
        if isinstance(value, ast.Constant):
            return isinstance(value.value, str) and value.value.startswith(
                "gregtec_voice."
            )
        if isinstance(value, ast.JoinedStr) and value.values:
            return ours(value.values[0])
        return False

    if not isinstance(arg, ast.Name):
        return ours(arg)
    assigned = [
        node.value
        for node in ast.walk(scope)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        and any(
            isinstance(t, ast.Name) and t.id == arg.id
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    params = (
        {a.arg for a in ast.walk(scope.args) if isinstance(a, ast.arg)}
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
        else set()
    )
    return (
        arg.id not in params
        and bool(assigned)
        and all(v is not None and ours(v) for v in assigned)
    )


def test_the_package_imports_only_its_own_modules_by_name() -> None:
    """``importlib.import_module`` (hooks.py's engine loader) can't be pointed at psutil."""
    found = []
    for name, tree in _trees():
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not _called(node, {"import_module", "__import__"}):
                continue
            assert isinstance(node, ast.Call)
            args = node.args or [k.value for k in node.keywords if k.arg == "name"]
            if not args or not _package_module_name(args[0], _scope(node, parents)):
                found.append(f"{name}:{node.lineno}: {ast.unparse(node)}")
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        'import ctypes\nf = getattr(ctypes.WinDLL("user32"), "Send" + "Input")\n',
        'import ctypes\nu = ctypes.WinDLL("user32")\nf = getattr(u, "SendInput")\n',
        'import ctypes\nctypes.WinDLL("user32").SendInput(1, None, 0)\n',
        "import ctypes\nu = ctypes.windll.user32\n",
        'import ctypes\nw = getattr(ctypes, "Win" + "DLL")\n',
    ],
)
def test_the_dll_allowlist_catches_computed_names(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys.modules[__name__], "_trees", lambda: [("x.py", ast.parse(source))]
    )
    with pytest.raises(AssertionError):
        test_only_known_windows_functions_are_called()


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nimportlib.import_module("psu" + "til")\n',
        "import importlib\ndef f(name):\n    return importlib.import_module(name)\n",
        'import importlib\ndef f():\n    n = "psutil"\n    return importlib.import_module(n)\n',
    ],
)
def test_the_import_allowlist_catches_other_modules(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys.modules[__name__], "_trees", lambda: [("x.py", ast.parse(source))]
    )
    with pytest.raises(AssertionError):
        test_the_package_imports_only_its_own_modules_by_name()


# --- 5. keys Discord users often bind --------------------------------------------------------


@pytest.mark.parametrize("name", ["pause", "scroll_lock"])
def test_keys_people_often_bind_in_discord_are_warned(name: str) -> None:
    warning = keys.discord_warning(keys.KEYS[name])
    assert warning is not None and "Settings > Keybinds" in warning


@pytest.mark.parametrize("name", ["f13", "f14", "f15", "f16", "f17", "f20", "f24"])
def test_other_keys_get_no_warning(name: str) -> None:
    assert keys.discord_warning(keys.KEYS[name]) is None


def test_gos_voice_keys_warns_about_discord_keys(tmp_path: Path) -> None:
    instances = tmp_path / "instances"
    game = instances / "GTNH-Lab" / ".minecraft"
    game.mkdir(parents=True)
    (instances / "GTNH-Lab" / "instance.cfg").write_text("", encoding="utf-8")
    (game / "options.txt").write_text("key_key.jump:57\n", encoding="utf-8")
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n'
        f'prism_instances = "{instances.as_posix()}"\n'
        '[voice]\ntalk_key = "Pause"\nconfirm_key = "F17"\ncancel_key = "F18"\n'
        'next_card_key = "F19"\n',
        encoding="utf-8",
    )
    out = io.StringIO()
    code = cli.main(["keys", "lab", "--config", str(toml)], out)
    text = out.getvalue()
    assert code == 0  # Discord's binds can't be read: a warning, not a failure
    assert (
        "talk: Pause: free in GTNH\n    Discord WARNING: people often bind Pause"
        in text
    )
    assert text.count("Discord WARNING") == 1


# --- 6. gos voice doctor ----------------------------------------------------------------------

INPUTS = [Device(1, MIC, True)]
OUTPUTS = [Device(2, GAME, True), Device(3, CHAT, False)]


def probe(
    *,
    inputs: Sequence[Device] = INPUTS,
    outputs: Sequence[Device] = OUTPUTS,
    ducking: int | None = 3,
    hook_error: Exception | None = None,
    ducking_error: Exception | None = None,
) -> doctor.Probe:
    hooked: list[Sequence[int]] = []

    def hook_check(watched: Sequence[int]) -> None:
        hooked.append(watched)
        if hook_error is not None:
            raise hook_error

    def read_ducking() -> int | None:
        if ducking_error is not None:  # e.g. int() of a REG_SZ that isn't a number
            raise ducking_error
        return ducking

    return doctor.Probe(
        lambda: list(inputs), lambda: list(outputs), read_ducking, hook_check
    )


def report(voice: VoiceSettings, p: doctor.Probe) -> tuple[int, str]:
    out = io.StringIO()
    return doctor.report(voice, p, out), out.getvalue()


def test_doctor_reports_everything_the_runbook_needs() -> None:
    problems, text = report(VoiceSettings(), probe())
    assert problems == 0
    assert "mode: WASAPI shared mode, 16 kHz mono, 20 ms blocks" in text
    assert f"microphone: {MIC} (input_device = '')" in text
    assert f"speech and the wake chime: {CHAT} (output_endpoint = 'Chat')" in text
    assert f"Windows' default output: {GAME} (GOS never changes it)" in text
    assert "Windows communications ducking: 3 (do nothing)" in text
    assert "nothing is suppressed or injected" in text
    assert "hook: installs and comes out again" in text
    for line in ("talk: F13", "confirm: F14", "cancel: F15", "next card: F16"):
        assert line in text
    assert "triggers: wake first, key as backup; talk mode hold" in text
    assert text.count("Discord WARNING") == 0  # D180's keys: Discord binds none (D246)
    assert "never mutes it (D58)" in text
    assert "\n! " not in text


@pytest.mark.parametrize(
    ("kwargs", "expect"),
    [
        ({"ducking": None}, "not set (Windows' default: reduce other sounds by 80%)"),
        (
            {"ducking": 1},
            "1 (reduce other sounds by 80%): run scripts\\set_comm_ducking.ps1",
        ),
        ({"outputs": [Device(2, GAME, True)]}, "using Windows' default output device"),
        ({"inputs": []}, "lists no capture device"),
        (
            {"ducking_error": ValueError("invalid literal for int(): 'x'")},
            "Windows communications ducking: can't read it",
        ),
        (
            {"hook_error": RuntimeError("no desktop")},
            "hook: didn't install (no desktop)",
        ),
    ],
)
def test_doctor_marks_what_needs_attention(kwargs: dict[str, Any], expect: str) -> None:
    problems, text = report(VoiceSettings(), probe(**kwargs))
    assert problems == 1
    (marked,) = [line for line in text.splitlines() if line.startswith("! ")]
    assert expect in marked


def test_doctor_checks_the_hook_on_the_configured_keys() -> None:
    seen: list[list[int]] = []

    def hook_check(watched: Sequence[int]) -> None:
        seen.append(list(watched))

    p = doctor.Probe(lambda: INPUTS, lambda: OUTPUTS, lambda: 3, hook_check)
    voice = parse({"talk_key": "Scroll Lock"})
    problems, text = report(voice, p)
    assert problems == 0 and "talk: Scroll Lock" in text
    assert seen == [[k.vk for k in voice.all_keys().values()]]


@pytest.mark.skipif(
    sys.platform == "win32", reason="Linux: the Windows checks are skipped"
)
def test_gos_voice_doctor_on_linux_says_what_it_did_not_check(tmp_path: Path) -> None:
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n[voice]\n',
        encoding="utf-8",
    )
    out = io.StringIO()
    assert cli.main(["doctor", "--config", str(toml)], out) == 0
    text = out.getvalue()
    assert text.count("not checked: Windows only") == 4  # mic, output, ducking, hook
    assert "mode: WASAPI shared mode" in text and "talk: F13" in text
    with pytest.raises(doctor.Unavailable):
        doctor.read_ducking()


def test_gos_voice_doctor_exits_1_when_something_needs_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "windows_probe", lambda: probe(ducking=0))
    toml = tmp_path / "gos.toml"
    toml.write_text(
        f'[paths]\ngos_home = "{(tmp_path / "home").as_posix()}"\n[voice]\n',
        encoding="utf-8",
    )
    out = io.StringIO()
    assert cli.main(["doctor", "--config", str(toml)], out) == 1
    assert (
        "! Windows communications ducking: 0 (mute all other sounds)" in out.getvalue()
    )
    assert "1 line(s) marked ! need attention." in out.getvalue()


@pytest.mark.skipif(sys.platform != "win32", reason="the registry is Windows only")
def test_reading_the_ducking_setting_on_windows() -> None:
    value = doctor.read_ducking()
    assert value is None or isinstance(value, int)


@pytest.mark.skipif(sys.platform != "win32", reason="the hook is Windows only")
def test_the_doctor_s_hook_check_installs_and_removes_the_real_hook() -> None:
    doctor.check_hook([keys.KEYS["f24"].vk])  # observe only: no key is sent
