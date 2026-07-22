"""Hardware diagnostics and acquisition device interfaces."""

from oculidoc.devices.auto_detect import AutoDetectEyeTrackerDevice
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
    DeviceConnectionError,
    DeviceCoordinationError,
    DeviceError,
    DeviceReadError,
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
from oculidoc.devices.opencv_camera import (
    OpenCVCameraDevice,
)
from oculidoc.devices.preflight import (
    GazePreflightResult,
    GazePreflightStore,
    failed_gaze_preflight,
    run_gaze_preflight,
)
from oculidoc.devices.simulated import (
    SimulatedCameraDevice,
    SimulatedEyeTrackerDevice,
)
from oculidoc.devices.synchronization import (
    PairedAcquisitionPacket,
    PairedAcquisitionRunner,
)
from oculidoc.devices.tobii_hospital_bridge import (
    TobiiHospitalBridgeDevice,
)
from oculidoc.devices.tobii_legacy_bridge import (
    TOBII_BRIDGE_PROTOCOL,
    TobiiLegacyBridgeDevice,
    parse_tobii_bridge_payload,
)
from oculidoc.devices.tobii_stream_engine import (
    TobiiStreamEngineDevice,
    discover_tobii_stream_engine_dll,
)

__all__ = [
    "AcquisitionDevice",
    "AutoDetectEyeTrackerDevice",
    "CameraDevice",
    "CameraFramePacket",
    "CameraProbeResult",
    "DeviceConnectionError",
    "DeviceCoordinationError",
    "DeviceCoordinator",
    "DeviceDiagnosticReport",
    "DeviceError",
    "DeviceInfo",
    "DeviceKind",
    "DeviceReadError",
    "DeviceState",
    "DeviceStreamEndedError",
    "DeviceTimestamp",
    "EyeTrackerDevice",
    "EyeTrackerSample",
    "GazeFrameMatch",
    "GazePreflightResult",
    "GazePreflightStore",
    "GazeSampleBuffer",
    "InvalidDeviceStateError",
    "MatchStatus",
    "OpenCVCameraDevice",
    "PairedAcquisitionPacket",
    "PairedAcquisitionRunner",
    "ProbeStatus",
    "SimulatedCameraDevice",
    "SimulatedEyeTrackerDevice",
    "SystemSnapshot",
    "TOBII_BRIDGE_PROTOCOL",
    "TimestampBasis",
    "TobiiHospitalBridgeDevice",
    "TobiiStreamEngineDevice",
    "TobiiLegacyBridgeDevice",
    "build_diagnostic_report",
    "collect_system_snapshot",
    "probe_camera",
    "parse_tobii_bridge_payload",
    "probe_cameras",
    "discover_tobii_stream_engine_dll",
    "failed_gaze_preflight",
    "run_gaze_preflight",
    "write_diagnostic_report",
]
