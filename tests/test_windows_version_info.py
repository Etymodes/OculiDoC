from __future__ import annotations

from pathlib import Path

from scripts.generate_windows_version_info import (
    numeric_version_tuple,
    project_version,
    render_version_info,
    write_version_info,
)


def test_numeric_version_tuple_handles_prerelease() -> None:
    assert numeric_version_tuple("1.2.3rc4") == (
        1,
        2,
        3,
        4,
    )


def test_version_resource_contains_brand_fields() -> None:
    text = render_version_info("2.5.1")

    assert "ProductName', 'OculiDoC" in text
    assert "OriginalFilename', 'OculiDoC.exe" in text
    assert "ProductVersion', '2.5.1" in text
    assert "filevers=(2, 5, 1, 0)" in text


def test_project_version_and_output(
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        ('[project]\nname = "oculidoc"\nversion = "3.4.5"\n'),
        encoding="utf-8",
    )
    output = tmp_path / "version.txt"

    assert project_version(pyproject) == "3.4.5"
    written = write_version_info(
        pyproject,
        output,
    )

    assert written == output.resolve()
    assert output.is_file()
    assert "prodvers=(3, 4, 5, 0)" in (
        output.read_text(
            encoding="utf-8",
        )
    )
