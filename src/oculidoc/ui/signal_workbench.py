"""Administrator controls for independent EEG, SSVEP, and MI workflows."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.application.builtin_test_patient import BUILTIN_TEST_PATIENT_CODE
from oculidoc.bci.ssvep.registry import DecoderRegistry
from oculidoc.signal_tasks.config import (
    SIGNAL_TASK_CAPABILITIES,
    SignalTaskConfig,
    SignalTaskKind,
)
from oculidoc.signals.models import SignalParadigm, SignalSourceKind
from oculidoc.signals.profile import PatientSignalProfile

_PARADIGM_LABELS = {
    SignalParadigm.GAZE: "眼动",
    SignalParadigm.SSVEP: "SSVEP",
    SignalParadigm.MI: "运动想象",
    SignalParadigm.PASSIVE_EEG: "被动 EEG",
    SignalParadigm.P300: "P300（后续）",
}


class SignalParadigmSelector(QWidget):
    """Homepage multi-select persisted in the patient signal profile."""

    selection_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("输入范式："))
        self._boxes: dict[SignalParadigm, QCheckBox] = {}
        for paradigm in SignalParadigm:
            box = QCheckBox(_PARADIGM_LABELS[paradigm])
            box.setObjectName(f"signalParadigm_{paradigm.value}")
            if paradigm is SignalParadigm.P300:
                box.setEnabled(False)
                box.setToolTip("v0.1.3 仅保留扩展位，尚未进入临床任务。")
            box.toggled.connect(self._emit_selection)
            self._boxes[paradigm] = box
            layout.addWidget(box)
        layout.addStretch(1)
        self.set_selected((SignalParadigm.GAZE,))

    def selected_paradigms(self) -> tuple[SignalParadigm, ...]:
        return tuple(paradigm for paradigm, box in self._boxes.items() if box.isChecked())

    def set_selected(self, values: Iterable[SignalParadigm | str]) -> None:
        selected = {SignalParadigm(value) for value in values}
        for paradigm, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(paradigm in selected)
            box.blockSignals(False)

    def set_patient_available(self, available: bool) -> None:
        for paradigm, box in self._boxes.items():
            box.setEnabled(available and paradigm is not SignalParadigm.P300)

    def _emit_selection(self) -> None:
        selected = self.selected_paradigms()
        if not selected:
            gaze = self._boxes[SignalParadigm.GAZE]
            gaze.blockSignals(True)
            gaze.setChecked(True)
            gaze.blockSignals(False)
            selected = (SignalParadigm.GAZE,)
        self.selection_changed.emit(selected)


class SignalWorkbenchDialog(QDialog):
    """Build one validated signal task configuration without running algorithms."""

    def __init__(
        self,
        profile: PatientSignalProfile,
        *,
        patient_code: str,
        selected_paradigms: tuple[SignalParadigm, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.profile = profile
        self.patient_code = patient_code
        self.selected_paradigms = selected_paradigms
        self.config: SignalTaskConfig | None = None
        self.setWindowTitle("神经信号与 BCI 工作台")
        self.setMinimumWidth(650)
        self.setObjectName("signalWorkbenchDialog")
        self.setStyleSheet(
            """
            QDialog#signalWorkbenchDialog { background: #eef3f8; color: #17324d; }
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
                background: #f8fbfe; border: 1px solid #bfd3e4;
                border-radius: 7px; padding: 6px; color: #17324d;
            }
            QPushButton { border-radius: 7px; padding: 7px 14px; }
            """
        )
        root = QVBoxLayout(self)
        boundary = QLabel(
            "v0.1.3 独立边界：眼动、SSVEP、被动 EEG 与运动想象分别运行、分别报告；"
            "二选一沟通任务提供刺激、质量门禁、识别、反馈、重试与原始数据闭环；"
            "不向外骨骼等外部设备下发动作。"
        )
        boundary.setWordWrap(True)
        boundary.setStyleSheet("color:#17324d;font-weight:700")
        root.addWidget(boundary)
        form = QFormLayout()

        self.task_combo = QComboBox()
        for capability in SIGNAL_TASK_CAPABILITIES:
            if capability.paradigm in selected_paradigms:
                self.task_combo.addItem(capability.title, capability.task_kind.value)
        form.addRow("独立任务", self.task_combo)

        self.source_combo = QComboBox()
        if patient_code.casefold() == BUILTIN_TEST_PATIENT_CODE.casefold():
            self.source_combo.addItem("工程模拟（仅 Beta00）", SignalSourceKind.SIMULATION.value)
        self.source_combo.addItem(
            "Tieying/JustSsvep 实时桥（本机 12991）",
            SignalSourceKind.MYLIAN_WEBSOCKET.value,
        )
        self.source_combo.addItem("Mylian 本地桥接文件", SignalSourceKind.MYLIAN_BRIDGE.value)
        self.source_combo.addItem("标准本地 JSON 桥", SignalSourceKind.LOCAL_BRIDGE.value)
        self.source_combo.addItem("标准 EEG 回放文件", SignalSourceKind.REPLAY.value)
        form.addRow("信号来源", self.source_combo)

        self.source_path_edit = QLineEdit()
        self.source_path_edit.setPlaceholderText("选择本地桥接 JSONL 或标准 EEG NPZ")
        self.source_path_row = QWidget()
        source_path_layout = QHBoxLayout(self.source_path_row)
        source_path_layout.setContentsMargins(0, 0, 0, 0)
        source_path_layout.addWidget(self.source_path_edit, 1)
        self.source_path_button = QPushButton("浏览")
        self.source_path_button.clicked.connect(self._browse_source)
        source_path_layout.addWidget(self.source_path_button)
        self.source_path_label = QLabel("来源文件")
        form.addRow(self.source_path_label, self.source_path_row)

        self.sample_rate_spin = QDoubleSpinBox()
        self.sample_rate_spin.setRange(50.0, 4_000.0)
        self.sample_rate_spin.setDecimals(1)
        self.sample_rate_spin.setValue(profile.eeg_sample_rate_hz)
        self.sample_rate_spin.setSuffix(" Hz")
        form.addRow("采样率", self.sample_rate_spin)

        self.channels_edit = QLineEdit(", ".join(profile.eeg_channel_names))
        form.addRow("通道（逗号分隔）", self.channels_edit)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.000001, 1_000.0)
        self.scale_spin.setDecimals(6)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setSuffix(" µV/计数")
        self.scale_verified_check = QCheckBox("厂商/设备协议已确认此换算")
        self.scale_row = QWidget()
        scale_layout = QHBoxLayout(self.scale_row)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.addWidget(self.scale_spin)
        scale_layout.addWidget(self.scale_verified_check, 1)
        self.scale_label = QLabel("原始计数换算")
        form.addRow(self.scale_label, self.scale_row)

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.5, 20.0)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setValue(2.0)
        self.duration_spin.setSuffix(" 秒/试次")
        form.addRow("采集窗", self.duration_spin)

        self.frequency_edit = QLineEdit("6, 7.5, 8.57, 10")
        self.frequency_label = QLabel("SSVEP 频率")
        form.addRow(self.frequency_label, self.frequency_edit)

        self.target_labels_edit = QLineEdit("是, 否")
        self.target_labels_label = QLabel("选择标签")
        form.addRow(self.target_labels_label, self.target_labels_edit)

        self.decoder_combo = QComboBox()
        for name in DecoderRegistry.names():
            self.decoder_combo.addItem(name.upper(), name)
        preferred_decoder = "trca" if profile.calibration_models else "fbcca"
        self.decoder_combo.setCurrentIndex(self.decoder_combo.findData(preferred_decoder))
        self.decoder_label = QLabel("解码器")
        form.addRow(self.decoder_label, self.decoder_combo)

        self.model_path_edit = QLineEdit()
        self.model_path_edit.setPlaceholderText("TRCA/eTRCA 患者校准模型")
        if profile.calibration_models:
            self.model_path_edit.setText(profile.calibration_models[-1])
        self.model_row = QWidget()
        model_layout = QHBoxLayout(self.model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(self.model_path_edit, 1)
        model_button = QPushButton("浏览")
        model_button.clicked.connect(self._browse_model)
        model_layout.addWidget(model_button)
        self.model_label = QLabel("校准模型")
        form.addRow(self.model_label, self.model_row)

        self.trial_count_spin = QSpinBox()
        self.trial_count_spin.setRange(1, 20)
        self.trial_count_spin.setValue(4)
        form.addRow("轮数", self.trial_count_spin)

        self.refresh_rate_spin = QDoubleSpinBox()
        self.refresh_rate_spin.setRange(30.0, 360.0)
        self.refresh_rate_spin.setDecimals(1)
        self.refresh_rate_spin.setValue(60.0)
        self.refresh_rate_spin.setSuffix(" Hz")
        form.addRow("显示刷新率", self.refresh_rate_spin)
        root.addLayout(form)

        self.notice_label = QLabel("")
        self.notice_label.setWordWrap(True)
        root.addWidget(self.notice_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.start_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.start_button.setText("创建独立会话并开始")
        buttons.accepted.connect(self._accept_config)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.task_combo.currentIndexChanged.connect(self._refresh_fields)
        self.source_combo.currentIndexChanged.connect(self._refresh_fields)
        self.decoder_combo.currentIndexChanged.connect(self._refresh_fields)
        self._refresh_fields()

    def _task_kind(self) -> SignalTaskKind:
        return SignalTaskKind(str(self.task_combo.currentData()))

    def _source_kind(self) -> SignalSourceKind:
        return SignalSourceKind(str(self.source_combo.currentData()))

    def _refresh_fields(self) -> None:
        if self.task_combo.count() == 0:
            self.notice_label.setText("当前输入范式只有眼动；请先勾选 SSVEP、运动想象或被动 EEG。")
            self.start_button.setEnabled(False)
            return
        self.start_button.setEnabled(True)
        task_kind = self._task_kind()
        capability = next(item for item in SIGNAL_TASK_CAPABILITIES if item.task_kind is task_kind)
        is_ssvep = capability.paradigm is SignalParadigm.SSVEP
        requires_model = is_ssvep and (
            str(self.decoder_combo.currentData()) in {"trca", "etrca"}
            or task_kind is SignalTaskKind.SSVEP_FREQUENCY_SCAN
        )
        has_source_path = self._source_kind() is not SignalSourceKind.SIMULATION
        is_websocket = self._source_kind() is SignalSourceKind.MYLIAN_WEBSOCKET
        is_mylian = self._source_kind() in {
            SignalSourceKind.MYLIAN_WEBSOCKET,
            SignalSourceKind.MYLIAN_BRIDGE,
        }
        is_communication = task_kind is SignalTaskKind.SSVEP_BINARY_COMMUNICATION
        for ssvep_widget in (
            self.frequency_label,
            self.frequency_edit,
            self.decoder_label,
            self.decoder_combo,
        ):
            ssvep_widget.setVisible(is_ssvep)
        for model_widget in (self.model_label, self.model_row):
            model_widget.setVisible(requires_model)
        self.model_label.setText(
            "既有模型（适配基线，可选）"
            if task_kind is SignalTaskKind.SSVEP_FREQUENCY_SCAN
            and str(self.decoder_combo.currentData()) not in {"trca", "etrca"}
            else "患者校准模型"
        )
        for label_widget in (self.target_labels_label, self.target_labels_edit):
            label_widget.setVisible(is_communication)
        for scale_widget in (self.scale_label, self.scale_row):
            scale_widget.setVisible(is_mylian)
        self.source_path_label.setVisible(has_source_path)
        self.source_path_row.setVisible(has_source_path)
        self.source_path_button.setVisible(has_source_path and not is_websocket)
        self.source_path_label.setText("实时桥地址" if is_websocket else "来源文件")
        if is_websocket and not self.source_path_edit.text().strip():
            self.source_path_edit.setText("ws://127.0.0.1:12991")
        defaults = {
            SignalTaskKind.SSVEP_SINGLE_TARGET: "10",
            SignalTaskKind.SSVEP_BINARY_CHOICE: "6, 10",
            SignalTaskKind.SSVEP_BINARY_COMMUNICATION: "6, 10",
            SignalTaskKind.SSVEP_FOUR_TARGET: "6, 7.5, 8.57, 10",
            SignalTaskKind.SSVEP_FREQUENCY_SCAN: "6, 7.5, 8.57, 10",
            SignalTaskKind.SSVEP_VALIDATION: "6, 7.5, 8.57, 10",
        }
        if task_kind in defaults:
            self.frequency_edit.setText(defaults[task_kind])
        self.notice_label.setText(
            "工程模拟会被永久标记，只生成工程报告，不进入患者临床报告。"
            if self._source_kind() is SignalSourceKind.SIMULATION
            else (
                "实时桥仅连接已观测的本机 WebSocket，不直接占用 COM4；连接或数据异常会明确失败，"
                "不会自动切换到模拟。未确认 µV 换算时可做工程识别，但不会获得报告/模型晋级资格。"
                if is_websocket
                else "设备或回放不可用时任务会明确失败，不会自动切换到模拟数据。"
            )
        )

    def _browse_source(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择信号来源文件",
            str(Path.home()),
            "Signal files (*.npz *.jsonl *.json *.csv);;All files (*)",
        )
        if path:
            self.source_path_edit.setText(path)

    def _browse_model(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择患者校准模型",
            str(Path.home()),
            "TRCA models (*.npz);;All files (*)",
        )
        if path:
            self.model_path_edit.setText(path)

    def _accept_config(self) -> None:
        if self.task_combo.count() == 0:
            QMessageBox.information(self, "没有可用任务", self.notice_label.text())
            return
        task_kind = self._task_kind()
        is_ssvep = (
            next(item for item in SIGNAL_TASK_CAPABILITIES if item.task_kind is task_kind).paradigm
            is SignalParadigm.SSVEP
        )
        try:
            frequencies = (
                tuple(float(value.strip()) for value in self.frequency_edit.text().split(","))
                if is_ssvep
                else ()
            )
            channels = tuple(
                value.strip() for value in self.channels_edit.text().split(",") if value.strip()
            )
            target_labels = (
                tuple(
                    value.strip()
                    for value in self.target_labels_edit.text().split(",")
                    if value.strip()
                )
                if task_kind is SignalTaskKind.SSVEP_BINARY_COMMUNICATION
                else ()
            )
            self.config = SignalTaskConfig(
                task_kind=task_kind,
                source_kind=self._source_kind(),
                sample_rate_hz=self.sample_rate_spin.value(),
                channel_names=channels,
                duration_seconds=self.duration_spin.value(),
                frequencies_hz=frequencies,
                decoder_name=str(self.decoder_combo.currentData()),
                trial_count=self.trial_count_spin.value(),
                source_path=(self.source_path_edit.text().strip() or None),
                model_path=(self.model_path_edit.text().strip() or None),
                refresh_rate_hz=self.refresh_rate_spin.value(),
                target_labels=target_labels,
                value_scale_uv_per_count=self.scale_spin.value(),
                scale_verified=self.scale_verified_check.isChecked(),
            )
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "信号任务设置无效", str(error))
            return
        self.accept()
