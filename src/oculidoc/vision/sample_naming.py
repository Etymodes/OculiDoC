"""Sequential naming for eye-observation samples."""

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SAMPLE_PREFIX = "OculiDoC_Eye_Sample"
DEFAULT_NUMBER_WIDTH = 4


@dataclass(frozen=True, slots=True)
class EyeSamplePaths:
    """File paths belonging to one observation sample."""

    index: int
    stem: str
    overlay_path: Path
    raw_path: Path
    record_path: Path

    def __post_init__(self) -> None:
        if self.index <= 0:
            raise ValueError("Sample index must be positive.")

        if not self.stem.strip():
            raise ValueError("Sample stem cannot be empty.")


def _validate_prefix(prefix: str) -> str:
    normalized = prefix.strip()

    if not normalized:
        raise ValueError("Sample prefix cannot be empty.")

    if "/" in normalized or "\\" in normalized:
        raise ValueError("Sample prefix cannot contain path separators.")

    return normalized


def find_used_sample_indices(
    directory: str | Path,
    *,
    prefix: str = DEFAULT_SAMPLE_PREFIX,
) -> set[int]:
    """Find indices represented by any existing sample artifact."""
    directory_path = Path(directory)
    normalized_prefix = _validate_prefix(prefix)

    if not directory_path.exists():
        return set()

    if not directory_path.is_dir():
        raise ValueError("Sample directory must be a directory.")

    pattern = re.compile(
        rf"^{re.escape(normalized_prefix)}"
        r"_?(?P<index>\d+)"
        r"(?:_[^.]+)?"
        r"\.(?:png|jpg|jpeg|json)$",
        flags=re.IGNORECASE,
    )

    indices: set[int] = set()

    for path in directory_path.iterdir():
        if not path.is_file():
            continue

        match = pattern.fullmatch(path.name)

        if match is None:
            continue

        index = int(match.group("index"))

        if index > 0:
            indices.add(index)

    return indices


def next_sample_index(
    directory: str | Path,
    *,
    prefix: str = DEFAULT_SAMPLE_PREFIX,
) -> int:
    """Return one greater than the largest existing index."""
    indices = find_used_sample_indices(
        directory,
        prefix=prefix,
    )

    return max(indices, default=0) + 1


def build_eye_sample_paths(
    directory: str | Path,
    *,
    index: int,
    prefix: str = DEFAULT_SAMPLE_PREFIX,
    number_width: int = DEFAULT_NUMBER_WIDTH,
) -> EyeSamplePaths:
    """Construct all primary paths for one sample."""
    if index <= 0:
        raise ValueError("Sample index must be positive.")

    if number_width <= 0:
        raise ValueError("number_width must be positive.")

    normalized_prefix = _validate_prefix(prefix)
    directory_path = Path(directory)
    width = max(
        number_width,
        len(str(index)),
    )
    stem = f"{normalized_prefix}_{index:0{width}d}"

    return EyeSamplePaths(
        index=index,
        stem=stem,
        overlay_path=(directory_path / f"{stem}.png"),
        raw_path=(directory_path / f"{stem}_raw.png"),
        record_path=(directory_path / f"{stem}.json"),
    )


def next_eye_sample_paths(
    directory: str | Path,
    *,
    prefix: str = DEFAULT_SAMPLE_PREFIX,
    number_width: int = DEFAULT_NUMBER_WIDTH,
) -> EyeSamplePaths:
    """Allocate the next sequential sample paths."""
    directory_path = Path(directory)
    directory_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    index = next_sample_index(
        directory_path,
        prefix=prefix,
    )

    while True:
        paths = build_eye_sample_paths(
            directory_path,
            index=index,
            prefix=prefix,
            number_width=number_width,
        )

        if not any(
            path.exists()
            for path in (
                paths.overlay_path,
                paths.raw_path,
                paths.record_path,
            )
        ):
            return paths

        index += 1
