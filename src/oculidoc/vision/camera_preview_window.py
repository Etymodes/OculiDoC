"""Standalone PySide6 camera and eye-region workbench."""

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QCloseEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from oculidoc.app_paths import (
    UNASSIGNED_PATIENT_KEY,
    eye_observation_dataset_directory,
    normalize_patient_key,
)
from oculidoc.devices.diagnostics import (
    probe_cameras,
)
from oculidoc.vision.camera_preview import (
    CameraPreviewController,
    bgr_frame_to_qimage,
)
from oculidoc.vision.eye_crop import (
    export_eye_crops,
)
from oculidoc.vision.eye_observation import (
    EYE_STATE_LABELS,
    EyeBoundingBox,
    EyeObservation,
    EyeOpeningState,
    EyeSide,
    ObservationReviewStatus,
    ObservationSource,
)
from oculidoc.vision.eye_record import (
    build_eye_observation_record,
    write_eye_observation_record,
)
from oculidoc.vision.eye_region_proposal import FaceDetection, propose_eye_regions_from_face
from oculidoc.vision.frame_identity import FrameSaveGuard, build_camera_frame_key
from oculidoc.vision.image_selection_widget import (
    ImageSelectionLabel,
)
from oculidoc.vision.sample_naming import next_eye_sample_paths

_BACKENDS: dict[str, int | None] = {
    "DirectShow (DSHOW)": cv2.CAP_DSHOW,
    "Media Foundation (MSMF)": cv2.CAP_MSMF,
    "Automatic": None,
}


class CameraPreviewWindow(QMainWindow):
    """Camera preview with manual left/right eye selection."""

    def __init__(
        self,
        *,
        patient_key: str = UNASSIGNED_PATIENT_KEY,
    ) -> None:
        super().__init__()

        self.setWindowTitle("OculiDoC Camera and Eye Workbench")
        self.resize(1180, 800)

        self._controller = CameraPreviewController()
        self._patient_key = normalize_patient_key(patient_key)

        self.setWindowTitle(f"OculiDoC Camera and Eye Workbench — {self._patient_key}")
        self._frame_save_guard = FrameSaveGuard()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._read_frame)

        self._frozen = False
        self._pending_side: EyeSide | None = None
        self._pending_face_selection = False
        self._eye_sources: dict[
            EyeSide,
            ObservationSource,
        ] = {}
        self._eye_review_statuses: dict[
            EyeSide,
            ObservationReviewStatus,
        ] = {}
        self._eye_boxes: dict[
            EyeSide,
            EyeBoundingBox,
        ] = {}

        self._build_interface()
        self._refresh_camera_list()

    def _build_interface(self) -> None:
        central_widget = QWidget()
        root_layout = QVBoxLayout(central_widget)

        controls_frame = QFrame()
        controls_layout = QHBoxLayout(controls_frame)

        camera_form = QFormLayout()

        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(240)
        camera_form.addRow(
            "摄像头：",
            self.camera_combo,
        )

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(_BACKENDS.keys())
        camera_form.addRow(
            "采集后端：",
            self.backend_combo,
        )

        self.max_index_spin = QSpinBox()
        self.max_index_spin.setRange(0, 20)
        self.max_index_spin.setValue(6)
        camera_form.addRow(
            "最大索引：",
            self.max_index_spin,
        )

        controls_layout.addLayout(camera_form)

        camera_buttons = QVBoxLayout()

        self.refresh_button = QPushButton("刷新摄像头")
        self.refresh_button.clicked.connect(self._refresh_camera_list)
        camera_buttons.addWidget(self.refresh_button)

        self.start_button = QPushButton("启动预览")
        self.start_button.clicked.connect(self._start_preview)
        camera_buttons.addWidget(self.start_button)

        self.stop_button = QPushButton("停止预览")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_preview)
        camera_buttons.addWidget(self.stop_button)

        self.freeze_button = QPushButton("冻结当前帧")
        self.freeze_button.setEnabled(False)
        self.freeze_button.clicked.connect(self._toggle_freeze)
        camera_buttons.addWidget(self.freeze_button)

        controls_layout.addLayout(camera_buttons)

        eye_form = QFormLayout()

        self.left_state_combo = QComboBox()
        self.right_state_combo = QComboBox()

        for combo in (
            self.left_state_combo,
            self.right_state_combo,
        ):
            for state in EyeOpeningState:
                combo.addItem(
                    EYE_STATE_LABELS[state],
                    state.value,
                )

            unknown_index = combo.findData(EyeOpeningState.UNKNOWN.value)
            combo.setCurrentIndex(unknown_index)
            combo.currentIndexChanged.connect(self._state_changed)

        eye_form.addRow(
            "左眼状态：",
            self.left_state_combo,
        )
        eye_form.addRow(
            "右眼状态：",
            self.right_state_combo,
        )

        self.face_proposal_button = QPushButton("框选整脸并建议双眼")

        self.face_proposal_button.setEnabled(False)

        self.face_proposal_button.clicked.connect(self._begin_face_selection)

        eye_form.addRow(
            "",
            self.face_proposal_button,
        )

        self.confirm_proposals_button = QPushButton("确认双眼建议框")

        self.confirm_proposals_button.setEnabled(False)

        self.confirm_proposals_button.clicked.connect(self._confirm_eye_proposals)

        eye_form.addRow(
            "",
            self.confirm_proposals_button,
        )

        self.left_eye_button = QPushButton("框选左眼")
        self.left_eye_button.setEnabled(False)
        self.left_eye_button.clicked.connect(lambda: self._begin_eye_selection(EyeSide.LEFT))
        eye_form.addRow(
            "",
            self.left_eye_button,
        )

        self.right_eye_button = QPushButton("框选右眼")
        self.right_eye_button.setEnabled(False)
        self.right_eye_button.clicked.connect(lambda: self._begin_eye_selection(EyeSide.RIGHT))
        eye_form.addRow(
            "",
            self.right_eye_button,
        )

        self.clear_boxes_button = QPushButton("清除眼框")
        self.clear_boxes_button.setEnabled(False)
        self.clear_boxes_button.clicked.connect(self._clear_eye_boxes)
        eye_form.addRow(
            "",
            self.clear_boxes_button,
        )

        controls_layout.addLayout(eye_form)

        snapshot_layout = QVBoxLayout()

        self.snapshot_button = QPushButton("保存带框快照")
        self.snapshot_button.setEnabled(False)
        self.snapshot_button.clicked.connect(self._save_snapshot)
        snapshot_layout.addWidget(self.snapshot_button)

        self.patient_status = QLabel(f"患者档案：{self._patient_key}")
        self.patient_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        snapshot_layout.addWidget(self.patient_status)

        self.connection_status = QLabel("未连接")
        snapshot_layout.addWidget(self.connection_status)

        self.mode_status = QLabel("—")
        snapshot_layout.addWidget(self.mode_status)

        self.frame_status = QLabel("—")
        snapshot_layout.addWidget(self.frame_status)

        snapshot_layout.addStretch(1)
        controls_layout.addLayout(snapshot_layout)

        root_layout.addWidget(controls_frame)

        self.preview_label = ImageSelectionLabel()
        self.preview_label.setText("摄像头预览尚未启动")
        self.preview_label.setMinimumSize(
            640,
            480,
        )
        self.preview_label.setStyleSheet(
            """
            QLabel {
                background: #15181d;
                color: #d6d9df;
                border: 1px solid #3a4049;
                font-size: 16px;
            }
            """
        )
        self.preview_label.selection_completed.connect(self._finish_eye_selection)
        self.preview_label.selection_cancelled.connect(self._cancel_eye_selection)

        root_layout.addWidget(
            self.preview_label,
            stretch=1,
        )

        self.setCentralWidget(central_widget)
        self.statusBar().showMessage("就绪")

    def _selected_backend(
        self,
    ) -> int | None:
        return _BACKENDS[self.backend_combo.currentText()]

    def _selected_camera_index(self) -> int:
        value = self.camera_combo.currentData()
        return int(value or 0)

    def _selected_eye_state(
        self,
        side: EyeSide,
    ) -> EyeOpeningState:
        combo = self.left_state_combo if side is EyeSide.LEFT else self.right_state_combo

        return EyeOpeningState(combo.currentData())

    def _build_observations(
        self,
    ) -> tuple[EyeObservation, ...]:
        observations = []

        for side, box in self._eye_boxes.items():
            source = self._eye_sources.get(
                side,
                ObservationSource.MANUAL,
            )
            review_status = self._eye_review_statuses.get(
                side,
                (
                    ObservationReviewStatus.MANUAL
                    if source is ObservationSource.MANUAL
                    else (ObservationReviewStatus.PROPOSED)
                ),
            )
            note = (
                "Face-geometry proposal; operator review required."
                if review_status is ObservationReviewStatus.PROPOSED
                else None
            )

            observations.append(
                EyeObservation(
                    side=side,
                    box=box,
                    opening_state=(self._selected_eye_state(side)),
                    source=source,
                    review_status=review_status,
                    note=note,
                )
            )

        return tuple(observations)

    def _update_observations(self) -> None:
        self._controller.set_observations(self._build_observations())

        if self._frozen and self._controller.latest_packet is not None:
            rendered = self._controller.render_latest_frame()
            self._show_frame(rendered)

        self.clear_boxes_button.setEnabled(bool(self._eye_boxes))

        self._update_review_controls()

    def _show_frame(self, frame) -> None:
        qimage = bgr_frame_to_qimage(frame)
        pixmap = QPixmap.fromImage(qimage)

        self.preview_label.set_frame_pixmap(
            pixmap,
            image_width_px=frame.shape[1],
            image_height_px=frame.shape[0],
        )

    def _set_eye_controls_enabled(
        self,
        enabled: bool,
    ) -> None:
        self.face_proposal_button.setEnabled(enabled)
        self.left_eye_button.setEnabled(enabled)
        self.right_eye_button.setEnabled(enabled)
        self.left_state_combo.setEnabled(enabled)
        self.right_state_combo.setEnabled(enabled)

    def _refresh_camera_list(self) -> None:
        if self._controller.running:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            results = probe_cameras(
                self.max_index_spin.value(),
                backend=self._selected_backend(),
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "摄像头扫描失败",
                str(error),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.camera_combo.clear()
        available_count = 0

        for result in results:
            if result.available:
                available_count += 1

            mode = f"{result.width_px or '?'}x{result.height_px or '?'}"
            self.camera_combo.addItem(
                f"Camera {result.index} — {result.status.value} — {mode}",
                result.index,
            )

        self.start_button.setEnabled(self.camera_combo.count() > 0)
        self.statusBar().showMessage(f"发现 {available_count} 个可用摄像头")

    def _start_preview(self) -> None:
        try:
            self._controller.start(
                index=self._selected_camera_index(),
                backend=self._selected_backend(),
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "摄像头启动失败",
                str(error),
            )
            return

        self._frozen = False
        self._eye_boxes.clear()
        self._eye_sources.clear()
        self._eye_review_statuses.clear()
        self._controller.clear_observations()

        width, height, fps = self._controller.reported_mode
        self.mode_status.setText(
            f"{width or '?'}x{height or '?'}; {fps if fps is not None else '?'} FPS"
        )
        self.connection_status.setText("实时采集中")

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.freeze_button.setEnabled(True)
        self.snapshot_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.camera_combo.setEnabled(False)
        self.backend_combo.setEnabled(False)
        self.max_index_spin.setEnabled(False)
        self._set_eye_controls_enabled(False)

        self._timer.start()

    def _read_frame(self) -> None:
        try:
            packet, rendered = self._controller.read_next_frame()
        except Exception as error:
            self._stop_preview()
            QMessageBox.critical(
                self,
                "摄像头读取失败",
                str(error),
            )
            return

        self._show_frame(rendered)
        self.frame_status.setText(
            f"Frame {packet.frame_index}; {packet.width_px}x{packet.height_px}"
        )
        self.snapshot_button.setEnabled(True)

    def _toggle_freeze(self) -> None:
        if not self._controller.running:
            return

        if self._frozen:
            self.preview_label.cancel_selection()
            self._pending_side = None
            self._frozen = False
            self._timer.start()
            self.freeze_button.setText("冻结当前帧")
            self.connection_status.setText("实时采集中")
            self._set_eye_controls_enabled(False)
            self.statusBar().showMessage("已恢复实时预览")
            return

        if self._controller.latest_packet is None:
            return

        self._timer.stop()
        self._frozen = True
        self.freeze_button.setText("恢复实时预览")
        self.connection_status.setText("当前帧已冻结")
        self._set_eye_controls_enabled(True)
        self.statusBar().showMessage("可拖拽框选左右眼")

    def _update_review_controls(
        self,
    ) -> None:
        has_proposed = any(
            status is ObservationReviewStatus.PROPOSED
            for status in self._eye_review_statuses.values()
        )

        self.confirm_proposals_button.setEnabled(self._frozen and has_proposed)

    def _confirm_eye_proposals(
        self,
    ) -> None:
        changed = False

        for side, status in tuple(self._eye_review_statuses.items()):
            if status is ObservationReviewStatus.PROPOSED:
                self._eye_review_statuses[side] = ObservationReviewStatus.CONFIRMED
                changed = True

        if not changed:
            return

        self._update_observations()
        self._update_review_controls()
        self.statusBar().showMessage("双眼建议框已经人工确认")

    def _begin_face_selection(
        self,
    ) -> None:
        if not self._frozen:
            return

        self._pending_side = None
        self._pending_face_selection = True

        try:
            self.preview_label.begin_selection()
        except Exception as error:
            self._pending_face_selection = False
            QMessageBox.warning(
                self,
                "无法开始整脸框选",
                str(error),
            )
            return

        self.statusBar().showMessage("请拖拽框住患者完整面部；系统随后生成双眼建议框")

    def _begin_eye_selection(
        self,
        side: EyeSide,
    ) -> None:
        if not self._frozen:
            return

        self._pending_face_selection = False
        self._pending_side = side

        try:
            self.preview_label.begin_selection()
        except Exception as error:
            self._pending_side = None
            QMessageBox.warning(
                self,
                "无法开始框选",
                str(error),
            )
            return

        side_text = "左眼" if side is EyeSide.LEFT else "右眼"
        self.statusBar().showMessage(f"请在画面中拖拽框选{side_text}")

    def _finish_eye_selection(
        self,
        box: EyeBoundingBox,
    ) -> None:
        if self._pending_face_selection:
            self._pending_face_selection = False
            self._pending_side = None

            packet = self._controller.latest_packet

            if packet is None:
                QMessageBox.warning(
                    self,
                    "没有可用画面",
                    "当前冻结帧不存在。",
                )
                return

            face = FaceDetection(
                x_px=box.x_px,
                y_px=box.y_px,
                width_px=box.width_px,
                height_px=box.height_px,
            )
            proposals = propose_eye_regions_from_face(
                face,
                image_width_px=packet.width_px,
                image_height_px=packet.height_px,
            )

            self._eye_boxes = {observation.side: observation.box for observation in proposals}
            self._eye_sources = {
                observation.side: (ObservationSource.ALGORITHM) for observation in proposals
            }
            self._eye_review_statuses = {
                observation.side: (ObservationReviewStatus.PROPOSED) for observation in proposals
            }

            for combo in (
                self.left_state_combo,
                self.right_state_combo,
            ):
                unknown_index = combo.findData(EyeOpeningState.UNKNOWN.value)

                if unknown_index >= 0:
                    combo.setCurrentIndex(unknown_index)

            self._update_observations()
            self._update_review_controls()

            self.statusBar().showMessage("已根据整脸区域生成双眼建议框；请确认建议框或重新框选单眼")
            return

        side = self._pending_side
        self._pending_side = None

        if side is None:
            return

        previous_source = self._eye_sources.get(side)

        self._eye_boxes[side] = box
        self._eye_sources[side] = ObservationSource.MANUAL
        self._eye_review_statuses[side] = (
            ObservationReviewStatus.CORRECTED
            if previous_source is ObservationSource.ALGORITHM
            else ObservationReviewStatus.MANUAL
        )

        self._update_observations()
        self._update_review_controls()

        self.statusBar().showMessage(
            f"{side.value} eye box: {box.x_px},{box.y_px},{box.width_px}x{box.height_px}"
        )

    def _cancel_eye_selection(self) -> None:
        self._pending_side = None
        self._pending_face_selection = False

    def _state_changed(self) -> None:
        if self._eye_boxes:
            self._update_observations()

    def _clear_eye_boxes(self) -> None:
        self.preview_label.cancel_selection()
        self._pending_side = None
        self._eye_boxes.clear()
        self._eye_sources.clear()
        self._eye_review_statuses.clear()
        self._controller.clear_observations()
        self.clear_boxes_button.setEnabled(False)

        if self._frozen and self._controller.latest_packet is not None:
            self._show_frame(self._controller.render_latest_frame())

    def _save_snapshot(self) -> None:
        observations = self._build_observations()

        if any(
            observation.review_status is ObservationReviewStatus.PROPOSED
            for observation in observations
        ):
            QMessageBox.warning(
                self,
                "建议眼框尚未确认",
                "仍有尚未确认的建议眼框。请点击“确认双眼建议框”，或重新人工框选对应眼睛。",
            )
            return

        if not observations:
            QMessageBox.warning(
                self,
                "尚未框选眼睛",
                "请先冻结画面并至少框选一只眼睛。",
            )
            return

        packet = self._controller.latest_packet

        if packet is None:
            QMessageBox.warning(
                self,
                "没有可保存画面",
                "摄像头尚未产生有效图像。",
            )
            return

        camera_index = self._selected_camera_index()
        backend_name = self._controller.backend_name
        frame_key = build_camera_frame_key(
            packet=packet,
            camera_index=camera_index,
            backend_name=backend_name,
        )

        if self._frame_save_guard.was_saved(frame_key):
            QMessageBox.information(
                self,
                "当前帧已经保存",
                "该冻结帧已经生成过观察样本。请恢复实时预览并重新冻结一帧。",
            )
            return

        dataset_directory = eye_observation_dataset_directory(self._patient_key)

        try:
            sample_paths = next_eye_sample_paths(dataset_directory)

            self._controller.save_snapshot(
                sample_paths.overlay_path,
                rendered=True,
            )
            self._controller.save_snapshot(
                sample_paths.raw_path,
                rendered=False,
            )

            crops = export_eye_crops(
                packet.image,
                observations,
                output_directory=dataset_directory,
                sample_stem=sample_paths.stem,
            )

            record = build_eye_observation_record(
                frame_key=frame_key,
                patient_key=self._patient_key,
                packet=packet,
                camera_index=camera_index,
                backend_name=backend_name,
                raw_image_filename=(sample_paths.raw_path.name),
                overlay_image_filename=(sample_paths.overlay_path.name),
                observations=observations,
                crops=crops,
            )

            write_eye_observation_record(
                record,
                sample_paths.record_path,
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "保存失败",
                str(error),
            )
            return

        self._frame_save_guard.mark_saved(frame_key)

        self.statusBar().showMessage(
            f"已保存患者档案 "
            f"{self._patient_key} 的样本 "
            f"{sample_paths.index:04d}；"
            f"目录：{dataset_directory}"
        )

    def _stop_preview(self) -> None:
        self._timer.stop()
        self.preview_label.cancel_selection()
        self._controller.stop()

        self._frozen = False
        self._pending_side = None
        self._eye_boxes.clear()
        self._eye_sources.clear()
        self._eye_review_statuses.clear()
        self.preview_label.clear_frame()
        self.preview_label.setText("摄像头预览尚未启动")

        self.connection_status.setText("未连接")
        self.mode_status.setText("—")
        self.frame_status.setText("—")
        self.freeze_button.setText("冻结当前帧")

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.freeze_button.setEnabled(False)
        self.snapshot_button.setEnabled(False)
        self.refresh_button.setEnabled(True)
        self.camera_combo.setEnabled(True)
        self.backend_combo.setEnabled(True)
        self.max_index_spin.setEnabled(True)
        self._set_eye_controls_enabled(False)
        self.clear_boxes_button.setEnabled(False)

    def closeEvent(
        self,
        event: QCloseEvent,
    ) -> None:
        """Release camera hardware before closing."""
        self._timer.stop()
        self._controller.stop()
        event.accept()
