"""Isolated aSeeGlasses Plus SDK capture support.

This module deliberately exposes raw scene-space samples only.  A wearable gaze
point must not become an :class:`EyeTrackerSample` until a separate, verified
scene-to-screen mapping has been applied by the local bridge.

The C declarations below are adapted from the vendor SDK headers.  Their full
required copyright and license statements are retained beside this module in
``Seveninvensun-aSeeGlassesPlus-SDK.txt`` and in the Windows license bundle.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import math
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEVENINVENSUN_GLASSES_RAW_PROTOCOL = "oculidoc-seveninvensun-glasses-raw-v1"

ASEEX_SHA256 = "8216ba265fbfe073276b7d09ee2eb9622dbace5de48f2e3d5c04fbebc423b0eb"
USERSDK_SHA256 = "ccba07dd68fd04d67d825fb98b97312aa1758514fe3e017846a4cd400760b08e"

RUNTIME_FILE_SHA256 = {
    "bin/AlgInterface.dll": "b800f98041c8b862b2bec5f62ea24a3c9e4c9513bac7592bb372601439f04b7f",
    "bin/GetPointDllt.dll": "62dae0583876284ef53a95a3f6aab644121965f5aa964c302b040430fc673ede",
    "bin/H265Decode.dll": "734949e5bd130756c02fb1b3b8b58743ab3ff9ac76b3c3d9f26d568222cdb80b",
    "bin/SdkEvent.dll": "c37d94ce4cd2f95472e32888cdfb07881093a5b2a2d2169342ea69daddde7f95",
    "bin/USERSDK.dll": USERSDK_SHA256,
    "bin/aSeeX.dll": ASEEX_SHA256,
    "bin/avcodec-58.dll": "e7dd68ac55e005534ef99fba8e31c26ead6b7b5ef3432749c78dc303135064cf",
    "bin/avdevice-58.dll": "4e31b3e1992efecdf095c6c40dc65fce94ed3f81405b4d280a529183459c2c95",
    "bin/avfilter-7.dll": "0c8f851088a418cdc0d22dfc628878d25c834ce63d7df6af19a65f3dbe28bff1",
    "bin/avformat-58.dll": "45cea453973d0a4d09bfe2a6b630935b9cdb31ce47ff1c47e32f37f87a754f51",
    "bin/avutil-56.dll": "90c95a4fcdc05a4888b15083ca5496c0f46a60263f4c389238db601b5f78bf46",
    "bin/libnlopt-0.dll": "90a53fe847e23739a0958b23a04337ed303de5a65b6ee45ab72edbc90d9366e6",
    "bin/opencv_world320.dll": "9dc4a22c0d21e4f547956cd92cbd5c18518bb46c081168148a85a95e10b0e2b9",
    "bin/opencv_world320d.dll": "e19704b9888971c6b70964d60672bc5e6f7cc7aef132d990e2a617bda8cbf9b8",
    "bin/postproc-55.dll": "270cb11aa574ae5912ba4cb9e91f681687289f4fc3790a3f95b06d8a936a45eb",
    "bin/smoothAbout.dll": "a5efd06ac0cc56845a7ba29471145097793a99543cebfb87c3ea631432d0b264",
    "bin/swresample-3.dll": "b1bc43dfe2f4c3c5382ad9571e6b801f4a62180927b0b1d697f4be0058899bc3",
    "bin/swscale-5.dll": "3285ee5de26dfc1df895f35eb0dcbe09132b26fbd93153d083acc153a3d021bd",
    "bin/config/1280_720.dat": ("3e8b0e92ea6fbfbd57c375e8bff61d6b34de9317cbfc755386ed1fb4958f3771"),
    "bin/config/1280_960.dat": ("a8217e890ae2db6ce37bf1d68ff3291ece89190590882464845b652dd60cc51b"),
    "bin/config/1920_1080.dat": (
        "c99f7cf2e852eb3ee4eb6b7c89a5db16b6cbf303e9e686d5597cd96027b14b4d"
    ),
    "bin/config/800_600.dat": ("89fff42280e7582f766a95cd9cfcd3965faf2df94af98301fa50240476f571e2"),
    "bin/config/base_coe.dat": ("0c67a3d551b68e2f9c9ae00c5472feeee154983954bd454518f476258e93e79e"),
    "bin/config/config.ini": ("d1e84f6c2c8cd3c868d8053c54d6ccd2ebcd30a8131738901f342945b12b956a"),
}

ENVIRONMENTS = {"indoor": 301, "outdoor": 302, "darkness": 303}
RESOLUTIONS = {
    "1280x960": 201,
    "1280x720": 202,
    "800x600": 203,
    "1920x1080": 204,
}
RESOLUTION_DIMENSIONS = {
    "1280x960": (1280, 960),
    "1280x720": (1280, 720),
    "800x600": (800, 600),
    "1920x1080": (1920, 1080),
}
CALIBRATION_POINT_COUNTS = frozenset({1, 3})
CALIBRATION_COEFFICIENT_SIZE = 1024
LEFT_EYE = 1
RIGHT_EYE = 2

DEVICE_DISCONNECT_TERMINAL_DISCONNECTED = "disconnected"
DEVICE_DISCONNECT_TERMINAL_ALREADY_ABSENT = "already_absent_after_successful_stop"

GAZE_VALIDITY = {
    "gaze_point": 0,
    "raw_point": 1,
    "smooth_point": 2,
    "origin": 3,
    "direction": 4,
    "confidence": 5,
}
PUPIL_VALIDITY = {
    "center": 0,
    "distance_mm": 1,
    "diameter_normalized": 2,
    "diameter_mm": 3,
    "minor_axis_normalized": 4,
    "minor_axis_mm": 5,
}
EYE_EXTRA_VALIDITY = {
    "blink": 0,
    "openness": 1,
    "eyelid_up": 2,
    "eyelid_down": 3,
}

# ctypes does not provide a safe public DLL-unload API. Keep callbacks, the DLL,
# and its search-directory cookie alive until this dedicated helper exits.
_CAPTURE_LIFETIME_GUARD: list[Any] = []


def classify_device_disconnect_terminal_state(
    *,
    authorization_established: bool,
    sdk_stop_result: int | None,
    device_disconnect_result: int | None,
) -> str | None:
    """Classify only teardown states supported by field and vendor-sample evidence."""
    if authorization_established is not True:
        return None
    if (
        not isinstance(sdk_stop_result, int)
        or isinstance(sdk_stop_result, bool)
        or sdk_stop_result != 0
    ):
        return None
    if not isinstance(device_disconnect_result, int) or isinstance(device_disconnect_result, bool):
        return None
    if device_disconnect_result == 0:
        return DEVICE_DISCONNECT_TERMINAL_DISCONNECTED
    if device_disconnect_result == -22:
        return DEVICE_DISCONNECT_TERMINAL_ALREADY_ABSENT
    return None


class SeveninvensunSdkError(RuntimeError):
    """Base error for local aSeeGlasses SDK capture."""


class SeveninvensunSdkCallError(SeveninvensunSdkError):
    """A documented SDK call returned a failure code."""

    def __init__(self, function_name: str, result_code: int) -> None:
        self.function_name = function_name
        self.result_code = result_code
        super().__init__(f"{function_name} failed with SDK code {result_code}.")


class _Point2D(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


class _Point3D(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("z", ctypes.c_float)]


class _Coefficient(ctypes.Structure):
    _fields_ = [("buf", ctypes.c_ubyte * 1024)]


class _GazePoint(ctypes.Structure):
    _fields_ = [
        ("gaze_bit_mask", ctypes.c_uint),
        ("gaze_point", _Point3D),
        ("raw_point", _Point3D),
        ("smooth_point", _Point3D),
        ("gaze_origin", _Point3D),
        ("gaze_direction", _Point3D),
        ("re", ctypes.c_float),
        ("ex_data_bit_mask", ctypes.c_uint),
        ("ex_data", ctypes.c_float * 32),
    ]


class _PupilInfo(ctypes.Structure):
    _fields_ = [
        ("pupil_bit_mask", ctypes.c_uint),
        ("pupil_center", _Point2D),
        ("pupil_distance", ctypes.c_float),
        ("pupil_diameter", ctypes.c_float),
        ("pupil_diameter_mm", ctypes.c_float),
        ("pupil_minor_axis", ctypes.c_float),
        ("pupil_minor_axis_mm", ctypes.c_float),
        ("ex_data_bit_mask", ctypes.c_uint),
        ("ex_data", ctypes.c_float * 32),
    ]


class _EyeExtraData(ctypes.Structure):
    _fields_ = [
        ("eye_data_ex_bit_mask", ctypes.c_uint),
        ("blink", ctypes.c_int),
        ("openness", ctypes.c_float),
        ("eyelid_up", ctypes.c_float),
        ("eyelid_down", ctypes.c_float),
        ("ex_data_bit_mask", ctypes.c_uint),
        ("ex_data", ctypes.c_float * 32),
    ]


class _FixationSaccade(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_longlong),
        ("duration", ctypes.c_longlong),
        ("count", ctypes.c_int),
        ("state", ctypes.c_int),
        ("center", _Point2D),
    ]


class _GyroData(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_longlong),
        ("quat_data", ctypes.c_float * 4),
        ("gyro", ctypes.c_float * 3),
        ("accel", ctypes.c_float * 3),
        ("mag", ctypes.c_float * 3),
        ("extra_data", ctypes.c_float * 8),
    ]


class _EyeDataEx(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_ulonglong),
        ("recommend", ctypes.c_int),
        ("recom_gaze", _GazePoint),
        ("left_gaze", _GazePoint),
        ("right_gaze", _GazePoint),
        ("left_pupil", _PupilInfo),
        ("right_pupil", _PupilInfo),
        ("left_ex_data", _EyeExtraData),
        ("right_ex_data", _EyeExtraData),
        ("stats", _FixationSaccade),
        ("gyro", _GyroData),
    ]


class _DateTime(ctypes.Structure):
    _fields_ = [
        ("year", ctypes.c_int16),
        ("month", ctypes.c_uint8),
        ("day", ctypes.c_uint8),
        ("hour", ctypes.c_uint8),
        ("minute", ctypes.c_uint8),
        ("second", ctypes.c_uint8),
    ]


class _UKeyInfo(ctypes.Structure):
    _fields_ = [
        ("device_id", ctypes.c_ubyte * 32),
        ("app_id", ctypes.c_ubyte * 20),
        ("version", ctypes.c_ubyte * 20),
        ("tip_id", ctypes.c_ubyte * 20),
        ("end_time", _DateTime),
    ]


EXPECTED_STRUCTURE_SIZES = {
    "point2d": 8,
    "point3d": 12,
    "coefficient": 1024,
    "gaze_point": 200,
    "pupil_info": 164,
    "eye_extra_data": 152,
    "fixation_saccade": 32,
    "gyro_data": 96,
    "eye_data_ex": 1376,
    "ukey_info": 100,
}

EXPECTED_EYE_DATA_EX_OFFSETS = {
    "timestamp": 0,
    "recommend": 8,
    "recom_gaze": 12,
    "left_gaze": 212,
    "right_gaze": 412,
    "left_pupil": 612,
    "right_pupil": 776,
    "left_ex_data": 940,
    "right_ex_data": 1092,
    "stats": 1248,
    "gyro": 1280,
}


def sdk_structure_sizes() -> dict[str, int]:
    """Return the local ctypes sizes used to validate the vendor ABI."""
    return {
        "point2d": ctypes.sizeof(_Point2D),
        "point3d": ctypes.sizeof(_Point3D),
        "coefficient": ctypes.sizeof(_Coefficient),
        "gaze_point": ctypes.sizeof(_GazePoint),
        "pupil_info": ctypes.sizeof(_PupilInfo),
        "eye_extra_data": ctypes.sizeof(_EyeExtraData),
        "fixation_saccade": ctypes.sizeof(_FixationSaccade),
        "gyro_data": ctypes.sizeof(_GyroData),
        "eye_data_ex": ctypes.sizeof(_EyeDataEx),
        "ukey_info": ctypes.sizeof(_UKeyInfo),
    }


def sdk_eye_data_ex_offsets() -> dict[str, int]:
    """Return critical composite offsets from the official C structure."""
    return {name: int(getattr(_EyeDataEx, name).offset) for name in EXPECTED_EYE_DATA_EX_OFFSETS}


def assert_sdk_abi_layout() -> None:
    """Fail closed when local structure alignment differs from the official header."""
    actual = sdk_structure_sizes()
    offsets = sdk_eye_data_ex_offsets()
    if actual != EXPECTED_STRUCTURE_SIZES or offsets != EXPECTED_EYE_DATA_EX_OFFSETS:
        raise SeveninvensunSdkError(
            "aSeeGlasses SDK ABI layout mismatch: "
            f"expected sizes {EXPECTED_STRUCTURE_SIZES} and offsets "
            f"{EXPECTED_EYE_DATA_EX_OFFSETS}; "
            f"got sizes {actual} and offsets {offsets}."
        )


@dataclass(frozen=True, slots=True)
class SeveninvensunRuntimeManifest:
    """Validated external runtime paths and non-secret integrity metadata."""

    sdk_root: Path
    bin_directory: Path
    config_directory: Path
    aseex_sha256: str
    usersdk_sha256: str
    validated_file_count: int
    runtime_bundle_sha256: str


@dataclass(frozen=True, slots=True)
class _QueuedSample:
    raw_data: bytes
    sequence: int
    host_monotonic_ns: int
    host_wall_time_ns: int


@dataclass(frozen=True, slots=True)
class SeveninvensunCalibrationEvent:
    """A copied calibration callback safe to consume outside the vendor thread."""

    eye: str
    kind: str
    index: int
    value: int


@dataclass(frozen=True, slots=True)
class SeveninvensunSceneFrame:
    """Latest decoded scene frame for the standalone calibration window."""

    bgr_bytes: bytes
    width: int
    height: int
    device_timestamp_raw: int
    host_monotonic_ns: int
    sequence: int
    decoder_result_raw: int


@dataclass(frozen=True, slots=True)
class _QueuedSceneImage:
    encoded_bytes: bytes
    width: int
    height: int
    device_timestamp_raw: int
    host_monotonic_ns: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_bundle_hash(actual_hashes: dict[str, str]) -> str:
    digest = hashlib.sha256(b"oculidoc-asee-runtime-v1\x00")
    for relative_path, file_hash in sorted(actual_hashes.items()):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def validate_sdk_runtime(sdk_root: str | Path) -> SeveninvensunRuntimeManifest:
    """Validate the complete public Glasses Plus SDK bundle without loading it."""
    requested = Path(sdk_root).expanduser().resolve()
    candidates = [requested]
    if requested.name.lower() == "bin":
        candidates.append(requested.parent)
    candidates.append(requested / "aSeeGlassesPlusUserSDK")

    root = next(
        (candidate for candidate in candidates if (candidate / "bin" / "aSeeX.dll").is_file()),
        None,
    )
    if root is None:
        raise SeveninvensunSdkError(
            "SDK root must contain bin/aSeeX.dll from aSeeGlassesPlusUserSDK."
        )

    bin_directory = root / "bin"
    config_directory = bin_directory / "config"
    required_paths = {relative_path: root / relative_path for relative_path in RUNTIME_FILE_SHA256}
    missing = [name for name, path in required_paths.items() if not path.is_file()]
    if missing:
        raise SeveninvensunSdkError(
            "The complete Glasses Plus SDK runtime is required; missing: " + ", ".join(missing)
        )

    allowed_dll_names = {
        Path(name).name.casefold()
        for name in RUNTIME_FILE_SHA256
        if Path(name).parent.as_posix() == "bin" and Path(name).suffix.casefold() == ".dll"
    }
    extra_dll_names = sorted(
        path.name
        for path in bin_directory.iterdir()
        if path.is_file()
        and path.suffix.casefold() == ".dll"
        and path.name.casefold() not in allowed_dll_names
    )
    if extra_dll_names:
        raise SeveninvensunSdkError(
            "Unknown DLLs are not allowed in the validated SDK bin folder: "
            + ", ".join(extra_dll_names)
        )

    actual_hashes = {name: _sha256(path) for name, path in required_paths.items()}
    mismatched = [
        name
        for name, expected_hash in RUNTIME_FILE_SHA256.items()
        if actual_hashes[name] != expected_hash
    ]
    if mismatched:
        raise SeveninvensunSdkError(
            "SDK binary integrity/version mismatch. Do not mix Studio, A8, or another model's "
            "DLLs. Mismatched: " + ", ".join(mismatched)
        )

    assert_sdk_abi_layout()
    return SeveninvensunRuntimeManifest(
        sdk_root=root,
        bin_directory=bin_directory,
        config_directory=config_directory,
        aseex_sha256=actual_hashes["bin/aSeeX.dll"],
        usersdk_sha256=actual_hashes["bin/USERSDK.dll"],
        validated_file_count=len(actual_hashes),
        runtime_bundle_sha256=_runtime_bundle_hash(actual_hashes),
    )


def _is_valid(mask: int, position: int) -> bool:
    return bool(mask & (1 << position))


def _number(value: Any) -> float | None:
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _point2(point: _Point2D) -> list[float | None]:
    return [_number(point.x), _number(point.y)]


def _point3(point: _Point3D) -> list[float | None]:
    return [_number(point.x), _number(point.y), _number(point.z)]


def _array(values: Any) -> list[float | None]:
    return [_number(value) for value in values]


def _top_left_scene_point(
    point: _Point3D,
    scene_dimensions: tuple[int, int] | None,
) -> list[float | None] | None:
    if scene_dimensions is None:
        return None
    x = _number(point.x)
    y = _number(point.y)
    z = _number(point.z)
    if not isinstance(x, float) or not isinstance(y, float):
        return None
    width, height = scene_dimensions
    return [x + (width / 2.0), y + (height / 2.0), z]


def _gaze_payload(
    gaze: _GazePoint,
    scene_dimensions: tuple[int, int] | None,
) -> dict[str, Any]:
    mask = int(gaze.gaze_bit_mask)

    def valid_point(name: str, value: _Point3D) -> list[float | None] | None:
        return _point3(value) if _is_valid(mask, GAZE_VALIDITY[name]) else None

    gaze_point = valid_point("gaze_point", gaze.gaze_point)
    return {
        "validity_mask": mask,
        "valid": gaze_point is not None,
        "gaze_point_scene_centered_px": gaze_point,
        "gaze_point_scene_top_left_px": (
            _top_left_scene_point(gaze.gaze_point, scene_dimensions)
            if gaze_point is not None
            else None
        ),
        "raw_point_scene_centered_px": valid_point("raw_point", gaze.raw_point),
        "smooth_point_scene_centered_px": valid_point("smooth_point", gaze.smooth_point),
        "origin_vendor_units": valid_point("origin", gaze.gaze_origin),
        "direction": valid_point("direction", gaze.gaze_direction),
        "confidence_re": (
            _number(gaze.re) if _is_valid(mask, GAZE_VALIDITY["confidence"]) else None
        ),
        "extra_data_validity_mask": int(gaze.ex_data_bit_mask),
        "calibration_score": (
            _number(gaze.ex_data[0]) if _is_valid(int(gaze.ex_data_bit_mask), 0) else None
        ),
    }


def _pupil_payload(
    pupil: _PupilInfo,
    eye_dimensions: tuple[int, int] | None,
) -> dict[str, Any]:
    mask = int(pupil.pupil_bit_mask)
    center = _point2(pupil.pupil_center) if _is_valid(mask, PUPIL_VALIDITY["center"]) else None
    diameter = (
        _number(pupil.pupil_diameter)
        if _is_valid(mask, PUPIL_VALIDITY["diameter_normalized"])
        else None
    )
    minor_axis = (
        _number(pupil.pupil_minor_axis)
        if _is_valid(mask, PUPIL_VALIDITY["minor_axis_normalized"])
        else None
    )
    center_px: list[float] | None = None
    diameter_px: float | None = None
    minor_axis_px: float | None = None
    if eye_dimensions is not None:
        width, height = eye_dimensions
        if center is not None:
            center_x, center_y = center
            if center_x is not None and center_y is not None:
                center_px = [center_x * width, center_y * height]
        if isinstance(diameter, float):
            diameter_px = diameter * width
        if isinstance(minor_axis, float):
            minor_axis_px = minor_axis * width
    return {
        "validity_mask": mask,
        "extra_data_validity_mask": int(pupil.ex_data_bit_mask),
        "center_normalized": center,
        "center_px": center_px,
        "distance_mm": (
            _number(pupil.pupil_distance)
            if _is_valid(mask, PUPIL_VALIDITY["distance_mm"])
            else None
        ),
        "diameter_normalized_by_image_width": diameter,
        "diameter_px": diameter_px,
        "diameter_mm": (
            _number(pupil.pupil_diameter_mm)
            if _is_valid(mask, PUPIL_VALIDITY["diameter_mm"])
            else None
        ),
        "minor_axis_normalized_by_image_width": minor_axis,
        "minor_axis_px": minor_axis_px,
        "minor_axis_mm": (
            _number(pupil.pupil_minor_axis_mm)
            if _is_valid(mask, PUPIL_VALIDITY["minor_axis_mm"])
            else None
        ),
    }


def _eye_extra_payload(
    extra: _EyeExtraData,
    eye_dimensions: tuple[int, int] | None,
) -> dict[str, Any]:
    mask = int(extra.eye_data_ex_bit_mask)
    eyelid_up = (
        _number(extra.eyelid_up) if _is_valid(mask, EYE_EXTRA_VALIDITY["eyelid_up"]) else None
    )
    eyelid_down = (
        _number(extra.eyelid_down) if _is_valid(mask, EYE_EXTRA_VALIDITY["eyelid_down"]) else None
    )
    eyelid_distance_normalized: float | None = None
    eyelid_distance_px: float | None = None
    if isinstance(eyelid_up, float) and isinstance(eyelid_down, float):
        eyelid_distance_normalized = abs(eyelid_down - eyelid_up)
        if eye_dimensions is not None:
            eyelid_distance_px = eyelid_distance_normalized * eye_dimensions[1]
    location = [
        _number(extra.ex_data[index]) if _is_valid(int(extra.ex_data_bit_mask), index) else None
        for index in range(3)
    ]
    return {
        "validity_mask": mask,
        "extra_data_validity_mask": int(extra.ex_data_bit_mask),
        "blink_state": (int(extra.blink) if _is_valid(mask, EYE_EXTRA_VALIDITY["blink"]) else None),
        "openness_percent": (
            _number(extra.openness) if _is_valid(mask, EYE_EXTRA_VALIDITY["openness"]) else None
        ),
        "eyelid_up_normalized": eyelid_up,
        "eyelid_down_normalized": eyelid_down,
        "eyelid_distance_normalized": eyelid_distance_normalized,
        "eyelid_distance_px": eyelid_distance_px,
        "pupil_location_vendor": location if any(value is not None for value in location) else None,
    }


def _image_dimensions(
    image_metadata: dict[str, dict[str, int]] | None,
    name: str,
) -> tuple[int, int] | None:
    if image_metadata is None:
        return None
    metadata = image_metadata.get(name)
    if not metadata:
        return None
    width = metadata.get("width")
    height = metadata.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None
    return width, height


def _top_left_scene_point2(
    point: _Point2D,
    scene_dimensions: tuple[int, int] | None,
) -> list[float] | None:
    if scene_dimensions is None:
        return None
    x = _number(point.x)
    y = _number(point.y)
    if x is None or y is None:
        return None
    width, height = scene_dimensions
    return [x + (width / 2.0), y + (height / 2.0)]


def decode_sdk_sample(
    raw_data: bytes,
    *,
    sequence: int,
    host_monotonic_ns: int,
    host_wall_time_ns: int,
    image_metadata: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Decode one copied callback structure into strict, raw JSON-compatible data."""
    if len(raw_data) != ctypes.sizeof(_EyeDataEx):
        raise SeveninvensunSdkError(
            f"Expected {ctypes.sizeof(_EyeDataEx)} callback bytes, got {len(raw_data)}."
        )
    sample = _EyeDataEx.from_buffer_copy(raw_data)
    scene_dimensions = _image_dimensions(image_metadata, "scene")
    left_dimensions = _image_dimensions(image_metadata, "left_eye")
    right_dimensions = _image_dimensions(image_metadata, "right_eye")
    state_names = {
        0: "none",
        1: "saccade_moving",
        2: "saccade_end",
        3: "fixation_staying",
        4: "fixation_end",
    }
    stats_valid = int(sample.stats.count) > 0
    return {
        "protocol": SEVENINVENSUN_GLASSES_RAW_PROTOCOL,
        "raw_callback_size_bytes": len(raw_data),
        "sequence": sequence,
        "host_monotonic_ns": host_monotonic_ns,
        "host_wall_time_ns": host_wall_time_ns,
        "device_timestamp_raw": int(sample.timestamp),
        "device_timestamp_unit": "vendor_unspecified",
        "device_mode": "wearable_glasses",
        "coordinate_space": "scene_camera_centered_px",
        "screen_mapping_status": "not_mapped",
        "recommended_eye_code_vendor": int(sample.recommend),
        "recommended_eye_code_status": (
            "header_defines_0_none_1_left_2_right_but_manual_marks_deprecated_meaningless"
        ),
        "scene_axis_status": "vendor_samples_use_x_right_y_down_live_validation_required",
        "validity": {
            "eye": _is_valid(int(sample.recom_gaze.gaze_bit_mask), 0),
            "left": _is_valid(int(sample.left_gaze.gaze_bit_mask), 0),
            "right": _is_valid(int(sample.right_gaze.gaze_bit_mask), 0),
        },
        "gaze": {
            "recommended": _gaze_payload(sample.recom_gaze, scene_dimensions),
            "left": _gaze_payload(sample.left_gaze, scene_dimensions),
            "right": _gaze_payload(sample.right_gaze, scene_dimensions),
        },
        "pupil": {
            "left": _pupil_payload(sample.left_pupil, left_dimensions),
            "right": _pupil_payload(sample.right_pupil, right_dimensions),
        },
        "eye_extra": {
            "left": _eye_extra_payload(sample.left_ex_data, left_dimensions),
            "right": _eye_extra_payload(sample.right_ex_data, right_dimensions),
        },
        "fixation_saccade": {
            "valid": stats_valid,
            "state": state_names.get(int(sample.stats.state), "unknown"),
            "state_code": int(sample.stats.state),
            "timestamp_us": int(sample.stats.timestamp) if stats_valid else None,
            "duration_us": int(sample.stats.duration) if stats_valid else None,
            "sample_count": int(sample.stats.count) if stats_valid else None,
            "center_scene_centered_px": _point2(sample.stats.center) if stats_valid else None,
            "center_scene_top_left_px": (
                _top_left_scene_point2(sample.stats.center, scene_dimensions)
                if stats_valid
                else None
            ),
        },
        "imu": {
            "timestamp_raw": int(sample.gyro.timestamp),
            "timestamp_unit": "vendor_unspecified",
            "quaternion_vendor_order": _array(sample.gyro.quat_data),
            "gyroscope_vendor_units": _array(sample.gyro.gyro),
            "accelerometer_vendor_units": _array(sample.gyro.accel),
            "magnetometer_vendor_units": _array(sample.gyro.mag),
            "validity": "sdk_has_no_validity_mask",
        },
        "image_metadata_snapshot_kind": "latest_at_decode",
        "image_metadata": image_metadata or {},
    }


class SeveninvensunGlassesSdkCapture:
    """Own the vendor SDK in a dedicated helper process and copy callbacks quickly."""

    def __init__(
        self,
        sdk_root: str | Path,
        *,
        queue_capacity: int = 8192,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive.")
        self.manifest = validate_sdk_runtime(sdk_root)
        self._queue: queue.Queue[_QueuedSample] = queue.Queue(maxsize=queue_capacity)
        self._lock = threading.Lock()
        self._gaze_callback_gate = threading.Lock()
        self._accepting_gaze_callbacks = True
        self._post_stop_callback_samples = 0
        self._last_images: dict[str, dict[str, int]] = {}
        self._camera_state: int | None = None
        self._next_sequence = 0
        self._dropped_callback_samples = 0
        self._discarded_callback_samples = 0
        self._sample_window_host_cutoff_ns: int | None = None
        self._dll: Any = None
        self._dll_directory_handle: Any = None
        self._callbacks: tuple[Any, ...] = ()
        self._calibration_callbacks: tuple[Any, ...] = ()
        self._calibration_events: queue.Queue[SeveninvensunCalibrationEvent] = queue.Queue()
        self._calibration_point_count: int | None = None
        self._active_calibration_index: int | None = None
        self._calibration_finish_status: dict[int, dict[str, int | None]] = {}
        self._successful_calibration_indices: set[int] = set()
        self._next_calibration_token = 1
        self._active_calibration_token: int | None = None
        self._active_calibration_expected_eyes: set[str] = set()
        self._active_calibration_cancel_requested = False
        self._active_calibration_start_pending = False
        self._calibration_point_terminal = threading.Event()
        self._calibration_point_terminal.set()
        self._h265_dll: Any = None
        self._h265_initialized = False
        self._scene_encoded_queue: queue.Queue[_QueuedSceneImage] = queue.Queue(maxsize=8)
        self._scene_decoder_thread: threading.Thread | None = None
        self._scene_decoder_stop = threading.Event()
        self._scene_decoder_ready = threading.Event()
        self._scene_decoder_init_result: int | None = None
        self._scene_decoder_error: Exception | None = None
        self._scene_buffer: Any = None
        self._scene_dimensions: tuple[int, int] | None = None
        self._latest_scene_frame: SeveninvensunSceneFrame | None = None
        self._scene_frame_sequence = 0
        self._dropped_scene_images = 0
        self._rejected_scene_images = 0
        self._scene_decode_return_codes: dict[int, int] = {}
        self._scene_decoder_release_result: int | None = None
        self._started = False
        self._start_attempted = False
        self._start_was_attempted = False
        self._authorized = False
        self._authorization_was_established = False
        self._closed = False
        self._stop_result: int | None = None
        self._disconnect_result: int | None = None
        self._calibration_complete_result: int | None = None

    @property
    def dropped_callback_samples(self) -> int:
        with self._lock:
            return self._dropped_callback_samples

    @property
    def received_callback_samples(self) -> int:
        """Return every gaze callback observed, including queued and dropped callbacks."""
        with self._lock:
            return self._next_sequence

    @property
    def discarded_callback_samples(self) -> int:
        with self._lock:
            return self._discarded_callback_samples

    @property
    def pending_callback_samples(self) -> int:
        """Return callbacks still queued after native stop."""
        return self._queue.qsize()

    @property
    def post_stop_callback_samples(self) -> int:
        """Return callbacks rejected after the stop barrier was sealed."""
        with self._gaze_callback_gate:
            return self._post_stop_callback_samples

    @property
    def camera_state(self) -> int | None:
        with self._lock:
            return self._camera_state

    @property
    def image_metadata(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {name: dict(metadata) for name, metadata in self._last_images.items()}

    @property
    def stop_result(self) -> int | None:
        return self._stop_result

    @property
    def disconnect_result(self) -> int | None:
        return self._disconnect_result

    @property
    def authorization_was_established(self) -> bool:
        return self._authorization_was_established

    @property
    def scene_decode_return_codes(self) -> dict[int, int]:
        with self._lock:
            return dict(self._scene_decode_return_codes)

    @property
    def dropped_scene_images(self) -> int:
        with self._lock:
            return self._dropped_scene_images

    @property
    def rejected_scene_images(self) -> int:
        with self._lock:
            return self._rejected_scene_images

    @property
    def scene_decoder_release_result(self) -> int | None:
        return self._scene_decoder_release_result

    @property
    def calibration_complete_result(self) -> int | None:
        return self._calibration_complete_result

    @property
    def calibration_active(self) -> bool:
        with self._lock:
            return self._calibration_point_count is not None

    @property
    def scene_decoder_running(self) -> bool:
        thread = self._scene_decoder_thread
        return thread is not None and thread.is_alive() and self._scene_decoder_error is None

    @property
    def scene_decoder_error(self) -> str | None:
        error = self._scene_decoder_error
        return None if error is None else f"{type(error).__name__}: {error}"

    def load(self) -> str:
        """Load the validated DLL and return its reported version."""
        if self._closed:
            raise SeveninvensunSdkError("A closed SDK capture cannot be reused.")
        if sys.platform != "win32" or sys.maxsize <= 2**32:
            raise SeveninvensunSdkError("aSeeGlasses Plus direct capture requires 64-bit Windows.")
        if self._dll is not None:
            return self.version()

        # Recheck immediately before LoadLibrary to narrow the validation/load window.
        self.manifest = validate_sdk_runtime(self.manifest.sdk_root)
        add_dll_directory = getattr(os, "add_dll_directory", None)
        if add_dll_directory is not None:
            self._dll_directory_handle = add_dll_directory(str(self.manifest.bin_directory))
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise SeveninvensunSdkError("ctypes.WinDLL is unavailable on this Python runtime.")
        try:
            self._dll = win_dll(str(self.manifest.bin_directory / "aSeeX.dll"))
        except OSError as error:
            raise SeveninvensunSdkError(
                "Unable to load aSeeX.dll and its SDK dependencies from the validated bin folder."
            ) from error
        self._configure_functions()
        return self.version()

    def _configure_functions(self) -> None:
        callback_factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        state_callback = callback_factory(None, ctypes.c_int, ctypes.c_void_p)
        image_callback = callback_factory(
            None,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_longlong,
            ctypes.c_void_p,
        )
        gaze_callback = callback_factory(
            None,
            ctypes.POINTER(_EyeDataEx),
            ctypes.c_void_p,
        )
        point_process_callback = callback_factory(
            None,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
        )
        point_finish_callback = callback_factory(
            None,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
        )
        self._callbacks = (
            state_callback(self._on_camera_state),
            image_callback(self._on_image),
            gaze_callback(self._on_gaze),
        )
        self._calibration_callbacks = (
            point_process_callback(self._on_left_calibration_progress),
            point_finish_callback(self._on_left_calibration_finish),
            point_process_callback(self._on_right_calibration_progress),
            point_finish_callback(self._on_right_calibration_finish),
        )

        self._dll._7i_set_camera_state_callback.argtypes = [state_callback, ctypes.c_void_p]
        self._dll._7i_set_image_callback.argtypes = [image_callback, ctypes.c_void_p]
        self._dll._7i_set_gaze_callback.argtypes = [gaze_callback, ctypes.c_void_p]
        self._dll._7i_device_connect.argtypes = [ctypes.c_char_p, ctypes.POINTER(_UKeyInfo)]
        self._dll._7i_device_disconnect.argtypes = []
        self._dll._7i_start.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self._dll._7i_stop.argtypes = []
        self._dll._7i_start_calibration.argtypes = [ctypes.c_int]
        self._dll._7i_start_calibration_point.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(_Point2D),
            point_process_callback,
            ctypes.c_void_p,
            point_finish_callback,
            ctypes.c_void_p,
        ]
        self._dll._7i_cancel_calibration.argtypes = [ctypes.c_int]
        self._dll._7i_compute_calibration.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(_Coefficient),
        ]
        self._dll._7i_complete_calibration.argtypes = []
        self._dll._7i_get_data.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_int),
        ]
        self._dll._7i_set_data.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
        ]
        self._dll._7i_get_version.argtypes = [ctypes.POINTER(ctypes.c_char), ctypes.c_int]
        self._dll._7i_get_mpu_id.argtypes = [ctypes.POINTER(ctypes.c_char), ctypes.c_int]
        for name in (
            "_7i_set_camera_state_callback",
            "_7i_set_image_callback",
            "_7i_set_gaze_callback",
            "_7i_device_connect",
            "_7i_device_disconnect",
            "_7i_start",
            "_7i_stop",
            "_7i_start_calibration",
            "_7i_start_calibration_point",
            "_7i_cancel_calibration",
            "_7i_compute_calibration",
            "_7i_complete_calibration",
            "_7i_get_data",
            "_7i_set_data",
            "_7i_get_version",
            "_7i_get_mpu_id",
        ):
            getattr(self._dll, name).restype = ctypes.c_int

    def version(self) -> str:
        if self._dll is None:
            raise SeveninvensunSdkError("SDK has not been loaded.")
        buffer = ctypes.create_string_buffer(1024)
        result = int(self._dll._7i_get_version(buffer, len(buffer)))
        if result != 0:
            raise SeveninvensunSdkCallError("_7i_get_version", result)
        return buffer.value.decode("utf-8", errors="replace").strip()

    def get_mpu_id_hash(self) -> str:
        """Hash the connected device identifier without exposing or retaining it."""
        if self._dll is None:
            raise SeveninvensunSdkError("SDK has not been loaded.")
        if not self._started:
            raise SeveninvensunSdkError("Start the connected device before reading its ID.")
        buffer = ctypes.create_string_buffer(256)
        try:
            result = int(self._dll._7i_get_mpu_id(buffer, len(buffer)))
            if result != 0:
                raise SeveninvensunSdkCallError("_7i_get_mpu_id", result)
            buffer_bytes = bytes(buffer)
            terminator = buffer_bytes.find(b"\x00")
            if terminator <= 0:
                raise SeveninvensunSdkError(
                    "The SDK returned an empty or unterminated device identifier."
                )
            raw_identifier = buffer_bytes[:terminator]
            return hashlib.sha256(b"oculidoc-asee-mpu-v1\x00" + raw_identifier).hexdigest()
        finally:
            ctypes.memset(buffer, 0, len(buffer))

    def authorize(self, password: str) -> int:
        """Authorize a UKey without retaining or exposing its identifying response."""
        if self._closed:
            raise SeveninvensunSdkError("A closed SDK capture cannot be reused.")
        if self._start_was_attempted:
            raise SeveninvensunSdkError("SDK capture objects are one-shot and cannot be reused.")
        if self._dll is None:
            raise SeveninvensunSdkError("SDK has not been loaded.")
        if not password:
            raise ValueError("authorization password cannot be empty.")
        encoded = bytearray(password.encode("utf-8"))
        secret = ctypes.create_string_buffer(bytes(encoded), len(encoded) + 1)
        info = _UKeyInfo()
        try:
            result = int(self._dll._7i_device_connect(secret, ctypes.byref(info)))
        finally:
            ctypes.memset(secret, 0, len(secret))
            ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
            for index in range(len(encoded)):
                encoded[index] = 0
        if result != 0:
            raise SeveninvensunSdkCallError("_7i_device_connect", result)
        self._authorized = True
        self._authorization_was_established = True
        return result

    def start(
        self,
        *,
        environment: str = "indoor",
        resolution: str = "1280x960",
        gyroscope: bool = True,
    ) -> None:
        if self._closed:
            raise SeveninvensunSdkError("A closed SDK capture cannot be reused.")
        if self._dll is None:
            raise SeveninvensunSdkError("SDK has not been loaded.")
        if self._start_was_attempted:
            raise SeveninvensunSdkError("SDK capture objects are one-shot and cannot be reused.")
        if self._started:
            raise SeveninvensunSdkError("SDK capture is already started.")
        try:
            environment_code = ENVIRONMENTS[environment]
            resolution_code = RESOLUTIONS[resolution]
        except KeyError as error:
            raise ValueError(f"Unsupported SDK setting: {error.args[0]}") from error

        self._start_was_attempted = True
        # Callback registration is part of the native start transaction.  If a
        # later registration fails, still call _7i_stop during cleanup and keep
        # this object terminal because the vendor's partial native state is
        # unknowable.
        self._start_attempted = True
        for function, callback in zip(
            (
                self._dll._7i_set_camera_state_callback,
                self._dll._7i_set_image_callback,
                self._dll._7i_set_gaze_callback,
            ),
            self._callbacks,
            strict=True,
        ):
            result = int(function(callback, None))
            if result != 0:
                raise SeveninvensunSdkCallError(function.__name__, result)

        config_path = str(self.manifest.config_directory).encode("utf-8")
        result = int(
            self._dll._7i_start(
                config_path,
                environment_code,
                resolution_code,
                int(gyroscope),
            )
        )
        if result != 0:
            raise SeveninvensunSdkCallError("_7i_start", result)
        self._started = True

    def start_scene_decoder(self, resolution: str) -> None:
        """Start the H.265 decoder used only by the standalone calibration helper."""
        if self._closed:
            raise SeveninvensunSdkError("A closed SDK capture cannot be reused.")
        if self._dll is None:
            raise SeveninvensunSdkError("SDK has not been loaded.")
        if self._started:
            raise SeveninvensunSdkError("Start the scene decoder before starting the SDK.")
        if self._scene_decoder_thread is not None:
            raise SeveninvensunSdkError("Scene decoder is already started.")
        try:
            dimensions = RESOLUTION_DIMENSIONS[resolution]
        except KeyError as error:
            raise ValueError(f"Unsupported SDK resolution: {resolution}") from error

        self._scene_dimensions = dimensions
        self._scene_decoder_stop.clear()
        self._scene_decoder_ready.clear()
        self._scene_decoder_init_result = None
        self._scene_decoder_error = None
        self._scene_decoder_release_result = None
        self._scene_decoder_thread = threading.Thread(
            target=self._scene_decoder_loop,
            name="oculidoc-asee-scene-decoder",
            daemon=True,
        )
        self._scene_decoder_thread.start()
        if not self._scene_decoder_ready.wait(timeout=5.0):
            self.stop_scene_decoder()
            raise SeveninvensunSdkError("Timed out while initializing the scene decoder.")
        if self._scene_decoder_error is not None:
            error = self._scene_decoder_error
            self.stop_scene_decoder()
            raise SeveninvensunSdkError("Unable to initialize the scene decoder.") from error
        if self._scene_decoder_init_result is None:
            self.stop_scene_decoder()
            raise SeveninvensunSdkError("Scene decoder returned no initialization result.")
        if self._scene_decoder_init_result != 0:
            result = self._scene_decoder_init_result
            self.stop_scene_decoder()
            raise SeveninvensunSdkCallError("_7i_h265_init", int(result))

    def stop_scene_decoder(self) -> None:
        """Stop the decoder thread and release its native state."""
        thread = self._scene_decoder_thread
        if thread is None:
            return
        self._scene_decoder_stop.set()
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise SeveninvensunSdkError("Scene decoder did not stop within five seconds.")
        self._scene_decoder_thread = None

    def latest_scene_frame(self) -> SeveninvensunSceneFrame | None:
        """Return the newest immutable decoded frame without blocking."""
        with self._lock:
            return self._latest_scene_frame

    def _scene_decoder_loop(self) -> None:
        dimensions = self._scene_dimensions
        try:
            if dimensions is None:
                raise SeveninvensunSdkError("Scene dimensions were not configured.")
            win_dll = getattr(ctypes, "WinDLL", None)
            if win_dll is None:
                raise SeveninvensunSdkError("ctypes.WinDLL is unavailable on this runtime.")
            self._h265_dll = win_dll(str(self.manifest.bin_directory / "H265Decode.dll"))
            self._h265_dll._7i_h265_init.argtypes = [ctypes.c_uint, ctypes.c_uint]
            self._h265_dll._7i_h265_decode.argtypes = [
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            self._h265_dll._7i_h265_release.argtypes = []
            self._h265_dll._7i_h265_init.restype = ctypes.c_int
            self._h265_dll._7i_h265_decode.restype = ctypes.c_int
            self._h265_dll._7i_h265_release.restype = ctypes.c_int

            width, height = dimensions
            buffer_size = width * height * 3
            self._scene_buffer = (ctypes.c_ubyte * buffer_size)()
            self._scene_decoder_init_result = int(self._h265_dll._7i_h265_init(width, height))
            self._h265_initialized = self._scene_decoder_init_result == 0
            self._scene_decoder_ready.set()
            if not self._h265_initialized:
                return

            while not self._scene_decoder_stop.is_set() or not self._scene_encoded_queue.empty():
                try:
                    encoded = self._scene_encoded_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if (encoded.width, encoded.height) != dimensions:
                    with self._lock:
                        self._scene_decode_return_codes[-7001] = (
                            self._scene_decode_return_codes.get(-7001, 0) + 1
                        )
                    continue
                encoded_buffer = (ctypes.c_ubyte * len(encoded.encoded_bytes)).from_buffer_copy(
                    encoded.encoded_bytes
                )
                result = int(
                    self._h265_dll._7i_h265_decode(
                        len(encoded.encoded_bytes),
                        encoded_buffer,
                        self._scene_buffer,
                        buffer_size,
                    )
                )
                with self._lock:
                    self._scene_decode_return_codes[result] = (
                        self._scene_decode_return_codes.get(result, 0) + 1
                    )
                    if result != buffer_size:
                        continue
                    sequence = self._scene_frame_sequence
                    self._scene_frame_sequence += 1
                frame = SeveninvensunSceneFrame(
                    bgr_bytes=bytes(self._scene_buffer),
                    width=encoded.width,
                    height=encoded.height,
                    device_timestamp_raw=encoded.device_timestamp_raw,
                    host_monotonic_ns=encoded.host_monotonic_ns,
                    sequence=sequence,
                    decoder_result_raw=result,
                )
                with self._lock:
                    self._latest_scene_frame = frame
        except Exception as error:  # noqa: BLE001 - relay native decoder failures to caller
            self._scene_decoder_error = error
            self._scene_decoder_ready.set()
        finally:
            if self._h265_initialized and self._h265_dll is not None:
                try:
                    self._scene_decoder_release_result = int(self._h265_dll._7i_h265_release())
                except Exception as error:  # noqa: BLE001 - preserve for diagnostics
                    if self._scene_decoder_error is None:
                        self._scene_decoder_error = error
            self._h265_initialized = False
            self._scene_decoder_ready.set()

    def begin_calibration(self, points: int = 3) -> None:
        """Begin one official one- or three-point calibration session."""
        if points not in CALIBRATION_POINT_COUNTS:
            raise ValueError("aSeeGlasses Plus supports only one- or three-point calibration.")
        if not self._started:
            raise SeveninvensunSdkError("Start the SDK before calibration.")
        with self._lock:
            if self._calibration_point_count is not None:
                raise SeveninvensunSdkError("A calibration session is already active.")
        result = int(self._dll._7i_start_calibration(points))
        if result != 0:
            raise SeveninvensunSdkCallError("_7i_start_calibration", result)
        while True:
            try:
                self._calibration_events.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._calibration_point_count = points
            self._active_calibration_index = None
            self._active_calibration_token = None
            self._active_calibration_expected_eyes = set()
            self._active_calibration_cancel_requested = False
            self._active_calibration_start_pending = False
            self._calibration_point_terminal.set()
            self._calibration_finish_status = {}
            self._successful_calibration_indices = set()
            self._calibration_complete_result = None

    def start_calibration_point(
        self,
        index: int,
        x_centered_px: float,
        y_centered_px: float,
    ) -> None:
        """Start both eyes for one 1-based native scene-pixel calibration point."""
        with self._lock:
            point_count = self._calibration_point_count
            active_index = self._active_calibration_index
            expected_index = len(self._successful_calibration_indices) + 1
        if point_count is None:
            raise SeveninvensunSdkError("Calibration has not been started.")
        if active_index is not None:
            raise SeveninvensunSdkError("Wait for both eyes to finish the active point.")
        if index != expected_index or not 1 <= index <= point_count:
            raise ValueError(f"Expected calibration point index {expected_index}.")
        if not math.isfinite(x_centered_px) or not math.isfinite(y_centered_px):
            raise ValueError("Calibration point coordinates must be finite.")
        if self._scene_dimensions is None:
            raise SeveninvensunSdkError("A decoded scene stream is required for calibration.")
        width, height = self._scene_dimensions
        if not (-width / 2 <= x_centered_px < width / 2):
            raise ValueError("Calibration X coordinate is outside the scene frame.")
        if not (-height / 2 <= y_centered_px < height / 2):
            raise ValueError("Calibration Y coordinate is outside the scene frame.")

        point = _Point2D(float(x_centered_px), float(y_centered_px))
        with self._lock:
            token = self._next_calibration_token
            self._next_calibration_token += 1
            self._active_calibration_index = index
            self._active_calibration_token = token
            self._active_calibration_expected_eyes = {"left", "right"}
            self._active_calibration_cancel_requested = False
            self._active_calibration_start_pending = True
            self._calibration_point_terminal.clear()
            self._calibration_finish_status[index] = {"left": None, "right": None}
        progress_left, finish_left, progress_right, finish_right = self._calibration_callbacks
        context = ctypes.c_void_p(token)
        left_result = int(
            self._dll._7i_start_calibration_point(
                LEFT_EYE,
                index,
                ctypes.byref(point),
                progress_left,
                context,
                finish_left,
                context,
            )
        )
        right_result = int(
            self._dll._7i_start_calibration_point(
                RIGHT_EYE,
                index,
                ctypes.byref(point),
                progress_right,
                context,
                finish_right,
                context,
            )
        )
        started_eyes = {
            eye for eye, result in (("left", left_result), ("right", right_result)) if result == 0
        }
        with self._lock:
            if self._active_calibration_token == token:
                self._active_calibration_expected_eyes = set(started_eyes)
                self._active_calibration_cancel_requested = left_result != 0 or right_result != 0
                self._active_calibration_start_pending = False
                self._finish_active_calibration_point_if_ready_locked(index)
        if left_result == 0 and right_result == 0:
            return
        cancel_results = self._cancel_started_calibration_eyes(started_eyes)
        if started_eyes and not self._calibration_point_terminal.wait(timeout=3.0):
            raise SeveninvensunSdkError(
                "A partially started calibration point did not reach its terminal callback state."
            )
        for eye in started_eyes:
            cancel_result = cancel_results[eye]
            if cancel_result != 0:
                raise SeveninvensunSdkCallError("_7i_cancel_calibration", cancel_result)
        failed_result = left_result if left_result != 0 else right_result
        raise SeveninvensunSdkCallError("_7i_start_calibration_point", failed_result)

    def read_calibration_event(
        self,
        timeout_seconds: float = 0.0,
    ) -> SeveninvensunCalibrationEvent | None:
        """Read one copied progress/finish callback event."""
        try:
            return self._calibration_events.get(timeout=max(0.0, timeout_seconds))
        except queue.Empty:
            return None

    def cancel_calibration_point(
        self,
        *,
        timeout_seconds: float = 3.0,
    ) -> tuple[int, int] | None:
        """Cancel both eyes and wait for both documented terminal callbacks."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        with self._lock:
            token = self._active_calibration_token
            if token is not None:
                self._active_calibration_cancel_requested = True
        if token is None:
            return None
        results = self._cancel_calibration_eyes()
        if not self._calibration_point_terminal.wait(timeout=timeout_seconds):
            raise SeveninvensunSdkError(
                "Calibration cancellation did not receive both terminal callbacks."
            )
        for result in results:
            if result != 0:
                raise SeveninvensunSdkCallError("_7i_cancel_calibration", result)
        return results

    def _cancel_calibration_eyes(self) -> tuple[int, int]:
        return (
            int(self._dll._7i_cancel_calibration(LEFT_EYE)),
            int(self._dll._7i_cancel_calibration(RIGHT_EYE)),
        )

    def _cancel_started_calibration_eyes(self, eyes: set[str]) -> dict[str, int]:
        results: dict[str, int] = {}
        if "left" in eyes:
            results["left"] = int(self._dll._7i_cancel_calibration(LEFT_EYE))
        if "right" in eyes:
            results["right"] = int(self._dll._7i_cancel_calibration(RIGHT_EYE))
        return results

    def _clear_active_calibration_point(self, token: int) -> None:
        with self._lock:
            if self._active_calibration_token == token:
                self._active_calibration_index = None
                self._active_calibration_token = None
                self._active_calibration_expected_eyes = set()
                self._active_calibration_cancel_requested = False
                self._active_calibration_start_pending = False
                self._calibration_point_terminal.set()

    def _finish_active_calibration_point_if_ready_locked(self, index: int) -> None:
        statuses = self._calibration_finish_status.get(index)
        if statuses is None:
            return
        if self._active_calibration_start_pending:
            return
        expected = self._active_calibration_expected_eyes
        if any(statuses[eye] is None for eye in expected):
            return
        if (
            expected == {"left", "right"}
            and not self._active_calibration_cancel_requested
            and statuses == {"left": 0, "right": 0}
        ):
            self._successful_calibration_indices.add(index)
        self._active_calibration_index = None
        self._active_calibration_token = None
        self._active_calibration_expected_eyes = set()
        self._active_calibration_cancel_requested = False
        self._active_calibration_start_pending = False
        self._calibration_point_terminal.set()

    def compute_calibration_coefficients(self) -> tuple[bytes, bytes, str | None]:
        """Compute, retrieve and complete a successful calibration session."""
        with self._lock:
            point_count = self._calibration_point_count
            active_index = self._active_calibration_index
            successful = set(self._successful_calibration_indices)
        if point_count is None:
            raise SeveninvensunSdkError("Calibration has not been started.")
        if active_index is not None:
            raise SeveninvensunSdkError("The active calibration point is not finished.")
        if successful != set(range(1, point_count + 1)):
            raise SeveninvensunSdkError("Every configured point must succeed for both eyes.")

        left = _Coefficient()
        right = _Coefficient()
        primary_error: Exception | None = None
        result_payload: tuple[bytes, bytes, str | None] | None = None
        try:
            left_result = int(self._dll._7i_compute_calibration(LEFT_EYE, ctypes.byref(left)))
            right_result = int(self._dll._7i_compute_calibration(RIGHT_EYE, ctypes.byref(right)))
            if left_result != 0:
                raise SeveninvensunSdkCallError("_7i_compute_calibration", left_result)
            if right_result != 0:
                raise SeveninvensunSdkCallError("_7i_compute_calibration", right_result)

            self._get_calibration_blob(b"biLeft", left)
            self._get_calibration_blob(b"biRight", right)
            score_buffer = (ctypes.c_ubyte * 256)()
            score_size = ctypes.c_int(len(score_buffer))
            score_result = int(
                self._dll._7i_get_data(
                    b"get_score",
                    len(b"get_score"),
                    score_buffer,
                    ctypes.byref(score_size),
                )
            )
            score = None
            if score_result == 0 and 0 <= score_size.value <= len(score_buffer):
                score = (
                    bytes(score_buffer[: score_size.value])
                    .rstrip(b"\x00")
                    .decode("utf-8", errors="replace")
                )
            result_payload = (bytes(left.buf), bytes(right.buf), score or None)
        except Exception as error:  # noqa: BLE001 - complete native session before re-raising
            primary_error = error
        finally:
            try:
                self._complete_calibration()
            except Exception as error:  # noqa: BLE001 - retain the primary compute failure
                if primary_error is None:
                    primary_error = error
            ctypes.memset(ctypes.byref(left), 0, ctypes.sizeof(left))
            ctypes.memset(ctypes.byref(right), 0, ctypes.sizeof(right))
        if primary_error is not None:
            raise primary_error
        if result_payload is None:
            raise SeveninvensunSdkError("Calibration produced no coefficient pair.")
        return result_payload

    def _get_calibration_blob(self, key: bytes, coefficient: _Coefficient) -> None:
        size = ctypes.c_int(CALIBRATION_COEFFICIENT_SIZE)
        result = int(self._dll._7i_get_data(key, len(key), coefficient.buf, ctypes.byref(size)))
        if result != 0:
            raise SeveninvensunSdkCallError("_7i_get_data", result)
        if size.value != CALIBRATION_COEFFICIENT_SIZE:
            raise SeveninvensunSdkError(
                "The SDK returned a calibration coefficient with an unexpected size."
            )

    def _complete_calibration(self) -> None:
        with self._lock:
            active = self._calibration_point_count is not None
            active_point = self._active_calibration_token is not None
        if not active:
            return
        if active_point:
            raise SeveninvensunSdkError(
                "Cannot complete calibration before the active point is terminal."
            )
        result = int(self._dll._7i_complete_calibration())
        self._calibration_complete_result = result
        with self._lock:
            self._calibration_point_count = None
            self._active_calibration_index = None
            self._active_calibration_token = None
            self._active_calibration_expected_eyes = set()
            self._active_calibration_cancel_requested = False
            self._active_calibration_start_pending = False
            self._calibration_point_terminal.set()
        if result != 0:
            raise SeveninvensunSdkCallError("_7i_complete_calibration", result)

    def abort_calibration(self) -> None:
        """Best-effort close of an incomplete calibration session."""
        with self._lock:
            active_point = self._active_calibration_token is not None
            active_session = self._calibration_point_count is not None
        if active_point:
            self.cancel_calibration_point()
        if active_session:
            self._complete_calibration()

    def apply_calibration_coefficients(
        self,
        left: bytes,
        right: bytes,
        *,
        expected_device_hash: str,
    ) -> None:
        """Apply an exact pair while checking device identity around each native write."""
        if not self._started:
            raise SeveninvensunSdkError("Start the SDK before loading calibration coefficients.")
        if len(expected_device_hash) != 64 or any(
            character not in "0123456789abcdef" for character in expected_device_hash
        ):
            raise ValueError("expected_device_hash must be a SHA-256 hex digest.")
        for key, raw in ((b"biLeft", left), (b"biRight", right)):
            if len(raw) != CALIBRATION_COEFFICIENT_SIZE:
                raise SeveninvensunSdkError(
                    "Each calibration coefficient must be exactly 1024 bytes."
                )
            self._require_device_binding(expected_device_hash)
            coefficient = _Coefficient.from_buffer_copy(raw)
            try:
                result = int(self._dll._7i_set_data(key, len(key), coefficient.buf, len(raw)))
            finally:
                ctypes.memset(ctypes.byref(coefficient), 0, ctypes.sizeof(coefficient))
            if result != 0:
                raise SeveninvensunSdkCallError("_7i_set_data", result)
        self._require_device_binding(expected_device_hash)

    def _require_device_binding(self, expected_device_hash: str) -> None:
        if not hmac.compare_digest(self.get_mpu_id_hash(), expected_device_hash):
            raise SeveninvensunSdkError(
                "Connected device identity changed during coefficient apply."
            )

    def discard_pending_samples(self) -> int:
        """Discard stale queued callbacks before a post-calibration verification window."""
        with self._lock:
            discarded = self._discard_queued_samples_locked()
            self._discarded_callback_samples += discarded
        return discarded

    def begin_fresh_sample_window(self) -> tuple[int, int, int, int]:
        """Set a callback-entry cutoff and return host/sequence/drop/discard baselines."""
        with self._lock:
            discarded = self._discard_queued_samples_locked()
            self._discarded_callback_samples += discarded
            cutoff_ns = time.perf_counter_ns()
            self._sample_window_host_cutoff_ns = cutoff_ns
            return (
                cutoff_ns,
                self._next_sequence,
                self._dropped_callback_samples,
                self._discarded_callback_samples,
            )

    def _discard_queued_samples_locked(self) -> int:
        discarded = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return discarded
            discarded += 1

    def read_sample(self, timeout_seconds: float = 0.25) -> dict[str, Any] | None:
        """Read and decode one copied callback, returning ``None`` on timeout."""
        if self._closed:
            raise SeveninvensunSdkError("A closed SDK capture cannot be reused.")
        try:
            queued = self._queue.get(timeout=timeout_seconds)
        except queue.Empty:
            return None
        return decode_sdk_sample(
            queued.raw_data,
            sequence=queued.sequence,
            host_monotonic_ns=queued.host_monotonic_ns,
            host_wall_time_ns=queued.host_wall_time_ns,
            image_metadata=self.image_metadata,
        )

    def stop(self) -> None:
        """Stop capture and release authorization; safe to call during cleanup."""
        if self._closed or self._dll is None:
            return
        cleanup_error: Exception | None = None
        if self.calibration_active:
            try:
                self.abort_calibration()
            except Exception as error:  # noqa: BLE001 - continue native shutdown
                cleanup_error = error
        if self._started or self._start_attempted:
            try:
                self._stop_result = int(self._dll._7i_stop())
            except Exception as error:  # noqa: BLE001 - retain callbacks, then report failure
                if cleanup_error is None:
                    cleanup_error = error
            finally:
                self._started = False
                self._start_attempted = False
        # The native API does not document callback quiescence. Serialize this
        # barrier with the complete callback copy/enqueue critical section so
        # stop() cannot return while an accepted gaze callback is still in flight.
        with self._gaze_callback_gate:
            self._accepting_gaze_callbacks = False
        try:
            self.stop_scene_decoder()
        except Exception as error:  # noqa: BLE001 - do not skip authorization cleanup
            if cleanup_error is None:
                cleanup_error = error
        if self._authorized:
            try:
                self._disconnect_result = int(self._dll._7i_device_disconnect())
            except Exception as error:  # noqa: BLE001 - do not skip lifetime quarantine
                if cleanup_error is None:
                    cleanup_error = error
            finally:
                self._authorized = False
        if cleanup_error is not None:
            if self._start_was_attempted and self not in _CAPTURE_LIFETIME_GUARD:
                _CAPTURE_LIFETIME_GUARD.append(self)
            raise cleanup_error
        if self._start_was_attempted and self not in _CAPTURE_LIFETIME_GUARD:
            _CAPTURE_LIFETIME_GUARD.append(self)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self._closed = True
            if self not in _CAPTURE_LIFETIME_GUARD:
                _CAPTURE_LIFETIME_GUARD.append(self)

    def __enter__(self) -> SeveninvensunGlassesSdkCapture:
        self.load()
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _on_camera_state(self, state: int, _context: int | None) -> None:
        with self._lock:
            self._camera_state = int(state)

    def _on_image(
        self,
        eye: int,
        _image: int | None,
        size: int,
        width: int,
        height: int,
        timestamp: int,
        _context: int | None,
    ) -> None:
        # Every executable vendor sample uses 13 for the H.265 scene stream.
        names = {1: "left_eye", 2: "right_eye", 10: "vendor_image_10", 13: "scene"}
        name = names.get(int(eye), f"vendor_eye_{int(eye)}")
        host_monotonic_ns = time.perf_counter_ns()
        metadata = {
            "vendor_eye_code": int(eye),
            "callback_payload_size_bytes": int(size),
            "width": int(width),
            "height": int(height),
            "device_timestamp_raw": int(timestamp),
            "host_monotonic_ns": host_monotonic_ns,
        }
        with self._lock:
            self._last_images[name] = metadata
        if int(eye) != 13 or self._scene_decoder_thread is None:
            return
        dimensions = self._scene_dimensions
        valid_payload = (
            dimensions is not None
            and (int(width), int(height)) == dimensions
            and bool(_image)
            and 0 < int(size) <= int(width) * int(height) * 3
        )
        if not valid_payload:
            with self._lock:
                self._rejected_scene_images += 1
            return
        if _image:
            encoded = _QueuedSceneImage(
                encoded_bytes=ctypes.string_at(_image, int(size)),
                width=int(width),
                height=int(height),
                device_timestamp_raw=int(timestamp),
                host_monotonic_ns=host_monotonic_ns,
            )
            try:
                self._scene_encoded_queue.put_nowait(encoded)
            except queue.Full:
                try:
                    self._scene_encoded_queue.get_nowait()
                except queue.Empty:
                    pass
                with self._lock:
                    self._dropped_scene_images += 1
                try:
                    self._scene_encoded_queue.put_nowait(encoded)
                except queue.Full:
                    with self._lock:
                        self._dropped_scene_images += 1

    def _on_gaze(self, eyes: Any, _context: int | None) -> None:
        if not eyes:
            return
        with self._gaze_callback_gate:
            if not self._accepting_gaze_callbacks:
                self._post_stop_callback_samples += 1
                return
            host_monotonic_ns = time.perf_counter_ns()
            host_wall_time_ns = time.time_ns()
            raw_data = ctypes.string_at(eyes, ctypes.sizeof(_EyeDataEx))
            with self._lock:
                sequence = self._next_sequence
                self._next_sequence += 1
                cutoff_ns = self._sample_window_host_cutoff_ns
                if cutoff_ns is not None and host_monotonic_ns <= cutoff_ns:
                    self._discarded_callback_samples += 1
                    return
                queued = _QueuedSample(raw_data, sequence, host_monotonic_ns, host_wall_time_ns)
                try:
                    self._queue.put_nowait(queued)
                except queue.Full:
                    self._dropped_callback_samples += 1

    def _on_left_calibration_progress(
        self,
        index: int,
        percent: int,
        context: int | None,
    ) -> None:
        self._on_calibration_progress("left", index, percent, context)

    def _on_right_calibration_progress(
        self,
        index: int,
        percent: int,
        context: int | None,
    ) -> None:
        self._on_calibration_progress("right", index, percent, context)

    def _on_calibration_progress(
        self,
        eye: str,
        index: int,
        percent: int,
        context: int | None,
    ) -> None:
        with self._lock:
            current = context == self._active_calibration_token
        if current:
            self._calibration_events.put(
                SeveninvensunCalibrationEvent(eye, "progress", int(index), int(percent))
            )

    def _on_left_calibration_finish(
        self,
        index: int,
        error: int,
        context: int | None,
    ) -> None:
        self._on_calibration_finish("left", index, error, context)

    def _on_right_calibration_finish(
        self,
        index: int,
        error: int,
        context: int | None,
    ) -> None:
        self._on_calibration_finish("right", index, error, context)

    def _on_calibration_finish(
        self,
        eye: str,
        index: int,
        error: int,
        context: int | None,
    ) -> None:
        with self._lock:
            if context != self._active_calibration_token:
                return
            statuses = self._calibration_finish_status.get(int(index))
            if statuses is None:
                return
            statuses[eye] = int(error)
            self._finish_active_calibration_point_if_ready_locked(int(index))
        self._calibration_events.put(
            SeveninvensunCalibrationEvent(eye, "finish", int(index), int(error))
        )
