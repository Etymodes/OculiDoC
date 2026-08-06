"""End-to-end task, report, process, and patient-safety tests."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

import oculidoc.__main__ as main_module
import oculidoc.signal_tasks.__main__ as signal_main_module
from oculidoc.application import RegisterPatientRequest
from oculidoc.application.signal_report import write_signal_report
from oculidoc.application.signal_task_session import (
    create_signal_task_launch,
    finalize_signal_task_launch,
)
from oculidoc.domain.experiment_session import ExperimentSessionStatus, SessionArtifactKind
from oculidoc.infrastructure.database import initialize_database
from oculidoc.process_launch import signal_task_process_command
from oculidoc.signal_tasks.config import SignalTaskConfig, SignalTaskKind
from oculidoc.signal_tasks.runner import run_signal_task
from oculidoc.signals.models import SignalSourceKind
from oculidoc.signals.profile import PatientSignalProfileStore
from oculidoc.signals.snapshot import SessionSignalSnapshot


def _ssvep_config() -> SignalTaskConfig:
    return SignalTaskConfig(
        task_kind=SignalTaskKind.SSVEP_FOUR_TARGET,
        source_kind=SignalSourceKind.SIMULATION,
        frequencies_hz=(8.0, 10.0, 12.0, 15.0),
        decoder_name="fbcca",
        trial_count=1,
    )


def test_signal_task_config_enforces_capability_matrix() -> None:
    with pytest.raises(ValueError, match="requires 2 frequencies"):
        SignalTaskConfig(
            SignalTaskKind.SSVEP_BINARY_CHOICE,
            SignalSourceKind.SIMULATION,
            frequencies_hz=(10.0,),
        )
    with pytest.raises(ValueError, match="requires a patient calibration model"):
        SignalTaskConfig(
            SignalTaskKind.SSVEP_BINARY_CHOICE,
            SignalSourceKind.SIMULATION,
            frequencies_hz=(10.0, 12.0),
            decoder_name="trca",
        )


def test_ssvep_runner_writes_auditable_engineering_result(tmp_path: Path) -> None:
    result_path = run_signal_task(_ssvep_config(), tmp_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    result = payload["result"]
    assert payload["summary"]["valid_sample_ratio"] == 1.0
    assert result["evaluation"]["accuracy"] == 1.0
    assert result["algorithm"]["version"] == "fbcca-1.0"
    assert result["simulated"] is True
    assert result["report_eligible"] is False
    assert len(tuple(result_path.parent.glob("eeg_trial_*.npz"))) == 4


def test_frequency_scan_creates_auditable_trca_calibration(tmp_path: Path) -> None:
    config = SignalTaskConfig(
        SignalTaskKind.SSVEP_FREQUENCY_SCAN,
        SignalSourceKind.SIMULATION,
        frequencies_hz=(10.0, 12.0),
        decoder_name="fbcca",
        trial_count=2,
    )
    result_path = run_signal_task(config, tmp_path, patient_id="beta-patient")
    result = json.loads(result_path.read_text(encoding="utf-8"))["result"]
    model = result["calibration_model"]
    assert model["algorithm_version"] == "trca-1.0"
    assert model["simulated"] is True
    assert (result_path.parent / model["file_name"]).is_file()


def test_mi_runner_stays_feature_only(tmp_path: Path) -> None:
    config = SignalTaskConfig(
        SignalTaskKind.MI_PROTOCOL,
        SignalSourceKind.SIMULATION,
        channel_names=("C3", "C4"),
        trial_count=1,
    )
    result = json.loads(run_signal_task(config, tmp_path).read_text(encoding="utf-8"))["result"]
    assert result["classification"] is None
    assert result["boundary"].startswith("MI remains independent")
    assert [trial["cue"] for trial in result["trials"]] == ["left", "right"]


def test_missing_hardware_source_fails_without_simulation_fallback(tmp_path: Path) -> None:
    config = SignalTaskConfig(
        SignalTaskKind.EEG_QUALITY,
        SignalSourceKind.MYLIAN_BRIDGE,
        source_path=str(tmp_path / "missing.jsonl"),
    )
    with pytest.raises(RuntimeError, match="Cannot read"):
        run_signal_task(config, tmp_path / "output")
    assert not tuple((tmp_path / "output").glob("tasks/*/task_result.json"))


def test_simulation_is_refused_for_real_patient(tmp_path: Path) -> None:
    runtime = initialize_database(tmp_path / "db.sqlite3", data_root=tmp_path / "data")
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="REAL-001", family_name="Patient")
    )
    store = PatientSignalProfileStore(tmp_path / "profiles.json")
    with pytest.raises(ValueError, match="Beta00"):
        create_signal_task_launch(
            runtime.experiment_session_service,
            store,
            patient_id=patient.patient_id,
            config=_ssvep_config(),
        )
    assert runtime.experiment_session_service.list_sessions_for_patient(patient.patient_id) == []
    runtime.dispose()


def test_beta00_signal_session_registers_snapshot_blocks_and_reports(tmp_path: Path) -> None:
    runtime = initialize_database(tmp_path / "db.sqlite3", data_root=tmp_path / "data")
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="Beta00", family_name="Engineer")
    )
    launch = create_signal_task_launch(
        runtime.experiment_session_service,
        PatientSignalProfileStore(tmp_path / "profiles.json"),
        patient_id=patient.patient_id,
        config=_ssvep_config(),
    )
    result_path = run_signal_task(
        launch.config,
        launch.session_directory,
        patient_id=str(patient.patient_id),
    )
    write_signal_report(SessionSignalSnapshot.read(launch.snapshot_path), result_path)
    status = finalize_signal_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
    )
    assert status is ExperimentSessionStatus.COMPLETED
    kinds = {
        artifact.kind
        for artifact in runtime.experiment_session_service.list_artifacts(launch.session_id)
    }
    assert {
        SessionArtifactKind.EEG,
        SessionArtifactKind.SIGNAL_CONFIGURATION,
        SessionArtifactKind.SIGNAL_SNAPSHOT,
        SessionArtifactKind.SIGNAL_REPORT,
    } <= kinds
    report = json.loads(
        next(launch.session_directory.glob("tasks/*/signal_report.json")).read_text(
            encoding="utf-8"
        )
    )
    assert report["report_type"] == "engineering_signal_report"
    assert report["report_eligible"] is False
    runtime.dispose()


def test_signal_child_process_commands_route_source_and_frozen_modes(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    output = tmp_path / "output"
    program, arguments = signal_task_process_command(
        config,
        output,
        executable="python.exe",
        frozen=False,
        headless=True,
    )
    assert program == "python.exe"
    assert arguments[:3] == ["-m", "oculidoc.signal_tasks", "--config"]
    assert arguments[-1] == "--headless"
    program, arguments = signal_task_process_command(
        config,
        output,
        executable="OculiDoC.exe",
        frozen=True,
    )
    assert program == "OculiDoC.exe"
    assert arguments[0] == "--signal-task"


def test_frozen_dispatch_forwards_signal_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[list[str] | None] = []

    def fake_main(argv: Sequence[str] | None = None) -> int:
        received.append(list(argv) if argv is not None else None)
        return 0

    monkeypatch.setattr(
        signal_main_module,
        "main",
        fake_main,
    )
    assert main_module.dispatch(["--signal-task", "--config", "c", "--output", "o"]) == 0
    assert received == [["--config", "c", "--output", "o"]]
