import ctypes
import json
from pathlib import Path
from time import monotonic_ns

import pytest

from oculidoc.config import GazeDeviceConfig, GazeDeviceConfigStore, Settings
from oculidoc.devices.auto_detect import AutoDetectEyeTrackerDevice
from oculidoc.devices.contracts import DeviceState
from oculidoc.devices.errors import DeviceConnectionError
from oculidoc.devices.simulated import SimulatedEyeTrackerDevice
from oculidoc.devices.thinks_tracker import (
    ThinksConfig,
    ThinksProcess,
    ThinksTrackerDevice,
    _worker_command,
    read_thinks_config,
)
from oculidoc.vendor_helpers.thinks_tracker_worker import (
    Bbox,
    Device,
    NativeTracker,
    Output,
    Point,
    downsample_gray,
    gaze_packet,
    preview_bbox,
    profile_key,
)


def config_file(tmp_path: Path) -> Path:
    dll = tmp_path / "ThinksTracker.dll"
    dll.write_bytes(b"not an SDK")
    path = tmp_path / "thinks.json"
    path.write_text(
        ThinksConfig(
            dll_path=dll, screen_physical=(20, 25, 354, 200, 60), geometry_confirmed=True
        ).model_dump_json(),
        encoding="utf-8",
    )
    return path


def test_sdk_abi_matches_msvc_x64_header():
    assert ctypes.sizeof(Device) == 260
    assert ctypes.sizeof(Output) == 256
    assert Output.pts.offset == 64
    assert Output.left_eye.offset == 72
    assert Output.merge.offset == 176
    assert Output.distance.offset == 248


def test_preview_is_bounded_grayscale_and_boxes_use_vendor_demo_scale():
    raw = bytes((x + y) % 256 for y in range(360) for x in range(640))
    pixels, width, height = downsample_gray(raw, 640, 360)
    assert (width, height) == (320, 180)
    assert len(pixels) == width * height
    assert pixels[0] == raw[0]
    assert pixels[width + 1] == raw[2 * 640 + 2]
    assert preview_bbox((400, 200, 160, 80), 640, 360, width, height) == [50, 25, 20, 10]
    assert preview_bbox(None, 640, 360, width, height) is None
    assert preview_bbox((0, 0, 0, 80), 640, 360, width, height) is None


def test_native_camera_callback_rate_limits_and_replaces_old_frame():
    from queue import Queue

    native = NativeTracker.__new__(NativeTracker)
    native.frames = 0
    native.preview_enabled = True
    native.last_preview_capture = 0.0
    native.preview_frames = Queue(maxsize=1)
    first = ctypes.create_string_buffer(b"abcd")
    second = ctypes.create_string_buffer(b"wxyz")
    native._on_camera(None, first, 2, 2, 4)
    assert native.preview_frames.qsize() == 1
    native._on_camera(None, second, 2, 2, 4)
    assert native.frames == 2
    assert native.preview_frames.get_nowait()[0] == b"abcd"
    native.preview_frames.put((b"abcd", 2, 2))
    native.last_preview_capture = 0.0
    native._on_camera(None, second, 2, 2, 4)
    assert native.preview_frames.get_nowait()[0] == b"wxyz"


def test_preview_packets_are_volatile_and_pause_hides_stale_eye_boxes(monkeypatch):
    import base64
    import zlib
    from queue import Queue
    from threading import Event, Lock, Thread

    native = NativeTracker.__new__(NativeTracker)
    native.preview_stop = Event()
    native.preview_frames = Queue(maxsize=1)
    native.preview_enabled = True
    native.eye_lock = Lock()
    native.eye_detection_state = "paused"
    native.eye_boxes = ((400, 200, 160, 80), None)
    native.eye_boxes_at = 0.0
    delivered = Event()
    packets = []

    def capture(packet):
        packets.append(packet)
        delivered.set()

    monkeypatch.setattr("oculidoc.vendor_helpers.thinks_tracker_worker.emit", capture)
    runner = Thread(target=native._preview_loop, daemon=True)
    runner.start()
    native.preview_frames.put((bytes([128]) * (960 * 540), 960, 540))
    assert delivered.wait(timeout=2)
    native.preview_stop.set()
    runner.join(timeout=1)
    assert packets[0]["event"] == "preview"
    assert (packets[0]["width"], packets[0]["height"]) == (320, 180)
    assert len(zlib.decompress(base64.b64decode(packets[0]["image_base64"]))) == 320 * 180
    assert packets[0]["left_bbox"] is None and packets[0]["right_bbox"] is None
    assert packets[0]["eye_detection"] == "paused"


def test_eye_detection_stops_before_point_capture(monkeypatch):
    from threading import Event, Lock
    from types import SimpleNamespace

    entered, stopped = Event(), Event()
    events = []

    def start(_handle):
        entered.set()
        stopped.wait(timeout=2)
        return 0

    def stop(_handle):
        stopped.set()
        return 0

    monkeypatch.setattr(
        "oculidoc.vendor_helpers.thinks_tracker_worker.emit", lambda packet: events.append(packet)
    )
    native = NativeTracker.__new__(NativeTracker)
    native.dll = SimpleNamespace(
        ThinksTracker_startEyeDetect=start, ThinksTracker_stopEyeDetect=stop
    )
    native.handle = 1
    native.eye_lock = Lock()
    native.eye_thread = None
    native.eye_detection_state = "off"
    native.eye_boxes = (None, None)
    native.eye_boxes_at = 0.0
    native.resume_eye_detection()
    assert entered.wait(timeout=1)
    native._on_eye_detect(
        None,
        ctypes.pointer(Bbox(400, 200, 160, 80)),
        None,
    )
    assert native.eye_boxes[0] == (400, 200, 160, 80)
    native.pause_eye_detection()
    assert stopped.is_set() and native.eye_thread is None
    assert native.eye_boxes == (None, None)
    assert [packet["event"] for packet in events] == [
        "eye_detection_resumed",
        "eye_detection_paused",
    ]


def test_valid_merge_and_no_unproven_units():
    output = Output(left_valid=1, right_valid=1, merge_valid=1, merge=Point(0.25, 0.75))
    output.left_eye.pupil_width = 40
    output.pts = 1234
    packet = gaze_packet(output)
    assert packet["x"] == 0.25 and packet["y"] == 0.75
    assert packet["left_valid"] and packet["right_valid"]
    assert "source_timestamp_ns" not in packet and "pupil_diameter_mm" not in packet


@pytest.mark.parametrize("x,y", [(float("nan"), 0.4), (float("inf"), 0.4), (-0.1, 0.5), (0.5, 1.1)])
def test_invalid_screen_coordinates_never_become_usable(x, y):
    packet = gaze_packet(Output(left_valid=1, merge_valid=1, merge=Point(x, y)))
    assert packet["x"] is None and not packet["left_valid"]


def test_pupil_detection_does_not_imply_valid_gaze():
    packet = gaze_packet(Output(left_info=1, right_info=1, merge_valid=1, merge=Point(0.5, 0.5)))
    assert not packet["left_valid"] and packet["x"] is None


def test_single_eye_and_binocular_fallback():
    output = Output(left_valid=1, left=Point(0.2, 0.4))
    assert gaze_packet(output)["x"] == pytest.approx(0.2)
    output.right_valid = 1
    output.right = Point(0.8, 0.6)
    assert gaze_packet(output)["x"] == pytest.approx(0.5)


def test_missing_dll_and_bad_mode_are_actionable(tmp_path):
    path = config_file(tmp_path)
    (tmp_path / "ThinksTracker.dll").unlink()
    with pytest.raises(DeviceConnectionError, match="找不到"):
        read_thinks_config(path)
    with pytest.raises(ValueError, match="不支持"):
        ThinksConfig(dll_path="x", screen_physical=(20, 25, 354, 200, 60), resolution="300")


@pytest.mark.parametrize(
    "status,operation", [(-1, "初始化"), (-2, "初始化"), (-9, "授权检查"), (-6, "完成校准")]
)
def test_vendor_failures_are_not_success(status, operation):
    with pytest.raises(RuntimeError, match=str(status)):
        NativeTracker.check(status, operation)


def test_missing_required_symbol_is_detected():
    from types import SimpleNamespace

    native = NativeTracker.__new__(NativeTracker)
    native.dll = SimpleNamespace()
    with pytest.raises(AttributeError, match="getSystemDevicesList"):
        native._bind()


@pytest.mark.parametrize("finished_count,success", [(1, True), (0, False), (2, False)])
def test_calibration_requires_matching_finished_callback(finished_count, success):
    from threading import Event
    from types import SimpleNamespace

    native = NativeTracker.__new__(NativeTracker)
    native.handle = 1
    native.point_finished = Event()
    native.finished_count = 0
    arguments = []

    def collect(*args):
        arguments.append(args)
        native._on_point_finished(None, finished_count)
        return 0

    native.dll = SimpleNamespace(ThinksTracker_startCalibration=collect)
    if success:
        native.calibrate_point(0.1, 0.1, 1)
        assert arguments[0][-1] == b"./"
    else:
        with pytest.raises(RuntimeError, match="未确认"):
            native.calibrate_point(0.1, 0.1, 1)


def test_calibration_reconfigures_vendor_for_all_nine_points():
    from types import SimpleNamespace

    calls = []
    native = NativeTracker.__new__(NativeTracker)
    native.handle = 1
    native.physical = (20, 350, 345, 215, 60)
    native.dll = SimpleNamespace(ThinksTracker_reConfig=lambda *args: calls.append(args) or 0)
    native.begin_calibration()
    assert calls == [(1, 0, 9, 9, native.physical)]


def test_calibration_bound_to_person_screen_geometry_and_device():
    config = {"resolution": "1920x1080x120", "screen_physical": [20, 25, 354, 200, 60]}
    identity = {"patient_id": "synthetic-A", "screen": ["display", 1920, 1080]}
    key = profile_key(config, identity, "device-A")
    assert key != profile_key(config, {**identity, "patient_id": "synthetic-B"}, "device-A")
    assert key != profile_key(config, {**identity, "screen": ["other", 1920, 1080]}, "device-A")
    assert key != profile_key(config, identity, "device-B")
    assert key != profile_key({"screen_physical": [10, 25, 354, 200, 60]}, identity, "device-A")
    assert key == profile_key(
        {**config, "license_file": "renewed.lic", "python_executable": "another.exe"},
        identity,
        "device-A",
    )
    assert key != profile_key({**config, "resolution": "1920x608x300"}, identity, "device-A")


def test_packaged_app_runs_isolated_worker_without_external_python(monkeypatch, tmp_path):
    config = read_thinks_config(config_file(tmp_path))
    app_executable = tmp_path / "OculiDoC.exe"
    monkeypatch.setattr("oculidoc.devices.thinks_tracker.sys.frozen", True, raising=False)
    monkeypatch.setattr("oculidoc.devices.thinks_tracker.sys.executable", str(app_executable))
    assert _worker_command(config) == [
        str(app_executable),
        "--thinks-worker",
    ]


def test_unconfirmed_geometry_prevents_launch(tmp_path):
    config = read_thinks_config(config_file(tmp_path)).model_copy(
        update={"geometry_confirmed": False}
    )
    process = ThinksProcess(config, tmp_path, {})
    with pytest.raises(DeviceConnectionError, match="安装参数"):
        process.start()
    assert process.process is None


def test_real_child_missing_symbols_fails_and_is_reaped(tmp_path):
    process = ThinksProcess(read_thinks_config(config_file(tmp_path)), tmp_path / "runtime", {})
    with pytest.raises(DeviceConnectionError):
        process.start("probe")
    assert process.process is None


class FakeProcess:
    def __init__(self, *_args):
        self.closed = False

    def start(self):
        return {"event": "ready", "vid": 0x1D6B, "pid": 0x201, "name": "Uvc Camera", "fps": 120}

    def receive(self):
        return {
            "event": "sample",
            "received_ns": monotonic_ns(),
            "x": 0.25,
            "y": 0.75,
            "left_valid": True,
            "right_valid": True,
        }

    def close(self):
        self.closed = True


def test_source_lifecycle_and_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr("oculidoc.devices.thinks_tracker.ThinksProcess", FakeProcess)
    device = ThinksTrackerDevice(
        config_file(tmp_path), tmp_path, "synthetic", ("display", 1920, 1080)
    )
    device.connect()
    device.start_stream()
    sample = device.read_sample()
    assert sample.gaze_valid and sample.gaze_x_normalized == 0.25
    assert sample.timestamp.source_timestamp_ns is None
    assert sample.left_pupil_diameter_mm is None
    assert not device.info.is_simulated and "120 Hz" in device.info.model
    device.stop_stream()
    assert device.state == DeviceState.CONNECTED
    device.start_stream()
    assert device.read_sample().gaze_valid
    device.disconnect()
    assert device.state == DeviceState.DISCONNECTED


def test_config_roundtrip_and_factory(qapp, tmp_path):
    from oculidoc.tasks.gaze_stream import create_eye_tracker

    settings = Settings(
        data_dir=tmp_path, gaze_source="thinks_tracker", thinks_config_path=tmp_path / "thinks.json"
    )
    store = GazeDeviceConfigStore.for_settings(settings)
    store.save(GazeDeviceConfig.from_settings(settings))
    restored = store.load(GazeDeviceConfig.from_settings(settings)).apply(settings)
    assert restored.thinks_config_path == settings.thinks_config_path
    assert isinstance(create_eye_tracker(restored), ThinksTrackerDevice)
    assert json.loads(store.path.read_text())["config"]["gaze_source"] == "thinks_tracker"


def test_settings_do_not_offer_other_vendors_calibration(qtbot, tmp_path):
    from oculidoc.ui.device_settings import DeviceSettingsDialog

    settings = Settings(data_dir=tmp_path, gaze_source="thinks_tracker")
    dialog = DeviceSettingsDialog(settings, GazeDeviceConfigStore.for_settings(settings))
    qtbot.addWidget(dialog)
    assert dialog.thinks_calibrate_button.isEnabled()
    assert not dialog.open_tobii_button.isEnabled()
    assert not dialog.opoin_thesis_button.isEnabled()
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("tobii_stream_engine"))
    assert not dialog.thinks_calibrate_button.isEnabled()
    assert dialog.open_tobii_button.isEnabled()


def test_failure_can_fall_through_to_another_hardware_adapter(tmp_path):
    # The existing autodetect contract allows another real source after a clean failure.
    class OtherHardware(SimulatedEyeTrackerDevice):
        @property
        def info(self):
            from dataclasses import replace

            return replace(super().info, is_simulated=False)

    auto = AutoDetectEyeTrackerDevice(
        (
            lambda: ThinksTrackerDevice(
                tmp_path / "missing.json", tmp_path, "x", ("d", 1920, 1080)
            ),
            lambda: OtherHardware(),
        )
    )
    auto.connect()
    auto.start_stream()
    assert auto.read_sample().gaze_valid
    auto.disconnect()
