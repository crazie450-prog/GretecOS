"""The screen key's one frame (plan 7.7, WO-8.2, D288; [H§3]).

**One frame per press, never more.** The voice client calls :meth:`Capture.grab` once when the
screen key goes down (the hook's auto-repeat and the release do nothing, :class:`ptt.Hotkeys`);
nothing here runs on a timer, keeps a capture session going or captures on any other event.

- :class:`DxcamCapture` (Windows): the client area of the foreground window when it is the
  game's: its title is GTNH's (:data:`TITLES`) and its window class is LWJGL's or GLFW's
  (:data:`CLASSES`), so an Explorer window or a browser tab titled like the pack is never
  captured. It is grabbed once with dxcam's DXGI backend (``grab(region=<the
  window on its monitor>, new_frame_only=False)``) [H§3]. Only the game window is captured,
  never the desktop, so Discord DMs and other windows on any monitor never leave the PC. A grab
  that gives no frame (DXGI had no new frame yet and dxcam no cached one) is tried again after a
  short wait with the same camera; a grab that raises (``DXGI_ERROR_ACCESS_LOST`` after a mode
  switch) recreates the camera once. A frame that is all black (exclusive fullscreen, V13) is
  refused, so the fallback runs.
- :class:`Screenshots` (any OS): the fallback, the newest ``.png`` in Play's or Lab's
  ``screenshots/`` (read only) written in the last :data:`FRESH_S` seconds: Clayton presses F2
  (the game's own screenshot key) and then the screen key. GOS never presses F2 itself: that would make the
  game write into Play (D288). The file is read once, share-delete, and let go
  (:func:`gregtec.gamestate.safe_open.read_bytes`).
- :class:`AutoCapture`: dxcam first, then the fallback; ``[voice] screen_capture =
  "screenshot"`` uses only the fallback.

Frames are scaled to at most :data:`LONG_EDGE` pixels on the long edge and sent as PNG.
"""

from __future__ import annotations

import ctypes
import io
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from gregtec.gamestate.safe_open import read_bytes

log = logging.getLogger(__name__)

DXCAM, SCREENSHOT = "dxcam", "screenshot"  # the core's ``source`` values
FRESH_S = 60.0  # an F2 screenshot this recent is the frame
LONG_EDGE = 1568  # as the core's cache: Claude's vision reads no more
BLACK_MAX = 8  # a frame whose brightest pixel is this dark is black (V13)
# GTNH's window titles: "GT: New Horizons ..." (the pack's own title), else Minecraft's.
TITLES = ("GT: New Horizons", "GT New Horizons", "Minecraft 1.7.10")
# The game window's class: LWJGL 2's on the Java 8 build, GLFW's on lwjgl3ify (Java 17+).
# No other program on the PC makes a window of these (GTNH is its only Java program, D161).
CLASSES = ("LWJGL", "GLFW30")
GRAB_TRIES = 3  # grabs that give no frame before dxcam is given up
GRAB_WAIT_S = 0.05  # between them: DXGI has a frame when the screen next updates


class CaptureError(Exception):
    """No frame: the message says why, in words GOS can say."""


@dataclass(frozen=True)
class Frame:
    png: bytes
    source: str  # DXCAM or SCREENSHOT
    width: int
    height: int


class Capture(Protocol):
    def grab(self) -> Frame:
        """One frame now. Raises :class:`CaptureError`."""
        ...


def encode(image: Image.Image, source: str) -> Frame:
    """``image`` as a :class:`Frame`: RGB, at most :data:`LONG_EDGE` on its long edge, PNG."""
    rgb = image.convert("RGB")
    scale = LONG_EDGE / max(rgb.size)
    if scale < 1:
        size = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
        rgb = rgb.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    rgb.save(out, format="PNG")
    return Frame(out.getvalue(), source, rgb.width, rgb.height)


def is_black(image: Image.Image) -> bool:
    _, high = image.convert("L").getextrema()
    return int(high) <= BLACK_MAX


# --- the fallback: the newest F2 screenshot ---------------------------------------------------


class Screenshots:
    """The newest ``screenshots/*.png`` of the game folders (Play's and Lab's, read only)
    written in the last :data:`FRESH_S` seconds."""

    def __init__(
        self,
        games: Sequence[Path | None],
        *,
        clock: Callable[[], float] = time.time,
        read: Callable[[Path], bytes] = read_bytes,
    ) -> None:
        self.games = [game for game in games if game is not None]
        self.clock = clock
        self.read = read

    def grab(self) -> Frame:
        if not self.games:
            raise CaptureError("the game folder isn't set up")
        newest: tuple[float, Path] | None = None
        for game in self.games:
            try:
                paths = list((game / "screenshots").iterdir())
            except OSError:
                continue  # no screenshots yet
            for path in paths:
                if path.suffix.lower() != ".png":
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if newest is None or mtime > newest[0]:
                    newest = (mtime, path)
        if newest is None or self.clock() - newest[0] > FRESH_S:
            raise CaptureError(
                "there's no screenshot from the last minute: press F2, then the screen key"
            )
        try:
            data = self.read(newest[1])
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                return encode(image, SCREENSHOT)
        except (OSError, ValueError) as exc:
            raise CaptureError(
                f"the newest screenshot doesn't read ({type(exc).__name__})"
            ) from None


# --- dxcam (Windows) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


def region_on(window: Rect, output: Rect) -> tuple[int, int, int, int] | None:
    """``window`` (desktop coordinates) as a region of ``output``, clipped to it; None when
    the window's centre isn't on that output or nothing of it is left."""
    cx, cy = (window.left + window.right) // 2, (window.top + window.bottom) // 2
    if not output.contains(cx, cy):
        return None
    left = max(window.left, output.left) - output.left
    top = max(window.top, output.top) - output.top
    right = min(window.right, output.right) - output.left
    bottom = min(window.bottom, output.bottom) - output.top
    if right - left < 2 or bottom - top < 2:
        return None
    return left, top, right, bottom


def pick_output(
    window: Rect, outputs: Sequence[tuple[int, int, Rect]]
) -> tuple[int, int, tuple[int, int, int, int]] | None:
    """(device, output, region) for the output that shows the window's centre."""
    for device, output, rect in outputs:
        region = region_on(window, rect)
        if region is not None:
            return device, output, region
    return None


def matches(title: str, titles: Sequence[str] = TITLES) -> bool:
    return any(title.startswith(t) for t in titles)


def is_game(title: str, window_class: str, titles: Sequence[str] = TITLES) -> bool:
    """The game's window: a GTNH title and LWJGL's (or GLFW's) window class. The title alone
    isn't enough: Explorer on the instance folder or a browser tab can carry it."""
    return matches(title, titles) and window_class in CLASSES


class Windows(Protocol):
    """The Win32 calls :class:`DxcamCapture` needs (a fake in the tests)."""

    def game_window(self) -> Rect | None:
        """The game window's client area in desktop coordinates, or None."""
        ...


@dataclass(frozen=True)
class Foreground:
    """What Windows says about the foreground window: its title, its window class and its
    client area in desktop coordinates (None when it doesn't read)."""

    title: str
    window_class: str
    client: Rect | None


class Win32:
    """The game window's client rectangle, through user32 (Windows only, read only): the window
    in the foreground, when it is the game's (:func:`is_game`). Clayton presses the key while
    playing, so the game is in front; GOS never looks for or at any other window, and never
    opens another process (the D247 audit)."""

    def __init__(
        self,
        titles: Sequence[str] = TITLES,
        *,
        read: Callable[[], Foreground | None] | None = None,  # a fake in the tests
    ) -> None:
        if read is None and sys.platform != "win32":
            raise CaptureError("screen capture needs Windows")
        self.titles = tuple(titles)
        self.read = read or read_foreground

    def game_window(self) -> Rect | None:
        window = self.read()
        if window is None or window.client is None:
            return None
        if not is_game(window.title, window.window_class, self.titles):
            return None
        rect = window.client
        return rect if rect.width > 1 and rect.height > 1 else None


def read_foreground() -> Foreground | None:
    """The foreground window as :class:`Foreground`, or None when there is none (Windows
    only). Every call is a read: its title, its class and its client rectangle."""
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    user32.GetForegroundWindow.restype = wintypes.HWND
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    n = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, title, n + 1)
    window_class = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, window_class, 256)
    client = None
    rect = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if user32.GetClientRect(hwnd, ctypes.byref(rect)) and user32.ClientToScreen(
        hwnd, ctypes.byref(origin)
    ):
        client = Rect(origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)
    return Foreground(title.value, window_class.value, client)


class DxcamCapture:
    """One DXGI grab of the game window (see the module docstring)."""

    def __init__(
        self,
        windows: Windows | None = None,
        *,
        dxcam: Any = None,  # noqa: ANN401 - the dxcam module (a fake in the tests)
    ) -> None:
        self._windows = windows
        self._dxcam = dxcam
        self._cameras: dict[tuple[int, int], Any] = {}
        self._factory: Any = None
        self.sleep: Callable[[float], None] = time.sleep

    def _module(self) -> Any:  # noqa: ANN401
        if self._dxcam is None:
            try:
                import dxcam  # type: ignore[import-not-found]
            except ImportError:
                raise CaptureError("dxcam isn't installed") from None
            self._dxcam = dxcam
        return self._dxcam

    def _outputs(self, dxcam: Any) -> list[tuple[int, int, Rect]]:  # noqa: ANN401
        if self._factory is None:
            # dxcam builds its one factory on import, and each later DXFactory() call logs a
            # warning: take the module's, else call it once
            self._factory = getattr(dxcam, "__factory", None) or dxcam.DXFactory()
        out = []
        for d, outputs in enumerate(self._factory.outputs):
            for o, output in enumerate(outputs):
                output.update_desc()
                c = output.desc.DesktopCoordinates
                out.append((d, o, Rect(c.left, c.top, c.right, c.bottom)))
        return out

    def _camera(self, dxcam: Any, device: int, output: int) -> Any:  # noqa: ANN401
        key = (device, output)
        camera = self._cameras.get(key)
        if camera is None or getattr(camera, "is_released", False):
            camera = dxcam.create(
                device_idx=device,
                output_idx=output,
                output_color="RGB",
                backend="dxgi",
                processor_backend="numpy",
            )
            self._cameras[key] = camera
        return camera

    def _drop(self, key: tuple[int, int]) -> None:
        camera = self._cameras.pop(key, None)
        if camera is not None:
            try:
                camera.release()
            except (
                Exception
            ):  # a lost device can fail to release; it is gone either way
                log.debug("dxcam camera not released", exc_info=True)

    def grab(self) -> Frame:
        if self._windows is None:
            self._windows = Win32()
        dxcam = self._module()
        try:
            # first: dxcam's outputs make the process per-monitor DPI aware, so the window's
            # rectangle below is in the same physical pixels as the outputs' coordinates
            outputs = self._outputs(dxcam)
        except Exception as exc:
            raise CaptureError(
                f"dxcam found no display ({type(exc).__name__})"
            ) from exc
        window = self._windows.game_window()
        if window is None:
            raise CaptureError("the game window isn't in front")
        picked = pick_output(window, outputs)
        if picked is None:
            raise CaptureError("the game window isn't on a display dxcam sees")
        device, output, region = picked
        array = None
        recreated = False
        for attempt in range(1, GRAB_TRIES + 1):
            try:
                array = self._camera(dxcam, device, output).grab(
                    region=region, new_frame_only=False
                )
            except Exception as exc:
                log.warning(
                    "dxcam grab failed",
                    extra={"error": type(exc).__name__, "try": attempt},
                    exc_info=True,
                )
                # after a mode switch the duplication is lost: recreate the camera, once
                self._drop((device, output))
                if recreated:
                    break
                recreated = True
                continue
            if array is not None:
                break
            # no new frame and no cached one (a static screen): wait for the next update
            self.sleep(GRAB_WAIT_S)
        if array is None:
            raise CaptureError("dxcam gave no frame")
        image = Image.fromarray(array[:, :, :3])
        if is_black(image):
            raise CaptureError("the frame was black (exclusive fullscreen?)")
        return encode(image, DXCAM)


class AutoCapture:
    """dxcam, then the newest F2 screenshot (D288)."""

    def __init__(self, first: Capture | None, fallback: Capture) -> None:
        self.first = first
        self.fallback = fallback

    def grab(self) -> Frame:
        why = None
        if self.first is not None:
            try:
                return self.first.grab()
            except CaptureError as exc:
                why = str(exc)
                log.info(
                    "screen capture fell back to the screenshot", extra={"why": why}
                )
        try:
            return self.fallback.grab()
        except CaptureError as exc:
            raise CaptureError(f"{why}, and {exc}" if why else str(exc)) from None


def for_settings(mode: str, games: Sequence[Path | None]) -> Capture:
    """The capture ``[voice] screen_capture`` names: ``auto`` (dxcam on Windows, then the
    screenshot) or ``screenshot``. ``games``: the game folders whose ``screenshots/`` the
    fallback reads (Play's and Lab's)."""
    shots = Screenshots(games)
    if mode == SCREENSHOT or sys.platform != "win32":
        return shots
    return AutoCapture(DxcamCapture(), shots)
