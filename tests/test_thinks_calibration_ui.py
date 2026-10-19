"""The ThinksTracker eye preview stays transient and clear of calibration targets."""

import base64
import zlib

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QMessageBox

from oculidoc.config import Settings
from oculidoc.ui.thinks_calibration import POINTS, ThinksCalibrationDialog, preview_panel_rect


@pytest.mark.parametrize("width,height", [(1920, 1080), (1366, 768), (800, 600)])
def test_preview_avoids_all_nine_targets(width: int, height: int) -> None:
    for target in POINTS:
        panel = preview_panel_rect(width, height, target)
        point = QPoint(round(target[0] * width), round(target[1] * height))
        assert not panel.adjusted(-20, -20, 20, 20).contains(point)
        assert panel.left() >= 0 and panel.right() < width
        assert panel.top() >= 0 and panel.bottom() < height


def test_preview_decodes_gray_frame_and_clears_boxes_when_detection_pauses(qtbot) -> None:
    dialog = ThinksCalibrationDialog(Settings())
    qtbot.addWidget(dialog)
    width, height = 16, 9
    pixels = bytes(range(width * height))
    dialog._packet(
        {
            "event": "preview",
            "image_base64": base64.b64encode(zlib.compress(pixels)).decode("ascii"),
            "width": width,
            "height": height,
            "left_bbox": [2, 2, 5, 4],
            "right_bbox": [10, 2, 5, 4],
            "eye_detection": "running",
        }
    )
    assert dialog._preview_image is not None
    assert dialog._preview_image.size().width() == width
    assert dialog._preview_image.pixelColor(2, 2).red() == pixels[2 + 2 * width]
    assert dialog._preview_boxes == {"left": (2, 2, 5, 4), "right": (10, 2, 5, 4)}

    dialog._packet({"event": "eye_detection_paused"})
    assert dialog._preview_boxes == {}
    assert dialog._preview_detection_state == "paused"

    dialog._packet({"event": "eye_detection_resumed"})
    assert dialog._preview_detection_state == "running"

    dialog._preview_last_at -= 2
    dialog._expire_preview()
    assert dialog._preview_image is None
    assert dialog._preview_boxes == {}


def test_saved_result_records_exact_file_and_accepts(qtbot, tmp_path) -> None:
    dialog = ThinksCalibrationDialog(Settings(data_dir=tmp_path))
    qtbot.addWidget(dialog)
    path = tmp_path / "runtime/thinks_tracker/calibration/profile.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"calibration")

    dialog._packet({"event": "saved", "calibration_path": str(path)})

    assert dialog.saved_path == path.resolve()
    assert dialog.result() == QDialog.DialogCode.Accepted


@pytest.mark.parametrize("state", ["missing", "empty", "outside", "no_path"])
def test_saved_result_does_not_claim_success_without_valid_file(
    qtbot, tmp_path, monkeypatch, state
):
    dialog = ThinksCalibrationDialog(Settings(data_dir=tmp_path))
    qtbot.addWidget(dialog)
    failures = []
    monkeypatch.setattr(dialog, "_fail", failures.append)
    path = tmp_path / "runtime/thinks_tracker/calibration/profile.dat"
    path.parent.mkdir(parents=True)
    if state == "empty":
        path.write_bytes(b"")
    elif state == "outside":
        path = tmp_path / "unrelated.dat"
        path.write_bytes(b"unrelated")

    dialog._packet(
        {"event": "saved", "calibration_path": None if state == "no_path" else str(path)}
    )

    assert failures
    assert dialog.saved_path is None
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_completion_message_shows_saved_path_and_can_open_folder(qtbot, tmp_path, monkeypatch):
    from oculidoc.ui import thinks_calibration

    dialog = ThinksCalibrationDialog(Settings(data_dir=tmp_path))
    qtbot.addWidget(dialog)
    dialog.saved_path = tmp_path / "runtime/thinks_tracker/calibration/profile.dat"
    observed = []
    opened = []

    def choose_folder(message):
        observed.append(message.informativeText())
        for button in message.buttons():
            if message.buttonRole(button) == QMessageBox.ButtonRole.ActionRole:
                button.click()
                break
        return 0

    monkeypatch.setattr(QMessageBox, "exec", choose_folder)
    monkeypatch.setattr(
        thinks_calibration.QDesktopServices, "openUrl", lambda url: opened.append(url) or True
    )
    dialog._show_saved_result()

    assert str(dialog.saved_path) in observed[0]
    assert "自检" in observed[0]
    assert opened[0].toLocalFile() == str(dialog.saved_path.parent).replace("\\", "/")


def set_eye_preview(dialog):
    from time import monotonic

    dialog._preview_image = QImage(16, 9, QImage.Format.Format_Grayscale8)
    dialog._preview_detection_state = "running"
    dialog._preview_last_at = monotonic()
    dialog._preview_boxes = {"left": (1, 1, 4, 4), "right": (8, 1, 4, 4)}


def test_outline_requires_fresh_both_eye_detection(qtbot):
    dialog = ThinksCalibrationDialog(Settings())
    qtbot.addWidget(dialog)
    assert not dialog._eyes_detected()
    set_eye_preview(dialog)
    assert dialog._eyes_detected()
    dialog._preview_boxes["right"] = None
    assert not dialog._eyes_detected()
    set_eye_preview(dialog)
    dialog._preview_last_at -= 1
    assert not dialog._eyes_detected()
    set_eye_preview(dialog)
    dialog._packet({"event": "eye_detection_paused"})
    assert not dialog._eyes_detected()


def test_timeout_stays_on_current_point_and_retries_without_modal(qtbot, monkeypatch):
    dialog = ThinksCalibrationDialog(Settings())
    qtbot.addWidget(dialog)
    sent = []
    failures = []
    monkeypatch.setattr(dialog, "_send", sent.append)
    monkeypatch.setattr(dialog, "_fail", failures.append)
    dialog._index = 3
    dialog._busy = True
    dialog._point_in_flight = True
    dialog._packet({"event": "point_retry", "count": 4, "status": -8})
    assert dialog._index == 3
    assert dialog._collect_timer.isActive()
    assert not dialog._closed
    assert not failures
    dialog._collect_timer.stop()
    set_eye_preview(dialog)
    dialog._collect_point()
    dialog._collect_point()
    assert sent == [{"command": "point", "point": POINTS[3]}]
    dialog._packet({"event": "point_done", "count": 4})
    assert dialog._point_confirmed
    assert dialog._index == 3
    dialog._advance_timer.stop()
    dialog._advance_point()
    assert dialog._index == 4


@pytest.mark.parametrize("preview", ["missing", "single_eye", "stale", "off", "both"])
def test_preview_is_informational_and_never_blocks_sdk_collection(qtbot, monkeypatch, preview):
    dialog = ThinksCalibrationDialog(Settings())
    qtbot.addWidget(dialog)
    sent = []
    monkeypatch.setattr(dialog, "_send", sent.append)
    dialog._preview_detection_state = "running"
    if preview != "missing":
        set_eye_preview(dialog)
    if preview == "single_eye":
        dialog._preview_boxes["right"] = None
    elif preview == "stale":
        dialog._preview_last_at -= 2
    elif preview == "off":
        dialog._preview_detection_state = "off"
    dialog._collect_point()
    dialog._collect_point()
    assert sent == [{"command": "point", "point": POINTS[0]}]


def test_cancel_stops_pending_retry(qtbot, monkeypatch):
    dialog = ThinksCalibrationDialog(Settings())
    qtbot.addWidget(dialog)
    sent = []
    monkeypatch.setattr(dialog, "_send", sent.append)
    dialog._point_in_flight = True
    dialog._packet({"event": "point_retry", "count": 1, "status": -8})
    assert dialog._collect_timer.isActive()
    dialog.reject()
    assert not dialog._collect_timer.isActive()
    dialog._collect_point()
    assert not sent


def test_only_nine_confirmed_points_trigger_save(qtbot, monkeypatch):
    dialog = ThinksCalibrationDialog(Settings())
    qtbot.addWidget(dialog)
    sent = []
    monkeypatch.setattr(dialog, "_send", sent.append)
    dialog._packet({"event": "ready"})
    qtbot.keyClick(dialog, Qt.Key.Key_Space)
    for index in range(9):
        dialog._collect_timer.stop()
        dialog._collect_point()
        dialog._packet({"event": "point_done", "count": index + 1})
        dialog._packet({"event": "point_done", "count": index + 1})
        assert dialog._index == index
        dialog._advance_timer.stop()
        dialog._advance_point()
    assert sent == [{"command": "point", "point": point} for point in POINTS] + [
        {"command": "save"}
    ]
