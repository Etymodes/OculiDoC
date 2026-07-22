import json
from pathlib import Path

import pytest

from oculidoc.config import Settings
from oculidoc.devices.contracts import DeviceState
from oculidoc.devices.gaze_collect_legacy import GazeCollectLegacyDevice
from oculidoc.devices.just_need_to_see_bundle import JustNeedToSeeBundleDevice
from oculidoc.tasks.gaze_stream import create_eye_tracker


def test_reads_hpf_pixel_sample(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("oculidoc.devices.gaze_collect_legacy._is_windows", lambda: True)
    path = tmp_path / "question" / "1_gaze.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps([{"timestamp_us": 123, "validity": 1, "x": 960, "y": 540}]),
        encoding="utf-8",
    )
    device = GazeCollectLegacyDevice(json_root=tmp_path)
    device.connect()
    assert device.state is DeviceState.CONNECTED
    device.start_stream()
    # start_stream ignores existing records; append one as HPFMediaPlayer would.
    path.write_text(
        json.dumps([
            {"timestamp_us": 123, "validity": 1, "x": 960, "y": 540},
            {"timestamp_us": 124, "validity": 1, "x": 480, "y": 270},
        ]),
        encoding="utf-8",
    )
    sample = device.read_sample()
    assert sample.gaze_x_normalized == pytest.approx(0.25)
    assert sample.gaze_y_normalized == pytest.approx(0.25)
    assert sample.gaze_valid
    assert sample.timestamp.source_timestamp_ns == 12_400


def test_rejects_out_of_screen_hpf_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("oculidoc.devices.gaze_collect_legacy._is_windows", lambda: True)
    path = tmp_path / "1_gaze.json"
    path.write_text("[]", encoding="utf-8")
    device = GazeCollectLegacyDevice(
        json_root=tmp_path,
        screen_width_px=100,
        screen_height_px=100,
    )
    device.connect()
    device.start_stream()
    path.write_text(
        json.dumps([{"timestamp_us": 1, "validity": 1, "x": 101, "y": 50}]),
        encoding="utf-8",
    )

    sample = device.read_sample()

    assert sample.gaze_valid is False
    assert sample.gaze_x_normalized is None


def test_factory_exposes_both_explicit_legacy_sources(tmp_path: Path) -> None:
    gaze_collect = create_eye_tracker(
        Settings(
            gaze_source="gaze_collect_legacy",
            gaze_collect_json_root=tmp_path,
            gaze_collect_player_executable=tmp_path / "HPFMediaPlayer.exe",
        )
    )
    assert isinstance(gaze_collect, GazeCollectLegacyDevice)
    assert gaze_collect.json_root == tmp_path.resolve()
    assert gaze_collect.player_executable is None

    just_need_to_see = create_eye_tracker(
        Settings(
            gaze_source="just_need_to_see_bundle",
            just_need_to_see_root=tmp_path,
        )
    )
    assert isinstance(just_need_to_see, JustNeedToSeeBundleDevice)
    assert just_need_to_see.bundle_root == tmp_path.resolve()
