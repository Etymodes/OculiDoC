"""Use an explicitly selected compatibility copy of Tobii Stream Engine."""

from __future__ import annotations

from pathlib import Path

from oculidoc.devices.tobii_stream_engine import TobiiStreamEngineDevice


class JustNeedToSeeBundleDevice(TobiiStreamEngineDevice):
    """Direct Tobii source using a known-good compatibility DLL location.

    OculiDoC owns the device subscription in this mode; another program must not
    subscribe to the same consumer eye tracker at the same time.
    """

    def __init__(
        self,
        *,
        bundle_root: str | Path = r"D:\JustNeedToSee",
    ) -> None:
        root = Path(bundle_root).expanduser().resolve()
        library_path = root / "tobii_stream_engine.dll"
        super().__init__(library_path=library_path)
        self.bundle_root = root
        self._info = self._info.__class__(
            device_id="tobii-dll-compatibility",
            kind=self._info.kind,
            name="Tobii DLL兼容",
            manufacturer="Tobii",
            model="Stream Engine compatibility bundle",
            serial_number=None,
            is_simulated=False,
            capabilities=self._info.capabilities + ("tobii_dll_compatibility",),
        )
