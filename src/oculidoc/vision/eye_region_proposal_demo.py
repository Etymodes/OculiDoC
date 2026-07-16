"""Capture one frame and generate eye-region proposals."""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

import cv2

from oculidoc.app_paths import (
    UNASSIGNED_PATIENT_KEY,
    patient_data_directory,
)
from oculidoc.devices import DeviceState
from oculidoc.devices.opencv_camera import (
    OpenCVCameraDevice,
)
from oculidoc.vision.eye_region_proposal import (
    EyeRegionProposal,
    FaceDetection,
    OpenCVHaarFaceDetector,
    propose_eye_regions,
    propose_eye_regions_from_face,
)
from oculidoc.vision.overlay import (
    draw_eye_observations,
)

_BACKENDS: dict[str, int | None] = {
    "auto": None,
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
}


def build_parser() -> argparse.ArgumentParser:
    """Create the proposal-demo parser."""
    parser = argparse.ArgumentParser(prog=("python -m oculidoc.vision.eye_region_proposal_demo"))
    parser.add_argument(
        "--patient-key",
        default=UNASSIGNED_PATIENT_KEY,
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
        "--warmup-frames",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--face-mode",
        choices=("auto", "manual"),
        default="auto",
        help=("Use automatic face detection or manually select the face region."),
    )

    return parser


def _select_face_manually(
    image,
) -> EyeRegionProposal:
    """Ask the operator to select one complete face."""
    print("Select the complete face region.")
    print("Press ENTER or SPACE to confirm; C cancels.")

    x_px, y_px, width_px, height_px = cv2.selectROI(
        "Select complete face",
        image,
        showCrosshair=True,
        fromCenter=False,
    )
    cv2.destroyWindow("Select complete face")

    if width_px <= 0 or height_px <= 0:
        raise RuntimeError("Manual face selection was cancelled.")

    face = FaceDetection(
        x_px=int(x_px),
        y_px=int(y_px),
        width_px=int(width_px),
        height_px=int(height_px),
    )
    observations = propose_eye_regions_from_face(
        face,
        image_width_px=image.shape[1],
        image_height_px=image.shape[0],
    )

    return EyeRegionProposal(
        face=face,
        observations=observations,
    )


def _generate_proposal(
    image,
    *,
    face_mode: str,
) -> tuple[EyeRegionProposal, str]:
    """Generate an automatic or operator-assisted proposal."""
    if face_mode == "manual":
        return (
            _select_face_manually(image),
            "manual-face-geometry",
        )

    try:
        detector = OpenCVHaarFaceDetector()
    except RuntimeError as error:
        print("Automatic Haar detector unavailable:")
        print(error)
        print("Falling back to manual face selection.")

        return (
            _select_face_manually(image),
            "manual-face-geometry",
        )

    proposal = propose_eye_regions(
        image,
        detector,
    )

    if proposal is not None:
        return proposal, "opencv-haar"

    print("No frontal face was detected automatically.")
    print("Falling back to manual face selection.")

    return (
        _select_face_manually(image),
        "manual-face-geometry",
    )


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Capture a frame and save proposed eye regions."""
    args = build_parser().parse_args(argv)

    if args.warmup_frames <= 0:
        raise ValueError("warmup-frames must be positive.")

    camera = OpenCVCameraDevice(
        index=args.index,
        backend=_BACKENDS[args.backend],
    )

    packet = None

    try:
        camera.connect()
        camera.start_stream()

        for _ in range(args.warmup_frames):
            packet = camera.read_frame()
    finally:
        if camera.state is DeviceState.STREAMING:
            camera.stop_stream()

        if camera.state is DeviceState.CONNECTED:
            camera.disconnect()

    if packet is None:
        raise RuntimeError("No camera frame was captured.")

    proposal, proposal_method = _generate_proposal(
        packet.image,
        face_mode=args.face_mode,
    )

    rendered = draw_eye_observations(
        packet.image,
        proposal.observations,
    )

    face = proposal.face
    cv2.rectangle(
        rendered,
        (face.x_px, face.y_px),
        (
            face.right_px - 1,
            face.bottom_px - 1,
        ),
        (255, 255, 0),
        2,
    )

    output_directory = patient_data_directory(args.patient_key) / "eye_region_proposals"
    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_path = output_directory / f"Eye_Proposal_{timestamp}.png"

    if not cv2.imwrite(
        str(output_path),
        rendered,
    ):
        raise RuntimeError(f"Could not save proposal: {output_path}")

    print(f"Face: {face.x_px},{face.y_px},{face.width_px}x{face.height_px}")

    for observation in proposal.observations:
        box = observation.box
        print(f"{observation.side.value}: {box.x_px},{box.y_px},{box.width_px}x{box.height_px}")

    print(f"Proposal method: {proposal_method}")
    print(f"Output: {output_path}")
    print("Opening-state classification: not performed")
    print("Operator review required: True")
    print("Camera disconnected: True")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
