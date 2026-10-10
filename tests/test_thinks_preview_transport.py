"""The diagnostic camera feed must not crowd out calibration responses."""

import json
from types import SimpleNamespace

from oculidoc.devices.thinks_tracker import ThinksConfig, ThinksProcess
from oculidoc.vendor_helpers.thinks_tracker_worker import PREFIX


def test_preview_backpressure_keeps_only_latest_frame(tmp_path):
    process = ThinksProcess(
        ThinksConfig(dll_path=tmp_path / "unused.dll", screen_physical=(20, 350, 345, 215, 60)),
        tmp_path,
        {},
    )
    packets = [
        {"event": "ready"},
        {"event": "preview", "frame": 1},
        {"event": "preview", "frame": 2},
        {"event": "point_done", "count": 1},
        {"event": "preview", "frame": 3},
    ]
    fake = SimpleNamespace(stdout=[PREFIX + json.dumps(packet) for packet in packets])
    process._read(fake)
    assert process.control_queue.qsize() == 2
    assert process.control_queue.get_nowait() == {"event": "ready"}
    assert process.control_queue.get_nowait() == {"event": "point_done", "count": 1}
    assert process.preview_queue.qsize() == 1
    assert process.preview_queue.get_nowait() == {"event": "preview", "frame": 3}
