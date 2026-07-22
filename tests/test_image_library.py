from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from oculidoc.image_library import IMAGE_UPLOAD_GUIDE, ImageLibraryStore
from oculidoc.tasks.image_choice import ImageChoiceConfig, eligible_image_assets


def write_test_image(path: Path, color: str) -> None:
    image = QImage(640, 640, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    assert image.save(str(path))


def test_uploaded_image_is_copied_and_metadata_is_editable(tmp_path: Path) -> None:
    source = tmp_path / "target.png"
    write_test_image(source, "#22aa66")
    store = ImageLibraryStore(tmp_path / "library")

    asset = store.add_file(
        source,
        label="绿色追踪球",
        category="追踪球",
        style="透明图标",
    )
    managed = store.resolve_path(asset)

    assert managed is not None
    assert managed.is_file()
    assert managed.parent == store.files_directory
    assert "1:1" in IMAGE_UPLOAD_GUIDE

    updated = store.update_metadata(
        asset.image_id,
        label="绿色目标",
        category="追踪球",
        style="自定义图标",
    )
    assert updated.image_id == asset.image_id
    assert {item.image_id: item for item in store.load()}[asset.image_id].label == "绿色目标"


def test_category_and_style_filters_require_two_distinct_images(tmp_path: Path) -> None:
    store = ImageLibraryStore(tmp_path / "library")
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    write_test_image(first, "#ff0000")
    write_test_image(second, "#0000ff")
    store.add_file(first, label="红球", category="追踪球", style="照片")
    store.add_file(second, label="蓝球", category="追踪球", style="照片")

    config = ImageChoiceConfig(
        category_filters=("追踪球",),
        style_filters=("照片",),
        question_count=2,
    )
    eligible = eligible_image_assets(config, store)

    assert {asset.label for asset in eligible} == {"红球", "蓝球"}

    with pytest.raises(ValueError, match="至少需要两张"):
        eligible_image_assets(
            ImageChoiceConfig(
                category_filters=("植物",),
                style_filters=("彩色图标",),
                question_count=1,
            ),
            store,
        )
