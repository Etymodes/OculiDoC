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
        root = QVBoxLayout(self)
        boundary = QLabel(
            "v0.1.3 独立边界：眼动、SSVEP、被动 EEG 与运动想象分别运行、分别报告；"
            "不融合为控制指令。"
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
        source_path_button = QPushButton("浏览")
        source_path_button.clicked.connect(self._browse_source)
        source_path_layout.addWidget(source_path_button)
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

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.5, 20.0)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setValue(2.0)
        self.duration_spin.setSuffix(" 秒/试次")
        form.addRow("采集窗", self.duration_spin)

        self.frequency_edit = QLineEdit("8, 10, 12, 15")
        self.frequency_label = QLabel("SSVEP 频率")
        form.addRow(self.frequency_label, self.frequency_edit)

        self.decoder_combo = QComboBox()
        for name in DecoderRegistry.names():
            self.decoder_combo.addItem(name.upper(), name)
        self.decoder_combo.setCurrentIndex(1)
        self.decoder_label = QLabel("解码器")
        form.addRow(self.decoder_label, self.decoder_combo)

        self.model_path_edit = QLineEdit()
        self.model_path_edit.setPlaceholderText("TRCA/eTRCA 患者校准模型")
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
        self.trial_count_spin.setValue(2)
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
        requires_model = is_ssvep and str(self.decoder_combo.currentData()) in {"trca", "etrca"}
        has_source_path = self._source_kind() is not SignalSourceKind.SIMULATION
        for ssvep_widget in (
            self.frequency_label,
            self.frequency_edit,
            self.decoder_label,
            self.decoder_combo,
        ):
            ssvep_widget.setVisible(is_ssvep)
        for model_widget in (self.model_label, self.model_row):
            model_widget.setVisible(requires_model)
        self.source_path_label.setVisible(has_source_path)
        self.source_path_row.setVisible(has_source_path)
        defaults = {
            SignalTaskKind.SSVEP_SINGLE_TARGET: "10",
            SignalTaskKind.SSVEP_BINARY_CHOICE: "10, 12",
            SignalTaskKind.SSVEP_FOUR_TARGET: "8, 10, 12, 15",
        }
        if task_kind in defaults:
            self.frequency_edit.setText(defaults[task_kind])
        self.notice_label.setText(
            "工程模拟会被永久标记，只生成工程报告，不进入患者临床报告。"
            if self._source_kind() is SignalSourceKind.SIMULATION
            else "设备或回放不可用时任务会明确失败，不会自动切换到模拟数据。"
        )

    def _browse_source(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择信号来源文件",
            str(Path.home()),
            "Signal files (*.npz *.jsonl *.json);;All files (*)",
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
            )
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "信号任务设置无效", str(error))
            return
        self.accept()
