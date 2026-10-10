"""The ThinksTracker eye preview stays transient and clear of calibration targets."""

import base64
import zlib

import pytest
from PySide6.QtCore import QPoint

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
