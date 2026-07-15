"""Hardware diagnostics and acquisition device interfaces."""

from oculidoc.devices.contracts import (
    AcquisitionDevice,
    CameraDevice,
    CameraFramePacket,
    DeviceInfo,
    DeviceKind,
    DeviceState,
    DeviceTimestamp,
    EyeTrackerDevice,
    EyeTrackerSample,
)
from oculidoc.devices.coordinator import DeviceCoordinator
from oculidoc.devices.diagnostics import (
    build_diagnostic_report,
    collect_system_snapshot,
    probe_camera,
    probe_cameras,
    write_diagnostic_report,
)
from oculidoc.devices.errors import (
    DeviceCoordinationError,
    DeviceError,
    DeviceStreamEndedError,
    InvalidDeviceStateError,
)
from oculidoc.devices.matching import (
    GazeFrameMatch,
    GazeSampleBuffer,
    MatchStatus,
    TimestampBasis,
)
from oculidoc.devices.models import (
    CameraProbeResult,
    DeviceDiagnosticReport,
    ProbeStatus,
    SystemSnapshot,
)
from oculidoc.devices.simulated import (
    SimulatedCameraDevice,
    SimulatedEyeTrackerDevice,
)
from oculidoc.devices.synchronization import (
    PairedAcquisitionPacket,
    PairedAcquisitionRunner,
)

__all__ = [
    "AcquisitionDevice",
    "CameraDevice",
    "CameraFramePacket",
    "CameraProbeResult",
    "DeviceCoordinationError",
    "DeviceCoordinator",
    "DeviceDiagnosticReport",
    "DeviceError",
    "DeviceInfo",
    "DeviceKind",
    "DeviceState",
    "DeviceStreamEndedError",
    "DeviceTimestamp",
    "EyeTrackerDevice",
    "EyeTrackerSample",
    "GazeFrameMatch",
    "GazeSampleBuffer",
    "InvalidDeviceStateError",
    "MatchStatus",
    "PairedAcquisitionPacket",
    "PairedAcquisitionRunner",
    "ProbeStatus",
    "SimulatedCameraDevice",
    "SimulatedEyeTrackerDevice",
    "SystemSnapshot",
    "TimestampBasis",
    "build_diagnostic_report",
    "collect_system_snapshot",
    "probe_camera",
    "probe_cameras",
    "write_diagnostic_report",
]
