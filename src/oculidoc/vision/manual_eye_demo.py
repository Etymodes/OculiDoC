"""Interactively label eye regions in one camera frame."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import cv2

from oculidoc.devices.opencv_camera import (
    OpenCVCameraDevice,
)
from oculidoc.vision.eye_observation import (
    EyeBoundingBox,
    EyeObservation,
    EyeOpeningState,
    EyeSide,
)
from oculidoc.vision.overlay import (
    draw_eye_observations,
)

_BACKENDS = {
    "auto": None,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
}


def build_parser() -> argparse.ArgumentParser:
    """Create the manual eye-labeling parser."""
    parser = argparse.ArgumentParser(
        prog=("python -m oculidoc.vision.manual_eye_demo"),
    )
    parser.add_argument(
        "--index",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--backend",
        choices=tuple(_BACKENDS),
        default="dshow",
    )
    parser.add_argument(
        "--left-state",
        type=EyeOpeningState,
        choices=list(EyeOpeningState),
        default=EyeOpeningState.UNKNOWN,
    )
    parser.add_argument(
        "--right-state",
        type=EyeOpeningState,
        choices=list(EyeOpeningState),
        default=EyeOpeningState.UNKNOWN,
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    return parser


def select_eye_box(
    window_title: str,
    image,
) -> EyeBoundingBox | None:
    """Let the operator select one eye region."""
    x, y, width, height = cv2.selectROI(
        window_title,
        image,
        showCrosshair=True,
        fromCenter=False,
    )
    cv2.destroyWindow(window_title)

    if width <= 0 or height <= 0:
        return None

    return EyeBoundingBox(
        x_px=int(x),
        y_px=int(y),
        width_px=int(width),
        height_px=int(height),
    )


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Capture and manually label both eye regions."""
    args = build_parser().parse_args(argv)

    camera = OpenCVCameraDevice(
        index=args.index,
        backend=_BACKENDS[args.backend],
    )

    try:
        camera.connect()
        camera.start_stream()

        for _ in range(5):
            packet = camera.read_frame()

        image = packet.image
    finally:
        if camera.state.value == "streaming":
            camera.stop_stream()

        if camera.state.value == "connected":
            camera.disconnect()

    print("Select the patient's anatomical LEFT eye.")
    print("Press ENTER or SPACE to confirm; C cancels.")

    left_box = select_eye_box(
        "Select LEFT eye",
        image,
    )

    print("Select the patient's anatomical RIGHT eye.")

    right_box = select_eye_box(
        "Select RIGHT eye",
        image,
    )

    observations = []

    if left_box is not None:
        observations.append(
            EyeObservation(
                side=EyeSide.LEFT,
                box=left_box,
                opening_state=args.left_state,
            )
        )

    if right_box is not None:
        observations.append(
            EyeObservation(
                side=EyeSide.RIGHT,
                box=right_box,
                opening_state=args.right_state,
            )
        )

    rendered = draw_eye_observations(
        image,
        observations,
    )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not cv2.imwrite(
        str(args.output),
        rendered,
    ):
        raise RuntimeError("Could not save the annotated image.")

    print(f"Observations: {len(observations)}")
    print(f"Output: {args.output.resolve()}")
    print("Camera disconnected: True")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
