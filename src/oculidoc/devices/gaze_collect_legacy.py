"""Compatibility adapter for a locally installed GazeCollect/HPF system."""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic_ns, sleep

from oculidoc.devices.contracts import (
    DeviceInfo,
    DeviceKind,
    DeviceState,
    DeviceTimestamp,
    EyeTrackerSample,
)
from oculidoc.devices.errors import DeviceConnectionError, DeviceReadError, InvalidDeviceStateError


def _is_windows() -> bool:
    return os.name == "nt"


class GazeCollectLegacyDevice:
    """Read new gaze samples written by HPFMediaPlayer into GazeCollect JSON files.

    This adapter deliberately does not load the copied Tobii DLLs. HPFMediaPlayer owns
    the Tobii connection; OculiDoC only consumes its output, avoiding two processes
    subscribing to the same consumer eye tracker at once.
    """

    def __init__(
        self,
        *,
        json_root: str | Path = r"D:\GazeCollect\HPFData\json",
        screen_width_px: int = 1920,
        screen_height_px: int = 1080,
        player_executable: str | Path | None = None,
        poll_seconds: float = 0.01,
    ) -> None:
        self.json_root = Path(json_root).expanduser().resolve()
        self.screen_width_px = int(screen_width_px)
        self.screen_height_px = int(screen_height_px)
        self.player_executable = (
            Path(player_executable).expanduser().resolve() if player_executable else None
        )
        self.poll_seconds = float(poll_seconds)
        if self.screen_width_px <= 0 or self.screen_height_px <= 0:
            raise ValueError("Screen dimensions must be positive.")
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive.")

        self._state = DeviceState.DISCONNECTED
        self._sequence = 0
        self._file: Path | None = None
        self._consumed = 0
        self._player: subprocess.Popen[bytes] | None = None
        self._info = DeviceInfo(
            device_id="gaze-collect-legacy-file-bridge",
            kind=DeviceKind.EYE_TRACKER,
            name="GazeCollect / HPF 兼容模式",
            manufacturer="Tobii / HPF legacy system",
            model="HPFMediaPlayer JSON bridge",
            is_simulated=False,
            capabilities=("screen_pixel_gaze", "json_file_bridge", "legacy_hpf"),
        )

    @property
    def info(self) -> DeviceInfo:
        return self._info

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def device_url(self) -> str:
        return self.json_root.as_uri()

    def connect(self) -> None:
        if self._state is not DeviceState.DISCONNECTED:
            raise InvalidDeviceStateError("Only a disconnected GazeCollect bridge can connect.")
        if not _is_windows():
            raise DeviceConnectionError("GazeCollect compatibility mode requires Windows.")
        if not self.json_root.is_dir():
            raise DeviceConnectionError(
                f"GazeCollect JSON directory does not exist: {self.json_root}"
            )
        if self.player_executable is not None and not self.player_executable.is_file():
            raise DeviceConnectionError(f"HPFMediaPlayer does not exist: {self.player_executable}")
        self._state = DeviceState.CONNECTED

    def disconnect(self) -> None:
        if self._state is DeviceState.STREAMING:
            self.stop_stream()
        self._state = DeviceState.DISCONNECTED

    def _newest_gaze_file(self) -> Path | None:
        candidates = (path for path in self.json_root.rglob("*_gaze.json") if path.is_file())
        return max(candidates, key=lambda path: path.stat().st_mtime_ns, default=None)

    def start_stream(self) -> None:
        if self._state is not DeviceState.CONNECTED:
            raise InvalidDeviceStateError("Connect the GazeCollect bridge before streaming.")
        if self.player_executable is not None:
            self._player = subprocess.Popen(
                [str(self.player_executable)],
                cwd=str(self.player_executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self._sequence = 0
        self._file = self._newest_gaze_file()
        self._consumed = self._record_count(self._file) if self._file else 0
        self._state = DeviceState.STREAMING

    @staticmethod
    def _record_count(path: Path | None) -> int:
        if path is None:
            return 0
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return 0
        return len(value) if isinstance(value, list) else 0

    def stop_stream(self) -> None:
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The GazeCollect bridge is not streaming.")
        self._file = None
        self._consumed = 0
        self._state = DeviceState.CONNECTED
        # Do not terminate an existing hospital process. Only stop a process this adapter launched.
        if self._player is not None and self._player.poll() is None:
            self._player.terminate()
            with suppress(subprocess.TimeoutExpired):
                self._player.wait(timeout=2.0)
        self._player = None

    def interrupt(self) -> None:
        return

    def _next_payload(self) -> dict[str, object]:
        if self._state is not DeviceState.STREAMING:
            raise InvalidDeviceStateError("The GazeCollect bridge is not streaming.")
        newest = self._newest_gaze_file()
        if newest is None:
            sleep(self.poll_seconds)
            raise TimeoutError("No GazeCollect gaze JSON file is available.")
        if newest != self._file:
            self._file = newest
            self._consumed = 0
        try:
            records = json.loads(newest.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            sleep(self.poll_seconds)
            raise TimeoutError("GazeCollect is still writing the gaze JSON file.") from error
        if not isinstance(records, list):
            raise DeviceReadError(f"GazeCollect gaze file must contain a JSON array: {newest}")
        if len(records) < self._consumed:
            # HPF rewrites the JSON array for a new run instead of appending forever.
            self._consumed = 0
        if self._consumed >= len(records):
            sleep(self.poll_seconds)
            raise TimeoutError("No new GazeCollect sample is available.")
        payload = records[self._consumed]
        self._consumed += 1
        if not isinstance(payload, dict):
            raise DeviceReadError("GazeCollect sample must be a JSON object.")
        return payload

    def read_sample(self) -> EyeTrackerSample:
        payload = self._next_payload()
        try:
            x_value = payload["x"]
            y_value = payload["y"]
            if isinstance(x_value, bool) or not isinstance(x_value, (int, float, str)):
                raise TypeError
            if isinstance(y_value, bool) or not isinstance(y_value, (int, float, str)):
                raise TypeError
            x_px = float(x_value)
            y_px = float(y_value)
        except (KeyError, TypeError, ValueError) as error:
            raise DeviceReadError("GazeCollect sample requires numeric x and y.") from error
        valid = payload.get("validity") in {1, "1"}
        if (
            not isfinite(x_px)
            or not isfinite(y_px)
            or not 0 <= x_px <= self.screen_width_px
            or not 0 <= y_px <= self.screen_height_px
        ):
            valid = False
        source_timestamp = payload.get("timestamp_us")
        # Observed HPF files use .NET ticks despite the historical timestamp_us name.
        try:
            if isinstance(source_timestamp, bool) or (
                source_timestamp is not None
                and not isinstance(source_timestamp, (int, float, str))
            ):
                raise TypeError
            source_timestamp_ns = (
                int(source_timestamp) * 100 if source_timestamp is not None else None
            )
        except (TypeError, ValueError) as error:
            raise DeviceReadError("GazeCollect timestamp_us must be an integer.") from error
        sample = EyeTrackerSample(
            timestamp=DeviceTimestamp(
                sequence=self._sequence,
                monotonic_timestamp_ns=monotonic_ns(),
                utc_timestamp=datetime.now(UTC),
                source_timestamp_ns=source_timestamp_ns,
                source_clock_id="gaze-collect-hpf-dotnet-ticks",
            ),
            gaze_x_normalized=x_px / self.screen_width_px if valid else None,
            gaze_y_normalized=y_px / self.screen_height_px if valid else None,
            left_eye_valid=valid,
            right_eye_valid=valid,
        )
        self._sequence += 1
        return sample
