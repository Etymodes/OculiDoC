"""End-to-end task, report, process, and patient-safety tests."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
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
from oculidoc.signals.models import EEGSampleBlock, SignalSourceKind
from oculidoc.signals.profile import PatientSignalProfileStore
from oculidoc.signals.snapshot import SessionSignalSnapshot
from oculidoc.signals.sources import SimulatedEEGSource


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
    assert len(result_path.parent.name) <= 16
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
        trial_count=4,
    )
    result_path = run_signal_task(config, tmp_path, patient_id="beta-patient")
    result = json.loads(result_path.read_text(encoding="utf-8"))["result"]
    model = result["calibration_model"]
    assert model["algorithm_version"] == "trca-1.0"
    assert model["simulated"] is True
    assert model["adaptation"]["accepted"] is True
    assert model["adaptation"]["unlabeled_self_training"] is False
    assert model["recommended_for_use"] is False
    assert (result_path.parent / model["file_name"]).is_file()


def test_binary_communication_closes_stimulus_data_decode_feedback_loop(
    tmp_path: Path,
) -> None:
    config = SignalTaskConfig(
        SignalTaskKind.SSVEP_BINARY_COMMUNICATION,
        SignalSourceKind.SIMULATION,
        frequencies_hz=(6.0, 10.0),
        target_labels=("是", "否"),
        decoder_name="fbcca",
        trial_count=2,
        feedback_seconds=0.0,
    )
    result_path = run_signal_task(config, tmp_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    result = payload["result"]
    assert [item["selected_label"] for item in result["task_outcomes"]] == ["是", "否"]
    assert result["task_closed_loop"] == {
        "stimulus": True,
        "raw_data_recorded": True,
        "quality_gated": True,
        "decoded": True,
        "visible_feedback": True,
        "rejection_retry": True,
        "external_robotic_actuation": False,
    }
    assert result["evaluation"] is None
    assert result["calibration_model"] is None
    assert len(tuple(result_path.parent.glob("eeg_trial_*.npz"))) == 2
    csv_path = next(result_path.parent.glob("eeg_trial_*.csv"))
    assert csv_path.read_text(encoding="utf-8").splitlines()[0] == "O1,Oz,O2,tag,timestamp"
    events = (result_path.parent / "task_events.jsonl").read_text(encoding="utf-8")
    assert events.count('"event_type":"task_feedback"') == 2


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


def test_unverified_justssvep_count_scale_blocks_report_eligibility(tmp_path: Path) -> None:
    source_path = tmp_path / "justssvep.csv"
    rows = [
        "elements,flag,timestamp",
        "FP1,FP2,O1,O2,Oz,PO3,PO4,POz,tag,2026-08-06T23:00:00Z",
    ]
    for index in range(500):
        value = np.sin(2.0 * np.pi * 10.0 * index / 250.0)
        rows.append(",".join([*(f"{value * scale:.8f}" for scale in range(1, 9)), "1", str(index)]))
    source_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    config = SignalTaskConfig(
        SignalTaskKind.EEG_QUALITY,
        SignalSourceKind.MYLIAN_BRIDGE,
        source_path=str(source_path),
        channel_names=("O1", "Oz", "O2"),
        scale_verified=False,
    )
    result = json.loads(run_signal_task(config, tmp_path / "output").read_text(encoding="utf-8"))[
        "result"
    ]
    assert result["simulated"] is False
    assert result["quality_gate"]["all_usable"] is True
    assert result["report_eligible"] is False
    assert result["report_ineligibility_reasons"] == [
        "historical_csv_labels_not_verified",
        "microvolt_scale_unverified",
    ]


def test_historical_csv_never_promotes_a_patient_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "historical.csv"
    source_path.write_text("placeholder\n", encoding="utf-8")
    config = SignalTaskConfig(
        SignalTaskKind.SSVEP_FREQUENCY_SCAN,
        SignalSourceKind.MYLIAN_BRIDGE,
        source_path=str(source_path),
        frequencies_hz=(6.0, 10.0),
        trial_count=4,
        scale_verified=True,
    )

    class RealLabeledSource:
        def __init__(self, frequency_hz: float, seed: int) -> None:
            self.frequency_hz = frequency_hz
            self.seed = seed

        def acquire(self, duration_seconds: float) -> EEGSampleBlock:
            block = SimulatedEEGSource(
                sample_rate_hz=250.0,
                channel_names=("O1", "Oz", "O2"),
                target_frequency_hz=self.frequency_hz,
                seed=self.seed,
            ).acquire(duration_seconds)
            return EEGSampleBlock(
                device_id="historical-test-source",
                start_timestamp_ns=block.start_timestamp_ns,
                sample_rate_hz=block.sample_rate_hz,
                channel_names=block.channel_names,
                values_uv=block.values_uv,
                quality=block.quality,
                markers=block.markers,
                simulated=False,
            )

    def fake_source(_config: object, trial: Any, **_kwargs: object) -> object:
        assert isinstance(trial.frequency_hz, float)
        return RealLabeledSource(trial.frequency_hz, trial.index)

    monkeypatch.setattr("oculidoc.signal_tasks.runner._source_for_trial", fake_source)
    result = json.loads(
        run_signal_task(config, tmp_path / "output", patient_id="real-patient").read_text(
            encoding="utf-8"
        )
    )["result"]

    assert result["calibration_model"]["adaptation"]["accepted"] is True
    assert result["calibration_model"]["recommended_for_use"] is False
    assert result["report_ineligibility_reasons"] == ["historical_csv_labels_not_verified"]


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


def test_accepted_labeled_adaptation_is_promoted_to_patient_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = initialize_database(tmp_path / "db.sqlite3", data_root=tmp_path / "data")
    patient = runtime.patient_service.register_patient(
        RegisterPatientRequest(patient_code="REAL-ADAPT", family_name="Patient")
    )
    source_path = tmp_path / "mylian.jsonl"
    source_path.write_text("{}\n", encoding="utf-8")
    config = SignalTaskConfig(
        SignalTaskKind.SSVEP_FREQUENCY_SCAN,
        SignalSourceKind.MYLIAN_BRIDGE,
        source_path=str(source_path),
        frequencies_hz=(6.0, 10.0),
        trial_count=4,
        decoder_name="fbcca",
    )
    store = PatientSignalProfileStore(tmp_path / "profiles.json")
    launch = create_signal_task_launch(
        runtime.experiment_session_service,
        store,
        patient_id=patient.patient_id,
        config=config,
    )

    class _RealCalibrationSource:
        def __init__(self, frequency_hz: float, seed: int) -> None:
            self.frequency_hz = frequency_hz
            self.seed = seed

        def acquire(self, duration_seconds: float) -> EEGSampleBlock:
            simulated = SimulatedEEGSource(
                sample_rate_hz=250.0,
                channel_names=("O1", "Oz", "O2"),
                target_frequency_hz=self.frequency_hz,
                seed=self.seed,
            ).acquire(duration_seconds)
            return EEGSampleBlock(
                device_id="test-real-bridge",
                start_timestamp_ns=simulated.start_timestamp_ns,
                sample_rate_hz=simulated.sample_rate_hz,
                channel_names=simulated.channel_names,
                values_uv=simulated.values_uv,
                quality=simulated.quality,
                markers=simulated.markers,
                simulated=False,
            )

    def fake_source(_config: object, trial: Any, **_kwargs: object) -> object:
        frequency = trial.frequency_hz
        index = trial.index
        assert isinstance(frequency, float)
        assert isinstance(index, int)
        return _RealCalibrationSource(frequency, index)

    monkeypatch.setattr("oculidoc.signal_tasks.runner._source_for_trial", fake_source)
    result_path = run_signal_task(
        launch.config,
        launch.session_directory,
        patient_id=str(patient.patient_id),
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))["result"]
    assert result["calibration_model"]["recommended_for_use"] is True
    status = finalize_signal_task_launch(
        runtime.experiment_session_service,
        launch,
        exit_code=0,
        profile_store=store,
    )
    assert status is ExperimentSessionStatus.COMPLETED
    profile = store.load(str(patient.patient_id))
    assert profile.calibration_models == (
        str(result_path.parent / result["calibration_model"]["file_name"]),
    )
    assert profile.algorithm_history[-1] == "trca@guarded-trca-1.0"
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
