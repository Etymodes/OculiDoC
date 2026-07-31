"""Clean-room eye-position display calculations."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import atan2, degrees, fsum

from oculidoc.devices.contracts import EyeTrackerSample

Point2D = tuple[float, float]
Point3D = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class EyePositioningParameters:
    """One display-ready binocular positioning state.

    These fields are the clean-room equivalents of the proven
    ``TrackStatusControlViewModel`` properties ``LeftEyePos``, ``RightEyePos``,
    ``Distance``, ``IsDistanceInRange``, ``LeftEyeAngle`` and
    ``RightEyeAngle``.
    """

    left_eye_position: Point2D | None
    right_eye_position: Point2D | None
    left_eye_extrapolated: bool
    right_eye_extrapolated: bool
    distance_mm: float | None
    is_distance_in_range: bool
    head_angle_degrees: float | None


class EyePositioningParametersCalculator:
    """Reproduce the observable positioning calculations of the audited viewer.

    The managed binary preserves the original type name
    ``EyePositioningParametersCalculator`` and the member names
    ``ReceiveGazeData``, ``ComputeEyesPosition``, ``ComputeEyesDistance``,
    ``SyncEyesPositioningParameters`` and ``ExtrapolatePosition``. Python local
    names in this implementation are new because the release has no PDB/source.
    Proven field names map directly as follows: ``BufferSize`` and
    ``TobiiRexFrequency`` to the constants below,
    ``MaxCountOfExtrapolatedGazeDataPosition`` to the history limit,
    ``_latestLeftEyePoints``/``_latestRightEyePoints`` to the two point deques,
    and ``_distanceBuffer`` to the rolling distance deque.
    """

    BUFFER_SIZE = 32
    TOBII_REX_FREQUENCY_HZ = 30
    MAX_COUNT_OF_EXTRAPOLATED_GAZE_DATA_POSITION = 10
    HEAD_ANGLE_LIMIT_DEGREES = 20.0
    STRANGE_DISTANCES_MM = (5.504, 10.223, 4.1)
    STRANGE_DISTANCE_TOLERANCE_MM = 0.1
    DEFAULT_MIN_DISTANCE_MM = 450.0
    DEFAULT_MAX_DISTANCE_MM = 850.0

    def __init__(
        self,
        *,
        min_distance_mm: float = DEFAULT_MIN_DISTANCE_MM,
        max_distance_mm: float = DEFAULT_MAX_DISTANCE_MM,
    ) -> None:
        if min_distance_mm < 0 or max_distance_mm <= min_distance_mm:
            raise ValueError("Eye-position distance limits are invalid.")

        self.min_distance_mm = float(min_distance_mm)
        self.max_distance_mm = float(max_distance_mm)
        self._latest_left_eye_points: deque[Point2D | None] = deque(
            maxlen=self.MAX_COUNT_OF_EXTRAPOLATED_GAZE_DATA_POSITION
        )
        self._latest_right_eye_points: deque[Point2D | None] = deque(
            maxlen=self.MAX_COUNT_OF_EXTRAPOLATED_GAZE_DATA_POSITION
        )
        self._distance_buffer: deque[float] = deque(maxlen=self.BUFFER_SIZE)

    @staticmethod
    def _display_point(position: Point3D | None) -> Point2D | None:
        if position is None:
            return None

        return 1.0 - position[0], position[1]

    @staticmethod
    def _extrapolate_position(
        latest_points: deque[Point2D | None],
    ) -> Point2D | None:
        if not latest_points:
            return None

        current = latest_points[-1]
        if current is not None:
            return current

        measured = [
            (index, point) for index, point in enumerate(latest_points) if point is not None
        ]
        if len(measured) < 2:
            return None

        previous_index, previous = measured[-2]
        latest_index, latest = measured[-1]
        measured_gap = latest_index - previous_index
        if measured_gap <= 0:
            return None

        missing_count = len(latest_points) - 1 - latest_index
        scale = missing_count / measured_gap
        return (
            latest[0] + (latest[0] - previous[0]) * scale,
            latest[1] + (latest[1] - previous[1]) * scale,
        )

    @classmethod
    def _usable_distance_mm(
        cls,
        eye_position_mm: Point3D | None,
    ) -> float | None:
        if eye_position_mm is None:
            return None

        distance_mm = eye_position_mm[2]
        if distance_mm <= 0:
            return None

        if any(
            abs(distance_mm - sentinel) < cls.STRANGE_DISTANCE_TOLERANCE_MM
            for sentinel in cls.STRANGE_DISTANCES_MM
        ):
            return None

        return distance_mm

    def _compute_distance_mm(
        self,
        sample: EyeTrackerSample,
    ) -> float | None:
        available = tuple(
            distance
            for distance in (
                self._usable_distance_mm(sample.left_eye_position_mm),
                self._usable_distance_mm(sample.right_eye_position_mm),
            )
            if distance is not None
        )
        if available:
            self._distance_buffer.append(fsum(available) / len(available))

        if not self._distance_buffer:
            return None

        return fsum(self._distance_buffer) / len(self._distance_buffer)

    @classmethod
    def _head_angle_degrees(
        cls,
        left_eye_position: Point2D | None,
        right_eye_position: Point2D | None,
    ) -> float | None:
        if left_eye_position is None or right_eye_position is None:
            return None

        angle = degrees(
            atan2(
                right_eye_position[1] - left_eye_position[1],
                right_eye_position[0] - left_eye_position[0],
            )
        )
        return max(
            -cls.HEAD_ANGLE_LIMIT_DEGREES,
            min(cls.HEAD_ANGLE_LIMIT_DEGREES, angle),
        )

    def receive_gaze_data(
        self,
        sample: EyeTrackerSample,
    ) -> EyePositioningParameters:
        """Compute the next display state from one device sample."""

        measured_left = self._display_point(sample.left_eye_position_normalized)
        measured_right = self._display_point(sample.right_eye_position_normalized)
        self._latest_left_eye_points.append(measured_left)
        self._latest_right_eye_points.append(measured_right)

        left_eye_position = self._extrapolate_position(self._latest_left_eye_points)
        right_eye_position = self._extrapolate_position(self._latest_right_eye_points)
        distance_mm = self._compute_distance_mm(sample)

        return EyePositioningParameters(
            left_eye_position=left_eye_position,
            right_eye_position=right_eye_position,
            left_eye_extrapolated=(measured_left is None and left_eye_position is not None),
            right_eye_extrapolated=(measured_right is None and right_eye_position is not None),
            distance_mm=distance_mm,
            is_distance_in_range=(
                distance_mm is not None
                and self.min_distance_mm <= distance_mm <= self.max_distance_mm
            ),
            head_angle_degrees=self._head_angle_degrees(
                left_eye_position,
                right_eye_position,
            ),
        )
