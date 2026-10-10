"""Local bridge contract for Seveninvensun aSee eye trackers."""

from __future__ import annotations

import json
from ipaddress import ip_address
from typing import Any

from oculidoc.devices.contracts import DeviceInfo, DeviceKind, EyeTrackerSample
from oculidoc.devices.errors import DeviceReadError
from oculidoc.devices.tobii_legacy_bridge import (
    TobiiLegacyBridgeDevice,
    parse_tobii_bridge_payload,
)

SEVENINVENSUN_BRIDGE_PROTOCOL = "oculidoc-seveninvensun-v1"

_BRIDGE_STATUS_ERRORS = {
    "sdk_missing": "尚未安装七鑫易维设备对应的 Windows SDK。",
    "device_not_found": "七鑫易维 SDK 未发现眼动设备。",
    "unsupported_model": "当前七鑫易维设备型号尚未由本机桥支持。",
    "calibration_required": "七鑫易维设备尚未完成校准。",
    "scene_mapping_required": "眼动眼镜的场景坐标尚未映射到当前任务屏幕。",
}


def _is_loopback_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ip_address(host.strip()).is_loopback
    except ValueError:
        return False


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DeviceReadError(f"七鑫易维桥接帧缺少 {key}。")
    return value.strip()


def parse_seveninvensun_bridge_payload(
    payload: dict[str, Any],
    *,
    fallback_sequence: int,
) -> EyeTrackerSample:
    """Validate one vendor-shim frame and convert its screen-space sample."""
    if payload.get("protocol") != SEVENINVENSUN_BRIDGE_PROTOCOL:
        raise DeviceReadError(f"七鑫易维桥协议不匹配；需要 {SEVENINVENSUN_BRIDGE_PROTOCOL}。")

    bridge_status = _required_text(payload, "bridge_status").lower()
    if bridge_status != "ready":
        message = _BRIDGE_STATUS_ERRORS.get(
            bridge_status,
            f"七鑫易维桥尚未就绪：{bridge_status}。",
        )
        raise DeviceReadError(message)

    device_mode = _required_text(payload, "device_mode").lower()
    if device_mode not in {"screen_bar", "wearable_glasses"}:
        raise DeviceReadError("device_mode 必须是 screen_bar 或 wearable_glasses。")

    calibration_status = _required_text(payload, "calibration_status").lower()
    if calibration_status != "calibrated":
        raise DeviceReadError("七鑫易维设备尚未完成当前用户校准。")

    sample_payload = payload.get("sample")
    if not isinstance(sample_payload, dict):
        raise DeviceReadError("七鑫易维桥接帧缺少 sample 对象。")

    coordinate_space = _required_text(sample_payload, "coordinate_space").lower()
    if coordinate_space != "screen_normalized":
        if device_mode == "wearable_glasses":
            raise DeviceReadError("眼动眼镜仍在输出场景/世界坐标；请先完成到当前屏幕的映射。")
        raise DeviceReadError("眼动条桥必须输出 screen_normalized 屏幕坐标。")

    if device_mode == "wearable_glasses":
        mapping_status = _required_text(payload, "mapping_status").lower()
        if mapping_status != "mapped":
            raise DeviceReadError("眼动眼镜尚未完成场景到当前任务屏幕的映射。")

    normalized_payload = dict(sample_payload)
    normalized_payload.setdefault("source_clock_id", "seveninvensun-sdk")
    sample = parse_tobii_bridge_payload(
        normalized_payload,
        fallback_sequence=fallback_sequence,
    )
    if sample.gaze_valid:
        gaze_x = sample.gaze_x_normalized
        gaze_y = sample.gaze_y_normalized
        if gaze_x is None or gaze_y is None:
            raise DeviceReadError("七鑫易维有效帧缺少屏幕注视坐标。")
        if not (0.0 <= gaze_x <= 1.0 and 0.0 <= gaze_y <= 1.0):
            raise DeviceReadError("七鑫易维屏幕注视坐标必须位于 0 到 1。")
    return sample


class SeveninvensunBridgeDevice(TobiiLegacyBridgeDevice):
    """Read a local, model-specific shim without bundling the proprietary SDK."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 9999,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 0.25,
        maximum_message_bytes: int = 1_048_576,
    ) -> None:
        if not _is_loopback_host(host):
            raise ValueError("七鑫易维 SDK 桥只允许使用本机回环地址。")
        super().__init__(
            host=host,
            port=port,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_message_bytes=maximum_message_bytes,
        )
        self._info = DeviceInfo(
            device_id=f"seveninvensun-bridge:{self.host}:{self.port}",
            kind=DeviceKind.EYE_TRACKER,
            name="七鑫易维 aSee 本机 SDK 桥",
            manufacturer="七鑫易维 / 7invensun",
            model="待现场确认",
            capabilities=(
                "normalized_gaze",
                "binocular_validity",
                "source_timestamp",
                "local_sdk_bridge",
                SEVENINVENSUN_BRIDGE_PROTOCOL,
            ),
        )

    def _update_device_info(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("device_model", "")).strip() or "待现场确认"
        serial = str(payload.get("serial_number", "")).strip() or None
        device_mode = str(payload.get("device_mode", "")).strip().lower()
        capabilities = list(self._info.capabilities)
        if device_mode:
            capabilities.append(device_mode)
        if device_mode == "wearable_glasses":
            capabilities.append("scene_to_screen_mapped")
        self._info = DeviceInfo(
            device_id=f"seveninvensun-bridge:{self.host}:{self.port}",
            kind=DeviceKind.EYE_TRACKER,
            name=f"七鑫易维 {model}",
            manufacturer="七鑫易维 / 7invensun",
            model=model,
            serial_number=serial,
            capabilities=tuple(dict.fromkeys(capabilities)),
        )

    def read_sample(self) -> EyeTrackerSample:
        while True:
            line = self._read_line()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise DeviceReadError("七鑫易维桥输出了无效 JSON。") from error
            if not isinstance(payload, dict):
                raise DeviceReadError("七鑫易维桥接帧必须是 JSON 对象。")

            sample = parse_seveninvensun_bridge_payload(
                payload,
                fallback_sequence=self._sequence,
            )
            self._sequence = sample.timestamp.sequence + 1
            self._update_device_info(payload)
            return sample
