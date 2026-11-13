"""WO-8.2 (plan 7.7, D288): the screen key's one frame in the voice client.

The capture parts (encoding, the black-frame check, the F2-screenshot fallback, the window on
its monitor, dxcam through a fake module and fake windows, the fallback order), the screen key
in the controller (one press, one frame; nothing captures without the key), and the frame's
trip to a real core over loopback HTTP. Synthetic images only; Windows' parts are faked.
"""

from __future__ import annotations

import io
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from gregtec_voice import screen
from gregtec_voice.audio import Block
from gregtec_voice.client import (
    NO_FRAME,
    SCREEN_OFF,
    Answered,
    CoreClient,
    CoreError,
    Online,
    Pushed,
    VoiceClient,
)
from gregtec_voice.ptt import KeyEvent
from gregtec_voice.screen import (
    DXCAM,
    SCREENSHOT,
    AutoCapture,
    CaptureError,
    DxcamCapture,
    Frame,
    Rect,
    Screenshots,
)
from gregtec_voice.settings import VoiceSettings, parse

F13, F14, F15, F16, F17 = 0x7C, 0x7D, 0x7E, 0x7F, 0x80
BLOCK = bytes(640)


def picture(
    size: tuple[int, int] = (320, 180), color: tuple[int, int, int] = (90, 120, 60)
) -> Image.Image:
    return Image.new("RGB", size, color)


# --- encoding --------------------------------------------------------------------------------


def test_a_frame_is_png_at_most_the_long_edge() -> None:
    frame = screen.encode(picture((3840, 2160)), DXCAM)
    assert (frame.width, frame.height) == (1568, 882) and frame.source == DXCAM
    with Image.open(io.BytesIO(frame.png)) as image:
        assert image.format == "PNG" and image.size == (1568, 882)
    assert screen.encode(picture(), SCREENSHOT).width == 320


def test_a_black_frame_is_told_apart() -> None:
    assert screen.is_black(picture(color=(0, 0, 0)))
    assert screen.is_black(picture(color=(6, 6, 6)))
    assert not screen.is_black(picture(color=(0, 0, 120)))


# --- the F2 fallback -------------------------------------------------------------------------


def shot(folder: Path, name: str, at: float, color: tuple[int, int, int]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    picture(color=color).save(path, format="PNG")
    os.utime(path, (at, at))
    return path


def test_the_newest_screenshot_from_the_last_minute_is_the_frame(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "screenshots"
    shot(folder, "2026-11-13_10.00.00.png", 1000.0, (255, 0, 0))
    shot(folder, "2026-11-13_10.00.30.png", 1030.0, (0, 255, 0))
    (folder / "notes.txt").write_text("not a screenshot", encoding="utf-8")
    os.utime(folder / "notes.txt", (1040.0, 1040.0))
    read: list[Path] = []

    def reader(path: Path) -> bytes:
        read.append(path)
        return path.read_bytes()

    got = Screenshots(tmp_path, clock=lambda: 1050.0, read=reader).grab()
    assert got.source == SCREENSHOT
    with Image.open(io.BytesIO(got.png)) as image:
        assert image.getpixel((5, 5)) == (0, 255, 0)
    assert [p.name for p in read] == [
        "2026-11-13_10.00.30.png"
    ]  # read once, the newest


def test_an_old_or_missing_screenshot_says_to_press_f2(tmp_path: Path) -> None:
    with pytest.raises(CaptureError, match="press F2"):
        Screenshots(tmp_path, clock=lambda: 0.0).grab()  # no folder yet
    shot(tmp_path / "screenshots", "old.png", 1000.0, (1, 2, 3))
    with pytest.raises(CaptureError, match="press F2"):
        Screenshots(tmp_path, clock=lambda: 1000.0 + screen.FRESH_S + 1).grab()
    with pytest.raises(CaptureError, match="game folder"):
        Screenshots(None).grab()


def test_a_screenshot_that_doesnt_read_says_so(tmp_path: Path) -> None:
    folder = tmp_path / "screenshots"
    folder.mkdir()
    (folder / "bad.png").write_bytes(b"not a png")
    with pytest.raises(CaptureError, match="doesn't read"):
        Screenshots(tmp_path, clock=lambda: (folder / "bad.png").stat().st_mtime).grab()


# --- the window on its monitor ---------------------------------------------------------------


def test_the_window_is_a_region_of_the_monitor_it_is_on() -> None:
    left = Rect(-1920, 0, 0, 1080)
    main = Rect(0, 0, 2560, 1440)
    window = Rect(100, 50, 1380, 770)
    assert screen.region_on(window, main) == (100, 50, 1380, 770)
    assert screen.region_on(window, left) is None
    on_left = Rect(-1800, 100, -200, 1000)
    assert screen.pick_output(on_left, [(0, 0, main), (0, 1, left)]) == (
        0,
        1,
        (120, 100, 1720, 1000),
    )
    spill = Rect(2400, 100, 2700, 400)  # its centre is on main: clipped to main
    assert screen.region_on(spill, main) == (2400, 100, 2560, 400)
    assert screen.pick_output(Rect(9000, 0, 9100, 100), [(0, 0, main)]) is None


def test_only_gtnh_titles_match() -> None:
    assert screen.matches("GT: New Horizons 2.9.0")
    assert screen.matches("Minecraft 1.7.10")
    assert not screen.matches("Discord")
    assert not screen.matches("New Horizons fan wiki - Chrome")


# --- dxcam, faked ----------------------------------------------------------------------------


@dataclass
class FakeWindows:
    rect: Rect | None = field(default_factory=lambda: Rect(100, 50, 1380, 770))
    asked: int = 0

    def game_window(self) -> Rect | None:
        self.asked += 1
        return self.rect


class FakeCamera:
    def __init__(self, frames: list[Any]) -> None:
        self.frames = frames
        self.grabs: list[dict[str, Any]] = []
        self.released = False

    @property
    def is_released(self) -> bool:
        return self.released

    def grab(self, **kw: Any) -> Any:  # noqa: ANN401
        self.grabs.append(kw)
        frame = self.frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return frame

    def release(self) -> None:
        self.released = True


class FakeDxcam:
    """dxcam's surface as DxcamCapture uses it: DXFactory().outputs and create()."""

    def __init__(self, cameras: list[FakeCamera]) -> None:
        self.cameras = cameras
        self.created: list[dict[str, Any]] = []
        coords = SimpleNamespace(left=0, top=0, right=2560, bottom=1440)
        output = SimpleNamespace(
            desc=SimpleNamespace(DesktopCoordinates=coords), update_desc=lambda: None
        )
        factory = SimpleNamespace(outputs=[[output]])
        self.DXFactory = lambda: factory

    def create(self, **kw: Any) -> FakeCamera:  # noqa: ANN401
        self.created.append(kw)
        return self.cameras.pop(0)


def array(color: tuple[int, int, int], size: tuple[int, int] = (1280, 720)) -> Any:  # noqa: ANN401
    import numpy as np

    return np.asarray(picture(size, color))


def test_dxcam_grabs_the_game_window_once_on_dxgi() -> None:
    camera = FakeCamera([array((90, 120, 60))])
    fake = FakeDxcam([camera])
    windows = FakeWindows()
    frame = DxcamCapture(windows, dxcam=fake).grab()
    assert frame.source == DXCAM and (frame.width, frame.height) == (1280, 720)
    assert fake.created == [
        {
            "device_idx": 0,
            "output_idx": 0,
            "output_color": "RGB",
            "backend": "dxgi",
            "processor_backend": "numpy",
        }
    ]
    assert camera.grabs == [{"region": (100, 50, 1380, 770), "new_frame_only": False}]
    assert windows.asked == 1


def test_a_lost_duplication_is_recreated_once() -> None:
    first = FakeCamera([RuntimeError("DXGI_ERROR_ACCESS_LOST")])
    second = FakeCamera([array((10, 200, 10))])
    fake = FakeDxcam([first, second])
    frame = DxcamCapture(FakeWindows(), dxcam=fake).grab()
    assert first.released and len(fake.created) == 2 and frame.width == 1280


def test_dxcam_failures_are_capture_errors() -> None:
    with pytest.raises(CaptureError, match="black"):
        DxcamCapture(
            FakeWindows(), dxcam=FakeDxcam([FakeCamera([array((0, 0, 0))])])
        ).grab()
    with pytest.raises(CaptureError, match="in front"):
        DxcamCapture(FakeWindows(None), dxcam=FakeDxcam([])).grab()
    none_twice = FakeDxcam([FakeCamera([None]), FakeCamera([None])])
    with pytest.raises(CaptureError, match="no frame"):
        DxcamCapture(FakeWindows(), dxcam=none_twice).grab()
    off_screen = FakeWindows(Rect(9000, 0, 9500, 300))
    with pytest.raises(CaptureError, match="display"):
        DxcamCapture(off_screen, dxcam=FakeDxcam([])).grab()


class Fixed:
    def __init__(self, frame: Frame | None = None, error: str | None = None) -> None:
        self.frame, self.error, self.grabs = frame, error, 0

    def grab(self) -> Frame:
        self.grabs += 1
        if self.error is not None:
            raise CaptureError(self.error)
        assert self.frame is not None
        return self.frame


FRAME = screen.encode(picture(), DXCAM)
SHOT = screen.encode(picture(color=(1, 99, 1)), SCREENSHOT)


def test_auto_falls_back_to_the_screenshot_and_says_both_reasons() -> None:
    first, fallback = Fixed(FRAME), Fixed(SHOT)
    assert AutoCapture(first, fallback).grab() is FRAME and fallback.grabs == 0
    black = Fixed(error="the frame was black (exclusive fullscreen?)")
    assert AutoCapture(black, fallback).grab() is SHOT
    with pytest.raises(CaptureError) as both:
        AutoCapture(black, Fixed(error="press F2, then the screen key")).grab()
    assert str(both.value) == (
        "the frame was black (exclusive fullscreen?), and press F2, then the screen key"
    )


def test_off_windows_or_screenshot_mode_is_the_fallback_only(tmp_path: Path) -> None:
    assert isinstance(screen.for_settings(SCREENSHOT, tmp_path), Screenshots)
    if os.name != "nt":
        assert isinstance(screen.for_settings("auto", tmp_path), Screenshots)


def test_win32_needs_windows() -> None:
    if os.name != "nt":
        with pytest.raises(CaptureError, match="Windows"):
            screen.Win32()


# --- the settings ----------------------------------------------------------------------------


def test_the_screen_key_is_f17_or_off() -> None:
    assert parse({}).screen_key is not None and parse({}).screen_key.label == "F17"
    assert parse({"screen_key": ""}).screen_key is None
    assert "screen" not in parse({"screen_key": ""}).hotkeys()
    assert parse({"screen_key": "F20"}).hotkeys()["screen"].label == "F20"
    # unset, F17 already taken: off, so a gos.toml written before the screen key still loads
    assert parse({"talk_key": "F17"}).screen_key is None
    with pytest.raises(ValueError, match="screen are both F17"):
        parse({"talk_key": "F17", "screen_key": "F17"})
    with pytest.raises(ValueError, match="screen_capture"):
        parse({"screen_capture": "continuous"})
    assert parse({"screen_capture": "screenshot"}).screen_capture == "screenshot"


# --- the screen key in the controller ---------------------------------------------------------


@dataclass
class ScreenCore:
    """FakeCore's surface plus ``screen``."""

    reply: dict[str, Any] = field(
        default_factory=lambda: {
            "text": "Steel Ingot: the first recipe runs in an Electric Blast Furnace.",
            "lane": "screen",
            "intent": "screen",
            "checked": True,
        }
    )
    fail: CoreError | None = None
    frames: list[Frame] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    uis: list[str] = field(default_factory=list)
    states: list[tuple[str, str | None]] = field(default_factory=list)
    hold: threading.Event | None = None

    def ask(self, text: str) -> dict[str, Any]:
        self.asked.append(text)
        if self.hold is not None:
            self.hold.wait(5)
        return {"text": f"You said {text}.", "lane": "fast"}

    def screen(self, frame: Frame) -> dict[str, Any]:
        self.frames.append(frame)
        if self.fail is not None:
            raise self.fail
        return self.reply

    def prewarm(self) -> None:
        pass

    def ui(self, action: str) -> None:
        self.uis.append(action)

    def state(self, state: str, trigger: str | None) -> None:
        self.states.append((state, trigger))


@pytest.fixture
def score() -> ScreenCore:
    return ScreenCore()


def make_voice(
    core: ScreenCore,
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
    capture: screen.Capture | None,
    **kw: Any,  # noqa: ANN401
) -> VoiceClient:
    return VoiceClient(
        VoiceSettings(**kw),
        core,
        transcriber=transcriber,
        speaker=speaker,
        clock=clock,
        screen=capture,
    )


def press(voice: VoiceClient, clock: Any, vk: int, down: bool = True) -> None:  # noqa: ANN401
    voice.handle(KeyEvent(vk, down, clock()))


def test_one_press_is_one_frame_and_its_answer_is_said(
    score: ScreenCore,
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> None:
    capture = Fixed(FRAME)
    voice = make_voice(score, speaker, transcriber, clock, capture)
    assert F17 in voice.watched
    press(voice, clock, F17)
    press(voice, clock, F17)  # the key's auto-repeat: no second frame
    press(voice, clock, F17)
    press(voice, clock, F17, down=False)
    run_events(voice, lambda: bool(speaker.said))
    assert capture.grabs == 1 and score.frames == [FRAME]
    assert speaker.said == [score.reply["text"]]
    press(voice, clock, F17)  # a new press: a new frame
    run_events(voice, lambda: len(speaker.said) == 2)
    assert capture.grabs == 2


def test_nothing_captures_without_the_screen_key(
    score: ScreenCore,
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> None:
    """Talk-key turns, a wake, every other hotkey, pushes, reconnects and an hour of ticks:
    the capture is never called, and the core never gets a frame."""
    capture = Fixed(FRAME)
    voice = make_voice(score, speaker, transcriber, clock, capture)
    for _ in range(3):
        press(voice, clock, F13)
        for _ in range(50):
            voice.recorder.feed(Block(BLOCK, clock.advance(0.02)))
        press(voice, clock, F13, down=False)
        for _ in range(10):
            voice.recorder.feed(Block(BLOCK, clock.advance(0.02)))
    run_events(voice, lambda: len(speaker.said) >= 1)
    voice.wake(clock())
    voice.end_of_speech(clock.advance(1.0))
    for vk in (F14, F15, F16):
        press(voice, clock, vk)
        press(voice, clock, vk, down=False)
    voice.post(Pushed({"type": "deep.task", "data": {"id": "h-1", "status": "done"}}))
    voice.post(Online(False, clock()))
    voice.post(Online(True, clock()))
    voice.post(Answered(voice._turn or _stub_turn(), {"text": "late"}))
    for _ in range(36_000):  # an hour of 100 ms ticks
        voice.tick(clock.advance(0.1))
    voice.drain()
    assert capture.grabs == 0 and score.frames == []
    assert score.asked  # the talk key turns did reach the core


def _stub_turn() -> Any:  # noqa: ANN401
    from gregtec_voice.client import Turn

    return Turn(999, "key", 0.0)


def test_the_screen_key_supersedes_a_question_in_flight(
    score: ScreenCore,
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> None:
    score.hold = threading.Event()
    voice = make_voice(score, speaker, transcriber, clock, Fixed(FRAME))
    press(voice, clock, F13)
    for _ in range(50):
        voice.recorder.feed(Block(BLOCK, clock.advance(0.02)))
    press(voice, clock, F13, down=False)
    for _ in range(10):
        voice.recorder.feed(Block(BLOCK, clock.advance(0.02)))
    run_events(voice, lambda: bool(score.asked))
    press(voice, clock, F17)
    run_events(voice, lambda: bool(speaker.said))
    score.hold.set()
    run_events(voice, lambda: voice._turn is None)
    voice.drain()
    assert speaker.said == [score.reply["text"]]  # the older answer isn't said


def test_a_frame_that_cant_be_had_says_why(
    score: ScreenCore,
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> None:
    capture = Fixed(
        error="there's no screenshot from the last minute: press F2, then the screen key"
    )
    voice = make_voice(score, speaker, transcriber, clock, capture)
    press(voice, clock, F17)
    run_events(voice, lambda: bool(speaker.said))
    assert speaker.said == [NO_FRAME.format(why=capture.error)]
    assert score.frames == []


def test_with_no_capture_the_key_says_so(
    score: ScreenCore,
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
) -> None:
    voice = make_voice(score, speaker, transcriber, clock, None)
    press(voice, clock, F17)
    assert speaker.said == [SCREEN_OFF]


def test_a_core_that_doesnt_answer_is_said_as_for_a_question(
    score: ScreenCore,
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
    run_events: Callable[[VoiceClient, Callable[[], bool]], None],
) -> None:
    score.fail = CoreError("POST /api/screen: 500", offline=False)
    voice = make_voice(score, speaker, transcriber, clock, Fixed(FRAME))
    press(voice, clock, F17)
    run_events(voice, lambda: bool(speaker.said))
    assert speaker.said == ["The core couldn't answer that."]


def test_with_the_screen_key_off_it_isnt_watched(
    score: ScreenCore,
    speaker: Any,  # noqa: ANN401
    transcriber: Any,  # noqa: ANN401
    clock: Any,  # noqa: ANN401
) -> None:
    voice = make_voice(
        score, speaker, transcriber, clock, Fixed(FRAME), screen_key=None
    )
    assert F17 not in voice.watched


# --- the frame to a real core ----------------------------------------------------------------


def test_the_frame_reaches_a_real_core_over_loopback(live_core: Any) -> None:  # noqa: ANN401
    """POST /api/screen with the PNG body: the core keeps it, and with no key stored says
    screen reading needs one (no model is called)."""
    client = CoreClient(
        live_core.port, live_core.client_token(), on_push=print, on_online=print
    )
    reply = client.screen(FRAME)
    assert reply["lane"] == "no_ai" and "no Anthropic key" in reply["text"]
    assert reply["card"]["tool"] == "screen_read"
    kept = list(
        (live_core.core.cfg.gos_home / "data" / "cache" / "screens").glob("*.png")
    )
    assert len(kept) == 1
    with pytest.raises(CoreError) as refused:
        CoreClient(live_core.port, "wrong" * 9, on_push=print, on_online=print).screen(
            FRAME
        )
    assert refused.value.refused
    bad = Frame(b"junk", DXCAM, 1, 1)
    with pytest.raises(CoreError) as junk:
        client.screen(bad)
    assert not junk.value.offline and "400" in str(junk.value)
