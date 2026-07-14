"""Hardware discovery and diagnostic tools."""

from oculidoc.devices.diagnostics import (
    build_diagnostic_report,
    collect_system_snapshot,
    probe_camera,
    probe_cameras,
    write_diagnostic_report,
)
from oculidoc.devices.models import (
    CameraProbeResult,
    DeviceDiagnosticReport,
    ProbeStatus,
    SystemSnapshot,
)

__all__ = [
    "CameraProbeResult",
    "DeviceDiagnosticReport",
    "ProbeStatus",
    "SystemSnapshot",
    "build_diagnostic_report",
    "collect_system_snapshot",
    "probe_camera",
    "probe_cameras",
    "write_diagnostic_report",
]
