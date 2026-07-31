"""Coordinate conversion between a display and one task widget."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Self

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QWidget

from oculidoc.devices.contracts import EyeTrackerSample
from oculidoc.experiments.recording import NormalizedAoi


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True, slots=True)
class TaskGazeCoordinateTransform:
    """Map normalized display gaze to a task viewport and back."""

    screen_left_px: float
    screen_top_px: float
    screen_width_px: float
    screen_height_px: float
    task_left_px: float
    task_top_px: float
    task_width_px: float
    task_height_px: float

    def __post_init__(self) -> None:
        values = (
            self.screen_left_px,
            self.screen_top_px,
            self.screen_width_px,
            self.screen_height_px,
            self.task_left_px,
            self.task_top_px,
            self.task_width_px,
            self.task_height_px,
        )
        if any(not isfinite(value) for value in values):
            raise ValueError("Coordinate geometry must contain finite values.")
        if self.screen_width_px <= 0 or self.screen_height_px <= 0:
            raise ValueError("Screen dimensions must be positive.")
        if self.task_width_px <= 0 or self.task_height_px <= 0:
            raise ValueError("Task dimensions must be positive.")

    @classmethod
    def from_widget(cls, widget: QWidget) -> Self | None:
        """Read the current logical-pixel geometry without caching it."""
        if widget.width() <= 0 or widget.height() <= 0:
            return None

        screen = widget.screen()
        application = QApplication.instance()

        if screen is None and application is not None:
            screen = application.primaryScreen()
        if screen is None:
            return None

        screen_geometry = screen.geometry()
        if screen_geometry.width() <= 0 or screen_geometry.height() <= 0:
            return None

        task_origin = widget.mapToGlobal(QPoint(0, 0))
        return cls(
            screen_left_px=float(screen_geometry.x()),
            screen_top_px=float(screen_geometry.y()),
            screen_width_px=float(screen_geometry.width()),
            screen_height_px=float(screen_geometry.height()),
            task_left_px=float(task_origin.x()),
            task_top_px=float(task_origin.y()),
            task_width_px=float(widget.width()),
            task_height_px=float(widget.height()),
        )

    def task_point_to_screen(self, x: float, y: float) -> tuple[float, float]:
        """Return one task-local normalized point in display coordinates."""
        screen_x = (
            self.task_left_px + float(x) * self.task_width_px - self.screen_left_px
        ) / self.screen_width_px
        screen_y = (
            self.task_top_px + float(y) * self.task_height_px - self.screen_top_px
        ) / self.screen_height_px
        return screen_x, screen_y

    def sample_to_task(self, sample: EyeTrackerSample) -> EyeTrackerSample:
        """Return a task-local copy while retaining the raw sample elsewhere."""
        if (
            not sample.gaze_valid
            or sample.gaze_x_normalized is None
            or sample.gaze_y_normalized is None
        ):
            if sample.gaze_x_normalized is None and sample.gaze_y_normalized is None:
                return sample
            return replace(
                sample,
                gaze_x_normalized=None,
                gaze_y_normalized=None,
            )

        global_x = self.screen_left_px + float(sample.gaze_x_normalized) * self.screen_width_px
        global_y = self.screen_top_px + float(sample.gaze_y_normalized) * self.screen_height_px
        task_x = (global_x - self.task_left_px) / self.task_width_px
        task_y = (global_y - self.task_top_px) / self.task_height_px

        if not 0.0 <= task_x <= 1.0 or not 0.0 <= task_y <= 1.0:
            return replace(
                sample,
                gaze_x_normalized=None,
                gaze_y_normalized=None,
            )

        return replace(
            sample,
            gaze_x_normalized=task_x,
            gaze_y_normalized=task_y,
        )

    def aoi_to_screen(self, aoi: NormalizedAoi) -> NormalizedAoi | None:
        """Convert a task-local AOI to normalized display coordinates."""
        left, top = self.task_point_to_screen(aoi.left, aoi.top)
        right, bottom = self.task_point_to_screen(aoi.right, aoi.bottom)
        left = _clamp_unit(left)
        top = _clamp_unit(top)
        right = _clamp_unit(right)
        bottom = _clamp_unit(bottom)

        if right <= left or bottom <= top:
            return None

        return replace(
            aoi,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )
