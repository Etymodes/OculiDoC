"""OculiDoC-native subjective eye-position view.

``OpoinThesis`` is a project coinage from ``ὀποῖν θέσις`` (``opoîn thésis``,
"position of the two eyes") and the English-sounding "open thesis".  The name
marks a future extension seam for licensed intelligent eye-position analysis,
patient-adaptive models, and EEG/BCI synchronization.  This module's current
boundary remains human observation without scoring or persistence.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QPainter,
    QPaintEvent,
    QPen,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.application.eye_positioning import (
    EyePositioningParameters,
    EyePositioningParametersCalculator,
)
from oculidoc.config import Settings
from oculidoc.devices.contracts import EyeTrackerSample
from oculidoc.devices.preflight import GazePreflightResult
from oculidoc.tasks.gaze_stream import (
    GazeStreamWorker,
    create_eye_position_tracker,
)

NormalizedEyeDisplayPosition = tuple[float, float]
TRACK_STATUS_EXECUTABLE = "TobiiDynavox.EyeAssist.Smorgasbord.exe"
TRACK_STATUS_ARGUMENT = "--showtrackstatus"


def _track_status_roots(settings: Settings) -> tuple[Path, ...]:
    roots: list[Path] = []

    if settings.tobii_stream_engine_dll is not None:
        roots.append(settings.tobii_stream_engine_dll.expanduser().resolve().parent)

    compatibility_root = settings.just_need_to_see_root.expanduser().resolve()
    roots.extend(
        [
            compatibility_root,
            compatibility_root.parent / "EyePosition",
            Path("D:/EyePosition"),
            Path.home() / "Downloads" / "EyePosition",
            Path.home() / "Documents" / "EyePosition",
        ]
    )

    for environment_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        environment_root = os.environ.get(environment_name)

        if not environment_root:
            continue

        root = Path(environment_root)
        roots.extend(
            [
                root / "Tobii Dynavox",
                root / "Programs" / "Tobii Dynavox",
            ]
        )

    return tuple(dict.fromkeys(roots))


def find_track_status_executable(settings: Settings) -> Path | None:
    """Find the EyeAssist Track Status host used by the legacy shortcut."""
    path_entry = shutil.which(TRACK_STATUS_EXECUTABLE)

    if path_entry:
        return Path(path_entry).resolve()

    for root in _track_status_roots(settings):
        direct = root / TRACK_STATUS_EXECUTABLE

        if direct.is_file():
            return direct.resolve()

        if not root.is_dir():
            continue

        try:
            match = next(root.rglob(TRACK_STATUS_EXECUTABLE), None)
        except OSError:
            continue

        if match is not None:
            return match.resolve()

    return None


def launch_track_status(
    settings: Settings,
) -> subprocess.Popen[bytes] | None:
    """Launch the same independent Track Status mode as the legacy shortcut."""
    if sys.platform != "win32":
        return None

    executable = find_track_status_executable(settings)

    if executable is None:
        return None

    try:
        return subprocess.Popen(
            [
                str(executable),
                TRACK_STATUS_ARGUMENT,
            ],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


def self_check_supports_current_eye_position(
    settings: Settings,
    result: GazePreflightResult | None,
) -> bool:
    """Use the current connection only when self-check observed real eye-position data."""
    return bool(
        result is not None
        and result.source == settings.gaze_source
        and result.observed("eye_position")
    )


def eye_position_diagnostic_text(
    settings: Settings,
    result: GazePreflightResult | None,
    *,
    compatibility_executable: Path | None = None,
) -> str:
    """Explain the selected OpoinThesis path using persisted self-check evidence."""
    if result is None or result.source != settings.gaze_source:
        compatibility = (
            "已找到 Tobii Experience 兼容眼位组件。"
            if compatibility_executable is not None
            else "未找到 Tobii Experience 兼容眼位组件。"
        )
        return (
            f"尚无与当前眼动源匹配的自检证据，本窗口不会据此推断设备能力。{compatibility}"
            "可先手动打开兼容窗口，正式任务前仍须重新运行设备自检。"
        )

    library_name = (
        str(result.library_path).replace("\\", "/").rsplit("/", 1)[-1]
        if result.library_path
        else None
    )
    source = f"DLL：{library_name}" if library_name else f"连接来源：{result.source}"
    evidence = (
        f"证据时间：{result.updated_at_utc}；Python：{struct.calcsize('P') * 8} 位；"
        f"{source}；采样：{result.sample_rate_hz:.2f} Hz；"
        f"组合注视有效率：{result.valid_ratio:.0%}；"
        f"{result.observed_capability_text()}。"
    )
    if result.observed("eye_position"):
        return f"结论：自检已实际观察到左右眼三维眼位，可在本窗口显示。\n{evidence}"

    notes = "；".join(result.capability_notes)
    reason = notes or "自检样本只观察到注视点，没有观察到左右眼三维眼位。"
    compatibility = (
        "已找到 Tobii Experience 兼容眼位组件。"
        if compatibility_executable is not None
        else "未找到 Tobii Experience 兼容眼位组件。"
    )
    return (
        f"结论：当前链路没有提供左右眼三维眼位；{reason}"
        "这不等于二维组合注视点不可用，也不能据此补造瞳孔或逐眼数据。\n"
        f"{evidence}\n{compatibility}"
    )


class OpoinThesisCanvas(QWidget):
    """Draw left and right eyes inside the normalized Tobii track box."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 420)
        self._calculator = EyePositioningParametersCalculator()
        self._parameters: EyePositioningParameters | None = None
        self._empty_message = "等待左右眼位置数据…"

    @property
    def eye_positions(
        self,
    ) -> tuple[
        NormalizedEyeDisplayPosition | None,
        NormalizedEyeDisplayPosition | None,
    ]:
        parameters = self._parameters
        if parameters is None:
            return None, None

        return parameters.left_eye_position, parameters.right_eye_position

    @property
    def positioning_parameters(self) -> EyePositioningParameters | None:
        return self._parameters

    def consume_sample(self, sample: EyeTrackerSample) -> EyePositioningParameters:
        self._parameters = self._calculator.receive_gaze_data(sample)
        self.update()
        return self._parameters

    def set_empty_message(self, message: str) -> None:
        self._empty_message = message.strip() or "等待左右眼位置数据…"
        self.update()

    @staticmethod
    def _clamp(value: float) -> float:
        return min(1.0, max(0.0, value))

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f4f7fb"))

        track_box = self.rect().adjusted(70, 42, -70, -150)
        painter.setPen(QPen(QColor("#bfd3e4"), 3))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(track_box, 18, 18)
        painter.setPen(QPen(QColor("#d7e2ec"), 2))
        painter.drawLine(
            track_box.center().x(),
            track_box.top(),
            track_box.center().x(),
            track_box.bottom(),
        )
        painter.drawLine(
            track_box.left(),
            track_box.center().y(),
            track_box.right(),
            track_box.center().y(),
        )

        parameters = self._parameters
        positions = (
            (
                "左眼",
                parameters.left_eye_position if parameters is not None else None,
                parameters.left_eye_extrapolated if parameters is not None else False,
                QColor("#1565c0"),
            ),
            (
                "右眼",
                parameters.right_eye_position if parameters is not None else None,
                parameters.right_eye_extrapolated if parameters is not None else False,
                QColor("#176b36"),
            ),
        )
        eye_radius = max(14.0, min(track_box.width(), track_box.height()) * 0.05)
        head_angle = (
            parameters.head_angle_degrees
            if parameters is not None and parameters.head_angle_degrees is not None
            else 0.0
        )

        for label, position, extrapolated, color in positions:
            if position is None:
                continue

            x = track_box.left() + round(self._clamp(position[0]) * track_box.width())
            y = track_box.top() + round(self._clamp(position[1]) * track_box.height())
            painter.save()
            painter.translate(x, y)
            painter.rotate(head_angle)
            outline = QPen(color, 4)
            if extrapolated:
                outline.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(outline)
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(
                QRectF(
                    -eye_radius,
                    -eye_radius * 0.65,
                    eye_radius * 2.0,
                    eye_radius * 1.3,
                )
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            pupil_radius = eye_radius * 0.34
            painter.drawEllipse(
                QRectF(
                    -pupil_radius,
                    -pupil_radius,
                    pupil_radius * 2.0,
                    pupil_radius * 2.0,
                )
            )
            painter.restore()
            painter.setPen(QPen(color, 2))
            painter.drawText(
                x - 44,
                y + round(eye_radius) + 12,
                88,
                28,
                Qt.AlignmentFlag.AlignCenter,
                f"{label}{' · 补偿' if extrapolated else ''}",
            )

        painter.setPen(QPen(QColor("#17324d"), 2))
        if parameters is None or (
            parameters.left_eye_position is None and parameters.right_eye_position is None
        ):
            painter.drawText(
                track_box,
                Qt.AlignmentFlag.AlignCenter,
                self._empty_message,
            )

        status_rect = self.rect().adjusted(32, self.height() - 126, -32, -20)
        left_status = self._eye_status(
            parameters.left_eye_position if parameters is not None else None,
            parameters.left_eye_extrapolated if parameters is not None else False,
        )
        right_status = self._eye_status(
            parameters.right_eye_position if parameters is not None else None,
            parameters.right_eye_extrapolated if parameters is not None else False,
        )
        angle_text = (
            f"头部倾角：{parameters.head_angle_degrees:+.1f}°"
            if parameters is not None and parameters.head_angle_degrees is not None
            else "头部倾角：—"
        )
        distance_text = self._distance_text(parameters)
        painter.setPen(QPen(QColor("#17324d"), 2))
        painter.drawText(
            status_rect,
            Qt.AlignmentFlag.AlignCenter,
            f"左眼：{left_status}　　右眼：{right_status}\n{angle_text}　　{distance_text}",
        )

    @staticmethod
    def _eye_status(
        position: tuple[float, float] | None,
        extrapolated: bool,
    ) -> str:
        if position is None:
            return "未追踪"
        if extrapolated:
            return "短暂补偿"
        return "已追踪"

    @staticmethod
    def _distance_text(
        parameters: EyePositioningParameters | None,
    ) -> str:
        if parameters is None or parameters.distance_mm is None:
            return "距离：当前数据源未提供毫米值"

        range_text = "参考范围内" if parameters.is_distance_in_range else "参考范围外"
        return f"距离：{parameters.distance_mm:.0f} mm · {range_text}（450–850 mm）"


class OpoinThesisDialog(QDialog):
    """Show eye position for human judgement without scoring or persistence."""

    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None = None,
        *,
        preflight_result: GazePreflightResult | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("opoinThesisDialog")
        self.setWindowTitle("OpoinThesis · 主观眼位检查")
        self.resize(900, 650)
        self.setStyleSheet(
            """
            QDialog#opoinThesisDialog { background: #eef3f8; }
            QLabel {
                color: #17324d;
                font-family: "Microsoft YaHei UI";
            }
            QLabel#opoinThesisStatus {
                color: #184e77;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#opoinThesisDiagnostic {
                color: #5a7184;
                font-size: 13px;
                background: #f8fbfe;
                border: 1px solid #d9e3ec;
                border-radius: 8px;
                padding: 8px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)

        explanation = QLabel(
            "OpoinThesis 取意于 opoîn thésis（双眼的位置），并与 open thesis"
            "（开放研究）双关。当前仅供操作者主观观察患者双眼是否位于追踪范围。"
            "窗口打开后会自动尝试"
            "Tobii 原生 Stream、已配置的兼容 DLL、通用桥接和 Tobii Experience"
            "自带追踪状态；无需手动选择连接方式。眼位显示包含短暂丢帧补偿、头部倾角"
            "和可用时的 450–850 mm 参考距离提示，但不生成合格结论、不计算有效率、"
            "不保存结果，也不参与设备自检或正式任务报告。"
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        self._compatibility_executable = find_track_status_executable(settings)
        self.status_label = QLabel("正在自动检测眼位状态…")
        self.status_label.setObjectName("opoinThesisStatus")
        root.addWidget(self.status_label)

        self.diagnostic_label = QLabel(
            eye_position_diagnostic_text(
                settings,
                preflight_result,
                compatibility_executable=self._compatibility_executable,
            )
        )
        self._diagnostic_base_text = self.diagnostic_label.text()
        self.diagnostic_label.setObjectName("opoinThesisDiagnostic")
        self.diagnostic_label.setWordWrap(True)
        self.diagnostic_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.diagnostic_label)

        self.canvas = OpoinThesisCanvas()
        root.addWidget(self.canvas, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)

        self._settings = settings
        self._worker = GazeStreamWorker(
            settings,
            self,
            device=create_eye_position_tracker(settings),
        )
        self._worker.status_changed.connect(self._update_worker_status)
        self._worker.sample_received.connect(self._consume_sample)
        self._worker.stream_error.connect(self._show_error)
        self._compatibility_process: subprocess.Popen[bytes] | None = None
        self._failure_shown = False
        self._started = False

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color:#b42318;" if error else "")

    def _update_worker_status(self, text: str) -> None:
        process = self._compatibility_process
        if self._failure_shown or (process is not None and process.poll() is None):
            return
        self._set_status(text)

    def _consume_sample(self, sample: EyeTrackerSample) -> None:
        parameters = self.canvas.consume_sample(sample)

        if sample.eye_position_available:
            if parameters.left_eye_extrapolated or parameters.right_eye_extrapolated:
                self._set_status("已连接 · 眼位信号短暂丢失，正在显示补偿位置")
            else:
                self._set_status("已连接 · 正在显示主观眼位")
        else:
            detail = getattr(
                self._worker.device,
                "eye_position_stream_detail",
                "当前数据暂未提供左右眼位。",
            )
            self._set_status("已连接 · 当前数据暂未提供左右眼位")
            self.diagnostic_label.setText(f"{self._diagnostic_base_text}\n实时状态：{detail}")

    def _show_error(self, message: str) -> None:
        if not self._open_compatibility_view():
            self._show_undetected(message)

    def _show_undetected(self, detail: str) -> None:
        self._set_status("未检测到眼位状态", error=True)
        self.canvas.set_empty_message("未检测到眼位状态")
        self.diagnostic_label.setText(f"{self._diagnostic_base_text}\n自动检测详情：{detail}")
        if self._failure_shown:
            return
        self._failure_shown = True
        QMessageBox.warning(
            self,
            "未检测到眼位状态",
            "已尝试所有可用眼位连接方式，但未检测到眼位状态。\n"
            "请确认眼动仪已连接、Tobii Experience 服务正在运行，"
            "或在设备设置中检查 DLL/桥接路径。",
        )

    def _open_compatibility_view(self, checked: bool = False) -> bool:
        del checked
        process = self._compatibility_process

        if process is not None and process.poll() is None:
            self._set_status("兼容眼位窗口正在运行")
            return True

        process = launch_track_status(self._settings)

        if process is None:
            return False

        self._compatibility_process = process
        self._worker.stop()
        self.canvas.set_empty_message("已打开 Tobii Experience 眼位状态")
        self._set_status("已打开 Tobii Experience 眼位状态 · 仅供人工观察")
        executable = self._compatibility_executable
        if executable is not None:
            self.diagnostic_label.setText(
                f"{self._diagnostic_base_text}\n"
                "已打开 Tobii Experience 兼容眼位组件。"
                "该窗口只显示追踪范围，不提供瞳孔直径或研究级单眼数据。"
            )
        return True

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)

        if not self._started:
            self._started = True
            self._worker.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._worker.stop()
        process = self._compatibility_process

        if process is not None:
            with suppress(OSError):
                if process.poll() is None:
                    process.terminate()

        event.accept()
