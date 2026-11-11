"""Audio devices for the wizard's headset step (WO-2.9, D168): a pluggable provider.

The voice client owns audio (WO-5.1 captures through WASAPI; WO-5.3's TTS will play through it),
and will enumerate devices itself later; until then the core asks a :class:`DeviceProvider`:

- :class:`WasapiDevices` (Windows): PortAudio's WASAPI host API through ``sounddevice`` (the voice
  dependency group's Windows-only package, D179), the capture and the playback devices, each
  with Windows' default marked. PortAudio lists devices once, when it starts, so it is started
  again first (no stream is open in the core), and a headset plugged in since is found.
- :class:`FakeDevices` everywhere else (Linux CI and dev, the e2e tests): a fixed list that says
  it is fake.

A device is ``{"name", "default"}``; gos.toml keeps the name (``[voice] input_device``,
``output_endpoint``), which the voice client matches as a part of a device name.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Device:
    name: str
    default: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "default": self.default}


class DeviceProvider(Protocol):
    name: str

    def inputs(self) -> list[Device]: ...

    def outputs(self) -> list[Device]: ...


class DeviceError(RuntimeError):
    """The devices can't be listed now (the message says why)."""


class FakeDevices:
    """A fixed list of devices (not Windows, or tests), shaped like the Arctis Nova Elite's
    endpoints (D56: TTS on its Chat endpoint)."""

    name = "fake"

    def __init__(
        self,
        inputs: Sequence[Device] | None = None,
        outputs: Sequence[Device] | None = None,
    ) -> None:
        self._inputs = list(
            inputs
            if inputs is not None
            else [
                Device("Microphone (Arctis Nova Elite)", default=True),
                Device("Microphone (Webcam)"),
            ]
        )
        self._outputs = list(
            outputs
            if outputs is not None
            else [
                Device("Headphones (Arctis Nova Elite Game)", default=True),
                Device("Headphones (Arctis Nova Elite Chat)"),
                Device("Speakers (Realtek Audio)"),
            ]
        )

    def inputs(self) -> list[Device]:
        return list(self._inputs)

    def outputs(self) -> list[Device]:
        return list(self._outputs)


class WasapiDevices:
    """Windows: WASAPI's capture and playback devices through PortAudio (``sounddevice``)."""

    name = "wasapi"

    def _list(self, channels: str, default_key: str) -> list[Device]:
        try:
            import sounddevice as sd  # Windows only (voice group, D179); starts PortAudio
        except Exception as exc:  # noqa: BLE001 - OSError (no PortAudio), PortAudioError
            raise DeviceError(f"PortAudio didn't start: {exc}") from None
        try:
            sd._terminate()  # a fresh device list (see the module docstring)
            sd._initialize()
            apis = list(sd.query_hostapis())
            index = next(
                (i for i, a in enumerate(apis) if a["name"] == "Windows WASAPI"), None
            )
            if index is None:
                raise DeviceError("PortAudio has no WASAPI host API on this PC")
            default = apis[index][default_key]
            return [
                Device(str(d["name"]), i == default)
                for i, d in enumerate(sd.query_devices())
                if d["hostapi"] == index and d[channels] > 0
            ]
        except DeviceError:
            raise
        except Exception as exc:  # noqa: BLE001 - PortAudioError and friends
            raise DeviceError(f"PortAudio can't list devices: {exc}") from None

    def inputs(self) -> list[Device]:
        return self._list("max_input_channels", "default_input_device")

    def outputs(self) -> list[Device]:
        return self._list("max_output_channels", "default_output_device")


def default_provider() -> DeviceProvider:
    return WasapiDevices() if sys.platform == "win32" else FakeDevices()


def listing(provider: DeviceProvider) -> dict[str, Any]:
    """``{"provider", "inputs", "outputs", "error"}``: what the wizard's device step shows."""
    try:
        inputs, outputs = provider.inputs(), provider.outputs()
    except DeviceError as exc:
        return {
            "provider": provider.name,
            "inputs": [],
            "outputs": [],
            "error": str(exc),
        }
    return {
        "provider": provider.name,
        "inputs": [d.as_dict() for d in inputs],
        "outputs": [d.as_dict() for d in outputs],
        "error": None,
    }
