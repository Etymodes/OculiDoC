"""Configurable gaze-following target task."""

from dataclasses import dataclass
from enum import StrEnum
from math import cos, pi, sin
from pathlib import Path

from PySide6.QtCore import (
    QElapsedTimer,
    QPointF,
    QRectF,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.devices.contracts import (
    EyeTrackerSample,
)


class TargetShape(StrEnum):
    CIRCLE = "circle"
    SQUARE = "square"
    DIAMOND = "diamond"
    STAR = "star"


class TargetEffect(StrEnum):
    NONE = "none"
    PULSE = "pulse"
    SPIN = "spin"


class TargetPath(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    CIRCLE = "circle"
    FIGURE_EIGHT = "figure_eight"
    RANDOM = "random"


@dataclass(frozen=True, slots=True)
class TrackingBallConfig:
    shape: TargetShape = TargetShape.CIRCLE
    effect: TargetEffect = TargetEffect.PULSE
    path: TargetPath = TargetPath.HORIZONTAL
    diameter_px: int = 100
    color: str = "#ffcc00"
    image_path: str | None = None
    period_seconds: float = 6.0
    duration_seconds: int = 60
    background_color: str = "#071521"
    show_gaze_cursor: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "shape",
            TargetShape(self.shape),
        )
        object.__setattr__(
            self,
            "effect",
            TargetEffect(self.effect),
        )
        object.__setattr__(
            self,
            "path",
            TargetPath(self.path),
        )

        if not 16 <= self.diameter_px <= 600:
            raise ValueError("diameter_px must be between 16 and 600.")

        if not 1.0 <= self.period_seconds <= 120.0:
            raise ValueError("period_seconds must be between 1 and 120.")

        if not 5 <= self.duration_seconds <= 3_600:
            raise ValueError("duration_seconds must be between 5 and 3600.")

        if not QColor(self.color).isValid():
            raise ValueError("color must be a valid Qt color.")

        if not QColor(self.background_color).isValid():
            raise ValueError("background_color must be valid.")

        if self.image_path is not None:
            normalized = self.image_path.strip()
            object.__setattr__(
                self,
                "image_path",
                normalized or None,
            )


class TrackingBallTask(QWidget):
    """Render a configurable target and live gaze marker."""

    def __init__(
        self,
        config: TrackingBallConfig,
    ) -> None:
        super().__init__()
        self.config = config
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)

        self._elapsed = QElapsedTimer()
        self._last_gaze_normalized: tuple[float, float] | None = None
        self._valid_sample_count = 0
        self._invalid_sample_count = 0

        self._image = QPixmap()

        if config.image_path:
            image_path = Path(config.image_path).expanduser()

            if image_path.is_file():
                self._image = QPixmap(str(image_path))

    @property
    def last_gaze_normalized(
        self,
    ) -> tuple[float, float] | None:
        return self._last_gaze_normalized

    def start(self) -> None:
        self._elapsed.start()
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def consume_sample(
        self,
        sample: EyeTrackerSample,
    ) -> None:
        if not sample.gaze_valid:
            self._invalid_sample_count += 1
            self._last_gaze_normalized = None
            self.update()
            return

        gaze_x = max(
            0.0,
            min(
                1.0,
                float(sample.gaze_x_normalized),
            ),
        )
        gaze_y = max(
            0.0,
            min(
                1.0,
                float(sample.gaze_y_normalized),
            ),
        )

        self._valid_sample_count += 1
        self._last_gaze_normalized = (
            gaze_x,
            gaze_y,
        )
        self.update()

    def _phase(self) -> float:
        if not self._elapsed.isValid():
            return 0.0

        elapsed_seconds = self._elapsed.elapsed() / 1_000.0

        return elapsed_seconds / self.config.period_seconds * 2.0 * pi

    def target_center_normalized(
        self,
        phase: float,
    ) -> tuple[float, float]:
        if self.config.path is TargetPath.HORIZONTAL:
            return (
                0.5 + 0.38 * sin(phase),
                0.5,
            )

        if self.config.path is TargetPath.VERTICAL:
            return (
                0.5,
                0.5 + 0.38 * sin(phase),
            )

        if self.config.path is TargetPath.CIRCLE:
            return (
                0.5 + 0.32 * cos(phase),
                0.5 + 0.32 * sin(phase),
            )

        if self.config.path is TargetPath.RANDOM:
            return (
                0.5 + 0.33 * sin(phase * 1.37 + 0.86 * sin(phase * 0.41)),
                0.5 + 0.31 * cos(phase * 1.73 + 0.79 * cos(phase * 0.53)),
            )

        return (
            0.5 + 0.36 * sin(phase),
            0.5 + 0.24 * sin(2.0 * phase),
        )

    def _target_path(
        self,
        diameter: float,
    ) -> QPainterPath:
        radius = diameter / 2.0
        rectangle = QRectF(
            -radius,
            -radius,
            diameter,
            diameter,
        )
        path = QPainterPath()

        if self.config.shape is TargetShape.CIRCLE:
            path.addEllipse(rectangle)
            return path

        if self.config.shape is TargetShape.SQUARE:
            path.addRoundedRect(
                rectangle,
                diameter * 0.08,
                diameter * 0.08,
            )
            return path

        if self.config.shape is TargetShape.DIAMOND:
            polygon = QPolygonF(
                [
                    QPointF(0.0, -radius),
                    QPointF(radius, 0.0),
                    QPointF(0.0, radius),
                    QPointF(-radius, 0.0),
                ]
            )
            path.addPolygon(polygon)
            path.closeSubpath()
            return path

        points: list[QPointF] = []

        for index in range(10):
            angle = -pi / 2.0 + index * pi / 5.0
            point_radius = radius if index % 2 == 0 else radius * 0.44
            points.append(
                QPointF(
                    point_radius * cos(angle),
                    point_radius * sin(angle),
                )
            )

        path.addPolygon(QPolygonF(points))
        path.closeSubpath()
        return path

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )
        painter.fillRect(
            self.rect(),
            QColor(self.config.background_color),
        )

        phase = self._phase()
        normalized_x, normalized_y = self.target_center_normalized(phase)
        center = QPointF(
            normalized_x * self.width(),
            normalized_y * self.height(),
        )

        scale = 1.0

        if self.config.effect is TargetEffect.PULSE:
            scale += 0.14 * sin(phase * 2.0)

        diameter = self.config.diameter_px * max(0.65, scale)
        path = self._target_path(diameter)

        painter.save()
        painter.translate(center)

        if self.config.effect is TargetEffect.SPIN:
            painter.rotate(phase * 180.0 / pi)

        painter.setPen(
            QPen(
                QColor("#ffffff"),
                max(2.0, diameter * 0.025),
            )
        )

        if not self._image.isNull():
            painter.save()
            painter.setClipPath(path)
            target_rectangle = QRectF(
                -diameter / 2.0,
                -diameter / 2.0,
                diameter,
                diameter,
            )
            painter.drawPixmap(
                target_rectangle,
                self._image,
                QRectF(self._image.rect()),
            )
            painter.restore()
            painter.drawPath(path)
        else:
            painter.fillPath(
                path,
                QColor(self.config.color),
            )
            painter.drawPath(path)

        painter.restore()

        if self.config.show_gaze_cursor and self._last_gaze_normalized is not None:
            gaze_x, gaze_y = self._last_gaze_normalized
            gaze_point = QPointF(
                gaze_x * self.width(),
                gaze_y * self.height(),
            )
            painter.setBrush(QColor(255, 255, 255, 45))
            painter.setPen(
                QPen(
                    QColor("#40e0ff"),
                    4,
                )
            )
            painter.drawEllipse(
                gaze_point,
                18,
                18,
            )
            painter.drawLine(
                QPointF(
                    gaze_point.x() - 25,
                    gaze_point.y(),
                ),
                QPointF(
                    gaze_point.x() + 25,
                    gaze_point.y(),
                ),
            )
            painter.drawLine(
                QPointF(
                    gaze_point.x(),
                    gaze_point.y() - 25,
                ),
                QPointF(
                    gaze_point.x(),
                    gaze_point.y() + 25,
                ),
            )

        painter.setPen(QColor("#dcecff"))
        painter.drawText(
            20,
            30,
            (f"有效样本 {self._valid_sample_count} · 无效样本 {self._invalid_sample_count}"),
        )

    def mouseMoveEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return

        position = event.position()
        self._last_gaze_normalized = (
            max(
                0.0,
                min(
                    1.0,
                    position.x() / self.width(),
                ),
            ),
            max(
                0.0,
                min(
                    1.0,
                    position.y() / self.height(),
                ),
            ),
        )
        self.update()


class TrackingBallSetupDialog(QDialog):
    """Configure tracking target appearance and motion."""

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("追踪球设置")
        self.resize(480, 360)

        form = QFormLayout()

        self.shape_combo = QComboBox()
        self.shape_combo.addItem(
            "圆形",
            TargetShape.CIRCLE,
        )
        self.shape_combo.addItem(
            "方形",
            TargetShape.SQUARE,
        )
        self.shape_combo.addItem(
            "菱形",
            TargetShape.DIAMOND,
        )
        self.shape_combo.addItem(
            "星形",
            TargetShape.STAR,
        )
        form.addRow(
            "目标形状：",
            self.shape_combo,
        )

        self.path_combo = QComboBox()
        self.path_combo.addItem(
            "水平往返",
            TargetPath.HORIZONTAL,
        )
        self.path_combo.addItem(
            "垂直往返",
            TargetPath.VERTICAL,
        )
        self.path_combo.addItem(
            "圆周",
            TargetPath.CIRCLE,
        )
        self.path_combo.addItem(
            "8 字轨迹",
            TargetPath.FIGURE_EIGHT,
        )
        self.path_combo.addItem(
            "平滑随机运动",
            TargetPath.RANDOM,
        )
        form.addRow(
            "运动轨迹：",
            self.path_combo,
        )

        self.effect_combo = QComboBox()
        self.effect_combo.addItem(
            "无",
            TargetEffect.NONE,
        )
        self.effect_combo.addItem(
            "呼吸缩放",
            TargetEffect.PULSE,
        )
        self.effect_combo.addItem(
            "旋转",
            TargetEffect.SPIN,
        )
        form.addRow(
            "动画效果：",
            self.effect_combo,
        )

        self.diameter_spin = QSpinBox()
        self.diameter_spin.setRange(16, 600)
        self.diameter_spin.setValue(100)
        self.diameter_spin.setSuffix(" px")
        form.addRow(
            "目标直径：",
            self.diameter_spin,
        )

        self.period_spin = QDoubleSpinBox()
        self.period_spin.setRange(1.0, 120.0)
        self.period_spin.setValue(6.0)
        self.period_spin.setSingleStep(0.5)
        self.period_spin.setSuffix(" 秒/周期")
        form.addRow(
            "运动速度：",
            self.period_spin,
        )

        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(
            5,
            3_600,
        )
        self.duration_spin.setValue(60)
        self.duration_spin.setSuffix(" 秒")
        form.addRow(
            "任务时长：",
            self.duration_spin,
        )

        color_row = QHBoxLayout()
        self.color_edit = QLineEdit("#ffcc00")
        color_button = QPushButton("选择颜色")
        color_button.clicked.connect(self._select_color)
        color_row.addWidget(self.color_edit, 1)
        color_row.addWidget(color_button)
        form.addRow(
            "填充颜色：",
            color_row,
        )

        image_row = QHBoxLayout()
        self.image_edit = QLineEdit()
        image_button = QPushButton("选择图片")
        image_button.clicked.connect(self._select_image)
        image_row.addWidget(self.image_edit, 1)
        image_row.addWidget(image_button)
        form.addRow(
            "填充图片：",
            image_row,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addStretch(1)
        root.addWidget(buttons)

    def _select_color(self) -> None:
        selected = QColorDialog.getColor(
            QColor(self.color_edit.text()),
            self,
            "选择目标颜色",
        )

        if selected.isValid():
            self.color_edit.setText(selected.name())

    def _select_image(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择目标填充图片",
            "",
            ("Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"),
        )

        if filename:
            self.image_edit.setText(filename)

    def build_config(self) -> TrackingBallConfig:
        return TrackingBallConfig(
            shape=self.shape_combo.currentData(),
            effect=self.effect_combo.currentData(),
            path=self.path_combo.currentData(),
            diameter_px=self.diameter_spin.value(),
            color=self.color_edit.text(),
            image_path=(self.image_edit.text().strip() or None),
            period_seconds=(self.period_spin.value()),
            duration_seconds=self.duration_spin.value(),
        )
