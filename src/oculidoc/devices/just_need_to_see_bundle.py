"""Use the Stream Engine library shipped beside JustNeedToSee without launching its UI."""

from __future__ import annotations

from pathlib import Path

from oculidoc.devices.tobii_stream_engine import TobiiStreamEngineDevice


class JustNeedToSeeBundleDevice(TobiiStreamEngineDevice):
    """Direct Tobii source using JustNeedToSee's known-good DLL location.

    JustNeedToSee itself must remain closed. OculiDoC owns the device subscription
    in this mode; reading cursor movement from the other application is deliberately
    not used because it discards timestamps and validity.
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
            device_id="just-need-to-see-stream-engine",
            kind=self._info.kind,
            name="JustNeedToSee 内置 Tobii 兼容模式",
            manufacturer="Tobii / HPF legacy bundle",
            model="Stream Engine 2.3.0.699",
            serial_number=None,
            is_simulated=False,
            capabilities=self._info.capabilities + ("just_need_to_see_bundle",),
        )
