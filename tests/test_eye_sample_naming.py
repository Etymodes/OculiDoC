"""Sequential eye-sample naming tests."""

from pathlib import Path

import pytest

from oculidoc.vision.sample_naming import (
    build_eye_sample_paths,
    find_used_sample_indices,
    next_eye_sample_paths,
    next_sample_index,
)


def test_empty_directory_starts_at_one(
    tmp_path: Path,
) -> None:
    paths = next_eye_sample_paths(tmp_path)

    assert paths.index == 1
    assert paths.stem == "OculiDoC_Eye_Sample_0001"
    assert paths.overlay_path.name == ("OculiDoC_Eye_Sample_0001.png")
    assert paths.raw_path.name == ("OculiDoC_Eye_Sample_0001_raw.png")
    assert paths.record_path.name == ("OculiDoC_Eye_Sample_0001.json")


def test_existing_artifacts_increment_number(
    tmp_path: Path,
) -> None:
    (tmp_path / "OculiDoC_Eye_Sample_0001.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (tmp_path / "OculiDoC_Eye_Sample_0003_raw.png").write_bytes(b"image")

    assert find_used_sample_indices(tmp_path) == {1, 3}
    assert next_sample_index(tmp_path) == 4

    paths = next_eye_sample_paths(tmp_path)

    assert paths.index == 4
    assert paths.stem == "OculiDoC_Eye_Sample_0004"


def test_crop_file_reserves_its_index(
    tmp_path: Path,
) -> None:
    (tmp_path / ("OculiDoC_Eye_Sample_0007_left_open.png")).write_bytes(b"crop")

    assert next_sample_index(tmp_path) == 8


def test_legacy_manual_name_is_recognized(
    tmp_path: Path,
) -> None:
    (tmp_path / "OculiDoC_Eye_Sample3.json").write_text(
        "{}",
        encoding="utf-8",
    )

    assert find_used_sample_indices(tmp_path) == {3}
    assert next_sample_index(tmp_path) == 4


def test_unrelated_files_are_ignored(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (tmp_path / "patient_9999.png").write_bytes(b"image")

    assert find_used_sample_indices(tmp_path) == set()


def test_large_index_is_not_truncated(
    tmp_path: Path,
) -> None:
    paths = build_eye_sample_paths(
        tmp_path,
        index=12345,
    )

    assert paths.stem == "OculiDoC_Eye_Sample_12345"


@pytest.mark.parametrize(
    "prefix",
    ["", "   ", "invalid/name"],
)
def test_invalid_prefix_is_rejected(
    tmp_path: Path,
    prefix: str,
) -> None:
    with pytest.raises(ValueError):
        next_eye_sample_paths(
            tmp_path,
            prefix=prefix,
        )
