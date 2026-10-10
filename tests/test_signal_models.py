"""Tests for device-neutral signal contracts and the Mylian boundary."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from oculidoc.devices.eeg.adapters.mylian import (
    MylianBridgeStatus,
    MylianBridgeUnavailable,
    MylianJsonLineSource,
    MylianPayloadAdapter,
)
from oculidoc.signals.coordinator import SignalCoordinator, SynchronizationMethod
from oculidoc.signals.models import EEGSampleBlock, SignalParadigm, SignalSourceKind
from oculidoc.signals.profile import PatientSignalProfileStore, SignalProfileConflict
from oculidoc.signals.snapshot import SessionSignalSnapshot
from oculidoc.signals.sources import (
    LocalJsonLineEEGSource,
    ReplayEEGSource,
    load_eeg_block,
    save_eeg_block,
)


def test_eeg_block_is_validated_and_immutable() -> None:
    source: NDArray[np.float64] = np.arange(12, dtype=np.float64).reshape(3, 4)
    block = EEGSampleBlock(
        device_id="device-a",
        start_timestamp_ns=10,
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz", "O2"),
        values_uv=source,
        quality={"Oz": 0.8},
    )
    source[0, 0] = 999.0
    assert block.values_uv[0, 0] == 0.0
    assert not block.values_uv.flags.writeable
    with pytest.raises(ValueError):
        block.values_uv[0, 0] = 1.0
    with pytest.raises(ValueError, match="channel count"):
        EEGSampleBlock("bad", 0, 250.0, ("Oz",), np.zeros((2, 10)))


def test_standard_replay_preserves_simulation_provenance(tmp_path: Path) -> None:
    original = EEGSampleBlock(
        "engineering-simulator",
        100,
        100.0,
        ("Oz",),
        np.ones((1, 100)),
        simulated=True,
    )
    path = save_eeg_block(tmp_path / "eeg.npz", original)
    loaded = load_eeg_block(path)
    replayed = ReplayEEGSource(path).acquire(0.5)
    assert loaded.simulated is True
    assert replayed.simulated is True
    assert replayed.sample_count == 50


def test_standard_local_bridge_is_vendor_neutral(tmp_path: Path) -> None:
    path = tmp_path / "bridge.jsonl"
    path.write_text(
        json.dumps(
            {
                "device_id": "bridge-device",
                "start_timestamp_ns": 100,
                "sample_rate_hz": 100,
                "channel_names": ["Oz"],
                "values_uv": [[1, 2, 3, 4]],
                "simulated": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    block = LocalJsonLineEEGSource(path).acquire(1.0)
    assert block.device_id == "bridge-device"
    assert block.simulated is False


def test_signal_coordinator_uses_documented_priority() -> None:
    coordinator = SignalCoordinator.for_available_methods(
        (
            SynchronizationMethod.SOFTWARE_ESTIMATE,
            SynchronizationMethod.TIMESTAMP,
            SynchronizationMethod.HARDWARE_TRIGGER,
        )
    )
    assert coordinator.method is SynchronizationMethod.HARDWARE_TRIGGER


def test_patient_profile_store_uses_revision_conflicts(tmp_path: Path) -> None:
    store = PatientSignalProfileStore(tmp_path / "profiles.json")
    original = store.load("patient-a")
    first = store.save(
        original.with_session_defaults(
            paradigms=(SignalParadigm.GAZE, SignalParadigm.SSVEP),
            frequencies_hz=(10.0, 12.0),
            algorithm="fbcca",
        ),
        expected_revision=0,
    )
    assert first.revision == 1
    assert first.default_paradigms == (SignalParadigm.GAZE, SignalParadigm.SSVEP)
    with pytest.raises(SignalProfileConflict):
        store.save(original, expected_revision=0)


def test_session_snapshot_detects_configuration_tampering(tmp_path: Path) -> None:
    snapshot = SessionSignalSnapshot.create(
        patient_id="patient-a",
        profile_revision=3,
        paradigms=(SignalParadigm.SSVEP,),
        task_kind="ssvep_binary_choice",
        source_kind=SignalSourceKind.REPLAY,
        device_id="replay-source",
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz", "O2"),
        task_configuration={"frequencies_hz": [10.0, 12.0]},
        algorithm_versions={"fbcca": "fbcca-1.0"},
        simulated=False,
        created_at_utc="2026-08-06T12:00:00+00:00",
    )
    path = snapshot.write(tmp_path / "snapshot.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sample_rate_hz"] = 500.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        SessionSignalSnapshot.read(path)


def test_session_snapshot_deeply_freezes_configuration() -> None:
    configuration: dict[str, object] = {"frequencies_hz": [10.0, 12.0]}
    snapshot = SessionSignalSnapshot.create(
        patient_id="patient-a",
        profile_revision=3,
        paradigms=(SignalParadigm.SSVEP,),
        task_kind="ssvep_binary_choice",
        source_kind=SignalSourceKind.REPLAY,
        device_id="replay-source",
        sample_rate_hz=250.0,
        channel_names=("O1", "Oz", "O2"),
        task_configuration=configuration,
        algorithm_versions={"fbcca": "fbcca-1.0"},
        simulated=False,
        created_at_utc="2026-08-06T12:00:00+00:00",
    )

    frequencies = configuration["frequencies_hz"]
    assert isinstance(frequencies, list)
    frequencies.append(15.0)
    assert snapshot.task_configuration["frequencies_hz"] == (10.0, 12.0)
    with pytest.raises(TypeError):
        snapshot.task_configuration["frequencies_hz"] = (8.0,)  # type: ignore[index]


def test_mylian_adapter_contains_private_compatibility_fields() -> None:
    block = MylianPayloadAdapter().decode(
        {
            "deviceId": "mylian-test",
            "sampleRate": 250,
            "channelNames": ["O1", "Oz"],
            "brainPayload": [[1, 2, 3], [4, 5, 6]],
            "targetFre_est": 12.0,
            "frequencyFeaturesStr": "vendor-private",
        },
        received_timestamp_ns=123,
    )
    metadata = block.metadata()
    assert block.device_id == "mylian-test"
    assert block.values_uv.shape == (2, 3)
    assert "brainPayload" not in metadata
    assert "targetFre_est" not in metadata
    assert "frequencyFeaturesStr" not in metadata


@pytest.mark.parametrize(
    "status",
    [
        MylianBridgeStatus.MISSING_RUNTIME,
        MylianBridgeStatus.UNSUPPORTED_DEVICE,
        MylianBridgeStatus.LICENCE_REQUIRED,
    ],
)
def test_mylian_bridge_reports_optional_capability_failures(
    status: MylianBridgeStatus,
) -> None:
    with pytest.raises(MylianBridgeUnavailable) as caught:
        MylianPayloadAdapter().decode({"oculidocBridgeStatus": status.value})
    assert caught.value.status is status


def test_mylian_source_missing_file_does_not_fall_back(tmp_path: Path) -> None:
    source = MylianJsonLineSource(tmp_path / "missing.jsonl")
    with pytest.raises(RuntimeError, match="Cannot read"):
        source.acquire(1.0)
