from datetime import UTC, datetime
from pathlib import Path

from pytestqt.qtbot import QtBot

import oculidoc.ui.opoin_thesis as opoin_thesis_module
from oculidoc.config import Settings
from oculidoc.devices.contracts import DeviceTimestamp, EyeTrackerSample
from oculidoc.devices.preflight import GazePreflightResult
from oculidoc.ui.opoin_thesis import (
    TRACK_STATUS_EXECUTABLE,
    OpoinThesisCanvas,
    OpoinThesisDialog,
    eye_position_diagnostic_text,
    find_track_status_executable,
    self_check_supports_current_eye_position,
)


def _sample(
    left: tuple[float, float, float] | None,
    right: tuple[float, float, float] | None,
) -> EyeTrackerSample:
    return EyeTrackerSample(
        timestamp=DeviceTimestamp(
            sequence=0,
            monotonic_timestamp_ns=1,
            utc_timestamp=datetime.now(UTC),
        ),
        gaze_x_normalized=None,
        gaze_y_normalized=None,
        left_eye_valid=left is not None,
        right_eye_valid=right is not None,
        left_eye_position_normalized=left,
        right_eye_position_normalized=right,
    )


def test_opoin_thesis_tracks_eye_positions_without_metrics(qtbot: QtBot) -> None:
    canvas = OpoinThesisCanvas()
    qtbot.addWidget(canvas)

    canvas.consume_sample(
        _sample(
            (-0.2, 0.4, 0.3),
            (1.2, 0.6, 0.7),
        )
    )

    assert canvas.eye_positions == (
        (-0.2, 0.4, 0.3),
        (1.2, 0.6, 0.7),
    )
    assert not hasattr(canvas, "valid_ratio")


def _preflight(
    *,
    source: str = "tobii_stream_engine",
    observed: tuple[str, ...] = (),
    capability_notes: tuple[str, ...] = (),
) -> GazePreflightResult:
    return GazePreflightResult(
        source=source,
        device_name="测试传感器",
        device_url=None,
        library_path=r"D:\JustNeedToSee\tobii_stream_engine.dll",
        duration_seconds=3.0,
        sample_count=90,
        valid_sample_count=90,
        sample_rate_hz=30.0,
        valid_ratio=1.0,
        minimum_valid_ratio=0.35,
        passed=True,
        error=None,
        updated_at_utc="2026-07-23T00:00:00+00:00",
        observed_capabilities=observed,
        capability_notes=capability_notes,
    )


def test_opoin_thesis_uses_capability_observed_by_matching_self_check() -> None:
    settings = Settings(environment="test", gaze_source="tobii_stream_engine")

    assert self_check_supports_current_eye_position(
        settings,
        _preflight(observed=("gaze_point", "eye_position")),
    )
    assert not self_check_supports_current_eye_position(
        settings,
        _preflight(observed=("gaze_point",)),
    )
    assert not self_check_supports_current_eye_position(
        settings,
        _preflight(source="tobii_hospital_bridge", observed=("eye_position",)),
    )

    diagnostic = eye_position_diagnostic_text(
        settings,
        _preflight(
            observed=("gaze_point",),
            capability_notes=("左右眼三维眼位：设备或当前 Stream Engine 不支持",),
        ),
    )
    assert "不支持" in diagnostic
    assert "JustNeedToSee" in diagnostic
    assert "组合注视有效率：100%" in diagnostic
    assert "Python：" in diagnostic
    assert "不能据此补造瞳孔或逐眼数据" in diagnostic


def test_opoin_thesis_uses_existing_evidence_without_forcing_new_self_check(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / TRACK_STATUS_EXECUTABLE
    executable.write_bytes(b"test executable")
    monkeypatch.setattr(opoin_thesis_module.sys, "platform", "win32")
    monkeypatch.setattr(
        opoin_thesis_module,
        "find_track_status_executable",
        lambda _settings: executable,
    )
    settings = Settings(environment="test", gaze_source="tobii_stream_engine")

    without_evidence = OpoinThesisDialog(settings)
    qtbot.addWidget(without_evidence)
    assert without_evidence.compatibility_button.isEnabled()
    assert not without_evidence._auto_open_compatibility
    assert "不会据此推断设备能力" in without_evidence.diagnostic_label.text()

    unsupported = OpoinThesisDialog(
        settings,
        preflight_result=_preflight(
            observed=("gaze_point",),
            capability_notes=("左右眼三维眼位：不支持（driver status 3）",),
        ),
    )
    qtbot.addWidget(unsupported)
    assert unsupported._auto_open_compatibility
    assert "driver status 3" in unsupported.diagnostic_label.text()
    assert str(executable) in unsupported.diagnostic_label.text()


def test_opoin_thesis_finds_legacy_track_status_next_to_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(opoin_thesis_module.shutil, "which", lambda _name: None)
    executable = tmp_path / "EyePosition" / TRACK_STATUS_EXECUTABLE
    executable.parent.mkdir()
    executable.write_bytes(b"test executable")
    settings = Settings(
        environment="test",
        just_need_to_see_root=tmp_path / "JustNeedToSee",
    )

    assert opoin_thesis_module.TRACK_STATUS_ARGUMENT == "--showtrackstatus"
    assert find_track_status_executable(settings) == executable.resolve()
