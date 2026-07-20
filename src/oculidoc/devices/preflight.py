"""Short eye-tracker quality check performed before every gaze task."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic, sleep

from oculidoc.devices.contracts import EyeTrackerDevice


@dataclass(frozen=True, slots=True)
class GazePreflightResult:
    source: str
    device_name: str
    device_url: str | None
    library_path: str | None
    duration_seconds: float
    sample_count: int
    valid_sample_count: int
    sample_rate_hz: float
    valid_ratio: float
    minimum_valid_ratio: float
    passed: bool
    error: str | None
    updated_at_utc: str

    @classmethod
    def from_dict(cls, value: object) -> GazePreflightResult:
        if not isinstance(value, dict):
            raise TypeError("Gaze preflight result must be an object.")

        return cls(
            source=str(value["source"]),
            device_name=str(value["device_name"]),
            device_url=(str(value["device_url"]) if value.get("device_url") else None),
            library_path=(str(value["library_path"]) if value.get("library_path") else None),
            duration_seconds=float(value["duration_seconds"]),
            sample_count=int(value["sample_count"]),
            valid_sample_count=int(value["valid_sample_count"]),
            sample_rate_hz=float(value["sample_rate_hz"]),
            valid_ratio=float(value["valid_ratio"]),
            minimum_valid_ratio=float(value["minimum_valid_ratio"]),
            passed=bool(value["passed"]),
            error=(str(value["error"]) if value.get("error") else None),
            updated_at_utc=str(value["updated_at_utc"]),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def status_text(self) -> str:
        if self.error:
            return f"{self.device_name} · 预检失败：{self.error}"

        return f"{self.device_name} · {self.sample_rate_hz:.0f} Hz · 有效率 {self.valid_ratio:.0%}"


class GazePreflightStore:
    """Persist the latest preflight result for the administrator status bar."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self) -> GazePreflightResult | None:
        if not self.path.exists():
            return None

        try:
            return GazePreflightResult.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, KeyError):
            return None

    def save(self, result: GazePreflightResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(result.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")

        temporary_path.replace(self.path)


def failed_gaze_preflight(
    *,
    source: str,
    device_name: str,
    minimum_valid_ratio: float,
    error: str,
) -> GazePreflightResult:
    return GazePreflightResult(
        source=source,
        device_name=device_name,
        device_url=None,
        library_path=None,
        duration_seconds=0.0,
        sample_count=0,
        valid_sample_count=0,
        sample_rate_hz=0.0,
        valid_ratio=0.0,
        minimum_valid_ratio=minimum_valid_ratio,
        passed=False,
        error=error.strip() or "未知设备错误",
        updated_at_utc=datetime.now(UTC).isoformat(),
    )


def run_gaze_preflight(
    device: EyeTrackerDevice,
    *,
    source: str,
    duration_seconds: float,
    minimum_valid_ratio: float,
    clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> GazePreflightResult:
    """Measure live sample rate and validity on an already streaming device."""
    if duration_seconds < 0:
        raise ValueError("duration_seconds cannot be negative.")

    if not 0.0 <= minimum_valid_ratio <= 1.0:
        raise ValueError("minimum_valid_ratio must be between zero and one.")

    started_at = clock()
    deadline = started_at + duration_seconds
    sample_count = 0
    valid_sample_count = 0
    first_attempt = True

    while first_attempt or clock() < deadline:
        first_attempt = False

        try:
            sample = device.read_sample()
        except TimeoutError:
            sleeper(0.002)
            continue

        sample_count += 1
        valid_sample_count += int(sample.gaze_valid)

    elapsed = max(clock() - started_at, 0.001)
    valid_ratio = valid_sample_count / sample_count if sample_count else 0.0
    sample_rate_hz = sample_count / elapsed
    passed = sample_count > 0 and valid_ratio >= minimum_valid_ratio
    device_url = getattr(device, "device_url", None)
    library_path = getattr(device, "library_path", None)

    return GazePreflightResult(
        source=source,
        device_name=device.info.name,
        device_url=str(device_url) if device_url else None,
        library_path=str(library_path) if library_path else None,
        duration_seconds=elapsed,
        sample_count=sample_count,
        valid_sample_count=valid_sample_count,
        sample_rate_hz=sample_rate_hz,
        valid_ratio=valid_ratio,
        minimum_valid_ratio=minimum_valid_ratio,
        passed=passed,
        error=(
            None
            if passed
            else (
                "预检期间未收到视线样本；请检查设备连接。"
                if sample_count == 0
                else (
                    f"实时有效率 {valid_ratio:.0%} 低于要求 "
                    f"{minimum_valid_ratio:.0%}；请调整患者位置并重新校准。"
                )
            )
        ),
        updated_at_utc=datetime.now(UTC).isoformat(),
    )
