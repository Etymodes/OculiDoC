"""OculiDoC-native subjective eye-position view."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import Qt
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

from oculidoc.config import Settings
from oculidoc.devices.contracts import EyeTrackerSample
from oculidoc.devices.preflight import GazePreflightResult
from oculidoc.tasks.gaze_stream import (
    GazeStreamWorker,
    create_eye_position_tracker,
)

NormalizedOcularPosition = tuple[float, float, float]
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
            f"已找到人工观察组件：{compatibility_executable} {TRACK_STATUS_ARGUMENT}。"
            if compatibility_executable is not None
            else "未找到 EyeAssist Track Status 兼容组件。"
        )
        return (
            f"尚无与当前眼动源匹配的自检证据，本窗口不会据此推断设备能力。{compatibility}"
            "可先手动打开兼容窗口，正式任务前仍须重新运行设备自检。"
        )

    source = f"DLL：{result.library_path}" if result.library_path else f"连接来源：{result.source}"
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
        f"兼容组件：{compatibility_executable} {TRACK_STATUS_ARGUMENT}。"
        if compatibility_executable is not None
        else "未找到 EyeAssist Track Status 兼容组件。"
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
        self._left_eye: NormalizedOcularPosition | None = None
        self._right_eye: NormalizedOcularPosition | None = None
        self._empty_message = "等待左右眼位置数据…"

    @property
    def eye_positions(
        self,
    ) -> tuple[NormalizedOcularPosition | None, NormalizedOcularPosition | None]:
        return self._left_eye, self._right_eye

    def consume_sample(self, sample: EyeTrackerSample) -> None:
        self._left_eye = sample.left_eye_position_normalized
        self._right_eye = sample.right_eye_position_normalized
        self.update()

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

        track_box = self.rect().adjusted(70, 42, -70, -105)
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

        positions = (
            ("左眼", self._left_eye, QColor("#1565c0")),
            ("右眼", self._right_eye, QColor("#176b36")),
        )
        for label, position, color in positions:
            if position is None:
                continue

            x = track_box.left() + round(self._clamp(position[0]) * track_box.width())
            y = track_box.top() + round(self._clamp(position[1]) * track_box.height())
            painter.setPen(QPen(QColor("#ffffff"), 4))
            painter.setBrush(color)
            painter.drawEllipse(x - 22, y - 22, 44, 44)
            painter.setPen(QPen(color, 2))
            painter.drawText(
                x - 44,
                y + 48,
                88,
                28,
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        painter.setPen(QPen(QColor("#17324d"), 2))
        if self._left_eye is None and self._right_eye is None:
            painter.drawText(
                track_box,
                Qt.AlignmentFlag.AlignCenter,
                self._empty_message,
            )

        depth_parts = []
        if self._left_eye is not None:
            depth_parts.append(f"左眼 Z={self._left_eye[2]:.2f}")
        if self._right_eye is not None:
            depth_parts.append(f"右眼 Z={self._right_eye[2]:.2f}")
        depth_text = " · ".join(depth_parts) if depth_parts else "Z 距离层级：—"
        painter.drawText(
            self.rect().adjusted(28, self.height() - 88, -28, -20),
            Qt.AlignmentFlag.AlignCenter,
            f"{depth_text}　（0 较近，1 较远）",
        )


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
            "供操作者主观观察患者双眼是否位于追踪范围。窗口打开后会自动尝试"
            "Tobii 原生 Stream、已配置的兼容 DLL、通用桥接和 Tobii Experience"
            "自带追踪状态；无需手动选择连接方式。此处不设合格阈值，不计算有效率，"
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
        self.canvas.consume_sample(sample)

        if sample.eye_position_available:
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
                f"兼容组件：{executable} {TRACK_STATUS_ARGUMENT}。"
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
