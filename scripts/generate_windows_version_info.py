from __future__ import annotations

import argparse
import re
import tomllib
from collections.abc import Sequence
from pathlib import Path


def numeric_version_tuple(
    version: str,
) -> tuple[int, int, int, int]:
    """Convert a project version to Windows' four integers."""

    numbers = [
        int(value)
        for value in re.findall(
            r"\d+",
            version,
        )
    ][:4]

    if not numbers:
        raise ValueError(f"Version contains no numeric component: {version}")

    numbers.extend([0] * (4 - len(numbers)))
    return tuple(numbers)


def render_version_info(
    version: str,
) -> str:
    """Render a PyInstaller Windows version resource."""

    numeric = numeric_version_tuple(version)
    comma_version = ", ".join(str(value) for value in numeric)
    dotted_version = ".".join(str(value) for value in numeric)

    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({comma_version}),
    prodvers=({comma_version}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [
          StringStruct('CompanyName', 'Etymodes and TiantanDoC'),
          StringStruct('FileDescription', 'OculiDoC desktop application'),
          StringStruct('FileVersion', '{dotted_version}'),
          StringStruct('InternalName', 'OculiDoC'),
          StringStruct('LegalCopyright', 'OculiDoC contributors'),
          StringStruct('OriginalFilename', 'OculiDoC.exe'),
          StringStruct('ProductName', 'OculiDoC'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([
      VarStruct('Translation', [2052, 1200])
    ])
  ]
)
"""


def project_version(
    pyproject_path: Path,
) -> str:
    """Read the canonical project version."""

    document = tomllib.loads(
        pyproject_path.read_text(
            encoding="utf-8",
        )
    )
    version = str(document["project"]["version"]).strip()

    if not version:
        raise ValueError("Project version is empty.")

    return version


def write_version_info(
    pyproject_path: Path,
    output_path: Path,
) -> Path:
    """Write version metadata for PyInstaller."""

    version = project_version(pyproject_path)
    output = output_path.expanduser().resolve()
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    output.write_text(
        render_version_info(version),
        encoding="utf-8",
    )
    return output


def main(
    argv: Sequence[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pyproject",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    args = parser.parse_args(argv)

    output = write_version_info(
        args.pyproject,
        args.output,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
