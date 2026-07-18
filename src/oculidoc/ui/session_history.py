"""Patient experiment-session history dialog."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from oculidoc.application.clinical_trends import (
    generate_patient_trend_report,
)
from oculidoc.application.experiment_session_service import (
    ExperimentSessionService,
)
from oculidoc.application.gaze_report import generate_gaze_session_report
from oculidoc.application.session_history import (
    SessionHistoryEntry,
    build_patient_session_history,
    export_session_zip,
    format_task_result_lines,
)
from oculidoc.domain import Patient
from oculidoc.domain.experiment_session import (
    ExperimentSessionStatus,
)
from oculidoc.modules.registry import (
    DEFAULT_MODULES,
)

_MODULE_TITLES = {module.module_id: module.title for module in DEFAULT_MODULES}

_STATUS_LABELS = {
    ExperimentSessionStatus.CREATED: "已创建",
    ExperimentSessionStatus.RUNNING: "运行中",
    ExperimentSessionStatus.COMPLETED: "已完成",
    ExperimentSessionStatus.ABORTED: "已取消",
    ExperimentSessionStatus.FAILED: "失败",
}


def _format_duration(
    seconds: float | None,
) -> str:
    if seconds is None:
        return "-"

    total_seconds = max(
        0,
        int(round(seconds)),
    )
    minutes, remainder = divmod(
        total_seconds,
        60,
    )

    if minutes:
        return f"{minutes}分{remainder:02d}秒"

    return f"{remainder}秒"


def _format_ratio(
    ratio: float | None,
) -> str:
    if ratio is None:
        return "-"

    return f"{ratio:.1%}"


class PatientSessionHistoryDialog(QDialog):
    """Browse and export one patient's sessions."""

    def __init__(
        self,
        service: ExperimentSessionService,
        patient: Patient,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.service = service
        self.patient = patient
        self._entries: dict[
            UUID,
            SessionHistoryEntry,
        ] = {}

        self.setWindowTitle("患者实验记录")
        self.resize(1120, 680)
        self.setMinimumSize(900, 560)

        title = QLabel(f"{patient.display_label} · 实验记录")
        title.setStyleSheet("font-size: 21px; font-weight: 700;")

        self.module_filter = QComboBox()
        self.module_filter.setObjectName("sessionModuleFilter")
        self.module_filter.addItem(
            "全部任务",
            None,
        )

        for module in DEFAULT_MODULES:
            self.module_filter.addItem(
                module.title,
                module.module_id,
            )

        self.status_filter = QComboBox()
        self.status_filter.setObjectName("sessionStatusFilter")
        self.status_filter.addItem(
            "全部状态",
            None,
        )

        for status in ExperimentSessionStatus:
            self.status_filter.addItem(
                _STATUS_LABELS[status],
                status.value,
            )

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("refreshSessionHistoryButton")
        self.refresh_button.clicked.connect(self.refresh_sessions)
        self.module_filter.currentIndexChanged.connect(self.refresh_sessions)
        self.status_filter.currentIndexChanged.connect(self.refresh_sessions)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("任务："))
        filters.addWidget(self.module_filter)
        filters.addWidget(QLabel("状态："))
        filters.addWidget(self.status_filter)
        filters.addStretch(1)
        filters.addWidget(self.refresh_button)

        self.table = QTableWidget(0, 7)
        self.table.setObjectName("patientSessionTable")
        self.table.setHorizontalHeaderLabels(
            [
                "时间",
                "任务",
                "状态",
                "时长",
                "样本数",
                "有效率",
                "文件数",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._refresh_detail)

        self.detail_label = QLabel("选择一条实验记录查看详情。")
        self.detail_label.setObjectName("sessionHistoryDetail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setStyleSheet(
            "background: #f5f8fb; border: 1px solid #d9e3ec; border-radius: 8px; padding: 10px;"
        )
        self.detail_label.setMinimumHeight(115)

        self.open_button = QPushButton("打开目录")
        self.open_button.setObjectName("openSessionDirectoryButton")
        self.open_button.clicked.connect(self._open_directory)

        self.result_button = QPushButton("查看结果")
        self.result_button.setObjectName("viewSessionResultButton")
        self.result_button.clicked.connect(self._show_result)

        self.report_button = QPushButton("生成报告")
        self.report_button.setObjectName("generateGazeReportButton")
        self.report_button.clicked.connect(self._generate_report)

        self.trend_button = QPushButton("患者趋势")
        self.trend_button.setObjectName("generatePatientTrendReportButton")
        self.trend_button.setToolTip("汇总该患者历次同类任务结果与数据质量")
        self.trend_button.clicked.connect(self._generate_trend_report)

        self.export_button = QPushButton("导出 ZIP")
        self.export_button.setObjectName("exportSessionZipButton")
        self.export_button.clicked.connect(self._export_zip)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)

        actions = QHBoxLayout()
        actions.addWidget(self.open_button)
        actions.addWidget(self.result_button)
        actions.addWidget(self.report_button)
        actions.addWidget(self.trend_button)
        actions.addWidget(self.export_button)
        actions.addStretch(1)
        actions.addWidget(close_button)

        root = QVBoxLayout(self)
        root.addWidget(title)
        root.addLayout(filters)
        root.addWidget(self.table, 1)
        root.addWidget(self.detail_label)
        root.addLayout(actions)

        self.refresh_sessions()

    def _current_entry(
        self,
    ) -> SessionHistoryEntry | None:
        row = self.table.currentRow()

        if row < 0:
            return None

        item = self.table.item(row, 0)

        if item is None:
            return None

        raw_session_id = item.data(Qt.ItemDataRole.UserRole)

        if raw_session_id is None:
            return None

        return self._entries.get(UUID(str(raw_session_id)))

    def refresh_sessions(
        self,
        *_args: object,
    ) -> None:
        """Reload and apply the visible filters."""

        module_id = self.module_filter.currentData()
        status_value = self.status_filter.currentData()
        entries = build_patient_session_history(
            self.service,
            self.patient.patient_id,
        )

        filtered = [
            entry
            for entry in entries
            if (module_id is None or entry.module_id == module_id)
            and (status_value is None or entry.status.value == status_value)
        ]

        self._entries = {entry.session_id: entry for entry in filtered}

        self.table.clearSelection()
        self.table.setCurrentItem(None)
        self.table.setRowCount(len(filtered))

        for row, entry in enumerate(filtered):
            created_text = entry.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            values = (
                created_text,
                _MODULE_TITLES.get(
                    entry.module_id,
                    entry.module_id,
                ),
                _STATUS_LABELS[entry.status],
                _format_duration(entry.duration_seconds),
                (str(entry.sample_count) if entry.sample_count is not None else "-"),
                _format_ratio(entry.valid_sample_ratio),
                str(entry.artifact_count),
            )

            for column, value in enumerate(values):
                item = QTableWidgetItem(value)

                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        str(entry.session_id),
                    )

                self.table.setItem(
                    row,
                    column,
                    item,
                )

        self.table.resizeColumnsToContents()

        if filtered:
            self.table.setCurrentCell(0, 0)
            self.table.selectRow(0)
            self._refresh_detail()
        else:
            self.detail_label.setText("当前筛选条件下没有实验记录。")

    def _refresh_detail(self) -> None:
        entry = self._current_entry()

        if entry is None:
            self.detail_label.setText("选择一条实验记录查看详情。")
            return

        dwell_text = (
            "、".join(
                (f"{role}: {duration:.0f} ms")
                for role, duration in sorted(entry.dwell_by_role_ms.items())
            )
            or "-"
        )

        details = [
            f"会话 ID：{entry.session_id}",
            (f"任务：{_MODULE_TITLES.get(entry.module_id, entry.module_id)}"),
            (f"状态：{_STATUS_LABELS[entry.status]}"),
            f"目录：{entry.session_directory}",
            f"AOI 停留：{dwell_text}",
        ]

        result_lines = format_task_result_lines(entry.task_results)

        if result_lines:
            details.append("结构化结果：")
            details.extend(f"  {line}" for line in result_lines)

        if entry.failure_reason:
            details.append(f"失败/取消原因：{entry.failure_reason}")

        self.detail_label.setText("\n".join(details))

    def _require_entry(
        self,
    ) -> SessionHistoryEntry | None:
        entry = self._current_entry()

        if entry is None:
            QMessageBox.information(
                self,
                "未选择记录",
                "请先选择一条实验记录。",
            )

        return entry

    def _open_directory(
        self,
        checked: bool = False,
    ) -> None:
        del checked
        entry = self._require_entry()

        if entry is None:
            return

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.session_directory)))

        if not opened:
            QMessageBox.warning(
                self,
                "无法打开目录",
                str(entry.session_directory),
            )

    def _show_result(
        self,
        checked: bool = False,
    ) -> None:
        del checked
        entry = self._require_entry()

        if entry is None:
            return

        dwell_text = (
            "\n".join(
                (f"  {role}: {duration:.0f} ms")
                for role, duration in sorted(entry.dwell_by_role_ms.items())
            )
            or "  -"
        )

        message = "\n".join(
            [
                (f"任务：{_MODULE_TITLES.get(entry.module_id, entry.module_id)}"),
                (f"状态：{_STATUS_LABELS[entry.status]}"),
                (f"样本数：{entry.sample_count if entry.sample_count is not None else '-'}"),
                (f"有效率：{_format_ratio(entry.valid_sample_ratio)}"),
                (f"时长：{_format_duration(entry.duration_seconds)}"),
                "AOI 停留：",
                dwell_text,
            ]
        )

        result_lines = format_task_result_lines(entry.task_results)

        if result_lines:
            message += "\n\n结构化结果：\n" + "\n".join(f"  {line}" for line in result_lines)

        QMessageBox.information(
            self,
            "实验结果",
            message,
        )

    def _generate_report(
        self,
        checked: bool = False,
    ) -> None:
        """Generate and open a gaze report."""

        del checked
        entry = self._require_entry()

        if entry is None:
            return

        if entry.status is not ExperimentSessionStatus.COMPLETED:
            QMessageBox.information(
                self,
                "无法生成报告",
                "仅已完成的实验会话可以生成报告。",
            )
            return

        try:
            report = generate_gaze_session_report(
                self.service,
                entry.session_id,
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "报告生成失败",
                str(error),
            )
            return

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(report.html_path)))

        if not opened:
            QMessageBox.information(
                self,
                "报告已生成",
                str(report.html_path),
            )

        self.refresh_sessions()

    def _default_archive_name(
        self,
        entry: SessionHistoryEntry,
    ) -> str:
        timestamp = entry.created_at.astimezone().strftime("%Y%m%d_%H%M%S")
        safe_patient_code = self.patient.patient_code.replace("/", "_").replace("\\", "_")

        return f"{safe_patient_code}_{entry.module_id}_{timestamp}.zip"

    def _generate_trend_report(
        self,
        checked: bool = False,
    ) -> None:
        "Generate and open the patient's longitudinal report."

        del checked
        entry = self._require_entry()

        if entry is None:
            return

        try:
            report = generate_patient_trend_report(
                self.service,
                entry.session_id,
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "趋势报告生成失败",
                str(error),
            )
            return

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(report.html_path)))

        if not opened:
            QMessageBox.information(
                self,
                "趋势报告已生成",
                str(report.html_path),
            )

        self.refresh_sessions()

    def _export_zip(
        self,
        checked: bool = False,
    ) -> None:
        del checked
        entry = self._require_entry()

        if entry is None:
            return

        default_path = Path.home() / "Downloads" / self._default_archive_name(entry)
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出实验会话",
            str(default_path),
            "ZIP 压缩包 (*.zip)",
        )

        if not selected_path:
            return

        try:
            archive_path = export_session_zip(
                self.service,
                entry.session_id,
                selected_path,
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "导出失败",
                str(error),
            )
            return

        QMessageBox.information(
            self,
            "导出完成",
            str(archive_path),
        )
