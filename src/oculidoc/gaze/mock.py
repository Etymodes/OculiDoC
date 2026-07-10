"""Deterministic simulated gaze source for development and tests."""

import math
import time

from oculidoc.gaze.models import GazeSample


class MockGazeSource:
    name = "mock"

    def __init__(self, screen_width: int = 1920, screen_height: int = 1080) -> None:
        if screen_width <= 0 or screen_height <= 0:
            raise ValueError("Screen dimensions must be positive.")

        self._screen_width = screen_width
        self._screen_height = screen_height
        self._phase = 0.0
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._phase = 0.0
        self._running = True

    def stop(self) -> None:
        self._running = False

    def read(self) -> GazeSample | None:
        if not self._running:
            return None

        self._phase = (self._phase + 0.12) % math.tau
        x_px = self._screen_width * (0.5 + 0.35 * math.cos(self._phase))
        y_px = self._screen_height * (0.5 + 0.30 * math.sin(self._phase * 1.5))

        return GazeSample(
            timestamp_ns=time.monotonic_ns(),
            x_px=x_px,
            y_px=y_px,
            valid=True,
            source=self.name,
        )
